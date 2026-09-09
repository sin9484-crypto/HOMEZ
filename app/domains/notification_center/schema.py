"""
=========================================================
Homez OS

File : app/domains/notification_center/schema.py

Gate Y-3(2026-08-12) — 알림 센터 API 요청/응답 스키마.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class NotificationResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    level: str
    title: str
    message: str
    link_path: str | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class UnifiedNotificationResponse(BaseModel):
    """Gate PT-2F — topbar 알림 벨/purchase_task 알림 패널이 공유하는
    단일 조회 계약. 기존 notifications 테이블을 그대로 읽고(새 테이블
    없음), purchase_task로 연결되는 알림만 읽기 시점에 그 작업의 현재
    상태를 재확인해 조치 필요/완료를 덧붙인다(projection — 저장하지
    않는다)."""

    id: int
    category: str
    level: str
    title: str
    message: str
    link_path: str | None
    is_read: bool
    created_at: datetime
    # 아래 3개는 이 알림이 purchase_task를 가리킬 때만 채워진다.
    purchase_task_id: int | None
    purchase_task_status: str | None
    action_status: str | None  # "ACTION_REQUIRED" | "ACTION_DONE" | None


class UnifiedNotificationListResponse(BaseModel):

    emergency_stop_active: bool
    items: list[UnifiedNotificationResponse]


class UnreadCountResponse(BaseModel):

    unread_count: int


class MarkReadResponse(BaseModel):

    id: int
    is_read: bool
    read_at: datetime | None


class MarkAllReadResponse(BaseModel):

    marked_count: int


class NotificationPreferenceResponse(BaseModel):
    """Gate PT-3(2026-08-23 17차 지시) — 전역 알림 설정."""

    email_enabled: bool
    quiet_hours_start: int | None
    quiet_hours_end: int | None


class NotificationPreferenceUpdate(BaseModel):

    email_enabled: bool | None = None
    quiet_hours_start: int | None = None
    quiet_hours_end: int | None = None
    clear_quiet_hours: bool = False


class NotificationEventPreferenceResponse(BaseModel):

    event_code: str
    enabled: bool
    email_immediate_override: bool | None
    unconfirmed_wait_minutes_override: int | None
    # 아래는 event_catalog.py 기본값 — override가 None일 때 실제로
    # 적용되는 값을 설정 화면이 바로 보여줄 수 있게 함께 내려준다.
    default_email_immediate: bool
    default_unconfirmed_wait_minutes: int | None
    critical_cannot_disable: bool
    severity: str
    category: str
    description_ko: str
    description_en: str


class NotificationEventPreferenceUpdate(BaseModel):

    enabled: bool | None = None
    email_immediate_override: bool | None = None
    unconfirmed_wait_minutes_override: int | None = None
    clear_overrides: bool = False


class NotificationCatalogEventResponse(BaseModel):
    """설정 화면이 이벤트별 토글 목록을 그릴 때 쓰는, 사용자 override가
    아직 없는 상태의 카탈로그 원본 정의(정직 공개용 wired 포함)."""

    event_code: str
    category: str
    severity: str
    critical_cannot_disable: bool
    default_email_immediate: bool
    default_unconfirmed_wait_minutes: int | None
    wired: bool
    description_ko: str
    description_en: str


class TestNotificationEmailRequest(BaseModel):

    to_email: str


class TestNotificationEmailResponse(BaseModel):

    status: str


__all__ = [
    "NotificationResponse",
    "UnifiedNotificationResponse",
    "UnifiedNotificationListResponse",
    "UnreadCountResponse",
    "MarkReadResponse",
    "MarkAllReadResponse",
    "NotificationPreferenceResponse",
    "NotificationPreferenceUpdate",
    "NotificationEventPreferenceResponse",
    "NotificationEventPreferenceUpdate",
    "NotificationCatalogEventResponse",
    "TestNotificationEmailRequest",
    "TestNotificationEmailResponse",
]
