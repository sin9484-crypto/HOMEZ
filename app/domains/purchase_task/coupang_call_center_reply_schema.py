"""
2026-10-26 — 쿠팡 고객센터(콜센터) 이관 문의 답변 요청 스키마. 사람이 쓴 답변 글(2~1,000자), 미리보기에서 본 상담사 이관글 번호,
고객 상담에 바로 쓰인다는 확인(`confirm_public`)을 보낸다. 서버는 지금 답변을 기다리는 이관글과 다르면 거절한다.
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictInt
from pydantic import StrictStr

from app.domains.purchase_task.coupang_call_center_reply_provider import MAX_CC_REPLY_CHARS
from app.domains.purchase_task.coupang_call_center_reply_provider import MIN_CC_REPLY_CHARS


class CoupangCallCenterReplyRequest(BaseModel):

    content: StrictStr = Field(min_length=MIN_CC_REPLY_CHARS, max_length=MAX_CC_REPLY_CHARS)
    expected_parent_answer_id: StrictStr | None = Field(default=None, pattern=r"^\d{1,20}$")
    confirm_public: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False
    # 2026-11-09 AI 초안 평가 고리 — 이 답변이 'AI 답변 초안'에서 시작했으면 그 초안 기록 번호. 등록이 성공했을 때만 보낸 글을 그 기록에 남긴다
    # (보내기 자체에는 영향 없음, 없어도 된다).
    ai_draft_record_id: StrictInt | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangCallCenterReplyRequest"]
