# Phase 7B 실제 테스트 주문 검증 계획 (2026-09-17, 계획만 — 미실행)

이 문서는 계획만 기록한다. **이 문서를 작성하는 시점까지 실제 쿠팡 API 호출,
실제 주문 생성, 실제 발주·결제는 전혀 실행하지 않았다.** 실행은 사용자의 별도
명시적 승인 후에만 시작한다.

## 1. 목표

실제 쿠팡 테스트 주문 1건이 ACCEPT 상태로 감지되어 HOMEZ에 **정확히 한 번**
저장되는지(중복 없음, 완료/취소 주문 오생성 없음, 발주·결제 미호출) 실데이터로
확인한다.

## 2. 사전조건 (모두 충족돼야 실행 승인 요청)

| 항목 | 확인 방법 |
|---|---|
| 테스트용으로 등록된 실제 상품 1개 | 쿠팡 Wing에 이미 등록된, 실제로 주문 가능한 최소가 상품 |
| 주문자 | 사용자 본인 또는 사용자가 지정한 테스터 — HOMEZ가 대신 주문을 넣지 않는다 |
| 배송지 | 사용자가 사전에 승인한 자택/사무실 주소만 사용 |
| 쿠팡 연결 인증 유효 | Phase 6에서 이미 확인한 실제 `StoreConnection`(연결 끊김·만료 아님) |
| `ORDER_COLLECTION` 함수모드 | `MANUAL`(자동 tick이 끼어들지 않도록, 이번 검증은 수동 "지금 확인" 1회로만 진행) |
| EmergencyStop | 비활성 |
| Migration pending | 0건(`diagnose()`로 직전 재확인) |
| 외부 발주·결제 자동화 | 계속 차단(코드 계약상 이 경로는 애초에 발주·결제를 호출하지 않음 — 12절 상태 가드로 재확인됨) |
| 테스트 예산·취소 가능 여부 | 최소가 상품 1건 기준 실제 결제금액 사전 확인, 쿠팡 주문 취소 정책 확인 |

## 3. 실행 전 미리보기(dry-run, 외부 호출 0)

`GET /orders/collection-ops/plan`(`plan_manual_trigger()`)을 실행 직전에 다시
호출해 다음을 사용자에게 그대로 보여준다:

- 실제 호출 URL 패턴: `GET https://api-gateway.coupang.com/v2/providers/openapi/apis/api/v5/vendors/{vendorId}/ordersheets?status=ACCEPT&searchType=timeFrame&createdAtFrom=...&createdAtTo=...`
- 상태: `ACCEPT`(고정, 이번 라운드에서 구조적으로 보장됨 — 11절 상태 계약)
- 조회 시간범위: 실행 시점의 커서 창(최초 실행이면 1시간, 아니면 마지막 성공~현재+5분 overlap)
- 최대 페이지: 연결당 최소 1 ~ 최대 100(페이지 상한 도달 시 완전 실패로 처리되고 저장 없음 — 14절)
- 예상 DB 쓰기 목록: `order_channel_fulfillments`(+1), `unresolved_order_items`(SKU 미리 매핑돼 있으면 0, 아니면 +1), `orders`(+1), `order_collection_cursors`(1행 갱신), `audit_logs`(실행 기록)
- 생성 예상 객체: `Order` 1건(PENDING), `OrderItem`은 SKU 연결 후에만 생성(자동연결 매핑이 미리 있으면 즉시, 없으면 운영자가 수동 연결해야 생성됨), `OrderChannelFulfillment` 1건, `PurchaseTask`는 `OrderItem`이 만들어진 뒤 `PurchaseTaskOrderSyncService`가 붙여야 생성됨(자동 발주는 아님 — 매입작업 대기열에 올라갈 뿐)
- 사용자에게 보일 알림: Console "지금 확인" 확인창(연결 수·상태·최소~최대 GET·시간범위·발주결제 미실행 안내) — 1차 라운드에서 이미 구현·격리검증 완료(5절/16절)
- 실패·UNKNOWN 처리: 응답 상태가 ACCEPT가 아니거나 알 수 없는 값이면 신규 Order를 만들지 않고 복구검토/실패로 분류(11~12절 상태 가드) — 자동 재시도 없음, 결과를 있는 그대로 보고하고 임의로 재시도하지 않는다
- 정리 방법: 검증 후 테스트 주문은 정상적인 취소 절차(쿠팡 앱/웹)로 취소하거나 실제 배송을 받고 종료 — 자동 취소·자동 환불 기능은 호출하지 않는다(원칙 10, 영구 차단)

