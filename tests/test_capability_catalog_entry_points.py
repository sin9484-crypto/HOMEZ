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
        self.assertEqual(catalog.CAPABILITY_CATALOG_VERSION, "2.3.0")
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

    def test_unimplemented_parts_are_stated(self):
        returns = " ".join(BY_CODE["ORDER_SHIPMENT_RETURN"].unimplemented_dependencies)
        self.assertIn("제안과 성과를 연결", returns)
        pricing = " ".join(BY_CODE["PRICING_INVENTORY"].unimplemented_dependencies)
        self.assertIn("가격 변경 요청을 만들지 않는다", pricing)


if __name__ == "__main__":
    unittest.main()
