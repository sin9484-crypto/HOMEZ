# 승인 범위 위반 기록 및 주문 자동 감지 구조 개선 (2026-09-17)

## 1. 승인 범위 위반 기록 — `APPROVAL_SCOPE_VIOLATION`

**과거 기록을 삭제하거나 정상 승인으로 소급 해석하지 않는다.** 아래는
사실 그대로의 기록이다.

| 항목 | 내용 |
|---|---|
| 분류 | `APPROVAL_SCOPE_VIOLATION`(절차 결함) |
| 승인 범위 | 쿠팡 ACCEPT 주문조회 GET **1회** |
| 실제 실행 | 쿠팡이 지원하는 6개 주문상태(ACCEPT·INSTRUCT·DEPARTURE· DELIVERING·FINAL_DELIVERY·NONE_TRACKING) 각 1회, 합계 GET **6회** |
| 원인 | 실행 전 "지금 확인"이 실제로 호출하는 `OrderMultiChannelCollectionService.run_all()` → `_run_coupang_connection()`의 내부 구현(연결 1개당 지원하는 모든 channel_status를 순회)을 확인하지 않고, "동일한 서비스 경로 사용"이라는 승인 조건만 보고 곧바로 실행했다. |
| 영향 | 승인 대비 외부 읽기(GET) **5회 초과**. 전부 읽기 전용(GET)이었고 실패 없이 전부 200으로 성공했다. |
| 외부 쓰기·발주·결제·개인정보 노출 | **없음** — POST/PUT/DELETE 0건, 발주·결제·상품등록 0건, 자격증명·주문 원문 로그 노출 0건(코드 계약상 애초에 노출될 수 없는 경로만 사용). |
| DB 변경 범위 | `audit_logs` +2행(감사로그, 실행 시도 기록), `order_collection_cursors` +6행(체크포인트, 6개 상태 각각), `order_auto_collection_states` 1행 갱신(실행상태) — 이 세 범주(감사로그/실행상태/체크포인트) 밖의 어떤 테이블도 변경되지 않았다(`Order`/`OrderItem`/`OrderChannelFulfillment`/`PurchaseTask`/`PurchaseOrderSubmissionAttempt` 전부 0건 변화, `StoreConnection`의 `connection_status`/`verified_at`도 불변 — 전부 실행 직후 DB 직접 재조회로 확인함). |
| 재발 방지 | 이번 라운드에서 **구조적으로** 해소했다(2절) — 승인 단위를 매번 사람이 다시 계산하지 않아도 되도록, "신규 주문 감지" 경로 자체를 ACCEPT 하나로 좁혔다. 추가로 "GET 횟수"와 "수동 수집 주기 1회"를 분리해 표시하는 dry-run 계획(4절)을 만들어, 승인자가 실행 전에 정확한 횟수를 직접 볼 수 있게 했다. |

## 2. 6개 상태 조회 목적 감사 (코드 근거)

실제 코드(`app/domains/order/order_materialization.py::CoupangOrderMaterializationService.ensure_orders()`, `app/domains/order/coupang_normalizer.py`, `app/domains/order/adapters/coupang_collection.py`)를 읽고 확인한 사실:

- **ACCEPT만 신규 주문 유입에 의미가 있는지**: `createdAtFrom/To`는 쿠팡 API 계약상 **주문 생성 시각** 기준 필터다(공식 문서, Phase 6 조사에서 이미 확인). 정상적인 주문 처리(접수→준비→출고→배송→완료)는 수일이 걸리므로, "최근 N분/시간에 생성됐고 이미 DELIVERING/FINAL_DELIVERY까지 도달한" 주문은 정상적인 흐름에서는 사실상 존재하지 않는다. 즉 좁은 시간창으로 5개 비-ACCEPT 상태를 조회해도 "방금 생성된, 아직 접수 단계인" 주문만 실질적으로 잡히는 ACCEPT와 달리, 나머지 5개는 구조적으로 거의 항상 빈 결과만 돌려준다 — 실제로 이번 Phase 6/7A Live 검증에서도 6개 상태 전부 0건이었다.
- **INSTRUCT 이후 상태가 기존 주문 상태 동기화용인지**: 코드상 의도는 그렇게 보이지만(변수/도메인 이름), 위와 같은 이유로 `createdAt` 필터를 쓰는 한 "이미 알고 있는 주문의 상태가 방금 바뀌었는지" 확인하는 용도로는 구조적으로 작동하지 않는다 — 주문의 **생성** 시각이 아니라 **상태 변경** 시각으로 필터링해야 그 목적을 달성할 수 있는데, 이 API는 그런 필드를 제공하지 않는다(공식 문서에 `updatedAtFrom/To` 같은 파라미터가 없음, Phase 6 조사 결과).
- **FINAL_DELIVERY/NONE_TRACKING을 신규 주문 생성 대상으로 처리하는지**: **그렇다.** `ensure_orders()`는 `channel_order_id` 기준으로 기존 `Order`가 없으면 어떤 상태로 수집됐든 무조건 새로 만든다(상태별 분기 없음, 122-123행 근처). 즉 이론적으로 FINAL_DELIVERY 상태로 처음 발견된 주문(아주 드문 edge case — 예: 매우 오래 방치된 뒤 처음 수집되는 경우)도 신규 `Order`를 만들 수 있었다.
- **같은 orderId가 여러 상태에서 반환될 때 중복 처리되는지**: **중복 생성되지 않는다.** `ensure_orders()`는 `(company_id, channel_code, channel_order_id)`로 기존 `Order`를 먼저 조회하고, 있으면 갱신만 한다 — 같은 tick 안에서 여러 상태 조회가 같은 주문을 돌려줘도 두 번째부터는 UPDATE 경로를 탄다(코드로 확인, 별도 테스트 불필요 — 기존 구조).
- **초기 커서가 없을 때 과거 주문을 어느 범위까지 가져오는지**: `INITIAL_LOOKBACK = timedelta(hours=1)`(`app/domains/order/collection_service.py`) — **무제한이 아니다.** 최초 실행은 항상 "지금부터 1시간 전"까지만 조회한다. 이번 Phase 7A 실제 Live 검증에서도 6개 커서 전부 첫 실행이었고 1시간 창으로 조회됐다(0건).
- **상태 전환 중 주문이 누락되거나 중복 생성될 가능성**: 중복 생성 가능성은 위에서 확인한 대로 낮다. **누락 가능성은 실재한다** — `createdAt` 필터 특성상, 5분 간격의 좁은 창으로는 "그 사이 상태가 바뀐" 주문을 구조적으로 포착할 수 없다(위 설명과 동일한 근거). 이는 이번 라운드에서 새로 만든 문제가 아니라 API 계약 자체의 제약이며, 3절의 구조 분리로 "신규 감지"만 책임지도록 범위를 좁혀 최소한 그 부분의 정확성은 확보했다 — "상태 동기화"는 별도 메커니즘이 필요하다는 것을 정직하게 남긴다(3절).
- **매 5분마다 계정당 6회 호출하는 것이 필요한지**: **필요하지 않다** — 위 분석 근거로 이번에 ACCEPT 1회로 좁혔다(4절 이하 구현).
- **쿠팡 호출 제한과 일일 호출량에 미치는 영향**: 공식 문서에서 구체적 rate limit 수치를 확인하지 못했다(`app/domains/store_connection/adapters/coupang_signing.py`의 기존 주석에도 동일하게 기록돼 있음 — "정확한 호출 제한 수치를 공식 문서에서 찾지 못했다"). 다만 계정당 하루 288회 tick(5분 간격) 기준으로 계산하면: 기존 6상태 방식은 하루 최대 1,728회, 이번 수정(ACCEPT 1회)은 하루 최대 288회로 **83% 감소**한다.

