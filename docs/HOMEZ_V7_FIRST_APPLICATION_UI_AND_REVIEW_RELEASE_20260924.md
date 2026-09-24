# HOMEZ V7 — 최초 신청 UI와 검토 해제 흐름 완성 (2026-09-24, 17차)

이 문서는 16차가 백엔드에만 만들어 둔 `confirmed_first_application` 게이트를
실제 발주 검토 화면에 연결하고, 과거 접수 증거가 있는 상품(NEEDS_REVIEW/
RESULT_UNKNOWN)을 사람이 실제로 확인한 근거로 해제하는 새 흐름
(`resolve_needs_review_as_confirmed_submitted()`)을 추가해 실제 화면으로
검증한 결과다. **원본 DB Migration 적용, `NEEDS_REVIEW` 원본 기록·해제, 실
자격증명 사용, 인증 API 호출, 판매신청·상품등록·발주·결제·환불은 이번에도
전혀 실행하지 않았다.**

## 기준선 재확인

`git fetch` 후 재확인: 로컬 HEAD = 원격 `origin/main` HEAD = `4c5618c`(16차
커밋). 다른 작업자의 워크트리 3개(`affectionate-cray-cb4fa5`,
`quirky-booth-62f752`, `quirky-diffie-3718ca`)가 존재함을 확인했고 전혀
건드리지 않았다. 16차의 권한 경계 테스트(104건)와 14~15차의 Migration
사본 검증은 코드·조건이 바뀌지 않아 재사용했다 — 이번에 실제로 수정한
범위(`sales_application_service.py`의 신규 메서드,
`service.py`/`schema.py`/`router.py`의 화면 연결, `console.js`/`console.css`/
i18n)만 새로 검증했다.

## ① 최초 신청 UI 완성 여부

**완성했다.** 기존 발주 검토 화면(`ptRenderOrderSubmissionReview`,
`/purchase-tasks/{id}/order-submission-review`)과 기존 승인 구조(재인증
토큰 + `VIEW_SENSITIVE_DATA` 권한, `order-approval/submit` 엔드포인트)를
그대로 재사용했다 — 새 화면·새 엔드포인트 권한 체계를 만들지 않았다.

- **연결**: `PurchaseTaskService.build_order_submission_review()`가 이제
  `sales_application` 응답에 `status`/`no_internal_record`/
  `needs_manual_review`/`evidence_detail`을 함께 내려준다(기존
  `confirmed`/`detail`은 유지) — 읽기 전용 원칙 그대로(상태를 바꾸는
  호출 없음).
- **기본 선택 해제**: `pt-first-application-checkbox`는 매 렌더마다
  `firstApplicationConfirmed` 매개변수(기본값 `false`)로만 초기화된다 —
  발주 승인·"실제 온채널에 발주 전송" 버튼 클릭 어느 것도 이 값을 자동
  으로 `true`로 만들지 않는다(별도 `confirm_real_submission` 플래그와
  완전히 분리된 채로 요청 바디에 실림).
- **대상 명시**: 체크박스 패널에 "대상: {연결 mall_code — account_label}
  / 상품: {조회된 상품명}"을 그대로 표시하고, 체크하면 "온채널에 실제
  판매신청 요청(POST)이 나갑니다"라고 명시한다.
- **경로 분기**: `no_internal_record`(내부 기록 없음)면 체크박스 패널을,
  `needs_manual_review`(NEEDS_REVIEW/RESULT_UNKNOWN)면 검토 해제 폼을
  보여준다 — 서버가 계산한 상태值 하나로 화면 분기가 갈리므로, 과거
  접수 이력이 있는 상품(내부에 NEEDS_REVIEW 행이 있는 상품)은 코드
  구조상 체크박스 경로 자체에 도달할 수 없다(두 패널이 동시에 뜨지
  않는다).
- **서버 검사 유지**: 재인증 토큰·`VIEW_SENSITIVE_DATA` 권한·
  `task_service.get_task(task_id, company_id)`(회사 범위)는 그대로다 —
  체크박스 값은 이 검사들을 우회하는 새 경로가 아니다(§③ 라우터 테스트로
  재확인).
- **재사용 방지**: 체크박스는 매번 새로 조회한 `review` 객체에 대해서만
  존재하고, 다른 상품·계정으로 다시 조회하면(`ptRunOrderSubmissionReview`
  전체 재실행) 항상 `false`로 리셋된다 — 실제 화면에서 상품코드를 바꿔
  재조회하면 체크박스가 해제된 채로 다시 뜨는 것을 확인했다(§③).
- **CH1147184 하드코딩 없음**: 체크박스·해제 폼 어디에도 특정 상품코드가
  들어가지 않는다 — `review.product_code`/`review.connection_id`를 그대로
  쓴다.
- **NEEDS_REVIEW/RESULT_UNKNOWN 우회 불가**: 체크박스는 `no_internal_
  record`일 때만 렌더된다 — 이미 행이 있는 상태에서는 애초에 체크박스가
  존재하지 않고, 서버(`order_submission_service.py`)도 `existing is not
  None`이면 `confirmed_first_application` 값과 무관하게 기존 판정을
  그대로 적용한다(16차에 이미 격리 테스트로 고정, 이번에 재확인만).

## ② 과거 접수 검토·해제 경로와 필요한 증거

### 2-1. 기존 모델 재사용 확인(신규 스키마 없음)

새 컬럼·새 테이블을 만들기 전에 기존 `PurchaseSalesApplicationAttempt`
(status/failure_detail/triggered_by/started_at/finished_at)와 기존
`write_audit_log()`/`audit_logs`가 이 목적에 그대로 맞는지 먼저 확인했다 —
맞았다. **그래서 새 스키마·Migration 없이 구현했다**(지시문의 "새 스키마가
필요하면 설계·리허설까지만"이 이번엔 해당하지 않는다).

### 2-2. 신규 메서드 — `resolve_needs_review_as_confirmed_submitted()`

`app/domains/purchase_task/sales_application_service.py`에 추가했다.
CH1147184 같은 상품을 최초 신청으로 처리하지 않는다는 것을 이 메서드
자체가 구조적으로 보장한다 — `existing is None`(내부 기록 없음, 최초
신청 경로)이면 즉시 거부한다(`BadRequestException`, "해제할 검토 대상
행이 없습니다"). 오직 이미 `NEEDS_REVIEW`/`RESULT_UNKNOWN`인 행만
해제 대상이다.

**요구하는 근거(전부 필수, 하나라도 없으면 거부)**:
- 정확한 계정·상품 식별: `connection_id`/`company_id`/`product_code`(호출
  인자 그대로, 서버가 추측하지 않음).
- 확인 출처(`confirmation_source`): 화면은 "온채널 판매자센터 화면 직접
  확인" / "공급처 공식 문의 회신" 두 선택지만 제공(자유 입력 아님 —
  근거 유형을 제한된 어휘로 강제).
- 실제로 확인한 내용(`confirmation_summary`): 자유 텍스트, 최소 1자
  이상 필수(스키마 레벨 `min_length=1`로 강제, 서버도 재확인).
- 확인자(`confirmed_by`): 호출한 사용자 ID(라우터에서 `current_user.id`
  그대로, 위조 불가 — 클라이언트가 다른 사용자 ID를 보낼 방법이
  없다).
- 확인 시각(`confirmed_at`): 서버가 자동 기록(`datetime.utcnow()`) — 사건
  발생 추정 시각(§4-1의 `event_occurred_at`, 이미 저장돼 있던 값)과 절대
  섞지 않는다. 기존 `failure_detail`(사건 발생 시각 포함)을 지우지 않고
  그 아래에 새 줄로 이어 붙인다 — 감사 이력이 한 행 안에서도 보존된다.

### 2-3. 상품 판매상태·계정 인증 성공·과거 HTTP 200만으로 확정하지 않음

이 메서드는 `review.sales_application`처럼 상품 상태를 보지 않는다(§4-1
확인 결과, 여전히 그런 API가 없다) — 오직 **사람이 입력한 확인 출처·
확인 내용**만 근거로 받는다. 계정 인증 성공(`verified_at` 갱신)이나
과거 HTTP 200(원래 NEEDS_REVIEW를 만든 그 증거 자체)만으로는 이 메서드가
호출되지 않는다 — 호출 자체가 사람의 새로운 확인 행동을 전제로 한다(그
행동이 실제로 있었는지는 코드가 검증할 수 없지만, "확인했다"는 사실을
구조화해 기록하도록 강제한다).

### 2-4. 원문에 없는 값을 생성하지 않음

`applied_product_code`를 이 메서드에서 설정하지 않는다(`None`으로 유지) —
실제 온채널 API 응답이 없으므로 "API가 돌려준 값"처럼 보이는 값을 지어
내지 않는다. 승인번호·접수번호도 만들지 않는다(애초에 이 스키마에 그런
필드가 없다 — 12차부터 유지된 원칙).

### 2-5. 성공·승인 기록이 아니라 "확인 전 실행 차단" 해제임을 명시

전환되는 상태는 `SUBMITTED`이며, 이 상태의 의미는 12차부터 문서화된 그대로
"접수 확인됨(승인 여부 불명)"이다 — 이 메서드가 "판매신청이 승인됐다"고
주장하지 않는다. 화면 문구도 "검토 해제"(release)라고만 표현하고 "승인"
이라는 단어를 쓰지 않는다.

### 2-6. 외부 호출 0회·중복 기록 안전성

`apply_for_sale()`을 호출하지 않는다(§③에서 스파이로 확인). 이미 해제된
행을 다시 해제하려 하면 `existing.status not in BLOCKS_AUTO_RETRY`에
걸려 `BadRequestException`으로 거부되고, 기존 `SUBMITTED` 상태·감사
기록은 그대로 유지된다(§③ 격리 테스트로 확인).

### 2-7. 검토 해제만으로 발주가 자동 실행되지 않음

`resolve_needs_review_as_confirmed_submitted()`는 판매신청 게이트 하나만
연다 — 발주(`submit_order()`)는 여전히 별도의 `confirm_real_submission=
True`, 포인트·배송비 승인(Gate D), 온채널 발주 계약 확인(Gate B)을
전부 통과해야 한다. 신규 테스트로 이 독립성을 직접 확인했다(§③).

## ③ 격리 테스트·화면 검증 결과

### 3-1. 신규 격리 테스트(합성 DB·Fake Provider·격리 Credential Store)

`tests/test_purchase_sales_application_service.py::
ResolveNeedsReviewAsConfirmedSubmittedTestCase`(7건, 신규):

| 지시문 조건 | 테스트 | 결과 |
|---|---|---|
| 증거 없는 검토 해제 → 차단 | `test_resolve_without_evidence_is_rejected` | PASS(상태 불변) |
| 내부 기록 자체가 없음(최초 신청 경로) → 차단 | `test_resolve_with_no_existing_row_is_rejected` | PASS |
| 이미 SUBMITTED(해제 불필요) → 차단 | `test_resolve_with_row_not_needing_review_is_rejected` | PASS |
| 유효한 근거 → 해제, 이력 보존, 외부 호출 0회 | `test_valid_evidence_resolves_to_submitted_without_external_call_and_preserves_history` | PASS(`call_log=[]`, 기존 정황 텍스트 보존, `applied_product_code`는 `None`) |
| 다른 연결·상품의 근거 재사용 차단 | `test_resolve_for_one_target_does_not_affect_a_different_product_or_connection` | PASS |
| 재시작·중복 클릭 → 상태·감사기록 일관 유지 | `test_double_resolve_call_keeps_state_consistent` | PASS(2차 호출 거부, 상태·근거 텍스트 불변) |
| 감사 기록 확인 | `test_resolution_recorded_in_audit_log` | PASS |

`tests/test_purchase_order_submission_service.py::
OrderApprovalGateIntegrationTestCase::
test_resolved_review_does_not_bypass_expired_approval_gate`(신규) — 검토
해제 후에도 발주 승인 만료는 독립적으로 여전히 막힘을 확인(Adapter 호출
0회).

`tests/test_purchase_task_router.py`(신규 3건) — 라우터를 거친 실제 해제
성공, 다른 회사 차단(`NotFoundException`), 근거 없는 상품 거부
(`BadRequestException`)를 확인.

**집중 회귀**(변경 파일 영향 범위, 고정 기준선 통합 실행): 189건 전부
통과, 실패·오류 0건, 가드 로그 `GUARD_ACTIVE` 확인·`ATTEMPT_BLOCKED`
0건. `test_purchase_task_order_submission_review.py`(review 응답 스키마
직접 검증하는 파일)도 포함해 기존 기대값 전부 무변경 통과.

### 3-2. 실제 화면 검증(격리 E2E 서버, 실 DB·자격증명·네트워크 없음)

기존에 이 세션이 만들어 둔 격리 E2E 서버(`order_review_e2e_server.py`,
2026-09-09 최초 작성)를 재사용했다 — 실제 `homez.db`가 아닌 격리 SQLite,
`InMemoryCredentialStore`, 가짜 온채널 Adapter만 쓴다. 이번 라운드에서
누락됐던 메서드(`check_member_point`/`apply_for_sale`/`submit_order`,
2026-09-09 이후 추가된 기능이라 원래 스크립트엔 없었음)를 가짜로 보강하고
새 DB로 재부트스트랩했다(스크립트 자체는 저장소 밖 스크래치 파일 —
커밋 대상 아님).

- **데스크톱**: 로그인 → 매입·발주 관리 → 작업 상세 → 온채널 상품코드
  입력·검토 실행 → **"최초 신청 확인" 패널이 정확히 렌더**(대상·경고
  문구·체크박스·안내 문구 전부 확인) → 체크박스 클릭 → "지금 발주를
  보낼 수 없는 이유" 목록에서 판매신청 사유만 실시간으로 사라짐(배송비
  사유는 그대로 유지 — 서버 재조회 없이 클라이언트에서 정확히 필터링됨)
  → 배송비 입력 제출(서버 재조회 발생) → 체크박스가 다시 해제 상태로
  리셋됨(재사용 방지 확인) → 별도 상품코드로 `record_unconfirmed_prior_
  evidence()`를 직접 호출해 `NEEDS_REVIEW` 시드 → 같은 화면에서 재조회 →
  **"판매신청 검토 해제 필요" 패널이 정확히 렌더**(기존 정황 증거 원문
  전체 표시 포함) → 확인 출처 선택 + 확인 내용 입력 + "검토 해제" 클릭 →
  **판매신청 상태가 "접수 확인됨"으로 전환**, 검토 해제 패널이 사라지고
  차단 사유 목록에서도 판매신청 사유가 제거됨(실제 서버 상태 전환 확인).
