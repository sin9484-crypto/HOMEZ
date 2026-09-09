"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/adapters/coupang_adapter.py

쿠팡 Fulfillment Adapter — 네트워크 호출 0회, 자격증명 없음. 내부
표준 enum(FulfillmentMode)을 쿠팡 공식 문서로 확인된 구조로만
변환한다(developers.coupang.com, 확인일 2026-07-31).

확인된 사실: 쿠팡 상품 생성 API는 단일 enum 필드가 아니라
marketplaceItemData / rocketGrowthItemData 객체의 존재 여부로 판매
방식을 구분한다. 문서에서 확인하지 못한 필드는 추측해 채우지 않는다.
=========================================================
"""

from typing import Literal

from app.domains.marketplace_listing.constants import FulfillmentMode

NOT_SUPPORTED: Literal["NOT_SUPPORTED"] = "NOT_SUPPORTED"


class CoupangFulfillmentAdapter:

    channel_code = "COUPANG"

    def translate(
        self, fulfillment_mode: str, listing_fields: dict,
    ) -> dict | Literal["NOT_SUPPORTED"]:

        if fulfillment_mode == FulfillmentMode.SELLER_FULFILLED:
            return {
                "marketplaceItemData": dict(listing_fields),
            }

        if fulfillment_mode == FulfillmentMode.MARKETPLACE_FULFILLED:
            return {
                "rocketGrowthItemData": dict(listing_fields),
                "rocketGrowthAdditionalInformation": {
                    "legalAgreement": "AGREE",
                },
            }

        # DIRECT_PURCHASE(로켓배송/직매입) — 일반 판매자 상품등록 Open
        # API 범위 밖임을 공식 확인(2026-07-31). 어떤 입력이 와도
        # 지원하지 않는다.
        return NOT_SUPPORTED


__all__ = [
    "CoupangFulfillmentAdapter",
    "NOT_SUPPORTED",
]
