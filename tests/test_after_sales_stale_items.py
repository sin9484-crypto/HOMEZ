"""
=========================================================
Homez OS

File : tests/test_after_sales_stale_items.py

2026-11-22 — 사용자 결정 "가 나 다".
  가) 10분 읽기는 오늘부터 7일 전까지만 본다. 그보다 오래 열린 채 남은 반품·문의는 하루 한 번, 그 건이 들어온 주를
      다시 읽어 끝났는지 본다(같은 허용·같은 기록 규칙, 호출 기록 이름만 `*_window`).
  나) 쿠팡 조회로 찾지 못하는 건은 사람이 'WING에서 처리 완료'로 닫는다 — 쿠팡에는 아무것도 보내지 않고 기록만 남는다.
      그 뒤 쿠팡에서 새 상태가 오면 그 상태를 따른다.
  다) 온채널 반품 줄은 '확인됨' 뒤에도 '온채널 상태 확인'을 남겨 환불 완료로 바뀔 수 있다.
가짜 HTTP·임시 DB — 외부 호출 없음.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse

from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.external_call.model import ExternalCallLog
from app.domains.order.after_sales_monitor_service import CLOSE_ACTION
from app.domains.order.after_sales_monitor_service import EVENT_ACTION
from app.domains.order.after_sales_monitor_service import MAX_RECHECK_WINDOWS
from app.domains.order.after_sales_monitor_service import AfterSalesMonitorService
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_after_sales_provider import KST
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from tests.test_after_sales_monitor import Base_
from tests.test_coupang_invoice_upload import response

ROOT = Path(__file__).resolve().parents[1]
EASY = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
EASY_TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
PROVIDER_NOW = datetime(2026, 11, 22, 1, 0, tzinfo=timezone.utc)   # 한국 11-22 10:00


def returns_body(rows):
    return {"code": 200, "message": "OK", "data": [{
        "receiptId": rid, "orderId": 8103602504198, "receiptType": "RETURN", "receiptStatus": status,
        "createdAt": created, "modifiedAt": created, "cancelReasonCategory1": "고객변심", "faultByType": "CUSTOMER",
        "returnItems": [{"cancelCount": 1}], "returnDeliveryType": "수기관리", "returnDeliveryDtos": [],
    } for rid, status, created in rows], "nextToken": ""}


def call_body(rows):
    return {"code": 200, "message": "OK", "data": {"content": [{
        "inquiryId": iid, "inquiryStatus": "complete", "csPartnerCounselingStatus": "requestAnswer" if needs else "answered",
        "orderId": 8103602504198, "inquiryAt": at, "replies": [{"needAnswer": needs}],
    } for iid, needs, at in rows], "pagination": {"currentPage": 1, "totalPages": 1}}}


def online_body(rows=()):
    return {"code": 200, "message": "OK", "data": {"content": list(rows), "pagination": {"currentPage": 1, "totalPages": 1}}}


class WindowTransport:
    """조회 기간 끝(날짜)에 따라 다른 응답을 준다 — 지금 주에는 아무것도 없고, 10-22로 끝나는 주에는 옛 건이 있다."""

    def __init__(self):
        self.old = {"returns": returns_body([(900, "RETURNS_COMPLETED", "2026-10-16T15:10:44")]),
                    "call": call_body([(622, False, "2026-10-16T19:00:14")]), "online": online_body()}
        self.recent = {"returns": returns_body([]), "call": call_body([]), "online": online_body()}
        self.calls = []

    def request(self, method, url, *, headers, json_body, timeout):
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        kind = next(k for k, marker in (("returns", "/returnRequests"), ("online", "/onlineInquiries"), ("call", "/callCenterInquiries"),
                                        ("settlement", "/settlement-histories")) if parsed.path.endswith(marker))
        end = (q.get("createdAtTo") or q.get("inquiryEndAt") or [""])[0][:10]
        self.calls.append((kind, method, end, q))
        if kind == "settlement":
            return response(200, [])
        return response(200, self.old[kind] if end == "2026-10-22" else self.recent[kind])


class StaleBase(Base_):

    def setUp(self):
        super().setUp()
        self.transport = WindowTransport()
        self.clock = [datetime.utcnow()]   # 호출 기록은 실제 시계를 쓰므로 감시 시계도 실제 시각 기준
        self.grant()

    def monitor(self):
        def factory(session):
            return CoupangChannelActions(
                session, credential_store=self.creds, now_factory=lambda: self.clock[0],
                provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport, now_factory=lambda: PROVIDER_NOW),
            )
        return AfterSalesMonitorService(self.db, actions_factory=factory, now_factory=lambda: self.clock[0])

    def seed(self, entity_id, payload):
        self.db.execute(text(
            "INSERT INTO audit_logs (company_id, user_id, action, entity, entity_id, description) VALUES (1, NULL, :a, 'after_sales', :e, :d)"),
            {"a": EVENT_ACTION, "e": entity_id, "d": json.dumps(payload, ensure_ascii=False)})
        self.db.commit()

    def seed_old_items(self):
        self.seed("RETURN:900", {"k": "RETURN", "o": "8103_6025_0419_8", "s": "RETURNS_UNCHECKED", "t": "2026-10-16T15:10:44+09:00", "g": "RETURNS_UNCHECKED||"})
        self.seed("INQ_CALL:622", {"k": "INQ_CALL", "o": "8103_6025_0419_8", "s": "UNANSWERED", "t": "2026-10-16T19:00:14+09:00"})

    def latest(self):
        self.db.expire_all()
        return {e["entity_id"]: e for e in AfterSalesMonitorService.latest_events(self.db)}

    def window_calls(self, end="2026-10-22"):
        return [c for c in self.transport.calls if c[2] == end]


# ---------------- 가) 지난 주 다시 읽기 ----------------

class ProviderWindowTests(StaleBase):

    def provider(self):
        return CoupangChannelControlProvider({"access_key": "AK", "secret_key": "SK", "vendor_id": "A00012345"},
                                             transport=self.transport, now_factory=lambda: PROVIDER_NOW)

    def test_end_at_sets_the_return_window(self):
        result = self.provider().read_return_requests(days=7, end_at=datetime(2026, 10, 22, 23, 59, 30, tzinfo=KST))
        self.assertTrue(result.ok)
        q = self.transport.calls[-1][3]
        self.assertEqual((q["createdAtFrom"], q["createdAtTo"]), (["2026-10-15T23:59"], ["2026-10-22T23:59"]))
        self.assertEqual([r["receipt_id"] for r in result.rows], [900])

    def test_end_at_sets_the_inquiry_window(self):
        result = self.provider().read_call_center_inquiries(days=7, end_at=datetime(2026, 10, 22, 19, 0, tzinfo=KST))
        q = self.transport.calls[-1][3]
        self.assertEqual((q["inquiryStartAt"], q["inquiryEndAt"]), (["2026-10-16"], ["2026-10-22"]))
        self.assertEqual([(r["inquiry_id"], r["answered"]) for r in result.rows], [(622, True)])

    def test_without_end_at_the_window_ends_now(self):
        self.provider().read_return_requests(days=7)
        self.assertEqual(self.transport.calls[-1][3]["createdAtTo"], ["2026-11-22T10:00"])
        self.provider().read_online_inquiries(days=7)
        self.assertEqual(self.transport.calls[-1][3]["inquiryEndAt"], ["2026-11-22"])

    def test_future_or_naive_end_is_refused_without_a_call(self):
        provider = self.provider()
        for end in (datetime(2026, 11, 23, tzinfo=KST), datetime(2026, 10, 22, 12, 0)):
            self.assertFalse(provider.read_return_requests(days=7, end_at=end).ok)
            self.assertFalse(provider.read_call_center_inquiries(days=7, end_at=end).ok)
        self.assertEqual(self.transport.calls, [])


class RecheckTests(StaleBase):

    def test_old_open_items_are_read_again_in_their_week_and_closed(self):
        self.seed_old_items()
        summary = self.monitor().refresh(1, settlement=False)
        self.assertTrue(summary["ok"], summary["failures"])
        self.assertEqual(summary["rechecked_windows"], 2)
        self.assertEqual(sorted(c[0] for c in self.window_calls()), ["call", "returns"])
        latest = self.latest()
        self.assertEqual(latest["RETURN:900"]["s"], "RETURNS_COMPLETED")
        self.assertEqual(latest["INQ_CALL:622"]["s"], "ANSWERED")
        self.assertNotIn("mc", latest["RETURN:900"])                    # 쿠팡이 알려 준 것 — 사람이 닫은 것이 아니다
        self.db.expire_all()
        targets = {l.target for l in self.db.query(ExternalCallLog).all()}
        self.assertTrue({"returns_window", "call_window"} <= targets)
        self.assertEqual({c[1] for c in self.transport.calls}, {"GET"})

    def test_the_week_is_read_at_most_once_a_day(self):
        self.seed("RETURN:901", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": "2026-10-17T09:00:00+09:00", "g": "x"})  # 쿠팡 조회에 안 나오는 건
        self.monitor().refresh(1, settlement=False)
        first = len(self.window_calls("2026-10-23"))                    # 10-17에 들어온 건 → 10-23으로 끝나는 주
        self.assertEqual(first, 1)
        self.monitor().refresh(1, settlement=False)
        self.assertEqual(len(self.window_calls("2026-10-23")), first)    # 24시간 안에는 다시 읽지 않는다
        self.clock[0] = self.clock[0] + timedelta(hours=25)
        self.monitor().refresh(1, settlement=False)
        self.assertEqual(len(self.window_calls("2026-10-23")), first + 1)
        self.assertEqual(self.latest()["RETURN:901"]["s"], "RETURNS_UNCHECKED")   # 찾지 못하면 바꾸지 않는다

    def test_recent_or_closed_items_do_not_trigger_window_reads(self):
        now_kst = self.clock[0].replace(tzinfo=timezone.utc).astimezone(KST)
        self.seed("RETURN:902", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": (now_kst - timedelta(days=2)).isoformat(), "g": "x"})
        self.seed("RETURN:903", {"k": "RETURN", "s": "RETURNS_COMPLETED", "t": "2026-10-10T09:00:00+09:00", "g": "x"})
        self.seed("INQ_ONLINE:904", {"k": "INQ_ONLINE", "s": "ANSWERED", "t": "2026-10-10T09:00:00"})
        self.seed("RETURN:905", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": None, "g": "x"})          # 시각 모름 — 지어내지 않는다
        self.seed("SETTLEMENT:2026-10:WEEKLY:x", {"k": "SETTLEMENT", "s": "SUBJECT:1", "t": "2026-10-01T00:00:00"})
        self.assertEqual(self.monitor()._old_open_windows(), [])              # 7일 안·끝난 것·시각 모름·정산은 다시 읽지 않는다
        summary = self.monitor().refresh(1, settlement=False)
        self.assertEqual(summary["rechecked_windows"], 0)
        self.assertEqual(summary["failures"], [])
        self.assertEqual(self.window_calls(), [])
        self.assertEqual({c[2] for c in self.transport.calls} - {now_kst.date().isoformat(), now_kst.strftime("%Y-%m-%d")}, set())

    def test_windows_are_capped_per_run_oldest_first(self):
        for i in range(MAX_RECHECK_WINDOWS + 2):
            self.seed(f"RETURN:{910 + i}", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": f"2026-10-{10 + i:02d}T09:00:00+09:00", "g": "x"})
        windows = self.monitor()._old_open_windows()
        self.assertEqual(len(windows), MAX_RECHECK_WINDOWS)
        self.assertEqual([end.date().isoformat() for _w, end in windows], ["2026-10-16", "2026-10-17", "2026-10-18"])
        for created, (_what, end) in zip(("2026-10-10", "2026-10-11", "2026-10-12"), windows):
            self.assertEqual((end - datetime.fromisoformat(created + "T09:00:00+09:00")).days, 6)   # 들어온 때가 기간 안

    def test_without_an_allowance_nothing_is_called(self):
        self.db.execute(text("DELETE FROM external_call_allowances"))
        self.db.commit()
        self.seed_old_items()
        summary = self.monitor().refresh(1, settlement=False)
        self.assertTrue(summary["blocked"])
        self.assertEqual(self.transport.calls, [])


# ---------------- 나) 'WING에서 처리 완료' ----------------

class ManualCloseTests(StaleBase):

    def setUp(self):
        super().setUp()
        self.seed_old_items()

    def close(self, entity_id, company_id=1):
        return AfterSalesMonitorService(self.db).close_manually(company_id, entity_id, user_id=1)

    def rows(self, action):
        self.db.expire_all()
        return self.db.execute(text("SELECT entity_id, description FROM audit_logs WHERE action=:a ORDER BY id"), {"a": action}).fetchall()

    def test_closing_hides_the_item_and_keeps_the_coupang_record(self):
        before = self.rows(EVENT_ACTION)
        self.assertEqual(self.close("RETURN:900"), {"entity_id": "RETURN:900", "closed": True})
        self.assertEqual(self.rows(EVENT_ACTION), before)                       # 쿠팡 상태 기록은 그대로
        closes = self.rows(CLOSE_ACTION)
        self.assertEqual(len(closes), 1)
        ref = json.loads(closes[0][1])
        self.assertEqual(ref["u"], 1)
        latest = self.latest()
        self.assertEqual((latest["RETURN:900"]["s"], latest["RETURN:900"]["mc"]), ("RETURNS_COMPLETED", 1))
        self.assertEqual(latest["INQ_CALL:622"]["s"], "UNANSWERED")              # 다른 항목은 그대로
        from app.domains.easy_view.service import EasyViewService
        after_sales = EasyViewService(self.db).build_after_sales()
        self.assertEqual([e["entity_id"] for e in after_sales["returns_open"]], [])
        self.assertEqual(after_sales["returns_done"][0]["mc"], 1)                # 화면에 '직접 닫음' 표시를 넘긴다

    def test_inquiries_close_as_answered(self):
        self.close("INQ_CALL:622")
        self.assertEqual(self.latest()["INQ_CALL:622"]["s"], "ANSWERED")

    def test_a_newer_coupang_state_wins_over_the_close(self):
        self.close("RETURN:900")
        self.seed("RETURN:900", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": "2026-10-16T15:10:44+09:00", "g": "again"})
        latest = self.latest()
        self.assertEqual(latest["RETURN:900"]["s"], "RETURNS_UNCHECKED")
        self.assertNotIn("mc", latest["RETURN:900"])

    def test_only_open_known_items_of_the_right_kind_can_be_closed(self):
        for bad in ("SETTLEMENT:2026-10", "RETURN:abc", "RETURN:", "x", "", "RETURN:900;DROP"):
            with self.assertRaises(BadRequestException, msg=bad):
                self.close(bad)
        with self.assertRaises(NotFoundException):
            self.close("RETURN:12345")                                           # 기록 없음
        with self.assertRaises(NotFoundException):
            self.close("RETURN:900", company_id=2)                               # 다른 회사
        self.close("RETURN:900")
        with self.assertRaises(NotFoundException):
            self.close("RETURN:900")                                             # 이미 닫힘 — 기록이 늘지 않는다
        self.assertEqual(len(self.rows(CLOSE_ACTION)), 1)

    def test_closed_returns_leave_the_supplier_return_list(self):
        from app.domains.order.supplier_return_service import SupplierReturnService
        self.close("RETURN:900")
        self.assertEqual([c["receipt_id"] for c in SupplierReturnService(self.db).candidates(1)], [])


class SupplierRowStaysTests(StaleBase):
    """2026-11-22(사용자 지시 "온채널 줄 남기도록 고쳐라") — 쿠팡 반품이 끝나거나 닫혀도, 온채널에 요청해 두고 환불 완료를 기록하지
    않은 건은 온채널 줄이 남아 '온채널 상태 확인'으로 환불 완료까지 기록할 수 있다."""

    def setUp(self):
        super().setUp()
        from app.database.base import Base
        from app.domains.order.model import Order
        from app.domains.order.supplier_return_service import SupplierReturnService
        from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
        Base.metadata.create_all(self.db.get_bind(), tables=[Order.__table__, PurchaseOrderSubmissionAttempt.__table__])
        self.seed_old_items()
        self.service = SupplierReturnService(self.db)

    def supplier(self, state, text_value=None):
        payload = {"s": state, "t": "2026-10-16T15:05:07", "u": 1}
        if text_value:
            payload["x"] = text_value
        self.service._write(1, "900", payload, 1)
        self.db.commit()

    def rows(self):
        self.db.expire_all()
        return {c["receipt_id"]: c for c in self.service.candidates(1)}

    def test_open_coupang_returns_are_listed_as_before(self):
        row = self.rows()["900"]
        self.assertEqual((row["supplier_state"], row["coupang_open"]), ("NOT_REQUESTED", True))

    def test_requested_rows_stay_after_a_manual_close(self):
        self.supplier("REQUESTED")
        AfterSalesMonitorService(self.db).close_manually(1, "RETURN:900", user_id=1)
        row = self.rows()["900"]
        self.assertEqual((row["supplier_state"], row["coupang_open"], row["coupang_status"]), ("REQUESTED", False, "RETURNS_COMPLETED"))

    def test_confirmed_rows_stay_after_a_manual_close(self):
        self.supplier("CONFIRMED", "반품/교환 처리중(클레임 접수)")
        AfterSalesMonitorService(self.db).close_manually(1, "RETURN:900", user_id=1)
        row = self.rows()["900"]
        self.assertEqual((row["supplier_state"], row["coupang_open"]), ("CONFIRMED", False))

    def test_rows_stay_after_coupang_completes_and_leave_once_refunded(self):
        self.supplier("CONFIRMED", "반품/교환 처리중(클레임 접수)")
        self.seed("RETURN:900", {"k": "RETURN", "o": "8103_6025_0419_8", "s": "RETURNS_COMPLETED", "t": "2026-10-16T15:10:44+09:00", "g": "done"})
        self.assertIn("900", self.rows())
        result = self.service.apply_supplier_status(1, "900", "반품/환불 완료(전체환불)", user_id=1)
        self.assertEqual((result["receipt_id"], result["supplier_state"]), ("900", "REFUNDED"))   # 목록에서 빠져도 결과는 돌려준다(오류 아님)
        self.assertEqual(result["supplier_status_text"], "반품/환불 완료(전체환불)")
        self.assertNotIn("900", self.rows())                                     # 환불까지 끝나면 사라진다
        self.db.expire_all()
        last = json.loads(self.db.execute(text("SELECT description FROM audit_logs WHERE entity_id='SUPRET:900' ORDER BY id DESC LIMIT 1")).first()[0])
        self.assertEqual(last["s"], "REFUNDED")

    def test_a_still_open_return_keeps_returning_its_listed_row(self):
        self.supplier("REQUESTED")
        result = self.service.apply_supplier_status(1, "900", "반품/교환 처리중(클레임 접수)", user_id=1)
        self.assertEqual((result["supplier_state"], result["coupang_open"]), ("CONFIRMED", True))
        self.assertEqual(self.rows()["900"]["supplier_state"], "CONFIRMED")

    def test_never_requested_rows_still_leave_when_coupang_is_done(self):
        AfterSalesMonitorService(self.db).close_manually(1, "RETURN:900", user_id=1)
        self.assertNotIn("900", self.rows())
        with self.assertRaises(NotFoundException):
            self.service.mark_requested(1, "900", 1)                             # 닫힌 뒤에는 새로 요청 기록을 만들지 않는다


class RouteSourceTests(unittest.TestCase):

    def test_close_route_is_admin_only_and_before_dynamic_routes(self):
        source = (ROOT / "app/domains/order/router.py").read_text(encoding="utf-8")
        block = source.split('@router.post("/after-sales/{entity_id}/close")')[1].split("@router")[0]
        self.assertIn("current_user: User = Depends(admin_guard)", block)
        self.assertIn("close_manually(current_user.company_id, entity_id, current_user.id)", block)
        self.assertLess(source.index('"/after-sales/{entity_id}/close"'), source.index('"/{order_id}'))

    def test_window_reads_are_registered_under_their_own_names(self):
        source = (ROOT / "app/domains/purchase_task/channel_action_service.py").read_text(encoding="utf-8")
        for name, method in (("returns_window", "read_return_requests"), ("online_window", "read_online_inquiries"), ("call_window", "read_call_center_inquiries")):
            self.assertIn(f'"{name}": "{method}"', source)

    def test_public_events_carry_the_manual_close_flag(self):
        source = (ROOT / "app/domains/easy_view/service.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'"dt", "mc"\)')


# ---------------- 화면(가·나·다) ----------------

def js_function(name):
    match = re.search(r"(?:async )?function " + re.escape(name) + r"\(", EASY)
    assert match, name
    i = EASY.index("{", EASY.index(")", match.end() - 1))
    depth = 0
    for j in range(i, len(EASY)):
        depth += {"{": 1, "}": -1}.get(EASY[j], 0)
        if depth == 0:
            return EASY[match.start():j + 1]
    raise AssertionError(name)


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class UiBehaviourTests(unittest.TestCase):

    def run_js(self, body, env=None):
        proc = subprocess.run([NODE, "-e", body], capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ, **(env or {})})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_stale_means_seven_days_or_more_and_unknown_is_not_stale(self):
        script = "const AFTER_SALES_STALE_HOURS = 7 * 24;\n" + js_function("inquiryAgeHours") + "\n" + js_function("afterSalesStale") + """
