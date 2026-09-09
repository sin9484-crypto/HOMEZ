"""
=========================================================
Homez OS

File : app/domains/orchestration/priority_service.py

Gate AI-F2(2026-08-22 CTO 지시) — 운영 우선순위 제안. 기존 Dashboard
COUNT 기반 요약(app/domains/orchestration/dashboard_service.py::
DashboardService.get_summary)은 이 서비스와 완전히 별개다 — Dashboard는
AI Capability 상태와 무관하게 항상 조회 가능해야 하므로 절대 건드리지
않는다(AG-0 원칙 그대로 유지).

이 서비스는 여러 도메인의 실제 상태를 읽어 "다음에 무엇부터 처리해야
하는가"를 우선순위로 묶어 보여준다. 새로운 판단 로직을 추가로
발명하지 않고, 이미 구현된 개별 분석(OrderExceptionAnalysisService,
SettlementDifferenceAnalysisService)과 단순 COUNT 신호를 재사용해
집계만 한다.

capability_code: OPERATIONS_COORDINATION.

정직하게 미구현으로 남긴 범위: 이 서비스 자신은 ProposedAction을 만들지
않는다 — 실제 처리 제안(ORDER_EXCEPTION_REVIEW 등)은 이미 각 하위
분석 서비스가 담당하므로 중복 생성하지 않는다. 하위 capability
(ORDER_SHIPMENT_RETURN/SETTLEMENT)가 개별적으로 비활성화된 경우 해당
항목만 "집계 불가"로 표시하고 전체 우선순위 조회 자체를 차단하지
않는다(AG-0 원칙 — 단순 조회는 다른 capability 비활성으로 막히면 안
된다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.ai_governance.constants import AIResultType
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.schema import AIResultEnvelope
from app.domains.ai_governance.service import InactiveCapabilityError
from app.domains.ai_governance.service import build_ai_result_envelope
from app.domains.ai_governance.service import require_active_capability
from app.domains.automation_safety.service import SafetyService
from app.domains.order.exception_analysis_service import (
    OrderExceptionAnalysisService,
)
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.purchase.constants import PurchaseStatus
from app.domains.purchase.constants import PurchaseSubmissionStatus
from app.domains.purchase.model import Purchase
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.model import ReturnOrder
from app.domains.settlement.difference_analysis_service import (
    SettlementDifferenceAnalysisService,
)

_URGENCY_HIGH = "HIGH"
_URGENCY_MEDIUM = "MEDIUM"


@dataclass(frozen=True)
class OperationsPriorityItem:

    category: str
    urgency: str
    label: str
    count: int | None
    source_capability: str
    available: bool


class OperationsPriorityService:

    def __init__(self, db: Session):

        self.db = db
        self.safety_service = SafetyService(db)

    def _try_order_exceptions(
        self, company_id: int, now: datetime,
    ) -> tuple[int, int] | None:
        """(HIGH건수, 전체건수) 또는 하위 capability 비활성 시 None."""

        try:
            exceptions, _envelope = OrderExceptionAnalysisService(
                self.db,
            ).analyze(company_id, now=now)
        except InactiveCapabilityError:
            return None

        high = sum(1 for e in exceptions if e.urgency == _URGENCY_HIGH)
        return high, len(exceptions)

    def _try_settlement_differences(
        self, company_id: int, now: datetime,
    ) -> tuple[int, int] | None:
        """(HIGH건수, 전체건수) 또는 하위 capability 비활성 시 None."""

        try:
            differences, _envelope = SettlementDifferenceAnalysisService(
                self.db,
            ).analyze(company_id, now=now)
        except InactiveCapabilityError:
            return None

        high = sum(1 for d in differences if d.urgency == _URGENCY_HIGH)
        return high, len(differences)

    def analyze(
        self, company_id: int, *, now: datetime | None = None,
    ) -> tuple[list[OperationsPriorityItem], AIResultEnvelope]:

        require_active_capability(CapabilityCode.OPERATIONS_COORDINATION)

        now = now or datetime.utcnow()
        items: list[OperationsPriorityItem] = []
        unavailable_sources: list[str] = []

        candidates_needing_input = (
            self.db.query(ProductCandidate)
            .filter(
                (ProductCandidate.owner_company_id == company_id)
                | (ProductCandidate.visibility == "GLOBAL"),
            )
            .filter(ProductCandidate.status == CandidateStatus.DISCOVERED)
            .count()
        )
        if candidates_needing_input > 0:
            items.append(OperationsPriorityItem(
                category="PRODUCT_INPUT_REQUIRED",
                urgency=_URGENCY_MEDIUM,
                label=f"판매 판단 대기 상품 {candidates_needing_input}건",
                count=candidates_needing_input,
                source_capability="PRODUCT_DISCOVERY_ANALYSIS",
                available=True,
            ))

        purchase_approval_pending = (
            self.db.query(Purchase)
            .filter(Purchase.company_id == company_id)
            .filter(Purchase.status == PurchaseStatus.REQUESTED)
            .count()
        )
        if purchase_approval_pending > 0:
            items.append(OperationsPriorityItem(
                category="PURCHASE_APPROVAL_PENDING",
                urgency=_URGENCY_MEDIUM,
                label=f"발주 승인 대기 {purchase_approval_pending}건",
                count=purchase_approval_pending,
                source_capability="PRICING_INVENTORY",
                available=True,
            ))

        purchase_failed = (
            self.db.query(Purchase)
            .filter(Purchase.company_id == company_id)
            .filter(
                Purchase.submission_status == PurchaseSubmissionStatus.FAILED,
            )
            .count()
        )
        if purchase_failed > 0:
            items.append(OperationsPriorityItem(
                category="PURCHASE_FAILED",
                urgency=_URGENCY_HIGH,
                label=f"공급처 발주 전송 실패 {purchase_failed}건 — 재시도 필요",
                count=purchase_failed,
                source_capability="PRICING_INVENTORY",
                available=True,
            ))

        return_refund_pending = (
            self.db.query(ReturnOrder)
            .filter(ReturnOrder.company_id == company_id)
            .filter(ReturnOrder.status.notin_((
                ReturnOrderStatus.COMPLETED, ReturnOrderStatus.REJECTED,
            )))
            .count()
        )
        if return_refund_pending > 0:
            items.append(OperationsPriorityItem(
                category="RETURN_REFUND_PENDING",
                urgency=_URGENCY_MEDIUM,
                label=f"반품·환불 처리 대기 {return_refund_pending}건",
                count=return_refund_pending,
                source_capability="ORDER_SHIPMENT_RETURN",
                available=True,
            ))

        order_result = self._try_order_exceptions(company_id, now)
        if order_result is None:
            unavailable_sources.append("ORDER_SHIPMENT_RETURN")
        else:
            high, total = order_result
            if total > 0:
                items.append(OperationsPriorityItem(
                    category="ORDER_SHIPMENT_RETURN_EXCEPTIONS",
                    urgency=(_URGENCY_HIGH if high > 0 else _URGENCY_MEDIUM),
                    label=(
                        f"주문·배송·반품 예외 {total}건"
                        f"(긴급 {high}건)"
                    ),
                    count=total,
                    source_capability="ORDER_SHIPMENT_RETURN",
                    available=True,
                ))

        settlement_result = self._try_settlement_differences(company_id, now)
        if settlement_result is None:
            unavailable_sources.append("SETTLEMENT")
        else:
            high, total = settlement_result
            if total > 0:
                items.append(OperationsPriorityItem(
                    category="SETTLEMENT_DIFFERENCES",
                    urgency=(_URGENCY_HIGH if high > 0 else _URGENCY_MEDIUM),
                    label=(
                        f"정산 차이·방치 {total}건"
                        f"(긴급 {high}건)"
                    ),
                    count=total,
                    source_capability="SETTLEMENT",
                    available=True,
                ))

        items.sort(key=lambda i: 0 if i.urgency == _URGENCY_HIGH else 1)

        envelope = build_ai_result_envelope(
            capability_code=CapabilityCode.OPERATIONS_COORDINATION,
            result_type=(
                AIResultType.HUMAN_REVIEW_REQUIRED if items
                else AIResultType.CONFIRMED_DATA
            ),
            decision=(
                "PRIORITIES_FOUND" if items else "NO_PRIORITIES_FOUND"
            ),
            confirmed_facts={
                "item_count": len(items),
                "estop_active": self.safety_service.is_emergency_stop_active(),
            },
            calculated_values={
                "high_urgency_count": sum(
                    1 for i in items if i.urgency == _URGENCY_HIGH
                ),
            },
            assumptions=[
                "이 서비스는 새 판정 로직을 만들지 않고 이미 구현된 "
                "개별 분석(주문·배송·반품 예외, 정산 차이) 및 단순 "
                "COUNT 신호를 재사용해 집계만 합니다.",
            ],
            missing_evidence=(
                [f"{code} capability 비활성으로 집계 불가" for code in unavailable_sources]
            ),
            confidence=0.6,
            recommended_actions=sorted({i.category for i in items}),
            execution_allowed=False,
            evaluated_at=now,
        )

        return items, envelope


__all__ = ["OperationsPriorityService", "OperationsPriorityItem"]
