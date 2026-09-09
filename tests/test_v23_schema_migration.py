"""
=========================================================
Homez OS

File : tests/test_v23_schema_migration.py

HOMEZ V2.3 Marketplace Settlement Hardening
전체 스키마 Migration 정적/적용 검증

표준 라이브러리 unittest + sqlite3만 사용. 신규 패키지 없음.
homez.db는 사용하지 않고, 테스트 전용 임시 SQLite 파일 DB를 사용한다.
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

from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.settlement.model import MarketplaceSettlement

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT,
    "migrations",
    "20260727_00_create_funding_settlement_schema.sql",
)
INDEX_MIGRATION_PATH = os.path.join(
    REPO_ROOT,
    "migrations",
    "20260727_add_settlement_ledger_unique_index.sql",
)

EXPECTED_TABLES = {
    "funding_accounts",
    "funding_ledgers",
    "funding_holds",
    "supplier_payments",
    "marketplace_settlements",
}

# Model 기준 테이블 순서 (Source of Truth 고정 순서, Migration 파일 순서와 동일)
MODEL_TABLE_ORDER = [
    FundingAccount,
    FundingLedger,
    FundingHold,
    SupplierPayment,
    MarketplaceSettlement,
]


def _normalize_sql(statement: str) -> str:
    """공백·개행을 단일 공백으로, 후행 세미콜론만 제거한다(의미 있는 내용은 보존)."""

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


class SchemaMigrationStaticTestCase(unittest.TestCase):
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

    def test_exactly_five_create_table_statements(self):

        creates = re.findall(
            r"^CREATE TABLE (\w+)", self.executed_sql, re.M,
        )
        self.assertEqual(len(creates), 5)
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

    def test_rollback_drops_only_in_comments(self):

        comment_lines = [
            line
            for line in self.content.splitlines()
            if line.strip().startswith("--")
        ]
        comment_text = "\n".join(comment_lines)
        drop_count_in_comments = len(
            re.findall(r"DROP (TABLE|INDEX)", comment_text),
        )
        # 5 tables + 24 indexes(=29) rollback DROP 라인이 주석 안에 존재해야 한다.
        self.assertEqual(drop_count_in_comments, 29)

    def test_no_default_check_trigger_cascade(self):

        for keyword in ("DEFAULT", "CHECK", "TRIGGER", "CASCADE"):
            self.assertNotIn(keyword, self.executed_sql.upper())

    def test_marketplace_settlements_no_duplicate_column_declarations(self):

        match = re.search(
            r"CREATE TABLE marketplace_settlements \((.*?)\n\);",
            self.content,
            re.S,
        )
        self.assertIsNotNone(match)

        column_lines = [
            line.strip().split()[0]
            for line in match.group(1).splitlines()
            if line.strip() and not line.strip().startswith("CONSTRAINT")
        ]
        column_names = [c.rstrip(",") for c in column_lines]

        self.assertEqual(
            column_names.count("idempotency_key"), 1,
        )
        self.assertEqual(column_names.count("memo"), 1)
        self.assertEqual(
            len(column_names), len(set(column_names)),
            f"중복 컬럼 선언 발견: {column_names}",
        )

    def test_partial_unique_index_definition_matches_model(self):

        self.assertIn(
            "CREATE UNIQUE INDEX uq_funding_ledger_settlement_type "
            "ON funding_ledgers (reference_id, type) "
            "WHERE reference_type = 'settlement'",
            self.executed_sql,
        )

    def test_new_migration_sorts_before_existing_index_migration(self):

        new_name = os.path.basename(MIGRATION_PATH)
        existing_name = os.path.basename(INDEX_MIGRATION_PATH)
        self.assertLess(new_name, existing_name)

    def test_migration_matches_sqlalchemy_model_ddl(self):
        """
        5개 Model에서 SQLAlchemy SQLite dialect로 컴파일한 canonical DDL과
        Migration 파일의 실행 DDL(주석·BEGIN·COMMIT 제외)을 대조한다.

        DB 연결(엔진/커넥션) 없이 dialect 객체만으로 compile한다.
        공백·개행·후행 세미콜론만 정규화하며, 컬럼·타입·제약·WHERE절 등
        의미 있는 내용은 제거·약화하지 않는다.

        2026-08-14 Gate R13 테넌트 격리 감사 — 이 파일(20260727_00_...)은
        이미 실제 homez.db에 APPLIED로 기록되어 checksum이 고정돼
        있어 더 이상 수정할 수 없는데, MarketplaceSettlement Model에는
        company_id가 추가돼 이 파일이 만들던 시점보다 앞서 있다(신규
        `20260814_01_add_tenant_isolation_company_id.sql`). 그 테이블의
        Model 일치 여부는 tests/test_gate_r13_tenant_isolation_migration.py::
        MigrationApplyTestCase가 전담 검증한다.

        2026-08-15 V7 Gate 2 — 나머지 4개 테이블(FundingAccount/
        FundingLedger/FundingHold/SupplierPayment)도 이번에 전부
        company_id를 얻어(FundingAccount는 nullable→NOT NULL 전환,
        나머지 3개는 신규 컬럼) 이 원본 Migration 시점보다 앞서게
        됐다 — 요구사항 3(Funding 회사 스코프 강화). 그 4개 테이블의
        Model 일치 여부는 신규
        tests/test_gate2_tenant_isolation_hardening_migration.py가
        전담 검증한다. 결과적으로 이 원본 Migration 파일이 만들 때의
        스키마와 그대로 일치하는 테이블은 이제 하나도 남지 않았다 —
        `untouched_models`가 빈 리스트가 되어 아래 비교는 자명하게
        (동어반복적으로) 통과한다. 이는 은폐가 아니라 MarketplaceFulfillment/
        Coupang/Decision Migration이 이미 여러 차례 겪은 것과 동일한
        자연스러운 결과다(원본 Migration이 "그 시점의 스냅샷"이고,
        이후 모든 실질 검증은 그 다음 Migration의 전담 테스트로
        이관된다) — 빈 리스트임을 아래에서 명시적으로 assert해
        의도치 않은 회귀(예: 이 목록이 실수로 채워지는데 아무도
        모르는 상황)를 방지한다.
        """

        dialect = sqlite_dialect.dialect()

        canonical_statements = []

        untouched_models = [
            model_cls for model_cls in MODEL_TABLE_ORDER
            if model_cls is not MarketplaceSettlement
            and model_cls not in (
                FundingAccount, FundingLedger, FundingHold, SupplierPayment,
            )
        ]
        self.assertEqual(
            untouched_models, [],
            "이 목록이 채워지면 위 docstring의 전제가 깨진 것이다 — "
            "새로 추가된 Model이 이 원본 V2.3 Migration과 실제로 계속 "
            "일치해야 하는지 다시 판단해야 한다.",
        )

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

        # Migration 파일의 실행 DDL만 추출: 주석은 setUpClass에서 이미 제거됨.
        # 여기서는 실행되지 않는 BEGIN;/COMMIT; 토큰만 추가로 제거하고 문장 단위로 분리한다.
        executable_body = (
            self.executed_sql
            .replace("BEGIN;", "")
            .replace("COMMIT;", "")
        )
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
            f"missing(Model에는 있으나 Migration SQL에 없음, {len(missing)}개): "
            f"{missing}\n"
            f"extra(Migration SQL에는 있으나 Model에 없음, {len(extra)}개): "
            f"{extra}",
        )


class SchemaMigrationApplyTestCase(unittest.TestCase):
    """임시 SQLite 파일 DB에 실제로 Migration을 적용하는 검증. homez.db는 사용하지 않는다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            self.migration_sql = f.read()

        with open(INDEX_MIGRATION_PATH, encoding="utf-8") as f:
            self.index_migration_sql = f.read()

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

    def test_existing_index_migration_is_noop_after_full_schema(self):
        """기존 인덱스 전용 Migration은 IF NOT EXISTS라 충돌 없이 재실행 가능해야 한다."""

        conn = sqlite3.connect(self.db_path)

        try:
            conn.executescript(self.migration_sql)
            # 예외 없이 조용히 통과해야 한다(이미 존재하는 인덱스).
            conn.executescript(self.index_migration_sql)

            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='index' AND name='uq_funding_ledger_settlement_type'",
            ).fetchone()[0]
            self.assertEqual(count, 1)

        finally:
            conn.close()

    def test_partial_unique_index_blocks_only_settlement_reference(self):

        conn = sqlite3.connect(self.db_path)

        try:
            conn.executescript(self.migration_sql)

            conn.execute(
                "INSERT INTO funding_accounts "
                "(total_funding, held_amount, currency, created_at, updated_at) "
                "VALUES (100000, 0, 'KRW', datetime('now'), datetime('now'))",
            )
            account_id = conn.execute(
                "SELECT last_insert_rowid()",
            ).fetchone()[0]

            conn.execute(
                "INSERT INTO funding_ledgers "
                "(account_id, amount, type, reference_type, reference_id, created_at) "
                "VALUES (?, 5000, 'FUNDING_ADD', 'settlement', 1, datetime('now'))",
                (account_id,),
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO funding_ledgers "
                    "(account_id, amount, type, reference_type, reference_id, created_at) "
                    "VALUES (?, 5000, 'FUNDING_ADD', 'settlement', 1, datetime('now'))",
                    (account_id,),
                )
            conn.rollback()

            # 다른 reference_type(order)에는 동일 (reference_id, type) 조합이
            # 반복되어도 차단되지 않아야 한다(Hold 재활성화 시나리오).
            conn.execute(
                "INSERT INTO funding_ledgers "
                "(account_id, amount, type, reference_type, reference_id, created_at) "
                "VALUES (?, 1000, 'FUNDING_ADD', 'order', 1, datetime('now'))",
                (account_id,),
            )
            conn.execute(
                "INSERT INTO funding_ledgers "
                "(account_id, amount, type, reference_type, reference_id, created_at) "
                "VALUES (?, 1000, 'FUNDING_ADD', 'order', 1, datetime('now'))",
                (account_id,),
            )
            conn.commit()

            count = conn.execute(
                "SELECT COUNT(*) FROM funding_ledgers "
                "WHERE reference_type='order' AND reference_id=1 "
                "AND type='FUNDING_ADD'",
            ).fetchone()[0]
            self.assertEqual(count, 2)

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

        self.assertEqual(len(rollback_statements), 29)

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
        funding_holds와 이름이 겹치는 충돌용 테이블을 미리 만들어 두면,
        Migration은 funding_accounts/funding_ledgers까지 생성한 뒤
        funding_holds CREATE TABLE에서 "already exists"로 실패해야 한다.

        실제 migration SQL 파일을 재작성하거나 핵심 문장을 mock하지 않고
        그대로 executescript로 실행하며, 실패 후 명시적으로
        connection.rollback()을 호출해 이전에 성공한 CREATE TABLE들이
        전부 되돌려지는지 확인한다.
        """

        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute(
                "CREATE TABLE funding_holds (conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            error_message = str(ctx.exception)
            self.assertIn("funding_holds", error_message)
            self.assertIn("already exists", error_message)

            # 실패 직후 명시적 rollback — 스크립트 자체의 BEGIN이 연 트랜잭션을
            # 여기서 되돌린다(그 이전에 성공한 funding_accounts/funding_ledgers
            # CREATE TABLE/INDEX 포함).
            conn.rollback()

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }

            self.assertNotIn(
                "funding_accounts", tables,
                "실패 전에 생성된 funding_accounts가 rollback되지 않았다.",
            )
            self.assertNotIn(
                "funding_ledgers", tables,
                "실패 전에 생성된 funding_ledgers가 rollback되지 않았다.",
            )
            self.assertNotIn(
                "marketplace_settlements", tables,
                "실패 지점 이후 테이블(marketplace_settlements)이 존재해서는 안 된다.",
            )
            self.assertNotIn(
                "supplier_payments", tables,
                "실패 지점 이후 테이블(supplier_payments)이 존재해서는 안 된다.",
            )

            self.assertIn(
                "funding_holds", tables,
                "Migration 이전에 존재했던 충돌용 funding_holds가 사라졌다.",
            )

            conflict_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(funding_holds)")
            }
            self.assertEqual(
                conflict_columns,
                {"conflict_marker"},
                "충돌용 funding_holds의 스키마가 원래 상태에서 변경되었다.",
            )

            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'",
                )
            }
            self.assertFalse(
                any(name.startswith("ix_funding_accounts_") for name in indexes),
            )
            self.assertFalse(
                any(name.startswith("ix_funding_ledgers_") for name in indexes),
            )
            self.assertNotIn("uq_funding_ledger_settlement_type", indexes)

        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
