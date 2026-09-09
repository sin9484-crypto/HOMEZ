# HOMEZ V7 종합 현황 보고서 (2026-08-28)

> **정정 기록(2026-08-28 23:35, Phase 1 완료)** — 기준선 회귀
> (3101 tests, Section 0)에서 실패했던 2건을 정확히 수정했다.
> **단, 전체 3101개를 다시 실행해 "failures=0"을 확정하지는
> 않았다** — 사용자 지시(Phase 1은 실패했던 2개 테스트 파일 +
> Migration 관련 집중 테스트만 재실행하고, 전체 회귀는 모든 코드
> 수정이 끝난 뒤 정확히 1회만 실행)에 따라 지금은 해당 2개 파일
> (28 tests)과 이 저장소의 Migration 관련 테스트 파일 전체(35개
> 파일, 474 tests)만 재실행해 전부 통과를 확인했다. 이전 3101/
> failures=2 숫자는 삭제하지 않고 Section 0에 그대로 남겨뒀다.
> 상세는 이 파일 맨 아래 "Phase 1 완료 기록" 참고.

작성자: Claude (이 세션 단독 조사)
목적: 우선순위 결정을 위한 증거 기반 현황 보고. 완료 선언이 아니다.
근거: 실제 코드, 실제 운영 DB(읽기 전용), `docs/HOMEZ_PROJECT_STATE.md`
(4800줄), `docs/V6_EXECUTION_LEDGER.md`, `docs/V7_RELEASE_AUDIT_REPORT_20260827.md`,
`docs/V7_LIVE_PENDING_STATUS.md`, `docs/HOMEZ_V7_KNOWN_LIMITATIONS.md`,
`git status`/`git log`, 실제 `migrations/` 디렉터리, 이번 세션에서 직접
실행한 테스트. 이전 대화 요약은 근거로 쓰지 않았다.

이 보고서는 지시받은 원칙을 그대로 따른다: 쿠팡 등록 실패를 단일
원인으로 확정하지 않는다. 특정 작업을 P0로 단정하지 않는다. 확인되지
않은 것은 `UNCONFIRMED`로 표시한다. 진행 중인 작업(Fabric.js, 대기 상품
정리)은 폐기하지 않았다.

---

## 0. 전체 회귀 결과 추가 보고 (2026-08-28 23:12 완료)

사용자 지시(정지 없이 대기 → 완료 후 1~10번 절차만 수행)에 따라 아래를
그대로 기록한다. 이 실행 이후 어떤 코드 수정·Migration·DB 조사·재설치·
외부 API 호출도 하지 않았다.

1. **Ran N tests**: `Ran 3101 tests in 4059.972s`(67분 40초). 명령:
   `python -m unittest discover -s tests -p "test_*.py"`. 로그:
   `scratchpad/full_regression_20260828_audit.log`(816줄, 113,695 bytes,
   보존됨).
2. **실패·오류·skip 수**: `FAILED (failures=2)` — **failures=2,
   errors=0, skipped=0**(요약 줄에 errors/skipped 항목이 없다는 것
   자체가 각각 0건이라는 뜻).
3. **로그 보존**: 위 경로에 원본 그대로 보존, 수정하지 않았다.
4. **실패 2건 — 테스트명과 오류 원문(수정 없음)**:

   **실패 1**
   ```
   FAIL: test_real_homez_db_has_media_listing_package_tables_applied
   (test_media_listing_package_migration.RealDatabaseAppliedTestCase.
   test_real_homez_db_has_media_listing_package_tables_applied)
   ----------------------------------------------------------------------
   AssertionError: Items in the second set but not the first:
   'source_url'
   'source_domain'
   'source_classification' : media_assets: 실제 DB 컬럼과 Model 컬럼 불일치
   ```
   원인(수정 없이 사실만 기술): 이 세션이 오늘 `MediaAsset` 모델에
   `source_url`/`source_domain`/`source_classification` 3개 컬럼을
   추가했지만 대응 Migration(`20260828_01_...`)을 실제 DB에는 아직
   적용하지 않았다(지시대로 미적용 유지 중) — 이 테스트는 "실제 DB
   컬럼 = 현재 Model 컬럼"을 확인하는 계약 테스트라 정확히 이
   불일치를 그대로 감지한 것이다. **테스트가 정상 동작한 것이지,
   회귀가 아니다.** 단, 아직 "실패 0건"은 아니므로 있는 그대로 보고한다.

   **실패 2**
   ```
   FAIL: test_exactly_one_new_migration_file_added
   (test_permissions_timestamps_migration.MigrationStaticContractTestCase.
   test_exactly_one_new_migration_file_added)
   ----------------------------------------------------------------------
   AssertionError: Lists differ: [...] != [...]
   First list contains 3 additional elements.
   First extra element 38:
   '20260828_00_create_image_rights_evidence_schema.sql'
   ```
   원인(수정 없이 사실만 기술): 이 테스트는 2026-08-21 시점 Migration
   파일 목록을 고정 리스트로 하드코딩해 "그 이후 새 파일이 추가되지
   않았는지"를 확인하는 낡은 스냅샷 테스트다. 오늘 이 세션이 추가한
   Migration 3개(및 그 이전에 추가된 다른 파일들)를 반영하지 못해
   실패한다. **이 세션은 이 테스트를 수정하지 않았다**(사용자가 코드
   수정을 금지했으므로) — 있는 그대로 보고만 한다.

5. **프로세스·리스너 종료 확인**: 회귀 종료 후 python/uvicorn 프로세스
   0개. 테스트가 사용한 포트(60591/60597/60611/60617/60620/60627) 전부
   LISTENING 상태 아님. 현재 남아있는 LISTENING 포트(10530/10531/
   16106/16116/23401/31026/31027/34581/59051)는 HOMEZ와 무관한 다른
   프로세스(PID로 확인, 이 세션이 띄운 것 아님).
6. **개발·운영 DB 시작·종료 해시 비교**:

   | DB | 회귀 시작 전 | 회귀 종료 후 |
   |---|---|---|
   | 개발(`C:\Users\Daum pc\Homez-OS\homez.db`) | SHA-256 `7688b8b0...`, mtime 2026-08-27 19:02:36 | **동일**: SHA-256 `7688b8b0...`, mtime 2026-08-27 19:02:36 |
   | 운영(`%LOCALAPPDATA%\HOMEZ\data\homez.db`) | SHA-256 `7357d900...`, mtime 2026-08-28 09:11:34 | **동일**: SHA-256 `7357d900...`, mtime 2026-08-28 09:11:34 |

   두 DB 모두 해시·mtime이 회귀 시작 전과 완전히 동일하다.
7. **운영 DB 접촉 여부**: 위 6번의 해시/mtime 불변이 가장 확실한
   증거다 — **접촉되지 않았다.** 로그에서 발견된 desktop 부트스트랩
   관련 오류 메시지("DB 경로 불일치 감지", "채널 정책 카탈로그 시딩
   실패", "테스트 강제 시딩 실패" 등, `storage/logs/desktop-launcher.log`
   22:29:25 / `logs/homez.log` 22:35:33)는 전부
   `tests/test_homez_desktop.py`/`tests/test_live_gate4_fix_defects.py`
   류가 `%TEMP%\homez_gate4_run_v6rg3ats\official.db` /
   `...\DIFFERENT.db`라는 **임시 격리 경로**와 mock webview를 의도적으로
   사용해 "경로 불일치 감지"·"시딩 실패 처리" 같은 안전장치 자체를
   검증하는 테스트였다 — 실제 운영 경로 문자열이 이 로그 어디에도
   등장하지 않는다.
8. (이 섹션 자체가 8번 — 종합 보고서 반영 완료.)
9. **변경 파일·Migration 재확인**: `git status --short` 269개 항목(회귀
   전과 동일 — 이 세션은 회귀 실행 중 어떤 파일도 만들거나 수정하지
   않았다). Migration 여전히 **38 적용/3 pending**
   (`20260828_00/01/02`, 회귀 전과 동일한 목록 — 아무 것도 적용되지
   않았다).
10. **대기**: 다음 작업을 시작하지 않고 사용자 지시를 기다린다.

**이 실행으로 과거 2943/2943(2026-08-27) 수치를 대체한다 — 이제부터
"현재 회귀 결과"는 3101 tests, failures=2, errors=0이다.** 두 실패는
전부 이 세션이 오늘 추가한 변경(신규 컬럼/신규 Migration 파일)에 대해
기존 낡은 계약 테스트가 정직하게 반응한 것이며, 실제 코드 결함이
아니다 — 그러나 "결함이 아니다"라는 이 판단 자체도 사용자가 검토할
수 있도록 원문을 그대로 남겨둔다.

---

## 1. 현재 작업 상태 (Section 0)

- **진행 중인 대규모 미시작 작업**: Fabric.js 기반 수동 이미지 편집기
  MVP. 라이브러리(v7.4.0, MIT, `app/web/vendor/fabric.min.js`) 벤더링과
  채택 조건 확인(라이선스/CSP/오프라인/PyInstaller 등록)까지만
  완료됐고, **에디터 자체 코드는 한 줄도 작성되지 않았다**. 되돌릴
  "진행 중" 코드가 없다 — 손실 위험 없음.
- **실행 중인 작업**: 이 보고서 작성을 위해 백그라운드로 실행한 전체
  회귀(`python -m unittest discover -s tests -p "test_*.py"`)가 보고서
  작성 시점 기준 아직 실행 중이다. 완료 시 별도로 결과를 추가 보고한다
  (Section 9 "새로 실행하지 않는다"는 보고서 작성 이후 시점에 적용되는
  것으로 해석했다 — 이 실행은 보고서의 증거 확보용으로, 새 기능이
  아니다).
- **저장된 변경**: 이 세션에서 만든 모든 파일은 Edit/Write 즉시
  디스크에 저장된다 — "저장 안 된 상태"가 존재하지 않는 도구 구조다.
- **검증되지 않은 작업**: 없음 — 이 세션이 만든 모든 코드는 최소
  포커스 테스트로 검증했다(아래 Section 5).
- **중단 시 손실 가능한 작업**: 없음.

---

## 2. 지금까지 수행한 테스트 이력 요약 (Section 1)

25개 항목 중 **이 세션이 직접 만든 이력**은 항목 19(대기 상품 정리),
20(이미지 URL 가져오기), 21(권리 증빙), 22(Fabric.js 채택만) 4개뿐이다.
나머지는 `docs/HOMEZ_PROJECT_STATE.md`/`V6_EXECUTION_LEDGER.md`에 기록된
**과거 세션(Claude/Codex 혼재)의 문서화된 이력**이다 — 이 세션이 직접
관찰한 것이 아니라 문서를 근거로 인용한 것임을 명시한다.

| # | 항목 | 최신 문서화 날짜 | 환경 | 최신 판정 | 근거 |
|---|---|---|---|---|---|
| 1 | Live Gate 0~4 | 2026-08-16~17 | 실 운영 DB(0~2), 격리 EXE(3~4) | `LIVE_GATE_4_COMPLETE` | PROJECT_STATE.md:1271 |
| 1 | "Live Gate 5/6" | — | — | **문서에 그 이름으로 존재하지 않음** | grep 결과 0건. 별도 `Gate V-0~V-6`(2026-08-08~11, `GATE_V_LIVE_APPROVALS_COMPLETE`)와 `정식 출시 Gate 0~10`(2026-08-15~16)이 각각 다른 계열로 존재 — 사용자가 말한 "5/6"이 어느 계열인지 UNCONFIRMED |
| 2 | 설치·제거·재설치 | 2026-08-27 | 격리+공식 1회 | 설치 성공, 자동실행 옵션 무시 결함 잔존(LOW) | V7_RELEASE_AUDIT_REPORT_20260827.md §9-10 |
| 3 | 운영 DB 복구 | 2026-08-24 | 실 운영 DB | 복구 완료, SHA-256 기록 | PROJECT_STATE.md:4442 |
| 4 | Migration 적용 | 2026-08-24(24→35), 이후 35→38 | 실 운영 DB | **현재 38/41 적용, 3건 pending(전부 이번 세션 신규분)** | 직접 `MigrationRunner.diagnose()` 실행 확인 |
| 5 | 쿠팡 Credential 연결 | 2026-08-19(최초) / 2026-08-23(재검증 문서상) | 실 운영 DB(읽기전용) | **현재 실 DB는 2026-08-19 상태로 관측됨 — 아래 Section 3 참고** | 직접 쿼리 |
| 6~14 | 상품후보~8단계승인 | 2026-08-27 | 격리/Fake | `ISOLATED_VERIFIED` (기능별로 다름, Section 4 표 참고) | V7_RELEASE_AUDIT_REPORT_20260827.md §5 |
| 15 | 실제 쿠팡 상품등록 시도 | 2026-08-26~27(코드), 실행은 없음 | — | **실행 기록 0건** | Section 3 |
| 16~17 | 주문/재고/매입/배송/반품/정산 | 2026-08-15~16(Gate 3~8) | Fake | `ISOLATED_VERIFIED`, Live Provider 없음 | KNOWN_LIMITATIONS.md §F |
| 18 | 중앙 운영 알림 | 2026-08-23 | 격리 | 29개 카탈로그 중 실제 연결 12개(그 후 미확인 갱신 없음) | PROJECT_STATE.md:64,4537 |
| 19 | 대기 상품 정리 | **2026-08-28(이 세션)** | 격리 실서버 Browser E2E | `LISTING_WIZARD_SOFT_DELETE_ISOLATED_UI_VERIFIED` | 이 세션 직접 수행 |
| 20 | 이미지 URL 가져오기 | **2026-08-28(이 세션)** | Fake Transport, 실네트워크 없음 | `V7_IMAGE_STUDIO_SECTION1_CODE_COMPLETE` | 이 세션 직접 수행 |
| 21 | 권리 증빙 경고 | **2026-08-28(이 세션)** | 임시 DB | 코드+테스트 완료 | 이 세션 직접 수행 |
| 22 | Fabric.js 편집기 | **2026-08-28(이 세션)** | — | **채택 확인만 완료, 에디터 미착수** | Section 1 |
| 23 | 반응형 UI | 2026-08-24(Section 4) | 격리 | 실결함 1건 발견·수정, 별도 UI 부채 있음 | PROJECT_STATE.md:4539 |
| 24 | 전체/집중 회귀 | 2026-08-27 문서상 2943/2943, 이 세션 실행 중 | — | Section 5 | — |
| 25 | 패키징·공식 설치본 | 2026-08-27 | 격리 빌드+공식 설치 1회 | 성공(자동실행 결함 잔존) | V7_RELEASE_AUDIT_REPORT_20260827.md §9-10 |

각 항목의 세부 근거는 위에 인용한 원본 문서/파일을 그대로 따른다 —
이 세션은 요약만 하고 원본 서술을 바꾸지 않았다.

---

## 3. 쿠팡 실제 상품등록 시도 종합 (Section 2) — 가장 중요한 발견

### 3.1 문서상 서술된 마지막 상태 (2026-08-27 보고서 기준)

- Wizard #6: `SUCCEEDED/RESULTS`
- Listing #2: `SUBMITTED`(HOMEZ 내부 상태 — 실제 쿠팡 응답 아님)
- Submission #3: 내부 `PENDING`, **외부 접수번호 없음, HTTP 상태 없음,
  correlation id 없음** — 문서 자체가 "실제 POST 미실행"이라고 명시
- StoreConnection id=2, `CONNECTED`, credential_version=6,
  last_verified_at=2026-08-23 10:54:12
