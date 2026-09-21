"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/coupang_submission_contract.py

2026-08-30 V7 상품등록 파이프라인 안정화 — 단일 제출 계약(Single
Submission Contract). 실제 Live 검증(위저드 4~7, 2026-08-29)에서
vendorUserId·택배사코드·구매옵션 단위가 서로 다른 시점에 하나씩
실패한 근본 원인은, 7단계 사전검사(listing_wizard_precheck.py)와
9~10단계 실제 전송 직전 Payload 생성(coupang_live_payload.py)이
서로 다른 검사를 수행했기 때문이다(감사 보고서 F-01).

이 모듈은 그 두 지점이 함께 호출하는 단 하나의 순수 검증 함수를
제공한다. 부작용이 전혀 없다 — DB를 쓰지 않고, 외부 API를 호출하지
않고, Payload도 만들지 않는다. 새 검사가 필요하면 반드시 이 파일에만
추가한다(두 호출자 중 한쪽에만 검사를 추가하는 것이 바로 이번에
고치는 결함의 재발이다).

승인(approval) fingerprint·자동화 안전(SafetyService)·자격
(EligibilityService) 같은 워크플로/권한 상태는 이 계약이 다루지
않는다 — 그건 DB·외부 상태에 의존하는 별도 관심사이고, 이미
listing_wizard_live_service.py::preflight()가 전담한다. 이 모듈은
오직 "쿠팡에 보낼 데이터 자체가 완전한가"만 검사한다.
=========================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.domains.marketplace_listing.category_metadata import (
    validate_purchase_options,
    validate_saved_notice_contract,
)


@dataclass(frozen=True)
class SubmissionContractIssue:
    """구조화된 검증 실패 1건 — 화면이 그대로 렌더링할 수 있는 모양."""

    code: str
    message_ko: str
    ui_step: str
    field_path: str
    ui_field: str | None = None
    blocking: bool = True


@dataclass(frozen=True)
class SubmissionContractResult:

    issues: list[SubmissionContractIssue] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not any(i.blocking for i in self.issues)

    @property
    def blocking_codes(self) -> list[str]:
        return [i.code for i in self.issues if i.blocking]


def _is_public_http_url(value: str) -> bool:
    from ipaddress import ip_address
    from urllib.parse import urlparse

    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 80, 443):
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith(".local"):
            return False
        try:
            address = ip_address(host)
        except ValueError:
            return True
        return address.is_global
    except (TypeError, ValueError):
        return False


_CONTENTS_TYPES = frozenset({
    "IMAGE", "IMAGE_NO_SPACE", "TEXT", "IMAGE_TEXT", "TEXT_IMAGE",
    "IMAGE_IMAGE", "TEXT_TEXT", "TITLE", "HTML",
})
_CONTENT_DETAIL_TYPES = frozenset({"IMAGE", "TEXT"})


def _issue(
    code: str, message_ko: str, ui_step: str, field_path: str,
    ui_field: str | None = None, blocking: bool = True,
) -> SubmissionContractIssue:

    return SubmissionContractIssue(
        code=code, message_ko=message_ko, ui_step=ui_step,
        field_path=field_path, ui_field=ui_field, blocking=blocking,
    )


