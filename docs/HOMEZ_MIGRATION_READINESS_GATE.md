# HOMEZ 실제 homez.db Migration 적용 준비 Gate

(2026-07-30, "Desktop 로그인부터 V5 진입 Gate 직전까지" 작업 — Phase J)

**이 문서 작성 시점 기준으로 실제 `homez.db`에는 어떤 Migration도 이
세션에서 적용하지 않았다.** 아래 표는 `migrations/*.sql` 파일 목록과
실제 `homez.db`(mode=ro, query_only로 조회, 쓰기 없음)를 직접 대조해
확정한 것이며, 이전 세션 보고 내용을 그대로 복사하지 않고 이번에
다시 확인했다.

## 1. 실제 적용 여부 확정 결과

`homez.db`(303104 bytes, mtime 2026-07-27 22:38:38, 세션 시작·종료 시
동일 확인)에는 현재 **16개 테이블**이 존재한다: `audit_logs, brands,
categories, companies, funding_accounts, funding_holds,
funding_ledgers, marketplace_settlements, marketplaces, permissions,
products, role_permissions, roles, supplier_payments, suppliers,
users`.

| Migration 파일 | 생성 대상 | 실제 DB 적용 여부 | 근거 |
|---|---|---|---|
| `20260727_00_create_funding_settlement_schema.sql` | funding_accounts, funding_holds, funding_ledgers, marketplace_settlements, supplier_payments (5) | **이미 적용됨** | 5개 테이블 모두 실제 DB에 존재 |
| `20260727_add_settlement_ledger_unique_index.sql` | `uq_funding_ledger_settlement_type` 부분 유일 인덱스 | **이미 적용됨** | 해당 인덱스가 실제 DB `funding_ledgers`에 존재 |
| `20260728_00_create_v24_v3_schema.sql` | automation_mode_states, emergency_stops, execution_limits, execution_period_usages, execution_usages, product_candidates, product_candidate_evidences, product_candidate_decisions (8) | **미적용** | 8개 테이블 모두 실제 DB에 없음 |
| `20260729_00_create_coupang_integration_schema.sql` | coupang_marketplace_products, coupang_product_options, coupang_product_notices, coupang_profit_estimates, coupang_policy_sets, coupang_policy_rules, coupang_dry_run_attempts, coupang_integration_decisions (8) | **미적용** | 8개 테이블 모두 실제 DB에 없음 |
| `20260730_00_create_decision_ai_schema.sql` | decision_policies, decision_evaluations, decision_scores, decision_reviews, decision_audit_logs (5) | **미적용** | 5개 테이블 모두 실제 DB에 없음 |
| `20260730_01_create_auth_session_schema.sql`(이번 세션 신규) | auth_sessions (1) | **미적용** | 실제 DB에 없음(이번 세션에 새로 작성된 파일 — 임시 DB에서만 검증) |

**이전 세션 보고와의 차이**: V2.3(Funding/Settlement) 스키마가 이미
실 DB에 적용돼 있다는 사실은 이번에 처음 직접 재확인했다(파일
mtime이 2026-07-27로 homez.db의 마지막 수정 시각과 일치 — 이
Migration이 그 시점에 실제로 적용된 것으로 추정된다). 나머지 4개
Migration(V2.4/V3, Coupang, Decision AI, auth_sessions)은 여전히
미적용 상태다.

## 2. Migration별 상세

