"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/adapters/naver_adapter.py

네이버 스마트스토어 Fulfillment Adapter — 네트워크 호출 0회, 자격증명
없음. 네이버 공식 GitHub(commerce-api-naver/commerce-api, 확인일
2026-07-31)로 확인된 판매자배송(deliveryType=DELIVERY,
deliveryAttributeType=NORMAL)만 변환한다. 다른 방식은 공식 문서에서
필드 수준으로 확인하지 못했으므로 NOT_SUPPORTED로 처리한다(추측 금지).
=========================================================
"""

from typing import Literal

from app.domains.marketplace_listing.constants import FulfillmentMode

NOT_SUPPORTED: Literal["NOT_SUPPORTED"] = "NOT_SUPPORTED"


class NaverFulfillmentAdapter:

    channel_code = "NAVER_SMARTSTORE"

    def translate(
        self, fulfillment_mode: str, listing_fields: dict,
    ) -> dict | Literal["NOT_SUPPORTED"]:

        if fulfillment_mode == FulfillmentMode.SELLER_FULFILLED:
            return {
                "deliveryType": "DELIVERY",
                "deliveryAttributeType": "NORMAL",
                **listing_fields,
            }

        # 다른 방식(네이버 자체 풀필먼트 등)은 공식 문서에서 필드 수준
        # 확인이 되지 않았으므로 추측하지 않는다.
        return NOT_SUPPORTED


__all__ = [
    "NaverFulfillmentAdapter",
    "NOT_SUPPORTED",
]
