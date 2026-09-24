# HOMEZ V7 — 13차 검증 증거 보완 및 Migration 3건 적용 준비 (2026-09-24, 14차)

이 문서는 13차(`docs/HOMEZ_V7_DUPLICATE_REGISTRATION_GUARD_20260924.md`)가 남긴
증거 공백 — 특히 가드 로그 근거 없이 "ATTEMPT_BLOCKED 0건"을 주장한 것과, 786건/
777건 두 숫자를 설명 없이 병기한 것 — 을 보완하고, 온채널 자동 재신청 방어와
쿠팡 중복등록 방어를 명확히 분리해 검증하며, 원본 DB 적용 승인안을 하나로
정리한 결과다. **이번 라운드도 원본 DB·실 API·실거래·정황 증거 기록은 전혀
실행하지 않았다.**

## ① 정정한 주장과 근거

### 1-1. 기준선 재확인(가정하지 않음)

- `git fetch` 후 재확인: 로컬 HEAD = 원격 `origin/main` HEAD = `2787b1f`(13차
  커밋) — 보고된 값을 그대로 믿지 않고 다시 확인했다. 다른 작업자의 커밋(`6b1e7e3`)
  은 여전히 그대로 보존돼 있다.
- **13차 커밋(`2787b1f`)의 실제 파일 수**: `git show --stat`로 재확인한 결과
  **정확히 7개**(코드 3: `listing_wizard_live_service.py`, `model.py`,
  `sales_application_service.py` / 문서 2: `HOMEZ_PROJECT_STATE.md`(수정),
  `HOMEZ_V7_DUPLICATE_REGISTRATION_GUARD_20260924.md`(신규) / Migration 1(신규) /
  테스트 1(신규)) — 13차 완료 보고의 "7개 파일"은 **정확했다**. 정정이 필요했던
  것은 12차 커밋(`0ce43fe`, 6개)이었고 그건 13차 문서에서 이미 바로잡았다.
- 13차 문서 안에서 **"777개 테스트"(§6-4)와 "786건 실행"(§8-④)를 서로 다른
  근거 없이 병기한 것 자체가 오류**였다. 두 숫자는 서로 다른 것을 가리킨다 —
  아래 1-2에서 명확히 분리한다.

### 1-2. 786 vs 777 — 실행 횟수 합계와 최종 고정 기준선 회귀를 분리

13차 라운드에서 `unittest`를 호출한 시점과 대상은 다음과 같다(시간 순):

| # | 시점 | 대상 | 테스트 수 | 비고 |
|---|---|---|---|---|
| 1 | 코드 완성 중간 확인 | `test_marketplace_submission_duplicate_registration_guard`(신규 파일 단독) | 6 | 이후 3번 배치에 **포함되는 부분집합** |
| 2 | 코드 완성 중간 확인 | `test_purchase_sales_application_service::RecordUnconfirmedPriorEvidenceTestCase`(파일 일부) | 3 | 이후 4번 배치에 **포함되는 부분집합** |
| 3 | 최종 고정 기준선 배치 A | `test_coupang_live_submission` + `test_listing_wizard_service` + `test_listing_wizard_option_links` + `test_marketplace_submission_duplicate_registration_guard`(4개 파일) | 183 | 1번을 포함, 서로 다른 파일 3개 추가 |
| 4 | 최종 고정 기준선 배치 B | `test_purchase_sales_application_service` + `test_purchase_order_submission_service`(2개 파일) | 94 | 2번을 포함, 나머지 파일 전체 추가 |
| 5 | 최종 고정 기준선 배치 C | 나머지 marketplace_listing 관련 파일 22개 | 500(skip 1) | 3·4번과 파일 기준 완전히 배타적 |

- **실행 횟수 합계(중복 포함) = 6+3+183+94+500 = 786** — 이 숫자는 "같은 테스트를
  두 번 이상 실행한 횟수까지 그대로 더한 값"이며, **고유 테스트 수가 아니다**.
  실제로 6번(1번 배치)과 3번(2번 배치)에 포함된 테스트는 각각 3번·4번 배치에서
  다시 한 번 더 실행됐다 — 즉 9개 테스트가 이 라운드 동안 2회씩 실행됐다.