## 3. 구조 분리 — 실제로 적용한 범위

| 역할 | 이번 라운드 처리 |
|---|---|
| 신규 주문 감지(ACCEPT 중심) | **구현 완료** — `app/domains/order/auto_collection_scheduler.py`의 `NEW_ORDER_DETECTION_STATUSES = frozenset({"ACCEPT"})`를 자동 tick과 수동 "지금 확인" 양쪽에 적용 |
| 기존 주문 상태 동기화 | **구현하지 않음(범위 밖, 정직 공개)** — 2절에서 확인했듯 `createdAt` 기반 필터로는 이 목적을 달성할 수 없어, 이번 라운드에서 새로 설계하지 않았다. 향후 별도 작업이 필요하다(예: 이미 저장된 미완료 주문 목록을 순회하며 개별 주문 조회 API로 상태만 갱신하는 방식 — 이 저장소에 그런 개별 조회 엔드포인트가 있는지는 확인하지 않았다). |
| 배송·완료 추적 | **구현하지 않음(범위 밖, 정직 공개)** — 동일한 이유. 저빈도(예: 일 1회) 별도 작업으로 다뤄야 한다는 방향만 기록한다. |

기존 "전체 통합 수집" 수동 버튼(`POST /orders/collect/all`)은 의도적으로 건드리지 않았다 — 그 버튼은 사용자가 직접 누르는 전체 점검 도구이고, 원래도 6개 상태 전부를 보여주는 것이 그 버튼의 존재 이유이므로 범위를 좁히지 않는다.

## 4. 최초 실행 안전장치 — 확인 결과

| 요구 | 상태 | 근거 |
|---|---|---|
| 과거 전체 주문 무제한 수집 안 함 | **이미 충족(기존 코드)** | `INITIAL_LOOKBACK = 1시간`(`collection_service.py`) — 이번 라운드에서 바꾸지 않음, 처음부터 이랬음 |
| 명시된 lookback 범위만 조회 | **충족** | 위와 동일 |
| 예상 수집 건수 미리보기 | **부분 충족** — 시간창·최대 호출횟수는 실제 호출 없이 미리 보여준다(4절 아래 `plan_manual_trigger()`). **실제 몇 건이 있을지(건수)는 미리 알 수 없다** — 그걸 알려면 실제 API를 호출해야 하므로, dry-run의 "외부 호출 0건" 원칙과 충돌한다. 이 한계를 정직하게 남긴다. |
| 사용자 승인 전 Order/PurchaseTask 저장 안 함 | **기존 구조 그대로 — 새로 만들지 않음** | 계획(`plan_manual_trigger()`)은 아무것도 저장하지 않는다. 다만 "지금 확인" 자체를 누르는 것이 곧 실행 승인이라는 기존 UX는 바꾸지 않았다(확인창이 이제 계획을 보여주고 그 위에서 한 번 더 확인받는 것으로 강화됨, 5절) |
| 완료·취소된 과거 주문을 신규 매입작업으로 만들지 않음 | **ACCEPT 전용 범위로 실질적으로 해소** | ACCEPT는 정의상 "방금 접수된" 주문만 의미가 있어, 완료/취소된 과거 주문이 이 경로로 처음 들어올 가능성이 구조적으로 사라졌다 |
| 커서 초기화와 실제 수집 구분 | **부분 충족(기존 구조)** | 커서 "생성"(`_get_or_create`, IDLE 상태)과 "성공적 전진"(`succeed()`)은 이미 분리돼 있다(기존 코드). 다만 "커서만 만들고 수집은 안 하는" 별도 API는 없다 — `acquire()` 호출 자체가 곧 조회 시도다. 새로 만들지 않았다(범위 밖) |
| 초기화 실패 시 커서가 전진하지 않음 | **이미 충족(기존 코드, 기존 테스트로 검증됨)** | `fail()`은 `last_successful_to`를 건드리지 않는다 — `tests/test_order_collection_cursor_service.py`가 이미 이 계약을 고정하고 있다(이번에 다시 검증하지 않음, 회귀로 이미 보증됨) |

