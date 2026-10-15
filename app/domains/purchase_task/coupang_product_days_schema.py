"""
2026-10-15 — 쿠팡 등록 상품 기준 출고일 변경 요청 스키마. 사용자가 미리보기에서 **본 현재 값**과 바꿀 일수를 그대로 보낸다
(서버가 쿠팡의 지금 값과 다르면 거절한다).
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class CoupangProductDaysSendRequest(BaseModel):

    days: int = Field(ge=1, le=14)
    expected_current_days: list[int] = Field(min_length=1, max_length=200)
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: bool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangProductDaysSendRequest"]