- **최종 고정 기준선 회귀(중복 제거, 서로 배타적인 파일 집합) = 183+94+500 = 777**
  — 이것이 "검증 종료 시점의 실제 코드에 대해, 한 번의 고정 기준선에서 수행한
  회귀"의 정확한 범위다. 1·2번(6건·3건)은 코드가 아직 완성되기 전 중간 확인용
  이었고 3·4번 배치에 완전히 포함되므로 최종 집계에서 제외한다.
- **앞으로는 786처럼 실행 횟수 합계를 쓸 때는 반드시 "실행 횟수 합계(중복
  포함)"라고 명시하고, 777처럼 고유 최종 회귀를 쓸 때는 "고정 기준선 회귀
  (중복 제거)"라고 명시한다** — 이번 라운드부터 이 표기 규칙을 지킨다.

### 1-3. 도메인 회귀와 저장소 전체 회귀의 구분

13차의 183건·500건 배치(§6-4)는 **저장소 전체 회귀가 아니다**. 저장소 전체
테스트 파일은 `tests/test_*.py` 기준 **322개**이며, 13차가 실제로 실행한 파일은
`MarketplaceSubmission`을 참조하는 파일 24개 중 4개(183건 배치) + 22개(500건
배치) = 26개, 그리고 `purchase_task` 도메인 파일 2개(94건 배치) 뿐이다 — 합쳐서
**28개 파일**(322개 중 약 8.7%)이다. 이것은 **변경된 모델(`MarketplaceSubmission`)
과 직접 관련된 도메인 회귀**이지 저장소 전체 회귀가 아니다. 13차 문서가 이
구분을 명시하지 않은 것이 부정확했다 — 이번 문서에서 바로잡는다. 이 범위
선택 자체는 정당했다(변경이 `MarketplaceSubmission` 스키마에 한정됐고, 이
테이블을 참조하는 파일 전체를 실행했으므로) — "전체 회귀를 생략했다"가 아니라
"애초에 전체 회귀가 필요한 근거(공용 계약 변경)의 정확한 영향 범위만 정확히
계산해 실행했다"로 정정한다.

## ② 가드 로그 증거 보완

### 2-1. 13차의 "ATTEMPT_BLOCKED 0건" 주장 철회

13차 문서 §6-4에서 500건 배치 실행 당시 `HOMEZ_GUARD_LOG` 환경변수를 **설정하지
않았다**(재확인: 당시 명령에 `HOMEZ_GUARD_LOG`가 없었다). 가드 스크립트
(`tests/support/regression_guard/sitecustomize.py:64,87-98`)를 직접 읽어
확인한 결과, `_LOG`(=`HOMEZ_GUARD_LOG`)가 없으면 `_log()`가 **아무것도 쓰지
않고 조용히 반환**한다 — `GUARD_ACTIVE` 줄도, `ATTEMPT_BLOCKED` 줄도 전혀
기록되지 않는다(감사 훅 자체는 `PYTHONPATH` 설정만으로 설치되어 **차단
기능은 정상 작동**했겠지만, 그 사실을 로그로 확인할 방법이 당시엔 없었다).

**정정**: 13차의 "가드 로그로 확인한 결과 ATTEMPT_BLOCKED 0건"이라는 표현을
철회한다. 정확한 표현은 "당시 로그 경로가 지정되지 않아 확인 근거가 없다 —
테스트 통과와 실 DB 해시 불변은 차단 건수나 자격증명 무접촉의 **대체
증거가 아니다**"이다(지시문이 명시적으로 금지한 대체 추론).

### 2-2. 가드 로깅 메커니즘 자체의 양성 대조(positive control)

로그 경로를 지정했을 때 실제로 의도된 차단 사건이 기록되는지, 이 라운드에서
먼저 확인했다: 기존 자체 시험 `tests/test_regression_guard_selftest.py`를
`HOMEZ_GUARD_LOG` 지정 상태로 실행 — **8/8 전부 통과**. 이 파일의 하위 시험들이
자식 프로세스에서 실제 차단(파일·자격증명 심볼·비루프백 네트워크 등)을 유도한
뒤 로그에 `ATTEMPT_BLOCKED`가 정확히 기록됨을 자체적으로 검증한다. 이 라운드가
지정한 바깥쪽 로그 파일에도 `GUARD_ACTIVE mode=block protected_files=4
protected_dirs=4 identities=56` 한 줄이 정확히 기록됐다(바깥쪽 프로세스 자신은
보호 자원을 건드리지 않으므로 `ATTEMPT_BLOCKED`는 없는 것이 정상).