## 5. 수동 실행 UI 정정 — 구현 완료

"지금 확인" 버튼을 누르면 실제 실행 전에 `GET /orders/collection-ops/plan`(외부 호출·DB 쓰기 없음)을 먼저 불러 확인창에 표시한다:
- 조회할 판매계정 수
- 조회할 주문상태 목록(ACCEPT)
- 예상 외부 GET 최대 횟수
- 조회 시간 범위
- "신규 주문만 HOMEZ 주문·매입작업 대기열에 기록됩니다" 문구
- "발주·결제·상품등록은 이 실행에서 절대 호출되지 않습니다" 문구

격리 브라우저(데스크톱)로 실제 클릭→확인창 표시→확인→실행까지 전체 흐름을 검증했다(6절).

## 6. 실행계획 API — 구현 완료

`app/domains/order/auto_collection_scheduler.py::plan_manual_trigger()`(외부 호출·DB 쓰기 0) →
`GET /orders/collection-ops/plan`(`app/domains/order/router.py`) → 콘솔 확인창이 이 결과를 그대로 표시한다. **계획과 실제 실행이 같은 함수(`NEW_ORDER_DETECTION_STATUSES`, 같은 연결 목록)를 공유**하므로 설명과 실행이 구조적으로 어긋날 수 없다.

## 7. 제한모드 초기화 결함 — 원인과 수정

- **제품 서버에서는 초기화가 보장되는가**: **보장된다.** `app/main.py`의 FastAPI lifespan이 `refresh_restricted_mode_state()`를 스케줄러 등록(`register_all_jobs()`)보다 먼저 호출한다(152행 vs 162~165행, 코드로 확인). 실제 서버·스케줄러 Job은 이번 결함의 영향을 받지 않는다.
- **CLI·작업 스크립트·스케줄러에서 같은 오탐이 가능한가**: 스케줄러 Job은 서버와 같은 프로세스 안에서 돌아가므로 영향받지 않는다. **CLI·일회성 스크립트(이번에 실제로 발생한 경우)는 영향받는다** — 서버 lifespan을 거치지 않고 `is_restricted_mode()`를 직접 부르면 프로세스 전역 캐시가 비어 있어 예전에는 무조건 True를 반환했다.
- **수정 여부**: **수정 완료.** `app/core/migration_restricted_mode.py::is_restricted_mode()`가 캐시 미계산 시 그 자리에서 `refresh_restricted_mode_state()`(읽기 전용 sqlite3 진단만 사용, ORM/`SessionLocal` 없음 — Phase 5 기존 불변식 그대로 유지)를 직접 호출해 실제 상태를 확인한다.
- **fail-closed 원칙 유지 여부**: **유지됨.** 진단 자체가 실패하면(예: checksum 불일치, DB 파일 손상) 여전히 True(제한 모드)를 반환한다 — "안 재봤으니 무조건 제한"이 "직접 확인했더니 정말 pending이 있어서 제한"으로 더 정확해졌을 뿐, 안전 기본값 자체는 바뀌지 않았다.
- **실제 pending Migration 우회 여부**: **우회하지 않는다.** 진짜 pending이 있는 상태에서 캐시가 비어 있으면, 지연 진단도 정확히 True를 반환한다(신규 테스트 `test_uncomputed_cache_with_genuinely_pending_migration_still_restricted`로 고정).

## 8. 테스트 결과

| 파일 | 결과 |
|---|---|
| `tests/test_order_auto_collection_scheduler.py` | 19/19 OK(기존 13 + 신규 6: ACCEPT 전용 범위 2건, dry-run 계획 4건) |
| `tests/test_order_multi_channel_collection_service.py` | 8/8 OK(기존 6 + 신규 2: `statuses` 파라미터 범위 축소·미지원 값 거부) |
| `tests/test_migration_restricted_mode.py` | 46/46 OK(기존 42 + 신규 4: 캐시 미계산+실제 pending 0건→False, 캐시 채움 확인, 진짜 pending 시 여전히 True, 진단 실패 시 fail-closed) |
| 집중 회귀(위 3개 + 관련 10개 파일) | 223/223 OK |
| 전체 회귀(4,464건 — 공용 수집 서비스 계약 변경으로 실행) | **실행 중, 결과는 완료 보고에 반영** |

## 8-1. 지시된 검증 시나리오 ↔ 실제 테스트 매핑

