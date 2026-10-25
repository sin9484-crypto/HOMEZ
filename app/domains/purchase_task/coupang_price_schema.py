"""
2026-10-25 — 쿠팡 옵션 판매가 변경 요청 스키마. 사용자가 미리보기에서 **본 지금 판매가**, 바꿀 판매가, 마진 확인용 매입가·배송비,
미리보기의 요청 참조값을 그대로 보낸다(서버가 쿠팡의 지금 판매가와 다르면 거절한다). 20% 이상 바뀌면 `confirm_big_change`가 따로 있어야 한다.
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictInt

from app.domains.purchase_task.coupang_channel_control_provider import MAX_SALE_PRICE
from app.domains.purchase_task.coupang_channel_control_provider import MIN_SALE_PRICE
from app.domains.purchase_task.coupang_channel_control_provider import PRICE_UNIT
from app.domains.purchase_task.coupang_price_service import MAX_COST


class CoupangPriceSendRequest(BaseModel):

    price: StrictInt = Field(ge=MIN_SALE_PRICE, le=MAX_SALE_PRICE, multiple_of=PRICE_UNIT)
    unit_cost: StrictInt = Field(ge=1, le=MAX_COST)
    shipping_cost: StrictInt = Field(ge=0, le=MAX_COST)
    expected_current_price: StrictInt = Field(ge=1)
    request_ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,40}$")
    confirm_big_change: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangPriceSendRequest"]
