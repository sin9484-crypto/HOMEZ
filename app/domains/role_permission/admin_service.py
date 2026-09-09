"""
=========================================================
Homez OS

File : app/domains/role_permission/admin_service.py

Gate T(2026-08-10) — 역할의 개별 Permission 집합을 편집하는 관리자
서비스. 기존 `RolePermissionService.replace_permissions()`(단순
delete+recreate)와 달리, 이 함수는 다음을 전부 강제한다:

  1. SUPER_ADMIN 역할 자체는 편집 대상에서 제외한다 — SUPER_ADMIN은
     `app/core/authorization.py::is_super_admin()`에 의해
     role_permissions 내용과 무관하게 모든 Permission 검사를 통과하므로
     (app/core/permission_check.py::has_permission 참고), 이 역할의
     행을 편집해도 실질적 접근 범위가 바뀌지 않는다 — 혼란을 막기
     위해 아예 막는다.
  2. recent-auth(POST /auth/recent-auth로 이미 발급된 토큰, 1회용)를
     소비해야 한다.
  3. 단발성 nonce(permission_edit_nonce.py, GET으로 현재 목록을 조회할
     때 발급됨)를 소비해야 한다.
  4. 낙관적 동시성 — 클라이언트가 마지막으로 읽은 현재 코드 집합
     (expected_codes)과 수정 시점의 실제 코드 집합이 다르면 거부한다
     (누군가 그사이에 이미 바꿨다는 뜻). role_permissions는 버전
     컬럼이 없는 다대다 테이블이므로, "실제 코드 집합 재조회 →
     비교 → 델타 적용"을 app/domains/account_registration/service.py
     ::atomic_register_user()와 동일한 raw sqlite3 BEGIN IMMEDIATE
     패턴으로 한 Transaction에 묶어 True 원자성을 보장한다(SQLAlchemy
     Session의 기본 지연 트랜잭션은 두 커넥션이 동시에 SELECT한 뒤
     둘 다 쓰는 경쟁을 막지 못하므로 여기서는 쓰지 않는다).
  5. 새 코드 집합의 모든 코드가 실제로 존재하는 활성 Permission인지
     서버가 재검증한다 — 클라이언트 Preset 이름은 UI 편의일 뿐 전혀
     신뢰하지 않는다.
  6. 수정 전/후 코드 목록을 audit_logs에 남긴다(비밀정보 없음).
=========================================================
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from datetime import timezone
from enum import Enum

from sqlalchemy.orm import Session

from app.core.recent_auth import consume_recent_auth_token
from app.core.permission_check import get_permission_codes_for_role
from app.domains.account_registration.service import resolve_sqlite_path
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.permission_edit_nonce import (
    PermissionEditNonceStatus,
    generate_permission_edit_nonce,
    mark_nonce_consumed,
    verify_nonce,
)
from app.domains.user.model import User

_BUSY_TIMEOUT_MS = 15000


class RolePermissionUpdateError(str, Enum):

    ROLE_NOT_FOUND = "ROLE_NOT_FOUND"
    SUPER_ADMIN_NOT_EDITABLE = "SUPER_ADMIN_NOT_EDITABLE"
    RECENT_AUTH_REQUIRED = "RECENT_AUTH_REQUIRED"
    NONCE_INVALID = "NONCE_INVALID"
    UNKNOWN_PERMISSION_CODE = "UNKNOWN_PERMISSION_CODE"
    VERSION_CONFLICT = "VERSION_CONFLICT"


def _now() -> datetime:

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_editable_role(db: Session, role_id: int) -> Role:

    role = db.query(Role).filter(Role.id == role_id).first()

    if role is None:
        raise LookupError("ROLE_NOT_FOUND")

    if (role.code or "").strip().upper() == "SUPER_ADMIN":
        raise ValueError(RolePermissionUpdateError.SUPER_ADMIN_NOT_EDITABLE.value)

    return role


def get_role_permission_codes(db: Session, role_id: int) -> set[str]:

    role = db.query(Role).filter(Role.id == role_id).first()

    if role is None:
        raise LookupError("ROLE_NOT_FOUND")

    return get_permission_codes_for_role(db, role.id)


def request_permission_edit_nonce(
    db: Session, *, role_id: int, current_user: User,
) -> tuple[str, datetime]:

    _get_editable_role(db, role_id)

    return generate_permission_edit_nonce(role_id)


def _atomic_update_role_permissions(
    db_path: str,
    *,
    role_id: int,
    current_user_id: int,
    expected_codes: set[str],
    new_codes: set[str],
) -> set[str]:

    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)

    try:
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        conn.execute("BEGIN IMMEDIATE")

        try:
            role_row = conn.execute("SELECT code FROM roles WHERE id = ?", (role_id,)).fetchone()

            if role_row is None:
                raise LookupError("ROLE_NOT_FOUND")

            if (role_row[0] or "").strip().upper() == "SUPER_ADMIN":
                raise ValueError(RolePermissionUpdateError.SUPER_ADMIN_NOT_EDITABLE.value)

            current_rows = conn.execute(
                "SELECT p.code FROM permissions p "
                "JOIN role_permissions rp ON rp.permission_id = p.id "
                "WHERE rp.role_id = ? AND p.active = 1",
                (role_id,),
            ).fetchall()
            current_codes = {row[0] for row in current_rows}

            if current_codes != expected_codes:
                raise ValueError(RolePermissionUpdateError.VERSION_CONFLICT.value)

            valid_map: dict[str, int] = {}

            if new_codes:
                placeholders = ",".join("?" for _ in new_codes)
                valid_rows = conn.execute(
                    f"SELECT code, id FROM permissions WHERE active = 1 AND code IN ({placeholders})",
                    list(new_codes),
                ).fetchall()
                valid_map = {row[0]: row[1] for row in valid_rows}

            if set(valid_map.keys()) != new_codes:
                raise ValueError(RolePermissionUpdateError.UNKNOWN_PERMISSION_CODE.value)

            conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))

            for code in new_codes:
                conn.execute(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    (role_id, valid_map[code]),
                )

            conn.execute(
                "INSERT INTO audit_logs "
                "(company_id, user_id, action, entity, entity_id, description, ip_address) "
                "VALUES (NULL, ?, ?, ?, ?, ?, NULL)",
                (
                    current_user_id,
                    "ROLE_PERMISSIONS_UPDATED",
                    "role_permissions",
                    str(role_id),
                    "before=[{}] after=[{}]".format(
                        ",".join(sorted(expected_codes)),
                        ",".join(sorted(new_codes)),
                    ),
                ),
            )

            conn.execute("COMMIT")

            return new_codes

        except Exception:
            conn.execute("ROLLBACK")
            raise

    finally:
        conn.close()


def update_role_permissions(
    db: Session,
    *,
    role_id: int,
    current_user: User,
    expected_codes: set[str],
    new_codes: set[str],
    nonce: str | None,
    recent_auth_token: str | None,
    db_path: str | None = None,
) -> set[str]:
    """
    라우터에서 호출하는 진입점. `db`(ORM Session)는 사전 존재/역할
    확인에만 쓰이고, 실제 원자적 CAS 갱신은 별도 raw sqlite3 연결로
    수행한다(둘 다 같은 파일을 잠그므로 순서만 보장되면 안전하다).

    `db_path`는 테스트 전용 오버라이드다(app/domains/account_registration
    /service.py::register()가 같은 목적으로 쓰는 관례와 동일) — 지정하지
    않으면 `resolve_sqlite_path()`로 실제 settings.DATABASE_URL을 따른다.
    """

    _get_editable_role(db, role_id)

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise ValueError(RolePermissionUpdateError.RECENT_AUTH_REQUIRED.value)

    nonce_status = verify_nonce(role_id, nonce)

    if nonce_status != PermissionEditNonceStatus.VALID:
        raise ValueError(RolePermissionUpdateError.NONCE_INVALID.value)

    result = _atomic_update_role_permissions(
        db_path or resolve_sqlite_path(),
        role_id=role_id,
        current_user_id=current_user.id,
        expected_codes=set(expected_codes),
        new_codes=set(new_codes),
    )

    mark_nonce_consumed(role_id)

    return result


__all__ = [
    "RolePermissionUpdateError",
    "get_role_permission_codes",
    "request_permission_edit_nonce",
    "update_role_permissions",
]
