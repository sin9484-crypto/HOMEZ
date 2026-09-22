# HOMEZ V7 — 옵션 연결 v2 검증 정리 및 코드·DB 통합 적용 계획 (2026-09-21, 5차)

이 문서는 **원본 DB 변경·실제 상품등록·발주·결제 승인이 아니다.** 실제 저장소 코드·`migrations/`·실제 `homez.db`는 이번에도 바뀌지 않았다
(이번에 저장소에 들어가는 것은 테스트 2개 수정과 문서뿐이다). 판정 용어는 다음처럼 구분한다.

| 구분 | 상태 |
|---|---|
| 패치 적용 가능 | **확인함** — `git apply --check`가 HEAD `d09a536`에서 통과, 25개 파일 |
| 격리 검증 완료 | **확인함** — 실제 적용 바이트(LF)로 만든 검증 트리에서 전체 회귀·Migration 리허설 (§5, §6) |
| 실제 적용 완료 | **아님** — 코드 채택·실제 DB 적용·실제 조회 모두 승인 대기 (§8) |

> **6차(2026-09-21) 정정 — 이 문서를 읽을 때 반드시 함께 볼 것**: `docs/HOMEZ_V7_TEST_ISOLATION_20260921.md`
> - §2·§5의 **"실제 DB 의존 29건"은 오집계**였다(진단 18건 + 일반 테스트 12건이 섞임, 진단에 PowerShell 자식 1건 추가). 테스트 ID별 확정 분류는 6차 문서 §2.
> - §5-2의 "실제 DB 열기 전부 차단·성공한 연결 0건"은 **파이썬 채널에 한정된 서술**이다(비파이썬 자식이 실제 후보 DB 핸들을 연 테스트가 있었다). 5차 2차 실행은 일반 테스트 12건을 수집하지 못한 불완전한 결과였다.
> - §6의 롤백(특히 수동 `DROP`)은 **표준 복구 절차로 쓰지 않는다** — 6차 문서 §7의 "검증된 백업 + 대응 코드 버전" 조합으로 대체됐다.
> - 크기·mtime·해시 불변은 무접촉의 증거가 아니다(6차 문서 §4·§6).

## 1. 기준선

- 원격 `origin/main` = 로컬 `HEAD` = `d09a536`, 작업 트리 깨끗(시작 시점). 실행 중이던 Python·서버 프로세스 없음, 실제 `homez.db`는 2026-09-17 19:25 / 3,702,784바이트
  그대로(`-wal`·`-shm` 없음, 코드에 WAL 설정 없음). 다른 작업의 변경 없음.
- 패치 `docs/proposals/20260921_supplier_option_link.patch`: SHA-256 `57eeead20a59cc0fbe5d1eecc895b180194d9858b5c1ed8236c8260487ba8eff`, 254,828바이트,
  **CR 0바이트(LF)**, 적용 대상 HEAD `d09a536`(= 문서만 다른 `f6fb44c` 이후), 25개 파일 +4,890/−3.
  신규 9개: `supplier_option_link_service.py`(1,094줄)·`supplier_option_link_router.py`·`listing_wizard_option_link_service.py`·
  `migrations/20260921_00_…sql`·테스트 5개 / 수정 16개: `model.py`·`service.py`·`order_approval_service.py`·`router.py`·`schema.py`·`constants.py`·`main.py`·
  `coupang_live_provider.py`·`coupang_test_fakes.py`·`listing_wizard_live_service.py`·`listing_wizard_router.py`·`console.js`·`console.css`·`i18n` 2개·
  `test_permissions_timestamps_migration.py`.
- **검증한 복제본(`dc97d1f`)과 패치의 차이**: 25개 파일을 줄바꿈을 정규화해 비교하면 **내용 차이 0건**이다. 다만 **바이트가 다르다** — 복제본에서는 12개 파일이 CRLF로 저장돼
  있었고(Migration 포함), 패치는 저장소 관례(전 파일 LF)로 정규화돼 있다. 이 차이가 체크섬에 영향을 주므로 §4에서 다룬다.

## 2. 전체 회귀의 실패 2건·skip 16건 — 확정 분류

이전 전체 회귀 로그(상세 모드가 아니었다)에는 skip 이름·사유가 없어, **같은 조건**(복제본 `w6f`, `homez.db` 없음, `venv` 없음)에서 skip 조건이 있는 23개 모듈(500건)을
알파벳 순서·상세 모드로 다시 실행했다. 결과는 전체 회귀와 정확히 같은 **실패 2·skip 16**이었다(부분 실행의 결과를 전체 회귀로 대체한 것이 아니라, 전체 수치를 재현해 이름을 얻은 것).
이전 보고에서 "실패 5·오류 2·skip 6"으로 적은 부분 실행은 `homez.db`가 이미 0바이트로 존재하던 다른 조건의 진단이었고 전체 회귀와 무관하다.

