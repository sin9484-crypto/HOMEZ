# HOMEZ V7 — 판매신청·상품등록 잔여 실행 위험 마무리 (2026-09-24, 13차)

이 문서는 12차(`docs/HOMEZ_V7_AUTO_REAPPLICATION_SAFETY_20260923.md`) §5-4/§8이
"결함으로 기록만 하고 미수정"으로 명시적으로 남긴 위험 — `MarketplaceSubmission`이
append-only이고 같은 (listing, marketplace_account)에 새 idempotency_key로 새
Submission 행을 만드는 경로에 "미해결 이전 시도" 확인 로직이 없던 문제 — 를 실제
코드·격리 테스트로 마무리한 결과다. **이번 라운드도 원본 DB·실 API·실거래는 전혀
실행하지 않았다** — 코드·격리 테스트·Migration 설계(리허설만)·문서만 변경했다.

## 1. 기준선 재확인 및 정정

- `git fetch` 후 재확인: 로컬 HEAD = 원격 `origin/main` HEAD = `6b1e7e3`. 이 커밋은
  12차 커밋(`0ce43fe`) **다음에** 다른 작업자가 만든 `docs: record Zendrop and
  Syncee supplier monitoring`(문서 전용)이다 — 이번 라운드 시작 시점에 "보고된
  HEAD는 0ce43fe"라는 가정을 그대로 믿지 않고 직접 재확인해 한 커밋 차이를
  잡아냈다. 이 커밋과 그 안의 변경분은 이번 라운드에서 전혀 건드리지 않았다.
- **"6개/7개" 파일 수 정정**: 12차 완료 보고에서 "문서 3개+코드 2개+테스트 2개=7개"
  라고 적었던 것은 **오류**였다. `git show --stat 0ce43fe`로 직접 재확인한 결과
  실제로는 `app/domains/purchase_task/constants.py`,
  `app/domains/purchase_task/sales_application_service.py`,
  `docs/HOMEZ_PROJECT_STATE.md`,
  `docs/HOMEZ_V7_AUTO_REAPPLICATION_SAFETY_20260923.md`,
  `tests/test_purchase_order_submission_service.py`,
  `tests/test_purchase_sales_application_service.py` — **정확히 6개**(코드 2 +
  문서 2 + 테스트 2)다. "문서 2개"를 "문서 3개"로 잘못 센 것이 원인이었다.
- 12차의 "집중 회귀 183건 통과" 주장을 이번 라운드에서 그대로 신뢰하지 않고
  실제로 재실행해 대조했다(§6) — 재실행 결과가 커밋된 코드와 실제로 일치함을
  확인했다.

## 2. CH1147184 보호 공백 재확인 (신규 코드 변경 없음, 읽기 전용 재확인만)

- 12차가 이미 확인한 대로, `NEEDS_REVIEW` **기능은 코드에 존재**하지만
  CH1147184에 대해 **실제로 원본 DB에 기록된 적은 없다**. 이번 라운드에서
  원본 `homez.db`를 읽기 전용으로 재조회해 재확인했다:

  ```sql
  SELECT * FROM purchase_sales_application_attempts WHERE product_code='CH1147184';
  -- 0 rows
  SELECT COUNT(*) FROM marketplace_submissions;
  -- 0
  ```

  "시스템이 NEEDS_REVIEW를 지원한다"는 사실과 "CH1147184에 실제로 NEEDS_REVIEW가
  기록됐다"는 사실을 분리해서 보고한다 — 후자는 **여전히 아니오**다.
- CH1147184는 애초에 `ListingWizard#1`의 `materialized_listing_ids_json=[]`
  상태로 정지해 있어(11차·12차에서 이미 확인, 변경 없음), `marketplace_submissions`
  행 자체가 CH1147184용으로 생성된 적이 없다 — 이번 라운드에서 새로 구현한
  §3의 중복등록 차단은 **아직 이 상품에 적용할 대상 행이 없는 상태**다. 다만
  Wizard#1이 재개되어 실제로 Submission이 처음 만들어지는 순간부터는 §3의
  두 가지 방어(§3-2, §3-3)가 즉시 적용된다(신규 코드가 상품코드를 하드코딩하지
  않고 `(company_id, listing_id)` 조합에 범용으로 걸리기 때문).
