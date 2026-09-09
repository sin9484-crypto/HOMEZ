"""
=========================================================
Homez OS

File : app/domains/notification_center/model.py

Gate Y-3(2026-08-12) — 앱 내부 알림 센터. company_id로 회사 격리하고
(기존 store_connection/marketplace_listing과 동일한 원칙),
user_id가 NULL이면 그 회사 전체에 보이는 공지, 특정 값이면 해당
사용자 전용 알림이다.

읽음 상태를 두 가지로 나눈다:
- 개인 알림(user_id != NULL): Notification.is_read/read_at에 직접
  기록한다 — 읽는 사람이 그 행의 유일한 소유자이므로 안전하다.
- 회사 공지(user_id == NULL): 행 자체가 회사 전체가 공유하는 한
  행이므로, 거기에 직접 is_read를 쓰면 한 사람이 읽었을 때 회사의
  다른 모든 사용자에게도 "읽음"으로 보이는 결함이 된다. 그래서
  공지는 NotificationRead(사용자별 개별 행)로 따로 추적한다 —
  Notification.is_read/read_at은 공지 행에서는 항상 기본값(False/
  NULL)으로 남고 무시된다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

NOTIFICATION_LEVEL_INFO = "info"
NOTIFICATION_LEVEL_WARNING = "warning"
NOTIFICATION_LEVEL_ERROR = "error"

NOTIFICATION_LEVELS = frozenset(
    {
        NOTIFICATION_LEVEL_INFO,
        NOTIFICATION_LEVEL_WARNING,
        NOTIFICATION_LEVEL_ERROR,
    },
)


class Notification(Base):

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # 이 코드베이스는 FK를 쓰지 않는다(논리 참조만 유지).
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # NULL이면 회사 전체 공지, 값이 있으면 해당 사용자 전용.
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Desktop Console 내 이동 경로(예: "backups") — 없으면 알림만
    # 보여주고 이동은 하지 않는다.
    link_path: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    is_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )

    read_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:

        return (
            f"<Notification(id={self.id}, category={self.category}, "
            f"level={self.level})>"
        )


class NotificationRead(Base):
    """
    회사 공지(Notification.user_id IS NULL)의 사용자별 읽음 기록.
    한 사용자가 한 공지를 읽었다는 사실을 이 테이블의 한 행으로
    남긴다 — 다른 사용자의 읽음 상태에 전혀 영향을 주지 않는다.
    """

    __tablename__ = "notification_reads"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    notification_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    read_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:

        return (
            f"<NotificationRead(notification_id="
            f"{self.notification_id}, user_id={self.user_id})>"
        )


class NotificationEmailLog(Base):
    """Gate PT-3(2026-08-23 17차 지시) — 중앙 이벤트 카탈로그
    (event_catalog.py) 이메일 발송 이력. purchase_task 자체 20개
    이벤트의 PurchaseTaskEmailLog와는 별개 테이블이다(그 시스템은
    이미 완성돼 있어 건드리지 않는다). append-only + 재시도 상태
    추적(멱등키 UNIQUE, 재시도 횟수 제한, 다음 재시도 예정시각)."""

    __tablename__ = "notification_email_logs"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_notification_email_logs_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    event_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 이 알림이 가리키는 대상(있으면) — 예: "product_candidate:123".
    # 사전 발송 재검증 콜백을 로그에도 남겨 감사 가능하게 한다.
    entity_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="ko-KR")
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Retry-After — 다음 재시도를 시도할 수 있는 가장 이른 시각. 이
    # 저장소에는 배경 스케줄러가 없으므로(email_service.py와 동일
    # 전제) 실제 재시도는 누군가 retry_failed_notifications()를
    # 명시적으로 호출할 때만 이 값을 확인해 실행한다.
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NotificationPreference(Base):
    """Gate PT-3 — 회사+사용자 전역 알림 설정(이메일 on/off,
    조용한 시간). 회사당 사용자 1행."""

    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "user_id",
            name="uq_notification_preferences_company_user",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    # 0~23시(로컬 시각 가정) — 둘 다 있어야 유효. 조용한 시간 중에는
    # Critical(끌 수 없는 항목)만 예외적으로 즉시 발송한다.
    quiet_hours_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class NotificationEventPreference(Base):
    """Gate PT-3 — 이벤트별 override. 행이 없으면 event_catalog.py의
    기본값을 그대로 따른다(명시적으로 바꾼 이벤트만 예외 기록)."""

    __tablename__ = "notification_event_preferences"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "user_id", "event_code",
            name="uq_notification_event_preferences_company_user_event",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # None이면 카탈로그 기본값을 따른다 — 명시적으로 켜거나 끈 경우만
    # True/False로 저장한다.
    email_immediate_override: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True,
    )
    unconfirmed_wait_minutes_override: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "Notification",
    "NotificationRead",
    "NotificationEmailLog",
    "NotificationPreference",
    "NotificationEventPreference",
    "NOTIFICATION_LEVEL_INFO",
    "NOTIFICATION_LEVEL_WARNING",
    "NOTIFICATION_LEVEL_ERROR",
    "NOTIFICATION_LEVELS",
]
