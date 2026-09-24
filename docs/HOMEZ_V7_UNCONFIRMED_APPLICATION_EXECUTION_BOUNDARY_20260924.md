# HOMEZ V7 — 미확인 판매신청 실행 차단 및 A/B 적용 준비 (2026-09-24, 15차)

이 문서는 14차가 "과거 데이터 보완 전에는 코드로 닫을 수 없는 경계"라고
판정했던 것을 정정하고, 실제로 코드 실행 경계에 최소 확인 게이트를
추가해 CH1147184류 상태(내부 판매신청 기록 없음, 과거 접수 미확인)에서의
자동 실행을 차단한 결과다. 원본 DB Migration 적용, `NEEDS_REVIEW` 원본
기록, 실 자격증명·외부 API 호출, 판매신청·상품등록·주문·발주·결제·환불은
**이번 프롬프트로 승인되지 않았고, 실행하지 않았다.**

## 기준선 재확인

`git fetch` 후 재확인: 로컬 HEAD = 원격 `origin/main` HEAD = `cef1e6c`(14차
커밋). 다른 작업자의 변경·프로세스 없음(작업 트리 clean, 실행 중인
`python*` 프로세스 0개로 시작). 14차의 500건 배치 가드 로그·Migration 3건
사본 검증은 이번 라운드에서 코드·조건이 바뀌지 않은 부분(실 DB 해시
`f3aafca1...` 불변, `migrations/` 디렉터리 무변경)은 재사용하고, 이번
라운드에서 실제로 코드를 바꾼 범위(`order_submission_service.py`,
`schema.py`, `router.py`)만 새로 검증했다. 저장소 전체·786건 전체를
이유 없이 반복하지 않았다.

## ① 정정한 판정

14차 §3-2가 "원본 데이터 보완 전에는 코드로 닫을 수 없는 경계"라고 쓴
것은 **과도한 결론**이었다. 정확히는:

- **코드가 누락된 과거 이력을 자동으로 알아낼 수 없다** — 여전히 사실이다.
  내부 판매신청 행이 0건인 상태에서, 이것이 "진짜 최초 신청"인지 "과거에
  추적되지 않은 방식으로 이미 시도된 적이 있는지"를 코드가 스스로 구별할
  방법은 없다(외부 상태를 조회하지 않는 한).
- **그렇다고 미확인 상태의 실행 자체를 차단할 수 없는 것은 아니다** —
  이것이 14차의 오류였다. 코드가 "확실히 안전하다"를 증명할 수 없다면,
  기본값을 "확실하지 않으면 실행하지 않는다"(fail-closed)로 두면 된다 —
  이 저장소의 다른 모든 게이트(`confirm_real_submission`,
  `override_unresolved_status`)가 이미 쓰고 있는 것과 같은 원칙이다.
  14차는 이 원칙을 이 지점 하나에만 적용하지 않고 있었다.
- 14차의 격리 재현 결과 — 판매신청 POST 1회·발주 Provider 호출 1회가
  실제로 실행됐다는 사실 — 은 그대로 유효한 관측이다. 다만 그 관측에서
  "그러므로 고칠 수 없다"로 건너뛴 것이 오류였다.
- 원본 CH1147184에는 이번 라운드에서도 `NEEDS_REVIEW`를 기록하지
  않았다(§③에서 재확인) — 그래서 **원본에서는 이 보호가 아직 적용되지
  않은 상태 그대로**다. 이 라운드의 코드 수정은 "코드가 보호를 제공할 수
  있게 됐다"는 뜻이지 "CH1147184가 지금 보호받고 있다"는 뜻이 아니다.
  둘을 분리해서 보고한다(②).
- **실행 준비 완료나 위험 해소로 판정하지 않는다** — 코드 방어가 새로
  생겼을 뿐, 원본 DB 적용(승인안 A)과 `NEEDS_REVIEW` 데이터 반영(승인안 B)
  이 실행되기 전까지 실제 서비스에서는 여전히 무방비 상태다.

