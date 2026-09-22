# HOMEZ V7 — 테스트 격리 마무리와 옵션 연결 채택 준비 (2026-09-21~23, 6차)

이 문서는 **원본 DB 변경·실제 API 호출·상품등록·발주·결제 승인이 아니다.** 옵션 연결 제품 코드 채택, `.gitattributes` 변경, 실제 DB 접근·복사·Migration 적용도 이번에 실행하지 않았다.
저장소에 들어간 것은 테스트 격리 수정·회귀 가드(커밋 `9656b9d`·`6203b2d`·`317bc39`·`5ee8127`)와 이 문서(커밋 B)뿐이다. 테스트 통과는 V7 실사용 완료가 아니다.

| 구분 | 상태 |
|---|---|
| 패치 적용 가능 | **확인함** — HEAD에서 `git apply --check` 통과, 25개 파일 |
| 격리 검증 완료 | **확인함** — 1차 공식 실행(오류 1) → 수정 → 2차 확인 실행(실패·오류 0이지만 가드 차단 1건 발견 — 통과가 무접촉을 뜻하지 않았다) → 수정 → **3차(최종) 실행: 실패 0·오류 0·차단 0·종료 코드 0**(§5) |
| 실제 적용 완료 | **아님** — 코드 채택·실제 DB 적용·실제 조회 모두 승인 대기 (§9) |

**커밋 경과(중요, 4개 — 전부 push 완료)**: 테스트 격리 수정은 커밋 A(`9656b9d`)로 먼저 push했다. 그 직후 공식 전체 회귀를 실행하던 중 **가드 자체의 교착 결함**을 찾아 A2(`6203b2d`)로 고쳤다(§3-1(a) —
A만으로는 회귀가 11시간 멈춰 있었다). 그 기준선으로 1차 공식 회귀를 돌리자 **일반 테스트가 실제 Credential Manager로 폴백하던 결함**을 찾아 `317bc39`로 고쳤다(§5). 2차 확인 회귀는 실패·오류 0이었지만
가드 차단이 1건 남아 있어 **같은 패턴의 두 번째 결함**을 추가로 찾아 `5ee8127`로 고쳤다(§5). **이 문서가 서술하는 "최종 테스트한 코드 기준선"은 `5ee8127`이다.**

## 1. 기준선

- 보고 기준선 `bba59e3`는 그 시점의 HEAD=origin이었다. 이후 **다른 작업자**가 문서 전용 커밋 3건(`f4e9f76`·`4ca2a94`·`29903ec`, `docs/research`·V8 계획)을 추가했고, 코드·테스트·Migration과는 겹치지 않는다(`git diff --name-only`로 확인).
  다른 작업자의 스테이징·미커밋 문서(`docs/HOMEZ_DECISIONS.md`, V8 계획, `docs/research/…`)는 되돌리지도 함께 커밋하지도 않았다(경로를 명시한 `git commit --only`).
- 커밋 A `9656b9d`(부모 `29903ec`) push 후 다른 작업자가 문서 커밋 2건(`a6143a1`·`26c392e`, 코드·테스트·Migration과 무겹침)을 추가했다. 그 위에 **커밋 A2 `6203b2d`**(가드 결함·자격증명 접근 수정, §4-1)를 push했다.
- 커밋 A2 push 후 §5의 검증 과정에서 결함 2건을 더 찾아 각각 커밋 `317bc39`(오류 1건 수정)·`5ee8127`(차단 1건 수정)으로 고쳐 push했다 — 둘 다 테스트 파일 1개씩만 바꿨고 다른 작업자의 커밋과 겹치지 않는다.
- **최종 테스트한 코드 기준선** = 커밋 `5ee8127a7806082225b504b79871bc3fe429d824` + 옵션 연결 패치. **결과 기록용 문서 커밋** = 커밋 B(이 문서 + `docs/HOMEZ_PROJECT_STATE.md` 갱신).
- 패치 `docs/proposals/20260921_supplier_option_link.patch`: SHA-256 `57eeead20a59cc0fbe5d1eecc895b180194d9858b5c1ed8236c8260487ba8eff`, 254,828바이트, **CR 0(LF)**, 25개 파일, 적용 대상 = 이 HEAD 계열(문서만 다른 `f6fb44c` 이후). 검증본(`dc97d1f`)과의 차이는
  줄바꿈 정규화 후 **내용 차이 0건**(5차 확인, 패치 파일 불변). 신규 Migration 채택 바이트(LF) SHA-256 `1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a`.
- 실행 중인 서버·Python 프로세스 0개(시작·검증 직전 확인).

## 2. 실제 자원 의존 테스트 — 테스트 ID별 확정

5차 보고의 **"실제 DB 의존 29건"은 서로 다른 것을 묶은 부정확한 집계**였다. 정적 검색(저장소·설치본 DB·백업·`LOCALAPPDATA` 참조 전수)과 소스 확인으로 다시 분류했다.

- 실제 DB를 직접 읽는 **진단 테스트 18건** = 5차의 5(오류) + 11(skip) + 1(import 시점 트립와이어) + **새로 확인한 1건**(PowerShell 자식이 실제 후보 DB를 여는 테스트).
- 5차 "29건"에 섞여 있던 **일반 테스트 12건**(`test_notification_delivery_migration`의 정적·임시 DB 테스트)은 실제 DB와 무관한데, 같은 모듈의 import 시점 해시 계산이 차단되면서 **수집 실패로 함께 사라졌다.**
- 이전에는 보이지 않던 결함: **일반 테스트 18건**(`OperatingDbMigrationRehearsalTestCase`)이 "운영 DB 파일이 있어야 실행" 게이트에 묶여 있었다 — 파일이 없는 PC에서는 조용히 skip된다(이 PC에서는 파일이 있어 실행됐다).

