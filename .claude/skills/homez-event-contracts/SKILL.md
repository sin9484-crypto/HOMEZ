---
name: homez-event-contracts
description: HOMEZ V3 Event 계약(app/domains/product_candidate/events.py)과 향후 pub/sub 배선 작업에서 사용한다.
---

# HOMEZ Event Contracts

## 현재 상태

`app/domains/product_candidate/events.py`에 Event **모양(schema)만**
정의되어 있다. 7종:

- PRODUCT_SOURCE_DISCOVERED
- TREND_ANALYSIS_COMPLETED
- NEW_PRODUCT_ANALYSIS_COMPLETED
- PRODUCT_CANDIDATE_CREATED
- PRODUCT_CANDIDATE_RECOMMENDED
- PRODUCT_CANDIDATE_APPROVED
- PRODUCT_CANDIDATE_REJECTED

봉투 필드: `event_id, event_type, aggregate_id, occurred_at, source,
correlation_id, schema_version, payload`.

## 미구현 (V4 이전 다음 작업)

실제 pub/sub 디스패처는 구현되어 있지 않다. `app/domains/event_bus/**`가
빈 스캐폴딩(model.py 등 0 byte)이라 이번 V3 범위에서 새로 만들지 않았다.
`ProductCandidateService`의 각 메서드는 `DomainEvent` 객체 리스트를
반환만 하고, 아무 곳에도 발행(publish)하지 않는다 — 호출자가 필요하면
직접 사용해야 한다.

## 배선 시 주의

- AI 모듈이 Funding/Order/Purchase DB를 직접 수정하는 우회로가 되지
  않도록, 배선은 Event → (Safety Layer 통과) → 명시적 실행 계층 순서를
  유지해야 한다.
- Event 자체에 금융 로직을 넣지 않는다 — 계약과 알림 목적만.
