# HOMEZ V7 — 최초 신청 확인 권한 점검 및 A/B 실제 적용 승인 준비 (2026-09-24, 16차)

이 문서는 15차가 추가한 `confirmed_first_application` 게이트의 권한 경계를
집중 점검하고, 준비된 원본 적용 승인안 A/B를 최종 형태로 제출한 결과다.
**원본 DB 변경·Migration 적용·`NEEDS_REVIEW` 기록, 실제 자격증명 사용,
외부 API·상품등록·발주·결제는 이번 프롬프트로도 승인되지 않았고 실행하지
않았다.**

## 기준선 재확인

`git fetch` 후 재확인: 로컬 HEAD = 원격 `origin/main` HEAD = `af467ad`(15차
커밋) — 보고값을 그대로 신뢰하지 않고 재확인했다. 작업 트리 clean, 실행
중인 `python*` 프로세스 0개로 시작. 다른 작업자의 별도 워크트리
(`.claude/worktrees/quirky-diffie-3718ca/`)가 존재함을 확인했고 — **이
디렉터리는 전혀 건드리지 않았다**(다른 세션의 독립 작업 공간). 15차의
174건 집중 검증과 A+B 사본 검증은 이번 라운드에서 코드·조건이 바뀌지
않았으므로(사본·원본 해시 불변) 재사용하고, 이번에 실제로 손댄 파일
(`tests/test_purchase_order_submission_service.py`,
`tests/test_purchase_task_router.py`)만 새로 검증했다. 동일 전체 감사·
전체 회귀를 반복하지 않았다.

## ① 최초 신청 권한 경계 검증 결과

### 1-1. 전달 경로 추적: UI → 요청 스키마 → 라우터 → 서비스

| 단계 | 확인 결과 |
|---|---|
| UI(`app/web/console.js:4817`) | **신규 발견**: 실제 발주 실행 버튼이 `confirm_real_submission: true`를 이미 하드코딩해 보내고 있으나, `confirmed_first_application`은 전혀 보내지 않는다(요청 바디에 키 자체가 없음) — Pydantic이 기본값 `False`로 채운다. |
| 요청 스키마(`schema.py::SubmitRealOrderRequest`) | `confirmed_first_application: bool = False`(15차 추가) — 필드가 빠지면 항상 False, 자동으로 `True`가 되는 경로 없음(Pydantic 기본값 동작을 코드로 재확인). |
| 라우터(`router.py::submit_real_order`) | `consume_recent_auth_token(...)`(재인증) → `require_permission(db, current_user, "VIEW_SENSITIVE_DATA")`(권한) 통과 후에만 `service.submit_order(..., confirmed_first_application=data.confirmed_first_application)` 호출 — **이 값은 confirm_real_submission과 별개의 필드로 그대로 전달될 뿐, 어디서도 파생·강제 True 처리되지 않는다**(코드에 그런 분기 없음, grep으로 재확인). |
| 서비스(`order_submission_service.py::submit_order`) | `existing_application is None and not confirmed_first_application`일 때만 차단 — 이미 행이 있으면(REJECTED/NEEDS_REVIEW/RESULT_UNKNOWN/SUBMITTED) 이 값과 무관하게 기존 판정을 그대로 탄다. |

**결론**: `confirmed_first_application`은 (a) 기본값 False가 코드 전 구간에서
유지되고, (b) `confirm_real_submission`에서 자동 파생되지 않으며, (c) 이
값 자체를 "외부 상태가 확인됐다는 증거"로 저장·해석하는 코드가 어디에도
없다(순수하게 이번 호출의 실행 여부만 결정하는 제어 플래그) — 단순
클라이언트 boolean을 외부 증거로 취급하지 않는다는 지시를 코드로 만족한다.

**UI 미배선 발견(중요, 이번 범위에서 수정하지 않음)**: 위 표에서 드러난 대로,
`console.js`의 실제 발주 버튼은 이 새 필드를 아직 보내지 않는다 — 이
버튼이 실제로 배포·실행되는 순간, CH1147184처럼 내부 기록이 0건인
**모든** 상품(과거 이력 유무와 무관하게 진짜 신규 상품 포함)에 대해
이 버튼을 눌러도 새 `ConflictException`으로 막힌다(안전한 방향의 실패 —
잘못된 자동 실행이 아니라 실행 자체가 멈춘다). **이것은 결함이 아니라
"UI가 아직 이 새 확인 단계를 따라오지 못한 상태"다** — 후속 과제로
명시한다(별도 UI 작업 필요, 이번 프롬프트의 "새 기능 추가 금지" 범위
밖이라 이번 라운드에서 구현하지 않음).

