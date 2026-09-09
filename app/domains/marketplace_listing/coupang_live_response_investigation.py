"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/coupang_live_response_investigation.py

2026-08-30 후속 지시(개인정보 재노출 재발 방지) — 실제 쿠팡 응답을
조사할 때 쓰는 안전 추출기. 이 세션에서 같은 종류의 사고(고정 키
이름만 지우는 denylist 방식이 notices[].content 같은 자유 텍스트
필드를 놓침)가 두 번 발생했다 — 이번에는 원천적으로 다시 발생할 수
없는 구조로 만든다.

원칙:
- allowlist만 통과한다. 목록에 없는 키는 이름을 몰라도 구조적으로
  빠진다(이 저장소의 redact_dict()와 동일한 원칙, 이 파일에 특화된
  버전).
- notices/content/전화번호/주소/담당자류 키는 allowlist 자체에
  아예 없다 — "지운다"가 아니라 "애초에 옮겨 담지 않는다."
- 출력 직전에 한 번 더, 결과에 남아있는 모든 문자열 값을
  redact_free_text()(전화번호/JWT 패턴)로 검사한다. 하나라도
  패턴에 걸리면 그 필드만 가리는 게 아니라 **전체 결과를 아예
  반환하지 않는다**(fail-closed) — allowlist 설계 실수가 있어도
  2차 방어선이 막는다.
- 이 함수는 순수 함수다 — 파일에 쓰거나 화면에 출력하지 않는다.
  호출자가 반환값을 다시 print()하지 않는 한 아무것도 노출되지
  않는다. 호출자는 반환값을 그대로 print/Read하지 말고, 이 모듈이
  제공하는 결과만 사용해야 한다.
=========================================================
"""

from __future__ import annotations

from typing import Any

from app.core.sensitive_data import redact_free_text

# 조사 목적에 실제로 필요한 필드만 명시한다 — 연락처·주소·담당자·
# notices류는 이 목록 어디에도 없다(이름을 나열해서 지우는 게
# 아니라 애초에 목록에 없어서 구조적으로 빠진다).
ALLOWED_TOP_LEVEL_FIELDS = frozenset({
    "code", "message",
})

ALLOWED_DATA_FIELDS = frozenset({
    "sellerProductId", "sellerProductName", "displayCategoryCode",
    "categoryId", "productId", "brand", "brandId", "statusName",
    "deliveryMethod", "deliveryCompanyCode", "deliveryChargeType",
    "deliveryCharge", "deliveryChargeOnReturn", "returnCharge",
    "returnCenterCode", "returnChargeName", "outboundShippingPlaceCode",
    "vendorUserId", "saleStartedAt", "saleEndedAt", "generalProductName",
    "displayProductName", "productGroup",
})

ALLOWED_ITEM_FIELDS = frozenset({
    "itemName", "externalVendorSku", "originalPrice", "salePrice",
    "maximumBuyCount", "unitCount", "barcode", "emptyBarcode",
    "emptyBarcodeReason", "maximumBuyForPerson", "maximumBuyForPersonPeriod",
    "taxType", "adultOnly", "modelNo",
})


def _has_sensitive_pattern(value: Any) -> bool:
    """redact_free_text()가 뭔가를 바꿨다면(=전화번호/JWT 패턴이
    있었다는 뜻) True. 값 자체가 문자열이 아니면 검사 대상이 아니다
    (검사 자체를 건너뛰되, allowlist 필드 이름 자체가 이미 좁으므로
    문제되는 종류의 값은 애초에 여기 들어올 일이 없다)."""

    if not isinstance(value, str) or not value:
        return False
    masked = redact_free_text(value)
    return masked != value


def extract_safe_live_status_fields(
    raw_response_json: dict[str, Any],
) -> dict[str, Any] | None:
    """
    쿠팡 상태 조회(GET) 원문 응답(dict, 이미 response.json()으로
    파싱된 것)에서 조사에 필요한 필드만 allowlist로 추출한다.

    반환값이 None이면 "결과에 민감 패턴이 남아 있어 fail-closed로
    전체를 버렸다"는 뜻이다 — 부분 결과를 대신 주지 않는다(부분
    노출도 노출이다).
    """

    if not isinstance(raw_response_json, dict):
        return None

    safe: dict[str, Any] = {}
    for key in ALLOWED_TOP_LEVEL_FIELDS:
        if key in raw_response_json:
            safe[key] = raw_response_json[key]

    data = raw_response_json.get("data")
    if isinstance(data, dict):
        safe_data = {k: v for k, v in data.items() if k in ALLOWED_DATA_FIELDS}

        items = data.get("items")
        if isinstance(items, list):
            safe_items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                safe_items.append({
                    k: v for k, v in item.items() if k in ALLOWED_ITEM_FIELDS
                })
            if safe_items:
                safe_data["items"] = safe_items

        if safe_data:
            safe["data"] = safe_data

    # 2차 방어선 — allowlist를 통과한 값 중에도 우연히 전화번호 형태
    # 숫자열이 있을 수 있다(예: itemName에 실수로 연락처가 들어간
    # 경우). 하나라도 걸리면 전체를 버린다.
    def _walk(node: Any) -> bool:
        if isinstance(node, dict):
            return any(_walk(v) for v in node.values())
        if isinstance(node, list):
            return any(_walk(v) for v in node)
        return _has_sensitive_pattern(node)

    if _walk(safe):
        return None

    return safe


__all__ = [
    "ALLOWED_TOP_LEVEL_FIELDS",
    "ALLOWED_DATA_FIELDS",
    "ALLOWED_ITEM_FIELDS",
    "extract_safe_live_status_fields",
]
