"""
=========================================================
Homez OS

File : tests/test_outbound_shipping_days.py

2026-10-15 — 쿠팡 "기준 출고일(outboundShippingTimeDay)" 기본값을 1일에서 2일(+1일)로 바꾼 규칙을 고정한다.
무재고 중개라 공급처가 주문을 받은 다음 날 출고한다. 첫 주문(8103602504198)이 WING에서 "당일출고 필요"로 잡힌 경험에서 나왔다.
 - 값이 없으면 2일, 사용자가 정하면 그 값(1~14일)이 전송 payload까지 그대로 간다.
 - 범위를 벗어나거나 정수가 아니면 만들어 보내지 않고 거절한다.
 - 5단계 화면에 입력칸이 있고, 저장 시 같은 범위를 확인하며, 고정값으로 저장할 수 있다.
=========================================================
"""

import unittest
from pathlib import Path

from pydantic import ValidationError

from app.domains.marketplace_listing.coupang_live_payload import build_coupang_live_payload
from app.domains.marketplace_listing.registration_assistant import parse_outbound_days
from app.domains.marketplace_listing.required_fields_schemas import DEFAULT_OUTBOUND_SHIPPING_TIME_DAY
from app.domains.marketplace_listing.required_fields_schemas import MAX_OUTBOUND_SHIPPING_TIME_DAY
from app.domains.marketplace_listing.required_fields_schemas import CoupangSellerFulfilledFields
from tests.test_coupang_live_submission import _base_live_required_fields
from tests.test_coupang_live_submission import _live_channel_policy_attributes

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
DRAFT = {"product_name": "상품", "brand": "HOMEZ"}


def build(**overrides):
    required = _base_live_required_fields(**overrides)
    payload, blockers = build_coupang_live_payload(
        draft=DRAFT, required_fields=required, channel_policy_attributes=_live_channel_policy_attributes(),
    )
    return payload, blockers, required


class DefaultAndBoundsTests(unittest.TestCase):

    def test_the_default_is_two_days_with_a_fourteen_day_limit(self):
        self.assertEqual((DEFAULT_OUTBOUND_SHIPPING_TIME_DAY, MAX_OUTBOUND_SHIPPING_TIME_DAY), (2, 14))

    def test_a_missing_value_is_sent_as_two_days(self):
        required = _base_live_required_fields()
        required.pop("outboundShippingTimeDay", None)
        payload, blockers = build_coupang_live_payload(
            draft=DRAFT, required_fields=required, channel_policy_attributes=_live_channel_policy_attributes(),
        )
        self.assertEqual(blockers, [])
        self.assertEqual([i["outboundShippingTimeDay"] for i in payload["items"]], [2] * len(payload["items"]))

    def test_a_chosen_value_reaches_every_item_unchanged(self):
        for days in (1, 3, 14):
            payload, blockers, _ = build(outboundShippingTimeDay=days)
            self.assertEqual(blockers, [])
            self.assertTrue(all(i["outboundShippingTimeDay"] == days for i in payload["items"]), days)

    def test_the_schema_defaults_to_two_and_rejects_values_outside_the_range(self):
        required = _base_live_required_fields()
        required.pop("outboundShippingTimeDay", None)
        self.assertEqual(CoupangSellerFulfilledFields(**required).outboundShippingTimeDay, 2)
        for bad in (0, -1, 15, 100):
            with self.subTest(days=bad), self.assertRaises(ValidationError):
                CoupangSellerFulfilledFields(**_base_live_required_fields(outboundShippingTimeDay=bad))
        for good in (1, 2, 14):
            self.assertEqual(
                CoupangSellerFulfilledFields(**_base_live_required_fields(outboundShippingTimeDay=good)).outboundShippingTimeDay, good,
            )

    def test_the_parser_accepts_only_whole_numbers_in_range(self):
        self.assertEqual([parse_outbound_days(v) for v in ("1", " 2 ", 14, "14")], [1, 2, 14, 14])
        for bad in ("0", "15", "-1", "1.5", "abc", "", None, 0, 99, True):
            self.assertIsNone(parse_outbound_days(bad), bad)


class AssistantTests(unittest.TestCase):

    def test_the_proposal_carries_two_days_unless_a_fixed_value_exists(self):
        from app.domains.marketplace_listing.registration_assistant import pick_seller_defaults
        from tests.test_registration_assistant import PRIOR_FIELDS
        from tests.test_registration_assistant import PRIOR_NOTICE
        from tests.test_registration_assistant import proposal

        self.assertEqual(proposal().selection["required_fields"]["outboundShippingTimeDay"], 2)
        fixed = pick_seller_defaults(PRIOR_FIELDS, PRIOR_NOTICE, {"outbound_shipping_time_day": "4"})
        self.assertEqual(proposal(defaults=fixed).selection["required_fields"]["outboundShippingTimeDay"], 4)


class ScreenTests(unittest.TestCase):

    def test_the_fulfillment_step_has_an_input_defaulting_to_two(self):
        self.assertIn('class="lw-outbound-days"', CONSOLE)
        self.assertIn("sel.required_fields?.outboundShippingTimeDay ?? LW_OUTBOUND_DAYS_DEFAULT", CONSOLE)
        self.assertIn("const LW_OUTBOUND_DAYS_DEFAULT = 2;", CONSOLE)
        self.assertIn("const LW_OUTBOUND_DAYS_MAX = 14;", CONSOLE)

    def test_saving_validates_the_range_before_storing(self):
        body = CONSOLE.split("requiredFields.deliveryCompanyCode = ")[1].split("const vendorUserId")[0]
        self.assertIn("Number.isInteger(outboundDays)", body)
        self.assertIn("outboundDays > LW_OUTBOUND_DAYS_MAX", body)
        self.assertLess(body.index("return false;"), body.index("requiredFields.outboundShippingTimeDay = outboundDays;"))

    def test_the_value_can_be_saved_as_a_fixed_default(self):
        self.assertIn('key: "outbound_shipping_time_day"', CONSOLE)
        self.assertIn('selector: ".lw-outbound-days"', CONSOLE)

    def test_texts_exist_in_both_languages(self):
        for key in ("lw.outbound_days_label", "lw.outbound_days_hint", "lw.outbound_days_invalid", "lw.fixed_default_label_outbound"):
            self.assertIn(f'"{key}":', KO, key)
            self.assertIn(f'"{key}":', EN, key)


if __name__ == "__main__":
    unittest.main()
