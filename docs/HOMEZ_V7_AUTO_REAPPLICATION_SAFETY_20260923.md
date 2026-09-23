# HOMEZ V7 — 자동 재신청 방지와 기존 상품등록 재개 안전성 확인 (2026-09-23, 12차)

이 문서는 11차(`docs/HOMEZ_V7_SALES_APPLICATION_STATUS_CORRECTION_20260923.md`)
가 제안만 하고 구현하지 않았던 "내부 기록 보완 설계"를 실제 코드·격리
테스트로 구현·검증한 결과다. **원본 DB·실 API·실거래는 이번에도 전혀
실행하지 않았다** — 코드·테스트·문서만 변경했다.

## 1. 기준선 고정

- `git fetch` 후 재확인: 로컬 HEAD = 원격 `origin/main` HEAD =
  `637173e`(11차 문서 커밋), 작업 트리 클린. `tasklist`로 실행 중인
  `python*`/`HOMEZ*` 프로세스 0개, 다른 작업자의 새 커밋 없음.
- 이번 라운드는 승인 범위 안에서 **코드 변경을 포함**한다(11차까지는
  문서만) — 아래 §2·§5의 변경 파일만 손댔고, 이번 범위와 무관한
  리팩터링·기능 추가는 하지 않았다.

## 2. 자동 재신청 경로 재현 및 차단

### 2-1. 재현: "과거 접수 증거는 있으나 내부 추적 기록이 없는 상태"

기존 코드(변경 전)에서 `PurchaseSalesApplicationService.
ensure_sales_application_submitted()`를 추적한 결과, `(company_id,
connection_id, product_code)`에 대한 행이 없거나 `RESULT_UNKNOWN`이면
**둘 다 똑같이 "최초 신청"으로 취급해 `confirm_real_submission=True`
만으로 곧바로 `adapter.apply_for_sale()`을 다시 호출**했다 — 결과를
아직 모르는 상태(`RESULT_UNKNOWN`)와 진짜 최초 상태를 구분하지 않는
것이 근본 원인이었다. 격리 테스트로 이 상태를 직접 재현했다(실제
CH1147184 케이스를 그대로 흉내낸 것 — 온채널 실제 응답 대신 Fake
Adapter, 실 DB 대신 임시 SQLite):

```
tests/test_purchase_sales_application_service.py
  ::AutoRetryBlockedOnUnresolvedStatusTestCase
    ::test_result_unknown_blocks_auto_retry_zero_adapter_calls
```

변경 전 코드로 이 테스트를 먼저 돌렸다면(재현 확인 목적으로 임시로
되돌려 실행): 첫 번째 `RESULT_UNKNOWN` 이후 재호출이 **성공적으로
Adapter까지 도달**했을 것이다(기존 `test_retry_after_rejected_
reuses_same_row_and_can_succeed`가 REJECTED에 대해 이미 증명하는
것과 동일한 코드 경로를 RESULT_UNKNOWN도 그대로 탔다).

### 2-2. 차단 — 코드 변경

