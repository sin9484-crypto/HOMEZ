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

---

# 4차 후속 — D1·D2 최종 정정 및 D3 실제 거래 준비 (2026-09-18, 같은 날 후속)

## 23. 이전 보고 정정 — "실행 중 수정해도 회귀는 유효하다"는 단정 철회

직전 라운드에서 "이미 실행 중인 전체 회귀 프로세스를 중간에 코드를 고쳐도,
파이썬은 시작 시점에 메모리에 적재한 코드로만 계속 실행되므로 그 회귀
결과 자체는 유효하다"고 보고했다 — **이 단정은 철회한다.** 정확히는:

- `sys.modules`에 이미 적재된 모듈은 다시 읽지 않는다는 것은 맞다.
- 그러나 전체 회귀 안에는 **지연 import**(함수 안에서 `from ... import ...`,
  이번 세션에서 제가 작성한 코드 다수가 이 패턴을 쓴다), **테스트가 직접
  띄우는 별도 서브프로세스**(`test_homez_desktop.py` 등 실제 uvicorn
  스레드/프로세스를 시작하는 테스트), **파일을 다시 읽는 경로**(Migration
  SQL 파일처럼 매번 디스크에서 다시 읽는 대상)가 섞여 있어, 회귀 도중
  파일을 바꾸면 **같은 1회 실행 안에서 앞부분은 이전 코드, 뒷부분(또는
  새로 뜬 서브프로세스)은 새 코드로 실행되는 "혼합 버전" 상태**가 될 수
  있다. 이번에 실제로 이런 혼합이 결과에 영향을 줬다는 증거는 없지만,
  "영향이 없었을 것"이라고 사후에 단정하는 것 자체가 근거 없는 주장이므로
  더 이상 하지 않는다.
- 이 지적을 반영해 이후로는 회귀 실행 중 코드·테스트를 수정하지 않는
  원칙을 그대로 지켰고(23절 이후 작업), 5절에서 "고정 기준선" 전체
  회귀를 다시 한 번 깨끗하게 실행한다.

**기존 전체 회귀(2026-09-18, 7,122.113초) 기록 — 있는 그대로**: 4,530건
실행, **실패 1건, 오류 3건**(+ 로그에 찍힌 `ERROR:` 문자열 2건은
`test_homez_desktop.py`가 포트충돌을 의도적으로 재현하는 테스트 로그
출력이며 unittest의 실패/오류 집계에 포함된 것이 아니다 — 이 둘을
혼동하지 않는다). 4건 모두 원인 규명 후 수정했고(21절 이후 절 참고),
그 수정은 focused 테스트로만 검증했다 — **이 특정 전체 회귀 실행 자체를
"통과"로 다시 표시하지 않는다.** 최종 확인은 5절의 새 전체 회귀로만
한다.

## 24. Migration 위험 표현 정정

"CREATE TABLE만 있으므로 업그레이드 위험이 없다"는 표현을 정정한다 —
정확히는:
- **기존 행 데이터가 손상될 위험은 없다**(ALTER 없이 신규 테이블
  생성뿐이므로, 기존 테이블의 어떤 행도 건드리지 않는다) — 이 부분은
  그대로 유효하다.
- 그러나 이것이 "위험이 전혀 없다"는 뜻은 아니다. **추가형(additive)
  변경이며, 임시 DB 사본에서 적용·재적용거부·Model-DDL 일치를
  검증했을 뿐**이고, **실제 원본(`homez.db`) 적용 시의 위험**(그
  순간 앱이 그 DB 파일을 열고 있는지, 잠금 경합, 백업 생성·
  `integrity_check` 자체가 걸리는 시간, 디스크 공간, 적용 도중
  프로세스가 죽었을 때의 복구 절차)은 **별도로 평가해야 하는 문제이며
  이번 라운드에서 그 실측(원본 DB 적용)은 하지 않았다.** 26절에서
  적용 계획(파일 목록·경로·체크섬·백업·복구 절차)만 명시하고 실행은
  보류한다.

## 25. 매입 비용·정산 연결 감사 (항목 3, 코드 추적)

`PurchaseRecord`(구 수동 구매 트랙의 "실제 구매 결과" 기록)를 실제로
소비하는 곳을 전부 추적했다:

- **유일한 소비처**: `PurchaseTaskPolicyService`의 하루 지출 한도
  가드(`policy_service.py`, `sum_recorded_amount_since()`)뿐이다.
