"""
=========================================================
Homez OS

File : tests/test_return_economics.py

2026-10-23 — 상품 관리 AI P3(반영 C): 반품 한 건의 손익 계산과 처리 체크포인트(`order/return_economics.py`, `POST /orders/return-economics`)를 고정한다. 순수 계산 — DB·네트워크·AI 0.

  - 2026-10-19 실제 사례가 재현된다: 고객 결제 12,050원·고객 환불 6,050원 → 남은 돈 6,000원 / 매입 8,310원·공급사 환불 2,310원(반품 배송비 6,000원 차감) → 못 돌려받은 돈 6,000원 → 손익 0원(수수료 정산 전).
  - 모르는 금액은 비워 둔 채로 두고 손익을 만들지 않는다. 모순(공급사 환불액과 차감액이 안 맞음·환불이 지급보다 큼)은 어느 쪽도 고치지 않고 막는다.
  - 수수료 정산 반영은 확인하지 못했으므로 항상 "확인 전"을 알린다. 체크포인트는 순서대로 다음 단계를 알리고, 협의가 끝나기 전의 수령 확인은 경고한다.
=========================================================
"""

import json
import re
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

import app.domains.order.router  # noqa: F401
from app.domains.order import return_economics as re_
from app.domains.order.schema import ReturnEconomicsRequest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/order/return_economics.py").read_text(encoding="utf-8")
ROUTER = sys.modules["app.domains.order.router"]


def codes(result, level=None):
    return [r["code"] for r in result["reasons"] if level is None or r["level"] == level]