### 2-3. 증거가 부족했던 500건 배치만 같은 조건으로 재실행

786건 전체나 저장소 전체를 반복하지 않고, 증거가 없던 **500건 배치만** 같은
코드 기준선(`2787b1f`, 재실행 전 작업 트리 clean 재확인)에서 고유 로그 경로로
재실행했다:

| 항목 | 값 |
|---|---|
| 로그 경로 | `<세션 스크래치>/guard_logs/domain_regression_22files_20260924T021356Z.log` |
| 코드 기준선 | `2787b1fe57d00e0e2640ed8f808a2fcf2291778c`(재실행 전후 작업 트리 clean 확인) |
| 시작 | 2026-09-24T02:13:56Z |
| 종료 | 2026-09-24T02:28:43Z |
| 종료코드 | 0 |
| 결과 | `Ran 500 tests ... OK (skipped=1)` |
| 로그 내용 | `GUARD_ACTIVE mode=block protected_files=4 protected_dirs=4 identities=56` 1줄, `ATTEMPT_BLOCKED` **0줄**(양성 대조로 로깅 자체가 작동함을 이미 확인했으므로, 이 0건은 이번엔 실제 근거가 있는 주장이다) |

### 2-4. 가드 차단 사건 발생 여부

이번 라운드의 모든 가드 로그(자체 시험 포함) 어디에도 `ATTEMPT_BLOCKED`가
기록되지 않았다 — 별도 조사할 차단 사건이 없다.

## ③ 온채널 자동 재신청 보호의 코드 상태와 원본 적용 상태 (쿠팡과 분리)

### 3-1. "쿠팡 Submission 없음 → 온채널 위험 없음" 추론을 쓰지 않음

13차 §2가 CH1147184에 대해 "Wizard#1의 `materialized_listing_ids_json=[]`라
쿠팡 Submission 생성 자체가 막혀 있다"고 확인한 것은 **쿠팡 등록 경로에만
해당하는 사실**이다. 온채널 판매신청 자동 재시도 방어(`sales_application_
service.py`)는 `marketplace_listing` 도메인과 완전히 별개의 코드 경로
(`purchase_task` 도메인)이므로, 쿠팡 쪽 상태와 무관하게 독립적으로 재확인해야
한다 — 이번 라운드에서 이를 실제 테스트로 분리해 확인했다(§3-2).

### 3-2. CH1147184류 상태 재현 — 신규 격리 테스트로 공백을 직접 증명

`tests/test_purchase_order_submission_service.py`에 신규 테스트 클래스
`UnrecordedPriorExternalEvidenceGapTestCase`를 추가했다. 상품코드는 하드코딩
하지 않고 이 파일의 공용 `VALID_KWARGS`(`CH1234567`, 실제 접수 증거와 무관한
합성 코드)를 그대로 썼다. 재현한 상태:

- 연결(Connection)은 존재하고 발주 가능 상태(`CONNECTED`)
- `purchase_sales_application_attempts`에 이 상품에 대한 행이 **0건**(CH1147184의
  실제 원본 DB 상태와 동일 — §3-4에서 재확인)
- `NEEDS_REVIEW`는 당연히 기록되지 않음(행 자체가 없으므로)

실제 발주 서비스 진입점(`PurchaseOrderSubmissionService.submit_order()`,
private 메서드 우회 없이 그대로 호출)에서 `apply_for_sale`(판매신청 POST)과
`submit_order`(발주 Provider 호출) 각각의 호출 횟수를 별도 스파이로 측정한
결과:

| 측정 대상 | 실제 호출 횟수 |
|---|---|
| `apply_for_sale`(판매신청 POST) | **1회** |
| `submit_order`(발주 Provider 호출) | **1회** |
| 결과 상태 | `SalesApplicationStatus.SUBMITTED`(정상 최초 신청과 동일 경로) |