| # | 구분 | 테스트 | 사유(원문 출처) | 원인 분류 |
|---|---|---|---|---|
| 1~7 | skip(의도됨, 기존 7) | `test_windows_credential_store.WindowsCredentialStoreTestCase` 6건 + `test_restore_helper…test_full_subprocess_helper_cli_end_to_end` | "실제 Windows Credential Manager 통합 테스트 — 기본 전체 회귀에서 제외한다(IA-012)" | 실제 자격증명 저장소를 쓰는 옵트인 테스트 — 항상 skip이 정상 |
| 8~11 | skip(추가 9 중) | `test_audit_logs_migration.AuditLogsMigrationRealDbCopyTestCase` 4건 | "실제 homez.db가 없는 환경에서는 건너뜀" (`test_audit_logs_migration.py:213`) | **실제 설치환경 대조** — 실제 DB 사본이 필요 |
| 12 | skip | `test_bootstrap_production_db_guard…test_real_db_untouched_after_rejected_bootstrap_attempts` | 같은 사유 | 실제 설치환경 대조 |
| 13~14 | skip | `test_marketplace_fulfillment_migration`·`test_media_listing_package_migration` 의 `…RealDatabaseAppliedTestCase` | "실제 homez.db가 이 환경에 없습니다." | 실제 설치환경 대조 |
| 15~16 | skip | `test_migration_approval`·`test_migration_restricted_mode` 의 `RealHomezDbUntouchedTestCase` | 같은 사유 | 실제 설치환경 대조 |
| F1 | **실패** | `test_store_connection_migration…test_real_homez_db_has_store_connections_table_applied` | 실행 중 생긴 **0바이트** `homez.db`를 "실제 DB가 있다"로 보고 표 목록이 비어 실패 | **테스트 구성 결함**(§3) |
| F2 | **실패** | `test_homez_launcher…test_launcher_refuses_when_port_is_other_service` | 런처가 `venv\Scripts\python.exe` 부재로 포트 검사 전에 종료 | **테스트 구성 결함**(§3) |

skip 9건(8~16번)이 "실제 `homez.db` 없음" 때문이라는 것은 이제 추정이 아니라 **이름과 사유로 확인**됐다. 다만 이 9건과 F1은 실제 DB가 있는 환경에서 **실행된 결과가 아직 없다**(§9 미검증).
(이 표의 skip 사유 문자열은 원본 출력이 cp949로 출력돼 있어 소스의 문자열과 대조해 옮겼다.)

## 3. 0바이트 `homez.db` 생성 경로와 조치

- 생성 시각: 복제본 `w6f/homez.db` 2026-09-21 13:18:13 — 앱 로그상 `test_media_asset_worker` 직후 구간.
- 재현·추적: 패치 없는 기준선에서 `homez.db` 없이 모듈을 순서대로 실행하고 `sqlite3.connect`를 추적하자 **`test_migration_restricted_mode`가 처음 만든다**(앞선 모듈은 모두 미생성).
  호출 스택: `test_curl_style_bypass_write_request_blocked_with_423` → `app.main._enforce_migration_restricted_mode` → `_notify_server_admin_restricted_mode_blocked_write`
  → `SessionLocal()`(기본 DB) → `PlatformAlertService.dispatch_alert` → `get_active_recipients` → SQLAlchemy 연결 → **기본 경로에 0바이트 `homez.db` 생성**.
- 판정: **제품 결함이 아니라 테스트 구성 결함**이다. 미들웨어는 쓰기를 막는 순간에만 best-effort로 관리자 알림을 시도하도록 설계돼 있고(예외는 삼킨다), 그 테스트는 423 판정만 검증하면서 알림 경로를
  격리하지 않았다. 더 중요한 점은 **실제 설치 환경에서는 이 테스트가 실제 업무 DB에 접속한다**는 것이다(모듈 docstring의 "실제 homez.db는 어디에서도 사용하지 않는다"와 어긋남).
- 조치(테스트만 수정, 제품 코드 무변경):
  - `tests/test_migration_restricted_mode.py` — `RestrictedModeMiddlewareTestCase.setUp`에서 `_notify_server_admin_restricted_mode_blocked_write`를 격리하고, 오히려 **단언을 추가**했다
    (쓰기가 실제로 막혔을 때만 알림 시도 1회, 읽기·화이트리스트 요청에서는 0회). 변이 검증: 알림 호출 제거 → 검출, 허용 요청에서도 호출 → 2건 검출.
  - `tests/test_homez_launcher.py` — 포트 충돌 테스트가 스크립트를 **임시 프로젝트 루트**로 복사하고 `venv\Scripts\python.exe` 자리표시 파일만 둔다(이 시나리오는 Python 실행 전에 종료).
    단언(종료 코드 ≠ 0, "다른 프로그램" 문구)은 그대로이고, 런처 로그가 실제 `storage\logs`가 아니라 임시 루트에 남는다. `venv` 의존은 이 테스트가 유일하다
    (`test_homez_desktop.py`의 `venv` 언급은 스크립트 내용 문자열 검사일 뿐이다).
