"""
=========================================================
Homez OS

File : tests/test_ai_tools_ui.py

2026-10-23 — "AI 점검 도구" 화면(`console.js`의 `loadAiTools`와 at* 함수)을 고정한다. 실제 화면 함수를 Node에서 실행하고, 서버 호출 위치·메뉴·문구 짝은 소스에서 확인한다. 네트워크·DB 0.

  - 주문 마진 점검(P2)·답변 초안(P3)·반품 손익·공급 조건 비교(S3)·정책 변경 비교(T3)·약관 점검(T4)을 한 화면 카드로 보여 준다.
  - 화면은 새 판정을 만들지 않는다: 판정·이유 코드는 정해진 문구로 옮기고 모르는 코드는 일반 문구, 서버가 준 글은 이스케이프한다.
  - 입력을 고쳐 쓰지 않는다: 모르는 금액은 빈 값(null)으로 보내고, 숫자가 아니면 보내지 않고 어느 칸인지 알린다(0으로 바꾸지 않는다).
  - 약관은 항상 "법률 자문 아님"이 보이고, 뼈대는 그대로 쓸 수 없다고 알린다. 정책 비교는 빈 글을 "변경 없음"으로 쓰지 않는다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.test_reference_generation_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
HTML = (ROOT / "app/web/console.html").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
PANEL = function_source("loadAiTools")


def constants_source():
    return "\n".join(m.group(0) for m in re.finditer(r"^  const AT_[A-Z_]+ = [^\n]+$", JS, re.M))


def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"((?:at\.[a-z_0-9]+)|nav\.ai_tools)": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.split(`{${k}}`).join(String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""

FUNCTIONS = ("atWon", "atDecisionPill", "atReasonText", "atMarginHtml", "atParseFacts", "atCsProblem", "atCsResultHtml", "atReturnBody", "atReturnHtml",
             "atOfferRowToInput", "atOfferHtml", "atWatchHtml", "atTermsModels", "atTermsHtml")


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BehaviourTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join([constants_source()] + [function_source(n) for n in FUNCTIONS])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    # ---- 공통 ----
    def test_money_and_pills(self):
        self.assertEqual(self.run_js("atWon(12050)"), "12,050원")
        self.assertEqual(self.run_js("atWon(null)"), "—")
        self.assertEqual(self.run_js('atWon("")'), "—")
        self.assertEqual(self.run_js("atWon(0)"), "0원")
        self.assertEqual(self.run_js('atWon("abc")'), "—")
        self.assertIn("손실·차단", self.run_js('atDecisionPill("BLOCK")'))
        self.assertIn("badge-danger", self.run_js('atDecisionPill("EXCLUDE")'))
        unknown = self.run_js('atDecisionPill("<b>X</b>")')
        self.assertIn("알 수 없음", unknown)
        self.assertNotIn("<b>", unknown)

    def test_reason_text_uses_fixed_wording_and_hides_unknown_codes(self):
        self.assertEqual(self.run_js('atReasonText("mw", AT_MW_REASONS, {code: "LOSS", params: {percent: -3.2}})'), "손실이 납니다(마진 -3.2%).")
        self.assertEqual(self.run_js('atReasonText("mw", AT_MW_REASONS, {code: "EVIL<script>", params: {}})'), "알 수 없는 이유")
        self.assertEqual(self.run_js('atReasonText("mw", AT_MW_REASONS, null)'), "알 수 없는 이유")
        self.assertEqual(self.run_js('atReasonText("ret", AT_RET_REASONS, {code: "AMOUNTS_UNKNOWN", params: {fields: ["customer_paid", "fee_refund"]}})'),
                         "모르는 금액이 있어 손익을 만들지 않았습니다: 고객 결제액, 수수료 환급액")
        self.assertEqual(self.run_js('atReasonText("ret", AT_RET_REASONS, {code: "REFUND_ABOVE_PAID", params: {field: "supplier_refund"}})'),
                         "'공급처 환불액'이(가) 낸 금액보다 큽니다. 고치지 않고 막았습니다.")
        self.assertEqual(self.run_js('atReasonText("of", AT_OF_REASONS, {code: "MIN_ORDER_ABOVE_ONE", params: {qty: 5}})'), "최소 주문이 5개입니다.")

    # ---- P2 ----
    def test_margin_report(self):
        report = {"checked_tasks": 2, "counts": {"BLOCK": 1, "HOLD": 0, "WARN": 0, "OK": 1}, "rows": [
            {"task_id": 7, "title": "<i>바디워시</i>", "decision": "BLOCK", "margin_percent": -3.2, "floor_price": 14451, "reasons": [{"code": "LOSS", "params": {"percent": -3.2}}]},
            {"task_id": 5, "title": "백산수", "decision": "OK", "margin_percent": 18.24, "floor_price": None, "reasons": []},
        ]}
        html = self.run_js(f"atMarginHtml({json.dumps(report)})")
        self.assertIn("2건 점검 — 손실 1 · 보류 0 · 주의 0 · 정상 1", html)
        self.assertIn("약 14,451원", html)
        self.assertIn("18.24%", html)
        self.assertIn("손실이 납니다", html)
        self.assertIn("&lt;i&gt;바디워시", html)
        self.assertNotIn("<i>바디워시", html)
        self.assertIn("윙 설정은 앱이 읽거나 바꿀 수 없습니다", html)
        empty = self.run_js('atMarginHtml({checked_tasks: 0, counts: {}, rows: []})')
        self.assertIn("점검할 매입 작업이 없습니다", empty)
        self.assertIn("0건 점검 — 손실 …", empty)
        self.assertEqual(self.run_js("atMarginHtml(null)"), "")

    # ---- P3 ----
    def test_facts_parsing(self):
        self.assertEqual(self.run_js('atParseFacts("출고일: 10월 22일\\n\\n택배사: CJ")'), {"facts": {"출고일": "10월 22일", "택배사": "CJ"}, "error": None, "line": None})
        self.assertEqual(self.run_js('atParseFacts("")'), {"facts": {}, "error": None, "line": None})
        self.assertEqual(self.run_js('atParseFacts("출고일 10월")'), {"facts": None, "error": "cs_err_fact_line", "line": 1})
        self.assertEqual(self.run_js('atParseFacts("a: 1\\n: 2")')["line"], 2)
        self.assertEqual(self.run_js('atParseFacts("a:")')["error"], "cs_err_fact_line")
        nine = "\\n".join(f"k{i}: v" for i in range(9))
        self.assertEqual(self.run_js(f'atParseFacts("{nine}")')["error"], "cs_err_fact_many")
        eight = "\\n".join(f"k{i}: v" for i in range(8))
        self.assertIsNone(self.run_js(f'atParseFacts("{eight}")')["error"])

    def test_cs_problem(self):
        self.assertIsNone(self.run_js('atCsProblem({kind: "DELIVERY", inquiry: "언제 와요?"})'))
        self.assertEqual(self.run_js('atCsProblem({kind: "HACK", inquiry: "x"})'), "cs_err_kind")
        self.assertEqual(self.run_js('atCsProblem({kind: "OTHER", inquiry: "   "})'), "cs_err_empty")
        self.assertEqual(self.run_js('atCsProblem({kind: "OTHER", inquiry: "가".repeat(1501)})'), "cs_err_long")
        self.assertIsNone(self.run_js('atCsProblem({kind: "OTHER", inquiry: "가".repeat(1500)})'))

    def test_cs_result_shows_the_draft_escaped_with_checks_or_the_rejection(self):
        ok = {"draft": {"draft": "안녕하세요 <b>고객님</b>", "needs_check": ["[확인 필요: 출고일]"], "open_placeholders": 1}, "rejected": {}, "masked_count": 2, "usage": {"cost_krw": 3.2}}
        html = self.run_js(f"atCsResultHtml({json.dumps(ok)})")
        self.assertIn("readonly", html)
        self.assertIn("&lt;b&gt;고객님", html)
        self.assertNotIn("<b>고객님", html)
        self.assertIn("보내기 전에 확인할 것", html)
        self.assertIn("개인정보로 보이는 2곳", html)
        self.assertIn("쿠팡 윙에서 직접 보내세요", html)
        self.assertIn("자동 전송은 없습니다", html)
        bad = {"draft": None, "rejected": {"draft": "확정 약속으로 읽히는 표현이 있다<script>"}, "masked_count": 0, "usage": {"cost_krw": 1}}
        html = self.run_js(f"atCsResultHtml({json.dumps(bad)})")
        self.assertIn("안전 기준을 통과하지 못해 버렸습니다", html)
        self.assertIn("확정 약속으로 읽히는 표현이 있다&lt;script&gt;", html)
        self.assertNotIn("textarea", html)

    # ---- 반품 손익 ----
    def test_return_body_keeps_unknowns_empty_and_refuses_bad_numbers(self):
        result = self.run_js('atReturnBody({customer_paid: "12,050원", customer_refund: "6050", supplier_paid: " ", fee_refund: ""}, {WING_RECEIPT_CONFIRMED: true, SETTLEMENT_CHECKED: false, HACK: true, SUPPLIER_REPLY_CHECKED: "yes"})')
        self.assertIsNone(result["error"])
        self.assertEqual(result["body"]["customer_paid"], "12050")
        self.assertEqual(result["body"]["customer_refund"], "6050")
        for field in ("supplier_paid", "supplier_refund", "supplier_deduction", "fee_refund"):
            self.assertIsNone(result["body"][field], field)                       # 모르는 값은 0이 아니라 빈 값
        self.assertEqual(result["body"]["done"], {"WING_RECEIPT_CONFIRMED": True, "SETTLEMENT_CHECKED": False})
        for bad in ("-100", "12a", "1e5"):
            refused = self.run_js(f'atReturnBody({{supplier_paid: "{bad}"}}, {{}})')
            self.assertEqual((refused["body"], refused["error"], refused["field"]), (None, "ret_err_amount", "supplier_paid"), bad)

    def test_return_result(self):
        result = {"economics": {"decision": "OK", "customer_kept": 6000, "supplier_unrecovered": 6000, "net_before_fee": 0, "net_with_fee_refund": None,
                                "reasons": [{"code": "SETTLEMENT_UNCONFIRMED", "level": "INFO", "params": {}}]},
                  "checkpoints": {"steps": [{"code": "SUPPLIER_REPLY_CHECKED", "done": True}, {"code": "EXTRA_REQUESTS_HANDLED", "done": None},
                                            {"code": "SHIPPING_DEDUCTION_DECIDED", "done": False}, {"code": "WING_RECEIPT_CONFIRMED", "done": True}, {"code": "SETTLEMENT_CHECKED", "done": None}],
                                  "next_step": "EXTRA_REQUESTS_HANDLED", "all_done": False, "warnings": [{"code": "RECEIPT_BEFORE_NEGOTIATION", "steps": []}]}}
        html = self.run_js(f"atReturnHtml({json.dumps(result)})")
        self.assertIn("고객에게서 남은 돈: 6,000원", html)
        self.assertIn("손익(수수료 환급 전): 0원", html)
        self.assertIn("손익(수수료 환급 포함): 모름", html)                     # 모르는 값은 0원으로 쓰지 않는다
        self.assertIn("정산 확인 필요", html)
        self.assertIn("공급사 답변 확인 — 했음", html)
        self.assertIn("반품 배송비 차감 수용 판단 — 아직", html)
        self.assertIn("정산 확인 — 확인 전", html)
        self.assertIn("다음 단계: 추가 요청 처리", html)
        self.assertIn("되돌릴 수 없는 단계가 앞서 갔으니", html)
        self.assertEqual(self.run_js("atReturnHtml(null)"), "")

    # ---- S3 ----
    def test_offer_row_conversion(self):
        ok = self.run_js('atOfferRowToInput({supplier: " 도매몰A ", external_id: "", unit_cost: "5,310", shipping_cost: "3000", return_shipping_cost: "", min_order_qty: "1", dropship: "YES"}, 2)')
        self.assertIsNone(ok["error"])
        self.assertEqual(ok["offer"], {"supplier_code": "도매몰A", "external_id": "3", "title": "", "unit_cost": "5310", "shipping_cost": "3000", "return_shipping_cost": None,
                                       "min_order_qty": 1, "price_visible": True, "dropship_supported": True})
        unknown = self.run_js('atOfferRowToInput({supplier: "B", dropship: ""}, 0)')["offer"]
        self.assertEqual((unknown["unit_cost"], unknown["shipping_cost"], unknown["dropship_supported"], unknown["min_order_qty"]), (None, None, None, None))
        self.assertFalse(self.run_js('atOfferRowToInput({supplier: "B", dropship: "NO"}, 0)')["offer"]["dropship_supported"])
        self.assertEqual(self.run_js('atOfferRowToInput({supplier: "", unit_cost: "1"}, 0)')["error"], "of_err_supplier")
        for bad in ("-1", "1.2.3", "abc"):
            self.assertEqual(self.run_js(f'atOfferRowToInput({{supplier: "B", shipping_cost: "{bad}"}}, 0)')["error"], "of_err_number", bad)

    def test_offer_table(self):
        data = {"results": [{"rank": 1, "supplier_code": "A<b>", "external_id": "9", "title": "", "decision": "READY", "floor_price": 14451, "margin_at_target_percent": 20.1,
                             "reasons": [{"code": "RETURN_COST_UNKNOWN", "params": {}}]},
                            {"rank": 2, "supplier_code": "B", "external_id": "1", "title": "", "decision": "EXCLUDE", "floor_price": None, "margin_at_target_percent": None,
                             "reasons": [{"code": "DROPSHIP_NO", "params": {}}]}]}
        html = self.run_js(f"atOfferHtml({json.dumps(data)})")
        self.assertIn("A&lt;b&gt;", html)
        self.assertIn("14,451원", html)
        self.assertIn("20.1%", html)
        self.assertIn("반품 배송비를 모릅니다", html)
        self.assertIn("위탁(무재고) 판매가 안 됩니다", html)
        self.assertIn("가정값입니다", html)

    # ---- T3 ----
    def test_watch_result(self):
        unknown = self.run_js('atWatchHtml({status: "UNKNOWN", added: [], removed: []})')
        self.assertIn("'변경 없음'이 아닙니다", unknown)
        same = self.run_js('atWatchHtml({status: "UNCHANGED"})')
        self.assertIn("복사한 범위 밖의 변경은 알 수 없습니다", same)
        self.assertNotIn("새로 생긴 줄", same)
        changed = {"status": "CHANGED", "added": ["원산지 <표기>"], "removed": ["옛 줄"], "added_count": 1, "removed_count": 1, "truncated": True,
                   "impact_candidates": [{"area": "원산지·제조국", "matched_words": ["원산지"], "rule_codes": ["PROHIBITED_ORIGIN_COUNTRY"]},
                                         {"area": "반품·환불·취소", "matched_words": ["반품"], "rule_codes": []}]}
        html = self.run_js(f"atWatchHtml({json.dumps(changed)})")
        self.assertIn("원산지 &lt;표기&gt;", html)
        self.assertIn("새로 생긴 줄 1개", html)
        self.assertIn("관련 규칙: PROHIBITED_ORIGIN_COUNTRY", html)
        self.assertIn("규칙 목록에 아직 없는 영역", html)
        self.assertIn("일부만 보여 줍니다", html)
        self.assertIn("규칙은 자동으로 바뀌지 않습니다", html)
        none = self.run_js('atWatchHtml({status: "CHANGED", added: ["x"], removed: [], added_count: 1, removed_count: 0, impact_candidates: []})')
        self.assertIn("영향이 없다는 뜻은 아니니", none)
        weird = self.run_js('atWatchHtml({status: "<b>?"})')
        self.assertIn("'변경 없음'이 아닙니다", weird)                         # 모르는 상태는 '알 수 없음'으로

    # ---- T4 ----
    def test_terms_models_and_results(self):
        self.assertIsNone(self.run_js('atTermsModels("UNKNOWN")'))
        self.assertEqual(self.run_js('atTermsModels("COMMON")'), [])
        self.assertEqual(self.run_js('atTermsModels("BOTH")'), ["PROXY_PURCHASE", "DROPSHIP"])
        self.assertEqual(self.run_js('atTermsModels("DROPSHIP")'), ["DROPSHIP"])
        self.assertEqual(self.run_js('atTermsModels("hack")'), [])
        coverage = {"status": "CHECKED", "mentioned": 1, "total": 2, "results": [
            {"code": "PRIVACY", "title": "개인정보 보호", "status": "MENTIONED", "must_cover": "x"},
            {"code": "DISPUTE", "title": "분쟁 <해결>", "status": "NOT_FOUND", "must_cover": "고객 불만 접수 창구"}]}
        html = self.run_js(f'atTermsHtml("coverage", {json.dumps(coverage)})')
        self.assertIn("법률 자문이 아닙니다", html)
        self.assertIn("2개 조항 중 1개를 언급합니다", html)
        self.assertIn("분쟁 &lt;해결&gt;", html)
        self.assertIn("고객 불만 접수 창구", html)
        self.assertIn("내용이 적절하다는 뜻이 아닙니다", html)
        self.assertIn("점검할 글이 없어", self.run_js('atTermsHtml("coverage", {status: "UNKNOWN"})'))
        skeleton = self.run_js('atTermsHtml("skeleton", {sections: [{title: "목적", must_cover: "m"}]})')
        self.assertIn("그대로 쓸 수 없습니다", skeleton)
        self.assertIn("법률 자문이 아닙니다", skeleton)
        checklist = self.run_js('atTermsHtml("checklist", {clauses: [{title: "배송", must_cover: "기간"}]})')
        self.assertIn("법률 자문이 아닙니다", checklist)
        self.assertIn("배송", checklist)


class SourceTests(unittest.TestCase):

    def test_the_menu_section_loader_and_tab_exist(self):
        self.assertIn('data-view="ai-tools"', HTML)
        self.assertIn('id="view-ai-tools"', HTML)
        self.assertIn('id="at-content"', HTML)
        self.assertIn('"ai-tools": loadAiTools,', JS)
        self.assertIn('["ai-tools", "nav.ai_tools"]', JS)

    def test_each_card_calls_the_right_route(self):
        for route in ("/purchase-tasks/margin-watch?min_margin_percent=", '"/orders/cs-reply-draft"', '"/orders/return-economics"', '"/product-import/supplier-offers/rank"',
                      '"/channel-policy/watch/compare"', "/channel-policy/terms/checklist", "/channel-policy/terms/skeleton", '"/channel-policy/terms/coverage"'):
            self.assertIn(route, PANEL, route)

    def test_checks_happen_before_the_server_is_called(self):
        cs = PANEL[PANEL.index('el("at-cs-btn")'): PANEL.index('el("at-ret-btn")')]
        self.assertLess(cs.index("atCsProblem(state)"), cs.index("/orders/cs-reply-draft"))
        self.assertLess(cs.index("atParseFacts("), cs.index("/orders/cs-reply-draft"))
        ret = PANEL[PANEL.index('el("at-ret-btn")'): PANEL.index("const offerRows")]
        self.assertLess(ret.index("atReturnBody(values, done)"), ret.index("/orders/return-economics"))
        of = PANEL[PANEL.index('el("at-of-btn")'): PANEL.index('el("at-pw-btn")')]
        self.assertLess(of.index("atOfferRowToInput(row, i)"), of.index("/product-import/supplier-offers/rank"))
        for part in (cs, ret, of):
            self.assertIn("withButtonGuard", part)

    def test_nothing_is_sent_or_saved_from_this_screen(self):
        for word in ("/send", "localStorage", "/apply", "price-change"):
            self.assertNotIn(word, PANEL, word)

    def test_every_used_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table(KO), table(EN)
        source = PANEL + "\n".join(function_source(n) for n in FUNCTIONS)
        used = set(re.findall(r'\bt\(\s*"([a-z_0-9]+)"', source)) | set(re.findall(r'HomezI18n\.t\("at\.([a-z_0-9]+)"', source))
        used |= {k for k in re.findall(r'"([a-z][a-z0-9_]+)"', source) if f"at.{k}" in ko}     # 삼항식·도우미(line 등)로 넘기는 키
        used |= {"cs_err_kind", "cs_err_empty", "cs_err_long", "cs_err_fact_line", "cs_err_fact_many", "ret_err_amount", "of_err_supplier", "of_err_number"}
        used |= {f"cs_kind_{k.lower()}" for k in ("DELIVERY", "RETURN_EXCHANGE", "PRODUCT", "OTHER")}
        for const, prefix in (("AT_MW_REASONS", "mw_reason_"), ("AT_RET_REASONS", "ret_reason_"), ("AT_OF_REASONS", "of_reason_"), ("AT_RET_STEPS", "ret_step_"),
                              ("AT_DECISIONS", "decision_"), ("AT_RET_FIELDS", "ret_field_")):
            codes = re.search(const + r" = \[([^\]]+)\]", JS).group(1).replace('"', "").replace(" ", "").split(",")
            used |= {prefix + c.lower() for c in codes}
        used |= {"mw_reason_other", "ret_reason_other", "of_reason_other", "ret_step_other", "decision_other", "pw_status_unchanged", "pw_status_changed", "pw_status_unknown", "view_hint"}
        for key in sorted(used):
            self.assertIn(f"at.{key}", ko, f"한국어 문구 없음: {key}")
            self.assertIn(f"at.{key}", en, f"영어 문구 없음: {key}")
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[f"at.{key}"])), sorted(re.findall(r"\{(\w+)\}", en[f"at.{key}"])), key)
        self.assertEqual(set(ko), set(en))
        unused = {k for k in ko if k.startswith("at.") and k[3:] not in used}
        self.assertEqual(unused, set(), "쓰지 않는 문구가 있다")

    def test_fixed_notices_say_what_matters(self):
        ko = table(KO)
        self.assertIn("법률 자문이 아닙니다", ko["at.tm_disclaimer"])
        self.assertIn("가져오지 않고", ko["at.pw_hint"])
        self.assertIn("고치지 않고", ko["at.mw_hint"])
        self.assertIn("저장·전송은 없습니다", ko["at.cs_hint"])
        self.assertIn("확인하지 못했습니다", ko["at.of_assumption_note"])
        self.assertEqual(ko["nav.ai_tools"], "AI 점검 도구")


class TermsQueryModelsTests(unittest.TestCase):

    def test_an_empty_models_value_means_common_clauses_only(self):
        import app.domains.channel_policy.router  # noqa: F401
        from app.domains.channel_policy import terms_checklist as tc

        module = sys.modules["app.domains.channel_policy.router"]
        user = SimpleNamespace(id=1, company_id=1)
        common = module.terms_checklist(models=[""], current_user=user)
        self.assertEqual(len(common["clauses"]), len(tc.applicable_clauses([])))
        self.assertEqual(common["models"], [])
        unknown = module.terms_checklist(models=None, current_user=user)
        self.assertEqual(len(unknown["clauses"]), len(tc.CLAUSES))
        drop = module.terms_skeleton(models=["", "DROPSHIP"], current_user=user)
        self.assertEqual(len(drop["sections"]), len(tc.applicable_clauses(["DROPSHIP"])))


if __name__ == "__main__":
    unittest.main()