### 1-2. 권한·승인 구조 재사용 — 누가 확인했는지 식별 가능성

기존 코드로 이미 충족됨을 확인했다 — **신규 코드를 추가하지 않았다**:

- 이 엔드포인트는 이미 `X-Recent-Auth-Token`(비밀번호 재확인)과
  `VIEW_SENSITIVE_DATA` 권한을 요구한다(15차 이전부터 존재하는 게이트,
  변경 없음) — `confirmed_first_application=True`를 실제로 보낼 수 있는
  사람은 이미 이 두 조건을 통과한 사람으로 좁혀진다.
- `sales_application_service.py::_get_or_create_attempt()`(변경 없음,
  기존 코드)가 새로 생성되는 `PurchaseSalesApplicationAttempt` 행에
  `triggered_by=current_user.id`와 `started_at`을 그대로 저장한다 — 그래서
  "확인된 최초 신청"으로 통과된 시도는 **누가, 언제** 실행했는지 해당 행
  자체에 이미 남는다.
- 결론: 재인증·권한 게이트(기존) + `triggered_by`/`started_at` 기록(기존)
  조합으로 "누가 어떤 계정·상품에 대해 최초 신청을 확인했는지" 식별
  가능하다 — 별도의 신규 감사로그 작성은 **불필요**로 판단했다(추가하면
  같은 정보를 이중으로 남기는 것일 뿐이다).

### 1-3. CH1147184 같은 과거 접수 증거 상품을 최초 신청으로 처리하지 않음

CH1147184는 원본 DB에서 `purchase_sales_application_attempts` 행이
0건이므로(§②에서 재확인), 코드 구조상 이 상품도 "내부 기록 없음" 상태로
분류된다 — 즉 **`confirmed_first_application` 없이는 여전히 차단된다**.
이것이 "과거 접수 증거가 있는 상품을 최초 신청으로 처리하지 않는다"는
요구와 상충하지 않는 이유: 이 게이트의 역할은 "확실하지 않으면 막는다"
이지 "CH1147184를 특별히 안다"가 아니다 — 상품코드 하드코딩이 없으므로
코드는 CH1147184를 다른 상품과 구별하지 않는다. **실제로 CH1147184가
"과거 접수 증거가 있는 상품"으로 올바르게 취급되려면, 사람이 그 사실을
알고 있는 이상 `confirmed_first_application=True`를 보내면 안 된다** —
이는 코드가 강제할 수 없는 부분이며, 승인안 B(§③)가 원본에 `NEEDS_
REVIEW`를 기록해 이 상품을 "내부 기록 없음" 상태에서 "내부 기록 있음
(확인 필요)" 상태로 옮겨 코드가 구조적으로 막게 만드는 것이 근본
해결책이다.

### 1-4. NEEDS_REVIEW/RESULT_UNKNOWN 우회 불가 재확인(신규 테스트 2건)

기존 15차 테스트는 NEEDS_REVIEW 우회 불가만 확인했다 — 이번에 RESULT_
UNKNOWN도 별도로 확인했다(같은 `BLOCKS_AUTO_RETRY` 튜플에 있어 코드
경로는 동일하지만, 지시문이 둘을 각각 명시했으므로 각각 증명):

```
tests/test_purchase_order_submission_service.py
  ::UnconfirmedFirstApplicationBoundaryTestCase
    ::test_confirmed_first_application_does_not_bypass_result_unknown
```

`confirmed_first_application=True`를 함께 보내도 `RESULT_UNKNOWN` 행이
있으면 여전히 `ConflictException`으로 막히고 판매신청·발주 호출 모두
0회임을 확인했다 — PASS.

### 1-5. 정상 최초 신청 경로(권한 있는 명시적 확인 후 진행) — 기존 대조군 재확인

