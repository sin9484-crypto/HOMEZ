"""
=========================================================
Homez OS

File : tests/test_listing_wizard_margin_calculator.py

Gate I(2026-08-08) — 마진 계산기 순수 함수 검증. DB/서버 없이 순수
Decimal 계산만 확인한다.
=========================================================
"""

import unittest
from decimal import Decimal

from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.margin_calculator import (
    calculate_economics,
)
from app.domains.marketplace_listing.margin_calculator import (
    calculate_economics_batch,
)


def _item(**overrides) -> EconomicsInputItem:

    defaults = dict(
        marketplace_account_id=1,
        cost_of_goods=Decimal("5000"),
        sale_price=Decimal("10000"),
        channel_fee_rate=Decimal("0.10"),
        payment_fee_rate=Decimal("0.02"),
        shipping_cost=Decimal("1000"),
        packaging_cost=Decimal("200"),
        ad_cost=Decimal("300"),
        return_reserve_rate=Decimal("0.01"),
        tax_basis_rate=Decimal("0.03"),
    )
    defaults.update(overrides)
    return EconomicsInputItem(**defaults)


class MarginCalculatorTestCase(unittest.TestCase):

    def test_known_values_produce_exact_expected_result(self):

        result = calculate_economics(_item())

        self.assertEqual(result.expected_revenue, Decimal("10000.00"))
        self.assertEqual(result.total_cost, Decimal("8100.00"))
        self.assertEqual(result.margin_amount, Decimal("1900.00"))
        self.assertEqual(result.margin_rate, Decimal("0.1900"))
        self.assertEqual(result.break_even_price, Decimal("7738.10"))

    def test_zero_fees_and_costs_gives_full_margin(self):

        result = calculate_economics(_item(
            cost_of_goods=Decimal("0"), channel_fee_rate=Decimal("0"),
            payment_fee_rate=Decimal("0"), shipping_cost=Decimal("0"),
            packaging_cost=Decimal("0"), ad_cost=Decimal("0"),
            return_reserve_rate=Decimal("0"), tax_basis_rate=Decimal("0"),
        ))

        self.assertEqual(result.total_cost, Decimal("0.00"))
        self.assertEqual(result.margin_amount, Decimal("10000.00"))
        self.assertEqual(result.margin_rate, Decimal("1.0000"))
        self.assertEqual(result.break_even_price, Decimal("0.00"))

    def test_negative_margin_when_costs_exceed_price(self):

        result = calculate_economics(_item(
            sale_price=Decimal("1000"), cost_of_goods=Decimal("5000"),
        ))

        self.assertLess(result.margin_amount, Decimal("0"))
        self.assertLess(result.margin_rate, Decimal("0"))

    def test_zero_sale_price_does_not_raise_and_margin_rate_is_zero(self):
        """
        0으로 나누지 않는다 — sale_price=0이면 margin_rate는 "계산
        불능" sentinel로 0 고정(실제 0% 마진이라는 의미가 아님, 이
        경우 자체는 사전검사가 별도로 BLOCKING 처리한다).
        """

        result = calculate_economics(_item(sale_price=Decimal("0")))

        self.assertEqual(result.expected_revenue, Decimal("0.00"))
        self.assertEqual(result.margin_rate, Decimal("0"))

    def test_break_even_is_none_when_rate_sum_reaches_one(self):

        result = calculate_economics(_item(
            channel_fee_rate=Decimal("0.5"), payment_fee_rate=Decimal("0.3"),
            return_reserve_rate=Decimal("0.15"),
            tax_basis_rate=Decimal("0.05"),
        ))

        self.assertIsNone(result.break_even_price)

    def test_break_even_is_none_when_rate_sum_exceeds_one(self):

        result = calculate_economics(_item(
            channel_fee_rate=Decimal("0.6"), payment_fee_rate=Decimal("0.5"),
        ))

        self.assertIsNone(result.break_even_price)

    def test_amounts_are_quantized_to_two_decimals(self):

        result = calculate_economics(_item(
            sale_price=Decimal("9999.999"),
            channel_fee_rate=Decimal("0.0333"),
        ))

        for value in (
            result.expected_revenue, result.total_cost,
            result.margin_amount,
        ):
            self.assertEqual(value.as_tuple().exponent, -2)

    def test_margin_rate_is_quantized_to_four_decimals(self):

        result = calculate_economics(_item())

        self.assertEqual(result.margin_rate.as_tuple().exponent, -4)

    def test_batch_preserves_order_and_account_ids(self):

        items = [
            _item(marketplace_account_id=1, sale_price=Decimal("10000")),
            _item(marketplace_account_id=2, sale_price=Decimal("20000")),
        ]
        results = calculate_economics_batch(items)

        self.assertEqual(
            [r.marketplace_account_id for r in results], [1, 2],
        )
        self.assertEqual(results[1].expected_revenue, Decimal("20000.00"))

    def test_result_is_locale_invariant_pure_decimal(self):
        """
        결과 값 자체는 지역화되지 않은 순수 Decimal이어야 한다 — 문자열
        포맷(천단위 구분자·통화 기호 등)은 화면 전담이고 이 모듈은
        절대 관여하지 않는다.
        """

        result = calculate_economics(_item())

        self.assertIsInstance(result.margin_amount, Decimal)
        self.assertNotIn(",", str(result.margin_amount))


if __name__ == "__main__":
    unittest.main()