## 3-1. 호출 예산 확정 (2026-09-18 추가 — 연결 1개 기준)

UI 계획(`GET /orders/collection-ops/plan`)과 실제 실행(`trigger_company_now()`)은
반드시 같은 상수(`NEW_ORDER_DETECTION_STATUSES`, `DEFAULT_MAX_PAGES`)를 공유한다
(구조적으로 어긋날 수 없음 — 격리 검증에서 재확인함). 연결이 1개일 때:

| 실행 단계 | 최소 GET | 최대 GET(페이지 상한 도달 시) | 비고 |
|---|---|---|---|
| ① 최초 수집("지금 확인" 1회차) | 1 | 100 | 대부분의 실제 상황에서 1회로 끝남(테스트 주문 1건은 1페이지 안에 들어옴) |
| ② 중복 재조회("지금 확인" 2회차, 검증 항목 9) | 1 | 100 | ①과 동일한 창을 다시 조회 — 이미 존재하는 fulfillment만 갱신, 신규 GET 자체는 동일하게 발생 |
| **①+② 합계(총 승인 요청 범위)** | **2** | **200** | 실제로는 페이지 상한에 도달할 일이 없는 시나리오(테스트 주문 1건)이므로 사실상 2회로 끝날 것으로 예상하되, 승인은 이론적 최댓값(200)까지 명시한다 — 숨기지 않는다 |

- UI 계획과 실제 실행이 다른 제한을 쓰는 경로는 없다(`plan_manual_trigger()`와
  `trigger_company_now()`가 같은 `NEW_ORDER_DETECTION_STATUSES` 객체를 참조 —
  10~17절 근거).
- 연결당 페이지 상한(100)에 도달했는데 `nextToken`이 남아 있으면 그 연결의
  이번 실행은 `PAGE_LIMIT_EXCEEDED`로 **완전 실패** 처리되고, 그때까지 읽은
  내용은 저장하지 않으며 체크포인트도 전진시키지 않는다(부분 성공 아님,
  불완전 수집을 완전 성공으로 표시하지 않음 — 기존 코드 계약, 이번 라운드
  회귀로 재확인함: `test_page_limit_exceeded_does_not_advance_checkpoint`).
- 승인 범위를 초과하는 추가 실행(3회차 이상)은 하지 않는다 — 필요하면 그때
  다시 승인을 받는다.

**2026-09-18 갱신 — 아래 3-2절의 시험 전용 경로로 대체한다.** 이 절(3-1)은
일반 "지금 확인"(회사의 모든 연결을 순회, 페이지 상한 100) 기준의 이론적
최댓값(200)을 승인 범위로 잡았었다 — 안전하지만 필요 이상으로 넓었다.
이번 라운드에서 연결 1개·페이지 1장을 서버가 강제하는 별도 경로를 실제로
구현했으므로, Phase 7B 실행은 3-2절 기준(합계 최대 2회, 훨씬 좁고 정확함)을
사용한다. 이 절의 기록 자체는 삭제하지 않는다.

## 3-2. 시험 전용 호출 예산 — 구현·격리검증 완료 (2026-09-18)

`plan_test_budget_run()`/`run_test_budget_collection()`
(`app/domains/order/auto_collection_scheduler.py`) +
`GET/POST /orders/collection-ops/test-budget-plan`,`test-budget-trigger`
(`app/domains/order/router.py`) + Console UI(연결 ID 입력 +
"최초 수집 시험 실행"/"동일 범위로 재조회" 버튼)로 구현했다. 3-1절과
다른 점:

| | 일반 "지금 확인"(3-1절) | 시험 전용 경로(3-2절, 이번에 신규 구현) |
|---|---|---|
| 대상 연결 | 회사의 **모든** CONNECTED 연결 | **명시적으로 지정한 연결 1개**만(스키마에 다른 연결을 추가할 방법 자체가 없음) |
| 페이지 상한 | 연결당 최대 100(제품 기본값, 변경 안 함) | **1로 서버가 강제**(`TEST_BUDGET_MAX_PAGES=1`, 이 경로에서만 적용 — 제품 기본값은 그대로) |
| 최초수집 GET | 최소1~최대100 | **최대 1**(요청 자체가 1페이지만 요청) |
| 중복재조회 GET | 최소1~최대100(커서가 전진해 다른 구간을 볼 수 있음) | **최대 1**, 그리고 **명시적으로 고정한 시간범위**를 그대로 재사용(`window_override`) — 커서 전진과 무관하게 항상 같은 구간 |
| **합계(연결 1개, 실행 2회)** | 최소2~최대200 | **최소2~최대2**(페이지 1장이므로 이론적 최댓값 자체가 2) |
| 1페이지로 못 받으면 | `PAGE_LIMIT_EXCEEDED`, 완전 실패, 저장 없음 | 동일(같은 코드 계약 재사용) — 실제 테스트 주문 1건은 1페이지에 반드시 들어오므로 발생 가능성 사실상 없음 |
| 숨은 다른 상태·다른 연결 조회 | 없음(원래도 없었음) | 없음(구조적으로 불가능 — ACCEPT 고정, 연결 1개 고정) |

