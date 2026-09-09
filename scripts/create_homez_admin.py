"""
=========================================================
Homez OS

File : scripts/create_homez_admin.py

HOMEZ 최초 관리자(SUPER_ADMIN) 계정 생성 도구 (2026-07-30).

실제 homez.db에 사람이 로그인 가능한 계정이 0건이라는 문제를 해결하기
위한 도구다. 비밀번호를 다루는 민감한 작업이므로 다음을 강제한다.

  - 기본 실행은 항상 dry-run(시뮬레이션)이다 — `--confirm` 플래그 +
    화면에 표시된 정확한 확인 문구를 대화형으로 입력해야만 실제
    INSERT가 일어난다.
  - 비밀번호는 인자(argv)로 받지 않는다 — `getpass`로만 2회 입력받아
    일치를 확인한다.
  - 비밀번호·해시는 화면·로그·파일·명령행 어디에도 기록하지 않는다.
    성공 시에도 user id만 출력한다.
  - `app/core/security.py::hash_password`를 그대로 재사용한다(새
    해싱 로직을 만들지 않는다).
  - 계정 INSERT + 감사 로그 INSERT를 하나의 Transaction으로 묶고,
    실패 시 전체 rollback한다.
  - 기존 계정(동일 username 또는 email)은 덮어쓰지 않는다 — 있으면
    거부한다.

사용 예(dry-run, 기본):
    python scripts/create_homez_admin.py

실제 생성(별도 승인 후에만):
    python scripts/create_homez_admin.py --confirm
=========================================================
"""

from __future__ import annotations

import argparse
import getpass
import os
import string
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DEFAULT_DB_PATH = os.path.join(REPO_ROOT, "homez.db")
SUPER_ADMIN_CODE = "SUPER_ADMIN"


# --------------------------------------------------
# 순수 검증 함수 (테스트에서 직접 호출)
# --------------------------------------------------

def normalize_username(raw: str) -> str:

    return raw.strip()


def normalize_email(raw: str) -> str:

    return raw.strip().lower()


def validate_password_policy(password: str, settings) -> list[str]:
    """
    `app/core/config.py`에 이미 정의된 정책(PASSWORD_MIN_LENGTH 등)을
    그대로 재사용한다 — 이 도구가 별도 정책을 새로 만들지 않는다.
    """

    errors: list[str] = []

    if len(password) < settings.PASSWORD_MIN_LENGTH:
        errors.append(f"비밀번호는 최소 {settings.PASSWORD_MIN_LENGTH}자 이상이어야 합니다.")

    if len(password) > settings.PASSWORD_MAX_LENGTH:
        errors.append(f"비밀번호는 최대 {settings.PASSWORD_MAX_LENGTH}자를 넘을 수 없습니다.")

    if settings.PASSWORD_REQUIRE_UPPER and not any(c.isupper() for c in password):
        errors.append("비밀번호에 대문자를 최소 1개 포함해야 합니다.")

    if settings.PASSWORD_REQUIRE_LOWER and not any(c.islower() for c in password):
        errors.append("비밀번호에 소문자를 최소 1개 포함해야 합니다.")

    if settings.PASSWORD_REQUIRE_NUMBER and not any(c.isdigit() for c in password):
        errors.append("비밀번호에 숫자를 최소 1개 포함해야 합니다.")

    if settings.PASSWORD_REQUIRE_SPECIAL and not any(
        c in string.punctuation for c in password
    ):
        errors.append("비밀번호에 특수문자를 최소 1개 포함해야 합니다.")

    return errors


def find_super_admin_role(db):
    """
    실제 시딩된 역할 코드는 대문자("SUPER_ADMIN")이지만, 대소문자
    표기가 다른 환경도 안전하게 인식하도록 대소문자 무시로 조회한다
    (app/core/authorization.py::has_role()의 대소문자 무시 정책과
    동일한 이유 — 2026-07-30 발견 사례 재발 방지).
    """

    from app.domains.role.model import Role

    for role in db.query(Role).all():
        if role.code.strip().upper() == SUPER_ADMIN_CODE:
            return role

    return None


