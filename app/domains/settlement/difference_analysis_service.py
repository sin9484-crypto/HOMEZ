"""
=========================================================
Homez OS

File : app/domains/settlement/difference_analysis_service.py

Gate AI-F2(2026-08-22 CTO 지시) — 정산 차이 분석. 기존 CRUD·상태
전이(SettlementService.confirm_deposit/reverse/hold/flag_mismatch 등)는
전혀 건드리지 않는다 — 이 서비스는 읽기 전용이며 어떤 Settlement
상태·금액도 직접 바꾸지 않는다.

지연·방치 판단 임계값은 공식 SLA 문서에서 확인한 값이 아니라 이번
라운드에서 정한 운영 휴리스틱 기본값이다(정직하게 공개 —
app/domains/order/exception_analysis_service.py와 동일한 방침).

capability_code: SETTLEMENT.

정직하게 미구현으로 남긴 범위: 채널 정산자료(마켓이 실제로 내려주는
정산 명세서 원본) 연동이 아직 없어, "차이"는 오직 이 시스템 내부의
Order.total_amount(주문 시점 기대 금액)와 MarketplaceSettlement.
gross_amount(실제 입금 예정/확정 금액)의 비교, 그리고 PENDING/HELD/
MISMATCH 상태의 방치 여부로만 판단한다. 마켓이 보낸 원본 정산 명세서
파일 파싱·대사는 이 라운드 범위 밖이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.ai_governance.constants import AIResultType
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.constants import ProposedActionStatus
from app.domains.ai_governance.model import ProposedAction
from app.domains.ai_governance.proposed_action_service import (
    ProposedActionService,
)
from app.domains.ai_governance.schema import AIResultEnvelope
from app.domains.ai_governance.service import build_ai_result_envelope
from app.domains.ai_governance.service import require_active_capability
from app.domains.automation_safety.service import SafetyService
from app.domains.order.model import Order
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.settlement.service import SettlementService

# 운영 휴리스틱 기본값(공식 SLA 아님, 정직하게 주석으로 명시).
AMOUNT_MISMATCH_TOLERANCE = 1.0  # 원 단위 — 부동소수 오차만 흡수
PENDING_STALE_HOURS = 336  # 14일 — 채널 정산 주기 감안
MISMATCH_UNRESOLVED_HOURS = 72
HELD_STALE_HOURS = 72

_URGENCY_HIGH = "HIGH"
_URGENCY_MEDIUM = "MEDIUM"


@dataclass(frozen=True)
class SettlementDifference:

    difference_type: str
    urgency: str
    target_entity: str
    evidence: str
    elapsed_hours: float
    recommended_action: str
    missing_evidence: list[str]
    approval_required: bool
    execution_allowed: bool


def _hours_since(dt: datetime, now: datetime) -> float:

    return (now - dt).total_seconds() / 3600.0


class SettlementDifferenceAnalysisService:

    def __init__(self, db: Session):

        self.db = db
        self.safety_service = SafetyService(db)
        self.proposed_action_service = ProposedActionService(db)

    def analyze(
        self, company_id: int, *, now: datetime | None = None,
    ) -> tuple[list[SettlementDifference], AIResultEnvelope]:

        require_active_capability(CapabilityCode.SETTLEMENT)

        now = now or datetime.utcnow()
        estop_active = self.safety_service.is_emergency_stop_active()
        differences: list[SettlementDifference] = []

        settlements = (
            self.db.query(MarketplaceSettlement)
            .filter(MarketplaceSettlement.company_id == company_id)
            .all()
        )

        # 주문-금액 비교용으로 필요한 order_id만 미리 모아 한 번에 조회.
        order_ids = {s.order_id for s in settlements if s.order_id is not None}
        orders_by_id: dict[int, Order] = {}
        if order_ids:
            orders = (
                self.db.query(Order)
                .filter(
                    Order.company_id == company_id,
                    Order.id.in_(order_ids),
                )
                .all()
            )
            orders_by_id = {o.id: o for o in orders}

        for settlement in settlements:

            # 1) 기대 금액(Order.total_amount) 대비 실제 정산 금액
            #    (gross_amount) 차이 — 주문이 연결돼 있고, 이미 수동으로
            #    MISMATCH 처리된 건은 중복 탐지하지 않는다(운영자가 이미
            #    인지하고 조사 중이므로).
            if (
                settlement.order_id is not None
                and settlement.status != SettlementService.STATUS_MISMATCH
                and settlement.status != SettlementService.STATUS_CANCELLED
            ):
                order = orders_by_id.get(settlement.order_id)
                if order is not None:
                    diff = float(settlement.gross_amount) - float(order.total_amount)
                    if abs(diff) > AMOUNT_MISMATCH_TOLERANCE:
                        differences.append(SettlementDifference(
                            difference_type="SETTLEMENT_AMOUNT_MISMATCH",
                            urgency=_URGENCY_HIGH,
                            target_entity=f"settlement:{settlement.id}",
                            evidence=(
                                f"주문 총액 {order.total_amount:.2f} vs "
                                f"정산 gross_amount {settlement.gross_amount:.2f} "
                                f"(차이 {diff:+.2f})"
                            ),
                            elapsed_hours=_hours_since(
                                settlement.created_at, now,
                            ),
                            recommended_action="SETTLEMENT_DIFFERENCE_REVIEW",
                            missing_evidence=[],
                            approval_required=True,
                            execution_allowed=(not estop_active),
                        ))

            # 2) PENDING 방치 — 채널 정산 주기를 넘겼는데도 입금 확인이
            #    안 된 건.
            if settlement.status == SettlementService.STATUS_PENDING:
                elapsed = _hours_since(settlement.created_at, now)
                if elapsed >= PENDING_STALE_HOURS:
                    differences.append(SettlementDifference(
                        difference_type="SETTLEMENT_PENDING_STALE",
                        urgency=_URGENCY_MEDIUM,
                        target_entity=f"settlement:{settlement.id}",
                        evidence=f"PENDING 상태로 {elapsed:.1f}시간 경과",
                        elapsed_hours=elapsed,
                        recommended_action="SETTLEMENT_DIFFERENCE_REVIEW",
                        missing_evidence=[],
                        approval_required=True,
                        execution_allowed=(not estop_active),
                    ))

            # 3) MISMATCH 미해소 방치.
            if settlement.status == SettlementService.STATUS_MISMATCH:
                elapsed = _hours_since(settlement.updated_at, now)
                if elapsed >= MISMATCH_UNRESOLVED_HOURS:
                    differences.append(SettlementDifference(
                        difference_type="SETTLEMENT_MISMATCH_UNRESOLVED",
                        urgency=_URGENCY_HIGH,
                        target_entity=f"settlement:{settlement.id}",
                        evidence=(
                            f"MISMATCH 상태로 {elapsed:.1f}시간 경과 — "
                            "해소 근거 미기록"
                        ),
                        elapsed_hours=elapsed,
                        recommended_action="SETTLEMENT_EVIDENCE_REQUEST",
                        missing_evidence=["해소 근거(memo)"],
                        approval_required=True,
                        execution_allowed=(not estop_active),
                    ))

            # 4) HELD 방치.
            if settlement.status == SettlementService.STATUS_HELD:
                elapsed = _hours_since(settlement.updated_at, now)
                if elapsed >= HELD_STALE_HOURS:
                    differences.append(SettlementDifference(
                        difference_type="SETTLEMENT_HELD_STALE",
                        urgency=_URGENCY_MEDIUM,
                        target_entity=f"settlement:{settlement.id}",
                        evidence=f"HELD 상태로 {elapsed:.1f}시간 경과",
                        elapsed_hours=elapsed,
                        recommended_action="SETTLEMENT_EVIDENCE_REQUEST",
                        missing_evidence=["보류 조사 결과"],
                        approval_required=True,
                        execution_allowed=(not estop_active),
                    ))

        differences.sort(
            key=lambda d: (
                0 if d.urgency == _URGENCY_HIGH else 1, -d.elapsed_hours,
            ),
        )

        envelope = build_ai_result_envelope(
            capability_code=CapabilityCode.SETTLEMENT,
            result_type=(
                AIResultType.HUMAN_REVIEW_REQUIRED if differences
                else AIResultType.CONFIRMED_DATA
            ),
            decision=(
                "DIFFERENCES_FOUND" if differences else "NO_DIFFERENCES_FOUND"
            ),
            confirmed_facts={
                "settlement_count": len(settlements),
                "difference_count": len(differences),
            },
            calculated_values={
                "high_urgency_count": sum(
                    1 for d in differences if d.urgency == _URGENCY_HIGH
                ),
            },
            assumptions=[
                f"AMOUNT_MISMATCH_TOLERANCE={AMOUNT_MISMATCH_TOLERANCE}",
                f"PENDING_STALE_HOURS={PENDING_STALE_HOURS}",
                f"MISMATCH_UNRESOLVED_HOURS={MISMATCH_UNRESOLVED_HOURS}",
                f"HELD_STALE_HOURS={HELD_STALE_HOURS}",
                "임계값은 공식 SLA가 아니라 운영 휴리스틱 기본값입니다.",
                "채널이 보낸 원본 정산 명세서 파일과의 대사는 아직 "
                "연동되어 있지 않습니다 — 이 시스템 내부 Order."
                "total_amount와의 비교만 수행합니다.",
            ],
            missing_evidence=sorted({
                m for d in differences for m in d.missing_evidence
            }),
            confidence=0.6,
            recommended_actions=sorted({
                d.recommended_action for d in differences
            }),
            execution_allowed=False,
            evaluated_at=now,
        )

        return differences, envelope

    def create_review_action(
        self,
        company_id: int,
        difference: SettlementDifference,
        *,
        idempotency_key: str,
        created_by: int | None = None,
    ) -> ProposedAction | None:
        """
        분석이 이미 실행 불가(EStop 등)로 판단한 차이는 ProposedAction을
        만들지 않는다 — AI가 EStop 활성 중 새 제안을 쌓는 것 자체를
        막는다(생성 시점에 이미 무의미한 제안을 남기지 않는다).
        """

        if not difference.execution_allowed:
            return None

        action, _dup = self.proposed_action_service.create(
            company_id=company_id,
            capability_code=CapabilityCode.SETTLEMENT,
            action_type=difference.recommended_action,
            target_entity=difference.target_entity,
            proposed_payload={
                "difference_type": difference.difference_type,
                "urgency": difference.urgency,
            },
            reason=difference.evidence,
            evidence=difference.missing_evidence,
            risk_level=(
                "HIGH" if difference.urgency == _URGENCY_HIGH else "MEDIUM"
            ),
            approval_required=difference.approval_required,
            idempotency_key=idempotency_key,
            status=ProposedActionStatus.REVIEW_REQUIRED,
            created_by=created_by,
        )

        return action


__all__ = ["SettlementDifferenceAnalysisService", "SettlementDifference"]
