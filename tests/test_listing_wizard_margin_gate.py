"""
=========================================================
Homez OS

File : tests/test_listing_wizard_margin_gate.py

2026-10-04 — 최소마진 18% 판정(margin_gate / min_margin_shortfall)의
반올림 전 판정과 경계값을 DB 없이 Decimal로만 검증한다.
=========================================================
"""

import json
import unittest
from decimal import Decimal

from app.domains.marketplace_listing import margin_gate
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.margin_calculator import (
    calculate_economics,
)
from app.domains.marketplace_listing.margin_calculator import (
    min_margin_shortfall,
)

TARGET = Decimal("0.18")


def _full(cost, sale="10000", **overrides):

    base = dict(
        marketplace_account_id=1, cost_of_goods=Decimal(cost),
        sale_price=Decimal(sale), channel_fee_rate=Decimal("0"),
        payment_fee_rate=Decimal("0"), shipping_cost=Decimal("0"),
        packaging_cost=Decimal("0"), return_reserve_rate=Decimal("0"),
        tax_basis_rate=Decimal("0"),
    )
    base.update(overrides)
    return EconomicsInputItem(**base)


def _json(item):

    return json.dumps([json.loads(item.model_dump_json())])


class MinMarginShortfallTestCase(unittest.TestCase):

    def test_exactly_at_target_has_zero_shortfall(self):

        self.assertEqual(min_margin_shortfall(_full("8200"), TARGET), Decimal("0"))

    def test_just_below_target_has_positive_shortfall_even_if_display_rounds_up(self):

        item = _full("8200.50")  # 마진율 17.995%
        self.assertEqual(calculate_economics(item).margin_rate, Decimal("0.1800"))
        self.assertGreater(min_margin_shortfall(item, TARGET), 0)

    def test_just_above_target_has_negative_shortfall(self):

        self.assertLess(min_margin_shortfall(_full("8199.50"), TARGET), 0)

    def test_current_product_numbers(self):
        """판매가 12,900원 기준 18%는 2,322원이다. 수수료 9.6%·반품준비율
        3%·배송비 3,000원·원가 5,050원(포장비·세금 미반영)이면 잔액은
        3,224.60원이고 기준까지 여유는 902.60원이다."""

        item = _full(
            "5050", sale="12900", channel_fee_rate=Decimal("0.096"),
            shipping_cost=Decimal("3000"), return_reserve_rate=Decimal("0.03"),
        )
        result = calculate_economics(item)
        self.assertEqual(result.margin_amount, Decimal("3224.60"))
        self.assertEqual(TARGET * Decimal("12900"), Decimal("2322.00"))
        self.assertEqual(
            -min_margin_shortfall(item, TARGET), Decimal("902.6000"),
        )


class PurchaseFeeOptInFieldTestCase(unittest.TestCase):
    """발주 예상비용 사용 선택은 기본 False이고 마진 계산·판정에 영향이 없다."""

    def test_default_is_false_and_does_not_change_the_margin(self):

        plain = _full("8200")
        opted = _full("8200", use_channel_fee_for_purchase_estimate=True)
        self.assertFalse(plain.use_channel_fee_for_purchase_estimate)
        self.assertTrue(opted.use_channel_fee_for_purchase_estimate)
        self.assertEqual(
            calculate_economics(plain).margin_amount,
            calculate_economics(opted).margin_amount,
        )
        self.assertEqual(
            margin_gate.evaluate_margin_gate(_json(opted), 1, TARGET).status,
            margin_gate.MET,
        )


class MarginGateTestCase(unittest.TestCase):

    def test_statuses(self):

        self.assertEqual(
            margin_gate.evaluate_margin_gate(_json(_full("8200")), 1, TARGET).status,
            margin_gate.MET,
        )
        self.assertEqual(
            margin_gate.evaluate_margin_gate(_json(_full("8200.50")), 1, TARGET).status,
            margin_gate.BELOW_TARGET,
        )
        self.assertEqual(
            margin_gate.evaluate_margin_gate(_json(_full("8199.50")), 1, TARGET).status,
            margin_gate.MET,
        )

    def test_below_target_reports_shortfall_and_never_displays_the_target(self):

        gate = margin_gate.evaluate_margin_gate(
            _json(_full("8200.50")), 1, TARGET,
        )
        self.assertEqual(gate.shortfall_amount, Decimal("0.50"))
        self.assertLess(gate.margin_rate_display, TARGET)

    def test_unconfirmed_costs_never_pass_on_balance_alone(self):
        """미확정 비용이 있으면 확인된 비용 기준 마진율이 90%여도
        PROVISIONAL이다 — 기준 충족으로 판정하지 않는다."""

        item = EconomicsInputItem(
            marketplace_account_id=1, cost_of_goods=Decimal("1000"),
            sale_price=Decimal("10000"),
        )
        gate = margin_gate.evaluate_margin_gate(_json(item), 1, TARGET)
        self.assertEqual(gate.status, margin_gate.PROVISIONAL)
        self.assertIn("channel_fee_rate", gate.missing_cost_fields)

    def test_missing_account_or_input(self):

        self.assertEqual(
            margin_gate.evaluate_margin_gate(_json(_full("8200")), 2, TARGET).status,
            margin_gate.MISSING,
        )
        self.assertEqual(
            margin_gate.evaluate_margin_gate(None, 1, TARGET).status,
            margin_gate.MISSING,
        )


if __name__ == "__main__":
    unittest.main()
