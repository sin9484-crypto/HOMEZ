# HOMEZ V7 Live Pending 상태 — 실제 구현·검증 상태 기준 (Gate M-2)

- 작성일: 2026-08-21
- 작성 배경: "Pre-Live 최종 하드닝" 지시 Gate M-2 — 아래 8개 항목을
  코드에 그 이름의 문자열 상수가 있는지 없는지가 아니라, **실제로
  무엇이 구현돼 있고 무엇이 검증됐는지**를 기준으로 문서화한다.
  마커 문자열이 없다는 이유로 항목을 목록에서 빼지 않는다 —
  아래 표의 "코드 마커" 칸이 "없음"이어도 그 항목 자체는 실재하는
  Pending 상태다.

## 표기 규칙

- **코드 마커**: 저장소 안에 그 정확한 이름의 상수 문자열이 실제로
  존재하는지(존재 위치 포함). 없으면 "없음"이라고 그대로 적는다.
- **실제 구현 상태**: 그 기능이 지금 무엇을 실제로 하는지(FAKE만
  동작/미구현/부분 구현 등)를 코드를 직접 읽고 확인한 그대로 적는다.
- **실제 검증 상태**: 이번 세션 포함 지금까지 실제 그 경로로 무언가
  검증된 적이 있는지(FAKE/임시 DB 기준 검증과 실제 외부 시스템 검증을
  구분한다).

## 1. SUPPLIER_DISCOVERY_PROVIDER_PENDING

- **코드 마커**: 있음 — `app/domains/source/discovery_providers.py`
  (파일 헤더 주석 + `get_discovery_provider()`의 예외 메시지).
- **실제 구현 상태**: `FakeSupplierDiscoveryProvider`/
  `ManualSupplierDiscoveryProvider`/`CsvSupplierDiscoveryProvider`
  3개 전부 실제 동작(네트워크 호출 없음, 결정론적/사용자 입력
  기반). 실제 외부 공급처 카탈로그·검색 API를 호출하는 Provider는
  코드 자체가 없다(등록된 Provider 목록에 그런 항목이 없음).
- **실제 검증 상태**: FAKE 검색(시연용 배지·연결 차단), CSV 검색
  (사용자 입력 행만 사용)까지 이번 세션 격리 Browser E2E로 실제
  클릭 검증 완료. 실제 외부 공급처 검색은 한 번도 호출된 적 없음.

## 2. SUPPLIER_ORDER_LIVE_PENDING

- **코드 마커**: **지시문의 이름과 정확히 일치하지 않는다** — 실제
  코드상 이름은 `SUPPLIER_ORDER_PROVIDER_PENDING`
  (`app/domains/purchase/supplier_order_providers.py` 헤더 주석 +
  `get_supplier_order_provider()`의 예외 메시지). 같은 사실을
  가리키는 것으로 판단해 함께 기록한다.
- **실제 구현 상태**: `FakeSupplierOrderProvider`(FULL_ACCEPT/
  PARTIAL/RETRYABLE_FAILURE/RETRY_AFTER/NON_RETRYABLE_FAILURE 5개
  결정적 시나리오 포함, 2026-08-21 신규)/`ManualSupplierOrderProvider`
  (항상 예외 — 수동 처리 안내)/`CsvSupplierOrderProvider`(CSV 행
  누적, 실제 접수 아님) 전부 실제 동작. 실제 공급처 EDI/API로 발주를
  전송하는 Provider는 코드 자체가 없다.
- **실제 검증 상태**: FAKE의 5개 시나리오(전량접수/부분접수·거절/
  재시도가능실패/Retry-After 대기+자동재개/재시도불가능) 전부 이번
  세션 격리 Browser E2E로 실제 클릭+API 응답까지 검증 완료. 재시도
  서버측 이중 방어(NOT_RETRYABLE, RETRY_AFTER_NOT_ELAPSED)도 직접
  API 우회 시도로 검증. 실제 공급처 발주는 한 번도 전송된 적 없음.

