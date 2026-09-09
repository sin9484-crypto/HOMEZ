"""
=========================================================
Homez OS

File : app/domains/notification_center/service.py

Gate Y-3(2026-08-12) — 알림 센터 Service. 다른 도메인(백업/복원/
Migration 등)이 사용자에게 알림을 남기고 싶을 때는 이 서비스의
notify_user()/notify_company()만 호출하면 된다 — 회사 격리/읽음
추적 등 세부사항은 여기서 전부 감춘다.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.notification_center.model import NOTIFICATION_LEVELS
from app.domains.notification_center.model import Notification
from app.domains.notification_center.repository import (
    NotificationRepository,
)

_NOT_FOUND_MESSAGE = "해당 알림을 찾을 수 없습니다."


class NotificationService:

    def __init__(
        self,
        db: Session,
    ):
        self.repository = NotificationRepository(db)

    def _create(
        self,
        *,
        company_id: int,
        user_id: int | None,
        category: str,
        level: str,
        title: str,
        message: str,
        link_path: str | None,
    ) -> Notification:

        if level not in NOTIFICATION_LEVELS:
            raise BadRequestException(
                f"알 수 없는 알림 level입니다: {level!r}",
            )

        if not category or not title or not message:
            raise BadRequestException(
                "category/title/message는 비어 있을 수 없습니다.",
            )

        notification = Notification(
            company_id=company_id,
            user_id=user_id,
            category=category,
            level=level,
            title=title,
            message=message,
            link_path=link_path,
        )

        return self.repository.create(notification)

    def notify_user(
        self,
        *,
        company_id: int,
        user_id: int,
        category: str,
        level: str,
        title: str,
        message: str,
        link_path: str | None = None,
    ) -> Notification:

        return self._create(
            company_id=company_id,
            user_id=user_id,
            category=category,
            level=level,
            title=title,
            message=message,
            link_path=link_path,
        )

    def notify_company(
        self,
        *,
        company_id: int,
        category: str,
        level: str,
        title: str,
        message: str,
        link_path: str | None = None,
    ) -> Notification:

        return self._create(
            company_id=company_id,
            user_id=None,
            category=category,
            level=level,
            title=title,
            message=message,
            link_path=link_path,
        )

    def list_for_user(
        self,
        company_id: int,
        user_id: int,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[tuple[Notification, bool]]:

        return self.repository.list_for_user(
            company_id,
            user_id,
            unread_only=unread_only,
            limit=limit,
        )

    def count_unread(
        self,
        company_id: int,
        user_id: int,
    ) -> int:

        return self.repository.count_unread(company_id, user_id)

    def mark_read(
        self,
        company_id: int,
        user_id: int,
        notification_id: int,
    ):

        notification = self.repository.get_visible(
            notification_id,
            company_id,
            user_id,
        )

        if notification is None:
            raise NotFoundException(_NOT_FOUND_MESSAGE)

        return self.repository.mark_read(notification, user_id)

    def mark_all_read(
        self,
        company_id: int,
        user_id: int,
    ) -> int:

        return self.repository.mark_all_read(company_id, user_id)


__all__ = ["NotificationService"]
