"""
=========================================================
Homez OS

File : app/domains/update/repository.py

Gate Y-4(2026-08-12) — 업데이트 공지 Repository.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository

from app.domains.update.model import UpdateNotice


class UpdateNoticeRepository(BaseRepository[UpdateNotice]):

    def __init__(
        self,
        db: Session,
    ):
        super().__init__(
            db=db,
            model=UpdateNotice,
        )

    def list_all(
        self,
        limit: int = 50,
    ) -> list[UpdateNotice]:

        return (
            self.db.query(UpdateNotice)
            .order_by(UpdateNotice.created_at.desc())
            .limit(limit)
            .all()
        )

    def list_active(
        self,
    ) -> list[UpdateNotice]:

        return (
            self.db.query(UpdateNotice)
            .filter(UpdateNotice.is_active.is_(True))
            .all()
        )

    def deactivate(
        self,
        notice: UpdateNotice,
    ) -> UpdateNotice:

        notice.is_active = False

        return self.save(notice)


__all__ = ["UpdateNoticeRepository"]
