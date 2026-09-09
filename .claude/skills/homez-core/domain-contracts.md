# HOMEZ Domain Contracts

## 물류 계열

- **Order** — 고객 주문. `orders`
- **Inventory** — 재고 상태(무재고 중개 모델에서는 공급처 재고 참조 성격이 강함)
- **Purchase** — 공급처로의 발주. `purchases` (orders.id, suppliers.id를 실제 FK로 참조)
- **Shipment** — 배송 상태 추적

책임: 상품이 고객에게 도달하기까지의 물리적 흐름만 다룬다. 자금 이동은 직접 처리하지
않고, Funding Domain에 필요한 이벤트/참조 ID만 전달한다.

## 자금 계열 (`app/domains/funding`)

- **FundingAccount** — 사업 운영자금 계정. 은행 잔액이 아니다. 은행 API 없음.
- **FundingLedger** — append-only 변경 이력. 모든 자금 변경은 반드시 Ledger를 남긴다.
- **FundingHold** — 주문별 공급 비용 예약(HELD/COMMITTED/RELEASED).
- **SupplierPayment** — 공급처 지급 확정 기록(관리자 수동 확정 MVP, 은행 송금 API 아님).

## 정산 계열 (`app/domains/settlement`)

- **MarketplaceSettlement** — 마켓 → HOMEZ 입금 예정/확정. Payment/PG와 분리.

## 금지된 의존 관계

```
Customer Payment  ≠ Marketplace Settlement
Marketplace Settlement  ≠ Supplier Payment
Funding Account  ≠ 은행 API/은행 잔액
```

- Customer Payment 코드가 Funding/Settlement 테이블을 직접 UPDATE하지 않는다.
- Marketplace Settlement가 Supplier Payment 로직을 직접 호출하지 않는다(둘 다
  FundingService를 통해서만 계좌에 반영된다).
- AI Domain(V3+)이 Funding/Order/Purchase 테이블을 직접 수정하지 않는다 —
  `ProductCandidate` 상태 전이와 Event만 발행한다.

## 향후 Domain (V3+)

- **ProductCandidate** — 상품 후보. 수집 원본(evidence) · AI 판단(score) · 운영자 결정
  (status)을 한 레코드 안에서도 필드 단위로 분리해서 보관하며, 재수집 시 기존 근거를
  덮어쓰지 않는다.
- **AutomationMode / EmergencyStop / ExecutionLimit / SafetyDecision / SafetyReason**
  (V2.4 Safety Layer) — 실제 자동 실행 여부를 게이트하는 정책 계층. 물류/자금 Domain에
  의존하되(한도 조회 등), 물류/자금 Domain은 Safety Layer에 의존하지 않는다(단방향).
