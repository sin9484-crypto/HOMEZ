"""
=========================================================
Homez OS

File : app/domains/product_selection/schema.py

상품 선별 통합 화면 — API 경계 Pydantic Schema. 완성된 문장을
저장·반환하지 않는다 — code + 구조화 필드만 반환하고 화면 i18n이
문구를 조립한다(listing_wizard_precheck.py와 동일 원칙).
=========================================================
"""

from decimal import Decimal

from pydantic import BaseModel
from pydantic import Field


class SelectionStepResult(BaseModel):

    step_code: str
    status: str
    detail_codes: list[str] = Field(default_factory=list)
    navigate_to: str | None = None


class ProductSelectionOverviewResponse(BaseModel):

    product_candidate_id: int
    channel: str | None
    steps: list[SelectionStepResult]
    # 정책 차단(POLICY)과 경제성 미달(PROFITABILITY)은 서로 다른
    # 축이다 — 이 두 플래그는 절대 같은 값에서 파생되지 않는다.
    policy_blocked: bool
    profitability_meets_target: bool | None


class ProfitabilityQuery(BaseModel):
    """선택적 — 없으면 PROFITABILITY_RISK 단계는 DATA_REQUIRED로
    표시된다(0으로 추정하지 않는다)."""

    sale_price: Decimal | None = None
    cost_of_goods: Decimal | None = None


__all__ = [
    "SelectionStepResult",
    "ProductSelectionOverviewResponse",
    "ProfitabilityQuery",
]