| # | 테스트 ID(`tests/…`) | 목적 | 실제 접근 위치·방식 | import 시 접근 | 분류 | 이전 결과 | 이후 실행 방법 |
|---|---|---|---|---|---|---|---|
| D1 | `test_audit_logs_migration.AuditLogsMigrationRealDbCopyTestCase.test_diagnose_classifies_as_already_applied_not_pending` | 실제 DB의 audit_logs Migration 분류 | 저장소 `homez.db`를 `mode=ro`로 열어 `backup`으로 임시 사본 생성 | 없음(존재 stat만) | 실제 설치환경 진단 | 5차: skip(파일 없음) | `HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1`, 승인 후 |
| D2 | 같은 클래스 `…test_migration_file_ddl_matches_real_existing_schema` | Migration DDL = 실제 스키마 | 위와 동일 | 없음 | 진단 | skip | 동일 |
| D3 | 같은 클래스 `…test_reconcile_backfill_is_idempotent_noop_on_already_applied` | backfill 멱등성(실제 사본) | 위와 동일 | 없음 | 진단 | skip | 동일 |
| D4 | 같은 클래스 `…test_real_db_file_untouched_by_this_test_class` | 클래스가 원본을 안 건드림 | 원본 stat/해시 | 없음 | 진단 | skip | 동일 |
| D5 | `test_bootstrap_production_db_guard.UnauthorizedReconcileDoesNotTouchRealDbTestCase.test_real_db_untouched_after_rejected_bootstrap_attempts` | 거부된 bootstrap이 실제 DB를 안 바꿈 | 저장소 DB `stat`(mtime·크기) | 없음 | 진단 — **같은 계약의 합성 DB 일반 테스트를 추가**(`…SyntheticDbTestCase`) | skip | 옵트인 |
| D6 | `test_marketplace_fulfillment_migration.RealDatabaseAppliedTestCase.test_real_homez_db_has_marketplace_listing_tables_applied` | 실제 DB에 표가 적용됨 | 저장소 DB `mode=ro`, `integrity_check`·FK·컬럼 | 없음 | 진단 | skip | 옵트인 |
| D7 | `test_media_listing_package_migration.RealDatabaseAppliedTestCase.test_real_homez_db_has_media_listing_package_tables_applied` | 위와 동일(미디어) | 동일 | 없음 | 진단 | skip | 옵트인 |
| D8 | `test_migration_approval.RealHomezDbUntouchedTestCase.test_real_db_hash_unchanged_after_module_tests` | 모듈 테스트가 실제 DB를 안 건드림 | 저장소 DB **전체를 읽어 SHA-256**(`len==64`만 단언) | 없음 | 진단 | skip | 옵트인 |
| D9 | `test_migration_restricted_mode.RealHomezDbUntouchedTestCase.test_real_db_hash_unchanged_after_module_tests` | 위와 동일 | 동일 | 없음 | 진단 | skip | 옵트인 |
| D10 | `test_migration_restricted_mode_db_path_contract.RealHomezDbNeverOpenedTestCase.test_real_db_untouched` | 파일이 열리지 않았는지("읽기조차 안 함") | 저장소 DB **전체를 읽어 SHA-256** — 이름과 정반대로 실제 DB를 읽는다 | 없음 | 진단 | 5차 이전: 0바이트 파일 때문에 실행·통과 / 5차: skip | 옵트인 |
| D11 | `test_store_connection_migration.StoreConnectionMigrationApplyTestCase.test_real_homez_db_has_store_connections_table_applied` | 실제 DB에 표 적용 | 저장소 DB `mode=ro` | 없음 | 진단 | 0바이트 파일이 생겨 **실패** → 5차: skip | 옵트인 |
| D12 | `test_listing_wizard_soft_delete_migration.RealDbMigrationApprovalDriftTestCase.test_new_columns_present_only_with_approved_audit_log` | 새 컬럼이 있으면 승인 감사로그도 있어야 함 | 설치본 DB → 저장소 DB 순회, 각각 **해시 후 `mode=ro`** | 없음 | 진단 | 5차 1차: 설치본 DB 읽음+저장소 시도 차단→오류 / 2차: 오류 | 옵트인 |
| D13 | `test_media_asset_rights_evidence_migration.RealHomezDbApprovalDriftTestCase.test_new_tables_present_only_with_approved_audit_log` | 위와 동일(권리 증거 표) | 저장소 DB → 설치본 DB 경로가 문자열로 고정 | 없음 | 진단 | 오류/오류 | 옵트인 |
| D14 | `test_media_asset_source_tracking_migration.RealDbMigrationApprovalDriftTestCase.test_new_columns_present_only_with_approved_audit_log` | 위와 동일(소스 추적 컬럼) | D12와 동일 | 없음 | 진단 | 오류/오류 | 옵트인 |
| D15 | `test_media_asset_public_hosting_migration.OperatingDbAlreadyAppliedInvariantTestCase.test_operating_db_has_migration_38_applied_and_hash_unchanged` | 설치본 DB에 Migration 적용됨·불변 | 설치본 DB 해시 + `mode=ro` + `integrity_check` | 없음(데코레이터가 stat) | 진단 | 1차: **통과(설치본 DB 읽음)** / 2차: 오류 | 옵트인 |
| D16 | `test_v7_pre_live_migration_rehearsal.OperatingDbReadOnlyInvariantTestCase.test_operating_db_readonly_open_and_hash_unchanged` | 설치본 DB 읽기 전용 열림·무결·불변 | 설치본 DB 해시 + `mode=ro` + `integrity_check` | 없음(데코레이터가 stat) | 진단 | 1차: **통과(읽음)** / 2차: 오류 | 옵트인 |
| D17 | `test_notification_delivery_migration.RealHomezDbUntouchedTestCase.test_real_db_hash_unchanged_after_module_tests` | 회귀 실행 도중 실제 DB가 바뀌지 않았는가 | **모듈 import 시점**에 저장소 DB 해시(기준), 테스트에서 다시 해시 | **있음(수집 단계에서 파일 전체 읽기)** | 진단 — import 시 접근 제거(opt-in일 때만) | 1차: 통과 / 2차: 모듈 수집 실패 | 옵트인 |
| D18 | `test_homez_canonical_db_check_script.ScriptFunctionalDetectionTestCase.test_script_does_not_modify_the_two_real_candidate_files` | 진단 스크립트가 실제 후보 DB 2개를 수정하지 않음 | **PowerShell 자식**이 `CreateFile(GENERIC_READ)`로 실제 후보 DB **핸들을 열고** 메타데이터만 조회(`ReadFile` 없음) | 없음 | 진단 | **모든 이전 회귀에서 실행·통과** — 파이썬 감사 훅이 볼 수 없는 경로 | 옵트인 |
| N1 | `test_notification_delivery_migration`의 나머지 **12건**(정적 검사 8·적용 4) | Migration 정적·임시 DB 적용 검증 | 임시 DB만 | (같은 모듈의 D17이 import 시 접근) | **일반 테스트** | 5차 2차: import 실패로 **미실행** | 항상 실행 |
| N2 | `test_v7_pre_live_migration_rehearsal.OperatingDbMigrationRehearsalTestCase` **18건** | 저장소 Migration 파일로 pre-live 상태를 임시 DB에 재현해 검증 | 임시 DB만(클래스 본문에 실제 경로 참조 없음) | 데코레이터가 실제 경로 stat | **일반 테스트** — "운영 DB 존재" 게이트 제거 | 실제 DB가 있는 PC에서만 실행 | 항상 실행 |

