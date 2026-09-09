---
name: homez-v3
description: HOMEZ V3 Discovery Core(ProductCandidate, Trend AI, New Product AI, 운영자 승인 API) 전체를 다루거나 V3 진행 상태를 판단할 때 사용한다.
---

# HOMEZ V3 Discovery Core

## 목표

실제 판매 자동화가 아니라 "상품 후보를 발견하고 근거와 함께 운영자에게
제안"하는 것. 실제 상품 등록·구매·주문은 V3에서 실행하지 않는다.

## Flow

```
Raw Source → Source Evidence → Trend/New Product 분석 → ProductCandidate
  → 위험·정책 검사(V2.4 Safety Layer) → 운영자 검토 → 승인/보류/거절
```

## 구성 Domain

- `app/domains/product_candidate/**` — 계약, 상태 전이, 운영자 승인 API
- `app/domains/trend_discovery/**` — Trend AI (Fixture 기반, 외부 네트워크 없음)
- `app/domains/new_product_discovery/**` — New Product AI (15일 기준, KST)

상세는 각각 `homez-product-candidate`, `homez-trend-discovery`,
`homez-new-product` Skill 참고.

## 하드닝 이력(감사 대응, 2026-07-28)

- Critical: (해당 없음, V3 자체에는 Critical 없었음 — Safety Layer/
  ProductCandidate 쪽 Critical/High는 각 Skill 참고)
- ProductCandidate: Repository commit 제거(no_commit/flux 구조),
  operator 결정과 recommend를 조건부 UPDATE + rowcount로 원자화,
  candidate_key 동시 생성 경쟁을 IntegrityError로 감지·복구
- `migrations/20260728_00_create_v24_v3_schema.sql` 신규 작성(임시 DB
  검증만, 실제 homez.db 미적용)

## 완료 조건

`homez-test-gate` Skill의 체크리스트 참고. 회귀 테스트 전부 통과 +
Critical/High 0 + 실제 DB 미변경 확인 전에는 "V3 완료"로 기록하지 않는다.
