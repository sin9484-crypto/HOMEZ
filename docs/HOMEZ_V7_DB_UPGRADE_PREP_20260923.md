# HOMEZ V7 — 옵션 연결 채택 이후 업무 DB 적용 준비 (2026-09-23, 8차)

이 문서는 **실제 업무 DB에 어떤 Migration도 적용하지 않았다.** 이번에 한 것은
① 실제 DB 두 후보에 대한 읽기 전용 조사, ② 그 사본에서만 한 Migration 리허설,
③ 사본·가짜 Provider·격리 Credential Store로 채택본 화면 검증, ④ 이 문서(및
`docs/HOMEZ_PROJECT_STATE.md` 갱신)의 커밋·push뿐이다.
**"사본 업그레이드 검증 완료"·"원본 적용 완료"·"V7 실사용 완료"는 서로 다르다.**
이 문서는 첫 번째만 충족한다.

## 1. 기준선 확인

- `git fetch` 후 재확인: 로컬 HEAD = 원격 `origin/main` HEAD = `f776a3b`
  (2026-09-23 12:47:10 +0900), 작업 트리 클린(`git status` 무변경). 보고된
  HEAD를 그대로 가정하지 않고 다시 확인한 값이다.
- `f776a3b`는 7차 문서 커밋으로, `ec44aae`(회귀 실행 기준 커밋) 대비 변경
  파일은 `docs/HOMEZ_PROJECT_STATE.md`, `docs/HOMEZ_V7_OPTION_LINK_V2_ADOPTED_20260923.md`
  **문서 2개뿐**(코드·테스트·Migration 변경 0건, `git diff --stat`/`--name-only`로
  확인).
- 따라서 지시 §1의 조건(`이후 변경이 문서뿐이면 해당 회귀를 재사용`)을
  충족한다 — **동일 전체 회귀를 다시 돌리지 않았다.** 재사용 근거:

  | 항목 | 값 |
  |---|---|
  | 실행 커밋 | `ec44aae` (별도 클론 `L7`, `core.autocrlf=false`) |
  | 실행/성공/실패/오류/skip | 4,677 / 4,652 / 0 / 0 / 25 |
  | 종료 코드 | 0 |
  | 가드 `ATTEMPT_BLOCKED` | 0건 |
  | 소요 | 7,455.435초(≈2시간 4분) |

- 다른 작업자 변경 확인: 이번 라운드 동안 `origin/main`에 새 커밋 없음(fetch
  전후 원격 HEAD 동일, ahead/behind 0/0).
- 이번 라운드에서 내가 새로 실행 중이던 프로세스: 격리 검증 서버
  `adopted-optlink-verify`(포트 8830, PID는 `.claude/launch.json`이 아닌
  Browser pane의 `preview_start`가 관리) 1개뿐 — §5에서 종료·확인했다. 그 외
  실행 중이던 다른 작업자의 프로세스는 없었다(`tasklist`로 HOMEZ.exe·
  python.exe 확인 결과 이 문서 작성 시점 기준 실행 중인 프로세스 0개).

## 2. 실제 업무 환경 확인 — DB 후보 2곳과 미해결 정합성 경고

**결론부터: 이번 라운드에서도 "어느 쪽이 유일한 공식 업무 DB인가"를 단정하지
않는다.** 아래 근거로 상충하는 신호가 실제로 존재하며, 이 판단은 사용자
결정 사항이다(§7 승인안에서 이 결정을 명시적으로 요청한다).

### 2-1. 후보 A — 저장소 루트 `C:\Users\Daum pc\Homez-OS\homez.db`

- 개발 실행 스크립트(`start_homez.ps1`)와 `.env`의 상대경로 `DATABASE_URL`이
  가리키는 경로(`get_homez_db_path()` 확인, 6차 §7 그대로).
- `docs/HOMEZ_PROJECT_STATE.md`에서 최근 수십 개 라운드에 걸쳐 "실제
  homez.db"로 지칭되며 검증 대상이 되어 온 경로(문서 내 100건 이상 참조).
- 이번 확인: SHA-256 `f3aafca1bf1e68af154f1a17c362607ed05c0683744c14d6623436ca76762008`,
  3,702,784 bytes, `schema_migrations` 73행(적용), 대기 2건(§3).

### 2-2. 후보 B — 설치본 `%LOCALAPPDATA%\HOMEZ\data\homez.db`

- 2026-08-24 실제 사고 기록(메모리 `homez_db_recovery_incident_20260824`)에서
  **"실제 운영 DB"로 명시적으로 지칭**되고, 실제로 손상·포렌식 보존·8곳
  백업 검색·12단계 원자적 복원 절차를 거친 경로다. 이는 "그냥 설치
  경로"가 아니라 최소 한 번 실제 손상·복구 사고의 대상이었던 경로라는
  뜻이다.
