"""
=========================================================
Homez OS

File : app/database/seed_role_permission.py
Version : 1.0.0

Role Permission Seed
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.role.model import Role
from app.domains.permission.model import Permission


ROLE_PERMISSION_MAP = {

    "SUPER_ADMIN": [
        "*",
    ],

    "ADMIN": [

        "USER_VIEW",
        "USER_CREATE",
        "USER_UPDATE",

        "COMPANY_VIEW",
        "COMPANY_UPDATE",

        "PRODUCT_VIEW",
        "PRODUCT_CREATE",
        "PRODUCT_UPDATE",
        "PRODUCT_DELETE",

        "CATEGORY_VIEW",
        "CATEGORY_CREATE",
        "CATEGORY_UPDATE",
        "CATEGORY_DELETE",

        "BRAND_VIEW",
        "BRAND_CREATE",
        "BRAND_UPDATE",
        "BRAND_DELETE",

        "SUPPLIER_VIEW",
        "SUPPLIER_CREATE",
        "SUPPLIER_UPDATE",
        "SUPPLIER_DELETE",

        "ORDER_VIEW",
        "ORDER_CREATE",
        "ORDER_UPDATE",
        "ORDER_DELETE",

        "STOCK_VIEW",
        "STOCK_UPDATE",

        "MARKETPLACE_VIEW",
        "MARKETPLACE_SYNC",

    ],

    "MANAGER": [

        "PRODUCT_VIEW",
        "PRODUCT_CREATE",
        "PRODUCT_UPDATE",

        "CATEGORY_VIEW",
        "CATEGORY_UPDATE",

        "BRAND_VIEW",
        "BRAND_UPDATE",

        "SUPPLIER_VIEW",

        "ORDER_VIEW",
        "ORDER_CREATE",
        "ORDER_UPDATE",

        "STOCK_VIEW",
        "STOCK_UPDATE",

    ],

    "STAFF": [

        "PRODUCT_VIEW",

        "CATEGORY_VIEW",

        "BRAND_VIEW",

        "SUPPLIER_VIEW",

        "ORDER_VIEW",

        "STOCK_VIEW",

    ],

    "VIEWER": [

        "PRODUCT_VIEW",

        "CATEGORY_VIEW",

        "BRAND_VIEW",

        "SUPPLIER_VIEW",

        "ORDER_VIEW",

        "STOCK_VIEW",

    ],

}
def seed_role_permissions(db: Session):
    """
    V7 Live Gate 4 재작업 — `initialize_seed()`가 단일 Transaction으로
    묶어 처리하도록 더 이상 자체 commit하지 않는다(호출자가 commit).

    범위 밖 기존 결함(수정하지 않음, 코디네이터에게 별도 보고): 아래
    `role.permissions = ...` 대입은 Role 모델에 `permissions` 관계가
    매핑되어 있지 않아(`role_permissions`만 존재) 실제로는 아무 것도
    persist하지 않는 조용한 no-op이다. 다만 ADMIN/SUPER_ADMIN 권한
    게이트(`app/domains/marketplace_listing/listing_wizard_permission_
    guard.py`)는 role 문자열 비교를 최우선으로 통과시키고 이 Permission
    조회는 그 외 역할에만 "추가로" 적용되도록 이미 설계돼 있어(주석에
    "아직 시딩되지 않음"을 전제로 명시), 이번 승인 범위(SUPER_ADMIN
    시딩·최초 관리자 설정)에는 영향이 없다.
    """

    roles = db.query(Role).all()

    permissions = db.query(Permission).all()

    permission_dict = {
        permission.code: permission
        for permission in permissions
    }

    for role in roles:

        if role.code not in ROLE_PERMISSION_MAP:
            continue

        codes = ROLE_PERMISSION_MAP[role.code]

        if "*" in codes:

            role.permissions = permissions

        else:

            role.permissions = [

                permission_dict[code]

                for code in codes

                if code in permission_dict

            ]