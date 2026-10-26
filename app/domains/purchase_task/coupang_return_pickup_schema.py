"""
2026-10-27 — 쿠팡 반품 회수 송장 등록 요청 스키마. 택배사(이름 또는 공식 코드)·회수 송장번호·회수번호(선택), 미리보기에서 본 반품 상태,
직접 회수 확인(`confirm_pickup`)을 보낸다. 서버는 쿠팡의 지금 상태가 다르거나 이미 회수 송장이 붙어 있으면 거절한다.
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictStr


class CoupangReturnPickupRequest(BaseModel):

    courier: StrictStr = Field(min_length=1, max_length=30)
    invoice_number: StrictStr = Field(pattern=r"^[A-Za-z0-9-]{6,30}$")
    reg_number: StrictStr | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,30}$")
    expected_status: StrictStr = Field(min_length=1, max_length=40)
    confirm_pickup: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangReturnPickupRequest"]
