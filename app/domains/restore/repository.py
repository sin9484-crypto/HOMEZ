"""
=========================================================
Homez OS

File : app/domains/restore/repository.py

Gate Y-2(2026-08-12) — 복원 시도 이력 Repository. backup_records와
동일하게 append-only.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository

from app.domains.restore.model import RestoreAttempt


class RestoreRepository(BaseRepository[RestoreAttempt]):

    def __init__(
        self,
        db: Session,
    ):
        super().__init__(
            db=db,
            model=RestoreAttempt,
        )

    def list_recent(
        self,
        limit: int = 50,
    ) -> list[RestoreAttempt]:

        return (
            self.db.query(RestoreAttempt)
            .order_by(RestoreAttempt.created_at.desc())
            .limit(limit)
            .all()
        )


__all__ = ["RestoreRepository"]