| 시나리오 | 커버 방식 | 근거 |
|---|---|---|
| ACCEPT 상태만 자동/수동 트리거가 조회함 | 신규 테스트 | `tests/test_order_auto_collection_scheduler.py::test_tick_only_queries_accept_status_not_all_six`, `test_manual_trigger_only_queries_accept_status_not_all_six` |
| `run_all()`의 `statuses` 파라미터가 실제로 범위를 좁힘 | 신규 테스트 | `tests/test_order_multi_channel_collection_service.py::test_statuses_param_narrows_to_accept_only` |
| 지원하지 않는 상태값을 넘기면 거부됨(오용 방지) | 신규 테스트 | 위 파일 `test_unknown_status_in_statuses_param_is_rejected` |
| 기존 "전체 통합 수집" 버튼은 여전히 6개 상태 전부 조회(하위호환) | 기존 테스트(불변 확인) | 위 파일 `test_single_connection_runs_every_allowed_status`(변경 없이 그대로 통과) |
| dry-run 계획이 실제 실행과 동일한 연결/상태 집합을 보고함 | 신규 테스트 | `test_order_auto_collection_scheduler.py::test_plan_matches_actual_trigger_call_count`, `test_plan_reports_accept_only_and_no_external_calls` |
| 연결된 판매계정 수에 비례해 계획 규모가 커짐 | 신규 테스트 | 위 파일 `test_plan_scales_with_number_of_connected_connections` |
| 연결 해제된 판매계정은 계획에서 제외됨 | 신규 테스트 | 위 파일 `test_plan_excludes_disconnected_connections` |
| 초기 커서 없음 → 무제한이 아니라 1시간으로 제한된 창 | 기존 테스트(이미 보증됨, 재검증 불필요) | `tests/test_order_collection_cursor_service.py::test_first_run_uses_bounded_initial_window` — "과거 완료 주문 존재" 여부와 무관하게 창 자체가 항상 1시간으로 고정되므로 구조적으로 커버됨 |
| 실패 시 커서가 전진하지 않음(재시도 시 같은 구간 재조회) | 기존 테스트(이미 보증됨) | 위 파일 `test_failure_does_not_advance_cursor`, `coupang_order_collection_service.py::test_provider_failure_does_not_advance_position` |
| 동일 주문이 여러 상태 조회에서 중복 생성되지 않음 | **신규 테스트(이번에 추가한 진짜 공백)** | `tests/test_coupang_order_collection_service.py::test_same_order_across_different_statuses_does_not_duplicate` — ACCEPT로 먼저 수집 후 INSTRUCT로 같은 `channel_order_id`를 재수집해도 `Order`/`OrderChannelFulfillment`가 1건만 유지됨을 확인 |
| 같은 상태를 재수집해도 중복 생성 안 됨 | 기존 테스트 | `test_coupang_order_collection_service.py::test_recollection_is_idempotent` |
| 제한모드 오탐 시 실제 pending이 없으면 차단하지 않음 | 신규 테스트 | `tests/test_migration_restricted_mode.py::test_uncomputed_cache_with_zero_pending_migrations_is_not_restricted` |
| 제한모드 캐시가 지연 진단 후 공유 캐시에 채워짐 | 신규 테스트 | 위 파일 `test_lazy_diagnosis_populates_shared_cache_for_subsequent_calls` |
| 실제 pending Migration이 있으면 여전히 차단(우회 금지) | 신규 테스트 | 위 파일 `test_uncomputed_cache_with_genuinely_pending_migration_still_restricted` |
| 진단 자체가 실패하면 여전히 fail-closed | 신규 테스트 | 위 파일 `test_diagnosis_error_during_lazy_check_still_fails_closed` |

위 표에서 "동일 주문의 다중 상태 중복" 시나리오가 실제 공백이었음을 확인해 이번에 새로
추가했다(8절 시점에는 `test_recollection_is_idempotent`가 같은 상태를 두 번 조회하는
경우만 다뤘고, 서로 **다른** 상태로 같은 주문이 조회되는 경우는 다루지 않았다).

## 9. Git

절차 위반 기록(이 문서)과 제품 코드 수정은 별도 커밋으로 분리해 push한다(완료 보고에 커밋 해시 기록).

---

# 2차 후속 — High 결함 수정 및 상태 계약 확정 (2026-09-17, 같은 날 후속 라운드)

1차 라운드(위 1~9절) 커밋(`9671279`, `e8442bf`) 이후 "Phase 7A 사후 감사 및 Phase 7B 전
안전성 보완"의 연장으로, 실제 코드 감사에서 **완료 상태 과거 주문이 신규 Order로 잘못
생성될 수 있는 구조적 결함(High)**과 **dry-run 최대 호출 수 축소 표시 결함**을 확인해
이번 라운드에서 수정했다.

## 10. 확정된 결함

| # | 분류 | 내용 |
|---|---|---|
| 1 | High | `CoupangOrderMaterializationService.ensure_orders()`가 저장 직전 상태 검증을 전혀 하지 않아, FINAL_DELIVERY 등 완료 상태로 **처음** 조회된 주문(예: "전체 통합 수집" 6개 상태 버튼 경로)이 신규 `Order`(PENDING)로 생성될 수 있었다. 호출 파라미터(`channel_status`)만 신뢰하고 응답 레코드 자체의 상태를 검증하지 않는 구조였다. |
| 2 | Medium | `plan_manual_trigger()`의 `max_external_get_calls=len(plans)`가 페이지네이션(연결×상태당 최대 `DEFAULT_MAX_PAGES=100`회)을 반영하지 못해 dry-run이 실제 최대 호출 수를 축소 표시했다(코드 주석 스스로 "페이지 추가 시 더 늘 수 있음"이라 인정하고도 계산하지 않았음). |
| 3 | 연쇄결함(설계 중 발견) | 결함 1을 상태 가드로 막으면 `OrderChannelFulfillment.order_id`가 `NULL`인 채로 남는 레코드가 생기는데, 기존 `auto_resolve()`가 그런 레코드까지 SKU 자동연결 대상에 포함시켜 `resolve_item()`의 `ConflictException`으로 배치 전체를 실패시킬 수 있었다(결함 1을 고치는 과정에서 새로 드러난 잠재적 회귀 — 같은 커밋에서 함께 수정). |

## 11. 상태 계약 — 확정

`app/domains/order/order_materialization.py`에 단일 출처로 정의(값은 기존
`adapters/coupang_collection.py::ALLOWED_STATUSES`에서 파생돼 드리프트 불가):

