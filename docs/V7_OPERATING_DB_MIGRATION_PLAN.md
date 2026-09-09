# V7 운영 DB 대기 Migration 적용 계획

- 작성일: 2026-08-21 (CA-7, CTO 지시)
- 상태: **계획만 확정, 운영 DB에는 아직 적용하지 않음**(사용자 명시
  승인 필요 — 정지 지점 1)

## 현재 상태 (읽기 전용 실측, 2026-08-21 기준)

- 운영 DB(`%LOCALAPPDATA%\HOMEZ\data\homez.db`) SHA-256:
  `97ef7196c691d1c8985e81fd70a93c021526eabda7dc9bc25b7aa72ef53299da`
- 개발 DB(`homez.db`) SHA-256:
  `5c22d204d7d290608dd4585cf95353823487b8b21549d82f9427b1756a7c5179`
- 운영 DB `integrity_check`: `ok`
- 운영 DB pending Migration: **7건**(개발 DB는 여기에
  `20260816_01_add_permissions_timestamps.sql` 1건이 더해져 8건 —
  개발 DB에만 있던 기존 pending 항목, 이번 라운드와 무관)

## 적용 순서(고정, `MigrationRunner`가 파일명 사전순으로 강제)

| # | 파일 | SHA-256 |
|---|---|---|
| 1 | `20260820_00_create_source_supplier_product_links_schema.sql` | (Gate M 라운드에서 기록됨) |
| 2 | `20260820_01_create_company_supplier_relations_schema.sql` | (Gate M 라운드에서 기록됨) |
| 3 | `20260820_02_add_purchase_supplier_order_submission_columns.sql` | (Gate M 라운드에서 기록됨) |
| 4 | `20260820_03_add_media_asset_rights_status.sql` | (Gate M 라운드에서 기록됨) |
| 5 | `20260821_00_add_audit_logs_created_at.sql` | (Gate M 라운드에서 기록됨) |
| 6 | `20260821_01_add_supplier_public_directory_columns.sql` | `eeff451eefda52cffc0c47a268dcd0c3927cbfc3ab541ead55040d6d45662b01` |
| 7 | `20260821_02_create_channel_policy_schema.sql` | `5577083ce9466badfb79ec802968bc79b7367be06b4579983bd680627c7d7c1c` |

6·7번이 이번(CP-1/CP-2/CA) 라운드에서 신규 추가됐다. 1~5번은 이전
Gate M 라운드에서 이미 리허설·검증 완료된 항목으로 이번에 다시
바꾸지 않았다(checksum 불변).

## 적용 절차 (실행 시 반드시 이 순서)

1. **백업**: `MigrationRunner`가 적용 직전 자동으로
   `%LOCALAPPDATA%\HOMEZ\backups\homez_pre_bootstrap_migration_
   <timestamp>.db`를 생성한다(수동 백업 불필요, 이미 코드에
   내장됨 — `app/database/bootstrap.py`).
2. **적용**: 공식 Desktop 앱의 "데이터베이스 업데이트 승인 필요"
   대화상자에서 SUPER_ADMIN이 현재 비밀번호로 재확인 후 "지금 적용"
   — `POST /desktop-setup/migration-status/approve`(loopback+Origin
   +Desktop 토큰+recent-auth+단발성 nonce 전부 요구, 우회 불가).
3. **검증**: 적용 직후 앱이 자동으로 `integrity_check`/
   `foreign_key_check`를 수행한다(`bootstrap_environment()` 내장).
   추가로 운영자가 직접 확인할 항목:
   - `suppliers` 테이블에 20개 신규 컬럼이 생겼는지, 기존 7개 레거시
     컬럼(company_id/business_number/ceo/phone/email/address/
     active)이 그대로 남아 있는지.
   - `channel_policy_rules`/`company_channel_policy_settings`/
     `channel_policy_evaluations` 3개 테이블이 생겼는지.
   - 기존 `suppliers` 행 수(현재 0건)가 그대로인지, `is_active`가
     전부 0(안전 기본값)으로 시작했는지.