- **모바일**(375×812 뷰포트): 같은 화면에서 체크박스 패널이 줄바꿈과
  함께 정상 렌더, 가로 스크롤 없음(`scrollWidth === clientWidth` 확인),
  체크박스 탭 → 동일하게 판매신청 차단 사유만 실시간 제거됨을 확인.
- 전 과정에서 실제 Windows Credential Manager·실제 온채널 네트워크를
  전혀 열지 않았다(가짜 Adapter·가짜 Credential Store만 사용, 격리 SQLite
  DB만 씀). 검증 종료 후 서버·브라우저 탭을 모두 종료했고, 원본
  `homez.db` SHA-256 불변을 재확인했다.

## ④ 코드 완성과 원본 미적용 상태 구분

| 구분 | 상태 |
|---|---|
| 코드(백엔드 게이트 + 화면 연결) | 완성, 격리 테스트 189건 + 실제 화면(데스크톱·모바일) 검증 완료 |
| 실행 서버 여부 | 이번 검증도 격리 스크래치 서버였다 — 실제 운영 서버는 이번 세션 내내 실행되지 않았다("배포·실행 중"이라고 보고하지 않는다) |
| 원본 `NEEDS_REVIEW` 기록(CH1147184) | 여전히 0행(재확인, §③ 격리 검증과 무관) — 승인안 B 미실행 |
| Migration 미적용에 따른 제한 | 이번 라운드의 신규 코드(체크박스·해제 메서드)는 스키마 변경이 없어 Migration 상태와 무관 — 13차 DB 인덱스·신규 테이블 2개만 여전히 영향받음(14~16차와 동일) |
| 연결 id=4의 인증 최신성(원본) | `verified_at=2026-09-08`, 16일 경과 — 실제 서비스에서 재사용하려면 재검증(실제 상품 조회) 필요(기존 사실 재확인, 변경 없음) |