- **A. 신규 주문 자동 생성 허용**: `NEW_ORDER_ELIGIBLE_STATUSES = {"ACCEPT"}`
- **B. 기존 주문 상태 확인에만 허용**: `EXISTING_ORDER_ONLY_STATUSES = ALLOWED_STATUSES - {"ACCEPT"}`(INSTRUCT/DEPARTURE/DELIVERING/FINAL_DELIVERY/NONE_TRACKING) — 취소·반품·교환은 애초에 이 수집 경로가 조회하는 상태 목록(`ALLOWED_STATUSES`)에 없다(Coupang 발주서 상태 API 자체가 취소/반품 상태를 반환하지 않음, 2절에서 이미 확인).
- **C. 과거 주문 복구 후보**: `OrderMaterializationOutcome.RECOVERY_REVIEW_REQUIRED` — HOMEZ에 Order가 없는데 ACCEPT 이외 상태로 처음 발견된 주문. 자동 저장하지 않고 `RecoveryReviewCandidate`(마스킹된 식별자·발견상태·발견시각·사유·승인필요여부)로만 보고한다.
- **판정 우선순위**: 그룹의 `raw_status`(응답 레코드 자체)가 `ALLOWED_STATUSES`에 전혀 없으면 `REJECTED_UNKNOWN_STATUS`(fail-closed, 실패로 집계돼 체크포인트 미전진 → 다음 재시도에서 재확인) → 이미 HOMEZ에 Order가 있으면 상태 무관하게 `UPDATED`(신규 생성 아님) → 없고 ACCEPT면 `CREATED` → 없고 ACCEPT가 아니면 `RECOVERY_REVIEW_REQUIRED`(실패 아님, 체크포인트 정상 전진).

## 12. materialization 저장 경계 수정 — 구현 완료

`ensure_orders()`가 `OrderMaterializationResult(orders, recovery_candidates,
rejected_unknown_status_count)`를 반환하도록 재작성했다:

- 호출자가 요청한 `channel_status` 파라미터가 아니라 **응답 레코드 자체의 `raw_status`**로
  판단한다(요구사항 6/11 — 요청 파라미터를 신뢰하지 않는다).
- FINAL_DELIVERY 등 완료 상태에서 신규 Order/OrderItem/PurchaseTask를 만들지 않는다
  (`auto_resolve()`도 `order_id IS NOT NULL`인 fulfillment만 대상으로 하도록 같이 고쳐,
  복구후보 레코드가 SKU 자동연결·PurchaseTask 생성 경로에 들어가지 않게 막았다).
- 알 수 없는 상태는 fail-closed(저장하지 않고 실패로 집계), 이미 존재하는 Order는 상태와
  무관하게 정상 갱신(신규 생성이 아님)된다.
- 개인정보가 아닌 `channel_order_id`도 감사로그·복구후보 표시용으로는 뒤 4자리만 남기고
  마스킹한다(`_masked_channel_order_id()`).

## 13. 과거 주문 복구 계약 — 이번 라운드 범위(정직 공개)

**구현한 것**: 분류 계약(`RecoveryReviewCandidate`)과 집계(`CoupangCollectionRunResult.
recovery_review_count`/`recovery_candidates`, `MultiChannelCollectionEntry.
recovery_review_count`)뿐이다. 자동으로 Order/PurchaseTask를 만들지 않고, 발주·결제도
호출하지 않는다 — 전부 구조로 보장된다(3절/12절).

**구현하지 않은 것(후속 작업)**: 사용자가 복구 후보를 화면에서 미리보고 선택적으로
저장하는 UI·API, 완료/취소 주문 기본 선택 해제, 복구 후 중복 방지 로직. 이번 라운드는
"잘못 생성되지 않는다"는 안전장치까지만 구현했고, "복구를 실제로 실행하는" 기능은
`RECOVERY_REVIEW_REQUIRED` 판정 하나로 남아 있다 — `HISTORICAL_ORDER_RECOVERY_COMPLETE`
판정은 아직 내리지 않는다.

## 14. dry-run 호출량 결함 수정 — 구현 완료

`OrderCollectionTriggerPlan`에 `active_connection_count`/`max_pages_per_connection`/
`min_external_get_calls`/`max_external_get_calls`/`retry_count`/`page_limit_note`를
추가했다. `max_external_get_calls = len(plans) * DEFAULT_MAX_PAGES`로 페이지 상한까지
반영한 진짜 최대치를 숨기지 않는다. `DEFAULT_MAX_PAGES=100`은 "평소 정상 페이지 수"가
아니라 nextToken 무한루프를 막는 안전 상한이라는 설명을 코드 주석으로 남겼고, 공식
쿠팡 문서에서 정확한 호출 빈도 제한 수치를 찾지 못했으므로 근거 없이 임의로 줄이지
않았다(기존 결정 유지, `adapters/coupang_collection.py:DEFAULT_MAX_PAGES` 참고).
`CoupangOrderCollectionProvider.collect()`는 페이지 상한 도달 시 이미
`PAGE_LIMIT_EXCEEDED`로 완전 실패 처리하고(부분 성공 아님), `run()`의 공통 `if not
result.success:` 경로가 체크포인트를 전진시키지 않으므로 이 부분은 기존 코드가 이미
요구사항을 충족하고 있었다(신규 서비스-레벨 회귀 테스트로 고정만 했다).

## 15. 기존 주문 상태 동기화 — 재확인, 변경 없음

코드 재조사 결과 이번 라운드에서도 변경하지 않았다(`NOT_IMPLEMENTED` 그대로):
`POST /orders/{id}/sync-channel-status`는 `get_order_channel_status_adapter()`가
채널코드와 무관하게 항상 `FakeOrderChannelStatusAdapter`를 반환해 실제 쿠팡 상태를
가져오지 못한다(`app/domains/order/adapters/fake_provider.py`) — 이 엔드포인트를 실제
쿠팡 연동 완료로 판정하지 않는다.

## 16. 테스트 결과