def account_exists(db, username: str, email: str) -> str | None:
    """중복이면 사유 문자열, 아니면 None을 반환한다."""

    from app.domains.user.model import User

    if db.query(User).filter(User.username == username).first() is not None:
        return f"username '{username}'이(가) 이미 존재합니다."

    if db.query(User).filter(User.email == email).first() is not None:
        return f"email '{email}'이(가) 이미 존재합니다."

    return None


class AdminCreationResult:

    def __init__(
        self, success: bool, user_id: int | None = None,
        errors: list[str] | None = None, dry_run: bool = True,
    ):
        self.success = success
        self.user_id = user_id
        self.errors = errors or []
        self.dry_run = dry_run


def create_admin_account(
    db,
    *,
    username: str,
    email: str,
    password: str,
    name: str | None = None,
    dry_run: bool = True,
) -> AdminCreationResult:
    """
    사전 검증(중복/역할 부재/비밀번호 정책)을 전부 통과해야만 실제
    INSERT를 시도한다. `dry_run=True`(기본)이면 검증만 수행하고 어떤
    쓰기도 하지 않는다.

    계정 INSERT + 감사 로그 INSERT는 같은 Session(단일 Transaction)
    안에서 이루어지며, 어느 한쪽이라도 실패하면 전체 rollback한다.
    """

    from app.core.config import settings
    from app.core.security import hash_password
    from app.domains.user.model import User

    username = normalize_username(username)
    email = normalize_email(email)

    errors: list[str] = []

    if not username:
        errors.append("username이 비어 있습니다.")

    if not email or "@" not in email:
        errors.append("email 형식이 올바르지 않습니다.")

    errors.extend(validate_password_policy(password, settings))

    duplicate_reason = account_exists(db, username, email) if username and email else None
    if duplicate_reason:
        errors.append(duplicate_reason)

    role = find_super_admin_role(db)
    if role is None:
        errors.append(
            f"'{SUPER_ADMIN_CODE}' 역할이 존재하지 않습니다 — 계정을 "
            "생성할 수 없습니다(역할이 먼저 시딩되어야 합니다).",
        )

    if errors:
        return AdminCreationResult(success=False, errors=errors, dry_run=dry_run)

    if dry_run:
        return AdminCreationResult(success=True, user_id=None, dry_run=True)

    try:
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            name=name,
            role_id=role.id,
            is_active=True,
        )
        db.add(user)
        db.flush()  # user.id 확보(아직 commit 아님)

        _insert_audit_log_no_commit(db, user_id=user.id)

        db.commit()

        return AdminCreationResult(success=True, user_id=user.id, dry_run=False)

    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # 예외 메시지 전체를 노출하지 않는다(바인딩된 파라미터 표현에
        # 해시 등이 섞여 나올 수 있는 드라이버 구현을 배제하기 위한
        # 방어적 조치) — 예외 종류만 보고한다.
        return AdminCreationResult(
            success=False,
            errors=[f"계정 생성 중 예기치 않은 오류가 발생했습니다({type(exc).__name__})."],
            dry_run=False,
        )


def _insert_audit_log_no_commit(db, *, user_id: int) -> None:
    """
    실제 `audit_logs` 테이블 컬럼(id/company_id/user_id/action/entity/
    entity_id/description/ip_address)에 맞춰 직접 INSERT한다 —
    `app/domains/audit/**`는 아직 빈 스캐폴딩이라 별도 ORM Model이
    없다(이 도구 범위에서 그 Domain 전체를 새로 만들지 않는다).
    """

    from sqlalchemy import text

    db.execute(
        text(
            "INSERT INTO audit_logs "
            "(company_id, user_id, action, entity, entity_id, description, ip_address) "
            "VALUES (NULL, :user_id, :action, :entity, :entity_id, :description, NULL)",
        ),
        {
            "user_id": user_id,
            "action": "CREATE_ADMIN_ACCOUNT",
            "entity": "users",
            "entity_id": str(user_id),
            "description": "Initial SUPER_ADMIN account created via scripts/create_homez_admin.py",
        },
    )