class CalculationTests(unittest.TestCase):

    def test_the_2026_10_19_case(self):
        result = re_.calculate(12050, 6050, 8310, 2310)
        self.assertEqual((result["decision"], result["customer_kept"], result["supplier_unrecovered"], result["net_before_fee"]), ("OK", 6000, 6000, 0))
        self.assertEqual(codes(result), ["SETTLEMENT_UNCONFIRMED"])
        self.assertIsNone(result["net_with_fee_refund"])

    def test_the_supplier_refund_can_be_derived_from_the_deduction(self):
        result = re_.calculate(12050, 6050, 8310, None, 6000)
        self.assertEqual((result["supplier_refund_used"], result["net_before_fee"]), (2310, 0))
        self.assertEqual(re_.calculate(12050, 6050, 8310, 2310, 6000)["net_before_fee"], 0)     # 둘 다 주고 맞으면 통과

    def test_a_deduction_equal_to_the_payment_means_a_zero_refund_not_an_error(self):
        result = re_.calculate(12050, 6050, 8310, None, 8310)
        self.assertNotIn("REFUND_ABOVE_PAID", codes(result))
        self.assertEqual((result["decision"], result["supplier_refund_used"]), ("OK", 0))

    def test_mismatching_supplier_amounts_are_blocked_not_fixed(self):
        result = re_.calculate(12050, 6050, 8310, 2000, 6000)
        self.assertEqual(result["decision"], "HOLD")
        mismatch = [r for r in result["reasons"] if r["code"] == "SUPPLIER_AMOUNTS_MISMATCH"][0]
        self.assertEqual(mismatch["params"], {"refund": 2000, "derived": 2310})
        self.assertIsNone(result["net_before_fee"])
        self.assertIsNone(result["supplier_refund_used"])

    def test_refunds_above_what_was_paid_are_blocked(self):
        cases = {
            "customer_refund": re_.calculate(12050, 12051, 8310, 2310),
            "supplier_refund": re_.calculate(12050, 6050, 8310, 8311),
            "supplier_deduction": re_.calculate(12050, 6050, 8310, None, 9000),
        }
        for field, result in cases.items():
            self.assertEqual(result["decision"], "HOLD", field)
            self.assertIn({"code": "REFUND_ABOVE_PAID", "level": "HOLD", "params": {"field": field}}, result["reasons"], field)
        equal = re_.calculate(12050, 12050, 8310, 8310)
        self.assertEqual((equal["decision"], equal["net_before_fee"]), ("OK", 0))              # 전액 환불은 허용

    def test_unknown_amounts_hold_and_list_what_is_missing(self):
        for field, expected in (("customer_paid", ["customer_paid"]), ("customer_refund", ["customer_refund"]), ("supplier_paid", ["supplier_paid"]), ("supplier_refund", ["supplier_refund"])):
            args = dict(customer_paid=12050, customer_refund=6050, supplier_paid=8310, supplier_refund=2310)
            args[field] = None
            result = re_.calculate(**args)
            self.assertEqual((result["decision"], result["missing"]), ("HOLD", expected), field)
            self.assertIn("AMOUNTS_UNKNOWN", codes(result))
            for key in ("customer_kept", "supplier_unrecovered", "net_before_fee"):
                self.assertIsNone(result[key], (field, key))
        empty = re_.calculate()
        self.assertEqual(empty["missing"], ["customer_paid", "customer_refund", "supplier_paid", "supplier_refund"])

    def test_invalid_values_count_as_unknown_not_zero(self):
        for bad in (-1, "abc", "NaN", "Infinity", True, [], {}):
            result = re_.calculate(12050, 6050, 8310, bad)
            self.assertEqual(result["decision"], "HOLD", repr(bad))
            self.assertIn("supplier_refund", result["missing"], repr(bad))
        self.assertEqual(re_.calculate(0, 0, 0, 0)["decision"], "OK")        # 0은 모르는 값이 아니라 0원이다

    def test_a_net_loss_is_a_warning_with_the_amount(self):
        result = re_.calculate(12050, 12050, 8310, 2310)      # 고객은 전액 환불, 공급사는 6,000원 차감 → 6,000원 손실
        self.assertEqual((result["decision"], result["net_before_fee"]), ("OK", -6000))
        self.assertEqual([r for r in result["reasons"] if r["code"] == "NET_LOSS"], [{"code": "NET_LOSS", "level": "WARN", "params": {"amount": 6000}}])

    def test_a_profit_has_no_loss_warning(self):
        result = re_.calculate(12050, 3050, 8310, 8000)       # 고객에게서 9,000 남음, 공급사에서 310 못 받음 → +8,690
        self.assertEqual(result["net_before_fee"], 8690)
        self.assertNotIn("NET_LOSS", codes(result))

    def test_fee_refund_is_added_and_replaces_the_unconfirmed_note(self):
        result = re_.calculate(12050, 12050, 8310, 2310, None, 2000)
        self.assertEqual((result["net_before_fee"], result["net_with_fee_refund"]), (-6000, -4000))
        self.assertIn("FEE_REFUND_INCLUDED", codes(result))
        self.assertNotIn("SETTLEMENT_UNCONFIRMED", codes(result))
        self.assertEqual([r["params"] for r in result["reasons"] if r["code"] == "NET_LOSS"], [{"amount": 4000}])
        covered = re_.calculate(12050, 12050, 8310, 2310, None, 6000)
        self.assertEqual(covered["net_with_fee_refund"], 0)
        self.assertNotIn("NET_LOSS", codes(covered))

    def test_a_negative_or_invalid_fee_refund_is_treated_as_not_given(self):
        for bad in (-5, "x", "NaN", True):
            result = re_.calculate(12050, 6050, 8310, 2310, None, bad)
            self.assertIn("SETTLEMENT_UNCONFIRMED", codes(result), repr(bad))
            self.assertIsNone(result["net_with_fee_refund"])

    def test_decimals_and_floats_are_handled_exactly(self):
        result = re_.calculate("12050.50", "6050.25", "8310", "2310.10")
        self.assertEqual(result["customer_kept"], 6000.25)
        self.assertEqual(result["supplier_unrecovered"], 5999.9)
        self.assertEqual(result["net_before_fee"], 0.35)
        self.assertEqual(re_.calculate(Decimal("100"), Decimal("40"), Decimal("70"), Decimal("20"))["net_before_fee"], 10)

    def test_results_are_json_and_do_not_depend_on_argument_style(self):
        a = re_.calculate(12050, 6050, 8310, 2310)
        self.assertEqual(a, re_.calculate(customer_paid=12050, customer_refund=6050, supplier_paid=8310, supplier_refund=2310))
        json.dumps(a, ensure_ascii=False)