**격리 검증 결과**(임시 DB+InMemoryCredentialStore+Fake Provider, 실제
브라우저): 최초 실행 → 신규 1건(합성 주문) → 확인창에 표시된 시간범위와
실제 실행이 정확히 일치 → 동일 범위로 재조회 → 확인창에 **최초 실행과
글자 하나까지 같은 시간범위**가 다시 표시됨 → 실행 결과 신규 0/중복
1(정확히 중복 인식) → DB 직접 조회로 Order/fulfillment 각각 정확히
1행만 존재함을 확인(재조회로 늘지 않음). 두 실행 모두 서버 로그에 오류
0건(자격증명·네트워크 tripwire 무발동).

**Phase 7B 실제 승인 요청은 이 절(3-2) 기준으로 진행한다**: 연결 1개
(사용자가 명시할 실제 `store_connection_id`), ACCEPT 고정, **합계 최대
GET 2회**, 자동 재시도 0회.

## 3-3. SKU 연결 상태별 DB 변경 표 (2026-09-18, 코드로 확인)

**선행 절차 필요(미정값)** — 실제 시험 전에 다음을 준비해야 한다: 테스트
상품에 대응하는 매입상품(재고 SKU)이 HOMEZ에 이미 등록돼 있는지, 그리고
`OrderSkuResolution`(채널 SKU ↔ 재고 SKU 매핑)이 미리 저장돼 있는지. 이
값이 없으면 아래 표의 "사전 매핑 없음" 행이 적용된다 — 임의로 매핑을
미리 만들어 넣지 않는다.

`app/domains/order/order_materialization.py`(`ensure_orders`/`resolve_item`/
`auto_resolve`), `app/domains/purchase_task/order_sync_service.py`
(`sync_order`/`sync_order_item`)를 코드로 추적해 확정했다 — 연결 1개,
ACCEPT, 신규 주문 1건(품목 1개) 기준:

| 테이블 | 사전 매핑 있음(자동 SKU 연결) | 사전 매핑 없음 | 동일 주문 재조회(3-2절) |
|---|---|---|---|
| `orders` | **+1**(PENDING→RESERVED 또는 PENDING, 아래 참고) | **+1**(PENDING) | 0(필드만 갱신 — buyer/receiver/총액) |
| `order_items` | **+1**(RESERVED 또는 재고부족 시 OUT_OF_STOCK) | **0** | 0 |
| `order_channel_fulfillments` | **+1** | **+1** | 0(raw_status/ordered_at만 갱신, `duplicate_fulfillment_count` +1) |
| `unresolved_order_items` | **0**(즉시 RESOLVED로 전환) | **+1**(MAPPING_REQUIRED) | 0(이미 있으면 유지) |
| `purchase_tasks` | **0~1건** — `sync_order_item()`이 재고 이미 충분하면 `SKIPPED_INVENTORY_AVAILABLE`로 **생성 자체를 건너뛸 수 있다**(코드로 확인, "정확히 1건"이 아니다). 그 외에는 `REVIEW_REQUIRED` 또는 정상 생성으로 1건 | **0**(OrderItem 자체가 없으므로 PurchaseTask 대상이 없음 — 운영자가 나중에 수동으로 SKU를 연결해야 이 경로를 탄다) | 0 |
| `order_collection_cursors` | 1행 갱신(`last_successful_to` 전진) | 1행 갱신 | 1행 갱신(전진, 되돌리지 않음) |
| `audit_logs` | +1(`ORDER_COLLECTION_TEST_BUDGET_RUN`) | +1 | +1 |
| `order_auto_collection_states`(회사 전체 상태 위젯) | **갱신 안 함** — 시험 전용 경로(`run_test_budget_collection`)는 이 상태를 건드리지 않는다(일반 "지금 확인"과 분리) | 동일 | 동일 |

