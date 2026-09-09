"""
=========================================================
Homez OS

File : app/domains/notification_center/router.py

Gate Y-3(2026-08-12) — 알림 센터 API. admin_guard가 아니라
get_current_user다 — 알림은 모든 역할(Manager/Staff/Viewer 포함)이
자기 몫을 볼 수 있어야 한다. company_id/user_id는 항상
current_user에서만 가져온다(요청 바디에 그런 필드가 없다 — 다른
사용자·다른 회사의 알림을 조회/읽음처리할 방법이 없다).
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.notification_center.delivery_service import (
    NotificationDeliveryService,
)
from app.domains.notification_center.email_provider import (
    NullNotificationEmailProvider,
)
from app.domains.notification_center.event_catalog import EVENT_CATALOG
from app.domains.notification_center.event_catalog import get_event_definition
from app.domains.notification_center.repository import (
    NotificationDeliveryRepository,
)
from app.domains.notification_center.schema import MarkAllReadResponse
from app.domains.notification_center.schema import MarkReadResponse
from app.domains.notification_center.schema import (
    NotificationCatalogEventResponse,
)
from app.domains.notification_center.schema import (
    NotificationEventPreferenceResponse,
)
from app.domains.notification_center.schema import (
    NotificationEventPreferenceUpdate,
)
from app.domains.notification_center.schema import (
    NotificationPreferenceResponse,
)
from app.domains.notification_center.schema import NotificationPreferenceUpdate
from app.domains.notification_center.schema import NotificationResponse
from app.domains.notification_center.schema import (
    TestNotificationEmailRequest,
)
from app.domains.notification_center.schema import (
    TestNotificationEmailResponse,
)
from app.domains.notification_center.schema import (
    UnifiedNotificationListResponse,
)
from app.domains.notification_center.schema import UnreadCountResponse
from app.domains.notification_center.service import NotificationService
from app.domains.notification_center.unified_service import (
    UnifiedNotificationService,
)
from app.domains.user.model import User


def _get_notification_email_provider():
    """Gate PT-3(2026-08-23 17차 지시): 실제 SMTP/Transactional Email
    Adapter 연결은 이번 Gate 범위 밖이다(사용자 별도 승인 전까지
    금지). 항상 Null Provider를 반환한다 — UI는 이 사실을 "발송됨"이
    아니라 "Provider 미구성"으로 정직하게 보여줘야 한다."""

    return NullNotificationEmailProvider()

router = APIRouter(
    prefix="/notifications",
    tags=["Notification"],
)


def _to_response(
    notification,
    is_read: bool,
) -> NotificationResponse:
    """
    notification.read_at은 개인 알림에만 정확하다 — 회사 공지는
    NotificationRead에 사용자별로 따로 있으므로(모델 문서 참고),
    여기서는 목록 API의 단순함을 위해 회사 공지의 read_at은 항상
    None으로 응답한다(is_read 불리언 자체는 정확하다).
    """

    return NotificationResponse(
        id=notification.id,
        category=notification.category,
        level=notification.level,
        title=notification.title,
        message=notification.message,
        link_path=notification.link_path,
        is_read=is_read,
        read_at=notification.read_at if notification.user_id else None,
        created_at=notification.created_at,
    )


@router.get(
    "",
    response_model=list[NotificationResponse],
)
def list_notifications(
    unread_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """현재 사용자 기준(개인 알림 + 소속 회사 공지)으로 알림을 조회한다."""

    service = NotificationService(db)
    rows = service.list_for_user(
        current_user.company_id,
        current_user.id,
        unread_only=unread_only,
    )

    return [
        _to_response(notification, is_read)
        for notification, is_read in rows
    ]


@router.get(
    "/unified",
    response_model=UnifiedNotificationListResponse,
)
def list_unified_notifications(
    unread_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Gate PT-2F — topbar 알림 벨/purchase_task 알림 패널이 공유하는
    단일 조회. 새 알림 저장소를 만들지 않는다(기존 notifications를
    읽기 시점에 투영)."""

    service = UnifiedNotificationService(db)
    return service.list_unified(
        current_user.company_id, current_user.id, unread_only=unread_only,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
)
def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """상단 배지용 안 읽은 알림 개수."""

    service = NotificationService(db)
    count = service.count_unread(
        current_user.company_id,
        current_user.id,
    )

    return UnreadCountResponse(unread_count=count)


@router.post(
    "/{notification_id}/read",
    response_model=MarkReadResponse,
)
def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """알림 하나를 읽음 처리한다(다시 호출해도 안전 — idempotent)."""

    service = NotificationService(db)
    read_at = service.mark_read(
        current_user.company_id,
        current_user.id,
        notification_id,
    )

    return MarkReadResponse(
        id=notification_id,
        is_read=True,
        read_at=read_at,
    )


@router.post(
    "/read-all",
    response_model=MarkAllReadResponse,
)
def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """현재 사용자 기준으로 보이는 모든 알림을 한 번에 읽음 처리한다."""

    service = NotificationService(db)
    marked = service.mark_all_read(
        current_user.company_id,
        current_user.id,
    )

    return MarkAllReadResponse(marked_count=marked)


# --------------------------------------------------
# Gate PT-3(2026-08-23 17차 지시) — 알림 설정. get_current_user만
# 요구한다(회사 관리자뿐 아니라 각 사용자가 자기 설정을 관리한다).
# company_id/user_id는 항상 current_user에서만 가져온다 — 요청
# 바디에 그런 필드가 없다(다른 사용자/회사 설정을 바꿀 방법이 없다).
# --------------------------------------------------