- 이번 확인: SHA-256 `c28eaf57616876fa034bf1bffb4fa87ac2dd630bf4057686b8dcfcd09d322913`,
  2,953,216 bytes, `schema_migrations` 41행(적용), 대기 34건(§3).
- **행 수 비교로 드러난 추가 신호**: 후보 A는 `purchase_channel_connections=2`·
  `store_connections=1`·`marketplace_submissions=0`·`listing_wizards=1`인
  반면, 후보 B는 `store_connections=2`·`marketplace_submissions=10`·
  `listing_wizards=7`이다. 후보 B가 실사용 흔적(마켓 제출 10건, 리스팅
  위저드 7건)이 더 많다 — 이는 "후보 B가 실제 운영에 더 가깝다"는
  해석과, "후보 A가 최근 더 많은 Migration이 적용된 개발 스냅샷일 뿐"이라는
  해석 둘 다와 양립한다. 이 비교만으로 결론 내지 않았다.

### 2-3. 경로 가상화 위험 재확인(2026-08-30 진단 코드에 기록된 알려진 함정)

`app/domains/diagnostics/db_identity.py`의 코드 주석은 "Claude 자신의 실행
컨텍스트가 Windows 패키지 앱으로 등록되어 `%LOCALAPPDATA%` 접근이
`Packages\<PackageId>\LocalCache\...`로 가상화될 수 있다"는, 실제로 겪었던
함정을 기록해 두고 있다. 이번 세션에서 직접 재확인했다:

- 이 세션의 PowerShell 프로세스 자체는 패키지 아이덴티티가 없음
  (`Package.Current` 조회 시 `0x80073D54` — 비패키지 컨텍스트 확인).
- 그러나 `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\HOMEZ\data\homez.db`
  섀도 경로가 실제로 존재한다.
- 두 경로의 SHA-256을 직접 대조한 결과 **완전히 동일**
  (`c28eaf57…2913`) — 이번 조사에서는 가상화로 인한 실제 괴리가 없었다.
  다만 이는 "이번 한 번, 이 순간" 확인일 뿐, 향후 다른 실행 컨텍스트
  (특히 실제 HOMEZ.exe가 패키지 앱으로 실행될 경우)에서 같다는 보장은
  아니다 — 원본 적용 직전 재확인 항목에 포함한다(§7).

### 2-4. 실행 중 프로세스 확인

이 문서 작성 시점 기준 `tasklist`로 확인한 결과 `HOMEZ*`·`python*` 이름의
실행 중인 프로세스는 0개였다 — 즉 지금 두 DB 후보 중 어느 쪽도 실제
서버가 붙잡고 있지 않다(WAL/저널 잠금 없이 안전하게 읽기 전용 조사·백업
가능했던 이유). **역으로, 실제 사용자가 평소 이 시스템을 쓸 때 어느
DB로 어떤 실행 방법(개발 스크립트 vs 설치된 앱)을 쓰는지는 이번 세션만으로는
확정할 수 없다** — 이것이 §7에서 사용자 확인을 요청하는 핵심 이유다.

### 2-5. 읽기 전용 원칙 준수

두 후보 모두 이번 조사 내내 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
+ `PRAGMA query_only=ON`으로만 열었다(순수 `sqlite3`, 앱 임포트·ORM 초기화
없음 — 암묵적 쓰기 경로 배제). 조사 전후 두 파일의 SHA-256이 완전히
동일함을 재확인했다(§1 값과 이 문서 작성 직전 재해시 결과가 일치).

## 3. 미적용 Migration 전수 대조 (이 문서 작성 시점에 재실행해 확인, 과거 보고 재인용 아님)

`MigrationRunner.plan_pending()`과 `compute_checksum()`(저장소
`migrations/`의 실제 파일 바이트 기준)을 이용해 이 문서 작성 직전에 다시
계산했다. 두 후보 모두 **이미 적용된 Migration의 체크섬 불일치 0건**,
순서 역전 0건, 예상 밖 파일 0건.

### 3-1. 후보 A(저장소 루트) — 적용 73 / 대기 2

| 순서 | 파일 | SHA-256 |
|---|---|---|
| 1 | `20260918_00_create_order_collection_test_budget_usage_schema.sql` | `93ca072401873e920365f1974fd70116626544c81cfd9ff0fc880597a481b4b1` |
| 2 | `20260921_00_create_supplier_option_link_schema.sql` | `1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a` |

### 3-2. 후보 B(설치본) — 적용 41 / 대기 34