## ② 코드 방어와 원본 적용 상태의 구분

### 2-1. 신규 코드 방어(격리 검증 완료, 아래 ③)

**`app/domains/purchase_task/order_submission_service.py`** —
`submit_order()`에 `confirmed_first_application: bool = False` 매개변수를
추가했다. 판매신청이 아직 접수 확인되지 않아 자동으로
`ensure_sales_application_submitted()`를 호출하려는 지점(기존 코드,
443~450번째 줄 부근) **직전에** 새 게이트를 추가했다:

```python
existing_application = self._sales_application_service.get_attempt(
    connection.id, company_id, product_code,
)
if existing_application is None and not confirmed_first_application:
    raise ConflictException(
        f"상품({product_code})에 대한 내부 판매신청 기록이 없습니다 — "
        "이것이 확인된 최초 신청인지 이 코드는 알 수 없습니다. "
        "confirm_real_submission=True는 \"실제로 요청을 보내라\"는 승인일 "
        "뿐 \"이것이 확인된 최초 신청이다\"라는 승인이 아닙니다. 최초 "
        "신청임을 확인했다면 confirmed_first_application=True를 별도로 "
        "명시하세요 — 과거 접수 이력이 불확실하면 온채널 공식 화면이나 "
        "공급처 문의로 사람이 먼저 확인해야 합니다.",
    )
```

**설계 근거(지시문 §3 항목별)**:
- **내부 신청 행 없음 / 확인된 최초 신청 / 과거 접수 미확인 구분**:
  `existing is None`(행 없음)은 두 경우(진짜 최초, 과거 미확인) 모두를
  포괄하는 상태다 — `confirmed_first_application`이 True면 "확인된 최초
  신청"으로 취급해 진행, False(기본값)면 "구분 불가 = 미확인"으로 보고
  차단한다. 이미 행이 있는 경우(REJECTED 재시도, NEEDS_REVIEW/
  RESULT_UNKNOWN)는 이 게이트를 거치지 않고 기존
  `ensure_sales_application_submitted()`의 판정을 그대로 탄다.
- **내부 기록 부재만으로 최초 신청 의도를 추정하지 않음**: 이전에는
  `existing is None`이면 자동으로 "최초 신청"으로 추정해 진행했다 — 이제는
  호출자가 명시해야 한다.
- **`confirm_real_submission`이 재실행 승인을 겸하지 않음**: 두 플래그를
  분리했다 — 하나는 "실제로 API를 호출해도 된다", 다른 하나는 "이것이
  확인된 최초 신청이다". 이 라운드 전에는 전자 하나로 둘 다 겸했다.
- **기존 구조 재사용 검토 결과 없음 확인 → 최소 신규 게이트 추가**: 이
  코드베이스에 "확인된 최초 신청"을 나타내는 기존 개념(플래그·상태값·
  승인 레코드)이 있는지 검색했으나 없었다(`grep`으로
  `confirmed_first`/`first_application`/`최초 신청 확인` 등 검색, 0건) —
  그래서 최소한의 새 boolean 플래그 하나만 추가했다.
- **CH1147184 하드코딩 없음**: 상품코드는 함수 인자 `product_code`
  그대로이며, 이 게이트는 어떤 상품코드에도 동일하게 적용된다.
- **모든 신규 상품 영구 차단 방지**: `confirmed_first_application=True`를
  넘기면 정상적으로 진행된다(§3-2 대조군 테스트) — 새 상품마다 사람이
  이 값을 True로 설정하면 기존과 동일하게 자동화가 작동한다.
- **NEEDS_REVIEW/RESULT_UNKNOWN 우회 방지**: 이 게이트는 `existing is
  None`일 때만 관여한다 — 이미 NEEDS_REVIEW/RESULT_UNKNOWN 행이 있으면
  `confirmed_first_application` 값과 무관하게 기존
  `BLOCKS_AUTO_RETRY`/`override_unresolved_status` 판정이 그대로
  적용된다(전용 테스트로 우회 불가 확인, §3-4).

