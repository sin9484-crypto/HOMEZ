# HOMEZ V6 Gate U-6(b) — 실 사용자 Permission 부여 계획서

작성일: 2026-08-10
상태: **계획 문서만 — 이 문서 자체는 실제 homez.db에 어떤 쓰기도
수행하지 않았다.** 아래 절차는 향후 별도의 명시적 사용자 승인을
받은 뒤에만 실행한다.

## 0. 이전 계획서와의 차이 — 왜 새로 쓰는가

`docs/HOMEZ_V6_GATE_R5_PERMISSION_SEED_PLAN.md`(2026-08-09)는 "8개
Permission *정의 자체*를 `permissions` 테이블에 아직 만들기 전" 상태를
전제로 작성됐다. 그 뒤 Gate S(2026-08-10)에서 실제로 그 8개 Permission
행을 real homez.db에 시딩했다(현재 `permissions` 38행, 그중 8개가
`listing_wizard.*`/`listing_wizard.economics_view`, 전부
dotted-lowercase). 즉 R-5 계획서의 "1. 추가할 8개 Permission"과
"2. 코드 충돌 확인"은 이미 실행 완료된 과거 사실이고, 이 문서가
계획하는 대상은 그 다음 단계 — **이미 존재하는 8개 Permission을
실제 역할(MANAGER/STAFF/VIEWER)의 `role_permissions`에 실제로
연결하는 것**이다. R-5 계획서의 4번 표(역할별 권장 기본 권한)는
여전히 유효한 설계 판단이므로 그대로 재사용한다.

실제 homez.db 현재 상태(2026-08-11 재확인, SHA-256
`570742f14bf58b2705818022c29f5beaf9362fa7f213a83d60b2bd8c60af2d14` —
2026-08-10 20:24:06 KST에 `bootstrap_environment()`가 격리 없이
실행되어 `schema_migrations`에 audit_logs Migration의 BACKFILLED
이력 1행이 추가된 것 외에는 변경 없음, 경위는
`docs/V6_EXECUTION_LEDGER.md`의 "Gate U 사고 정정" 절 참고 —
`role_permissions`/`permissions`/`roles`/`users`는 이 사고와 무관하게
전부 그대로다):
- `permissions` 38행 — 8개 `listing_wizard.*` 코드 전부 존재
  (`listing_wizard.view/create/edit/approve/submit/retry/export`,
  `listing_wizard.economics_view`).
- `roles` 5행: SUPER_ADMIN, ADMIN, MANAGER, STAFF, VIEWER.
- `role_permissions` 30행 — **이 8개 코드에 대한 행은 현재 0개**
  (실측 확인 완료, 2026-08-10). 즉 오늘 시점에 ADMIN/SUPER_ADMIN이
  아닌 어떤 역할도 이 Domain에 실제로 접근할 수 없다(Guard가
  ADMIN/SUPER_ADMIN만 무조건 통과시키므로).
- `users` 1행뿐(SUPER_ADMIN, id=1).
- `listing_wizards` 0행(기능 자체가 아직 실사용되지 않음).

## 1. 역할별 부여 대상(R-5 계획서 4번 표 재확인 후 그대로 채택)

| 역할 | 부여할 코드 |
|---|---|
| SUPER_ADMIN | 없음(코드 레벨에서 이미 전체 우회 — 불필요) |
| ADMIN | 없음(코드 레벨에서 이미 전체 우회 — 불필요) |
| MANAGER | `listing_wizard.view`, `listing_wizard.create`, `listing_wizard.edit`, `listing_wizard.approve`, `listing_wizard.submit`, `listing_wizard.retry`, `listing_wizard.export`, `listing_wizard.economics_view`(전부 8개) |
| STAFF | `listing_wizard.view`, `listing_wizard.create`, `listing_wizard.edit`(3개 — 승인·제출·재시도·Export·Economics 제외, 초안 작업까지만) |
| VIEWER | `listing_wizard.view`(1개만 — Economics/Export는 기본 제외, 2번 항목 참고) |

예상 신규 `role_permissions` 행 수: MANAGER 8 + STAFF 3 + VIEWER 1 =
**12행**(ADMIN/SUPER_ADMIN 0행).

## 2. Economics/Export 기본 비공개 정책 재확인

Gate U 전체가 이번에 강화한 계약(값이 아니라 키/컬럼 자체를 뺀다 —
Gate U-1/U-3)이 실제로 의미를 가지려면, 기본값 자체가 신중해야
한다:
- `listing_wizard.economics_view`는 VIEWER/STAFF에 기본 부여하지
  않는다(원가·마진은 회사별 민감도가 다르므로 필요한 회사만
  관리자가 개별 추가 부여).
- `listing_wizard.export`도 VIEWER에는 기본 부여하지 않는다(Export는
  CSV로 데이터를 외부에 반출하는 행위이므로 "조회 가능"보다 더 넓은
  권한으로 취급한다 — MANAGER만 기본 부여, STAFF/VIEWER는 필요 시
  개별 추가).

## 3. 기존 ADMIN/SUPER_ADMIN 동작 보존