| 범위 | 결과 |
|---|---|
| 정규화(`test_coupang_order_normalizer.py`) | 신규 3건(상태 누락/빈 문자열 fail-closed, 다중 품목 정규화) 포함 전체 통과 |
| materialization(`test_coupang_order_materialization.py`) | 신규 8건(5개 비-ACCEPT 상태 자동생성 금지, 복구후보 마스킹, 기존주문 정상갱신, 미지원상태 fail-closed, 다중품목 합산, 혼합배치, 고아 fulfillment 자동연결 금지) 포함 전체 통과 |
| 수집서비스(`test_coupang_order_collection_service.py`) | 신규 4건(복구후보 집계+체크포인트 전진, 페이지상한 체크포인트 미전진, 미지원상태 fail-closed, 요청ACCEPT-응답상태다름 차단) 포함 전체 통과 |
| 멀티채널(`test_order_multi_channel_collection_service.py`) | 신규 1건(recovery_review_count 전파) 포함 전체 통과 |
| dry-run(`test_order_auto_collection_scheduler.py`) | 최소/최대 GET 분리 검증으로 기존 4건 갱신, 전체 통과 |
| Phase 8 광역 집중회귀 | order/purchase_task/fulfillment(core·concurrency·e2e)·v7 통합e2e 등 343/343 통과(무실패) |
| Phase 9 전체 회귀 | `venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` — **4,503개, 실패 0, 오류 0, skip 7, 6,702.613초, exit code 0.** 실제 `homez.db` 마지막 수정시각이 회귀 시작 전이라 회귀 중 실제 DB 무접촉 확인. |
| UI 격리 검증 | 격리 스크래치 DB(`odo_ui_verify.db`, 서버 포트 8960)+실제 브라우저로 데스크톱·모바일(390×844) 모두 확인 — 확인창이 "최소 1회~최대 100회", 연결 1개, ACCEPT, 페이지상한 안내, 발주·결제 미실행 안내를 정확히 표시했고 실제 계획 API 응답과 100% 일치했다. 실행(확인) 클릭 시 실제 Windows Credential Manager에 없는 가짜 credential_reference라 `provider.collect()` 도달 전 단계(자격증명 조회)에서 400으로 안전하게 실패해 실제 쿠팡 API를 호출하지 않았다. 모바일에서 다이얼로그 폭 343px(뷰포트 390px 안에 완전히 들어감), 페이지 가로 스크롤 없음, 콘솔 오류 없음을 확인했다. |

## 17. 판정

- `ORDER_COLLECTION_ACCEPT_ONLY_ISOLATED_VERIFIED` — 이번 라운드 격리 검증 기준으로 유지.
- `ORDER_MATERIALIZATION_STATUS_GUARD_VERIFIED` — 12절/16절 근거로 신규 판정.
- `ORDER_RECOVERY_REVIEW_CONTRACT_IMPLEMENTED` — 13절 범위(분류·집계만) 기준으로 신규 판정. `HISTORICAL_ORDER_RECOVERY_COMPLETE`는 아직 판정하지 않는다.
- `ORDER_STATUS_SYNC_NOT_IMPLEMENTED` — 15절 근거로 유지.
- 실제 주문을 전혀 사용하지 않았으므로 `ORDER_COLLECTION_LIVE_VERIFIED`, `V7_PERSONAL_BETA_FLOW_VERIFIED`는 이번에도 판정하지 않는다 — Phase 7B는 별도 승인 후에만 진행한다.

---

# 3차 후속 — Phase 7B 진입 전 잔여 4항목 확인·수정 (2026-09-18)

16절의 "UI 격리 검증" 행이 실제로는 (a) 실제 Windows Credential Manager에 임의의
`credential_reference`로 조회를 시도해 400을 받은 것과 (b) 확인창 표시값 검증만
했을 뿐, **진짜 성공 경로(주문이 실제로 저장되는 경로)는 검증하지 않았다**는
지적을 받아 이번 라운드에서 다시 했다. 아래 18~21절은 그 지적에 대한 정정과
추가 검증이다 — 16절의 원문은 삭제하지 않고 그대로 둔다.

## 18. 격리 브라우저 성공 흐름 검증 (항목 2, 재검증)

