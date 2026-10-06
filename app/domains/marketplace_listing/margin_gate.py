"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/margin_gate.py

비용 완결성과 최소마진 기준을 한 곳에서 판정한다. 7단계 사전검사,
8단계 승인, 실제 쿠팡 전송 직전 점검이 전부 이 함수를 호출하므로
"화면에서는 통과했는데 최종 실행에서는 다른 기준"이 생기지 않는다.

판정은 저장된 입력(economics_input_json)에서 반올림 전 Decimal로
다시 계산한다. 저장된 결과(economics_result_json)의 반올림된
margin_rate/margin_amount는 화면 표시용이라 판정에 쓰지 않는다.
=========================================================
"""

import json
from dataclasses import dataclass
from decimal import ROUND_DOWN
from decimal import ROUND_UP
from decimal import Decimal

from pydantic import ValidationError

from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.margin_calculator import (
    calculate_economics,
)
from app.domains.marketplace_listing.margin_calculator import (
    min_margin_shortfall,
)

MISSING = "MISSING"
PROVISIONAL = "PROVISIONAL"
BELOW_TARGET = "BELOW_TARGET"
MET = "MET"


@dataclass(frozen=True)
class MarginGate:
    status: str
    missing_cost_fields: list[str]
    margin_rate_display: Decimal | None
    shortfall_amount: Decimal | None


def evaluate_margin_gate(
    economics_input_json: str | None,
    marketplace_account_id: int,
    target_margin_rate: Decimal,
) -> MarginGate:

    try:
        entries = json.loads(economics_input_json or "[]")
    except json.JSONDecodeError:
        entries = []
    entry = next(
        (
            x for x in entries
            if x.get("marketplace_account_id") == marketplace_account_id
        ),
        None,
    )
    if entry is None:
        return MarginGate(MISSING, [], None, None)
    try:
        item = EconomicsInputItem(**entry)
    except (ValidationError, TypeError):
        return MarginGate(MISSING, [], None, None)

    result = calculate_economics(item)
    if result.is_provisional:
        return MarginGate(
            PROVISIONAL, list(result.missing_cost_fields), None, None,
        )

    shortfall = min_margin_shortfall(item, target_margin_rate)
    if item.sale_price > 0:
        # 표시용 마진율은 내림한다 — 17.995%가 "0.1800"으로 보이는
        # 일이 없도록 한다(판정 자체는 아래 shortfall만 쓴다).
        rate_display = (
            (item.sale_price * target_margin_rate - shortfall)
            / item.sale_price
        ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    else:
        rate_display = Decimal("0")

    if shortfall > 0:
        return MarginGate(
            BELOW_TARGET, [], rate_display,
            shortfall.quantize(Decimal("0.01"), rounding=ROUND_UP),
        )
    return MarginGate(MET, [], rate_display, Decimal("0.00"))
