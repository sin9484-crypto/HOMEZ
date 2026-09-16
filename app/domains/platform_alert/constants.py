"""
=========================================================
Homez OS

File : app/domains/platform_alert/constants.py

2026-09-16 개인 베타 잔여 작업(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
10-18) — 서버 관리자 알림 대상 8종의 중앙 카탈로그.
`notification_center/event_catalog.py`의 `wired` 정직 공개 관례를
그대로 따른다: `wired=True`는 이 Gate에서 실제로 그 사건을 감지·
발생시키는 코드 호출 지점이 존재한다는 뜻이고, `wired=False`는
카탈로그 정의와 배송 메커니즘은 준비됐지만 그 사건을 감지하는
코드 자체가 아직 이 저장소에 없다는 뜻이다(추측으로 트리거를
심지 않는다).

8종 중 7종은 이미 존재하는 실제 감지/차단 지점(Migration 제한모드,
DB 무결성검사, 백업복구 리허설, 매입처 반복오류, Credential 저장소
오류, 리콜 차단, 발주 중복차단)에 알림 호출만 추가했다.
"스케줄러 중지" 하나만은 이 저장소에 스케줄러 자체의 정지/누락
감지 로직(heartbeat, 마지막 실행시각 추적 등)이 전혀 없어(신규
기능 구현이 필요 — 이번 Phase 범위 밖) wired=False로 정직하게
남긴다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.notification_center.event_catalog import NotificationSeverity


class PlatformAlertEventCode:

    MIGRATION_RESTRICTED_MODE_ENTERED = "MIGRATION_RESTRICTED_MODE_ENTERED"
    DB_INTEGRITY_CHECK_FAILED = "DB_INTEGRITY_CHECK_FAILED"
    BACKUP_RESTORE_FAILED = "BACKUP_RESTORE_FAILED"
    SCHEDULER_STOPPED = "SCHEDULER_STOPPED"
    REPEATED_PROVIDER_ERROR = "REPEATED_PROVIDER_ERROR"
    CREDENTIAL_STORE_ERROR = "CREDENTIAL_STORE_ERROR"
    RECALL_SALE_STOP_DETECTED = "RECALL_SALE_STOP_DETECTED"
    DUPLICATE_ORDER_PAYMENT_BLOCKED = "DUPLICATE_ORDER_PAYMENT_BLOCKED"

    ALL = (
        MIGRATION_RESTRICTED_MODE_ENTERED,
        DB_INTEGRITY_CHECK_FAILED,
        BACKUP_RESTORE_FAILED,
        SCHEDULER_STOPPED,
        REPEATED_PROVIDER_ERROR,
        CREDENTIAL_STORE_ERROR,
        RECALL_SALE_STOP_DETECTED,
        DUPLICATE_ORDER_PAYMENT_BLOCKED,
    )


@dataclass(frozen=True)
class PlatformAlertEventDefinition:

    event_code: str
    severity: str
    wired: bool
    description_ko: str
    description_en: str


def _e(*args, **kwargs) -> PlatformAlertEventDefinition:

    return PlatformAlertEventDefinition(*args, **kwargs)


PLATFORM_ALERT_CATALOG: dict[str, PlatformAlertEventDefinition] = {
    d.event_code: d for d in [

        _e(
            PlatformAlertEventCode.MIGRATION_RESTRICTED_MODE_ENTERED,
            NotificationSeverity.CRITICAL, wired=True,
            description_ko=(
                "Migration 제한 모드가 활성화됐습니다 — 대부분의 쓰기 "
                "요청이 차단되고 있습니다."
            ),
            description_en="Migration restricted mode is active — most write requests are blocked.",
        ),
        _e(
            PlatformAlertEventCode.DB_INTEGRITY_CHECK_FAILED,
            NotificationSeverity.CRITICAL, wired=True,
            description_ko="DB 무결성 검사(PRAGMA integrity_check)가 실패했습니다.",
            description_en="A database integrity check (PRAGMA integrity_check) failed.",
        ),
        _e(
            PlatformAlertEventCode.BACKUP_RESTORE_FAILED,
            NotificationSeverity.HIGH, wired=True,
            description_ko="백업 또는 주간 복구 리허설이 실패했습니다.",
            description_en="A backup or weekly restore rehearsal failed.",
        ),
        _e(
            PlatformAlertEventCode.SCHEDULER_STOPPED,
            NotificationSeverity.HIGH, wired=False,
            description_ko=(
                "스케줄러 정지/누락을 감지하는 코드가 아직 이 "
                "저장소에 없습니다 — 이 카탈로그 항목은 준비 상태로만 "
                "남아 있습니다(추측으로 감지 로직을 심지 않았습니다)."
            ),
            description_en=(
                "No scheduler heartbeat/stop detection exists in this "
                "repository yet — this catalog entry is prepared but not "
                "wired to any real detection code."
            ),
        ),
        _e(
            PlatformAlertEventCode.REPEATED_PROVIDER_ERROR,
            NotificationSeverity.HIGH, wired=True,
            description_ko="매입처 연결의 실제 조회가 반복해서 실패하고 있습니다.",
            description_en="Real lookups against a purchase channel connection failed repeatedly.",
        ),
        _e(
            PlatformAlertEventCode.CREDENTIAL_STORE_ERROR,
            NotificationSeverity.HIGH, wired=True,
            description_ko=(
                "자격증명 보안 저장소(Windows Credential Manager) 쓰기가 "
                "실패했습니다."
            ),
            description_en="A write to the credential security store failed.",
        ),
        _e(
            PlatformAlertEventCode.RECALL_SALE_STOP_DETECTED,
            NotificationSeverity.HIGH, wired=True,
            description_ko="리콜/판매중지가 확인돼 상품이 차단됐습니다.",
            description_en="A product was blocked because a recall/stop-sale was confirmed.",
        ),
        _e(
            PlatformAlertEventCode.DUPLICATE_ORDER_PAYMENT_BLOCKED,
            NotificationSeverity.MEDIUM, wired=True,
            description_ko="중복 발주 시도가 멱등성 제약으로 차단됐습니다.",
            description_en="A duplicate purchase-order attempt was blocked by an idempotency constraint.",
        ),
    ]
}


def get_platform_alert_definition(event_code: str) -> PlatformAlertEventDefinition:

    definition = PLATFORM_ALERT_CATALOG.get(event_code)
    if definition is None:
        raise KeyError(f"알 수 없는 서버 관리자 알림 이벤트입니다: {event_code!r}")
    return definition


__all__ = [
    "PlatformAlertEventCode",
    "PlatformAlertEventDefinition",
    "PLATFORM_ALERT_CATALOG",
    "get_platform_alert_definition",
]