**이것은 결함이 아니라 코드로 확인한 설계상 경계다**: 내부 기록이 0건인
상태는 "진짜 최초 신청"과 "실제로는 시도됐으나 추적되지 않은 신청"을 코드가
구분할 방법이 없다 — 이 둘을 구분하려면 사람이 `record_unconfirmed_prior_
evidence()`로 먼저 `NEEDS_REVIEW`를 기록해야 한다. **이 공백은 추가 코드로
닫을 수 없다** — 원본 DB에 데이터(증거 기록)를 보완하기 전까지는 구조적으로
열려 있다. "원본 기록 보완 전 보호 없음"을 코드로 명확히 증명했으므로,
지시문의 "최소 수정"에 해당하는 코드 변경은 없다(닫을 수 있는 코드 결함이
아니기 때문) — 필요한 것은 §7의 데이터 변경 승인이다.

### 3-3. 재시작 후에도 확정된 보호는 유지됨(별도 테이블, 별도 증명 필요)

기존 재시작 검증(`test_result_unknown_attempt_survives_process_restart_and_
still_blocks_retry`)은 `PurchaseOrderSubmissionAttempt`(발주 시도) 테이블
대상이었다 — 이보다 앞 단계인 판매신청 게이트(`PurchaseSalesApplicationAttempt`)
는 다른 테이블·다른 코드 경로이므로 별도로 증명해야 한다. 신규 테스트
`SalesApplicationUnresolvedStatusBlocksOrderTestCase::test_needs_review_
still_blocks_after_process_restart`를 추가해, `NEEDS_REVIEW`가 **이미 기록된
뒤에는** 완전히 새 세션·새 서비스 인스턴스(같은 파일 DB, 메모리 상태 공유
없음)에서도 판매신청 POST·발주 Provider 호출이 모두 0회로 유지됨을 확인했다
— PASS. (§3-2와 대조: 기록이 있으면 재시작 후에도 막히고, 기록이 아예 없으면
애초에 막을 수 없다 — 두 상태를 혼동하지 않는다.)

### 3-4. 원본 DB 재확인(읽기 전용)

```sql
SELECT * FROM purchase_sales_application_attempts WHERE product_code='CH1147184';
-- 0 rows (13차와 동일, 변경 없음)
```

CH1147184에 대해 `NEEDS_REVIEW`는 여전히 원본 DB에 기록되지 않았다. §3-2가
증명한 공백이 CH1147184에 그대로 열려 있다는 뜻이다 — 원본 기록 보완(§7)이
승인되기 전까지는, 만약 어떤 경로로든 CH1147184에 대해 `confirm_real_
submission=True`로 재시도가 발생하면 판매신청 POST가 실제로 나갈 수 있다.

## ④ 쿠팡 중복 등록 방어·인덱스 검증

13차의 `DUPLICATE_LIVE_ATTEMPT_ON_SAME_LISTING` 블로커·부분 UNIQUE 인덱스·
DB 레벨 TOCTOU 방어(§3-2·§3-3, 13차 문서)는 이번 라운드에서 코드 변경 없음 —
재검증도 불필요하다고 판단해 반복하지 않았다(§4-2·§4-3 재검증은 이미 검증된
근거를 재사용). 이번 라운드에서 새로 확인한 것은 **마이그레이션이 실제로
적용된 사본에서도** 이 인덱스가 동일하게 작동하는지(⑤ 참고, 기존 검증은
ORM이 만든 테스트 DB 기준이었고 이번엔 실제 Migration SQL이 만든 스키마
기준이라는 차이가 있다).

## ⑤ Migration 사본 리허설

### 5-1. 미적용 파일 전체를 가정 없이 확인

`MigrationRunner.plan_pending()`을 사본에 대해 직접 실행해 확인한 결과 —
**"3건"을 가정하지 않고 재확인했으며, 실제로 정확히 3건**이었다:

```
20260918_00_create_order_collection_test_budget_usage_schema.sql
20260921_00_create_supplier_option_link_schema.sql
20260924_00_add_marketplace_submissions_live_claim_index.sql
```

### 5-2. 기존 적용 Migration 무결성 재확인

`schema_migrations`에 기록된 **73개** 적용 완료 Migration 전부에 대해
`compute_checksum()`으로 다시 계산한 체크섬과 기록된 체크섬을 대조 —
**불일치 0건**(기존 적용분의 바이트·체크섬은 전혀 변경되지 않았다).

### 5-3. WAL/실행 상태를 고려한 일관된 사본 생성

