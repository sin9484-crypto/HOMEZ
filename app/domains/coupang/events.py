"""
=========================================================
Homez OS

File : app/domains/coupang/events.py

V3.1 Coupang Event 계약 — 형태(schema)만 정의한다.

app/domains/product_candidate/events.py와 동일한 철학이지만, 그 파일의
닫힌 EventType.ALL 검증을 건드리지 않기 위해 독립적으로 정의한다(기존
V3 Event 계약을 수정하지 않음). 실제 pub/sub 배선(app/core/event_bus)은
이번 범위에서 연결하지 않는다 — Event 객체 생성까지만 제공한다.
=========================================================
"""

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone


class CoupangEventType:

    DRAFT_CREATED = "COUPANG_DRAFT_CREATED"
    POLICY_VALIDATED = "COUPANG_POLICY_VALIDATED"
    POLICY_BLOCKED = "COUPANG_POLICY_BLOCKED"
    PROFIT_ESTIMATED = "COUPANG_PROFIT_ESTIMATED"
    DRY_RUN_PASSED = "COUPANG_DRY_RUN_PASSED"
    DRY_RUN_FAILED = "COUPANG_DRY_RUN_FAILED"
    APPROVED_FOR_SUBMISSION = "COUPANG_APPROVED_FOR_SUBMISSION"

    ALL = (
        DRAFT_CREATED,
        POLICY_VALIDATED,
        POLICY_BLOCKED,
        PROFIT_ESTIMATED,
        DRY_RUN_PASSED,
        DRY_RUN_FAILED,
        APPROVED_FOR_SUBMISSION,
    )


SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class CoupangDomainEvent:

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

        if self.event_type not in CoupangEventType.ALL:
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


def build_coupang_event(
    event_type: str,
    aggregate_id: int,
    source: str,
    correlation_id: str,
    payload: dict | None = None,
) -> CoupangDomainEvent:

    return CoupangDomainEvent(
        event_type=event_type,
        aggregate_id=aggregate_id,
        source=source,
        correlation_id=correlation_id,
        payload=payload or {},
    )


__all__ = [
    "CoupangEventType",
    "CoupangDomainEvent",
    "build_coupang_event",
    "SCHEMA_VERSION",
]
