"""
=========================================================
Homez OS

File : app/domains/order/exception_analysis_service.py

Gate AI-F2(2026-08-22 CTO 지시) — 주문·배송·반품 예외 분석. 기존
CRUD·상태 전이(OrderService/ShipmentService/ReturnOrderService)는
전혀 건드리지 않는다 — 이 서비스는 읽기 전용이며 어떤 상태도 직접
바꾸지 않는다.

지연 판단 임계값(READY_STALE_HOURS 등)은 공식 SLA 문서에서 확인한
값이 아니라 이번 라운드에서 정한 운영 휴리스틱 기본값이다(정직하게
공개 — CHANNEL_POLICY_ASSIST처럼 법적 근거가 필요한 영역이 아니라
운영 편의 목적의 임계값이므로 합리적 기본값 사용이 허용된다).

capability_code: ORDER_SHIPMENT_RETURN.

정직하게 미구현으로 남긴 범위: "취소 요청"(Order.status에 별도
CANCEL_REQUESTED 상태가 없어 CANCELLED와 구분할 신호가 없음).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta

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
from app.domains.order.constants import OrderIngestionStatus
from app.domains.order.constants import OrderItemStatus
from app.domains.order.constants import OrderStatus
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.model import ReturnOrder
from app.domains.shipment.constants import ShipmentStatus
from app.domains.shipment.model import Shipment

# 운영 휴리스틱 기본값(공식 SLA 아님, 정직하게 주석으로 명시).
PENDING_ORDER_STALE_HOURS = 24
READY_STALE_HOURS = 48
IN_TRANSIT_STALE_HOURS = 120
RETURN_REVIEW_STALE_HOURS = 72

_URGENCY_HIGH = "HIGH"
_URGENCY_MEDIUM = "MEDIUM"


@dataclass(frozen=True)
class OrderException:

    exception_type: str
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


class OrderExceptionAnalysisService:

    def __init__(self, db: Session):

        self.db = db
        self.safety_service = SafetyService(db)
        self.proposed_action_service = ProposedActionService(db)

    def analyze(
        self, company_id: int, *, now: datetime | None = None,
    ) -> tuple[list[OrderException], AIResultEnvelope]:

        require_active_capability(CapabilityCode.ORDER_SHIPMENT_RETURN)

        now = now or datetime.utcnow()
        estop_active = self.safety_service.is_emergency_stop_active()
        exceptions: list[OrderException] = []

        # 1) 신규 처리 대기 주문 — PENDING 상태로 임계값 이상 머문 주문.
        pending_orders = (
            self.db.query(Order)
            .filter(Order.company_id == company_id, Order.status == OrderStatus.PENDING)
            .all()
        )
        for order in pending_orders:
            elapsed = _hours_since(order.created_at, now)
            if elapsed >= PENDING_ORDER_STALE_HOURS:
                exceptions.append(OrderException(
                    exception_type="PENDING_ORDER_STALE",
                    urgency=_URGENCY_MEDIUM,
                    target_entity=f"order:{order.id}",
                    evidence=f"PENDING 상태로 {elapsed:.1f}시간 경과",
                    elapsed_hours=elapsed,
                    recommended_action="ORDER_EXCEPTION_REVIEW",
                    missing_evidence=[],
                    approval_required=True,
                    execution_allowed=(not estop_active),
                ))

        # 2) 재고 부족.
        out_of_stock_items = (
            self.db.query(OrderItem)
            .filter(
                OrderItem.company_id == company_id,
                OrderItem.status == OrderItemStatus.OUT_OF_STOCK,
            )
            .all()
        )
        for item in out_of_stock_items:
            exceptions.append(OrderException(
                exception_type="OUT_OF_STOCK",
                urgency=_URGENCY_HIGH,
                target_entity=f"order_item:{item.id}",
                evidence=(
                    f"주문 {item.order_id} 품목({item.sku_code_snapshot})이 "
                    "재고 부족 상태"
                ),
                elapsed_hours=0.0,
                recommended_action="ORDER_EXCEPTION_REVIEW",
                missing_evidence=[],
                approval_required=True,
                execution_allowed=(not estop_active),
            ))

        # 3) 상품·옵션 연결 실패(수집 실패).
        failed_ingestions = (
            self.db.query(OrderIngestionEvent)
            .filter(
                OrderIngestionEvent.company_id == company_id,
                OrderIngestionEvent.status == OrderIngestionStatus.FAILED,
            )
            .all()
        )
        for event in failed_ingestions:
            exceptions.append(OrderException(
                exception_type="ORDER_INGESTION_FAILED",
                urgency=_URGENCY_HIGH,
                target_entity=f"order_ingestion_event:{event.id}",
                evidence=(
                    f"채널 주문({event.channel_code}:{event.channel_order_id}) "
                    "수집 실패 — 상품·옵션 연결 확인 필요"
                ),
                elapsed_hours=_hours_since(event.created_at, now),
                recommended_action="ORDER_EXCEPTION_REVIEW",
                missing_evidence=["채널 SKU ↔ InventorySku 매핑"],
                approval_required=True,
                execution_allowed=(not estop_active),
            ))

        # 4) 배송 준비 지연 / 5) 송장 누락 / 6) 배송 지연.
        shipments = (
            self.db.query(Shipment)
            .filter(
                Shipment.company_id == company_id,
                Shipment.status.in_((
                    ShipmentStatus.READY, ShipmentStatus.SHIPPED,
                    ShipmentStatus.IN_TRANSIT,
                )),
            )
            .all()
        )
        for shipment in shipments:
            elapsed = _hours_since(shipment.created_at, now)

            if (
                shipment.status == ShipmentStatus.READY
                and elapsed >= READY_STALE_HOURS
            ):
                exceptions.append(OrderException(
                    exception_type="SHIPMENT_PREPARATION_DELAYED",
                    urgency=_URGENCY_MEDIUM,
                    target_entity=f"shipment:{shipment.id}",
                    evidence=f"READY 상태로 {elapsed:.1f}시간 경과",
                    elapsed_hours=elapsed,
                    recommended_action="SHIPMENT_DELAY_REVIEW",
                    missing_evidence=[],
                    approval_required=True,
                    execution_allowed=(not estop_active),
                ))

            if (
                shipment.status in (
                    ShipmentStatus.SHIPPED, ShipmentStatus.IN_TRANSIT,
                )
                and not shipment.invoice_number
            ):
                exceptions.append(OrderException(
                    exception_type="SHIPMENT_INVOICE_MISSING",
                    urgency=_URGENCY_HIGH,
                    target_entity=f"shipment:{shipment.id}",
                    evidence=f"상태={shipment.status}인데 송장번호 없음",
                    elapsed_hours=elapsed,
                    recommended_action="SHIPMENT_DELAY_REVIEW",
                    missing_evidence=["invoice_number"],
                    approval_required=True,
                    execution_allowed=(not estop_active),
                ))

            if (
                shipment.status == ShipmentStatus.IN_TRANSIT
                and elapsed >= IN_TRANSIT_STALE_HOURS
            ):
                exceptions.append(OrderException(
                    exception_type="SHIPMENT_IN_TRANSIT_DELAYED",
                    urgency=_URGENCY_HIGH,
                    target_entity=f"shipment:{shipment.id}",
                    evidence=f"IN_TRANSIT 상태로 {elapsed:.1f}시간 경과",
                    elapsed_hours=elapsed,
                    recommended_action="SHIPMENT_DELAY_REVIEW",
                    missing_evidence=[],
                    approval_required=True,
                    execution_allowed=(not estop_active),
                ))

        # 7) 반품 요청 / 8) 반품 처리 지연 / 9) 환불·재배송 검토 필요.
        return_orders = (
            self.db.query(ReturnOrder)
            .filter(
                ReturnOrder.company_id == company_id,
                ReturnOrder.status.in_((
                    ReturnOrderStatus.REQUESTED, ReturnOrderStatus.APPROVED,
                    ReturnOrderStatus.RECEIVED,
                )),
            )
            .all()
        )
        for ret in return_orders:
            elapsed = _hours_since(ret.created_at, now)

            if ret.status == ReturnOrderStatus.RECEIVED:
                exceptions.append(OrderException(
                    exception_type="REFUND_OR_RESHIPMENT_REVIEW_NEEDED",
                    urgency=_URGENCY_HIGH,
                    target_entity=f"return_order:{ret.id}",
                    evidence="반품 상품 회수 완료(RECEIVED) — 환불/재배송 결정 대기",
                    elapsed_hours=elapsed,
                    recommended_action="REFUND_REVIEW",
                    missing_evidence=[],
                    approval_required=True,
                    execution_allowed=(not estop_active),
                ))
            elif elapsed >= RETURN_REVIEW_STALE_HOURS:
                exceptions.append(OrderException(
                    exception_type="RETURN_REVIEW_DELAYED",
                    urgency=_URGENCY_MEDIUM,
                    target_entity=f"return_order:{ret.id}",
                    evidence=f"{ret.status} 상태로 {elapsed:.1f}시간 경과",
                    elapsed_hours=elapsed,
                    recommended_action="RETURN_REVIEW",
                    missing_evidence=[],
                    approval_required=True,
                    execution_allowed=(not estop_active),
                ))
            else:
                exceptions.append(OrderException(
                    exception_type="RETURN_REQUESTED",
                    urgency=_URGENCY_MEDIUM,
                    target_entity=f"return_order:{ret.id}",
                    evidence=f"반품 요청 접수({ret.status})",
                    elapsed_hours=elapsed,
                    recommended_action="RETURN_REVIEW",
                    missing_evidence=[],
                    approval_required=True,
                    execution_allowed=(not estop_active),
                ))

        exceptions.sort(
            key=lambda e: (0 if e.urgency == _URGENCY_HIGH else 1, -e.elapsed_hours),
        )

        envelope = build_ai_result_envelope(
            capability_code=CapabilityCode.ORDER_SHIPMENT_RETURN,
            result_type=(
                AIResultType.HUMAN_REVIEW_REQUIRED if exceptions
                else AIResultType.CONFIRMED_DATA
            ),
            decision=(
                "EXCEPTIONS_FOUND" if exceptions else "NO_EXCEPTIONS_FOUND"
            ),
            confirmed_facts={"exception_count": len(exceptions)},
            calculated_values={
                "high_urgency_count": sum(
                    1 for e in exceptions if e.urgency == _URGENCY_HIGH
                ),
            },
            assumptions=[
                f"PENDING_ORDER_STALE_HOURS={PENDING_ORDER_STALE_HOURS}",
                f"READY_STALE_HOURS={READY_STALE_HOURS}",
                f"IN_TRANSIT_STALE_HOURS={IN_TRANSIT_STALE_HOURS}",
                f"RETURN_REVIEW_STALE_HOURS={RETURN_REVIEW_STALE_HOURS}",
                "임계값은 공식 SLA가 아니라 운영 휴리스틱 기본값입니다.",
            ],
            missing_evidence=sorted({
                m for e in exceptions for m in e.missing_evidence
            }),
            confidence=0.6,
            recommended_actions=sorted({e.recommended_action for e in exceptions}),
            execution_allowed=False,
            evaluated_at=now,
        )

        return exceptions, envelope

    def create_review_action(
        self,
        company_id: int,
        exception: OrderException,
        *,
        idempotency_key: str,
        created_by: int | None = None,
    ) -> ProposedAction | None:
        """
        분석이 이미 실행 불가(EStop 등)로 판단한 예외는 ProposedAction을
        만들지 않는다 — AI가 EStop 활성 중 새 제안을 쌓는 것 자체를
        막는다(생성 시점에 이미 무의미한 제안을 남기지 않는다).
        """

        if not exception.execution_allowed:
            return None

        action, _dup = self.proposed_action_service.create(
            company_id=company_id,
            capability_code=CapabilityCode.ORDER_SHIPMENT_RETURN,
            action_type=exception.recommended_action,
            target_entity=exception.target_entity,
            proposed_payload={
                "exception_type": exception.exception_type,
                "urgency": exception.urgency,
            },
            reason=exception.evidence,
            evidence=exception.missing_evidence,
            risk_level=(
                "HIGH" if exception.urgency == _URGENCY_HIGH else "MEDIUM"
            ),
            approval_required=exception.approval_required,
            idempotency_key=idempotency_key,
            status=ProposedActionStatus.REVIEW_REQUIRED,
            created_by=created_by,
        )

        return action


__all__ = ["OrderExceptionAnalysisService", "OrderException"]