**`app/domains/purchase_task/schema.py`**(`SubmitRealOrderRequest`) +
**`app/domains/purchase_task/router.py`**(`submit_real_order`) — 새
필드 `confirmed_first_application: bool = False`를 요청 스키마에 추가하고
그대로 서비스에 전달했다. **이 라우터가 실제로 이미 배선돼 있었다는
사실을 이번에 재확인했다** — `router.py:1292`가
`PurchaseOrderSubmissionService.submit_order()`를 실제로 호출하며,
`confirm_real_submission`을 요청 바디값 그대로 전달한다(`channel_
connection_service.py`의 오래된 주석 "라우터 미배선"은 이제 사실과
다르다 — 이번 범위 밖이라 그 주석 자체는 고치지 않았다, 후속 과제로만
남긴다). 새 필드를 스키마에 추가하지 않았다면 이 라이브 엔드포인트가
진짜 최초 신청조차 영원히 실행할 수 없게 될 뻔했다 — 그래서 반드시
함께 배선했다.

`sales_application_service.py`(판매신청 서비스 자체)는 **이번 라운드에서
전혀 수정하지 않았다** — 새 게이트는 `order_submission_service.py`의
자동 내부 호출 지점에만 추가했다. 이 서비스를 직접 호출하는 향후 다른
화면(현재는 없음, `order_submission_service.py`가 유일한 호출자)이
생기면 그 호출자가 자신의 문맥에 맞는 확인을 별도로 설계해야 한다 —
이 범위 밖의 가정을 만들지 않기 위해서다.

### 2-2. 원본 DB 적용 상태(이번 라운드에서도 미실행)

원본 `homez.db`의 CH1147184 관련 상태는 14차와 **완전히 동일하다**(읽기
전용 재확인):

```sql
SELECT * FROM purchase_sales_application_attempts WHERE product_code='CH1147184';
-- 0 rows
```

새 코드 방어는 "행이 아예 없으면 명시적 확인 없이는 막는다"는 규칙이지,
"과거 이력을 알아내 자동으로 NEEDS_REVIEW로 바꾼다"는 규칙이 아니다.
그래서 CH1147184에 실제로 이 코드가 적용되면, 그 시점에 누군가
`confirmed_first_application`을 지정하지 않고 재신청을 시도하면 **여전히
차단**된다(원본 데이터가 없어도 이 게이트 자체는 즉시 유효) — 다만
"올바른 다음 행동"(온채널 확인 후 재신청 또는 `record_unconfirmed_
prior_evidence()`로 NEEDS_REVIEW 기록)은 사람이 해야 한다. 이 둘을 다시
구분해 보고한다: **코드는 준비됐다. 원본 데이터(승인안 B)는 아직
반영되지 않았다.**

## ③ A+B 사본 검증 결과

### 3-1. 사본 재사용 판단

14차에서 만든 사본(`homez_copy_20260924.db`, Migration 3건 적용 완료)을
재사용했다 — 원본 `homez.db`가 그 사이 변경되지 않았음을 SHA-256
(`f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008`)으로
재확인했고, 사본 자체의 `PRAGMA integrity_check`도 `ok`였다. 새 사본을
다시 만들 필요가 없었다.

### 3-2. B(정황 기록) — 검증된 서비스 경로로 사본에 실행

`PurchaseSalesApplicationService.record_unconfirmed_prior_evidence()`
(원시 SQL 아님, 기존 검증된 메서드 그대로)를 사본에 대해 실제로 호출했다:

| 항목 | 값 |
|---|---|
| 대상 | `connection_id=4`(ONCHANNEL, `sin945`), `company_id=1`, `product_code=CH1147184` |
| 근거 | `docs/HOMEZ_V7_AUTO_REAPPLICATION_SAFETY_20260923.md` §4-1(2026-09-14 `POST /openapi/seller/product/apply` HTTP 200, 감사 기록) |
| 사건 발생 추정 시각 | 2026-09-14 |
| 기록(확인) 시각 | 스크립트 실행 시각(자동, 사건 시각과 분리 저장) |
| 결과 | `status=NEEDS_REVIEW`, 신규 행 id=1 |
| 감사 로그 | `audit_logs`에 `SALES_APPLICATION_PRIOR_EVIDENCE_RECORDED` 1건 정상 기록 확인 |

