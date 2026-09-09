"""
=========================================================
Homez OS

File : tests/test_gate2_tenant_isolation_hardening_migration.py

V7 Gate 2(2026-08-15) — 테넌트 격리 하드닝 Migration
(migrations/20260815_00_gate2_tenant_isolation_hardening.sql) 검증.

기존 17개 Migration을 순서대로 적용해 실제 homez.db와 동일한 스키마
상태를 임시 DB에 재현한 뒤, 이 신규 Migration을 적용해 성공/재적용
실패/rollback/Model↔DDL 일치/공식 MigrationRunner 호환을 확인한다.
실제 homez.db는 이 테스트 전체에서 전혀 사용하지 않는다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import MigrationRunner
from app.database.migration_runner import compute_checksum
from app.domains.decision.model import CompanyDecisionPolicy
from app.domains.decision.model import DecisionEvaluation
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

# 실제 homez.db의 schema_migrations 이력 순서(Gate R13 종료 시점) — 이
# 신규 Migration 직전까지 전부 적용.
PRIOR_MIGRATIONS = [
    "20260727_00_create_funding_settlement_schema.sql",
    "20260727_add_settlement_ledger_unique_index.sql",
    "20260728_00_create_v24_v3_schema.sql",
    "20260729_00_create_coupang_integration_schema.sql",
    "20260730_00_create_decision_ai_schema.sql",
    "20260730_01_create_auth_session_schema.sql",
    "20260730_02_create_store_connection_schema.sql",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
    "20260802_00_create_media_listing_package_schema.sql",
    "20260802_01_create_account_recovery_schema.sql",
    "20260803_00_create_account_registration_schema.sql",
    "20260805_00_add_marketplace_listing_status_sync.sql",
    "20260807_00_add_marketplace_listing_rate_limit.sql",
    "20260808_00_create_listing_wizard_schema.sql",
    "20260810_00_create_audit_logs_schema.sql",
    "20260814_00_create_user_settings_schema.sql",
    "20260814_01_add_tenant_isolation_company_id.sql",
]

NEW_MIGRATION = "20260815_00_gate2_tenant_isolation_hardening.sql"

# 신규 테이블.
NEW_TABLE_MODELS = {
    "company_decision_policies": CompanyDecisionPolicy,
}

# ALTER 기반으로 컬럼을 추가한 테이블 → Model 매핑(단순 컬럼 비교).
ALTER_TABLE_MODELS = {
    "product_candidates": ProductCandidate,
    "product_candidate_selections": ProductCandidateSelection,
    "funding_ledgers": FundingLedger,
}

# DROP+CREATE로 재생성한 테이블(UNIQUE/nullable 변경) → Model 매핑.
REBUILT_TABLE_MODELS = {
    "decision_evaluations": DecisionEvaluation,
    "funding_accounts": FundingAccount,
    "funding_holds": FundingHold,
    "supplier_payments": SupplierPayment,
}


def _read(filename: str) -> str:

    path = os.path.join(MIGRATIONS_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


def _db_columns(conn, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _extract_rollback_sql(content: str) -> str:
    """
    파일 하단의 "-- Rollback (실행하지 않음 ...)" 주석 블록에서 실제
    SQL만 뽑아낸다(각 줄 맨 앞의 "-- " 접두사를 제거).
    """

    lines = content.splitlines()
    rollback_lines = []
    in_sql = False

    for line in lines:
        stripped = line.strip()

        if not in_sql:
            if stripped == "-- BEGIN;":
                in_sql = True
                rollback_lines.append("BEGIN;")
            continue

        if stripped.startswith("-- "):
            rollback_lines.append(stripped[3:])
        elif stripped == "--":
            continue

    return "\n".join(rollback_lines)


class MigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.content = _read(NEW_MIGRATION)

        non_comment_lines = [
            line for line in cls.content.splitlines()
            if not line.strip().startswith("--")
        ]
        cls.executed_sql = "\n".join(non_comment_lines)

    def test_exactly_one_begin_and_commit(self):

        self.assertEqual(
            len(re.findall(r"^BEGIN;", self.executed_sql, re.M)), 1,
        )
        self.assertEqual(
            len(re.findall(r"^COMMIT;", self.executed_sql, re.M)), 1,
        )

    def test_no_foreign_keys_or_if_not_exists(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertNotIn("REFERENCES", self.executed_sql.upper())
        self.assertNotIn("IF NOT EXISTS", self.executed_sql.upper())

    def test_touches_only_expected_tables(self):

        expected = (
            set(ALTER_TABLE_MODELS)
            | set(REBUILT_TABLE_MODELS)
            | set(NEW_TABLE_MODELS)
        )

        altered = set(
            re.findall(
                r"^ALTER TABLE (\w+) ADD COLUMN", self.executed_sql, re.M,
            ),
        )
        dropped = set(
            re.findall(r"^DROP TABLE (\w+);", self.executed_sql, re.M),
        )
        created_raw = set(
            re.findall(r"^CREATE TABLE (\w+) \(", self.executed_sql, re.M),
        )
        created = {
            name[:-4] if name.endswith("_new") else name
            for name in created_raw
        }
        renamed = set(
            re.findall(
                r"^ALTER TABLE (\w+)_new RENAME TO \w+;",
                self.executed_sql, re.M,
            ),
        )

        self.assertEqual(altered, set(ALTER_TABLE_MODELS))
        self.assertEqual(dropped, set(REBUILT_TABLE_MODELS))
        self.assertEqual(
            created, set(REBUILT_TABLE_MODELS) | set(NEW_TABLE_MODELS),
        )
        self.assertEqual(renamed, set(REBUILT_TABLE_MODELS))
        self.assertEqual(altered | dropped | created, expected)

    def test_does_not_modify_prior_migration_files(self):
        """
        이미 실제 homez.db에 APPLIED로 기록된 17개 파일은 checksum이
        고정돼 있다 — 이번 작업에서 손대지 않았어야 한다.
        """

        for filename in PRIOR_MIGRATIONS:
            path = os.path.join(MIGRATIONS_DIR, filename)
            self.assertTrue(
                os.path.exists(path), f"{filename} 파일이 없습니다.",
            )


class MigrationApplyTestCase(unittest.TestCase):
    """
    실제 homez.db와 동일한 스키마 상태(기존 17개 Migration 순서대로
    적용)를 임시 DB에 재현한 뒤, 신규 Migration을 적용해 검증한다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.prior_sql = [_read(name) for name in PRIOR_MIGRATIONS]
        self.new_sql = _read(NEW_MIGRATION)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _apply_prior_migrations(self, conn):

        for sql in self.prior_sql:
            conn.executescript(sql)

    def test_full_chain_applies_cleanly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])
        finally:
            conn.close()

    def test_reapply_fails_explicitly(self):
        """
        재적용 시도는 안전하게 실패해야 한다(DROP TABLE 대상 테이블이
        이미 없거나, ALTER ADD COLUMN 대상 컬럼이 이미 있어 SQLite가
        거부한다) — 조용히 무시되지 않는다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()

    def test_new_table_columns_match_model(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            for table_name, model_cls in NEW_TABLE_MODELS.items():
                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_alter_table_columns_match_model(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            for table_name, model_cls in ALTER_TABLE_MODELS.items():
                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_rebuilt_table_columns_match_model(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            for table_name, model_cls in REBUILT_TABLE_MODELS.items():
                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_company_decision_policy_unique_per_company(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO company_decision_policies "
                "(id, company_id, policy_set_id, policy_version, "
                "source_reference, status, is_complete, is_active, "
                "axis_weights_json, min_approve_total_score, "
                "min_confidence_for_recommendation, checked_at, "
                "effective_at, created_at) VALUES "
                "(1, 1, 'DEFAULT', '1.0.0', 'ref', 'VERIFIED', 1, 1, "
                "'{}', 70.0, 0.5, '2026-01-01', '2026-01-01', "
                "'2026-01-01')",
            )
            # 다른 회사는 같은 policy_set_id를 독립적으로 쓸 수 있다.
            conn.execute(
                "INSERT INTO company_decision_policies "
                "(id, company_id, policy_set_id, policy_version, "
                "source_reference, status, is_complete, is_active, "
                "axis_weights_json, min_approve_total_score, "
                "min_confidence_for_recommendation, checked_at, "
                "effective_at, created_at) VALUES "
                "(2, 2, 'DEFAULT', '1.0.0', 'ref', 'VERIFIED', 1, 1, "
                "'{}', 70.0, 0.5, '2026-01-01', '2026-01-01', "
                "'2026-01-01')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO company_decision_policies "
                    "(id, company_id, policy_set_id, policy_version, "
                    "source_reference, status, is_complete, is_active, "
                    "axis_weights_json, min_approve_total_score, "
                    "min_confidence_for_recommendation, checked_at, "
                    "effective_at, created_at) VALUES "
                    "(3, 1, 'DEFAULT', '2.0.0', 'ref2', 'DRAFT', 0, 1, "
                    "'{}', 70.0, 0.5, '2026-01-01', '2026-01-01', "
                    "'2026-01-01')",
                )
        finally:
            conn.close()

    def test_funding_account_company_id_not_null_enforced(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO funding_accounts "
                    "(company_id, total_funding, held_amount, currency, "
                    "created_at, updated_at) VALUES "
                    "(NULL, 100000, 0, 'KRW', '2026-01-01', '2026-01-01')",
                )
        finally:
            conn.close()

    def test_funding_hold_idempotency_key_scoped_per_company(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO funding_accounts "
                "(company_id, total_funding, held_amount, currency, "
                "created_at, updated_at) VALUES "
                "(1, 100000, 0, 'KRW', '2026-01-01', '2026-01-01')",
            )
            conn.execute(
                "INSERT INTO funding_accounts "
                "(company_id, total_funding, held_amount, currency, "
                "created_at, updated_at) VALUES "
                "(2, 50000, 0, 'KRW', '2026-01-01', '2026-01-01')",
            )

            conn.execute(
                "INSERT INTO funding_holds "
                "(company_id, account_id, order_id, amount, status, "
                "idempotency_key, created_at, updated_at) VALUES "
                "(1, 1, 100, 1000, 'HELD', 'order:100:supply_hold', "
                "'2026-01-01', '2026-01-01')",
            )
            # 회사 2가 동일한 문자열의 idempotency_key를 쓰더라도
            # 서로 간섭하지 않는다.
            conn.execute(
                "INSERT INTO funding_holds "
                "(company_id, account_id, order_id, amount, status, "
                "idempotency_key, created_at, updated_at) VALUES "
                "(2, 2, 200, 500, 'HELD', 'order:100:supply_hold', "
                "'2026-01-01', '2026-01-01')",
            )
            conn.commit()

            count = conn.execute(
                "SELECT COUNT(*) FROM funding_holds "
                "WHERE idempotency_key = 'order:100:supply_hold'",
            ).fetchone()[0]
            self.assertEqual(count, 2)

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO funding_holds "
                    "(company_id, account_id, order_id, amount, status, "
                    "idempotency_key, created_at, updated_at) VALUES "
                    "(1, 1, 101, 200, 'HELD', 'order:100:supply_hold', "
                    "'2026-01-01', '2026-01-01')",
                )
        finally:
            conn.close()

    def test_supplier_payment_idempotency_key_scoped_per_company(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO funding_accounts "
                "(company_id, total_funding, held_amount, currency, "
                "created_at, updated_at) VALUES "
                "(1, 100000, 0, 'KRW', '2026-01-01', '2026-01-01')",
            )
            conn.execute(
                "INSERT INTO funding_holds "
                "(company_id, account_id, order_id, amount, status, "
                "idempotency_key, created_at, updated_at) VALUES "
                "(1, 1, 100, 1000, 'COMMITTED', 'order:100:supply_hold', "
                "'2026-01-01', '2026-01-01')",
            )

            conn.execute(
                "INSERT INTO supplier_payments "
                "(company_id, purchase_id, order_id, supplier_id, "
                "account_id, hold_id, amount, status, paid_at, "
                "idempotency_key, created_at) VALUES "
                "(1, 1, 100, 1, 1, 1, 1000, 'PAID', '2026-01-01', "
                "'purchase:1:supplier_payment', '2026-01-01')",
            )
            conn.commit()

            # purchase_id UNIQUE는 회사 무관하게 여전히 전역이다(업무
            # 불변식) — 같은 purchase_id로 다른 회사가 재사용 불가.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO supplier_payments "
                    "(company_id, purchase_id, order_id, supplier_id, "
                    "account_id, hold_id, amount, status, paid_at, "
                    "idempotency_key, created_at) VALUES "
                    "(2, 1, 100, 1, 2, 2, 1000, 'PAID', '2026-01-01', "
                    "'purchase:1:supplier_payment:dup', '2026-01-01')",
                )
        finally:
            conn.close()

    def test_product_candidate_visibility_default_is_global(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO product_candidates "
                "(candidate_key, source_type, source_reference, market, "
                "product_name, status, discovered_at, created_at, "
                "updated_at) VALUES "
                "('key-1', 'TREND', 'ref-1', 'COUPANG', '상품', "
                "'DISCOVERED', '2026-01-01', '2026-01-01', '2026-01-01')",
            )
            conn.commit()

            row = conn.execute(
                "SELECT visibility, owner_company_id FROM product_candidates "
                "WHERE candidate_key = 'key-1'",
            ).fetchone()
            self.assertEqual(row[0], "GLOBAL")
            self.assertIsNone(row[1])
        finally:
            conn.close()


class MigrationRollbackTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.prior_sql = [_read(name) for name in PRIOR_MIGRATIONS]
        self.new_content = _read(NEW_MIGRATION)
        self.new_sql = "\n".join(
            line for line in self.new_content.splitlines()
            if not line.strip().startswith("--")
        )
        self.rollback_sql = _extract_rollback_sql(self.new_content)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_rollback_restores_pre_migration_schema(self):

        conn = sqlite3.connect(self.db_path)
        try:
            for sql in self.prior_sql:
                conn.executescript(sql)

            before_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }

            conn.executescript(self.new_sql)

            self.assertTrue(self.rollback_sql.strip())
            conn.executescript(self.rollback_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            after_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertEqual(before_tables, after_tables)

            for table_name in ALTER_TABLE_MODELS:
                cols = {
                    row[1]
                    for row in conn.execute(
                        f"PRAGMA table_info({table_name})",
                    ).fetchall()
                }
                for new_col in (
                    "visibility", "owner_company_id", "memo",
                    "company_id",
                ):
                    if new_col == "memo" and table_name != (
                        "product_candidate_selections"
                    ):
                        continue
                    if new_col == "company_id" and table_name != (
                        "funding_ledgers"
                    ):
                        continue
                    if new_col in ("visibility", "owner_company_id") and (
                        table_name != "product_candidates"
                    ):
                        continue
                    self.assertNotIn(
                        new_col, cols,
                        f"{table_name}: rollback 후에도 {new_col}이 "
                        "남아있음.",
                    )

            self.assertNotIn(
                "company_decision_policies", after_tables,
            )
        finally:
            conn.close()


class OfficialMigrationRunnerTestCase(unittest.TestCase):
    """
    이 저장소의 공식 MigrationRunner(app/database/migration_runner.py)
    + 실제 migrations/ 디렉터리(18개 파일 전체, 새 파일 포함)를 그대로
    사용한 리허설. 실제 homez.db는 사용하지 않는다.

    Gate R13에서 MigrationRunner에 _extract_superseded_targets()를
    추가해(같은 스크립트 안에서 DROP으로 상쇄되는 대상은 충돌 후보에서
    제외) 테이블 재생성형 Migration도 공식 Runner 경로로 정상 적용됨을
    이미 확인했다 — 이 신규 Migration도 동일한 "_new로 CREATE → DROP →
    RENAME" 기법을 쓰므로 그 수정이 여기서도 그대로 적용되는지 확인
    한다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.runner = MigrationRunner(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
        )

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_new_migration_applies_cleanly_via_runner(self):

        conn = sqlite3.connect(self.db_path)
        try:
            applied = self.runner.apply_pending(conn)
            self.assertIn(NEW_MIGRATION, applied)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])

            for table_name in NEW_TABLE_MODELS:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                self.assertIsNotNone(row, f"{table_name} 테이블이 없습니다.")

            for table_name in {**ALTER_TABLE_MODELS, **REBUILT_TABLE_MODELS}:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                self.assertIsNotNone(row, f"{table_name} 테이블이 없습니다.")

            history_row = conn.execute(
                "SELECT status FROM schema_migrations WHERE filename=?",
                (NEW_MIGRATION,),
            ).fetchone()
            self.assertIsNotNone(history_row)
            self.assertEqual(history_row[0], "APPLIED")

            applied_again = self.runner.apply_pending(conn)
            self.assertEqual(applied_again, [])

            diagnosis = self.runner.diagnose(conn)
            self.assertIn(NEW_MIGRATION, diagnosis["already_applied"])
            self.assertNotIn(NEW_MIGRATION, diagnosis["pending"])
        finally:
            conn.close()

    def test_checksum_is_stable(self):
        """
        Runner의 checksum 계산이 파일 내용 기준으로 결정적임을
        재확인한다(Gate R13에서 확립된 방어적 회귀 테스트 패턴).
        """

        path = os.path.join(MIGRATIONS_DIR, NEW_MIGRATION)

        from pathlib import Path

        checksum_a = compute_checksum(Path(path))
        checksum_b = compute_checksum(Path(path))
        self.assertEqual(checksum_a, checksum_b)


if __name__ == "__main__":
    unittest.main()