## 3. IMAGE_SEARCH_PROVIDER_PENDING

- **코드 마커**: 있음 — `app/domains/media_asset/router.py`
  (`search_images()` 엔드포인트 docstring).
- **실제 구현 상태**: FAKE Provider만 실제 동작(네트워크 없음,
  시연용 결과, 저장하지 않음). 실제 인터넷 이미지 검색 API로
  연결된 Provider는 없음.
- **실제 검증 상태**: FAKE 검색 결과 표시·`permission_status`
  기반 selectable 필터링은 이전 라운드(미디어 자산 Gate)에서
  검증됨(이번 라운드 재검증 대상 아님).

## 4. IMAGE_PROCESSING_LIVE_PENDING

- **코드 마커**: **저장소 전체를 검색해도 이 정확한 이름의 마커는
  존재하지 않는다**(4차 보고에서 이미 확인, 이번에 재확인). 다만
  가리키는 실제 사실 자체는 존재한다 — 아래 참고.
- **실제 구현 상태**: 이미지 "검색"(3번)과 "생성/가공"은 서로 다른
  코드 경계다. 생성/가공 쪽은 `app/domains/media_asset/providers.py`
  의 `ImageGenerationProvider` 계열이 담당하며, 실제 동작하는 것은
  `FakeImageGenerationProvider`(결정론적 시연용, `FAKE_COST_PER_ITEM`
  고정값 사용)뿐이다. `DisabledImageGenerationProvider`가 명시적으로
  등록돼 있어 "FAKE 외 Provider 선택 시 항상 차단, 실제 연동은 별도
  승인 후"라는 계약이 코드로 강제돼 있다(에러 메시지: "이미지 생성
  Provider가 연결되지 않았습니다").
- **실제 검증 상태**: FAKE 생성 경로는 이전 라운드에서 검증됨. 실제
  유료 이미지 생성 API 연동은 코드가 아예 없으므로 검증 대상 자체가
  없음.

## 5. COUPANG_PRODUCT_SUBMISSION_PENDING

- **코드 마커**: **이 정확한 이름의 마커는 존재하지 않는다.** 같은
  사실은 `docs/V7_LIVE_GATE_PLAN.md`(Gate Z-2, 2026-08-12)의 "A. 외부
  마켓플레이스 실거래" 절과 마켓플레이스 등록 화면의
  `LIVE_E2E_PENDING_USER_CREDENTIAL` 배너로 문서화·UI 고지돼 있다.
- **실제 구현 상태**: `app/domains/coupang`의 상품 등록 파이프라인·
  adapter는 실제 쿠팡 API 스펙에 맞춰 구현돼 있지만, 실제 API 호출
  코드 경로 자체가 "제출" 마지막 단계에서 항상 dry-run/내부 검증만
  수행하도록 게이트돼 있다(요청 필수값·Schema·Safety·승인만 검사).
- **실제 검증 상태**: 실제 쿠팡 판매자 계정으로 실제 상품 등록
  요청을 보낸 적이 이번 세션을 포함해 지금까지 단 한 번도 없다
  (`docs/V7_LIVE_GATE_PLAN.md`에 명시적으로 기록된 사실, 이번 라운드
  재확인 — 이번 라운드도 실제 쿠팡 API를 호출하지 않았다).

## 6. PACKAGE_REBUILD_PENDING

- **코드 마커**: 프로젝트 상태 문서(`docs/HOMEZ_PROJECT_STATE.md`)에
  기록된 상태명.
- **실제 구현 상태**: `homez.spec`(PyInstaller 스펙)은 존재하지만
  저장소 안에 `dist/`, `installer-output/` 산출물이 전혀 없다 —
  즉 로컬에는 빌드된 실행 파일이 하나도 없다. `homez.spec` 파일
  자체의 마지막 수정 시각은 2026-08-16이고, 이번 세션(2026-08-19~21)
  의 공급처·발주 UI, 역할별 권한, FAKE 시나리오, 버전 단일화, 날짜
  파서 수정 등은 전부 그 이후 변경이라 아직 어떤 빌드에도 포함된
  적이 없다.