**중복 기록 안전성**: 같은 `(connection, company, product)`에 대해
`record_unconfirmed_prior_evidence()`를 한 번 더 호출 — 예외 없이 처리됐고
**같은 행(id 동일)을 그대로 반환**했다(새 행을 만들지 않음, 상태를
훼손하지 않음) — 반복 호출에도 안전함을 확인했다.

### 3-3. 재시작 재현 + 실제 발주 진입점 실행

완전히 새 SQLAlchemy `engine`/`session`/서비스 인스턴스로 "재시작"을
재현했다(B를 기록한 세션과 메모리 상태를 전혀 공유하지 않음 — DB에
커밋된 것만 봄). Credential Store는 `InMemoryCredentialStore` 하나만
썼다(가짜 값만 등록, **실제 Windows Credential Manager는 전혀 열지
않음** — 재시작 후에도 실제 자격증명 저장소는 값을 유지한다는 사실을
대신하기 위해 같은 가짜 스토어 인스턴스를 재사용했을 뿐, 이것이 세션
재시작 재현을 무효화하지 않는다 — DB 세션·서비스 인스턴스는 완전히
새로 만들었다). 발주 Provider는 "호출되면 즉시 `AssertionError`를 던지는"
Fake Adapter로 대체해, 만에 하나 호출이 새어 나가면 시험 자체가 실패하게
설계했다.

```python
order_service.submit_order(
    connection_id=4, company_id=1,
    idempotency_key="ab-full-verify-1", product_code="CH1147184",
    options=[...], recv_name=..., ...,
    confirm_real_submission=True,   # confirmed_first_application 없음
)
```

**결과**:

| 확인 항목 | 결과 |
|---|---|
| 예외 | `ConflictException` — "이 상품·연결의 판매신청 결과를 아직 확신할 수 없습니다(현재 상태: NEEDS_REVIEW) — 자동으로 다시 신청하지 않습니다. 온채널 공식 화면이나 공급처 문의로 현재 판매신청 상태를 사람이 먼저 확인하세요. 확인 후 재신청이 필요하면 그 결과를 근거로 명시적으로 다시 진행해야 합니다. 참고: [정황 증거 반영 — 실제 판매신청 실행 아님] 사건 발생 추정 시각=미상; 기록(확인) 시각=...; 기록자=user#1; 근거=..." |
| 판매신청 POST(`apply_for_sale`) 호출 | **0회** |
| 발주 Provider(`submit_order`) 호출 | **0회** |
| `PurchaseOrderSubmissionAttempt` 생성 행 | **0건** |
| 판매신청 행 상태 | `NEEDS_REVIEW` 그대로(훼손 없음) |
| 화면 표시 | 예외 메시지 자체에 현재 상태·차단 사유·다음 확인 행동(온채널 공식 화면/공급처 문의)이 포함됨 — 기존 전역 오류 표시 경로로 노출(신규 전용 UI는 이번 범위 밖) |

**실제 Credential Store·외부 네트워크**: `HOMEZ_GUARD_LOG` 지정 상태로
전 과정을 실행 — `GUARD_ACTIVE` 확인, `ATTEMPT_BLOCKED` **0건**(실
자원 접근 시도 자체가 없었다는 뜻). 이 결과는 **"원본 적용 후 검증"이
아니다** — 사본(격리 파일 DB)에서의 검증이며, 원본 적용 후 확인은
승인된 로컬 조회로 데이터·스키마만 대조하고 실제 발주 경로를 시험
호출하지 않는 것이 계획이다(승인안 A/B 표에 명시).

## ④ 원본 적용 승인안

### 승인안 A — 업무 DB Migration 적용 (14차와 대조, 목록 동일 확인)

| 항목 | 값 |
|---|---|
| 대상 | `C:\Users\Daum pc\Homez-OS\homez.db`(설치본 제외) |
| 현재 미적용 파일(가정 아님, `plan_pending()`으로 이번 라운드에도 재확인) | 1) `20260918_00_create_order_collection_test_budget_usage_schema.sql` 2) `20260921_00_create_supplier_option_link_schema.sql` 3) `20260924_00_add_marketplace_submissions_live_claim_index.sql` |
| 14차 승인안과 대조 | **동일**(파일명·순서·해시 전부 일치) — 목록이 달라지지 않았으므로 기존 승인안을 그대로 갱신 제출한다 |
| 해시 | `93ca072401873e920365f1974fd70116626544c81cfd9ff0fc880597a481b4b1` / `1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a` / `3e114a08feb2c715958ed6e429771ca8b0b7305a84193202b95c8287d5a2d081` |
| 백업 조건 | 적용 직전 `C:\Users\Daum pc\Homez-Backups\`에 타임스탬프 파일명으로 복사 + SHA-256 기록 |
| 사본 검증 | 14차 §5 전체(integrity/FK/157개 테이블 행 수 대조/ORM 호환/재적용 방지/격리 경쟁 검증) 유효 |
| 복구 조건 | 적용 후 `integrity_check`/`foreign_key_check` 실패 시 백업본 즉시 교체 |
| 자동 실행 비활성화 | 적용 스크립트는 대화형 1회 실행이며 스케줄러·cron·백그라운드 서비스로 등록하지 않는다. A 적용과 B 실행 사이에는 어떤 서버·스케줄러도 기동하지 않는다(둘 다 로컬 DB 파일 작업일 뿐 서버 구동이 필요 없음). |
| 실행 승인 필요 | **예** — 이번 프롬프트로 승인되지 않았고 실행하지 않았다 |

### 승인안 B — CH1147184 원본 `NEEDS_REVIEW` 데이터 반영 (Migration과 별도)

| 항목 | 값 |
|---|---|
| 대상 | `connection_id=4`(ONCHANNEL, `sin945`), `company_id=1`, `product_code=CH1147184` |
| 방법 | 기존 검증된 서비스 경로 `PurchaseSalesApplicationService.record_unconfirmed_prior_evidence()` 그대로 사용(원시 SQL 삽입 없음, §③에서 사본으로 이미 검증) |
| 근거 구분 | 과거 접수 근거(2026-09-14 HTTP 200 감사 기록) / 사건 발생 추정 시각(2026-09-14) / 기록(확인) 시각(실제 호출 시각, 자동) — 셋을 코드가 구조적으로 분리 저장 |
| 결과 상태의 의미 | `NEEDS_REVIEW`는 **성공·승인 상태가 아니다** — "확인 전 실행 차단" 상태다. 이 값을 기록해도 판매신청이 접수되거나 승인된 것으로 처리되지 않는다(`SATISFIES_ORDER_GATE`에 포함되지 않음, 발주로 이어지지 않음). |
| 외부 호출 | **없음** — `apply_for_sale()`을 호출하지 않는다(§③에서 검증) |
| 중복 안전성 | 검증됨(§3-2) — 반복 호출해도 새 행을 만들거나 상태를 훼손하지 않음 |
| Migration과의 관계 | **독립적** — 스키마 변경이 아니라 기존 `purchase_sales_application_attempts` 테이블에 대한 데이터 삽입이다. 승인안 A 적용 여부와 무관하게 실행 가능(이 테이블은 이미 존재함) — 순서를 강제하지 않는다. |
| 실행 승인 필요 | **예** — 이번 프롬프트로 승인되지 않았고 실행하지 않았다 |

### A/B 실제 승인 여부 확인

이번 대화를 포함해 지금까지의 모든 라운드에서 사용자가 "A를 적용하라"/
"B를 기록하라"는 형태의 명시적 승인을 한 적이 없다 — 9차~14차의 승인안
표는 전부 **제안**이었고, 이 문서가 그 제안들을 다시 정리해 제출하는
것 자체를 승인으로 해석하지 않는다. **승인 없이는 적용하지 않았고, 이
문서는 두 승인안을 한 번에 제출하는 것으로 마무리한다.**

## ⑤ 외부 확인 C와 이후 개별 실행 조건

### 외부 확인 C(별도 승인 대상, 유지)

| 항목 | 값 |
|---|---|
| 대상 | `connection_id=4`, `product_code=CH1147184` |
| 방법 | `GET /openapi/seller/product/{code}`(기존 `lookup_product()`) |
| 목적 | 상품 판매 상태(1=판매전~5=품절) 정황 확인 — **이 계정의 판매신청 승인 상태와는 다른 사실**(승인 상태 조회 API 자체가 없음이 공식 확정됨, `HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md`) |
| NEEDS_REVIEW 해제 조건 | 상품 status 값만으로는 해제하지 않는다 — 공식 근거(온채널 판매자센터 화면 직접 확인, 또는 공급처 공식 문의 회신)가 있어야 사람이 `override_unresolved_status=True`로 재개하거나 새 확인 근거로 상태를 갱신할 수 있다 |
| 최대 횟수 | 1회, 재시도 없음 |
| 중단 조건 | 4xx/5xx, 한도(1회) 도달 |
| 확인 안 되면 | 자동 재신청하지 않는다(현재 코드 방어가 이미 이 기본값을 강제한다) |
| 실행 승인 필요 | 예 |

### 이후 실행 범위(개별 항목, 포괄 승인 문구 사용하지 않음)

| # | 항목 | 대상 | 외부 호출 상한 | DB 쓰기 | 재시도 | 중단 조건 |
|---|---|---|---|---|---|---|
| 1 | 시험상품 최종 선택 | 기존 3개 후보 중 확정(사용자 결정, 이미 제공된 URL 재요청 안 함) | 0(내부 결정) | `product_candidates` 확정 플래그 갱신만 | 해당 없음 | 사용자 미확정 시 대기 |
| 2 | Wizard#1 로컬 초안·옵션 보완 | `listing_wizards#1`(현재 `CHANNELS` 단계) | 0(로컬 초안 편집만, 외부 미호출) | `listing_wizards.draft_json` 갱신 | 해당 없음 | 옵션 4종 최신 가격·재고 미확인 시 다음 단계 진행 금지 |
| 3 | 외부 상품·옵션 조회 | 쿠팡 상세조회 2회 한도(9차 기존 예산), 온채널 옵션 조회는 위 외부 확인 C에 포함 | 쿠팡 2회, 온채널 1회(C와 합산) | 조회 결과 캐시만(주문·결제 없음) | 없음(한도 도달 시 중단) | 4xx/5xx, 한도 도달 |
| 4 | 실제 쿠팡 상품등록 | Wizard#1 재개 후 `ListingWizardLiveService.send()` | 1회(성공/실패 확정까지, 13차 중복등록 방어 적용됨) | `marketplace_submissions`, `marketplace_listings` | 결과불명 시 §13차 방어로 자동 재시도 차단(사람 확인 후 승인 재개) | preflight 블로커, DB 인덱스 충돌 |
| 5 | HOMEZ 매핑 저장 | 등록 성공한 쿠팡 옵션 ID ↔ 내부 SKU | 0(로컬 저장) | `supplier_option_links`(원본 미적용 상태이므로 이 단계 전 승인안 반영 필요) | 해당 없음 | 매핑 대상 옵션 ID 없음 |
| 6 | 정책상 허용된 시험 주문 | 쿠팡 판매자센터 시험 주문 정책 회신 확인 후 1건 | 정책 문의 1회 발송(회신 수신은 무제한 대기) | `purchase_tasks`/주문 관련 신규 행 | 없음 | 정책 미회신·미허용 |
| 7 | 금액·수량·배송지 확정된 발주·결제 | 이 승인안이 완료된 실제 발주(§⑤-6 이후) | `submit_order()` 1회(연결당) | `purchase_order_submission_attempts` | 결과불명 시 자동 재시도 금지(기존 방어) | 판매신청 미확인(§외부 확인 C), 포인트·배송비 승인 게이트 미충족 |
| 8 | 별도 반품·환불 시험 | §⑤-7 성공 이후, 별도 사용자 지시 시점 | 반품 API 1회 | 환불 관련 신규 행 | 없음 | 발주 자체가 아직 없음 |

이미 전달된 상품 URL·확정된 결정(예: CH1147184 추천 유지)은 다시
요청하지 않았다 — 위 표의 각 항목은 **독립적으로** 승인 대상이며, 어느
하나의 승인이 다른 항목의 승인을 의미하지 않는다.

## ⑥ 남은 미확인 사항

- CH1147184의 온채널 현재 판매신청 상태(외부 확인 C, 미실행).
- 옵션 4종의 현재 가격·재고(마지막 확인 시점 이후 재확인 필요).
- 매입 계정(연결 id=4)의 재검증 유효기간 — 실제 원본 DB에서는 마지막
  확인 2026-09-08 기준 9일 이상 경과, 실제 재확인 필요 가능성 높음(이
  라운드의 사본 검증에서는 이 문제를 가짜 자격증명으로 우회했을
  뿐이며, **원본에는 그대로 남아 있는 별도 문제**다 — 승인안 A/B와
  무관하게 실제 재검증이 필요하다).
- 쿠팡 판매자센터 시험 주문 정책 회신 여부.
- `channel_connection_service.py`의 "라우터 미배선" 주석이 실제와 다르다는
  사실(§②-1에서 발견, 이번 범위 밖이라 주석 자체는 고치지 않음 — 후속
  과제로 남김, 코드 동작 자체에는 영향 없음).

