"""
=========================================================
Homez OS

File : app/domains/recall_notice/model.py

2026-09-15 전면 감사 후속(Phase 9I/9J, HOMEZ_USER_OPERATION_SETTINGS.md
10-17/10-18 — "리콜·판매중지 여부를 매일 확인하고, 확인된 문제
상품은 신규 등록·가격 확대·가상재고 증가·자동발주를 동시에 막는다").

**RecallNotice는 회사와 무관한 전역 데이터다**(제조사/정부 발표는
특정 회사 소유가 아니다) — company_id가 없다. 반대로
RecallProductBlock(실제 차단 상태)은 회사별로 완전히 격리된다(같은
공고를 근거로 삼아도 회사마다 별도 행 — 한 회사의 해제가 다른
회사에 영향을 주지 않는다).

아직 공식 리콜·판매중지 데이터 소스가 선정되지 않았으므로(공통
규칙 15) `app/domains/recall_notice/provider.py`에는 Protocol
계약과 Fake Provider만 있다 — 실제 네트워크 Provider는 이번 Phase에
만들지 않는다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class RecallNotice(Base):
    """확인된 리콜/판매중지 공고 1건(append-only — 같은 공고를 다시
    수집해도 dedupe_key가 같으면 새 행을 만들지 않는다)."""

    __tablename__ = "recall_notices"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_recall_notices_dedupe_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    product_identifier: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True,
    )
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    announcement_date: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False)

    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class RecallCheckRun(Base):
    """매일 확인 Job 1회 실행 기록 — 실행/중복/부분실패/재시작 검증에
    쓴다(Fake Provider 기반, 실제 Provider 없음)."""

    __tablename__ = "recall_check_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    notices_found_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    new_notices_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )


class RecallCheckJobState(Base):
    """리콜 확인 Job 자동화 모드 — append-only, 최신 행이 현재 값
    (다른 도메인의 설정 패턴과 동일). 기본값은 서비스 계층이 "행이
    없으면 PAUSED"로 해석한다(Model 자체는 기본 행을 만들지 않는다 —
    "설정한 적 없음"과 "명시적으로 PAUSED로 설정함"을 구분하지 않아도
    되는 단순 on/off라 굳이 구분하지 않는다)."""

    __tablename__ = "recall_check_job_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    set_by: Mapped[int] = mapped_column(Integer, nullable=False)
    set_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class RecallProductBlock(Base):
    """회사별로 격리된 "확인된 문제 상품" 차단 상태(현재 상태 1행 —
    PENDING 개념 없이 곧바로 BLOCKED로 시작, UNBLOCKED로 전이). 같은
    회사+상품에 다시 리콜 공고가 확인되면 재차단(re-block)으로 새
    행을 추가한다 — 과거 해제 이력이 자동 면제를 주지 않는다(Phase
    9F의 VirtualStockZeroProposal과 동일한 설계 원칙)."""

    __tablename__ = "recall_product_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    product_identifier: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True,
    )
    recall_notice_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("recall_notices.id"), nullable=True,
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    unblock_requested_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    unblock_justification: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    unblock_approved_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    unblock_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )


__all__ = [
    "RecallNotice",
    "RecallCheckRun",
    "RecallCheckJobState",
    "RecallProductBlock",
]
