"""
=========================================================
Homez OS

File : app/domains/restore/model.py

Gate Y-2(2026-08-12) — 복원 시도 이력 테이블(append-only, backup_records
와 동일한 감사 철학). 복원은 검증 실패 시 실제로 실행되지 않으므로
FAILED 행도 "시도했으나 막힘"의 증거로 남긴다(성공만 기록하는
backup_records와 다른 점 — 복원은 실패 자체가 중요한 안전 신호다).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

RESTORE_STATUS_SUCCEEDED = "succeeded"
RESTORE_STATUS_FAILED = "failed"


class RestoreAttempt(Base):

    __tablename__ = "restore_attempts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    source_backup_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    target_db_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # 복원 직전 target_db_path 자체를 안전하게 한 번 더 백업해둔
    # 경로(존재했을 경우만) — 복원이 잘못됐을 때 되돌릴 수 있는
    # 유일한 수단이므로 반드시 기록한다.
    pre_restore_backup_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    integrity_check_result: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    file_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    # 이 코드베이스는 FK를 쓰지 않는다(논리 참조만 유지).
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:

        return (
            f"<RestoreAttempt(id={self.id}, status={self.status})>"
        )


__all__ = [
    "RestoreAttempt",
    "RESTORE_STATUS_SUCCEEDED",
    "RESTORE_STATUS_FAILED",
]
