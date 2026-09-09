# HOMEZ V7 — 공통 매입 장부·상태 처리 감사

- **작성일**: 2026-09-07
- **작업 순서상 위치**: 사용자 확정 14단계 순서의 4번("공통 매입
  장부·상태 처리 완성 — 판매 주문과 매입 주문·결제·배송·환불 연결,
  중복 구매 방지, 불명확한 결과 조회·복구"). **이 문서는 그중
  "감사" 절반이다** — 실제 코드 변경(보완)은 아래 §6의 결정이
  필요해 아직 하지 않았다.
- **선행 문서**: `docs/HOMEZ_V7_INTEGRATED_PROCUREMENT_GOALS_
  20260907.md`(§4 "공통 거래 처리 기준"이 이 감사가 검증해야 할
  목표), `docs/HOMEZ_V7_PROCUREMENT_CHANNEL_SURVEY_20260907.md`
  (순서 3번).
- **조사 방법**: 서브에이전트(general-purpose, 읽기 전용, 코드 수정
  없음)에게 4개 매입 관련 도메인의 모델·서비스·테스트를 file:line
  단위로 추적하도록 위임하고, 그 결과를 이 세션이 직접 재검토·
  정리했다. 실제 `homez.db`는 읽기 전용으로 관련 테이블 행 수만
  추가 확인했다(아래 §0).
- **성격**: 이 문서는 **감사 결과의 기록**이다. 아래에 나열된
  "MISSING"은 전부 실제 코드를 근거로 한 사실이며, 추측이 아니다.
  이 문서 자체는 아무것도 고치지 않는다.

---

## 0. 실 DB 상태(참고, 읽기 전용 확인)

```
marketplace_settlements  -> 0 rows
supplier_payments        -> 0 rows
purchase_tasks           -> 0 rows
purchases                -> 0 rows
retail_purchase_orders   -> 0 rows
```

아직 실제 거래가 없는 신규 사업자 계정이라 전부 0건이다 — 이
감사는 "실제로 무엇이 쌓였는가"가 아니라 "코드가 쌓을 수 있는
구조인가"를 검증한다.

---

## 1. 정정 — 매입 관련 도메인은 3개가 아니라 4개다

착수 전 이 세션은 "source/purchase_task/retail_purchase" 3개로
추정했으나, 실제로는 **`source`와 `purchase`가 분리된 두 도메인**
이었다.

| HTTP 경로 | 도메인 | 역할 |
|---|---|---|
| `/sourcing` | `app/domains/source/` | 공급처 카탈로그 연결(상류) — `SupplierProductLink`/`CompanySupplierRelation`을 찾아 연결만 함. 새 테이블을 만들지 않고 `PurchaseService.create_purchase()`를 호출해 넘긴다. |
| `/purchases` | `app/domains/purchase/` | B2B 공급처 발주(PO) — `SupplierOrderProvider` 계약 기반(Fake/Manual/CSV만 존재, 실 네트워크 Provider 없음). |
| `/purchase-tasks` | `app/domains/purchase_task/` | **사람이 직접 실행**하는 소매 매입 — HOMEZ는 검색어·후보·마진·예산만 준비하고, 사람이 링크를 열어 로그인·결제한다. |
| `/retail-purchase` | `app/domains/retail_purchase/` | **Provider가 자동 실행**하는 소매 매입 — 서면 계약된 Provider API로 자동 발주(현재 5개 Provider 전부 미계약, `LiveInputRequiredError`). |

**`source`+`purchase`는 하나의 파이프라인**(상류→PO)이라 중복이
아니다. `purchase_task` vs `retail_purchase`는 **"누가 결제 버튼을
누르는가"만 다른, 의도적으로 분리된 두 채널**이다(각 도메인 코드
주석이 이 분리를 명시적으로 선언함 — `app/domains/purchase_task/
constants.py:7-14`, `app/domains/purchase_task/model.py:411-417`).
다만 **정책 설정 테이블(최소 마진·가격상승허용률·한도 등)이 두
도메인에 사실상 동일한 필드로 중복 정의**돼 있다(`retail_purchase/
model.py:212-259` ↔ `purchase_task/model.py:426-461`) — 통합
모델링 여지가 있다(§6 참고, 제안일 뿐 확정 아님).