각 항목은 정적 검증(`tests/test_*_migration.py`)과 임시 SQLite DB
적용 테스트로 이미 확인되어 있다(전부 실제 실행, 아래 "3.신규·전체
테스트" 참고). 실제 `homez.db`에는 적용하지 않았다.

### 20260728_00_create_v24_v3_schema.sql (V2.4/V3, 8개 테이블)
- 선행 Migration: 없음(FK 없음, 독립적으로 적용 가능)
- Model↔DDL 테스트: `tests/test_v24_v3_schema_migration.py` (있음, 통과)
- 중간 실패 rollback 테스트: 있음, 통과
- 재적용 동작: 명시적 실패(`CREATE TABLE IF NOT EXISTS` 미사용)
- 운영 적용 위험: **낮음** — 신규 테이블만 생성, 기존 16개 테이블에
  전혀 손대지 않음. 사전 중복 데이터 검사 불필요(빈 테이블 생성뿐).
- 적용 권장 순서: **2순위** — Decision AI/Coupang의 핵심 입력
  (ProductCandidate)이 이 스키마에 의존하므로, 두 Domain을 실제로
  쓰려면 이 Migration이 먼저 적용돼야 실질적 가치가 생긴다.

### 20260729_00_create_coupang_integration_schema.sql (Coupang, 8개 테이블)
- 선행 Migration: 기능적으로 V2.4/V3(ProductCandidate.id를 논리
  참조) — DB 제약으로 강제되진 않지만 순서상 그 뒤에 적용 권장
- Model↔DDL 테스트: `tests/test_coupang_migration.py`(있음, 통과)
- 중간 실패 rollback 테스트: 있음, 통과
- 재적용 동작: 명시적 실패
- 운영 적용 위험: **낮음** — 신규 테이블만 생성. 단, `CoupangPolicySet`이
  시딩되지 않으면 적용 직후에도 모든 정책 검증이 fail-closed로
  차단된 상태로 시작한다(의도된 안전 동작).
- 적용 권장 순서: **3순위**

### 20260730_00_create_decision_ai_schema.sql (Decision AI, 5개 테이블)
- 선행 Migration: 기능적으로 V2.4/V3(candidate_id 논리 참조),
  automation_safety(Emergency Stop 게이트 — 스키마가 없어도
  fail-closed로 안전하게 동작하므로 DB 레벨 필수는 아님)
- Model↔DDL 테스트: `tests/test_decision_migration.py`(있음, 12개 통과)
- 중간 실패 rollback 테스트: 있음, 통과
- 재적용 동작: 명시적 실패
- 운영 적용 위험: **낮음** — 신규 테이블만 생성. `DecisionPolicy`가
  시딩되지 않으면 적용 직후에도 모든 평가가 fail-closed로 차단된
  상태로 시작한다(의도된 안전 동작 — `docs/
  HOMEZ_DECISION_POLICY_GOVERNANCE.md` 참고).
- 적용 권장 순서: **4순위**

### 20260730_01_create_auth_session_schema.sql (auth_sessions, 1개 테이블, 이번 세션 신규)
- 선행 Migration: 없음(users 테이블을 논리 참조만 함, FK 없음)
- Model↔DDL 테스트: `tests/test_auth_session_migration.py`(신규,
  11개 통과)
- 중간 실패 rollback 테스트: 있음, 통과
- 재적용 동작: 명시적 실패
- 운영 적용 위험: **가장 낮음** — 다른 어떤 Domain도 참조하지 않는
  독립 테이블 1개. 적용 전에는 `app/domains/session/service.py`가
  `SCHEMA_NOT_READY`로 안전하게 폴백해(기존 JWT 자연 만료 동작 유지)
  로그인 자체가 막히지 않는다.
- 적용 권장 순서: **1순위** — 위험이 가장 낮고, 적용 즉시 "로그아웃
  시 서버 세션 실제 폐기"라는 이번 작업의 핵심 요구 사항이 실제
  운영 DB에서도 동작하게 된다.

## 3. 신규·전체 테스트 실제 수치 (Migration 관련)

- `tests/test_auth_session_migration.py`: 11개, 전부 통과(이번 세션
  신규 작성).
- 기존 `tests/test_v23_schema_migration.py`(15개), `tests/
  test_v24_v3_schema_migration.py`(12개), `tests/
  test_coupang_migration.py`, `tests/test_decision_migration.py`(12개)
  — 전부 재실행해 통과 확인(수치는 본 응답의 16번 항목 참고).

## 4. 백업·복원 절차 (적용 승인 시 따를 절차 — 이번 단계에서는 실행하지 않음)

1. `homez.db`를 별도 경로로 복사(예: `storage/backups/homez_pre_migration_<timestamp>.db`) —
   Desktop Shell의 기존 `get_backups_dir()` 경로 계약을 그대로 사용.
2. 복사본에 대해 `PRAGMA integrity_check`로 무결성 확인.
3. 위 권장 순서(auth_sessions → V2.4/V3 → Coupang → Decision AI)대로
   하나씩 적용 — 매 파일 적용 직후 해당 Migration의 정적/적용 테스트를
   실 DB 스키마 스냅샷(읽기 전용 조회)으로 다시 확인.
4. 문제가 있으면 각 Migration 파일 하단 주석의 rollback SQL을
   역순으로 실행.
5. 전체 적용 후 `python -m unittest discover -s tests` 재실행으로
   회귀 확인.

## 5. 실제 적용 승인 요청 (아직 하지 않음)

이번 세션에서는 위 어떤 Migration도 실제 `homez.db`에 적용하지
않았다. 적용하려면 다음 문구로 별도 승인을 받아야 한다:

> "백업·Preflight·적용 순서와 예상 변경 객체를 검증했습니다. 실제
> C:\Users\Daum pc\Homez-OS\homez.db에 Migration을 적용해도 됩니까?"

승인 전까지 `executescript`/`CREATE`/`ALTER`/`DROP`, 운영 정책·계정
시딩은 실행하지 않는다.