- 실제 설치환경 대조 테스트(§2의 8~16·F1)는 **의도적으로 건드리지 않았다.** 파일이 없으면 skip한다는 설계를 그대로 두고, 0바이트 파일이 더 이상 생기지 않게 원인만 제거했다
  (skip을 늘리거나 단언을 완화하지 않음). 이 테스트들은 "격리 테스트"가 아니라 "설치 환경 확인"이며, 실제 DB 읽기 전용 접근 승인 후 실제 저장소에서만 의미가 있다.
- 전체 회귀에서 다른 생성 경로가 없는지는 **회귀 중 가드(sqlite 연결 추적)**로 확인했다(§5).

## 4. Migration 줄바꿈·체크섬 보존

**발견(중요)**: 이전 문서에 적은 신규 Migration 체크섬 `aebbd3fd…9884`는 **CRLF 바이트**의 값이었다(검증 복제본에서 이 파일이 CRLF였다). 저장소에 채택되는 실제 바이트(LF)의
SHA-256은 **`1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a`**이다. 승인·적용 시 기준은 이 값이며, 이전 문서의 값은 정정한다.
내용은 같다(줄바꿈만 다름 — 49줄). 러너는 작업 트리 바이트로 체크섬을 저장·비교하므로(`MigrationRunner`), 적용은 반드시 LF 파일로 해야 한다.

- **기존 승인·적용 기준은 LF**: 저장소 index는 전 파일 LF(`git ls-files --eol`: Migration 74개 `i/lf w/lf`, CRLF `i/crlf` 0건), 실제 작업 트리도 LF이고, 테스트가 고정한 기준 체크섬 10건은
  전부 **LF 바이트의 SHA-256과 일치**하며 CRLF 변환값과는 일치하지 않는다. 실제 DB 이력의 체크섬은 승인 없이 읽지 않았으므로 직접 대조하지 못했다(위 근거는 저장소·테스트 기준).
- 적용된 Migration을 LF/CRLF로 **일괄 변환하지 않았고**, 기록된 체크섬·테스트 기대값을 바꾸지 않았다.
- `.gitattributes`(`migrations/*.sql text eol=lf`) 실제 diff — 새 체크아웃(`autocrlf=true`) 복제본에서 실측:
  변경 전 Migration 74/74가 CRLF로 체크아웃됨 → `.gitattributes` 추가 + `git add --renormalize .` 후 **변경 파일은 `.gitattributes` 1개뿐**(기존 파일 diff 0), 재체크아웃 후 Migration **0/74 CRLF**,
  다른 파일 유형(docs·app)은 종전대로 CRLF 체크아웃. 이 규칙은 경로 규칙이라 **기존 파일과 향후 신규 파일에 구분 없이** 같이 적용되며 별도 정책이 필요하지 않다.
  이미 CRLF로 체크아웃된 기존 복제본은 재체크아웃이 필요하다. **이 파일은 아직 커밋하지 않았다**(별도 승인 범위).
- 패치 파일: 새 체크아웃(`autocrlf=true`)에서 `.patch`가 **CRLF(5,263줄)로 바뀌어 `git apply`가 실패**함을 실측했다. `*.patch -text`를 추가하면 LF가 보존된다.
  그러나 이는 패치 파일 하나의 문제이며 **다른 환경 문제(실제 DB·venv 부재 등)를 해결하지 않는다.**
- 한계: 새로 만든 Migration이 편집기에서 CRLF로 저장되면 커밋 전 작업 트리 바이트와 커밋된 바이트의 체크섬이 다를 수 있다 — 적용은 항상 커밋된(LF) 파일로 한다.

## 5. 최종 고정 상태 검증 결과

**고정 트리 `L`** = HEAD `d09a536` + 옵션 연결 패치 25개 파일(LF) + 테스트 수정 2건, `core.autocrlf=false`로 체크아웃해 **실제 적용될 바이트 그대로**(Migration LF, SHA-256 `1a8c65c9…821a`).
`homez.db`·`venv` 없음(이전 조건과 동일). 실행 중 `L`은 수정하지 않았다. 코드 무변경 원칙 유지, 실행 명령은 상세 모드 `unittest discover -v -s tests`.

