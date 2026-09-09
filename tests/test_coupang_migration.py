"""
=========================================================
Homez OS

File : tests/test_coupang_migration.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation
Migration 정적/적용 검증 + Model 자동 드리프트 감지 + 중간 실패 rollback
검증.

Whitelist에 명시된 5개 테스트 파일(product_contract/policy/
profit_estimate/dry_run/concurrency) 외에, 기존 V2.3/V2.4/V3와 동일하게
Migration 자체의 정적/적용 검증이 반드시 필요하다고 판단해 추가한다
(tests/test_v24_v3_schema_migration.py와 동일한 패턴 — 필요성이
확인되어 추가한 파일임을 완료 보고에 명시한다).

표준 라이브러리 unittest + sqlite3만 사용. 신규 패키지 없음. homez.db는
사용하지 않고, 테스트 전용 임시 SQLite 파일 DB를 사용한다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangPolicyRule
from app.domains.coupang.model import CoupangPolicySet
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT,
    "migrations",
    "20260729_00_create_coupang_integration_schema.sql",
)

EXPECTED_TABLES = {
    "coupang_marketplace_products",
    "coupang_product_options",
    "coupang_policy_sets",
    "coupang_policy_rules",
    "coupang_product_notices",
    "coupang_profit_estimates",
    "coupang_dry_run_attempts",
    "coupang_integration_decisions",
}

MODEL_TABLE_ORDER = [
    CoupangMarketplaceProduct,
    CoupangProductOption,
    CoupangPolicySet,
    CoupangPolicyRule,
    CoupangProductNotice,
    CoupangProfitEstimate,
    CoupangDryRunAttempt,
    CoupangIntegrationDecision,
]


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


class CoupangSchemaMigrationStaticTestCase(unittest.TestCase):
    """Migration 파일 내용 자체에 대한 정적 검증."""

    @classmethod
    def setUpClass(cls):

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            cls.content = f.read()

        cls.non_comment_lines = [
            line
            for line in cls.content.splitlines()
            if not line.strip().startswith("--")
        ]
        cls.executed_sql = "\n".join(cls.non_comment_lines)

    def test_exactly_eight_create_table_statements(self):

        creates = re.findall(
            r"^CREATE TABLE (\w+)", self.executed_sql, re.M,
        )
        self.assertEqual(len(creates), 8)
        self.assertEqual(set(creates), EXPECTED_TABLES)

    def test_no_create_table_if_not_exists(self):

        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)

    def test_no_foreign_keys(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertNotIn("REFERENCES", self.executed_sql.upper())

    def test_exactly_one_executed_begin_and_commit(self):

        begins = re.findall(r"^BEGIN;", self.executed_sql, re.M)
        commits = re.findall(r"^COMMIT;", self.executed_sql, re.M)
        self.assertEqual(len(begins), 1)
        self.assertEqual(len(commits), 1)

    def test_no_executed_mutation_or_ddl_drop_statements(self):

        forbidden = re.findall(
            r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER)\b",
            self.executed_sql,
            re.M | re.I,
        )
        self.assertEqual(forbidden, [])

    def test_rollback_drops_only_in_comments_and_count_matches(self):

        comment_lines = [
            line
            for line in self.content.splitlines()
            if line.strip().startswith("--")
        ]
        comment_text = "\n".join(comment_lines)
        drop_count_in_comments = len(
            re.findall(r"DROP (TABLE|INDEX)", comment_text),
        )

        table_count = len(
            re.findall(r"^CREATE TABLE", self.executed_sql, re.M),
        )
        index_count = len(
            re.findall(r"^CREATE( UNIQUE)? INDEX", self.executed_sql, re.M),
        )

        self.assertEqual(
            drop_count_in_comments, table_count + index_count,
        )

    def test_no_default_check_trigger_cascade(self):
        """
        단어 경계(\\b) 매칭을 사용한다 — "last_stock_checked_at"/
        "checked_at"처럼 CHECK를 부분 문자열로 포함하는 정상 컬럼명을
        SQL CHECK 키워드로 오탐하지 않기 위함이다.
        """

        for keyword in ("DEFAULT", "CHECK", "TRIGGER", "CASCADE"):
            self.assertNotRegex(
                self.executed_sql.upper(), rf"\b{keyword}\b",
            )

    def test_migration_matches_sqlalchemy_model_ddl(self):
        """
        2026-08-14 Gate R13 테넌트 격리 감사 — 이 파일(20260729_00_...)은
        이미 실제 homez.db에 APPLIED로 기록되어 checksum이 고정돼
        있어 더 이상 수정할 수 없는데, coupang_marketplace_products/
        coupang_product_options/coupang_product_notices/
        coupang_profit_estimates/coupang_dry_run_attempts/
        coupang_integration_decisions 6개 Model에는 company_id가
        추가돼 이 파일이 만들던 시점보다 앞서 있다(신규
        `20260814_01_add_tenant_isolation_company_id.sql`, 아직 미적용).
        그 6개 테이블의 Model 일치 여부는
        tests/test_gate_r13_tenant_isolation_migration.py::
        MigrationApplyTestCase가 이미 전담 검증한다(기존 15개 Migration
        누적 적용 + 신규 Migration 적용 후 최종 스키마 비교) —
        여기서는 "이미 적용된 8개 테이블 중 손대지 않은 2개
        (CoupangPolicySet/CoupangPolicyRule, 전역 유지 판단)는 지금 이
        파일과 정확히 일치한다"만 계속 지킨다(MarketplaceFulfillment
        Migration이 확립한 동일한 패턴 — test_marketplace_fulfillment_
        migration.py 참고).
        """

        dialect = sqlite_dialect.dialect()
        canonical_statements = []

        untouched_models = [
            model_cls for model_cls in MODEL_TABLE_ORDER
            if model_cls in (CoupangPolicySet, CoupangPolicyRule)
        ]

        for model_cls in untouched_models:

            table = model_cls.__table__
            canonical_statements.append(
                _normalize_sql(
                    str(CreateTable(table).compile(dialect=dialect)),
                ),
            )

            for index in sorted(table.indexes, key=lambda ix: ix.name):
                canonical_statements.append(
                    _normalize_sql(
                        str(CreateIndex(index).compile(dialect=dialect)),
                    ),
                )

        untouched_table_names = {
            model_cls.__table__.name for model_cls in untouched_models
        }

        executable_body = (
            self.executed_sql
            .replace("BEGIN;", "")
            .replace("COMMIT;", "")
        )
        # company_id가 추가된 6개 테이블의 CREATE TABLE/INDEX 문은 이
        # 비교에서 제외한다(위 docstring 참고) — 손대지 않은 2개
        # 테이블(정책 세트/규칙)에 대한 문장만 남긴다.
        migration_statements = [
            _normalize_sql(stmt)
            for stmt in executable_body.split(";")
            if stmt.strip()
            and any(
                f" {name} " in f" {_normalize_sql(stmt)} "
                or f" {name} (" in f" {_normalize_sql(stmt)} "
                for name in untouched_table_names
            )
        ]

        missing = [
            stmt for stmt in canonical_statements
            if stmt not in migration_statements
        ]
        extra = [
            stmt for stmt in migration_statements
            if stmt not in canonical_statements
        ]

        self.assertEqual(
            (missing, extra),
            ([], []),
            "Model ↔ Migration DDL 불일치.\n"
            f"missing({len(missing)}개): {missing}\n"
            f"extra({len(extra)}개): {extra}",
        )


class CoupangSchemaMigrationApplyTestCase(unittest.TestCase):
    """임시 SQLite 파일 DB에 실제로 Migration을 적용하는 검증. homez.db는 사용하지 않는다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            self.migration_sql = f.read()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_migration_applies_and_creates_all_tables(self):

        conn = sqlite3.connect(self.db_path)

        try:
            conn.executescript(self.migration_sql)

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables))

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

        finally:
            conn.close()

    def test_migration_reapply_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)

        try:
            conn.executescript(self.migration_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.migration_sql)

        finally:
            conn.close()

    def test_rollback_script_drops_all_objects_cleanly(self):

        rollback_statements = []
        in_rollback = False

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()

                if stripped.startswith("-- BEGIN;"):
                    in_rollback = True
                    continue

                if stripped.startswith("-- COMMIT;"):
                    break

                if in_rollback and stripped.startswith("-- DROP"):
                    rollback_statements.append(stripped[3:])

        self.assertTrue(len(rollback_statements) > 0)

        conn = sqlite3.connect(self.db_path)

        try:
            conn.executescript(self.migration_sql)

            for stmt in rollback_statements:
                conn.execute(stmt)
            conn.commit()

            remaining = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table', 'index') "
                "AND name NOT LIKE 'sqlite_%'",
            ).fetchall()
            self.assertEqual(remaining, [])

        finally:
            conn.close()

    def test_mid_migration_failure_rolls_back_created_objects(self):
        """
        coupang_dry_run_attempts와 이름이 겹치는 충돌용 테이블을 미리
        만들어 두면, Migration은 그 앞의 6개 테이블(products~
        profit_estimates)까지 생성한 뒤 거기서 실패해야 한다. 실패 후
        명시적 rollback으로 그 이전 성공분이 전부 되돌려지는지 확인한다.
        """

        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute(
                "CREATE TABLE coupang_dry_run_attempts "
                "(conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            error_message = str(ctx.exception)
            self.assertIn("coupang_dry_run_attempts", error_message)
            self.assertIn("already exists", error_message)

            conn.rollback()

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }

            for table_name in EXPECTED_TABLES - {"coupang_dry_run_attempts"}:
                self.assertNotIn(
                    table_name, tables,
                    f"실패 전에 생성된 {table_name}가 rollback되지 않았다.",
                )

            self.assertIn("coupang_dry_run_attempts", tables)

            conflict_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(coupang_dry_run_attempts)",
                )
            }
            self.assertEqual(conflict_columns, {"conflict_marker"})

        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
