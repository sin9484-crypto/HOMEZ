"""
=========================================================
Homez OS

File : app/domains/inventory/replenishment_advisory_service.py

Gate AI-F1-B(2026-08-22 CTO 지시) — 재고 보충(발주) 추천. 기존
InventoryService(실제 재고 CRUD)는 전혀 건드리지 않는다 — 이 서비스는
읽기 전용이며, 재고를 직접 변경하거나 발주를 직접 실행하지 않는다.
판매량 자료가 없으면(과거 CONSUMED 이력 0건) 수요를 임의로 만들지
않고 EVIDENCE_REQUIRED로 정직하게 표시한다.

capability_code: PRICING_INVENTORY. AI Capability Registry 강제는
여기(추천 계산)에만 건다 — 실제 재고 CRUD(inventory/service.py)는
AG-0에서 이미 되돌린 그대로 게이트하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from decimal import ROUND_CEILING
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundException
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
from app.domains.inventory.constants import InventoryLedgerEventType
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventorySku
from app.domains.source.model import SupplierProductLink

_DEFAULT_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class ReplenishmentSuggestion:

    sku_id: int
    available_qty: int
    reserved_qty: int
    safety_stock: int
    lookback_days: int
    total_consumed_in_lookback: int
    avg_daily_sales: Decimal | None
    projected_stockout_date: str | None
    recommended_order_by_date: str | None
    recommended_order_qty: int | None
    moq_applied: bool
    supplier_lead_time_days: int | None
    projected_arrival_date: str | None
    reasoning: list[str]
    confidence: float
    missing_evidence: list[str]


def _iso_date(dt: datetime) -> str:

    return dt.date().isoformat()


class ReplenishmentAdvisoryService:

    def __init__(self, db: Session):

        self.db = db
        self.proposed_action_service = ProposedActionService(db)

    def suggest(
        self,
        sku_id: int,
        company_id: int,
        *,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
        now: datetime | None = None,
    ) -> tuple[ReplenishmentSuggestion, AIResultEnvelope]:

        require_active_capability(CapabilityCode.PRICING_INVENTORY)

        now = now or datetime.utcnow()

        sku = self.db.get(InventorySku, sku_id)
        if sku is None or sku.company_id != company_id:
            raise NotFoundException("InventorySku를 찾을 수 없습니다.")

        window_start = now - timedelta(days=lookback_days)

        # CONSUMED는 available 기준 음수로 기록된다 — 절대값 합이
        # "이 기간 동안 실제로 팔려나간 수량"이다.
        total_consumed = (
            self.db.query(
                func.coalesce(func.sum(InventoryLedgerEvent.quantity_delta), 0),
            )
            .filter(
                InventoryLedgerEvent.company_id == company_id,
                InventoryLedgerEvent.inventory_sku_id == sku_id,
                InventoryLedgerEvent.event_type
                == InventoryLedgerEventType.CONSUMED,
                InventoryLedgerEvent.created_at >= window_start,
                InventoryLedgerEvent.created_at <= now,
            )
            .scalar()
        )
        total_consumed = abs(int(total_consumed or 0))

        supplier_link = (
            self.db.query(SupplierProductLink)
            .filter(
                SupplierProductLink.company_id == company_id,
                SupplierProductLink.product_candidate_id
                == sku.product_candidate_id,
            )
            .order_by(SupplierProductLink.id.asc())
            .first()
        )

        reasoning: list[str] = []
        missing_evidence: list[str] = []
        moq_applied = False
        projected_arrival_date: str | None = None

        if total_consumed == 0:
            # 판매 이력이 없으면 수요를 임의로 만들지 않는다 — 발주
            # 시점·수량 전부 계산 불가로 정직하게 표시.
            missing_evidence.append("최근 판매(CONSUMED) 이력 없음")
            avg_daily_sales = None
            projected_stockout_date = None
            recommended_order_by_date = None
            recommended_order_qty = None
            confidence = 0.0
            reasoning.append(
                f"최근 {lookback_days}일간 판매 이력이 없어 수요를 "
                "추정하지 않았습니다.",
            )
        else:
            avg_daily_sales = (
                Decimal(total_consumed) / Decimal(lookback_days)
            ).quantize(Decimal("0.01"))
            reasoning.append(
                f"최근 {lookback_days}일간 {total_consumed}개 판매 "
                f"(일평균 {avg_daily_sales}개).",
            )

            if avg_daily_sales > 0:
                days_of_available_left = (
                    Decimal(sku.available_qty) / avg_daily_sales
                )
                stockout_dt = now + timedelta(
                    days=float(days_of_available_left),
                )
                projected_stockout_date = _iso_date(stockout_dt)
            else:
                projected_stockout_date = None

            lead_time_days = (
                supplier_link.lead_time_days if supplier_link else None
            )
            if lead_time_days is None:
                missing_evidence.append(
                    "공급처 연결(SupplierProductLink)의 리드타임 정보 없음",
                )
                recommended_order_by_date = None
                recommended_order_qty = None
                confidence = 0.3
            else:
                # 안전재고 + 리드타임 동안 소진될 예상 수량만큼 재고가
                # 남아있을 때가 발주 시점이다.
                buffer_days = lead_time_days
                trigger_qty = int(
                    (
                        Decimal(sku.safety_stock)
                        + avg_daily_sales * Decimal(buffer_days)
                    ).to_integral_value(rounding=ROUND_CEILING),
                )
                if sku.available_qty <= trigger_qty:
                    recommended_order_by_date = _iso_date(now)
                    reasoning.append(
                        "가용재고가 이미 안전재고+리드타임 소진 예상치 "
                        f"({trigger_qty}개) 이하입니다 — 즉시 발주를 "
                        "권장합니다.",
                    )
                else:
                    days_until_trigger = (
                        Decimal(sku.available_qty - trigger_qty)
                        / avg_daily_sales
                    )
                    recommended_order_by_date = _iso_date(
                        now + timedelta(days=float(days_until_trigger)),
                    )

                # 30일치 판매량을 기준 발주 수량으로 제안(안전재고 포함).
                target_qty = int(
                    (
                        avg_daily_sales * Decimal(30) + Decimal(sku.safety_stock)
                    ).to_integral_value(rounding=ROUND_CEILING),
                )
                moq = supplier_link.moq if supplier_link else None
                moq_applied = bool(moq and target_qty < moq)
                recommended_order_qty = (
                    moq if moq_applied else max(target_qty, 0)
                )
                if moq_applied:
                    reasoning.append(
                        f"계산된 권장 수량({target_qty}개)이 MOQ({moq}개) "
                        "미만이라 MOQ로 상향 조정했습니다.",
                    )

                if recommended_order_by_date == _iso_date(now):
                    projected_arrival_date = _iso_date(
                        now + timedelta(days=buffer_days),
                    )
                confidence = 0.7

        suggestion = ReplenishmentSuggestion(
            sku_id=sku_id,
            available_qty=sku.available_qty,
            reserved_qty=sku.reserved_qty,
            safety_stock=sku.safety_stock,
            lookback_days=lookback_days,
            total_consumed_in_lookback=total_consumed,
            avg_daily_sales=avg_daily_sales,
            projected_stockout_date=projected_stockout_date,
            recommended_order_by_date=recommended_order_by_date,
            recommended_order_qty=recommended_order_qty,
            moq_applied=moq_applied,
            supplier_lead_time_days=(
                supplier_link.lead_time_days if supplier_link else None
            ),
            projected_arrival_date=projected_arrival_date,
            reasoning=reasoning,
            confidence=confidence,
            missing_evidence=missing_evidence,
        )

        if missing_evidence:
            result_type = AIResultType.EVIDENCE_REQUIRED
        else:
            result_type = AIResultType.AI_ESTIMATE

        envelope = build_ai_result_envelope(
            capability_code=CapabilityCode.PRICING_INVENTORY,
            result_type=result_type,
            decision=(
                "INVENTORY_REPLENISHMENT_SUGGESTED"
                if recommended_order_qty else "INSUFFICIENT_DATA"
            ),
            confirmed_facts={
                "available_qty": sku.available_qty,
                "reserved_qty": sku.reserved_qty,
                "safety_stock": sku.safety_stock,
                "total_consumed_in_lookback": total_consumed,
            },
            calculated_values={
                "avg_daily_sales": (
                    str(avg_daily_sales) if avg_daily_sales is not None else None
                ),
                "recommended_order_qty": recommended_order_qty,
                "recommended_order_by_date": recommended_order_by_date,
                "projected_stockout_date": projected_stockout_date,
            },
            assumptions=reasoning,
            missing_evidence=missing_evidence,
            confidence=confidence,
            recommended_actions=(
                ["INVENTORY_REPLENISHMENT_PROPOSAL"]
                if recommended_order_qty else []
            ),
            execution_allowed=False,
            evaluated_at=now,
        )

        return suggestion, envelope

    def propose_replenishment(
        self,
        sku_id: int,
        company_id: int,
        *,
        idempotency_key: str,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
        now: datetime | None = None,
    ) -> tuple[ReplenishmentSuggestion, AIResultEnvelope, ProposedAction | None]:
        """
        Gate AI-F8(2026-08-22) — 발주 수량이 실제로 계산됐을 때만
        `ProposedAction(REVIEW_REQUIRED)`을 만든다. 실제 발주는
        수행하지 않는다 — 사람이 승인한 뒤에만 호출부가 기존 Purchase
        도메인으로 실제 발주를 진행한다.
        """

        suggestion, envelope = self.suggest(
            sku_id, company_id, lookback_days=lookback_days, now=now,
        )

        if suggestion.recommended_order_qty is None:
            return suggestion, envelope, None

        action, _dup = self.proposed_action_service.create(
            company_id=company_id,
            capability_code=CapabilityCode.PRICING_INVENTORY,
            action_type="INVENTORY_REPLENISHMENT_PROPOSAL",
            target_entity=f"inventory_sku:{sku_id}",
            proposed_payload={
                "recommended_order_qty": suggestion.recommended_order_qty,
                "recommended_order_by_date": (
                    suggestion.recommended_order_by_date
                ),
            },
            reason="; ".join(suggestion.reasoning) or "재고 보충 제안 계산 결과",
            evidence=[
                f"avg_daily_sales:{suggestion.avg_daily_sales}",
                f"lookback_days:{suggestion.lookback_days}",
            ],
            risk_level=(
                "HIGH"
                if suggestion.recommended_order_by_date
                == suggestion.projected_stockout_date
                else "MEDIUM"
            ),
            approval_required=True,
            idempotency_key=idempotency_key,
            status=ProposedActionStatus.REVIEW_REQUIRED,
        )

        return suggestion, envelope, action


__all__ = ["ReplenishmentAdvisoryService", "ReplenishmentSuggestion"]
