"""
=========================================================
Homez OS

File : app/domains/order/policy.py

2026-08-15 V7 Gate 4 — 이전 `OrderPolicy`(NEW/PROCESSING/COMPLETED/
CANCELLED 4단계 상태머신, pre-pivot 레거시 Order 모델 대상)는 Order
도메인 전체 재설계로 대체됐다. 상태 전이 규칙은 이제
`app/domains/order/constants.py`(OrderStatus/OrderItemStatus)와
`app/domains/order/service.py`(실제 전이 로직 — collect_channel_
order/cancel_order/retry_reservation/sync_channel_status)가 함께
담당한다 — Inventory Gate 3가 별도 policy.py 없이 constants.py +
service.py만으로 상태머신을 구현한 것과 동일한 컨벤션.

저장소 전체에서 `OrderPolicy`를 import하는 곳이 없음을 확인했다
(grep 검증 완료) — 안전하게 비워둔다.
=========================================================
"""

from __future__ import annotations