### 5-1. 실제 DB 접근에 대한 정정 (중요)

이전 전체 회귀들은 **실제 DB 파일 접근을 차단하지 않았다.** 일부 기존 테스트는 실제 DB 경로가 코드에 고정돼 있어 복제본에서 돌려도 실제 DB를 **읽기 전용**(`mode=ro`·`query_only`, 파일 전체 해시 계산)으로 연다.
대상은 두 곳 — 저장소 `homez.db`와 `%LOCALAPPDATA%\HOMEZ\data\homez.db`(설치본). 1차 실행에서 확인된 접근:

| 테스트 | 접근 대상 | 비고 |
|---|---|---|
| `test_listing_wizard_soft_delete_migration…RealDbMigrationApprovalDriftTestCase` | 설치본 DB 읽기 + 저장소 DB 시도 | 경로 두 곳 순회 |
| `test_media_asset_source_tracking_migration…RealDbMigrationApprovalDriftTestCase` | 설치본 DB 읽기 + 저장소 DB 시도 | 경로 두 곳 순회 |
| `test_media_asset_rights_evidence_migration…RealHomezDbApprovalDriftTestCase` | 저장소 DB 시도(가드가 막음) | 두 경로가 코드에 문자열로 고정 |
| `test_media_asset_public_hosting_migration…OperatingDbAlreadyAppliedInvariantTestCase` | 설치본 DB 읽기 | 존재하면 실행 |
| `test_v7_pre_live_migration_rehearsal…OperatingDbReadOnlyInvariantTestCase` | 설치본 DB 읽기 | 존재하면 실행 |
| `test_notification_delivery_migration` (13건) | 저장소 DB **파일 해시를 모듈 import(수집) 시점에** 계산 | 회귀 실행 때마다 읽음 |

쓰기는 없었다: 두 파일의 크기·수정시각은 실행 전후 동일(설치본 2,953,216B / 2026-08-29 21:02:41, 저장소 3,702,784B / 2026-09-17 19:25:02). 다만 **이전 보고의 "실제 `homez.db` 미접촉"은 회귀 부분에서 사실이 아니었다**(읽기 접근은 있었다).
1차 가드는 저장소 DB의 `sqlite3.connect`만 막고 설치본 경로·`open()`(해시 계산)은 막지 못했다 — 가드의 결함이며, 2차에서 감사 훅으로 보완했다. 그보다 앞선 `dc97d1f` 전체 회귀(채택 문서 §10)는 **외부 네트워크만** 차단했으므로 위 테스트들이 저장소·설치본 DB를 모두 읽기 전용으로 열었다(같은 결과: 쓰기 없음, 두 파일 메타데이터 불변).

### 5-2. 실행 결과 (두 번 — 무엇이 다른지 구분)

| 실행 | 가드 | 실행 | 성공 | 실패 | 오류 | skip | 종료 코드 | 소요 |
|---|---|---|---|---|---|---|---|---|
| 1차 | 외부 네트워크 + 저장소 DB `sqlite3.connect` 차단(설치본 경로·`open()`은 미차단) | 4,668 | 4,647 | 0 | 3 | 18 | 1 | 7,322초 |
| **2차(최종)** | 외부 네트워크 + **감사 훅으로 실제 DB 2곳·백업 폴더의 `open`·`sqlite3.connect` 전부 차단**·기록 | **4,656** | **4,632** | **0** | **6** | **18** | 1 | 7,494초 |

(성공 = 실행 − 실패 − 오류 − skip. 종료 코드 1은 아래 오류가 남아 있기 때문이며 통과가 아니다.)

- **2차 오류 6건은 전부 "실제 DB를 열려던 테스트가 가드에 막힌 것"**이다(패치·수정과 무관): 위 표의 5개 테스트 메서드(5건) + `test_notification_delivery_migration` 모듈이 import 시점 해시 계산을 막혀 **수집 실패**(1건 — 이 모듈의 13건은 2차에서 실행되지 않아 실행 건수가 4,668→4,656). 1차에서는 이 13건이 통과했다(파일 읽기가 허용된 상태).
  집계: 4,668 = 성공 4,632 + skip 18 + 오류 메서드 5 + 미실행 13(수집 실패 모듈).
- **2차** 가드 기록: 외부 네트워크 차단 시도 **0건**, 실제 DB로의 **성공한 연결 0건**(감사 훅 차단 5건 + 저장소 DB `sqlite3.connect` 차단 1건), 트리 `L`의 기본 위치에 `homez.db` 생성 **0건**(2회 모두 `L/homez.db` 없음).
- **skip 18건 = 의도된 7(Credential Manager 옵트인) + 실제 DB 없음 11.** 이전 16건 대비 늘어난 2건은 0바이트 `homez.db`가 더 이상 생기지 않아서다: 이전에는 빈 파일이 있어 `test_store_connection_migration`이 실패하고
  `test_migration_restricted_mode_db_path_contract…test_real_db_untouched`가 실행됐으나, 이제 둘 다 설계대로 "실제 DB 없음"으로 skip한다.
