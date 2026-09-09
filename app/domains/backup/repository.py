"""
=========================================================
Homez OS

File : app/domains/backup/repository.py

Gate Y-1(2026-08-12) — 백업 이력 Repository. 다른 append-only
이력 테이블(예: MarketplaceSubmissionApproval)과 같은 패턴 —
행을 만든 뒤에는 수정하지 않는다(백업 이력을 사후에 고쳐 쓰는
것은 감사 무결성을 해친다).
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository

from app.domains.backup.model import BackupRecord


class BackupRepository(BaseRepository[BackupRecord]):

    def __init__(
        self,
        db: Session,
    ):
        super().__init__(
            db=db,
            model=BackupRecord,
        )

    def list_recent(
        self,
        limit: int = 50,
    ) -> list[BackupRecord]:

        return (
            self.db.query(BackupRecord)
            .order_by(BackupRecord.created_at.desc())
            .limit(limit)
            .all()
        )

    def list_beyond_retention(
        self,
        keep_count: int,
    ) -> list[BackupRecord]:
        """
        2026-08-15 V7 Gate 8 — 보존 정책(retention) 조회 전용, 읽기
        전용이다. 최신 `keep_count`개를 제외한 나머지(=보존 기간이
        지난 것으로 볼 수 있는 백업)를 오래된 순으로 반환한다. 이
        메서드는 아무것도 삭제하지 않는다 — 실제 파일/이력 삭제는
        CLAUDE.md Safety 규칙상 이 세션에서 자동으로 하지 않는다
        (별도 명시적 사용자 승인 후 관리 스크립트/화면에서 수행해야
        한다).
        """

        if keep_count < 0:
            keep_count = 0

        beyond_newest_first = (
            self.db.query(BackupRecord)
            .order_by(BackupRecord.created_at.desc())
            .offset(keep_count)
            .all()
        )

        # 삭제 검토는 보통 가장 오래된 것부터 보는 것이 자연스러우므로
        # 오래된 순으로 반환한다.
        return list(reversed(beyond_newest_first))


__all__ = ["BackupRepository"]
