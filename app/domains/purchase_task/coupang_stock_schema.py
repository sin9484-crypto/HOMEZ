"""
2026-10-24 — 쿠팡 옵션 재고 수량 변경 요청 스키마. 사용자가 미리보기에서 **본 현재 재고**, 바꿀 수량, 미리보기의 요청 참조값을 그대로 보낸다
(서버가 쿠팡의 지금 재고와 다르면 거절한다). 0으로 바꿀 때는 `confirm_zero`가 따로 있어야 한다.
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictInt

from app.domains.purchase_task.coupang_channel_control_provider import MAX_STOCK_QUANTITY


class CoupangStockSendRequest(BaseModel):

    quantity: StrictInt = Field(ge=0, le=MAX_STOCK_QUANTITY)
    expected_current_stock: StrictInt = Field(ge=0)
    request_ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,40}$")
    confirm_zero: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangStockSendRequest"]
