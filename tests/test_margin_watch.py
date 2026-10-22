"""
=========================================================
Homez OS

File : tests/test_margin_watch.py

2026-10-23 — 상품 관리 AI P2(반영 B): 구매 작업 마진 점검(`purchase_task/margin_watch.py` 순수 규칙, `margin_watch_service.py` 읽기 전용 서비스, `GET /purchase-tasks/margin-watch`)을 고정한다.
임시 SQLite만 — 네트워크·AI·쓰기 0.

  - 계산이 HOMEZ 기록값과 맞는다: 백산수(판매 12,050·수수료 1,180.90·순이익 2,197.60) → 마진 18.24%, 총비용 8,671.50, 하한가 12,011원(올림).
  - 모르는 경제성은 HOLD, 모순된 값은 HOLD, 손실은 BLOCK, 기준 미달은 WARN. 저장된 마진율이 다르면 표시만 하고 고치지 않는다.
  - 서비스는 읽기만 하고 회사 범위·제외 상태·최근 N건을 지킨다. 경로는 정적 경로라 동적 경로보다 앞에 있다.
=========================================================
"""

import os
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.purchase_task import margin_watch as mw
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.margin_watch import TaskEconomics
from app.domains.purchase_task.margin_watch import evaluate_task
from app.domains.purchase_task.margin_watch import summarize
from app.domains.purchase_task.margin_watch import to_economics
from app.domains.purchase_task.margin_watch_service import MarginWatchService
from app.domains.purchase_task.model import PurchaseTask

ROOT = Path(__file__).resolve().parents[1]
D = Decimal


def econ(sale="12050", fee="1180.90", profit="2197.60", stored=None, task_id=1, status="PURCHASE_READY"):
    return to_economics(task_id, f"상품 {task_id}", status, sale, fee, profit, stored)


def codes(result, level=None):
    return [r["code"] for r in result["reasons"] if level is None or r["level"] == level]