- **이전 실패 2건 해소**: 런처 테스트(12/12 모듈 통과), 실제 DB 대조 테스트(빈 파일 미생성 → skip). 실패 0건.
- 옵션 연결 관련: `test_supplier_option_link_service` 46, `test_supplier_option_identifier_paths` 23, `test_listing_wizard_option_links` 15, `test_coupang_option_identifiers_provider` 9,
  `test_option_link_console_ui` 6, `test_coupang_option_sku_contract` 6 — **전부 통과(105건)**. 체크섬 잠금 `test_permissions_timestamps_migration` 13/13, `test_v7_followup_migrations` 9/9 통과(LF 바이트).
  `test_migration_restricted_mode` 33 통과 + 1 skip, `test_i18n` 51, `test_migration_runner` 26 통과.

**해석의 한계**: 2차는 "실제 DB를 읽지 않는 테스트 전부 통과, 실제 DB에 의존하는 테스트 29건(오류 5 + 미실행 13 + skip 11)은 이 격리 환경에서 검증할 수 없음"을 보인 것이다.
후자는 패치가 적용된 실제 저장소·실제 DB 환경에서 읽기 전용으로 실행한 결과가 없다(§9). 이 29건을 통과로 세지 않았다.

### 5-3. 재사용한 검증과 하지 않은 검증

- **재사용**: 옵션 연결 E2E(데스크톱·모바일)와 Migration 리허설 A/B/C는 검증 복제본(CRLF 12개 파일 포함)에서 수행했다. 패치와 내용 차이 0건(줄바꿈만 다름)이고 JS·CSS·Python의 줄바꿈은 동작에 영향이 없으므로 재사용했다.
  단 Migration은 체크섬이 바이트에 의존하므로 **LF 바이트로 §6의 리허설(S0~S4)을 새로 수행**했다(체크섬 `1a8c65c9…821a` 이력 저장·일치 확인).
- **하지 않음**: LF 트리에서의 브라우저 E2E 재실행, 실제 저장소·실제 DB 환경의 회귀.

## 6. 코드·DB 통합 적용 계획 (6차 문서 §7로 대체됨 — 아래는 5차 시점의 기록)

**전제(문서 기준·직접 재관측 아님)**: 실제 DB에는 기존 `20260918_00_create_order_collection_test_budget_usage_schema.sql`(SHA-256 `93ca0724…b4b1`)이 **미적용**이다. 실제 DB를 열어 재관측하지
않았으므로 적용 직전에 읽기 전용 진단으로 다시 확인해야 한다.

**제한 모드의 실제 동작(코드 확인)**: `migrations/`에 있고 DB 이력에 없는 파일이 있으면 서버 시작 시(그리고 승인 직후) 진단이 제한 모드를 켠다 → 쓰기 요청이 423으로 차단된다.
승인 화면과 `bootstrap_environment`는 **승인 목록이 그 시점의 실제 대기 목록과 정확히 일치할 때만** 적용한다(부분 승인은 적용되지 않는다 — 리허설 S1 확인).
따라서 코드만 먼저 채택한 상태로 서버를 재시작하면 업무가 막히고, 기존 20260918과 신규 파일이 함께 대기 목록에 들어가 **두 파일을 같이 승인해야만** 풀린다.
서버는 지금 실행 중이 아니므로(측정 시점) 현재 업무에 영향은 없다. 실행 중이던 서버가 있다면 파일만 바꿔서는 제한 모드가 켜지지 않고 **다음 재시작에서** 켜진다.

**권장 순서(승인이 각각 필요한 4단계, 한 번의 정비 창에서 연속 수행, 서버 정지 상태로 시작)**

