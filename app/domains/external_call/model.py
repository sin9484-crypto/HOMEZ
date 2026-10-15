"""
=========================================================
Homez OS

File : app/domains/external_call/model.py

2026-10-16 — 바깥 호출 허용(`external_call_allowances`)과 호출 기록(`external_call_logs`) — 추가형 테이블 2개.
허용 한 행이 "요청(PENDING) → 허용 중(ACTIVE) → 소진/만료/취소" 전 과정을 담는다. 비밀값·고객 개인정보는 저장하지 않는다
(`target`은 주소 경로의 종류만, 120자 제한).
=========================================================
"""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class ExternalCallAllowance(Base):

    __tablename__ = "external_call_allowances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    system: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    requested_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remaining_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_hour_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    requested_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )


class ExternalCallLog(Base):

    __tablename__ = "external_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    system: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allowance_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


__all__ = ["ExternalCallAllowance", "ExternalCallLog"]