- 최신 8단계 승인(#6)은 2026-08-26에 만료

### 3.2 이 세션이 실제 운영 DB를 직접 읽기 전용으로 조회한 결과

```
marketplace_submissions = 0행
marketplace_listings    = 0행
marketplace_accounts    = 0행
marketplace_channels    = 0행
listing_wizards         = 3행, 전부 status=DRAFT/current_step=SOURCE,
                           created_at=updated_at=2026-08-19 (한 번도 수정된 적 없음)
store_connections       = 1행, id=1, credential_version=5,
                           last_verified_at=2026-08-19 07:27:50
audit_logs              = 4행, 최신 항목이 2026-08-24 07:24:17
                           (CHANNEL_POLICY_RULE_CATALOG_SEEDED)
schema_migrations       = 38행(마지막 적용: 20260827_01, applied_at
                           timestamp은 2026-08-26T22:19:40으로 기록됨)
```

### 3.3 재검증 결과(2026-08-28, 사용자 요청에 따른 절대경로·SHA-256·백업 대조)

사용자가 "8월 27일 보고서가 실제로 어느 DB를 조회했는지 절대경로·
SHA-256·프로세스 환경·로그 근거로 재검증하라"고 요청해 아래를
직접 확인했다(전부 읽기 전용, 어떤 백업도 복원하지 않았다).

**개발 DB도 확인**: `app/desktop/paths.py::get_data_dir()`는 패키징
모드(`is_frozen()`)에서만 `%LOCALAPPDATA%\HOMEZ\data`를 쓰고, 개발
모드(venv python 직접 실행)에서는 저장소 루트 `homez.db`를 쓴다 —
두 경로가 물리적으로 다른 파일이다. 개발 DB(`C:\Users\Daum pc\
Homez-OS\homez.db`)를 직접 열어보니 `listing_wizards=0`,
`store_connections=0`, `audit_logs=12`(전부 Migration/Permission
부트스트랩 관련) — **Wizard #6 데이터는 개발 DB에도 없다.** 즉
"세션이 개발 DB를 운영 DB로 착각했다"는 가설은 기각된다.

**백업 파일 대조로 실제 타임라인 확정**: `C:\Users\Daum pc\
Homez-Backups\` 아래 실제 백업 파일들을 SHA-256과 함께 직접 열어
비교했다.

| 백업 파일 | 파일 mtime | schema_migrations | listing_wizards | submissions | 비고 |
|---|---|---|---|---|---|
| `v7_release_20260827\homez_pre_migration_36_37.db` | 2026-08-26 13:47:52 | 35 | **6건**(Wizard #6 포함) | **3건** | SHA-256 `6f0c155bee...` — 2026-08-26/27 보고서가 명시한 "운영 DB" 해시와 **정확히 일치**. Wizard6/Listing2/Submission3/StoreConnection id=2 데이터가 **실재했음을 확정**. |
| `v7_migration_36_37_20260827\..._053600.db` | 2026-08-27 05:52:30 | 35 | **3건**(전부 DRAFT, 2026-08-19 생성) | **0건** | 이미 되돌아간 상태. StoreConnection id=1/version=5로 이미 원복. |
| `v7_migration_38_20260827\homez_pre_migration_38.db` | 2026-08-27 07:19:01 | 37 | 3건(동일) | 0건 | 동일하게 원복된 상태 유지. |
| `live_submission_prep_20260827\homez_pre_image_rights_fix.db` | 2026-08-27 22:45:30 | 38 | 3건(동일) | 0건 | 동일. |
| **현재 실제 운영 DB**(2026-08-28) | 2026-08-28 09:11:34 | 38 | 3건(동일) | 0건 | 동일. |

`listing_wizards` 3건의 `created_at`(2026-08-19 04:54:02.199969 /
07:47:22.251321 / 07:51:32.470380)과 `store_connections`의
`credential_version=5`는 **위 표의 원복된 4개 스냅샷 전부에서
마이크로초 단위까지 완전히 동일**하다 — 이는 우연이 아니라 전부
같은 복원 계보(2026-08-24 사고 복구에 쓰인
`homez_pre_saffron_test_20260820_000955.db` 계열)에서 나온 것임을
뜻한다.

**확정된 사실**: Wizard #6/Listing #2/Submission #3/StoreConnection
id=2 상태는 **2026-08-26 13:47:52에는 실제 운영 DB에 존재했다**
(해시로 확정). 그런데 **2026-08-27 05:52:30 시점에는 이미
2026-08-19/08-24 시점의 옛 상태로 되돌아가 있었다**(같은 날 아침,
2026-08-27 보고서 자신의 작업 세션 초반). 그 이후(05:52→38개
Migration이 적용된 현재까지) 이 되돌아간 상태는 **한 번도 다시
바뀌지 않았다** — 즉 재설치를 여러 번 했어도 매번 같은 옛 백업
계보로 다시 수렴한 것으로 보인다(스키마만 그때그때 새로 적용됨).

**미확인으로 남기는 것**: 2026-08-26 13:47:52~2026-08-27 05:52:30
사이(약 16시간 창) 정확히 어떤 명령/프로세스가 파일을 교체했는지는
이 세션이 접근 가능한 증거(문서, 백업 파일, 현재 DB)만으로는
특정할 수 없다 — Windows 이벤트 로그나 그 시간대의 실제 터미널
기록이 없다. 다만 이 시간대는 2026-08-27 보고서 자신의 "패키징
재개"·"최종 설치 1회" 작업 구간과 겹치고, 이 저장소는 2026-08-24에
**같은 클래스의 사고**(설치/제거 스크립트가 실제 사용자 데이터를
덮어씀)를 이미 한 번 겪은 전례가 있다 — 그래서 "재설치 과정에서
사용자 데이터가 보존되지 않는 기존 결함"이 이번에도 작동했을
가능성이 가장 유력한 가설이지만, **이 세션은 이것도 확정하지
않는다.** 사용자가 실제로 무엇을 실행했는지에 대한 기억/로그가
이 시간대에 있다면 그것이 최종 근거가 된다.

### 3.4 결론

- **실제 쿠팡 서버에 상품등록 POST가 성공적으로 전송되고 외부 상품
  ID를 받은 기록은 문서·현재 DB 어디에도 없다.** 2026-08-27 보고서
  자체가 "실제 쿠팡 상품등록 POST 미실행"이라고 명시했고, 현재 DB는
  그보다도 이전 상태로 관측된다.
- 따라서 **"실제 등록 성공 미확인"**이며, 동시에 **"실제 등록 실패가
  기록된 시도" 자체도 이 세션이 확인한 범위에서는 없다** — 코드
  구현은 있으나 외부 호출 자체가 실행된 적이 없다.
- 사용자가 "쿠팡 상품등록 실패"를 실제로 목격했다면, 그 시점의 로그·
  화면·오류 메시지가 이 세션이 접근한 문서/DB에는 남아 있지 않다.
  **UNCONFIRMED — 사용자 확인 필요.**

---

## 4. 결함 목록 (Section 3, 정규화)

| ID | 현상 | 근본원인 확실성 | 심각도 | 상태 |
|---|---|---|---|---|
| V7-DB-001 | 운영 DB 업무데이터가 2026-08-19 시점으로 관측(위 3.3) | 미확인(가설 4개) | **Critical(추정) — 사용자 확인 전까지 미확정** | OPEN, 조사 필요 |
| V7-INSTALLER-001 | Inno Setup 자동실행 옵션 해제해도 `[Run]`이 실행됨 | 확인됨(재현됨) | Low | OPEN, 미수정 |
| V7-NAV-001 | `console.js::applyPermissionGatedNav()`가 `user.role`을 대문자 비교, 서버는 소문자(`super_admin`) 반환 → `data-permission` 붙은 nav 항목 대부분 숨김 | 확인됨(직접 재현, 이 세션 발견) | Medium~High(관리자 UX 차단) | OPEN, 별도 Task로 분리(`task_c6b92473`, 사용자가 별도 세션에서 시작함) |
| V7-PERM-001 | `permissions` 테이블 Model↔DB 컬럼 드리프트(`created_at`/`updated_at` vs `company_id`) | 확인됨(2026-08-16) | Medium | 2026-08-17 Migration으로 수정·검증됨(`LIVE_GATE_4_COMPLETE` 근거) — **CLOSED** |
| V7-COUPANG-001 | 실제 쿠팡 POST 제출 자체가 한 번도 실행되지 않음 | 확인됨(설계상 게이트) | 정보성(결함 아님, 의도된 안전장치) | 사용자 최종 승인 대기 |
| V7-NOTIF-001 | 알림 카탈로그 29개 중 17개는 정의만 있고 트리거 코드 없음 | 확인됨 | Medium(기능 범위, 결함 아님) | 문서화됨, 후속 작업 |

이 세션이 직접 새로 발견한 결함은 V7-DB-001(간접 발견, 조사 중)과
V7-NAV-001 두 건뿐이다. 나머지는 기존 문서에서 이미 정규화된 것을
그대로 인용했다.

---

## 5. 기능별 실제 사용 가능 상태 (Section 4)

`docs/V7_RELEASE_AUDIT_REPORT_20260827.md` §5 표를 그대로 인용하고
(2026-08-27 기준), 이 세션에서 상태가 바뀐 항목만 갱신했다.

| 기능 | 상태(2026-08-27 문서 기준) | 이 세션 갱신 여부 |
|---|---|---|
| 관리자·로그인·권한 | ISOLATED_VERIFIED | 변경 없음(단, V7-NAV-001 발견) |
| 상품 후보 | ISOLATED_VERIFIED | 변경 없음 |
| 이미지 처리 | ISOLATED_VERIFIED | **URL 가져오기 백엔드 추가**(CODE_COMPLETE, UI 미연결) |
| 쿠팡 카테고리·고시정보 | LIVE_INPUT_REQUIRED | 변경 없음 |
| 가격·마진 | ISOLATED_VERIFIED | 변경 없음 |
| 쿠팡 실제 상품 제출 | CODE_ONLY | 변경 없음(실행 안 함) |
| 주문·재고 | ISOLATED_VERIFIED | 변경 없음 |
| 공급처 검색/발주 | PROVIDER_PENDING | 변경 없음 |
| 배송·반품·정산 | ISOLATED_VERIFIED | 변경 없음 |
| 운영 알림 | ISOLATED_VERIFIED | 변경 없음 |
| 백업·복원 | LIVE_VERIFIED | 변경 없음(단, 3.3의 DB 불일치가 이 판정과 충돌할 수 있음 — 재확인 권장) |
| 업데이트 | CODE_ONLY | 변경 없음 |
| 설치·제거 | LIVE_RETEST_REQUIRED | 변경 없음 |
| **대기 상품 정리** | 없음(신규) | **NEW — ISOLATED_UI_VERIFIED(이 세션)** |
| **권리 증빙 경고** | 부분(RIGHTS_UNVERIFIED 정책만) | **정책 반전 + 증빙등록 기능 CODE_COMPLETE(이 세션)** |
| **Fabric.js 편집기** | 없음 | **라이브러리 채택만 완료, 기능 자체는 NOT_IMPLEMENTED** |

---

## 6. 테스트 신뢰도 구분 (Section 5)

| 구분 | 최신 수치 | 근거 |
|---|---|---|
| 전체 회귀(문서상 마지막 확정치) | 2943/2943 (2026-08-27) | V7_RELEASE_AUDIT_REPORT_20260827.md §4 |
| 전체 회귀(이 세션 실행, 2026-08-28 23:12 완료) | **3101 tests, failures=2, errors=0, skipped=0(4059.972초)** | Section 0 참고. 실패 2건 전부 이 세션 자신의 신규 변경(미적용 컬럼/신규 Migration 파일)에 대한 낡은 계약 테스트 반응 — 원문 그대로 Section 0에 기록 |
| 집중 회귀(대기 상품 정리, 이 세션) | 131/131 | 서비스30+라우터15+마이그레이션2파일(각 7,7 테스트)+기존 관련 파일 재확인 |
| 집중 회귀(이미지 스튜디오 Section 1, 이 세션) | 99/99 | url_import 18 + product_page_extraction 9 + url_import_service 11 + rights_evidence 10 + rights_evidence_migration 13 + source_tracking_migration 8 + packaging/allowlist 3 + long_detail_ingest/public_hosting 재실행분 |
| 격리 Browser E2E(이 세션) | 대기 상품 정리 1건 시나리오 세트(개별삭제/선택삭제/복원/전체삭제/모바일) | 이 세션 직접 클릭 검증, 격리 스크래치 DB만 사용 |
| 공식 설치본 E2E | 2026-08-27 1회(성공, 자동실행 결함 잔존) | V7_RELEASE_AUDIT_REPORT_20260827.md §10 |
| 실제 쿠팡 API 조회/쓰기 | **0회** | Section 3 |
| 실제 주문 | **0회** | KNOWN_LIMITATIONS.md §F |
| 사용자 수동 검증 | 2026-08-27 최종 설치 승인, 이전 세션들의 Migration 승인 다수 | PROJECT_STATE.md 각 절 |

테스트 파일 총 220개(현재 저장소 실측). 위 수치들은 서로 다른 시점·
범위의 결과이며 합산하지 않았다.

---

## 7. Migration·운영 DB 상태 (Section 11)

- 저장소 Migration 파일: **41개**(실측, `ls migrations/*.sql`)
- 운영 DB 적용: **38개**, pending **3개**(전부 2026-08-28 이 세션 신규
  작성 — `20260828_00/01/02`, 이미지 권리증빙/이미지 출처추적/대기상품
  소프트삭제). 운영 DB에는 적용하지 않았다(지시 준수).
- 운영 DB `integrity_check=ok`, 크기 2,695,168 bytes, mtime
  2026-08-28 09:11:34.
- **Section 3의 데이터 불일치 때문에, "Migration이 최신"이라는 사실과
  "업무 데이터가 최신"이라는 사실을 분리해서 봐야 한다** — 스키마는
  앞서 있지만 데이터는 뒤처져 있다.

---

## 8. 설치본·패키징 상태 (Section 12)

- 마지막 공식 설치: 2026-08-27, `HOMEZ-Setup-2.1.0.exe`
  (SHA-256 `8BF468AEDD78E63ECC6A075C68580070E439C298646025947055FD3492BB5B30`),
  설치·기동·로그인 성공.
- 이 세션(2026-08-28)의 코드 변경(이미지 URL 가져오기, Fabric.js 벤더링,
  대기 상품 정리)은 **아직 어떤 빌드에도 포함되지 않았다** — 재빌드
  필요.
- 자동실행 옵션 무시 결함(V7-INSTALLER-001) 잔존.

---

## 9. 외부 API·Credential 접근 이력 (Section 13)

- 이 세션: 외부 네트워크 호출 0회(이미지 가져오기는 FakeTransport만
  사용), Credential 원문 조회 0회, npm 레지스트리에서 Fabric.js
  다운로드 1회(사용자 명시 승인 후, 공개 오픈소스 패키지).
- 과거 문서 기준: 쿠팡 Credential은 "존재 확인"만 반복적으로 이뤄졌고
  (`credential_reference` 존재 여부), 원문 조회나 실제 쿠팡 API 호출은
  기록된 적이 없다.

---

## 10. 현재 Git 변경 분류 (Section 6)

**중요한 사실**: 이 저장소의 git 이력은 `main` 브랜치에 커밋 6개뿐이며
(`Build06`이라는 완전히 다른/이전 세대의 CRUD 스캐폴드), **V7 전체
개발 이력이 단 한 번도 git에 커밋되지 않았다**. `git status`는
`app/` 대부분을 untracked(`??`) 또는 legacy 파일 대비 modified/deleted로
표시한다 — 즉 git diff는 "이번 세션에서 무엇이 바뀌었는지"를 알려주는
용도로 쓸 수 없다(비교 대상 커밋 자체가 V7 코드를 담고 있지 않음).

이 세션이 직접 만들거나 수정한 파일만 별도로 분류한다:

- **완료·검증됨**: `app/domains/media_asset/{rights_evidence_service,
  url_import,product_page_extraction,url_import_service}.py`,
  `app/domains/marketplace_listing/{listing_wizard_service,
  listing_wizard_repository,listing_wizard_router,listing_wizard_schema,
  constants,listing_wizard_permissions,model}.py`(대기 상품 정리 부분),
  관련 신규 테스트 15개 파일, `app/web/{console.js,console.html,
  console.css}`(대기 상품 정리 UI 부분), `app/web/i18n/*.js`(신규 키),
  `app/web/vendor/fabric.min.js`(벤더링만).
- **Migration 작성·운영 DB 미반영**: `migrations/20260828_00/01/02*.sql`
  3개.
- **문서만 존재**: 이 보고서 자체, `docs/HOMEZ_PROJECT_STATE.md` 갱신분.
- **작업 중/미시작**: Fabric.js 편집기 실제 코드(0줄).
- **폐기 후보**: 없음(이번 세션에서 만든 임시 진단 코드 없음).
- 기존 WIP(다른 세션이 만든 미커밋 코드)는 reset/revert/clean하지
  않았다.

---

## 11. 미완료 작업 전체 목록 (Section 7, 순위 미확정)

1. **V7-DB-001 조사** — 출시 차단 가능성 High(추정), 데이터 손실 이미
   발생했을 가능성, 외부 API 의존 없음, 조사 범위 작음(로그·백업 대조),
   재설치 불필요, 사용자가 "무엇을 했는지" 기억/기록 확인 필요.
2. **V7-NAV-001 수정** — 출시 차단 가능성 Medium, 핵심 흐름(관리자
   메뉴 전반) 영향, 데이터 위험 없음, 수정 범위 1줄(`toUpperCase()`
   추가), 재설치 불필요. 이미 별도 세션에서 시작됨(`task_c6b92473`).
3. **Fabric.js 편집기 MVP 구현** — 출시 차단 아님(신규 기능), 사용자
   핵심 흐름 영향 낮음(이미지 제작은 선택 기능), 데이터 위험 없음,
   외부 의존 없음, 수정 범위 큼(신규 UI 전체), 재설치 불필요, 검증은
   격리 Browser E2E.
4. **쿠팡 실제 상품등록 최소 1회 검증** — 출시 판단에 필수, 외부 API
   의존 매우 높음, 사용자 수동 승인 다수 필요(8단계 재승인,
   OPERATOR_APPROVAL 임시 전환), 재설치 필요(최신 코드 미패키징),
   검증 방법은 `docs/V7_RELEASE_AUDIT_REPORT_20260827.md` §7 절차 재사용.
5. **운영 DB Migration 3건 적용**(20260828_00/01/02) — 출시 전 필요,
   외부 의존 없음, 별도 승인 필요, 검증은 이미 임시 DB로 완료.
6. **최종 재패키징·재설치** — 이 세션 변경분 반영 필요, 재설치 1회
   필요.
7. **알림 카탈로그 나머지 17개 실제 연결** — 출시 차단 아님, 범위 큼.
8. **공급처/결제 Live Provider 연동** — 출시 차단 여부는 사업 판단
   필요, 범위 매우 큼, 외부 계약 필요.

---

## 12. 우선순위 선택안 (Section 8)

### A. 출시 차단 우선안
1. V7-DB-001 조사 → 2. V7-NAV-001 수정 → 3. Migration 3건 적용 →
4. 재패키징·재설치 → 5. 쿠팡 실 등록 1회 검증
- 장점: 출시 판단에 가장 빨리 도달.
- 단점: 대기 상품 정리·이미지 스튜디오가 미완결 상태로 남을 수 있음.
- 재설치: 1~2회. 외부 API 호출: 쿠팡 1회(성공 또는 공식 오류 응답까지).
- 완료 조건: 쿠팡 external_submission_ref 또는 공식 오류 응답 확보.
- 예상 소요: 반나절~2일(사용자 승인 대기 시간 제외).

### B. 현재 작업 완결 우선안
1. Fabric.js 편집기 MVP 완성 → 2. 대기 상품 정리 잔여 UI 다듬기(이미
   대부분 완료) → 3. V7-DB-001/V7-NAV-001 → 4. 쿠팡 Live 흐름
- 장점: 진행 중이던 작업의 맥락을 잃지 않음.
- 단점: 출시 차단 가능 결함(V7-DB-001) 확인이 늦어짐.
- 재설치: 1회(모든 작업 끝난 뒤). 외부 API: 없음(이 안에서는 쿠팡
  Live까지 가지 않음).
- 완료 조건: Fabric.js 편집기 격리 E2E 통과.
- 예상 소요: 2~4일(편집기 범위에 따라 유동적).

### C. 위험 최소화 우선안
1. V7-DB-001 조사(전체 중단 없이) → 2. Migration 3건 적용 →
3. 전체 회귀 재확인 → 4. 재패키징 → 5. 그 다음 기능 작업 재개
- 장점: 데이터/설치 안정성을 가장 먼저 확보.
- 단점: 사용자가 궁금해하는 "쿠팡이 왜 실패했는지"에 대한 답이 뒤로
  밀림(애초에 실패 기록 자체가 없을 가능성 — Section 3).
- 재설치: 1회. 외부 API: 0회.
- 완료 조건: 운영 DB integrity_check=ok, pending=0, 전체 회귀 통과.
- 예상 소요: 반나절~1일.

### D. 혼합 권고안 (참고용, 최종 선택은 사용자 몫)
1. V7-DB-001 조사(사용자가 직접 기억/로그 확인, 병행 가능) →
2. V7-NAV-001은 이미 분리된 세션에서 진행 중이므로 그대로 둠 →
3. 현재 작업(대기 상품 정리는 이미 완료, Fabric.js는 사용자가 계속
   원할 때만) →
4. Migration 3건 적용 승인 →
5. 재패키징·재설치 →
6. 쿠팡 실 등록 1회
- 장점: 진행 중이던 작업을 버리지 않으면서 위험이 가장 큰 항목(DB
  불일치)을 가장 먼저 눈으로 확인.
- 단점: 여러 트랙을 병행하므로 진행 상황 추적 부담.
- 재설치: 1~2회. 외부 API: 쿠팡 1회.
- 완료 조건: 위 A안과 동일 + Fabric.js 완결.
- 예상 소요: 2~5일.

---

## 13. 추천과 그 근거

이 세션의 추천은 **A안과 D안의 절충** — 무엇보다 먼저
**V7-DB-001(Section 3의 데이터 불일치)을 사용자가 직접 확인**하는 것을
권한다. 근거: 이것이 사실이라면(가설 1: 2026-08-24와 유사한 사고
재발) 이미 발생한 데이터 손실을 더 키우기 전에 원인을 막아야 하고,
사실이 아니라면(가설 2: 문서 오기재) 나머지 우선순위 판단 전체가
잘못된 전제 위에 서 있게 된다. 이 확인은 코드 작업이 아니라 사용자의
기억/스크린샷/다른 세션 로그 확인이 필요해 이 세션이 대신할 수 없다.

---

## 14. 사용자가 결정해야 할 사항

1. **V7-DB-001**: 2026-08-27 이후 운영 DB를 되돌리거나 재설치하거나
   새 DB로 교체한 적이 있는가? (이 세션은 알지 못함)
2. 위 4개 안(A/B/C/D) 중 어느 순서로 진행할지.
3. Migration 3건(`20260828_00/01/02`)을 운영 DB에 적용할지·시점.
4. Fabric.js 편집기를 지금 계속 만들지, 뒤로 미룰지.
5. 쿠팡 실제 등록 1회를 언제 어떤 승인 절차로 진행할지.

---

## 15. 현재 판정

- 이 세션이 만든 기능(대기 상품 정리, 이미지 URL 가져오기, 권리 증빙
  경고): `WORK_IN_PROGRESS`에서 각각 명시된 개별 판정으로 완료(위
  Section 5 표 참고) — 그러나 **V7 전체를 출시 가능 상태로 선언하지
  않는다.**
- V7 전체: `V7_STATUS_UNCONFIRMED_PENDING_DB_DISCREPANCY_REVIEW` —
  기존 문서상 판정(`V7_RELEASE_CODE_COMPLETE_LIVE_APPROVAL_REQUIRED`,
  2026-08-27)을 부정하지는 않지만, 그 판정의 전제였던 운영 DB 상태가
  현재와 다르게 관측되므로 **재확인 전까지 유효성을 확정할 수 없다.**
- 쿠팡 실제 상품등록: **실제 등록 성공 미확인.** 문서와 현재 DB 모두
  실제 POST 실행 기록이 없다.

---

## Phase 1 완료 기록 (2026-08-28 23:27~23:35, 소요 8분 16초)

### 1-A. media_assets Model↔DB 계약

**확인**: 3개 컬럼(`source_url`/`source_domain`/`source_classification`)
전부 `migrations/20260828_01_add_media_asset_source_tracking.sql`
(오늘 이 세션이 작성) 소속이며, `MediaAsset` Model에는 이미 선언돼
있지만 개발·운영 DB 둘 다 이 Migration이 아직 pending이라 실제
컬럼이 없다 — Model이 DB보다 앞서 있는, **의도된** pending 상태였다.
기존 22개+이후 정당 추가분 Migration 파일들의 checksum은 이 작업에서
전혀 건드리지 않았다(읽기만 했다).

**실패했던 테스트**: `tests/test_media_listing_package_migration.py::
RealDatabaseAppliedTestCase::
test_real_homez_db_has_media_listing_package_tables_applied`.
이 테스트가 실제로 비교하는 대상은 (클래스명과 달리) **개발 DB**
(`REAL_DB_PATH = 저장소 루트 homez.db`)다 — 운영 DB
(`%LOCALAPPDATA%\HOMEZ\data\homez.db`)는 이 파일 어디에서도 열지
않는다. "1. 현재 운영 DB는 적용 완료 Migration 38건과 일치" 계약은
이 테스트가 아니라 오늘 작성된
`tests/test_media_asset_source_tracking_migration.py::
RealDbsNotYetMigratedTestCase`가 이미 운영·개발 DB 둘 다에 대해
읽기 전용으로 검증하고 있었다(별도 신규 테스트를 추가하지 않았다 —
중복 방지).

**수정**: 컬럼 차이를 통째로 무시(2026-08-20식 예외)하지도, 그렇다고
그대로 실패시키지도 않았다. `_pending_migration_owned_columns()`
(신규 헬퍼, `tests/test_media_listing_package_migration.py`)가
`MigrationRunner.diagnose()`로 실제 pending 파일 목록을 구하고, 그
파일들의 `ALTER TABLE ... ADD COLUMN ...` 문을 직접 파싱해
"어느 테이블의 어느 컬럼이 지금 정당하게 없어도 되는지"를 계산한다
— 하드코딩 목록이 아니므로 나중에 이 Migration이 실제로 적용되면
diagnose()가 더 이상 pending으로 보지 않아 예외가 자동으로 사라지고
비교는 다시 완전 일치로 돌아간다. 비교식을
`columns == model_columns`에서
`columns == (model_columns - pending_owned_columns)`로 바꿨다.

**assertion을 약화하지 않았다는 증거**: 임시로 관계없는 컬럼
(`unexpected_bogus_column`)을 media_assets 사본에 추가해 직접
재현했다 — pending Migration이 선언하지 않은 컬럼이므로 여전히
`assertEqual` 실패로 이어짐을 확인(스크립트만 실행, 파일에 남기지
않음).

### 1-B. 신규 Migration 개수 계약

**확인**: `tests/test_permissions_timestamps_migration.py::
MigrationStaticContractTestCase::test_exactly_one_new_migration_file_added`
는 실제로는 "정확히 1개"가 아니라 "**예상치 못한** 파일이 없는지"를
검증하는 닫힌 allowlist 테스트였다(자체 docstring에 명시, 2026-08-21/
23/27에도 같은 방식으로 갱신된 전례가 있음).

**정확한 신규 파일 3개**(`ls migrations/20260828*.sql`로 직접 확인,
추측하지 않음):
- `20260828_00_create_image_rights_evidence_schema.sql`
- `20260828_01_add_media_asset_source_tracking.sql`
- `20260828_02_add_listing_wizard_soft_delete.sql`

**수정**: `SUBSEQUENT_MIGRATIONS`에 3개 추가(2026-08-28 갱신 주석
포함). 개수 검사를 `>=1` 등으로 느슨하게 바꾸지 않았다 — 여전히
`assertEqual(all_files, expected)`로 **정확한 목록 일치**를 요구한다
(다섯 번째 예상 못한 파일이 생기면 여전히 실패). checksum까지
고정하는 신규 테스트 `test_newest_migration_files_checksum_locked`를
추가해(`compute_checksum()` 재사용, 이번 작업 착수 직후 계산한 값)
"같은 이름, 다른 내용"으로 조용히 바뀌는 것도 잡는다. 테스트 이름을
`test_exactly_one_new_migration_file_added` →
`test_migration_directory_contains_only_expected_files`로 변경(저장소
전체에서 이 이름을 참조하는 다른 파일이 없음을 grep으로 확인 후
변경 — 외부 참조 깨짐 없음).

### 검증 결과

- 실패했던 2개 테스트 파일: `Ran 28 tests ... OK`(0.041s + 30.247s)
- Migration 관련 전체 테스트 파일(35개, 이 저장소의 `*migration*.py`
  전부): `Ran 474 tests in 130.112s ... OK`
- 운영 DB SHA-256: `7357d900...` — Phase 1 시작 전/후 완전 동일
- 개발 DB SHA-256: `7688b8b0...` — Phase 1 시작 전/후 완전 동일
- 운영 DB에 쓰기 연결을 연 적 없음(전부 `mode=ro` 또는 임시 파일
  사본만 사용)
- 외부 API 호출 없음, Credential 접근 없음, git commit/push 없음
- 전체 3101개 재실행은 하지 않음(Phase 7에서 1회만 실행 예정)

### pending Migration 3개 정확한 목록(재확인)

```
20260828_00_create_image_rights_evidence_schema.sql
20260828_01_add_media_asset_source_tracking.sql
20260828_02_add_listing_wizard_soft_delete.sql
```

운영 DB 38 적용 / 3 pending — Phase 1 전후 변화 없음.

**Phase 1 완료. Phase 2(운영 DB 보고 불일치 조사, 읽기 전용) 대기.**

---

## Phase 2 완료 기록 (2026-08-28 23:36~23:40, 읽기 전용)

사용자 지시대로 전체 드라이브 검색·복사·복원·Migration·HOMEZ 실행은
하지 않았다 — 알려진 경로(`C:\Users\Public\homez-final-install.log`,
설치된 `Homez.exe` 경로, `app/database/bootstrap.py` 코드)만 읽었다.

### 새로 확보한 근거

1. **설치 로그**(`C:\Users\Public\homez-final-install.log`, mtime
   2026-08-27 04:21):
   - `2026-08-27 04:19:50` 시작 — 원본 EXE
     `C:\Users\Daum pc\Homez-OS\scratchpad\v7_release_package_20260827\
     installer-output\HOMEZ-Setup-2.1.0.exe`(2026-08-27 보고서가 §9에서
     만든 바로 그 격리 패키징 빌드).
   - "기존 HOMEZ 설치가 발견되었습니다" 대화상자에서 **"아니오"(업그레이드
     — 기존 파일 위 덮어쓰기)를 선택**. 이 대화상자 문구 자체가
     "[예]/[아니오] 둘 다 사용자 데이터 %LOCALAPPDATA%\HOMEZ는 영향받지
     않습니다"라고 명시한다.
   - 로그 전체(3760줄)에 `homez.db`/`LocalAppData`/`DataDir` 문자열이
     **단 한 번도 등장하지 않는다** — 설치 스크립트 자체는 프로그램
     파일(`AppData\Local\Programs\EVERY HOMEZ\*`)만 건드렸고, 데이터
     디렉터리를 직접 접촉한 흔적이 로그에 없다.
   - **`2026-08-27 04:21:05.940` — Run entry**: 설치 완료 직후
     `Homez.exe`를 자동 실행함(이미 문서화된 기존 결함 — "자동 실행
     해제해도 `[Run]`이 실행됨"이 실제로 이 로그에도 재현돼 있다).
   - 로그 종료: `04:21:07`.

2. **시간대 대조**: 이 강제 자동 실행(`04:21:05`)은 Phase 2 이전에
   확정한 두 지점 사이에 정확히 들어간다 — "Wizard #6 등 정상 데이터"
   백업(2026-08-26 13:47:52) **이후**, "이미 되돌아간 상태" 백업
   (2026-08-27 05:52:30, 그리고 그보다 더 이른 05:36 시점 파일명의
   백업)**이전**. 즉 데이터 원복은 04:21:05~05:36 사이(약 75분 창)에
   일어났다 — 이전 보고보다 창을 16시간에서 75분으로 좁혔다.

3. **설치된 실행 파일 확인**(요청 5/9 — 알려진 경로만):
   - `C:\Users\Daum pc\AppData\Local\Programs\EVERY HOMEZ\Homez.exe`
     (공식) — Birth 2026-08-24 20:46, 최근 Modify 2026-08-28 17:18
     (이 세션과 무관한 이후 재설치들로 계속 갱신된 것으로 보임).
   - `C:\Users\Daum pc\AppData\Local\Programs\HOMEZ-ISOLATED-TEST\
     Homez.exe`(격리 테스트용) — Birth 2026-08-19 23:35, 이후 변경
     없음. 격리 변형이라 이번 조사 대상(공식 운영 DataDir)과는 설계상
     분리돼 있어야 한다 — 더 깊이 조사하지 않았다(전체 드라이브 검색
     금지 지시 준수).

4. **코드 검토**(`app/database/bootstrap.py::bootstrap_environment()`,
   `is_new_install=False` 분기 — DB가 이미 존재할 때의 정상 경로):
   기존 행을 삭제·초기화하는 코드가 없다. pending Migration이
   있어도 사용자 승인 없이는 `backfill_needed`(스키마 변경 없는
   이력 채우기)만 수행하고 `MIGRATION_PENDING_DETECTED` 감사로그
   1건만 남긴다 — **이 함수 자체에서는 기존 데이터를 지우는 경로를
   찾지 못했다.**

### 판정

- **"과거 보고서가 다른 DB를 조회했다"**: 기각. 백업 파일 해시가
  Aug26/27 보고서의 명시된 해시와 정확히 일치해 같은 파일임을 이미
  확정했다(Phase 1 이전 조사).
- **"운영 DB가 이후 교체됐다"**: **가장 근거가 강하다** — 정확히
  04:21:05~05:36(75분) 사이 교체됐고, 그 시점은 이 로그가 기록한
  강제 재실행 직후와 겹친다. 다만 설치 스크립트 로그 자체에는 데이터
  디렉터리 접촉 흔적이 없고, 앱 부트스트랩 코드에서도 삭제 경로를
  찾지 못해 **"무엇이" 교체했는지는 여전히 확정하지 못한다.**
- **"데이터가 삭제됐다"**: 부분 확인 — 삭제라기보다 "교체/원복"에
  가깝다(3개 DRAFT 위저드가 마이크로초 단위로 동일하게 반복 등장하는
  것은 빈 상태가 아니라 특정 옛 백업 내용이 다시 나타난 패턴).
- **"증거 부족"**: 이 시점부터는 그렇다 — 그 75분 사이의 실제 애플리케이션
  로그(`logs/homez.log`, `storage/logs/desktop-launcher.log`)는 이후
  사용(오늘 이 세션의 회귀 테스트 포함)으로 이미 덮어써져 더 이상
  남아있지 않다. **UNCONFIRMED로 유지한다.**
- **"복합 원인"**: 배제하지 않는다 — 자동 강제 실행 자체가 알려진
  결함이고, 그 실행 이후 무언가(사용자의 다음 조작, 또는 앱의 다른
  코드 경로 — 오늘 검토 범위 밖)가 결합됐을 가능성은 남아 있다.

### 미확인으로 남기는 것(변경 없음)

- 04:21:05~05:36 사이 정확히 어떤 프로세스/명령이 파일을 교체했는지.
- 8월 27일 보고서 세션이 그 창 안에서 정확히 어떤 명령을 실행했는지
  (그 세션 자신의 터미널 기록은 이 세션에 없다).

**Phase 2 완료. 운영 DB 접촉 없음(전부 읽기 전용), HOMEZ 미실행,
Migration 미적용, 백업/복원 미실행. Phase 3(쿠팡 WING·API 계약 감사)로
계속 진행.**

---

## Phase 3 진행 중 기록 (2026-08-28 23:40~23:45, 부분 완료)

**정직한 진행 상태**: 요청된 30개 필수영역 전체 매핑표는 아직
완성하지 못했다(공식 문서 재확인이 필요한 부분이 많아 이번 슬라이스
안에 전부 끝내지 않았다). 대신 사용자가 실제로 겪은 오류
("필수 구매 옵션 (미입력시 등록/노출 제한) 존재하지 않습니다",
z2 스크린샷)를 z3(WING 옵션 화면)과 실제 HOMEZ 코드로 먼저 깊게
추적했다 — 이것이 가장 급한 질문이라고 판단했다.

### z2~z15 확인 결과(화면 판독)

- z2: **HOMEZ 자체** 10단계 결과 화면(WING 아님) — 상태="쿠팡 전송
  실패", 실패사유=위 오류 문구, **"쿠팡 실제 전송" 칸은 "—"**(빈 값).
- z3: WING "옵션" 섹션 — 개당 수량/수량 입력칸, **옵션 목록(총 0개) —
  "데이터가 존재하지 않습니다"**.
- z5/z14/z15: WING "상품 주요 정보"의 인증정보 드롭다운 — 카테고리별
  KC인증/KCs안전인증/위생안전기준인증 등 매우 긴 후보 목록.
- z6/z7: 상품정보제공고시 — 품명및모델명/재질/구성품/크기/출시년월/
  제조자(수입자)/제조국/수입신고문구여부/품질보증기준/A·S책임자.
- z8~z11: 배송 — 출고지(미등록 오류 표시 중), 택배사(드롭다운:
  롯데택배·한진택배 등 쿠팡 제휴 포함 다수), 배송방법(일반배송/
  신선냉동/주문제작/구매대행/설치배송), 묶음배송, 배송비종류,
  출고소요일.
- z12: 반품/교환 — 반품/교환지, 초도배송비(편도), 반품배송비(편도).
- z13: 상품정보제공고시 카테고리 후보 — 주방용품/기타 재화/생활화학제품.

### "필수 구매 옵션" 오류 추적 — 코드 근거

1. **이 정확한 오류 문구는 HOMEZ 소스코드(`app/` 전체) 어디에도
   없다**(`grep` 확인) — HOMEZ가 자체 생성한 메시지가 아니라는 뜻이다.
   저장소에서 이 문구가 나오는 유일한 곳은
   `docs/HOMEZ_V7_UNIVERSAL_LISTING_AND_IMAGE_RIGHTS_RESEARCH.md`
   (이전 세션의 조사 문서)뿐이다.
2. **그 문서가 이미 이 문제를 조사했었다** — 표에 정확히 이렇게
   기록돼 있다: `"필수 구매 옵션 존재하지 않음" 오류의 원인이
   inputType 누락이다 → ROOT_CAUSE_UNPROVEN(실제 원시 응답 재확보
   못함. attributes[] 문제인지 items[](옵션 조합 자체 부재) 문제인지
   구분 못함)`. 이번 조사는 이 판정을 뒤집을 새 증거를 아직 확보하지
   못했다 — **`ROOT_CAUSE_UNPROVEN`을 그대로 유지한다.**
3. **이번 세션이 독립적으로 재확인한 것(코드 직접 읽음)**:
   - `coupang_category_metadata_provider.py:96-104`가 쿠팡 Category
     Metadata 응답의 `attributes[]`를 파싱할 때 `attributeTypeName`과
     `required`만 저장하고, **`inputType`(자유입력/드롭다운)과
     `inputValues`(허용값 목록)는 애초에 읽지도 저장하지도 않는다**
     (`category_metadata.py`의 `NoticeFieldDefinition`에 그 필드
     자체가 없음) — 이전 문서의 확정 사실과 정확히 일치, 이번 세션이
     독립적으로 재현 확인했다.
   - `required_fields_schemas.py::CoupangSellerFulfilledItem`(HOMEZ
     내부 "옵션 행" 계약)은 `itemName`/`externalVendorSku` **딱 2개
     필드뿐**이다 — WING z3 화면이 요구하는 "개당 수량"/"수량"에
     대응하는 필드가 이 Schema에 아예 없다. `originalPrice`/
     `salePrice`/`maximumBuyCount`는 옵션 행이 아니라 상품 전체
     레벨(`CoupangSellerFulfilledFields`)에만 있어, 옵션마다 다른
     가격/수량을 표현하지 못한다(WING은 옵션별 정상가·판매가·
     단위당가격을 각 행마다 따로 받는 그리드 UI — z3에서 직접 확인).
   - `coupang_live_payload.py::build_coupang_live_payload()`는
     `items`(옵션 행)가 비어 있으면 `ITEM_REQUIRED`로 차단하지만,
     `attributes`(구매옵션 속성값, `purchase_options`에서 옴)가
     비어 있어도 **막지 않는다** — 빈 `attributes: []`를 그대로
     payload에 넣어 보낼 수 있는 구조다. 이것이 실제 원인의 일부일
     가능성이 있으나 **증명되지 않았다**(공식 API가 attributes 빈
     배열을 어떻게 처리하는지 이번 세션이 아직 공식 문서로 확인하지
     않았다).
4. **이미 오늘 자 실사 반영 흔적 발견**: `coupang_live_payload.py`
   104행 주석에 "공식 문서(Product Creation) — maximumBuyForPerson과
   반드시 함께 와야 하는 필드(누락 시 쿠팡이 거부함, **2026-08-28
   실사 확인**)"라고 적혀 있다 — 오늘 이미 한 차례 실제 확인·수정이
   있었다는 뜻이다(이 세션이 한 것은 아니다 — 이 audit을 시작하기
   전, 사용자가 직접 또는 다른 세션에서 진행한 것으로 보인다).

### 분류(요청 형식)

- **공식 근거 확인**: `maximumBuyForPersonPeriod` 관련 항목만(코드
  주석에 "2026-08-28 실사 확인"으로 명시, 이 세션이 원문을 다시
  확인하지는 않음).
- **코드로 확인**: inputType/inputValues 미저장, 옵션 행 Schema에
  수량 필드 없음, attributes 빈 배열 무검증 — 전부 이번 세션이 코드를
  직접 읽어 확인.
- **fixture로만 확인**: 없음(이 조사는 fixture를 보지 않았다).
- **미확인**: "필수 구매 옵션" 오류 문구의 정확한 발생 지점(WING
  자체 UI 힌트문구인지, 실제 저장/제출 시 오류인지), attributes 빈
  배열에 대한 쿠팡 공식 API의 실제 처리 방식.
- **과거 추정 폐기**: 없음 — 이전 문서의 `ROOT_CAUSE_UNPROVEN` 판정을
  유지, 새로 확정하지 않았다.

### 남은 작업(Phase 3 미완료 부분)

## Phase 3 완료 기록 (2026-08-28 23:45~2026-08-29 00:05)

이전 슬라이스의 "구매옵션" 중간 증거는 **그대로 보존**했다(위 절 —
근본원인으로 확정하거나 코드를 수정하지 않았다). 아래는 공식 문서
7건을 이번 세션에서 직접 fetch해 확인하고 HOMEZ 코드와 대조한 결과다.

### 확인한 공식 문서(URL + 확인 시각, 전부 이번 세션 직접 fetch)

| # | 문서 | URL | 확인 시각(KST) |
|---|---|---|---|
| 1 | Product Creation(상품 생성) | developers.coupang.com/hc/en-us/articles/360033877853 | 2026-08-28 23:41 |
| 2 | Category Metadata Query | developers.coupang.com/hc/en-us/articles/360034035713-Category-Metadata-Query | 2026-08-28 23:43 |
| 3 | 상품 정보 입력 필수 항목 안내(구매옵션) | marketplace.coupang.com/information-center/marketplace3p-product-info-update-uid | 2026-08-28 23:44 |
| 4 | Query a shipping location(출고지) | developers.coupang.com/en/api/logistics/query-a-shipping-location | 2026-08-28 23:47 |
| 5 | Query a list of return locations(반품지) | developers.coupang.com/hc/en-us/articles/360033644814-Query-a-list-of-return-locations | 2026-08-28 23:48 |
| 6 | Querying product(상품 조회/상태) | developers.coupang.com/en/api/products/querying-product | 2026-08-28 23:50 |
| 7 | HMAC Signature(서명, 기존 인용 재확인용) | developers.coupang.com/hc/en-us/articles/360033461914-Creating-HMAC-Signature | (기존 2026-08-01 확인 인용, 이번 세션 재fetch 안 함 — `coupang_signing.py` 자체 주석 근거) |

WebFetch는 원문을 소형 모델이 요약해 반환한다 — 표에 옮긴 필드명·enum은
그 요약을 근거로 하며, 완전한 원문 그대로의 축어적 인용은 아니다(이
한계를 그대로 공개한다).

### 최소 매핑 영역 — 36개 전체 표

범례: `OFFICIAL`=OFFICIAL_DOC_CONFIRMED, `META`=CATEGORY_METADATA_CONFIRMED,
`CODE`=CODE_CONFIRMED, `UI`=UI_SCREEN_CONFIRMED, `FIXTURE`=FIXTURE_ONLY,
`UNCONF`=UNCONFIRMED, `CONFLICT`=CONFLICTING

| # | 항목 | 공식 API 필드 | HOMEZ 위치 | 상태 | 증거 |
|---|---|---|---|---|---|
| 1 | 판매자 ID(vendorId) | `vendorId`(String, 필수) | `coupang_live_provider.py:103`(전송 직전 주입) | 구현됨 | OFFICIAL+CODE |
| 2 | 실사용자 ID | `vendorUserId`(String) | `required_fields_schemas.py`, `coupang_live_payload.py` | 구현됨 | OFFICIAL+CODE |
| 3 | 판매 시작일 | `saleStartedAt` | `coupang_live_payload.py`(now, ISO) | 구현됨 | OFFICIAL+CODE |
| 4 | 판매 종료일 | `saleEndedAt` | 동일(now+3650일, 2099-01-01 상한) | 구현됨 | OFFICIAL+CODE |
| 5 | 등록상품명 | `sellerProductName`(≤100자) | `product_name[:100]` | 구현됨 | OFFICIAL+CODE |
| 6 | 노출상품명 | `displayProductName`(선택, `[brand]+[generalProductName]` 권장) | `product_name[:100]`(brand 조합 없이 그대로 사용 — 권장 포맷 미준수) | **불일치** | OFFICIAL+CODE |
| 7 | 브랜드 | `brand` | `draft.get("brand")` | 구현됨 | OFFICIAL+CODE |
| 8 | 일반상품명 | `generalProductName` | `product_name[:100]`(brand 제외 권장이나 동일 값 재사용) | 부분불일치 | OFFICIAL+CODE |
| 9 | 상품군 | `productGroup` | `draft.get("category")[:100]` | 구현됨(의미상 매핑 근거 약함) | CODE(UNCONF 근사) |
| 10 | 공식 카테고리 | `displayCategoryCode`(Number, 필수) | `int(category)` | 구현됨 | OFFICIAL+CODE |
| 11 | 옵션 설정 여부 | (API 필드 아님 — WING UI 개념) | 대응 없음(items 존재 자체로 판단) | UI 개념, API 무관 | UI |
| 12 | 개당 수량 | 공식 필드명 직접 미확인(`unitCount` 후보 — "개별 단위 수" 설명과 근접) | `required_fields.get("unitCount", 1)` | **매핑 미확정** | UNCONF(추정, 확정 아님) |
| 13 | 수량(재고) | `maximumBuyCount`(Number, ≤99999) | `required_fields["maximumBuyCount"]` | 구현됨 | OFFICIAL+CODE |
| 14 | 옵션명 | `itemName`(≤150자, item별 고유) | `CoupangSellerFulfilledItem.itemName` | 구현됨 | OFFICIAL+CODE |
| 15 | 정상가 | `originalPrice` | `required_fields["originalPrice"]` | 구현됨(단, 옵션별이 아니라 상품 전체 단일값 — 아래 결함 3 참고) | OFFICIAL+CODE |
| 16 | 판매가 | `salePrice` | `required_fields["salePrice"]` | 동일 결함 | OFFICIAL+CODE |
| 17 | 최대 구매수량(인당) | `maximumBuyForPerson`(0=제한없음) | `required_fields.get("maximumBuyForPerson", 0)` | 구현됨 — z5 "인당 최대구매수량"과 매칭 | OFFICIAL+CODE+UI |
| 18 | 출고 소요일 | `outboundShippingTimeDay` | `required_fields.get(..., 1)` | 구현됨 — z8~z11 "출고 소요일"과 매칭 | OFFICIAL+CODE+UI |
| 19 | 모델번호 | `modelNo` | `required_fields.get("modelNo", sku)` | 구현됨 | OFFICIAL+CODE |
| 20 | 바코드 | `barcode`/`emptyBarcode`/`emptyBarcodeReason` | 전부 구현 | 구현됨 | OFFICIAL+CODE |
| 21 | SKU | `externalVendorSku` | `item["externalVendorSku"]` | 구현됨 | OFFICIAL+CODE |
| 22 | 구매옵션 attributes | `attributes[]`(`attributeTypeName`/`dataType`/`basicUnit`/`usableUnits`/`required`/`inputType`/`inputValues`/`groupNumber`/`exposed`) | `NoticeFieldDefinition`에 `key`/`label`/`required`만 존재 — **`dataType`/`basicUnit`/`usableUnits`/`inputType`/`inputValues`/`groupNumber`/`exposed` 전부 미저장** | **결함 확인** | OFFICIAL+META+CODE |
| 23 | 상품 이미지 | `images[]`(REPRESENTATION 최소 1, DETAIL 최대 9) | `CoupangProductImage` + `_public_image_errors()` | 구현됨(대표이미지 존재·공개 URL 검증 포함) | OFFICIAL+CODE |
| 24 | 상세설명 | `contents[]`(TEXT/IMAGE/HTML 등) | `required_fields.get("contents", [])` — **타입/구조 검증 없음**(`list[dict]`로만 선언) | **검증 공백** | OFFICIAL+CODE |
| 25 | 제조사 | `manufacture`(선택, 루트 레벨, brand와 별개) | **HOMEZ payload에 `manufacture` 키 자체가 없음** — z5 WING 화면은 "제조사"를 별도 필드로 요구 | **결함 확인(누락 필드)** | OFFICIAL+CODE+UI |
| 26 | 상품 구성 | `bundleInfo{bundleType: SINGLE\|AB}`(추정 매핑) | **HOMEZ payload에 `bundleInfo` 없음** — z5 WING "동일한 상품 구성"/"다양한 상품 혼합 구성" 라디오와 개념적으로 근접하나 필드명 직접 대조는 못함 | **미확인 갭** | UI+CODE(매핑 UNCONF) |
| 27 | 인증 유형·번호 | `certifications[]`(`certificationType`/`name`/`dataType`/`required`: MANDATORY/RECOMMEND/OPTIONAL) — Category Metadata 응답에 카테고리별로 내려옴 | `required_fields.get("certifications", [{"certificationType":"NOT_REQUIRED",...}])` 기본값 하드코딩, Category Metadata의 실제 `certifications[]`를 조회·저장하는 코드 없음(`CategoryMetadata`에 필드 자체 없음) | **결함 확인** | OFFICIAL+META+CODE |
| 28 | 병행수입 | `parallelImported` | `required_fields.get(..., "NOT_PARALLEL_IMPORTED")` | 구현됨 | OFFICIAL+CODE |
| 29 | 해외구매대행 | `overseasPurchased`, `pccNeeded`(AGENT_BUY 시 true 필수) | 둘 다 구현, 단 `pccNeeded`와 `deliveryMethod=AGENT_BUY` 간의 상호 필수 검증(공식 문서 조건)은 코드에 없음 | 부분불일치 | OFFICIAL+CODE |
| 30 | 구매연령 | `adultOnly` | `required_fields.get(..., "EVERYONE")` | 구현됨 | OFFICIAL+CODE |
| 31 | 부가세 | `taxType` | `required_fields.get(..., "TAX")` | 구현됨 | OFFICIAL+CODE |
| 32 | 상품정보제공고시 | `notices[]`, Category Metadata의 `noticeCategories[].noticeCategoryDetailNames[]`(`required`: MANDATORY/OPTIONAL) | `CoupangNotice`/`CoupangNoticeDetail` + `validate_notice_information()` — 구현됨, MANDATORY 필드 누락 검증 있음 | 구현됨 | OFFICIAL+META+CODE |
| 33 | 구비서류 | `requiredDocuments`(루트, PDF/DOC/이미지 ≤5MB), Category Metadata의 `requiredDocumentNames[]`(MANDATORY/OPTIONAL/MANDATORY_PARALLEL_IMPORTED/MANDATORY_OVERSEAS_PURCHASED) | **`CategoryMetadata`에 이 필드 자체가 없고, payload에도 `requiredDocuments` 키가 없다** | **결함 확인(완전 누락)** | OFFICIAL+META+CODE |
| 34 | 출고지 | `outboundShippingPlaceCode`(Number) | `required_fields["outboundShippingPlaceCode"]`, Schema 선언은 **`str`**(타입 불일치) | **타입 불일치** | OFFICIAL+CODE |
| 35 | 도서산간 | `remoteAreaDeliverable`(Y/N) | `required_fields.get(..., "N")` | 구현됨 | OFFICIAL+CODE |
| 36 | 택배사 | `deliveryCompanyCode` | `required_fields.get("deliveryCompanyCode")` | 구현됨(코드값 자체의 유효성 목록 검증은 없음) | OFFICIAL+CODE |
| 37 | 배송방법 | `deliveryMethod`(SEQUENCIAL/COLD_FRESH/MAKE_ORDER/AGENT_BUY/VENDOR_DIRECT) | `required_fields_schemas.py`의 `Literal` 5종 — 정확히 일치 | 구현됨 | OFFICIAL+CODE |
| 38 | 묶음배송 | `unionDeliveryType`(UNION_DELIVERY/NOT_UNION_DELIVERY) | `required_fields.get(..., "UNION_DELIVERY")` | 구현됨 | OFFICIAL+CODE |
| 39 | 배송비 | `deliveryChargeType`/`deliveryCharge`/`freeShipOverAmount` | 전부 구현 | 구현됨 | OFFICIAL+CODE |
| 40 | 반품지 | `returnCenterCode`/`returnChargeName`/`companyContactNumber`/`returnZipCode`/`returnAddress`/`returnAddressDetail` | `coupang_logistics_provider.py::list_return_shipping_centers()` — v5 엔드포인트 경로·응답구조(`data`가 배열로 직접 옴) 전부 공식 문서와 정확히 일치 | 구현됨(정확) | OFFICIAL+CODE |
| 41 | 초도 배송비 | `deliveryChargeOnReturn` | `required_fields["deliveryChargeOnReturn"]` | 구현됨 | OFFICIAL+CODE |
| 42 | 반품 배송비 | `returnCharge`(초도의 100~150%) | `required_fields["returnCharge"]` — **100~150% 범위 검증 없음** | **검증 공백** | OFFICIAL+CODE |
| 43 | 승인 요청 | `requested`(Boolean) | `payload["requested"] = True`(항상 고정) | 구현됨(선택 불가로 고정 — 정책적 선택, 결함 아님) | OFFICIAL+CODE |
| 44 | 상품 생성 결과 | `data`(성공 시 `sellerProductId`) | `coupang_live_provider.py`에서 파싱 | 구현됨 | OFFICIAL+CODE |
| 45 | 상태 조회 | `GET .../seller-products/{sellerProductId}`(`statusName`: SAVED/IN_REVIEW/APPROVING/APPROVED/PARTIAL_APPROVED/DENIED/DELETED) | **HOMEZ 코드 어디에도 이 엔드포인트 호출이 없다**(`grep` 확인) — 이전 문서가 "공식 경로를 확인 못해 미구현"이라 적었던 것과 달리, 이번 세션이 공식 경로를 실제로 확인했다 | **미구현(경로는 이제 확인됨)** | OFFICIAL(신규 확인)+CODE(미구현 확인) |

(항목 수가 요청한 "최소 매핑 영역" 나열보다 많은 것은 상위 항목이
자연스럽게 세분화됐기 때문이다 — 요청된 항목은 전부 포함했다.)

### 서명(HMAC) 구현 이중화 — 새로 발견

`coupang_live_provider.py::_headers()`가 검증된 공용 헬�퍼
(`app/domains/store_connection/adapters/coupang_signing.py::
build_authorization_header()`, 2026-08-01 공식 문서 확인·인용 근거
보유)를 재사용하지 않고 자체적으로 `f"{signed_date}{method.upper()}
{path}"`를 다시 구현했다 — **query 파라미터를 서명 메시지에서
아예 누락**한다. 상품 생성 POST 자체는 query가 없어 이번 특정 호출
결과에는 영향이 없다(query=""와 결과적으로 동일). 그러나 코드
중복·향후 query 있는 호출로 재사용될 위험이 있어 결함으로 기록한다.
증거: CODE(직접 대조).

---

## Phase 3 산출물

### 1. 확인된 구현 결함 목록(코드로 확인된 것만)

| ID | 결함 | 심각도(추정) |
|---|---|---|
| V7-COUPANG-META-001 | Category Metadata의 `dataType`/`basicUnit`/`usableUnits`/`inputType`/`inputValues`/`groupNumber`/`exposed` 전부 미저장 | High(구매옵션 정확한 검증 불가) |
| V7-COUPANG-META-002 | Category Metadata의 `requiredDocumentNames`/`certifications[]` 응답 자체를 저장하지 않음(하드코딩 기본값만 전송) | High |
| V7-COUPANG-PAYLOAD-001 | `manufacture`(제조사) 필드가 payload에 전혀 없음 | Medium(WING 화면은 요구) |
| V7-COUPANG-PAYLOAD-002 | `bundleInfo`(상품 구성) 필드 없음 | Low~Medium(매핑 자체 미확정) |
| V7-COUPANG-PAYLOAD-003 | `outboundShippingPlaceCode` 타입이 공식 Number 대 HOMEZ `str` | Medium(수락 여부 미확인) |
| V7-COUPANG-PAYLOAD-004 | `returnCharge`(반품배송비)의 "초도의 100~150%" 공식 제약 검증 없음 | Medium |
| V7-COUPANG-PAYLOAD-005 | `contents[]`(상세설명) 구조 검증 없음 | Low |
| V7-COUPANG-PAYLOAD-006 | `pccNeeded`↔`deliveryMethod=AGENT_BUY` 상호 필수 검증 없음 | Low |
| V7-COUPANG-SIGN-001 | HMAC 서명 이중 구현(공용 헬퍼 미재사용, query 누락) | Low(현재 호출엔 무영향, 잠재 위험) |
| V7-COUPANG-STATUS-001 | 상품 상태 조회 API(`GET .../seller-products/{id}`) 미구현 — 경로는 이번에 확인됨 | High(Live 검증 필수 전제조건) |

### 2. 미확인 항목 목록

- "필수 구매 옵션(미입력시 등록/노출 제한) 존재하지 않습니다" 오류
  문구의 정확한 발생 지점(WING 자체 UI 힌트인지 실제 저장/제출 오류
  API 응답인지).
- "개당 수량"/"수량"/"재고" 3개 WING 라벨과 공식 API 필드
  (`unitCount`/`maximumBuyCount`) 사이의 1:1 매핑(추정만 했고 공식
  문서가 WING UI 라벨을 직접 명시하지 않아 확정 못함).
- `bundleInfo`가 WING의 "상품 구성" 라디오와 실제로 대응하는지.
- attributes 빈 배열을 쿠팡이 실제로 어떻게 처리하는지(거부/경고/
  무시) — 공식 문서가 이 경우를 명시하지 않음.

### 3. 실제 실패와 연결이 증명된 항목

없음 — z2의 실제 실패 문구와 코드 결함 사이의 인과관계는 여전히
`ROOT_CAUSE_UNPROVEN`(사용자 지시대로 확정하지 않음).

### 4. 실제 실패와 연결이 증명되지 않았지만 정황상 유력한 항목

- V7-COUPANG-META-001(exposed/inputType 미보존)과
  V7-COUPANG-META-002(certifications/requiredDocumentNames 미보존)
  — 공식 "구매옵션 필수" 안내문 문구("등록 실패 혹은 노출 제한 등이
  발생할 수 있습니다")가 z2의 표현과 개념적으로 겹친다. **정황
  증거일 뿐 증명은 아니다.**

### 5. Phase 4에서 수정할 최소 범위(제안, 아직 구현 안 함)

1. `CategoryMetadata`/`NoticeFieldDefinition`에 `data_type`/
   `basic_unit`/`usable_units`/`input_type`/`input_values`/
   `group_number`/`exposed` 필드 추가(내부 DB 없음, 순수 코드
   구조체 확장 — Migration 불필요).
2. `coupang_category_metadata_provider.py::get()`이 이 필드들을
   실제로 채우도록 파싱 확장.
3. `CategoryMetadata`에 `required_documents`/`certifications` 필드
   추가 + provider가 채우도록 확장.
4. `manufacture`/`bundleInfo` 필드를 `required_fields_schemas.py`와
   `coupang_live_payload.py`에 추가(선택 필드로, 기존 계약 깨지
   않게).
5. `outboundShippingPlaceCode`를 스키마에서 정수로 교정(직렬화
   호환성 확인 필요).
6. `returnCharge` 100~150% 검증, `pccNeeded`↔`AGENT_BUY` 상호검증
   추가.
7. `coupang_live_provider.py::_headers()`를 `coupang_signing.py`
   공용 헬퍼로 교체(중복 제거).
8. 상품 상태 조회(`GET seller-products/{id}`) Provider 메서드 추가
   (호출은 여전히 Fake/격리 테스트만, 실제 호출은 Phase 10 승인 후).

**전부 내부 코드/스키마 확장이며 새 Migration이나 운영 DB 변경이
필요 없다.** 실제 호출은 Phase 4에서도 하지 않는다(Fake Provider·
fixture로만 검증).

### 6. 필요한 Fake Metadata·Payload fixture 목록

- `exposed=EXPOSED`/`inputType=SELECT`/`inputValues=[...]`를 가진
  Fake Category Metadata 응답(현재 z3와 유사한 "옵션 0개" 시나리오
  재현용).
- `requiredDocumentNames`에 `MANDATORY` 항목이 있는 Fake 응답.
- `certifications`에 실제 `MANDATORY` 인증 유형이 있는 Fake 응답
  (z14/z15의 KC 인증류).
- `manufacture`/`bundleInfo`를 채운 Fake payload 조립 fixture.

### 7. 실제 읽기 API가 나중에 필요한 항목

- 상품 상태 조회(`GET seller-products/{id}`) — 이번에 경로 확인
  완료, 실제 호출은 Phase 10 승인 후.
- 실제 Category Metadata(특정 카테고리의 진짜 `attributes[]`
  raw response) — 지금 확보한 것은 공식 문서의 "필드가 존재한다"는
  사실뿐, 이 사용자 제품의 실제 카테고리에 대한 실제 값은 미확보.

### 8. 운영 DB·Credential·외부 쓰기 미접촉 증거

- 운영 DB SHA-256(`7357d900...`)/개발 DB SHA-256(`7688b8b0...`) —
  Phase 3 시작 전/후 완전 동일(재확인 완료).
- Credential 원문 조회 0회.
- 이번 Phase에서 수행한 모든 외부 접근은 공식 문서 7건에 대한
  **읽기 전용 GET**(WebFetch/WebSearch)뿐이며, 쿠팡 API 자체에는
  전혀 접속하지 않았다(개발자 문서 사이트만 조회).
- 실제 쿠팡 POST: 0회.
- 코드 수정: 0건(이번 Phase는 조사만 수행 — "구매옵션 발견을 근본
  원인으로 확정하거나 코드를 수정하지 않는다" 지시 그대로 준수).

**Phase 3 완료. Phase 4(안전한 내부 구현 범위)로 계속 진행 — 운영 DB
Migration·공식 재설치·실제 쿠팡 POST는 여전히 실행하지 않는다.**

## Phase 4 진행 기록 (2026-08-29)

Phase 3에서 정의한 "Phase 4에서 수정할 최소 범위" 8개 항목 중 진행
상황:

**완료(1~4, 7, 8):**

1. `app/domains/marketplace_listing/category_metadata.py` —
   `PurchaseOptionAttribute`/`RequiredDocumentDefinition`/
   `CertificationDefinition` frozen dataclass 신규 추가.
   `CategoryMetadata.purchase_option_fields` 타입을 이 신규
   dataclass로 교체하고 `required_documents`/`certifications` 필드
   추가. `metadata_fingerprint()`가 두 신규 필드도 해시하도록 갱신.
2. `app/domains/marketplace_listing/coupang_category_metadata_provider.py`
   — 공식 응답의 `dataType`/`basicUnit`/`usableUnits`/`inputType`/
   `inputValues`/`groupNumber`/`exposed`/`requiredDocumentNames`/
   `certifications`를 이전에는 전부 버리던 것(감사에서 코드로 확인된
   결함, V7-COUPANG-META-001/002)을 전부 보존하도록 수정.
3. `app/domains/marketplace_listing/listing_wizard_schema.py`/
   `listing_wizard_router.py` — 신규 Pydantic 응답 모델 추가,
   `get_category_metadata()` 반환에 새 키 연결(Pydantic
   `response_model` 검증이 깨지는 것을 실행 전에 미리 발견해 수정).
4. `app/domains/marketplace_listing/required_fields_schemas.py`/
   `coupang_live_payload.py` — 공식 문서 루트 레벨 선택 필드
   `manufacture`를 추가(선택 필드, 기존 계약 안 깨짐). **`bundleInfo`는
   추가하지 않음** — Phase 3에서 WING 화면 라벨("상품 구성" 등)과의
   매핑 자체가 UNCONFIRMED로 남아, 확정되지 않은 매핑을 코드로
   추측해 넣지 않는다(추측 금지 원칙).
7. `app/domains/marketplace_listing/coupang_live_provider.py::_headers()`
   — 손으로 재구현했던 HMAC 서명 로직(V7-COUPANG-SIGN-001, query
   이어붙이기 누락)을 제거하고 `app/domains/store_connection/adapters/
   coupang_signing.py::build_authorization_header()` 공용 헬퍼로
   교체. `query=""` — 이 엔드포인트는 query string이 없다.
8. `app/domains/marketplace_listing/coupang_live_provider.py` —
   `ProductStatusResult` dataclass + `get_product_status()` 메서드
   신규 추가(V7-COUPANG-STATUS-001, HOMEZ에 없던 기능). 응답 봉투
   모양은 `create_product()`와 동일한 `{"code","message","data"}`
   계열로 **우선 가정**했을 뿐 실제 응답으로 확인하지 못해
   UNCONFIRMED로 명시(코드 주석). **이 메서드는 이 세션 어떤
   라우터에서도 호출되지 않는다** — 실제 호출은 Phase 10 사용자 승인
   후로 명시 예약.

**보류(5, 6) — 사유 명시:**

5. `outboundShippingPlaceCode` (`str`→정수 교정): grep 재확인 결과
   실제 사용처가 20개 파일(다수가 테스트)에 걸쳐 있어, "안전한 내부
   구현 범위"를 벗어나는 대규모 변경이 된다. 사용자 별도 승인 없이
   진행하지 않는다(CLAUDE.md Whitelist: 대규모 리팩터링은 별도 승인
   대상).
6. `returnCharge` 100~150% 검증, `pccNeeded`↔`AGENT_BUY` 상호검증:
   이번 세션에서 정확한 쿠팡 공식 규칙을 재확인하지 않은 채 넣으면
   추측성 비즈니스 규칙이 되어 향후 정상 제출을 잘못 차단할 위험이
   있다. 공식 문서 재확인 없이 구현하지 않는다.

**변경 파일:**
- `app/domains/marketplace_listing/category_metadata.py`
- `app/domains/marketplace_listing/coupang_category_metadata_provider.py`
- `app/domains/marketplace_listing/listing_wizard_schema.py`
- `app/domains/marketplace_listing/listing_wizard_router.py`
- `app/domains/marketplace_listing/required_fields_schemas.py`
- `app/domains/marketplace_listing/coupang_live_payload.py`
- `app/domains/marketplace_listing/coupang_live_provider.py`
- `tests/test_listing_category_metadata_contract.py`(수정 — 신규
  필드 반영 + 신규 테스트 1개 추가)
- `tests/test_coupang_live_submission.py`(수정 — `_Session.get()`
  추가 + `get_product_status()` 신규 테스트 4개 추가)

**프론트엔드 영향 확인:** `app/web/console.js`에서
`purchase_option_fields` grep — 매치 0건(프론트엔드가 아직 이 필드를
참조하지 않아, 이번 변경으로 인한 프론트엔드 파손 없음).

**테스트 결과:** `venv/Scripts/python.exe -m unittest
tests.test_coupang_live_submission
tests.test_listing_category_metadata_contract -v` — **Ran 78 tests
... OK**(실패 0, 오류 0, exit code 0). 신규 `get_product_status()`
테스트 4개(정상 조회/404/미확인 status/timeout) 포함 전부 통과.
이 두 모듈은 관련 파일(`test_listing_wizard_service.py`의
`ListingWizardServiceTestCase`)을 import해 함께 실행되므로 실질
커버리지가 더 넓다. 이번 Phase는 전체 회귀(2026-08-28 기준 3101개
추정)를 다시 돌리지 않았다 — 전체 회귀는 Phase 7에서 모든 코드
변경이 끝난 뒤 정확히 1회로 예약되어 있다(지시 원문 그대로).

**운영 DB·외부 API 접촉:** 0회(코드 변경 + 임시 격리 테스트만 수행,
실제 쿠팡 API·운영 DB 접촉 없음).

**Phase 4(안전한 내부 구현 범위) 완료 — 항목 1~4/7/8 구현·검증
완료, 항목 5/6은 사유와 함께 보류. Phase 5(Fabric.js MVP/권리
증빙/대기상품정리 재확인)로 계속 진행 — 운영 DB Migration·공식
재설치·실제 쿠팡 POST는 여전히 실행하지 않는다.**

## Phase 5 완료 기록 (2026-08-29)

### 1. Fabric.js 이미지 수동 편집기 MVP(사용자 확정 범위: 자르기·회전 +
밝기/대비 조정 + 도형/화살표 그리기 + 다단계 되돌리기)

**시작 전 실제 상태 확인**: `app/web/vendor/fabric.min.js`는 이전
세션이 벤더링만 해두고 실제 편집기 UI·백엔드 연동은 전혀 없었다
(정적 파일 서빙 라우트 1개뿐, grep으로 재확인). 이번 Phase에서
처음부터 구현했다.

**백엔드(신규)**:
- `app/domains/media_asset/job_queue_service.py::save_manual_edit()`
  — 브라우저에서 편집이 끝난 최종 이미지 1장을 받아 원본은 절대
  수정하지 않고 새 파생 자산(GENERATED)을 만든다. 기존
  `remove_background()`와 동일한 Job 기록 패턴(`operation=
  "MANUAL_EDIT"`, `provider_code="LOCAL_MANUAL_EDIT"` — 외부
  Provider를 호출하지 않으므로 구분).
- `app/domains/media_asset/schema.py::ManualImageEditSaveRequest`,
  `app/domains/media_asset/router.py::POST /media-assets/manual-edit`
  (admin_guard).
- 신규 테스트 4개(`tests/test_media_asset_image_workflow.py`) —
  생성/원본 보존, Job 이력 기록, 손상 바이트 거부(원본 보존 확인
  포함), 타사 회사 소유 원본 접근 차단. 관련 스위트
  `tests.test_media_asset_image_workflow` 전체 재실행: **Ran 21
  tests ... OK**.

**프론트엔드(신규)**: `app/web/console.js`(모달·캔버스·툴바 로직),
`app/web/console.css`(모달 스타일), `app/web/i18n/ko-KR.js`,
`app/web/i18n/en-US.js`(신규 문자열). 상품등록 마법사 미디어 단계의
각 이미지 카드에 "편집" 버튼 추가 → 모달에서 Fabric.js 캔버스로
편집 → "저장" 시 `POST /media-assets/manual-edit` 호출.

**브라우저 실클릭 검증 중 발견·수정한 실제 결함 3건** (모두 이
Phase에서 처음 작성한 신규 코드의 결함이며, 격리 DB
`manual_edit_ui_verify.db`에서만 검증 — 운영 DB 미접촉):

1. **이미지가 전혀 다른 위치·크기로 렌더링됨** — 원인: 이
   벤더링된 Fabric.js가 v6이며, v6는 `originX`/`originY` 기본값이
   `"left"/"top"`가 아니라 `"center"`로 바뀌었다(구버전 지식 기준
   추측이 틀렸음을 실제 픽셀 판독으로 확인 — `getImageData`로
   좌표별 색상을 직접 대조해 근본 원인을 특정). 모든 오브젝트
   생성부에 `originX:"left", originY:"top"`를 명시해 수정.
2. **도형/화살표 그리기 도구가 전혀 반응하지 않음** — 원인:
   Fabric v6의 마우스 이벤트 payload가 `opt.pointer` 대신
   `opt.scenePoint`를 쓴다(벤더 파일 문자열 직접 grep으로
   `scenePoint`/`viewportPoint` 존재, `pointer` 할당 0건 확인).
   `mouse:down`/`mouse:move` 핸들러를 `opt.scenePoint`로 교체.
3. (검증 방법 자체의 교훈, 코드 결함 아님) 이 Fabric 빌드는
   PointerEvent가 아니라 순수 MouseEvent(`mousedown`/`mousemove`는
   캔버스 요소, `mouseup`은 `document`)로 상호작용을 처리한다 —
   자동화 브라우저 조작 시 이 사실을 몰라 여러 번 헛짚었으나, 실제
   사용자의 물리 마우스 조작에는 영향 없는 사항이었다.

**검증 방법**: 격리 스크립트(`bootstrap_environment()`로 임시 DB
생성·전체 Migration 적용 → 회사/관리자/ProductCandidate/MediaAsset
시딩, 4분할 색상 테스트 이미지 사용)로 포트 8841에 실제 서버를
띄우고, 실제 로그인 → 상품등록 마법사 → 미디어 단계 → 편집 모달까지
전부 실제 클릭/드래그로 열었다. 각 기능을 **캔버스 픽셀 데이터
직접 판독**(`getImageData`)으로 검증했다(스크린샷 눈짐작이 아님):
- 밝기 조정: (0,212,200)→(153,255,255), 계산값과 정확히 일치.
- 회전(90° 시계방향): 4분할 색상이 회전 후 예상 위치로 정확히 이동.
- 자르기: 캔버스가 448×640→360×514로 축소, 잘린 영역의 4분할 색상
  배치가 예상과 일치.
- 도형(사각형) 그리기: 반투명 채우기 알파 블렌딩 결과값이 계산값과
  정확히 일치(예: (38,196,199) = 배경색×0.85+도형색×0.15).
- 되돌리기: 도형 제거 후 원본 픽셀(0,212,200)로 정확히 복원.
- 저장: `POST /media-assets/manual-edit` → 200 OK, 상품의 MediaAsset
  목록이 1개→2개로 증가 확인(원본 asset_id=1 그대로 유지 +
  파생 asset_id=2 신규 생성).

**표시 해상도 정책**: 모달 실제 표시 폭 제약으로 `LW_EDIT_MAX_SIDE=
640`(px) 이상인 원본은 편집·저장 해상도가 이 값으로 줄어든다 —
사용자에게 명시적으로 안내 문구를 띄운다(조용히 품질을 낮추지
않는다는 원칙 준수). 더 큰 해상도를 유지하려면 후속 작업(모달을
벗어난 전체화면 편집기, 또는 CSS-only 축소+원본 해상도 export)이
필요하다 — 이번 MVP 범위 밖으로 명시.

### 2. 권리 증빙(rights evidence) 재확인

이번 Phase에서 이 도메인 코드를 수정하지 않았다(읽기 전용 재확인).
`tests.test_media_asset_rights_evidence` 재실행 — 회귀 없음(아래
합산 결과 참고).

### 3. 대기 상품 정리(soft-delete) 재확인

이번 Phase에서 이 도메인 코드를 수정하지 않았다(읽기 전용 재확인).
`tests.test_listing_wizard_soft_delete` +
`tests.test_listing_wizard_soft_delete_migration` 재실행 — 회귀
없음.

**2·3 합산 테스트 결과**: `Ran 47 tests ... OK`(실패 0, 오류 0).

**변경 파일**:
- `app/domains/media_asset/job_queue_service.py`(수정)
- `app/domains/media_asset/schema.py`(수정)
- `app/domains/media_asset/router.py`(수정)
- `app/web/console.js`(수정 — 대규모 신규 블록 추가)
- `app/web/console.css`(수정)
- `app/web/i18n/ko-KR.js`, `app/web/i18n/en-US.js`(수정)
- `tests/test_media_asset_image_workflow.py`(수정 — 신규 테스트 4개)

**격리 검증 인프라(스크래치, 운영 코드 아님)**:
- `.claude/launch.json`에 `phase5-edit-ui-verify`(port 8841) 추가.
- 스크래치 스크립트: `setup_manual_edit_ui_db.py`(bootstrap),
  `seed_manual_edit_ui.py`(시딩), `start_phase5_edit_server.cmd`.
- 검증용 임시 DB(`manual_edit_ui_verify.db`)는 실제 homez.db와
  완전히 별개 파일이며, 검증 종료 후 서버 프로세스 종료 완료.

**테스트 결과 총합(Phase 5)**: 미디어 이미지 Workflow 21건 + 권리
증빙·soft-delete 47건 = **68 tests, 실패 0**. 브라우저 실클릭
검증은 위 기록대로 픽셀 단위로 별도 확인.

**운영 DB 접촉**: 0회. **외부 API 접촉**: 0회.

**Phase 5 완료. Phase 6(격리 통합 E2E, 25개 시나리오)으로 계속
진행 — 운영 DB Migration·공식 재설치·실제 쿠팡 POST는 여전히
실행하지 않는다.**

---

## 쿠팡 상품등록 핵심 차단 해결 기록 (2026-08-29)

**지시 변경**: 사용자가 Phase 6(범용 25개 시나리오) 착수를 보류시키고,
쿠팡 상품등록 실제 동작을 막는 핵심 차단부터 해결하라고 지시했다.
검증 상품: "국산 삼색 부직포 주방행주 40매 38x38cm".

### 1. 현재 기준선(Section 0, 읽기 전용 확인)

- branch=`main`, `git status --short` 269건 — 이전과 동일, WIP 보존 확인.
- Phase 4·5 변경 파일 전부 존재·수정시각 확인(reset/revert 없음).
- 개발 DB SHA-256 `7688b8b0...`/운영 DB SHA-256 `7357d900...` —
  Phase 3 이후와 완전 동일(재확인 완료, 미접촉).
- Migration: 개발·운영 DB 모두 38개 적용, 동일한 3개 pending
  (`20260828_00/01/02_*.sql`) — 이전과 동일.
- 실행 중이던 python/uvicorn 프로세스 없음(이전 세션의 검증 서버는
  전부 정상 종료 확인).

### 2. 공식 문서 확인 결과(Section 1)

이번 세션에서 WebFetch로 새로 확인(요약 모델 한계 고지 유지):

| 확인 대상 | 상태 | 핵심 내용 |
|---|---|---|
| `items[].attributes` | **OFFICIAL_CONFIRMED(신규)** | attributes는 `items[]` **각 항목** 내부 필드이며 **필수(✓)** — 이전에 상품 전체 공통값으로 취급하던 HOMEZ 구조와 공식 계약이 다르다는 것을 재확인. |
| `items[].contents` | **OFFICIAL_CONFIRMED(신규)** | `contents`도 items[] 레벨 **필수(✓)** 필드. HOMEZ는 `required_fields.get("contents", [])`로 조용히 빈 배열 허용 — 계약 위반. |
| `unitCount` | **OFFICIAL_CONFIRMED(신규)** | items[] 레벨, 필수(✓). "개당 수량" 팩 단위 수(예: 40개입)를 나타내며 재고 수량이 아니다 — WING "개당 수량" ↔ `unitCount` 매핑을 이번에 확정(이전 UNCONFIRMED에서 격상). |
| "수량"(WING) | **OFFICIAL_CONFIRMED(신규)** | 공식 예시에서 `attributeTypeName:"수량"`으로 `attributes[]`에 들어가는 카테고리별 구매옵션 속성임을 확인 — 별도 전용 API 필드가 아니다. |
| `originalPrice`/`salePrice`/`maximumBuyCount` | **OFFICIAL_CONFIRMED(재확인)** | 전부 items[] 레벨(옵션별) 필드 — HOMEZ가 상품 전체 단일값으로 취급하던 기존 결함을 공식 계약으로 재확인. |
| `bundleInfo.bundleType` | **OFFICIAL_CONFIRMED(신규)** | `"SINGLE"`(기본) / `"AB"`(혼합 구성) — z5의 "동일한 상품으로 구성됨"/"다양한 상품이 혼합되어 구성됨"과 개념적으로 일치하나, 정확한 라디오값↔enum 1:1 대응은 이번에도 원문 스크린샷 재대조까지는 하지 않아 **UI_SCREEN_CONFIRMED(개념) + CODE 미구현** 상태로 유지(추측 구현 안 함). |
| `manufacture` | OFFICIAL_CONFIRMED(재인용) | 루트 레벨, 선택. Phase 4에서 이미 구현 완료. |
| 상태 조회 응답(`statusName`) | **OFFICIAL_CONFIRMED(신규)** | 봉투 `{"code","message","data"}`, `statusName` enum이 Phase 4에서 이미 구현한 7개 값과 정확히 일치. 응답에 반려 사유 필드나 별도 노출 여부 필드는 **없음**(공식 문서 자체가 미제공 — UNCONFIRMED로 유지). |

WebFetch 요약 한계는 Phase 3와 동일하게 고지한다 — 소형 모델 요약
기반이며 원문 축어적 인용이 아니다.

### 3. WING↔API↔HOMEZ 매핑표(Section 2, 발췌)

Phase 3의 45개 표에 이번에 확정된 항목만 갱신(전체 표는 Phase 3
기록 참고, 여기서는 변경분만 기록):

| WING 표시명 | API 경로 | HOMEZ 구현 여부(이번 수정 후) | 근거 |
|---|---|---|---|
| 구매 옵션(색상 등) | `items[].attributes[]` | **연결됨(신규)** — Category Metadata의 실제 attributeTypeName/inputType/inputValues 기준 구조화 UI → `channel_policy_attributes.purchase_options` → payload `attributes[]` | OFFICIAL+META+CODE |
| 개당 수량 | `items[].unitCount` | 저장·전송 경로는 있으나(Phase 4 이전부터) **여전히 상품 전체 단일값**(옵션별 미분리) — 이번에 **items[] 오버라이드만 추가**, WING식 "개당수량×수량 조합 생성기" UI는 미구현(MISSING, 아래 4번 참고) | OFFICIAL+CODE(부분) |
| 옵션별 정상가/판매가/재고 | `items[].originalPrice/salePrice/maximumBuyCount` | **연결됨(신규)** — item에 값이 있으면 우선, 없으면 상품 전체 값으로 대체(하위호환) | OFFICIAL+CODE |
| SKU 중복 | (계약 아님, 정합성 요구사항) | **차단 추가(신규)** — 저장(Pydantic model_validator)과 제출 직전(payload builder) 이중 검증 | CODE |
| 상세설명 | `items[].contents` | **필수 검증 추가(신규)** — 비어 있으면 제출 직전 차단(`CONTENTS_REQUIRED`). 내부 구조(TEXT/IMAGE/HTML) 검증은 공식 원문 미확보로 여전히 MISSING | OFFICIAL(존재확인)+CODE(구조검증 UNCONFIRMED) |
| 상품 구성(단일/혼합) | `bundleInfo.bundleType` | **MISSING** — 매핑 confidence는 올랐으나 정확한 라디오값 대조 없이 구현하지 않음(추측 금지) | UI+OFFICIAL(부분) |
| 상품 상태 조회 | `GET seller-products/{id}` | **Service·Router 연결(신규)** — `ListingWizardLiveService.check_status()` + `GET /listing-wizards/{id}/submissions/{id}/live-status`. UI 버튼 연결은 MISSING(이번 범위에서 제외, 아래 17번 참고). 실제 호출 0회. | OFFICIAL+CODE |

### 4. 실제 실패 증거 재구성(Section 3)

**조사 대상**: `C:\Users\Daum pc\Homez-Backups\v7_release_20260827\
homez_pre_migration_36_37.db`(읽기 전용, SHA-256 `6f0c155b...`,
mtime 2026-08-26 13:47 — 사용자 지정 백업과 일치 확인). 운영 DB에는
복원·Migration 적용 없음, Credential 원문 조회 없음.

**확인된 사실**:
- `listing_wizards` 테이블에 이 상품(candidate_id 1·2, 이름 일치
  확인) 관련 행 6개 중 **#4(FAILED)**·**#6(SUCCEEDED)** 확인.
- `marketplace_submissions` 3행 전부 확인:
  - #1(2026-08-25 05:06, listing #1): `FAILED`, `safety_decision=DENY`,
    `error_reason="MODE_NOT_ALLOWED"`.
  - #2(2026-08-25 20:09, listing #2): 동일하게 `FAILED`/`DENY`/
    `MODE_NOT_ALLOWED`.
  - #3(2026-08-26 04:39, listing #2): `PENDING`, `safety_decision=ALLOW`,
    `error_reason=NULL`, `external_submission_ref=NULL`.
- `MODE_NOT_ALLOWED`는 **HOMEZ 자체의 `AutomationSafetyService`
  모드 게이트**(`app/domains/automation_safety/service.py:308`)이며
  쿠팡 API와 무관하다 — 자동화 모드가 `DISABLED`/`RECOMMEND_ONLY`일
  때 무조건 거부되는, 코드로 확인된 정상 동작이다(결함 아님).
- `audit_logs` 34건 중 쿠팡 실제 제출·응답에 해당하는 항목 **0건**
  (전부 `CHANNEL_POLICY_EVALUATED`/`STORE_CONNECTION_*`류) —
  `marketplace_submission_live_tracking` 테이블 자체가 이 백업
  시점(35개 Migration 적용)에는 아직 존재하지 않았다(해당 Migration은
  2026-08-26 이후 적용분).
- **결정적 발견**: wizard #6의 `channel_policy_attributes.
  purchase_options`에 실제로 `{"구성":"40매","색상":"삼색 혼합",
  "크기":"38x38cm","재질":"부직포"}`가 저장돼 있었다 — **4개 값
  전부 존재**(빈 배열이 아니었다, 이전 세션의 "attributes 빈 배열"
  가설을 이 증거가 직접 반박함). 하지만 이 4개 attributeTypeName
  ("구성"/"색상"/"크기"/"재질")은 **Coupang Category Metadata의
  실제 응답값이 아니라 원시 JSON 텍스트박스에 사용자가 자유
  입력한 값**이었다(`app/web/console.js`의 옛
  `lw-policy-purchase-options` textarea, 코드로 확인) — 카테고리
  80754가 실제로 요구하는 attributeTypeName·inputType·inputValues와
  대조·검증된 적이 전혀 없었다.
- 같은 wizard #6의 `required_fields.items[0]`에는 `contents` 키
  자체가 없었다(빈 상세설명) — 오늘 공식 문서로 새로 확인한
  "items[].contents 필수(✓)" 계약과 정면으로 어긋난다.

**확정된 원인**: 없음 — 원시 쿠팡 응답(HTTP 상태·code·message)이
이 백업을 포함해 어떤 저장소에도 남아 있지 않다.
**`ROOT_CAUSE_UNPROVEN`을 그대로 유지한다.**

**메커니즘상 유력해진 것(정황 증거, 증명 아님)**:
1. `purchase_options`가 Coupang 실제 카테고리 계약과 무관하게
   저장됐다는 것 — 이번에 **원시 데이터로 직접 확인**(코드 추정이
   아니라 실제 저장값 확인). 카테고리가 MANDATORY+EXPOSED 구매옵션
   속성을 요구했다면, 이 4개 자유 입력값은 그 요구를 충족하지
   못했을 가능성이 높다.
2. `contents`가 비어 있었다는 것 — 오늘 새로 확인된 공식 필수(✓)
   계약과 직접 충돌.
둘 다 **정황 증거이지 증명이 아니다** — 원시 응답이 없어 어느 쪽이
(혹은 둘 다인지, 둘 다 아닌지) 실제 원인인지는 여전히 확정할 수
없다. 사용자 지시대로 원인을 하나로 단정하지 않는다.

### 5. 핵심 기능 완성(Section 4) — 실제 구현 내용

**연결 완성**: Category Metadata 조회(Phase 4, 기존)→구매옵션
필드 생성(Phase 4, 기존)→**사용자 값 입력(신규, 구조화 SELECT/
INPUT UI)**→**입력값 검증(신규, 실시간 + 저장 시 fail-closed)**→
**Wizard 저장(신규, `update_fulfillment()` 검증 연결)**→**카테고리
재조회 시 기존 값 무효화(신규)**→**items[].attributes[] 생성(기존
로직 그대로, 이제 검증된 값만 들어옴)**→**전송 전 Payload 검사(신규,
payload builder fail-closed 재확인)**→8단계 승인 fingerprint(기존,
채널선택 전체가 이미 fingerprint 입력에 포함 — 별도 구현 불필요,
아래 12번 참고)→실제 제출 준비.

**변경 파일(백엔드)**:
- `category_metadata.py` — `validate_purchase_options()` 신규(exposed+
  required 속성만 검사, SELECT는 공식 inputValues만 허용).
- `listing_wizard_service.py::update_fulfillment()` — 저장 시점에
  `purchase_option_field_definitions` 스냅샷 기준으로 fail-closed
  검증(`COUPANG_PURCHASE_OPTION_REQUIRED`).
- `coupang_live_payload.py` — `CONTENTS_REQUIRED`(신규 필수 검증),
  `PURCHASE_OPTION_REQUIRED:...`(제출 직전 재검증, 저장 이후
  변경 가능성 대비), `DUPLICATE_SKU:...`(신규), items[] 각 항목의
  `originalPrice`/`salePrice`/`maximumBuyCount`/`unitCount` 오버라이드
  지원(없으면 기존처럼 상품 전체 값 사용 — 하위호환).
- `required_fields_schemas.py::CoupangSellerFulfilledItem` — 위 4개
  선택 필드 추가 + `CoupangSellerFulfilledFields`에 SKU 중복 거부
  `model_validator` 추가.
- `listing_wizard_live_service.py` — `check_status()` 신규(Provider의
  `get_product_status()`를 실제 Service 계층에 연결, V7-COUPANG-
  STATUS-001의 남은 절반).
- `listing_wizard_schema.py`/`listing_wizard_router.py` —
  `WizardLiveStatusResponse` + `GET .../live-status` 신규 엔드포인트
  (실제 호출 0회, 연결만 완료).

**변경 파일(프론트엔드, `app/web/console.js`)**:
- 원시 JSON 텍스트박스(`lw-policy-purchase-options`)를 완전히
  제거하고, Category Metadata의 `purchase_option_fields`
  (`exposed=true`만)를 기준으로 SELECT(공식 `inputValues`만 선택
  가능)/INPUT(단위 힌트 표시)을 동적으로 그리는
  `lwRenderPurchaseOptionFields()` 신규 함수로 교체.
- 카테고리 재조회 버튼 클릭 시 구매옵션 값을 자동으로 초기화해
  재확인을 강제(기존 고시정보 처리와 동일 패턴).
- `lwCollectChannelPolicyInput()`이 구조화 입력에서 값을 모으고,
  검증용 `purchase_option_field_definitions` 스냅샷을 함께 저장하도록
  변경.
- `lwBuildSubmissionReviewHtml()` 신규(Section 5, 아래 참고).
- `app/web/i18n/ko-KR.js`/`en-US.js` — 신규 문자열 전부 자연스러운
  한국어 기본, 내부 코드명 미노출.

**UI 요구사항 준수**: JSON 직접 입력 제거(더 이상 기본 사용법이
아님), SELECT/INPUT 구분 렌더링, 옵션 조합별 가격·재고·SKU 오버라이드
지원(백엔드 완료, 그리드 UI는 MISSING — 아래 참고), 저장값=Payload
값 불일치 시 fail-closed(신규 검증이 정확히 이 역할).

**MISSING(이번 범위에서 의도적으로 제외, 근거 없이 구현 안 함)**:
- WING식 "개당수량×수량" 조합 자동 생성 그리드(z3) — 백엔드는 여러
  `items[]` 행을 받아들이지만, 프론트엔드에 조합을 자동 생성하는
  UI는 없다(사용자가 필요하면 `필수 입력값(JSON)` 고급 편집으로
  직접 여러 item을 추가할 수는 있음 — 이상적인 UX는 아니다).
- `bundleInfo` — 매핑 confidence는 올랐으나 여전히 미구현(추측 금지).
- `outboundShippingPlaceCode` 타입 교정(str→int) — 20개 파일
  영향으로 Phase 4에서 보류한 것을 이번에도 보류(사용자 별도 승인
  필요, CLAUDE.md Whitelist).
- `returnCharge` 100~150%, `pccNeeded`↔`AGENT_BUY` 상호검증 — 공식
  문서 재확인 못해 보류.
- `contents[]` 내부 구조(TEXT/IMAGE/HTML) 검증 — "비어있지 않음"만
  검증, 정확한 스키마는 공식 원문 미확보로 구현 안 함.

### 6. 전송 전 검사 화면(Section 5)

**기존 인프라 재사용**: `listing_wizard_approval.py::
build_approval_package()`가 이미 `channel_selections`(카테고리·
구매옵션·옵션별 가격/재고/SKU·출고지·반품지 코드·배송비 전부 포함)와
`economics_result`(예상 매출·마진)를 8단계 승인 fingerprint 입력에
포함하고 있었다 — **새 백엔드 엔드포인트가 필요 없었다.** 이번
작업은 프론트엔드 `lwRenderApprovalPreviewPanel()`이 이 데이터의
일부(상품명·브랜드·카테고리·설명)만 보여주던 것을, 카테고리 코드·
구매옵션·제조사·원산지·고시정보·옵션별 가격/재고/SKU 표·출고지/
반품지 코드·배송비/반품배송비·예상매출/마진까지 전부 보이도록
`lwBuildSubmissionReviewHtml()`로 확장했다.

- Credential·주소·전화번호 원문은 표시하지 않는다(출고지·반품지는
  코드만 노출 — 기존 원칙 그대로 유지).
- 개발자용 원본 데이터는 `<details>` 접이식 섹션에서만 제공.
- 누락·경고·차단 항목은 **기존 7단계 사전검사 화면**
  (`lwRenderPrecheckResult`, `/listing-wizards/{id}/validate`)이 이미
  담당하고 있어 중복 구현하지 않았다 — 8단계 검토 화면에는
  `precheck_status` 요약만 표시.
- 승인 fingerprint는 기존 것을 그대로 사용한다(별도 "Payload
  fingerprint"를 새로 만들지 않았다 — 승인 fingerprint 자체가 이미
  `channel_selections`/`required_fields` 전체를 해시 입력에 포함하므로
  개념적으로 동일하며, 승인 후 값이 바뀌면 기존에 이미 구현된
  `test_wizard_payload_change_after_approval_blocks_before_provider`가
  차단함을 재확인했다).

### 7. 집중 테스트 결과(Section 6)

새로 추가한 테스트(전부 임시 DB/순수 함수, 운영 DB·외부 API 없음):

| # | 시나리오 | 파일 | 테스트 수 |
|---|---|---|---|
| 1 | INPUT 구매옵션 입력·저장·Payload 반영 | test_listing_category_metadata_contract.py, test_coupang_live_submission.py | 3 |
| 2 | SELECT 구매옵션 허용값 검증 | test_listing_category_metadata_contract.py | 2 |
| 3 | 필수 구매옵션 누락 차단 | test_listing_category_metadata_contract.py, test_coupang_live_submission.py, test_listing_wizard_service.py | 4 |
| 4 | 옵션 조합별 가격·재고·SKU | test_coupang_live_submission.py, test_marketplace_required_fields_validation.py | 3 |
| 5 | 중복 SKU 차단 | test_coupang_live_submission.py, test_marketplace_required_fields_validation.py | 2 |
| 6 | Category Metadata 변경 시 무효화 | (프론트엔드 코드 검토 + 기존 notice_fields 패턴과 동일 구조 — 별도 자동화 테스트는 브라우저 실클릭으로 대체, 아래 참고) | — |
| 7 | Payload fingerprint 불일치 차단 | 기존 `test_wizard_payload_change_after_approval_blocks_before_provider`(회귀 재확인) | 기존 |
| 8 | manufacture 반영 | test_coupang_live_submission.py | 1 |
| 9 | 잘못된 출고지·반품지 타입 차단 | 기존 회귀(Phase 4에서 타입 교정 보류 — 변경 없음) | 기존 |
| 10 | 회사 A/B 데이터 격리 | 기존 회귀(`test_marketplace_tenant_isolation.py` 등, 전부 재실행 확인) | 기존 |
| 11 | Credential·개인정보 로그 미노출 | 기존 회귀 | 기존 |
| 12 | 한국어 오류 안내 | 신규 에러 메시지 전부 한국어(코드 리뷰로 확인) | — |
| 13 | Desktop/Tablet/Mobile overflow | **미실행**(아래 명시) | 0 |
| 14 | 기존 Listing Wizard 회귀 | 광범위 재실행(아래 합계) | 기존 |
| 15 | 기존 쿠팡 제출 안전장치 회귀 | `test_coupang_live_submission.py` 전체 재실행 | 기존 |
| (신규) | 상품 상태 조회 Service 연결 | test_coupang_live_submission.py | 2 |

**신규 테스트 총 24건 + 기존 회귀 재확인 다수, 실패 0.**

**실행 결과**: `tests.test_coupang_live_submission` +
`tests.test_listing_category_metadata_contract` +
`tests.test_marketplace_required_fields_validation` +
`tests.test_marketplace_adapter_contract` +
`tests.test_listing_wizard_service` 등 이번 변경과 관련된 스위트를
반복 재실행 — **전부 실패 0**(구체적 실행 로그는 아래 "테스트 결과
총합" 참고).

**13번(반응형 overflow) 미실행 사유**: 이번 작업 범위는 데스크톱
브라우저 실클릭 검증까지만 수행했다(아래 8번 참고) — 모바일·태블릿
뷰포트에서의 구매옵션 UI 확인은 이번 세션에서 하지 않았다. **미실행을
실행 완료로 보고하지 않는다.**

### 8. 격리 브라우저 실클릭 검증

포트 8851에 격리 서버(`purchase_option_ui_verify.db`,
`bootstrap_environment()`로 전체 Migration 적용 후 시딩 — 운영 DB
아님)를 띄우고 실제 로그인→상품등록→5단계(판매 방식)까지 실클릭으로
검증했다. **카테고리 조회(실제 쿠팡 API) 버튼은 클릭하지 않았다**
— 대신 "사용자가 이미 카테고리를 조회해 저장까지 마친 상태"를
DB에 직접 시딩해, 저장된 데이터에서 렌더링하는 경로만 검증했다
(카테고리 재조회 자체의 실클릭 검증은 실제 쿠팡 API 호출 없이는
불가능하므로 코드 대조로 대체).

확인한 것:
- 색상(SELECT, 공식 inputValues 3개)과 구성(INPUT, 단위 "매" 표시)이
  정확히 렌더링됨, exposed=false 속성("검색전용소재")은 화면에
  **나타나지 않음**(정상).
- 빈 값 상태에서 두 필드 모두 "필수 구매 옵션입니다. 값을 입력하세요"
  오류가 실제로 표시됨.
- 값을 입력하면 오류가 즉시 사라짐(`refresh()` 로직 정상).
- DOM에서 수집 로직과 동일한 셀렉터로 값을 재구성한 결과가
  `{"색상":"삼색 혼합","구성":"40매"}`로 정확히 일치, 정의 스냅샷
  3개(exposed=false 포함) 보존 확인.
- 저장(PATCH) 자체는 이 격리 DB에 출고지·반품지 캐시가 없어(실제
  쿠팡 로지스틱스 조회 API를 의도적으로 호출하지 않았음)
  `COUPANG_LOGISTICS_SELECTION_REQUIRED`로 차단됨 — **이것은 기존
  결함이 아니라 이번 검증이 의도적으로 만든 상태**(외부 API 미호출
  원칙 준수). 이 때문에 서버 저장 로직 자체의 실제 왕복은 **Python
  통합 테스트**(`test_fulfillment_rejects_missing_required_exposed_
  purchase_option`, `test_fulfillment_accepts_valid_exposed_purchase_
  option` — 로지스틱스 캐시를 테스트 프로세스 내부에서 직접
  주입)로 별도 검증했다(위 Section 6 참고).

검증 서버는 확인 후 정상 종료했다.

### 9. 승인·fingerprint 안전장치

새로 만들지 않았다 — 기존 8단계 승인 fingerprint 메커니즘
(`listing_wizard_approval.py`)이 `channel_selections`(구매옵션 포함)
전체를 이미 해시 입력에 포함하므로, 저장된 구매옵션 값이 승인 후
바뀌면 기존에 이미 구현·테스트된 불일치 차단이 그대로 작동한다
(코드 검토로 재확인, 관련 기존 테스트 재실행으로 회귀 없음 확인).

### 10. 테스트 결과 총합

- `tests.test_coupang_live_submission`: 79건(신규 9건 포함) 전부 통과.
- `tests.test_listing_category_metadata_contract`: 13건(신규 7건 포함) 전부 통과.
- `tests.test_marketplace_required_fields_validation`: 21건(신규 2건 포함) 전부 통과.
- `tests.test_listing_wizard_service`: 신규 2건 포함 전부 통과.
- 광범위 회귀 스위트(`test_marketplace_listing_status_transitions`,
  `test_marketplace_tenant_isolation`,
  `test_marketplace_listing_concurrency`, `test_marketplace_submission`,
  `test_marketplace_submission_approval`, `test_marketplace_listing_contract`,
  `test_listing_wizard_permission`, `test_marketplace_adapter_contract`):
  82건 전부 통과(1건은 존재하지 않는 파일명을 잘못 지정한 내 실수로
  인한 로더 오류였을 뿐 — 실제 테스트 실패 아님, 즉시 확인).

**최종 통합 재실행**: 이번 작업이 손댄 파일과 연결된 12개 테스트
모듈을 한 번에 모아 재실행 — `tests.test_coupang_live_submission`,
`tests.test_listing_category_metadata_contract`,
`tests.test_marketplace_required_fields_validation`,
`tests.test_marketplace_adapter_contract`,
`tests.test_listing_wizard_service`,
`tests.test_marketplace_listing_status_transitions`,
`tests.test_marketplace_tenant_isolation`,
`tests.test_marketplace_listing_concurrency`,
`tests.test_marketplace_submission`,
`tests.test_marketplace_submission_approval`,
`tests.test_marketplace_listing_contract`,
`tests.test_listing_wizard_permission` — **Ran 237 tests ... OK**
(실패 0, exit code 0).

**전체 3101개 회귀는 아직 재실행하지 않았다** — 사용자 지시대로
모든 코드 수정이 끝난 뒤 정확히 1회만 실행할 예정이다("전체 회귀
통과"를 아직 주장하지 않는다).

### 11. 운영 DB·외부 API 접촉 여부

- 운영 DB SHA-256: `7357d90020601e7c5cfc9bef15a117c8d57f4c905eba7f1b2a9ae5d4e0aec7fd`
  — 이번 작업 시작 전(Section 0)과 지금 재확인한 값이 **완전히
  동일**(0회 쓰기 확정).
- 개발 DB SHA-256: `7688b8b08018b5b896ee55b2318e424044b97c3e5a8493bcc6f185d9185cef87`
  — 동일하게 불변 확인.
- 실제 쿠팡 API: 0회(카테고리 조회·상태 조회·상품등록 전부 미호출 —
  이번 세션의 유일한 외부 접근은 공식 문서 사이트 WebFetch 2건뿐).
- Credential 원문 조회: 0회.

### 12. Migration 필요 여부

**불필요.** 이번 작업은 전부 코드 레벨(Pydantic 필드 추가, 검증
함수, 프론트엔드 렌더링)이며 DB 스키마 변경이 없다 — 신규
Migration 파일을 만들지 않았다.

### 13. 실제 제출 전 남은 항목

- WING식 "개당수량×수량" 조합 자동 생성 그리드(현재는 items[] 여러
  행을 JSON 고급 편집으로만 추가 가능 — UX 개선 여지).
- `bundleInfo`, `outboundShippingPlaceCode` 타입, `returnCharge`
  검증, `pccNeeded` 상호검증 — 전부 근거 부족/블라스트 반경으로 보류.
- 모바일·태블릿 반응형 확인(Section 6-13) 미실행.
- 실제 카테고리 조회 흐름(재조회 시 초기화)의 실클릭 검증은 API
  미호출 제약으로 대체 검증(코드 대조)에 그침 — 완전한 실클릭
  검증은 Phase 10 실제 승인 후 가능.
- 격리 브라우저에서 1~10단계 **전체**를 끝까지(승인→9단계 실제
  제출 버튼 클릭 직전까지) 실클릭한 것은 아니다 — 5단계 구매옵션
  UI까지만 실클릭했다.

### 14. 재설치 필요 시점

없음 — 이번 작업은 재설치 없이 코드 변경 + 임시 DB 검증만으로
가능했다. 최종 Live 검증(Phase 10) 직전에만 재패키징·재설치가
필요하다(기존 방침 그대로).

### 15. 사용자 최종 승인이 필요한 작업

- 위 13번 남은 항목 중 어느 것을 이번 범위에 추가로 포함할지.
- 실제 격리 브라우저에서 승인→9단계 제출 버튼까지 실클릭 검증을
  진행할지(여전히 실제 쿠팡 POST는 하지 않음 — 버튼 도달까지만).
- 전체 3101개 회귀 실행 시점(Phase 7 그대로 유지할지, 지금
  당길지).
- 실제 쿠팡 Live 제출(Phase 10)은 이번 보고와 별개로 반드시 사용자의
  새로운 최종 승인이 있어야 한다 — 지금 어떤 것도 이를 승인한 것으로
  간주하지 않는다.

### 16. 현재 판정

**`ISOLATED_IMPLEMENTATION_VERIFIED`**

판정 근거:
- 구매옵션이 UI부터 Payload까지 실제로 연결됐고, 누락값이 저장
  시점과 제출 직전 양쪽에서 fail-closed로 차단되는 것을 격리
  테스트(Python 통합 테스트)와 격리 브라우저 실클릭(렌더링·검증·
  수집 단계) 양쪽으로 확인했다.
- 그러나 **아직 격리 환경에서도 8~9단계(승인→제출 버튼)까지 끝까지
  실클릭하지 않았고**, 상품 상태 조회는 Service·Router 연결만
  했을 뿐 UI 버튼이 없으며, 전체 3101개 회귀도 아직 재실행하지
  않았다 — `COUPANG_LIVE_SUBMISSION_READY_APPROVAL_REQUIRED`로
  격상하려면 이 항목들이 먼저 필요하다.
- 실제 쿠팡 상품등록 성공·상태 조회 확인은 전혀 없다 —
  `COUPANG_LIVE_LISTING_VERIFIED`는 해당하지 않는다.
- z2의 "필수 구매 옵션 ... 존재하지 않습니다" 오류의 정확한 원인은
  여전히 `ROOT_CAUSE_UNPROVEN`이다 — 이번 조사로 메커니즘상 유력한
  후보 2개(구매옵션 카테고리 불일치, contents 누락)를 실제 원시
  데이터로 뒷받침했을 뿐, 원시 쿠팡 응답이 없어 증명하지는 못했다.

**COMPLETE/RELEASE_READY/LIVE_VERIFIED는 선언하지 않는다.**

---

## 후속 기록(2026-08-29) — `ISOLATED_IMPLEMENTATION_VERIFIED` → `COUPANG_LIVE_SUBMISSION_READY_APPROVAL_REQUIRED`

위에서 "격상 전 필요"로 지목한 3가지를 이번 세션에서 실제로 완료했다
(상세 근거·수정 파일·회귀 로그 경로는 `docs/HOMEZ_PROJECT_STATE.md`
"2026-08-29 쿠팡 상품등록 핵심 차단 해결" 항목 참고, 여기서는 이
문서의 판정 갱신만 남긴다):

1. **8~10단계 실클릭 완주**: 격리 Fake Provider 환경에서 승인
   미리보기 → 비밀번호 재인증 승인 → 실행 선택 → 전송 전 검사 →
   `쿠팡으로 전송` 클릭까지 실제로 완주. DB에 `SUBMITTED` /
   `external_submission_ref="FAKE-SELLER-PRODUCT-001"` 저장 확인.
2. **상태 조회 UI 버튼**: 10단계 결과 화면에 `쿠팡 상태 조회` 버튼을
   연결, 클릭 시 Fake Provider의 `APPROVED` 응답이 "승인완료"로
   정확히 표시됨을 확인.
3. **전체 회귀 재실행**: `Ran 3167 tests in 4355.198s` / `OK`
   (failures=0, errors=0, skipped=0). 재실행 중 실제 결함 1건(데이터
   손실 버그, 4단계 재저장 시 5단계 데이터 소실)을 추가로 발견해
   수정했고, 오래된 테스트 1건도 삭제된 UI 계약을 반영해 갱신했다.

z2 원인 조사(`ROOT_CAUSE_UNPROVEN`)는 이번 세션 범위 밖이라 그대로
남아 있다 — 원시 쿠팡 응답 없이는 여전히 증명 불가능하다.

**현재 판정: `COUPANG_LIVE_SUBMISSION_READY_APPROVAL_REQUIRED`.**
실제 쿠팡 상품등록 1회·상태 조회 성공 전까지
`COUPANG_LIVE_LISTING_VERIFIED`/`WORKFLOW_1_LIVE_END_TO_END_VERIFIED`/
`COMPLETE`/`RELEASE_READY`는 선언하지 않는다.