`ListingWizardPermissionGuard`는 ADMIN/SUPER_ADMIN을 Permission 행
존재 여부와 무관하게 무조건 통과시킨다(Gate Q-2 확정, 이번 Gate
U-1~U-4에서도 변경 없이 재확인됨). 이번 부여는 ADMIN/SUPER_ADMIN에
대해 `role_permissions` 행을 추가하지 않는다(불필요한 행 증가 방지).

## 4. 적용 전 사전 확인(읽기 전용)

1. `permissions` 테이블에서 8개 코드가 여전히 존재하고 정확히
   dotted-lowercase인지 재확인한다(1번 항목 표와 일치).
2. `role_permissions`에서 이 8개 코드에 대한 기존 행이 여전히
   0개인지 재확인한다(이 문서 작성 이후 다른 프로세스가 먼저
   부여했을 가능성 배제 — 있다면 중복 INSERT를 피하기 위해 이미
   있는 (role_id, permission_id) 쌍은 건너뛴다).
3. `roles` 테이블의 MANAGER/STAFF/VIEWER `id`가 이 문서의 가정과
   같은지 재확인한다(스키마상 오늘은 3/4/5지만, 실제 적용 시점에
   다시 SELECT로 확인하고 하드코딩하지 않는다).

## 4b. recent-auth 요구

`role_permissions` 변경은 이미 임시 DB 테스트에서
`RolePermissionAdminTestCase.test_update_requires_recent_auth`로
확립된 것과 같은 민감도의 작업이다 — 실 DB에 대해서도 동일한
계약을 그대로 적용한다: 이 절차를 실행하는 운영자는 실행 직전
`app/core/recent_auth.py`(또는 동등한 recent-auth 검증 경로)로
최근 재인증된 상태여야 하며, 검증에 실패하면 즉시 중단하고
어떤 쓰기도 시작하지 않는다. 이 검증은 6번 Transaction을 열기
**전에** 완료돼야 한다(검증 실패 후 rollback으로 되돌리는 것이
아니라, 애초에 쓰기 자체를 시작하지 않는다).

## 5. 적용 전 백업

`homez-migration-safety` 스킬 원칙 그대로: SQLite Online Backup API로
`C:\Users\Daum pc\Homez-Backups\
homez_pre_gate_u6_permission_grant_<타임스탬프>.db` 생성 →
테이블 목록·DDL·행 수·`integrity_check` 일치 확인 → 통과 후에만
6번으로 진행.

## 6. 단일 Transaction + 멱등 + 감사 로그

`BEGIN IMMEDIATE` 한 번으로 다음을 전부 하나의 Transaction에 묶는다:
1. 1번 표에 따른 `role_permissions` INSERT — 각 INSERT 전에
   `SELECT`로 (role_id, permission_id) 쌍의 존재 여부를 먼저 확인하고,
   이미 존재하면 건너뛴다(재실행해도 행 수가 늘지 않는 멱등 스크립트).
2. `app/core/audit_db.py::write_audit_log()`로 이 부여 자체를
   감사 로그에 남긴다(action=`PERMISSION_GRANT`, entity=`RolePermission`,
   부여한 역할·코드 목록을 description에 요약).

중간에 어떤 단계라도 예외가 발생하면 전체를 ROLLBACK한다.

## 7. 적용 후 검증

1. `role_permissions` 총 행 수가 적용 전 30 → 적용 후 42(30+12)인지
   확인한다.
2. 새로 추가된 12행이 정확히 1번 표와 일치하는지(역할별 코드 목록
   재조회) 확인한다.
3. `users` 1행(SUPER_ADMIN)은 이 적용으로 전혀 영향받지 않아야
   한다(MANAGER/STAFF/VIEWER 역할에 속한 실사용자가 현재 0명이므로
   이 적용 자체는 "지금 당장 접근 가능한 사람"을 늘리지 않는다 —
   향후 그 역할로 사용자가 생성/변경될 때 비로소 효력이 생긴다).
4. `PRAGMA integrity_check`가 `ok`인지 재확인한다.

## 8. Rollback

역방향 Migration(DELETE 스크립트)은 만들지 않는다 — 문제가 발견되면
5번에서 만든 백업으로 전체 DB를 복원하는 것이 유일한 rollback
경로다(R-5 계획서와 동일 원칙 — 부분적으로 `role_permissions` 행만
골라 지우는 방식은 채택하지 않는다).

## 9. 실제 적용 전 필요한 사용자 승인 — 정확한 문구

실제 적용을 시작하기 전, 다음 내용을 사용자에게 그대로 제시하고
명시적 "예" 응답을 받아야 한다:

> "실제 `homez.db`에서 이미 시딩된 8개 Listing Wizard Permission을
> 실제 역할(MANAGER 8개·STAFF 3개·VIEWER 1개, 총 12개
> role_permissions 행)에 실제로 부여하려 합니다. Economics/Export는
> MANAGER에게만 기본 부여하고 STAFF/VIEWER는 제외합니다. 실행 전
> recent-auth 재인증을 확인하고, 자동 백업을 만들고, 실패 시 그
> 백업으로만 복구합니다. ADMIN/SUPER_ADMIN 동작은 변경되지
> 않습니다. 진행할까요?"

이 승인 없이는 어떤 실제 쓰기도 수행하지 않는다.