원본 DB 파일 옆에 `-wal`/`-shm`/`-journal` 사이드카가 **없음**을 확인했고
(`journal_mode=delete`, 즉 롤백 저널 모드), 그럼에도 raw 파일 복사가 아니라
SQLite 공식 백업 API(`sqlite3.Connection.backup()`)로 사본을 만들었다(어떤
저널 모드에서도 안전). 사본은 이 세션의 스크래치 디렉터리(저장소 밖, Git
추적 대상 아님)에만 저장했다.

| 항목 | 원본 | 사본 |
|---|---|---|
| 크기 | 3,702,784 bytes | 3,702,784 bytes |
| SHA-256 | `f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008` | `d2025c696009e884ead705370bbd151d26cb0a490ba36ad62e0780e7495d7a84` |
| `PRAGMA integrity_check` | ok | ok |

SHA-256이 서로 다른 것은 예상된 정상 결과다 — SQLite 백업 API는 페이지 단위로
새 파일을 만들어 파일 헤더·프리리스트 배치가 원본과 바이트 단위로 달라질 수
있다(데이터 동등성은 보장하지만 바이트 동일성은 보장하지 않는다). 대신
**157개 테이블 전체의 행 수를 원본(읽기 전용 연결)과 사본에서 각각 세어
대조 — 불일치 0건**으로 데이터 동등성을 직접 증명했다.

### 5-4. 충돌 데이터 조사(새 인덱스의 실제 조건 기준)

새 인덱스 조건 그대로(`company_id, listing_id` 기준, `correlation_id IS NOT
NULL AND (external_submission_ref IS NOT NULL OR status IN ('SUBMITTING',
'UNKNOWN'))`)로 사본의 `marketplace_submissions`를 조회 — **전체 0행**이라
애초에 조건을 만족하는 행 자체가 없다. 충돌 없음(가정이 아니라 쿼리로 확인).

### 5-5. 사본에 순서대로 적용 및 검증

`MigrationRunner.apply_pending()`으로 3건을 순서대로 적용:

- `plan_pending()`(적용 후): 빈 목록 — 전부 적용됨.
- `PRAGMA integrity_check`: ok.
- `PRAGMA foreign_key_check`: 위반 0건.
- 새 인덱스: `sqlite_master`에 정확한 정의로 존재 확인.
- 다른 두 Migration이 만드는 테이블(`order_collection_test_budget_usages`,
  `supplier_option_links`) 존재 확인.
- **ORM 호환성**: SQLAlchemy `MarketplaceSubmission`/`SupplierOptionLink`
  모델로 실제 쿼리 실행 — 예외 없음. `inspect(engine).get_indexes(...)`로
  새 인덱스가 ORM 리플렉션에도 정확히 나타남을 확인.
- **재적용 방지**: `apply_pending()`을 다시 호출 — 빈 목록 반환(안전한
  no-op, 오류 없이 조용히 건너뜀 — `schema_migrations` 기록 기준으로 판단
  하므로 raw SQL을 직접 재실행할 때의 "index already exists" 오류와는 다른,
  더 안전한 경로).
- **복구**: 기존 두 Migration의 리허설(9차·13차)에서 이미 확인된 BEGIN/COMMIT
  트랜잭션 래핑 구조(각 `.sql` 파일 자체에 포함)를 재확인만 했다 — 이번
  라운드에서 의도적 실패를 주입한 파괴적 복구 시험은 하지 않았다(기존
  확립된 안전장치 재사용, 반복 검증 불필요로 판단).

### 5-6. 인덱스 적용 후 격리 경쟁 검증(실제 마이그레이션 스키마 기준)

13차의 스레드 경쟁 테스트는 ORM(`Base.metadata.create_all()`)이 만든 테스트
DB를 기준으로 했다 — 이번엔 **실제 Migration SQL이 적용된 사본**에서 같은
동작을 별도로 확인했다. 사본에 서로 다른 키(`race-key-A`/`race-key-B`,
서로 다른 `correlation_id`)로 같은 `(company_id=9001, listing_id=9001)`에
대해 순차 INSERT를 시도한 결과, 두 번째 INSERT가 `sqlite3.IntegrityError:
UNIQUE constraint failed: marketplace_submissions.company_id, marketplace_
submissions.listing_id`로 정확히 거절됨을 확인했다 — 서로 다른 키로도 같은
listing에 대한 중복 외부 전송 claim은 실제 마이그레이션 스키마에서도 막힌다.
시험에 사용한 합성 행(`listing_id=9001`)은 검증 직후 사본에서 삭제했다
(사본 자체가 스크래치 디렉터리의 일회용 파일이며, 이 삭제 후에도
`integrity_check`는 ok로 유지된다).

