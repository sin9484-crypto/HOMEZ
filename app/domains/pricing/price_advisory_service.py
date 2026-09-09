"""
=========================================================
Homez OS

File : app/domains/pricing/price_advisory_service.py

Gate AI-F1-A(2026-08-22 CTO 지시) — 가격 제안. 기존 PricingService
(실제 가격 변경 요청 CRUD)는 전혀 건드리지 않는다 — 이 서비스는 읽기
전용 계산이며, 실제 가격을 직접 바꾸지 않는다. 계산 자체는
`app/domains/marketplace_listing/margin_calculator.py::
calculate_economics()`를 그대로 재사용한다(같은 공식을 다시 만들지
않는다) — 권장가는 그 공식을 역산해서 구하고, 반드시 같은 함수로
재검증한 뒤에만 결과로 반환한다(반올림 오차까지 실제 값으로 확인).

capability_code: PRICING_INVENTORY.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP
from decimal import Decimal

from app.core.exceptions import BadRequestException
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
from app.domains.channel_policy.constants import ChannelPolicyResult
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.margin_calculator import (
    calculate_economics,
)

_MONEY = Decimal("0.01")


@dataclass(frozen=True)
class PriceSuggestionInput:

    current_sale_price: Decimal
    cost_of_goods: Decimal | None
    channel_fee_rate: Decimal | None
    payment_fee_rate: Decimal | None
    shipping_cost: Decimal | None
    packaging_cost: Decimal | None
    ad_cost: Decimal | None
    return_reserve_rate: Decimal | None
    tax_basis_rate: Decimal | None
    target_margin_rate: Decimal | None
    min_price: Decimal | None = None
    max_change_rate: Decimal | None = None
    # 정책 검사 대상을 명시하면(선택) BLOCKED 여부를 확인한다 —
    # 넘기지 않으면 정책 검사를 건너뛴다(호출자 책임 — 예: 아직
    # 채널을 정하지 않은 순수 What-if 계산).
    product_candidate_id: int | None = None
    channel: str | None = None


@dataclass(frozen=True)
class PriceSuggestion:

    recommended_sale_price: Decimal | None
    expected_contribution_margin: Decimal | None
    expected_margin_rate: Decimal | None
    change_vs_current: Decimal | None
    change_rate_vs_current: Decimal | None
    break_even_price: Decimal | None
    missing_cost_fields: list[str]
    is_provisional: bool
    reason: list[str]
    risk_warnings: list[str]
    policy_blocked: bool


class PriceAdvisoryService:

    def __init__(self, db):

        self.db = db
        self.channel_policy_service = ChannelPolicyService(db)
        self.proposed_action_service = ProposedActionService(db)

    def suggest(
        self, company_id: int, data: PriceSuggestionInput,
        *, now: datetime | None = None,
    ) -> tuple[PriceSuggestion, AIResultEnvelope]:

        require_active_capability(CapabilityCode.PRICING_INVENTORY)

        now = now or datetime.utcnow()
        reason: list[str] = []
        risk_warnings: list[str] = []

        # 정책상 판매 불가 상품에는 가격을 제안하지 않는다(추정으로
        # 우회하지 않는다) — 채널·후보를 명시한 경우에만 확인한다.
        policy_blocked = False
        if data.product_candidate_id is not None and data.channel is not None:
            status = self.channel_policy_service.get_current_status(
                company_id, data.product_candidate_id, data.channel,
            )
            if (
                status is not None
                and status.result == ChannelPolicyResult.CHANNEL_POLICY_BLOCKED
            ):
                policy_blocked = True

        cost_fields = {
            "cost_of_goods": data.cost_of_goods,
            "channel_fee_rate": data.channel_fee_rate,
            "payment_fee_rate": data.payment_fee_rate,
            "shipping_cost": data.shipping_cost,
            "packaging_cost": data.packaging_cost,
            "ad_cost": data.ad_cost,
            "return_reserve_rate": data.return_reserve_rate,
            "tax_basis_rate": data.tax_basis_rate,
        }
        missing_cost_fields = [
            name for name, value in cost_fields.items() if value is None
        ]
        is_provisional = bool(missing_cost_fields)

        if policy_blocked:
            suggestion = PriceSuggestion(
                recommended_sale_price=None,
                expected_contribution_margin=None,
                expected_margin_rate=None,
                change_vs_current=None,
                change_rate_vs_current=None,
                break_even_price=None,
                missing_cost_fields=missing_cost_fields,
                is_provisional=is_provisional,
                reason=["POLICY_BLOCKED: 채널 정책상 판매 불가 상품입니다."],
                risk_warnings=[],
                policy_blocked=True,
            )
            envelope = build_ai_result_envelope(
                capability_code=CapabilityCode.PRICING_INVENTORY,
                result_type=AIResultType.POLICY_BLOCKED,
                decision="PRICE_SUGGESTION_BLOCKED",
                blocking_rules=["CHANNEL_POLICY_BLOCKED"],
                execution_allowed=False,
                evaluated_at=now,
            )
            return suggestion, envelope

        # 누락 비용은 절대 0으로 대체하지 않는다 — 계산 자체가
        # provisional(잠정)임을 명시하고, 확정 수식 계산은 가능한
        # 필드만으로 최선을 다해 시도하되 0을 "실제 값"처럼 쓰지
        # 않기 위해 없는 값은 그대로 0으로 넣는 대신 provisional
        # 표시로 그 사실을 분리해 전달한다(margin_calculator 자체는
        # None을 받지 않으므로 계산용으로만 0을 넣고, 결과 해석은
        # is_provisional로 구분한다 — CA-3/product_selection과 동일
        # 원칙: "계산은 하되 확정으로 위장하지 않는다").
        item = EconomicsInputItem(
            marketplace_account_id=0,
            sale_price=data.current_sale_price,
            cost_of_goods=data.cost_of_goods or Decimal("0"),
            channel_fee_rate=data.channel_fee_rate or Decimal("0"),
            payment_fee_rate=data.payment_fee_rate or Decimal("0"),
            shipping_cost=data.shipping_cost or Decimal("0"),
            packaging_cost=data.packaging_cost or Decimal("0"),
            ad_cost=data.ad_cost or Decimal("0"),
            return_reserve_rate=data.return_reserve_rate or Decimal("0"),
            tax_basis_rate=data.tax_basis_rate or Decimal("0"),
        )
        current_calc = calculate_economics(item)

        recommended_sale_price = None
        expected_contribution_margin = None
        expected_margin_rate = None
        change_vs_current = None
        change_rate_vs_current = None

        if data.target_margin_rate is None:
            reason.append(
                "회사 목표 마진율이 없어 권장 판매가를 계산하지 않았습니다"
                "(손익분기가만 참고로 제공합니다).",
            )
        else:
            fixed_costs = (
                (data.cost_of_goods or Decimal("0"))
                + (data.shipping_cost or Decimal("0"))
                + (data.packaging_cost or Decimal("0"))
                + (data.ad_cost or Decimal("0"))
            )
            rate_sum = (
                (data.channel_fee_rate or Decimal("0"))
                + (data.payment_fee_rate or Decimal("0"))
                + (data.return_reserve_rate or Decimal("0"))
                + (data.tax_basis_rate or Decimal("0"))
            )
            denominator = (Decimal("1") - rate_sum) - data.target_margin_rate

            if denominator <= 0:
                reason.append(
                    "채널·결제 수수료 합계가 이미 목표 마진율 공간을 "
                    "넘어서 수학적으로 도달 불가능한 목표입니다 — "
                    "권장가를 계산하지 않았습니다.",
                )
            else:
                raw_price = (fixed_costs / denominator).quantize(
                    _MONEY, rounding=ROUND_HALF_UP,
                )

                bounded_price = raw_price
                if data.min_price is not None and bounded_price < data.min_price:
                    risk_warnings.append(
                        f"계산된 권장가({raw_price})가 최소 가격"
                        f"({data.min_price}) 미만이라 최소 가격으로 "
                        "상향 조정했습니다.",
                    )
                    bounded_price = data.min_price

                if data.max_change_rate is not None:
                    max_delta = (
                        data.current_sale_price * data.max_change_rate
                    )
                    upper = data.current_sale_price + max_delta
                    lower = data.current_sale_price - max_delta
                    if bounded_price > upper:
                        risk_warnings.append(
                            f"계산된 권장가({bounded_price})가 허용 최대 "
                            f"변경률을 초과해 {upper.quantize(_MONEY)}으로 "
                            "제한했습니다.",
                        )
                        bounded_price = upper.quantize(_MONEY)
                    elif bounded_price < lower:
                        risk_warnings.append(
                            f"계산된 권장가({bounded_price})가 허용 최대 "
                            f"하락률을 초과해 {lower.quantize(_MONEY)}으로 "
                            "제한했습니다.",
                        )
                        bounded_price = lower.quantize(_MONEY)

                # 반올림·상하한 조정 이후의 실제 마진을 반드시 같은
                # 공식으로 재검증한다 — 역산값을 그대로 확정 결과로
                # 쓰지 않는다.
                verify_item = EconomicsInputItem(
                    marketplace_account_id=0,
                    sale_price=bounded_price,
                    cost_of_goods=item.cost_of_goods,
                    channel_fee_rate=item.channel_fee_rate,
                    payment_fee_rate=item.payment_fee_rate,
                    shipping_cost=item.shipping_cost,
                    packaging_cost=item.packaging_cost,
                    ad_cost=item.ad_cost,
                    return_reserve_rate=item.return_reserve_rate,
                    tax_basis_rate=item.tax_basis_rate,
                )
                verified = calculate_economics(verify_item)

                recommended_sale_price = bounded_price
                expected_contribution_margin = verified.margin_amount
                expected_margin_rate = verified.margin_rate
                change_vs_current = (
                    bounded_price - data.current_sale_price
                ).quantize(_MONEY)
                if data.current_sale_price != 0:
                    change_rate_vs_current = (
                        change_vs_current / data.current_sale_price
                    ).quantize(Decimal("0.0001"))

                reason.append(
                    f"목표 마진율 {data.target_margin_rate}에 도달하려면 "
                    f"판매가 {bounded_price}이 필요합니다"
                    f"(현재 대비 {change_vs_current:+}).",
                )

        if is_provisional:
            risk_warnings.append(
                f"다음 비용 항목이 입력되지 않아 잠정 계산입니다: "
                f"{', '.join(missing_cost_fields)}",
            )

        suggestion = PriceSuggestion(
            recommended_sale_price=recommended_sale_price,
            expected_contribution_margin=expected_contribution_margin,
            expected_margin_rate=expected_margin_rate,
            change_vs_current=change_vs_current,
            change_rate_vs_current=change_rate_vs_current,
            break_even_price=current_calc.break_even_price,
            missing_cost_fields=missing_cost_fields,
            is_provisional=is_provisional,
            reason=reason,
            risk_warnings=risk_warnings,
            policy_blocked=False,
        )

        result_type = (
            AIResultType.EVIDENCE_REQUIRED if is_provisional
            else AIResultType.CALCULATED_RESULT
        )

        envelope = build_ai_result_envelope(
            capability_code=CapabilityCode.PRICING_INVENTORY,
            result_type=result_type,
            decision=(
                "PRICE_CHANGE_SUGGESTED" if recommended_sale_price is not None
                else "PRICE_SUGGESTION_NOT_COMPUTABLE"
            ),
            calculated_values={
                "recommended_sale_price": (
                    str(recommended_sale_price)
                    if recommended_sale_price is not None else None
                ),
                "expected_margin_rate": (
                    str(expected_margin_rate)
                    if expected_margin_rate is not None else None
                ),
                "break_even_price": (
                    str(current_calc.break_even_price)
                    if current_calc.break_even_price is not None else None
                ),
            },
            assumptions=reason,
            missing_evidence=[
                f"missing_cost_field:{f}" for f in missing_cost_fields
            ],
            confidence=(0.4 if is_provisional else 0.8),
            recommended_actions=(
                ["PRICE_CHANGE_PROPOSAL"]
                if recommended_sale_price is not None else []
            ),
            execution_allowed=False,
            evaluated_at=now,
        )

        return suggestion, envelope

    def propose_price_change(
        self,
        company_id: int,
        listing_id: int,
        data: PriceSuggestionInput,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> tuple[PriceSuggestion, AIResultEnvelope, ProposedAction | None]:
        """
        Gate AI-F8(2026-08-22) — 계산된 제안이 실제로 실행 가능한
        값을 냈을 때만(정책 차단 아님, 권장가 계산 성공) `ProposedAction
        (REVIEW_REQUIRED)`을 만든다. 이 메서드도 실제 가격을 바꾸지
        않는다 — 사람이 이 제안을 승인한 뒤에만 기존
        `PricingService.request_price_change()`가 실행된다(호출부의
        책임, 이 서비스는 그 실행을 직접 수행하지 않는다).
        """

        suggestion, envelope = self.suggest(company_id, data, now=now)

        if suggestion.recommended_sale_price is None:
            return suggestion, envelope, None

        action, _dup = self.proposed_action_service.create(
            company_id=company_id,
            capability_code=CapabilityCode.PRICING_INVENTORY,
            action_type="PRICE_CHANGE_PROPOSAL",
            target_entity=f"marketplace_listing:{listing_id}",
            proposed_payload={
                "requested_sale_price": str(suggestion.recommended_sale_price),
                "current_sale_price": str(data.current_sale_price),
            },
            reason="; ".join(suggestion.reason) or "가격 제안 계산 결과",
            evidence=suggestion.risk_warnings,
            risk_level=("HIGH" if suggestion.risk_warnings else "MEDIUM"),
            approval_required=True,
            idempotency_key=idempotency_key,
            status=ProposedActionStatus.REVIEW_REQUIRED,
        )

        return suggestion, envelope, action


__all__ = [
    "PriceAdvisoryService", "PriceSuggestionInput", "PriceSuggestion",
]