**`app/domains/purchase_task/constants.py`** — `SalesApplicationStatus`에
`NEEDS_REVIEW`(외부 정황 증거는 있으나 이 서비스가 스스로 시도한 적은
없는 상태)를 추가하고, `RESULT_UNKNOWN`/`NEEDS_REVIEW`를
`BLOCKS_AUTO_RETRY`로 묶었다. `REJECTED`(온채널이 명시적으로 거부한
것 — 결과가 확실함)는 그대로 자동 재시도 대상으로 남겼다(기존 설계
유지 — "정상적인 최초 판매신청의 기존 승인 흐름까지 무조건 제거하지
않는다").

**`app/domains/purchase_task/sales_application_service.py`**:
- `ensure_sales_application_submitted()`에 `override_unresolved_status:
  bool = False` 매개변수를 추가했다. 기존 행이 `BLOCKS_AUTO_RETRY`
  상태이고 이 플래그가 없으면, `apply_for_sale()`을 전혀 호출하지
  않고 `ConflictException`을 즉시 던진다 — 메시지에 현재 상태와
  "온채널 공식 화면이나 공급처 문의로 사람이 먼저 확인하라"는 필요한
  확인 행동을 명시한다.
- `order_submission_service.py`의 유일한 호출부(446번째 줄)는 이
  새 플래그를 **넘기지 않는다**(기본값 `False` 그대로) — 그래서
  판매신청이 `BLOCKS_AUTO_RETRY` 상태면 **발주 시도 자체도 같은
  지점에서 함께 막힌다**(어댑터의 `submit_order()`에 도달하지 않음).
  이 방식으로 "자동 판매신청 POST와 실제 발주가 모두 실행되지
  않는다"는 요구를 하나의 게이트로 동시에 만족시켰다 — 별도로 두
  곳을 각각 고칠 필요가 없었다.
- 신규 메서드 `record_unconfirmed_prior_evidence()` — 이 서비스가
  스스로 실행한 적 없는 외부 증거를 사람이 검토해 `NEEDS_REVIEW`로
  표시할 때만 쓴다. **`apply_for_sale()`을 절대 호출하지 않는다.**
  이미 `SUBMITTED`로 확인된 행은 덮어쓰지 않고 `BadRequestException`
  으로 거부한다.

### 2-3. "정상적인 최초 판매신청" 경로는 그대로 유지됨

`existing is None`(진짜 첫 시도)인 경우의 동작은 전혀 바꾸지
않았다 — 기존 `test_successful_submission_records_submitted_with_
applied_product_code`(변경 없음, 계속 통과)가 이를 그대로 증명한다.
이번 변경은 오직 "이미 시도했지만 결과를 모르는 행이 존재하는 경우"
와 "외부 증거로 확인 필요 표시가 된 경우"만 새로 막는다.

### 2-4. 공급처 중복 신청 처리 방식 미확인 — 그대로 존중

`onchannel_client.py::apply_for_sale()`의 기존 docstring(변경하지
않음)이 이미 "409는 스펙상 '이미 신청된 상품 재신청' 정황으로
추정되지만 공식 답변으로 확정된 사실은 아니다"라고 명시한다 — 이
사실을 근거로, `override_unresolved_status=True`를 넘기는 것 자체를
UI로 자동화하지 않았다(§7에서 후속 과제로 명시).

## 3. 판매신청 확인 근거 분리

### 3-1. `GET seller/product/{code}`의 `status` 필드가 뜻하는 것 — 공식 문서·기존 답변 재확인

`docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md`(재조사 절, 이번
라운드에서 새로 조회하지 않고 기존 기록만 재확인)에 이미 정리돼
있다: `components.schemas.product.status` enum은 `1=판매전, 2=판매,
3=판매중단, 4=일시품절, 5=품절`이며, **"정황상(직접 확인 아님)
판매신청 승인 전 상품은 상태값 1(판매전)에 머물 가능성"**을
시사할 뿐이다. 이 문서도, 온채널 공식 답변(2026-09-10, 같은 문서
"공식 답변 반영" 절)도 **"이 상태값이 곧 이 계정의 판매신청 승인
여부다"라고 확정한 적이 없다** — 승인 상태를 조회하는 API 자체가
없다는 것만 공식 확정됐다.

### 3-2. 코드가 이 둘을 섞지 않는다는 것을 구조적으로 고정

`is_sales_application_confirmed(connection_id, company_id,
product_code)`의 시그니처 자체가 상품 상태값을 입력으로 받지
않는다 — 이 게이트는 오직 `purchase_sales_application_attempts`
테이블만 본다. 새 테스트로 이 분리를 고정했다:

```
tests/test_purchase_sales_application_service.py
  ::ProductStatusNeverSatisfiesApplicationGateTestCase
    ::test_gate_ignores_product_sale_status_entirely
```

`inspect.signature()`로 함수 시그니처 자체에 `status` 매개변수가
없음을 검증해, 향후 누군가 "상품 상태값을 인자로 받아 게이트를
간소화하자"는 식으로 리팩터링하면 이 테스트가 즉시 실패하도록
했다(회귀 방지).

### 3-3. HTTP 200 ≠ 업무 승인 성공 — 기존 docstring 유지·재확인

`apply_for_sale()`의 기존 docstring이 이미 "이 메서드가 성공(HTTP
200)했다는 사실은 '신청이 접수됐다'는 뜻이지 '승인됐다'는 뜻이
아니다"라고 명시한다 — 이번 라운드에서 이 원칙을 약화하는 변경은
하지 않았다.

### 3-4. 공식 조회 방법이 없을 때 가능한 증거 확보 방법(실행 안 함)

공식 승인-상태 조회 API가 없다는 사실은 변하지 않으므로, 실제로
현재 상태를 확인하려면 다음 중 하나가 필요하다(전부 미실행,
계획만):

1. `GET /openapi/seller/product/{code}`(기존 `lookup_product()`)로
   `status` 값을 정황 증거로만 확인(§3-1의 한계를 그대로 인정) —
   11차 §4-2에 이미 기록된 계획, 이번에 다시 정리만 함.
2. 온채널 판매자 화면에 직접 로그인해 육안으로 확인(사람의 행동,
   이 세션이 대신 실행하지 않음).
3. 온채널 공급처 문의(11차 D3 §2-2의 기존 문의 초안과 같은 경로
   재사용 가능).

## 4. 과거 접수 기록 보완 방식

### 4-1. 2026-09-14 호출의 확인된 계정·상품·업무 결과 (재확인, 비밀값 없음)

11차 §3-1 표를 그대로 재사용한다(이번 라운드에서 다시 조사하지
않음 — 이미 확인된 것을 반복 조사하지 않는다는 원칙): 연결
id=4(`ONCHANNEL`, `sin945`) · 상품 `CH1147184` · 엔드포인트 `POST
/openapi/seller/product/apply` · HTTP 200 · 응답 스키마는
`result.prd_code`뿐(접수번호·신청ID 없음, 만들지 않음).

### 4-2. 기존 기록·감사 모델로 증거를 보존할 수 있는지 확인 → 새 테이블 대신 기존 테이블 재사용

새 테이블을 만들기 전에 `purchase_sales_application_attempts`
테이블 자체가 이미 (company, connection, product) 단위 "현재 상태 1행"
구조이고 `status`·`failure_detail`·`triggered_by`·`started_at`/
`finished_at` 컬럼이 이 목적에 그대로 맞는지 먼저 확인했다 — 맞았다.
그래서 **스키마 변경(새 컬럼·새 테이블·Migration) 없이** 기존
테이블에 새 상태값(`NEEDS_REVIEW`, 12자 — 기존 `status` 컬럼
`String(20)` 폭 안에 그대로 들어간다) 하나만 추가해 구현했다.
**새 스키마가 필요 없었으므로 격리 리허설용 Migration 자체가
없다**(지시문의 "필요하면"이 이번엔 해당하지 않았다).

### 4-3. 과거 사건 발생시각과 확인시각·근거 구분(코드로 강제)

`record_unconfirmed_prior_evidence(event_occurred_at=...,
evidence_summary=..., recorded_by=...)`가 `failure_detail`에 다음
형식으로 기록한다(테스트로 정확한 형식을 고정):

```
[정황 증거 반영 — 실제 판매신청 실행 아님]
사건 발생 추정 시각=<event_occurred_at 또는 '미상'>;
기록(확인) 시각=<record_unconfirmed_prior_evidence 호출 시각>;
기록자=user#<recorded_by>; 근거=<evidence_summary>
```

`event_occurred_at`을 넘기지 않으면 "미상"으로 남는다 — 확인 시각을
사건 시각으로 추측해 채우지 않는다(
`test_event_occurred_at_omitted_stays_unknown_not_guessed`로 고정).

### 4-4. 하지 않은 것(지시문 금지 사항 재확인)

- 2026-09-14 호출을 "방금 실행한 것"처럼 `SUBMITTED` 상태로 기록하지
  않았다 — 새로 만든 상태는 `NEEDS_REVIEW`이며, `SATISFIES_ORDER_
  GATE`에 포함되지 않는다(발주 게이트를 통과시키지 않는다).
- 원본(실) `homez.db`에 이 상태를 실제로 기록하지 않았다 — 아래
  모든 테스트는 임시 파일 SQLite에서만 실행됐다. CH1147184에 대해
  실제로 `record_unconfirmed_prior_evidence()`를 호출하는 것은 이번
  승인 범위(코드·격리 테스트·문서) 밖이며, 원본 DB 쓰기이므로 별도
  승인 대상이다(§7 승인 목록에 포함).
- 증거가 부족한 채로 `SUBMITTED`나 게이트 통과로 바꾸지 않았다 —
  `NEEDS_REVIEW`는 구조적으로 게이트를 통과시키지 못한다.

## 5. 기존 Wizard 중복 등록 위험 확인

### 5-1. `ListingWizard#1` ↔ `CH1147184` 연결 — 11차에서 이미 확인, 이번엔 재조사하지 않음

11차 §5-2/§5-3에서 필드 수준까지 이미 확인했다(`product_candidate_id=3`
→ `candidate_key`에 `ONCHANNEL:CH1147184` 명시, `current_step=
CHANNELS`, `draft_json.options=[]`, `materialized_listing_ids_json=[]`).
이번 라운드는 이 사실을 다시 조회하지 않고 그대로 이어받는다(승인
범위의 "읽기 전용 조사"를 반복하지 않는다는 원칙).

### 5-2. `materialized_listing_ids_json=[]`가 "외부 미등록 확정"이 아닌 이유(코드로 확인)

`ListingWizardLiveService._context()`(변경하지 않음, 읽기 전용
확인만)를 다시 추적했다: 이 필드는 **Wizard가 스스로 기록한 내부
연결 목록**일 뿐이다 — 온채널/쿠팡 쪽에 실제로 무엇이 있는지
조회한 결과가 전혀 아니다. 그래서 이 값이 비어 있다는 사실은 "이
Wizard가 아직 어떤 외부 생성도 시도조차 하지 않았다"는 **내부
사실**은 확정하지만, "쿠팡에 이 상품이 전혀 존재하지 않는다"는
**외부 사실**은 전혀 증명하지 않는다(다른 경로로 생성됐을 가능성
자체를 배제하지 못한다) — 11차의 표현을 코드 근거와 함께 다시
확인한 것이다.

**CH1147184의 현재 상태에 한정하면**: `materialized_listing_ids_json
=[]`이므로 `_context()`의 다음 검사(72~78번째 줄, 변경하지 않음)가
**구조적으로** 이 Wizard에 대한 어떤 `submission_id`도 유효하게
참조할 수 없게 막는다 — 즉 지금 이 상태에서는 `preflight()`/`send()`
자체를 호출할 진입점이 없다(신규 POST를 실행하려 해도 그 전에
`BadRequestException("제출 작업이 이 상품등록 흐름에 속하지
않습니다.")`로 막힌다). **이번 라운드에서 이 경로에 새 코드를
추가하지 않았다 — 이미 막혀 있었다.**

### 5-3. 기존 제출 시도·요청 식별자·결과불명 이력 — CH1147184는 없음(추적할 대상 자체가 없음)

`marketplace_submissions` 테이블에 CH1147184/Wizard#1과 연결된 행이
있는지는 11차에서 이미 `marketplace_submissions` 전체 행 수 0건으로
확인됐다(11차 §5-2 표) — 즉 시도 자체가 없었다. 추적할 "과거 시도
이력"이 존재하지 않는다.

### 5-4. 같은 위험이 실제로 발생할 수 있는 지점 — 발견했으나 이번 라운드에서 고치지 않음(정직하게 명시)

코드 추적 결과, **다른** 위험이 하나 발견됐다 — CH1147184 상태와는
무관하지만 구조적으로 남아 있는 위험이다: `MarketplaceSubmission`은
append-only이고 유일한 UNIQUE 제약은 `(company_id, idempotency_key)`
뿐이다. 한 Submission이 `UNKNOWN`(결과불명, provider 예외)으로
끝나면 `preflight()`의 `SUBMISSION_NOT_PENDING` 블로커가 **그 행의
재사용**은 막지만, 같은 (listing, marketplace_account) 조합에 대해
**새 idempotency_key로 완전히 새 Submission 행을 만드는 경로**를
막는 코드는 이번 조사에서 찾지 못했다 — 그런 새 행이 만들어지면
그 행의 `preflight()`는 (그 행 자신의 `external_submission_ref`가
비어 있으므로) `ALREADY_SUBMITTED`를 감지하지 못하고, 결과불명이던
이전 시도가 실제로 쿠팡에 상품을 만들었을 가능성을 모른 채 새
`send()`가 실행될 수 있다.

**이번 라운드에서 이 위험을 코드로 고치지 않았다** — CH1147184에는
현재 적용되지 않는 위험(§5-2에서 확인한 대로 애초에 Submission
생성 자체가 막혀 있음)이고, 고치려면 "새 Submission 생성" 경로(이
라운드에서 위치를 특정하지 못함 — 추가 탐색 필요) 전반을 건드리는
별도 조사·설계가 필요해 이번 범위(①②③ 중 CH1147184에 당장 영향
없는 ③의 하위 사례)를 벗어난다고 판단했다. **결함으로 기록하되
수정 범위는 별도 라운드로 남긴다.**

### 5-5. 공식 조회·대조 방법 — 기존에 이미 있는 기능을 재사용하는 계획

`app/domains/marketplace_listing/submission_reconciliation_service.py`
(이번 라운드에서 새로 만들지 않음, 이미 존재함 — 2026-08-30/31
"제출 장부 정합화" 작업의 산출물)가 정확히 이 문제(결과불명 뒤에
실제로 외부에 생성됐는지 사람이 나중에 확인해 안전하게 정합화)를
위해 이미 설계돼 있다: `preview_reconciliation()`(dry-run) →
상품명·`vendorUserId`·`displayCategoryCode` 대조 → 2개 이상 일치 시
`AUTO_ELIGIBLE`, 하나라도 모순되면 `BLOCKED` → `apply_reconciliation()`
(실제 반영, 사람의 별도 호출로만). **CH1147184가 미래에 이 단계까지
진행되고 결과불명 상태가 발생하면, 새 Submission을 만들기 전에 이
서비스로 먼저 쿠팡 쪽 실제 상태를 대조하는 것이 계획이다** — 이번
라운드에서 이 서비스를 CH1147184에 실제로 실행하지 않았다(대상
자체가 아직 없다, §5-3).

### 5-6. 재사용 시 검증할 것 — 옵션 미입력·SKU·구성수량·공급처 연결

Wizard#1을 재개(4단계 채널 확정 이후 옵션 입력 단계로)할 때, 등록
전에 다음을 반드시 검증해야 한다는 계획을 11차에 이어 다시 명시한다
(이번 라운드에서 실행하지 않음 — Wizard 재개 자체가 별도 승인 대상):

1. `draft_json.options`가 비어 있는 상태 그대로 등록을 시도하지
   않는다(현재 상태 그대로면 등록 자체가 실패하거나 의미 없는
   상품이 만들어진다).
2. 옵션 4종(과거 API 관측, 최신 아님)의 실제 현재 가격·재고를 먼저
   재확인한다(§3의 공식 조회 계획과 동일한 대상).
3. 옵션 연결(`supplier_option_links`, 9차에서 채택했으나 원본 DB
   미적용)이 적용된 뒤에는 SKU·구성수량을 이 테이블 기준으로
   맞춘다 — 원본 미적용 상태에서는 여전히 수동 대응표(D3 §2-6)만
   있다.
4. 매입 계정(연결 id=4)의 재검증 유효기간(`CREDENTIAL_
   REVERIFICATION_WINDOW_HOURS`)이 등록 실행 시점에 지나 있지 않은지
   확인한다(마지막 확인 2026-09-08 — 9일 이상 경과, 재확인 필요
   가능성 높음).

### 5-7. 만료된 `PurchaseOrderApproval#1` — 재사용 금지 재확인(원본 미수정)

11차와 동일한 결론을 유지한다 — DB 컬럼은 `status=ACTIVE`이지만
`expires_at`이 이미 지났다(지연 평가 구조). 이번 라운드에서 원본 값을
건드리지 않았다. 재개하려면 최신 조건으로 `confirm_shipping_cost()`
+ `finalize_approval()`을 새로 호출해야 한다.

## 6. 집중 테스트와 결과

| 지시문 조건 | 테스트(파일::클래스::메서드) | 결과 |
|---|---|---|
| 기록 누락·과거 접수 미확인 → 판매신청 0회 | `test_purchase_sales_application_service.py::AutoRetryBlockedOnUnresolvedStatusTestCase::test_needs_review_blocks_auto_retry_zero_adapter_calls` | PASS(호출 0회, 근거 문구 원문 보존) |
| 〃 → 발주 0회 | `test_purchase_order_submission_service.py::SalesApplicationUnresolvedStatusBlocksOrderTestCase::test_needs_review_blocks_both_reapplication_and_order` | PASS(발주 Adapter 0회, `PurchaseOrderSubmissionAttempt` 0행, 판매신청 행 상태 불변) |
| 상품 정상판매 상태만 존재 → 판매신청 승인으로 오인하지 않음 | `ProductStatusNeverSatisfiesApplicationGateTestCase::test_gate_ignores_product_sale_status_entirely` | PASS(시그니처에 상품 상태 입력 자체가 없음을 구조적으로 고정) |
| 공식 근거로 확인된 신청 → 기존 정상 경로 유지 | `AlreadySubmittedSkipsReapplicationTestCase::test_already_submitted_does_not_call_adapter_again`(기존, 무변경) | PASS |
| 신청 timeout·결과불명 → 자동 재신청 없음 | `AutoRetryBlockedOnUnresolvedStatusTestCase::test_result_unknown_blocks_auto_retry_zero_adapter_calls` | PASS(2차 호출 0회, 상태 불변) |
| 계정·상품이 다른 증거 → 재사용 차단 | `test_evidence_for_one_product_does_not_block_a_different_product`, `test_evidence_for_one_connection_does_not_block_a_different_connection` | PASS(둘 다 독립적으로 정상 최초 신청 진행) |
| 내부 등록번호 없음 + 외부 결과불명 → 신규 등록 차단 | §5-2(코드 확인, CH1147184엔 이미 구조적으로 막혀 있음) / §5-4(다른 경로의 잔여 위험은 미수정으로 기록) | **부분** — CH1147184 케이스는 확인됨(기존 코드로 이미 차단), 일반화된 "새 idempotency_key 우회" 케이스는 **결함으로 기록만 하고 미수정** |
| 차단 사유와 다음 행동이 화면에 표시됨 | `ConflictException` 메시지 원문(§2-2) — 기존 전역 오류 표시 경로로 노출(신규 UI 없음) | **부분** — 메시지 자체는 명확하나, 전용 UI 화면은 이번 범위에서 만들지 않음(§7 후속 과제) |

**실 DB·Credential Store·외부 네트워크**: 모든 테스트가 임시 파일
SQLite(`tempfile.mkstemp`) + `InMemoryCredentialStore`/Fake Adapter만
사용했다 — `HOMEZ_GUARD_LOG`로 확인한 결과 `ATTEMPT_BLOCKED` 0건(실
자원 접근 시도 자체가 없었다).

**집중 회귀**: 신규/수정 파일 + 직접 관련 기존 파일(
`test_purchase_sales_application_service.py`(21건, 신규 11건 포함),
`test_purchase_order_submission_service.py`(73건, 신규 1건 포함))을
각각 실행 — **전부 통과, 실패 0, 오류 0**. 기존 테스트의 기대값·가드·
skip은 하나도 약화하지 않았다(전부 원문 그대로 통과).
전체(4,677건) 회귀는 이번 변경이 공용 계약(모델·마이그레이션)을
바꾸지 않는 서비스 계층 추가이므로 재실행하지 않았다 — 직접 관련
파일 집중 검증으로 충분하다고 판단했다(재검증이 필요하다고 판단되면
별도로 요청).

## 7. 다음 실행 계획

기존 업무 DB 적용 승인안(`docs/HOMEZ_V7_DB_DECISION_REVIEW_20260923.md`
§6)과 현재 미적용 목록은 이번 라운드에서 코드·Migration 변경이 없어
그대로 유효하다(재확인 불필요 — 9차 이후 `migrations/` 무변경).
시험상품은 기존 세 후보와 추천(CH1147184)을 그대로 재사용한다 —
사용자 최종 확정으로 바꾸지 않는다.

