"""
=========================================================
Homez OS

File : app/domains/ai_learning/repository.py

2026-09-10 Phase 12 — DecisionOutcome/LearningDatasetRecord/
ModelCandidate/ModelCandidateStatusEvent 저장소 계층.
=========================================================
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.ai_learning.model import DecisionOutcome
from app.domains.ai_learning.model import LearningDatasetRecord
from app.domains.ai_learning.model import ModelCandidate
from app.domains.ai_learning.model import ModelCandidateStatusEvent


class AiLearningRepository:

    def __init__(self, db: Session):

        self.db = db

    # ------------------------------
    # DecisionOutcome
    # ------------------------------

    def create_outcome(self, outcome: DecisionOutcome) -> DecisionOutcome:

        self.db.add(outcome)
        self.db.commit()
        self.db.refresh(outcome)

        return outcome

    def get_outcome_by_evaluation(
        self, evaluation_id: int,
    ) -> DecisionOutcome | None:

        return (
            self.db.query(DecisionOutcome)
            .filter(DecisionOutcome.evaluation_id == evaluation_id)
            .first()
        )

    # ------------------------------
    # LearningDatasetRecord
    # ------------------------------

    def record_exists_for_evaluation(self, evaluation_id: int) -> bool:

        return (
            self.db.query(LearningDatasetRecord)
            .filter(LearningDatasetRecord.evaluation_id == evaluation_id)
            .first()
            is not None
        )

    def create_dataset_record(
        self, record: LearningDatasetRecord,
    ) -> LearningDatasetRecord:

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        return record

    def count_dataset_records(self, company_id: int) -> int:

        return (
            self.db.query(LearningDatasetRecord)
            .filter(LearningDatasetRecord.company_id == company_id)
            .count()
        )

    def list_dataset_records(
        self, company_id: int, *, limit: int = 200,
    ) -> list[LearningDatasetRecord]:

        return (
            self.db.query(LearningDatasetRecord)
            .filter(LearningDatasetRecord.company_id == company_id)
            .order_by(LearningDatasetRecord.id.desc())
            .limit(limit)
            .all()
        )

    # ------------------------------
    # ModelCandidate
    # ------------------------------

    def create_candidate(self, candidate: ModelCandidate) -> ModelCandidate:

        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)

        return candidate

    def get_candidate(
        self, candidate_id: int, company_id: int,
    ) -> ModelCandidate | None:

        return (
            self.db.query(ModelCandidate)
            .filter(
                ModelCandidate.id == candidate_id,
                ModelCandidate.company_id == company_id,
            )
            .first()
        )

    def list_candidates(
        self, company_id: int, *, status: str | None = None,
    ) -> list[ModelCandidate]:

        query = self.db.query(ModelCandidate).filter(
            ModelCandidate.company_id == company_id,
        )
        if status is not None:
            query = query.filter(ModelCandidate.status == status)

        return query.order_by(ModelCandidate.created_at.desc()).all()

    def transition_status_conditional(
        self,
        candidate_id: int,
        company_id: int,
        from_status: str,
        to_status: str,
        *,
        extra_values: dict | None = None,
    ) -> int:

        values: dict = {"status": to_status}
        if extra_values:
            values.update(extra_values)

        stmt = (
            update(ModelCandidate)
            .where(ModelCandidate.id == candidate_id)
            .where(ModelCandidate.company_id == company_id)
            .where(ModelCandidate.status == from_status)
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def add_status_event_no_commit(
        self, event: ModelCandidateStatusEvent,
    ) -> ModelCandidateStatusEvent:

        self.db.add(event)

        return event

    def list_status_events(
        self, model_candidate_id: int,
    ) -> list[ModelCandidateStatusEvent]:

        return (
            self.db.query(ModelCandidateStatusEvent)
            .filter(
                ModelCandidateStatusEvent.model_candidate_id
                == model_candidate_id,
            )
            .order_by(ModelCandidateStatusEvent.id.asc())
            .all()
        )


__all__ = ["AiLearningRepository"]
