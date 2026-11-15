"""
=========================================================
Homez OS

File : tests/test_capability_catalog_entry_points.py

2026-10-19 — 마케팅 AI M5: AI 역량 등록부(`capability_catalog.py`)가 **현실을 말하는지** 기계로 확인한다. DB·네트워크·AI 없음.

  - `actual_entry_points`에 `파일::이름` 형태로 적힌 코드 경로는 실제로 있어야 한다: 파일이 있고, 거기 적힌 클래스·함수 이름이 그 파일에 정의돼 있어야 한다(없는 기능을 등록하지 않는다).
  - 이번 라운드(마케팅·진단·후보 평가)에서 만든 기능이 정직하게 올라가 있다: 어느 역량에 올렸는지, 사용자가 누를 때만 AI를 부르는지, 실제 호출은 아직 하지 않았다는 사실, 판정·변경을 하지 않는다는 한계.
  - 새 역량 코드·새 호출 문턱·유형 변경은 없다(13개 그대로, 유형은 건드리지 않는다).
=========================================================
"""

import re
import unittest
from pathlib import Path

from app.domains.ai_governance import capability_catalog as catalog
from app.domains.ai_governance.capability_catalog import AI_CAPABILITY_CATALOG
from app.domains.ai_governance.constants import CapabilityCode

