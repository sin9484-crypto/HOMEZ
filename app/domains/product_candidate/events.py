"""
=========================================================
Homez OS

File : app/domains/product_candidate/events.py

V3 Event 계약 (Trend AI / New Product AI / ProductCandidate 병렬 연결)

이 모듈은 Event "모양"(schema)만 정의한다. 실제 발행/구독 디스패처는
기존 app/domains/event_bus/**가 비어 있는 WIP 스캐폴딩이라(0 byte),
이번 V3 범위에서는 새로 구현하지 않는다 — Event 객체를 만들어 반환하는
수준까지만 제공하고, 실제 pub/sub 배선은 V4 이전 다음 작업으로 남긴다.

AI 모듈(Trend/New Product)은 이 Event를 통해서만 ProductCandidate와
연결되며, Funding/Order/Purchase 테이블을 직접 수정하지 않는다.
=========================================================
"""

import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone


class EventType:

    PRODUCT_SOURCE_DISCOVERED = "PRODUCT_SOURCE_DISCOVERED"
    TREND_ANALYSIS_COMPLETED = "TREND_ANALYSIS_COMPLETED"
    NEW_PRODUCT_ANALYSIS_COMPLETED = "NEW_PRODUCT_ANALYSIS_COMPLETED"
    PRODUCT_CANDIDATE_CREATED = "PRODUCT_CANDIDATE_CREATED"
    # 2026-08-19 CTO 보완 지시 — 수동 후보의 "기본 정보 확인"은
    # NEW_PRODUCT_ANALYSIS_COMPLETED(실제 AI 분석 전용)와 별개 Event다.
    PRODUCT_CANDIDATE_INFO_VERIFIED = "PRODUCT_CANDIDATE_INFO_VERIFIED"
    PRODUCT_CANDIDATE_RECOMMENDED = "PRODUCT_CANDIDATE_RECOMMENDED"
    PRODUCT_CANDIDATE_APPROVED = "PRODUCT_CANDIDATE_APPROVED"
    PRODUCT_CANDIDATE_REJECTED = "PRODUCT_CANDIDATE_REJECTED"

    ALL = (
        PRODUCT_SOURCE_DISCOVERED,
        TREND_ANALYSIS_COMPLETED,
        NEW_PRODUCT_ANALYSIS_COMPLETED,
        PRODUCT_CANDIDATE_CREATED,
        PRODUCT_CANDIDATE_INFO_VERIFIED,
        PRODUCT_CANDIDATE_RECOMMENDED,
        PRODUCT_CANDIDATE_APPROVED,
        PRODUCT_CANDIDATE_REJECTED,
    )


SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class DomainEvent:

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

        if self.event_type not in EventType.ALL:
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


def build_event(
    event_type: str,
    aggregate_id: int,
    source: str,
    correlation_id: str,
    payload: dict | None = None,
) -> DomainEvent:

    return DomainEvent(
        event_type=event_type,
        aggregate_id=aggregate_id,
        source=source,
        correlation_id=correlation_id,
        payload=payload or {},
    )


__all__ = [
    "EventType",
    "DomainEvent",
    "build_event",
    "SCHEMA_VERSION",
]
