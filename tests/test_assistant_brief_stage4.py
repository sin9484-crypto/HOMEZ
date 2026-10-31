"""
=========================================================
Homez OS

File : tests/test_assistant_brief_stage4.py

2026-10-31 — AI 통합 계획 4단계(운영 비서 1단계, 규칙·비용 0)를 고정한다. 실제 `easy_view.js`의 순수 함수를 Node에서 실행한다.
네트워크·DB 없음.

  - 가장 먼저 볼 것: 받은 지 24시간 넘은 문의가 맨 앞(반려보다 앞). 문의끼리는 오래된 것이 먼저, 받은 시각을 모르면 원래 순서.
  - 왜 먼저인지: 행의 종류로 정한 규칙 문구. 문의는 받은 지 몇 시간, 모르면 '모름'.
  - 상품 발굴 칸: 3단계 탐색 흐름의 다음 할 일 개수(서버가 센 값 그대로), 읽지 못하면 '읽지 못했어요'.
  - 비서는 아무것도 보내지 않는다: 오늘 할 일 화면은 읽기(GET)만 하고, 새 함수에는 요청이 없다.
=========================================================
"""

import json
import os
import re
import subprocess
import unittest

from tests.test_easy_briefing_ui import HARNESS, JS, NODE, NOW, TEXTS, constants_source, function_source, ko_table

NEW_KEYS = re.findall(r'"(easy\.brief\.(?:why|disc|promise|f_age|age_unknown)[a-z_]*)"', JS)