def validate_coupang_submission_contract(
    *,
    draft: dict[str, Any],
    required_fields: dict[str, Any],
    channel_policy_attributes: dict[str, Any],
) -> SubmissionContractResult:
    """
    쿠팡 판매자배송(SELLER_FULFILLED) 실제 제출 자격을 판단하는 단일
    검증기. 7단계 사전검사와 9~10단계 Payload 생성 양쪽에서 동일하게
    호출한다 — 이 함수가 통과시킨 데이터는 반드시 실제 전송 가능해야
    하고, 이 함수가 막은 데이터는 반드시 7단계에서부터 막혀야 한다.
    """

    issues: list[SubmissionContractIssue] = []

    # --- 상품명(draft) ---
    if not str(draft.get("product_name") or "").strip():
        issues.append(_issue(
            "PRODUCT_NAME_REQUIRED", "상품명을 입력해 주세요.",
            "DRAFT", "draft.product_name", "lw-draft-product-name",
        ))

    # --- 브랜드 3상태 계약(감사 F-02) — 값을 추측해서 자동으로
    # 채우지 않는다. UNRESOLVED는 항상 차단한다. ---
    brand_state = required_fields.get("brandState") or "UNRESOLVED"
    if brand_state == "UNRESOLVED":
        issues.append(_issue(
            "BRAND_UNRESOLVED",
            "브랜드 정보를 확인해 주세요 — 공식 브랜드를 검색해 선택하거나, "
            "무브랜드 상품임을 직접 확인해 주세요.",
            "FULFILLMENT", "required_fields.brandState", "lw-brand-state",
        ))
    elif brand_state == "OFFICIAL_BRAND":
        for key, label in (
            ("brandId", "브랜드 ID"),
            ("officialBrandName", "공식 브랜드명"),
            ("brandLookupFingerprint", "브랜드 조회 fingerprint"),
        ):
            if not required_fields.get(key):
                issues.append(_issue(
                    "BRAND_OFFICIAL_DATA_INCOMPLETE",
                    f"브랜드 조회 정보가 불완전합니다({label} 누락) — "
                    "브랜드를 다시 검색해 주세요.",
                    "FULFILLMENT", f"required_fields.{key}", "lw-brand-state",
                ))
    elif brand_state == "NO_BRAND":
        # 무브랜드 확정은 허용하되, HOMEZ가 임의로 문자열을 만들어
        # 넣지 않는다 — 사용자가 직접 확인한 표현이 이미 저장되어
        # 있어야 한다(빈 값이면 아직 미확인 상태와 같다).
        if not str(required_fields.get("brand") or "").strip():
            issues.append(_issue(
                "BRAND_NO_BRAND_VALUE_MISSING",
                "무브랜드로 확인했지만 실제 전송할 브랜드 표기가 비어 "
                "있습니다 — 직접 확인한 표현을 입력해 주세요.",
                "FULFILLMENT", "required_fields.brand", "lw-brand-state",
            ))

    # --- vendorUserId / 택배사코드 — 실제 Live 검증으로 처음 확인된
    # 필수값(감사 F-01). 스키마에는 선택값으로 남아 있어야
    # Draft 저장이 막히지 않는다 — 여기서만 제출 조건으로 강제한다. ---
    if not str(required_fields.get("vendorUserId") or "").strip():
        issues.append(_issue(
            "VENDOR_USER_ID_REQUIRED",
            "쿠팡 WING 아이디(vendorUserId)를 입력해 주세요.",
            "FULFILLMENT", "required_fields.vendorUserId", "lw-vendor-user-id",
        ))
    if not str(required_fields.get("deliveryCompanyCode") or "").strip():
        issues.append(_issue(
            "DELIVERY_COMPANY_CODE_REQUIRED",
            "택배사를 선택해 주세요.",
            "FULFILLMENT", "required_fields.deliveryCompanyCode", "lw-delivery-company",
        ))

    # --- 이미지 ---
    images = required_fields.get("images")
    if not isinstance(images, list) or not images:
        issues.append(_issue(
            "PUBLIC_IMAGE_URL_REQUIRED",
            "공개 접근 가능한 대표 이미지 URL이 필요합니다.",
            "FULFILLMENT", "required_fields.images", "lw-live-image-url",
        ))
    else:
        has_representation = False
        for image in images:
            if not isinstance(image, dict):
                issues.append(_issue(
                    "INVALID_IMAGE_ENTRY", "이미지 항목 형식이 올바르지 않습니다.",
                    "FULFILLMENT", "required_fields.images", "lw-live-image-url",
                ))
                continue
            if image.get("imageType") == "REPRESENTATION":
                has_representation = True
            if image.get("cdnPath"):
                continue
            if not _is_public_http_url(str(image.get("vendorPath") or "")):
                issues.append(_issue(
                    "PUBLIC_IMAGE_URL_REQUIRED",
                    "이미지 URL이 공개 접근 가능한 http(s) 주소가 아닙니다.",
                    "FULFILLMENT", "required_fields.images", "lw-live-image-url",
                ))
        if not has_representation:
            issues.append(_issue(
                "REPRESENTATION_IMAGE_REQUIRED",
                "대표 이미지(REPRESENTATION)가 1개 이상 필요합니다.",
                "FULFILLMENT", "required_fields.images", "lw-live-image-url",
            ))
    if not required_fields.get("liveImageRightsConfirmed"):
        issues.append(_issue(
            "IMAGE_RIGHTS_CONFIRMATION_REQUIRED",
            "이미지 사용권 확인이 필요합니다.",
            "FULFILLMENT", "required_fields.liveImageRightsConfirmed",
            "lw-live-image-rights",
        ))

    # --- 카테고리 ---
    category = required_fields.get("displayCategoryCode")
    if not category:
        issues.append(_issue(
            "DISPLAY_CATEGORY_CODE_REQUIRED",
            "쿠팡 공식 카테고리 코드가 필요합니다.",
            "FULFILLMENT", "required_fields.displayCategoryCode",
            "lw-policy-official-category",
        ))

    # --- 상품정보제공고시 — 내용 존재 + fingerprint·사용자 확인 계약 ---
    notices = required_fields.get("notices")
    if not notices:
        issues.append(_issue(
            "NOTICE_INFORMATION_REQUIRED",
            "상품정보제공고시 항목이 필요합니다.",
            "FULFILLMENT", "required_fields.notices", "lw-notice-fields",
        ))
    notice_contract_missing = validate_saved_notice_contract(
        channel_policy_attributes,
    )
    if notice_contract_missing:
        issues.append(_issue(
            "NOTICE_CONTRACT_INCOMPLETE",
            "상품정보제공고시 확인 근거가 불완전합니다("
            + ", ".join(notice_contract_missing) + ") — "
            "고시정보를 다시 확인해 주세요.",
            "FULFILLMENT", "channel_policy_attributes.notice_information",
            "lw-notice-confirm",
        ))

    # --- 구매옵션 / items ---
    field_definitions = channel_policy_attributes.get(
        "purchase_option_field_definitions",
    )
    items = required_fields.get("items")
    has_per_item_options = False
    if not items:
        issues.append(_issue(
            "ITEM_REQUIRED", "옵션 조합표에 최소 1개 항목이 필요합니다.",
            "FULFILLMENT", "required_fields.items", "lw-item-combo-builder",
        ))
    elif isinstance(items, list):
        seen_skus: set[str] = set()
        duplicate_skus: set[str] = set()
        seen_item_names: set[str] = set()
        duplicate_item_names: set[str] = set()
        seen_combinations: set[str] = set()
        duplicate_combination = False
        sku_required = False
        for entry in items:
            if not isinstance(entry, dict):
                continue
            sku = entry.get("externalVendorSku")
            # 2026-09-21 옵션 연결 완성 — 스키마를 거치지 않은 옛 초안도
            # 여기서 막는다(SKU는 주문 수집의 channel_sku와 만나는 조인
            # 키라 비어 있거나 공백이 붙으면 안 된다).
            if (
                not isinstance(sku, str) or not sku.strip()
                or sku != sku.strip() or len(sku) > 150
            ):
                sku_required = True
            if sku:
                if sku in seen_skus:
                    duplicate_skus.add(sku)
                seen_skus.add(sku)
            # 2026-08-31 Phase 7.6 — 공식 문서 원문("Input for each
            # item so that there is no overlap")으로 확정된 계약.
            item_name = entry.get("itemName")
            if item_name:
                if item_name in seen_item_names:
                    duplicate_item_names.add(item_name)
                seen_item_names.add(item_name)
            option_attrs = entry.get("optionAttributes")
            if isinstance(option_attrs, dict) and option_attrs:
                has_per_item_options = True
                combo_key = json.dumps(
                    option_attrs, sort_keys=True, ensure_ascii=False,
                )
                if combo_key in seen_combinations:
                    duplicate_combination = True
                seen_combinations.add(combo_key)
                if field_definitions:
                    item_missing = validate_purchase_options(
                        field_definitions, option_attrs,
                    )
                    if item_missing:
                        issues.append(_issue(
                            "PURCHASE_OPTION_REQUIRED",
                            "옵션 조합 " + json.dumps(option_attrs, ensure_ascii=False)
                            + "에 다음 필수 구매옵션이 비어 있거나 허용된 값이 "
                            "아닙니다: " + ", ".join(item_missing),
                            "FULFILLMENT", "required_fields.items[].optionAttributes",
                            "lw-item-combo-builder",
                        ))
        if sku_required:
            issues.append(_issue(
                "SKU_REQUIRED",
                "옵션마다 SKU(externalVendorSku)를 입력해 주세요 — 비어 있거나 "
                "앞뒤에 공백이 있거나 150자를 넘으면 안 됩니다. 이 값이 주문 "
                "수집 뒤 공급처 옵션 연결의 기준이 됩니다.",
                "FULFILLMENT", "required_fields.items[].externalVendorSku",
                "lw-item-combo-builder",
            ))
        if duplicate_skus:
            issues.append(_issue(
                "DUPLICATE_SKU",
                "동일한 SKU가 중복되었습니다: " + ", ".join(sorted(duplicate_skus)),
                "FULFILLMENT", "required_fields.items[].externalVendorSku",
                "lw-item-combo-builder",
            ))
        if duplicate_item_names:
            issues.append(_issue(
                "DUPLICATE_ITEM_NAME",
                "동일한 옵션명(itemName)이 중복되었습니다 — 옵션을 구분할 "
                "수 있는 이름으로 각각 다르게 입력하세요: "
                + ", ".join(sorted(duplicate_item_names)),
                "FULFILLMENT", "required_fields.items[].itemName",
                "lw-item-combo-builder",
            ))
        if duplicate_combination:
            issues.append(_issue(
                "DUPLICATE_OPTION_COMBINATION",
                "동일한 구매옵션 조합이 중복 등록되었습니다.",
                "FULFILLMENT", "required_fields.items[].optionAttributes",
                "lw-item-combo-builder",
            ))
    if field_definitions and not has_per_item_options:
        missing_options = validate_purchase_options(
            field_definitions, channel_policy_attributes.get("purchase_options") or {},
        )
        if missing_options:
            issues.append(_issue(
                "PURCHASE_OPTION_REQUIRED",
                "다음 필수 구매옵션이 비어 있거나 허용된 값이 아닙니다: "
                + ", ".join(missing_options),
                "FULFILLMENT", "channel_policy_attributes.purchase_options",
                "lw-purchase-option-fields",
            ))

    # --- 상세설명(contents) — 공식 문서 원문 예시로 확인된 필수 계약 ---
    contents = required_fields.get("contents")
    if not isinstance(contents, list) or not contents:
        issues.append(_issue(
            "CONTENTS_REQUIRED", "상세설명(contents)이 필요합니다.",
            "FULFILLMENT", "required_fields.contents", "lw-live-image-url",
        ))
    else:
        for entry in contents:
            if not isinstance(entry, dict):
                issues.append(_issue(
                    "INVALID_CONTENTS_ENTRY", "상세설명 항목 형식이 올바르지 않습니다.",
                    "FULFILLMENT", "required_fields.contents", "lw-live-image-url",
                ))
                continue
            if entry.get("contentsType") not in _CONTENTS_TYPES:
                issues.append(_issue(
                    "INVALID_CONTENTS_TYPE", "상세설명 유형(contentsType)이 올바르지 않습니다.",
                    "FULFILLMENT", "required_fields.contents[].contentsType",
                    "lw-live-image-url",
                ))
            details = entry.get("contentDetails")
            if not isinstance(details, list) or not details:
                issues.append(_issue(
                    "CONTENT_DETAILS_REQUIRED", "상세설명 내용이 비어 있습니다.",
                    "FULFILLMENT", "required_fields.contents[].contentDetails",
                    "lw-live-image-url",
                ))
                continue
            for detail in details:
                if not isinstance(detail, dict):
                    issues.append(_issue(
                        "INVALID_CONTENT_DETAIL_ENTRY", "상세설명 세부 항목 형식이 올바르지 않습니다.",
                        "FULFILLMENT", "required_fields.contents[].contentDetails",
                        "lw-live-image-url",
                    ))
                    continue
                detail_type = detail.get("detailType")
                if detail_type not in _CONTENT_DETAIL_TYPES:
                    issues.append(_issue(
                        "INVALID_CONTENT_DETAIL_TYPE", "상세설명 세부 유형이 올바르지 않습니다.",
                        "FULFILLMENT", "required_fields.contents[].contentDetails[].detailType",
                        "lw-live-image-url",
                    ))
                elif detail_type == "TEXT" and not str(detail.get("content") or "").strip():
                    issues.append(_issue(
                        "CONTENT_TEXT_REQUIRED", "상세설명 텍스트 내용이 비어 있습니다.",
                        "FULFILLMENT", "required_fields.contents[].contentDetails[].content",
                        "lw-live-image-url",
                    ))
                elif detail_type == "IMAGE":
                    content_html = str(detail.get("content") or "")
                    import re
                    match = re.search(
                        r"""<img[^>]+src=["']([^"']+)["']""", content_html,
                        re.IGNORECASE,
                    )
                    if not match:
                        issues.append(_issue(
                            "CONTENT_IMAGE_URL_REQUIRED",
                            "상세설명 이미지 URL이 없습니다.",
                            "FULFILLMENT",
                            "required_fields.contents[].contentDetails[].content",
                            "lw-live-image-url",
                        ))
                    elif not _is_public_http_url(match.group(1)):
                        issues.append(_issue(
                            "CONTENT_IMAGE_URL_MUST_BE_PUBLIC",
                            "상세설명 이미지 URL이 공개 접근 가능한 주소가 아닙니다.",
                            "FULFILLMENT",
                            "required_fields.contents[].contentDetails[].content",
                            "lw-live-image-url",
                        ))

    # --- 출고지 코드(공식 Number 타입) ---
    outbound_raw = required_fields.get("outboundShippingPlaceCode")
    outbound_text = str(outbound_raw).strip() if outbound_raw is not None else ""
    if not outbound_text:
        issues.append(_issue(
            "OUTBOUND_SHIPPING_PLACE_CODE_REQUIRED", "출고지 코드가 필요합니다.",
            "FULFILLMENT", "required_fields.outboundShippingPlaceCode",
            "lw-outbound-place",
        ))
    else:
        try:
            int(outbound_text)
        except ValueError:
            issues.append(_issue(
                "OUTBOUND_SHIPPING_PLACE_CODE_INVALID",
                "출고지 코드는 숫자여야 합니다.",
                "FULFILLMENT", "required_fields.outboundShippingPlaceCode",
                "lw-outbound-place",
            ))

    return SubmissionContractResult(issues=issues)


__all__ = [
    "SubmissionContractIssue",
    "SubmissionContractResult",
    "validate_coupang_submission_contract",
]
