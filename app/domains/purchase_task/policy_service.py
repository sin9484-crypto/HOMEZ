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

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
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


def _describe_policy_changes(changes: list[tuple[str, object, object]]) -> str:
    """변경 항목과 전후 값(없음은 None). 500자 컬럼에 맞게 자른다."""

    text = "; ".join(f"{name}: {before!r} -> {after!r}" for name, before, after in changes)
    return text if len(text) <= 480 else text[:477] + "..."


class PurchaseTaskPolicyService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = PurchaseTaskRepository(db)
        self.safety = SafetyService(db)

    def update_settings(
        self, company_id: int, user_id: int, update_data: dict,
        if_unmodified_since: str | None = None,
    ) -> PurchaseTaskPolicySetting:
        """정책 저장(전체 폼 저장 계약).

        `update_data`는 `PolicySettingUpdate.model_dump(exclude_unset=True)`다 —
        **생략**(키 없음)은 기존 값 유지, **명시적 null**은 "미설정"으로
        되돌림(권장 기본값 적용), **명시적 숫자**는 그 값 저장이다. 셋은
        서로 다르며 여기서 섞지 않는다.

        동시 수정 보호: `if_unmodified_since`(마지막으로 조회한 updated_at)는
        **필수**다 — 생략을 허용하면 오래된 화면이나 동시 요청이 다른
        관리자의 변경을 조용히 덮어쓴다. 비교는 "읽고 비교한 뒤 쓰기"가
        아니라 `UPDATE ... WHERE updated_at = :기대값` 조건부 UPDATE 한 번으로
        한다(ListingWizard.version과 같은 패턴) — 같은 이전 값을 읽은 두
        요청 중 하나만 성공하고 나머지는 0행이라 충돌로 거부된다. 정책
        테이블에는 version 컬럼이 없어(Migration 없이) updated_at을 토큰으로
        쓴다.

        변경된 항목의 전후 값과 변경자·회사는 같은 Transaction의 감사
        로그로 남긴다(감사 실패 시 설정도 롤백, 충돌이면 감사도 없음).
        비밀번호·토큰은 이 함수에 들어오지 않는다."""

        if not if_unmodified_since:
            raise BadRequestException(
                "PURCHASE_TASK_POLICY_VERSION_REQUIRED: 정책을 저장하려면 "
                "마지막으로 조회한 수정 시각(X-If-Unmodified-Since)이 필요합니다 "
                "— 화면을 새로고침한 뒤 다시 시도하세요.",
            )
        try:
            expected = datetime.fromisoformat(if_unmodified_since)
        except ValueError:
            raise BadRequestException(
                "X-If-Unmodified-Since 형식이 올바르지 않습니다.",
            )

        setting = self.get_or_create_default_settings(company_id)
        conflict = ConflictException(
            "다른 곳에서 이미 정책이 변경되었습니다 — 새로고침 후 "
            "다시 시도하세요.",
        )
        if setting.updated_at != expected:
            raise conflict

        changes = [
            (name, getattr(setting, name), value)
            for name, value in update_data.items()
            if getattr(setting, name) != value
        ]
        if not update_data:
            return setting

        new_stamp = max(
            datetime.utcnow(), expected + timedelta(microseconds=1),
        )
        try:
            result = self.db.execute(
                update(PurchaseTaskPolicySetting)
                .where(
                    PurchaseTaskPolicySetting.id == setting.id,
                    PurchaseTaskPolicySetting.company_id == company_id,
                    PurchaseTaskPolicySetting.updated_at == expected,
                )
                .values(**update_data, updated_at=new_stamp),
            )
            if result.rowcount != 1:
                self.db.rollback()
                raise conflict
            if changes:
                write_audit_log(
                    self.db, user_id=user_id,
                    action="PURCHASE_TASK_POLICY_UPDATED",
                    entity="purchase_task_policy", entity_id=str(setting.id),
                    company_id=company_id,
                    description=_describe_policy_changes(changes),
                )
            self.db.commit()
        except ConflictException:
            raise
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(setting)
        return setting

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
