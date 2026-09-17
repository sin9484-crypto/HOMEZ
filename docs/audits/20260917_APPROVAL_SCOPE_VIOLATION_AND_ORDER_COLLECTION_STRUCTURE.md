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
