# HOMEZ V7 — 알려진 한계 및 Live-Input 필요 목록

**작성**: V7 Gate 10(최종 릴리스 검증), 2026-08-16
**목적**: V7 Gate 0~10 전 과정에서 발견됐지만 이번 세션 Whitelist
범위 밖이거나 CTO의 정책 판단이 필요해 미해결로 남긴 항목, 그리고
실제 서명/배포/유료 API 연동처럼 이 자동화 세션이 원천적으로 수행할
수 없는 Live Gate 항목을 하나의 문서로 취합한다. 각 항목은 발견된
Gate, 현재 상태, 필요한 결정을 명시한다 — 이 세션이 임의로 결정하지
않는다(CLAUDE.md 원칙).

---

## A. 실제 homez.db Migration 미적용(6건) — 가장 중요, 릴리스 전 필수 결정

Gate 2/3/4/5/8/9가 각각 신설한 Migration 파일이 전부 실제 운영
`homez.db`에 **아직 적용되지 않았다**(V7 전 과정에서 실 DB는 읽기
전용으로만 접근됨, 쓰기 연결 0회). Gate 10 최종 재확인(공식
`MigrationRunner.diagnose()`, 읽기 전용) 결과도 동일:

| 파일 | 신설 Gate | 분류 |
|---|---|---|
| `20260815_00_gate2_tenant_isolation_hardening.sql` | Gate 2 | pending |
| `20260815_01_create_v7_gate3_inventory_schema.sql` | Gate 3 | pending |
| `20260815_02_create_v7_gate4_order_fulfillment_schema.sql` | Gate 4 | pending |
| `20260815_03_create_v7_gate5_pricing_settlement_schema.sql` | Gate 5 | pending |
| `20260815_04_create_v7_gate8_operations_schema.sql` | Gate 8 | pending |
| `20260816_00_create_v7_gate9_core_foundation_schema.sql` | Gate 9 | backfill_needed(대상 테이블이 실 DB에 이미 존재 — 안전 분류) |

**필요한 결정**: 실제 적용 시점과 방식(운영 중단 창, 사전 백업
확인 절차, 승인자)을 CTO가 결정해야 한다. 이 6건이 적용되기 전까지
Gate 3~9가 만든 모든 신규 기능(재고/주문/발주/배송/반품/가격/정산
대사/백업이력/알림센터/업데이트공지)은 **실제 운영 DB에서 전혀
동작하지 않는다**(테이블 자체가 없음) — 임시 DB로 검증한 코드는
전부 정상이지만, 실 배포는 이 Migration 적용이 선행돼야 한다.

## B. Permission 모델 Model↔DB 드리프트

Gate 9가 발견: `app/domains/permission/model.py`의 `Permission`
모델이 실제 운영 DB `permissions` 테이블에 없는 `created_at`/
`updated_at` 컬럼을 선언하고, 실제 테이블에 있는 `company_id`
컬럼은 선언하지 않는다. 실제 동작에 당장 영향은 없지만(SQLAlchemy가
없는 컬럼을 SELECT하려 하면 즉시 에러가 나야 하는데 지금까지 별
문제 없이 동작해왔다는 것은 이 모델이 실제로 자주 조회되지 않거나
다른 경로로 회피되고 있다는 뜻일 수 있음 — 근본 원인 미조사) 향후
`permissions` 테이블에 실제로 `created_at`/`updated_at`을 쓰는 코드가
추가되면 즉시 깨질 수 있는 잠재 결함이다.

**필요한 결정**: 모델을 실제 DB에 맞출지, 실제 DB를 모델에 맞춰
Migration할지, 혹은 `company_id` 자체를 Permission에 도입할지(회사별
커스텀 Permission을 지원하려는 의도였을 가능성) CTO 판단 필요.

## C. exe 헬스체크 타임아웃 원인 미확정 — Live Gate 필요

