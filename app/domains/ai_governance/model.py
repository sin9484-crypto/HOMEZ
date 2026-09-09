"""
=========================================================
Homez OS

File : app/domains/ai_governance/model.py

AG-4(2026-08-21 CTO 후속 지시) — ProposedAction. AI가 실행이 필요한
제안(가격 변경안, 발주안, 재고 보충안 등)을 만들 때 쓰는 공통 계약을
DB로 영속화한다. append-only(이 행 자체가 감사 기록) — 상태가
바뀔 때마다 새 행을 추가하지 않고, 이 행 자체를 조건부 UPDATE로
전이시킨다(idempotency_key 유일 인덱스 + 상태 머신 WHERE절로 동시
전이 시 정확히 하나만 성공하게 한다 — 이 코드베이스의 다른 승인
모델과 동일한 원칙).

AI는 status=DRAFT/REVIEW_REQUIRED로만 이 행을 만들 수 있다(서비스
레벨에서 강제 — 이 모델 자체는 어떤 상태 문자열도 그대로 저장할 수
있지만, `ProposedActionService.create()`가 그 두 상태만 받는다).
APPROVED/EXECUTED로의 전이는 사람의 명시적 액션만 수행한다.
=========================================================
"""

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class ProposedAction(Base):
    """
    이 행 자체가 "AI가 이런 실행을 하자고 제안했다"는 감사 기록이자
    승인 대상이다. 실제 실행(Domain Service 호출)은 이 행과 별개로
    일어난다 — `executed_at`/`executed_reference`는 그 실행이 실제로
    일어났음을 사후에 기록하는 참고용 필드일 뿐, 이 모델이 실행을
    수행하지 않는다.
    """

    __tablename__ = "ai_proposed_actions"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_ai_proposed_actions_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    capability_code: Mapped[str] = mapped_column(
        String(60), nullable=False, index=True,
    )

    # 예: "PRICE_CHANGE_REQUEST", "PURCHASE_ORDER_DRAFT" — 자유 문자열이
    # 아니라 각 capability가 스스로 정의하는 값(이 모델은 강제하지
    # 않는다 — 강제는 호출부 Service의 책임).
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)

    # 예: "product_candidate:123", "marketplace_listing:45" — 어떤
    # 엔티티에 대한 제안인지 논리 참조로 남긴다(FK 없음, 이 코드베이스
    # 전역 컨벤션).
    target_entity: Mapped[str] = mapped_column(String(150), nullable=False)

    # 구조화된 값만(완성 문장 금지 — 이 코드베이스 전역 원칙).
    proposed_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )

    # LOW / MEDIUM / HIGH / CRITICAL — ai_governance/constants.py에
    # 고정 열거형을 두지 않는다(위험도 산정 기준은 capability마다
    # 다를 수 있어 강제하지 않음 — Service 레벨에서 검증).
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)

    approval_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )

    # 제안 생성 시점의 입력 fingerprint — 이 코드베이스 전역 fingerprint
    # 패턴(SHA-256 + canonical_json)과 동일. 승인 시점에 다시 계산해
    # 값이 달라졌으면(대상 데이터 변경) 승인을 거부한다.
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # DRAFT / REVIEW_REQUIRED / APPROVED / REJECTED / EXPIRED /
    # EXECUTED / INVALIDATED (ProposedActionStatus).
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
    )

    # 논리 참조(users.id) — AI가 만든 제안이면 NULL(사람이 아님).
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 논리 참조(users.id) — 승인/거절한 사람. AI는 절대 이 값을 채울
    # 수 없다(Service가 항상 current_user에서만 채운다).
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 실행 결과로 생긴 실제 엔티티(예: "price_change_request:99") —
    # 참고용 감사 필드, 이 모델이 그 엔티티를 만들지 않는다.
    executed_reference: Mapped[str | None] = mapped_column(
        String(150), nullable=True,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = ["ProposedAction"]
