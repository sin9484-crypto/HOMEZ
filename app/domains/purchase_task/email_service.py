"""
=========================================================
Homez OS

File : app/domains/purchase_task/email_service.py

작업 G — 이메일 알림 발송(큐+idempotency+제한된 재시도+감사로그).
이 코드베이스에는 백그라운드 작업 스케줄러가 없다 — "큐"는 즉시
동기 발송 시도 + PurchaseTaskEmailLog에 결과를 남기는 방식으로
구현한다(무한 재시도 없음, 실패 시 감사로그에 남기고 그대로 끝낸다
— 자동 재시도가 필요하면 호출자가 다시 enqueue해야 한다). 실 Provider
Credential은 여기에도 어디에도 없다.
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.domains.notification_center.model import NOTIFICATION_LEVEL_WARNING
from app.domains.notification_center.service import NotificationService
from app.domains.purchase_task.constants import EmailSendStatus
from app.domains.purchase_task.email_provider import EmailMessage
from app.domains.purchase_task.email_provider import NullPurchaseTaskEmailProvider
from app.domains.purchase_task.email_provider import PurchaseTaskEmailProvider
from app.domains.purchase_task.email_templates import render_body
from app.domains.purchase_task.email_templates import render_subject
from app.domains.purchase_task.model import PurchaseTaskEmailPreference
from app.domains.purchase_task.repository import PurchaseTaskRepository

MAX_SEND_ATTEMPTS = 3


class PurchaseTaskEmailService:

    def __init__(
        self, db: Session, provider: PurchaseTaskEmailProvider | None = None,
    ):

        self.db = db
        self.repository = PurchaseTaskRepository(db)
        self.provider = provider or NullPurchaseTaskEmailProvider()

    def _is_opted_in(self, company_id: int, user_id: int, event_type: str) -> bool:

        pref = self.repository.get_email_preference(company_id, user_id)
        if pref is None:
            return True  # 기본값: 켜짐(안내를 놓치지 않는 것이 안전측 기본값)
        if not pref.enabled:
            return False
        try:
            toggles = json.loads(pref.event_toggles_json or "{}")
        except (TypeError, ValueError):
            toggles = {}
        return bool(toggles.get(event_type, True))

    def send_notification(
        self, *, company_id: int, user_id: int, to_email: str,
        event_type: str, purchase_task_id: int | None, product_title: str,
        detail: str, locale: str = "ko-KR", idempotency_key: str,
    ) -> str:
        """반환값은 EmailSendStatus 중 하나. 예외를 던지지 않는다 —
        실패도 정상적인 결과 값으로 다룬다(호출자가 매번 try/except를
        쓰지 않아도 되게)."""

        existing = self.repository.get_email_log_by_idempotency(
            company_id, idempotency_key,
        )
        if existing is not None:
            return existing.status  # 이미 처리됨 — 중복 발송하지 않는다.

        if not self._is_opted_in(company_id, user_id, event_type):
            status = EmailSendStatus.SKIPPED_OPTED_OUT
            self._log(
                company_id=company_id, user_id=user_id,
                purchase_task_id=purchase_task_id, event_type=event_type,
                locale=locale, status=status, idempotency_key=idempotency_key,
                attempt_count=0,
            )
            self.db.commit()
            return status

        if not self.provider.is_configured:
            status = EmailSendStatus.PROVIDER_NOT_CONFIGURED
            self._log(
                company_id=company_id, user_id=user_id,
                purchase_task_id=purchase_task_id, event_type=event_type,
                locale=locale, status=status, idempotency_key=idempotency_key,
                attempt_count=0,
                error_detail="EMAIL_PROVIDER_NOT_CONFIGURED",
            )
            write_audit_log(
                self.db, user_id=user_id, action="PURCHASE_TASK_EMAIL_SKIPPED",
                entity="purchase_task_email", entity_id=str(purchase_task_id or 0),
                description=f"이메일 미발송(Provider 미구성): {event_type}",
                company_id=company_id,
            )
            self.db.commit()
            return status

        message = EmailMessage(
            to_email=to_email, subject=render_subject(event_type, locale),
            body_text=render_body(
                event_type, locale, product_title=product_title, detail=detail,
            ),
            event_type=event_type, locale=locale,
        )

        attempt = 0
        status = EmailSendStatus.FAILED
        error_detail = None

        while attempt < MAX_SEND_ATTEMPTS:
            attempt += 1
            try:
                self.provider.send(message)
                status = EmailSendStatus.SENT
                break
            except Exception as e:  # noqa: BLE001 — Provider 예외 형태를 강제하지 않는다
                error_detail = str(e)[:400]

        sent_at = datetime.utcnow() if status == EmailSendStatus.SENT else None
        self._log(
            company_id=company_id, user_id=user_id,
            purchase_task_id=purchase_task_id, event_type=event_type,
            locale=locale, status=status, idempotency_key=idempotency_key,
            attempt_count=attempt, error_detail=error_detail, sent_at=sent_at,
        )

        write_audit_log(
            self.db, user_id=user_id,
            action=(
                "PURCHASE_TASK_EMAIL_SENT" if status == EmailSendStatus.SENT
                else "PURCHASE_TASK_EMAIL_FAILED"
            ),
            entity="purchase_task_email", entity_id=str(purchase_task_id or 0),
            description=f"이메일 발송({status}): {event_type}",
            company_id=company_id,
        )

        self.db.commit()

        if status == EmailSendStatus.FAILED:
            # EMAIL_DELIVERY_FAILED — 이메일 발송 자체가 실패했다는
            # 사실을 다시 이메일로 알리지 않는다(순환·2차 실패 위험).
            # Desktop 알림(notification_center)으로만 알린다 — 이
            # 알림 자체가 실패해도 발송 결과 자체는 이미 기록됐으므로
            # 삼킨다.
            try:
                NotificationService(self.db).notify_user(
                    company_id=company_id, user_id=user_id,
                    category="purchase_task",
                    level=NOTIFICATION_LEVEL_WARNING,
                    title="이메일 알림 발송 실패",
                    message=(
                        f"'{event_type}' 이메일 발송이 {attempt}회 시도 후 "
                        "실패했습니다 — Desktop 알림으로만 안내합니다."
                    ),
                    link_path=(
                        f"purchase-task-detail?id={purchase_task_id}"
                        if purchase_task_id else None
                    ),
                )
                self.db.commit()
            except Exception:  # noqa: BLE001
                self.db.rollback()

        return status

    def _log(
        self, *, company_id: int, user_id: int, purchase_task_id: int | None,
        event_type: str, locale: str, status: str, idempotency_key: str,
        attempt_count: int, error_detail: str | None = None,
        sent_at: datetime | None = None,
    ) -> None:

        try:
            self.repository.add_email_log_row(
                company_id=company_id, user_id=user_id,
                purchase_task_id=purchase_task_id, event_type=event_type,
                locale=locale, status=status, idempotency_key=idempotency_key,
                attempt_count=attempt_count, error_detail=error_detail,
                sent_at=sent_at,
            )
        except IntegrityError:
            self.db.rollback()

    def set_preference(
        self, company_id: int, user_id: int, *, enabled: bool,
        event_toggles: dict[str, bool] | None = None,
    ) -> PurchaseTaskEmailPreference:

        pref = self.repository.get_email_preference(company_id, user_id)
        if pref is None:
            pref = PurchaseTaskEmailPreference(
                company_id=company_id, user_id=user_id, enabled=enabled,
                event_toggles_json=json.dumps(
                    event_toggles or {}, ensure_ascii=False,
                ),
            )
            self.repository.add_email_preference(pref)
        else:
            pref.enabled = enabled
            if event_toggles is not None:
                pref.event_toggles_json = json.dumps(
                    event_toggles, ensure_ascii=False,
                )

        self.db.commit()
        return pref

    def get_preference(
        self, company_id: int, user_id: int,
    ) -> PurchaseTaskEmailPreference:

        pref = self.repository.get_email_preference(company_id, user_id)
        if pref is None:
            pref = PurchaseTaskEmailPreference(
                company_id=company_id, user_id=user_id, enabled=True,
                event_toggles_json="{}",
            )
            pref = self.repository.add_email_preference(pref)
            self.db.commit()
        return pref


__all__ = ["PurchaseTaskEmailService", "MAX_SEND_ATTEMPTS"]