Gate 9가 실제 PyInstaller 빌드(`Homez.exe`, onedir, 161초 빌드 성공)를
클린 `%LOCALAPPDATA%`에서 실행한 결과, DB 부트스트랩(Migration 23건
적용)은 성공하지만 FastAPI 서버가 `HEALTH_TIMEOUT_SECONDS=25.0`초
안에 응답하지 않아 `HomezHealthCheckFailed`로 자체 종료된다. 같은
코드를 소스 모드로 직접 실행하면 1~2초 만에 정상 응답 — 이 세션의
loopback 네트워킹 자체 문제는 아님을 확인했으나, **PyInstaller 빌드
실행 파일에서만 재현되는 정확한 원인은 확정하지 못했다**(자동화
샌드박스의 신규 미서명 실행 파일 초기 구동 지연, 백신 실시간 검사
지연 등 가설만 있고 검증 못함).

**필요한 결정**: 실제 대화형 Windows PC(자동화 샌드박스가 아닌
환경)에서 CTO가 직접 `Homez.exe`를 실행해 재현 여부를 확인해야
한다. 재현되면 `app/desktop/server.py`의 `HEALTH_TIMEOUT_SECONDS`
상향 조정을 검토. 재현되지 않으면 이 세션 환경 특유의 문제로 결론
내리고 클린 설치 Gate를 통과 처리할 수 있다. **이 문서 작성 시점
기준 V7 전체에서 유일하게 "실제 여부조차 확인 못 한" 미해결
항목이다.**

## D. 설치 프로그램 — 완전 미착수(Live Gate)

Inno Setup 등 실제 설치 프로그램 도구가 이 저장소에 전혀 선정/도입돼
있지 않다. `docs/PACKAGING_CHECKLIST.md`(Gate Z-5)의 "설치 프로그램"
섹션 전부 미체크. 언인스톨 시 사용자 데이터(`%LOCALAPPDATA%\HOMEZ`
내 homez.db 등) 보존 정책도 "잠정 판단"일 뿐 확정되지 않았다.

**필요한 결정**: 설치 프로그램 도구 선정(Inno Setup/WiX/NSIS 등),
서명 인증서 확보, 언인스톨 데이터 보존 정책 확정 — 전부 CTO/사업
결정 + 실제 구매/계약이 필요한 Live Gate 항목.

## E. 실제 업데이트 배포/서명 — 완전 미착수(Live Gate)

자동 업데이트 다운로드/서명 검증/설치/롤백은 설계 문서
(`docs/adr/0002-update-manifest-signing-design.md`)만 존재하고 실제
구현이 없다. 코드 서명 인증서, 업데이트 배포 서버, 실제 네트워크
연동이 전부 선행돼야 하는 Live Gate 항목이다.

## F. 실제 마켓플레이스/결제/AI Provider 연동 — 전 구간 Fake만

V7 전체(Gate 3~9)가 재고/주문/발주/배송/반품/가격/정산/AI초안/이미지
생성까지 전부 **Fake Provider로만** 구현·검증됐다(지시문에 따라
의도적). 실제 쿠팡/네이버 등 채널 API, 실제 결제/PG, 실제 LLM/이미지
생성 API는 전혀 연동돼 있지 않다.

**필요한 결정**: 각 채널/Provider별 실제 연동은 별도 Live Gate로
진행해야 하며, 이 세션은 그 경계(어댑터 인터페이스, Fake/Real 분리
설계)만 준비해뒀다.

---

## G. 정책 판단 대기 항목(Gate별 CTO 확인 필요 사항 전체 취합)

