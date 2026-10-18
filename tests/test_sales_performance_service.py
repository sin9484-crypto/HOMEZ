"""
=========================================================
Homez OS

File : tests/test_sales_performance_service.py

2026-10-18 — 마케팅 AI M4(lite): 판매 성과 읽기(`SalesPerformanceService`)와 라우트를 고정한다. 가짜 HTTP, 쿠팡 호출 0(실제 읽기는 사용자가 앱에서 눌렀을 때만).

  - 허용(`AFTER_SALES_CHECK`)이 없으면 호출 0, 허용 요청만 남는다. 다른 허용으로는 열리지 않는다. 연결이 없으면 읽을 것이 없다.
  - GET만 나가고 경로·쿼리·기간(어제까지, 최대 31일)이 문서와 같다. 쪽마다 호출하되 허용은 읽기 한 번에 한 번만 센다.
  - 읽기 실패는 일부로 집계하지 않는다. 잘림(`truncated`)은 알린다. 고객 이름·연락처·주소는 결과 어디에도 없다.
  - 아무것도 저장하지 않는다(감사 기록·후보·위저드 변화 없음).
=========================================================
"""

import json
import re
import unittest
from datetime import datetime
from datetime import timezone
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse

from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.order.sales_performance_service import SalesPerformanceService
from app.domains.purchase_task import coupang_after_sales_provider as provider_module
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from tests.test_after_sales_monitor import AfterSalesTransport
from tests.test_after_sales_monitor import Base_
from tests.test_coupang_invoice_upload import response

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "app/domains/order/sales_performance_service.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "app/domains/order/router.py").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 18, 6, 30, 0)   # UTC → 한국 15:30, 어제 = 2026-10-17
PII = ("홍길동", "010-1234-5678", "buyer@example.com", "서울시 반품 주소 1-2", "문 앞에 놔 주세요")


def revenue_page(orders, has_next=False, next_token=""):
    return {"code": 200, "message": "OK", "data": orders, "hasNext": has_next, "nextToken": next_token}


def revenue_order(order_id="8100001", sale_type="SALE", item_id="9001", qty=2, amount=25800):
    return {
        "orderId": order_id, "saleType": sale_type, "saleDate": "2026-10-08", "recognitionDate": "2026-10-16",
        "buyerName": PII[0], "buyerPhone": PII[1], "buyerEmail": PII[2], "receiverAddress": PII[3], "deliveryMemo": PII[4],
        "items": [{"vendorItemId": item_id, "productName": "바디워시 상품", "vendorItemName": "바디워시 3개입", "quantity": qty, "salePrice": amount,
                   "saleAmount": amount, "serviceFee": round(amount * 0.108, 2), "serviceFeeVat": round(amount * 0.0108, 2), "settlementAmount": round(amount * 0.892, 2)}],
    }


class RevenueTransport(AfterSalesTransport):
    def __init__(self, pages=None):
        super().__init__()
        self.queues["revenue"] = pages or [response(200, revenue_page([revenue_order()]))]

    @staticmethod
    def kind(url):
        return "revenue" if urlparse(url).path.endswith("/revenue-history") else AfterSalesTransport.kind(url)


class RevenueBase(Base_):

    def setUp(self):
        super().setUp()
        self.transport = RevenueTransport()
        self.sleeps = []
        original = provider_module._sleep
        provider_module._sleep = self.sleeps.append
        self.addCleanup(lambda: setattr(provider_module, "_sleep", original))

    def use(self, *pages):
        self.transport = RevenueTransport(list(pages))

    def service(self):
        def factory(session):
            return CoupangChannelActions(
                session, credential_store=self.creds, now_factory=lambda: NOW,
                provider_factory=lambda cred: CoupangChannelControlProvider(
                    cred, transport=self.transport, now_factory=lambda: datetime(2026, 10, 18, 6, 30, tzinfo=timezone.utc)),
            )
        return SalesPerformanceService(self.db, actions_factory=factory, now_factory=lambda: NOW)

    def read(self, days=30, company_id=1):
        return self.service().read(company_id, days)

    def revenue_calls(self):
        return [c for c in self.transport.calls if c[0] == "revenue"]


class GateTests(RevenueBase):

    def test_without_an_allowance_nothing_is_called_and_a_request_is_left(self):
        result = self.read()
        self.assertEqual((result["ok"], result["blocked"], result["summary"]), (False, True, None))
        self.assertEqual(self.transport.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).all()
        self.assertEqual([(a.kind, a.purpose) for a in pending], [("READ", "AFTER_SALES_CHECK")])

    def test_other_allowances_do_not_open_it(self):
        ExternalCallPolicyService(self.db).grant(
            1, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.ORDER_TRACKING,
            mode=AllowanceMode.STANDING, per_hour_limit=30, hours=24, user_id=1)
        self.assertTrue(self.read()["blocked"])
        self.assertEqual(self.transport.calls, [])

    def test_no_connection_means_nothing_to_read(self):
        self.store.connection_status = "DISCONNECTED"
        self.db.commit()
        self.grant()
        result = self.read()
        self.assertEqual((result["no_connection"], result["ok"]), (True, False))
        self.assertEqual(self.transport.calls, [])
        self.assertTrue(self.service().read(2)["no_connection"])   # 다른 회사는 연결이 없다

    def test_bad_ranges_are_refused_before_any_call(self):
        self.grant()
        for days in (0, 32, -3, True, "7", None):
            with self.assertRaises(BadRequestException, msg=str(days)):
                self.read(days)
        self.assertEqual(self.transport.calls, [])


