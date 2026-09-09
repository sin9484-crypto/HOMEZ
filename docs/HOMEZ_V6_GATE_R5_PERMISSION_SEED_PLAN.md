# HOMEZ V6 Gate R-5 — 실 Permission 시딩 계획서

작성일: 2026-08-09
상태: **계획 문서만 — 이 문서 자체는 실제 homez.db에 어떤 쓰기도
수행하지 않았다.** 아래 15개 항목은 향후 별도의 명시적 사용자 승인을
받은 뒤 실행할 절차를 미리 확정해 둔 것이다.

실제 homez.db 현재 상태(2026-08-09 확인, SHA-256
`9c8ac7f4974d849302604d933241dc239843aec85bd766cbd7fa5dcc295b2edd`):
- `permissions` 30행, 전부 `company_id IS NULL`(전역 Permission).
- `roles` 5행: SUPER_ADMIN, ADMIN, MANAGER, STAFF, VIEWER.
- `users` 1행뿐(SUPER_ADMIN, id=1) — 실사용자 데이터가 극히 적어
  시딩 자체의 파급 범위는 작지만, 절차 안전성은 동일하게 지킨다.
- `listing_wizards` 0행(기능 자체가 아직 실사용되지 않음).

## 1. 추가할 8개 Permission

| code | 설명(한국어) | 설명(English) |
|---|---|---|
| `LISTING_WIZARD_VIEW` | 상품등록 통합 마법사 목록·상세를 조회한다 | View listing wizard list and detail |
| `LISTING_WIZARD_CREATE` | 새 상품등록 마법사를 시작한다 | Start a new listing wizard |
| `LISTING_WIZARD_EDIT` | 마법사 단계(상품정보·미디어·채널·이행방식 등)를 수정한다 | Edit listing wizard step data |
| `LISTING_WIZARD_APPROVE` | 마법사 제출 전 최종 승인/취소를 수행한다 | Approve or revoke the listing wizard before submission |
| `LISTING_WIZARD_SUBMIT` | 승인된 마법사를 실제 채널에 제출한다 | Submit an approved listing wizard to channels |
| `LISTING_WIZARD_RETRY` | 실패한 채널만 재시도한다 | Retry failed channels only |
| `LISTING_WIZARD_EXPORT` | 등록 현황을 CSV로 내보낸다 | Export listing status as CSV |
| `LISTING_ECONOMICS_VIEW` | 원가·마진 등 수익성 정보를 조회한다 | View cost/margin economics data |

## 2. 기존 Permission 코드와의 충돌 여부

실제 DB의 기존 30개 코드(`BRAND_*`, `CATEGORY_*`, `COMPANY_*`,
`MARKETPLACE_*`, `ORDER_*`, `PRODUCT_*`, `STOCK_*`, `SUPPLIER_*`,
`USER_*`)를 전수 대조했다 — **8개 신규 코드 중 어느 것도 이름이
겹치지 않는다.** `code` 컬럼은 UNIQUE 제약이 없으므로(모델 확인
필요 — 아래 8번 항목의 사전 검증에 포함), 시딩 스크립트 자체가
INSERT 전에 `SELECT ... WHERE code = ?`로 존재 여부를 확인해 멱등성을
보장한다(스키마 제약에 의존하지 않는다).

## 3. ko-KR/en-US 설명

위 표의 "설명(한국어)"/"설명(English)" 컬럼이 그대로 `permissions.
description`과 콘솔 UI 카탈로그(`app/web/i18n/ko-KR.js`,
`en-US.js`)에 들어갈 문구다. 이미 존재하는 다른 Permission의 설명
문체(동사형 종결, 존댓말 없음)를 그대로 따른다.

## 4. 역할별 권장 기본 권한

| 역할 | 권장 부여 |
|---|---|
| SUPER_ADMIN | (코드 레벨에서 이미 전체 우회 — Permission 행 부여 불필요, 5번 항목 참고) |
| ADMIN | (코드 레벨에서 이미 전체 우회 — 동일) |
| MANAGER | `LISTING_WIZARD_VIEW`, `LISTING_WIZARD_CREATE`, `LISTING_WIZARD_EDIT`, `LISTING_WIZARD_APPROVE`, `LISTING_WIZARD_SUBMIT`, `LISTING_WIZARD_RETRY`, `LISTING_WIZARD_EXPORT`, `LISTING_ECONOMICS_VIEW`(전부) |
| STAFF | `LISTING_WIZARD_VIEW`, `LISTING_WIZARD_CREATE`, `LISTING_WIZARD_EDIT`(승인·제출·재시도·Export·Economics는 제외 — 초안 작업까지만) |
| VIEWER | `LISTING_WIZARD_VIEW`만(6번 항목 참고 — Economics는 기본 제외) |

이 매핑은 권장안이며, 실제 적용 시점에 사용자가 다르게 지정할 수
있다 — 이 문서가 그 값을 강제하지 않는다.

## 5. 기존 ADMIN/SUPER_ADMIN 동작 보존

`ListingWizardPermissionGuard`(app/domains/marketplace_listing/
listing_wizard_permission_guard.py)는 ADMIN/SUPER_ADMIN을 Permission
행 존재 여부와 무관하게 무조건 통과시키도록 이미 구현되어 있다(Gate
Q-2에서 확정, 이번 Gate R-2에서 재사용). 따라서 이번 시딩은
ADMIN/SUPER_ADMIN에 대해 `role_permissions` 행을 추가하지 않아도
되고, 추가하더라도 기존 동작에 변화가 없다 — **권장안은 추가하지
않는 것**(불필요한 행 증가 방지).

## 6. VIEWER 기본 최소 권한

