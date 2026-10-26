"""
2026-10-27 — 쿠팡 고객센터 업체 이관 문의 확인 요청 스키마. 문의와 쿠팡 상담 내용을 봤다는 확인(`confirm_seen`)을 보낸다.
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import StrictBool


class CoupangCallCenterConfirmRequest(BaseModel):

    confirm_seen: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangCallCenterConfirmRequest"]
