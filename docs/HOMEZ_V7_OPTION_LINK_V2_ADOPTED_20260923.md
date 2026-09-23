# HOMEZ V7 — 옵션 연결 v2 채택·격리검증 완료, 다음 단계 계획 (2026-09-23, 7차)

이 문서는 **실제 업무 DB Migration 적용·실제 자격증명 사용·외부 상거래 API 호출·상품등록·발주·결제 승인이 아니다.** 이번에 승인된 것은 옵션 연결 v2 코드·테스트·신규 Migration 파일·문서의 저장소 반영과 그 커밋·push뿐이다.
**"옵션 연결 채택·격리검증 완료"와 "V7 실사용 완료"는 다르다** — 후자는 이 문서의 어떤 항목도 충족하지 않는다.

## 1. 기준선과 실행 중 작업 확인

- 이 지시 시작 시점 실제 확인: 로컬 HEAD = 원격 HEAD = `17ddcb7`(작업 트리 깨끗). "보고된 HEAD"를 그대로 가정하지 않고 `git fetch` 후 재확인했다.
- 패치(`docs/proposals/20260921_supplier_option_link.patch`, SHA-256 `57eeead2…8eff`)는 이 HEAD에 `git apply --check` 통과, 내용 불변.
- `docs/HOMEZ_DECISIONS.md`에서 이 채택과 상충하는 사용자 확정 결정 없음(V9 로드맵의 "옵션 연결"은 이 작업과 방향이 같다).
- 다른 작업자의 진행 중 변경·프로세스 확인: 이번 라운드 중 다른 작업자의 커밋은 없었다(직전 라운드까지는 `docs/research`·V8 계획 문서 커밋들이 간헐적으로 있었으나 코드·테스트·Migration과 무겹침이었다).
- **실행 작업 기록**(Python 프로세스뿐 아니라 셸 대기 루프·자식 프로세스까지 확인):

| 작업 | 환경(가드/PYTHONPATH) | 목적 | 결과 파일 | 종료 조건 |
|---|---|---|---|---|
| 초점 재검증 1회차 | `tests/support/regression_guard` + 실제 저장소 | 패치 105건+인접 432건이 이 저장소에서 통과하는지 | `opt_focus_out.txt`/`.log` | `Ran/OK\|FAILED` 줄 출력 |
| 초점 재검증 2회차(수정 반영) | 동일 | 위 재확인 | `opt_focus2_out.txt`/`.log` | 동일 |
| **고정 기준선 전체 회귀**(진행 중) | 클론 `L7`(커밋 `ec44aae`, `core.autocrlf=false`) + 가드 + 10분 무진행 감시(`run_watched.sh`) | 완료 조건(§5) 확인 | `full_regression_L7_final.txt`, `guard_L7_final.log` | `exit=` 줄 기록(정상 종료 또는 `WATCHDOG_KILLED`) |

이전 라운드에서 "완료 알림만 기다리다 11시간 무응답을 놓친" 사고(6차 §3-1(a))의 재발 방지로, 이번 전체 회귀는 무진행 10분 시 자동 종료·기록되는 감시로만 실행했다(별도 "기다리기만 하는" 폴링 루프를 걸지 않음).

## 2. 테스트 격리 결과 고정 (재확인, 재실행하지 않음)

6차 문서(`docs/HOMEZ_V7_TEST_ISOLATION_20260921.md`)의 3차(최종) 회귀가 근거다 — **이유 없이 다시 돌리지 않았다**:

| 항목 | 값 |
|---|---|
| 실행 커밋 | `5ee8127` |
| 명령 | `python -m unittest discover -v -s tests`, `PYTHONPATH=tests/support/regression_guard;<repo>` |
| 실행/실패/오류/skip | 4,677 / 0 / 0 / 25 |
| 종료 코드 | 0 |
| 가드 `ATTEMPT_BLOCKED` | **0건**(문서 §5) |

**"가드 차단 0건"과 "모든 자원 무접촉"은 동일하지 않다** — 6차 §3-1/§5가 실제로 증명한 바다(2차 확인 실행은 실패·오류가 0이었는데도 차단이 1건 있었다). 이번 7차에서도 같은 원칙을 적용해, 초점 재검증에서 실제로 차단 1건이 나온 것을 무시하지 않고 원인(§3)까지 추적했다.

**실제 설치환경 진단 18건과 일반 테스트 12건 이름 대조**(기존 "29건"과의 차이 재확인, 6차 §2 그대로):

- 진단 18건(옵트인 게이트, 이번에도 실행하지 않음): `test_audit_logs_migration` 4건, `test_bootstrap_production_db_guard` 1건, `test_homez_canonical_db_check_script` 1건, `test_listing_wizard_soft_delete_migration` 1건, `test_marketplace_fulfillment_migration` 1건, `test_media_asset_public_hosting_migration` 1건, `test_media_asset_rights_evidence_migration` 1건, `test_media_asset_source_tracking_migration` 1건, `test_media_listing_package_migration` 1건, `test_migration_approval` 1건, `test_migration_restricted_mode` 1건, `test_migration_restricted_mode_db_path_contract` 1건, `test_notification_delivery_migration` 1건, `test_store_connection_migration` 1건, `test_v7_pre_live_migration_rehearsal` 1건 = 18건.
- 일반 테스트 12건(`test_notification_delivery_migration`의 정적 검사 8·임시 DB 적용 4)은 실제 DB와 무관하며 매 회 정상 실행·통과한다.

가드·옵트인 게이트 정책은 이번에도 유지했고, 통과를 위해 약화하지 않았다(예: `readiness`·승인 스냅샷 관련 assertion을 이번 채택 과정에서 하나도 완화하지 않았다).

## 3. 옵션 연결 v2 패치 채택

- **커밋**: `8fcf54d`(패치 25개 파일 그대로, LF 체크섬 `1a8c65c9…821a`로 정정) → `ec44aae`(채택 검증 중 발견한 무관 결함 1건 수정). 둘 다 push 완료.
- **적용 검사**: `git apply --check`가 채택 직전 HEAD(`17ddcb7`)에서 통과, 실제 적용 후 `git status`가 정확히 패치의 25개 파일과 일치.
- **줄바꿈**: 새 Migration 파일은 `git apply` 직후 로컬 작업 트리에서 CRLF로 스미지됐으나(이 저장소 `core.autocrlf=true`), `git add` 시 clean 필터가 LF로 정규화해 스테이지된 블롭이 정확히 `1a8c65c9…821a`(기존 74개 Migration과 같은 관례)와 일치함을 확인했다 — 작업 트리 파일도 수동으로 LF로 맞췄다. **`.gitattributes`는 건드리지 않았다**(이번 범위 제외, 6차와 동일한 미승인 상태 유지). 이미 적용된 Migration 바이트는 손대지 않았다.
- **덮어쓰기 없음 확인**: 패치의 25개 파일과 6~7차에서 수정한 자격증명/가드 격리 파일(총 8개: `tests/support/...`, `test_homez_desktop.py`, `test_purchase_order_submission_service.py`, `test_live_gate4_fix_defects.py`, `test_full_migration_bootstrap_orm_smoke.py`, `test_purchase_task_channel_connection_assignment.py`, `test_migration_restricted_mode.py` 등) 사이에 **겹치는 파일이 0개**임을 채택 전 대조로 확인했다 — Credential 격리·승인(nonce/재인증)·중복 방지(멱등키)·한도·재시작 보호 코드를 이번 채택이 덮어쓰지 않았다.
- **스크래치 vs 채택 구분**: 이전 6개 라운드의 브라우저 E2E·변이 검증·Migration 리허설(A/B/C)은 전부 **스크래치 복제본**에서 수행됐고 이번에 다시 재현하지 않았다 — 내용이 패치와 100% 동일(6차 이전 문서에서 "정규화 후 차이 0건" 확인됨)하므로 재사용했다. **이번에 새로 한 것은 "실제 저장소에 채택된 뒤" 그 코드가 이 저장소의 다른 모든 테스트·가드와 함께 문제없이 동작하는지의 확인**(§4~§5)이다.
- **제한 모드 영향**: 신규 Migration 파일이 `migrations/`에 들어갔으므로, 이 저장소 코드로 서버를 실제로 띄우면 **제한 모드(쓰기 423)**가 된다. **이번에 운영 서버를 기동하지 않았다**(요구사항대로) — `app.main` 임포트 스모크(서버 미기동)로만 로딩 가능성을 확인했다.

## 4. 필수 연결 검증 (화면 → API → Service → 저장소 → Provider)

코드 추적 + 기존 105건 단위 테스트(신규 채택본에서 재실행, §5)로 확인했다. 새로 화면을 만들거나 추상화를 늘리지 않고 기존 상품 준비·매입 검토 화면 흐름을 그대로 재사용한다(요구사항대로).

| 흐름 | 화면 | API | Service | 저장소 | Provider |
|---|---|---|---|---|---|
| 판매 옵션 ↔ 비어있지 않은 고유 SKU | `console.js` `pt-link-panel`/`lw-optlink-panels` | `GET/PUT /purchase-tasks/{id}/supplier-option-link`, `GET/PUT /listing-wizards/{id}/submissions/{sid}/option-links` | `SupplierOptionLinkService.save_link_for_task`(빈 SKU·150자 초과 차단은 기존 커밋 `71f2b0e`대로 유지) | `supplier_option_links`(UNIQUE 회사+스토어+channel_sku, +vendorItemId) | — |
| 등록 결과 외부 식별자 보존 | 위저드 결과 표 | `POST .../option-links/sync-identifiers` | `ListingWizardOptionLinkService.sync_identifiers` → `attach_coupang_identifiers` | `supplier_option_links.coupang_vendor_item_id` | `CoupangLiveProductProvider.get_product_option_identifiers`(상세조회 GET 1회, 미발급/불일치는 저장 안 함) |
| 주문 SKU → HOMEZ SKU → 공급처 연결 | 발주 검토 화면 | 발주 검토 조립 경로(`PurchaseTaskService.build_order_submission_review`) | `SupplierOptionLinkService._resolve_link`(SKU/vendorItemId 결정표) | `OrderItem.channel_sku`, `UnresolvedOrderItem.vendor_item_id` → `supplier_option_links` 조회 | — |
| 판매 수량 × 구성 수량 환산 | 검토 입력칸(자동 채움) | 동일 | `units_per_sale` × 판매 수량 | `supplier_option_links.units_per_sale` | — |
| 저장 연결의 다음 주문 재사용 | 다음 발주 검토 시 자동 채움 | 저장 API 재호출 없이 조회만 | `resolve_for_task` | 동일 표 조회(재저장 아님) | — |
| 승인 스냅샷 고정 | 발주 승인 | 기존 승인 엔드포인트(무변경) | `order_approval_service.py`(연결 결과는 승인 시점 스냅샷에만 반영, 이후 연결 변경이 과거 승인에 영향 없음 — 기존 정책 유지) | 기존 승인 스냅샷 테이블 | — |

**실패 조건 확인**(단위 테스트로 커버, 표에 이름 표기):
- 미발급·누락·충돌 식별자, 알 수 없는 옵션 → `attach_coupang_identifiers`의 `ID_NOT_ISSUED`/`ID_NOT_RETURNED`/`MISSING_IN_RESPONSE`/`CONFLICT`/`AMBIGUOUS_RESPONSE` (test_listing_wizard_option_links)
- 이름·배열 순서 의존 금지 → `_resolve_link`가 오직 `channel_sku`/`vendor_item_id` 문자열 일치로만 동작(이름 필드를 조회 조건으로 쓰지 않음, test_supplier_option_identifier_paths)
- 회사·판매계정·공급계정 혼입 → UNIQUE 제약(company_id, store_connection_id, …) + 저장 시 매입 계정 소속 재확인(test_supplier_option_link_service `WrongLinkPreventionTestCase`)
- 승인 이후 연결·가격·수량 변경 → 승인 스냅샷은 연결 테이블을 참조하지 않고 승인 시점 값을 그대로 보존(order_approval_service 기존 계약 무변경)
- 부분 성공·중복 요청·재시작·결과불명 → `attach_coupang_identifiers`의 `ATTACHED`/`ALREADY_ATTACHED`/`NO_LINK` 구분, `save_link_for_task`의 버전 CAS(동시 수정 방지), 외부 성공 후 내부 저장 실패는 재등록하지 않고 조회로 복구(문서화된 정책, 이번에 재구현하지 않음)

## 5. 검증과 종료 조건

- 합성 DB·Fake Provider(`coupang_test_fakes.py`)·`InMemoryCredentialStore`로 집중 테스트(총 537건: 초점 재검증 432건 + 옵션 연결 105건 중복 포함 실행)를 실제 저장소에서 실행 — **전부 통과, 최종 가드 차단 0건**(1건 발견 후 수정·재확인).
- 데스크톱/모바일 화면 확인: 이번에는 **다시 브라우저를 띄우지 않았다** — 패치 내용이 5차까지 검증한 스크래치 버전과 100% 동일(정규화 후 차이 0건, 반복 검증 아님)하므로 그때의 E2E 결과(§ 6차 이전 문서)를 그대로 재사용한다. 화면 코드 자체가 이번 채택으로 바뀌지 않았다.
- **결함 발견·수정**(채택 검증 중 발견, 옵션 연결 로직 자체와는 무관):
  `test_migration_restricted_mode.py::ConcurrentApprovalTestCase::test_two_real_concurrent_threads_exactly_one_applies`가 승인 성공 후 호출되는 `refresh_restricted_mode_state()`의 별도 경로(`_real_migration_paths()`, `DATABASE_URL` 기반)를 격리하지 않아 **실제 `homez.db`를 읽기 전용으로 열려 했다**(가드가 차단, 테스트 자체는 통과라 결과만 보고는 드러나지 않음). `_real_migration_paths`도 같은 임시 경로로 패치해 수정(커밋 `ec44aae`). 결함 주입(`wraps=`원본)으로 가드가 재현함을 확인, 수정 후 모듈 34건·차단 0건.
- **기준선 고정 후 전체 회귀**: 커밋 `ec44aae`를 별도 클론(`L7`, `core.autocrlf=false`)에 체크아웃해 실행 중 수정하지 않았다. 10분 무진행 시 자동 종료·기록되는 감시를 적용했다(완료 알림만 기다리지 않음). **결과: 4,677건 실행 / 성공 4,652 / 실패 0 / 오류 0 / skip 25 / 종료 코드 0**, 7,455.435초(≈2시간 4분, 10:39:06~12:44:54).
skip 25건은 §2의 진단 18건 + 의도된 Credential Manager 7건과 **이름까지 완전히 일치**(수집 누락·추가 없음, 6차 최종 결과와 동일 집합).
**가드 `ATTEMPT_BLOCKED` 0건.** `CHILD_UNGUARDED`(비파이썬 자식) 27건 — `powershell` 10·`node.exe` 16·`node` 1, 전부 보호 경로 미포함.
트리 `L7`의 기본 위치에 `homez.db` 생성 0건. 실제 저장소 `homez.db`는 실행 전후 완전히 동일(3,702,784바이트, 2026-09-17 19:25:02). 잔여 파이썬 프로세스 0개.

**완료 조건 대조**: failures=0·errors=0·exit=0 — 충족. 모든 skip 이름·사유 설명 — 충족(§2). 수집 누락 없음 — 충족(4,677 = 이전 회귀와 동일 모집합 + 패치 신규 테스트, 전부 계정됨).
일반 테스트의 실제 자원 접근 시도 0건 — 충족(가드 차단 0건). 진단 18건은 이번에도 실행하지 않아 통과로 집계하지 않음 — 충족.

## 6. 다음 단계 계획 (실제 DB는 열지 않음 — 계획만)

### 6-1. Migration 확인·적용 계획

**대상 DB 재확인 필요**: 개발 모드(`start_homez.ps1`)는 저장소 `C:\Users\Daum pc\Homez-OS\homez.db`, 설치본은 `%LOCALAPPDATA%\HOMEZ\data\homez.db`. 이 계획은 저장소 DB를 기본 대상으로 한다(6차 §7 확인 근거 그대로 — `.env`의 `DATABASE_URL` 상대경로가 저장소 루트로 해석됨, 실행 설정 무변경).

| 항목 | 값 |
|---|---|
| 확인 방법(실제 DB 열지 않고 서술) | 서버 정지 상태에서 `GET /desktop-setup/migration-status`(=`get_migration_status()`, 읽기 전용 `mode=ro`+`query_only=ON`)로 대기 목록·체크섬을 조회. 관리자 화면에서 승인 전 항상 이 조회를 거친다 |
| 대기 목록(문서 기준, 재관측 전) | `20260918_00_create_order_collection_test_budget_usage_schema.sql`(SHA-256 `93ca0724…b4b1`, 주문 수집 시험 예산 — 옵션 연결과 무관한 별도 업무 결정) → `20260921_00_create_supplier_option_link_schema.sql`(SHA-256 `1a8c65c95d24ca36cfd4f59b5553fbd42bd33ce7491a09c1c3a5a4df72b5821a`) |
| 백업 | 서버 정지 → `homez.db`를 `Homez-Backups\option_link_apply_<시각>\`에 복사 → 원본·사본 SHA-256 일치 + 사본 `integrity_check`=ok |
| 사본 리허설 | **실제 DB 사본**(승인 필요, 원본은 열지 않음)에서 진단(대기=[20260918, 20260921]) → `20260918` 단독 적용 → 코드는 이미 채택돼 있으므로 재적용 없이 `20260921` 적용 → 기존 표 행수·해시 불변, 신규 표만 추가, 재적용 차단 확인 |
| 원본 적용 | 사본 리허설 통과 후에만, 콘솔 승인 화면에서 대기 목록과 승인 목록이 정확히 일치할 때만(다르면 자동 적용 안 됨 — 코드 계약) |
| 롤백 | 검증된 백업 + 대응 코드 버전(`ec44aae` 또는 그 이전) 조합. 운영 DB 수동 `DROP`은 표준 절차 아님 |

### 6-2. 실제 상품 흐름 순서(승인 후 실행할 순서 — 이번엔 실행하지 않음)

1. **실제 상품 등록**(쿠팡) — 승인 필요. `externalVendorSku` 포함, 등록 응답은 `sellerProductId`만 반환(옵션번호 없음이 정상).
2. **옵션 식별자 확인** — 승인된 상품 상세조회(§6-3 L1)로 옵션별 `vendorItemId` 확인. 승인 전(SAVED/IN_REVIEW)에는 null이 정상 — 재조회는 판매승인 후 1회 더.
3. **공급처 매핑** — 콘솔에서 매입 계정 선택 → 공급 상품코드 조회(§6-3 L2) → 조회 결과에서 옵션 선택 → 구성 수량 입력 → 저장(서버가 실조회로 소속 재확인).
4. **주문 수집** — 실제 쿠팡 주문이 들어오면 기존 자동 수집 경로가 `channel_sku`/`vendorItemId`를 채운다(코드 무변경).
5. **발주 검토** — 저장된 연결이 있으면 자동 채움, 없거나 충돌이면 차단·재확인 요구. 가격·재고·배송비·한도 확인은 기존 정책 그대로.

**온채널 판매신청 별도 시험은 생략하더라도, 1단계 실제 등록 전에 "발주 전제조건"(공식 문서 기준 계약)이 이미 충족돼 있는지는 반드시 확인한다** — 구체적으로: (a) `externalVendorSku` 등록 규칙(빈 값·공백·150자 초과 차단, 커밋 `71f2b0e`, 이미 코드에 있음 — 재확인만), (b) 상세조회 응답이 문서 계약(§6-3 L1의 응답 스키마)과 일치하는지는 **실제 호출 전에는 확인 불가** — 첫 호출 결과를 코드 계약과 대조하는 것이 이 확인의 실체이며, 이는 승인 항목 L1의 "승인 전 상태 확인 1회"가 그 역할을 겸한다.

### 6-3. 외부 실행 계획

| | L1: 쿠팡 상품 상세조회 | L2: 온채널 상품 조회 |
|---|---|---|
| 대상 | **미확정**(실제 등록 후 `sellerProductId` 발급 시 확정) | **미확정**(시험상품 선정 시 확정), 매입 계정은 이전 기록상 연결 id=4 — 실행 시 재확인 |
| 엔드포인트 | `GET https://api-gateway.coupang.com/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}` | `GET /openapi/seller/product/{code}` |
| 호출 목적 | ①승인 전 옵션번호 상태 확인 ②판매 승인 후 발급된 옵션번호 확인·부착 | ①옵션 조회(화면) ②연결 저장 시 소속 재확인(서버) ③발주 검토 조립(캐시 없음) |
| 최대 호출 수 | 등록 상품 1개당 **2회**(승인 전 1 + 승인 후 1) | 첫 연결 시험 **3회**(조회 1+저장 1+검토 1), 다음 주문 재사용 시 검토 화면 1회 추가 — 총 **4회 이내** |
| DB 쓰기 | 응답이 계약과 일치하고 미확인 옵션이 없을 때만 `supplier_option_links.coupang_vendor_item_id` 갱신. 불일치·부분·모름은 쓰지 않음 | 연결 저장 성공 시에만 `supplier_option_links` upsert |
| 재시도 | **없음**(코드에 재시도 로직 없음, 실패는 `UNVERIFIED`/`UNKNOWN`으로 남기고 사용자가 다시 눌러야 재조회) | 없음(기존 어댑터 동작, 이번 변경과 무관) |
| 중단 조건 | 한도(2회) 도달, 응답이 계약과 다름(`SELLER_PRODUCT_ID_MISMATCH`/`ITEMS_MISSING`), 승인 없음 | 한도(4회) 도달, 매입 계정 인증 실패, 승인 없음 |
| 코드의 횟수 강제 | **없음(미구현)** — 한도는 실행 절차로만 지킨다(실행자가 호출마다 기록) |

