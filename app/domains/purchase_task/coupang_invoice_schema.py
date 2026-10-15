"""
2026-10-15 — 쿠팡 송장 등록 요청 스키마. 사용자가 미리보기에서 **본 값 그대로** 보낸다(서버가 지금 값과 다르면 거절한다).
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class CoupangInvoiceSendRequest(BaseModel):

    delivery_company_code: str = Field(min_length=2, max_length=20)
    invoice_number: str = Field(min_length=6, max_length=30)
    shipment_box_id: str = Field(min_length=1, max_length=30)
    vendor_item_ids: list[str] = Field(min_length=1, max_length=50)
    # 이전 요청이 거부(ACTION_REQUIRED)된 뒤 원인을 해결하고 사람이 다시 보낼 때만 true.
    retry_action_required: bool = False

    model_config = ConfigDict(extra="forbid")


__all__ = ["CoupangInvoiceSendRequest"]