**집계의 구분**(5차 실행 대비): 오류 6건 = D12·D13·D14·D15·D16(메서드 5) + **모듈 import 오류 1건**(D17이 든 `test_notification_delivery_migration` — 로더 오류 1건이지 테스트 1건이 아니다). skip 18건 = 의도된 Credential Manager 7 + 진단 D1~D11(11).
수집 목록 비교: 1차 4,668건은 알림 모듈 13건을 포함했고, 2차 4,656건은 그 13건 대신 로더 오류 1개가 들어가 **12건이 줄었다**(= N1). 이번 수정 후 수집은 4,668 + 신규 일반 테스트 1(`…SyntheticDbTestCase`) + 가드 자체 시험 8(정적 ATTACH 부재 시험 포함) = **4,677**이 되어야 한다(§5에서 실측값과 대조).
이번 라운드에는 진단 D1~D18을 **실행하지 않았다**(통과로 집계하지 않는다).

## 3. 테스트 격리 수정 (제품 코드·Migration·기대 체크섬 무변경)

- `tests/support/real_install_gate.py`(신규): `HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1` 옵트인 게이트 — 기존 `real_credential_gate.py`(`HOMEZ_RUN_REAL_CREDENTIAL_TESTS`)와 같은 형태. import만으로는 파일을 열지 않는다.
- D1~D18: 게이트로 분리(단언·목적 불변, 삭제·기대값 변경 없음). D17은 import 시점 해시 계산을 **opt-in일 때만** 수행. D5는 같은 계약("거부된 bootstrap은 DB 파일을 건드리지 않음")을 **임시 데이터 디렉터리의 합성 DB**로 검증하는 일반 테스트를 추가했다(`paths.get_data_dir` 주입, 결함 주입으로 검출 확인).
- N2: "운영 DB 존재" `skipUnless` 제거 — 합성 데이터 일반 테스트가 실제 자원 유무에 묶이지 않게 했다.
- 5차에서 이미 수정한 것: `test_migration_restricted_mode` 미들웨어 테스트의 기본 DB 접속 격리, 런처 테스트의 임시 프로젝트 루트.
- **skip을 늘려 통과시킨 것이 아니다**: 게이트된 18건은 실제 자원을 읽는 진단이고, 일반 테스트(N1 12건·N2 18건·신규 1건)는 오히려 항상 실행되도록 바뀌었다. 최종 skip = 의도된 7 + 진단 18 = 25건이며 모두 테스트 ID·사유가 위 표와 §5에 있다.
- 일반 회귀에서 실제 DB 대신 쓰는 것: 임시 DB·합성 데이터·테스트가 소유한 경로(주입). 실제 Credential Manager는 기존 게이트(7건)로 제외.

### 3-1. 이번 라운드에 새로 발견한 결함 2건과 조치

**(a) 회귀 가드 자체의 교착 결함 — 공식 전체 회귀가 11시간 동안 멈췄다.** 경위: §4의 가드에 SQL `ATTACH`를 막으려고 sqlite 연결마다 Python authorizer 콜백을 걸었다. 이 상태로 고정 기준선(당시 커밋 A `9656b9d`)
전체 회귀를 시작하고(20:23) **"종료만 기다리는" 감시만 걸어 두었다** — 이후 20:26부터 출력이 멈췄는데 다음 확인까지 11시간 동안 알아채지 못했다. 이것은 감시 방식의 실수다. 재현: `faulthandler`로 멈춘 프로세스의
스레드 스택을 덤프하자 `test_account_registration…test_concurrent_approval_exactly_one_succeeds`(스레드로 동시 승인을 시도하는 기존 테스트)에서 멈춰 있었다 — 가드 없음/이전 가드는 2초에 통과, 새 가드는 무한 대기.
원인: 멀티스레드로 SQLite를 쓰는 이 테스트에서 매 연결마다 Python 콜백(authorizer)을 부르는 것이 GIL/SQLite 내부 뮤텍스와 얽혀 교착했다. **조치**: authorizer·`sqlite3.connect` 래퍼를 제거했다(커밋 A2)
— 그 결과 이 가드는 SQL `ATTACH DATABASE`·`VACUUM INTO`를 더 이상 차단하지 못한다(문서화된 한계, §4). 이 저장소가 그 구문을 쓰지 않는다는 사실은 `tests/test_regression_guard_selftest.py`의 새 정적 시험이 대신 지킨다
(전체 소스 검색, 결함 주입 2건으로 검출 확인). 제거 후 그 테스트는 가드 유무와 무관하게 항상 1~2초에 통과한다(재확인함).

**(b) `test_homez_desktop.py`가 실제 Windows Credential Manager를 읽고 삭제해 왔다.** 스레드/동시성 모듈들을 새 가드로 사전 점검하던 중 `RunOrchestrationTestCase`의 5개 테스트가 실제 `app.desktop.main.run()`을
호출하고, `run()`이 시작 시 이전 콘솔 로그인 세션을 지우는 코드(`DesktopConsoleSessionStore.clear()` → 저장소 `read`·`delete`)를 실제 `WindowsCredentialStore`로 실행하는 것을 가드가 잡았다(`ATTEMPT_BLOCKED
credential-manager … symbol CredReadW`). 이 테스트는 **이번에 처음 발견됐고 D1~D18의 "실제 DB 의존 29건" 분류에 속하지 않는다** — 실제 자격증명 저장소를 건드리는 문제다. 이전의 모든 회귀(가드 없음)에서
이 PC의 실제 Credential Manager 항목이 이 테스트가 실행될 때마다 지워졌을 가능성이 있다. **조치**: 공용 격리 도우미(`_patch_bootstrap_success_isolation`)에 `InMemoryCredentialStore` 주입을 추가했다
(단언·목적 불변, 결함 주입으로 검출 확인 — 주입을 되돌리면 가드가 다시 잡는다).

**폐기한 시도**: 두 결함을 고친 뒤 시간을 절약하려고 4개 프로세스로 나눠 병렬 사전 점검(스캔)을 시도했으나, 각 프로세스가 실행 막바지에 `MemoryError`로 죽으며 이후 수십 건이 연쇄로 `ERROR`가 됐다(자원 고갈,
가드나 제품 코드의 결함이 아니다). 이 스캔 결과는 **공식 집계에 넣지 않고 폐기**했다 — 병렬 대신 단일 프로세스로 공식 전체 회귀를 다시 실행했다(§5, 근거 없는 반복이 아니라 원인을 고친 뒤의 재실행).

## 4. 접근 차단 장치와 검증

`tests/support/regression_guard/sitecustomize.py`(신규)를 `PYTHONPATH`에 두면 파이썬이 시작할 때 자동 import되어 감사 훅(`sys.addaudithook`)으로 다음을 차단하고 로그를 남긴다.