---

## 2. 식별자 체인 — 목표(§4 "판매 주문·공급처 주문·결제·배송·환불 식별자 연결")와 실제 코드 대조

세 경로(Route A=`purchase`, Route B=`purchase_task`,
Route C=`retail_purchase`) 전부 확인했다. **어느 경로도 목표 문서가
요구한 전체 체인을 끝까지 연결하지 못한다.**

### Route A — `purchase`(공급처 PO) — 가장 완전하지만 실거래에 안 쓰임

```
Order → OrderItem → Purchase → 공급처 발주 → FundingHold → SupplierPayment
                                                          → 재고 복원 → OrderItem 재예약
Order → Shipment → ReturnOrder
```
전부 SOLID(실제 컬럼·코드로 연결 확인, 예: `Purchase.order_id`
`app/domains/purchase/model.py:72`, 양방향 write-back
`OrderItem.purchase_id` `purchase/service.py:174`,
`SupplierPayment.purchase_id` UNIQUE `funding/model.py:287-290`).

**끊긴 지점**:
- `Purchase ↔ ReturnOrder`: `ReturnOrder`에 `purchase_id`가 없다 —
  고객 반품이 공급처 발주까지 이어지지 않는다.
- `Order ↔ MarketplaceSettlement`: `MarketplaceSettlement.order_id`가
  **nullable + 호출자가 그냥 넘기는 값**(`settlement/schema.py:26`)
  — 실제로 이 값을 채워 넣는 생산 코드 경로가 `app/domains/order/`
  어디에도 없다(0건). 정산 대사(`difference_analysis_service.py:
  121-128`)는 `order_id is not None`일 때만 비교하도록 조용히
  건너뛴다.
- 매입비 자체가 정산에 없다: `MarketplaceSettlement`에 `purchase_id`도
  원가 필드도 없다 — 이 목표 문서 §4의 "총매입비에는 배송비·대행료·
  결제비용·환율 등을 반영"은 이 경로에서 **연결할 대상 자체가
  없다.**

### Route B — `purchase_task` — 실제 쿠팡 라이브 흐름이 쓰는 경로인데 반쪽만 연결됨

```
Order/OrderItem → PurchaseTask(SOLID, source_order_id/source_order_item_id)
                → PurchaseRecord(결제, SOLID)
                → PurchaseTaskTrackingInfo(배송, SOLID 1:1)
                → cancel/return/refund_status(도메인 내부 상태 컬럼만, SOLID-within-domain)
```

**끊긴 지점(가장 심각)**:
- **`PurchaseTask → OrderItem` 역방향 연결이 없다.** `purchase_task/
  service.py`는 `OrderItem`을 아예 import하지 않는다(전수 grep
  0건). 즉 실제로 매입이 끝나도 `OrderItem.purchase_id`는 계속
  NULL, `OrderItem.status`는 계속 `OUT_OF_STOCK`으로 남고, 이
  경로에는 재고 복원 코드 자체가 없다.
- `PurchaseTask ↔ MarketplaceSettlement`: 양방향 모두 없음.
- `PurchaseTask ↔ Purchase/Shipment`(Route A 쪽 테이블): 없음.
- 결제는 `FundingLedger(reference_type="purchase_task")`로만 남고
  `SupplierPayment`(Route A가 쓰는, UNIQUE 제약이 걸린 정식
  결제원장)는 생성되지 않는다.

### Route C — `retail_purchase` — 사실상 개점휴업 상태

- `source_order_id` 컬럼은 있지만 `create_purchase_request()`가
  실제로 `Order`를 조회하지 않는다 — API 바디로 받은 정수를 그냥
  저장한다(`retail_purchase/service.py:138-176`).
- 5개 Provider 전부 미계약이라 이 경로로 실제 매입이 자동 생성될
  일이 현재 없다(도메인 자기 자신·`main.py`·알림센터·`purchase_
  task`(상수 재사용)·콘솔 JS 외 아무도 참조하지 않음).

### 결론(감사 판정)

**목표 문서 §4가 요구한 "전체 과정에 판매 주문·공급처 주문·결제·
배송·환불 식별자를 연결한다"는 현재 어떤 경로로도 완전히 충족되지
않는다.** 가장 연결이 잘 된 경로(A)는 실거래에 쓰이지 않고,
실거래에 쓰이는 경로(B)는 주문 쪽으로 돌아오는 연결이 아예 없다.
정산(Settlement) 연결은 두 경로 모두 없다.