**다중 품목·배송 묶음일 때(테스트 주문이 1개 품목이 아니라면) 달라지는
것**: `order_items`/`unresolved_order_items`/`purchase_tasks`는 품목
수만큼 늘어날 수 있다(품목별로 개별 판정 — 모두 매핑돼 있어도 재고
상태에 따라 `purchase_tasks`는 품목마다 0 또는 1). `order_channel_
fulfillments`는 `shipment_box_id`(배송 묶음) 단위로 생기므로, 같은
주문이 여러 배송 묶음으로 나뉘면 그 수만큼 늘어난다(`orders`는 여전히
1건). "모든 테이블이 무조건 +1"이라고 가정하지 않는다 — 실제 시험
결과는 이 표와 대조해 보고한다.

## 3-4. 불완전 수집과 DB 저장 정책 (2026-09-18, 코드로 확인)

| 실패 유형 | 트랜잭션 경계 | 실제 동작 |
|---|---|---|
| HTTP 오류(타임아웃/네트워크/인증실패/`PAGE_LIMIT_EXCEEDED` 등, `provider.collect()` 자체가 `success=False`) | `CoupangOrderCollectionService.run()`이 `if not result.success:` 에서 **어떤 페이지도 순회하지 않고 즉시 반환** | **전부 미저장**(부분 저장 없음) + 체크포인트 미전진. "일부만 저장하고 불완전으로 표시"하는 동작은 이 경로에 없다 — 코드로 확인했다(있는 그대로 보고, 새로 만들지 않았다) |
| 정규화 오류(개별 주문 파싱 실패) | 그 레코드만 `except CoupangOrderNormalizationError`로 건너뜀, 나머지 레코드는 정상 진행 | **레코드 단위 부분 저장** — 성공한 레코드는 저장되고 실패 레코드만 `failed_order_count`에 집계, 배치 전체는 `PARTIAL`, 체크포인트 미전진(다음에 같은 구간 재시도) |
| DB/저장 오류(`ensure_orders`/`auto_resolve` 등에서 예외) | `run()`의 `except Exception: self.db.rollback(); failed += 1` — 단, `upsert_fulfillment()`/`preserve_unresolved_items()`는 **그 이전에 이미 개별 커밋**됐으므로 롤백 대상이 아니다 | 이미 커밋된 fulfillment/unresolved 행은 **유지**, 그 이후 단계(Order 생성·SKU 자동연결)만 실패로 집계, 체크포인트 미전진 |
| 알 수 없는 상태(`REJECTED_UNKNOWN_STATUS`) | 그 레코드만 저장 거부, 나머지는 정상 진행 | 레코드 단위 부분 저장, 배치는 `PARTIAL`/`failed_order_count` 반영, 체크포인트 미전진 |

**다음 조회에서 중복 생성 안 됨**: 실패해서 체크포인트가 전진하지 않으면
다음 조회는 **같은 구간을 다시** 본다 — `upsert_fulfillment()`가
`(company_id, store_connection_id, channel_order_id, shipment_box_id)`
키로 기존 행을 찾아 갱신하므로(신규 삽입이 아니라 UPDATE) 재시도가
중복을 만들지 않는다(기존 테스트로 이미 고정됨,
`test_recollection_is_idempotent`).

**운영 커서는 뒤로 이동하지 않는다** — `succeed()`는 항상 `lease.
created_at_to`(그 실행 시작 시점의 "지금")로만 전진시키고, 실패 시엔
아예 호출되지 않는다. 복구 후보(`RECOVERY_REVIEW_REQUIRED`) 보존·중복
방지 동작은 이번 라운드에서 바꾸지 않았다(2026-09-18 이전 라운드에서
이미 검증됨 — 감사 문서 19절).

## 4. Phase 7B 실행 중 검증 항목 (총 15개)

