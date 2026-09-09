"""
=========================================================
Homez OS

File : tests/test_decision_ai.py

HOMEZ V4 Decision AI 검증: 결정적 평가, 입력 부족 차단, 정책 위반
비상쇄, Emergency Stop 최우선, 멱등성, 동시성, rollback, 권한, override
감사 완전성, Decimal 경계.

실제 homez.db는 사용하지 않는다.
=========================================================
"""

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.decision.constants import DecisionPolicyStatus
from app.domains.decision.constants import EvaluationRecommendation
from app.domains.decision.constants import ReviewAction
from app.domains.decision.constants import ScoreAxis
from app.domains.decision.model import CompanyDecisionPolicy
from app.domains.decision.model import DecisionAuditLog
from app.domains.decision.model import DecisionEvaluation
from app.domains.decision.model import DecisionPolicy
from app.domains.decision.model import DecisionReview
from app.domains.decision.model import DecisionScore
from app.domains.decision.repository import DecisionRepository
from app.domains.decision.schema import DecisionPolicyCreateRequest
from app.domains.decision.schema import DecisionSupplementaryInputs
from app.domains.decision.service import DecisionService
from app.domains.decision.service import evaluate_axes
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate

FULL_WEIGHTS = {
    ScoreAxis.REVENUE_POTENTIAL: Decimal("0.12"),
    ScoreAxis.MARGIN_AND_FEES: Decimal("0.15"),
    ScoreAxis.PRICE_COMPETITIVENESS: Decimal("0.08"),
    ScoreAxis.DEMAND_TREND_STRENGTH: Decimal("0.12"),
    ScoreAxis.COMPETITION_INTENSITY: Decimal("0.08"),
    ScoreAxis.SUPPLY_STABILITY: Decimal("0.08"),
    ScoreAxis.INVENTORY_SHIPPING_RISK: Decimal("0.07"),
    ScoreAxis.RETURN_CLAIM_RISK: Decimal("0.07"),
    ScoreAxis.POLICY_PROHIBITED_RISK: Decimal("0.10"),
    ScoreAxis.BRAND_IP_RISK: Decimal("0.05"),
    ScoreAxis.DATA_RELIABILITY: Decimal("0.03"),
    ScoreAxis.FUNDING_LIMIT_IMPACT: Decimal("0.05"),
}


COMPANY_ID = 1


class DecisionAITestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ProductCandidate.__table__,
                DecisionPolicy.__table__,
                CompanyDecisionPolicy.__table__,
                DecisionEvaluation.__table__,
                DecisionScore.__table__,
                DecisionReview.__table__,
                DecisionAuditLog.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = DecisionService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_safety_tables(self):

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )

    def _seed_policy(
        self,
        weights=None,
        status=DecisionPolicyStatus.VERIFIED,
        is_complete=True,
        min_approve=Decimal("70.0000"),
        min_confidence=Decimal("0.5000"),
        policy_set_id="test-policy-1",
    ) -> DecisionPolicy:

        return self.service.create_policy(
            DecisionPolicyCreateRequest(
                policy_set_id=policy_set_id,
                policy_version="v1",
                source_reference="test-source",
                status=status,
                is_complete=is_complete,
                axis_weights=weights or FULL_WEIGHTS,
                min_approve_total_score=min_approve,
                min_confidence_for_recommendation=min_confidence,
                checked_at=datetime.utcnow() - timedelta(days=1),
                effective_at=datetime.utcnow() - timedelta(days=1),
            ),
        )

    def _create_candidate(
        self,
        ref="CAND-1",
        trend_score=80.0,
        novelty_score=70.0,
        demand_score=85.0,
        competition_score=60.0,
        margin_score=75.0,
        risk_score=10.0,
        confidence=0.9,
    ) -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=f"COUPANG_API:COUPANG:{ref}",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference=ref,
            product_name="테스트 상품",
            status=CandidateStatus.RECOMMENDED,
            trend_score=trend_score,
            novelty_score=novelty_score,
            demand_score=demand_score,
            competition_score=competition_score,
            margin_score=margin_score,
            risk_score=risk_score,
            confidence=confidence,
        )
        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)

        return candidate

    def _full_supplementary(self, **overrides) -> DecisionSupplementaryInputs:

        base = dict(
            expected_margin_rate=Decimal("0.20"),
            gross_revenue=Decimal("50000"),
            price_competitiveness_score=Decimal("80"),
            supply_stability_score=Decimal("85"),
            inventory_shipping_risk_score=Decimal("80"),
            return_claim_risk_score=Decimal("80"),
            brand_ip_risk_score=Decimal("90"),
            required_funding=Decimal("10000"),
            available_funding=Decimal("50000"),
        )
        base.update(overrides)

        return DecisionSupplementaryInputs(**base)

    # --------------------------------------------------
    # 1) 결정적 평가
    # --------------------------------------------------

    def test_evaluate_axes_is_deterministic(self):

        candidate = self._create_candidate()
        supplementary = self._full_supplementary()

        results_a = evaluate_axes(candidate, supplementary)
        results_b = evaluate_axes(candidate, supplementary)

        self.assertEqual(len(results_a), len(results_b))
        for a, b in zip(results_a, results_b):
            self.assertEqual(a.axis, b.axis)
            self.assertEqual(a.raw_score, b.raw_score)
            self.assertEqual(a.confidence, b.confidence)

    def test_full_evaluation_is_deterministic_across_calls(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()
        supplementary = self._full_supplementary()

        eval_a, scores_a, _ = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-det-a", supplementary,
        )
        eval_b, scores_b, _ = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-det-b", supplementary,
        )

        self.assertEqual(eval_a.total_score, eval_b.total_score)
        self.assertEqual(eval_a.confidence, eval_b.confidence)
        self.assertEqual(eval_a.recommendation, eval_b.recommendation)
        self.assertEqual(eval_a.input_fingerprint, eval_b.input_fingerprint)

    # --------------------------------------------------
    # 2) 입력 부족 시 자동 추천 금지
    # --------------------------------------------------

    def test_insufficient_data_blocks_recommendation(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = ProductCandidate(
            candidate_key="COUPANG_API:COUPANG:BARE-1",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference="BARE-1",
            product_name="정보 부족 상품",
            status=CandidateStatus.DISCOVERED,
        )
        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)

        evaluation, scores, _dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-bare", DecisionSupplementaryInputs(),
        )

        self.assertEqual(
            evaluation.recommendation,
            EvaluationRecommendation.INSUFFICIENT_DATA,
        )
        insufficient_axes = [s for s in scores if not s.data_sufficient]
        self.assertGreaterEqual(len(insufficient_axes), 6)

    # --------------------------------------------------
    # 3) 정책 위반이 점수로 상쇄되지 않음
    # --------------------------------------------------

    def test_policy_risk_not_offset_by_high_scores(self):

        self._create_safety_tables()
        self._seed_policy()
        # risk_score=95 → POLICY_PROHIBITED_RISK 축은 100-95=5로 반전되어
        # 임계값(40) 미만 → hard block. 나머지는 전부 만점급으로 설정.
        candidate = self._create_candidate(
            trend_score=95.0, novelty_score=95.0, demand_score=95.0,
            competition_score=95.0, margin_score=95.0, risk_score=95.0,
            confidence=0.95,
        )
        supplementary = self._full_supplementary(
            expected_margin_rate=Decimal("0.30"),
            gross_revenue=Decimal("100000"),
            price_competitiveness_score=Decimal("100"),
            supply_stability_score=Decimal("100"),
            inventory_shipping_risk_score=Decimal("100"),
            return_claim_risk_score=Decimal("100"),
            brand_ip_risk_score=Decimal("100"),
        )

        evaluation, scores, _dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-hardblock", supplementary,
        )

        self.assertEqual(
            evaluation.recommendation,
            EvaluationRecommendation.REVIEW_REQUIRED,
        )
        policy_score = next(
            s for s in scores if s.axis == ScoreAxis.POLICY_PROHIBITED_RISK
        )
        self.assertTrue(policy_score.risk_flag)

    # --------------------------------------------------
    # 4) Emergency Stop 최우선
    # --------------------------------------------------

    def test_emergency_stop_blocks_evaluation(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()

        safety = SafetyService(self.db)
        safety.activate_emergency_stop(
            reason="테스트 긴급 정지", set_by=1, is_admin=True,
        )

        with self.assertRaises(ForbiddenException):
            self.service.evaluate_candidate(
                candidate.id, COMPANY_ID, "idem-estop", self._full_supplementary(),
            )

        # 평가 자체가 생성되지 않아야 한다.
        self.assertIsNone(
            DecisionRepository(self.db).get_evaluation_by_idempotency_key(
                COMPANY_ID,
                "idem-estop",
            ),
        )

        audit_rows = self.service.list_audit_log_for_candidate(candidate.id, COMPANY_ID)
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0].event_type, "EVALUATION_BLOCKED_BY_SAFETY")
        self.assertIsNone(audit_rows[0].evaluation_id)

    def test_missing_safety_schema_fails_closed(self):
        """automation_safety 스키마 자체가 없으면 안전을 확인할 수 없어
        차단한다(과거 Coupang fail-open 재감사와 동일한 철학)."""

        self._seed_policy()
        candidate = self._create_candidate()

        with self.assertRaises(ForbiddenException):
            self.service.evaluate_candidate(
                candidate.id, COMPANY_ID, "idem-noschema", self._full_supplementary(),
            )

    def test_evaluation_succeeds_when_safety_clear(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()

        evaluation, _scores, duplicate = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-clear", self._full_supplementary(),
        )

        self.assertFalse(duplicate)
        self.assertFalse(evaluation.blocked_by_safety)

    def test_evaluate_candidate_blocks_when_product_selection_capability_deactivated(
        self,
    ):
        """Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        실연결 증거. EStop이 클리어하고 정책·데이터가 전부 정상이어도
        capability_code가 비활성이면 평가 자체가 차단돼야 한다."""

        from app.domains.ai_governance.service import InactiveCapabilityError
        from tests.ai_governance_test_helpers import deactivated_capability

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()

        with deactivated_capability("PRODUCT_SELECTION"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.evaluate_candidate(
                    candidate.id, COMPANY_ID, "idem-capability-blocked",
                    self._full_supplementary(),
                )

    # --------------------------------------------------
    # 5) 멱등 재호출
    # --------------------------------------------------

    def test_duplicate_idempotency_key_returns_same_evaluation(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()

        first, first_scores, first_dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-dup", self._full_supplementary(),
        )
        second, second_scores, second_dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-dup", self._full_supplementary(),
        )

        self.assertFalse(first_dup)
        self.assertTrue(second_dup)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(first_scores), len(second_scores))

    # --------------------------------------------------
    # 6) 동시 idempotency 경쟁
    # --------------------------------------------------

    def test_concurrent_evaluate_same_idempotency_key_creates_one_row(self):

        self._create_safety_tables()

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        SessionLocal2 = sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

        setup_db = SessionLocal2()
        setup_service = DecisionService(setup_db)
        setup_service.create_policy(
            DecisionPolicyCreateRequest(
                policy_set_id="race-policy",
                policy_version="v1",
                source_reference="test-source",
                status=DecisionPolicyStatus.VERIFIED,
                is_complete=True,
                axis_weights=FULL_WEIGHTS,
                min_approve_total_score=Decimal("70.0000"),
                min_confidence_for_recommendation=Decimal("0.5000"),
                checked_at=datetime.utcnow() - timedelta(days=1),
                effective_at=datetime.utcnow() - timedelta(days=1),
            ),
        )
        candidate = ProductCandidate(
            candidate_key="COUPANG_API:COUPANG:RACE-1",
            source_type="COUPANG_API", market="COUPANG",
            source_reference="RACE-1", product_name="경쟁 테스트 상품",
            status=CandidateStatus.RECOMMENDED,
            trend_score=80.0, demand_score=80.0, competition_score=60.0,
            margin_score=75.0, risk_score=10.0, confidence=0.9,
        )
        setup_db.add(candidate)
        setup_db.commit()
        candidate_id = candidate.id
        setup_db.close()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = DecisionService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    evaluation, _scores, duplicate = (
                        service.evaluate_candidate(
                            candidate_id, COMPANY_ID, "race-key",
                            self._full_supplementary(),
                        )
                    )
                    results[name] = (
                        "duplicate" if duplicate else "created",
                        evaluation.id,
                    )
                except Exception as e:  # noqa: BLE001
                    results[name] = (f"error:{type(e).__name__}", None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("a",))
            t2 = threading.Thread(target=worker, args=("b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = [v[0] for v in results.values()]
            self.assertNotIn(
                True, [o.startswith("error:") for o in outcomes],
                f"오류 발생: {results}",
            )
            self.assertEqual(outcomes.count("created"), 1, results)
            self.assertEqual(outcomes.count("duplicate"), 1, results)

            eval_ids = {v[1] for v in results.values()}
            self.assertEqual(len(eval_ids), 1)

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(DecisionEvaluation)
                    .filter(
                        DecisionEvaluation.idempotency_key == "race-key",
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()

        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 7) 평가 중간 실패 전체 rollback
    # --------------------------------------------------

    def test_mid_evaluation_failure_rolls_back(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()

        with mock.patch.object(
            DecisionRepository, "add_audit_log_no_commit",
            side_effect=RuntimeError("강제 실패"),
        ):
            with self.assertRaises(RuntimeError):
                self.service.evaluate_candidate(
                    candidate.id, COMPANY_ID, "idem-midfail",
                    self._full_supplementary(),
                )

        self.assertIsNone(
            DecisionRepository(self.db).get_evaluation_by_idempotency_key(
                COMPANY_ID,
                "idem-midfail",
            ),
        )
        self.assertEqual(
            self.db.query(DecisionScore).count(), 0,
            "중간 실패 시 DecisionScore가 남아있으면 안 됩니다.",
        )

    # --------------------------------------------------
    # 8) 조건부 상태 UPDATE rowcount 충돌
    # --------------------------------------------------

    def test_review_requires_pending_review_status(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()

        evaluation, _scores, _dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-review-status", self._full_supplementary(),
        )

        self.service.review_evaluation(
            evaluation.id, COMPANY_ID, ReviewAction.APPROVE, operator_id=1,
            is_admin=True, idempotency_key="review-1",
        )

        with self.assertRaises(BadRequestException):
            self.service.review_evaluation(
                evaluation.id, COMPANY_ID, ReviewAction.REJECT, operator_id=1,
                is_admin=True, idempotency_key="review-2",
            )

    # --------------------------------------------------
    # 9) 동시 승인/거절 중 하나만 성공
    # --------------------------------------------------

    def test_concurrent_approve_and_reject_only_one_succeeds(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        SessionLocal2 = sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()
        evaluation, _scores, _dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-concurrency-review",
            self._full_supplementary(),
        )
        evaluation_id = evaluation.id

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name, action, idempotency_key):

                thread_db = SessionLocal2()
                service = DecisionService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    service.review_evaluation(
                        evaluation_id, COMPANY_ID, action,
                        operator_id=1, is_admin=True,
                        idempotency_key=idempotency_key,
                    )
                    results[name] = "ok"
                except (ConflictException, BadRequestException):
                    results[name] = "rejected"
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(
                target=worker,
                args=("approve", ReviewAction.APPROVE, "review-race-a"),
            )
            t2 = threading.Thread(
                target=worker,
                args=("reject", ReviewAction.REJECT, "review-race-b"),
            )
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = list(results.values())
            self.assertEqual(outcomes.count("ok"), 1, results)
            self.assertEqual(outcomes.count("rejected"), 1, results)

        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 10) 권한 없는 평가/승인/override 차단
    # --------------------------------------------------

    def test_review_without_admin_is_blocked(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()
        evaluation, _scores, _dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-noadmin", self._full_supplementary(),
        )

        with self.assertRaises(ForbiddenException):
            self.service.review_evaluation(
                evaluation.id, COMPANY_ID, ReviewAction.APPROVE, operator_id=1,
                is_admin=False, idempotency_key="noadmin-1",
            )

    def test_override_without_admin_is_blocked(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()
        evaluation, _scores, _dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-noadmin-override", self._full_supplementary(),
        )

        with self.assertRaises(ForbiddenException):
            self.service.review_evaluation(
                evaluation.id, COMPANY_ID, ReviewAction.OVERRIDE, operator_id=1,
                is_admin=False, idempotency_key="noadmin-override-1",
                override_reason="사유", new_value="RECOMMEND_APPROVE",
            )

    # --------------------------------------------------
    # 11) override 감사 로그 완전성
    # --------------------------------------------------

    def test_override_records_full_audit_trail(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()
        evaluation, _scores, _dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-override", self._full_supplementary(),
        )
        original_recommendation = evaluation.recommendation

        updated, _dup2 = self.service.review_evaluation(
            evaluation.id, COMPANY_ID, ReviewAction.OVERRIDE, operator_id=42,
            is_admin=True, idempotency_key="override-1",
            memo="운영자 판단으로 재정의",
            override_reason="시장 상황이 데이터보다 나음",
            new_value=EvaluationRecommendation.RECOMMEND_APPROVE,
        )

        self.assertEqual(
            updated.recommendation, EvaluationRecommendation.RECOMMEND_APPROVE,
        )

        reviews = self.service.list_reviews(evaluation.id, COMPANY_ID)
        self.assertEqual(len(reviews), 1)
        review = reviews[0]
        self.assertEqual(review.action, ReviewAction.OVERRIDE)
        self.assertEqual(review.reviewer_id, 42)
        self.assertEqual(review.previous_value, original_recommendation)
        self.assertEqual(
            review.new_value, EvaluationRecommendation.RECOMMEND_APPROVE,
        )
        self.assertEqual(review.override_reason, "시장 상황이 데이터보다 나음")
        self.assertIsNotNone(review.decided_at)

        audit_rows = self.service.list_audit_log_for_candidate(candidate.id, COMPANY_ID)
        override_events = [
            r for r in audit_rows if r.event_type == "OVERRIDE_APPLIED"
        ]
        self.assertEqual(len(override_events), 1)
        self.assertIn("previous=", override_events[0].payload_summary)
        self.assertIn("new=", override_events[0].payload_summary)

    def test_override_requires_reason_and_new_value(self):

        self._create_safety_tables()
        self._seed_policy()
        candidate = self._create_candidate()
        evaluation, _scores, _dup = self.service.evaluate_candidate(
            candidate.id, COMPANY_ID, "idem-override-missing", self._full_supplementary(),
        )

        with self.assertRaises(BadRequestException):
            self.service.review_evaluation(
                evaluation.id, COMPANY_ID, ReviewAction.OVERRIDE, operator_id=1,
                is_admin=True, idempotency_key="override-missing-1",
            )

    # --------------------------------------------------
    # 12) Decimal 금액/점수 경계
    # --------------------------------------------------

    def test_nan_input_is_rejected(self):
        """
        NaN은 이미 Pydantic 스키마 계층(DecisionSupplementaryInputs)
        에서 finite_number 검증으로 차단된다 — 서비스 계층까지 도달하지
        않는다. 그래도 "NaN이 최종적으로 거부된다"는 요구사항은 동일하게
        만족한다.
        """

        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self._full_supplementary(gross_revenue=Decimal("NaN"))

    def test_negative_funding_is_rejected(self):

        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            # Pydantic 필드 자체의 ge=0 검증에서 이미 막힌다.
            self._full_supplementary(required_funding=Decimal("-1"))

    def test_score_module_does_not_use_float_for_money(self):

        import ast
        import inspect

        import app.domains.decision.service as service_module

        source = inspect.getsource(service_module)
        tree = ast.parse(source)

        float_constants = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, float)
        ]

        self.assertEqual(float_constants, [])

    # --------------------------------------------------
    # 정책 관리
    # --------------------------------------------------

    def test_policy_requires_all_axes_and_weights_sum_to_one(self):

        incomplete_weights = dict(FULL_WEIGHTS)
        incomplete_weights.pop(ScoreAxis.FUNDING_LIMIT_IMPACT)

        with self.assertRaises(BadRequestException):
            self.service.create_policy(
                DecisionPolicyCreateRequest(
                    policy_set_id="bad-policy",
                    policy_version="v1",
                    source_reference="test",
                    status=DecisionPolicyStatus.VERIFIED,
                    is_complete=True,
                    axis_weights=incomplete_weights,
                    checked_at=datetime.utcnow(),
                    effective_at=datetime.utcnow() - timedelta(days=1),
                ),
            )

    def test_no_usable_policy_blocks_evaluation(self):

        self._create_safety_tables()
        candidate = self._create_candidate()

        with self.assertRaises(BadRequestException):
            self.service.evaluate_candidate(
                candidate.id, COMPANY_ID, "idem-nopolicy", self._full_supplementary(),
            )

    def test_ai_never_directly_sets_product_candidate_status(self):
        """
        DecisionService는 ProductCandidate.status를 직접 변경하지
        않는다 — 최종 상태 전환은 사람이 기존 product_candidate API를
        통해서만 한다(Domain 경계 유지). evaluation.status(자체
        PENDING_REVIEW/REVIEWED)는 조건부 UPDATE로만 바뀌므로 예외로
        허용한다.
        """

        import inspect

        source = inspect.getsource(DecisionService)
        self.assertNotIn("candidate.status", source)
        self.assertNotIn("candidate.recommendation", source)


if __name__ == "__main__":
    unittest.main()
