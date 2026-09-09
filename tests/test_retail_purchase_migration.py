"""
=========================================================
Homez OS

File : tests/test_retail_purchase_migration.py

Gate RP-1(2026-08-22 14차 지시 — 작업 8) 검증 —
migrations/20260822_00_create_retail_purchase_schema.sql 정적 검증 +
임시 SQLite 파일 적용/재적용실패/rollback/중간실패rollback +
Model↔DDL 자동 드리프트 감지. 실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.retail_purchase.model import PaymentAccountReference
from app.domains.retail_purchase.model import RetailPurchaseOrder
from app.domains.retail_purchase.model import RetailPurchasePolicySetting
from app.domains.retail_purchase.model import RetailPurchaseWebhookEvent
from app.domains.retail_purchase.provider import FakeRetailPurchaseProvider
from app.domains.retail_purchase.service import RetailPurchaseService

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations", "20260822_00_create_retail_purchase_schema.sql",
)

EXPECTED_TABLES = {
    "retail_purchase_orders",
    "payment_account_references",
    "retail_purchase_policy_settings",
    "retail_purchase_webhook_events",
}

MODEL_TABLE_ORDER = [
    RetailPurchaseOrder,
    PaymentAccountReference,
    RetailPurchasePolicySetting,
    RetailPurchaseWebhookEvent,
]


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


class SchemaMigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            cls.content = f.read()

        cls.non_comment_lines = [
            line for line in cls.content.splitlines()
            if not line.strip().startswith("--")
        ]
        cls.executed_sql = "\n".join(cls.non_comment_lines)

    def test_exactly_four_create_table_statements(self):

        creates = re.findall(r"^CREATE TABLE (\w+)", self.executed_sql, re.M)
        self.assertEqual(len(creates), 4)
        self.assertEqual(set(creates), EXPECTED_TABLES)

    def test_no_create_table_if_not_exists(self):

        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)

    def test_no_foreign_keys(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        # 단순 substring이 아니라 단어 경계로 찾는다 — 이 스키마는
        # payment_account_references처럼 "reference"를 포함하는 실제
        # 테이블/컬럼/제약 이름이 있어(예: uq_payment_account_
        # references_company_provider), 단순 substring 검사는 오탐
        # 한다. 실제 FK 문법(FOREIGN KEY ... REFERENCES table)에서만
        # REFERENCES가 독립된 키워드로 등장하므로 \b로 식별자 일부와
        # 구분한다.
        self.assertIsNone(
            re.search(r"\bREFERENCES\b", self.executed_sql.upper()),
        )

    def test_exactly_one_executed_begin_and_commit(self):

        begins = re.findall(r"^BEGIN;", self.executed_sql, re.M)
        commits = re.findall(r"^COMMIT;", self.executed_sql, re.M)
        self.assertEqual(len(begins), 1)
        self.assertEqual(len(commits), 1)

    def test_no_executed_mutation_or_ddl_drop_statements(self):

        forbidden = re.findall(
            r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER)\b",
            self.executed_sql, re.M | re.I,
        )
        self.assertEqual(forbidden, [])

    def test_rollback_drops_only_in_comments_and_count_matches(self):

        comment_lines = [
            line for line in self.content.splitlines()
            if line.strip().startswith("--")
        ]
        comment_text = "\n".join(comment_lines)
        drop_count_in_comments = len(
            re.findall(r"DROP (TABLE|INDEX)", comment_text),
        )

        table_count = len(re.findall(r"^CREATE TABLE", self.executed_sql, re.M))
        index_count = len(
            re.findall(r"^CREATE( UNIQUE)? INDEX", self.executed_sql, re.M),
        )
        self.assertEqual(drop_count_in_comments, table_count + index_count)

    def test_no_default_check_trigger_cascade(self):

        for keyword in ("DEFAULT", "CHECK", "TRIGGER", "CASCADE"):
            self.assertNotIn(keyword, self.executed_sql.upper())

    def test_migration_matches_sqlalchemy_model_ddl(self):

        dialect = sqlite_dialect.dialect()
        canonical_statements = []

        for model_cls in MODEL_TABLE_ORDER:
            table = model_cls.__table__
            canonical_statements.append(
                _normalize_sql(str(CreateTable(table).compile(dialect=dialect))),
            )
            for index in sorted(table.indexes, key=lambda ix: ix.name):
                canonical_statements.append(
                    _normalize_sql(
                        str(CreateIndex(index).compile(dialect=dialect)),
                    ),
                )

        table_names = {m.__table__.name for m in MODEL_TABLE_ORDER}
        executable_body = (
            self.executed_sql.replace("BEGIN;", "").replace("COMMIT;", "")
        )
        migration_statements = [
            _normalize_sql(stmt)
            for stmt in executable_body.split(";")
            if stmt.strip()
            and any(
                f" {name} " in f" {_normalize_sql(stmt)} "
                or f" {name} (" in f" {_normalize_sql(stmt)} "
                for name in table_names
            )
        ]

        missing = [s for s in canonical_statements if s not in migration_statements]
        extra = [s for s in migration_statements if s not in canonical_statements]

        self.assertEqual(
            (missing, extra), ([], []),
            "Model ↔ Migration DDL 불일치.\n"
            f"missing({len(missing)}개): {missing}\nextra({len(extra)}개): {extra}",
        )


class SchemaMigrationApplyTestCase(unittest.TestCase):

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
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables))

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_violations, [])
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
                "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'",
            ).fetchall()
            self.assertEqual(remaining, [])
        finally:
            conn.close()

    def test_mid_migration_failure_rolls_back_created_objects(self):
        """마지막 테이블(retail_purchase_webhook_events)과 이름이
        겹치는 충돌용 테이블을 미리 만들어 두면, Migration은 앞의
        3개 테이블까지 생성한 뒤 거기서 실패해야 한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE retail_purchase_webhook_events "
                "(conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            error_message = str(ctx.exception)
            self.assertIn("retail_purchase_webhook_events", error_message)
            self.assertIn("already exists", error_message)

            conn.rollback()

            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            for table_name in EXPECTED_TABLES - {"retail_purchase_webhook_events"}:
                self.assertNotIn(
                    table_name, tables,
                    f"실패 전에 생성된 {table_name}가 rollback되지 않았다.",
                )
            self.assertIn("retail_purchase_webhook_events", tables)

            conflict_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(retail_purchase_webhook_events)",
                )
            }
            self.assertEqual(conflict_columns, {"conflict_marker"})
        finally:
            conn.close()


