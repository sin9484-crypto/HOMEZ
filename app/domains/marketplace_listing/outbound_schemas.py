"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/outbound_schemas.py

Adapter 출력(outbound payload) 재검증 Schema — Adapter가 반환한 dict가
실제로 그 채널·방식의 예상되는 모양과 일치하는지 다시 검증한다.
Adapter 내부 버그나 예기치 못한 변형이 검증되지 않은 채로 그대로
"제출 성공(PENDING)"으로 기록되는 것을 막는다.
=========================================================
"""

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict

from app.domains.marketplace_listing.constants import ChannelCode
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.required_fields_schemas import (
    CoupangRocketGrowthAdditionalInfo,
)
from app.domains.marketplace_listing.required_fields_schemas import (
    CoupangRocketGrowthFields,
)
from app.domains.marketplace_listing.required_fields_schemas import (
    CoupangSellerFulfilledFields,
)


class _StrictModel(BaseModel):

    model_config = ConfigDict(extra="forbid")


class CoupangSellerFulfilledOutbound(_StrictModel):

    marketplaceItemData: CoupangSellerFulfilledFields


class CoupangRocketGrowthOutbound(_StrictModel):

    rocketGrowthItemData: CoupangRocketGrowthFields
    rocketGrowthAdditionalInformation: CoupangRocketGrowthAdditionalInfo


class NaverSellerFulfilledOutbound(_StrictModel):

    deliveryType: Literal["DELIVERY"]
    deliveryAttributeType: Literal["NORMAL"]


OUTBOUND_SCHEMA_REGISTRY: dict[tuple[str, str], type[BaseModel]] = {
    (ChannelCode.COUPANG, FulfillmentMode.SELLER_FULFILLED): (
        CoupangSellerFulfilledOutbound
    ),
    (ChannelCode.COUPANG, FulfillmentMode.MARKETPLACE_FULFILLED): (
        CoupangRocketGrowthOutbound
    ),
    (ChannelCode.NAVER_SMARTSTORE, FulfillmentMode.SELLER_FULFILLED): (
        NaverSellerFulfilledOutbound
    ),
}


def get_outbound_schema(
    channel_code: str, fulfillment_mode: str,
) -> type[BaseModel] | None:

    return OUTBOUND_SCHEMA_REGISTRY.get((channel_code, fulfillment_mode))


__all__ = [
    "CoupangSellerFulfilledOutbound",
    "CoupangRocketGrowthOutbound",
    "NaverSellerFulfilledOutbound",
    "OUTBOUND_SCHEMA_REGISTRY",
    "get_outbound_schema",
]