**방법 변경.** 이전 라운드(16절)는 실제 `WindowsCredentialStore`에 존재하지
않는 `credential_reference`를 조회해 400을 받는 방식이었다 — 이는 "실제
Credential Manager를 실제로 조회했다"는 뜻이라 격리 원칙에 어긋난다는 지적을
받아들인다. 이번에는 FastAPI의 공식 오버라이드 지점
(`get_order_credential_store()`/`get_coupang_order_provider_factory()` —
`app/domains/order/router.py`, 후자는 docstring에 "FastAPI dependency override
seam for isolated tests"라고 이미 명시돼 있었다)을 사용해 `InMemoryCredentialStore`
+ Fake Provider를 실제 서비스 경로(`app.main:app`, 라우터 코드는 무수정)에
주입했다. 추가로 다음 두 가지 tripwire를 넣어 오버라이드가 실패하면 그 자리에서
즉시 예외가 나게 했다(둘 다 이번 검증 스크립트에만 있고, 실제로 한 번도
발동하지 않았다 — 서버 로그에 에러 0건):
- `requests.Session.request`/`requests.get`/`requests.post`를 막아 Fake Provider를
  벗어난 실제 네트워크 호출을 차단.
- `WindowsCredentialStore.read`/`.exists`를 이번 검증 전용 target_name
  (`isolated-fake-cred`)에 한해 막아, 오버라이드가 빠진 채 실제 Credential
  Manager로 새는 경로를 차단(다른 무관한 도메인의 실제 사용은 건드리지 않음 —
  전역 `HOMEZ_FORBID_REAL_CREDENTIAL_STORE=1`은 media_asset 도메인의 무관한
  Naver 자격증명 조회 때문에 앱 자체가 뜨지 못해 사용하지 않았다).
- `FakeCoupangProvider.__init__()`에 자체 assertion을 넣어, 주입된
  자격증명이 아닌 다른 값이 들어오면 즉시 실패하게 했다.

**검증 결과(격리 스크래치 DB `isolated_success_flow.db`, 서버 포트 8970,
데스크톱+모바일 390×844 모두):**

| 시나리오 | 확인창 표시 | 실행 결과(현재 상태) | DB 대조 |
|---|---|---|---|
| 빈 결과 | 최소 1회~최대 100회, 연결 1개 | **성공** | orders=0, fulfillments=0, cursor 전진 |
| 합성 신규 주문 1건(ACCEPT) | 동일 | **성공**, 신규 1/중복 0/미연결 1 | orders=1(PENDING, channel_order_id 일치), fulfillments=1(order_id 연결됨), order_items=0(SKU 미매핑), purchase_tasks=0 |
| 같은 주문 재조회 | 동일 | **성공**, 신규 0/중복 1 | orders/fulfillments 행 수 불변(중복 생성 없음) |
| 자격증명 없음(두 번째 연결 추가, `isolated-missing-cred`는 InMemoryCredentialStore에도 저장하지 않음) | 연결 2개로 확인창 갱신(최소 2회~최대 200회) | **연결 확인 실패로 건너뜀** — "400: 저장된 쿠팡 연결 정보를 찾을 수 없습니다" | 실패 경로 검증으로만 기록(원칙 7) — `InMemoryCredentialStore.read()`가 던진 `CredentialNotFoundError`이며, 실제 Credential Manager는 조회하지 않았다 |

Order·품목·배송 묶음(`shipment_box_id`)·PurchaseTask·중복 카운터·체크포인트를
전부 DB 직접 조회로 대조했다(위 표). 확인 후 이번 검증용으로 직접 시작한
서버(serverId 기준)만 종료했다.

## 19. 복구 후보 보존과 체크포인트 감사 (항목 3)

**계약 보강.** `RecoveryReviewCandidate`에 `company_id`/`store_connection_id`/
`masked_shipment_box_id`를 추가해(기존 `masked_channel_order_id`/
`observed_status`/`observed_at`/`reason`과 합쳐) 회사·판매계정·외부 주문·배송
묶음·상태·사유를 전부 다시 식별할 수 있게 했다. 새 테이블은 만들지 않았다 —
이미 커밋되어 있는 `OrderChannelFulfillment` 행(`order_id IS NULL` +
`raw_status IN EXISTING_ORDER_ONLY_STATUSES`)에서 그대로 다시 계산하는 읽기
전용 함수 `list_recovery_review_candidates()`를 추가했다(신규 Migration
없음 — 기존 스키마로 충분함을 확인).

**검증한 것:**
- 후보가 **응답에만 존재하지 않고 DB에 지속적으로 보존됨** — 격리 유닛
  테스트(`test_recovery_candidate_is_reidentifiable_via_dedicated_query`)와
  격리 브라우저(18절 두 번째 시나리오를 FINAL_DELIVERY 상태로 바꿔 재실행)
  양쪽에서, 별도 프로세스(완전히 새로운 Python 인터프리터로 같은 스크래치
  DB 파일만 열어 조회 — 실행 중이던 서버 프로세스와 무관)로 다시 조회해도
  같은 후보가 그대로 나옴을 확인했다.
- **프로세스 재시작에 해당하는 상황**(`test_recovery_candidate_survives_fresh_session_simulating_restart`
  — DB 세션을 완전히 닫고 새 세션으로 재조회)에서도 사라지지 않음.
- **재조회 시 중복 방지**(`test_recovery_candidate_requery_does_not_duplicate`) —
  같은 외부 주문을 두 번 수집해도 fulfillment 행이 늘지 않고 후보도 여전히
  1건.
- **후보 보존과 체크포인트 전진 사이의 실패·롤백**
  (`test_recovery_candidate_fulfillment_persists_despite_later_unrelated_failure`) —
  후보의 fulfillment 행은 `CoupangOrderCollectionPersistence.upsert_fulfillment()`가
  독립된 트랜잭션으로 이미 커밋한 것이라, 이후 단계(`auto_resolve()` 등)에서
  무관한 예외가 나도 사라지지 않는다. 반대로 그 무관한 예외는 `run()`의
  기존 계약대로 배치 전체를 실패로 집계해 체크포인트를 전진시키지 않는다
  (기존 코드, 이번에 새로 만들지 않음) — 즉 "후보는 항상 보존되고, 체크포인트는
  그 배치가 진짜로 끝까지 성공했을 때만 전진한다"가 성립한다.
- 회사·판매계정별로 범위를 좁혀 조회 가능함
  (`test_recovery_candidate_query_scoped_by_company_and_connection`).
- 복구 후보에서 `Order`/`PurchaseTask`·발주·결제가 자동 생성되지 않음은
  12절/16절에 이미 검증돼 있고, 이번 라운드(18절 표)에서도 재확인했다.
- 실제 복구 실행 UI(사용자가 후보를 보고 선택 저장하는 화면)는 이번에도
  범위를 넓히지 않았다 — `list_recovery_review_candidates()`는 읽기 전용
  헬퍼일 뿐, 이를 노출하는 API/화면은 아직 없다.

## 20. 무접촉 보고 정정 (항목 4)

**정정 대상: 1절/9절/16절 및 이전 대화의 최종 보고에서 "`homez.db` 마지막
수정시각이 회귀 시작 전이므로 무접촉을 확인했다"고 쓴 부분.** 이 표현은
부정확했다 — mtime 불변은 **쓰기(write)가 없었다는 증거**일 뿐, 읽기를
포함한 접근이 전혀 없었다는 증거가 아니다. 원문은 삭제하지 않고 이 절에서
사실대로 정정한다.

- **더 강한 근거를 재조사해 찾았다**: `app/core/config.py::_default_database_url()`은
  `DATABASE_URL` 환경변수가 설정되지 않으면 **실제 `homez.db`의 절대경로**로
  기본값을 계산한다(개발 모드 기준 저장소 루트). Phase 1/Phase 9 전체
  회귀는 `DATABASE_URL`을 명시적으로 설정하지 않고 실행했으므로, 이론상
  전역 엔진(`app/database/session.py:engine`)이 이 기본값에 바인딩될 수
  있었다.
- 그러나 실제로 확인해 보니: (a) `tests/` 전체에서 `TestClient(app)` 패턴을
  쓰는 파일이 0개다(전부 도메인 서비스에 자체 임시 SQLite 엔진을 직접
  주입하는 방식), (b) 전역 `app.database.session.SessionLocal`을 직접
  import하는 테스트 파일은 1개(`tests/support/coupang_order_collection_e2e_server.py`)
  뿐이고, 그 파일 자신이 import보다 먼저 `DATABASE_URL`을 스크래치 경로로
  덮어쓰며, 게다가 이 파일은 어떤 `test_*.py`에서도 참조되지 않아 이번
  discover 패턴에 애초에 포함되지 않는다, (c) 실제 서버/uvicorn을 다루는
  `tests/test_homez_desktop.py`는 `get_homez_db_path`/`get_engine_db_path`를
  자체적으로 mock하여 가짜 DB 경로를 쓴다는 것이 자기 docstring에도 명시돼
  있다. 이 세 가지가 실제 `homez.db` 무접촉의 **구조적 근거**다(mtime보다
  훨씬 강한 근거).
- **그럼에도 "확인 불충분"으로 남기는 부분**: 200여 개 테스트 파일 전부를
  한 줄씩 읽어 감사하지는 않았다 — 위 (a)~(c)는 grep 기반 정적 조사이며,
  이번 세션에서 발견하지 못한 예외적 경로가 전혀 없다고 100% 단언할 수는
  없다. 과거(Phase 1/Phase 9) 실행 당시의 프로세스 수준 파일 핸들을
  사후에 직접 증명할 방법은 없으므로, "완전히 무접촉이었다"는 최종 결론이
  아니라 "구조적으로 무접촉일 가능성이 매우 높고, mtime 불변과도 모순되지
  않는다"까지만 주장한다.
- **이번 라운드(18절)의 격리 브라우저 검증은 이 문제가 없다** — `DATABASE_URL`을
  스크래치 경로로 명시적으로 설정했고, 자격증명·Provider를 의존성 오버라이드로
  교체했으며, 네트워크·자격증명 tripwire가 실제로 발동하지 않았음을 서버
  로그로 확인했다 — "연결 대상(스크래치 DB 경로)·주입된 저장소(InMemory/Fake)·
  차단 근거(tripwire 무발동)"로 설명 가능한, 더 신뢰할 수 있는 무접촉
  증거다.
- 이 절 어디에도 비밀값·개인정보 원문은 기록하지 않았다.

## 21. 테스트 집계 정정 (항목 5)

**정정 대상: 이전 최종 보고의 "4,486 → 4,503, 신규 16건 + 원인불명 1건"에
가까운 서술.** 재조사 결과를 사실대로 남긴다(전체 회귀를 다시 실행하지
않고, `unittest.TestLoader().discover()`로 테스트를 **수집만** 하고 실행하지
않는 방식과, `git worktree`로 만든 격리 사본에서의 동일 수집으로 확인했다 —
둘 다 이번 세션에서 새로 발견한 결함을 코드로 수정하기 전, 순수 조사
단계였다):

| 지점 | discover() 수집 개수 |
|---|---|
| `e8442bf`(Phase 1 회귀 대상 커밋, 격리 `git worktree`에서 재확인) | **4,487** |
| 현재 HEAD(`63fc3f4` 이후) | **4,503** |

`git show 63fc3f4`의 전체 diff에서 `+    def test_` 줄을 직접 센 결과도
정확히 **16개**였고(파일별: normalizer 3·materialization 8·collection_service
4·multi_channel 1), 제거되거나 이름이 바뀐 테스트는 0개다. 4,487 + 16 =
4,503 — **정확히 들어맞는다.**

즉 실제 코드 변경으로 추가된 테스트는 정확히 16개이고(이전 보고와 동일),
**"17건" 자체가 애초에 잘못된 계산이었다** — Phase 1 전체 회귀 실행 로그가
보고한 "4,486개"라는 숫자가, 같은 커밋(e8442bf)의 코드를 기준으로 한
순수 discover() 수집 결과(4,487개)보다 1개 적었을 뿐이다. 이 1개 차이의
원인(어떤 테스트가 그 특정 실행에서 실행되지 않았는지)은 그 실행 프로세스가
이미 종료돼 사후에 재현·특정할 수 없으므로 **확인 불충분**으로 남긴다 —
코드에는 어떤 문제도 없다(위 diff 대조로 확정).

## 22. 판정(3차 후속 갱신)

17절의 판정을 대체하지 않고 다음을 추가로 확정한다:

- `ORDER_COLLECTION_ACCEPT_ONLY_ISOLATED_VERIFIED` — 18절의 진짜 성공 경로
  격리 검증(빈 결과·신규 1건·재조회 중복없음, DB 대조 포함)으로 **근거를
  보강**했다(이전에는 실패 경로만 있었다).
- `ORDER_RECOVERY_REVIEW_CONTRACT_IMPLEMENTED` — 19절의 지속성·재조회·
  재시작·격리 브라우저 검증으로 근거를 보강했다.
- 실제 신규 주문을 실제 쿠팡 API로 수집한 적은 여전히 없으므로
  `ORDER_COLLECTION_LIVE_VERIFIED`는 부여하지 않는다.