## ⑤ A/B/C 승인 요청 및 이후 실제 등록 순서

### 승인안 A — 업무 DB Migration 3건 (16차와 동일, 변경 없음)

대상·파일명·순서·해시·백업/복구/사본 검증 결과는 16차 문서와 완전히
동일하다(재확인만, 재작성하지 않음). **미승인, 미실행.**

### 승인안 B — CH1147184 `NEEDS_REVIEW` 원본 기록 (16차와 동일, 변경 없음)

정확한 대상(`company_id=1`, `connection_id=4`, `product_code=CH1147184`)·
과거 접수 근거·검증된 서비스 경로(`record_unconfirmed_prior_evidence()`)는
16차 문서와 동일하다. **미승인, 미실행.**

### 승인안 C — CH1147184 상품 조회 1회 (재확인, 변경 없음)

| 항목 | 값 |
|---|---|
| 대상 재확인 | `connection_id=4`(ONCHANNEL, `sin945`, `CONNECTED`), `product_code=CH1147184` — 원본 DB에서 방금 재조회, 변경 없음 |
| 방법 | `PurchaseChannelConnectionService.lookup_product()`(기존 메서드, 신규 아님) |
| 최대 호출 | 1회, 자동 재시도 없음 |
| 성공 시 로컬 DB 변경(코드 기준) | `connection.verified_at`/`status`/`last_checked_at` 갱신뿐(코드
  `channel_connection_service.py::lookup_product()`/`_record_real_check_success()`
  확인) — 판매신청 테이블에는 이 호출만으로 아무것도 쓰지 않는다 |
