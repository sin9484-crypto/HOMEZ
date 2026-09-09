# HOMEZ V6 Gate U-6(a) — audit_logs Migration 실제 적용 계획서

작성일: 2026-08-10 / **2026-08-11 정정**

**상태 변경(2026-08-11) — 이 계획서의 핵심 동작(4번 항목)은 이미
사실상 일어났다, 승인된 경로를 통해서가 아니라 사고로.**
`app/database/bootstrap.py::bootstrap_environment()`가 DB 경로
격리 없이 실제 DB를 대상으로 실행된 시점(2026-08-10 20:24:06 KST,
경위는 `docs/V6_EXECUTION_LEDGER.md`의 "Gate U 사고 정정" 절 참고)에
자동으로 `reconcile_backfill()`을 호출해, 이 문서가 계획한 것과
정확히 같은 결과(`schema_migrations`에 이력 1행 추가, 데이터·스키마
변경 없음)를 이미 만들었다. 즉 **"4. 실제 적용"의 4-1/4-2는 이미
완료됐고, 재실행할 대상이 없다**(`diagnose()`가 이제 이 파일을
`already_applied`로 분류함 — `tests/test_audit_logs_migration.py`로
재확인됨). 아직 완료되지 않은 것은 **4-3(`write_audit_log()`로
이 backfill 자체를 `audit_logs`에 감사 기록으로 남기는 것)뿐**이다 —
사고 당시의 자동 경로는 이 특정 파일에 대해 그 감사 로그를 남기지
않았다(`audit_logs` 테이블 행 수가 사고 전후로 8행 그대로임을 실측
확인함). 아래 원안은 과거 기록으로 그대로 두고, 남은 범위만 별도
승인 문구(맨 아래)로 좁혀 다시 제시한다.

실제 homez.db 현재 상태(2026-08-11 재확인, SHA-256
`570742f14bf58b2705818022c29f5beaf9362fa7f213a83d60b2bd8c60af2d14` —
2026-08-10 20:24:06 KST의 backfill 이후로는 다시 바뀌지 않음,
`schema_migrations` 15행):
- `audit_logs` 테이블이 이미 존재한다(8행, 컬럼 `id/company_id/
  user_id/action/entity/entity_id/description/ip_address`, FOREIGN
  KEY 2개 — `companies(id)`, `users(id)`). 이 스키마를 만드는
  Migration 파일이 저장소에 없었다(Gate S/T 때부터 확인된 사실).
- 이번 Gate U-2에서 `migrations/20260810_00_create_audit_logs_schema.sql`
  을 새로 작성해, 실제 DB의 기존 스키마를 원문 그대로(FK 2개 포함)
  재현했다 — 새 설계가 아니라 기존 사실의 문서화다.
- `tests/test_audit_logs_migration.py`(9개 시나리오)로 이미 검증
  완료: 빈 DB에 정확히 이 모양으로 생성됨, 실제 DB 복사본에서는
  스키마가 이미 동일함이 확인되어 SQL 재실행 없이 이력만 기록되는
  backfill 경로로 안전하게 분류됨(데이터 8행 보존, `integrity_check`
  ok), 스키마가 다른 합성 케이스에서는 자동으로 고치지 않고 즉시
  예외로 중단됨.

## 1. 이 적용이 실제로 하는 일

**아무 데이터도 바뀌지 않는다.** 이 Migration의 실제 적용은
`schema_migrations` 부기 테이블(MigrationRunner 전용)에 "이 파일은
이미 적용된 것으로 확인됨(BACKFILLED)"이라는 이력 한 줄을 추가하는
것뿐이다 — `audit_logs` 테이블 자체의 컬럼·행·인덱스는 전혀
건드리지 않는다(diagnose()가 대상 테이블이 이미 존재함을 확인하면
`apply_pending()`이 아니라 `reconcile_backfill()` 경로로만 처리되기
때문 — app/database/migration_runner.py 참고).

## 2. 적용 전 사전 확인(읽기 전용)

1. `sqlite3.connect('file:homez.db?mode=ro', uri=True)` +
   `PRAGMA query_only=ON`으로 다시 한번 실제 DDL을 읽어, 이 문서
   작성 시점과 동일한지 재확인한다(그 사이 다른 프로세스가 스키마를
   바꿨을 가능성 배제).
2. `audit_logs` 행 수를 다시 세어 8행(또는 그 사이 정상적으로 늘어난
   수)인지 확인한다 — 갑자기 줄어들었다면 즉시 중단하고 보고한다.
3. `MigrationRunner(real_db_path, real_migrations_dir).diagnose()`를
   읽기 전용 연결로 실행해 `20260810_00_create_audit_logs_schema.sql`이
   정확히 `backfill_needed`에만 있고 `pending`에는 없는지 확인한다
   (있다면 사전 가정이 깨진 것 — 즉시 중단).

## 3. 적용 전 백업

`homez-migration-safety` 스킬의 기존 원칙을 그대로 따른다:
1. SQLite Online Backup API로 `C:\Users\Daum pc\Homez-Backups\
   homez_pre_gate_u6_audit_logs_backfill_<타임스탬프>.db` 생성.
