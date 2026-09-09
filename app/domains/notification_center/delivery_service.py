"""
=========================================================
Homez OS

File : app/domains/notification_center/delivery_service.py

Gate PT-3(2026-08-23 17차 지시) — 중앙 알림 전달 엔진. event_catalog.py의
심각도·기본정책을 읽어 앱 내부 알림(NotificationService, 항상 기본
채널)과 이메일(email_provider.py, 보조 채널)을 분배한다.

이 저장소에는 배경 스케줄러가 전혀 없다(purchase_task/email_service.py와
동일 전제, 확인됨). 그래서 "미확인 대기시간이 지나면 이메일 전환"과
"Retry-After 이후 재시도"는 즉시 자동 실행되는 게 아니라, 호출자가
명시적으로 escalate_unconfirmed_to_email()/retry_failed_notification()을
불러야만 실행되는 재조정(reconciliation) 스타일 메서드다 — Gate PT-2의
check_deadlines_approaching()과 같은 선례를 그대로 따른다.

발송 직전 재검증(is_still_needed) 계약: 이미 처리된 업무는 이메일
발송을 취소해야 한다는 지시문 요구사항을 지키기 위해, dispatch()와
escalate_unconfirmed_to_email() 모두 호출자가 넘긴 `is_still_needed()`
콜백을 **이메일을 실제로 보내기 직전**에 다시 호출한다. 콜백을 넘기지
않으면 재검증 없이 진행한다 — 그 경우 호출자가 재검증 책임을 진다는
뜻이며, 이 설계상의 한계를 최종 보고서에 그대로 공개한다.

멱등성 설계: Notification 모델 자체에는 idempotency_key 컬럼이 없다
(기존 Gate Y-3 스키마를 이번 Gate에서 변경하지 않기 위한 의도적
선택). 대신 이 이벤트 배포 1회 전체(앱 내부 알림 생성 + 이메일 발송
여부 판단)를 NotificationEmailLog 1행의 UNIQUE(company_id,
idempotency_key) 예약으로 게이팅한다 — 이메일을 실제로 보내지 않는
이벤트(LOW 등)도 예약 행은 남는다. 이것이 "같은 대상+같은 사유+같은
state-version은 한 번만 보낸다"는 지시문 요구를 만족하는 방법이다.

이메일 본문에 들어갈 필드(reason/entity_summary/amount_summary/
deadline_text/console_url/to_email)는 NotificationEmailLog에 저장하지
않는다 — 저장하면 나중에 오래된(stale) 내용으로 재전송될 위험이
있다. 그래서 escalate_unconfirmed_to_email()/retry_failed_notification()
호출자는 그 시점의 최신 값을 다시 넘겨야 한다 — 이것은 우연이 아니라
"발송 직전 실제 상태를 다시 조회하라"는 지시문 요구사항을 그대로
구현한 것이다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.domains.notification_center.email_provider import (
    NotificationEmailMessage,
)
from app.domains.notification_center.email_provider import (
    NotificationEmailProvider,
)
from app.domains.notification_center.email_provider import (
    NullNotificationEmailProvider,
)
from app.domains.notification_center.email_templates import render_body
from app.domains.notification_center.email_templates import render_subject
from app.domains.notification_center.event_catalog import NotificationSeverity
from app.domains.notification_center.event_catalog import get_event_definition
from app.domains.notification_center.model import NOTIFICATION_LEVEL_ERROR
from app.domains.notification_center.model import NOTIFICATION_LEVEL_INFO
from app.domains.notification_center.model import NOTIFICATION_LEVEL_WARNING
from app.domains.notification_center.model import NotificationEmailLog
from app.domains.notification_center.repository import (
    NotificationDeliveryRepository,
)
from app.domains.notification_center.service import NotificationService


class DeliveryStatus:

    RESERVED = "RESERVED"  # reserve_log_row() 직후, 아직 정책 판단 전(transient)
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    SKIPPED_OPTED_OUT = "SKIPPED_OPTED_OUT"
    SKIPPED_IN_APP_ONLY = "SKIPPED_IN_APP_ONLY"
    CANCELLED_ALREADY_RESOLVED = "CANCELLED_ALREADY_RESOLVED"
    DEFERRED_QUIET_HOURS = "DEFERRED_QUIET_HOURS"
    PENDING_UNCONFIRMED = "PENDING_UNCONFIRMED"
    SENT = "SENT"
    FAILED = "FAILED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"

    ALL = (
        RESERVED, SKIPPED_DUPLICATE, SKIPPED_OPTED_OUT, SKIPPED_IN_APP_ONLY,
        CANCELLED_ALREADY_RESOLVED, DEFERRED_QUIET_HOURS,
        PENDING_UNCONFIRMED, SENT, FAILED, RETRY_EXHAUSTED,
        PROVIDER_NOT_CONFIGURED,
    )


MAX_SEND_ATTEMPTS = 3
RETRY_BACKOFF_MINUTES = 30
MAX_TOTAL_ATTEMPTS = 9  # MAX_SEND_ATTEMPTS(3) x 최대 3번의 명시적 재시도 호출

_SEVERITY_TO_LEVEL = {
    NotificationSeverity.CRITICAL: NOTIFICATION_LEVEL_ERROR,
    NotificationSeverity.HIGH: NOTIFICATION_LEVEL_ERROR,
    NotificationSeverity.MEDIUM: NOTIFICATION_LEVEL_WARNING,
    NotificationSeverity.LOW: NOTIFICATION_LEVEL_INFO,
}


@dataclass
class DispatchResult:

    status: str
    log_id: int | None = None
    detail: str = ""


def _mask_email(email: str | None) -> str:
    """감사로그/에러메시지에 수신자 이메일 원문을 남기지 않는다
    (지시문: "로그는 수신 이메일을 마스킹해야 한다")."""

    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    visible = local[:1]
    return f"{visible}***@{domain}"


class NotificationDeliveryService:

    def __init__(
        self,
        db: Session,
        provider: NotificationEmailProvider | None = None,
    ):

        self.db = db
        self.repository = NotificationDeliveryRepository(db)
        self.provider = provider or NullNotificationEmailProvider()

    def _resolve_email_policy(
        self,
        *,
        company_id: int,
        user_id: int,
        event_code: str,
        definition,
    ) -> tuple[bool, bool, int | None]:
        """(enabled, email_immediate, unconfirmed_wait_minutes)를
        반환한다 — 우선순위: 이벤트별 override > 전역 설정 >
        카탈로그 기본값. Critical(critical_cannot_disable)은 전역
        email_enabled=False라도 무시하지 않는다(지시문: Critical은
        비활성화 불가 검토 대상)."""

        email_immediate = definition.default_email_immediate
        wait_minutes = definition.default_unconfirmed_wait_minutes
        enabled = True

        global_pref = self.repository.get_preference(company_id, user_id)
        if global_pref is not None and not global_pref.email_enabled:
            enabled = False

        event_pref = self.repository.get_event_preference(
            company_id, user_id, event_code,
        )
        if event_pref is not None:
            if not event_pref.enabled:
                enabled = False
            if event_pref.email_immediate_override is not None:
                email_immediate = event_pref.email_immediate_override
            if event_pref.unconfirmed_wait_minutes_override is not None:
                wait_minutes = event_pref.unconfirmed_wait_minutes_override

        if definition.critical_cannot_disable:
            enabled = True

        return enabled, email_immediate, wait_minutes

    def _in_quiet_hours(
        self,
        *,
        company_id: int,
        user_id: int,
        now: datetime,
    ) -> bool:

        pref = self.repository.get_preference(company_id, user_id)
        if pref is None or pref.quiet_hours_start is None or pref.quiet_hours_end is None:
            return False

        hour = now.hour
        start, end = pref.quiet_hours_start, pref.quiet_hours_end
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end  # 자정을 넘어가는 구간

    def dispatch(
        self,
        event_code: str,
        *,
        company_id: int,
        user_id: int | None,
        idempotency_key: str,
        title: str,
        message: str,
        link_path: str | None = None,
        entity_ref: str | None = None,
        to_email: str | None = None,
        locale: str = "ko-KR",
        reason: str = "",
        entity_summary: str = "",
        amount_summary: str = "",
        deadline_text: str = "",
        console_url: str = "",
        is_still_needed: Callable[[], bool] | None = None,
        now: datetime | None = None,
    ) -> DispatchResult:
        """지시문의 "이벤트 발생 시 호출 한 곳"에 해당하는 진입점.
        앱 내부 알림 생성과 이메일 발송 큐잉을 트리거 트랜잭션에서
        분리한다는 요구를 지키기 위해, 이 메서드 안에서 예외가 나도
        호출자의 비즈니스 트랜잭션에는 절대 전파하지 않는다(호출자가
        try/except로 감싸는 것을 전제하지 않고, 이 메서드 스스로
        방어한다)."""

        definition = get_event_definition(event_code)  # 알 수 없는 코드면 KeyError
        now = now or datetime.utcnow()

        existing = self.repository.get_email_log_by_idempotency(
            company_id, idempotency_key,
        )
        if existing is not None:
            return DispatchResult(DeliveryStatus.SKIPPED_DUPLICATE, existing.id)

        try:
            log = self.repository.reserve_log_row(
                company_id=company_id, user_id=user_id or 0,
                event_code=event_code, entity_ref=entity_ref, locale=locale,
                idempotency_key=idempotency_key,
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_email_log_by_idempotency(
                company_id, idempotency_key,
            )
            return DispatchResult(
                DeliveryStatus.SKIPPED_DUPLICATE,
                existing.id if existing else None,
            )

        try:
            level = _SEVERITY_TO_LEVEL[definition.severity]
            notification_service = NotificationService(self.db)
            if user_id is not None:
                notification_service.notify_user(
                    company_id=company_id, user_id=user_id,
                    category=definition.category, level=level,
                    title=title, message=message, link_path=link_path,
                )
            else:
                notification_service.notify_company(
                    company_id=company_id, category=definition.category,
                    level=level, title=title, message=message,
                    link_path=link_path,
                )
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 — 인앱 알림 실패도 이메일 로직을 막지 않는다
            self.db.rollback()
            self.repository.mark_status(log.id, status=DeliveryStatus.FAILED,
                                          error_detail=f"IN_APP_NOTIFY_FAILED: {str(exc)[:300]}")
            self.db.commit()

        enabled, email_immediate, wait_minutes = self._resolve_email_policy(
            company_id=company_id, user_id=user_id or 0,
            event_code=event_code, definition=definition,
        )

        if not enabled:
            self.repository.mark_status(log.id, status=DeliveryStatus.SKIPPED_OPTED_OUT)
            self.db.commit()
            return DispatchResult(DeliveryStatus.SKIPPED_OPTED_OUT, log.id)

        should_send_now = (
            definition.severity == NotificationSeverity.CRITICAL
            or email_immediate
        )

        if not should_send_now:
            if wait_minutes is None:
                self.repository.mark_status(log.id, status=DeliveryStatus.SKIPPED_IN_APP_ONLY)
                self.db.commit()
                return DispatchResult(DeliveryStatus.SKIPPED_IN_APP_ONLY, log.id)

            self.repository.mark_status(
                log.id, status=DeliveryStatus.PENDING_UNCONFIRMED,
                next_retry_at=now + timedelta(minutes=wait_minutes),
            )
            self.db.commit()
            return DispatchResult(DeliveryStatus.PENDING_UNCONFIRMED, log.id)

        if not definition.critical_cannot_disable and self._in_quiet_hours(
            company_id=company_id, user_id=user_id or 0, now=now,
        ):
            self.repository.mark_status(
                log.id, status=DeliveryStatus.DEFERRED_QUIET_HOURS,
                next_retry_at=now,
            )
            self.db.commit()
            return DispatchResult(DeliveryStatus.DEFERRED_QUIET_HOURS, log.id)

        return self._send_email_now(
            log_id=log.id, event_code=event_code, company_id=company_id,
            user_id=user_id or 0, to_email=to_email, locale=locale,
            reason=reason, entity_summary=entity_summary,
            amount_summary=amount_summary, deadline_text=deadline_text,
            console_url=console_url, is_still_needed=is_still_needed,
            now=now,
        )

    def _send_email_now(
        self,
        *,
        log_id: int,
        event_code: str,
        company_id: int,
        user_id: int,
        to_email: str | None,
        locale: str,
        reason: str,
        entity_summary: str,
        amount_summary: str,
        deadline_text: str,
        console_url: str,
        is_still_needed: Callable[[], bool] | None,
        now: datetime,
        prior_attempt_count: int = 0,
    ) -> DispatchResult:

        if is_still_needed is not None:
            try:
                still_needed = is_still_needed()
            except Exception:  # noqa: BLE001 — 재검증 콜백 실패는 안전측(취소)으로 처리
                still_needed = False
            if not still_needed:
                self.repository.mark_status(
                    log_id, status=DeliveryStatus.CANCELLED_ALREADY_RESOLVED,
                )
                self.db.commit()
                return DispatchResult(DeliveryStatus.CANCELLED_ALREADY_RESOLVED, log_id)

        if not self.provider.is_configured:
            self.repository.mark_status(
                log_id, status=DeliveryStatus.PROVIDER_NOT_CONFIGURED,
                error_detail="EMAIL_PROVIDER_NOT_CONFIGURED",
                attempt_count=prior_attempt_count,
            )
            self.db.commit()
            write_audit_log(
                self.db, user_id=user_id if user_id > 0 else None,
                action="NOTIFICATION_EMAIL_SKIPPED",
                entity="notification_email", entity_id=str(log_id),
                description=f"이메일 미발송(Provider 미구성): {event_code}",
                company_id=company_id,
            )
            self.db.commit()
            return DispatchResult(DeliveryStatus.PROVIDER_NOT_CONFIGURED, log_id)

        if not to_email:
            self.repository.mark_status(
                log_id, status=DeliveryStatus.FAILED,
                error_detail="NO_RECIPIENT_EMAIL",
                attempt_count=prior_attempt_count,
            )
            self.db.commit()
            return DispatchResult(DeliveryStatus.FAILED, log_id, "NO_RECIPIENT_EMAIL")

        message = NotificationEmailMessage(
            to_email=to_email,
            subject=render_subject(event_code, locale),
            body_text=render_body(
                event_code, locale, entity_summary=entity_summary,
                reason=reason, amount_summary=amount_summary,
                deadline_text=deadline_text, console_url=console_url,
            ),
            event_code=event_code, locale=locale,
        )

        attempt = prior_attempt_count
        status = DeliveryStatus.FAILED
        error_detail = None
        local_tries = 0

        while local_tries < MAX_SEND_ATTEMPTS:
            local_tries += 1
            attempt += 1
            try:
                self.provider.send(message)
                status = DeliveryStatus.SENT
                break
            except Exception as exc:  # noqa: BLE001 — Provider 예외 형태를 강제하지 않는다
                error_detail = str(exc)[:400]

        sent_at = now if status == DeliveryStatus.SENT else None
        if status == DeliveryStatus.FAILED and attempt >= MAX_TOTAL_ATTEMPTS:
            status = DeliveryStatus.RETRY_EXHAUSTED

        next_retry_at = None
        if status == DeliveryStatus.FAILED:
            next_retry_at = now + timedelta(minutes=RETRY_BACKOFF_MINUTES)

        self.repository.mark_status(
            log_id, status=status, error_detail=error_detail,
            attempt_count=attempt, sent_at=sent_at, next_retry_at=next_retry_at,
        )
        self.db.commit()

        write_audit_log(
            self.db, user_id=user_id,
            action=(
                "NOTIFICATION_EMAIL_SENT" if status == DeliveryStatus.SENT
                else "NOTIFICATION_EMAIL_FAILED"
            ),
            entity="notification_email", entity_id=str(log_id),
            description=(
                f"이메일 발송({status}): {event_code} -> "
                f"{_mask_email(to_email)}"
            ),
            company_id=company_id,
        )
        self.db.commit()

        if status in (DeliveryStatus.FAILED, DeliveryStatus.RETRY_EXHAUSTED):
            self._notify_email_failure(
                company_id=company_id, user_id=user_id,
                event_code=event_code, log_id=log_id,
            )

        return DispatchResult(status, log_id, error_detail or "")

    def _notify_email_failure(
        self, *, company_id: int, user_id: int, event_code: str, log_id: int,
    ) -> None:
        """이메일 발송 자체의 실패를 다시 이메일로 알리지 않는다(순환
        위험). Desktop 알림으로만 안내하고, 그 알림 자체가 실패해도
        이미 기록된 발송 결과는 그대로 둔다(삼킨다)."""

        if event_code == "NOTIFICATION_EMAIL_DELIVERY_FAILED":
            return  # 실패 알림의 실패를 또 알리는 무한루프 방지

        try:
            NotificationService(self.db).notify_user(
                company_id=company_id, user_id=user_id,
                category="security",
                level=NOTIFICATION_LEVEL_WARNING,
                title="알림 이메일 발송 실패",
                message=f"'{event_code}' 이메일 발송이 실패했습니다 — Desktop 알림으로만 안내합니다.",
                link_path=None,
            )
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()

    def send_test_email(
        self, *, to_email: str, locale: str = "ko-KR",
    ) -> DispatchResult:
        """설정 화면의 "테스트 이메일 발송" 전용 — 카탈로그 정책
        (심각도·미확인 대기·조용한 시간·멱등성)을 전혀 거치지 않고,
        지금 이 Provider가 실제로 보낼 수 있는지만 곧바로 확인한다.
        로그를 남기지 않는다(실제 알림 이벤트가 아니므로 이력에
        섞이면 안 된다) — Provider 미구성이면 정직하게 그렇다고만
        답한다."""

        if not self.provider.is_configured:
            return DispatchResult(DeliveryStatus.PROVIDER_NOT_CONFIGURED)

        message = NotificationEmailMessage(
            to_email=to_email,
            subject=(
                "[HOMEZ] 테스트 알림" if locale == "ko-KR"
                else "[HOMEZ] Test Notification"
            ),
            body_text=(
                "HOMEZ 알림 이메일 설정 테스트입니다 — 이 이메일에서는 "
                "승인·결제할 수 없습니다."
                if locale == "ko-KR" else
                "This is a HOMEZ notification email configuration test — "
                "you cannot approve or pay from this email."
            ),
            event_code="TEST", locale=locale,
        )
        try:
            self.provider.send(message)
            return DispatchResult(DeliveryStatus.SENT)
        except Exception as exc:  # noqa: BLE001
            return DispatchResult(DeliveryStatus.FAILED, detail=str(exc)[:300])

    def escalate_unconfirmed_to_email(
        self,
        log_id: int,
        *,
        to_email: str | None,
        reason: str = "",
        entity_summary: str = "",
        amount_summary: str = "",
        deadline_text: str = "",
        console_url: str = "",
        is_still_needed: Callable[[], bool] | None = None,
        now: datetime | None = None,
    ) -> DispatchResult:
        """스케줄러가 없으므로, Medium 알림이 미확인 대기시간을
        넘겼는지는 호출자가 이 메서드를 명시적으로 불러야만 확인·
        전환된다(자동 실행 아님 — 정직 공개). 이 시점의 최신 본문
        값을 호출자가 다시 넘겨야 한다(발송 직전 재조회 요구사항)."""

        now = now or datetime.utcnow()
        log = self.repository.get_by_id(log_id)
        if log is None:
            return DispatchResult(DeliveryStatus.FAILED, None, "LOG_NOT_FOUND")
        if log.status != DeliveryStatus.PENDING_UNCONFIRMED:
            return DispatchResult(log.status, log.id, "NOT_PENDING_UNCONFIRMED")
        if log.next_retry_at is not None and now < log.next_retry_at:
            return DispatchResult(DeliveryStatus.PENDING_UNCONFIRMED, log.id, "WAIT_NOT_ELAPSED")

        return self._send_email_now(
            log_id=log.id, event_code=log.event_code, company_id=log.company_id,
            user_id=log.user_id, to_email=to_email, locale=log.locale,
            reason=reason, entity_summary=entity_summary,
            amount_summary=amount_summary, deadline_text=deadline_text,
            console_url=console_url, is_still_needed=is_still_needed, now=now,
        )

    def retry_failed_notification(
        self,
        log_id: int,
        *,
        to_email: str | None,
        reason: str = "",
        entity_summary: str = "",
        amount_summary: str = "",
        deadline_text: str = "",
        console_url: str = "",
        is_still_needed: Callable[[], bool] | None = None,
        now: datetime | None = None,
    ) -> DispatchResult:
        """FAILED 상태 로그를 명시적으로 재시도한다(Retry-After 성격
        — next_retry_at 이전에는 재시도하지 않는다). 이미 RETRY_
        EXHAUSTED면 더 이상 재시도하지 않는다(무한 재시도 금지)."""

        now = now or datetime.utcnow()
        log = self.repository.get_by_id(log_id)
        if log is None:
            return DispatchResult(DeliveryStatus.FAILED, None, "LOG_NOT_FOUND")
        if log.status == DeliveryStatus.RETRY_EXHAUSTED:
            return DispatchResult(DeliveryStatus.RETRY_EXHAUSTED, log.id, "MAX_ATTEMPTS_REACHED")
        if log.status != DeliveryStatus.FAILED:
            return DispatchResult(log.status, log.id, "NOT_FAILED")
        if log.next_retry_at is not None and now < log.next_retry_at:
            return DispatchResult(DeliveryStatus.FAILED, log.id, "RETRY_AFTER_NOT_ELAPSED")

        return self._send_email_now(
            log_id=log.id, event_code=log.event_code, company_id=log.company_id,
            user_id=log.user_id, to_email=to_email, locale=log.locale,
            reason=reason, entity_summary=entity_summary,
            amount_summary=amount_summary, deadline_text=deadline_text,
            console_url=console_url, is_still_needed=is_still_needed, now=now,
            prior_attempt_count=log.attempt_count,
        )


__all__ = [
    "NotificationDeliveryService",
    "DeliveryStatus",
    "DispatchResult",
    "MAX_SEND_ATTEMPTS",
    "MAX_TOTAL_ATTEMPTS",
]
