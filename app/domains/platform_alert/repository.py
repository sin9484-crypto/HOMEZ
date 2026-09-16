"""
=========================================================
Homez OS

File : app/domains/platform_alert/repository.py

2026-09-16 개인 베타 잔여 작업(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
10-18) — 서버 관리자 알림 저장소 접근 계층.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.platform_alert.model import PlatformAlertDeliveryLog
from app.domains.platform_alert.model import PlatformAlertRecipient


class PlatformAlertRepository:

    def __init__(self, db: Session):
        self.db = db

    # ---------------- 수신자 ----------------

    def get_active_recipients(self) -> list[PlatformAlertRecipient]:

        return (
            self.db.query(PlatformAlertRecipient)
            .filter(PlatformAlertRecipient.active.is_(True))
            .order_by(PlatformAlertRecipient.id.asc())
            .all()
        )

    def get_recipient(self, recipient_id: int) -> PlatformAlertRecipient | None:

        return (
            self.db.query(PlatformAlertRecipient)
            .filter(PlatformAlertRecipient.id == recipient_id)
            .first()
        )

    def add_recipient(
        self, *, contact_type: str, contact_value: str, label: str | None = None,
    ) -> PlatformAlertRecipient:

        recipient = PlatformAlertRecipient(
            contact_type=contact_type, contact_value=contact_value, label=label,
        )
        self.db.add(recipient)
        self.db.flush()
        return recipient

    def set_recipient_active(self, recipient_id: int, *, active: bool) -> bool:

        recipient = self.get_recipient(recipient_id)
        if recipient is None:
            return False
        recipient.active = active
        self.db.flush()
        return True

    # ---------------- 전달 로그 ----------------

    def get_log_by_idempotency(
        self, idempotency_key: str, recipient_id: int | None,
    ) -> PlatformAlertDeliveryLog | None:

        return (
            self.db.query(PlatformAlertDeliveryLog)
            .filter(
                PlatformAlertDeliveryLog.idempotency_key == idempotency_key,
                PlatformAlertDeliveryLog.recipient_id == recipient_id,
            )
            .first()
        )

    def reserve_log_row(
        self, *, event_code: str, severity: str, title: str, message: str,
        entity_ref: str | None, idempotency_key: str,
        recipient_id: int | None, channel: str | None,
    ) -> PlatformAlertDeliveryLog:

        log = PlatformAlertDeliveryLog(
            event_code=event_code, severity=severity, title=title,
            message=message, entity_ref=entity_ref,
            idempotency_key=idempotency_key, recipient_id=recipient_id,
            channel=channel, status="RESERVED", attempt_count=0,
        )
        self.db.add(log)
        self.db.flush()
        return log

    def get_by_id(self, log_id: int) -> PlatformAlertDeliveryLog | None:

        return (
            self.db.query(PlatformAlertDeliveryLog)
            .filter(PlatformAlertDeliveryLog.id == log_id)
            .first()
        )

    def mark_status(
        self, log_id: int, *, status: str, error_detail: str | None = None,
        attempt_count: int | None = None, sent_at: datetime | None = None,
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
        self.db.flush()

    def list_failed_due_for_retry(self, now: datetime) -> list[PlatformAlertDeliveryLog]:

        return (
            self.db.query(PlatformAlertDeliveryLog)
            .filter(
                PlatformAlertDeliveryLog.status == "FAILED",
                PlatformAlertDeliveryLog.next_retry_at.isnot(None),
                PlatformAlertDeliveryLog.next_retry_at <= now,
            )
            .order_by(PlatformAlertDeliveryLog.id.asc())
            .all()
        )


__all__ = ["PlatformAlertRepository"]
