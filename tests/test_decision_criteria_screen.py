"""
=========================================================
Homez OS

File : tests/test_decision_criteria_screen.py

2026-10-30 — AI 통합 계획 1단계: 판매 판단 AI '평가 기준' 화면(보기·D1 초안·확정)을 고정한다.

  - 조회는 읽기만 한다: 지금 쓰는 기준(회사 → 전역 → 없음), D1 초안, 바꿀 수 없는 규칙, 이 회사의 확정 기록.
  - 확정은 비밀번호 재확인(5분·1회용 토큰) 뒤에만 회사 정책을 VERIFIED로 만든다. 이름·상태·유효기간은 서버가 정한다.
  - 화면에서 본 '지금 쓰는 기준'이 그사이 바뀌었으면 만들지 않는다. 잘못된 비중(음수·합계≠1·빠진 항목)은 거절한다.
  - 정책 행과 감사 기록은 한 트랜잭션이다(감사 기록을 못 쓰면 정책도 남지 않는다).
  - 확정한 정책으로 실제 평가가 돈다(policy_source=COMPANY). 다른 회사에는 보이지 않는다.
격리 SQLite(실제 homez.db 사용 안 함), 외부 호출 없음.
=========================================================
"""

import json
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import UnauthorizedException
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.domains.decision import router as decision_router
from app.domains.decision.constants import CONFIRMED_CRITERIA_VALID_DAYS
from app.domains.decision.constants import D1_DRAFT_AXIS_WEIGHTS
from app.domains.decision.constants import DecisionPolicyStatus
from app.domains.decision.constants import PolicySource
from app.domains.decision.constants import ScoreAxis
from app.domains.decision.model import CompanyDecisionPolicy
from app.domains.decision.schema import DecisionCompanyPolicyCreateRequest
from app.domains.decision.schema import DecisionCriteriaConfirmRequest
from app.domains.decision.service import REQUIRED_FOR_APPROVE
from tests.test_decision_ai import COMPANY_ID
import tests.test_decision_ai as decision_base  # 클래스를 직접 가져오면 그 시험들이 여기서 다시 돈다

ROOT = Path(__file__).resolve().parents[1]
ROUTER = (ROOT / "app/domains/decision/router.py").read_text(encoding="utf-8")
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
USER_ID = 7


def draft_request(expected=None, **changes):
    weights = dict(D1_DRAFT_AXIS_WEIGHTS)
    weights.update(changes.pop("weights", {}))
    return DecisionCriteriaConfirmRequest(
        axis_weights=weights,
        min_approve_total_score=changes.pop("score", "70"),
        min_confidence_for_recommendation=changes.pop("confidence", "0.60"),
        expected_in_use_id=expected,
    )


class CriteriaBase(unittest.TestCase):
    # 기존 시험 클래스의 준비 함수만 빌린다(상속하면 그 시험들까지 다시 돈다).
    _base_setup = decision_base.DecisionAITestCase.setUp
    tearDown = decision_base.DecisionAITestCase.tearDown
    _create_safety_tables = decision_base.DecisionAITestCase._create_safety_tables
    _seed_policy = decision_base.DecisionAITestCase._seed_policy
    _create_candidate = decision_base.DecisionAITestCase._create_candidate
    _full_supplementary = decision_base.DecisionAITestCase._full_supplementary

    def setUp(self):
        self._base_setup()
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
                "description VARCHAR(500), ip_address VARCHAR(50))",
            )

    def audit_rows(self):
        return self.db.execute(text("SELECT company_id, user_id, action, entity, entity_id FROM audit_logs")).fetchall()

    def policy_count(self):
        return self.db.query(CompanyDecisionPolicy).count()