1. 테스트 주문이 실제 쿠팡 주문 목록에서 ACCEPT 상태로 나타나는지(쿠팡 앱/Wing에서 직접 확인, HOMEZ 조회 전)
2. Console "시험 실행" 화면의 "최초 수집 시험 실행" 버튼으로 **정확히 1회**만 실행(3-2절 경로 — 회사 전체를 순회하는 일반 "지금 확인"이 아니다. 승인된 횟수 초과 금지 — 1차 라운드의 승인범위 위반을 반복하지 않는다)
3. 신규 `Order` **정확히 1건** 생성 확인(company_id·channel_order_id로 조회)
4. `OrderItem`의 수량·금액·SKU가 실제 주문과 일치하는지 확인
5. `channel_order_id`(orderId)와 `OrderChannelFulfillment.shipment_box_id`(shipmentBoxId)가 분리 보존되는지 확인
6. 수취인 정보(이름·전화·주소)가 로그·보고서에 마스킹 없이 원문으로 남지 않는지 확인(코드 계약상 `assertNotIn`류 보장 — 실제 DB 조회 결과도 개수/필드존재만 보고, 값 자체는 이 보고서에 쓰지 않는다)
7. SKU 자동연결 여부 확인(미리 매핑돼 있으면 자동, 없으면 `UnresolvedOrderItem`으로 대기)
8. `PurchaseTask`가 3-3절 표와 일치하는 수만큼만 생성되는지(SKU 연결 이후 시점 — 재고 보유 시 0건일 수 있음, "정확히 1건"을 기본 가정하지 않는다)
9. 같은 주문을 **한 번 더**, "동일 범위로 재조회" 버튼으로 재조회(3-2절 — 최초 실행과 동일한 시간범위가 확인창에 그대로 표시되는지 먼저 확인한 뒤 진행, 총 2회째, 사전 승인 범위에 포함해야 함). **주의**: 상태 변경 등으로 두 번째 응답에 그 주문이 실제로 없으면(예: 이미 다음 상태로 넘어가 ACCEPT로 재조회되지 않음) 이 항목은 "검증 불충분"으로 보고하고, 승인 없이 세 번째 호출을 하지 않는다
10. 재조회 시 신규 `Order`/`PurchaseTask` 추가 생성 **0건** 확인
11. `duplicate_fulfillment_count`가 정상적으로 증가하는지 확인
12. `OrderCollectionCursor`가 정상적으로 전진하는지 확인(`last_successful_to` 갱신)
13. 검증 전 과정에서 발주·결제·상품준비중(예약) 처리가 **0건**인지 확인(`PurchaseOrderSubmissionAttempt`, `Payment` 관련 테이블 변화 없음)
14. 실행 전후 `PRAGMA integrity_check`·`foreign_key_check`로 실제 DB 무결성 확인
15. 테스트 주문의 이후 처리(정상 배송 완료 대기 또는 정상 취소 절차) 계획을 사용자와 합의하고 기록

## 5. Phase 10(다음 승인 Gate)에서 함께 보고·요청할 사항

실제 주문이나 외부 호출은 이 보고 전까지 실행하지 않는다. 다음을 한 번에 보고하고
승인을 요청한다:

- 사용할 테스트 상품(이름/가격/카테고리) — **미정, 사용자 확인 필요**
- 매입상품(재고 SKU) 사전 매핑 여부(3-3절 표의 어느 행이 적용될지) — **미정, 사용자 확인 필요**
- 사용할 실제 `store_connection_id`(연결이 여러 개면 명시적으로 지정) — **미정, 사용자 확인 필요**
- 사용자가 실제로 테스트 주문을 넣는 방법(쿠팡 앱/웹에서 직접, HOMEZ가 대신 주문하지 않음)
- 예상 결제 금액 — **미정, 사용자 확인 필요**
- 배송지 확인 방법(사전에 사용자가 승인한 주소인지)
- 쿠팡 GET 최소·최대 호출 횟수(**3-2절 기준으로 대체** — 최초수집 최대1·중복재조회 최대1, 합계 최소2·최대2)
- 페이지 상한 도달 시 처리 방침(3-4절 — 완전 실패, 부분 저장 없음, 재시도는 별도 승인 후에만)
- 실제 DB 쓰기 예상(3-3절 표 그대로 인용 — SKU 매핑 여부에 따라 갈림)
- 실패·UNKNOWN 처리 방침(자동 재시도 없음, 결과를 있는 그대로 보고, 2회째 응답에 주문이 없으면 검증 불충분으로 보고하고 3회차 임의 실행 안 함)
- 중복 재조회 방법(4절 9~11번, 3-2절 UI의 "동일 범위로 재조회" 버튼 사용)
- 테스트 주문 이후 배송·취소 처리 계획

## 6. 금지 사항 (Phase 7B 승인 이후에도 계속 적용)

- 자동 발주·자동 결제·자동 환불 실행 (원칙 10, 영구 차단)
- `AUTOMATIC` 모드 전환
- 승인된 횟수를 초과하는 추가 GET 호출(사전에 정확한 횟수를 명시하고, 실행 전 실제
  호출 구조를 코드로 재확인한 뒤에만 실행한다 — 1차 라운드의 승인범위 위반 재발 방지)
