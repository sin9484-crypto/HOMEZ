"""
=========================================================
Homez OS

File : app/domains/recall_notice/repository.py

2026-09-15 전면 감사 후속(Phase 9I/9J, 10-17/10-18) — 저장소 계층.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.recall_notice.model import RecallCheckJobState
from app.domains.recall_notice.model import RecallCheckRun
from app.domains.recall_notice.model import RecallNotice
from app.domains.recall_notice.model import RecallProductBlock
from app.domains.recall_notice.constants import RecallProductBlockStatus


class RecallNoticeRepository:

    def __init__(self, db: Session):

        self.db = db

    # ------------------------------
    # RecallNotice
    # ------------------------------

    def get_notice_by_dedupe_key(self, dedupe_key: str) -> RecallNotice | None:

        return (
            self.db.query(RecallNotice)
            .filter(RecallNotice.dedupe_key == dedupe_key)
            .first()
        )

    def add_notice(self, notice: RecallNotice) -> RecallNotice:

        self.db.add(notice)
        self.db.flush()
        return notice

    def list_notices_for_product(
        self, product_identifier: str,
    ) -> list[RecallNotice]:

        return (
            self.db.query(RecallNotice)
            .filter(RecallNotice.product_identifier == product_identifier)
            .order_by(RecallNotice.discovered_at.desc())
            .all()
        )

    # ------------------------------
    # RecallCheckRun
    # ------------------------------

    def add_check_run(self, run: RecallCheckRun) -> RecallCheckRun:

        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def list_check_runs(self, *, limit: int = 50) -> list[RecallCheckRun]:

        return (
            self.db.query(RecallCheckRun)
            .order_by(RecallCheckRun.started_at.desc())
            .limit(limit)
            .all()
        )

    # ------------------------------
    # RecallCheckJobState
    # ------------------------------

    def get_latest_job_state(self) -> RecallCheckJobState | None:

        return (
            self.db.query(RecallCheckJobState)
            .order_by(RecallCheckJobState.set_at.desc())
            .first()
        )

    def add_job_state(self, state: RecallCheckJobState) -> RecallCheckJobState:

        self.db.add(state)
        self.db.commit()
        self.db.refresh(state)
        return state

    # ------------------------------
    # RecallProductBlock
    # ------------------------------

    def add_block(self, block: RecallProductBlock) -> RecallProductBlock:

        self.db.add(block)
        self.db.commit()
        self.db.refresh(block)
        return block

    def get_active_block(
        self, company_id: int, product_identifier: str,
    ) -> RecallProductBlock | None:

        return (
            self.db.query(RecallProductBlock)
            .filter(
                RecallProductBlock.company_id == company_id,
                RecallProductBlock.product_identifier == product_identifier,
                RecallProductBlock.status == RecallProductBlockStatus.BLOCKED,
            )
            .order_by(RecallProductBlock.blocked_at.desc())
            .first()
        )

    def get_block(
        self, block_id: int, company_id: int,
    ) -> RecallProductBlock | None:

        return (
            self.db.query(RecallProductBlock)
            .filter(
                RecallProductBlock.id == block_id,
                RecallProductBlock.company_id == company_id,
            )
            .first()
        )

    def list_blocks(
        self, company_id: int, *, status: str | None = None,
    ) -> list[RecallProductBlock]:

        query = self.db.query(RecallProductBlock).filter(
            RecallProductBlock.company_id == company_id,
        )
        if status is not None:
            query = query.filter(RecallProductBlock.status == status)
        return query.order_by(RecallProductBlock.blocked_at.desc()).all()


__all__ = ["RecallNoticeRepository"]