| 단계 | 내용 | 상태 확인·검증 | 실패 시 |
|---|---|---|---|
| P0 사전 | 서버·데스크톱·스케줄러 프로세스 없음 확인, 진행 중 발주·자동수집 없음 확인(서버를 끄기 전 화면에서), 실제 DB 크기·수정시각·SHA-256 기록 | 프로세스 목록, 파일 메타데이터 | 진행 중 작업이 있으면 중단 |
| P1 백업 | 서버 정지 후 `homez.db`를 `C:\Users\Daum pc\Homez-Backups\option_link_apply_<시각>\`에 복사, 원본·사본 SHA-256 일치 + 사본 `integrity_check`=ok | 해시 일치, ok | 불일치 시 중단(적용 금지) |
| P2 사본 리허설 | **실제 DB 사본**으로 진단(`pending` = 기대 목록과 일치)·단계별 적용·행수/해시 불변 확인(합성 리허설 S0~S4와 같은 검사) — 실제 DB 사본 접근 승인 필요 | pending 목록, 기존 표 불변 | 불일치 시 중단 |
| S-A | **20260918 단독 적용**(현재 저장소 코드, 신규 파일이 아직 `migrations/`에 없음) — 콘솔 승인 화면에서 목록=[20260918] 확인 → 재인증·nonce → 자동 백업(`storage/backups/homez_pre_bootstrap_migration_*.db`) → 적용 | `integrity_check`=ok, 제한 모드 해제 | 자동 백업/P1 복원 |
| S-B | 코드 채택: 패치 적용 커밋(서버 정지 상태) → 이 시점 `migrations/`에 신규 파일이 생겨 대기 목록=[20260921]만 | `git apply --check`, 대기 목록 | 커밋 되돌리기(R1) |
| S-C | 서버 시작(제한 모드) → 승인 화면 목록=[20260921] 정확 확인 → 재인증·nonce → 자동 백업 → 적용 | 이력 checksum=`1a8c65c9…821a`, 새 표 0행, `integrity_check`/`foreign_key_check`, 제한 모드 해제 | R2/R3 |
| S-D 사후 | 기존 표 행수 = P1 스냅샷, 콘솔 옵션 연결 패널 읽기 표시, 실제 저장소에서 읽기 전용 실제 DB 테스트(§9)·옵션 연결 집중 테스트 실행 | 위 항목 | 롤백 |

- **대안(비권장)**: 두 파일을 한 번에 승인해 함께 적용. 리허설 S2에서 순서대로(20260918 → 20260921) 적용됨을 확인했다. 단, 20260918은 별도 업무 결정(주문 수집 시험 예산 사용 기록 표)에 딸린 Migration이므로
  옵션 연결 승인과 **묶지 않는** 것을 권한다. 승인받지 않은 파일이 함께 적용되는 경로는 없다: 목록이 정확히 일치해야 적용되므로, 두 파일이 대기 중일 때 하나만 승인하면 아무것도 적용되지 않는다(S1, S1b).
- **롤백 조합(리허설로 확인)**
  - R1 코드만 되돌림(`git revert <채택 커밋>`, 서버 정지): **구 코드 + 신 DB**는 대기 0건·오류 없음(S4a) — 제한 모드가 켜지지 않고 구 기능이 그대로 동작. 신규 표는 남지만 사용되지 않는다.
  - R2 DB 복원(서버 정지): P1 사본 또는 자동 백업으로 `homez.db` 교체 → 내용이 적용 전 상태와 동일(S4b, 백업은 SQLite 백업 API로 만들어져 **바이트 해시는 다르고 내용이 같다**).
    복원 후 조합: **복원 DB + 신 코드 = 제한 모드(안전)**, **복원 DB + 구 코드 = 원상**. 복원 뒤에는 P1의 SHA-256 기록으로 파일을 대조한다.
  - R3 수동 롤백: 신규 표 `DROP` + `schema_migrations` 이력 1행 삭제(S4c) — 표 내용은 적용 전과 동일, 단 감사 로그·백업 이력에는 적용 사건 행이 **남는다**(append-only, 삭제하지 않음).
  - **원본 적용 전에 호환 조합으로 돌아갈 방법**은 P1(파일 사본) + R1(코드 되돌리기) + 자동 백업 3중이다. 셋 다 P1이 성공한 뒤에만 S-A로 진행한다.
- 실행하지 않는 것: 이 계획은 어떤 단계도 아직 실행하지 않았다(리허설은 합성 DB에서 수행했고 가드 기록에 실제 DB 경로로의 접근 시도는 0건이었다).

## 7. 실제 조회 계획 (승인 예산 — "버튼당 1회"·"상품당 약 2회" 표현은 예산으로 쓰지 않는다)

이번에도 실제 호출은 **하지 않았다.** 아래는 승인을 요청할 때 그대로 쓰는 범위다. 대상이 확정되지 않은 항목은 대상 미확정으로 남긴다.

**L1. 쿠팡 상품 상세 조회 (옵션번호 확인, 읽기 전용)**

| 항목 | 값 |
|---|---|
| 엔드포인트 | `GET https://api-gateway.coupang.com/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}` |
| 대상 판매 계정(연결) | **미확정** — 실제 DB의 쿠팡 스토어 연결 조회가 필요(승인 전 읽지 않음) |
| 대상 상품번호 | **미확정** — 실제 등록이 아직 없어 `sellerProductId`가 존재하지 않는다. 등록·시험상품 승인 후에만 생긴다 |
| 호출 목적 | ① 등록 결과의 옵션별 `vendorItemId`(승인 전 null 가능) 확인 → 기존 판매 옵션에 부착 ② 부착 전후 준비 완료 판정 확인 |
| **최대 횟수(총)** | **상품 1개당 2회**(승인 전 상태 확인 1 + 판매 승인 후 번호 발급 확인 1)를 **총 한도**로 한다. 초과가 필요하면 다시 승인받는다 |
| 숨은 호출 | 없음 — 코드 검색상 호출 지점은 `ListingWizardOptionLinkService.sync_identifiers` 1곳이고 요청 1회(`requests.Session` 기본값, 재시도 어댑터 없음, 응답은 페이지 없는 단건). 옵션 연결 화면의 목록 새로고침·재조회(`GET …/option-links`)는 **내부 DB만** 읽는다. 버튼은 확인창 + 중복 클릭 방지가 있다 |
| 한도 강제 | **코드가 호출 횟수를 세거나 막지 않는다(미구현).** 한도는 실행 절차로만 지킨다 — 실행자가 호출마다 횟수를 기록하고 한도에 닿으면 호출하지 않는다 |
| 승인 밖 | 상품 등록·수정·판매신청(=판매 승인 요청)·발주·결제는 이 승인에 **포함되지 않는다** |