# --------------------------------------------------
# CLI
# --------------------------------------------------

def _make_session(db_path: str):

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)

    return Session()


def _prompt_password() -> str | None:

    first = getpass.getpass("비밀번호: ")
    second = getpass.getpass("비밀번호 확인: ")

    if first != second:
        print("오류: 두 비밀번호가 일치하지 않습니다.")
        return None

    return first


def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(
        description="HOMEZ 최초 관리자(SUPER_ADMIN) 계정 생성 도구. "
        "--confirm 없이는 항상 dry-run(시뮬레이션)만 수행한다.",
    )
    parser.add_argument(
        "--db-path", default=DEFAULT_DB_PATH,
        help="대상 SQLite 파일 경로(기본: 저장소 루트의 homez.db).",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="실제 계정을 생성한다(기본은 dry-run). 이 플래그만으로는 "
        "부족하며, 실행 중 화면에 표시되는 확인 문구를 정확히 입력해야 "
        "실제 INSERT가 일어난다.",
    )
    args = parser.parse_args(argv)

    # DATABASE_URL 전체가 아니라 파일 경로만 표시한다(비밀 정보 없음을
    # 전제하되, 이 값 자체도 최소한만 노출한다는 원칙을 지킨다).
    print(f"대상 DB 파일: {args.db_path}")
    print(f"실행 모드: {'실제 생성(--confirm)' if args.confirm else 'dry-run(시뮬레이션만)'}")

    if not os.path.exists(args.db_path):
        print(f"오류: 대상 DB 파일이 존재하지 않습니다: {args.db_path}")
        return 1

    username = normalize_username(input("username: "))
    email = normalize_email(input("email: "))
    name = input("표시 이름(선택, 그냥 Enter 가능): ").strip() or None

    password = _prompt_password()
    if password is None:
        return 1

    db = _make_session(args.db_path)

    try:
        # 1단계: 항상 dry-run으로 먼저 검증(실제 --confirm 여부와 무관).
        dry_result = create_admin_account(
            db, username=username, email=email, password=password,
            name=name, dry_run=True,
        )

        if not dry_result.success:
            print("검증 실패 — 계정을 생성하지 않았습니다:")
            for err in dry_result.errors:
                print(f"  - {err}")
            return 1

        role = find_super_admin_role(db)

        print()
        print("=== 실행 직전 확인 ===")
        print(f"대상 DB: {args.db_path}")
        print(f"username: {username}")
        print(f"role: {role.code} (id={role.id})")
        print()

        if not args.confirm:
            print("dry-run 모드입니다 — 실제로 계정을 생성하려면 "
                  "--confirm 플래그와 함께 다시 실행하세요.")
            return 0

        expected_phrase = f"계정을 생성합니다 {username}"
        typed = input(
            f"계속하려면 정확히 다음 문구를 입력하세요: \"{expected_phrase}\"\n> ",
        )

        if typed != expected_phrase:
            print("확인 문구가 일치하지 않습니다 — 계정을 생성하지 않았습니다.")
            return 1

        result = create_admin_account(
            db, username=username, email=email, password=password,
            name=name, dry_run=False,
        )

        if not result.success:
            print("계정 생성 실패 — 전체 rollback되었습니다:")
            for err in result.errors:
                print(f"  - {err}")
            return 1

        print(f"계정 생성 성공. user_id={result.user_id}")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "normalize_username",
    "normalize_email",
    "validate_password_policy",
    "find_super_admin_role",
    "account_exists",
    "create_admin_account",
    "AdminCreationResult",
    "main",
]
