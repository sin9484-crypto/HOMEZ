"""
=========================================================
Homez OS

File : tests/test_decision_evaluate_screen.py

2026-10-31 — AI 통합 계획 1단계 마무리: 판매 판단 AI '평가하기'(위저드 마진 제안 · 저장 없는 미리 보기 · 기록)를 고정한다.

  - 마진 제안의 첫 자료원은 이 후보의 등록 위저드 마진 계산이다. 등록 성공 위저드가 먼저, 보관·취소·실패는 쓰지 않는다.
    임시 계산·빠진 비용이면 쓰지 않고, 계정이 여럿이면 가장 낮은 마진율, 손해(음수)면 숫자를 내지 않는다(0으로 바꾸지 않음).
  - 다른 회사의 위저드는 보지 않는다. 위저드 표가 없는 환경에서도 다른 제안은 그대로 나온다.
  - 미리 보기는 평가 실행과 같은 계산·같은 막힘 규칙(기준 없음·긴급 중지)을 따르지만 평가·점수·감사 기록을 남기지 않는다.
  - 화면: 미리 본 입력과 지금 입력이 같을 때만, 확인 창 뒤에 기록한다. 마진은 %로 받아 비율로 보낸다.
격리 SQLite(실제 homez.db 사용 안 함), 외부 호출 없음.
=========================================================
"""

import json
import shutil
import subprocess
import unittest
import unittest.mock
from decimal import Decimal
from pathlib import Path

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.automation_safety.service import SafetyService
from app.domains.decision.input_suggestion import suggest_inputs
from app.domains.decision.input_suggestion import wizard_margin
from app.domains.decision.model import DecisionAuditLog
from app.domains.decision.model import DecisionEvaluation
from app.domains.decision.model import DecisionScore
from app.domains.decision.schema import DecisionSupplementaryInputs
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.service import ProductCandidateService
from app.database.base import Base
from tests.test_decision_ai import COMPANY_ID
import tests.test_decision_ai as decision_base  # 클래스를 직접 가져오면 그 시험들이 여기서 다시 돈다

ROOT = Path(__file__).resolve().parents[1]
ROUTER = (ROOT / "app/domains/decision/router.py").read_text(encoding="utf-8")
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")


def econ(*rates, provisional=False, missing=(), revenue="12900.00"):
    return json.dumps([
        {"marketplace_account_id": i + 1, "expected_revenue": revenue, "margin_rate": rate,
         "is_provisional": provisional, "missing_cost_fields": list(missing)}
        for i, rate in enumerate(rates)
    ])


class WizardMarginRuleTests(unittest.TestCase):

    def test_a_registered_wizard_wins_over_a_newer_draft(self):
        picked = wizard_margin([(1, "SUCCEEDED", econ("0.2500")), (5, "DRAFT", econ("0.3000"))])
        self.assertEqual((picked["wizard_id"], picked["registered"], picked["rate"]), (1, True, Decimal("0.2500")))

    def test_the_latest_registered_wizard_is_used(self):
        picked = wizard_margin([(8, "SUCCEEDED", econ("0.1824")), (4, "SUCCEEDED", econ("0.1900")), (9, "PARTIALLY_SUCCEEDED", econ("0.2000"))])
        self.assertEqual((picked["wizard_id"], picked["rate"]), (9, Decimal("0.2000")))

    def test_a_draft_is_used_when_nothing_was_registered(self):
        picked = wizard_margin([(2, "DRAFT", econ("0.2200")), (1, "NEEDS_CORRECTION", econ("0.2100"))])
        self.assertEqual((picked["wizard_id"], picked["registered"]), (2, False))

    def test_archived_cancelled_and_failed_wizards_are_never_used(self):
        for status in ("ARCHIVED", "CANCELLED", "FAILED"):
            self.assertIsNone(wizard_margin([(3, status, econ("0.3000"))]), status)
        picked = wizard_margin([(9, "FAILED", econ("0.3000")), (2, "DRAFT", econ("0.1900"))])
        self.assertEqual(picked["wizard_id"], 2)

    def test_provisional_or_incomplete_costs_skip_that_wizard(self):
        picked = wizard_margin([(9, "SUCCEEDED", econ("0.40", provisional=True)), (7, "SUCCEEDED", econ("0.35", missing=["shipping_cost"])),
                                (3, "SUCCEEDED", econ("0.2000"))])
        self.assertEqual(picked["wizard_id"], 3)
        self.assertIsNone(wizard_margin([(1, "SUCCEEDED", econ("0.2", "0.3", provisional=True))]))

    def test_with_several_accounts_the_lowest_margin_is_used(self):
        self.assertEqual(wizard_margin([(1, "SUCCEEDED", econ("0.2500", "0.1700", "0.3000"))])["rate"], Decimal("0.1700"))

    def test_unreadable_or_impossible_values_are_not_used(self):
        for raw in (None, "", "{", "{}", "[]", json.dumps(["x"]), econ("abc"), econ("1.0"), econ("NaN"), econ("Infinity"),
                    json.dumps([{"margin_rate": True, "is_provisional": False, "missing_cost_fields": []}]),
                    json.dumps([{"margin_rate": "0.2", "missing_cost_fields": []}])):
            self.assertIsNone(wizard_margin([(1, "SUCCEEDED", raw)]), raw)
        self.assertIsNone(wizard_margin(None))

    def test_a_loss_is_reported_and_not_turned_into_zero(self):
        self.assertEqual(wizard_margin([(1, "SUCCEEDED", econ("-0.0500"))])["rate"], Decimal("-0.0500"))
        result = suggest_inputs([], None, [(1, "SUCCEEDED", econ("-0.0500"))])
        self.assertNotIn("expected_margin_rate", result["inputs"])
        self.assertTrue(any("손해" in note for note in result["notes"]))


