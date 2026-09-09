from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class Event:
    """
    Homez 시스템 공통 이벤트 객체
    """

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    event_id: str = field(
        default_factory=lambda: str(uuid4())
    )
    created_at: datetime = field(
        default_factory=datetime.utcnow
    )


@dataclass(slots=True)
class DomainEvent(Event):
    """
    도메인 이벤트
    """
    pass


@dataclass(slots=True)
class UserEvent(DomainEvent):
    """
    사용자 이벤트
    """
    pass


@dataclass(slots=True)
class ProductEvent(DomainEvent):
    """
    상품 이벤트
    """
    pass


@dataclass(slots=True)
class OrderEvent(DomainEvent):
    """
    주문 이벤트
    """
    pass


@dataclass(slots=True)
class PaymentEvent(DomainEvent):
    """
    결제 이벤트
    """
    pass
@dataclass(slots=True)
class SystemEvent(DomainEvent):
    """
    시스템 이벤트
    """
    pass


@dataclass(slots=True)
class NotificationEvent(DomainEvent):
    """
    알림 이벤트
    """
    pass


@dataclass(slots=True)
class LogEvent(DomainEvent):
    """
    로그 이벤트
    """
    pass


__all__ = [
    "Event",
    "DomainEvent",
    "UserEvent",
    "ProductEvent",
    "OrderEvent",
    "PaymentEvent",
    "SystemEvent",
    "NotificationEvent",
    "LogEvent",
]