## ⑥ 남은 정확한 승인 (원본 적용 승인안 갱신)

과거 9차 승인안(2건)에 13차의 신규 인덱스를 묵시적으로 얹지 않고, 아래
하나의 승인안으로 갱신한다. **이번 라운드에서도 원본에는 어떤 변경도
하지 않았다.**

### 승인안 A — 원본 `homez.db` Migration 적용 (신규 통합, 이전 9차 승인안을 대체)

| 항목 | 내용 |
|---|---|
| 대상 DB | `C:\Users\Daum pc\Homez-OS\homez.db`(저장소 루트 — 기존 결정 유지, 설치본 제외) |
| 적용 전 상태 | 크기 3,702,784 bytes, SHA-256 `f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008`, `integrity_check` ok, 적용 완료 Migration 73건(체크섬 전수 재확인 완료) |
| 적용 대상(순서대로, 3건) | 1) `20260918_00_create_order_collection_test_budget_usage_schema.sql`(`93ca072401873e920365f1974fd70116626544c81cfd9ff0fc880597a481b4b1`) 2) `20260921_00_create_supplier_option_link_schema.sql`(`1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a`) 3) `20260924_00_add_marketplace_submissions_live_claim_index.sql`(`3e114a08feb2c715958ed6e429771ca8b0b7305a84193202b95c8287d5a2d081`) |
| 사본 리허설 결과 | §5 전체 — integrity ok, FK 위반 0, 데이터 동등성(157개 테이블 행 수 대조) 확인, ORM 호환 확인, 재적용 안전, 인덱스 적용 후 격리 경쟁 검증 통과 |
| 백업 조건 | 적용 직전 `homez.db`를 `C:\Users\Daum pc\Homez-Backups\`에 타임스탬프 포함 파일명으로 복사 + SHA-256 기록(기존 V2.3 적용 시 관례와 동일) |
| 복구 조건 | 적용 후 `integrity_check`/`foreign_key_check` 실패 시 백업本으로 즉시 교체, 각 Migration 파일의 주석에 있는 수동 rollback(`DROP INDEX`/`DROP TABLE`)은 백업 복원이 불가능할 때만 최후 수단으로 사용 |
| 실행 승인 필요 | 예 — 이 표 자체가 실행이 아니라 제안이다 |

### 승인안 B — CH1147184 원본 `NEEDS_REVIEW` 데이터 반영 (Migration과 별도, 데이터 변경)

Migration(스키마)과 무관한 **데이터 변경**이므로 별도 항목으로 유지한다.
`record_unconfirmed_prior_evidence(connection_id=4, company_id=..., product_
code='CH1147184', mall_code='ONCHANNEL', evidence_summary=..., event_
occurred_at=2026-09-14, recorded_by=...)` 실제 호출 — §3-2가 증명한 공백을
실제로 닫는 유일한 방법이다. 실행 승인 필요.

### 승인안 C·D — 기존 독립 항목(변경 없음, 재확인만)

- C: CH1147184 상품 상태 조회 1회(연결 id=4, `GET seller/product/{code}`,
  §3-4 계획대로 여전히 미실행).
- D: 시험상품 최종 확정, 쿠팡 판매자센터 문의, Wizard#1 재개, 실제 등록·
  매핑·실주문·발주·결제·환불 — 전부 기존 문서(12차·13차)의 표를 그대로
  유지, 이번 라운드에서 순서·조건 변경 없음.

## Git 상태

이번 라운드 소유 파일만 검토해 커밋한다: `tests/test_purchase_order_submission_
service.py`(신규 테스트 2건 추가 — 온채널 재현 테스트, 재시작 지속성 테스트),
이 문서. 다른 작업자의 커밋과 13차까지의 기존 파일은 건드리지 않았다. 실
DB·백업·자격증명·개인정보를 포함한 로그는 이 저장소에 커밋하지 않는다(전부
세션 스크래치 디렉터리에만 존재).
