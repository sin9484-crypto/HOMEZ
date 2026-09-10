"""
=========================================================
Homez OS

File : tests/test_ai_learning_domain.py

2026-09-10 Phase 12(HOMEZ_USER_OPERATION_SETTINGS.md 12번) — 실제
결과 기록, 학습 데이터셋 export(운영 테이블에서 복사, 참조 아님),
표본 크기 판정(50/1,000/100), 후보 모델 검증 상태기계 검증.
=========================================================
"""

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.database.bootstrap import bootstrap_environment
from app.domains.ai_learning.constants import INITIAL_MIN_SAMPLE_SIZE
from app.domains.ai_learning.constants import RAISE_THRESHOLD_TOTAL_ORDERS
from app.domains.ai_learning.constants import RAISED_MIN_SAMPLE_SIZE
from app.domains.ai_learning.constants import ModelCandidateStatus
from app.domains.ai_learning.service import AiLearningService
from app.domains.company.model import Company
from app.domains.decision.model import DecisionEvaluation
from app.domains.decision.model import DecisionReview
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def _make_evaluation(db, *, company_id, candidate_id=1, idempotency_key):

    evaluation = DecisionEvaluation(
        company_id=company_id, candidate_id=candidate_id,
        policy_source="GLOBAL", policy_version="v1",
        input_snapshot_json='{"price": 10000}',
        input_fingerprint="fp-" + idempotency_key,
        total_score=Decimal("80.0000"), confidence=Decimal("0.9000"),
        recommendation="APPROVE_CANDIDATE",
        recommendation_reason="마진·신뢰도 기준 충족",
        idempotency_key=idempotency_key,
        evaluator_kind="fixture", evaluator_version="v1",
    )
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)
    return evaluation


class AiLearningDomainTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)
        self.assertFalse(result.migration_approval_required)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="AI학습테스트 회사", business_number="151-51-51515",
            ceo="테스트", phone="02-000-0000",
            email="ai-learning@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="ailearnadmin",
            email="ailearnadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.service = AiLearningService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    # ------------------------------
    # 실제 결과 기록
    # ------------------------------

    def test_record_outcome_creates_row(self):

        evaluation = _make_evaluation(
            self.db, company_id=self.company.id, idempotency_key="e1",
        )

        outcome = self.service.record_outcome(
            company_id=self.company.id, evaluation_id=evaluation.id,
            actual_sales_amount=30000.0, actual_margin_amount=5000.0,
            recorded_by=self.admin.id,
        )

        self.assertEqual(outcome.actual_sales_amount, 30000.0)
        self.assertFalse(outcome.stockout)

    def test_record_outcome_rejects_duplicate(self):

        evaluation = _make_evaluation(
            self.db, company_id=self.company.id, idempotency_key="e2",
        )
        self.service.record_outcome(
            company_id=self.company.id, evaluation_id=evaluation.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.record_outcome(
                company_id=self.company.id, evaluation_id=evaluation.id,
            )

    # ------------------------------
    # 학습 데이터셋 export — 복사, 참조 아님
    # ------------------------------

    def test_export_requires_outcome_first(self):

        evaluation = _make_evaluation(
            self.db, company_id=self.company.id, idempotency_key="e3",
        )

        with self.assertRaises(BadRequestException):
            self.service.export_to_learning_dataset(
                self.company.id, evaluation.id,
            )

    def test_export_copies_fields_from_evaluation_and_outcome(self):

        evaluation = _make_evaluation(
            self.db, company_id=self.company.id, idempotency_key="e4",
        )
        self.service.record_outcome(
            company_id=self.company.id, evaluation_id=evaluation.id,
            actual_sales_amount=25000.0, actual_margin_amount=4000.0,
            cancelled=True,
        )

        record = self.service.export_to_learning_dataset(
            self.company.id, evaluation.id,
        )

        self.assertEqual(record.policy_version, "v1")
        self.assertEqual(record.recommendation, "APPROVE_CANDIDATE")
        self.assertEqual(record.actual_sales_amount, 25000.0)
        self.assertTrue(record.cancelled)

    def test_export_includes_latest_user_decision(self):

        evaluation = _make_evaluation(
            self.db, company_id=self.company.id, idempotency_key="e5",
        )
        self.db.add(DecisionReview(
            company_id=self.company.id,
            evaluation_id=evaluation.id, action="APPROVE",
            reviewer_id=self.admin.id, idempotency_key="review-e5",
        ))
        self.db.commit()
        self.service.record_outcome(
            company_id=self.company.id, evaluation_id=evaluation.id,
        )

        record = self.service.export_to_learning_dataset(
            self.company.id, evaluation.id,
        )

        self.assertEqual(record.user_decision, "APPROVE")

    def test_export_does_not_modify_operational_evaluation_row(self):
        """복사이지 참조가 아니다 — export 이후 DecisionEvaluation을
        바꿔도 이미 export된 학습 행은 영향받지 않아야 한다."""

        evaluation = _make_evaluation(
            self.db, company_id=self.company.id, idempotency_key="e6",
        )
        self.service.record_outcome(
            company_id=self.company.id, evaluation_id=evaluation.id,
        )
        record = self.service.export_to_learning_dataset(
            self.company.id, evaluation.id,
        )

        evaluation.recommendation_reason = "나중에 바뀐 사유"
        self.db.commit()

        self.db.refresh(record)
        self.assertEqual(record.recommendation_reason, "마진·신뢰도 기준 충족")

    def test_export_rejects_duplicate(self):

        evaluation = _make_evaluation(
            self.db, company_id=self.company.id, idempotency_key="e7",
        )
        self.service.record_outcome(
            company_id=self.company.id, evaluation_id=evaluation.id,
        )
        self.service.export_to_learning_dataset(
            self.company.id, evaluation.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.export_to_learning_dataset(
                self.company.id, evaluation.id,
            )

    def test_export_nonexistent_evaluation_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.export_to_learning_dataset(self.company.id, 999999)

    # ------------------------------
    # 표본 크기 판정
    # ------------------------------

    def test_required_sample_size_defaults_to_fifty(self):

        self.assertEqual(
            self.service.get_required_sample_size(0), INITIAL_MIN_SAMPLE_SIZE,
        )
        self.assertEqual(INITIAL_MIN_SAMPLE_SIZE, 50)

    def test_required_sample_size_at_exactly_threshold_stays_fifty(self):

        self.assertEqual(
            self.service.get_required_sample_size(
                RAISE_THRESHOLD_TOTAL_ORDERS,
            ),
            INITIAL_MIN_SAMPLE_SIZE,
        )

    def test_required_sample_size_raises_beyond_threshold(self):

        self.assertEqual(
            self.service.get_required_sample_size(
                RAISE_THRESHOLD_TOTAL_ORDERS + 1,
            ),
            RAISED_MIN_SAMPLE_SIZE,
        )
        self.assertEqual(RAISED_MIN_SAMPLE_SIZE, 100)

    def test_readiness_false_when_dataset_too_small(self):

        ready, reason = self.service.check_learning_readiness(
            self.company.id, total_completed_orders=10,
        )
        self.assertFalse(ready)
        self.assertIsNotNone(reason)

    def test_readiness_true_when_dataset_meets_requirement(self):

        for i in range(INITIAL_MIN_SAMPLE_SIZE):
            evaluation = _make_evaluation(
                self.db, company_id=self.company.id,
                idempotency_key=f"bulk-{i}",
            )
            self.service.record_outcome(
                company_id=self.company.id, evaluation_id=evaluation.id,
            )
            self.service.export_to_learning_dataset(
                self.company.id, evaluation.id,
            )

        ready, reason = self.service.check_learning_readiness(
            self.company.id, total_completed_orders=10,
        )
        self.assertTrue(ready)
        self.assertIsNone(reason)

    # ------------------------------
    # 후보 모델 상태기계 — "학습됐다"고 주장하지 않는다
    # ------------------------------

    def test_new_candidate_starts_as_draft(self):

        candidate = self.service.create_model_candidate(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, name="가격추천 모델", version="2026-09-10-a",
        )
        self.assertEqual(candidate.status, ModelCandidateStatus.DRAFT)

    def test_full_lifecycle_reaches_approved(self):

        candidate = self.service.create_model_candidate(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, name="가격추천 모델", version="2026-09-10-b",
        )
        candidate = self.service.mark_offline_evaluated(
            candidate_id=candidate.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
            summary="오프라인 평가 결과 양호", sample_size_used=60,
        )
        self.assertEqual(
            candidate.status, ModelCandidateStatus.OFFLINE_EVALUATED,
        )

        candidate = self.service.mark_regression_compared(
            candidate_id=candidate.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
            summary="기존 정책 대비 순이익 개선 확인",
        )
        self.assertEqual(
            candidate.status, ModelCandidateStatus.REGRESSION_COMPARED,
        )

        candidate = self.service.approve_model_candidate(
            candidate_id=candidate.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )
        self.assertEqual(candidate.status, ModelCandidateStatus.APPROVED)
        self.assertEqual(candidate.approved_by, self.admin.id)
        self.assertIsNotNone(candidate.approved_at)

    def test_cannot_skip_offline_evaluation_step(self):

        candidate = self.service.create_model_candidate(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, name="모델", version="v1",
        )

        with self.assertRaises(BadRequestException):
            self.service.mark_regression_compared(
                candidate_id=candidate.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True, summary="건너뛰기 시도",
            )

    def test_cannot_approve_without_regression_comparison(self):

        candidate = self.service.create_model_candidate(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, name="모델", version="v1",
        )
        self.service.mark_offline_evaluated(
            candidate_id=candidate.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True, summary="평가",
            sample_size_used=60,
        )

        with self.assertRaises(BadRequestException):
            self.service.approve_model_candidate(
                candidate_id=candidate.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

    def test_reject_terminates_lifecycle(self):

        candidate = self.service.create_model_candidate(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, name="모델", version="v1",
        )
        rejected = self.service.reject_model_candidate(
            candidate_id=candidate.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True, reason="표본 부족",
        )
        self.assertEqual(rejected.status, ModelCandidateStatus.REJECTED)

        with self.assertRaises(BadRequestException):
            self.service.mark_offline_evaluated(
                candidate_id=candidate.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True, summary="너무 늦음",
                sample_size_used=60,
            )

    def test_status_transitions_require_admin(self):

        candidate = self.service.create_model_candidate(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, name="모델", version="v1",
        )

        with self.assertRaises(ForbiddenException):
            self.service.mark_offline_evaluated(
                candidate_id=candidate.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=False, summary="x",
                sample_size_used=1,
            )

    def test_status_events_are_recorded(self):

        candidate = self.service.create_model_candidate(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, name="모델", version="v1",
        )
        self.service.mark_offline_evaluated(
            candidate_id=candidate.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True, summary="x",
            sample_size_used=60,
        )

        events = self.service.repository.list_status_events(candidate.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].new_status, ModelCandidateStatus.OFFLINE_EVALUATED,
        )

    def test_candidates_are_isolated_per_company(self):

        other_company = Company(
            name="다른회사AI학습", business_number="161-61-61616",
            ceo="테스트", phone="02-000-0000",
            email="other-ai@example.com", address="테스트",
        )
        self.db.add(other_company)
        self.db.commit()

        self.service.create_model_candidate(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, name="모델", version="v1",
        )

        self.assertEqual(
            len(self.service.list_candidates(other_company.id)), 0,
        )


if __name__ == "__main__":
    unittest.main()
