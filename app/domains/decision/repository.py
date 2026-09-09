"""
=========================================================
Homez OS

File : app/domains/decision/repository.py

쓰기 메서드는 commit하지 않고 flush만 수행한다(*_no_commit) —
Transaction 경계는 DecisionService가 소유한다(app/domains/coupang,
product_candidate와 동일한 패턴).

2026-08-14 테넌트 격리 감사(Gate R13) — DecisionEvaluation/Score/
Review/AuditLog의 모든 조회·조건부 UPDATE에 company_id 필터를
추가했다. company_id 없이 id만으로 접근하는 옛 메서드는 삭제했다.
DecisionPolicy는 전역이라 예외다(코드 근거는 model.py 참고, CTO 확인
대기 항목으로 별도 보고).
=========================================================
"""

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.decision.model import CompanyDecisionPolicy
from app.domains.decision.model import DecisionAuditLog
from app.domains.decision.model import DecisionEvaluation
from app.domains.decision.model import DecisionPolicy
from app.domains.decision.model import DecisionReview
from app.domains.decision.model import DecisionScore


class DecisionRepository:

    def __init__(self, db: Session):

        self.db = db

    # --------------------------------------------------
    # DecisionPolicy (전역 — company_id 없음)
    # --------------------------------------------------

    def add_policy_no_commit(self, policy: DecisionPolicy) -> DecisionPolicy:

        self.db.add(policy)
        self.db.flush()

        return policy

    def get_policy(self, policy_id: int) -> DecisionPolicy | None:

        return (
            self.db.query(DecisionPolicy)
            .filter(DecisionPolicy.id == policy_id)
            .first()
        )

    def get_policy_by_business_id(
        self, policy_set_id: str,
    ) -> DecisionPolicy | None:

        return (
            self.db.query(DecisionPolicy)
            .filter(DecisionPolicy.policy_set_id == policy_set_id)
            .first()
        )

    def list_active_policies(self) -> list[DecisionPolicy]:

        return (
            self.db.query(DecisionPolicy)
            .filter(DecisionPolicy.is_active.is_(True))
            .all()
        )

    def list_policies(self) -> list[DecisionPolicy]:

        return (
            self.db.query(DecisionPolicy)
            .order_by(DecisionPolicy.id.desc())
            .all()
        )

    # --------------------------------------------------
    # CompanyDecisionPolicy (회사 스코프, 2026-08-15 V7 Gate 2)
    # --------------------------------------------------

    def add_company_policy_no_commit(
        self, policy: CompanyDecisionPolicy,
    ) -> CompanyDecisionPolicy:

        self.db.add(policy)
        self.db.flush()

        return policy

    def get_company_policy(
        self, policy_id: int, company_id: int,
    ) -> CompanyDecisionPolicy | None:

        return (
            self.db.query(CompanyDecisionPolicy)
            .filter(CompanyDecisionPolicy.id == policy_id)
            .filter(CompanyDecisionPolicy.company_id == company_id)
            .first()
        )

    def get_company_policy_by_business_id(
        self, company_id: int, policy_set_id: str,
    ) -> CompanyDecisionPolicy | None:

        return (
            self.db.query(CompanyDecisionPolicy)
            .filter(CompanyDecisionPolicy.company_id == company_id)
            .filter(CompanyDecisionPolicy.policy_set_id == policy_set_id)
            .first()
        )

    def list_active_company_policies(
        self, company_id: int,
    ) -> list[CompanyDecisionPolicy]:

        return (
            self.db.query(CompanyDecisionPolicy)
            .filter(CompanyDecisionPolicy.company_id == company_id)
            .filter(CompanyDecisionPolicy.is_active.is_(True))
            .all()
        )

    def list_company_policies(
        self, company_id: int,
    ) -> list[CompanyDecisionPolicy]:

        return (
            self.db.query(CompanyDecisionPolicy)
            .filter(CompanyDecisionPolicy.company_id == company_id)
            .order_by(CompanyDecisionPolicy.id.desc())
            .all()
        )

    # --------------------------------------------------
    # DecisionEvaluation (회사 스코프)
    # --------------------------------------------------

    def add_evaluation_no_commit(
        self, evaluation: DecisionEvaluation,
    ) -> DecisionEvaluation:

        self.db.add(evaluation)
        self.db.flush()

        return evaluation

    def get_evaluation_for_company(
        self, evaluation_id: int, company_id: int,
    ) -> DecisionEvaluation | None:

        return (
            self.db.query(DecisionEvaluation)
            .filter(DecisionEvaluation.id == evaluation_id)
            .filter(DecisionEvaluation.company_id == company_id)
            .first()
        )

    def get_evaluation_by_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> DecisionEvaluation | None:

        return (
            self.db.query(DecisionEvaluation)
            .filter(DecisionEvaluation.company_id == company_id)
            .filter(DecisionEvaluation.idempotency_key == idempotency_key)
            .first()
        )

    def get_latest_evaluation_for_candidate(
        self, candidate_id: int, company_id: int,
    ) -> DecisionEvaluation | None:

        return (
            self.db.query(DecisionEvaluation)
            .filter(DecisionEvaluation.candidate_id == candidate_id)
            .filter(DecisionEvaluation.company_id == company_id)
            .order_by(DecisionEvaluation.id.desc())
            .first()
        )

    def list_pending_review_for_company(
        self, company_id: int, skip: int = 0, limit: int = 100,
    ) -> list[DecisionEvaluation]:

        return (
            self.db.query(DecisionEvaluation)
            .filter(DecisionEvaluation.company_id == company_id)
            .filter(DecisionEvaluation.status == "PENDING_REVIEW")
            .order_by(DecisionEvaluation.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def list_evaluations_for_company(
        self, company_id: int, skip: int = 0, limit: int = 100,
    ) -> list[DecisionEvaluation]:

        return (
            self.db.query(DecisionEvaluation)
            .filter(DecisionEvaluation.company_id == company_id)
            .order_by(DecisionEvaluation.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def update_status_conditional(
        self,
        evaluation_id: int,
        company_id: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
    ) -> int:

        stmt = (
            update(DecisionEvaluation)
            .where(DecisionEvaluation.id == evaluation_id)
            .where(DecisionEvaluation.company_id == company_id)
            .where(DecisionEvaluation.status.in_(expected_statuses))
            .values(status=new_status)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # DecisionScore (회사 스코프, 부모에서 비정규화)
    # --------------------------------------------------

    def add_score_no_commit(self, score: DecisionScore) -> DecisionScore:

        self.db.add(score)
        self.db.flush()

        return score

    def list_scores_for_company(
        self, evaluation_id: int, company_id: int,
    ) -> list[DecisionScore]:

        return (
            self.db.query(DecisionScore)
            .filter(DecisionScore.evaluation_id == evaluation_id)
            .filter(DecisionScore.company_id == company_id)
            .order_by(DecisionScore.id.asc())
            .all()
        )

    # --------------------------------------------------
    # DecisionReview (회사 스코프)
    # --------------------------------------------------

    def add_review_no_commit(self, review: DecisionReview) -> DecisionReview:

        self.db.add(review)
        self.db.flush()

        return review

    def get_review_by_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> DecisionReview | None:

        return (
            self.db.query(DecisionReview)
            .filter(DecisionReview.company_id == company_id)
            .filter(DecisionReview.idempotency_key == idempotency_key)
            .first()
        )

    def list_reviews_for_company(
        self, evaluation_id: int, company_id: int,
    ) -> list[DecisionReview]:

        return (
            self.db.query(DecisionReview)
            .filter(DecisionReview.evaluation_id == evaluation_id)
            .filter(DecisionReview.company_id == company_id)
            .order_by(DecisionReview.id.asc())
            .all()
        )

    # --------------------------------------------------
    # DecisionAuditLog (회사 스코프)
    # --------------------------------------------------

    def add_audit_log_no_commit(
        self, entry: DecisionAuditLog,
    ) -> DecisionAuditLog:

        self.db.add(entry)
        self.db.flush()

        return entry

    def list_audit_log_for_candidate_and_company(
        self, candidate_id: int, company_id: int,
    ) -> list[DecisionAuditLog]:

        return (
            self.db.query(DecisionAuditLog)
            .filter(DecisionAuditLog.candidate_id == candidate_id)
            .filter(DecisionAuditLog.company_id == company_id)
            .order_by(DecisionAuditLog.id.asc())
            .all()
        )

    def list_audit_log_for_evaluation(
        self, evaluation_id: int, company_id: int,
    ) -> list[DecisionAuditLog]:

        return (
            self.db.query(DecisionAuditLog)
            .filter(DecisionAuditLog.evaluation_id == evaluation_id)
            .filter(DecisionAuditLog.company_id == company_id)
            .order_by(DecisionAuditLog.id.asc())
            .all()
        )


__all__ = [
    "DecisionRepository",
]
