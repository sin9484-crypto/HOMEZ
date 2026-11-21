"""
=========================================================
Homez OS

File : tests/test_order_collection_gap.py

2026-11-21 — 연결을 바꿔 주문 감지가 읽지 않은 구간 알리기(사용자 결정 "2-B").
실제 사례(11-03 11:43 KST에 멈춘 옛 연결 #1, 11-08 14:05 KST에 만든 새 연결 #2)를 그대로 본떠
① 구간을 정확히 찾는지 ② 알리면 안 되는 경우를 알리지 않는지 ③ '확인함'이 감사 기록 한 건만 남기는지
④ 화면이 안내·버튼을 그리고 이름을 이스케이프하는지 고정한다. 임시 SQLite — 외부 호출 0.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import NotFoundException
from app.database.base import Base
import importlib

order_router = importlib.import_module("app.domains.order.router")
from app.domains.order.collection_gap import GAP_ACK_ACTION
from app.domains.order.collection_gap import GAP_ACK_ENTITY
from app.domains.order.collection_gap import find_collection_gaps
from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.order.auto_collection_scheduler import NEW_ORDER_DETECTION_STATUSES
from app.domains.order.schema import OrderCollectionOpsStatusResponse
from app.domains.store_connection.model import StoreConnection
from tests.test_reference_generation_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
CSS = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
EASY = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
EASY_TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
I18N_JS = (ROOT / "app/web/i18n/i18n.js").read_text(encoding="utf-8")

OLD_LAST = datetime(2026, 11, 3, 2, 43, 36)      # 옛 연결이 마지막으로 읽은 끝(UTC)
NEW_CREATED = datetime(2026, 11, 8, 5, 5, 4)     # 새 연결을 만든 때(UTC)
STATUSES = NEW_ORDER_DETECTION_STATUSES


class Base_(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(self.engine, tables=[StoreConnection.__table__, OrderCollectionCursor.__table__])
        self.db = sessionmaker(bind=self.engine)()
        self.db.execute(text(
            "CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
            "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50), created_at VARCHAR(40))"))
        self.db.commit()
        self.old = self.connection(1, "홈즈쿠팡-이전-1", "DISABLED", datetime(2026, 9, 14, 10, 53))
        self.new = self.connection(1, "홈즈쿠팡", "CONNECTED", NEW_CREATED)
        self.old_accept = self.cursor(1, self.old.id, "ACCEPT", OLD_LAST)
        self.old_delivering = self.cursor(1, self.old.id, "DELIVERING", datetime(2026, 9, 17, 10, 9))
        self.new_accept = self.cursor(1, self.new.id, "ACCEPT", datetime(2026, 11, 21, 6, 4))

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    _n = 0

    def connection(self, company_id, name, status, created_at, marketplace="COUPANG"):
        Base_._n += 1
        row = StoreConnection(
            company_id=company_id, marketplace_code=marketplace, display_name=name, seller_identifier=f"s{Base_._n}",
            connection_status=status, created_by=1, creation_idempotency_key=f"k{Base_._n}",
            creation_request_fingerprint="f" * 64, created_at=created_at, updated_at=created_at,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def cursor(self, company_id, connection_id, status, last):
        row = OrderCollectionCursor(company_id=company_id, store_connection_id=connection_id, channel_status=status,
                                    run_status="FAILED" if last == OLD_LAST else "IDLE", last_successful_to=last,
                                    last_error_code="FORBIDDEN" if last == OLD_LAST else None)
        self.db.add(row)
        self.db.commit()
        return row

    def audit_rows(self):
        return self.db.execute(text("SELECT company_id, user_id, action, entity, entity_id, description FROM audit_logs ORDER BY id")).fetchall()

    def snapshot(self):
        return (
            [tuple(r) for r in self.db.execute(text("SELECT * FROM store_connections ORDER BY id")).fetchall()],
            [tuple(r) for r in self.db.execute(text("SELECT * FROM order_collection_cursors ORDER BY id")).fetchall()],
        )


class FindGapTests(Base_):

    def test_the_real_case_is_found_exactly(self):
        gaps = find_collection_gaps(self.db, 1, STATUSES)
        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertEqual(gap.cursor_id, self.old_accept.id)
        self.assertEqual(gap.channel_status, "ACCEPT")
        self.assertEqual((gap.old_store_connection_id, gap.old_display_name), (self.old.id, "홈즈쿠팡-이전-1"))
        self.assertEqual((gap.new_store_connection_id, gap.new_display_name), (self.new.id, "홈즈쿠팡"))
        self.assertEqual(gap.gap_from, OLD_LAST)
        self.assertEqual(gap.new_connection_created_at, NEW_CREATED)

    def test_statuses_the_scheduler_does_not_read_are_not_reported(self):
        self.assertEqual(set(STATUSES), {"ACCEPT"})
        self.assertEqual([g.channel_status for g in find_collection_gaps(self.db, 1, STATUSES)], ["ACCEPT"])
        self.assertEqual(find_collection_gaps(self.db, 1, []), [])

    def test_no_gap_while_the_old_connection_is_still_connected(self):
        self.old.connection_status = "CONNECTED"
        self.db.commit()
        self.assertEqual(find_collection_gaps(self.db, 1, STATUSES), [])

    def test_an_error_connection_replaced_by_a_new_one_is_reported(self):
        self.old.connection_status = "ERROR"
        self.db.commit()
        self.assertEqual(len(find_collection_gaps(self.db, 1, STATUSES)), 1)

    def test_no_gap_without_an_active_replacement(self):
        self.new.connection_status = "DISABLED"
        self.db.commit()
        self.assertEqual(find_collection_gaps(self.db, 1, STATUSES), [])

    def test_a_replacement_made_before_the_old_cursor_stopped_is_not_a_gap(self):
        self.new.created_at = datetime(2026, 11, 3, 2, 0)
        self.db.commit()
        self.assertEqual(find_collection_gaps(self.db, 1, STATUSES), [])

    def test_boundary_same_instant_counts_as_a_gap(self):
        self.new.created_at = OLD_LAST
        self.db.commit()
        self.assertEqual(len(find_collection_gaps(self.db, 1, STATUSES)), 1)

    def test_other_marketplace_or_company_is_not_a_replacement(self):
        self.new.marketplace_code = "NAVER"
        self.db.commit()
        self.assertEqual(find_collection_gaps(self.db, 1, STATUSES), [])
        self.new.marketplace_code = "COUPANG"
        self.new.company_id = 2
        self.db.commit()
        self.assertEqual(find_collection_gaps(self.db, 1, STATUSES), [])
        self.assertEqual(find_collection_gaps(self.db, 2, STATUSES), [])

    def test_a_cursor_that_never_read_is_not_a_gap(self):
        self.old_accept.last_successful_to = None
        self.db.commit()
        self.assertEqual(find_collection_gaps(self.db, 1, STATUSES), [])

    def test_the_earliest_replacement_is_named(self):
        later = self.connection(1, "나중 연결", "CONNECTED", datetime(2026, 11, 10))
        gap = find_collection_gaps(self.db, 1, STATUSES)[0]
        self.assertEqual(gap.new_store_connection_id, self.new.id)
        self.assertNotEqual(gap.new_store_connection_id, later.id)

    def test_acknowledged_only_for_the_same_company_and_cursor(self):
        rows = [(2, GAP_ACK_ACTION, GAP_ACK_ENTITY, str(self.old_accept.id)),      # 다른 회사
                (1, "OTHER_ACTION", GAP_ACK_ENTITY, str(self.old_accept.id)),       # 다른 동작
                (1, GAP_ACK_ACTION, "company", str(self.old_accept.id)),           # 다른 대상
                (1, GAP_ACK_ACTION, GAP_ACK_ENTITY, "not-a-number")]
        for company_id, action, entity, entity_id in rows:
            self.db.execute(text("INSERT INTO audit_logs (company_id, action, entity, entity_id) VALUES (:c, :a, :e, :i)"),
                            {"c": company_id, "a": action, "e": entity, "i": entity_id})
        self.db.commit()
        self.assertEqual(len(find_collection_gaps(self.db, 1, STATUSES)), 1)
        self.db.execute(text("INSERT INTO audit_logs (company_id, action, entity, entity_id) VALUES (1, :a, :e, :i)"),
                        {"a": GAP_ACK_ACTION, "e": GAP_ACK_ENTITY, "i": str(self.old_accept.id)})
        self.db.commit()
        self.assertEqual(find_collection_gaps(self.db, 1, STATUSES), [])

    def test_finding_gaps_writes_nothing(self):
        before = self.snapshot()
        find_collection_gaps(self.db, 1, STATUSES)
        self.db.expire_all()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.audit_rows(), [])


class RouteTests(Base_):

    def user(self, company_id=1):
        return SimpleNamespace(id=7, company_id=company_id)

    def test_status_payload_lists_the_gap(self):
        gaps = order_router._collection_gap_responses(self.db, 1)
        self.assertEqual([g.cursor_id for g in gaps], [self.old_accept.id])
        self.assertEqual(gaps[0].gap_from, OLD_LAST)
        self.assertEqual(OrderCollectionOpsStatusResponse.model_fields["collection_gaps"].default, [])
        source = (ROOT / "app/domains/order/router.py").read_text(encoding="utf-8")
        block = source[source.index("def get_collection_ops_status("):source.index("def _collection_gap_responses(")]
        self.assertIn("collection_gaps=_collection_gap_responses(db, company_id)", block)

    def test_acknowledge_writes_one_audit_row_and_changes_nothing_else(self):
        before = self.snapshot()
        result = order_router.acknowledge_collection_gap(self.old_accept.id, current_user=self.user(), db=self.db)
        self.assertEqual((result.cursor_id, result.acknowledged), (self.old_accept.id, True))
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1)
        company_id, user_id, action, entity, entity_id, description = rows[0]
        self.assertEqual((company_id, user_id, action, entity, entity_id), (1, 7, GAP_ACK_ACTION, GAP_ACK_ENTITY, str(self.old_accept.id)))
        self.assertIn("2026-11-03T02:43:36", description)
        self.db.expire_all()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(find_collection_gaps(self.db, 1, STATUSES), [])

    def test_acknowledge_refuses_anything_not_currently_reported(self):
        for cursor_id, company_id in ((self.new_accept.id, 1), (self.old_delivering.id, 1), (9999, 1), (self.old_accept.id, 2)):
            with self.assertRaises(NotFoundException, msg=(cursor_id, company_id)):
                order_router.acknowledge_collection_gap(cursor_id, current_user=self.user(company_id), db=self.db)
        self.assertEqual(self.audit_rows(), [])
        order_router.acknowledge_collection_gap(self.old_accept.id, current_user=self.user(), db=self.db)
        with self.assertRaises(NotFoundException):  # 두 번째는 거절 — 기록이 늘지 않는다
            order_router.acknowledge_collection_gap(self.old_accept.id, current_user=self.user(), db=self.db)
        self.assertEqual(len(self.audit_rows()), 1)

    def test_route_is_admin_only_post(self):
        routes = [r for r in order_router.router.routes if getattr(r, "path", "").endswith("/collection-ops/gaps/{cursor_id}/acknowledge")]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].methods, {"POST"})
        source = (ROOT / "app/domains/order/router.py").read_text(encoding="utf-8")
        block = source[source.index("def acknowledge_collection_gap("):]
        block = block[:block.index("\n@router.")]
        self.assertIn("current_user: User = Depends(admin_guard)", block)


# ---------------- 화면 ----------------

def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(odo\.gap_[a-z_]+)": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; for (const [k, v] of Object.entries(params || {})) s = s.replace("{" + k + "}", String(v)); return s; } };  // i18n.js t()와 같이 첫 번째만 바꾼다
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fmtDate = (v) => "D[" + v + "]";
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""

GAP_JS = ("{cursor_id: 1, old_store_connection_id: 1, old_display_name: '옛<b>', gap_from: '2026-11-03T02:43:36',"
          " new_store_connection_id: 2, new_display_name: '새', new_connection_created_at: '2026-11-08T05:05:04'}")


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class UiBehaviourTests(unittest.TestCase):

    def run_js(self, expression):
        script = HARNESS.replace("__SOURCES__", function_source("odoGapNoticeHtml")).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))},
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_notice_names_the_period_and_offers_the_button(self):
        html = self.run_js(f"odoGapNoticeHtml([{GAP_JS}])")
        self.assertIn('class="banner banner-warn odo-gap"', html)
        self.assertIn("주문을 자동으로 읽지 않은 기간이 있어요", html)
        self.assertIn("D[2026-11-03T02:43:36]까지만", html)
        self.assertIn("D[2026-11-08T05:05:04]에 만듦", html)
        self.assertIn("옛&lt;b&gt;", html)
        self.assertNotIn("옛<b>", html)
        self.assertIn('data-gap-ack="1"', html)
        self.assertIn("WING에서 확인함", html)
        self.assertNotRegex(html, r"\{(old|from|new|created)\}")

    def test_nothing_without_gaps_and_bad_rows_are_skipped(self):
        for value in ("[]", "null", "undefined", "'x'", "[null, {cursor_id: '1'}, {cursor_id: 1.5}]"):
            self.assertEqual(self.run_js(f"odoGapNoticeHtml({value})"), "", value)
        self.assertEqual(self.run_js(f"odoGapNoticeHtml([{GAP_JS}, {{cursor_id: 2}}])").count("data-gap-ack="), 2)


class UiSourceTests(unittest.TestCase):

    def test_notice_sits_under_the_health_line_and_the_button_posts_after_confirm(self):
        load = function_source("loadOrderCollectionOps")
        self.assertLess(load.index("data.health_message"), load.index("${odoGapNoticeHtml(data.collection_gaps)}"))
        self.assertLess(load.index("${odoGapNoticeHtml(data.collection_gaps)}"), load.index('odo.status_heading'))
        handler = load[load.index('querySelectorAll("[data-gap-ack]")'):]
        handler = handler[:handler.index('el("odo-trigger-btn")')]
        self.assertLess(handler.index("confirmDialog"), handler.index("apiFetch"))
        self.assertIn("if (!confirmed) return;", handler)
        self.assertIn('apiFetch(`/orders/collection-ops/gaps/${cursorId}/acknowledge`, { method: "POST" })', handler)
        self.assertIn("loadOrderCollectionOps();", handler)

    def test_texts_exist_in_both_languages_with_the_same_placeholders(self):
        ko, en = table(KO), table(EN)
        self.assertEqual(set(ko), set(en))
        self.assertEqual(len(ko), 8)
        for key in ko:
            self.assertEqual(set(re.findall(r"\{\w+\}", ko[key])), set(re.findall(r"\{\w+\}", en[key])), key)
        self.assertEqual(set(re.findall(r"\{\w+\}", ko["odo.gap_item"])), {"{old}", "{from}", "{new}", "{created}"})
        self.assertIn(".odo-gap-list {", CSS)

    def test_each_placeholder_appears_once_because_i18n_replaces_only_the_first(self):
        # 2026-11-21 실제 화면에서 두 번째 {from}이 그대로 보였다 — i18n.js t()는 자리마다 첫 번째만 바꾼다.
        self.assertIn("acc.replace(`{${paramKey}}`", I18N_JS)
        texts = list(table(KO).items()) + list(table(EN).items())
        texts += [(k, json.loads(v)) for k, v in re.findall(r'"(easy\.today\.gap_[a-z_]+)": ("(?:[^"\\]|\\.)*")', EASY_TEXTS)]
        self.assertEqual(len(texts), 30)
        for key, value in texts:
            for name in set(re.findall(r"\{\w+\}", value)):
                self.assertEqual(value.count(name), 1, (key, name))


class EasyViewTests(unittest.TestCase):
    """2026-11-21 — 쉬운 보기에는 '주문 자동 감지' 메뉴가 없어 알림을 찾을 수 없었다(사용자 "확인함 어디서 확인하는것이지").
    '오늘 할 일'에 확인 필요 줄을 올리고 그 화면으로 보낸다."""

    def block(self):
        start = EASY.index("// 3-1) 2026-11-21")
        return EASY[start:EASY.index("// 4) 허용 요청", start)]

    def test_today_list_shows_the_gap_and_links_to_the_screen(self):
        block = self.block()
        self.assertIn("collect.collection_gaps", block)
        self.assertIn('group: "CHECK"', block)
        self.assertIn('action: { label: T("easy.today.gap_action"), run: () => B.navigateTo("order-collection-ops") }', block)
        self.assertIn("if (gaps.length) {", block)
        load = EASY[EASY.index("async function loadToday()"):]
        self.assertLess(load.index('api("/orders/collection-ops/status")'), load.index("// 3-1) 2026-11-21"))
        self.assertLess(EASY.index("// 3-1) 2026-11-21"), EASY.index("// 4) 허용 요청"))

    def test_easy_texts_exist_in_both_languages_with_the_same_placeholders(self):
        found = re.findall(r'"(easy\.today\.gap_[a-z_]+)": ("(?:[^"\\]|\\.)*")', EASY_TEXTS)
        keys = [k for k, _ in found]
        self.assertEqual(len(keys), 14)
        ko = {k: json.loads(v) for k, v in found[:7]}
        en = {k: json.loads(v) for k, v in found[7:]}
        self.assertEqual(set(ko), set(en))
        for key in ko:
            self.assertEqual(set(re.findall(r"\{\w+\}", ko[key])), set(re.findall(r"\{\w+\}", en[key])), key)
        self.assertIn("WING에서 확인함", ko["easy.today.gap_detail"])
        self.assertIn("{from}", ko["easy.today.gap_sub"])
        self.assertLess(EASY_TEXTS.index('"easy.today.gap_title": "주문을'), EASY_TEXTS.index('"easy.today.gap_title": "Check'))


if __name__ == "__main__":
    unittest.main()