조회 승인과 상품등록·판매신청·발주·결제 승인은 별개다. 실제 결제 금액·배송지·상품 정보를는 이번 문서에서 추측하지 않았고(전부 "미확정"으로 표시), 과거 다른 실행에 대한 승인을 이번 계획에 확대 적용하지 않는다.

## 7. Git 상태

이번 작업 소유 파일만 커밋·push했다(패치 25개 + 발견한 무관 결함 수정 1개 + 이 문서). 실제 DB·자격증명·개인정보·원문 주문 로그는 Git에 넣지 않았다(그런 파일을 다루지 않았다).

| 커밋 | 내용 |
|---|---|
| `8fcf54d` | 옵션 연결 v2 채택(패치 25개 파일) |
| `ec44aae` | 채택 검증 중 발견한 승인 재계산 경로의 실제 DB 접근 결함 수정 |
| (이 문서) | 커밋 예정 |

이 문서까지 커밋한 뒤 `git fetch`+`git push`로 원격 반영을 재확인한다(최종 보고에 실제 값 기록).

## 8. 최종 판정

| 구분 | 판정 |
|---|---|
| 옵션 연결 v2 코드 채택 | **완료**(실제 저장소, push 완료) |
| 격리검증 | **완료**(105건+인접 432건, 최종 고정 전체 회귀 4,677건 모두 실패 0·오류 0·가드 차단 0) |
| 실제 DB Migration 적용 | **아님**(계획만, §6-1) |
| 실제 등록·판매신청·발주·결제 | **아님** |
| V7 실사용 완료 | **아님** — 실제 거래 전에는 선언하지 않는다 |

**남은 실제 실행 승인**(전부 독립, 이번에도 미승인·미실행):
1. `20260918`/`20260921` Migration의 실제 DB 적용(백업+사본 리허설 후)
2. 실제 DB 사본 접근(리허설용)
3. 쿠팡 상세조회 L1(대상 미확정, 총 2회 한도)
4. 온채널 조회 L2(대상 미확정, 총 4회 한도)
5. `.gitattributes` 줄바꿈 고정(별도, 이번 범위 제외)
6. 시험상품 선정·실제 등록·판매신청·시험 주문·결제
