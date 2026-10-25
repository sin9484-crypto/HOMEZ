"""
2026-10-25 — 쿠팡 상품 문의 답변 요청 스키마. 사람이 쓴 답변 글과, 고객에게 바로 보인다는 확인(`confirm_public`)을 보낸다.
같은 문의에는 한 번만 보낸다(작업 장부 키 = 문의 번호) — 참조값이 필요 없다.
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictStr

from app.domains.purchase_task.coupang_inquiry_reply_provider import MAX_REPLY_CHARS


class CoupangInquiryReplyRequest(BaseModel):

    content: StrictStr = Field(min_length=1, max_length=MAX_REPLY_CHARS)
    confirm_public: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangInquiryReplyRequest"]