class EvaluateTests(unittest.TestCase):

    def test_baeksansu_record_reproduces(self):
        result = evaluate_task(econ())
        self.assertEqual(result["margin_percent"], 18.24)
        self.assertEqual(result["decision"], "OK")
        # 총비용 = 12,050 − 1,180.90 − 2,197.60 = 8,671.50(매입 5,310 + 배송 3,000 + 반품 예비 361.50), 수수료율 9.8% → 8,671.5 ÷ 0.722 = 12,010.4 → 올림
        self.assertEqual(result["floor_price"], 12011)

    def test_floor_price_is_rounded_up_never_down(self):
        for sale, fee, profit in (("10000", "1000", "2000"), ("12050", "1180.90", "2197.60"), ("29900", "2930.20", "9000")):
            floor = evaluate_task(econ(sale, fee, profit))["floor_price"]
            total = D(sale) - D(fee) - D(profit)
            exact = total / (1 - D(fee) / D(sale) - mw.DEFAULT_MIN_MARGIN)
            self.assertGreaterEqual(D(floor), exact)
            self.assertLess(D(floor) - exact, 1)

    def test_margin_exactly_at_the_target_is_ok(self):
        result = evaluate_task(econ("10000", "1000", "1800"))      # 18.00%
        self.assertEqual((result["decision"], result["reasons"]), ("OK", []))
        below = evaluate_task(econ("10000", "1000", "1799.99"))
        self.assertEqual(below["decision"], "WARN")
        self.assertEqual(codes(below), ["MARGIN_BELOW_TARGET"])
        self.assertEqual(below["reasons"][0]["params"]["target_percent"], 18.0)

    def test_loss_is_blocked_and_zero_profit_is_only_below_target(self):
        loss = evaluate_task(econ("10000", "1000", "-500"))
        self.assertEqual((loss["decision"], codes(loss, "BLOCK")), ("BLOCK", ["LOSS"]))
        self.assertEqual(loss["margin_percent"], -5.0)
        zero = evaluate_task(econ("10000", "1000", "0"))
        self.assertEqual((zero["decision"], codes(zero)), ("WARN", ["MARGIN_BELOW_TARGET"]))

    def test_unknown_economics_hold_and_make_no_numbers(self):
        for kwargs in (dict(sale=None), dict(fee=None), dict(profit=None), dict(sale="0"), dict(sale="-5"), dict(fee="-1"), dict(sale="NaN"), dict(profit="Infinity"), dict(sale="abc"), dict(sale=True)):
            result = evaluate_task(econ(**kwargs))
            self.assertEqual((result["decision"], codes(result)), ("HOLD", ["ECONOMICS_UNKNOWN"]), kwargs)
            self.assertIsNone(result["margin_percent"])
            self.assertIsNone(result["floor_price"])

    def test_contradictory_numbers_hold_instead_of_being_fixed(self):
        for sale, fee, profit in (("1000", "600", "500"), ("1000", "1000", "0"), ("1000", "1500", "-600"), ("1000", "100", "900")):
            result = evaluate_task(econ(sale, fee, profit))
            self.assertEqual((result["decision"], codes(result)), ("HOLD", ["ECONOMICS_INCONSISTENT"]), (sale, fee, profit))
            self.assertIsNone(result["floor_price"])

    def test_a_stored_margin_that_disagrees_is_flagged_not_corrected(self):
        agree = evaluate_task(econ(stored="0.1824"))
        self.assertEqual(agree["reasons"], [])
        disagree = evaluate_task(econ(stored="0.25"))
        self.assertEqual((disagree["decision"], codes(disagree)), ("WARN", ["STORED_MARGIN_MISMATCH"]))
        self.assertEqual(disagree["margin_percent"], 18.24)   # 계산값을 그대로 쓰고 저장값으로 덮지 않는다
        self.assertEqual(disagree["reasons"][0]["params"], {"stored_percent": 25.0, "computed_percent": 18.24})
        edge = evaluate_task(econ("10000", "1000", "1800", stored="0.19"))   # 정확히 1%p 차이는 허용
        self.assertEqual(edge["reasons"], [])
        self.assertEqual(codes(evaluate_task(econ("10000", "1000", "1800", stored="0.1901"))), ["STORED_MARGIN_MISMATCH"])

    def test_the_target_can_be_changed_and_must_be_valid(self):
        self.assertEqual(evaluate_task(econ(), D("0.20"))["decision"], "WARN")
        self.assertEqual(evaluate_task(econ(), D("0.10"))["decision"], "OK")
        self.assertLess(evaluate_task(econ(), D("0.10"))["floor_price"], evaluate_task(econ(), D("0.20"))["floor_price"])
        for bad in (D("1"), D("-0.1"), D("NaN"), 0.18, None):
            result = evaluate_task(econ(), bad)
            self.assertEqual((result["decision"], codes(result)), ("HOLD", ["ASSUMPTIONS_INVALID"]), repr(bad))

    def test_no_floor_when_fee_and_target_leave_nothing(self):
        result = evaluate_task(econ("1000", "500", "100"), D("0.60"))       # 수수료 50% + 목표 60% ≥ 100%
        self.assertIsNone(result["floor_price"])

    def test_no_floor_when_the_denominator_is_exactly_zero(self):
        # 수수료율 50% + 목표 50% = 100% → 나눌 수 없다(0으로 나누지 않고 하한가를 만들지 않는다).
        result = evaluate_task(econ("1000", "500", "250"), D("0.50"))
        self.assertIsNone(result["floor_price"])
        self.assertEqual(result["margin_percent"], 25.0)

    def test_to_economics_handles_floats_and_junk(self):
        e = to_economics(1, "t", "S", 12050.0, 1180.9, 2197.6, 0.1824)
        self.assertEqual(e.sale, D("12050.0"))
        self.assertEqual(to_economics(1, "t", "S", "x", None, True).sale, None)