- **"실제 보호 대상에 보호가 적용됐다"고 이번 라운드에서 보고하지 않는다** —
  CH1147184에 대해 원본 DB에 아무것도 쓰지 않았기 때문이다. 원본 반영 승인
  제안은 12차 §7 표와 동일하게 유효하며 이번 라운드에서 다시 제출하지 않는다
  (반복 조사 금지 원칙 — 12차 §7을 그대로 참조).

## 3. 결과불명 이후 중복 상품등록 차단 (이번 라운드의 핵심 구현)

### 3-1. 실제 위험 경로 재추적

`MarketplaceSubmission` 생성부터 실제 Provider 호출까지 전체 경로를 다시
따라갔다: `listing_wizard_submission.py::submit_wizard_channels()` /
`submission_service.py::submit()`(구조 검증만, `correlation_id` 절대 설정 안 함)
→ (승인) → `listing_wizard_live_service.py::send()`(실제 외부 호출, `correlation_id`
여기서만 설정). 기존 `(company_id, idempotency_key)` UNIQUE 제약은 **위저드
단위**로 계산되는 idempotency_key(`wizard-{id}-account-{id}-submission`) 때문에,
같은 상품 후보·같은 판매계정으로 **새 위저드**를 하나 더 만들면(또는 동일
listing·selection에 직접 새 `idempotency_key`로 `submit()`을 다시 호출하면)
새 idempotency_key로 완전히 새 행이 생겨 이 제약을 그대로 통과한다는 것을
재확인했다 — 이것이 12차 §5-4가 남긴 정확한 구멍이었다.

### 3-2. 1차 방어 — 애플리케이션 레벨 `preflight()` 검사(신규)

**`app/domains/marketplace_listing/listing_wizard_live_service.py`** — `preflight()`
에 새 블로커 `DUPLICATE_LIVE_ATTEMPT_ON_SAME_LISTING`을 추가했다. 기존
`MarketplaceListingRepository.list_submissions_for_listing()`(기존 메서드 재사용,
신규 작성 아님)으로 같은 `listing_id`를 참조하는 **다른** 행 중 다음 조건을
만족하는 것이 있는지 확인한다:

```
other.correlation_id is not None AND (
    other.external_submission_ref is not None
    OR other.status in (SUBMITTING, UNKNOWN)
)
```

`correlation_id`가 있다는 것은 그 행이 **구조 검증만이 아니라 실제 전송을
시도했다**는 뜻이다(§3-1에서 확인한 대로 `submission_service.submit()`은 이
값을 절대 설정하지 않는다). 명시적으로 `FAILED`로 끝난 행은 이 조건에서
**제외**된다 — 정상적인 "상품 수정 후 재등록" 경로를 막지 않기 위해서다(§3-5
로 대조군 테스트 확인).

### 3-3. 2차(최종) 방어 — DB 레벨 부분 UNIQUE INDEX(신규, TOCTOU 대응)

`preflight()`는 INSERT/UPDATE 이전 SELECT이므로 두 요청이 거의 동시에 그 SELECT를
통과하는 경쟁 상태(TOCTOU)를 원천적으로 막지 못한다. 이를 DB 제약으로 직접
막기 위해 **`app/domains/marketplace_listing/model.py`**의
`MarketplaceSubmission.__table_args__`에 부분 UNIQUE 인덱스를 추가했다:

```python
Index(
    "uq_marketplace_submissions_live_claim_per_listing",
    "company_id", "listing_id",
    unique=True,
    sqlite_where=text(
        "correlation_id IS NOT NULL AND ("
        "external_submission_ref IS NOT NULL OR "
        "status IN ('SUBMITTING', 'UNKNOWN')"
        ")",
    ),
)
```

