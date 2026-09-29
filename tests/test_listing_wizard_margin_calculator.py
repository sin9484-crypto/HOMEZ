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
from app.domains.marketplace_listing.margin_calculator import (
    derive_sale_price_for_margin_rate,
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

    def test_none_fields_are_excluded_not_treated_as_zero(self):
        """
        2026-09-28(45차) — None(미확인)은 0원 확정과 다르다. 채널
        수수료·배송비 등을 None으로 두면 total_cost는 cost_of_goods만
        반영해야 한다(0으로 대체돼 같은 숫자가 나올 수는 있지만, 그
        의미가 다르다는 것을 is_provisional/missing_cost_fields로
        구분해야 한다).

        2026-09-28(47차) — ad_cost는 여기 포함하지 않는다. "미확인"이
        아니라 회사 정책상 "제외"이므로 missing_cost_fields가 아니라
        excluded_cost_fields로 간다(아래 별도 테스트).
        """

        result = calculate_economics(_item(
            channel_fee_rate=None, payment_fee_rate=None,
            shipping_cost=None, packaging_cost=None, ad_cost=None,
            return_reserve_rate=None, tax_basis_rate=None,
        ))

        self.assertEqual(result.total_cost, Decimal("5000.00"))
        self.assertTrue(result.is_provisional)
        self.assertEqual(
            set(result.missing_cost_fields),
            {
                "channel_fee_rate", "payment_fee_rate", "shipping_cost",
                "packaging_cost", "return_reserve_rate",
                "tax_basis_rate",
            },
        )
        self.assertEqual(result.excluded_cost_fields, ["ad_cost"])

    def test_ad_cost_none_is_policy_excluded_not_missing(self):
        """
        2026-09-28(47차) — HOMEZ_USER_OPERATION_SETTINGS.md §6(기존
        확정 정책): "광고비는 초기 이익 계산에서 제외한다." ad_cost만
        None이고 나머지 6개가 전부 확인된 경우, is_provisional이
        서면 안 된다(광고비는 사용자가 확인해야 할 미확인 항목이
        아니라 이미 결정된 제외 항목이므로 — 이 필드 하나 때문에
        영구히 잠정 상태로 남으면 안 된다).
        """

        result = calculate_economics(_item(ad_cost=None))

        self.assertFalse(result.is_provisional)
        self.assertEqual(result.missing_cost_fields, [])
        self.assertEqual(result.excluded_cost_fields, ["ad_cost"])
        # ad_cost=None은 연산에서 0으로 처리된다(정책 제외 = 0으로
        # 취급해 계산에서 뺀다는 뜻 — 값을 아는데 0이라는 것과는
        # 다르지만 연산 결과는 같다). 5000(원가) + 1000(수수료10%)
        # + 200(결제2%) + 1000(배송) + 200(포장) + 0(광고,제외)
        # + 100(반품1%) + 300(세금3%) = 7800.
        self.assertEqual(result.total_cost, Decimal("7800.00"))

    def test_explicit_ad_cost_value_is_neither_missing_nor_excluded(self):
        """광고비를 실제로 입력하면(정책 예외로 사용자가 굳이 반영을
        원하는 경우) excluded_cost_fields에도 나오면 안 된다 — None일
        때만 "정책상 제외 중"이다."""

        result = calculate_economics(_item(ad_cost=Decimal("500")))

        self.assertEqual(result.excluded_cost_fields, [])
        self.assertEqual(result.missing_cost_fields, [])
        self.assertFalse(result.is_provisional)

    def test_explicit_zero_is_not_provisional(self):
        """명시적으로 확인된 0원은 미확인이 아니다 — is_provisional이
        서지 않아야 한다(기존 `test_zero_fees_and_costs_gives_full_
        margin`과 동일한 입력, 새 필드만 추가 확인)."""

        result = calculate_economics(_item(
            cost_of_goods=Decimal("0"), channel_fee_rate=Decimal("0"),
            payment_fee_rate=Decimal("0"), shipping_cost=Decimal("0"),
            packaging_cost=Decimal("0"), ad_cost=Decimal("0"),
            return_reserve_rate=Decimal("0"), tax_basis_rate=Decimal("0"),
        ))

        self.assertFalse(result.is_provisional)
        self.assertEqual(result.missing_cost_fields, [])

    def test_missing_cost_fields_lists_only_the_none_fields(self):
        """일부만 미확인이면 그 필드만 정확히 나열해야 한다."""

        result = calculate_economics(_item(
            packaging_cost=None, return_reserve_rate=None,
        ))

        self.assertTrue(result.is_provisional)
        self.assertEqual(
            set(result.missing_cost_fields),
            {"packaging_cost", "return_reserve_rate"},
        )

    def test_default_fixture_has_no_missing_fields(self):
        """기존 전체 확정 fixture(`_item()`)는 새 필드 추가 후에도
        여전히 provisional이 아니어야 한다(회귀 방지)."""

        result = calculate_economics(_item())

        self.assertFalse(result.is_provisional)
        self.assertEqual(result.missing_cost_fields, [])

    def test_derive_sale_price_for_margin_rate_handles_none_fields(self):
        """역산 함수도 None 필드에서 TypeError 없이 0으로 연산해야
        한다(합계·곱셈 연산자가 NoneType을 만나면 그대로 죽는다)."""

        item = _item(
            shipping_cost=None, packaging_cost=None, ad_cost=None,
            payment_fee_rate=None, return_reserve_rate=None,
            tax_basis_rate=None,
        )
        derived = derive_sale_price_for_margin_rate(item, Decimal("0.1"))
        # fixed_costs = cost_of_goods(5000)만 남음, rate_sum = channel_fee_rate(0.1)만 남음
        # sale_price = 5000 / (1 - 0.1 - 0.1) = 6250.00
        self.assertEqual(derived, Decimal("6250.00"))

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