- **실제 검증 상태**: 아래 Gate M-3에서 처음으로 clean venv 기준
  재빌드를 수행한다(이 문서 작성 시점 기준 아직 수행 전).

## 7. MANAGER_PURCHASE_APPROVAL_PERMISSION_PENDING

- **코드 마커**: 없음(신규 Permission 코드를 만들지 않는다는 지시에
  따라 의도적으로 코드화하지 않음).
- **실제 구현 상태**: 계획만 존재 —
  `docs/adr/0004-supplier-sourcing-permission-matrix.md`에 신규
  `PURCHASE_APPROVE`(가칭) Permission이 필요하다는 결론과 반영
  범위가 문서화돼 있다. 현재 MANAGER는 발주 승인·전송·입고 등
  쓰기 액션에서 STAFF/VIEWER와 동일하게 조회만 가능하다(코드로
  강제됨, `spsIsAdmin()` 게이트).
- **실제 검증 상태**: "MANAGER가 조회는 되지만 쓰기는 ADMIN과 달리
  차단된다"는 현재 동작 자체는 이번 세션 단위 테스트·Browser E2E로
  검증됨(VIEWER와 동일 경로 공유 확인). 승인 Permission 부여 이후의
  동작은 애초에 코드가 없으므로 검증 대상이 아니다.

## 8. LIVE_MIGRATION_PENDING

- **코드 마커**: 없음(고정 상수가 아니라 `MigrationRunner.diagnose()`
  가 매번 실측하는 동적 상태).
- **실제 구현 상태**: 운영 DB(`%LOCALAPPDATA%\HOMEZ\data\homez.db`)
  pending 6건(2026-08-21 CP-1 시점 `MigrationRunner.diagnose()` 실측),
  개발 DB(`homez.db`) pending 7건(같은 6건 + 개발 DB에만 이미 있던
  `20260816_01_add_permissions_timestamps.sql`) — 실측 파일명은
  이 문서의 상위 보고(4/5차)와 CP-1 보고에 기록됨.
- **2026-08-21 CP-1 갱신**: `20260821_01_add_supplier_public_
  directory_columns.sql`(suppliers 운영 스키마 드리프트 해소용
  additive Migration) 신규 추가 — 대기 목록이 5건에서 6건으로
  늘었다. 임시 SQLite 복사본(레거시 8컬럼 fixture 포함) 기준 적용·
  기존 행 보존·재적용 안전성 전부 검증 완료
  (`tests/test_supplier_legacy_schema_upgrade.py`, 9/9 통과).
- **실제 검증 상태**: 임시 SQLite 복사본 기준 적용 가능함이 Gate
  M-4(기존 5건) + CP-1(신규 1건 추가)에서 검증됐다. **운영 DB
  원본에는 이번 라운드를 포함해 지금까지 한 번도 적용되지
  않았다** — 이 라운드도 적용하지 않는다(명시적 금지 사항).

## 요약 — 마커 이름 정정이 필요한 항목

| 지시문 표기 | 실제 코드 마커 |
|---|---|
| `SUPPLIER_ORDER_LIVE_PENDING` | `SUPPLIER_ORDER_PROVIDER_PENDING` |
| `IMAGE_PROCESSING_LIVE_PENDING` | 없음(사실은 `DisabledImageGenerationProvider`로 코드 강제) |
| `COUPANG_PRODUCT_SUBMISSION_PENDING` | 없음(사실은 `docs/V7_LIVE_GATE_PLAN.md` + `LIVE_E2E_PENDING_USER_CREDENTIAL` 배너로 문서화) |
| `LIVE_MIGRATION_PENDING` | 없음(고정 상수가 아니라 diagnose() 동적 결과) |

어느 항목도 "마커가 없다"는 이유로 이 문서에서 삭제하지 않았다 —
전부 위에 실제 상태와 함께 남겨뒀다.