**L2. 온채널 상품 조회 (공급 옵션 확인, 읽기 전용)** — 이전부터 승인 대기 중인 항목이며, 이번 변경이 늘리는 호출을 정확히 센 결과를 함께 적는다.

| 항목 | 값 |
|---|---|
| 엔드포인트 | `GET /openapi/seller/product/{code}` |
| 대상 연결 | 구매 채널 연결 id=4(이전 기록) — 적용 시점에 재확인 |
| 대상 상품코드 | **미확정** — 시험상품 미선정 |
| 호출 지점(코드 확인) | ① 화면 "옵션 조회"(`purchase_task/router.py`의 `lookup_product` 엔드포인트) 1회 ② **연결 저장 시 서버가 옵션 ID 소속을 재확인** 1회(`supplier_option_link_service.py`의 저장 경로, 이번 변경이 추가) ③ 발주 검토 화면 열 때마다 1회(`purchase_task/service.py`의 `build_order_submission_review`, 기존 동작, 캐시 없음). 승인 경로에는 이번 변경이 추가한 조회가 없다 |
| **최대 횟수(총)** | 상품 1개의 첫 연결 시험 = 조회 1 + 저장 1 + 검토 1 = **3회**(이전 승인 요청 "3회 이하"와 일치). 다음 주문 재사용 확인 = 검토 화면 1회(저장 없음). **총 4회 이내**를 한도로 요청한다 |
| 숨은 호출 | 재시도·페이지 추가는 이번 변경에 없음(공급처 어댑터의 기존 동작은 별도). 검토 화면 새로고침은 매번 조회이므로 **검토를 여는 횟수 = 조회 횟수**다 |
| 승인 밖 | 발주·결제·판매신청은 포함되지 않는다 |

**조회 승인과 등록·발주·결제 승인은 별개다.** L1은 등록이 존재해야 하므로 등록 승인이 먼저이며, 그 승인도 아직 없다.

## 8. 필요한 승인과 정확한 실행 범위

서로 **독립**이다(하나의 승인이 다른 항목을 승인하지 않는다). 아래 어느 것도 아직 승인·실행되지 않았다.

**대상 DB 확정 필요**: 앱은 실행 방식에 따라 다른 DB를 쓴다 — 개발 모드(`scripts/start_homez.ps1`, `venv` 런처)는 저장소 `C:\Users\Daum pc\Homez-OS\homez.db`
(3,702,784바이트, 2026-09-17 19:25), 패키징된 설치본은 `C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db`(2,953,216바이트, 2026-08-29 21:02, 2026-08-24 사고 기록 파일 3개가
같은 폴더에 있음)를 쓴다(`app/core/config.py`·`app/desktop/paths.py`). **어느 쪽이 업무 DB인지 사용자가 확정해야 한다.** 이 계획은 저장소 `homez.db`(최근 수정, 런처 경로)를 기본 대상으로 쓰며,
설치본 DB는 같은 절차를 **별도로** 밟아야 하고 그 DB의 미적용 Migration 상태는 확인하지 않았다. 패키징된 설치본에 이 코드를 넣으려면 재빌드가 필요하며 이번 범위 밖이다.

