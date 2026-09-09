"""
=========================================================
Homez OS

File : app/domains/notification_center/repository.py

Gate Y-3(2026-08-12) — 알림 Repository. company_id 격리를 모든
조회에 강제한다(다른 회사의 알림을 절대 조회할 수 없다).

읽음 상태는 개인 알림과 회사 공지가 서로 다른 방식으로 저장되므로
(model.py 문서 참고), 이 Repository가 그 차이를 감추고 호출자에게는
항상 "이 사용자 기준으로 읽었는가"라는 하나의 결과만 돌려준다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository

from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationEmailLog
from app.domains.notification_center.model import NotificationEventPreference
from app.domains.notification_center.model import NotificationPreference
from app.domains.notification_center.model import NotificationRead


class NotificationRepository(BaseRepository[Notification]):

    def __init__(
        self,
        db: Session,
    ):
        super().__init__(
            db=db,
            model=Notification,
        )

    def _visible_to_user_filter(
        self,
        company_id: int,
        user_id: int,
    ):

        return and_(
            Notification.company_id == company_id,
            or_(
                Notification.user_id == user_id,
                Notification.user_id.is_(None),
            ),
        )

    def _broadcast_read_ids(
        self,
        user_id: int,
        notification_ids: list[int],
    ) -> set[int]:

        if not notification_ids:
            return set()

        rows = (
            self.db.query(NotificationRead.notification_id)
            .filter(
                NotificationRead.user_id == user_id,
                NotificationRead.notification_id.in_(notification_ids),
            )
            .all()
        )

        return {row[0] for row in rows}

    def is_read_for_user(
        self,
        notification: Notification,
        user_id: int,
    ) -> bool:

        if notification.user_id is not None:
            return notification.is_read

        read = (
            self.db.query(NotificationRead)
            .filter(
                NotificationRead.notification_id == notification.id,
                NotificationRead.user_id == user_id,
            )
            .first()
        )

        return read is not None

    def list_for_user(
        self,
        company_id: int,
        user_id: int,
        *,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Notification]:

        rows = (
            self.db.query(Notification)
            .filter(self._visible_to_user_filter(company_id, user_id))
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .all()
        )

        broadcast_ids = [n.id for n in rows if n.user_id is None]
        read_broadcast_ids = self._broadcast_read_ids(
            user_id,
            broadcast_ids,
        )

        results = []
        for notification in rows:
            if notification.user_id is None:
                effective_read = notification.id in read_broadcast_ids
            else:
                effective_read = notification.is_read

            if unread_only and effective_read:
                continue

            results.append((notification, effective_read))

        return results

    def count_unread(
        self,
        company_id: int,
        user_id: int,
    ) -> int:

        rows = (
            self.db.query(Notification)
            .filter(self._visible_to_user_filter(company_id, user_id))
            .all()
        )

        broadcast_ids = [n.id for n in rows if n.user_id is None]
        read_broadcast_ids = self._broadcast_read_ids(
            user_id,
            broadcast_ids,
        )

        count = 0
        for notification in rows:
            if notification.user_id is None:
                if notification.id not in read_broadcast_ids:
                    count += 1
            elif not notification.is_read:
                count += 1

        return count

    def get_visible(
        self,
        notification_id: int,
        company_id: int,
        user_id: int,
    ) -> Notification | None:

        return (
            self.db.query(Notification)
            .filter(
                Notification.id == notification_id,
                self._visible_to_user_filter(company_id, user_id),
            )
            .first()
        )

    def mark_read(
        self,
        notification: Notification,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> datetime:
        """
        읽음 처리한다. 반환값은 이 사용자 기준의 read_at(호출 전에
        이미 읽은 상태였다면 그 최초 시각을 그대로 반환 — idempotent).
        """

        now = now or datetime.utcnow()

        if notification.user_id is not None:
            if not notification.is_read:
                notification.is_read = True
                notification.read_at = now
                self.save(notification)
            return notification.read_at

        existing = (
            self.db.query(NotificationRead)
            .filter(
                NotificationRead.notification_id == notification.id,
                NotificationRead.user_id == user_id,
            )
            .first()
        )

        if existing is not None:
            return existing.read_at

        record = NotificationRead(
            notification_id=notification.id,
            user_id=user_id,
            read_at=now,
        )
        self.db.add(record)
        self.db.commit()

        return now

    def mark_all_read(
        self,
        company_id: int,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> int:

        now = now or datetime.utcnow()

        notifications = (
            self.db.query(Notification)
            .filter(self._visible_to_user_filter(company_id, user_id))
            .all()
        )

        broadcast_ids = [n.id for n in notifications if n.user_id is None]
        already_read_broadcast_ids = self._broadcast_read_ids(
            user_id,
            broadcast_ids,
        )

        marked_count = 0

        for notification in notifications:
            if notification.user_id is not None:
                if not notification.is_read:
                    notification.is_read = True
                    notification.read_at = now
                    marked_count += 1
            else:
                if notification.id not in already_read_broadcast_ids:
                    self.db.add(
                        NotificationRead(
                            notification_id=notification.id,
                            user_id=user_id,
                            read_at=now,
                        ),
                    )
                    marked_count += 1

        self.db.commit()

        return marked_count


class NotificationDeliveryRepository:
    """Gate PT-3(2026-08-23 17차 지시) — 중앙 이메일 발송 이력/설정
    Repository. 기존 NotificationRepository(Gate Y-3, 앱 내부 알림
    전용)와 별개다 — 이메일 로그·설정 테이블은 이번 Gate에서 새로
    추가된 3개 테이블만 다룬다."""

    def __init__(self, db: Session):
        self.db = db

    def get_email_log_by_idempotency(
        self, company_id: int, idempotency_key: str,
    ) -> NotificationEmailLog | None:

        return (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == company_id,
                NotificationEmailLog.idempotency_key == idempotency_key,
            )
            .first()
        )

    def get_by_id(self, log_id: int) -> NotificationEmailLog | None:

        return (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.id == log_id)
            .first()
        )

    def reserve_log_row(
        self, *, company_id: int, user_id: int, event_code: str,
        entity_ref: str | None, locale: str, idempotency_key: str,
    ) -> NotificationEmailLog:
        """UNIQUE(company_id, idempotency_key)에 기대 — 이미 존재하면
        IntegrityError를 그대로 호출자에게 전파한다(호출자가 중복
        발송으로 처리)."""

        log = NotificationEmailLog(
            company_id=company_id, user_id=user_id, event_code=event_code,
            entity_ref=entity_ref, locale=locale, status="RESERVED",
            idempotency_key=idempotency_key, attempt_count=0,
        )
        self.db.add(log)
        self.db.flush()
        return log

    def mark_status(
        self, log_id: int, *, status: str,
        error_detail: str | None = None,
        attempt_count: int | None = None,
        sent_at: datetime | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:

        log = self.get_by_id(log_id)
        if log is None:
            return
        log.status = status
        if error_detail is not None:
            log.error_detail = error_detail
        if attempt_count is not None:
            log.attempt_count = attempt_count
        if sent_at is not None:
            log.sent_at = sent_at
        log.next_retry_at = next_retry_at
        self.db.add(log)
        self.db.flush()

    def list_pending_unconfirmed(
        self, company_id: int, *, now: datetime,
    ) -> list[NotificationEmailLog]:

        return (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == company_id,
                NotificationEmailLog.status == "PENDING_UNCONFIRMED",
                NotificationEmailLog.next_retry_at <= now,
            )
            .all()
        )

    def list_failed_retryable(
        self, company_id: int, *, now: datetime,
    ) -> list[NotificationEmailLog]:

        return (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == company_id,
                NotificationEmailLog.status == "FAILED",
                NotificationEmailLog.next_retry_at <= now,
            )
            .all()
        )

    def get_preference(
        self, company_id: int, user_id: int,
    ) -> NotificationPreference | None:

        return (
            self.db.query(NotificationPreference)
            .filter(
                NotificationPreference.company_id == company_id,
                NotificationPreference.user_id == user_id,
            )
            .first()
        )

    def upsert_preference(
        self, company_id: int, user_id: int, *,
        email_enabled: bool | None = None,
        quiet_hours_start: int | None = None,
        quiet_hours_end: int | None = None,
        clear_quiet_hours: bool = False,
    ) -> NotificationPreference:

        pref = self.get_preference(company_id, user_id)
        if pref is None:
            pref = NotificationPreference(
                company_id=company_id, user_id=user_id,
                email_enabled=True if email_enabled is None else email_enabled,
            )
            self.db.add(pref)
        else:
            if email_enabled is not None:
                pref.email_enabled = email_enabled

        if clear_quiet_hours:
            pref.quiet_hours_start = None
            pref.quiet_hours_end = None
        else:
            if quiet_hours_start is not None:
                pref.quiet_hours_start = quiet_hours_start
            if quiet_hours_end is not None:
                pref.quiet_hours_end = quiet_hours_end

        self.db.commit()
        self.db.refresh(pref)
        return pref

    def get_event_preference(
        self, company_id: int, user_id: int, event_code: str,
    ) -> NotificationEventPreference | None:

        return (
            self.db.query(NotificationEventPreference)
            .filter(
                NotificationEventPreference.company_id == company_id,
                NotificationEventPreference.user_id == user_id,
                NotificationEventPreference.event_code == event_code,
            )
            .first()
        )

    def list_event_preferences(
        self, company_id: int, user_id: int,
    ) -> list[NotificationEventPreference]:

        return (
            self.db.query(NotificationEventPreference)
            .filter(
                NotificationEventPreference.company_id == company_id,
                NotificationEventPreference.user_id == user_id,
            )
            .all()
        )

    def upsert_event_preference(
        self, company_id: int, user_id: int, event_code: str, *,
        enabled: bool | None = None,
        email_immediate_override: bool | None = None,
        unconfirmed_wait_minutes_override: int | None = None,
        clear_overrides: bool = False,
    ) -> NotificationEventPreference:

        pref = self.get_event_preference(company_id, user_id, event_code)
        if pref is None:
            pref = NotificationEventPreference(
                company_id=company_id, user_id=user_id, event_code=event_code,
                enabled=True if enabled is None else enabled,
            )
            self.db.add(pref)
        else:
            if enabled is not None:
                pref.enabled = enabled

        if clear_overrides:
            pref.email_immediate_override = None
            pref.unconfirmed_wait_minutes_override = None
        else:
            if email_immediate_override is not None:
                pref.email_immediate_override = email_immediate_override
            if unconfirmed_wait_minutes_override is not None:
                pref.unconfirmed_wait_minutes_override = unconfirmed_wait_minutes_override

        self.db.commit()
        self.db.refresh(pref)
        return pref


__all__ = ["NotificationRepository", "NotificationDeliveryRepository"]
