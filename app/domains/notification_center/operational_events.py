"""Best-effort bridge from committed business transitions to notifications."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.logger import logger
from app.domains.notification_center.delivery_service import (
    NotificationDeliveryService,
)


def dispatch_operational_event(
    db: Session,
    event_code: str,
    *,
    company_id: int,
    user_id: int | None,
    idempotency_key: str,
    title: str,
    message: str,
    link_path: str | None,
    entity_ref: str,
    reason: str = "",
    entity_summary: str = "",
    amount_summary: str = "",
    deadline_text: str = "",
    is_still_needed: Callable[[], bool] | None = None,
) -> None:
    """Dispatch after the owning business transaction has committed.

    Notification delivery is deliberately best-effort. Older databases and
    narrow unit-test fixtures may not have the pending notification-delivery
    migration yet; that must never roll back an already committed business
    action.
    """

    bind = db.get_bind()
    if not inspect(bind).has_table("notification_email_logs"):
        return

    to_email = None
    if user_id is not None:
        try:
            from app.domains.user.model import User

            user = (
                db.query(User)
                .filter(User.id == user_id, User.company_id == company_id)
                .first()
            )
            if user is not None:
                to_email = user.email
        except Exception:  # noqa: BLE001 - notification remains best-effort
            db.rollback()

    try:
        NotificationDeliveryService(db).dispatch(
            event_code,
            company_id=company_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            title=title,
            message=message,
            link_path=link_path,
            entity_ref=entity_ref,
            to_email=to_email,
            reason=reason,
            entity_summary=entity_summary,
            amount_summary=amount_summary,
            deadline_text=deadline_text,
            is_still_needed=is_still_needed,
        )
    except Exception as exc:  # noqa: BLE001 - never break the source workflow
        db.rollback()
        logger.warning(
            "운영 알림 생성 실패(원 업무는 유지): event=%s entity=%s error=%s",
            event_code,
            entity_ref,
            type(exc).__name__,
        )


__all__ = ["dispatch_operational_event"]