| 실패 시(401/403) 로컬 DB 변경 | 연결 상태를 `NOT_CONNECTED` 계열로 낮춤(`_record_real_check_failure()`,
  기존 코드) — 그 외 실패(429/네트워크/형식오류)는 상태를 건드리지 않음 |
| 이 호출이 뜻하지 않는 것 | 상품 조회 성공은 **계정별 판매신청 승인 확인이 아니다**(§외부 확인 ③은
  여전히 공식 방법 없음, 16차와 동일 확정) — 이 호출로 `NEEDS_REVIEW`를
  자동 해제하지 않는다(승인안 B·검토 해제(§②)는 사람이 별도로 판단·
  기록해야 한다) |
| 묵시적으로 포함하지 않는 것 | 추가 인증 호출(연결 인증 갱신은 이 호출의 부수 효과일 뿐 별도 예산이
  아님, 16차에서 이미 확인)·다른 상품 조회 |
| 실행 승인 필요 | **예 — 미승인, 미실행** |

### 이후 실제 등록 순서(참고용, 지금 실행하지 않음)

승인이 접수되면: **① 판매신청 상태 확인**(승인안 C, 상품 상태 정황 확인
1회) **→ ② 승인된 DB 적용**(승인안 A) **및 필요한 기록 보완**(승인안 B,
A와 순서 무관) **→ ③ 기존 Wizard#1 옵션 검토**(15차 이전부터 유지된
체크리스트: 옵션 4종 최신 가격·재고, `supplier_option_links` 반영 여부) **→
④ 승인된 상품등록**(쿠팡, 13차 중복등록 방어 적용된 상태) **→ ⑤ 매핑**
**→ ⑥ 승인된 실주문 시험**(15차 문서 §⑤의 8개 개별 항목 표 그대로 유지,
포괄 승인 표현 사용 안 함).

이미 전달된 상품 URL·확정된 업무 DB 결정(저장소 루트)은 다시 묻지
않았다.

## 검증·Git

집중 테스트 189건(고정 기준선, 6개 테스트 파일 통합 1회 실행) 전부 통과,
실패·오류 0건, 가드 로그 확인. `marketplace_listing` 도메인은 코드 변경이
없어 재실행하지 않았다. 원본 `homez.db` SHA-256 불변 재확인
(`f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008`).
격리 E2E 서버·브라우저 탭은 검증 직후 전부 종료했다(확인:
`python.exe` 0개).

이번 라운드 소유 파일만 커밋한다:
- `app/domains/purchase_task/sales_application_service.py`
- `app/domains/purchase_task/schema.py`
- `app/domains/purchase_task/router.py`
- `app/domains/purchase_task/service.py`
- `app/web/console.js`
- `app/web/console.css`
- `app/web/i18n/ko-KR.js`
- `app/web/i18n/en-US.js`
- `tests/test_purchase_sales_application_service.py`
- `tests/test_purchase_order_submission_service.py`
- `tests/test_purchase_task_router.py`
- 이 문서, `docs/HOMEZ_PROJECT_STATE.md` 갱신

`.claude/launch.json`(gitignore 대상, 이번 검증용 로컬 서버 항목 1개
추가)과 세션 스크래치 디렉터리의 E2E 스크립트·시드 DB는 커밋하지
않는다. 다른 작업자의 워크트리는 건드리지 않았다.
