"""
=========================================================
Homez OS

File : app/domains/ai_learning/service.py

2026-09-10 Phase 12(HOMEZ_USER_OPERATION_SETTINGS.md 12번) — 실제
결과 기록, 학습 데이터셋 export, 표본 크기 판정, 후보 모델 검증
상태기계.

**절대 하지 않는 것**: 실제 모델 학습·자동 적용. `approve_model_
candidate()`가 이 상태기계의 마지막 전이지만, 그 이후 "실제로
라이브 정책에 반영"하는 코드는 이 세션 어디에도 없다 — 그 연결은
이 Phase 범위 밖이다(정직하게 기록, docs/HOMEZ_PROJECT_STATE.md
Phase 12 절 참고).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.ai_learning.constants import INITIAL_MIN_SAMPLE_SIZE
from app.domains.ai_learning.constants import RAISE_THRESHOLD_TOTAL_ORDERS
from app.domains.ai_learning.constants import RAISED_MIN_SAMPLE_SIZE
from app.domains.ai_learning.constants import ModelCandidateStatus
from app.domains.ai_learning.model import DecisionOutcome
from app.domains.ai_learning.model import LearningDatasetRecord
from app.domains.ai_learning.model import ModelCandidate
from app.domains.ai_learning.model import ModelCandidateStatusEvent
from app.domains.ai_learning.repository import AiLearningRepository


class AiLearningService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = AiLearningRepository(db)

    # ------------------------------
    # 실제 결과 기록
    # ------------------------------

    def record_outcome(
        self,
        *,
        company_id: int,
        evaluation_id: int,
        order_id: int | None = None,
        actual_sales_amount: float | None = None,
        actual_margin_amount: float | None = None,
        stockout: bool = False,
        cancelled: bool = False,
        returned: bool = False,
        shipping_delayed: bool = False,
        recorded_by: int | None = None,
    ) -> DecisionOutcome:

        if self.repository.get_outcome_by_evaluation(evaluation_id):
            raise BadRequestException(
                f"이미 결과가 기록된 판단입니다(evaluation_id="
                f"{evaluation_id}) — 결과는 한 번만 기록합니다.",
            )

        outcome = DecisionOutcome(
            company_id=company_id, evaluation_id=evaluation_id,
            order_id=order_id, actual_sales_amount=actual_sales_amount,
            actual_margin_amount=actual_margin_amount, stockout=stockout,
            cancelled=cancelled, returned=returned,
            shipping_delayed=shipping_delayed, recorded_by=recorded_by,
        )

        return self.repository.create_outcome(outcome)

    # ------------------------------
    # 학습 데이터셋 export — 운영 테이블에서 복사, 참조 아님.
    # ------------------------------

    def export_to_learning_dataset(
        self, company_id: int, evaluation_id: int,
    ) -> LearningDatasetRecord:
        """
        `DecisionEvaluation`(app/domains/decision/model.py, 이 함수는
        읽기만 한다 — 절대 수정하지 않는다) + 그 평가에 대한 가장
        최근 `DecisionReview`(있으면) + `DecisionOutcome`(있어야
        한다 — 결과 없이는 export하지 않는다, "정답 없는 학습 행"을
        만들지 않기 위함)을 한 번에 복사해 독립된 행을 만든다.
        """

        from app.domains.decision.model import DecisionEvaluation
        from app.domains.decision.model import DecisionReview

        if self.repository.record_exists_for_evaluation(evaluation_id):
            raise BadRequestException(
                f"이미 export된 평가입니다(evaluation_id="
                f"{evaluation_id}).",
            )

        evaluation = (
            self.db.query(DecisionEvaluation)
            .filter(
                DecisionEvaluation.id == evaluation_id,
                DecisionEvaluation.company_id == company_id,
            )
            .first()
        )
        if evaluation is None:
            raise NotFoundException("해당 AI 판단을 찾을 수 없습니다.")

        outcome = self.repository.get_outcome_by_evaluation(evaluation_id)
        if outcome is None:
            raise BadRequestException(
                "실제 결과가 아직 기록되지 않은 판단은 학습 데이터셋"
                "으로 내보낼 수 없습니다 — record_outcome()을 먼저 "
                "호출하세요.",
            )

        latest_review = (
            self.db.query(DecisionReview)
            .filter(DecisionReview.evaluation_id == evaluation_id)
            .order_by(DecisionReview.id.desc())
            .first()
        )

        record = LearningDatasetRecord(
            company_id=company_id, evaluation_id=evaluation_id,
            input_snapshot_json=evaluation.input_snapshot_json,
            policy_version=evaluation.policy_version,
            recommendation=evaluation.recommendation,
            recommendation_reason=evaluation.recommendation_reason,
            user_decision=(latest_review.action if latest_review else None),
            actual_sales_amount=outcome.actual_sales_amount,
            actual_margin_amount=outcome.actual_margin_amount,
            stockout=outcome.stockout, cancelled=outcome.cancelled,
            returned=outcome.returned,
            shipping_delayed=outcome.shipping_delayed,
        )

        return self.repository.create_dataset_record(record)

    def get_dataset_size(self, company_id: int) -> int:

        return self.repository.count_dataset_records(company_id)

    # ------------------------------
    # 표본 크기 판정
    # ------------------------------

    def get_required_sample_size(self, total_completed_orders: int) -> int:
        """문서 원문: "초기 학습 검토는 최소 50건의 완료 주문부터
        시작... 누적 주문이 1,000건을 넘으면 최소 100건으로
        상향"."""

        if total_completed_orders >= RAISE_THRESHOLD_TOTAL_ORDERS:
            return RAISED_MIN_SAMPLE_SIZE

        return INITIAL_MIN_SAMPLE_SIZE

    def check_learning_readiness(
        self, company_id: int, total_completed_orders: int,
    ) -> tuple[bool, str | None]:

        required = self.get_required_sample_size(total_completed_orders)
        available = self.get_dataset_size(company_id)

        if available < required:
            return False, (
                f"학습 후보 데이터가 부족합니다({available}건/필요 "
                f"{required}건)."
            )

        return True, None

    # ------------------------------
    # 후보 모델 검증 상태기계 — 실제 적용은 하지 않는다.
    # ------------------------------

    def create_model_candidate(
        self,
        *,
        company_id: int,
        user_id: int,
        is_admin: bool,
        name: str,
        version: str,
    ) -> ModelCandidate:

        if not is_admin:
            raise ForbiddenException(
                "후보 모델 생성은 관리자만 가능합니다.",
            )

        if not name or not name.strip():
            raise BadRequestException("후보 모델 이름이 필요합니다.")
        if not version or not version.strip():
            raise BadRequestException("버전 식별자가 필요합니다.")

        candidate = ModelCandidate(
            company_id=company_id, name=name.strip(),
            version=version.strip(), status=ModelCandidateStatus.DRAFT,
            created_by=user_id,
        )

        return self.repository.create_candidate(candidate)

    def _transition(
        self,
        *,
        candidate_id: int,
        company_id: int,
        user_id: int,
        is_admin: bool,
        from_status: str,
        to_status: str,
        extra_values: dict | None = None,
        forbidden_message: str,
        invalid_transition_message: str,
    ) -> ModelCandidate:

        if not is_admin:
            raise ForbiddenException(forbidden_message)

        candidate = self.repository.get_candidate(candidate_id, company_id)
        if candidate is None:
            raise NotFoundException("해당 후보 모델을 찾을 수 없습니다.")

        rowcount = self.repository.transition_status_conditional(
            candidate_id, company_id, from_status, to_status,
            extra_values=extra_values,
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                f"{invalid_transition_message} — 현재 상태: "
                f"{candidate.status}",
            )

        self.repository.add_status_event_no_commit(
            ModelCandidateStatusEvent(
                company_id=company_id, model_candidate_id=candidate_id,
                previous_status=from_status, new_status=to_status,
                triggered_by=user_id,
            ),
        )
        self.db.commit()
        self.db.refresh(candidate)

        return candidate

    def mark_offline_evaluated(
        self,
        *,
        candidate_id: int,
        company_id: int,
        user_id: int,
        is_admin: bool,
        summary: str,
        sample_size_used: int,
    ) -> ModelCandidate:

        if not summary or not summary.strip():
            raise BadRequestException("오프라인 평가 요약이 필요합니다.")
        if sample_size_used <= 0:
            raise BadRequestException(
                "사용한 표본 크기는 0보다 커야 합니다.",
            )

        return self._transition(
            candidate_id=candidate_id, company_id=company_id,
            user_id=user_id, is_admin=is_admin,
            from_status=ModelCandidateStatus.DRAFT,
            to_status=ModelCandidateStatus.OFFLINE_EVALUATED,
            extra_values={
                "offline_eval_summary": summary.strip(),
                "sample_size_used": sample_size_used,
            },
            forbidden_message="오프라인 평가 기록은 관리자만 가능합니다.",
            invalid_transition_message=(
                "초안 상태가 아닌 후보는 오프라인 평가를 기록할 수 "
                "없습니다"
            ),
        )

    def mark_regression_compared(
        self,
        *,
        candidate_id: int,
        company_id: int,
        user_id: int,
        is_admin: bool,
        summary: str,
    ) -> ModelCandidate:

        if not summary or not summary.strip():
            raise BadRequestException("회귀 비교 요약이 필요합니다.")

        return self._transition(
            candidate_id=candidate_id, company_id=company_id,
            user_id=user_id, is_admin=is_admin,
            from_status=ModelCandidateStatus.OFFLINE_EVALUATED,
            to_status=ModelCandidateStatus.REGRESSION_COMPARED,
            extra_values={"regression_comparison_summary": summary.strip()},
            forbidden_message="회귀 비교 기록은 관리자만 가능합니다.",
            invalid_transition_message=(
                "오프라인 평가를 마치지 않은 후보는 회귀 비교를 "
                "기록할 수 없습니다"
            ),
        )

    def approve_model_candidate(
        self,
        *,
        candidate_id: int,
        company_id: int,
        user_id: int,
        is_admin: bool,
    ) -> ModelCandidate:
        """
        이 전이가 상태기계의 끝이다 — "사람이 승인했다"는 사실만
        기록할 뿐, 실제로 이 후보를 라이브 정책에 적용하는 코드는
        어디에도 없다(모듈 docstring 참고).
        """

        return self._transition(
            candidate_id=candidate_id, company_id=company_id,
            user_id=user_id, is_admin=is_admin,
            from_status=ModelCandidateStatus.REGRESSION_COMPARED,
            to_status=ModelCandidateStatus.APPROVED,
            extra_values={
                "approved_by": user_id,
                "approved_at": datetime.utcnow(),
            },
            forbidden_message="후보 모델 승인은 관리자만 가능합니다.",
            invalid_transition_message=(
                "회귀 비교를 마치지 않은 후보는 승인할 수 없습니다"
            ),
        )

    def reject_model_candidate(
        self,
        *,
        candidate_id: int,
        company_id: int,
        user_id: int,
        is_admin: bool,
        reason: str,
    ) -> ModelCandidate:

        if not is_admin:
            raise ForbiddenException("후보 모델 반려는 관리자만 가능합니다.")

        candidate = self.repository.get_candidate(candidate_id, company_id)
        if candidate is None:
            raise NotFoundException("해당 후보 모델을 찾을 수 없습니다.")

        if candidate.status in (
            ModelCandidateStatus.APPROVED, ModelCandidateStatus.REJECTED,
        ):
            raise BadRequestException(
                f"이미 종료된 후보는 반려할 수 없습니다 — 현재 상태: "
                f"{candidate.status}",
            )

        rowcount = self.repository.transition_status_conditional(
            candidate_id, company_id, candidate.status,
            ModelCandidateStatus.REJECTED,
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                f"반려할 수 없습니다 — 현재 상태: {candidate.status}",
            )

        self.repository.add_status_event_no_commit(
            ModelCandidateStatusEvent(
                company_id=company_id, model_candidate_id=candidate_id,
                previous_status=candidate.status,
                new_status=ModelCandidateStatus.REJECTED, reason=reason,
                triggered_by=user_id,
            ),
        )
        self.db.commit()
        self.db.refresh(candidate)

        return candidate

    def list_candidates(
        self, company_id: int, *, status: str | None = None,
    ) -> list[ModelCandidate]:

        return self.repository.list_candidates(company_id, status=status)


__all__ = ["AiLearningService"]