| # | 승인 항목 | 정확한 범위 | 상태 |
|---|---|---|---|
| 1 | 테스트 수정 2건 + 문서 커밋·push | 이번 지시의 "이번 작업 소유 변경만 승인 범위에서 커밋·push"에 해당해 **이번에 수행**(이 문서 커밋에 포함) | 수행 |
| 2 | 코드 채택(S-B) | 패치 25개 파일(LF), SHA-256 `57eeead2…8eff`를 서버 정지 상태의 정비 창에서 커밋. DB는 건드리지 않음. 채택 후 서버를 재시작하면 제한 모드이므로 S-C와 연속 수행 | 미승인·미수행 |
| 3 | `20260918_00` 단독 적용(S-A) | `20260918_00_create_order_collection_test_budget_usage_schema.sql`(SHA-256 `93ca0724…b4b1`) 1개, 자동 백업 후. 주문 수집 시험 예산 표 — **옵션 연결과 별개의 업무 결정** | 미승인·미수행 |
| 4 | `20260921_00` 적용(S-C) | `20260921_00_create_supplier_option_link_schema.sql`, **LF 바이트 SHA-256 `1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a`**(이전 문서의 `aebbd3fd…`는 CRLF 값이라 정정), 표 1개 추가, 기존 데이터 무변경 | 미승인·미수행 |
| 5 | 실제 DB 접근(읽기 전용/사본) | (a) P0·S-D의 파일 메타데이터·읽기 전용 진단(`mode=ro`) 각 1회, (b) P1 백업: 서버 정지 후 `homez.db` **복사 1회**와 사본 `integrity_check`, (c) P2 사본 리허설: **사본에서만** 쓰기, 원본은 열지 않음. 원본 쓰기는 항목 3·4의 적용 때만 | 미승인·미수행 |
| 6 | 실제 저장소 환경 읽기 전용 회귀 | 채택 후 실제 저장소에서 옵션 연결 집중 테스트 + 실제 DB를 읽는 테스트(§5·§9)를 **읽기 전용으로** 실행. 이 테스트들은 실제 DB 2곳(저장소·설치본 경로)을 읽는다 | 미승인·미수행 |
| 7 | 쿠팡 상품 상세 조회 L1 | §7 표(대상 미확정, 총 2회 한도) | 미승인·미수행, 등록 승인이 선행 |
| 8 | 온채널 상품 조회 L2 | §7 표(대상 미확정, 총 4회 한도) | 미승인·미수행 |
| 9 | `.gitattributes` 커밋 | 정확히 `migrations/*.sql text eol=lf` 한 줄(+ 선택: `*.patch -text`). 다른 파일 diff 없음(§4 실측). 기존 CRLF 체크아웃 복제본은 재체크아웃 필요 | 미승인·미수행(별도 세션 제안 `task_7aad9609`와 같은 내용) |
| 10 | 시험상품 선정·실제 등록·판매신청·시험 주문·결제, 쿠팡 시험 주문 정책 문의 발송 | 이전 라운드부터 계속 미승인 | 미승인·미수행 |

**결정이 필요한 사항(승인이 아니라 방향)**: 실제 DB를 읽는 테스트는 경로가 코드에 고정돼 있어 **복제본에서 돌려도 실제 DB를 읽는다**(§5). 기본 회귀에서 제외하는 방식(실제 자격증명
테스트가 이미 쓰는 `HOMEZ_RUN_REAL_*` 옵트인 패턴 재사용)으로 바꿀지 결정이 필요하다. 바꾸면 기본 회귀가 실제 DB를 절대 열지 않지만 skip 수가 늘고 실제 설치 확인은 명시 실행으로만 하게 된다 — 이번에는 바꾸지 않았다.

## 9. 실제로 검증하지 못한 것 (V7 완료·실운영 준비 완료가 아니다)

- 쿠팡 상세 조회의 **실제 응답 형태** — 공식 문서 계약 기준 구현(불일치 시 저장하지 않고 "준비 미완료").
- 온채널의 실제 동작·응답, 실제 등록·판매신청·주문·발주·결제 — 하지 않았다.
- 실제 DB에 대한 **적용·제한 모드 거동·사본 업그레이드 리허설** — 합성 DB(S0~S4)로만 확인.
- 실제 DB의 미적용 목록(20260918 미적용)은 **문서 기준**이며 재관측하지 않았다. 실제 DB 이력의 체크섬 대조도 하지 않았다.
- **실제 DB를 읽는 테스트**(§2의 skip 11건 + §5의 실제 DB 읽기 테스트)는 패치가 적용된 실제 저장소·실제 DB 환경에서 통과한 결과가 없다.
- 브라우저 E2E는 **LF 바이트 트리에서 다시 돌리지 않았다** — 내용은 줄바꿈 정규화 후 동일(차이 0건)하고 JS·CSS의 줄바꿈은 동작에 영향이 없다고 판단해 이전 결과를 재사용했다.
- 설치본(패키징) 경로의 DB·빌드는 확인하지 않았다.