ROOT = Path(__file__).resolve().parents[1]
_PATH_FORM = re.compile(r"^(app/[\w/.\-]+\.(?:py|js))::(.+)$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BY_CODE = {c.capability_code: c for c in AI_CAPABILITY_CATALOG}


def symbols_of(rest: str) -> list[str]:
    """`ClassName.method()/other() (설명)` → [ClassName, method, other]. 설명(괄호 안)은 보지 않는다."""

    head = rest.split(" (", 1)[0]
    return _IDENT.findall(head)


def defined(source: str, name: str) -> bool:
    patterns = (rf"^\s*(?:async\s+)?def {name}\b", rf"^\s*class {name}\b", rf"^\s*(?:async\s+)?function {name}\b", rf"^\s*(?:const|let|var) {name}\b", rf"^{name}\s*=")
    return any(re.search(p, source, re.M) for p in patterns)


class EntryPointTests(unittest.TestCase):

    def test_every_path_style_entry_point_exists_with_its_symbols(self):
        checked = 0
        for contract in AI_CAPABILITY_CATALOG:
            for entry in contract.actual_entry_points:
                match = _PATH_FORM.match(entry)
                if not match:
                    continue   # "GET /경로 (파일)" 같은 HTTP 설명 형식은 건너뛴다
                path, rest = match.groups()
                file = ROOT / path
                self.assertTrue(file.exists(), f"{contract.capability_code}: 파일이 없습니다 — {path}")
                source = file.read_text(encoding="utf-8")
                for name in symbols_of(rest):
                    self.assertTrue(defined(source, name), f"{contract.capability_code}: {path}에 {name} 정의가 없습니다")
                checked += 1
        self.assertGreater(checked, 25)

    def test_the_checker_itself_catches_a_missing_symbol_or_file(self):
        self.assertFalse(defined("def other():\n    pass\n", "missing"))
        self.assertTrue(defined("class A:\n    def run(self):\n        pass\n", "run"))
        self.assertTrue(defined("  async function loadAnalysis() {\n", "loadAnalysis"))
        self.assertEqual(symbols_of("Cls.a()/b() (설명 c)"), ["Cls", "a", "b"])
        self.assertFalse((ROOT / "app/domains/nope/none.py").exists())

    def test_the_thirteen_capabilities_and_their_types_are_unchanged(self):
        self.assertEqual(len(AI_CAPABILITY_CATALOG), 13)
        self.assertEqual(set(BY_CODE), set(CapabilityCode.ALL))
        expected = {
            "PRODUCT_DISCOVERY": "EXTERNAL_DATA_PROVIDER", "PRODUCT_ANALYSIS": "RULE_ENGINE", "PRODUCT_SELECTION": "RULE_ENGINE",
            "PROFITABILITY_CALCULATION": "CALCULATOR", "CONTENT_GENERATION": "FAKE_PROVIDER", "IMAGE_PROCESSING": "FAKE_PROVIDER",
            "SUPPLIER_RECOMMENDATION": "FAKE_PROVIDER", "PRICING_INVENTORY": "CALCULATOR", "ORDER_SHIPMENT_RETURN": "RULE_ENGINE",
            "SETTLEMENT": "CALCULATOR", "OPERATIONS_COORDINATION": "CALCULATOR",
        }
        for code, kind in expected.items():
            self.assertEqual(BY_CODE[code].capability_type, kind, code)
        self.assertEqual(catalog.CAPABILITY_CATALOG_VERSION, "2.5.0")
        self.assertTrue(all(c.active for c in AI_CAPABILITY_CATALOG))


class HonestRegistrationTests(unittest.TestCase):

    def entries(self, code):
        return " | ".join(BY_CODE[code].actual_entry_points)

    def status(self, code):
        return BY_CODE[code].provider_status

    def test_the_marketing_features_are_registered_where_they_belong(self):
        self.assertIn("marketing_name_service.py::MarketingNameService", self.entries("CONTENT_GENERATION"))
        pricing = self.entries("PRICING_INVENTORY")
        for expected in ("price_strategy.py::strategy_report", "competitor_price_stats.py::compare", "competitor_price_service.py::CompetitorPriceService"):
            self.assertIn(expected, pricing)
        returns = self.entries("ORDER_SHIPMENT_RETURN")
        for expected in ("sales_performance_service.py::SalesPerformanceService", "marketing_outcomes.py::summarize", "product_rejection_advice.py::classify_rejection"):
            self.assertIn(expected, returns)

    def test_diagnostics_and_candidate_evaluation_are_registered(self):
        ops = self.entries("OPERATIONS_COORDINATION")
        for expected in ("health_summary.py::evaluate", "health_ai_service.py::HealthAiService", "easy_view.js::briefingFrom"):
            self.assertIn(expected, ops)
        selection = self.entries("PRODUCT_SELECTION")
        for expected in ("candidate_evaluation.py::evaluate", "candidate_similarity_service.py::CandidateSimilarityService"):
            self.assertIn(expected, selection)

    def test_every_ai_touching_status_says_it_runs_only_when_the_user_presses_and_has_not_called_the_real_api(self):
        for code in ("CONTENT_GENERATION", "PRICING_INVENTORY", "OPERATIONS_COORDINATION"):
            status = self.status(code)
            self.assertIn("사용자가 누를 때만", status, code)
            self.assertRegex(status, r"실제 (API |Claude |)호출(은|을)? (아직 )?(하지 않았|검증하지 않았)", code)

    def test_no_status_claims_the_ai_judges_or_changes_prices_or_hit_rates(self):
        pricing, returns = self.status("PRICING_INVENTORY"), self.status("ORDER_SHIPMENT_RETURN")
        self.assertIn("가격은 바꾸지 않는다", pricing)
        self.assertIn("마진 하한가 아래", pricing)
        self.assertIn("제안이 맞았는지는 판정하지 않는다", returns)
        self.assertIn("실제 쿠팡 응답은 아직 읽지 않았다", returns)
        for code in BY_CODE:
            for word in ("자동으로 가격을 바꾼", "적중률을 계산", "학습을 적용"):
                self.assertNotIn(word, self.status(code), code)

    def test_the_ai_reading_of_competitor_pages_is_the_only_new_ai_use_in_pricing(self):
        status = self.status("PRICING_INVENTORY")
        self.assertIn("AI_ANALYSIS", status)
        self.assertIn("금액·통계·제안은 코드가", status)

    def test_the_autonomous_round_features_are_registered_where_they_belong(self):
        image = self.entries("IMAGE_PROCESSING")
        for expected in ("reference_generation_service.py::ReferenceGenerationService", "batch_adjust_service.py::BatchAdjustService",
                         "concept_run_service.py::ConceptRunService", "concept_recommendation.py::recommend"):
            self.assertIn(expected, image)
        self.assertIn("offer_rules.py::rank_offers", self.entries("SUPPLIER_RECOMMENDATION"))
        self.assertIn("margin_watch_service.py::MarginWatchService", self.entries("PRICING_INVENTORY"))
        returns = self.entries("ORDER_SHIPMENT_RETURN")
        for expected in ("cs_reply_draft_service.py::CsReplyDraftService", "return_economics.py::calculate"):
            self.assertIn(expected, returns)

    def test_the_new_statuses_do_not_claim_a_real_image_api_real_ai_or_policy_checks(self):
        image = self.status("IMAGE_PROCESSING")
        self.assertIn("실제 이미지 생성 API는 한 번도 호출하지 않았다", image)
        self.assertIn("NOT_CHECKED", image)
        self.assertIn("미확인", image)
        self.assertIn("실제 AI 호출은 아직 하지 않았다", self.status("ORDER_SHIPMENT_RETURN"))
        self.assertIn("실제 공급처 호출은 하지 않았다", self.status("SUPPLIER_RECOMMENDATION"))
        self.assertIn("고치지 않고", self.status("PRICING_INVENTORY"))
        image_gaps = " ".join(BY_CODE["IMAGE_PROCESSING"].unimplemented_dependencies)
        # 2026-11-15(v2.5.0): '실제 이미지 생성 API 없음'은 Cloudflare·Gemini 연결 코드가 생겨 틀린 문장이 됐다 —
        # 같은 엄격함으로 지금 사실('호출 검증 없음 … 실제 호출 0회')을 고정한다.
        self.assertIn("실제 이미지 생성 API 호출 검증 없음", image_gaps)
        self.assertIn("실제 호출 0회", image_gaps)
        self.assertNotIn("실제 이미지 생성 API 없음", image_gaps)
        self.assertIn("정책 적합", image_gaps)

    def test_unimplemented_parts_are_stated(self):
        returns = " ".join(BY_CODE["ORDER_SHIPMENT_RETURN"].unimplemented_dependencies)
        self.assertIn("제안과 성과를 연결", returns)
        pricing = " ".join(BY_CODE["PRICING_INVENTORY"].unimplemented_dependencies)
        self.assertIn("가격 변경 요청을 만들지 않는다", pricing)


class Version250RegistrationTests(unittest.TestCase):
    """2026-11-15(v2.5.0, 작업 계획서 I6·S5·T6) — 10-23 이후 기능이 제자리에 정직하게 올라갔는지."""

    def entries(self, code):
        return " | ".join(BY_CODE[code].actual_entry_points)

    def status(self, code):
        return BY_CODE[code].provider_status

    def gaps(self, code):
        return " ".join(BY_CODE[code].unimplemented_dependencies)

    def test_new_features_are_registered_where_they_belong(self):
        expected = {
            "IMAGE_PROCESSING": ("ai_background_service.py::AiBackgroundService", "cloudflare_image_client.py::CloudflareImageClient",
                                 "image_ai_service.py::GeminiImageProvider", "concept_preview_service.py::ConceptPreviewService",
                                 "image_edit_record.py::ImageEditRecordService"),
            "PRODUCT_DISCOVERY": ("keyword_rule.py::extract_search_keyword", "jobs.py::run_discovery_auto_run_job", "seasonality.py::judge"),
            "PRODUCT_SELECTION": ("DecisionService.criteria_overview", "confirm_criteria", "preview_candidate", "input_suggestion.py::suggest_inputs"),
            "CONTENT_GENERATION": ("listing_claim_check.py::check_listing_text", "attribute_extract.py::extract",
                                   "attribute_suggest_service.py::AttributeSuggestService", "ai_draft_service.py::budget_stage"),
            "OPERATIONS_COORDINATION": ("morning_brief_service.py::MorningBriefService", "autonomy.py::AutonomyTableService"),
        }
        for code, names in expected.items():
            for name in names:
                self.assertIn(name, self.entries(code), code)

    def test_no_new_real_call_is_claimed(self):
        image = self.status("IMAGE_PROCESSING")
        self.assertIn("둘 다 실제 호출은 0회다", image)
        self.assertIn("비용 때문에 보류", image)
        self.assertIn("상품을 다시 그리지 않음", image)
        self.assertIn("대표(MAIN)에는 쓰지 않고", image)
        self.assertIn("실제로 그린 배경은 0장", self.gaps("IMAGE_PROCESSING"))
        self.assertIn("실제 Claude 호출은 아직 0회", self.status("CONTENT_GENERATION"))
        self.assertIn("관심 키워드 경로는 실제 호출이 아직 0회", self.status("PRODUCT_DISCOVERY"))
        self.assertIn("8회", self.status("PRODUCT_DISCOVERY"))
        for code in BY_CODE:
            for claim in ("Cloudflare 실제 호출 성공", "Gemini 실제 호출 성공", "Claude 실제 호출 성공"):
                self.assertNotIn(claim, self.status(code), code)

    def test_new_rules_only_warn_or_suggest_and_change_nothing(self):
        content = self.status("CONTENT_GENERATION")
        self.assertIn("**경고만**", content)
        self.assertIn("위저드 필수값을 저절로 채우지 않는다", content)
        ops = self.status("OPERATIONS_COORDINATION")
        self.assertIn("**제안만**", ops)
        self.assertIn("올리는 장치는 없고", ops)
        self.assertIn("금액은 보내지 않는다", ops)
        self.assertIn("올리는 장치는 없다", self.gaps("OPERATIONS_COORDINATION"))
        self.assertIn("평가를 하지 않는다(fail-closed)", self.status("PRODUCT_SELECTION"))

    def test_coupang_writes_are_kept_outside_the_pricing_capability(self):
        pricing = self.status("PRICING_INVENTORY")
        self.assertIn("이 역량 밖의 사람 실행 경로", pricing)
        self.assertIn("저절로 이어지지 않는다", pricing)
        self.assertIn("판매가 변경은 0회", pricing)

    def test_stale_sentences_were_corrected_not_left_contradicting_the_status(self):
        self.assertNotIn("아직 등록되지 않음)", self.gaps("PRODUCT_DISCOVERY"))
        self.assertIn("[해소됨 2026-09-07]", self.gaps("PRODUCT_DISCOVERY"))
        self.assertIn("③ 실제 호출 검증 완료: TRUE", self.status("PRODUCT_DISCOVERY"))
        self.assertNotIn("생성형(L2) 컨셉은 아직 없다", self.gaps("IMAGE_PROCESSING"))

    def test_types_still_follow_the_verified_call_rule(self):
        # 연결 코드만 있고 실제 호출이 검증되지 않은 역량은 유형을 올리지 않는다(PRODUCT_DISCOVERY는 2026-09-07 검증 뒤에 바꿨다).
        for code in ("IMAGE_PROCESSING", "CONTENT_GENERATION"):
            self.assertEqual(BY_CODE[code].capability_type, "FAKE_PROVIDER", code)
            self.assertIsNone(BY_CODE[code].external_provider, code)


if __name__ == "__main__":
    unittest.main()