class SuggestionWithWizardTests(unittest.TestCase):

    def test_the_wizard_margin_comes_first_with_its_basis(self):
        snapshot = {"verdict": "CANDIDATE", "scenarios": [{"p": "15900", "m": "0.3000"}]}
        result = suggest_inputs([], snapshot, [(22, "SUCCEEDED", econ("0.2500"))])
        self.assertEqual(result["inputs"]["expected_margin_rate"], Decimal("0.2500"))
        basis = [b for b in result["basis"] if b["field"] == "expected_margin_rate"]
        self.assertEqual(basis[0]["codes"], ["WIZARD_MARGIN"])
        self.assertIn("등록한 위저드 #22", basis[0]["note"])
        self.assertIn("12,900원", basis[0]["note"])

    def test_without_a_usable_wizard_the_analysis_snapshot_rule_still_applies(self):
        snapshot = {"verdict": "CANDIDATE", "scenarios": [{"p": "15900", "m": "0.2200"}]}
        result = suggest_inputs([], snapshot, [(3, "ARCHIVED", econ("0.5"))])
        self.assertEqual(result["inputs"]["expected_margin_rate"], Decimal("0.2200"))
        self.assertEqual(suggest_inputs([], snapshot)["inputs"]["expected_margin_rate"], Decimal("0.2200"))

    def test_nothing_known_means_nothing_suggested(self):
        result = suggest_inputs([], None, [])
        self.assertNotIn("expected_margin_rate", result["inputs"])
        self.assertTrue(any("위저드" in note for note in result["notes"]))


class EvalBase(unittest.TestCase):
    # 기존 시험 클래스의 준비 함수만 빌린다(상속하면 그 시험들까지 다시 돈다).
    setUp_base = decision_base.DecisionAITestCase.setUp
    tearDown = decision_base.DecisionAITestCase.tearDown
    _create_safety_tables = decision_base.DecisionAITestCase._create_safety_tables
    _seed_policy = decision_base.DecisionAITestCase._seed_policy
    _create_candidate = decision_base.DecisionAITestCase._create_candidate
    _full_supplementary = decision_base.DecisionAITestCase._full_supplementary

    def setUp(self):
        self.setUp_base()
        self._create_safety_tables()
        Base.metadata.create_all(bind=self.engine, tables=[ListingWizard.__table__, ProductCandidateEvidence.__table__])

    def add_wizard(self, candidate_id, status, result_json, company_id=COMPANY_ID):
        wizard = ListingWizard(company_id=company_id, created_by_user_id=1, status=status, product_candidate_id=candidate_id,
                               economics_result_json=result_json, creation_idempotency_key=f"w-{candidate_id}-{status}-{company_id}-{result_json}"[:160])
        self.db.add(wizard)
        self.db.commit()
        return wizard

    def counts(self):
        return (self.db.query(DecisionEvaluation).count(), self.db.query(DecisionScore).count(), self.db.query(DecisionAuditLog).count())

    def suggest(self, candidate_id, company_id=COMPANY_ID):
        with unittest.mock.patch(
            "app.domains.product_candidate.candidate_evaluation_service.CandidateEvaluationService.evaluate_all",
            return_value={"items": [{"candidate_id": candidate_id, "decision": "READY", "reasons": []}]},
        ):
            return self.service.suggest_inputs(candidate_id, company_id)