2. 원본과 백업의 테이블 목록·DDL 텍스트·행 수·`PRAGMA
   integrity_check`가 전부 일치하는지 확인한다(바이트 단위 파일
   해시는 SQLite Online Backup API 특성상 다를 수 있음 — Gate S에서
   이미 확립된 검증 방법 재사용, raw 파일 해시로 비교하지 않는다).
3. 두 조건이 전부 통과한 뒤에만 4번으로 진행한다.

## 4. 실제 적용

1. `app/database/audit_logs_schema_check.py::
   verify_matches_migration_or_raise()`를 실제 DB 연결(쓰기 가능)로
   호출해, 지금 이 순간의 실제 DDL이 Migration 파일의 CREATE TABLE
   선언과 정규화 비교 시 정확히 일치하는지 마지막으로 재확인한다.
   여기서 `AuditLogsSchemaMismatchError`가 발생하면 **즉시 중단**하고
   4-2 이후로 진행하지 않는다 — 이 경우 실제 스키마가 이 Migration
   파일 작성 시점과 달라졌다는 뜻이므로, 자동으로 고치지 않고
   사람이 직접 진단해야 한다.
2. `MigrationRunner.reconcile_backfill(conn)`을 실제 DB 쓰기 가능
   연결로 호출한다 — 이 호출이 하는 일은 정확히 `schema_migrations`
   테이블에 이력 한 줄을 INSERT하는 것뿐이다(2번 항목 참고).
3. 같은 Transaction 안에서 `app/core/audit_db.py::write_audit_log()`로
   이 적용 자체를 감사 로그에 남긴다(action=`MIGRATION_BACKFILLED`,
   entity=`MigrationRunner`, entity_id=파일명).

## 5. 적용 후 검증

1. `audit_logs` 행 수가 적용 전과 정확히 동일한지 확인한다(+1은
   허용 — 4-3의 감사 로그 자체가 한 행을 추가하므로).
2. `PRAGMA integrity_check`가 `ok`인지 재확인한다.
3. `schema_migrations`에 `20260810_00_create_audit_logs_schema.sql`
   행이 `status='BACKFILLED'`로 정확히 1개만 있는지 확인한다.
4. `app.core.migration_restricted_mode.refresh_restricted_mode_state()`
   를 호출해(서버 재시작 시나리오와 동일한 경로) `restricted=False`,
   `pending_files=[]`인지 확인한다 — 이 적용 전에도 이미 pending
   목록에 이 파일이 없었으므로(1번 항목 확인 대상) 제한 모드 자체는
   이 적용으로 바뀌지 않아야 한다(변화가 있다면 예상과 다른 것 —
   원인 조사 필요).

## 6. Rollback

`reconcile_backfill()`은 데이터를 전혀 바꾸지 않으므로(1번 항목),
실패 시 rollback은 단순히 3번에서 만든 백업으로 전체 DB를 복원하는
것이다. `schema_migrations`의 해당 이력 행만 DELETE하는 것도
가능하지만(그 자체로는 `audit_logs` 데이터에 영향 없음), 이 문서는
백업 복원을 유일한 공식 rollback 경로로 채택한다(기존
`homez-migration-safety` 스킬 원칙과 동일 — 부분 되돌리기보다
전체 백업 복원이 항상 더 안전하다).

## 7. 실제 적용 전 필요한 사용자 승인 — 정확한 문구(원안, 과거 기록)

실제 적용을 시작하기 전, 다음 내용을 사용자에게 그대로 제시하고
명시적 "예" 응답을 받아야 한다:

> "실제 `homez.db`의 `audit_logs` 테이블은 이미 존재하고 8행의
> 데이터가 있습니다. 이번에 적용하려는 것은 그 테이블을 만드는
> 새 Migration 파일 하나를 '이미 적용된 것으로' 이력에 기록하는
> 것뿐입니다 — 테이블의 컬럼·데이터·인덱스는 전혀 바뀌지 않습니다.
> 적용 전 자동 백업을 만들고, 스키마가 예상과 다르면 자동으로
> 고치지 않고 즉시 중단합니다. 진행할까요?"

이 승인 없이는 어떤 실제 쓰기도 수행하지 않는다.

## 7b. 2026-08-11 정정 — 남은 범위만을 위한 승인 문구

위 7번의 핵심 동작(이력 1행 기록)은 이미 사고로 완료됐다(상단
"상태 변경" 참고). 남은 것은 그 사고를 사후적으로 감사 기록에
남기는 것뿐이며, 이는 실질적 위험이 없는 선택 사항이다:

> "실제 `homez.db`의 `audit_logs` 테이블(8행, 불변)에 2026-08-10
> 사고로 인한 `20260810_00_create_audit_logs_schema.sql` backfill
> 사실을 사후 감사 기록 1행(action=`MIGRATION_BACKFILLED_RETROACTIVE_LOG`,
> 설명에 사고 경위·시각 명시)으로 남기려 합니다. `schema_migrations`는
> 이미 15행으로 변경 없이 유지되고, 이 기록 자체 외에는 어떤 것도
> 바뀌지 않습니다. 적용 전 자동 백업을 만듭니다. 진행할까요? (건너뛰어도
> 무방 — 데이터 무결성에 영향 없음)"

이 승인 없이는 어떤 실제 쓰기도 수행하지 않는다.
