---
name: homez-v23
description: HOMEZ V2.3 Marketplace Settlement Hardening을 구현, 검증, 복구하거나 상태를 판단할 때 사용한다. Settlement, Funding, Ledger, Migration, 동시성, 멱등성 작업에서 자동 호출한다.
---

# Settlement Fixed Rules (절대 변경 금지)

1. `net_amount == gross_amount - fee_amount` — 불일치 시 생성 차단, 자동 보정 금지
2. `confirm_deposit` 순서: Settlement 검증 → Funding 반영 → Ledger 확인 → DEPOSITED 저장
3. `DEPOSITED` 재호출 시 `FUNDING_ADD` 금지, 기존 Settlement 반환
4. `REVERSED` 재호출 시 `FUNDING_REMOVE` 금지, 기존 Settlement 반환

# Hardening Requirements

- Account/Ledger/Settlement 변경은 단일 Transaction, 최종 commit 1회
- 예외 발생 시 전체 rollback (부분 성공 금지)
- Ledger INSERT(flush) 성공 후에만 Account 잔액 반영
- Settlement 상태 전이는 조건부 UPDATE + rowcount 검증(1이 아니면 ConflictException)
- `funding_ledgers`에 `reference_type='settlement'`에 한정된 부분 UNIQUE 인덱스
  (`uq_funding_ledger_settlement_type` on (reference_id, type))로 중복 Ledger를 DB 레벨 차단
- 기존 Ledger 발견만으로 상태를 자동 완료 처리하는 경로 금지(orphan Ledger 자동 완료 금지) —
  항상 새 INSERT를 시도하고, IntegrityError 시에만 재조회 후 상태·타입·참조·계좌·금액이
  전부 일치할 때만 경쟁 승자의 결과를 반환
- Hold/Supplier Payment 기존 흐름 회귀 없음

# Current State (2026-07-27 기준, 실제 확인된 사실)

- `tests/test_settlement_hardening.py` 17개 + `tests/test_v23_schema_migration.py` 17개
  = 총 34개 테스트 통과 (venv/Scripts/python -m unittest ...)
- 전체 스키마 Migration(`migrations/20260727_00_create_funding_settlement_schema.sql`)을
  실제 `homez.db`에 적용 완료 — 5개 테이블(`funding_accounts`, `funding_ledgers`,
  `funding_holds`, `supplier_payments`, `marketplace_settlements`) + 24개 인덱스 생성 확인
- 적용 전 검증 백업 존재: `C:\Users\Daum pc\Homez-Backups\homez_pre_v23_migration_20260727_223250.db`
  (적용 직전 SHA-256으로 무결성 확인됨)
- 적용 후 `PRAGMA integrity_check` = ok, 기존 11개 테이블 데이터 보존 확인
- Cursor/독립 감사 판정: `V2_3_SCHEMA_REAUDIT_PASS_WITH_CONDITIONS` → Medium 2건
  (Model↔Migration 자동 드리프트 감지, 중간 실패 rollback 검증) 보완 완료
- 잔여 Low: 인덱스 no-op이 이름 기준(내용 비교 아님), `app/core/dependency.py`/
  `app/core/dependency/` 이름 충돌(현재는 무해), 동시성 검증이 회귀 테스트로
  영속화되어 있지 않음, `datetime.utcnow()` Deprecation
- 실제 다중 프로세스 동시성 및 Staging 부하 테스트는 미검증
- V2.3 관련 파일 대부분이 git에 untracked 상태(대규모 WIP와 공존)

# Migration 파일 순서

1. `migrations/20260727_00_create_funding_settlement_schema.sql` (전체 스키마, 먼저 적용)
2. `migrations/20260727_add_settlement_ledger_unique_index.sql` (인덱스 전용,
   `IF NOT EXISTS`라 위 파일 적용 후에는 no-op)

완료 조건 체크리스트는 `completion-checklist.md` 참고.