class CheckpointTests(unittest.TestCase):

    def test_default_is_all_unknown_and_the_first_step_is_next(self):
        result = re_.checkpoints(None)
        self.assertEqual([s["code"] for s in result["steps"]], list(re_.STEP_KEYS))
        self.assertEqual([s["done"] for s in result["steps"]], [None] * 5)
        self.assertEqual((result["next_step"], result["all_done"], result["warnings"]), ("SUPPLIER_REPLY_CHECKED", False, []))

    def test_order_and_labels_are_pinned(self):
        self.assertEqual(re_.STEP_KEYS, ("SUPPLIER_REPLY_CHECKED", "EXTRA_REQUESTS_HANDLED", "SHIPPING_DEDUCTION_DECIDED", "WING_RECEIPT_CONFIRMED", "SETTLEMENT_CHECKED"))
        self.assertEqual([label for _, label in re_.STEPS], ["공급사 답변 확인", "추가 요청 처리(예: 새 안심번호)", "반품 배송비 차감 수용 판단", "윙 수령 확인", "정산 확인"])

    def test_the_next_step_is_the_first_not_done_in_order(self):
        result = re_.checkpoints({"SUPPLIER_REPLY_CHECKED": True, "EXTRA_REQUESTS_HANDLED": False, "SHIPPING_DEDUCTION_DECIDED": True})
        self.assertEqual(result["next_step"], "EXTRA_REQUESTS_HANDLED")
        self.assertEqual([s["done"] for s in result["steps"]], [True, False, True, None, None])

    def test_all_done(self):
        result = re_.checkpoints({key: True for key in re_.STEP_KEYS})
        self.assertEqual((result["next_step"], result["all_done"], result["warnings"]), (None, True, []))

    def test_only_real_booleans_count(self):
        result = re_.checkpoints({"SUPPLIER_REPLY_CHECKED": 1, "EXTRA_REQUESTS_HANDLED": "true", "SHIPPING_DEDUCTION_DECIDED": "yes", "UNKNOWN_STEP": True})
        self.assertEqual([s["done"] for s in result["steps"]], [None] * 5)
        for junk in ("x", 5, [], None):
            self.assertEqual(re_.checkpoints(junk)["next_step"], "SUPPLIER_REPLY_CHECKED")

    def test_receipt_before_negotiation_is_warned_with_the_open_steps(self):
        result = re_.checkpoints({"SUPPLIER_REPLY_CHECKED": True, "WING_RECEIPT_CONFIRMED": True})
        self.assertEqual(result["warnings"], [{"code": "RECEIPT_BEFORE_NEGOTIATION", "steps": ["EXTRA_REQUESTS_HANDLED", "SHIPPING_DEDUCTION_DECIDED"]}])
        ok = re_.checkpoints({"SUPPLIER_REPLY_CHECKED": True, "EXTRA_REQUESTS_HANDLED": True, "SHIPPING_DEDUCTION_DECIDED": True, "WING_RECEIPT_CONFIRMED": True})
        self.assertEqual(ok["warnings"], [])
        self.assertEqual(ok["next_step"], "SETTLEMENT_CHECKED")
        self.assertEqual(re_.checkpoints({"WING_RECEIPT_CONFIRMED": False})["warnings"], [])


class RouteTests(unittest.TestCase):

    def test_route_is_post_admin_guarded(self):
        route = [r for r in ROUTER.router.routes if r.path == "/orders/return-economics"]
        self.assertEqual([sorted(r.methods) for r in route], [["POST"]])
        names = {getattr(d.call, "__name__", type(d.call).__name__) for d in route[0].dependant.dependencies}
        self.assertTrue({"admin_guard", "AdminGuard"} & names, names)

    def test_route_returns_economics_and_checkpoints_together(self):
        request = ReturnEconomicsRequest(customer_paid="12050", customer_refund="6050", supplier_paid="8310", supplier_deduction="6000",
                                         done={"SUPPLIER_REPLY_CHECKED": True})
        result = ROUTER.calculate_return_economics(data=request, current_user=SimpleNamespace(id=1, company_id=1))
        self.assertEqual(result["economics"]["net_before_fee"], 0)
        self.assertEqual(result["economics"]["supplier_refund_used"], 2310)
        self.assertEqual(result["checkpoints"]["next_step"], "EXTRA_REQUESTS_HANDLED")

    def test_an_empty_request_is_all_unknown(self):
        result = ROUTER.calculate_return_economics(data=ReturnEconomicsRequest(), current_user=SimpleNamespace(id=1, company_id=1))
        self.assertEqual(result["economics"]["decision"], "HOLD")
        self.assertEqual(result["checkpoints"]["next_step"], "SUPPLIER_REPLY_CHECKED")

    def test_schema_limits(self):
        for bad in (dict(customer_paid="-1"), dict(supplier_paid="NaN"), dict(fee_refund="abc"), dict(unknown_field=1), dict(done={"a": 1}), dict(done={str(i): True for i in range(6)}),
                    dict(done={"SUPPLIER_REPLY_CHECKED": "maybe"})):
            with self.assertRaises(ValidationError, msg=str(bad)):
                ReturnEconomicsRequest(**bad)
        self.assertEqual(ReturnEconomicsRequest(done={"SUPPLIER_REPLY_CHECKED": True}).done, {"SUPPLIER_REPLY_CHECKED": True})


class PurityTests(unittest.TestCase):

    def test_no_io_no_ai_no_storage(self):
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", SOURCE, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "sqlalchemy", "subprocess", "pathlib", "random", "datetime", "os", "ai_draft"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)
        for word in (".add(", ".commit(", "write_audit_log", "open("):
            self.assertNotIn(word, SOURCE)


if __name__ == "__main__":
    unittest.main()
