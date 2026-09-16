"""
=========================================================
Homez OS

File : app/domains/platform_alert/service.py

2026-09-16 개인 베타 잔여 작업(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
10-18) — 서버 관리자 알림 배송 엔진.

`notification_center/delivery_service.py`와 같은 원칙: **알림 실패는
호출자의 비즈니스 트랜잭션에 절대 전파하지 않는다**(best-effort).
`dispatch_alert()` 자체가 모든 예외를 삼키고 로그만 남긴다 — 알림이
실패해도 이미 커밋된 업무(Migration 적용, 백업 실패 감지, 리콜 차단
등)는 그대로 유지된다.

"알림 실패 ≠ 업무 실행 성공"을 코드로 표현한 부분: `dispatch_alert()`
는 무엇을 반환하든 호출자가 그 반환값을 무시해도 안전하다 — 반환값
자체가 업무 성공 여부와 무관한 별개의 정보(전달 시도 결과)이기
때문이다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logger import logger
from app.domains.platform_alert.constants import get_platform_alert_definition
from app.domains.platform_alert.provider import NullPlatformAlertProvider
from app.domains.platform_alert.provider import PlatformAlertMessage
from app.domains.platform_alert.provider import PlatformAlertProvider
from app.domains.platform_alert.repository import PlatformAlertRepository

MAX_TOTAL_ATTEMPTS = 5
RETRY_BACKOFF_MINUTES = 30


class PlatformAlertDeliveryStatus:

    RESERVED = "RESERVED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    NO_RECIPIENT_CONFIGURED = "NO_RECIPIENT_CONFIGURED"
    NO_PROVIDER_CONFIGURED = "NO_PROVIDER_CONFIGURED"
    SENT = "SENT"
    FAILED = "FAILED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"

    ALL = (
        RESERVED, SKIPPED_DUPLICATE, NO_RECIPIENT_CONFIGURED,
        NO_PROVIDER_CONFIGURED, SENT, FAILED, RETRY_EXHAUSTED,
    )


@dataclass
class PlatformAlertDispatchResult:

    status: str
    log_id: int | None = None
    recipient_id: int | None = None
    detail: str = ""


class PlatformAlertService:

    def __init__(self, db: Session, provider: PlatformAlertProvider | None = None):

        self.db = db
        self.repository = PlatformAlertRepository(db)
        self.provider = provider or NullPlatformAlertProvider()

    def dispatch_alert(
        self, event_code: str, *, title: str, message: str,
        entity_ref: str | None = None, idempotency_key: str,
        now: datetime | None = None,
    ) -> list[PlatformAlertDispatchResult]:
        """호출 시점에 이미 벌어진 사건(Migration 제한모드 진입,
        DB 무결성 실패, 리콜 차단 등)을 서버 관리자에게 알린다.
        이 메서드 자체는 절대 예외를 던지지 않는다 — 호출자가
        try/except로 감쌀 필요가 없다(호출자의 실제 업무 로직을
        절대 막지 않는다는 계약)."""

        try:
            return self._dispatch_alert_unsafe(
                event_code, title=title, message=message,
                entity_ref=entity_ref, idempotency_key=idempotency_key,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - 알림은 항상 best-effort
            self.db.rollback()
            logger.warning(
                "서버 관리자 알림 생성 실패(원 업무는 유지): event=%s error=%s",
                event_code, type(exc).__name__,
            )
            return []

    def _dispatch_alert_unsafe(
        self, event_code: str, *, title: str, message: str,
        entity_ref: str | None, idempotency_key: str,
        now: datetime | None,
    ) -> list[PlatformAlertDispatchResult]:

        definition = get_platform_alert_definition(event_code)  # 알 수 없으면 KeyError
        now = now or datetime.utcnow()

        recipients = self.repository.get_active_recipients()

        if not recipients:
            # 알려진 한계: recipient_id가 NULL인 행은 SQLite UNIQUE
            # 제약이 NULL끼리 서로 다르다고 취급해 DB 레벨 중복방지가
            # 걸리지 않는다(동시 호출 시 이론상 중복 행 가능) — 다만
            # 이 분기는 실제로 아무것도 발송하지 않으므로(수신자 자체가
            # 없음) 중복 행이 생겨도 외부 영향은 없다.
            existing = self.repository.get_log_by_idempotency(idempotency_key, None)
            if existing is not None:
                return [PlatformAlertDispatchResult(
                    PlatformAlertDeliveryStatus.SKIPPED_DUPLICATE, existing.id,
                )]
            log = self._reserve_or_get_existing(
                event_code=event_code, severity=definition.severity,
                title=title, message=message, entity_ref=entity_ref,
                idempotency_key=idempotency_key, recipient_id=None, channel=None,
            )
            if log is None:
                return []
            self.repository.mark_status(
                log.id, status=PlatformAlertDeliveryStatus.NO_RECIPIENT_CONFIGURED,
            )
            self.db.commit()
            return [PlatformAlertDispatchResult(
                PlatformAlertDeliveryStatus.NO_RECIPIENT_CONFIGURED, log.id,
            )]

        results: list[PlatformAlertDispatchResult] = []
        for recipient in recipients:
            existing = self.repository.get_log_by_idempotency(
                idempotency_key, recipient.id,
            )
            if existing is not None:
                results.append(PlatformAlertDispatchResult(
                    PlatformAlertDeliveryStatus.SKIPPED_DUPLICATE,
                    existing.id, recipient.id,
                ))
                continue

            log = self._reserve_or_get_existing(
                event_code=event_code, severity=definition.severity,
                title=title, message=message, entity_ref=entity_ref,
                idempotency_key=idempotency_key, recipient_id=recipient.id,
                channel=recipient.contact_type,
            )
            if log is None:
                continue

            results.append(self._attempt_send(
                log_id=log.id, recipient_id=recipient.id,
                channel=recipient.contact_type, to=recipient.contact_value,
                title=title, message=message, event_code=event_code,
                now=now, prior_attempt_count=0,
            ))

        return results

    def _reserve_or_get_existing(
        self, *, event_code: str, severity: str, title: str, message: str,
        entity_ref: str | None, idempotency_key: str,
        recipient_id: int | None, channel: str | None,
    ):

        try:
            log = self.repository.reserve_log_row(
                event_code=event_code, severity=severity, title=title,
                message=message, entity_ref=entity_ref,
                idempotency_key=idempotency_key, recipient_id=recipient_id,
                channel=channel,
            )
            self.db.commit()
            return log
        except IntegrityError:
            self.db.rollback()
            return self.repository.get_log_by_idempotency(idempotency_key, recipient_id)

    def _attempt_send(
        self, *, log_id: int, recipient_id: int | None, channel: str | None,
        to: str, title: str, message: str, event_code: str,
        now: datetime, prior_attempt_count: int,
    ) -> PlatformAlertDispatchResult:

        if not self.provider.is_configured:
            self.repository.mark_status(
                log_id, status=PlatformAlertDeliveryStatus.NO_PROVIDER_CONFIGURED,
                error_detail="PLATFORM_ALERT_PROVIDER_NOT_CONFIGURED",
                attempt_count=prior_attempt_count,
            )
            self.db.commit()
            return PlatformAlertDispatchResult(
                PlatformAlertDeliveryStatus.NO_PROVIDER_CONFIGURED, log_id, recipient_id,
            )

        attempt = prior_attempt_count + 1
        try:
            self.provider.send(PlatformAlertMessage(
                channel=channel or "EMAIL", to=to, subject=title,
                body=message, event_code=event_code,
            ))
            self.repository.mark_status(
                log_id, status=PlatformAlertDeliveryStatus.SENT,
                attempt_count=attempt, sent_at=now,
            )
            self.db.commit()
            return PlatformAlertDispatchResult(
                PlatformAlertDeliveryStatus.SENT, log_id, recipient_id,
            )
        except Exception as exc:  # noqa: BLE001 - Provider 예외 형태를 강제하지 않는다
            error_detail = str(exc)[:400]
            status = (
                PlatformAlertDeliveryStatus.RETRY_EXHAUSTED
                if attempt >= MAX_TOTAL_ATTEMPTS
                else PlatformAlertDeliveryStatus.FAILED
            )
            next_retry_at = (
                now + timedelta(minutes=RETRY_BACKOFF_MINUTES)
                if status == PlatformAlertDeliveryStatus.FAILED else None
            )
            self.repository.mark_status(
                log_id, status=status, error_detail=error_detail,
                attempt_count=attempt, next_retry_at=next_retry_at,
            )
            self.db.commit()
            return PlatformAlertDispatchResult(status, log_id, recipient_id, error_detail)

    def retry_failed_alerts(
        self, *, now: datetime | None = None,
    ) -> list[PlatformAlertDispatchResult]:
        """이 저장소에는 배경 스케줄러가 없다(notification_center와
        동일 전제) — 호출자가 명시적으로 불러야만 FAILED 상태를
        재시도한다."""

        now = now or datetime.utcnow()
        due = self.repository.list_failed_due_for_retry(now)

        results: list[PlatformAlertDispatchResult] = []
        for log in due:
            recipient = (
                self.repository.get_recipient(log.recipient_id)
                if log.recipient_id is not None else None
            )
            if recipient is None or not recipient.active:
                # 수신자가 그 사이 삭제/비활성화됐다 — 더 보낼 대상이
                # 없으므로 재시도를 그만둔다(무한 재시도 금지).
                self.repository.mark_status(
                    log.id, status=PlatformAlertDeliveryStatus.RETRY_EXHAUSTED,
                    error_detail="RECIPIENT_NO_LONGER_ACTIVE",
                    attempt_count=log.attempt_count,
                )
                self.db.commit()
                results.append(PlatformAlertDispatchResult(
                    PlatformAlertDeliveryStatus.RETRY_EXHAUSTED, log.id, log.recipient_id,
                ))
                continue

            results.append(self._attempt_send(
                log_id=log.id, recipient_id=recipient.id,
                channel=recipient.contact_type, to=recipient.contact_value,
                title=log.title, message=log.message, event_code=log.event_code,
                now=now, prior_attempt_count=log.attempt_count,
            ))

        return results


__all__ = [
    "PlatformAlertService",
    "PlatformAlertDeliveryStatus",
    "PlatformAlertDispatchResult",
    "MAX_TOTAL_ATTEMPTS",
    "RETRY_BACKOFF_MINUTES",
]