---

## 3. 중복 구매 방지

| 도메인 | 메커니즘 | 평가 |
|---|---|---|
| `purchase` | ①`UniqueConstraint(company_id, idempotency_key)` + IntegrityError 재확인, ②**`order_item.purchase_id is not None`이면 즉시 차단**(품목 단위 중복 발주 방지, 이 저장소에서 유일하게 이 수준의 가드), ③제출 클레임을 조건부 UPDATE로 원자화, 재시도는 FAILED+retryable+retry_after 경과 시만 | 가장 견고함 |
| `purchase_task` | ①`UniqueConstraint(company_id, idempotency_key)`, ②자동 동기화 경로는 안정적 키(`order_item:{id}:purchase_task`)라 재수집해도 중복 안 됨, ③결제 쪽은 `(company_id, shopping_mall_code, external_order_number)` + `(company_id, idempotency_key)` 이중 UNIQUE | **품목 단위 중복 작업 생성 가드가 없다** — `PurchaseTaskCreate`가 `source_order_item_id`를 검증 없이 받고, 다른 `idempotency_key`로 같은 품목에 대해 수동 `POST /purchase-tasks`를 두 번 호출하면 **작업이 2건 생성된다**(unique index 없음) |
| `retail_purchase` | `UniqueConstraint(company_id, idempotency_key)`만(완전히 호출자 제공 키) | **가장 약함** — `RetailPurchasePolicyReason.DUPLICATE_SOURCE_ORDER` 상수가 선언만 되고 코드 어디서도 쓰이지 않는 **죽은 코드**(정확히 이 가드가 빠져 있다는 증거). 서로 다른 idempotency_key로 같은 `source_order_id`에 대해 구매를 2번 만들 수 있다. |
| `source` | 자체 메커니즘 없음, `purchase`에 위임 | `purchase`의 가드를 상속하므로 문제 없음 |

---

## 4. 결과 불명확(UNCERTAIN) 처리 — 목표 문서 §4 "결과 불명확 시 재결제·대체구매 금지"

| 도메인 | 상태 |
|---|---|
| `retail_purchase` | **완전 구현** — `UNCERTAIN` 상태, 예산은 계속 보유(중복결제 방지가 예산회수보다 우선이라는 주석 명시), `NO_AUTO_RETRY`에 포함돼 재시도 자체가 `ConflictException`으로 차단됨. `HOLD_INDEFINITELY`/`AUTO_RELEASE_AFTER_TIMEOUT` 정책도 있고, 타임아웃 시에도 **예산만 반환하지 구매는 절대 재시도하지 않는다.** |
| `purchase_task` | **동등하게 완전 구현** — 결제 마감 경과, 원 주문 취소 두 경로 모두 UNCERTAIN 전이 + 재시도 차단 + 예산 유지. |
| `purchase` | **개념 자체가 없다(가장 중요한 발견).** `PurchaseSubmissionStatus`에 UNCERTAIN이 없다. **`provider.create_order()`에서 발생한 모든 예외(네트워크 타임아웃 포함 — 정확히 "결제됐는지 알 수 없는" 경우)가 그냥 `FAILED` + `retryable=True`로 하드코딩되어 자동 재시도 대상이 된다**(`purchase/service.py:524-535`). idempotency_key로 일부 완화되지만 Provider가 이를 지켜야만 안전하다 — 이 경로는 목표 문서의 "결과 불명확 시 재시도 금지" 원칙을 지키지 않는다. |
| `source` | 자체 개념 없음, `purchase`에 위임(→ 위 문제를 그대로 물려받음) |

---

## 5. 기존 테스트가 이미 검증한 것 / 안 한 것

**검증됨**: Route A의 주문↔매입↔정산 금액 비교 로직 자체
(`test_settlement_difference_analysis.py`, 다만 `order_id`를 테스트가
직접 손으로 채워 넣어 검증 — 실제로 그 값이 production에서
채워지는지는 검증하지 않음), Route A/B 각각의 idempotency
(`test_order_fulfillment_core.py`, `test_purchase_task_order_sync.py`),
UNCERTAIN 처리(`test_retail_purchase_service.py`,
`test_purchase_task_service.py`, `test_purchase_task_order_sync.py`).

