"""Build the official Coupang product-creation payload.

2026-08-30 V7 안정화 Phase 1 — 검증은 이 파일이 더 이상 직접 하지
않는다. coupang_submission_contract.py::validate_coupang_submission_
contract()가 유일한 검증기이고, 7단계 사전검사(listing_wizard_
precheck.py)와 이 파일 양쪽에서 동일하게 호출된다. 이 파일은 검증
통과 이후의 순수 Payload 조립만 담당한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.marketplace_listing.coupang_submission_contract import (
    validate_coupang_submission_contract,
)


def _coerce_outbound_shipping_place_code(raw: Any) -> int | None:
    """공식 문서상 outboundShippingPlaceCode는 Number다. 검증기가 이미
    숫자 문자열임을 확인했으므로 여기서는 형 변환만 한다."""

    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def build_coupang_live_payload(
    *,
    draft: dict[str, Any],
    required_fields: dict[str, Any],
    channel_policy_attributes: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Return a payload only when every non-secret live input is present."""

    contract = validate_coupang_submission_contract(
        draft=draft, required_fields=required_fields,
        channel_policy_attributes=channel_policy_attributes,
    )
    if not contract.ready:
        return None, sorted(set(contract.blocking_codes))

    images = required_fields.get("images")
    notices = required_fields.get("notices")
    category = required_fields.get("displayCategoryCode")
    items = required_fields.get("items")
    outbound_code = _coerce_outbound_shipping_place_code(
        required_fields.get("outboundShippingPlaceCode"),
    )

    product_name = str(draft.get("product_name") or "").strip()
    # 2026-08-30 Phase 3 — 브랜드 3상태 계약. 계약 검증을 이미
    # 통과했으므로 brandState는 UNRESOLVED가 아니다. OFFICIAL_BRAND는
    # 조회로 확인된 공식 브랜드명(+brandId)을 그대로 쓰고, NO_BRAND는
    # 사용자가 직접 확인해 저장한 표현을 그대로 쓴다 — 이 함수가
    # 값을 새로 만들어내지 않는다.
    brand_state = required_fields.get("brandState") or "UNRESOLVED"
    brand_id: str | None = None
    if brand_state == "OFFICIAL_BRAND":
        brand = str(required_fields.get("officialBrandName") or "").strip()
        brand_id = str(required_fields.get("brandId") or "").strip() or None
    else:
        brand = str(required_fields.get("brand") or "").strip()
    general_name = product_name[:100]
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    end = min(now + timedelta(days=3650), datetime(2099, 1, 1))

    # The item-level fields are explicit inputs.  Defaults below are official
    # enum values, not invented product facts.
    live_items: list[dict[str, Any]] = []
    purchase_options = channel_policy_attributes.get("purchase_options") or {}
    shared_attributes = [
        {"attributeTypeName": str(name), "attributeValueName": str(value)}
        for name, value in purchase_options.items() if str(value).strip()
    ]
    for item in items:
        # 2026-08-29 — 옵션 조합 UI 지원: item 자신의 optionAttributes가
        # 있으면(다중 옵션 상품) 그 조합만의 attributes[]를 만든다.
        # 없으면(단일 옵션/레거시) 기존처럼 상품 전체 공유값을 쓴다.
        item_option_attrs = item.get("optionAttributes")
        if isinstance(item_option_attrs, dict) and item_option_attrs:
            attributes = [
                {"attributeTypeName": str(name), "attributeValueName": str(value)}
                for name, value in item_option_attrs.items() if str(value).strip()
            ]
        else:
            attributes = shared_attributes
        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 공식 문서 확인
        # 결과 originalPrice/salePrice/maximumBuyCount/unitCount는
        # 전부 items[] 각 항목(옵션 조합) 레벨 필드다. 옵션 조합별로
        # 다른 가격·재고를 지정할 수 있도록 item 자신의 값을 우선하고,
        # 없으면 기존처럼 상품 전체 값으로 대체한다(하위 호환 유지 —
        # 옵션이 1개뿐인 기존 데이터는 동작이 바뀌지 않는다).
        live_items.append({
            "itemName": item["itemName"],
            "originalPrice": item.get("originalPrice", required_fields["originalPrice"]),
            "salePrice": item.get("salePrice", required_fields["salePrice"]),
            "maximumBuyCount": item.get("maximumBuyCount", required_fields["maximumBuyCount"]),
            "maximumBuyForPerson": required_fields.get("maximumBuyForPerson", 0),
            # 공식 문서(Product Creation) — maximumBuyForPerson과 반드시
            # 함께 와야 하는 필드(누락 시 쿠팡이 거부함, 2026-08-28 실사
            # 확인). 제한이 없으면 '1'을 넣는다(문서 기준값).
            "maximumBuyForPersonPeriod": required_fields.get(
                "maximumBuyForPersonPeriod", 1,
            ),
            "outboundShippingTimeDay": required_fields.get("outboundShippingTimeDay", 1),
            "unitCount": item.get("unitCount", required_fields.get("unitCount", 1)),
            "adultOnly": required_fields.get("adultOnly", "EVERYONE"),
            "taxType": required_fields.get("taxType", "TAX"),
            "parallelImported": required_fields.get(
                "parallelImported", "NOT_PARALLEL_IMPORTED",
            ),
            "overseasPurchased": required_fields.get(
                "overseasPurchased", "NOT_OVERSEAS_PURCHASED",
            ),
            "pccNeeded": required_fields.get("pccNeeded", False),
            "externalVendorSku": item["externalVendorSku"],
            "barcode": required_fields.get("barcode", ""),
            "emptyBarcode": not bool(required_fields.get("barcode")),
            "emptyBarcodeReason": required_fields.get(
                "emptyBarcodeReason", "상품확인불가_바코드없음사유",
            ),
            "modelNo": required_fields.get("modelNo", item["externalVendorSku"]),
            "certifications": required_fields.get(
                "certifications",
                [{"certificationType": "NOT_REQUIRED", "certificationCode": ""}],
            ),
            "searchTags": list(draft.get("keywords") or [])[:20],
            "images": images,
            "notices": [
                {
                    "noticeCategoryName": notice["noticeCategoryName"],
                    "noticeCategoryDetailName": detail["noticeCategoryDetailName"],
                    "content": detail["content"],
                }
                for notice in notices
                for detail in notice["noticeCategoryDetailNames"]
            ],
            "attributes": attributes,
            "contents": required_fields.get("contents", []),
        })

    payload = {
        "displayCategoryCode": int(category),
        "sellerProductName": product_name[:100],
        "saleStartedAt": now.isoformat(),
        "saleEndedAt": end.isoformat(),
        "displayProductName": product_name[:100],
        "brand": brand,
        "brandId": brand_id,
        "generalProductName": general_name,
        "productGroup": str(draft.get("category") or "")[:100],
        "deliveryMethod": required_fields["deliveryMethod"],
        "deliveryCompanyCode": required_fields.get("deliveryCompanyCode"),
        "deliveryChargeType": required_fields["deliveryChargeType"],
        "deliveryCharge": required_fields["deliveryCharge"],
        "freeShipOverAmount": required_fields.get("freeShipOverAmount", 0),
        "deliveryChargeOnReturn": required_fields["deliveryChargeOnReturn"],
        "remoteAreaDeliverable": required_fields.get("remoteAreaDeliverable", "N"),
        "unionDeliveryType": required_fields.get("unionDeliveryType", "UNION_DELIVERY"),
        "returnCenterCode": required_fields["returnCenterCode"],
        "returnChargeName": required_fields["returnChargeName"],
        "companyContactNumber": required_fields["companyContactNumber"],
        "returnZipCode": required_fields["returnZipCode"],
        "returnAddress": required_fields["returnAddress"],
        "returnAddressDetail": required_fields.get("returnAddressDetail"),
        "returnCharge": required_fields["returnCharge"],
        "outboundShippingPlaceCode": outbound_code,
        "vendorUserId": required_fields.get("vendorUserId"),
        # 2026-08-29 Phase 4(V7-COUPANG-PAYLOAD-001 수정) — 공식 문서
        # 루트 레벨 선택 필드. bundleInfo는 WING 화면 라벨과의 매핑이
        # Phase 3 감사에서 UNCONFIRMED로 남아 이번에는 추가하지 않는다
        # (추측 금지 원칙).
        "manufacture": required_fields.get("manufacture"),
        "requested": True,
        "items": live_items,
    }
    return {k: v for k, v in payload.items() if v is not None}, []