class OverviewTests(CriteriaBase):

    def test_with_no_policy_nothing_is_in_use_and_the_d1_draft_is_offered(self):
        view = self.service.criteria_overview(COMPANY_ID)
        self.assertIsNone(view["in_use"])
        self.assertEqual(view["history"], [])
        self.assertEqual(view["draft"]["axis_weights"], D1_DRAFT_AXIS_WEIGHTS)
        self.assertEqual(sum(Decimal(v) for v in view["draft"]["axis_weights"].values()), Decimal("1"))
        self.assertEqual((view["draft"]["min_approve_total_score"], view["draft"]["min_confidence_for_recommendation"]), ("70", "0.60"))
        self.assertEqual(view["valid_days"], CONFIRMED_CRITERIA_VALID_DAYS)
        self.assertEqual(self.policy_count(), 0, "보기만 해서는 정책이 생기지 않는다")

    def test_the_fixed_rules_are_the_evaluator_values(self):
        rules = self.service.criteria_overview(COMPANY_ID)["fixed_rules"]
        self.assertEqual(rules["min_sufficient_axes"], 6)
        self.assertEqual(Decimal(rules["min_weight_coverage"]), Decimal("0.70"))
        self.assertEqual(Decimal(rules["hard_block_threshold"]), Decimal("40"))
        self.assertEqual(rules["hard_block_axes"], [ScoreAxis.POLICY_PROHIBITED_RISK, ScoreAxis.BRAND_IP_RISK])
        self.assertEqual(rules["required_for_approve"], list(REQUIRED_FOR_APPROVE))
        self.assertEqual(Decimal(rules["min_margin_rate"]), Decimal("0.18"))

    def test_a_usable_global_policy_is_shown_as_the_default(self):
        policy = self._seed_policy()
        in_use = self.service.criteria_overview(COMPANY_ID)["in_use"]
        self.assertEqual((in_use["source"], in_use["id"], in_use["usable"]), (PolicySource.GLOBAL, policy.id, True))

    def test_draft_or_expired_policies_are_not_shown_as_in_use(self):
        self._seed_policy(status=DecisionPolicyStatus.DRAFT)
        self.assertIsNone(self.service.criteria_overview(COMPANY_ID)["in_use"])