15차 `test_zero_internal_rows_with_explicit_confirmation_proceeds_
normally`가 이미 증명 — 재확인만, 변경 없음.

### 1-6. 다른 회사·계정·상품 재사용 차단(신규 테스트)

```
::UnconfirmedFirstApplicationBoundaryTestCase
  ::test_confirmation_for_one_target_does_not_satisfy_a_different_product_or_company
```

한 상품(A)에 대해 `confirmed_first_application=True`로 정상 진행된 뒤,
**같은 회사·같은 연결의 다른 상품(B)**과 **다른 회사의 연결**에 대해
`confirmed_first_application` 없이 호출하면 둘 다 여전히 차단됨을
확인했다(스파이 호출 횟수가 1에서 늘지 않음) — 이 플래그는 매 호출의
`(connection, company, product)` 조합에만 적용되고 어디에도 캐시·재사용
되지 않는다는 것을 구조적으로 증명한다.

### 1-7. 라우터 레벨 권한 경계(신규 테스트)

```
tests/test_purchase_task_router.py
  ::OrderApprovalRouterTestCase
    ::test_confirmed_first_application_true_does_not_bypass_recent_auth
```

요청 바디에 `confirmed_first_application=True`가 있어도 재인증 토큰이
없으면 `UnauthorizedException`으로 즉시 막힌다(서비스 계층까지 도달하지
않음) — 이 값이 재인증·권한 검사를 우회하는 새 경로가 아님을 확인했다.

## ② 코드 보호와 원본 적용 상태

**"원본은 무방비"라는 표현을 쓰지 않는다** — 아래 네 가지를 구분해
기록한다:

| 구분 | 상태 |
|---|---|
| 수정 코드의 기본 차단 기능 | `af467ad`(15차)에 커밋됨, 이번 라운드 §①에서 격리 테스트 79건 + 라우터 테스트 25건으로 재검증 — **정상 작동 확인** |
| 실제 실행 서버가 그 코드를 사용 중인지 | **아무 서버도 실행 중이지 않다**(이번 세션은 `preview_start`를 한 번도 쓰지 않았고, `python.exe` 프로세스 0개로 시작·종료했다) — 그래서 "이 코드가 지금 실서비스에서 요청을 막고 있다/뚫려 있다" 어느 쪽도 사실이 아니다. 배포된 서버가 없으므로 "실행 중"이라는 서술 자체가 성립하지 않는다. |
| 원본 DB의 `NEEDS_REVIEW` 기록 | CH1147184에 대해 여전히 0행(§③ 사본 검증과 별개로 원본 재확인) — 승인안 B 미실행 상태 |
| Migration 미적용에 따른 스키마 상태 | 원본 `homez.db`에는 13차 인덱스를 포함한 3건이 여전히 미적용 — 다만 **이 15/16차 게이트는 스키마 변경이 필요 없다**(기존 `purchase_sales_application_attempts` 테이블만 사용) — 그래서 Migration 미적용이 `confirmed_first_application` 게이트의 작동 여부에는 영향을 주지 않는다. Migration 미적용이 실제로 제한하는 것은 13차의 쿠팡 중복등록 DB 레벨 최종 방어(부분 UNIQUE 인덱스)와 두 신규 테이블(`order_collection_test_budget_usages`, `supplier_option_links`)뿐이다. |

## ③ A/B의 정확한 승인 요청

### 승인 여부 재확인

이번 대화를 포함해 지금까지 어느 라운드에서도 "A를 적용하라"/"B를
기록하라"는 사용자의 명시적 승인 문구를 받은 적이 없다 — 9~15차의 표는
전부 제안이었다. **이번에도 실행하지 않았고, 아래 두 안을 최종 제출
형태로만 제시한다.**

### 승인안 A — 업무 DB Migration 3건