class ServiceOnMigratedSchemaTestCase(unittest.TestCase):
    """Base.metadata.create_all이 아니라 실제 Migration SQL을 그대로
    적용한 스키마 위에서 RetailPurchaseService 핵심 흐름(요청생성→
    정책확인→예산예약→견적→발주)이 실제로 동작하는지 확인한다 —
    Model 정의와 Migration DDL이 서로 다른 방식으로 어긋나 있어도
    ORM 레벨 테스트만으로는 못 잡는 실사용 결함을 잡기 위함이다."""

    def setUp(self):

        FakeRetailPurchaseProvider.reset_state()

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            migration_sql = f.read()

        conn = sqlite3.connect(path)
        conn.executescript(migration_sql)
        # 2026-08-24 Section 2 — RetailPurchaseService가 이제
        # write_audit_log()를 호출한다. 이 파일은 retail_purchase
        # 전용 Migration 파일 하나만 적용하므로(audit_logs는 별도
        # Migration, 20260810_00) 여기서 직접 보강한다.
        conn.execute(
            "CREATE TABLE audit_logs ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "company_id INTEGER, user_id INTEGER, "
            "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
            "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
            "ip_address VARCHAR(50)"
            ")",
        )
        conn.commit()
        conn.close()

        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, FundingAccount.__table__,
                FundingLedger.__table__, AutomationModeState.__table__,
                EmergencyStop.__table__, ExecutionLimit.__table__,
                ExecutionUsage.__table__, ExecutionPeriodUsage.__table__,
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()

        self.account = FundingAccount(
            company_id=self.company.id, total_funding=1000000.0,
        )
        self.db.add(self.account)
        self.db.commit()

        self.service = RetailPurchaseService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        FakeRetailPurchaseProvider.reset_state()

    def test_full_flow_on_migrated_schema(self):

        import json
        from decimal import Decimal

        from app.domains.retail_purchase.constants import RetailPurchaseOrderStatus
        from app.domains.retail_purchase.policy_service import (
            RetailPurchasePolicyCheckInput,
            RetailPurchasePolicyService,
        )
        from app.domains.retail_purchase.product_matching import (
            ProductAttributes,
            evaluate_same_product,
        )

        policy_svc = RetailPurchasePolicyService(self.db)
        setting = policy_svc.get_or_create_default_settings(self.company.id)
        setting.allowed_provider_codes_json = json.dumps(["FAKE"])
        setting.min_seller_trust_score = 0.5
        self.db.commit()

        order = self.service.create_purchase_request(
            self.company.id, source_order_id=1, provider_code="FAKE",
            product_url="https://fake.example/p1",
            external_product_id="FAKE-PRD-p1", selected_option=None,
            quantity=1, idempotency_key="migrated:1", match_confidence=1.0,
            match_evidence_json="[]", expected_amount=10000.0,
        )

        attrs = ProductAttributes(brand="A", manufacturer="A", model_name="M1")
        match = evaluate_same_product(attrs, attrs)

        order, result = self.service.run_policy_check(
            order.id, self.company.id,
            RetailPurchasePolicyCheckInput(
                provider_code="FAKE", match_result=match, in_stock=True,
                estimated_delivery_days=2, return_allowed=True,
                seller_trust_score=0.9,
                coupang_sale_amount=Decimal("30000"),
                retail_actual_amount=Decimal("13000"),
                shipping_fee=Decimal("3000"), coupang_fee_amount=Decimal("3000"),
            ),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.POLICY_CHECKED)

        order = self.service.reserve_budget(
            order.id, self.company.id, Decimal("16000"),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BUDGET_RESERVED)

        order, quote = self.service.request_quote(order.id, self.company.id)
        order = self.service.place_order(
            order.id, self.company.id, quote_id=quote.quote_id,
            shipping_address_reference="addr-1",
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.ORDERED)


if __name__ == "__main__":
    unittest.main()