def load_today_body():
    return JS.split("async function loadToday()")[1].split("\n  // =========================================================\n  // 상품")[0]


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BriefTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join([constants_source()] + [function_source(n) for n in ("parseUtcMs", "briefWeight", "briefSysText", "briefingFrom", "briefingHtml", "briefDiscoveryFrom")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__NOW__", str(NOW)).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(ko_table())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def urgent(self, rows):
        data = {"rows": rows, "ext": None, "collect": None, "today": None, "nowMs": NOW}
        return self.run_js(f"briefingFrom({json.dumps(data)}).urgent")

    # ---- 순서 ----

    def test_an_inquiry_older_than_24_hours_comes_before_everything_else(self):
        rows = [
            {"key": "pstatus:7", "group": "CHECK", "title": "반려", "product": {"name": "f"}},
            {"key": "invoice:3", "group": "CHECK", "title": "송장", "product": {"name": "e"}},
            {"key": "inq:9", "group": "CHECK", "title": "문의", "product": {"name": "d"}, "ageHours": 25},
        ]
        self.assertEqual([u["key"] for u in self.urgent(rows)], ["inq:9", "pstatus:7", "invoice:3"])

    def test_exactly_24_hours_counts_as_old_but_23_does_not(self):
        base = [{"key": "pstatus:7", "group": "CHECK", "title": "반려", "product": None}]
        self.assertEqual(self.urgent(base + [{"key": "inq:1", "group": "CHECK", "title": "문의", "product": None, "ageHours": 24}])[0]["key"], "inq:1")
        self.assertEqual(self.urgent(base + [{"key": "inq:1", "group": "CHECK", "title": "문의", "product": None, "ageHours": 23}])[0]["key"], "pstatus:7")

    def test_a_young_inquiry_keeps_its_old_place_after_rejections_and_invoices(self):
        rows = [
            {"key": "inq:9", "group": "CHECK", "title": "문의", "product": None, "ageHours": 3},
            {"key": "invoice:3", "group": "CHECK", "title": "송장", "product": None},
            {"key": "pstatus:7", "group": "CHECK", "title": "반려", "product": None},
        ]
        self.assertEqual([u["key"] for u in self.urgent(rows)], ["pstatus:7", "invoice:3", "inq:9"])

    def test_inquiries_of_the_same_weight_put_the_oldest_first_and_unknown_times_keep_their_order(self):
        rows = [
            {"key": "inq:a", "group": "CHECK", "title": "문의", "product": None, "ageHours": 2},
            {"key": "inq:b", "group": "CHECK", "title": "문의", "product": None, "ageHours": 10},
            {"key": "inq:c", "group": "CHECK", "title": "문의", "product": None, "ageHours": None},
        ]
        self.assertEqual([u["key"] for u in self.urgent(rows)], ["inq:b", "inq:a", "inq:c"])
        old = [
            {"key": "inq:x", "group": "CHECK", "title": "문의", "product": None, "ageHours": 30},
            {"key": "inq:y", "group": "CHECK", "title": "문의", "product": None, "ageHours": 50},
        ]
        self.assertEqual([u["key"] for u in self.urgent(old)], ["inq:y", "inq:x"])

    # ---- 왜 먼저인지 ----

    def test_each_urgent_item_says_why_by_its_kind(self):
        cases = [
            ({"key": "inq:1", "group": "CHECK", "ageHours": 30}, {"key": "easy.brief.why_inq_old", "h": 30}),
            ({"key": "inq:2", "group": "CHECK", "ageHours": 5}, {"key": "easy.brief.why_inq", "h": 5}),
            ({"key": "inq:5", "group": "CHECK", "ageHours": 24}, {"key": "easy.brief.why_inq_old", "h": 24}),
            ({"key": "inq:6", "group": "CHECK", "ageHours": 23}, {"key": "easy.brief.why_inq", "h": 23}),
            ({"key": "inq:3", "group": "CHECK", "ageHours": None}, {"key": "easy.brief.why_inq_unknown"}),
            ({"key": "inq:4", "group": "CHECK"}, {"key": "easy.brief.why_inq_unknown"}),
            ({"key": "pstatus:1", "group": "CHECK"}, {"key": "easy.brief.why_pstatus"}),
            ({"key": "invoice:1", "group": "CHECK"}, {"key": "easy.brief.why_invoice"}),
            ({"key": "return:1", "group": "CHECK"}, {"key": "easy.brief.why_return"}),
            ({"key": "supret:1", "group": "CHECK"}, {"key": "easy.brief.why_supret"}),
            ({"key": "unres:1", "group": "CHECK"}, {"key": "easy.brief.why_unres"}),
            ({"key": "detect", "group": "APPROVAL"}, {"key": "easy.brief.why_detect"}),
            ({"key": "days:1", "group": "CHECK"}, {"key": "easy.brief.why_days"}),
            ({"key": "allowances", "group": "APPROVAL"}, {"key": "easy.brief.why_approval"}),
            ({"key": "wizard:5", "group": "CHECK"}, {"key": "easy.brief.why_check"}),
            ({"key": "detector:1", "group": "CHECK"}, {"key": "easy.brief.why_check"}),
        ]
        for row, why in cases:
            got = self.urgent([{**row, "title": "t", "product": None}])[0]["why"]
            self.assertEqual(got, why, row)

    # ---- 상품 발굴 요약 ----

    def test_discovery_lines_follow_the_flow_order_and_drop_zero_counts(self):
        flow = {"rows": [], "counts": {"TREND": 4, "REVIEW": 1, "EVALUATE": 0, "NEW_PRODUCT": 2, "DONE_REVIEWED": 3, "CLOSED": 1}, "truncated": False}
        self.assertEqual(self.run_js(f"briefDiscoveryFrom({json.dumps(flow)})"),
                         {"lines": [{"next": "REVIEW", "n": 1}, {"next": "NEW_PRODUCT", "n": 2}, {"next": "TREND", "n": 4}], "truncated": False})

    def test_discovery_that_could_not_be_read_is_unknown_not_zero(self):
        for bad in ("null", "{}", '{"counts": null}', '{"counts": "x"}'):
            self.assertIsNone(self.run_js(f"briefDiscoveryFrom({bad})"), bad)
        self.assertEqual(self.run_js('briefDiscoveryFrom({"counts": {}, "truncated": true})'), {"lines": [], "truncated": True})

    # ---- HTML ----

    def brief_html(self, **extra):
        data = {"mine": 1, "urgent": [{"key": "inq:1", "title": "<b>문의</b>", "name": "주문", "why": {"key": "easy.brief.why_inq_old", "h": 30}}], "upcoming": [], "stalled": [], **extra}
        return self.run_js(f"briefingHtml({json.dumps(data)})")

    def test_the_reason_is_shown_under_each_urgent_item_and_escaped(self):
        html = self.brief_html()
        self.assertIn("왜 먼저: 고객이 30시간째 답을 기다려요(24시간 넘음)", html)
        self.assertNotIn("<b>문의", html)
        self.assertIn("비서는 보여 주고 준비만 해요", html)
        # 이유 문구도 글자로만 넣는다(문구표에 없는 키는 키 글자가 그대로 나온다).
        odd = self.run_js('briefingHtml({"mine": 1, "urgent": [{"key": "a", "title": "t", "name": "", "why": {"key": "<i>x</i>"}}], "upcoming": [], "stalled": []})')
        self.assertNotIn("<i>x", odd)
        self.assertIn("&lt;i&gt;x", odd)

    def test_the_discovery_card_shows_counts_and_a_button_to_the_flow(self):
        html = self.brief_html(discovery={"lines": [{"next": "REVIEW", "n": 2}, {"next": "TREND", "n": 5}], "truncated": True})
        for text in ("상품 발굴", "사람 검토를 기다리는 판매 판단 2건", "트렌드를 볼 후보 5건", "최근 후보 200개 기준", "data-ez-brief-flow", "탐색 흐름 보기"):
            self.assertIn(text, html)
        self.assertNotIn("repeat(3,1fr)", html)  # 칸이 넷이면 화면 폭에 맞는 기본 배치(넓으면 4, 좁으면 2)

    def test_the_discovery_card_says_unknown_or_nothing_to_do_and_hides_when_not_loaded(self):
        unknown = self.brief_html(discovery=None)
        self.assertIn("후보 흐름을 읽지 못했어요", unknown)
        self.assertNotIn("data-ez-brief-flow", unknown)
        empty = self.brief_html(discovery={"lines": [], "truncated": False})
        self.assertIn("지금 이어서 할 후보가 없어요", empty)
        self.assertIn("data-ez-brief-flow", empty)
        self.assertNotIn("최근 후보 200개 기준", empty)  # 잘리지 않았으면 '200개 기준'을 말하지 않는다
        missing = self.brief_html()
        self.assertNotIn("상품 발굴", missing)
        self.assertIn("repeat(3,1fr)", missing)

    def test_an_urgent_item_without_a_reason_still_renders(self):
        data = {"mine": 1, "urgent": [{"key": "a", "title": "하나", "name": ""}], "upcoming": [], "stalled": []}
        html = self.run_js(f"briefingHtml({json.dumps(data)})")
        self.assertIn("하나", html)
        self.assertNotIn("왜 먼저", html)


class WiringTests(unittest.TestCase):

    def test_the_today_screen_reads_the_discovery_flow_and_survives_its_failure(self):
        body = load_today_body()
        self.assertIn('api("/product-candidates/discovery-flow?limit=200").catch(() => null)', body)
        self.assertIn("brief.discovery = briefDiscoveryFrom(discFlow);", body)
        self.assertIn('S.selected.analysisTab = "FLOW"; B.navigateTo("easy-analysis");', body)

    def test_the_assistant_screen_only_reads_and_sends_nothing(self):
        body = load_today_body()
        self.assertNotIn("method:", body)
        for name in ("briefWeight", "briefingFrom", "briefingHtml", "briefDiscoveryFrom"):
            code = function_source(name)
            for forbidden in ("api(", "apiFetch", "fetch(", "XMLHttpRequest", "method:"):
                self.assertNotIn(forbidden, code, (name, forbidden))

    def test_inquiry_rows_carry_their_age_oldest_first_with_a_red_chip_after_24_hours(self):
        body = load_today_body()
        self.assertIn("inquiryOldestFirst((afterSales.inquiries_open || []).map((q) => ({ q, inquiry_at: q.t, h: inquiryAgeHours(q.t, inqNow) })))", body)
        self.assertIn("ageHours: h,", body)
        self.assertIn('h !== null && h >= INQ_OLD_HOURS ? chip("red", T("easy.cs.age_old", { h }))', body)
        self.assertIn('T("easy.brief.age_unknown")', body)

    def test_the_brief_uses_the_same_24_hour_mark_as_the_inquiry_screen(self):
        brief = int(re.search(r"const BRIEF_OLD_INQUIRY_HOURS = (\d+);", JS).group(1))
        screen = int(re.search(r"const INQ_OLD_HOURS = (\d+);", JS).group(1))
        self.assertEqual(brief, screen)

    def test_every_flow_step_counted_by_the_server_that_needs_work_has_a_line(self):
        from app.domains.product_candidate import discovery_flow_service as flow

        mapped = set(re.findall(r"([A-Z_]+): \"easy\.brief\.disc_", re.search(r"const BRIEF_FLOW_KEYS = [^\n]+", JS).group(0)))
        self.assertEqual(mapped, {flow.NEXT_REVIEW, flow.NEXT_EVALUATE, flow.NEXT_NEW_PRODUCT, flow.NEXT_TREND})

    def test_every_new_text_exists_in_both_languages_with_matching_placeholders(self):
        self.assertGreaterEqual(len(set(NEW_KEYS)), 25)
        ko = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        en = re.search(r'"en-US": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        for key in set(NEW_KEYS):
            kv = re.search(rf'"{re.escape(key)}": "([^"]*)"', ko)
            ev = re.search(rf'"{re.escape(key)}": "([^"]*)"', en)
            self.assertTrue(kv and ev, key)
            self.assertEqual(set(re.findall(r"\{(\w+)\}", kv.group(1))), set(re.findall(r"\{(\w+)\}", ev.group(1))), key)

    def test_the_reason_texts_do_not_claim_a_coupang_deadline(self):
        ko = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        for key in set(NEW_KEYS):
            text = re.search(rf'"{re.escape(key)}": "([^"]*)"', ko).group(1)
            self.assertNotIn("기한", text, key)
            self.assertNotIn("마감", text, key)


if __name__ == "__main__":
    unittest.main()
