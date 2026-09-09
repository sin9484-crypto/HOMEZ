from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_service import BaseService
from app.domains.activity_log.model import ActivityLog
from app.domains.activity_log.repository import (
    ActivityLogRepository,
)


class ActivityLogService(
    BaseService[ActivityLog]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        super().__init__(db)

        self.repository = (
            ActivityLogRepository(db)
        )

    def get(
        self,
        log_id: int,
    ) -> ActivityLog | None:

        return self.repository.get_by_id(
            log_id,
        )

    def list(
        self,
    ) -> list[ActivityLog]:

        return self.repository.get_all()
    def get_by_user(
        self,
        user_id: int,
    ) -> list[ActivityLog]:

        return self.repository.get_by_user(
            user_id,
        )

    def get_by_action(
        self,
        action: str,
    ) -> list[ActivityLog]:

        return self.repository.get_by_action(
            action,
        )

    def get_recent(
        self,
        limit: int = 100,
    ) -> list[ActivityLog]:

        return self.repository.get_recent(
            limit,
        )

    def create(
        self,
        **kwargs,
    ) -> ActivityLog:

        entity = ActivityLog(
            **kwargs,
        )

        return self.repository.create(
            entity,
        )
    def delete(
        self,
        log_id: int,
    ) -> bool:

        return self.repository.delete_by_id(
            log_id,
        )

    def get_by_status(
        self,
        status: str,
    ) -> list[ActivityLog]:

        return self.repository.get_by_status(
            status,
        )

    def get_by_request_id(
        self,
        request_id: str,
    ) -> ActivityLog | None:

        return self.repository.get_by_request_id(
            request_id,
        )

    def get_by_session_id(
        self,
        session_id: str,
    ) -> list[ActivityLog]:

        return self.repository.get_by_session_id(
            session_id,
        )
    def update(
        self,
        entity: ActivityLog,
    ) -> ActivityLog:

        return self.repository.update(
            entity,
        )

    def save(
        self,
        entity: ActivityLog,
    ) -> ActivityLog:

        return self.repository.save(
            entity,
        )


__all__ = [
    "ActivityLogService",
]    