| 항목 | 값 |
|---|---|
| 대상 | `C:\Users\Daum pc\Homez-OS\homez.db`(설치본 제외) |
| 현재 미적용 파일(재확인, 가정 아님) | `20260918_00_create_order_collection_test_budget_usage_schema.sql` → `20260921_00_create_supplier_option_link_schema.sql` → `20260924_00_add_marketplace_submissions_live_claim_index.sql` |
| 15차 승인안과 대조 | **완전히 동일**(파일명·순서·해시 전부 일치, 재확인 완료) |
| 해시 | `93ca072401873e920365f1974fd70116626544c81cfd9ff0fc880597a481b4b1` / `1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a` / `3e114a08feb2c715958ed6e429771ca8b0b7305a84193202b95c8287d5a2d081` |
| 사본 리허설 | 14~15차에서 완료(integrity/FK/157개 테이블 행 수 대조/ORM 호환/재적용 방지/격리 경쟁 검증) — 조건 불변이라 재실행하지 않음 |
| 백업 조건 | 적용 직전 `C:\Users\Daum pc\Homez-Backups\`에 타임스탬프 파일명으로 복사 + SHA-256 기록 |
| 적용 중 서버·스케줄러 | 적용 스크립트 실행 동안 어떤 서버·스케줄러도 기동하지 않는다(현재도 0개, 계속 0개 유지) |
| 적용 후 확인 방법 | **로컬 조회로만**(`PRAGMA integrity_check`, `PRAGMA foreign_key_check`, 인덱스·테이블 존재 확인) — 실제 발주 진입점을 시험 호출하지 않는다 |
| 복구 조건 | 위 확인 중 하나라도 실패하면 백업본으로 즉시 교체 |
| 실행 승인 필요 | **예 — 미승인, 미실행** |

### 승인안 B — CH1147184 `NEEDS_REVIEW` 원본 기록

| 항목 | 값 |
|---|---|
| 정확한 대상 | `company_id=1`, `connection_id=4`(ONCHANNEL, `sin945`), `product_code=CH1147184` |
| 과거 접수 근거 | `docs/HOMEZ_V7_AUTO_REAPPLICATION_SAFETY_20260923.md` §4-1 — 2026-09-14 `POST /openapi/seller/product/apply` HTTP 200(감사 기록) |
| 방법 | `PurchaseSalesApplicationService.record_unconfirmed_prior_evidence()`(검증된 서비스 경로, 원시 SQL 삽입 없음) — 15차 사본 검증에서 이미 실행·확인 완료 |
| 시각 구분 | `event_occurred_at=2026-09-14`(사건 발생 추정) / 기록(확인) 시각은 호출 시점 자동 기록 — 코드가 구조적으로 분리 저장 |
| 기록의 의미 | **`NEEDS_REVIEW`는 성공·승인 기록이 아니다** — 확인 전 실행을 차단하는 상태다(`SATISFIES_ORDER_GATE`에 포함되지 않음, 발주로 이어지지 않음) |
| 외부 호출 | **0회**(15차 사본 검증에서 `apply_for_sale()` 호출 없이 상태만 기록됨을 확인) |
| 중복 기록 안전성 | 검증됨(15차) — 같은 대상에 반복 호출해도 새 행을 만들지 않고 같은 행을 그대로 반환, 상태 훼손 없음 |
| Migration과의 관계 | 독립적 — 기존 테이블에 대한 데이터 삽입이므로 승인안 A 적용 여부와 무관하게 실행 가능 |
| 실행 승인 필요 | **예 — 미승인, 미실행** |

**대상·내용이 일치할 때만 진행**: 향후 실제 승인이 있을 경우, 그 승인이
위 표의 대상(연결 id=4/company_id=1/product_code=CH1147184, Migration
파일 3건 해시 일치)과 정확히 일치하는지 다시 한번 대조한 뒤에만
실행한다 — 표현이 비슷하다는 이유로 다른 대상에 적용하지 않는다.

## ④ 외부 확인별 호출 계획(분리)

기존 코드(`channel_connection_service.py::lookup_product()`,
`_record_real_check_success()`)를 직접 추적해 확인한 사실: **CREDENTIAL
방식(온채널) 연결은 실제 상품 조회(`lookup_product`) 성공이 유일한
"연결 확인" 경로다** — 성공하면 그 호출 하나가 `verified_at`을 즉시
갱신한다(코드 주석 "실제 API 호출 성공(lookup_product/lookup_order)만이
verified_at을 채운다"). 그래서 아래 ①·②는 **같은 API 호출 1회**이고
③만 완전히 별개다:

| # | 확인 항목 | 공식 경로 | 목적 | 최대 호출 | 재시도 | DB 쓰기 |
|---|---|---|---|---|---|---|
| ①+② | 연결 인증 최신성 확인 + CH1147184 상품·옵션 조회 | `GET /openapi/seller/product/CH1147184`(`PurchaseChannelConnectionService.lookup_product()`, 기존 메서드) | 상품 판매 상태(1~5) 정황 확인이 **주 목적**이며, 성공 시 연결의 `verified_at`이 부수 효과로 갱신됨(코드 사실, 별도 호출 추가 아님) | **1회**(기존 승인 예산 그대로, 인증 확인용으로 별도 호출을 추가하지 않는다) | 없음 | `connection.verified_at`/`status`/`last_checked_at` 갱신(연결 메타데이터만, 판매신청 테이블에는 쓰지 않음) |
| ③ | 계정별 판매신청 승인 상태 확인 | **공식 방법 없음**(승인 상태 조회 API 자체가 없다고 온채널이 공식 확정, `HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md`) | 해당 없음 | 0(호출할 API가 없음) | 없음 | 없음 |

**상품 판매상태를 계정별 판매신청 승인으로 대체하지 않는다**: ①+②의
결과(status enum 1~5)는 정황 증거일 뿐이며, 이 값만으로 `NEEDS_REVIEW`를
해제하거나 판매신청이 승인됐다고 판정하지 않는다 — 공식 확인 방법(③)
자체가 없으므로, 이 부분은 **영구히 미확인 상태로 유지하고 자동
재신청하지 않는다**(현재 코드 기본값이 이미 이를 강제한다). 사람이
온채널 판매자센터 화면을 직접 열어 확인하거나 공급처에 공식 문의하는
것만이 유일한 해제 경로다(기존 계획, 변경 없음).

## ⑤ 승인 후 바로 수행할 다음 작업(순서, 참고용 — 지금 실행하지 않음)

실제 승인이 접수되면 다음 순서로 진행한다(각 단계는 §③·§④ 표의 독립
승인 항목에 대응하며, 이 문서 자체가 그 승인이 아니다):

1. 승인안 A 적용(백업 → 사본 재확인 → 원본 적용 → 로컬 조회로만 확인,
   실제 발주 진입점 시험 호출 없음) — 서버·스케줄러 정지 상태 유지.
2. 승인안 B 실행(`record_unconfirmed_prior_evidence()` 1회 호출) — A와
   순서 무관, 독립 실행 가능.
3. 외부 확인 ①+②(상품 조회 1회) — 승인 시.
4. 그 결과에 따라 §④의 "확인 안 되면 자동 재신청하지 않는다" 원칙을
   유지한 채 이후 8개 실행 항목(15차 문서 §⑤ 표, 변경 없음)을 각각
   개별 승인받아 진행.

이미 전달된 상품 URL·확정된 업무 DB 결정(저장소 루트)은 다시 묻지
않았다.

## 검증·Git

**집중 테스트**(변경 파일 2개, 고정 기준선 통합 실행):
`tests.test_purchase_order_submission_service`(79건, 신규 2건 추가) +
`tests.test_purchase_task_router`(25건, 신규 1건 추가) = **104건 전부
통과, 실패·오류 0건**, 가드 로그 `GUARD_ACTIVE` 확인·`ATTEMPT_BLOCKED`
0건. `sales_application_service.py`/`marketplace_listing` 도메인은 이번
라운드에서 코드 변경이 없어 재실행하지 않았다(15차·13차 검증 유효).
기존 테스트 기대값·가드·skip은 하나도 약화하지 않았다.

원본 `homez.db` SHA-256 불변 재확인
(`f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008`).
이번 라운드도 `preview_start` 서버를 띄우지 않았다 — 종료할 프로세스가
없다(확인: `python.exe` 0개).

이번 라운드 소유 파일만 커밋한다:
- `tests/test_purchase_order_submission_service.py`
- `tests/test_purchase_task_router.py`
- 이 문서, `docs/HOMEZ_PROJECT_STATE.md` 갱신

다른 작업자의 워크트리(`.claude/worktrees/quirky-diffie-3718ca/`)와 그
안의 파일은 전혀 건드리지 않았다. DB·백업·자격증명·개인정보는 포함하지
않는다.
