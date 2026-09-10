"""
=========================================================
Homez OS

File : app/database/seed.py
Version : 1.1.0

Default Seed Data

V7 Live Gate 4 재작업(2026-08-16) — 결함 2: 이 파일의 `initialize_seed()`가
이전까지 bootstrap/FastAPI startup 어디에도 연결돼 있지 않아, 신규 설치
DB는 SUPER_ADMIN 등 역할이 전혀 없었다. `seed_environment(db_path)`가
공식 진입점(app/desktop/main.py)이 Migration 적용 직후 호출하는 새 진입점
이다 — 자체 SQLAlchemy 엔진을 열어 단일 Transaction으로 시딩하고
닫는다(app/database/bootstrap.py는 의도적으로 SQLAlchemy 의존성을 두지
않으므로, seeding 오케스트레이션은 이 파일에 둔다).

멱등성 계약: 이미 존재하는 role/permission 코드는 절대 건드리지 않는다
(존재 여부만 확인, 없을 때만 add) — 기존 값을 덮어쓰거나 삭제하지 않는다.
회사·사용자·초대코드·복구코드는 이 파일 어디에서도 생성하지 않는다.
=========================================================
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.domains.role.model import Role
from app.domains.permission.model import Permission

logger = logging.getLogger(__name__)


DEFAULT_ROLES = [

    {
        "name": "Super Administrator",
        "code": "SUPER_ADMIN",
    },

    {
        "name": "Administrator",
        "code": "ADMIN",
    },

    {
        "name": "Manager",
        "code": "MANAGER",
    },

    {
        "name": "Staff",
        "code": "STAFF",
    },

    {
        "name": "Viewer",
        "code": "VIEWER",
    },

]


DEFAULT_PERMISSIONS = [

    # =============================
    # User
    # =============================

    ("USER_VIEW", "User View"),
    ("USER_CREATE", "User Create"),
    ("USER_UPDATE", "User Update"),
    ("USER_DELETE", "User Delete"),

    # =============================
    # Company
    # =============================

    ("COMPANY_VIEW", "Company View"),
    ("COMPANY_UPDATE", "Company Update"),

    # =============================
    # Product
    # =============================

    ("PRODUCT_VIEW", "Product View"),
    ("PRODUCT_CREATE", "Product Create"),
    ("PRODUCT_UPDATE", "Product Update"),
    ("PRODUCT_DELETE", "Product Delete"),

    # =============================
    # Category
    # =============================

    ("CATEGORY_VIEW", "Category View"),
    ("CATEGORY_CREATE", "Category Create"),
    ("CATEGORY_UPDATE", "Category Update"),
    ("CATEGORY_DELETE", "Category Delete"),

    # =============================
    # Brand
    # =============================

    ("BRAND_VIEW", "Brand View"),
    ("BRAND_CREATE", "Brand Create"),
    ("BRAND_UPDATE", "Brand Update"),
    ("BRAND_DELETE", "Brand Delete"),

    # =============================
    # Supplier
    # =============================

    ("SUPPLIER_VIEW", "Supplier View"),
    ("SUPPLIER_CREATE", "Supplier Create"),
    ("SUPPLIER_UPDATE", "Supplier Update"),
    ("SUPPLIER_DELETE", "Supplier Delete"),

    # =============================
    # Marketplace
    # =============================

    ("MARKETPLACE_VIEW", "Marketplace View"),
    ("MARKETPLACE_SYNC", "Marketplace Sync"),

    # =============================
    # Inventory
    # =============================

    ("STOCK_VIEW", "Stock View"),
    ("STOCK_UPDATE", "Stock Update"),

    # =============================
    # Order
    # =============================

    ("ORDER_VIEW", "Order View"),
    ("ORDER_CREATE", "Order Create"),
    ("ORDER_UPDATE", "Order Update"),
    ("ORDER_DELETE", "Order Delete"),

    # =============================
    # Listing Wizard (Gate S, 2026-08-10)
    #
    # 코드 값은 app/domains/marketplace_listing/listing_wizard_permissions.py
    # 의 실제 런타임 상수(dotted-lowercase)와 반드시 byte-for-byte 일치해야
    # 한다 — ListingWizardPermissionGuard/user_can()이 이 문자열을 그대로
    # 비교한다. Gate T에서 UPPER_SNAKE로 잘못 시딩했던 결함을 사용자 승인
    # 하에 수정함(2026-08-10).
    # =============================

    ("listing_wizard.view", "Listing Wizard View"),
    ("listing_wizard.create", "Listing Wizard Create"),
    ("listing_wizard.edit", "Listing Wizard Edit"),
    ("listing_wizard.approve", "Listing Wizard Approve"),
    ("listing_wizard.submit", "Listing Wizard Submit"),
    ("listing_wizard.retry", "Listing Wizard Retry"),
    ("listing_wizard.export", "Listing Wizard Export"),
    ("listing_wizard.economics_view", "Listing Economics View"),

    # =============================
    # Sensitive Data (Phase 11, 2026-09-10)
    #
    # HOMEZ_USER_OPERATION_SETTINGS.md 11번 — "개인정보 조회 권한을
    # 별도로 설정한다." SUPER_ADMIN은 app/core/permission_check.py::
    # is_super_admin() 단락 평가로 이 코드가 role_permissions에 없어도
    # 항상 통과한다 — 이 권한은 SUPER_ADMIN이 아닌 역할에게 개인정보
    # 원문 조회를 개별적으로 허용할 때만 의미가 생긴다(현재
    # ROLE_PERMISSION_MAP에는 어떤 비-SUPER_ADMIN 역할에도 이 코드를
    # 매핑해두지 않았다 — 기본값은 "아무도 못 봄", 필요할 때 관리자가
    # 화면에서 개별 부여).
    # =============================

    ("VIEW_SENSITIVE_DATA", "View Sensitive Data"),

]
def seed_roles(db: Session) -> int:
    """
    DEFAULT_ROLES 중 아직 없는 code만 추가한다(멱등, 기존 역할 불변).

    V7 Live Gate 4 재작업 — 이전에는 `active`(NOT NULL, DEFAULT 없음)를
    채우지 않아 첫 커밋에서 IntegrityError로 실패했다. 이 함수는 더 이상
    자체적으로 commit하지 않는다 — 호출자(`initialize_seed`를 거치는
    `seed_environment`)가 roles/permissions/role_permissions 시딩
    전체를 단일 Transaction으로 묶어 commit/rollback한다.

    반환값: 새로 추가된 역할 수(감사 로그·테스트 검증용, Secret 없음).
    """

    added = 0

    for item in DEFAULT_ROLES:

        role = db.query(Role).filter(
            Role.code == item["code"]
        ).first()

        if role is None:

            db.add(
                Role(
                    name=item["name"],
                    code=item["code"],
                    active=True,
                )
            )
            added += 1

    return added


def seed_permissions(db: Session) -> int:
    """
    DEFAULT_PERMISSIONS 중 아직 없는 code만 추가한다(멱등, 기존 권한 불변).

    `seed_roles()`와 동일하게 자체 commit하지 않는다.

    반환값: 새로 추가된 Permission 수(감사 로그·테스트 검증용, Secret 없음).
    """

    added = 0

    for code, name in DEFAULT_PERMISSIONS:

        permission = db.query(
            Permission
        ).filter(
            Permission.code == code
        ).first()

        if permission is None:

            db.add(
                Permission(
                    name=name,
                    code=code,
                )
            )
            added += 1

    return added


from app.database.seed_role_permission import seed_role_permissions  # noqa: E402


def initialize_seed(db: Session) -> dict[str, int]:
    """
    최초 실행 시 최소한의 기준 데이터(역할·권한)를 보장하는 공식 진입점.

    - idempotent: 이미 있는 role/permission code는 절대 건드리지 않는다.
    - 회사·사용자·초대코드·복구코드는 생성하지 않는다 — 최초 실제
      관리자 계정은 사용자의 UI 입력을 통해서만 생성된다(이 함수의
      책임 밖).
    - commit은 여기서 하지 않는다 — `seed_environment()`가 단일
      Transaction으로 감싼다(중간 실패 시 무엇도 반영되지 않아야 하므로).

    반환값: {"roles_added": N, "permissions_added": M} — 감사 로그용.
    """

    roles_added = seed_roles(db)
    permissions_added = seed_permissions(db)

    # seed_role_permissions()는 SUPER_ADMIN 등 role↔permission 연결을
    # 시도한다 — 기존 role/permission 행은 건드리지 않는다(멱등).
    seed_role_permissions(db)

    return {
        "roles_added": roles_added,
        "permissions_added": permissions_added,
    }


def seed_environment(db_path: Path) -> dict[str, int]:
    """
    공식 bootstrap 흐름(app/desktop/main.py)이 Migration 적용 직후
    호출하는 진입점 — 주어진 db_path에 대해 `initialize_seed()`를 자체
    단발성 SQLAlchemy 엔진으로 열어 단일 Transaction으로 실행한다.

    이 엔진은 app/database/session.py의 전역 `engine`과는 별개다(전역
    상태에 의존하지 않아, 테스트가 임의의 임시 db_path로 직접 호출할 수
    있다) — 다만 공식 호출자(main.py)는 항상 bootstrap이 실제로 사용한
    것과 동일한 db_path를 넘기므로, 결과적으로 같은 파일을 시딩한다.

    실패 시 전체 rollback 후 예외를 다시 던진다 — fail-closed 여부는
    호출자가 결정한다. 예외 메시지는 이 시딩 로직이 다루는 데이터
    (하드코딩된 역할/권한 이름 상수)에는 애초에 Secret이 없으므로
    로그에 그대로 남긴다.
    """

    engine = create_engine(f"sqlite:///{db_path}")
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    try:

        result = initialize_seed(db)
        db.commit()

        logger.info(
            "기준 데이터 시딩 완료: roles_added=%d, permissions_added=%d",
            result["roles_added"],
            result["permissions_added"],
        )

        return result

    except Exception as exc:

        db.rollback()
        logger.error("기준 데이터 시딩 실패, 전체 rollback: %s", exc)
        raise

    finally:

        db.close()
        engine.dispose()


__all__ = [
    "DEFAULT_ROLES",
    "DEFAULT_PERMISSIONS",
    "seed_roles",
    "seed_permissions",
    "initialize_seed",
    "seed_environment",
]