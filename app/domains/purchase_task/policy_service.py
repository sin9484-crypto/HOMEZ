"""
=========================================================
Homez OS

File : app/domains/purchase_task/policy_service.py

작업 H — 매입 정책 판단. retail_purchase/policy_service.py와 동일한
철학(안전측 기본값, 허위로 ALLOW를 만들지 않는다, 확인 안 된 값은
0으로 채우지 않고 EVIDENCE_REQUIRED로 차단)을 이 도메인 전용으로
다시 적용한다(테이블은 공유하지 않음 — model.py 주석 참고).
=========================================================
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domains.automation_safety.service import SafetyService
from app.domains.purchase_task.model import PurchaseTaskPolicySetting
from app.domains.purchase_task.repository import PurchaseTaskRepository


class PurchaseTaskPolicyDecision:

    ALLOW = "ALLOW"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"
    BLOCK = "BLOCK"


class PurchaseTaskPolicyReason:

    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    PRODUCT_MATCH_INSUFFICIENT = "PRODUCT_MATCH_INSUFFICIENT"
    MIN_PROFIT_NOT_MET = "MIN_PROFIT_NOT_MET"
    MIN_MARGIN_RATE_NOT_MET = "MIN_MARGIN_RATE_NOT_MET"
    PER_ORDER_MAX_EXCEEDED = "PER_ORDER_MAX_EXCEEDED"
    MAX_QUANTITY_PER_PRODUCT_EXCEEDED = "MAX_QUANTITY_PER_PRODUCT_EXCEEDED"
    DAILY_LIMIT_EXCEEDED = "DAILY_LIMIT_EXCEEDED"
    MONTHLY_BUDGET_EXCEEDED = "MONTHLY_BUDGET_EXCEEDED"
    MAX_CONCURRENT_TASKS_EXCEEDED = "MAX_CONCURRENT_TASKS_EXCEEDED"
    DELIVERY_DEADLINE_EXCEEDED = "DELIVERY_DEADLINE_EXCEEDED"
    RETURN_NOT_ALLOWED = "RETURN_NOT_ALLOWED"
    PRICE_INCREASE_RATE_EXCEEDED = "PRICE_INCREASE_RATE_EXCEEDED"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"


@dataclass(frozen=True)
class PurchaseTaskPolicyCheckInput:

    match_confidence: float | None
    match_tier: str | None
    quantity: int
    gtin: str | None
    model_name: str | None
    expected_net_profit: Decimal | None
    expected_margin_rate: Decimal | None
    required_budget_amount: Decimal | None
    estimated_delivery_days: int | None
    return_allowed: bool | None
    expected_amount_at_creation: Decimal | None = None


@dataclass(frozen=True)
class PurchaseTaskPolicyCheckResult:

    decision: str
    reasons: tuple[str, ...]


class PurchaseTaskPolicyService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = PurchaseTaskRepository(db)
        self.safety = SafetyService(db)

    def get_or_create_default_settings(
        self, company_id: int,
    ) -> PurchaseTaskPolicySetting:

        existing = self.repository.get_policy_setting(company_id)
        if existing is not None:
            return existing

        setting = PurchaseTaskPolicySetting(
            company_id=company_id,
            min_net_profit=0, min_margin_rate=0,
            max_price_increase_rate=0.05, require_return_allowed=True,
            min_match_confidence=0.98, budget_reservation_hours=24,
        )
        return self.repository.add_policy_setting(setting)

    @staticmethod
    def _num(value):
        """숫자 컬럼을 fingerprint 계산 전에 항상 float로 고정한다.
        SQLite REAL affinity는 커밋 전(파이썬에서 방금 만든 int 0)과
        커밋 후 재조회(REAL 컬럼이라 0.0으로 변환됨) 시점에 같은 값이
        int/float로 다르게 보일 수 있고, json.dumps는 0과 0.0을 다른
        문자열로 렌더링해 SHA-256이 달라진다 — 이 메서드가 그 불안정성
        자체를 원천 제거한다."""

        return None if value is None else float(value)

    def compute_fingerprint(self, setting: PurchaseTaskPolicySetting) -> str:

        snapshot = {
            "per_order_max_amount": self._num(setting.per_order_max_amount),
            "daily_purchase_limit_amount": self._num(
                setting.daily_purchase_limit_amount,
            ),
            "monthly_purchase_budget_amount": self._num(
                setting.monthly_purchase_budget_amount,
            ),
            "max_quantity_per_product": setting.max_quantity_per_product,
            "min_net_profit": self._num(setting.min_net_profit),
            "min_margin_rate": self._num(setting.min_margin_rate),
            "max_price_increase_rate": self._num(
                setting.max_price_increase_rate,
            ),
            "max_delivery_days": setting.max_delivery_days,
            "require_return_allowed": bool(setting.require_return_allowed),
            "min_match_confidence": self._num(setting.min_match_confidence),
            "max_concurrent_tasks": setting.max_concurrent_tasks,
        }
        snapshot_json = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()

    def current_policy_fingerprint(self, company_id: int) -> str:

        return self.compute_fingerprint(
            self.get_or_create_default_settings(company_id),
        )

    def evaluate(
        self, company_id: int, check_input: PurchaseTaskPolicyCheckInput,
        *, now: datetime | None = None,
    ) -> PurchaseTaskPolicyCheckResult:

        now = now or datetime.utcnow()
        setting = self.get_or_create_default_settings(company_id)
        reasons: list[str] = []

        if self.safety.is_emergency_stop_active():
            return PurchaseTaskPolicyCheckResult(
                decision=PurchaseTaskPolicyDecision.BLOCK,
                reasons=(PurchaseTaskPolicyReason.EMERGENCY_STOP_ACTIVE,),
            )

        if check_input.match_tier == "BLOCKED":
            reasons.append(PurchaseTaskPolicyReason.PRODUCT_MATCH_INSUFFICIENT)

        if (
            setting.max_quantity_per_product is not None
            and check_input.quantity > setting.max_quantity_per_product
        ):
            reasons.append(
                PurchaseTaskPolicyReason.MAX_QUANTITY_PER_PRODUCT_EXCEEDED,
            )

        if setting.max_delivery_days is not None:
            if check_input.estimated_delivery_days is None:
                reasons.append(PurchaseTaskPolicyReason.EVIDENCE_REQUIRED)
            elif (
                check_input.estimated_delivery_days > setting.max_delivery_days
            ):
                reasons.append(
                    PurchaseTaskPolicyReason.DELIVERY_DEADLINE_EXCEEDED,
                )

        if setting.require_return_allowed and check_input.return_allowed is not True:
            reasons.append(PurchaseTaskPolicyReason.RETURN_NOT_ALLOWED)

        if (
            check_input.expected_net_profit is None
            or check_input.expected_margin_rate is None
            or check_input.required_budget_amount is None
        ):
            reasons.append(PurchaseTaskPolicyReason.EVIDENCE_REQUIRED)
        else:
            if check_input.expected_net_profit < Decimal(str(setting.min_net_profit)):
                reasons.append(PurchaseTaskPolicyReason.MIN_PROFIT_NOT_MET)
            if (
                check_input.expected_margin_rate
                < Decimal(str(setting.min_margin_rate)) * 100
            ):
                reasons.append(PurchaseTaskPolicyReason.MIN_MARGIN_RATE_NOT_MET)
            if (
                setting.per_order_max_amount is not None
                and check_input.required_budget_amount
                > Decimal(str(setting.per_order_max_amount))
            ):
                reasons.append(PurchaseTaskPolicyReason.PER_ORDER_MAX_EXCEEDED)

            if (
                check_input.expected_amount_at_creation is not None
                and check_input.expected_amount_at_creation > 0
            ):
                increase_rate = (
                    (
                        check_input.required_budget_amount
                        - check_input.expected_amount_at_creation
                    ) / check_input.expected_amount_at_creation
                )
                if increase_rate > Decimal(str(setting.max_price_increase_rate)):
                    reasons.append(
                        PurchaseTaskPolicyReason.PRICE_INCREASE_RATE_EXCEEDED,
                    )

            from app.domains.funding.service import FundingService

            funding = FundingService(self.db)
            account = funding.repository.get_account_by_company(company_id)
            if account is None:
                reasons.append(PurchaseTaskPolicyReason.BUDGET_INSUFFICIENT)
            else:
                available = Decimal(str(funding.available_amount(account)))
                if available < check_input.required_budget_amount:
                    reasons.append(PurchaseTaskPolicyReason.BUDGET_INSUFFICIENT)

        if setting.daily_purchase_limit_amount is not None:
            since = now - timedelta(days=1)
            spent = self.repository.sum_recorded_amount_since(company_id, since)
            if spent >= setting.daily_purchase_limit_amount:
                reasons.append(PurchaseTaskPolicyReason.DAILY_LIMIT_EXCEEDED)

        if setting.monthly_purchase_budget_amount is not None:
            since = now - timedelta(days=30)
            spent = self.repository.sum_recorded_amount_since(company_id, since)
            if spent >= setting.monthly_purchase_budget_amount:
                reasons.append(PurchaseTaskPolicyReason.MONTHLY_BUDGET_EXCEEDED)

        if setting.max_concurrent_tasks is not None:
            open_count = self.repository.count_open_tasks(company_id)
            if open_count >= setting.max_concurrent_tasks:
                reasons.append(
                    PurchaseTaskPolicyReason.MAX_CONCURRENT_TASKS_EXCEEDED,
                )

        hard_block = {
            PurchaseTaskPolicyReason.PRODUCT_MATCH_INSUFFICIENT,
            PurchaseTaskPolicyReason.MAX_QUANTITY_PER_PRODUCT_EXCEEDED,
            PurchaseTaskPolicyReason.DELIVERY_DEADLINE_EXCEEDED,
            PurchaseTaskPolicyReason.RETURN_NOT_ALLOWED,
            PurchaseTaskPolicyReason.MIN_PROFIT_NOT_MET,
            PurchaseTaskPolicyReason.MIN_MARGIN_RATE_NOT_MET,
            PurchaseTaskPolicyReason.PER_ORDER_MAX_EXCEEDED,
            PurchaseTaskPolicyReason.PRICE_INCREASE_RATE_EXCEEDED,
            PurchaseTaskPolicyReason.BUDGET_INSUFFICIENT,
            PurchaseTaskPolicyReason.DAILY_LIMIT_EXCEEDED,
            PurchaseTaskPolicyReason.MONTHLY_BUDGET_EXCEEDED,
            PurchaseTaskPolicyReason.MAX_CONCURRENT_TASKS_EXCEEDED,
            PurchaseTaskPolicyReason.EVIDENCE_REQUIRED,
        }

        if any(r in hard_block for r in reasons):
            decision = PurchaseTaskPolicyDecision.BLOCK
        elif (
            check_input.match_tier == "NEEDS_REVIEW"
            or (
                check_input.match_confidence is not None
                and check_input.match_confidence < setting.min_match_confidence
            )
        ):
            decision = PurchaseTaskPolicyDecision.REQUIRE_REVIEW
        else:
            decision = PurchaseTaskPolicyDecision.ALLOW

        return PurchaseTaskPolicyCheckResult(
            decision=decision, reasons=tuple(dict.fromkeys(reasons)),
        )


__all__ = [
    "PurchaseTaskPolicyDecision",
    "PurchaseTaskPolicyReason",
    "PurchaseTaskPolicyCheckInput",
    "PurchaseTaskPolicyCheckResult",
    "PurchaseTaskPolicyService",
]
