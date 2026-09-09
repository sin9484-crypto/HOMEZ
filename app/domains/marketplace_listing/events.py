"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/events.py

채널별 판매 방식 선택 — Event 계약(형태만 정의). app/domains/coupang/
events.py와 동일한 철학 — 실제 pub/sub 배선(event_bus)은 이번 범위에서
연결하지 않는다. Event 객체 생성까지만 제공한다.
=========================================================
"""

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone


class MarketplaceListingEventType:

    LISTING_CREATED = "MARKETPLACE_LISTING_CREATED"
    FULFILLMENT_MODE_SELECTED = "MARKETPLACE_FULFILLMENT_MODE_SELECTED"
    ELIGIBILITY_STATE_CHANGED = "MARKETPLACE_ELIGIBILITY_STATE_CHANGED"
    SUBMISSION_ATTEMPTED = "MARKETPLACE_SUBMISSION_ATTEMPTED"

    ALL = (
        LISTING_CREATED,
        FULFILLMENT_MODE_SELECTED,
        ELIGIBILITY_STATE_CHANGED,
        SUBMISSION_ATTEMPTED,
    )


SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class MarketplaceListingDomainEvent:

    event_type: str
    aggregate_id: int
    source: str
    correlation_id: str
    payload: dict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):

        if self.event_type not in MarketplaceListingEventType.ALL:
            raise ValueError(f"알 수 없는 event_type: {self.event_type}")

    def as_dict(self) -> dict:

        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_id": self.aggregate_id,
            "occurred_at": self.occurred_at.isoformat(),
            "source": self.source,
            "correlation_id": self.correlation_id,
            "schema_version": self.schema_version,
            "payload": self.payload,
        }


def build_marketplace_listing_event(
    event_type: str,
    aggregate_id: int,
    source: str,
    correlation_id: str,
    payload: dict | None = None,
) -> MarketplaceListingDomainEvent:

    return MarketplaceListingDomainEvent(
        event_type=event_type,
        aggregate_id=aggregate_id,
        source=source,
        correlation_id=correlation_id,
        payload=payload or {},
    )


__all__ = [
    "MarketplaceListingEventType",
    "MarketplaceListingDomainEvent",
    "build_marketplace_listing_event",
    "SCHEMA_VERSION",
]
