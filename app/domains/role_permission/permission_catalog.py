"""
=========================================================
Homez OS

File : app/domains/role_permission/permission_catalog.py

Gate T(2026-08-10) — Permission 편집 UI가 쓰는 "카탈로그" 메타데이터.

`permissions` 테이블 자체는 code/name/description/active만 가진다
(app/domains/permission/model.py). 여기서는 이미 존재하는 행에
한국어/영어 표시 이름, 짧은 평문 영향 설명, "위험한 permission" 여부,
업무 영역 그룹을 덧붙인다 — DB 스키마를 바꾸지 않고 순수 코드 상수로
관리한다(그룹/설명이 자주 바뀔 수 있는 UX 문구이기 때문).

Presets(조회 전용/상품 담당자/승인 관리자/전체 운영 관리자)는 여기서
만들지 않는다 — 사용자 지시대로 "순수 UI 편의"이며 서버는 항상 개별
Permission 코드 목록만 검증한다(app/domains/role_permission/
admin_service.py::update_role_permissions 참고). 프리셋 계산은
app/web/console.js에서 이 카탈로그를 그대로 필터링해 클라이언트에서만
수행한다.

이 파일에 없는 코드(향후 새 Permission)는 자동으로 "기타" 그룹의
안전한 기본값(risky=False, 설명 없음)으로 표시된다 — 목록에 없다고
호출이 실패하지 않는다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domains.permission.model import Permission


@dataclass(frozen=True)
class PermissionMeta:

    group: str
    name_ko: str
    name_en: str
    impact_ko: str
    impact_en: str
    risky: bool = False
    # "view"/"create"/"edit"/"approve"/"submit"/"retry"/"export"/
    # "economics"/"update"/"delete"/"sync" 중 하나. Gate T Permission
    # 편집 화면의 Preset(조회 전용/상품 담당자/승인 관리자/전체 운영
    # 관리자)이 실제 Permission 코드 문자열을 하드코딩하지 않고 이
    # 분류만으로 필터링하도록 하기 위한 필드다(console.js가 개별
    # Permission 코드를 알고 있으면 서버 403과 별개인 "그림자 권한
    # 로직"이 생길 위험이 있다 — tests/test_listing_wizard_permission.py
    # ::test_console_js_does_not_hardcode_permission_codes가 이를 막는다).
    action: str = "other"


GROUP_LABELS: dict[str, dict[str, str]] = {
    "user": {"ko": "사용자", "en": "User"},
    "company": {"ko": "회사", "en": "Company"},
    "product": {"ko": "상품", "en": "Product"},
    "category": {"ko": "카테고리", "en": "Category"},
    "brand": {"ko": "브랜드", "en": "Brand"},
    "supplier": {"ko": "공급처", "en": "Supplier"},
    "marketplace": {"ko": "판매채널", "en": "Marketplace"},
    "inventory": {"ko": "재고", "en": "Inventory"},
    "order": {"ko": "주문", "en": "Order"},
    "listing_wizard": {"ko": "상품등록 마법사", "en": "Listing Wizard"},
    "other": {"ko": "기타", "en": "Other"},
}

# code -> PermissionMeta. 여기 없는 코드는 자동으로 "other" 그룹으로
# 폴백한다(_meta_for 참고) — 존재하지 않는 코드를 조회해도 예외를
# 던지지 않는다.
_PERMISSION_METADATA: dict[str, PermissionMeta] = {

    "USER_VIEW": PermissionMeta("user", "사용자 조회", "User View", "사용자 목록·상세를 조회할 수 있다.", "View user list and detail.", action="view"),
    "USER_CREATE": PermissionMeta("user", "사용자 생성", "User Create", "새 사용자를 만들 수 있다.", "Create new users.", action="create"),
    "USER_UPDATE": PermissionMeta("user", "사용자 수정", "User Update", "사용자 정보를 수정할 수 있다.", "Edit user information.", action="edit"),
    "USER_DELETE": PermissionMeta("user", "사용자 삭제", "User Delete", "사용자를 삭제할 수 있다 — 되돌리기 어렵다.", "Delete users — hard to undo.", risky=True, action="delete"),

    "COMPANY_VIEW": PermissionMeta("company", "회사 정보 조회", "Company View", "회사 정보를 조회할 수 있다.", "View company information.", action="view"),
    "COMPANY_UPDATE": PermissionMeta("company", "회사 정보 수정", "Company Update", "회사명 등 회사 정보를 수정할 수 있다.", "Edit company information.", risky=True, action="edit"),

    "PRODUCT_VIEW": PermissionMeta("product", "상품 조회", "Product View", "상품 목록·상세를 조회할 수 있다.", "View product list and detail.", action="view"),
    "PRODUCT_CREATE": PermissionMeta("product", "상품 생성", "Product Create", "새 상품을 등록할 수 있다.", "Create new products.", action="create"),
    "PRODUCT_UPDATE": PermissionMeta("product", "상품 수정", "Product Update", "상품 정보를 수정할 수 있다.", "Edit product information.", action="edit"),
    "PRODUCT_DELETE": PermissionMeta("product", "상품 삭제", "Product Delete", "상품을 삭제할 수 있다 — 되돌리기 어렵다.", "Delete products — hard to undo.", risky=True, action="delete"),

    "CATEGORY_VIEW": PermissionMeta("category", "카테고리 조회", "Category View", "카테고리를 조회할 수 있다.", "View categories.", action="view"),
    "CATEGORY_CREATE": PermissionMeta("category", "카테고리 생성", "Category Create", "카테고리를 만들 수 있다.", "Create categories.", action="create"),
    "CATEGORY_UPDATE": PermissionMeta("category", "카테고리 수정", "Category Update", "카테고리를 수정할 수 있다.", "Edit categories.", action="edit"),
    "CATEGORY_DELETE": PermissionMeta("category", "카테고리 삭제", "Category Delete", "카테고리를 삭제할 수 있다.", "Delete categories.", risky=True, action="delete"),

    "BRAND_VIEW": PermissionMeta("brand", "브랜드 조회", "Brand View", "브랜드를 조회할 수 있다.", "View brands.", action="view"),
    "BRAND_CREATE": PermissionMeta("brand", "브랜드 생성", "Brand Create", "브랜드를 만들 수 있다.", "Create brands.", action="create"),
    "BRAND_UPDATE": PermissionMeta("brand", "브랜드 수정", "Brand Update", "브랜드를 수정할 수 있다.", "Edit brands.", action="edit"),
    "BRAND_DELETE": PermissionMeta("brand", "브랜드 삭제", "Brand Delete", "브랜드를 삭제할 수 있다.", "Delete brands.", risky=True, action="delete"),

    "SUPPLIER_VIEW": PermissionMeta("supplier", "공급처 조회", "Supplier View", "공급처를 조회할 수 있다.", "View suppliers.", action="view"),
    "SUPPLIER_CREATE": PermissionMeta("supplier", "공급처 생성", "Supplier Create", "공급처를 등록할 수 있다.", "Create suppliers.", action="create"),
    "SUPPLIER_UPDATE": PermissionMeta("supplier", "공급처 수정", "Supplier Update", "공급처 정보를 수정할 수 있다.", "Edit suppliers.", action="edit"),
    "SUPPLIER_DELETE": PermissionMeta("supplier", "공급처 삭제", "Supplier Delete", "공급처를 삭제할 수 있다.", "Delete suppliers.", risky=True, action="delete"),

    "MARKETPLACE_VIEW": PermissionMeta("marketplace", "판매채널 조회", "Marketplace View", "판매채널 연동 상태를 조회할 수 있다.", "View marketplace connections.", action="view"),
    "MARKETPLACE_SYNC": PermissionMeta("marketplace", "판매채널 동기화", "Marketplace Sync", "판매채널과 데이터를 동기화할 수 있다.", "Sync data with marketplaces.", action="sync"),

    "STOCK_VIEW": PermissionMeta("inventory", "재고 조회", "Stock View", "재고 수량을 조회할 수 있다.", "View stock quantities.", action="view"),
    "STOCK_UPDATE": PermissionMeta("inventory", "재고 수정", "Stock Update", "재고 수량을 수정할 수 있다.", "Edit stock quantities.", action="edit"),

    "ORDER_VIEW": PermissionMeta("order", "주문 조회", "Order View", "주문을 조회할 수 있다.", "View orders.", action="view"),
    "ORDER_CREATE": PermissionMeta("order", "주문 생성", "Order Create", "주문을 생성할 수 있다.", "Create orders.", action="create"),
    "ORDER_UPDATE": PermissionMeta("order", "주문 수정", "Order Update", "주문을 수정할 수 있다.", "Edit orders.", action="edit"),
    "ORDER_DELETE": PermissionMeta("order", "주문 삭제", "Order Delete", "주문을 삭제할 수 있다.", "Delete orders.", risky=True, action="delete"),

    "listing_wizard.view": PermissionMeta("listing_wizard", "마법사 조회", "Wizard View", "위저드 목록·상세를 조회할 수 있다(금액 정보는 별도 권한).", "View wizard list/detail (amounts require a separate permission).", action="view"),
    "listing_wizard.create": PermissionMeta("listing_wizard", "마법사 생성", "Wizard Create", "새 위저드를 만들거나 복제할 수 있다.", "Create or clone wizards.", action="create"),
    "listing_wizard.edit": PermissionMeta("listing_wizard", "마법사 편집", "Wizard Edit", "위저드의 각 단계를 수정할 수 있다.", "Edit each wizard step.", action="edit"),
    "listing_wizard.approve": PermissionMeta("listing_wizard", "마법사 승인 열람", "Wizard Approval View", "승인 미리보기를 열람할 수 있다(실제 승인은 SUPER_ADMIN 전용).", "View approval preview (actual approval is SUPER_ADMIN-only).", risky=True, action="approve"),
    "listing_wizard.submit": PermissionMeta("listing_wizard", "마법사 제출", "Wizard Submit", "승인된 위저드를 실제 판매채널에 제출할 수 있다 — 실거래에 영향.", "Submit approved wizards to real marketplaces — affects live listings.", risky=True, action="submit"),
    "listing_wizard.retry": PermissionMeta("listing_wizard", "마법사 재시도", "Wizard Retry", "부분 실패한 채널만 재시도할 수 있다.", "Retry partially failed channels.", action="retry"),
    "listing_wizard.export": PermissionMeta("listing_wizard", "마법사 내보내기", "Wizard Export", "위저드 데이터를 CSV 등으로 내보낼 수 있다.", "Export wizard data (e.g. CSV).", action="export"),
    "listing_wizard.economics_view": PermissionMeta("listing_wizard", "원가·마진 열람", "Cost/Margin View", "원가·판매가·마진 등 금액 필드를 열람할 수 있다 — 민감한 재무 정보다.", "View cost/price/margin fields — sensitive financial data.", risky=True, action="economics"),
}


def _meta_for(code: str, fallback_name: str) -> PermissionMeta:

    meta = _PERMISSION_METADATA.get(code)

    if meta is not None:
        return meta

    return PermissionMeta(
        group="other",
        name_ko=fallback_name,
        name_en=fallback_name,
        impact_ko="",
        impact_en="",
        risky=False,
    )


def build_permission_catalog(db: Session) -> list[dict]:
    """
    `permissions` 테이블의 활성 행 전체를 업무 영역별로 그룹화해
    반환한다. 순서: GROUP_LABELS 정의 순서, 그룹 내부는 code 오름차순.
    """

    rows = (
        db.query(Permission)
        .filter(Permission.active.is_(True))
        .order_by(Permission.code.asc())
        .all()
    )

    by_group: dict[str, list[dict]] = {key: [] for key in GROUP_LABELS}

    for row in rows:

        meta = _meta_for(row.code, row.name)
        group_key = meta.group if meta.group in GROUP_LABELS else "other"

        by_group[group_key].append({
            "code": row.code,
            "name_ko": meta.name_ko,
            "name_en": meta.name_en,
            "impact_ko": meta.impact_ko,
            "impact_en": meta.impact_en,
            "risky": meta.risky,
            "action": meta.action,
        })

    catalog = []

    for group_key, label in GROUP_LABELS.items():

        items = by_group[group_key]

        if not items:
            continue

        catalog.append({
            "group": group_key,
            "label_ko": label["ko"],
            "label_en": label["en"],
            "permissions": items,
        })

    return catalog


__all__ = [
    "PermissionMeta",
    "GROUP_LABELS",
    "build_permission_catalog",
]
