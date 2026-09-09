"""
=========================================================
Homez OS

File : app/core/first_admin_setup.py

HOMEZ Desktop 최초 관리자(SUPER_ADMIN) 계정 — 원자적 생성.

`SELECT COUNT(*) FROM users`로 0임을 확인한 뒤 무방비 INSERT하는
TOCTOU 구조를 금지한다는 요구에 따라, `BEGIN IMMEDIATE`로 즉시 쓰기
잠금을 확보한 다음에만 확인·삽입을 수행한다 — 확인과 삽입이 하나의
Transaction 안에서 원자적으로 일어난다. 동시에 두 요청이 들어오면
하나는 이 잠금을 기다렸다가(그 사이 잠금을 먼저 확보한 요청이 이미
커밋한) users 행을 보고 정직하게 ALREADY_COMPLETED를 반환한다 —
정확히 하나만 성공한다.

SQLAlchemy ORM Session을 쓰지 않고 표준 `sqlite3` 모듈을 직접
사용한다(`isolation_level=None`으로 드라이버 자동 트랜잭션을 끄고
`BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`을 직접 제어하기 위함 — 이
프로젝트의 Migration/Migration 테스트가 이미 이 방식을 쓰고 있어
일관된 패턴이다).

2026-08-02 CTO 보안 보완 Gate R2: 최초 관리자 계정은 이제 회사명을
함께 입력받아, Company INSERT + User INSERT(그 Company의 id로
company_id 설정) + audit_log INSERT를 전부 이 하나의 Transaction
안에서 처리한다 — "관리자는 있는데 소속 회사가 없는" 상태(2026-08-02
실제 운영 DB에서 발견된 결함, sin9484@gmail.com 계정의 company_id가
NULL이고 companies 테이블이 비어 있던 사례)가 신규 설치에서는 아예
발생할 수 없도록 구조적으로 막는다.
=========================================================
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from enum import Enum

from app.core.config import settings

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_BUSY_TIMEOUT_MS = 15000


def resolve_sqlite_path(database_url: str | None = None) -> str:
    """
    `settings.DATABASE_URL`(예: "sqlite:///./homez.db")에서 실제 파일
    경로를 계산한다. 상대 경로는 현재 작업 디렉터리가 아니라 저장소
    루트 기준으로 해석한다(`scripts/create_homez_admin.py`와 동일한
    이유 — 실행 시점의 CWD에 의존하지 않기 위함).
    """

    url = database_url or settings.DATABASE_URL
    prefix = "sqlite:///"

    if not url.startswith(prefix):
        raise ValueError(
            "첫 관리자 원자적 생성은 SQLite DATABASE_URL만 지원합니다 "
            f"(현재: {url.split(':', 1)[0]}...).",
        )

    raw_path = url[len(prefix):]

    if os.path.isabs(raw_path):
        return raw_path

    return os.path.normpath(os.path.join(REPO_ROOT, raw_path))


class FirstAdminSetupStatus(str, Enum):

    SUCCESS = "SUCCESS"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"


@dataclass(frozen=True)
class FirstAdminSetupResult:

    status: FirstAdminSetupStatus
    user_id: int | None = None
    company_id: int | None = None


def atomic_create_first_admin(
    db_path: str,
    *,
    username: str,
    email: str,
    password_hash: str,
    role_id: int,
    company_name: str,
    name: str | None = None,
) -> FirstAdminSetupResult:

    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)

    try:
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        conn.execute("BEGIN IMMEDIATE")

        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

        if count != 0:
            conn.execute("ROLLBACK")
            return FirstAdminSetupResult(status=FirstAdminSetupStatus.ALREADY_COMPLETED)

        try:
            company_cursor = conn.execute(
                "INSERT INTO companies "
                "(name, business_number, ceo, phone, email, address, active) "
                "VALUES (?, NULL, NULL, NULL, NULL, NULL, 1)",
                (company_name,),
            )
            company_id = company_cursor.lastrowid

            cursor = conn.execute(
                "INSERT INTO users "
                "(company_id, role_id, username, email, password, name, phone, active) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 1)",
                (company_id, role_id, username, email, password_hash, name),
            )
            user_id = cursor.lastrowid

            conn.execute(
                "INSERT INTO audit_logs "
                "(company_id, user_id, action, entity, entity_id, description, ip_address) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    company_id,
                    user_id,
                    "CREATE_FIRST_ADMIN_ACCOUNT",
                    "users",
                    str(user_id),
                    "Initial SUPER_ADMIN account and owning Company created together "
                    "via HOMEZ Desktop first-run setup screen",
                ),
            )

            conn.execute("COMMIT")

            return FirstAdminSetupResult(
                status=FirstAdminSetupStatus.SUCCESS, user_id=user_id,
                company_id=company_id,
            )

        except sqlite3.IntegrityError:
            # username/email UNIQUE 제약 위반 — 동시 요청 경쟁의 패자,
            # 또는 (이론상 불가능해야 하지만) 데이터 정합성 문제.
            # 어느 쪽이든 이미 계정이 존재한다는 뜻이므로 동일하게
            # 처리한다(회사명 UNIQUE 충돌도 여기서 함께 잡히지만, 신규
            # 설치에서 companies가 비어 있다는 전제상 실질적으로는
            # 거의 발생하지 않는다).
            conn.execute("ROLLBACK")
            return FirstAdminSetupResult(status=FirstAdminSetupStatus.ALREADY_COMPLETED)

        except Exception:
            conn.execute("ROLLBACK")
            raise

    finally:
        conn.close()


__all__ = [
    "resolve_sqlite_path",
    "FirstAdminSetupStatus",
    "FirstAdminSetupResult",
    "atomic_create_first_admin",
]