## 검증·Git

**집중 테스트**(변경 영향 범위): `test_purchase_sales_application_service`
(21건, 무변경 파일 — 재확인만, 전부 통과), `test_purchase_order_
submission_service`(77건, 신규 3건 교체), `test_order_unknown_
resolution_and_tracking_refresh` + `test_purchase_order_approval_
survives_option_link_change`(26건, 기존 호출부에 `confirmed_first_
application=True` 추가), `test_purchase_task_router`(24건, 무변경 확인),
`test_purchase_task_order_submission_review`(26건, 무변경 확인) — 고정
기준선에서 한 번에 실행: **174건 전부 통과, 실패·오류 0건**, 가드 로그
`GUARD_ACTIVE` 확인·`ATTEMPT_BLOCKED` 0건. `marketplace_listing` 도메인은
이번 라운드에서 코드 변경이 없어 재실행하지 않았다.

기존 테스트 기대값·가드·skip은 하나도 약화하지 않았다 — 영향받은
호출부에는 새로 필요해진 `confirmed_first_application=True`만 추가했고,
그 외 어떤 assertion도 바꾸지 않았다. 실행 중 코드·테스트를 추가로
수정하지 않았다(먼저 확신해서 push하지 않고, 결과 확인 후에만 진행).

원본 `homez.db` SHA-256 불변 재확인
(`f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008`).
이번 라운드는 `preview_start` 서버를 띄우지 않았다 — 종료할 별도
프로세스가 없다(확인: 실행 중인 `python.exe` 0개).

이번 라운드 소유 파일만 커밋한다:
- `app/domains/purchase_task/order_submission_service.py`
- `app/domains/purchase_task/router.py`
- `app/domains/purchase_task/schema.py`
- `tests/test_purchase_order_submission_service.py`
- `tests/test_order_unknown_resolution_and_tracking_refresh.py`
- `tests/test_purchase_order_approval_survives_option_link_change.py`
- 이 문서, `docs/HOMEZ_PROJECT_STATE.md` 갱신

DB·백업·자격증명·개인정보가 담긴 로그는 포함하지 않는다(사본·가드 로그는
전부 세션 스크래치 디렉터리에만 있다). 다른 작업자의 커밋과 무관한
파일은 건드리지 않았다.