**어디에도 없음**:
- `purchase_task`↔정산 또는 `purchase`↔정산 연결 테스트.
- `MarketplaceSettlement.order_id`가 실제 production 코드로
  채워진다는 테스트.
- `purchase` 도메인의 타임아웃/불명확 케이스(예외→FAILED→재시도)
  테스트.
- `PurchaseTask`가 같은 `source_order_item_id`에 대해 두 번 생성될
  수 없다는 테스트.
- `retail_purchase`의 `source_order_id` 중복 테스트.

---

## 6. 기타 발견(감사 범위 밖이지만 중요)

1. **`app/domains/payment/`, `refund/`, `shipping/`, `order_item/`,
   `tracking/`은 전부 0바이트 빈 스캐폴드**다(`__init__.py`
   포함, `docs/HOMEZ_EMPTY_SCAFFOLD_INVENTORY.md` 기존 기록과
   일치). 목표 문서 §4가 말하는 "결제"·"환불"에는 전용 Domain이
   아예 없다 — 결제는 `funding.SupplierPayment`/`purchase_task.
   PurchaseRecord`/`retail_purchase`의 각자 외부 주문 필드에 흩어져
   있고, 환불은 상태 문자열로만 존재한다.
2. `RetailPurchasePolicyReason.DUPLICATE_SOURCE_ORDER`(선언만 되고
   미사용) — 바로 위 §3에서 빠진 그 가드의 이름 그대로다.
3. `PurchaseTaskOrderSyncService.sync_order_item_quantity_changed()`
   — 자체 주석으로 "정의만 하고 자동 트리거는 없음(정직 공개)"이라
   밝힌 죽은 메서드.
4. **운영자 이메일 알림이 구조적으로 비활성 상태다** —
   `PurchaseTaskEmailProviderSetting`을 다 설정해도
   `router.py::_get_email_provider()`가 항상
   `NullPurchaseTaskEmailProvider`를 반환한다. UNCERTAIN·실패 알림이
   콘솔 안에서만 뜨고 이메일로는 절대 가지 않는다.
5. **Event Bus가 0바이트 스택**이라 도메인 간 결합이 전부 직접
   호출 + `except Exception: rollback`이다 — 매입 작업 생성 실패는
   조용히 삼켜지진 않지만(감사로그+알림은 남음), 주문 수집 자체는
   그대로 "성공"으로 보고된다. 대사는 운영자가 수동으로 트리거하는
   백필(`reconcile_recent_orders`)에 의존한다.
6. **같은 "공급처에 돈을 지급했다"는 사건에 대해 서로 대사되지
   않는 3개의 금전 경로**가 있다: `SupplierPayment`(Route A,
   UNIQUE 보장) / `FundingLedger(reference_type="purchase_task")`
   (Route B) / `FundingLedger(reference_type="retail_purchase_
   order")`(Route C). **`SupplierPayment` 기준으로 "공급처 지급액"을
   조회하는 어떤 회계 쿼리도 실거래가 쓰는 Route B 지급액을
   놓친다.**

---

## 7. 감사 판정 요약

| 목표(§4) 항목 | 상태 |
|---|---|
| 판매 주문·공급처 주문·결제·배송·환불 식별자 연결(전 과정) | **미충족** — 어느 경로도 끝까지 연결되지 않음(§2) |
| 총매입비에 배송비·대행료·결제비용·환율 반영 | **판단 불가** — 반영할 정산 연결 자체가 없음 |
| 결제 성공과 주문 성공 별도 확인 | Route B/C는 충족, **Route A는 미충족**(§4) |
| 결과 불명확 시 재시도·대체구매 금지 | Route B/C는 충족, **Route A는 미충족**(예외를 곧바로 FAILED+retryable=True로 처리) |
| 중복 구매 방지 | Route A는 견고, **Route B·C는 품목/주문 단위 가드 없음**(§3) |

**이 문서는 감사만 완료했다.** "보완"(실제 코드 수정)은 최소한
다음 중 하나 이상을 요구하며, 전부 Model/Migration 또는 여러
도메인에 걸친 설계 변경이라 CLAUDE.md의 "Model, Migration…은 별도
승인 대상" 원칙에 해당한다 — 이 감사만으로 착수하지 않는다.