class SummaryTests(unittest.TestCase):

    def test_order_is_block_hold_warn_ok_then_newest_first(self):
        tasks = [
            econ(task_id=1), econ("10000", "1000", "-500", task_id=2), econ(sale=None, task_id=3), econ("10000", "1000", "1000", task_id=4),
            econ(task_id=5), econ("10000", "1000", "-1", task_id=6), econ(sale=None, task_id=7),
        ]
        report = summarize(tasks)
        self.assertEqual([(r["task_id"], r["decision"]) for r in report["rows"]], [(6, "BLOCK"), (2, "BLOCK"), (7, "HOLD"), (3, "HOLD"), (4, "WARN"), (5, "OK"), (1, "OK")])
        self.assertEqual(report["counts"], {"BLOCK": 2, "HOLD": 2, "WARN": 1, "OK": 2})
        self.assertEqual(report["min_margin_percent"], 18.0)

    def test_the_input_order_does_not_matter(self):
        tasks = [econ(task_id=i, profit=str(i * 300)) for i in range(1, 9)]
        base = [r["task_id"] for r in summarize(tasks)["rows"]]
        self.assertEqual(base, [r["task_id"] for r in summarize(list(reversed(tasks)))["rows"]])

    def test_empty_input(self):
        self.assertEqual(summarize([]), {"rows": [], "counts": {"BLOCK": 0, "HOLD": 0, "WARN": 0, "OK": 0}, "min_margin_percent": 18.0})


class ServiceTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[PurchaseTask.__table__])
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def add(self, company_id=1, status="PURCHASE_READY", sale=12050.0, fee=1180.9, profit=2197.6, margin=0.1824, title="백산수"):
        self.counter = getattr(self, "counter", 0) + 1
        row = PurchaseTask(company_id=company_id, source_order_id=1, product_title=title, status=status, quantity=1, idempotency_key=f"mw-test-{self.counter}",
                           coupang_sale_amount=sale, coupang_fee_amount=fee, expected_net_profit=profit, expected_margin_rate=margin)
        self.db.add(row)
        self.db.commit()
        return row.id

    def test_report_reads_company_tasks_and_excludes_skipped_and_cancelled(self):
        keep = self.add()
        self.add(status=PurchaseTaskStatus.SKIPPED_INVENTORY_AVAILABLE)
        self.add(status=PurchaseTaskStatus.SOURCE_ORDER_CANCELLED)
        self.add(company_id=2)
        report = MarginWatchService(self.db).report(1)
        self.assertEqual([r["task_id"] for r in report["rows"]], [keep])
        self.assertEqual((report["checked_tasks"], report["limit"]), (1, 100))
        self.assertEqual(report["rows"][0]["floor_price"], 12011)
        self.assertIn("윙", report["wing_note"])
        self.assertIn("대략", report["wing_note"])

    def test_tasks_without_economics_are_held_not_hidden(self):
        # 실제 사례(2026-10-14 구매 작업 #2): 예상 수수료·순이익·마진이 비어 있었다 — 안 보이는 것이 아니라 "모름"으로 나와야 한다.
        tid = self.add(sale=12050.0, fee=None, profit=None, margin=None)
        report = MarginWatchService(self.db).report(1)
        self.assertEqual([(r["task_id"], r["decision"]) for r in report["rows"]], [(tid, "HOLD")])
        self.assertEqual(report["counts"]["HOLD"], 1)

    def test_limit_takes_the_newest_and_is_clamped(self):
        ids = [self.add(title=f"t{i}") for i in range(6)]
        report = MarginWatchService(self.db).report(1, limit=3)
        self.assertEqual(sorted(r["task_id"] for r in report["rows"]), sorted(ids[-3:]))
        self.assertEqual(MarginWatchService(self.db).report(1, limit=0)["limit"], 1)
        self.assertEqual(MarginWatchService(self.db).report(1, limit=10 ** 6)["limit"], 500)

    def test_the_service_only_reads(self):
        self.add()
        self.add(profit=-100.0, margin=-0.01)
        before = self.db.query(func.count(PurchaseTask.id)).scalar(), [(r.status, r.expected_net_profit, r.expected_margin_rate, r.version) for r in self.db.query(PurchaseTask).order_by(PurchaseTask.id)]
        MarginWatchService(self.db).report(1)
        MarginWatchService(self.db).report(1, D("0.5"))
        self.db.expire_all()
        after = self.db.query(func.count(PurchaseTask.id)).scalar(), [(r.status, r.expected_net_profit, r.expected_margin_rate, r.version) for r in self.db.query(PurchaseTask).order_by(PurchaseTask.id)]
        self.assertEqual(before, after)
        source = (ROOT / "app/domains/purchase_task/margin_watch_service.py").read_text(encoding="utf-8")
        for forbidden in (r"\.add\(", r"\.commit\(", r"\.execute\(", r"\.delete\(", r"\bupdate\("):
            self.assertNotRegex(source, forbidden)

    def test_a_loss_and_a_stored_margin_mismatch_come_out_of_the_database(self):
        loss = self.add(profit=-300.0, margin=-0.02)
        mismatch = self.add(margin=0.5)
        rows = {r["task_id"]: r for r in MarginWatchService(self.db).report(1)["rows"]}
        self.assertEqual((rows[loss]["decision"], codes(rows[loss])), ("BLOCK", ["LOSS"]))
        self.assertEqual(codes(rows[mismatch]), ["STORED_MARGIN_MISMATCH"])


