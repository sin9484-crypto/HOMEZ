from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository
from app.domains.activity_log.model import ActivityLog


class ActivityLogRepository(
    BaseRepository[ActivityLog]
):

    model = ActivityLog

    def __init__(
        self,
        db: Session,
    ) -> None:

        super().__init__(db)

    def get_by_id(
        self,
        log_id: int,
    ) -> ActivityLog | None:

        return self.db.get(
            ActivityLog,
            log_id,
        )

    def get_all(
        self,
    ) -> list[ActivityLog]:

        return list(
            self.db.scalars(
                select(ActivityLog)
            )
        )
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository
from app.domains.activity_log.model import ActivityLog


class ActivityLogRepository(
    BaseRepository[ActivityLog]
):

    model = ActivityLog

    def __init__(
        self,
        db: Session,
    ) -> None:

        super().__init__(db)

    def get_by_id(
        self,
        log_id: int,
    ) -> ActivityLog | None:

        return self.db.get(
            ActivityLog,
            log_id,
        )

    def get_all(
        self,
    ) -> list[ActivityLog]:

        return list(
            self.db.scalars(
                select(ActivityLog)
            )
        )
    def get_recent(
        self,
        limit: int = 100,
    ) -> list[ActivityLog]:

        return list(
            self.db.scalars(
                select(ActivityLog)
                .order_by(
                    ActivityLog.created_at.desc()
                )
                .limit(limit)
            )
        )

    def get_by_request_id(
        self,
        request_id: str,
    ) -> ActivityLog | None:

        return self.db.scalar(
            select(ActivityLog).where(
                ActivityLog.request_id == request_id
            )
        )

    def get_by_session_id(
        self,
        session_id: str,
    ) -> list[ActivityLog]:

        return list(
            self.db.scalars(
                select(ActivityLog).where(
                    ActivityLog.session_id == session_id
                )
            )
        )
    def delete_by_id(
        self,
        log_id: int,
    ) -> bool:

        entity = self.get_by_id(
            log_id,
        )

        if entity is None:
            return False

        self.db.delete(
            entity,
        )

        self.db.commit()

        return True


__all__ = [
    "ActivityLogRepository",
]        