- **정산·마진 도메인은 이 데이터를 전혀 읽지 않는다** — `app/domains/
  settlement/*`는 마켓(쿠팡)→HOMEZ 입금(매출 쪽)만 다루는 별개
  도메인이고(`MarketplaceSettlement` 모델 자체 docstring: "Payment/PG와
  분리"), `PurchaseRecord`/`PurchaseOrderApproval`/
  `PurchaseOrderSubmissionAttempt` 무엇도 참조하지 않는다(grep 0건).
  마진 계산기(`margin_calculator.py`)는 **구매 전** 추정 도구일 뿐,
  구매 후 실제 원가를 반영하지 않는다.
- **정직한 결론**: 온채널 API 발주 트랙이 성공해도 "실제로 얼마를
  냈는지"를 `PurchaseRecord.actual_amount`와 같은 모양으로 남기는
  곳이 **어디에도 없다.** 가장 가까운 값은 `PurchaseOrderApproval.
  item_amount_snapshot`/`shipping_cost_amount`인데, 이는 발주 **직전**
  스냅샷(재확인용)이지 발주 **후** "실제 청구된" 사실이 아니다. 온채널
  주문상세 조회(`GET seller/order/{code}`) 응답에는 `sum_delivery_price`
  (발주 후 실제 청구된 배송비 합계, 공식 스펙에 존재)가 있지만 이번
  코드베이스는 이 값을 조회는 해도(송장조회 경로에서 `get_order()`를
  호출하긴 함) 원가 기록 목적으로 저장하지 않는다.
- **이번 라운드에서 고치지 않는 이유**: 이 원가 기록을 실제로 소비하는
  기능(정산·마진 리포트)이 현재 코드베이스에 전혀 없다 — 즉 없어서
  당장 끊어지는 "필수 흐름"이 없다(TRACKING_REQUIRED 전환 결함과는
  다르다 — 그건 이미 존재하는 송장조회 화면을 막고 있었다). 새 원가
  기록 체계를 만드는 것은 이번 라운드의 "필수 연결 결함 수정"이
  아니라 "신규 기능 확장"에 해당하므로 만들지 않았다 — **분리를
  이유로 누락을 정상이라고 판단하지 않으며, 이 격차를 그대로
  남긴다.** D3/D4 실제 시험에서는 `PurchaseOrderApproval` 스냅샷과
  실제 온채널 주문상세 조회 결과를 사람이 직접 대조해야 한다(아래
  26-1절 검증 항목 참고).

**주문번호 → 송장조회 → HOMEZ 저장 → 판매채널 배송 처리 — 단계별 상태**:

| 단계 | 상태 |
|---|---|
| order_code → `PurchaseOrderSubmissionAttempt.external_order_code` 영구 저장 | **실제** |
| order_code → 송장조회(`lookup_tracking()`) | **실제 API**(`OnchannelApiClient.get_order()`, Fake 아님) |
| 조회 결과 → HOMEZ 저장(`PurchaseTaskTrackingInfo`) | **실제**, 이미 완성됨(이번 세션 이전부터) |
| HOMEZ 저장 → 쿠팡(판매채널)에 송장번호 업로드 | **미구현** — 코드베이스 전체에 쿠팡 송장 업로드 API 호출이 없다(grep 확인). 설계상 운영자가 쿠팡 Wing에 직접 입력하는 수동 경로로 대체된다(D5의 "수동 처리 경로 확인"과 일치 — 결함이 아니라 처음부터 그렇게 설계됨) |

## 26. 시험 호출 예산 — D3 적용 조건 재확인

3-2절/D1 추가확인에서 이미 구현·검증됨(연결 1개·ACCEPT 고정·페이지
1장·총 2회 DB 영구 강제·다중 프로세스 원자성 증명). D3에서 지킬 조건을
다시 명시한다: **승인된 연결 ID 1개로 고정**하고, 새 연결을 만들어
예산을 재설정하지 않는다(그렇게 하면 `(company_id, store_connection_id)`
단위 예산 테이블에 새 버킷이 생기는 것을 이미 알고 있는 한계로
기록했다 — 신뢰된 운영자만 연결을 만들 수 있다는 전제 하의 한계이며,
D3 자체에서는 이 한계를 이용하지 않는다는 뜻이다).

## 27. 온채널 상품목록 조사 결과 (항목 7, 문서·코드 조사만 — 추가 실 API 호출 없음)

이번 대화에서 이미 실행된 실 API 2회(상품 목록·상세, 연결 id=4,
정상판매 1건 CH1147184 "레이펄스 워터리스 샴푸/바디워시" 응답)에
대한 추가 해석이다 — **새로운 API 호출은 하지 않았다.**

- 공식 스펙(`docs/HOMEZ_ONCHANNEL_OPENAPI_SPEC_20260908.json`)의 상품
  목록(`GET seller/product`) 응답은 `total_cnt`/`last_page`/
  `_pager.has_more`/`next_url`을 포함하는 **페이지네이션 스펙**이다
  (예시 응답 자체가 `total_cnt: 57`).
- 코드(`onchannel_client.py::list_products()`)는 요청한 `page`/
  `page_size`만큼 **정확히 1회 호출**하고 응답의 `total_cnt`/
  `has_more`는 아예 읽지 않는다(버림). 실제로 호출되는 두 래퍼
  (`channel_adapter.py`/`channel_connection_service.py`의
  `list_products()`)는 **`page_size=1`을 기본값으로 하드코딩**하고,
  이 이번 세션 이전 승인("item 7 승인된 검증")도 정확히 이 좁은
  범위(연결 확인용 1건 조회)였다.
- **결론**: 이번 대화의 "정상판매 1건" 응답은 `page_size=1`로
  요청한 것의 필연적 결과다 — 실제 판매자의 정상판매 상품이 1개뿐인지,
  더 있는데 1페이지만 봤는지는 **이 응답만으로는 알 수 없다.** 더 큰
  `page_size`(최대 100)로 다시 조회하거나 `total_cnt`를 확인해야
  판단할 수 있는데, 이는 **추가 실 API 호출**이므로 별도 승인 없이는
  하지 않는다.
- 이 조사 결과가 판매신청 POST 실행을 정당화하지 않는다 — 목록
  조회 범위가 좁았다는 사실 자체가 판매신청을 임의로 밀어붙일 근거는
  아니다.

## 30. 27절 정정 — "page_size=1 때문에 1건만 나왔다"는 설명 철회 (항목 2)

**사용자가 전달한 사실**: 실행 주체 Codex가 실제로 보낸 목록 조회
파라미터는 `page=1, page_size=100, status=1`이었고, 응답은
`total_cnt=1, last_page=1`, 상품 CH1147184 1건이었다. 이어서 같은
상품의 상세를 조회해 총 GET 2회, 모두 HTTP 200이었다. 이 2회(목록+
상세)는 27절에서 "이번 대화에서 이미 실행된 실 API 2회"로 언급한
바로 그 호출과 건수·순서·연결ID(4)·결과 상품(CH1147184)이 정확히
일치한다 — **같은 호출을 가리키는 것으로 판단한다.**

**정정 사유**: 27절은 실제 호출 로그를 근거로 삼지 않고, 코드에서
`list_products()`의 `page_size` **기본값**이 1인 것을 보고
"그러므로 이 호출도 page_size=1이었을 것"이라고 추론했다 — 이는
실행 주체(Codex)의 실제 호출 파라미터를 직접 확인하지 않은 채
코드 기본값을 실제 호출값으로 치환한 오류였다. `channel_adapter.py:
771`/`channel_connection_service.py:902`의 `page_size=1`은 호출자가
얼마든지 다른 값으로 넘길 수 있는 **기본값**일 뿐, 강제되는 값이
아니다(전 라운드에서 "하드코딩"이라 표현한 것 자체가 부정확했다).

**정정된 해석**: 스펙(`HOMEZ_ONCHANNEL_OPENAPI_SPEC_20260908.json`)의
`total_cnt`/`last_page`는 `page_size`와 무관하게 서버가 계산하는
"해당 필터 조건에 맞는 전체 건수"다(스펙 예시 자체가
`total_cnt:57, page_size:1, last_page:57`로 두 값이 독립적임을
보여준다). 실제 호출은 `page_size=100`으로 `total_cnt=1`보다 훨씬
큰 페이지 크기를 요청했으므로, **1건이라는 결과는 페이지 크기의
한계가 아니라 서버가 직접 보고한 실제 건수다.**

**그러나 결론(성급한 일반화 금지)은 유지된다 — 근거만 바뀐다**:
스펙상 `status` 파라미터는 1=정상판매, 2=단종, 3=판매중단,
4=일시품절, 5=품절 중 하나를 고르는 **필터**다. 이번 호출은
`status=1`(정상판매)로만 걸었으므로, `total_cnt=1`은 **"이 연결
계정의 정상판매 상태 상품이 정확히 1개"**라는 뜻이지, 단종·
판매중단·일시품절·품절 상태의 상품이 없다는 뜻이 아니다. 따라서
"온채널 전체 상품이 1개"라는 해석은 여전히 근거가 없다 — 다만
그 이유가 "페이지가 작아서"가 아니라 "다른 상태값을 조회하지
않아서"로 바뀐 것이다.

**추가로 필요한 실 API 호출(실행하지 않음, 승인 대상)**: `status`
파라미터를 생략하거나(전체 상태 통합 조회 여부는 스펙에 명시 없음 —
호출해 봐야 확인 가능) 2~5 각 값으로 개별 조회해야 다른 상태의
상품 유무를 알 수 있다. 목적은 "정상판매 외 상태의 상품 존재 여부
확인"이며, 경로는 동일한 `GET /openapi/seller/product`, 최대
호출 횟수는 상태값 생략 조회 1회로 충분한지 우선 시도하고(생략이
전체를 반환하지 않으면 상태값 2~5 각 1회, 최대 4회 추가), 실패 시
자동 재시도는 하지 않는다. **이번 라운드에서는 이 호출을 실행하지
않았다** — 별도 승인 필요.

## 28. Migration 적용 준비 (항목 6, 계획만 — 원본 미적용)

**실제 pending 목록과의 대조(읽기 전용 확인, 값 변경 없음)**: 실제
`homez.db`의 `schema_migrations` 테이블을 조회한 결과 적용된 행이
정확히 73개이고, 가장 최근 적용분은 `20260916_02_add_order_auto_
collection_state_last_run_counts.sql`이다. 저장소의 `migrations/`
디렉터리에는 74개 파일이 있다 — **차이는 정확히 1개**,
`20260918_00_create_order_collection_test_budget_usage_schema.sql`
뿐이다(다른 미승인 Migration이 함께 섞여 있지 않음을 확인했다).

| 항목 | 값 |
|---|---|
| 적용 대상 DB 경로 | `C:\Users\Daum pc\Homez-OS\homez.db`(현재 크기 3,702,784바이트) |
| 적용할 파일(정확히 1개) | `migrations/20260918_00_create_order_collection_test_budget_usage_schema.sql` |
| 파일 SHA-256 | `93ca072401873e920365f1974fd70116626544c81cfd9ff0fc880597a481b4b1` |
| 파일 크기 | 1,762바이트 |
| 변경 범위 | `CREATE TABLE order_collection_test_budget_usages`(신규 테이블 1개) + 인덱스 3개, 기존 테이블 ALTER 없음 |
| 백업 절차 | 기존 `BackupService.create_backup()` 경로 재사용(Online Backup API로 파일 복사 → `integrity_check` → SHA-256 → 암호화 → 북키핑) — 이번 라운드에서 새로 만들지 않는다 |
| 복구 절차 | 적용 실패 시 직전 백업 파일로 복원(기존 `BackupService`/`RestoreService` 경로 재사용) — Migration 자체의 롤백은 `DROP TABLE IF EXISTS order_collection_test_budget_usages`(migration 파일 하단 주석으로 이미 기록됨) |
| 승인하지 않은 다른 Migration 동반 적용 가능성 | 없음(위 대조로 확인 — 정확히 1개만 pending) |

**이번 라운드에서 원본 적용은 실행하지 않는다** — 위 표는 계획 근거일
뿐이며, 실행은 별도의 명시적 승인 이후에만 한다.

## 29. 외부 성공 + 내부 저장 실패/재시작 시 자동 재주문 방지 확인 (항목 3, 코드·기존 테스트 확인만 — 신규 코드 없음)

`PurchaseOrderSubmissionService.submit_order()`의 실제 순서
(`app/domains/purchase_task/order_submission_service.py:462-535`)를
다시 읽어 확인했다: `attempt` 행은 `IN_FLIGHT` 상태로 **온채널 실제
발주 호출(`adapter.submit_order()`, 495행) 이전에 이미 커밋된다**
(462-471행). 따라서 "외부 발주는 성공했지만 그 직후 내부 저장
(`_finalize_attempt`)이 실패"하는 최악의 경우에도, DB에는 이미
`IN_FLIGHT` 행이 남아 있다.

재시도 차단은 인메모리 상태가 아니라 매번 새로 DB를 조회하는
`_has_blocking_task_attempt()`(203-234행)가 담당한다 — 같은
`purchase_task_id`에 `PENDING`/`IN_FLIGHT`/`SUCCEEDED` 행이 하나라도
있으면(또는 사람이 아직 "주문 없음"으로 확정하지 않은
`RESULT_UNKNOWN` 행이 있으면) 새 시도 생성 자체를 막는다. 이 조회가
매번 DB를 다시 읽는 구조이므로 **재시작 여부와 무관하게 동일하게
작동한다** — 별도의 재시작 전용 복구 로직이 필요 없는 이유다.

기존 테스트로 이미 증명되어 있다(신규 테스트 불필요, 확인만):
- `tests/test_purchase_order_submission_service.py::DuplicateLockAndRestartRecoveryTestCase::test_repeated_click_with_same_idempotency_key_blocked_after_first_success`
  — 실제 첫 발주가 성공(`call_log` 1건)한 뒤 같은
  `idempotency_key`로 재시도하면 `ConflictException`이 나고
  `call_log`는 여전히 1건 — **두 번째 시도는 Adapter(실제 외부 호출)
  까지 도달하지 않는다.**
- `test_task_lock_blocks_pending_inflight_and_succeeded_attempts` —
  `PENDING`/`IN_FLIGHT`/`SUCCEEDED` 행이 하나만 남아 있어도
  `_has_blocking_task_attempt()`가 True를 반환함을 직접 검증한다.
  `IN_FLIGHT`는 정확히 "외부 호출 성공 여부가 아직 내부에 확정되지
  않은" 상태를 대표하므로, 이 테스트가 곧 "외부 성공 + 내부 저장
  실패" 시나리오의 차단을 증명한다.

**결론**: 이 보호는 D2 이전부터 이미 존재하던 구조이며, 이번 라운드
필수 결함 수정 대상이 아니었다(별도 구멍이 발견되지 않았다). 유일한
탈출구는 사람이 온채널 관리자 화면을 직접 확인해
`resolve_unknown_attempt(resolution=ORDER_NOT_CONFIRMED)`으로 명시
확정하는 경로뿐이며, 이는 자동 재주문이 아니라 사람의 개입이다.

## 31. 지출한도 누락 재현 및 수정 (2026-09-19, 항목 2/3/4/5)

**재현한 결함**: `PurchaseTaskPolicyService.evaluate()`의 일간/월간
지출한도(`daily_purchase_limit_amount`/`monthly_purchase_budget_amount`)
는 `PurchaseTaskRepository.sum_recorded_amount_since()`가 유일한
근거였고, 그 메서드는 `PurchaseRecord.actual_amount`만 더했다.
온채널 API 발주 트랙은 `record_purchase()`를 절대 타지 않으므로
`PurchaseRecord`가 전혀 생기지 않는다 — 즉 **온채널 실 발주가 아무리
쌓여도 일간/월간 지출한도는 0원으로 보이고, 다음 발주 승인이 실제
지출과 무관하게 계속 통과했다.** `tests/test_purchase_order_submission_
service.py::OnchannelSpendLimitProtectionTestCase::
test_next_purchase_evaluation_actually_blocks_on_reduced_headroom`이
수정 후 이 판정 결과 자체(`PurchaseTaskPolicyReason.DAILY_LIMIT_
EXCEEDED`)로 재현·검증한다.

**이중계산·누락 전수 점검(항목 2 — "테이블이 다르다"로 단정하지
않음)**: 이 지출한도와 완전히 별개로, 온채널 발주 자체를 막는
**연결별 하루/월 발주 한도**(`PurchaseOrderApprovalService.
_sum_consumed_amount_today/_sum_consumed_amount_this_month`,
`PurchaseOrderApproval` 테이블 기반)가 이미 존재한다. 이 게이트는
`submit_order()`/`resolve_unknown_attempt()` 양쪽 성공 경로 모두에서
이미 `mark_consumed()`/직접 상태 갱신으로 정상 작동 중임을 코드로
확인했다(별도 결함 없음, 수정하지 않음) — `PurchaseTaskPolicyService`
쪽 한도와는 검사 시점·범위가 다른, 의도된 이중 방어(defense in
depth)이지 중복이 아니다.

**최소 수정안(Model·Migration 변경 없이 기존 구조 재사용)**:

| 단계 | 시점 | 기준 금액 | 예약 상태 | 지출한도 포함 여부 |
|---|---|---|---|---|
| 예약(양쪽 트랙 공통) | 정책평가(`evaluate_and_reserve`) | 예상 필요예산 | `RESERVED`/`EXTENDED` | 미포함(아직 확정 아님) |
| **Stage 1(신규)** | `submit_order()` 성공 또는 `resolve_unknown_attempt(ORDER_CONFIRMED)` — **송장조회 이전** | 승인 스냅샷(`item_amount_snapshot`+`shipping_cost_amount`) | `PENDING_VERIFICATION`(신규) | **포함** |
| **Stage 2(기존 확장)** | `refresh_tracking_live()` 성공(실 API `sum_product_price`+`sum_delivery_price`+`sum_add_price`) | API 확인된 실제 금액 | `CONFIRMED`(최종) | 포함(금액 갱신) |
| 내부 저장 실패/재시작 | 언제든 | — | 마지막으로 커밋된 상태 유지(IN_FLIGHT/RESERVED 등) | 그 상태 그대로(29절 근거 재사용) |
| 취소·반품 요청 이후 | `record_refund()` 등 | 환불액 | 예약 상태는 건드리지 않음(그대로 CONFIRMED류 유지) | `held_amount`만 환불액만큼 감소, 예약 자체는 불변 |
| 늦게 도착한 재조회 | 취소·반품 요청 이후 | — | **무시**(task.status 가드) | 변화 없음 — 환불이 되돌려지지 않음 |
| 수동 트랙(`record_purchase()`) | 사람이 실제 구매 결과 입력 | 실제 결제금액 | (동일 `CONFIRMED`, 다만 `PurchaseRecord`도 동시 생성) | `PurchaseRecord.actual_amount`로 포함(기존 그대로) |

이중계산 방지: `sum_recorded_amount_since()`는 `PurchaseRecord`가
이미 있는 `purchase_task_id`의 예약은 (수동 트랙 몫이므로) 예약
합계에서 제외한다 — `record_purchase()`와 온채널 두 확정 메서드만
`CONFIRMED_LIKE` 상태를 만든다는 사실을 코드 전수 확인(grep)으로
근거를 남겼다.

**PurchaseRecord를 억지로 만들지 않은 이유(항목 3 명시)**:
`PurchaseRecord`는 "Provider가 자동으로 채우지 않는다"는 것 자체가
모델 docstring의 전제다. 자동 실행되는 Stage 1/2가 이 모델에 행을
쓰면 그 전제와 충돌하므로, 대신 이미 존재하고 이미 정책평가 때부터
자동으로 조작되던 `PurchaseTaskBudgetReservation`(양쪽 트랙 공통,
`_reserve_budget()`가 이미 자동 생성)을 재사용했다. `reservation.amount`
는 `record_purchase()`(수동 트랙, `PurchaseRecord`가 정확한 출처라
갱신 불필요)와 달리 온채널 트랙에서는 지출한도 집계의 직접 출처이므로
Stage 1/2에서 최신 확정 금액으로 함께 갱신하도록 했다(D2 라운드
코드에는 없던 부분 — 이번에 추가).

**Model·Migration 변경 없음**: 새 예약 상태값(`PENDING_VERIFICATION`)
은 기존 `status String(20)` 컬럼에 들어가는 문자열 값 하나를
늘린 것뿐이다(20자 이내로 맞춤 — 첫 시도였던
`CONFIRMED_PENDING_VERIFICATION`은 30자라 컬럼 선언을 넘겨 즉시
`PENDING_VERIFICATION`으로 정정했다). 실제 DB pending Migration은
여전히 1개(`20260918_00_...`)뿐이며 이번 라운드에서 추가하지 않았다.

**격리 테스트(항목 5, 실제 발주·결제·환불 API 미사용)**:
`tests/test_purchase_order_submission_service.py::
OnchannelSpendLimitProtectionTestCase`(11건, 신규) — 발주 성공 직후
송장조회 전 한도 반영, 다음 승인 실제 차단, UNKNOWN 수동 확정도
동일 보호, 승인액-실제액 불일치 조정, 환불 후 지연 재조회가 환불을
되돌리지 않음(전액/부분 각각), 수동 트랙 기존 동작 불변, 수동+온채널
혼합 집계 이중계산 없음, 회사 간 격리, 일간 경계 누락·이중차감 없음.
기존 `DuplicateLockAndRestartRecoveryTestCase::
test_internal_finalize_failure_after_external_success_leaves_
attempt_in_flight_and_blocks_retry`(29절)를 "외부 성공 직후 내부
저장 실패·재시작" 항목으로 그대로 재사용했다(중복 작성하지 않음).

## 32. 한도 보호 마지막 연결 대조와 실제 공백 3건 (2026-09-20)

**로그 끝 한글 3줄의 출처(보고 불확실성 정리)**: `app/desktop/main.py`의
`print()`(368·401·473행)가 `tests/test_live_gate4_fix_defects.py`의 fail-closed
테스트 3건(`test_run_aborts_when_bootstrap_and_engine_paths_differ` /
`..._seeding_fails` / `..._channel_policy_seeding_fails`)이 `desktop_main.run()`을
직접 호출할 때 찍는 **테스트 부수 출력**이다(별도 프로세스 오류 아님).
해당 파일만 `> 파일 2>&1`로 단독 실행해 `OK` 뒤에 같은 3줄이 같은 순서로
재현됨을 확인했다 — 표준출력은 파일로 리다이렉트되면 블록 버퍼링되어
인터프리터 종료 시 flush되므로 표준오류로 나오는 unittest 요약 뒤에
붙는다. "OK 뒤에 나왔다"는 이유가 아니라 이 재현으로 판단한다.
과거 기록(`HOMEZ_PROJECT_STATE.md` 5614행 등)과도 일치한다. mtime 불변은
관측 사실일 뿐 DB 무접촉의 증명으로 쓰지 않는다.

**대조 결과 — RESERVED·IN_FLIGHT·UNKNOWN 금액의 보호 경로**:

| 상태 | 정책 단계(`PurchaseTaskPolicyService`) | 발주 승인 단계(`_sum_reserved_amount`) | 같은 작업 재발주 |
|---|---|---|---|
| RESERVED(예약만) | `held_amount`로 가용금액에서 차감(`BUDGET_INSUFFICIENT`) — 지출 집계에는 미포함이 **의도** | 승인 유효시간(10분) 내 ACTIVE만 집계 | 해당 없음 |
| IN_FLIGHT/PENDING | 예약은 RESERVED로 남아 위와 동일 | 유효시간 내 ACTIVE 집계, **만료 후에는 빠짐 → 공백 1(수정)** | `_has_blocking_task_attempt`가 차단(기존 테스트) |
| RESULT_UNKNOWN(미확정) | 동일 | 만료 후 빠짐 → 공백 1(수정) | 차단(기존 테스트) |
| SUCCEEDED | 예약 PENDING_VERIFICATION→CONFIRMED, 지출 집계 포함(31절) | 승인 CONSUMED로 집계 | 차단 |

RESERVED가 지출 집계에 없다는 사실만으로 결함이라 단정하지 않았고, 가용금액
차감·승인 집계·같은 작업 차단이 각각 보호함을 코드와 테스트로 확인했다.
두 집계는 서로 다른 게이트(정책 단계 vs 발주 직전)가 각자 하나의 출처만
읽으므로 게이트 간 이중계산은 없다. 신규 상태(`PENDING_VERIFICATION`)를 읽는
기존 경로 — 만료 스윕(ACTIVE만), 원 주문 취소 예약 해제(ACTIVE만), 예약 연장
(RESERVED/EXTENDED만), `record_refund`(예약 행을 건드리지 않음),
`record_purchase`(USER_PAYMENT_PENDING만, 온채널 작업은 도달 불가) — 은 모두
누락 없이 이 상태를 건드리지 않음을 확인했다. UI·스키마는 예약 상태를 읽지 않는다.
(참고: `release_expired_reservations`는 저장소 안 어디에서도 호출되지 않아
실제로는 예약이 자동 만료되지 않는다 — 나중에 배선한다면 결과 미확정 발주가
있는 작업은 건너뛰어야 한다는 잠재 한계로만 기록, 이번에 수정하지 않음.)

**재현 후 수정한 실제 공백 2건(재현 테스트 3건)**(모두 수정 전 상태에서 테스트가
실패함을 먼저 확인):
1. 발주 승인 단계 한도(`_sum_reserved_amount`): 외부 성공 직후 내부 저장
   실패(IN_FLIGHT 잔존)나 미확정 UNKNOWN은 승인이 만료되면 일간/월간 한도
   집계에서 사라졌다. → 결과 미확정(PENDING/IN_FLIGHT, 또는 사람이 아직 확정하지
   않은 RESULT_UNKNOWN) 발주의 승인 금액을 만료 뒤에도 집계에 포함한다(하나의 OR
   조건이라 이중계산 없음). 사람이 ORDER_NOT_CONFIRMED로 확정하면 빠지고
   ORDER_CONFIRMED면 승인이 CONSUMED로 넘어가 계속 집계된다.
2. Stage 1/2: 이미 나간 외부 지출을 운영 가능 금액이 모자라면 **기록하지
   않았다**(예약을 RESERVED로 남김 — 지출 집계에서 영영 누락, 이전 라운드에서
   "재시도 가능"이라 보고한 것은 잘못된 판단이었다). → `_reserve_external_spend`가
   초과해서라도 `held_amount`에 올리고 원장·감사로그·알림을 남기며, 가용금액이
   음수가 되어 다음 승인·발주가 `BUDGET_INSUFFICIENT`로 막힌다. 알림 실패가
   성공한 발주 흐름을 깨지 않도록 예외는 삼킨다. 이전 라운드 테스트
   `test_insufficient_funds_blocks_without_corrupting_state_or_raising`는 옛
   동작을 단언하고 있어 `..._still_records_already_spent_money_and_never_raises`로
   바꿨다(기대값 약화가 아니라 반대 방향의 더 엄격한 단언 — 확정 상태·금액·
   held_amount 증가·음수 가용금액·원장 1건).
(참고) 위 2번의 예약 사전 조건(`_reserve_budget`)은 그대로다 —
   발주 **전** 예약은 여전히 부족하면 차단한다.

## 33. 한도 수정 최종 검증 — 고정 기준선 전체 회귀 결과와 미호출 만료 함수 조건 (2026-09-20)

**고정 기준선**: HEAD `3703273`(원격과 일치, 미커밋 변경 없음), 명령
`python -m unittest discover -s tests -p "test_*.py"`, 시작 2026-09-20 19:23:49,
종료 21:17:39. 코드·테스트는 이 실행 동안 수정하지 않았다(문서 초안은 저장소 밖에서
작성). 격리: 테스트는 임시 SQLite 파일만 쓰며, 이 실행에는 저장소 밖 가드
(`sitecustomize`, 루프백 외 `connect` 차단)를 PYTHONPATH로 주입했다 — 가드가 켜진
프로세스 7개, **회귀 중 차단된 외부 접속 시도 0건**(가드 자체 점검용 1건은 별도).
`homez.db`는 실행 전후 SHA-256 `f3aafca1...762008`·크기 3,702,784바이트·수정시각이
동일(관측 사실이며 그 자체가 무접촉의 증명은 아님). 가짜 자격증명은 테스트가 쓰는
InMemory 저장소 기준이며 실제 Windows 자격 증명 저장소 접촉 여부는 이번에 별도로 관측하지
않았다.

**결과: 4,563건 실행, 실패 1, 오류 0, skip 7, exit code 1 — 전체 통과가 아니다.**
- 실패 1건: `tests.test_product_candidate_analysis_workflow...test_concurrent_verify_info_only_one_succeeds`
  (상품후보 도메인의 2스레드 동시성 테스트, `purchase_task` 참조 0건).
- 원인: 두 스레드가 각자 `InterfaceError`/`ObjectDeletedError`로 끝나 `ok`가 0건(저장소
  밖 사본에서 실패 실행의 결과를 출력해 확인).
- (2026-09-21 정정) 이 실패를 "이전 기준선에서도 났으니 안전하다"는 근거로 쓰지 않는다.
  2026-09-20 당시 기록은 같은 테스트를 단독 재실행했을 때 3703273에서 15회 중 1회·
  40회 중 2회, 이전 기준선 `dec78db`에서 40회 중 2~3회 실패했다는 **관측 횟수**였고,
  이를 고정된 실패 확률로 단정하지 않는다. 원인은 34절에서 실제 traceback으로 구분했다
  (테스트 하네스 결함, 제품 코드 결함 아님). 테스트는 이 실행에서는 수정·삭제·완화하지
  않았고, 동일 코드의 전체 회귀는 반복하지 않았다.
- **정정된 집계(unittest 기준)**: 실행 4,563건 = 성공 **4,555건** + 실패 1건 + 오류 0건 +
  skip 7건. 앞서 적은 "4,562건 통과(skip 7 포함)"는 skip을 성공에 섞은 표현이라 철회한다.
  전체 회귀 통과라고 선언하지 않는다. 이전 집중 회귀(443건 통과 + 수정 후 approval 32건
  통과)는 이 전체 회귀 결과와 합산하지 않는다(별도 근거).

**한도 수정 검증 항목의 근거 테스트**(모두 위 실행에 포함되어 통과):
승인 만료 후 UNKNOWN·IN_FLIGHT 금액 보존(`test_unresolved_attempt_amount_stays_in_approval_
layer_limit_after_approval_expiry`, `test_unknown_attempt_amount_is_protected_until_human_
resolves_it_as_not_created`), 초과 지출 기록 + 다음 실행 차단(`test_external_spend_is_
recorded_even_when_funds_cannot_cover_the_difference` — `BUDGET_INSUFFICIENT` 판정 포함),
환불 후 재조회 무영향(전액·부분 2건), 외부 성공 직후 내부 저장 실패·재시작(`test_internal_
finalize_failure_after_external_success_...`), 수동 트랙 불변, 회사 격리, 일간 경계.

**미호출 예약 만료 함수(`release_expired_reservations`) — 현재 실행 결함이 아니다**:
앱 어디에서도 호출되지 않아(테스트만 호출) 운영에서는 예약이 자동 만료되지 않는다. 나중에
읽기 시점·스케줄러 등으로 연결할 때 필요한 보호 조건: (1) `PENDING`/`IN_FLIGHT` 또는 사람이
확정하지 않은 `RESULT_UNKNOWN` 발주 시도가 있는 작업의 예약은 만료·해제하지 않는다(실제
주문이 있을 수 있음, 수동 트랙의 `UNCERTAIN` 처리와 같은 원칙), (2) 사람이
`ORDER_NOT_CONFIRMED`로 확정한 뒤에만 해제 대상이 된다, (3) `PENDING_VERIFICATION`/
`CONFIRMED`는 건드리지 않는다(현재 `ACTIVE` 조건이 이미 보장), (4) 만료된(`EXPIRED`) 예약에
대해 나중에 발주 성공·수동 확정이 오면 Stage 1이 아직 `RESERVED`/`EXTENDED`만 처리하므로
`EXPIRED`도 처리하도록 확장해야 지출 집계가 즉시 보호된다(Stage 2는 이미 처리), (5) 연결 시
위 (1)~(4)를 검증하는 테스트를 함께 추가한다.

## 34. 동시성 실패 `test_concurrent_verify_info_only_one_succeeds` — 원인 구분과 수정 (2026-09-21)

**원인: 테스트 하네스 결함(제품 코드 결함 아님).** 저장소 밖 사본(3703273)에 traceback 출력만 추가해
실패 실행(58번째 반복)을 잡았다. 두 오류 모두 워커가 `service.verify_private_candidate_info(...)`
**인자를 만드는 시점**(테스트 401행 `candidate.id`)에서 났고 서비스 코드에는 아직 닿지 않았다.
- t1: `ObjectDeletedError` — `_load_expired` → `load_scalar_attributes`(만료된 객체 새로고침 중
  행이 없다고 판정).
- t2: `sqlite3.InterfaceError: bad parameter or other API misuse` —
  `SELECT ... FROM product_candidates WHERE id=?` 새로고침 중.
- 메커니즘: `_create_private()`가 커밋해 `candidate` 객체가 만료되고(기본 `expire_on_commit`),
  두 워커가 각자 Session을 만들었지만 인자 `candidate.id`는 **메인 스레드 Session의 만료된
  객체**를 읽어, 두 스레드가 같은 Session·같은 SQLite 커넥션으로 동시에 새로고침했다.
- 서비스 없이 이 접근 패턴만 재현: 스레드 2개×200회(400회 접근) — 공유 객체 `ok` 344 /
  `InterfaceError` 28 / `ObjectDeletedError` 27 / `IndexError` 1, 원시 id 사용 시 400회 전부 `ok`
  (관측 횟수이며 고정 확률로 단정하지 않는다). 같은 파일 `tests/test_product_candidate.py`의 동시성
  테스트는 이미 `candidate_id = candidate.id`를 스레드 시작 전에 원시 값으로 고정한다.

**제품 코드 점검**: `verify_private_candidate_info`는 사전 상태 검사(DISCOVERED 아니면
`BadRequestException`) 뒤 근거 행을 flush하고 **원자적 조건부 UPDATE**
(`WHERE id=? AND status IN (DISCOVERED)` → `rowcount != 1`이면 `ConflictException` + 롤백)로 승자를
정한다(service 599~643행). 운영 구성과의 차이: 운영은 요청마다 새 Session(`get_db()`가
`SessionLocal()` 생성·`finally`에서 close)이고 엔진은 `pool_pre_ping=True`, SQLite 기본 대기(5초),
이 모듈에서 WAL/busy_timeout을 따로 지정하지 않는다. 테스트는 엔진 2개(두 번째는 busy_timeout 15초)를
쓴다. 운영 요청 경로는 Session을 스레드 간에 공유하지 않아 이 패턴이 생기지 않는다 — 다만 백그라운드
스레드(스케줄러 등)의 Session 사용은 이번 범위에서 감사하지 않았다.

**수정(테스트만, 제품 코드 무변경)**: ① 메인 스레드에서 `candidate_id`를 원시 값으로 고정, ② 두 요청이
모두 사전 상태 검사(DISCOVERED)를 통과한 뒤 쓰기 단계 직전(`add_evidence_no_commit`을 워커별 인스턴스에서
감싸 barrier)에서 만나게 해 충돌 시점을 결정적으로 고정(실제 두 스레드·두 SQLite 커넥션 유지,
BrokenBarrierError를 삼키지 않음), ③ 단언 강화: 정확히 1건 `ok` + 1건 `ConflictException`(패자는
사전 검사를 이미 통과했으므로 항상 Conflict), 승자 이벤트 1건, 알림 디스패치 정확히 1회(멱등 키
`candidate-review:{id}:ANALYZED`), 근거 행은 등록 시 `RAW_SOURCE` 1 + 승자 `MANUAL_INFO_CHECK` 1만
(패자 롤백 확인), 최종 상태 ANALYZED, 워커 스레드 종료 확인. sleep·예외 무시·skip·단언 약화 없음.
(첫 시도에서 근거 행 총수를 1로 단언했다가 등록 시 `RAW_SOURCE`가 이미 있어 실패했다 — 제 단언의
오류였고 종류별 단언으로 고쳤다.)

**검증**: (1) 수정 전 재현 — 위 traceback. (2) mutation — 사본에서 조건부 UPDATE의 상태 조건을 제거하면
새 테스트가 3회 모두 `2 != 1 : {'t1': 'ok', 't2': 'ok'}`로 결정적으로 실패(원본 사본은 통과).
(3) 수정 후 — 단독 300회 연속 반복 300 통과 / 0 실패(관측), 상품후보 인접 테스트 5개 파일 58건 통과.
(4) 고정 기준선 전체 회귀 결과는 이 절 아래 35절에 별도 기록한다.

## 35. 고정 기준선 전체 회귀 — 동시성 수정 후 (2026-09-21)

**고정 기준선**: HEAD `a32623e`(원격 일치, 실행 시작 시 미커밋 변경 0건). 3703273 대비 코드 차이는
`tests/test_product_candidate_analysis_workflow.py` 1개뿐(제품 코드·Migration 무변경). 명령
`python -m unittest discover -s tests -p "test_*.py"`, 시작 2026-09-20 23:38:30, 종료 2026-09-21
01:33:12(6,852.343초). 실행 중 저장소 파일은 수정하지 않았다. 이 회귀는 수정 후 **1회만** 실행했다.

**결과: 실행 4,563건 / 성공 4,556건 / 실패 0 / 오류 0 / skip 7 / exit code 0** (unittest 요약
`OK (skipped=7)`, 성공 = 실행 − skip). 3703273 기준 회귀(4,563건 / 성공 4,555 / 실패 1 / skip 7 /
exit 1)와 실행 건수가 같고, 그때 실패한 테스트가 이번에 통과했다 — 이 두 실행의 숫자를 합산하거나
"이전 실패를 지웠다"고 표현하지 않는다. 이 테스트의 통과는 우연히 통과할 때까지 재실행한 결과가 아니라
원인(34절)을 규명·수정한 뒤의 단일 실행이며, 같은 테스트의 단독 300회 반복 통과(관측)와 mutation
검증(34절)이 별도 근거다.

**격리 관측**: 외부 접속 차단 가드(`sitecustomize`, 루프백 외 `connect` 차단)가 켜진 프로세스 8개,
**차단된 외부 접속 시도 0건**. `homez.db` SHA-256 `f3aafca1...762008`·크기 3,702,784바이트·수정시각이
실행 전후 동일(관측 사실이며 무접촉의 증명은 아님). 가짜 자격증명은 테스트의 InMemory 저장소 기준이며
실제 자격 증명 저장소 접촉 여부는 별도로 관측하지 않았다. Migration: 미적용 정확히 1개
(`20260918_00_...`, 1,762바이트, SHA-256 `93ca0724...b4b1`), 변경·적용 없음.