class SuggestionServiceWizardTests(EvalBase):

    def test_the_service_uses_this_companys_wizard_only_and_writes_nothing(self):
        candidate = self._create_candidate(ref="W")
        self.add_wizard(candidate.id, "SUCCEEDED", econ("0.9000"), company_id=2)
        before = (self.db.query(ListingWizard).count(), self.counts())
        self.assertNotIn("expected_margin_rate", self.suggest(candidate.id)["inputs"], "다른 회사 위저드는 쓰지 않는다")
        self.add_wizard(candidate.id, "SUCCEEDED", econ("0.1824"))
        result = self.suggest(candidate.id)
        self.assertEqual(result["inputs"]["expected_margin_rate"], "0.1824")
        self.assertEqual((self.db.query(ListingWizard).count(), self.counts()), (before[0] + 1, before[1]))

    def test_without_the_wizard_table_other_suggestions_still_come(self):
        candidate = self._create_candidate(ref="NT")
        ListingWizard.__table__.drop(bind=self.engine)
        result = self.suggest(candidate.id)
        self.assertEqual(result["inputs"]["brand_ip_risk_score"], "70")
        self.assertNotIn("expected_margin_rate", result["inputs"])


class PreviewTests(EvalBase):

    def test_preview_matches_the_real_evaluation_and_saves_nothing(self):
        self._seed_policy()
        candidate = self._create_candidate()
        inputs = self._full_supplementary(expected_margin_rate=Decimal("0.17"))
        preview = self.service.preview_candidate(candidate.id, COMPANY_ID, inputs)
        self.assertEqual(self.counts(), (0, 0, 0), "미리 보기는 평가·점수·감사 기록을 남기지 않는다")
        evaluation, scores, _ = self.service.evaluate_candidate(candidate.id, COMPANY_ID, "prev-1", inputs)
        self.assertEqual((preview["recommendation"], preview["reason"]), (evaluation.recommendation, evaluation.recommendation_reason))
        self.assertEqual((Decimal(preview["total_score"]), Decimal(preview["confidence"])), (evaluation.total_score, evaluation.confidence))
        self.assertEqual([(a["axis"], Decimal(a["raw_score"]), a["data_sufficient"], a["risk_flag"]) for a in preview["axes"]],
                         [(s.axis, s.raw_score, s.data_sufficient, s.risk_flag) for s in scores])
        self.assertEqual(preview["recommendation"], "REVIEW_REQUIRED", "마진 17%는 회사 최소 18% 미만")
        self.assertEqual((preview["policy_source"], preview["fixed_rules"]["min_sufficient_axes"]), ("GLOBAL", 6))

    def test_preview_reports_missing_evidence_honestly(self):
        self._seed_policy()
        candidate = self._create_candidate(trend_score=None, demand_score=None, competition_score=None, margin_score=None,
                                           risk_score=None, confidence=None, novelty_score=None)
        preview = self.service.preview_candidate(candidate.id, COMPANY_ID, DecisionSupplementaryInputs(
            expected_margin_rate=Decimal("0.25"), policy_prohibited_score=Decimal("90"), brand_ip_risk_score=Decimal("70")))
        self.assertEqual((preview["recommendation"], preview["sufficient_count"]), ("INSUFFICIENT_DATA", 3))
        self.assertIn("SUPPLY_STABILITY", preview["missing_required"])

    def test_preview_is_blocked_without_criteria_and_writes_nothing(self):
        candidate = self._create_candidate()
        with self.assertRaises(BadRequestException):
            self.service.preview_candidate(candidate.id, COMPANY_ID, self._full_supplementary())
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_preview_is_blocked_by_emergency_stop_without_an_audit_row(self):
        self._seed_policy()
        candidate = self._create_candidate()
        SafetyService(self.db).activate_emergency_stop(reason="시험 긴급 정지", set_by=1, is_admin=True)
        with self.assertRaises(ForbiddenException):
            self.service.preview_candidate(candidate.id, COMPANY_ID, self._full_supplementary())
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_preview_follows_the_capability_switch(self):
        from tests.ai_governance_test_helpers import deactivated_capability

        self._seed_policy()
        candidate = self._create_candidate()
        with deactivated_capability("PRODUCT_SELECTION"):
            with self.assertRaises(Exception):
                self.service.preview_candidate(candidate.id, COMPANY_ID, self._full_supplementary())
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_preview_hides_other_companies_candidates(self):
        self._seed_policy()
        candidate = self._create_candidate()
        with unittest.mock.patch.object(ProductCandidateService, "get_visible_for_company", side_effect=NotFoundException("x")):
            with self.assertRaises(NotFoundException):
                self.service.preview_candidate(candidate.id, 2, self._full_supplementary())

    def test_the_evaluation_record_is_unchanged_by_the_refactor(self):
        self._seed_policy()
        candidate = self._create_candidate()
        evaluation, scores, replayed = self.service.evaluate_candidate(candidate.id, COMPANY_ID, "same", self._full_supplementary())
        again, _, replayed_again = self.service.evaluate_candidate(candidate.id, COMPANY_ID, "same", self._full_supplementary())
        self.assertEqual((replayed, replayed_again, again.id), (False, True, evaluation.id))
        self.assertEqual(len(scores), 12)
        self.assertEqual(json.loads(evaluation.input_snapshot_json)["min_margin_rate"], "0.18")
        self.assertEqual(self.counts(), (1, 12, 1))


