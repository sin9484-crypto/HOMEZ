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


__all__ = ["build_coupang_live_payload"]