const now = Date.UTC(2026, 10, 22, 1, 0);   // 한국 11-22 10:00
console.log(JSON.stringify([
  afterSalesStale("2026-10-16T15:10:44+09:00", now), afterSalesStale("2026-11-15T10:00:00", now), afterSalesStale("2026-11-15T10:01:00", now),
  afterSalesStale("2026-11-21T10:00:00", now), afterSalesStale(null, now), afterSalesStale("모름", now)]));"""
        self.assertEqual(self.run_js(script), [True, True, False, False, False, False])

    def test_close_asks_first_posts_once_and_reloads(self):
        script = js_function("closeAfterSales") + """
const calls = [], toasts = []; let asks = 0, reloads = 0;
const T = (k) => k;
const api = async (path, o) => { calls.push([path, o && o.method]); if (process.env.FAIL) throw new Error("거절"); return {}; };
const B = { askConfirm: async () => { asks += 1; return Boolean(process.env.OK); }, toast: (m, t) => toasts.push([m, t]) };
const loadToday = () => { reloads += 1; };
closeAfterSales(process.env.ID || "").then(() => console.log(JSON.stringify({ calls, toasts, asks, reloads })));"""
        out = self.run_js(script, {"ID": "RETURN:1919873062", "OK": ""})
        self.assertEqual((out["calls"], out["asks"], out["reloads"]), ([], 1, 0))
        out = self.run_js(script, {"ID": "RETURN:1919873062", "OK": "1"})
        self.assertEqual(out["calls"], [["/orders/after-sales/RETURN%3A1919873062/close", "POST"]])
        self.assertEqual((out["toasts"], out["reloads"]), ([["easy.today.close_done", "success"]], 1))
        out = self.run_js(script, {"ID": "INQ_CALL:622088519", "OK": "1", "FAIL": "1"})
        self.assertEqual((out["toasts"], out["reloads"]), ([["거절", "error"]], 1))
        out = self.run_js(script, {"ID": "", "OK": "1"})
        self.assertEqual((out["calls"], out["asks"]), ([], 0))


class UiSourceTests(unittest.TestCase):

    def test_old_return_and_inquiry_rows_offer_the_close_button(self):
        returns = EASY[EASY.index('key: `return:${r.id}`'):]
        returns = returns[:returns.index("}));")]
        self.assertIn('action: afterSalesStale(r.t)\n        ? { label: T("easy.today.close_action"), run: () => closeAfterSales(r.entity_id) }', returns)
        self.assertIn('B.navigateTo("easy-orders")', returns)                     # 7일 안이면 예전처럼 주문 보기
        inquiries = EASY[EASY.index('key: `inq:${q.entity_id}`'):]
        inquiries = inquiries[:inquiries.index("}));")]
        self.assertIn('action: afterSalesStale(q.t)\n          ? { label: T("easy.today.close_action"), run: () => closeAfterSales(q.entity_id) }', inquiries)

    def test_confirmed_supplier_returns_keep_the_check_button_until_refunded(self):
        block = EASY[EASY.index("// 2-d) 공급처(온채널) 반품 요청"):]
        block = block[:block.index("// 2-d2)")]
        self.assertIn('const refunded = state === "REFUNDED";', block)
        self.assertIn('(refunded ? { label: T("easy.today.return_action"), run: () => B.navigateTo("easy-orders") } : { label: T("easy.sr.action_check"), run: () => supplierReturnCheck(c) })', block)
        self.assertIn('chip("ok", T("easy.sr.chip_refunded"))', block)
        check = js_function("supplierReturnCheck")
        self.assertIn('r.supplier_state === "REFUNDED" ? "easy.sr.check_refunded"', check)

    def test_texts_exist_in_both_languages_with_the_same_placeholders(self):
        keys = ("easy.today.close_action", "easy.today.close_title", "easy.today.close_body", "easy.today.close_done", "easy.today.close_error",
                "easy.sr.chip_refunded", "easy.sr.sub_refunded", "easy.sr.check_refunded")
        found = {}
        for key, value in re.findall(r'"(easy\.(?:today\.close_[a-z]+|sr\.(?:chip|sub|check)_refunded))": ("(?:[^"\\]|\\.)*")', EASY_TEXTS):
            found.setdefault(key, []).append(json.loads(value))
        self.assertEqual(set(found), set(keys))
        for key in keys:
            self.assertEqual(len(found[key]), 2, key)                              # 한국어·영어
            ko, en = found[key]
            self.assertEqual(set(re.findall(r"\{\w+\}", ko)), set(re.findall(r"\{\w+\}", en)), key)
            for name in set(re.findall(r"\{\w+\}", ko)):
                self.assertEqual(ko.count(name), 1, key)                           # i18n은 자리마다 첫 번째만 바꾼다
        self.assertIn("쿠팡에는 아무것도 보내지 않고", found["easy.today.close_body"][0])


if __name__ == "__main__":
    unittest.main()
