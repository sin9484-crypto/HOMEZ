---
name: homez-migration-safety
description: HOMEZ 데이터베이스 스키마, SQLite, Migration SQL, 백업, rollback, Model과 DDL 대조 작업에서 사용한다.
disable-model-invocation: false
---

# HOMEZ Migration Safety

## 원칙

- Model(`app/domains/**/model.py`)을 항상 Source of Truth로 삼는다. Migration SQL은
  Model에서 파생되어야 하며, Model에 없는 CHECK/DEFAULT/CASCADE/trigger/FK를 임의로
  추가하지 않는다.
- 실제 DB dialect(SQLite/Postgres 등)를 먼저 확인하고 그 dialect 기준으로 DDL을 생성한다.
- 어떤 보고서에도 실제 DB 파일 경로 안의 사용자명 등 민감할 수 있는 정보는 필요
  이상으로 노출하지 않는다(개인정보/자격증명이 아니면 경로 자체는 통상 문제 없음).
- 실제 DB에 무엇이든 적용하기 **전에** 반드시 백업을 만들고 SHA-256/`integrity_check`로
  검증한다.
- 대상 테이블이 하나라도 이미 존재하거나 일부만 존재하면(부분 적용) 적용을 중단하고
  보고한다 — 조용히 건너뛰거나 자동으로 맞추지 않는다.
- Model ↔ SQL 대응은 SQLAlchemy `CreateTable`/`CreateIndex` + 해당 dialect로 canonical
  DDL을 컴파일해 문자열 diff(공백/후행 세미콜론만 정규화)로 확인한다.
- 모든 Migration은 먼저 임시 SQLite 파일 DB에서 적용/재적용 실패/rollback까지 검증한
  뒤에만 실제 DB 적용을 사용자에게 제안한다.
- 실제 DB 적용은 항상 별도의 명시적 사용자 승인을 받은 뒤에만 수행한다.
- 적용 후에는 테이블·제약·인덱스가 기대대로 생성됐는지, `PRAGMA integrity_check`가
  ok인지, 기존 데이터가 보존됐는지 재확인한다.
- 적용 전후로 DB 파일의 크기와 수정 시각을 기록해 의도치 않은 변경이 없었는지
  대조 가능하게 남긴다.
- rollback 계획(역순 DROP)을 항상 함께 작성하되, Migration 파일 자체에서는 주석으로만
  두고 자동 실행되지 않게 한다.
- Migration 적용 전후로 관련 unittest를 실행해 회귀를 확인한다.

## HOMEZ V2.3 특수 규칙 (기록된 사실)

- 적용 순서: `20260727_00_create_funding_settlement_schema.sql` 먼저,
  `20260727_add_settlement_ledger_unique_index.sql`은 그 다음(또는 생략 가능).
- 후자는 `CREATE UNIQUE INDEX IF NOT EXISTS`를 사용하므로 전자가 이미 동일 인덱스를
  만든 뒤에는 이름 기준 no-op이 된다(내용 비교가 아니므로 정의가 갈리면 조용히
  구버전이 유지될 수 있음 — Low 리스크로 기록됨).
- 5개 테이블(`funding_accounts`, `funding_ledgers`, `funding_holds`,
  `supplier_payments`, `marketplace_settlements`)이 하나라도 없는 상태에서 인덱스
  전용 SQL을 단독 실행하면 `no such table: funding_ledgers`로 실패한다 — 반드시
  전체 스키마 Migration을 먼저 적용해야 한다.
- 이 전체 스키마 Migration은 2026-07-27에 실제 `homez.db`에 적용 완료되었다(백업:
  `C:\Users\Daum pc\Homez-Backups\homez_pre_v23_migration_20260727_223250.db`).
  향후 같은 Migration을 다시 실행할 필요는 없다(재실행 시 `table already exists`로
  실패하도록 설계되어 있음 — 이는 결함이 아니라 의도된 fail-fast 정책).
