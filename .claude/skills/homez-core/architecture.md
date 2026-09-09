# HOMEZ Architecture Roadmap

## 버전 로드맵

| 버전 | 이름 | 핵심 |
|---|---|---|
| V1 | 물류 | 상품·주문·재고·배송 기본 Flow |
| V2 | 자금 | Funding Account/Hold, Supplier Payment 도입 |
| V2.3 | Settlement Hardening | Marketplace Settlement, 동시성·멱등성·Migration 강화 |
| V2.4 | Commerce Safety Layer | Emergency Stop, 실행 권한, 자금/수량 한도, 승인 Gate |
| V3 | Discovery AI | Trend/New Product 발견 AI, ProductCandidate, 운영자 승인 |
| V4 | Decision AI | (계획 단계, 상세 미정) |
| V5 | Autonomous Commerce OS | (계획 단계, 상세 미정) |

## 공통 원칙

- **V4까지 FastAPI 기반 Modular Monolith 유지.** 서비스 분리, 별도 배포 단위,
  메시지 브로커 도입은 하지 않는다.
- AI 프로젝트(Trend/New Product/향후 Decision AI)는 기존 금융·주문 Model을
  **직접 변경하지 않는다.** 반드시 `ProductCandidate` 계약과 Event를 통해서만 연결한다.
- 원본 데이터(수집), AI 판단(점수·근거), 운영자 결정(승인/보류/거절)은
  **세 계층으로 분리 저장**하며 서로 덮어쓰지 않는다. 각 계층은 독립적으로 감사 가능해야 한다.
- 자동화는 항상 "추천 → 운영자 승인 → 제한된 자동 실행" 순서를 따른다.
  V3까지는 운영자 승인 없이 실제 상품 등록·구매·주문을 자동 실행하지 않는다.
- Emergency Stop과 자금/수량 한도(Safety Layer)는 V3(AI 자동화)보다 먼저 구현한다.

## Phase 간 관계

```
V2.3 (자금 정합성 확정)
  → V2.4 (안전장치: Emergency Stop, 한도, 승인 Gate)
    → V3 (발견 AI: 추천만, 실행 없음)
      → V4 (판단 AI, 상세 미정)
        → V5 (자율 운영, 상세 미정)
```

하위 버전의 안전장치가 갖춰지지 않으면 상위 버전의 자동화 범위를 넓히지 않는다.
