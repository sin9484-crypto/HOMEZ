"""
=========================================================
Homez OS

File : app/domains/platform_alert/model.py

2026-09-16 개인 베타 잔여 작업(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
10-18) — "서버 관리자" 전용 알림 채널.

기존 `app/domains/notification_center`는 전부 `company_id` 기준으로
격리된다(사용자관리자 몫). 서버 관리자는 특정 회사에 속하지 않고
플랫폼 전체(모든 회사)의 운영 상태를 본다 — 그래서 이 도메인의
테이블은 의도적으로 `company_id`가 없다. 개인 베타에서는 같은
사람이 두 역할을 겸할 수 있지만, "역할별 전달기록은 분리한다"는
지시에 따라 이 테이블들은 `notification_center`의 회사/사용자
알림과 완전히 별개의 저장 공간이다 — 같은 사람에게 이메일이 두 번
갈 수 있어도(사용자관리자 알림 + 서버관리자 알림), 그건 두 역할이
논리적으로 분리돼 있다는 증거이지 결함이 아니다.

`PlatformAlertRecipient`: 알림을 받을 사람(연락처). 회사와 무관.
`PlatformAlertDeliveryLog`: 알림 생성 → 전달 시도 → 성공/실패 →
재전송까지의 전체 이력. **감사로그(write_audit_log)의 대체물이
아니다** — 감사로그는 "이런 일이 있었다"는 사실 기록이고, 이 로그는
"그 사실을 서버 관리자에게 실제로 전달하려 했는가/성공했는가"라는
별개의 질문에 답한다(지시문: "감사로그를 알림전달성공 대체물로
사용금지").
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

PLATFORM_ALERT_CONTACT_TYPE_EMAIL = "EMAIL"
PLATFORM_ALERT_CONTACT_TYPE_SMS = "SMS"

PLATFORM_ALERT_CONTACT_TYPES = frozenset(
    {PLATFORM_ALERT_CONTACT_TYPE_EMAIL, PLATFORM_ALERT_CONTACT_TYPE_SMS},
)


class PlatformAlertRecipient(Base):
    """서버 관리자 알림을 받을 연락처 — 회사에 속하지 않는다(플랫폼
    전체 스코프). 개인 베타에서는 보통 1건(운영자 본인)만 존재한다."""

    __tablename__ = "platform_alert_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    contact_type: Mapped[str] = mapped_column(String(10), nullable=False)
    contact_value: Mapped[str] = mapped_column(String(200), nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    def __repr__(self) -> str:

        return (
            f"<PlatformAlertRecipient(id={self.id}, "
            f"contact_type={self.contact_type}, active={self.active})>"
        )


class PlatformAlertDeliveryLog(Base):
    """알림 1건을 수신자 1명에게 전달하려 한 시도의 전체 이력.
    `(idempotency_key, recipient_id)` UNIQUE — 같은 사건을 같은
    수신자에게 중복 기록하지 않는다(다른 수신자에게는 각자 별도
    행이 생긴다 — 수신자별로 성공/실패가 다를 수 있으므로)."""

    __tablename__ = "platform_alert_delivery_logs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", "recipient_id",
            name="uq_platform_alert_delivery_logs_key_recipient",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    event_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    entity_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False)

    # NULL이면(recipient_id) "당시 등록된 수신자가 아무도 없었다"는
    # 의미의 단독 행 — 알림 자체가 없었던 척하지 않고, "생성은 됐으나
    # 보낼 대상이 없었다"는 사실을 그대로 남긴다.
    recipient_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(10), nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:

        return (
            f"<PlatformAlertDeliveryLog(id={self.id}, "
            f"event_code={self.event_code}, status={self.status})>"
        )


__all__ = [
    "PlatformAlertRecipient",
    "PlatformAlertDeliveryLog",
    "PLATFORM_ALERT_CONTACT_TYPE_EMAIL",
    "PLATFORM_ALERT_CONTACT_TYPE_SMS",
    "PLATFORM_ALERT_CONTACT_TYPES",
]