`20260830_00_create_refresh_token_schema.sql`부터
`20260921_00_create_supplier_option_link_schema.sql`까지 34개(위 2개 포함,
저장소의 75개 Migration 파일 중 41개가 이미 적용되어 있고 나머지 34개가
날짜순 그대로 대기). 전체 파일명·SHA-256 34행은 별도 스크래치 로그
(`pending_full_hashes.txt`, Git 비포함)에 원문 보존했고, 대표로 이번
채택과 직접 관련된 마지막 2개만 위 표와 동일하다. 34개 전량은 `apply_pending()`이
그대로 순서대로 적용할 대상이며, **이 중 하나만 골라 승인해도 나머지가
함께 적용되는 구조**이므로(§7에서 "34개 전체 단위로만 승인 가능"임을
명시한다) 단일 파일 선택 승인은 없다.

## 4. 사본 생성 및 검증

- 저장 위치: `C:\Users\Daum pc\Homez-Backups\db_investigation_20260923\`
  (Git 저장소 밖, `.gitignore` 대상 아님 — 애초에 저장소 트리 밖이라
  Git이 추적할 수 없는 경로). 이 폴더에는 원본 DB 사본과 리허설용
  WORKCOPY만 있으며, 개인정보·자격증명·주문 원문 로그를 별도로 발췌해
  담지 않았다(DB 파일 자체에는 합성 테스트 데이터 외 실제 고객 데이터가
  포함될 수 있으므로, **이 폴더 자체를 외부 서비스·AI 도구에 업로드하지
  않는다**는 원칙을 지켰다 — 이번 세션에서 실제로 업로드한 적 없음).
- 생성 방법: `sqlite3.Connection.backup()`(온라인 백업 API) — WAL/저널
  상태와 무관하게 논리적으로 일관된 사본을 만든다(단순 파일 복사가
  아님). 원본은 `mode=ro`로만 열어 복사했다.
- 무결성: 사본 4개 파일 모두 `PRAGMA integrity_check = ok`.
- 원본 불변 확인(이 문서 작성 시점 재검증, §2 수치와 일치):
  - 후보 A: `f3aafca1…62008` (3,702,784 bytes)
  - 후보 B: `c28eaf57…2913` (2,953,216 bytes)
  둘 다 사본을 뜬 시점의 해시와 지금 재확인한 해시가 **완전히 동일** —
  이번 조사 전체를 통틀어 원본에 어떤 쓰기도 없었다.
- 자격증명 격리: 두 사본 모두 `purchase_channel_connections`/`store_connections`
  테이블에 암호화된 자격증명 참조가 남아 있을 수 있으나, 사본을 여는
  어떤 스크립트도 `app.core.windows_credential_store.WindowsCredentialStore`
  (실제 Windows Credential Manager)를 초기화하지 않았다 — 순수 `sqlite3`
  또는 격리된 `InMemoryCredentialStore`만 사용해 구조적으로 실제
  Credential Store와 연결될 수 없게 했다.

## 5. 사본 업그레이드 리허설

### 5-1. 후보 A(2개 Migration)

`repo_root_dev_WORKCOPY.db`에 순서대로 적용:

- Dry-run(`apply_pending(dry_run=True)`) → 스냅샷 변화 없음 확인 후 실제 적용.
- 적용 후 `PRAGMA integrity_check = ok`, `foreign_key_check` 위반 행 0건.
- 사전/사후 전체 테이블(157개, `schema_migrations` 제외) 콘텐츠 해시 비교
  → **100% 동일**(최초 "existing_tables_unchanged: false"는 비교 스크립트가
  `schema_migrations`까지 포함해서 생긴 것으로, `schema_migrations`만 73→75행
  증가한 것이 원인임을 diff로 직접 확인 — 실제 데이터 변경 아님).
- 재적용 시도 → `apply_pending()`이 빈 리스트 반환(중복 적용 방지 확인),
  로우레벨 `executescript` 재실행은 `OperationalError: table
  supplier_option_links already exists`로 실패(스키마 자체도 중복 생성을
  막음, 이중 안전장치).
- **롤백 리허설**: 수동 `DROP TABLE`(2개) + `schema_migrations` 해당 행
  삭제 → 전체 테이블이 Migration 적용 이전 상태와 정확히 일치, `integrity_check`
  여전히 ok.
- **기능 검증**(ORM, `DATABASE_URL`을 WORKCOPY로 지정): `Company`/`User`/
  `PurchaseTask`/`StoreConnection`/`SupplierOptionLink`/`PurchaseOrderApproval`
  조회 전부 정상. 로그인 스키마 호환 확인(`user.password_hash` 필드가
  채워져 있는지 진위값만 확인, 값 자체는 읽지도 출력하지도 않음).
  합성 `SupplierOptionLink` 행을 만들어 저장→재조회 성공(최초 시도 시
  `confirmed_by`/`confirmed_at` NOT NULL 제약이 정확히 위반을 잡아냈다 —
  이는 내 테스트 스크립트의 미비였고 스키마 제약이 올바르게 작동함을
  방증한다).

### 5-2. 후보 B(34개 Migration)

`installed_localappdata_WORKCOPY.db`에 순서대로 34개 전부 적용:

- Dry-run 무변화 확인 후 실제 적용 → `applied_count=34`, 계획과 정확히 일치.
- `integrity_check = ok`, `foreign_key_check` 위반 0건.
- 신규 테이블 45개 추가(예: `refresh_tokens`, `purchase_channel_connections`,
  `purchase_order_approvals`, `supplier_option_links`, `refunds`,
  `payment_methods` 등) — 기존 테이블 손실 0개.
- 추적 대상 6개 테이블(`marketplace_submissions=10`, `listing_wizards=7`,
  `companies=1`, `users=1`, `store_connections=2`, `audit_logs=43`) 행 수
  적용 전후 완전히 동일.
- **콘텐츠 해시 비교로 발견된 유일한 변화 3개 테이블** —
  `backup_records`·`marketplace_submissions`·`users`. 행 수준이 아니라
  **컬럼 수준**으로 정밀 대조한 결과:
  - 기존 컬럼 값은 3개 테이블 전부 **행 단위로 100% 동일**(값 변경 0건).
  - 변화는 오직 새로 추가된 컬럼뿐이며, 그 값은 해당 Migration 파일이
    명시한 기본값과 정확히 일치:
    `users`: `failed_login_count=0`, `locked_until=NULL`,
    `last_failed_login_at=NULL` (`20260909_00_add_login_lockout_columns.sql`).
    `marketplace_submissions`: `provider_warning_summary=NULL`,
    `provider_response_code=NULL` (`20260830_02_add_marketplace_submission_provider_warning.sql`).
    `backup_records`: `is_encrypted=0` (`20260915_03_add_backup_records_is_encrypted.sql`,
    파일 주석에 "기존 백업은 실제로 평문이었다"는 근거가 명시됨).
  - 세 파일 모두 순수 `ALTER TABLE ... ADD COLUMN`이며 기존 컬럼·데이터를
    건드리는 구문이 없음을 원문으로 직접 확인했다.
  - 결론: **이 변화는 "의도된 데이터 변경"이지 결함이 아니다.** 지시
    §4가 요구한 구분("행 수뿐 아니라 기존 필드 보존과 의도된 변경 대조")을
    충족한다.
- 재적용 시 `apply_pending()` 빈 리스트 반환, 체크섬 불일치 0건.
- **롤백은 후보 A 방식과 동일한 절차로 별도 WORKCOPY에서 원칙 확인**(34개
  전부에 대한 개별 DROP 리허설은 이번엔 반복하지 않았다 — 각 Migration이
  동일한 `CREATE TABLE IF NOT EXISTS`/`ALTER TABLE ADD COLUMN` 패턴이고,
  대표 샘플(후보 A의 2개 + 후보 B의 컬럼 추가형 3개 검증)로 패턴 자체의
  안전성은 확인됐다고 판단했다 — 34개 전부의 개별 DROP 순서를 원본 적용
  직전에는 반드시 별도로 재리허설한다, §7).

## 6. 채택본 화면 검증

### 6-1. 검증 환경

- 격리 서버(`adopted-optlink-verify`, 포트 8830) — 합성 DB
  `adopted_e2e.db`(저장소 `migrations/` 75개를 신규 설치, 실제 두 후보
  DB와 무관), `HOMEZ_TEST_FAKE_COUPANG_PROVIDER=1`, `SCHEDULER_ENABLED=false`,
  실제 Windows Credential Manager 대신 `InMemoryCredentialStore`,
  온채널 어댑터는 로컬 JSON 카탈로그를 읽는 Fake로 교체, 비루프백 소켓
  연결 차단.
- 로그인은 서버 자체 라우트(`/__e2e/session-token`, 이 스크래치 서버에만
  존재, 저장소에는 없음)로 비밀번호 없이 세션 토큰 발급 — 실제 사용자
  비밀번호를 추측하거나 입력하지 않았다.
- 합성 회사·계정·판매채널·매입계정·주문 5건(AD-1~AD-5)을 실제 채택 코드
  (`PurchaseTaskService`, `SupplierOptionLinkService` 등)로 시딩했다.

### 6-2. 화면 경로 확인(이번에 새로 규명)

이전 시도에서 "매입·발주 관리" 목록 항목 클릭이 `ml.step1_title`("1단계 ·
상품 후보 선택")이라는 **다른 기존 기능**(마켓 리스팅 위저드 1단계)으로
잘못 이어진 것처럼 보였던 문제를, `console.js`를 코드로 직접 추적해
해소했다:

- `pt-link-panel`(옵션 연결 패널)은 `ptRenderDetail()`이 그리는
  `view-purchase-task-detail` 화면의 일부이며, `task.channel_connection_id`가
  있을 때만 렌더된다.
- 목록 행(`tr[data-id]`) 클릭은 정확히 `navigateTo("purchase-task-detail",
  {id})`를 호출한다 — 이 자체는 올바른 배선이다.
- 실제 원인은 배선 결함이 아니라 **좁은 브라우저 폭에서 카드형
  반응형 레이아웃(`responsive-cards`)의 특정 좌표를 클릭했을 때 클릭이
  의도한 `<tr>`에 도달하지 못한 것**이었다 — `tr.click()`을 DOM에서
  직접 호출하자 즉시 올바른 상세 화면으로 전환됨을 확인했다. 이전
  라운드의 "다른 화면으로 잘못 이동"은 실제로는 별도의 오조작(다른
  위치를 클릭)이었을 가능성이 높고, 코드 배선 자체에는 결함이 없음을
  이번에 코드 추적으로 확정했다.

### 6-3. 시나리오별 결과 (5건 전부 실행)

| 시나리오 | 조건 | 결과 |
|---|---|---|
| AD-1 정상 | SKU=AD-BLACK, vendorItemId=V-9001 | 화면에서 공급 상품코드 `CH-VERIFY-001` 조회 → `OPT-BLACK` 선택 → 저장 → 상태 `연결됨`/"판매자 SKU와 옵션번호가 모두 일치". **하드 리로드(로그아웃 아님, 브라우저 재구동) 후 API 재조회**로 영속 확인: `link_id=2`, `linked_by=BOTH`, `coupang_ids_confirmed=true` — 클라이언트 캐시가 아니라 실제 DB 재조회로 검증. |
| AD-2 SKU 없음(vendorItemId 대체) | 판매자 SKU 없음, vendorItemId=V-9001(AD-1과 동일 값으로 의도적으로 설계) | 별도 조작 없이 자동으로 `state=ACTIVE`, `linked_by=VENDOR_ITEM_ID`, `seller_sku_state=ABSENT_OR_SAME` — AD-1에서 저장한 연결을 vendorItemId만으로 정확히 재사용함을 확인. |
| AD-3 식별자 충돌 | 미리 저장해 둔 연결의 vendorItemId(`V-DIFFERENT-8888`)와 실제 주문 vendorItemId(`V-9002`)가 불일치 | `state=IDENTIFIER_CONFLICT`, 자동 선택 없이 차단, 화면에 `식별자 충돌` 배지와 안내 문구 표시. **연결 만들기/변경 버튼 자체가 렌더되지 않음**(임의 덮어쓰기 경로 자체가 없음) — 데스크톱·모바일(375px) 양쪽에서 DOM으로 확인. |
| AD-4 수량 환산 | 판매 수량 2 × 구성 2개입 옵션(`OPT-GRAY2`, `units_per_sale=2`) | 저장 후 API 재조회로 `expected_options=[{id:OPT-GRAY2, qty:4}]` 확인(2×2=4, 코드 계산이 아니라 서버 재계산 결과를 대조). |
| AD-5 공급 옵션 누락 | 대상 옵션이 어느 공급 상품코드 카탈로그에도 없음 | 존재하는 공급 상품코드(`CH-VERIFY-001`)로 조회해도 필요한 옵션이 목록에 없어 저장 버튼이 계속 비활성 상태로 유지됨(강제로 잘못된 옵션을 고르게 하지 않음). 존재하지 않는 상품코드로 조회 시 "조회된 옵션이 없습니다" 명확한 안내와 함께 저장 차단. |

### 6-4. 모바일 뷰포트

375×812 에뮬레이션에서 확인 — **단, 이 환경의 스크린샷 캡처 자체가
`devicePixelRatio=2`로 인해 페인트 영역의 1/4만 담아내는 도구 한계가
있음을 발견**(`elementFromPoint`로 스크린샷상 "빈 공간"처럼 보이는
좌표에 실제로는 정상 렌더링된 카드 요소가 존재함을 확인). 이 도구
한계 때문에 **스크린샷 이미지가 아니라 `getBoundingClientRect`/
`getComputedStyle`/`elementFromPoint`로 실제 레이아웃을 판정**했다
(skill `homez-console-e2e` §3 원칙 그대로 적용). 결과: 대시보드·옵션
연결 패널(`pt-link-panel`) 모두 뷰포트 폭(375px) 대비 정상적으로 전체
폭(317~351px, 좌우 여백 제외)으로 렌더링되며, 식별자 충돌 배지·오류
문구도 데스크톱과 동일하게 표시됨을 확인했다. **모바일 레이아웃 자체의
결함은 발견하지 못했다** — 단, 이 확인은 스크린샷 육안 대조가 아니라
DOM 측정 기반이라는 점을 한계로 명시한다.

### 6-5. 승인 이후 연결 변경 차단 — 코드 경로 확인만(실제 화면 재현 아님, 한계로 명시)

`order_approval_service.py`의 최종 승인 함수는 승인 시점에
`approval.options_snapshot_json`/`item_amount_snapshot` 등을 **그 시점 값으로
한 번만** 복사해 저장하고, 이후 이 값을 다시 쓰는 코드 경로는 저장소
전체에서 이 한 곳(승인 생성 시점)뿐임을 `grep`으로 확인했다(연결
테이블(`supplier_option_links`)을 이후 다시 읽어 승인 스냅샷을 갱신하는
코드 없음). `approval_block_reason()`은 승인 **시도 시점**에 연결 상태를
재검사해 충돌·미확인 상태면 승인 자체를 막는다.
**한계**: 실제 최종 승인까지 가려면 배송비 확인·마진율·한도·동시 작업
수 등 옵션 연결과 무관한 여러 정책 설정이 함께 필요해, 이번 라운드
범위(옵션 연결 화면 검증)를 넘어서는 별도 준비가 필요하다고 판단해
**라이브 화면으로 재현하지 않았다.** 이 항목은 정적 코드 경로 확인으로만
검증됐다는 것을 명시한다 — "화면에서 실제로 재현해 확인"과는 다른
수준의 확신이다.

### 6-6. 서버 종료 확인

작업 종료 후 `preview_stop`으로 `adopted-optlink-verify` 서버를
중지하고, `Get-NetTCPConnection -LocalPort 8830`으로 `LISTENING` 상태가
없음(`FinWait2`/`TimeWait`만 남아 자연 소멸 중)을 확인했다. `python.exe`
잔여 프로세스 0개. 이번 세션에서 열었던 브라우저 탭도 닫았다.

## 7. 결함·한계 종합

**제품 코드 결함으로 확정된 것: 0건.** 이번 라운드는 조사·사본 검증
범위이므로 결함이 나왔더라도 제품 코드를 고치거나 테스트 기대값을
낮추지 않기로 했는데, 실제로 그럴 필요가 있는 결함을 찾지 못했다.

**미해결 한계(원본 적용을 즉시 막지는 않지만, 승인 전 반드시 재확인해야 함)**:

1. **DB 정체성 미확정**(§2) — 후보 A/B 중 실제 사용자가 매일 쓰는
   "공식 업무 DB"가 어느 쪽인지 이 세션만으로는 결론 낼 수 없다. 이
   결정 없이는 §8 승인안의 "대상 DB 경로"를 확정할 수 없다.
2. 경로 가상화 위험(§2-3)은 이번엔 실제 괴리가 없었지만, 실제 HOMEZ.exe
   실행 컨텍스트에서 재확인된 적은 없다(현재 실행 중인 프로세스가
   없어 `db_identity.py` 진단을 라이브로 호출하지 못했다).
3. 후보 B의 34개 Migration 각각에 대한 **개별** 롤백 리허설은 반복하지
   않고 대표 샘플(후보 A 2개 + 후보 B 컬럼 추가형 3개)로 패턴 안전성만
   확인했다 — 원본 적용 직전에는 34개 전체에 대한 순차 리허설을 한 번
   더 하는 것을 권장한다(§8에 포함).
4. 승인-이후-연결변경-차단(§6-5)은 코드 경로 확인만 했고 라이브 화면
   재현은 하지 않았다.
5. 이전 세션이 보고했던 "화면이 다른 위저드로 잘못 이동한다"는 관찰은
   이번 재조사 결과 배선 결함이 아니라 좁은 폭에서의 클릭 오차였을
   가능성이 높다고 판단했지만(§6-2), 실제 사용자의 마우스/터치 조작
   환경에서 같은 오조작이 재현되지 않는다고 100% 보장하지는 않는다 —
   실사용 시 유사한 혼동이 보고되면 이 결론을 재검토해야 한다.
6. 해시 불변 확인은 "이 세션이 원본을 읽고 있던 매 순간"의 스냅샷
   대조일 뿐, 그 사이 다른 프로세스의 읽기·중간 상태까지 전부 없었다고
   증명하지는 않는다(지시 §10 원칙 그대로 인정한다).

## 8. 원본 적용 승인안 (승인 전에는 실행하지 않음)

**이 승인안은 "제출"이며 "승인"이 아니다.** 아래 항목에 대한 명시적
승인 없이는 어떤 항목도 실행하지 않는다. 목록·대상이 향후 바뀌면 이
승인을 확대 해석하지 않는다.

| 항목 | 내용 |
|---|---|
| **1. 대상 DB 확정 필요** | §2의 두 후보 중 어느 쪽에 적용할지(또는 둘 다, 순서는 어떻게 할지) **사용자 결정이 선행되어야 한다.** 이 결정 없이는 이하 항목의 "대상 경로"를 채울 수 없다 — 이 승인안은 결정이 내려진 뒤 그 경로에 그대로 적용 가능한 절차이지, 특정 경로를 이미 확정한 것이 아니다. |
| **2. 적용할 Migration 전체 목록** | 후보 A라면 §3-1의 2개(해시 포함). 후보 B라면 §3-2의 34개 전체(대표 마지막 2개 해시는 §3-1과 동일, 전체 34개 해시는 `pending_full_hashes.txt`에 보존— 필요 시 이 문서에 전체 인라인 첨부 가능). **34개는 단위로만 승인 가능**(개별 선택 불가, §3-2). |
| **3. 적용 직전 백업** | 서버 완전 정지 확인 → `sqlite3.Connection.backup()`으로 `Homez-Backups\<대상>_pre_optionlink_<타임스탬프>\`에 복사 → 원본·사본 SHA-256 일치 + 사본 `integrity_check=ok` 확인 후에만 다음 단계 진행. 백업 실패 시 원본 잠금 해제·강제 진행 금지(이번 조사에서 실제로 지킨 원칙 그대로). |
| **4. 예상 스키마·데이터 변경** | 후보 A: 신규 테이블 2개(`order_collection_test_budget_usages`, `supplier_option_links`), 기존 데이터 변경 0. 후보 B: 신규 테이블 45개, 기존 테이블 3개(`users`/`marketplace_submissions`/`backup_records`)에 컬럼 추가(§5-2 기본값 그대로), 그 외 기존 컬럼 값 변경 0건(이번 사본 리허설로 실측). |
| **5. 프로세스 정지·자동 실행 차단** | 대상이 후보 A(개발 실행)면 `start_homez.ps1`으로 띄운 서버·스케줄러를 먼저 종료. 대상이 후보 B(설치본)면 HOMEZ 데스크톱 앱을 완전히 종료(트레이 상주 포함 확인)하고 관련 스케줄 작업(Windows 작업 스케줄러 등록 여부 재확인)이 실행 중이 아님을 확인한 뒤 적용. 적용 전 `tasklist`로 관련 프로세스 0개 재확인. |
| **6. 사본 검증 이후 변경 여부 재확인 방법** | 적용 직전, §1/§2/§4에 기록한 원본 SHA-256·`schema_migrations` 행 수를 다시 계산해 이 문서의 값과 **정확히 일치**하는지 확인한다. 하나라도 다르면 이 승인안은 무효이며 새로 조사부터 다시 시작한다(문서 재사용 안 함). |
| **7. 적용 후 검증** | `integrity_check=ok`, `foreign_key_check` 위반 0, 적용된 파일 목록이 계획과 정확히 일치, 추적 대상 테이블 행 수 사전값과 동일, 신규 테이블 생성 확인. 서버 재기동 후 로그인·매입 작업 목록·옵션 연결 패널이 정상 로드되는지 실제 화면(스크래치 아님, 대상 DB를 가리키는 실제 실행)에서 1회 확인. |
| **8. 실패 시 복구** | 실패 즉시 서버 재기동 금지 → 항목 3의 백업으로 원자적 교체(임시 파일명 → 검증 → rename, 2026-08-24 복구 절차와 동일 패턴) → 복구 후 재검증(§7과 동일 체크리스트) → 원인 규명 전 재시도 금지. |
| **9. 구버전 앱 호환** | 이번 34개(또는 2개)는 전부 `CREATE TABLE`/`ALTER TABLE ADD COLUMN`(컬럼 삭제·타입 변경·이름 변경 없음) — 신규 컬럼을 모르는 구버전 코드가 기존 컬럼만 사용한다면 계속 정상 동작한다(신규 테이블/컬럼을 참조하는 신규 코드만 새 스키마를 요구). 단, 이 호환성 주장은 **코드 검토 기반**이며 구버전 바이너리로 실제 기동 테스트를 하지는 않았다 — 구버전 앱을 여전히 배포·운영 중이라면 별도 확인이 필요하다. |

**이번에도 승인하지 않는 것(§8과 별개로 재확인)**: 실 상거래 API 호출,
실 자격증명 조회, 상품등록·판매신청·주문·발주·결제·환불, `.gitattributes`
변경, 기존 Migration 바이트 변경.

## 9. 상품 시험 준비 계획 (실행하지 않음, 계획만)

승인 후 실행 순서(7차 문서 §6-2/§6-3과 동일 원칙 유지, 이번엔 대상
DB 결정을 선행 조건으로 명시):

1. **상품 선정** — 대상 상품·수량·가격 미확정(추측하지 않음).
2. **판매 정책·자료 이용권리 확인** — 이미지·상세페이지 등 자료의
   재사용 권리를 실제 등록 전에 확인(추측 불가, 승인된 상품 선정 후
   실행자가 직접 확인).
3. **공급처 필수조건 확인** — 온채널 판매신청 전제조건(공식 문서 기준)을
   실제 조회 없이 코드 계약만으로 재확인 가능한 부분(예: `externalVendorSku`
   빈 값·공백·150자 초과 차단, 커밋 `71f2b0e`)은 이번에도 재확인됨 —
   나머지(실제 응답 스키마 일치)는 첫 실제 호출에서만 확인 가능하다는
   한계를 그대로 유지한다(7차 문서 §6-2 결론과 동일).
4. **판매채널 등록** — 쿠팡 실제 등록(승인 필요). 등록 응답은
   `sellerProductId`만 반환(옵션번호는 승인 후 발급).
5. **외부 옵션 식별자 확인** — 승인된 상품 상세조회(쿠팡 API, 승인
   전/후 각 1회, 총 2회 한도)로 `vendorItemId` 확인.
6. **HOMEZ 매핑** — 콘솔에서 매입 계정 선택 → 공급 상품코드 조회(온채널
   API, 조회 1+저장 1+검토 1 = 첫 연결 3회, 다음 주문 재사용 시 검토
   1회 추가, 총 4회 이내) → 옵션 선택·구성 수량 입력 → 저장(이번
   라운드에서 화면 동작을 합성 데이터로 이미 검증함, §6).
7. **주문 수집** — 실제 쿠팡 주문이 들어오면 기존 자동 수집 경로가
   `channel_sku`/`vendorItemId`를 채운다(코드 무변경, 이번 라운드에서
   변경한 것 없음).
8. **승인 발주** — 저장된 연결이 있으면 자동 채움, 없거나 충돌이면
   차단·재확인 요구(§6-5 코드 경로 확인대로).

**외부 실행 계획(대상 미확정, 실행하지 않음)**은 7차 문서 §6-3 표와
정책이 그대로 유효하다(쿠팡 상세조회 상품당 최대 2회, 온채널 조회 첫
연결 3회+재사용 1회 이내 4회, 재시도 없음, DB 쓰기는 응답이 계약과
일치할 때만). 이번 라운드에서 이 한도·정책을 변경하지 않았다.

## 10. Git 상태

이번 라운드 소유 파일만 커밋한다: 이 문서(`docs/HOMEZ_V7_DB_UPGRADE_PREP_20260923.md`)와
`docs/HOMEZ_PROJECT_STATE.md` 갱신. DB 파일·백업·개인정보·자격증명·
원문 주문 로그는 저장소 밖(`C:\Users\Daum pc\Homez-Backups\...`)에만
두었고 Git에 넣지 않았다. `.claude/launch.json`에 추가한 스크래치 서버
설정(`adopted-optlink-verify`)은 `.gitignore:14`에 의해 애초에 Git이
추적하지 않는 파일이라 별도 조치가 필요 없다(확인 완료).

## 11. 최종 판정

| 구분 | 판정 |
|---|---|
| 실제 DB 읽기 전용 조사 | **완료**(후보 A·B 모두) |
| 일관된 사본 생성·검증 | **완료**(SHA-256+`integrity_check`, 원본 불변 재확인) |
| 미적용 Migration 전수 대조 | **완료**(체크섬 불일치 0, 순서 역전 0) |
| 사본 업그레이드 리허설 | **완료**(후보 A 2건, 후보 B 34건, 롤백 포함) |
| 채택본 화면 검증 | **완료**(5개 시나리오 + 모바일, 승인-후-차단은 코드 검토로만) |
| DB 정체성 확정 | **미해결 — 사용자 결정 필요**(§2, §8-1) |
| 원본 DB Migration 적용 | **아님**(승인안만 제출, §8) |
| 실제 등록·판매신청·발주·결제 | **아님** |
| V7 실사용 완료 | **아님** |

**"사본 업그레이드 검증 완료"는 이번 라운드에서 달성했다. "원본 적용
완료"·"V7 실사용 완료"는 아직 아니며, §8의 명시적 승인(특히 대상 DB
확정) 없이는 다음 단계로 진행하지 않는다.**
