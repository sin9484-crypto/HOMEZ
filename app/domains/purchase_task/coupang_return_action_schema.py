"""
2026-10-26 — 쿠팡 반품 입고 확인·승인 요청 스키마. 사용자가 미리보기에서 **본 반품 상태**(승인은 **접수 수량**도)를 그대로 보낸다
(서버가 쿠팡의 지금 값과 다르면 거절한다). 입고 확인은 `confirm_received`, 승인은 거기에 `confirm_refund`가 있어야 한다.
"""

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictInt
from pydantic import StrictStr


class CoupangReturnActionRequest(BaseModel):

    action: Literal["RECEIVE", "APPROVE"]
    expected_status: StrictStr = Field(min_length=1, max_length=40)
    expected_cancel_count: StrictInt | None = Field(default=None, ge=1, le=9_999)
    confirm_received: StrictBool = False
    confirm_refund: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangReturnActionRequest"]