_ATTRIBUTE_VALUE_MAX_LENGTH = 300  # product_attribute_comparison_items 값 칸 길이

# 구매옵션 속성 이름(쿠팡 카테고리 메타데이터의 attributeTypeName). 수량은 상품
# 구성수량(구매옵션 "수량")이며 unitCount·maximumBuyCount와 다른 값이다.
_QUANTITY_ATTRIBUTE_NAMES = ("수량",)
_SIZE_ATTRIBUTE_NAMES = ("개당 용량", "개당 중량", "용량", "중량")
_MANUFACTURER_NOTICE_KEYWORDS = ("제조업자", "제조자", "제조사", "제조원")
_ORIGIN_NOTICE_KEYWORDS = ("제조국", "원산지")


def _clip(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return text[:_ATTRIBUTE_VALUE_MAX_LENGTH]


def extract_registration_attribute_values(
    payload: dict[str, Any],
) -> dict[str, str | None]:
    """실제로 쿠팡에 보낼 payload(`build_coupang_live_payload`의 결과)에서 속성 비교 대상
    값을 뽑는다. 위저드 입력이 아니라 **최종 payload**를 기준으로 하므로, 비교가
    확인한 내용과 실제 등록되는 내용이 같은 출처에서 나온다. 값이 없으면 None이며
    추측으로 채우지 않는다.

    * NAME: sellerProductName
    * OPTIONS: 각 item의 itemName과 externalVendorSku
    * QUANTITY: 각 item의 구매옵션 "수량" 속성 — unitCount·maximumBuyCount·주문 수량이
      아니다(그 값으로 대체하지 않는다)
    * SIZE: 구매옵션의 개당 용량/중량 속성과 정보고시의 용량(중량)
    * MANUFACTURER: payload.manufacture, 정보고시의 제조업자/제조자/제조사 항목
    * ORIGIN_COUNTRY: 정보고시의 제조국/원산지 항목

    정보고시 항목 이름으로 찾지 못하는 카테고리는 해당 필드가 None이 되어(= 확인 불가)
    비교에서 계속 확인 필요로 남는다.
    """

    items = payload.get("items") or []

    def _attr_values(names: tuple[str, ...]) -> list[str]:
        found: list[str] = []
        for item in items:
            row = "(없음)"
            for attribute in item.get("attributes") or []:
                if str(attribute.get("attributeTypeName")) in names:
                    row = f'{attribute.get("attributeTypeName")}={attribute.get("attributeValueName")}'
                    break
            found.append(row)
        return found

    def _notice_values(keywords: tuple[str, ...]) -> list[str]:
        if not items:
            return []
        return [
            f'{notice.get("noticeCategoryDetailName")}={notice.get("content")}'
            for notice in (items[0].get("notices") or [])
            if any(
                keyword in str(notice.get("noticeCategoryDetailName") or "")
                for keyword in keywords
            )
        ]

    quantity_rows = _attr_values(_QUANTITY_ATTRIBUTE_NAMES)
    quantity = (
        " | ".join(quantity_rows)
        if any(row != "(없음)" for row in quantity_rows) else None
    )

    size_parts: list[str] = []
    for item in items:
        for attribute in item.get("attributes") or []:
            if str(attribute.get("attributeTypeName")) in _SIZE_ATTRIBUTE_NAMES:
                size_parts.append(
                    f'{attribute.get("attributeTypeName")}={attribute.get("attributeValueName")}',
                )
    size_parts.extend(_notice_values(("용량", "중량")))

    manufacturer_parts = []
    if payload.get("manufacture"):
        manufacturer_parts.append(f'manufacture={payload["manufacture"]}')
    manufacturer_parts.extend(_notice_values(_MANUFACTURER_NOTICE_KEYWORDS))

    return {
        "NAME": _clip(payload.get("sellerProductName")),
        "OPTIONS": _clip(" | ".join(
            f'{item.get("itemName")} [{item.get("externalVendorSku")}]'
            for item in items
        )),
        "QUANTITY": _clip(quantity),
        "SIZE": _clip(" | ".join(size_parts)),
        "MANUFACTURER": _clip(" | ".join(manufacturer_parts)),
        "ORIGIN_COUNTRY": _clip(" | ".join(_notice_values(_ORIGIN_NOTICE_KEYWORDS))),
    }


__all__ = ["build_coupang_live_payload", "extract_registration_attribute_values"]