@router.get(
    "/preference", response_model=NotificationPreferenceResponse,
)
def get_notification_preference(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    repository = NotificationDeliveryRepository(db)
    pref = repository.get_preference(current_user.company_id, current_user.id)
    if pref is None:
        return NotificationPreferenceResponse(
            email_enabled=True, quiet_hours_start=None, quiet_hours_end=None,
        )
    return NotificationPreferenceResponse(
        email_enabled=pref.email_enabled,
        quiet_hours_start=pref.quiet_hours_start,
        quiet_hours_end=pref.quiet_hours_end,
    )


@router.put(
    "/preference", response_model=NotificationPreferenceResponse,
)
def update_notification_preference(
    data: NotificationPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    repository = NotificationDeliveryRepository(db)
    pref = repository.upsert_preference(
        current_user.company_id, current_user.id,
        email_enabled=data.email_enabled,
        quiet_hours_start=data.quiet_hours_start,
        quiet_hours_end=data.quiet_hours_end,
        clear_quiet_hours=data.clear_quiet_hours,
    )
    return NotificationPreferenceResponse(
        email_enabled=pref.email_enabled,
        quiet_hours_start=pref.quiet_hours_start,
        quiet_hours_end=pref.quiet_hours_end,
    )


@router.get(
    "/catalog", response_model=list[NotificationCatalogEventResponse],
)
def list_notification_catalog(
    current_user: User = Depends(get_current_user),
):
    """설정 화면이 이벤트별 토글 목록을 그리는 데 쓴다 — wired=False인
    이벤트도 그대로 보여준다(카탈로그에는 있지만 아직 실제로 발생시키는
    코드가 없다는 사실을 사용자에게 숨기지 않는다)."""

    return [
        NotificationCatalogEventResponse(
            event_code=d.event_code, category=d.category, severity=d.severity,
            critical_cannot_disable=d.critical_cannot_disable,
            default_email_immediate=d.default_email_immediate,
            default_unconfirmed_wait_minutes=d.default_unconfirmed_wait_minutes,
            wired=d.wired,
            description_ko=d.description_ko, description_en=d.description_en,
        )
        for d in EVENT_CATALOG.values()
    ]


@router.get(
    "/event-preferences",
    response_model=list[NotificationEventPreferenceResponse],
)
def list_notification_event_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    repository = NotificationDeliveryRepository(db)
    overrides = {
        p.event_code: p
        for p in repository.list_event_preferences(
            current_user.company_id, current_user.id,
        )
    }

    results = []
    for d in EVENT_CATALOG.values():
        override = overrides.get(d.event_code)
        results.append(NotificationEventPreferenceResponse(
            event_code=d.event_code,
            enabled=override.enabled if override else True,
            email_immediate_override=(
                override.email_immediate_override if override else None
            ),
            unconfirmed_wait_minutes_override=(
                override.unconfirmed_wait_minutes_override if override else None
            ),
            default_email_immediate=d.default_email_immediate,
            default_unconfirmed_wait_minutes=d.default_unconfirmed_wait_minutes,
            critical_cannot_disable=d.critical_cannot_disable,
            severity=d.severity, category=d.category,
            description_ko=d.description_ko, description_en=d.description_en,
        ))
    return results


@router.put(
    "/event-preferences/{event_code}",
    response_model=NotificationEventPreferenceResponse,
)
def update_notification_event_preference(
    event_code: str,
    data: NotificationEventPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    try:
        d = get_event_definition(event_code)
    except KeyError:
        raise NotFoundException(f"알 수 없는 알림 이벤트입니다: {event_code!r}")

    enabled = data.enabled
    if d.critical_cannot_disable and enabled is False:
        # 지시문: "Critical 알림은 비활성화할 수 없도록 검토" — 여기서는
        # 끄려는 시도를 조용히 무시하지 않고 즉시 거부한다.
        raise BadRequestException(
            f"{event_code}는 Critical(비활성화 불가) 알림입니다.",
        )

    repository = NotificationDeliveryRepository(db)
    pref = repository.upsert_event_preference(
        current_user.company_id, current_user.id, event_code,
        enabled=enabled,
        email_immediate_override=data.email_immediate_override,
        unconfirmed_wait_minutes_override=data.unconfirmed_wait_minutes_override,
        clear_overrides=data.clear_overrides,
    )
    return NotificationEventPreferenceResponse(
        event_code=event_code,
        enabled=pref.enabled,
        email_immediate_override=pref.email_immediate_override,
        unconfirmed_wait_minutes_override=pref.unconfirmed_wait_minutes_override,
        default_email_immediate=d.default_email_immediate,
        default_unconfirmed_wait_minutes=d.default_unconfirmed_wait_minutes,
        critical_cannot_disable=d.critical_cannot_disable,
        severity=d.severity, category=d.category,
        description_ko=d.description_ko, description_en=d.description_en,
    )


@router.post(
    "/preference/test-email", response_model=TestNotificationEmailResponse,
)
def send_test_notification_email(
    data: TestNotificationEmailRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """지시문: "테스트 이메일 발송" + "Provider 미구성은 UI에 명확히
    표시" — 실제 Provider가 없으므로 이 호출은 항상
    PROVIDER_NOT_CONFIGURED를 반환한다(성공한 것처럼 보여주지
    않는다)."""

    service = NotificationDeliveryService(
        db, provider=_get_notification_email_provider(),
    )
    result = service.send_test_email(to_email=data.to_email)
    return TestNotificationEmailResponse(status=result.status)


__all__ = ["router"]
