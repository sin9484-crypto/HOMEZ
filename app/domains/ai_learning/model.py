"""
=========================================================
Homez OS

File : app/domains/ai_learning/model.py

2026-09-10 Phase 12 — 3개 테이블:

1. `DecisionOutcome` — AI 판단(`app/domains/decision/model.py::
   DecisionEvaluation`) 1건에 대한 실제 결과(판매·마진·품절·취소·
   반품·배송지연)를 나중에(주문 완료 시점에) 연결한다. `decision`
   도메인 테이블은 전혀 수정하지 않는다 — evaluation_id는 논리
   참조(FK 없음)다.

2. `LearningDatasetRecord` — "학습 가능한 데이터셋을 운영 DB와
   분리한다"의 구현. `DecisionEvaluation`+`DecisionReview`+
   `DecisionOutcome`에서 값을 복사해 독립된 행으로 저장한다(참조가
   아니라 복사 — 운영 테이블이 나중에 바뀌어도 이미 export된 학습
   행은 그 시점 스냅샷 그대로 남는다). `export_to_learning_dataset()`
   (service.py)가 채운다.

3. `ModelCandidate`(+`ModelCandidateStatusEvent`) — 후보 모델/정책
   버전의 검증 상태기계. `app/domains/refund/model.py`와 동일한
   "현재상태+append-only 이력" 패턴.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base
from app.domains.ai_learning.constants import ModelCandidateStatus


class DecisionOutcome(Base):
    """AI 판단 1건(evaluation_id)당 최대 1행 — 실제 결과가 확정되면
    (주문 완료/취소/반품 등) 그때 기록한다. 판단 시점에는 존재하지
    않는 게 정상이다(아직 결과를 모르므로)."""

    __tablename__ = "decision_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_id", name="uq_decision_outcomes_evaluation_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (decision_evaluations.id — app/domains/decision/model.py)
    evaluation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (orders.id); 판단이 특정 주문과 연결되지 않을 수도
    # 있어(예: 상품 발굴 단계 판단) nullable.
    order_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    actual_sales_amount: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    actual_margin_amount: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    stockout: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    cancelled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    returned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    shipping_delayed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # 논리 참조 (users.id); 시스템 자동 기록은 None.
    recorded_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class LearningDatasetRecord(Base):
    """
    운영 테이블(DecisionEvaluation/DecisionReview/DecisionOutcome)의
    특정 시점 스냅샷을 복사해 저장한다 — 참조가 아니다. 이 테이블은
    "완료된"(즉 outcome이 기록된) 판단만 담아, 실제로 라벨(정답)이
    있는 학습 후보 데이터셋 역할을 한다.
    """

    __tablename__ = "learning_dataset_records"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_id", name="uq_learning_dataset_records_evaluation_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (decision_evaluations.id) — 중복 export 방지용 식별자.
    evaluation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    input_snapshot_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    policy_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    recommendation: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    recommendation_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    user_decision: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    actual_sales_amount: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    actual_margin_amount: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    stockout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    returned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shipping_delayed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    exported_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class ModelCandidate(Base):
    """후보 모델/정책 버전의 검증 상태기계 — 실제 학습·적용 코드는
    이 세션 범위 밖이다(모듈 docstring 참고)."""

    __tablename__ = "model_candidates"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=ModelCandidateStatus.DRAFT,
        index=True,
    )

    sample_size_used: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    offline_eval_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    regression_comparison_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # 논리 참조 (users.id)
    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 논리 참조 (users.id)
    approved_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ModelCandidateStatusEvent(Base):
    """append-only 후보 모델 상태 이력."""

    __tablename__ = "model_candidate_status_events"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (model_candidates.id)
    model_candidate_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    previous_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    new_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # 논리 참조 (users.id)
    triggered_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "DecisionOutcome",
    "LearningDatasetRecord",
    "ModelCandidate",
    "ModelCandidateStatusEvent",
]
