"""
=========================================================
Homez OS

File : app/domains/platform_alert/hooks.py

2026-09-16 개인 베타 잔여 작업(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
10-18) — 서버 관리자 알림 8종 중 실제 감지 지점이 있는 7종을 그
지점에서 한 줄로 호출할 수 있게 감싼 얇은 헬퍼 모음
(`notification_center/operational_events.py::dispatch_operational_event`
와 같은 역할). `PlatformAlertService.dispatch_alert()` 자체가 이미
모든 예외를 삼키지만, 이 함수들도 한 번 더 감싼다 — 호출부(백업
검증, Migration 승인, 리콜 차단 등 실제 업무 로직) 안에 배선하는
코드는 예외 상황에서도 절대 원 업무를 막아서는 안 되기 때문이다.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.domains.platform_alert.constants import PlatformAlertEventCode
from app.domains.platform_alert.service import PlatformAlertService


def _dispatch(
    db: Session, event_code: str, *, title: str, message: str,
    entity_ref: str | None, idempotency_key: str,
) -> None:

    try:
        PlatformAlertService(db).dispatch_alert(
            event_code, title=title, message=message,
            entity_ref=entity_ref, idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 - 서버 관리자 알림은 항상 best-effort
        logger.warning(
            "서버 관리자 알림 훅 호출 실패(원 업무는 유지): event=%s error=%s",
            event_code, type(exc).__name__,
        )


def notify_db_integrity_check_failed(
    db: Session, *, detail: str, idempotency_key: str,
) -> None:

    _dispatch(
        db, PlatformAlertEventCode.DB_INTEGRITY_CHECK_FAILED,
        title="DB 무결성 검사 실패",
        message=f"PRAGMA integrity_check 실패가 감지됐습니다: {detail}",
        entity_ref=None, idempotency_key=idempotency_key,
    )


def notify_backup_restore_failed(
    db: Session, *, detail: str, idempotency_key: str,
) -> None:

    _dispatch(
        db, PlatformAlertEventCode.BACKUP_RESTORE_FAILED,
        title="백업/복구 리허설 실패",
        message=f"백업 또는 주간 복구 리허설이 실패했습니다: {detail}",
        entity_ref=None, idempotency_key=idempotency_key,
    )


def notify_repeated_provider_error(
    db: Session, *, detail: str, idempotency_key: str,
    entity_ref: str | None = None,
) -> None:

    _dispatch(
        db, PlatformAlertEventCode.REPEATED_PROVIDER_ERROR,
        title="매입처 실제 조회 반복 실패",
        message=f"매입처 연결의 실제 조회가 반복해서 실패하고 있습니다: {detail}",
        entity_ref=entity_ref, idempotency_key=idempotency_key,
    )


def notify_credential_store_error(
    db: Session, *, detail: str, idempotency_key: str,
    entity_ref: str | None = None,
) -> None:

    _dispatch(
        db, PlatformAlertEventCode.CREDENTIAL_STORE_ERROR,
        title="Credential 저장소 오류",
        message=f"자격증명 보안 저장소 쓰기/조회가 실패했습니다: {detail}",
        entity_ref=entity_ref, idempotency_key=idempotency_key,
    )


def notify_recall_sale_stop_detected(
    db: Session, *, detail: str, idempotency_key: str,
    entity_ref: str | None = None,
) -> None:

    _dispatch(
        db, PlatformAlertEventCode.RECALL_SALE_STOP_DETECTED,
        title="리콜/판매중지 감지",
        message=f"리콜/판매중지가 확인돼 상품이 차단됐습니다: {detail}",
        entity_ref=entity_ref, idempotency_key=idempotency_key,
    )


def notify_duplicate_order_payment_blocked(
    db: Session, *, detail: str, idempotency_key: str,
    entity_ref: str | None = None,
) -> None:

    _dispatch(
        db, PlatformAlertEventCode.DUPLICATE_ORDER_PAYMENT_BLOCKED,
        title="중복 발주/결제 차단",
        message=f"중복 발주 시도가 멱등성 제약으로 차단됐습니다: {detail}",
        entity_ref=entity_ref, idempotency_key=idempotency_key,
    )


__all__ = [
    "notify_db_integrity_check_failed",
    "notify_backup_restore_failed",
    "notify_repeated_provider_error",
    "notify_credential_store_error",
    "notify_recall_sale_stop_detected",
    "notify_duplicate_order_payment_blocked",
]