4. **최초 카탈로그 시딩**: Migration 적용만으로는 `channel_policy_
   rules` 테이블이 비어 있다 — SUPER_ADMIN이 `POST /channel-policy/
   rules/seed-catalog`를 1회 호출해야 실제 정책 규칙이 채워진다.
   이 엔드포인트는 부트스트랩에서 자동 실행되지 않는다 — `app/
   domains/marketplace_listing/router.py::seed_capability_
   registry()`와 동일한 기존 컨벤션(정적 카탈로그는 admin_guard로
   명시적으로만 반영, 자동 실행 없음)을 그대로 따른다. **이 호출을
   빠뜨리면 모든 채널이 활성 규칙 0개 상태라 정책 검사가 사실상
   비활성**이므로 반드시 필요한 절차다.

## Rollback 계획

SQLite는 컬럼/테이블 DROP에 제약이 있어(3.35+ 필요, 이 프로젝트
최소 버전 미확정) 자동 rollback 스크립트를 만들지 않는다 — 대신
**백업 파일로 전체 복원**하는 것이 유일하고 가장 안전한 rollback이다:

1. 적용 직전 자동 생성된 백업(`homez_pre_bootstrap_migration_
   <timestamp>.db`) 확인.
2. 앱 완전 종료.
3. 손상된 `homez.db`를 다른 이름으로 옮기고, 백업 파일을
   `homez.db`로 복사.
4. 앱 재시작 — `schema_migrations` 이력이 백업 시점으로 되돌아가
   6·7번이 다시 pending으로 표시된다(정상 동작).

각 Migration 파일 자체에도 개별 DROP 문 rollback 계획이 주석으로
남아 있다(`migrations/20260821_01_...`, `migrations/20260821_02_
...` 파일 하단) — 다만 이 코드베이스 컨벤션상 그 주석은 참고용이며
자동 실행되지 않는다. 실제 rollback은 항상 백업 복원 방식을
우선한다.

## 참고

`submission_service.py::submit()`의 채널 정책 강제 게이트가 의존
하는 9개 테스트 파일 픽스처는 이미 갱신 완료(CA-1) — 운영 적용
자체와는 무관하다.

## 추가 pending 항목(2026-08-22, Gate RP-2 확인 — 아직 미적용)

이 문서 작성(2026-08-21) 이후 pending 목록에 2건이 더 늘었다(둘 다
읽기 전용 재확인 완료, 실제 적용은 하지 않음):

| # | 파일 | 비고 |
|---|---|---|
| 8 | `20260821_03_create_ai_proposed_actions_schema.sql` | 이 라운드(Gate RP-1/RP-2)와 무관한 이전 작업분 — 내용 검증은 이 문서 범위 밖 |
| 9 | `20260822_00_create_retail_purchase_schema.sql` | Gate RP-1/RP-2(구매 실행 인프라) — 4개 테이블(`retail_purchase_orders`/`payment_account_references`/`retail_purchase_policy_settings`/`retail_purchase_webhook_events`) |

9번(retail_purchase) 검증 내역:
- 임시 SQLite 파일에서 적용/재적용실패/rollback/중간실패rollback
  전부 통과(`tests/test_retail_purchase_migration.py`, 13건).
- **Fresh-install 리허설**: 개발 DB(`homez.db`)와 운영 DB(LOCALAPPDATA)
  각각의 **스크래치 사본**(실제 파일 아님)에 `MigrationRunner.
  apply_pending()`을 그대로 실행 — 개발 사본은 8번(20260816_01)부터
  10개, 운영 사본은 8번(20260821_03)부터 2개가 함께 적용되며,
  기존에 이미 기록된 checksum은 전부 불변, `integrity_check`=ok,
  `foreign_key_check`=0건 확인. 스크래치 사본은 검증 직후 삭제,
  실제 두 DB 파일의 SHA-256은 세션 시작·종료 완전 동일함을 재확인.
- 8번(20260821_03)의 정확한 컬럼 구성은 이 라운드에서 재검증하지
  않았다 — 9번을 적용하려면 8번도 함께(먼저) 적용돼야 한다는 순서
  제약만 사실로 확인했다(`MigrationRunner`가 파일명 사전순을 강제).

**실제 운영 DB 적용은 이번에도 하지 않는다** — 8·9번 모두 별도
사용자 승인 필요(정지 지점, 위 "적용 절차" 섹션과 동일한 승인
경로를 그대로 따른다).
