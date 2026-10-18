"""
=========================================================
Homez OS

File : tests/test_marketing_name_ui.py

2026-10-18 — 마케팅 AI M1(상품명·검색어 제안)의 화면·라우터·배선을 소스 수준에서 고정한다(바깥 호출 0).

  - 화면: 등록 도우미 3단계 AI 초안 패널 바로 뒤에 붙고, 확인창 → 요청 순서, 요청 본문 없음, 고른 상품명 1개와 체크한 검색어만 보낸다. 모든 서버 글은 이스케이프한다.
  - 문구: 한국어·영어 짝, 자리표시자 일치·중복 없음, 쓰지 않는 문구 없음. Phase B 패널 검사 범위(`lwAttachAiDraft`)를 건드리지 않는다.
  - 라우터: 제안·적용 모두 위저드 편집 권한, 제안은 본문·쿼리 없음, 적용은 정해진 필드만 받는다(extra 금지).
  - 서비스: AI는 `call_llm` 한 경로, 외부 호출·실행·파일 접근 없음.
=========================================================
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
ROUTER = (ROOT / "app/domains/marketplace_listing/listing_wizard_router.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/domains/marketplace_listing/marketing_name_service.py").read_text(encoding="utf-8")


def panel_source():
    start = CONSOLE.index("function lwAttachMarketingNames(content)")
    return CONSOLE[start:CONSOLE.index("// 2026-10-14 — 제품 색에 맞춘 배경 고르기", start)]


class ConsolePanelTests(unittest.TestCase):

    def test_the_panel_hangs_off_the_draft_step_right_after_the_ai_draft_panel(self):
        draft = CONSOLE[CONSOLE.index("function lwRenderDraftStep"):CONSOLE.index("function lwAttachAiDraft(content)")]
        self.assertIn("lwAttachAiDraft(content);\n    lwAttachMarketingNames(content);", draft)
        self.assertIn('content.querySelector("[data-lw-ai]")', panel_source())
        self.assertIn("anchor.after(panel)", panel_source())

    def test_it_stays_outside_the_phase_b_panel_window(self):
        from tests.test_ai_draft_ui import panel_source as phase_b_source

        self.assertNotIn("lwAttachMarketingNames", phase_b_source())

    def test_propose_asks_first_and_sends_no_body(self):
        src = panel_source()
        self.assertLess(src.index("askConfirm("), src.index('apiFetch(base, { method: "POST" })'))
        self.assertIn("if (!ok) return;", src)
        self.assertEqual(re.findall(r"apiFetch\(([^)]*?)\)", src.replace("{ method: \"POST\", body: JSON.stringify(body) }", "X")),
                         ['base, { method: "POST" }', "`${base}/apply`, X", "`/listing-wizards/${wizardId}`"])

    def test_apply_sends_only_the_picked_name_and_checked_keywords_with_the_version(self):
        src = panel_source()
        self.assertIn("const body = { expected_version: lwState.wizard.version };", src)
        self.assertIn("if (index >= 0 && proposal.names[index]) body.product_name = proposal.names[index].name;", src)
        self.assertIn('[data-lw-mkt-kw]:checked', src)
        self.assertIn("if (words.length) body.add_keywords = words;", src)
        self.assertIn("if (Object.keys(body).length === 1)", src)
        self.assertIn('data-lw-mkt-name="-1" checked', src)  # 기본은 "상품명은 그대로"

    def test_every_server_text_is_escaped_and_nothing_is_stored_in_the_browser(self):
        src = panel_source()
        for expr in ("escapeHtml(item.name)", "escapeHtml(item.why || \"\")", "escapeHtml(word)", "escapeHtml(why)", "escapeHtml(err.message", "escapeHtml(signal)"):
            self.assertIn(expr, src, expr)
        self.assertIn("escapeHtml((data.current && data.current.product_name) || \"—\")", src)
        for forbidden in ("localStorage", "sessionStorage", "document.cookie", "eval(", "Function("):
            self.assertNotIn(forbidden, src, forbidden)
        # innerHTML에 서버 값을 그대로 넣는 곳이 없다: item./word/why/data. 로 시작하는 보간은 모두 escapeHtml 안에 있다
        raw = re.findall(r"\$\{(?!escapeHtml|t\(|HomezI18n|index|names|keywords|nameRows|keywordRows|rejected|usage|signal|cached)([^}]*)\}", src)
        # data.at는 escapeHtml(t("mkt_cached", ...)) 안에서 시각 변환(new Date)에만 쓰인다
        self.assertEqual([r for r in raw if re.match(r"\s*(item|word|why|data|err)\b", r) and r.strip() != "data.at"], [])
        self.assertIn('escapeHtml(t("mkt_cached", { at: new Date(`${data.at}Z`).toLocaleString() }))', src)


class TextTests(unittest.TestCase):

    def keys(self):
        return sorted(set(re.findall(r't\("(mkt_[a-z_]+)"', panel_source())))

    def test_every_used_text_exists_in_both_languages_with_matching_placeholders_and_no_repeats(self):
        keys = self.keys()
        self.assertGreaterEqual(len(keys), 18)
        for key in keys:
            kv = re.search(rf'"lw\.{key}": "((?:[^"\\]|\\.)*)"', KO)
            ev = re.search(rf'"lw\.{key}": "((?:[^"\\]|\\.)*)"', EN)
            self.assertTrue(kv and ev, key)
            names_ko, names_en = re.findall(r"\{(\w+)\}", kv.group(1)), re.findall(r"\{(\w+)\}", ev.group(1))
            self.assertEqual(set(names_ko), set(names_en), key)
            self.assertEqual(len(names_ko), len(set(names_ko)), key)  # 같은 자리표시자를 두 번 쓰면 첫 번째만 바뀐다

    def test_no_defined_text_is_unused(self):
        defined = set(re.findall(r'"lw\.(mkt_[a-z_]+)":', KO))
        self.assertEqual(defined, set(self.keys()))
        self.assertEqual(defined, set(re.findall(r'"lw\.(mkt_[a-z_]+)":', EN)))

    def test_the_screen_says_it_is_not_coupang_search_volume(self):
        for source, phrase in ((KO, "쿠팡 검색량이 아니에요"), (EN, "not Coupang search volume")):
            self.assertIn(phrase, source)


class RouterTests(unittest.TestCase):

    def routes(self):
        import app.domains.marketplace_listing.listing_wizard_router as module

        return {r.path: r for r in module.router.routes}

    def test_both_routes_are_post_and_need_the_wizard_edit_permission(self):
        routes = self.routes()
        for path in ("/listing-wizards/{wizard_id}/ai-draft/names", "/listing-wizards/{wizard_id}/ai-draft/names/apply"):
            self.assertEqual(routes[path].methods, {"POST"}, path)
        block = ROUTER[ROUTER.index("class MarketingNamesApplyRequest"):ROUTER.index('@router.post("/{wizard_id}/coupang/contents-from-media")')]
        self.assertEqual(block.count("Depends(ListingWizardPermissionGuard(LISTING_WIZARD_EDIT))"), 2)

    def test_propose_takes_no_body_and_no_query_and_apply_takes_fixed_fields(self):
        routes = self.routes()
        propose = routes["/listing-wizards/{wizard_id}/ai-draft/names"].dependant
        self.assertEqual(propose.body_params, [])
        self.assertEqual(propose.query_params, [])
        block = ROUTER[ROUTER.index("class MarketingNamesApplyRequest"):]
        cls = block.split("def _marketing_name_service")[0]
        self.assertIn('extra="forbid"', cls)
        self.assertEqual(re.findall(r"^    (\w+): ", cls, re.M), ["expected_version", "product_name", "add_keywords"])

    def test_the_new_routes_do_not_shadow_the_existing_ai_draft_routes(self):
        paths = [p for p in self.routes() if "/ai-draft/" in p]
        for existing in ("status", "settings", "propose", "apply"):
            self.assertIn(f"/listing-wizards/{{wizard_id}}/ai-draft/{existing}", paths)


class ServiceWiringTests(unittest.TestCase):

    def test_the_ai_goes_only_through_the_guarded_path_with_the_draft_allowance(self):
        self.assertIn("self.ai.call_llm(", SERVICE)
        self.assertIn("ext_purpose=CallPurpose.AI_DRAFT", SERVICE)
        for forbidden in (r"client\.complete\(", r"\brequests\.", r"\bsubprocess\b", r"\bopen\(", r"\bshutil\b", r"\bos\.", r"\bprint\(", r"trend_discovery", r"NaverDataLab", r"api_usage_tracker", r"urllib"):
            self.assertIsNone(re.search(forbidden, SERVICE), forbidden)

    def test_no_new_external_call_system_or_purpose_was_added(self):
        from app.domains.external_call.constants import CallPurpose

        self.assertIn(CallPurpose.AI_DRAFT, CallPurpose.ENFORCED)

    def test_the_signal_is_only_read_from_what_is_already_stored(self):
        body = SERVICE[SERVICE.index("def _signal"):SERVICE.index("# ---- 저장된 제안")]
        self.assertIn("ProductCandidateEvidence", body)
        self.assertIn("get_visible_for_company", body)  # 회사에 보이는 후보만
        for forbidden in ("adapter", "fetch(", "TrendAdapter"):
            self.assertNotIn(forbidden, body, forbidden)


if __name__ == "__main__":
    unittest.main()
