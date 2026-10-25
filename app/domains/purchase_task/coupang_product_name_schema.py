"""
2026-10-25 — 쿠팡 등록 상품의 상품명 수정 요청 스키마. 사용자가 미리보기에서 **본 지금 노출상품명**, 바꿀 노출상품명(필수)·등록상품명(선택),
미리보기의 요청 참조값을 그대로 보낸다(서버가 쿠팡의 지금 이름과 다르면 거절한다). 바꾼 이름은 쿠팡 승인 뒤에 반영돼 `confirm_review`가 있어야 한다.
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictStr

from app.domains.purchase_task.coupang_channel_control_provider import MAX_PRODUCT_NAME_CHARS


class CoupangProductNameSendRequest(BaseModel):

    display_name: StrictStr = Field(min_length=1, max_length=MAX_PRODUCT_NAME_CHARS)
    seller_name: StrictStr | None = Field(default=None, max_length=MAX_PRODUCT_NAME_CHARS)
    expected_display_name: StrictStr = Field(max_length=MAX_PRODUCT_NAME_CHARS)
    request_ref: str = Field(pattern=r"^[A-Za-z0-9-]{8,40}$")
    confirm_review: StrictBool = False
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: StrictBool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangProductNameSendRequest"]