VIEWER 기본값은 `LISTING_WIZARD_VIEW` 단 하나만 부여한다.
`LISTING_ECONOMICS_VIEW`는 기본 제외한다(7번 항목 참고) — 마진·원가는
회사별로 민감도가 다르므로, 필요한 회사만 관리자가 개별적으로 추가
부여하는 것을 원칙으로 한다.

## 7. Economics 기본 비공개 정책

`LISTING_ECONOMICS_VIEW`는 어떤 역할에도 기본으로 자동 부여하지
않는다(위 4번 표에서 MANAGER만 권장 부여, VIEWER/STAFF는 기본
제외). 이미 구현된 서버측 게이트(Gate R-2에서 검증: 이 Permission이
없으면 원가·마진 필드 자체가 응답에서 빠짐, UI에서 가리는 방식이
아님)가 이 정책의 실제 강제 지점이다 — 이 문서는 "기본값을 무엇으로
시딩할 것인가"만 결정한다.

## 8. 적용 전 백업

`homez-migration-safety` 스킬의 기존 원칙을 그대로 따른다:
1. SQLite Online Backup API로 `C:\Users\Daum pc\Homez-Backups\
   homez_pre_gate_r5_permission_seed_<타임스탬프>.db` 생성.
2. 원본과 백업의 SHA-256을 비교해 바이트 단위로 동일함을 확인.
3. `PRAGMA integrity_check`가 둘 다 `ok`인지 확인.
이 세 가지가 전부 통과한 뒤에만 4번 실제 쓰기로 진행한다.

## 9. 단일 Transaction

시딩 스크립트는 `BEGIN IMMEDIATE` 한 번으로 다음을 전부 하나의
Transaction에 묶는다: (1) 8개 Permission INSERT(이미 존재하는 코드는
건너뜀), (2) 4번 표에 따른 `role_permissions` INSERT(이미 존재하는
(role_id, permission_id) 쌍은 건너뜀). 중간에 어떤 단계라도 예외가
발생하면 전체를 ROLLBACK한다 — 부분 적용 상태를 남기지 않는다.

## 10. 멱등 Seed

스크립트는 재실행 가능하도록 작성한다: 각 INSERT 전에 반드시
`SELECT`로 존재 여부를 먼저 확인하고, 이미 존재하면 그 행을 건드리지
않고 건너뛴다(UPDATE도 하지 않는다 — description 문구가 나중에
바뀌어도 이 스크립트가 임의로 덮어쓰지 않는다. description 변경은
별도의, 더 좁은 범위의 스크립트로 처리한다).

## 11. 중복 적용 시 결과

같은 스크립트를 두 번 실행해도 두 번째 실행은 (1) 8개 Permission
전부 "이미 존재 — 건너뜀", (2) 모든 `role_permissions` 행 "이미
존재 — 건너뜀"으로 끝나며, `permissions`/`role_permissions` 테이블의
행 수는 첫 실행 이후로 전혀 늘어나지 않는다. 이 성질을 실제 적용
전에 임시 SQLite 파일 DB에서 2회 연속 실행으로 미리 검증한다(3번째
항목의 "8번 사전 검증"에 포함).

## 12. 적용 후 행 수 + Permission 테이블 결과

적용 성공 후 스크립트가 자체적으로 다음을 출력·기록한다:
- `permissions` 테이블 총 행 수(적용 전 30 → 적용 후 38 예상).
- 신규 8개 코드 각각의 `id`.
- `role_permissions`에 새로 추가된 행 수(4번 표 기준 MANAGER 8개 +
  STAFF 3개 + VIEWER 1개 = 12행 예상, ADMIN/SUPER_ADMIN 0행).
- 이 출력을 완료 보고에 그대로 포함한다(추정치가 아니라 실측치로
  교체).

## 13. 감사 로그

기존 `app/core/audit.py`/`audit_log.py` 패턴을 재사용해, 이 시딩을
수행한 사용자 id·시각·정확한 SQL 요약(각 INSERT 문의 정규화된
형태)을 감사 로그 테이블에 append-only로 남긴다 — 이 로그 자체도
같은 Transaction 안에서 커밋한다(9번 항목과 동일 경계).

## 14. Rollback은 백업 복원만

이 시딩에 대한 "역방향 Migration"(DELETE 스크립트)은 만들지 않는다
— 문제가 발견되면 8번에서 만든 백업 파일로 전체 DB를 복원하는 것이
유일한 rollback 경로다(기존 `homez-migration-safety` 스킬 원칙과
동일). 부분적으로 Permission 행만 골라 지우는 방식은 다른 곳에서
이미 부여된 `role_permissions` 참조와의 일관성을 보장하기 어려워
채택하지 않는다.

## 15. 실제 적용 전 필요한 사용자 승인 — 정확한 문구

실제 적용을 시작하기 전, 다음 내용을 사용자에게 그대로 제시하고
명시적 "예" 응답을 받아야 한다(요약이 아니라 이 문서의 1~14번
항목 전체를 링크하거나 첨부한 뒤):

> "실제 `homez.db`에 8개 Listing Wizard Permission 코드와
> 역할별 기본 권한(MANAGER 8개·STAFF 3개·VIEWER 1개, 총 12개
> role_permissions 행)을 추가하려 합니다. 적용 전 자동 백업을
> 만들고, 실패 시 그 백업으로만 복구합니다. ADMIN/SUPER_ADMIN
> 동작은 변경되지 않습니다. 진행할까요?"

이 승인 없이는 어떤 실제 쓰기도 수행하지 않는다 — Gate R 전체
지침의 "실제 DB Permission 시딩 필요"는 명시된 Stop 조건이었고,
이 문서 작성으로 그 조건을 해소한 것이 아니라 **다음 단계로 넘어갈
준비를 마쳤을 뿐**이다.