---

## 8. 보완 착수를 위해 필요한 결정(사용자 결정 필요, 제안일 뿐)

1. **Route A(`purchase`)와 Route B(`purchase_task`) 중 어느 쪽을
   "정식 매입 장부"로 삼을지.** 지금처럼 둘 다 살려두고 서로
   write-back을 연결할지, 아니면 실거래가 이미 쓰는 Route B를
   중심으로 Route A의 견고한 가드(품목 단위 중복 방지, 정식
   `SupplierPayment` 원장)를 이식할지.
2. **`MarketplaceSettlement`에 매입비·`purchase_id`/
   `purchase_task_id` 연결을 추가할지** — 목표 문서 §4의 "총매입비
   반영"을 만족하려면 필수.
3. **`purchase`(Route A)에도 UNCERTAIN 상태를 추가할지** — 지금은
   예외가 곧장 재시도 가능한 FAILED가 된다.
4. **`purchase_task`에 품목 단위 중복 작업 방지(unique index 또는
   사전 조회)를 추가할지.**
5. **`retail_purchase`의 `DUPLICATE_SOURCE_ORDER` 죽은 코드를
   실제로 살릴지**, 아니면 이 도메인 자체가 개점휴업 상태이니
   후순위로 미룰지(순서 9번 "HOMEZ 자체 구매대행·가상카드 적용"
   시점까지 보류해도 되는 항목으로 보임).

이 5개 결정 없이 코드를 먼저 바꾸면 "완료 표시 후 재작업"이 될
위험이 커서, 감사 결과만 우선 보고하고 결정을 기다린다.

---

## 9. 보완 진행 현황(2026-09-07 추가 기록 — 정정 아님, 이어지는 사실)

사용자가 §8-1을 "**purchase_task 중심, purchase의 가드를 이식**"으로
확정한 뒤, §8-1(부분)·§8-4를 실제로 구현·테스트·실 DB 적용까지
완료했다. 상세 구현 내용은 `docs/HOMEZ_PROJECT_STATE.md` "2026-09-07
후속 9" 절 참고 — 여기서는 이 문서의 §7/§8 표만 최신 상태로
갱신한다.

| §8 결정 | 상태 |
|---|---|
| 1. Route A vs B 중 정식 장부 | **결정됨**(purchase_task 중심) + **부분 구현 완료** — OrderItem 역방향 연결, 실제 Shipment 생성 연동. `InventoryReservation`을 "개별 조달용 가짜 예약"(`reference_type=PURCHASE_TASK_EXTERNAL_PROCUREMENT`)으로 매개해 기존 `shipment` 도메인 파이프라인을 그대로 재사용(신규 결정, 감사 당시엔 없던 방식) |
| 2. 정산 연결 | **보류**(사용자 지시, 다음 우선순위 아님) |
| 3. `purchase`(Route A) UNCERTAIN | **보류** |
| 4. `purchase_task` 품목 단위 중복 방지 | **구현·테스트·완료** |
| 5. `retail_purchase` 중복가드 활성화 | **보류** |

**갱신된 §7 감사 판정**(Route B만 기준, 2026-09-07 보완 이후):

| 목표(§4) 항목 | Route B(purchase_task, 이제 정식 장부) 상태 |
|---|---|
| 판매 주문 → 매입 식별자 연결 | **충족**(`OrderItem.purchase_task_id`, 신규) |
| 매입 → 배송 식별자 연결 | **충족**(기존 `shipment` 도메인 실제 재사용) |
| 매입 → 정산 식별자 연결 | 여전히 **미충족**(§8-2 보류) |
| 결제 성공과 주문 성공 별도 확인 | 기존대로 **충족**(변경 없음) |
| 결과 불명확 시 재시도·대체구매 금지 | 기존대로 **충족**(변경 없음) |
| 중복 구매 방지(품목 단위) | **충족**(신규 — `SAFE_TO_RECREATE_AFTER` 가드) |

Route A(`purchase`)/Route C(`retail_purchase`)는 이번 보완에서
건드리지 않았다 — §7 표의 그 두 경로에 대한 원래 판정은 그대로
유효하다(정정 아님).
