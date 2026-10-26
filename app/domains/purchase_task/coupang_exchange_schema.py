"""
2026-10-27 — 쿠팡 교환 거부·교환 회수 송장 등록 요청 스키마. 동작(REJECT|PICKUP)과 그 값, 미리보기에서 본 상태(거부는 교환 상태, 회수 송장은 회수 상태),
따로 받는 확인(`confirm`)을 보낸다. 서버는 쿠팡의 지금 상태가 다르면 거절한다.
"""

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictStr


class CoupangExchangeActionRequest(BaseModel):

    action: Literal["REJECT", "PICKUP"]
    reject_code: Literal["SOLDOUT", "WITHDRAW"] | None = None
    courier: StrictStr | None = Field(default=None, min_length=1, max_length=30)
    invoice_number: StrictStr | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{6,30}$")
    expected_status: StrictStr = Field(min_length=1, max_length=40)
    confirm: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangExchangeActionRequest"]
