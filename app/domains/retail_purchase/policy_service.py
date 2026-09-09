"""
=========================================================
Homez OS

File : app/domains/retail_purchase/policy_service.py

Gate RP-1(2026-08-22) — 구매처 선택정책(지시문 E) 판단. 순이익 계산에
쓰는 모든 금액은 호출자가 실제 값으로 채워야 한다 — 알 수 없는
값은 여기서 0으로 대체하지 않고 EVIDENCE_REQUIRED로 차단한다
(허위 수수료·임의 가격 금지 원칙).
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
from app.domains.funding.service import FundingService
from app.domains.retail_purchase.constants import RetailPurchasePolicyDecision
from app.domains.retail_purchase.constants import RetailPurchasePolicyReason
from app.domains.retail_purchase.constants import SameProductConfidenceTier
from app.domains.retail_purchase.model import RetailPurchasePolicySetting
from app.domains.retail_purchase.product_matching import SameProductMatchResult
from app.domains.retail_purchase.repository import RetailPurchaseRepository

DEFAULT_ALLOWED_PROVIDER_CODES: tuple[str, ...] = ()  # 안전측 기본값: 전부 차단


def compute_policy_fingerprint(setting: RetailPurchasePolicySetting) -> str:
    """정책 설정의 SHA-256 fingerprint(app/domains/decision/service.py
    의 input_fingerprint 패턴과 동일하게 json.dumps(sort_keys=True) +
    SHA-256). 정책 검사 시점에 order.policy_fingerprint로 저장해 두고,
    예산예약/견적/발주 시점에 다시 계산해 비교한다 — 그 사이 관리자가
    정책을 바꾸면 예전 판정을 더 진행시키지 않고 차단한다(2026-08-22
    14차 지시 — "기존 승인의 전제가 바뀌면 승인 fingerprint 무효화")."""

    try:
        allowed_codes = sorted(json.loads(setting.allowed_provider_codes_json))
    except (TypeError, ValueError):
        allowed_codes = []

    snapshot = {
        "min_net_profit": setting.min_net_profit,
        "min_margin_rate": setting.min_margin_rate,
        "max_purchase_price": setting.max_purchase_price,
        "max_price_increase_rate": setting.max_price_increase_rate,
        "max_delivery_days": setting.max_delivery_days,
        "require_return_allowed": setting.require_return_allowed,
        "min_seller_trust_score": setting.min_seller_trust_score,
        "min_match_confidence": setting.min_match_confidence,
        "allowed_provider_codes": allowed_codes,
        "per_order_max_amount": setting.per_order_max_amount,
        "daily_purchase_limit_amount": setting.daily_purchase_limit_amount,
        "monthly_purchase_budget_amount": setting.monthly_purchase_budget_amount,
        "max_concurrent_orders": setting.max_concurrent_orders,
        "max_quantity_per_product": setting.max_quantity_per_product,
        "auto_execute_enabled": setting.auto_execute_enabled,
        "approval_required_amount_threshold": (
            setting.approval_required_amount_threshold
        ),
        "uncertain_handling_policy": setting.uncertain_handling_policy,
        "uncertain_auto_release_after_hours": (
            setting.uncertain_auto_release_after_hours
        ),
    }
    snapshot_json = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()


def compute_margin_check_fingerprint(
    check_input: "RetailPurchasePolicyCheckInput",
    setting: RetailPurchasePolicySetting,
) -> str:
    """2026-08-24 Section 3 — MARGIN_BELOW_MINIMUM 알림의 anti-spam
    지문. 실제 순이익 계산에 들어가는 금액 입력과 그 순간의 임계값
    (min_net_profit/min_margin_rate)만 묶는다 — compute_policy_
    fingerprint()(정책 설정 전체)와 달리 이 주문 하나의 실제 판정에
    쓰인 값만 대상이므로, 가격·매입가·배송비·수수료·임계값 중
    어느 하나라도 바뀌면 지문이 달라져 새 알림이 허용되고, 완전히
    동일한 값으로 재평가되면 같은 지문이라 중복 알림이 나가지
    않는다(마진이 회복됐다가 다시 미달되면 그 사이 최소 한 값이
    바뀌어야 재판정 자체가 의미 있으므로, 값이 바뀌면 자연히 새
    지문이 된다)."""

    snapshot = {
        "coupang_sale_amount": str(check_input.coupang_sale_amount),
        "retail_actual_amount": str(check_input.retail_actual_amount),
        "shipping_fee": str(check_input.shipping_fee),
        "coupang_fee_amount": str(check_input.coupang_fee_amount),
        "automation_cost": str(check_input.automation_cost),
        "return_reserve_amount": str(check_input.return_reserve_amount),
        "min_net_profit": setting.min_net_profit,
        "min_margin_rate": setting.min_margin_rate,
    }
    snapshot_json = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RetailPurchasePolicyCheckInput:

    provider_code: str
    match_result: SameProductMatchResult
    in_stock: bool | None
    estimated_delivery_days: int | None
    return_allowed: bool | None
    seller_trust_score: float | None
    # 순이익 계산 입력 — 전부 실제 값(쿠팡 원 주문 실 결제금액, 구매처
    # 실제 결제금액, 배송비, 쿠팡 수수료)이어야 한다. None이면 그
    # 항목을 계산할 수 없다는 뜻으로 EVIDENCE_REQUIRED 처리한다.
    coupang_sale_amount: Decimal | None
    retail_actual_amount: Decimal | None
    shipping_fee: Decimal | None
    coupang_fee_amount: Decimal | None
    # 설정 안 하면 0으로 취급(값이 "확인 불가"가 아니라 "적용 안 함"
    # 이므로 임의 채움과 다르다).
    automation_cost: Decimal = Decimal("0")
    return_reserve_amount: Decimal = Decimal("0")
    # 최초 제안 시점 예상 금액 — 가격 상승률 판정용(없으면 그 항목만
    # 건너뜀, 첫 조회이므로 상승률 자체가 아직 정의되지 않는다).
    expected_amount_at_proposal: Decimal | None = None
    # 상품별 최대 구매수량 정책 판정용 — 기본값 1은 기존 호출부(수량
    # 정책이 아직 없던 시점에 작성된 테스트)와의 하위호환을 위함이다.
    quantity: int = 1


@dataclass(frozen=True)
class RetailPurchasePolicyCheckResult:

    decision: str
    reasons: tuple[str, ...]
    expected_net_profit: Decimal | None
    expected_margin_rate: Decimal | None
    required_budget_amount: Decimal | None


class RetailPurchasePolicyService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = RetailPurchaseRepository(db)
        self.safety = SafetyService(db)
        self.funding = FundingService(db)

    # --------------------------------------------------
    # 설정
    # --------------------------------------------------

    def get_or_create_default_settings(
        self, company_id: int,
    ) -> RetailPurchasePolicySetting:
        """행이 없으면 안전측 기본값(허용 쇼핑몰 0개 = 전부 차단)으로
        생성한다 — 관리자가 명시적으로 설정을 채워야만 자동구매가
        가능해진다(정책 미설정 상태에서 조용히 전부 허용되는 사고를
        막는다)."""

        existing = self.repository.get_policy_setting(company_id)
        if existing is not None:
            return existing

        setting = RetailPurchasePolicySetting(
            company_id=company_id,
            min_net_profit=0, min_margin_rate=0,
            max_purchase_price=None, max_price_increase_rate=0.05,
            max_delivery_days=None, require_return_allowed=True,
            min_seller_trust_score=0.8, min_match_confidence=0.98,
            allowed_provider_codes_json="[]",
            per_order_max_amount=None, daily_purchase_limit_amount=None,
            monthly_purchase_budget_amount=None, max_concurrent_orders=None,
        )
        return self.repository.add_policy_setting(setting)

    def current_policy_fingerprint(self, company_id: int) -> str:
        """지금 이 순간의 정책 설정 fingerprint. run_policy_check() 시점
        저장해 둔 order.policy_fingerprint와 이후 단계(예산예약/견적/
        발주)에서 비교하는 용도로 쓴다."""

        setting = self.get_or_create_default_settings(company_id)
        return compute_policy_fingerprint(setting)

    @staticmethod
    def _allowed_provider_codes(setting: RetailPurchasePolicySetting) -> set[str]:

        import json

        try:
            codes = json.loads(setting.allowed_provider_codes_json)
        except (TypeError, ValueError):
            codes = []
        return set(codes) if isinstance(codes, list) else set()

    # --------------------------------------------------
    # 판단
    # --------------------------------------------------

    def evaluate(
        self, company_id: int, check_input: RetailPurchasePolicyCheckInput,
        *, now: datetime | None = None,
    ) -> RetailPurchasePolicyCheckResult:

        now = now or datetime.utcnow()
        setting = self.get_or_create_default_settings(company_id)
        reasons: list[str] = []

        # 1) EStop — 기존 전역 SafetyService 재사용, 중복 구현하지 않음.
        if self.safety.is_emergency_stop_active():
            return RetailPurchasePolicyCheckResult(
                decision=RetailPurchasePolicyDecision.BLOCK,
                reasons=(RetailPurchasePolicyReason.EMERGENCY_STOP_ACTIVE,),
                expected_net_profit=None, expected_margin_rate=None,
                required_budget_amount=None,
            )

        # 2) 허용 쇼핑몰.
        if check_input.provider_code not in self._allowed_provider_codes(setting):
            reasons.append(RetailPurchasePolicyReason.RETAILER_NOT_ALLOWED)

        # 3) 동일상품 판정 — BLOCKED 등급만 여기서 hard-block 사유를
        # 추가한다. NEEDS_REVIEW 등급(90~97%)은 사유 목록에 넣지
        # 않고, 아래 최종 판정에서 REQUIRE_REVIEW로만 낮춘다(BLOCK과
        # 구분되는 별도 결과여야 하므로 hard_block_reasons에 섞지
        # 않는다).
        match = check_input.match_result
        if match.tier == SameProductConfidenceTier.BLOCKED:
            reasons.append(
                RetailPurchasePolicyReason.CORE_ATTRIBUTE_MISMATCH
                if match.blocked_reason
                and match.blocked_reason.startswith("CORE_ATTRIBUTE_MISMATCH")
                else RetailPurchasePolicyReason.PRODUCT_MATCH_INSUFFICIENT,
            )

        # 3.5) 상품별 최대 구매수량.
        if (
            setting.max_quantity_per_product is not None
            and check_input.quantity > setting.max_quantity_per_product
        ):
            reasons.append(
                RetailPurchasePolicyReason.MAX_QUANTITY_PER_PRODUCT_EXCEEDED,
            )

        # 4) 재고.
        if check_input.in_stock is not True:
            reasons.append(RetailPurchasePolicyReason.OUT_OF_STOCK)

        # 5) 배송기한.
        if setting.max_delivery_days is not None:
            if check_input.estimated_delivery_days is None:
                reasons.append(RetailPurchasePolicyReason.EVIDENCE_REQUIRED)
            elif check_input.estimated_delivery_days > setting.max_delivery_days:
                reasons.append(
                    RetailPurchasePolicyReason.DELIVERY_DEADLINE_EXCEEDED,
                )

        # 6) 반품 가능 여부.
        if setting.require_return_allowed and check_input.return_allowed is not True:
            reasons.append(RetailPurchasePolicyReason.RETURN_NOT_ALLOWED)

        # 7) 판매자 신뢰도.
        if check_input.seller_trust_score is None:
            reasons.append(RetailPurchasePolicyReason.EVIDENCE_REQUIRED)
        elif check_input.seller_trust_score < setting.min_seller_trust_score:
            reasons.append(RetailPurchasePolicyReason.SELLER_TRUST_BELOW_MINIMUM)

        # 8) 순이익·마진율 — 필수 입력 중 하나라도 없으면 계산 자체를
        # 하지 않는다(0으로 채우지 않음).
        net_profit: Decimal | None = None
        margin_rate: Decimal | None = None
        required_budget: Decimal | None = None

        required_money_fields = (
            check_input.coupang_sale_amount, check_input.retail_actual_amount,
            check_input.shipping_fee, check_input.coupang_fee_amount,
        )
        if any(v is None for v in required_money_fields):
            reasons.append(RetailPurchasePolicyReason.EVIDENCE_REQUIRED)
        else:
            net_profit = (
                check_input.coupang_sale_amount
                - check_input.retail_actual_amount
                - check_input.shipping_fee
                - check_input.coupang_fee_amount
                - check_input.automation_cost
                - check_input.return_reserve_amount
            )
            margin_rate = (
                (net_profit / check_input.coupang_sale_amount)
                if check_input.coupang_sale_amount > 0 else Decimal("0")
            )
            required_budget = (
                check_input.retail_actual_amount + check_input.shipping_fee
            )

            if net_profit < Decimal(str(setting.min_net_profit)):
                reasons.append(RetailPurchasePolicyReason.MIN_PROFIT_NOT_MET)
            if margin_rate < Decimal(str(setting.min_margin_rate)):
                reasons.append(RetailPurchasePolicyReason.MIN_MARGIN_RATE_NOT_MET)

            if (
                setting.max_purchase_price is not None
                and check_input.retail_actual_amount
                > Decimal(str(setting.max_purchase_price))
            ):
                reasons.append(
                    RetailPurchasePolicyReason.MAX_PURCHASE_PRICE_EXCEEDED,
                )

            if setting.per_order_max_amount is not None and (
                required_budget > Decimal(str(setting.per_order_max_amount))
            ):
                reasons.append(RetailPurchasePolicyReason.LIMIT_EXCEEDED)

            # 9) 가격 상승률(최초 제안 시점 대비).
            if (
                check_input.expected_amount_at_proposal is not None
                and check_input.expected_amount_at_proposal > 0
            ):
                increase_rate = (
                    (
                        check_input.retail_actual_amount
                        - check_input.expected_amount_at_proposal
                    ) / check_input.expected_amount_at_proposal
                )
                if increase_rate > Decimal(str(setting.max_price_increase_rate)):
                    reasons.append(
                        RetailPurchasePolicyReason.PRICE_INCREASE_RATE_EXCEEDED,
                    )

            # 10) 매입예산(FundingAccount 재사용).
            account = self.funding.repository.get_account_by_company(company_id)
            if account is None:
                reasons.append(RetailPurchasePolicyReason.BUDGET_INSUFFICIENT)
            else:
                available = Decimal(str(self.funding.available_amount(account)))
                if available < required_budget:
                    reasons.append(
                        RetailPurchasePolicyReason.BUDGET_INSUFFICIENT,
                    )

        # 11) 일일·월간 구매한도.
        if setting.daily_purchase_limit_amount is not None:
            since = now - timedelta(days=1)
            spent_today = self.repository.sum_ordered_amount_since(
                company_id, since,
            )
            if spent_today >= setting.daily_purchase_limit_amount:
                reasons.append(RetailPurchasePolicyReason.LIMIT_EXCEEDED)

        if setting.monthly_purchase_budget_amount is not None:
            since_month = now - timedelta(days=30)
            spent_month = self.repository.sum_ordered_amount_since(
                company_id, since_month,
            )
            if spent_month >= setting.monthly_purchase_budget_amount:
                reasons.append(RetailPurchasePolicyReason.LIMIT_EXCEEDED)

        # 12) 동시 구매 건수.
        if setting.max_concurrent_orders is not None:
            open_count = self.repository.count_open_orders(company_id)
            if open_count >= setting.max_concurrent_orders:
                reasons.append(
                    RetailPurchasePolicyReason.CONCURRENT_ORDER_LIMIT_EXCEEDED,
                )

        # --------------------------------------------------
        # 최종 판정. BLOCK 사유가 하나라도 있으면 BLOCK, 아니면
        # NEEDS_REVIEW 등급의 동일상품 판정만 REQUIRE_REVIEW, 그 외는
        # ALLOW(정책 범위 안 — 사용자 개별 승인 없이 자동 실행 가능).
        # --------------------------------------------------

        hard_block_reasons = {
            RetailPurchasePolicyReason.CORE_ATTRIBUTE_MISMATCH,
            RetailPurchasePolicyReason.OUT_OF_STOCK,
            RetailPurchasePolicyReason.RETAILER_NOT_ALLOWED,
            RetailPurchasePolicyReason.RETURN_NOT_ALLOWED,
            RetailPurchasePolicyReason.DELIVERY_DEADLINE_EXCEEDED,
            RetailPurchasePolicyReason.SELLER_TRUST_BELOW_MINIMUM,
            RetailPurchasePolicyReason.MIN_PROFIT_NOT_MET,
            RetailPurchasePolicyReason.MIN_MARGIN_RATE_NOT_MET,
            RetailPurchasePolicyReason.MAX_PURCHASE_PRICE_EXCEEDED,
            RetailPurchasePolicyReason.PRICE_INCREASE_RATE_EXCEEDED,
            RetailPurchasePolicyReason.BUDGET_INSUFFICIENT,
            RetailPurchasePolicyReason.LIMIT_EXCEEDED,
            RetailPurchasePolicyReason.CONCURRENT_ORDER_LIMIT_EXCEEDED,
            RetailPurchasePolicyReason.MAX_QUANTITY_PER_PRODUCT_EXCEEDED,
            RetailPurchasePolicyReason.EVIDENCE_REQUIRED,
            RetailPurchasePolicyReason.PRODUCT_MATCH_INSUFFICIENT,
        }

        if any(r in hard_block_reasons for r in reasons):
            decision = RetailPurchasePolicyDecision.BLOCK
        elif match.tier == SameProductConfidenceTier.NEEDS_REVIEW:
            decision = RetailPurchasePolicyDecision.REQUIRE_REVIEW
        elif match.confidence < setting.min_match_confidence:
            # 동일상품 판정 자체는 AUTO_CANDIDATE(고정 98% 기준)라도,
            # 이 회사가 그보다 더 엄격한 기준을 설정했다면 사람 확인
            # 단계로 낮춘다(D 섹션의 98%/90% 경계는 전역 고정값이고,
            # min_match_confidence는 회사가 그 위에 추가로 얹는 상향
            # 조정 전용 — 완화 방향으로는 쓰지 않는다).
            decision = RetailPurchasePolicyDecision.REQUIRE_REVIEW
        elif not setting.auto_execute_enabled:
            # 관리자가 "자동 실행"을 껐다 — 정책 범위 안이라도 매 건
            # 사람 승인을 요구한다(2026-08-22 14차 지시 작업 2).
            decision = RetailPurchasePolicyDecision.REQUIRE_REVIEW
        elif (
            setting.approval_required_amount_threshold is not None
            and required_budget is not None
            and required_budget
            > Decimal(str(setting.approval_required_amount_threshold))
        ):
            # 자동 실행은 켜져 있지만, 이 건의 필요예산이 관리자가
            # 지정한 "승인 필요 금액 기준"을 넘으면 그 건만 사람
            # 확인으로 낮춘다.
            decision = RetailPurchasePolicyDecision.REQUIRE_REVIEW
        else:
            decision = RetailPurchasePolicyDecision.ALLOW

        return RetailPurchasePolicyCheckResult(
            decision=decision, reasons=tuple(dict.fromkeys(reasons)),
            expected_net_profit=net_profit, expected_margin_rate=margin_rate,
            required_budget_amount=required_budget,
        )


__all__ = [
    "RetailPurchasePolicyCheckInput",
    "RetailPurchasePolicyCheckResult",
    "RetailPurchasePolicyService",
    "compute_margin_check_fingerprint",
]