class RouterWiringTests(unittest.TestCase):

    def test_the_preview_route_is_an_admin_read_that_calls_the_preview(self):
        start = ROUTER.index('@router.post("/preview")')
        block = ROUTER[start:ROUTER.index("@router.get(", start)]
        self.assertIn("Depends(admin_guard)", block)
        self.assertIn("preview_candidate(", block)
        self.assertNotIn("evaluate_candidate(", block)


class ScreenTests(unittest.TestCase):

    def section(self):
        return JS[JS.index("// ---- 평가하기 (2026-10-31"):JS.index("// ---- /평가하기 ----")]

    def test_the_analysis_screen_has_an_evaluate_tab(self):
        self.assertIn('["EVAL", "easy.an.tab_eval", ""]', JS)
        self.assertIn('} else if (tab === "EVAL") {\n      renderEval(host, cands);', JS.replace("\r\n", "\n"))

    def test_recording_needs_the_same_inputs_as_the_preview_then_a_confirmation(self):
        code = self.section()
        record = code[code.index("async function evalRecord"):]
        self.assertLess(record.index("!== evalState.previewKey"), record.index("ezDialog("))
        self.assertLess(record.index("ezDialog("), record.index('api("/decisions/evaluate"'))
        self.assertIn("if (!ok) return;", record)
        self.assertIn("idempotency_key: evalState.recordKey", record)
        self.assertIn('B.navigateTo("decision-detail"', record)
        # 입력을 바꾸면 미리 본 결과를 지운다
        self.assertIn("evalState.preview = null;", code[code.index("async function evalLoad"):code.index("async function evalPreview")])

    def test_the_preview_only_reads(self):
        code = self.section()
        preview = code[code.index("async function evalPreview"):code.index("async function evalRecord")]
        self.assertIn('api("/decisions/preview"', preview)
        self.assertNotIn("/decisions/evaluate", preview)

    def test_server_text_is_escaped(self):
        code = self.section()
        for needle in ("esc(b.note", "esc(p.reason", "esc(text)", "esc(c.product_name)", "esc(message)"):
            self.assertIn(needle, code)

    @unittest.skipUnless(shutil.which("node"), "node 없음")
    def test_the_form_sends_percent_as_a_rate_and_leaves_blanks_out(self):
        code = self.section()
        fields = code[code.index("const EVAL_FIELDS"):code.index("const evalState")]
        reader = code[code.index("function evalReadForm"):code.index("function evalFormHtml")]
        script = fields + reader + """
const values = JSON.parse(process.argv[1]);
const root = { querySelector: (sel) => { const f = sel.match(/data-ez-eval="([a-z_]+)"/)[1]; return f in values ? { value: values[f] } : null; } };
const T = (k, p) => k + (p ? ":" + p.name : "");
console.log(JSON.stringify(evalReadForm(root)));
"""
        def run(values):
            out = subprocess.run(["node", "-e", script, json.dumps(values)], capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            return json.loads(out.stdout)

        ok = run({"expected_margin_rate": "18.24", "supply_stability_score": "80", "brand_ip_risk_score": " ", "policy_prohibited_score": "0"})
        self.assertEqual(ok, {"inputs": {"expected_margin_rate": "0.1824", "policy_prohibited_score": "0", "supply_stability_score": "80"}, "problem": ""})
        self.assertEqual(run({"expected_margin_rate": "25"})["inputs"], {"expected_margin_rate": "0.2500"})
        for bad in ({"expected_margin_rate": "100"}, {"expected_margin_rate": "-1"}, {"supply_stability_score": "101"},
                    {"return_claim_risk_score": "abc"}, {"price_competitiveness_score": "-0.5"}):
            self.assertNotEqual(run(bad)["problem"], "", bad)


if __name__ == "__main__":
    unittest.main()