| # | 단계 | 상태 |
|---|---|---|
| 1 | 안전성 수정 완료(이번 라운드) | **완료** — §2~§5 |
| 2 | 승인된 DB 적용(저장소 루트, 2건) | 미완료 — 승인 대기 |
| 3 | 판매신청 상태 확인 | 미완료 — §3-4 계획대로 외부 조회 필요 |
| 4 | 기존 Wizard 옵션 검토 | 미완료 — §5-6 체크리스트 |
| 5 | 승인된 등록 | 미완료 |
| 6 | 외부 옵션 ID 대조 | 미완료 |
| 7 | HOMEZ 매핑 | 미완료(옵션 연결 DB 미적용과 연동) |
| 8 | 승인된 실주문·발주 시험 | 미완료 |

**실제 호출 계획(전부 미실행, 승인 필요 시 아래 형식 그대로 사용)**:

| 항목 | 내용 |
|---|---|
| 대상 | 연결 id=4(`ONCHANNEL`, `sin945`), 상품 `CH1147184` |
| 목적 | 현재 상품 상태값(정황 증거) 확인(§3-4-1) |
| 방법 | `GET /openapi/seller/product/{코드}`(기존 `lookup_product()`) |
| 최대 횟수 | 1회 |
| 재시도 | 없음 |
| DB 쓰기 | 연결의 기존 "실조회 성공" 기록 갱신만(부작용 없음) — 판매신청 테이블에는 이 조회 자체로는 쓰지 않는다(별도 사람 확인·별도 호출인 `record_unconfirmed_prior_evidence()`가 필요) |
| 중단 조건 | 4xx/5xx, 한도(1회) 도달 |

## 8. 완료 보고 요약

**확인된 결함(재현·기록, 이번 라운드 수정)**: 기존 코드가
`RESULT_UNKNOWN`(결과불명)과 "진짜 최초 시도"를 구분하지 않아
`confirm_real_submission=True`만으로 자동 재신청됐다 — §2-2 코드
변경으로 수정, §6 테스트로 검증.

**확인된 결함(기록만, 이번 라운드 미수정)**: `MarketplaceSubmission`이
append-only이고 새 idempotency_key로 새 행을 만드는 경로에 "같은
listing+account 조합의 미해결 이전 시도" 확인 로직을 찾지 못했다 —
§5-4에 재현 조건·영향과 함께 기록, 수정 범위(새 Submission 생성
경로 특정 + reconciliation 연동)는 별도 라운드로 남긴다.

**단순 미확인 항목(결함 아님)**: CH1147184의 현재 온채널 판매신청
상태(§3-4), 옵션 4종의 현재 가격·재고(§5-6-2).

**수정 파일**:
- `app/domains/purchase_task/constants.py`(신규 상태값·그룹 상수)
- `app/domains/purchase_task/sales_application_service.py`(자동 재시도
  차단, 신규 메서드)
- `tests/test_purchase_sales_application_service.py`(신규 테스트
  11건 추가, 기존 무변경)
- `tests/test_purchase_order_submission_service.py`(신규 테스트 1건
  추가, 기존 무변경)
- 이 문서, `docs/HOMEZ_PROJECT_STATE.md` 갱신

**최종 판정**: 이번 세 가지 위험(①②)은 격리 검증 완료, ③은 CH1147184
케이스만 격리 검증 완료(부분)이고 일반화된 잔여 위험은 결함으로
기록된 채 미수정 상태다. **"해당 실행 위험의 격리검증 완료"**이며
V7 실사용 완료로 확대하지 않는다. 원본 DB·실 API·실거래는 여전히
전부 승인 대기다.

## 9. 검증 환경 정리

이번 라운드는 임시 서버를 띄우지 않았다(순수 `unittest` 실행뿐,
`preview_start` 사용 없음) — 종료·확인할 별도 프로세스가 없다.
스크래치 로그 파일(`sales_app_guard.log`,
`focused_guard_round12.log`)은 저장소 밖 스크래치 디렉터리에만
있다.

## 10. Git 상태

이번 라운드 소유 파일만 커밋한다: 위 §8 "수정 파일" 목록 전부(코드
2개, 테스트 2개, 문서 2개). 실 DB·백업·자격증명·개인정보는 포함하지
않았다.