### Gate 1
- `UserRole` enum(`app/core/authorization.py`)에 SELLER/CUSTOMER/GUEST
  죽은 코드가 남아있고 VIEWER가 누락돼 있다(Low 우선순위로 스킵 확정).
  단, 실제 인가 판정은 `app/core/permission_check.py`가 role.code
  문자열 + role_permissions 테이블 기반으로 별도 처리해 VIEWER도
  정상 동작함을 **Gate 10이 실제 HTTP 호출로 재확인**했다(아래 "Gate
  10 자체 발견" 참고) — enum 자체의 코드 위생 문제일 뿐 기능 결함은
  아님.

### Gate 2
- "관심 표시(interest marking)" 기능 — 이 저장소에 기존 기능이 없어
  승인/보류로 충분하다 판단해 신규 구현하지 않음. 별도 기능으로
  원하면 후속 작업 필요.
- `marketplace_listing`/`listing_package`의 `create_draft` 등 일부
  경로가 `ProductCandidate`를 가시성 검사 없이 직접 조회 — PRIVATE
  후보의 필드 데이터 유출은 없으나 낮은 심각도의 존재-오라클
  (candidate_id 존재 여부 확인 가능) 잔존. Whitelist 밖이라 미수정.

### Gate 4
- Supplier 도메인 자체는 여전히 company_id가 없다(전역 공급처) —
  여러 회사가 동일 HOMEZ 인스턴스를 쓰는 시나리오라면 재검토 필요.
- 부분출고/부분반품은 품목(OrderItem) 단위까지만 지원(품목 1개의
  수량 자체를 여러 송장/반품으로 쪼개는 것은 미지원, Inventory
  Reservation 모델의 구조적 한계).
- 교환(EXCHANGE)은 동일 SKU 재발송만 지원(다른 SKU로의 교환 미지원).

### Gate 5
- `record_actual_margin()`은 Settlement/Order 상태 전이에 자동
  연결돼 있지 않다 — 관리자가 명시적으로 호출해야 한다(자동 연결 시
  Gate 4급 cross-service 트랜잭션 결함 재발 위험이 있어 의도적으로
  분리).
- 실제 정산 채널수수료/결제수수료 분리 불가(channel_fee 하나로 합산,
  payment_fee=0) — 실제 마켓 API가 분리 제공하기 시작하면 교체 필요.
- shipping/packaging/ad_cost/tax/return_reserve는 저장소 전체에
  실측 소스가 없어 ACTUAL 마진에서도 항상 예상값을 대신 사용.
- XLSX 내보내기는 신규 의존성(openpyxl 등) 승인이 필요해 미구현
  (CSV는 완비, ADR 문서 `docs/adr/0003-accounting-software-
  integration-boundary.md`에 경계 명시).

### Gate 6
- Auto(LIMITED_AUTOMATION) 모드에서 위저드/초안/이미지Job까지는
  자동 실행하지만 최종 `approve()`(사람 승인 게이트)는 의도적으로
  자동화하지 않았다 — "정책 범위 안에서만 자동 실행"이 승인 자체까지
  포함하는지는 CTO 판단 필요. **Gate 10이 실제 HTTP로 재확인**:
  Auto 모드에서 위저드(DRAFT)+이미지Job(PENDING) 자동 생성까지는
  정상 동작, `approve()`는 호출되지 않음 — 설계 그대로 동작 확인.

### Gate 7
- "발주 생성" UI는 품목 개별 발주만 지원(여러 OUT_OF_STOCK 품목을
  한 발주로 묶는 멀티 선택 UI 미구현, 백엔드는 배열 지원).
- `POST /orders/collect`(채널 주문 수집) 수동 테스트 폼 미구현
  (실채널 웹훅 전제 설계라 의도적).
- Purchase 상세 화면 액션 성공 시 상단 목록 테이블이 다음 새로고침
  전까지 이전 상태로 남는 경미한 비일관성(다른 5개 신규 화면은 액션
  성공 시 목록까지 재조회).
- AI 파이프라인 실행 결과는 재실행 시 이전 결과를 덮어씀(이력 보관
  없음, `candidate_pipeline_service` 설계상 UI만으로는 못 바꿈).

### Gate 8
- `GET /backups/retention-check`는 조회 전용, 실제 자동 삭제 없음
  (스케줄러 도메인이 없어 범위 밖 — 필요 시 별도 도메인 설계 선행).
- shipment/return_order/settlement 도메인 상태변경은 전역
  `audit_logs`/`notification_center`에 연결하지 않음(각 도메인 자체
  append-only 이력은 존재).
- `app/domains/audit/router.py`(872 bytes, 실제로는 잘못 위치한
  로그인 라우터 죽은 코드, import 시 실제 ImportError) +
  `app/api/router.py`(그것을 참조하는, 저장소 어디서도 import되지
  않는 죽은 코드) — 69개 빈 스캐폴딩 인벤토리 범위 밖 신규 발견이라
  사전 승인 없이 삭제하지 않음. 삭제 여부 확인 필요.
- 실제 운영 DB 복원 실행 엔드포인트는 여전히 미노출
  (`require_app_closed_confirmation()` 안전장치는 준비됨) — 노출
  여부/시점 별도 결정 필요.

### Gate 9
- 위 "A. Migration 미적용", "B. Permission 드리프트", "C. exe
  헬스체크 타임아웃" 참고.

---

## H. Gate 10 자체 신규 발견(전부 낮은 심각도, Critical/High 아님)

1. **역할별 상태체크 UX**: VIEWER 등 admin_guard 게이트를 통과하지
   못하는 역할로 로그인하면 대시보드 상단 상태 배지가 "스키마 확인
   실패/모드 확인 실패/EStop 확인 실패"(붉은색)로 표시된다. 실제로는
   "권한이 없어 조회 못 함"이지 시스템 장애가 아닌데, 문구가 이를
   구분하지 못해 VIEWER/STAFF/MANAGER 사용자가 실제 장애로 오인할
   수 있다. 서버 응답(403)과 UI 표시가 일치하지 않는 것은 아니며
   (fail-safe 자체는 정상 동작), 문구 개선 정도의 낮은 심각도 UX
   후보다.
2. **Order/Purchase/Shipment/Return_order/Pricing/Settlement
   전용 tenant-isolation 테스트 파일 부재**: 기존
   `test_*_tenant_isolation.py` 계열 파일은 coupang/decision/
   settlement(V2.3)/funding(V2.3)/product_candidate/marketplace/
   store_connection만 다루고, Gate3~8이 신설한 6개 신규 도메인은
   서비스 레벨에서도 `test_inventory_core.py`의 일부 테스트만 격리를
   부분 커버했을 뿐 전용 파일이 없었다. Gate 10이 이번에 처음 실제
   HTTP 레벨(urllib, 실제 라우팅 통과)로 8개 도메인 전체를
   검증했고(회사 A/B 양방향, leak 0건) 문제는 발견되지 않았지만,
   **이 검증을 고정하는 영구 회귀 테스트 파일은 아직 없다**(Gate
   10 자체가 임시 스크립트로만 검증) — 후속 작업으로
   `tests/test_gate10_new_domains_tenant_isolation.py`류의 신규
   영구 테스트 추가를 권장한다(이번 세션 범위 밖, 코드 수정 없이
   검증만 했으므로 Whitelist 위반 아님).

---

## I. Live-Input 필요 목록 요약(재출시 승인 전 반드시 필요)

1. Migration 6건 실제 homez.db 적용 승인 및 적용 시점.
2. exe 헬스체크 타임아웃 실제 Windows PC 재현 확인.
3. 설치 프로그램 도구 선정 + 서명 인증서 확보.
4. 실제 채널/결제/AI Provider 연동(단계적으로, 필요한 채널부터).
5. Permission 모델 드리프트 수정 여부 결정.
6. 위 "G" 절의 정책 판단 대기 항목들(대부분 저위험, 기능 확장
   여부의 문제 — 릴리스 차단 사유는 아님).

이 문서는 V7 Gate 10 최종 릴리스 검증의 일부로 작성됐으며, 릴리스
승인 체크리스트는 `docs/HOMEZ_V7_RELEASE_PLAN.md`를 참고한다.