class ReadTests(RevenueBase):

    def setUp(self):
        super().setUp()
        self.grant()

    def test_only_get_goes_out_with_the_documented_path_and_range(self):
        result = self.read(30)
        self.assertTrue(result["ok"])
        method, url = self.revenue_calls()[0][1], self.revenue_calls()[0][2]
        self.assertEqual(method, "GET")
        self.assertEqual({m for _k, m, _u, _b in self.transport.calls}, {"GET"})
        self.assertTrue(all(b is None for *_x, b in self.transport.calls))
        self.assertEqual(urlparse(url).path, "/v2/providers/openapi/apis/api/v1/revenue-history")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query, keep_blank_values=True).items()}
        self.assertEqual((q["vendorId"], q["recognitionDateFrom"], q["recognitionDateTo"], q["token"], q["maxPerPage"]), ("A00012345", "2026-09-18", "2026-10-17", "", "50"))

    def test_the_summary_comes_back_with_the_hand_computed_numbers(self):
        self.use(response(200, revenue_page([revenue_order("1", qty=2, amount=25800), revenue_order("2", qty=1, amount=12900), revenue_order("2", "REFUND", qty=1, amount=12900)])))
        result = self.read()
        item = result["summary"]["items"][0]
        self.assertEqual((item["vendor_item_id"], item["sale_units"], item["sale_amount"], item["refund_units"], item["return_rate"]), ("9001", 3, "38700", 1, "0.3333"))
        self.assertEqual((result["summary"]["totals"]["orders"], result["summary"]["totals"]["sample"]), (2, "INSUFFICIENT"))
        self.assertEqual((result["days"], result["calls"], result["truncated"]), (30, 1, False))

    def test_pages_are_followed_but_the_allowance_counts_one_read(self):
        self.use(response(200, revenue_page([revenue_order("1")], True, "T1")), response(200, revenue_page([revenue_order("2")], True, "T2")), response(200, revenue_page([revenue_order("3")])))
        result = self.read()
        self.assertEqual((result["calls"], len(self.revenue_calls())), (3, 3))
        self.assertEqual(result["summary"]["totals"]["orders"], 3)
        self.assertEqual(len(self.sleeps), 2)
        logs = self.db.query(ExternalCallLog).filter(ExternalCallLog.purpose == "AFTER_SALES_CHECK", ExternalCallLog.target == "revenue").all()
        self.assertEqual(len(logs), 1)   # 쪽이 셋이어도 허용을 쓴 읽기는 한 번

    def test_a_cut_read_says_so(self):
        pages = [response(200, revenue_page([revenue_order(str(n))], True, f"T{n}")) for n in range(provider_module.MAX_REVENUE_PAGES + 2)]
        self.use(*pages)
        result = self.read()
        self.assertEqual((result["ok"], result["truncated"], result["calls"]), (True, True, provider_module.MAX_REVENUE_PAGES))

    def test_a_failed_read_returns_no_partial_numbers(self):
        self.use(response(200, revenue_page([revenue_order("1")], True, "T1")), response(500, {"code": "E", "message": "서버 오류"}))
        result = self.read()
        self.assertEqual((result["ok"], result["summary"]), (False, None))
        self.assertIn("code=E", result["detail"])

    def test_an_unreadable_shape_is_a_failure(self):
        self.use(response(200, {"code": 200, "data": {"unexpected": True}}))
        result = self.read()
        self.assertEqual((result["ok"], result["summary"]), (False, None))
        self.assertIn("해석 불가", result["detail"])

    def test_nothing_personal_reaches_the_result(self):
        text_out = json.dumps(self.read(), ensure_ascii=False)
        for secret in PII:
            self.assertNotIn(secret, text_out)

    def test_nothing_is_stored(self):
        tables = ("audit_logs", "purchase_tasks", "store_connections")
        before = [self.db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables]
        self.read()
        self.db.expire_all()
        self.assertEqual(before, [self.db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables])


class WiringTests(unittest.TestCase):

    def test_the_route_is_post_admin_only_and_only_takes_the_period(self):
        import sys

        import app.domains.order.router  # noqa: F401

        module = sys.modules["app.domains.order.router"]
        route = next(r for r in module.router.routes if r.path == "/orders/sales-performance")
        self.assertEqual(route.methods, {"POST"})
        self.assertEqual([p.name for p in route.dependant.query_params], ["days"])
        self.assertEqual(route.dependant.body_params, [])
        block = ROUTER[ROUTER.index('@router.post("/sales-performance")'):ROUTER.index('"/collection-ops/test-budget-plan"')]
        self.assertIn("Depends(admin_guard)", block)

    def test_the_service_has_no_write_ai_or_direct_http(self):
        for forbidden in (r"self\.db\.(add|commit|delete|flush)", r"write_audit_log", r"call_llm", r"\brequests\b", r"urllib", r"\bprint\(", r"\.post\(", r"\.put\("):
            self.assertIsNone(re.search(forbidden, SERVICE), forbidden)
        self.assertIn('what="revenue"', SERVICE)


# 기반 시험 클래스에서 물려받은 시험(송장 서비스 전체)은 이 파일에서 돌리지 않는다.
for _cls in (GateTests, ReadTests):
    for _name in dir(_cls):
        if _name.startswith("test_") and _name not in _cls.__dict__:
            setattr(_cls, _name, None)


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for cls in (GateTests, ReadTests, WiringTests):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