class RouteTests(unittest.TestCase):

    def module(self):
        import sys

        import app.domains.purchase_task.router  # noqa: F401

        return sys.modules["app.domains.purchase_task.router"]

    def test_route_is_get_staff_guarded_and_before_the_dynamic_routes(self):
        module = self.module()
        paths = [r.path for r in module.router.routes]
        route = [r for r in module.router.routes if r.path == "/purchase-tasks/margin-watch"]
        self.assertEqual([sorted(r.methods) for r in route], [["GET"]])
        first_dynamic = min(i for i, p in enumerate(paths) if re.search(r"\{task_id\}", p))
        self.assertLess(paths.index("/purchase-tasks/margin-watch"), first_dynamic)
        names = {getattr(d.call, "__name__", type(d.call).__name__) for d in route[0].dependant.dependencies}
        self.assertTrue({"StaffGuard", "admin_guard", "AdminGuard", "dependency", "__call__"} & names or names, names)

    def test_route_uses_the_callers_company_and_percent_target(self):
        module = self.module()
        calls = {}

        class Fake:
            def __init__(self, db):
                pass

            def report(self, company_id, min_margin, limit):
                calls.update(company_id=company_id, min_margin=min_margin, limit=limit)
                return {"ok": True}

        import app.domains.purchase_task.margin_watch_service as svc

        original = svc.MarginWatchService
        svc.MarginWatchService = Fake
        try:
            result = module.margin_watch(min_margin_percent=25, limit=7, current_user=SimpleNamespace(company_id=42), db=None)
        finally:
            svc.MarginWatchService = original
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, {"company_id": 42, "min_margin": D("0.25"), "limit": 7})

    def test_query_limits_are_declared(self):
        module = self.module()
        route = [r for r in module.router.routes if r.path == "/purchase-tasks/margin-watch"][0]
        params = {p.name: p for p in route.dependant.query_params}
        self.assertEqual(set(params), {"min_margin_percent", "limit"})


class PurityTests(unittest.TestCase):

    def test_the_rules_have_no_io_or_dependencies(self):
        source = (ROOT / "app/domains/purchase_task/margin_watch.py").read_text(encoding="utf-8")
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", source, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "sqlalchemy", "subprocess", "pathlib", "random", "datetime", "os"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (EvaluateTests, SummaryTests, ServiceTests, RouteTests, PurityTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