같은 `(company_id, listing_id)`에 대해 "실제 전송을 시도했고(`correlation_id`
있음) 아직 해소되지 않았거나(`SUBMITTING`/`UNKNOWN`) 이미 성공한
(`external_submission_ref` 있음)" 행은 **동시에 하나만** 존재할 수 있다.
기존 컬럼·데이터는 전혀 건드리지 않았고(인덱스 추가만), 과거 행을 삭제·덮어쓰지
않는다 — append-only 이력은 그대로 보존된다.

같은 파일 `send()`에서 이 인덱스 위반(`IntegrityError`)을 잡아 사용자에게
명확한 사유(`ConflictException`, "다른 제출이 이미 쿠팡 전송을 시작했거나
성공했습니다")로 변환하도록 했다 — 원시 DB 예외가 그대로 노출되지 않는다.

### 3-4. Migration(설계·격리 리허설만, 원본 미적용)

**`migrations/20260924_00_add_marketplace_submissions_live_claim_index.sql`**
(신규 파일) — 위 인덱스와 정확히 같은 DDL. 검증:
- 신선한 임시 SQLite 파일: 76개 Migration 순차 적용, `PRAGMA integrity_check` ok,
  인덱스 생성 확인, 재적용 시 `index ... already exists`로 안전하게 차단됨(설계된
  fail-fast, 결함 아님).
- 저장소 루트 실 DB 백업의 워크카피(`repo_root_dev_WORKCOPY2.db`, 이미 검증된
  `repo_root_dev.db`에서 새로 복사)에 대기 중이던 3개 Migration을 순서대로 적용:
  전부 깨끗하게 적용, integrity ok, FK 위반 0건, 인덱스 존재 확인, 재적용 차단
  확인.
- **원본 `homez.db`에는 이번에도 적용하지 않았다.** 라운드 시작 전/후 SHA-256이
  동일함을 재확인했다: `f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008`
  (이 값은 여러 라운드에 걸쳐 반복 재확인된 것과 동일 — 그 사이 어떤 라운드도
  실제로 원본에 쓰지 않았다는 누적 증거이기도 하다).

### 3-5. 정상 경로 보존 확인(대조군)

같은 listing이 명시적으로 `FAILED`로 끝난 뒤 위저드를 다시 만들어 보내는
경로는 **차단되지 않고 정상 전송·성공**함을 별도 테스트로 확인했다(§6
`test_second_submission_allowed_after_first_is_explicit_failure`) — "기록 없는
상품마다 무조건 재등록 금지"로 과잉 차단하지 않는다는 지시를 코드로 지켰다.

## 4. REJECTED 재시도 정책 재점검 (신규 코드 변경 없음, 코드 추적으로 정확성 재확인)

`sales_application_service.py::apply_for_sale()` 내부 예외 분기를 실제로
추적했다(`app/domains/purchase_task/sales_application_service.py:170-208`):

| 예외 | 분류 | 근거 |
|---|---|---|
| `OnchannelAuthenticationError`/`PermissionError`/`NotFoundError`/`RateLimitedError`/`ValidationError` | `REJECTED` | 온채널 API가 실제로 응답을 반환해 명확한 4xx류 사유를 확정한 경우만 |
| `PurchaseChannelAdapterError`(직접 발생 위치 확인: 자격증명 미등록/자격증명에 인증키 없음/알 수 없는 mall_code) | `REJECTED` | 셋 다 **네트워크 호출 이전에** 확정되는 설정 오류다 — 타임아웃·파싱 실패가 이 경로로 새어 들어올 수 없음을 발생 위치(`channel_adapter.py:726,732,995`)에서 직접 확인 |
| `OnchannelNetworkError`/`OnchannelResponseFormatError` | `RESULT_UNKNOWN` | 네트워크 실패·응답 파싱 실패는 결과가 확정되지 않은 것으로 별도 분류 |
| 그 외 예상 못한 모든 예외 | `RESULT_UNKNOWN`(안전 기본값) | "확실하지 않으면 거절이 아니라 결과불명으로" — 확정되지 않은 실패를 거절로 오인해 재시도를 영구 차단하지 않도록, 반대로 거절을 결과불명으로 오인해 무한 재시도하지도 않도록 함 |

**결론**: 이 분류는 이미 정확하다 — 결함을 발견하지 못했다. 확실한 업무
거절(REJECTED)만 재시도 허용 대상으로 남고, "확실성 없음"은 전부
`BLOCKS_AUTO_RETRY`(`RESULT_UNKNOWN`/`NEEDS_REVIEW`)로 묶여 사람 확인 전까지
자동 재시도되지 않는다. 최초 승인(`confirm_real_submission=True` 1회)이나
`override_unresolved_status` 노출 부재(§5)로, "1회 승인 = 무제한 재신청 허용"
으로 해석될 여지도 구조적으로 없다.

## 5. `override_unresolved_status` 우회 확인 (읽기 전용 재확인)

```
grep -rn "override_unresolved_status\|record_unconfirmed_prior_evidence" app/
```

결과: `sales_application_service.py` 자기 자신과 `constants.py`의 주석 한 줄
외에는 **어디에도 없다** — 어떤 router, 어떤 request schema, 어떤 UI 호출도
이 둘을 참조하지 않는다. `order_submission_service.py`의 유일한 호출부(446번째
줄 부근, 12차부터 무변경)도 여전히 기본값 `False`만 사용한다.

**"현재 노출되지 않음"과 "앞으로도 안전함"을 구분해 보고한다**: 지금은
노출 지점이 없다는 것은 코드 검색으로 확정했지만, 이것이 "영구히 안전"을
보장하지는 않는다 — 향후 누군가 router에 이 플래그를 그대로 통과시키는 엔드포인트를
추가하면 우회가 생긴다. 이번 라운드에서 이를 막는 별도의 구조적 잠금(예:
호출자 화이트리스트, 별도 승인 토큰)은 추가하지 않았다 — 현재 스코프(중복
등록 차단)를 벗어난다고 판단했다(후속 과제로 남김, §8).

## 6. 격리 테스트와 회귀 결과

### 6-1. 신규 파일: 중복등록 차단(6/6 통과)

`tests/test_marketplace_submission_duplicate_registration_guard.py`(신규 파일).
실제 DB 대신 `tempfile` SQLite, 실제 Credential Store 대신 In-Memory, 실제
Provider 대신 스파이(`_DelayedProvider`)만 사용했다.

| 지시문 조건 | 테스트 | 결과 |
|---|---|---|
| 같은 listing, 이전 시도 결과불명(UNKNOWN) → 새 위저드 전송 차단 | `ApplicationLevelDuplicateGuardTestCase::test_second_submission_blocked_after_first_reaches_unknown` | PASS(2차 Provider 호출 0회, `ForbiddenException`에 `DUPLICATE_LIVE_ATTEMPT_ON_SAME_LISTING` 포함) |
| 같은 listing, 이전 시도 성공(SUBMITTED) → 새 위저드 전송 차단 | `test_second_submission_blocked_after_first_succeeds` | PASS(동일) |
| 같은 listing, 이전 시도 명시적 FAILED → 정상 재등록 허용(대조군) | `test_second_submission_allowed_after_first_is_explicit_failure` | PASS(2차 성공, 1회 호출) |
| 다른 회사 → 교차 차단 없음 | `test_different_company_not_affected` | PASS(`NotFoundException`, 회사 경계로 격리) |
| DB 레벨 인덱스가 실제로 두 번째 동시 claim을 거절하는가(순차 SQL) | `DbLevelRaceProtectionTestCase::test_index_rejects_second_concurrent_claim_at_db_level` | PASS(`IntegrityError`) |
| **실제 스레드**(ORM 세션 미공유, 서로 다른 `engine`/`Session`) 경쟁 → 정확히 1승 1패 | `test_real_threads_race_to_claim_same_listing_only_one_wins` | PASS(3.6초, OK 1건/ConflictException 1건/최종 `correlation_id` 있는 행 정확히 1개, `threading.Barrier`로 결정론적 동시 진입 확인) |

가드 로그: 6건 실행 중 `ATTEMPT_BLOCKED` 0건(`GUARD_ACTIVE` 확인됨).

### 6-2. 감사 로그 추가분 재확인

`sales_application_service.py::record_unconfirmed_prior_evidence()`에 감사 로그
(`SALES_APPLICATION_PRIOR_EVIDENCE_RECORDED`, 실패해도 본 작업을 막지 않도록
try/except) 호출을 추가한 뒤 `RecordUnconfirmedPriorEvidenceTestCase`(3건)를
재실행 — 3/3 통과(이 테스트 픽스처엔 `audit_logs` 테이블이 없어 정상적으로
try/except 경로로 흡수됨을 함께 확인).

### 6-3. 집중 회귀 (판매신청·발주)

```
tests.test_purchase_sales_application_service tests.test_purchase_order_submission_service
```
**94개 전부 통과, 실패 0, 오류 0.**

### 6-4. 공유 계약 변경으로 인한 확장 회귀 (Section 7 판단 근거)

`MarketplaceSubmission` 모델에 새 Index를 추가한 것은 **공유 스키마 계약
변경**이다 — `Base.metadata.create_all()`을 쓰는 모든 기존 테스트 픽스처가
이 인덱스를 자동으로 함께 생성하게 되므로, 이 테이블을 다루는 다른 테스트
전부가 회귀 대상이라고 판단했다. `grep -rl "MarketplaceSubmission" tests/*.py`로
관련 파일 전체(24개)를 식별해 두 단계로 나눠 실행했다:

**1단계**(핵심 경로): `test_coupang_live_submission`, `test_listing_wizard_service`,
`test_listing_wizard_option_links`, 신규 파일 — **183개 전부 통과**.

**2단계**(나머지 전부, 한 번에 고정 기준선으로 실행 — 코드 변경 없이):
`test_gate6_candidate_pipeline`, `test_listing_wizard_csv_export`,
`test_listing_wizard_permission`, `test_listing_wizard_reconciliation_router`,
`test_listing_wizard_router`, `test_marketplace_adapter_contract`,
`test_marketplace_fulfillment_eligibility`, `test_marketplace_fulfillment_migration`,
`test_marketplace_listing_concurrency`, `test_marketplace_listing_contract`,
`test_marketplace_listing_referential_integrity`, `test_marketplace_listing_retry_after`,
`test_marketplace_listing_status_sync`, `test_marketplace_listing_status_sync_csv_security`,
`test_marketplace_listing_status_transitions`, `test_marketplace_required_fields_validation`,
`test_marketplace_server_side_guard`, `test_marketplace_submission`,
`test_marketplace_submission_approval`, `test_marketplace_tenant_isolation`,
`test_operational_events_wiring`, `test_submission_reconciliation` — **500개
전부 통과(skipped=1, 기존부터 있던 skip — 이번에 새로 만들지 않음), 실패 0,
오류 0.**

세 배치 합계 **777개 테스트, 실패·오류 0건**. 코드는 실행 중 전혀 수정하지
않았다(먼저 확신해서 push하지 않고, 실행 전에 변경을 모두 모아 고정한 뒤
한 번만 돌렸다). 실행 종료 후 원본 `homez.db` SHA-256을 다시 확인 —
`f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008`로 불변(777개
테스트 전부 임시 파일 SQLite만 사용했다는 결과 기반 증거).

## 7. 검증 환경 정리

이번 라운드는 `preview_start` 서버를 띄우지 않았다(순수 `unittest` 실행뿐).
백그라운드로 실행한 세 회귀 작업은 모두 완료(exit code 0) 확인 후 종료됐다 —
남아 있는 별도 프로세스·서버·모니터링 잡이 없다.

## 8. 완료 보고 요약 (지시문 §8 순서)

**① 이번 라운드에서 확인·수정한 결함**: 12차 §5-4가 기록만 하고 미수정으로
남긴 위험 — `MarketplaceSubmission` append-only 구조에서 새 idempotency_key로
새 행을 만들면 같은 listing에 대한 미해결/성공 이전 시도를 확인하지 못하고
중복 외부 등록이 가능했던 경로. §3-2(애플리케이션 레벨)·§3-3(DB 레벨, TOCTOU
최종 방어)로 수정, §6-1로 검증.

**② 실제로 코드에 구현된 보호**: (a) `preflight()`의
`DUPLICATE_LIVE_ATTEMPT_ON_SAME_LISTING` 블로커, (b) `MarketplaceSubmission`의
부분 UNIQUE 인덱스(모델 레벨, `Base.metadata.create_all()`로 테스트 환경에는
이미 자동 적용됨), (c) `send()`의 `IntegrityError` → `ConflictException` 변환.

**③ 실제 업무 대상(CH1147184 등)에는 아직 적용되지 않은 것**: (a) §3-3의
DB 인덱스는 원본 `homez.db`에 아직 적용되지 않았다(Migration 리허설만 완료,
§3-4) — 원본에 적용되기 전까지는 실 서비스에서 §3-3(최종 방어)이 작동하지
않고 §3-2(애플리케이션 레벨 SELECT 방어)만 작동한다. (b) CH1147184에 대한
`NEEDS_REVIEW` 기록도 여전히 원본 DB에 없다(§2, 12차와 동일 상태 유지).

**④ 검증 결과와 기준선**: §6 참조 — 신규 6건 + 감사로그 3건 + 집중 94건 +
확장 회귀 183건 + 확장 회귀 500건(1 skip) = **총 786건 실행, 실패·오류 0건**,
가드 로그 `ATTEMPT_BLOCKED` 0건, 실 DB 해시 불변. 기준선은 이번 라운드 변경분을
전부 반영한 작업 트리(커밋 전 상태) 기준이다.

**⑤ 여전히 확인되지 않은 외부 사실**: CH1147184의 온채널 현재 판매신청
상태(12차 §3-4 계획대로 여전히 미실행), 옵션 4종의 현재 가격·재고.

**⑥ 원본 DB 변경·실제 조회를 위해 남은 승인**: 12차 §7 표와 동일 + 이번
라운드에서 새로 준비된 것 하나 추가 — **(신규) §3-3 DB 인덱스 Migration의
원본 `homez.db` 적용**(백업 후 적용 여부는 별도 승인 필요, `homez-migration-safety`
스킬 절차 그대로 따름).

**의존 관계(지시문 원문 그대로)**: 판매신청 상태 확인 → 승인된 DB 적용·필요한
기록 보완 → 기존 Wizard 옵션 검토 → 승인된 상품등록 → 매핑 → 승인된 실주문
시험. 이번 라운드는 이 사슬의 첫 단계(판매신청 상태 확인)에 필요한 외부 조회를
실행하지 않았다 — 여전히 승인 대기.

**최종 판정**: 이번 지시문의 8개 절 중 §3(중복등록 차단)을 실제로 구현·검증
완료했다(격리 검증 기준). §1·§4·§5는 재확인 결과 기존 코드가 이미 올바르게
설계돼 있음을 확인(신규 코드 불필요). §2는 CH1147184 실제 반영이 여전히
승인 대기임을 재확인. **"판매신청·상품등록 잔여 실행 위험 중 중복등록 경로는
격리검증 완료"**이며, 이것이 V7 실사용 완료나 원본 DB 반영 완료를 의미하지
않는다.

## 9. Git 상태

이번 라운드 소유 파일만 커밋한다:
- `app/domains/marketplace_listing/listing_wizard_live_service.py`
- `app/domains/marketplace_listing/model.py`
- `app/domains/purchase_task/sales_application_service.py`
- `migrations/20260924_00_add_marketplace_submissions_live_claim_index.sql`(신규)
- `tests/test_marketplace_submission_duplicate_registration_guard.py`(신규)
- 이 문서, `docs/HOMEZ_PROJECT_STATE.md` 갱신

다른 작업자의 커밋(`6b1e7e3`, Zendrop/Syncee 공급처 모니터링 문서)과 그 이후
작업 트리에 남아 있을 수 있는 무관한 변경은 건드리지 않는다. 실 DB·백업·
자격증명·개인정보는 포함하지 않았다.