class ConfirmTests(CriteriaBase):

    def test_confirming_the_draft_creates_a_verified_company_policy_with_an_audit_row(self):
        before = datetime.utcnow()
        brief = self.service.confirm_criteria(draft_request(), COMPANY_ID, USER_ID)
        policy = self.db.query(CompanyDecisionPolicy).one()
        self.assertEqual((policy.company_id, policy.status, policy.is_complete, policy.is_active), (COMPANY_ID, "VERIFIED", True, True))
        self.assertEqual(policy.policy_version, "D1-20261029")
        self.assertIn("user_id=7", policy.source_reference)
        self.assertEqual({k: Decimal(v) for k, v in json.loads(policy.axis_weights_json).items()},
                         {k: Decimal(v) for k, v in D1_DRAFT_AXIS_WEIGHTS.items()})
        self.assertEqual((Decimal(policy.min_approve_total_score), Decimal(policy.min_confidence_for_recommendation)), (Decimal("70"), Decimal("0.6")))
        self.assertGreaterEqual(policy.effective_at, before)
        self.assertEqual(policy.expires_at - policy.effective_at, timedelta(days=CONFIRMED_CRITERIA_VALID_DAYS))
        self.assertTrue(brief["usable"])
        self.assertEqual(self.audit_rows(), [(COMPANY_ID, USER_ID, "DECISION_CRITERIA_CONFIRMED", "company_decision_policy", str(policy.id))])

        view = self.service.criteria_overview(COMPANY_ID)
        self.assertEqual((view["in_use"]["source"], view["in_use"]["id"]), (PolicySource.COMPANY, policy.id))
        self.assertEqual([(h["id"], h["in_use"]) for h in view["history"]], [(policy.id, True)])

    def test_edited_values_are_marked_as_edited(self):
        self.service.confirm_criteria(
            draft_request(weights={ScoreAxis.MARGIN_AND_FEES: "0.24", ScoreAxis.REVENUE_POTENTIAL: "0.00"}, score="75"),
            COMPANY_ID, USER_ID,
        )
        policy = self.db.query(CompanyDecisionPolicy).one()
        self.assertEqual(policy.policy_version, "D1-20261029-EDITED")
        self.assertIn("고침", policy.source_reference)
        self.assertEqual(Decimal(policy.min_approve_total_score), Decimal("75"))

    def test_a_threshold_change_alone_also_counts_as_edited(self):
        self.service.confirm_criteria(draft_request(confidence="0.65"), COMPANY_ID, USER_ID)
        self.assertEqual(self.db.query(CompanyDecisionPolicy).one().policy_version, "D1-20261029-EDITED")

    def test_a_newer_confirmation_replaces_the_one_in_use(self):
        first = self.service.confirm_criteria(draft_request(), COMPANY_ID, USER_ID)
        second = self.service.confirm_criteria(draft_request(expected=first["id"], score="72"), COMPANY_ID, USER_ID)
        view = self.service.criteria_overview(COMPANY_ID)
        self.assertEqual(view["in_use"]["id"], second["id"])
        self.assertEqual([(h["id"], h["in_use"]) for h in view["history"]], [(second["id"], True), (first["id"], False)])

    def test_a_stale_screen_is_refused_and_nothing_is_written(self):
        first = self.service.confirm_criteria(draft_request(), COMPANY_ID, USER_ID)
        for stale in (None, first["id"] + 99):
            with self.assertRaises(ConflictException):
                self.service.confirm_criteria(draft_request(expected=stale), COMPANY_ID, USER_ID)
        self.assertEqual(self.policy_count(), 1)
        self.assertEqual(len(self.audit_rows()), 1)

    def test_bad_values_are_refused_and_nothing_is_written(self):
        bad_requests = [
            draft_request(weights={ScoreAxis.MARGIN_AND_FEES: "0.40", ScoreAxis.REVENUE_POTENTIAL: "-0.16"}),  # 합계는 1이지만 음수
            draft_request(weights={ScoreAxis.MARGIN_AND_FEES: "0.30"}),  # 합계 1.10
            draft_request(score="0"), draft_request(score="100.01"), draft_request(score="-5"),
            draft_request(confidence="0"), draft_request(confidence="1.5"),
        ]
        missing = dict(D1_DRAFT_AXIS_WEIGHTS)
        missing.pop(ScoreAxis.DATA_RELIABILITY)
        missing[ScoreAxis.FUNDING_LIMIT_IMPACT] = "0.04"
        bad_requests.append(DecisionCriteriaConfirmRequest(
            axis_weights=missing, min_approve_total_score="70", min_confidence_for_recommendation="0.60",
        ))
        for request in bad_requests:
            with self.assertRaises(BadRequestException, msg=request):
                self.service.confirm_criteria(request, COMPANY_ID, USER_ID)
        self.assertEqual(self.policy_count(), 0)
        self.assertEqual(self.audit_rows(), [])

    def test_non_finite_numbers_are_refused_before_reaching_the_service(self):
        for value in ("NaN", "Infinity"):
            with self.assertRaises(ValidationError):
                draft_request(weights={ScoreAxis.MARGIN_AND_FEES: value})
            with self.assertRaises(ValidationError):
                draft_request(score=value)

    def test_the_boundaries_are_accepted(self):
        brief = self.service.confirm_criteria(draft_request(score="100", confidence="1"), COMPANY_ID, USER_ID)
        self.assertTrue(brief["usable"])

    def test_negative_weights_are_also_refused_on_the_existing_policy_api(self):
        weights = dict(D1_DRAFT_AXIS_WEIGHTS)
        weights.update({ScoreAxis.MARGIN_AND_FEES: "1.20", ScoreAxis.REVENUE_POTENTIAL: "-0.36"})
        with self.assertRaises(BadRequestException):
            self.service.create_company_policy(DecisionCompanyPolicyCreateRequest(
                policy_set_id="neg", policy_version="v1", source_reference="t", status="VERIFIED", is_complete=True,
                axis_weights=weights, checked_at=datetime.utcnow(), effective_at=datetime.utcnow(),
            ), COMPANY_ID)
        self.assertEqual(self.policy_count(), 0)

    def test_if_the_audit_row_cannot_be_written_no_policy_is_left(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE audit_logs")
        with self.assertRaises(Exception):
            self.service.confirm_criteria(draft_request(), COMPANY_ID, USER_ID)
        self.db.rollback()
        self.assertEqual(self.policy_count(), 0)

    def test_another_company_does_not_see_or_use_the_policy(self):
        self.service.confirm_criteria(draft_request(), COMPANY_ID, USER_ID)
        other = self.service.criteria_overview(COMPANY_ID + 1)
        self.assertIsNone(other["in_use"])
        self.assertEqual(other["history"], [])
        # 다른 회사는 자기 화면 기준(없음)으로 확정할 수 있다 — 1번 회사 정책과 섞이지 않는다.
        self.service.confirm_criteria(draft_request(), COMPANY_ID + 1, USER_ID + 1)
        self.assertEqual(len(self.service.criteria_overview(COMPANY_ID)["history"]), 1)

    def test_an_expired_confirmation_stops_being_in_use(self):
        brief = self.service.confirm_criteria(draft_request(), COMPANY_ID, USER_ID)
        policy = self.db.get(CompanyDecisionPolicy, brief["id"])
        policy.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()
        view = self.service.criteria_overview(COMPANY_ID)
        self.assertIsNone(view["in_use"])
        self.assertEqual([(h["in_use"], h["usable"]) for h in view["history"]], [(False, False)])


class EvaluationUsesTheConfirmedCriteriaTests(CriteriaBase):

    def setUp(self):
        super().setUp()
        self._create_safety_tables()

    def test_evaluation_is_blocked_before_and_uses_the_company_policy_after(self):
        candidate = self._create_candidate()
        with self.assertRaises(BadRequestException):
            self.service.evaluate_candidate(candidate.id, COMPANY_ID, "crit-before", self._full_supplementary())
        brief = self.service.confirm_criteria(draft_request(), COMPANY_ID, USER_ID)
        evaluation, _scores, _replayed = self.service.evaluate_candidate(candidate.id, COMPANY_ID, "crit-after", self._full_supplementary())
        self.assertEqual((evaluation.policy_source, evaluation.company_policy_id), (PolicySource.COMPANY, brief["id"]))


class RouterTests(CriteriaBase):

    def setUp(self):
        super().setUp()
        reset_recent_auth_state_for_tests()
        self.user = SimpleNamespace(id=USER_ID, company_id=COMPANY_ID)

    def tearDown(self):
        reset_recent_auth_state_for_tests()
        super().tearDown()

    def confirm(self, token):
        return decision_router.confirm_criteria(
            draft_request(), current_user=self.user, db=self.db, _desktop=None, recent_auth_token=token,
        )

    def test_confirming_needs_a_fresh_password_check_and_the_token_is_single_use(self):
        for token in (None, "", "not-a-token"):
            with self.assertRaises(UnauthorizedException):
                self.confirm(token)
        self.assertEqual(self.policy_count(), 0)
        token, _ = issue_recent_auth_token(USER_ID)
        self.assertTrue(self.confirm(token)["usable"])
        with self.assertRaises(UnauthorizedException):
            self.confirm(token)
        self.assertEqual(self.policy_count(), 1)

    def test_another_users_token_is_refused(self):
        token, _ = issue_recent_auth_token(USER_ID + 1)
        with self.assertRaises(UnauthorizedException):
            self.confirm(token)
        self.assertEqual(self.policy_count(), 0)

    def test_reading_the_criteria_writes_nothing(self):
        view = decision_router.get_criteria(current_user=self.user, db=self.db)
        self.assertIsNone(view["in_use"])
        self.assertEqual((self.policy_count(), self.audit_rows()), (0, []))

    def test_the_confirm_endpoint_is_desktop_only_and_checks_the_password_before_the_service(self):
        body = ROUTER.split('@company_policy_router.post("/criteria/confirm")')[1].split("__all__")[0]
        self.assertIn("Depends(require_desktop_token)", body)
        self.assertIn('alias="X-Recent-Auth-Token"', body)
        self.assertLess(body.index("consume_recent_auth_token("), body.index(".confirm_criteria("))
        self.assertIn("current_user.company_id", body)


class ScreenTests(unittest.TestCase):

    def section(self, start, end):
        return JS.split(start)[1].split(end)[0]

    def test_the_analysis_screen_has_a_criteria_tab(self):
        body = self.section("async function loadAnalysis(", "\n  // =====")
        self.assertIn('["CRIT", "easy.an.tab_crit"', body)
        self.assertIn('tab === "CRIT"', body)
        self.assertIn("renderCriteria(host)", body)

    def test_opening_the_tab_only_reads(self):
        body = self.section("async function renderCriteria(", "async function confirmCriteria(")
        self.assertIn('api("/decision-company-policies/criteria")', body)
        self.assertNotIn("method:", body)

    def test_confirming_asks_then_checks_the_password_with_a_summary_then_sends(self):
        body = self.section("async function confirmCriteria(", "// ---- /평가 기준 ----")
        self.assertLess(body.index("critProblem("), body.index("await ezDialog("))
        self.assertLess(body.index("await ezDialog("), body.index("B.promptRecentAuthToken({ summary })"))
        self.assertLess(body.index("B.promptRecentAuthToken({ summary })"), body.index('"/decision-company-policies/criteria/confirm"'))
        self.assertIn('"X-Recent-Auth-Token": token', body)
        self.assertIn("if (token === null) return;", body)
        self.assertIn("expected_in_use_id:", body)
        self.assertIn("critState.busy", body)

    def test_every_axis_has_a_label_and_the_screen_covers_all_twelve(self):
        axes = set(ScoreAxis.ALL)
        listed = set(self.section("const CRIT_AXES = [", "];").split('"')[1::2]) & axes
        self.assertEqual(listed, axes)

    def test_server_text_is_escaped(self):
        body = self.section("function critHtml(", "async function renderCriteria(")
        for raw in ("${p.policy_version}", "${use.policy_version}", "${d.valid_days}"):
            self.assertNotIn(raw, body)


if __name__ == "__main__":
    unittest.main()