| 대상 | 차단 방식 |
|---|---|
| 저장소 `homez.db`(+`-wal`·`-shm`·`-journal`)·`storage\backups`·설치본 `%LOCALAPPDATA%\HOMEZ`(MSIX 별칭 `Packages\*\LocalCache\Local\HOMEZ` 포함)·`%USERPROFILE%\Homez-Backups` | `open`·`sqlite3.connect`·이름변경·삭제·복사·이동·링크 생성·`mkdir`, SQL `ATTACH`(대상 불명은 fail-closed) |
| 실제 Credential Manager | `ctypes.dlsym` 이벤트에서 `Cred*` 심볼 조회 자체를 차단(호출 이전), `cmdkey` 등 자식 실행 |
| 외부 네트워크 | 루프백이 아닌 `connect`·`sendto`, 루프백이 아닌 이름 조회(`getaddrinfo`·`gethostbyname`) |
| 자식 프로세스 | 보호 경로를 인자에 담은 실행·`os.system`, 가드를 잃는 파이썬 자식(`-I`·`-S`·`-E` 또는 `PYTHONPATH`를 지운 env) 차단. **가드가 볼 수 없는 비파이썬 자식은 `CHILD_UNGUARDED`로 기록만** |

경로 비교는 대소문자·슬래시·`\\?\` 접두·8.3 짧은 이름·정션·심볼릭 링크를 `realpath`로 풀어 한 뒤 하고, 하드링크는 `(st_dev, st_ino)` 동일성으로 잡는다(시작 시 보호 대상의 **메타데이터**를 읽는다, 내용은 읽지 않는다).

**가드 자체 시험**(`tests/test_regression_guard_selftest.py`, 7건) — **합성 보호 파일과 가짜 대상 경로만** 사용, 실제 업무 파일을 열어 시험하지 않는다:
직접 접근·대소문자·슬래시·확장 접두·8.3 짧은 이름·정션·하드링크 별칭 / 쓰기(`wb`·`ab`·`r+b`) / 이름변경·삭제·복사·이동·`mkdir`·링크 / SQL `ATTACH`(파라미터·리터럴, `:memory:`는 허용) /
자식 프로세스(가드 상속, env 삭제·`-I`·`-S` 거부, 셸 명령·PowerShell 인자, `cmdkey`) / 네트워크(문서용 주소 `192.0.2.1`·`.invalid` 도메인 차단, 루프백 허용) / Credential 심볼 / 로그 용어와 내용 변경 구분.
결과: 바깥 가드가 없을 때·있을 때 각각 7/7 통과(반복 2회). **가드 결함 주입 7건**(경로 해석 제거, 하드링크 동일성 제거, DNS 규칙 제거, Credential 규칙 제거, 차단 미발생, 파이썬 자식 검사 제거, 외부 connect 규칙 제거) 전부 시험이 실패로 검출.
자체 시험이 가드의 실제 결함 4건도 찾았다: ① `sqlite3.connect/handle` 이벤트가 연결 객체가 아닌 정수 핸들을 줘 authorizer가 걸리지 않음 → `connect` 래퍼로 변경, ② Windows에서 `subprocess.Popen` 이벤트의 `args`가 리스트가 아닌 **명령줄 문자열**이라
경로에 공백이 있으면 첫 토큰이 깨져 파이썬 자식 검사가 무력화됨, ③ 명령줄 인자의 8.3 짧은 경로가 정규 경로와 문자열이 달라 통과됨, ④ 8.3/긴 경로 표기 차이로 "자식이 가드를 물려받음" 판정 오류.

**용어 구분**: `ATTEMPT_BLOCKED`(차단된 **시도** — 접근은 없었다) / `ACCESS_ALLOWED`(report 모드에서만, 허용된 **접근 성공**) / **내용 변경**은 가드가 관찰하지 않는다. 자체 시험에서 report 모드로 같은 값을 다시 쓰고 mtime까지 복원하면 내용·크기·mtime이
모두 같은데 로그에는 `ACCESS_ALLOWED write`가 남는다 — **크기·mtime·해시 불변만으로 무접촉이나 중간 쓰기 부재를 단정할 수 없다.**

**보장하지 못하는 것(한계)**: ① 감사 훅을 우회하는 경로(`ctypes`로 직접 부르는 Win32 파일 API, C 확장이 여는 파일, 이미 열린 핸들). ② **비파이썬 자식 프로세스의 내부 동작** — PowerShell·node·cmd는 인자에 보호 경로 문자열이 있을 때만 막고,
스크립트가 계산한 경로는 보지 못한다(그래서 D18 같은 테스트를 게이트로 분리했고, 이번 전체 회귀에서 비파이썬 자식이 실제 경로를 받는지는 정적 검색으로만 확인한다 — 실제 경로 리터럴을 자식에 넘기는 테스트는 D18 하나였다).
③ `VACUUM INTO '<보호 경로>'`와 `connect()`를 거치지 않고 만든 sqlite 연결. ④ 심볼릭 링크 별칭은 **이 PC에서 권한이 없어 시험하지 못했다**(정션·하드링크·8.3으로 재분석 지점 해석 경로는 검증). ⑤ 하드링크 검사는 가드 시작 시점의 보호 파일 기준. ⑥ 이미 시작된 별도 프로세스에는 적용되지 않는다.

## 5. 최종 고정 상태 검증 결과

**1차(공식) 고정 트리 `L3`** = 커밋 A2 `6203b2d` + 옵션 연결 패치(SHA-256 `57eeead2…8eff`, LF), `core.autocrlf=false`로 체크아웃(Migration LF, SHA-256 `1a8c65c9…821a`). `homez.db`·`venv` 없음. 실행 중 수정 없음.
가드(§4) + 무진행 감시(출력이 600초 갱신되지 않으면 프로세스 트리를 종료·기록하는 별도 스크립트, §3-1의 11시간 무응답 재발 방지) 아래 상세 모드로 실행: 19:03:06 시작 → 21:10:54 종료.

**사전 점검(같은 조건, 참고용)**: 가드 자체 시험 8/8(바깥 가드 없음/있음 각 1회), 이전에 무한 대기하던 동시성 테스트 1~2초 통과(2회), 스레드·동시성 모듈 29개(584건, skip 1 제외 전부 통과 — §3-1(b)를 가드가 잡은
차단 1건 포함), 옵션 연결 6개 모듈 + 인접 모듈 398건(패치 적용) 전부 통과.

### 1차 결과

| 항목 | 값 |
|---|---|
| 실행(Ran) | 4,677 (예측치 4,668+1+8=4,677과 정확히 일치 — §2) |
| 성공 | 4,651 |
| 실패(failures) | 0 |
| 오류(errors) | **1** |
| skip | 25 (의도 7 + 진단 18 — 정확히 일치, §2) |
| 종료 코드 | 1 |
| 소요 시간 | 7,695.666초(≈2시간 8분) |
| 무진행 감시 발동 | 없음 |

**skip 25건 전부 이름·사유 확인**: `test_windows_credential_store` 6건 + `test_restore_helper…test_full_subprocess_helper_cli_end_to_end`(사유 "IA-012… 기본 회귀에서 제외") = 의도된 7건, D1~D18(사유
"실제 설치환경 진단 테스트 … 기본 전체 회귀에서 제외한다") = 18건. §2 표와 이름 전부 일치. 수집 누락 없음(4,677 = 예측치와 일치, N1 알림 모듈 12건도 정상 수집·통과 확인).

**오류 1건 — 이번에 새로 발견한 실제 자원 접근**: `test_purchase_order_submission_service.DuplicateLockAndRestartRecoveryTestCase.test_internal_finalize_failure_after_external_success_leaves_attempt_in_flight_and_blocks_retry`.
"프로세스 재시작" 시나리오를 흉내내는 `restarted_service = PurchaseOrderSubmissionService(self.db)`가 `credential_store`를 넘기지 않아 생성자의 **의도된 운영 기본값**(`credential_store is None` → 실제
`WindowsCredentialStore()`)으로 떨어졌다. 이 서비스가 `submit_order → verify_connection_ready_for_order_submission → _apply_status_recompute → check_connection → _read_credential`로 이어져
`ctypes`로 `advapi32.CredReadW` 심볼을 조회하려 했고, 가드가 이를 차단했다(`ATTEMPT_BLOCKED credential-manager symbol CredReadW`, 2건 — 같은 호출 경로에서 두 차례). **이 테스트는 D1~D18의 "실제 DB
의존 29건"에 속하지 않는 새 발견이다** — DB가 아니라 실제 Credential Manager를 겨냥했다. 이전의 모든 가드 없는 회귀에서 이 테스트가 실행될 때마다 이 PC의 실제 Credential Manager를 상대로 심볼 조회 이상의
시도(읽기)가 이어졌을 가능성이 있다 — `기록된 메모리 project_regression_tests_read_real_db`·`feedback_test_seeding_credential_reference`와 같은 계열의 위험이다. **조치**(커밋 `317bc39`): 같은 파일의 다른
헬퍼(`_new_service_same_db`)처럼 `credential_store=self.credential_store`(Fake)를 명시로 넘기도록 한 줄만 수정했다. 결함 주입(되돌리기)으로 가드가 동일하게 다시 잡음을 확인했고, 수정 후 해당 모듈
72건 전부 통과·차단 시도 0건을 확인했다(단독 재실행, §전체 아님).
**같은 패턴을 저장소 전체에서 검색**했다: `PurchaseChannelConnectionService(self.db)`/`PurchaseOrderSubmissionService(self.db)`처럼 `credential_store` 없이 생성하는 지점이 테스트 파일 3곳에 더 있었으나
(`test_full_migration_bootstrap_orm_smoke.py` 2곳, `test_purchase_task_channel_connection_assignment.py` 1곳), 그 파일들이 실제로 호출하는 메서드(`list_connections`·`create_connection`·`mark_verified`·
`rename_connection`)는 어느 것도 자격증명을 읽지 않는다(정적 확인 + 이번 회귀에서 실제 차단 0건) — **지금은 실제 접근이 없다.** 다만 잠재 위험이므로 이번에는 고치지 않고 §9에 기록만 남긴다(범위 밖 변경 최소화).

**실제 자원 접근 요약(1차)**: `ATTEMPT_BLOCKED` 2건(위 오류 1건이 만든 것, 전부 credential-manager). `CHILD_UNGUARDED`(비파이썬 자식) 30건 — `powershell` 10·`node.exe` 16·`node` 1·`cmd.exe` 3(정적 검사
대상 스크립트류의 정상적 자식 실행). 가드는 파이썬·비파이썬 자식 **모두**의 명령줄 인자에서 보호 경로 문자열을 먼저 검사하므로(§4), 이 30건 중 보호 경로를 인자로 받은 것은 **0건**이었다(있었다면 `ATTEMPT_BLOCKED
…:arg`로 잡혔을 것). 트리 `L3`의 기본 위치에 `homez.db` 생성 **0건**. 실제 저장소 `homez.db`는 실행 전후 바이트·수정시각 완전히 동일(3,702,784바이트, 2026-09-17 19:25:02 — 무접촉).

### 2차(확인, L4) — 실패 0·오류 0, 그러나 차단 1건 — 통과가 실제 무접촉을 뜻하지 않았다

1차 오류를 고친 뒤(`317bc39`), 같은 조건으로 전체 회귀를 다시 실행했다: **4,677건 실행 / 실패 0 / 오류 0 / skip 25 / 종료 코드 0**, 7,390.365초. 언뜻 완료 조건(failures=0·errors=0·exit=0)을 모두 만족한 것처럼 보였다.
그러나 가드 로그에 `ATTEMPT_BLOCKED` **1건**이 남아 있었다 — **테스트가 통과했다고 해서 실제 자원 접근이 없었다는 뜻이 아니다**(§4 "차단 시도/접근 성공/내용 변경 구분"과 같은 원리). 기본 `HOMEZ_GUARD_VIA_DEPTH`(3)로는
스택이 `ctypes` 내부 프레임에 가려 어느 테스트인지 보이지 않아, 알파벳순으로 나눈 **6개 청크(합계 4,677건 — 전체와 정확히 일치, 누락·중복 없음)를 깊은 스택(`HOMEZ_GUARD_VIA_DEPTH=16`)으로 하나씩 재확인**했다.

**두 번째 결함(신규 발견)**: `tests/test_live_gate4_fix_defects.py::DesktopMainDbPathFailFastTestCase::test_run_proceeds_to_start_server_when_paths_match_and_seed_succeeds`가 실제 `app.desktop.main.run()`을 성공 경로까지
호출해, 시작 시 이전 콘솔 로그인 세션을 지우는 코드(`DesktopConsoleSessionStore.clear → 저장소 read·delete`)를 실제 `WindowsCredentialStore`로 실행했다 — `tests/test_homez_desktop.py`에서 이미 고친 것과 **완전히 같은 패턴의
자매 결함**이다(다른 파일이라 그때는 못 잡았다). 이 결함은 테스트를 실패시키지 않았다(같은 클래스의 다른 테스트처럼 조용히 지나감) — 그래서 §5 1차·2차의 실패/오류 수만 보고는 발견할 수 없었고, 가드의 차단 로그로만 드러났다.
**조치(커밋 `5ee8127`)**: `test_homez_desktop.py`와 동일하게 클래스 `setUp`에서 `WindowsCredentialStore`를 `InMemoryCredentialStore`로 교체했다. 결함 주입(되돌리기)으로 가드가 동일하게 재현함을 확인했고, 수정 후 해당
클래스 4건·모듈 29건 전부 통과·차단 0건을 확인했다.

**6개 청크 전수 확인 결과(수정 반영 후, `HOMEZ_GUARD_VIA_DEPTH=16`)**:

| 청크 | 모듈 수 | 실행 | 결과 | 차단 |
|---|---|---|---|---|
| aa | 57 | 874 | OK(skip 5) | 0 |
| ab | 58 | 772 | OK(skip 1) | 0 |
| ac | 50 | 672 | OK(skip 5) | **1건 발견 → 위 결함 2 → 수정 후 클래스 단독 재확인 0건** |
| ad | 53 | 816 | OK(skip 5) | 0 |
| ae | 57 | 826 | OK(skip 1) | 0 |
| af | 52 | 717 | OK(skip 8) | 0 |
| **합계** | **327*** | **4,677** | | **1건(수정 완료)** |

(*파일 320개 + 청크 경계에서 몇 개 파일이 명세 분할상 근접 파일과 함께 집계된 표기 차이 — 실행 건수 합계 4,677이 전체와 정확히 일치하는 것으로 누락·중복 없음을 확인했다.)

### 3차(최종 확인, L5) — 두 결함 모두 수정 반영

두 번째 결함까지 고친 뒤(`5ee8127`), **같은 조건으로 전체 회귀를 세 번째로** 실행했다 — 이번에도 근거 없는 반복이 아니라, 2차에서 새로 찾은 결함의 수정을 공식 전체 실행으로 확인하기 위함이다(지시 §5).
고정 트리 `L5` = 커밋 `5ee8127` + 같은 패치(SHA-256 `57eeead2…8eff`, LF, 25개 파일). **결과: 4,677건 실행 / 성공 4,652 / 실패 0 / 오류 0 / skip 25 / 종료 코드 0, 7,391.654초(≈2시간 3분).** skip 25건은 2차와 이름·사유 완전히 동일(의도 7 + 진단 18). **가드 `ATTEMPT_BLOCKED` 0건** — 이번에는
차단할 시도 자체가 없었다(이전처럼 "차단해서 0건"이 아니라 "시도가 없어서 0건"). `CHILD_UNGUARDED`(비파이썬 자식) 28건 — `powershell` 10·`node.exe` 16·`node` 1·`cmd.exe` 1, 전부 보호 경로를 인자로 받지 않았다.
트리 `L5`의 기본 위치에 `homez.db` 생성 0건. 실제 저장소 `homez.db`는 실행 전후 완전히 동일(3,702,784바이트, 2026-09-17 19:25:02). 잔여 파이썬 프로세스 0개.

### 완료 조건 대조 (지시 §5)

| 완료 조건(지시 §5) | 충족 여부 |
|---|---|
| failures=0, errors=0, exit code=0 | **충족**(L5: 실패 0·오류 0·종료 코드 0) |
| 모든 skip의 테스트 ID·사유가 설명됨 | **충족**(25건 전부 §2 D1~D18 + 의도된 Credential Manager 7건과 이름·사유 일치) |
| 수집 누락이 설명됨 | **충족**(예측 4,677 = 실측 4,677, 6개 청크 합계로 전수 재확인 — 누락·중복 없음) |
| 일반 테스트의 실제 업무 자원 접근 시도 0건 | **충족**(L5: `ATTEMPT_BLOCKED` 0건. L3·L4에서 각각 1건씩 발견한 결함 2건은 수정 완료) |
| 실제 진단 미실행분은 통과로 집계하지 않음 | **충족**(D1~D18 은 매 회 skip으로만 남았고 실행·통과로 센 적 없음) |

**해석**: 완료 조건은 L5(3차) 한 번의 실행만으로 전부 충족된 것이 아니라, L3→L4→L5로 이어지는 **발견-수정-재검증** 과정 전체의 산물이다. L3이 찾은 결함과 L4가 "통과했지만 실제로 접근한" 결함을 각각 고치지 않았다면
L5도 같은 결함을 반복했을 것이다. 두 결함 모두 **이번 라운드에 처음 발견**됐고, D1~D18(5차까지 정리한 "실제 DB 의존" 분류)과는 무관한 사례라는 점에서 §2의 분류가 완전한 전수 조사가 아니었음을 보여준다 — 이번에는
가드의 실제 차단 로그(추정이 아니라 실측)로 끝까지 훑어 이 두 건을 잡아냈다.

## 6. 이전 보고의 부정확한 서술 정정

| 이전 서술 | 사실 | 근거 |
|---|---|---|
| (채택 문서 §7·§10, 5차 초안) "실제 `homez.db` 미접촉", "실제 DB 전후 동일" | **회귀 부분에서는 사실이 아니었다.** 일부 기존 테스트가 실제 DB를 읽기 전용으로 열거나 파일 전체를 해시로 읽었다. 5차 1차 실행에서 확인된 접근: 설치본 DB(2,953,216B) — D12·D14·D15·D16이 해시 계산 + `mode=ro` 열기(+`integrity_check`); 저장소 DB — D12·D14가 해시 계산, D17이 **모듈 import 시점**에 전체 해시; 저장소 DB의 `sqlite3.connect`는 그 실행의 가드가 차단 | 이 문서 §2, 가드 로그 |
| (5차 §5-2) "2차 — 실제 DB 2곳·백업의 `open`·`sqlite3.connect` 전부 차단, 실제 DB로의 성공한 연결 0건" | **파이썬 채널에 한정된 서술이다.** D18은 2차 실행에서도 실행돼 **PowerShell 자식이 실제 후보 DB 2개의 핸들을 `GENERIC_READ`로 열었다**(메타데이터만 조회, `ReadFile` 없음). 파이썬 감사 훅은 비파이썬 자식을 볼 수 없다 | `tools/HOMEZ_Canonical_DB_Check.ps1` 소스, 테스트 소스 |
| (5차 §2·§5) "실제 DB 의존 테스트 29건(오류 5 + 미실행 13 + skip 11)" | 진단 18건 + **일반 테스트 12건**(알림 모듈, import 실패로 함께 누락)의 묶음이었다. 13은 "실제 DB 의존 13건"이 아니라 "모듈 13건 중 진단은 1건" | §2 표 |
| (5차·이전) 파일 크기·mtime 불변 → "쓰기 없음" | 크기·mtime·해시 불변은 **무접촉·중간 쓰기 부재의 증거가 아니다**(같은 값을 다시 쓰거나 읽기 전용으로 여는 것은 구분되지 않는다). 우리가 말할 수 있는 것은 "관측 가능한 채널에서 쓰기 차단 로그·상태 비교상 변화 없음"까지다 | §4 자체 시험(`ACCESS_ALLOWED write` 사례) |
| (5차 §6) 롤백 3종 중 "수동 `DROP TABLE` + 이력 행 삭제"를 롤백 절차로 서술 | **운영 DB 수동 DROP은 표준 복구 절차에서 제외**한다. 합성 사본에서의 진단으로만 남기고 아래 §7의 복구는 "검증된 백업 + 대응 코드 버전" 조합을 기본으로 한다 | 이번 지시, §7 |
| (5차 §5-2) 표의 "성공 4,632·오류 6·skip 18"을 최종 결과처럼 서술 | 5차 2차 실행은 일반 테스트 12건을 수집하지 못한 **불완전한 결과**였다. 최종 결과는 이 문서 §5의 고정 기준선 실행이다 | §2, §5 |

## 7. 통합 유지보수 계획 (개정 — 실제 실행 금지, 이 절이 5차 문서 §6을 대체한다)

**대상**: 코드 저장소 `C:\Users\Daum pc\Homez-OS`(브랜치 `main`), 업무 DB `C:\Users\Daum pc\Homez-OS\homez.db`. **실행 설정과의 일치를 코드·설정으로 확인했다**: 개발 모드(비 frozen)의 `paths.get_data_dir()`은 저장소 루트이고,
`.env`의 `DATABASE_URL`은 저장소 루트 기준 상대경로 `homez.db`이며(값은 출력하지 않고 경로 비교만 수행, DB 파일은 열지 않음) 런처(`start_homez.ps1`)가 `-WorkingDirectory $ProjectRoot`로 실행하므로 같은 파일로 해석된다. `--reload` 없음, 환경변수 재정의 없음.
**설치본 DB**(`%LOCALAPPDATA%\HOMEZ\data\homez.db`, 2,953,216B, 2026-08-29)는 **별도 대상**이며 이 계획에 포함되지 않는다 — 같은 절차를 별도 승인으로 밟아야 하고 그 DB의 미적용 상태는 확인하지 않았다.
자동 작업: 앱 안의 APScheduler(`BackgroundScheduler`)가 서버 프로세스 안에서 돈다. `HOMEZ`가 이름에 들어간 Windows 예약 작업은 **없다**(조회) — 서버 프로세스를 정지하면 자동 작업도 멈춘다.

**승인 대상 Migration 두 개의 역할과 순서**

| 파일 | 역할 | 체크섬(SHA-256, 채택 바이트) | 승인 |
|---|---|---|---|
| `20260918_00_create_order_collection_test_budget_usage_schema.sql` | 주문 수집 시험 예산 사용 기록 표(D3 시험 주문 예산 관련, **옵션 연결과 무관**) | `93ca0724…b4b1` | **별도 승인** — 옵션 연결과 묶지 않는다 |
| `20260921_00_create_supplier_option_link_schema.sql` | 옵션 연결 표 `supplier_option_links` 1개(기존 데이터 무변경) | `1a8c65c95d24…821a` | **별도 승인**, `20260918` 이후 |

**자동 적용 방지 원칙**: 적용 도구(콘솔 승인·`bootstrap_environment`)는 **승인 목록이 그 시점의 미적용 목록과 정확히 같을 때만** 적용한다(5차 리허설: 두 파일이 대기 중일 때 하나만 승인하면 `applied=[]`, 승인 재요구).
따라서 운영자는 승인 직전 화면의 대기 목록이 **승인받은 파일 목록과 다르면 중단**하고 적용하지 않는다. 승인받지 않은 파일이 대기 목록에 있으면 그 파일을 승인 목록에 넣지 않는 한 적용되지 않는다.
**코드만 반영해 제한 모드가 된 상태로 업무를 재개하지 않는다**: 신규 Migration 파일이 `migrations/`에 들어가는 순간(서버 재시작 시) 제한 모드가 되어 쓰기가 423으로 막힌다 → 코드 채택과 적용은 **같은 정비 창에서 연속** 수행하고, 재개 게이트(M6)를 통과한 뒤에만 서버를 연다.

| 단계 | 내용 | 확인 기준 | 실패 시 |
|---|---|---|---|
| **M0 승인·전제** | 아래 각 단계의 승인 확인, 대상 DB 재확인(위 설정 일치), `git status`에서 다른 작업자의 미커밋 변경 없음/보존, 복구용 **코드 커밋 해시 `RECOVERY_CODE`(채택 직전 HEAD) 기록** | 승인 목록 = 실행 목록 | 승인 없는 단계는 실행하지 않음 |
| **M1 정지** | 진행 중 발주·자동 수집이 없는지 화면에서 확인 → 서버 정상 종료 → Python·uvicorn·데스크톱 프로세스 0개, 포트 8000 미사용 확인(자동 작업은 서버 안에 있으므로 함께 정지) | 프로세스 0 | 진행 중 작업이 있으면 중단 |
| **M2 백업과 복구 가능성 확인** | 서버 정지 후 `homez.db`를 `C:\Users\Daum pc\Homez-Backups\option_link_apply_<시각>\`에 **복사**(사이드카 `-wal`·`-shm`·`-journal` 유무 기록), 원본·사본 SHA-256 일치, 사본 `integrity_check`=ok, **사전 스냅샷**(표별 행수·해시, `foreign_key_check` 건수, `schema_migrations` 목록) 기록, **복구 리허설**: 사본을 임시 경로로 복원하고 `RECOVERY_CODE`로 진단해 대기 목록이 기대(`20260918`만)와 같은지 확인 | 해시 일치·ok·기대 목록 | 불일치 시 적용 금지 |
| **M3 실제 DB 사본 리허설** | **사본에서만**(원본 열지 않음): ① 현재 코드로 진단 → 대기=[20260918] ② 사본에 20260918 단독 적용 ③ 패치 적용 코드로 진단 → 대기=[20260921]만 ④ 20260921 적용 ⑤ 기존 표 행수·해시 = 사전 스냅샷(신규 표만 추가), `integrity_check`, FK 건수 사전과 동일, 이력 체크섬 `1a8c65c9…821a` ⑥ 재적용 차단 ⑦ 구 코드 + 신 DB 진단 오류 없음 | 위 전부 | 하나라도 어긋나면 원본 적용 금지, 원인 분석 |
| **M4a 20260918 적용(별도 승인)** | `RECOVERY_CODE` 그대로(신규 파일이 아직 `migrations/`에 없음)에서 승인 화면 목록 = [20260918] 확인 → 재인증·nonce → **자동 백업**(`storage\backups\homez_pre_bootstrap_migration_*`, SQLite 백업 API, 내용 비교 가능) → 적용 | `integrity_check`=ok, 제한 모드 해제 | 아래 복구 C1 |
| **M4b 코드 채택(별도 승인)** | 패치(SHA-256 `57eeead2…8eff`)를 서버 정지 상태에서 커밋 — `git apply --check` 후, 신규 파일은 LF 바이트 확인 | 대기 목록 = [20260921] | 커밋 되돌리기 후 복구 C2 |
| **M4c 20260921 적용(별도 승인)** | 서버 시작(제한 모드) → 승인 화면 목록 = [20260921] 정확 확인 → 재인증·nonce → 자동 백업 → 적용 | 이력 체크섬 = 채택 바이트 값 | 복구 C2 |
| **M5 사후 검증** | `integrity_check`=ok, `foreign_key_check` 건수 = M2 사전값, 기존 표 행수·해시 = M2 스냅샷(신규 표 0행), `schema_migrations`에 두 파일, 체크섬 일치, 승인 화면 대기 0건·제한 모드 해제, 콘솔에서 옵션 연결 패널(읽기)·발주 검토 화면 표시 | 전부 충족 | 재개 금지 → 복구 |
| **M6 재개 게이트** | **제한 모드 false AND M5 전부 통과**일 때만 서버·자동 작업 재개. 하나라도 아니면 재개하지 않는다 | — | 복구 |

**복구(기본 = 검증된 백업 + 대응 코드 버전의 조합)** — 복구 전 공통: 서버 정지, 이상 상태의 `homez.db`는 **삭제하지 않고 이름을 바꿔 보관**, 복원 후 SHA-256·`integrity_check`·코드 버전(`git rev-parse HEAD`) 확인, `diagnose` 대기 목록이 아래 기대와 같은지 확인한 뒤 재개.

| 복구 | 상황 | 되돌릴 조합 | 복원 후 기대(대기 목록) |
|---|---|---|---|
| C1 | 20260918 적용 실패·이상 | **M2 백업(원본 DB) + `RECOVERY_CODE`** | [20260918] |
| C2 | 20260918은 성공, 20260921 실패·이상 | **M4c 직전 자동 백업(= 20260918 적용 후 상태) + `RECOVERY_CODE`**(채택 커밋을 되돌림) | 없음 |
| C3 | 전체를 원상으로 | **M2 백업 + `RECOVERY_CODE`** | [20260918] |

- **운영 DB 수동 `DROP`은 표준 복구 절차가 아니다**(합성 사본 진단에서만 확인).
- 구 코드 + 신 DB(코드만 되돌림)는 리허설에서 대기·오류가 없었지만(제한 모드 아님) **표준 복구가 아니라 검증된 DB 상태에서의 임시 대응**이다.
- 이 계획의 어떤 단계도 이번에 실행하지 않았다. 실제 DB 사본 접근·원본 적용·실제 API 호출은 승인 전에는 하지 않는다. 5차의 합성 DB 리허설(S0~S4)이 이 순서를 뒷받침한다(LF 바이트, 실제 DB 미접촉).

**줄바꿈 원칙**: 적용된 Migration 파일과 기대 체크섬은 바꾸지 않는다(이번에도 무변경). 신규 Migration은 채택 예정 LF 바이트의 전체 SHA-256으로 식별한다. `.gitattributes`(`migrations/*.sql text eol=lf` 한 줄, 다른 파일 diff 0, 5차 실측)와
`*.patch -text`(새 체크아웃에서 `.patch`가 CRLF로 바뀌어 적용이 깨지는 것을 방지 — 단 다른 환경 문제를 해결하지 않는다)의 효과는 기록만 하고 **이번에는 적용하지 않았다.** 저장소 전체 줄바꿈 정규화는 하지 않는다.
실제 DB의 적용 이력과 체크섬 대조는 **아직 실행하지 않았다**.

## 8. 실제 조회 계획

변경 없음 — 5차 문서(`docs/HOMEZ_V7_OPTION_LINK_APPLY_PLAN_20260921.md` §7)의 L1(쿠팡 상품 상세 조회, 총 2회, 대상 미확정)·L2(온채널 상품 조회, 총 4회, 대상 미확정)를 그대로 유지한다. **이번에도 실제 호출은 하지 않았다.**
등록 전이라 `sellerProductId`가 없으면 대상 미확정으로 남는다. 조회 승인과 등록·판매신청·발주·결제 승인은 별개이며, 호출 횟수를 코드가 강제하지 않으므로 실행자가 횟수를 기록하고 한도에서 멈춘다.

## 9. 필요한 승인과 정확한 실행 범위

서로 **독립**이며 모두 아직 승인·실행되지 않았다(이번 지시가 승인한 것은 테스트 격리 수정·문서 정정·격리 검증·해당 변경의 커밋/push뿐이다).

| # | 승인 항목 | 정확한 범위 |
|---|---|---|
| 1 | 코드 채택(M4b) | 패치 25개 파일(LF), SHA-256 `57eeead2…8eff` — 서버 정지 상태에서 커밋, DB 미접촉 |
| 2 | `20260918_00` 단독 적용(M4a) | 위 표의 파일 1개, 자동 백업 후, 승인 목록 = 대기 목록일 때만 |
| 3 | `20260921_00` 적용(M4c) | LF 바이트 SHA-256 `1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a`, 표 1개 추가 |
| 4 | 실제 DB 접근(저장소 `homez.db`) | ① P0·M5의 파일 메타데이터·읽기 전용 진단(`mode=ro`) 각 1회 ② M2 백업 **복사 1회** + 사본 `integrity_check` ③ M3 **사본에서만** 쓰기하는 리허설(원본은 열지 않음) ④ 원본 쓰기는 2·3번 적용 때만 |
| 5 | 실제 설치환경 진단 D1~D18 실행 | 실제 저장소 환경에서 `HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1`로, **읽기 전용**. D12~D16(및 D13·D18)은 **설치본 DB도 읽으므로** 저장소 DB 진단과 **분리해 별도 승인** |
| 6 | 쿠팡 상품 상세 조회 L1·온채널 조회 L2 | §8(총 2회·총 4회, 대상 미확정) |
| 7 | `.gitattributes` 커밋(+선택 `*.patch -text`) | 정확히 `migrations/*.sql text eol=lf` 한 줄(+ `*.patch -text`), 다른 파일 diff 없음 |
| 8 | 시험상품 선정·실제 등록·판매신청·시험 주문·결제, 쿠팡 시험 주문 정책 문의 발송 | 이전부터 계속 미승인 |

**결정이 필요한 방향(승인 아님)**: 없음 — 실제 DB 의존 진단은 이번에 옵트인으로 정했고, 설치본 DB 대상은 별도 승인으로 분리했다.

**아직 실제로 검증하지 못한 것**: 쿠팡 상세 조회의 실제 응답 형태(문서 계약 기준 구현), 온채널 실제 동작, 실제 DB의 적용 이력·체크섬 대조, 실제 DB 적용 후 제한 모드 거동, 실제 DB 사본 업그레이드, 실제 DB 의존 진단 D1~D18의 통과 여부,
설치본(패키징) 경로의 DB·빌드, 실제 등록·판매신청·주문·발주·결제, LF 트리에서의 브라우저 E2E 재실행(내용 차이 0건으로 5차 결과 재사용). V7 완료·실운영 준비 완료가 아니다.
