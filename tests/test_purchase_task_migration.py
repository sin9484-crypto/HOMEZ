"""
=========================================================
Homez OS

File : tests/test_purchase_task_migration.py

Gate PT-1(2026-08-22 15차 지시) 검증 —
migrations/20260822_01_create_purchase_task_schema.sql 정적 검증 +
임시 SQLite 파일 적용/재적용실패/rollback/중간실패rollback +
Model↔DDL 자동 드리프트 감지 + fresh-install 리허설. 실제 homez.db는
전혀 열지 않는다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
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
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationRead
from app.domains.purchase_task.model import PurchaseRecord
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskBudgetReservation
from app.domains.purchase_task.model import PurchaseTaskCandidate
from app.domains.purchase_task.model import PurchaseTaskCsvImportLog
from app.domains.purchase_task.model import PurchaseTaskEmailLog
from app.domains.purchase_task.model import PurchaseTaskEmailPreference
from app.domains.purchase_task.model import PurchaseTaskEmailProviderSetting
from app.domains.purchase_task.model import PurchaseTaskPolicySetting
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo
from app.domains.purchase_task.service import PurchaseTaskService
from app.domains.role.model import Role
from app.domains.user.model import User

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations", "20260822_01_create_purchase_task_schema.sql",
)

EXPECTED_TABLES = {
    "purchase_tasks", "purchase_task_candidates",
    "purchase_task_budget_reservations", "purchase_records",
    "purchase_task_tracking_infos", "purchase_task_email_preferences",
    "purchase_task_email_logs", "purchase_task_policy_settings",
    "purchase_task_csv_import_logs",
    "purchase_task_email_provider_settings",
}

MODEL_TABLE_ORDER = [
    PurchaseTask, PurchaseTaskCandidate, PurchaseTaskBudgetReservation,
    PurchaseRecord, PurchaseTaskTrackingInfo, PurchaseTaskEmailPreference,
    PurchaseTaskEmailLog, PurchaseTaskPolicySetting, PurchaseTaskCsvImportLog,
    PurchaseTaskEmailProviderSetting,
]

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


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

    def test_exactly_ten_create_table_statements(self):

        creates = re.findall(r"^CREATE TABLE (\w+)", self.executed_sql, re.M)
        self.assertEqual(len(creates), 10)
        self.assertEqual(set(creates), EXPECTED_TABLES)

    def test_no_create_table_if_not_exists(self):

        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)

    def test_no_foreign_keys(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
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
        """단순 substring이 아니라 단어 경계로 찾는다 — 이 스키마는
        default_additional_shipping_fee/default_return_risk_reserve
        처럼 "default"로 시작하는 실제 컬럼명이 있어(운영정책의
        "기본값" 의미, SQL DEFAULT 절이 아님) 단순 substring 검사는
        오탐한다."""

        for keyword in ("CHECK", "TRIGGER", "CASCADE"):
            self.assertNotIn(keyword, self.executed_sql.upper())

        self.assertIsNone(
            re.search(r"\bDEFAULT\b", self.executed_sql.upper()),
        )

    # 2026-09-08 추가 — migrations/20260908_00_create_purchase_channel_
    # connection_schema.sql이 purchase_tasks/purchase_records에
    # channel_connection_id를 더했다(V7 item 7, purchase_channel_
    # connections 연결). 이 테스트는 오직 이 Gate PT-1 파일(20260822_01)
    # 하나만 놓고 비교하므로, 그 이후 정당하게 추가된 컬럼은 canonical
    # DDL 문자열에서 제거하고 비교한다(별도 테스트 tests/test_purchase_
    # channel_connection_migration.py가 이 컬럼의 실제 추가를 이미
    # 검증한다). 이 인덱스도 그 신규 Migration이 만들므로 함께 제외한다.
    _LATER_MIGRATION_COLUMN_FRAGMENT = ", channel_connection_id INTEGER"
    _LATER_MIGRATION_INDEX_NAMES = frozenset({
        "ix_purchase_tasks_channel_connection_id",
        "ix_purchase_records_channel_connection_id",
    })

    # 2026-09-10 Phase 10 추가 — migrations/20260910_04_add_purchase_
    # task_candidate_price_baseline.sql이 purchase_task_candidates에
    # expected_amount_at_creation을 더했다(가격 인상 감지 기준값 —
    # Critical 결함 #21 해결). 위와 동일한 이유로 이 컬럼도 canonical
    # DDL 문자열에서 제거하고 비교한다(별도 테스트
    # tests/test_purchase_task_service.py::PriceIncreaseBaselineTestCase
    # 가 이 컬럼의 실제 추가·동작을 이미 검증한다). 인덱스는 만들지
    # 않는 컬럼이라 _LATER_MIGRATION_INDEX_NAMES에는 추가할 것이 없다.
    _LATER_MIGRATION_CANDIDATE_COLUMN_FRAGMENT = (
        ", expected_amount_at_creation FLOAT"
    )

    def test_migration_matches_sqlalchemy_model_ddl(self):

        dialect = sqlite_dialect.dialect()
        canonical_statements = []

        for model_cls in MODEL_TABLE_ORDER:
            table = model_cls.__table__
            normalized = _normalize_sql(
                str(CreateTable(table).compile(dialect=dialect)),
            )
            if table.name in ("purchase_tasks", "purchase_records"):
                normalized = normalized.replace(
                    self._LATER_MIGRATION_COLUMN_FRAGMENT, "",
                )
            if table.name == "purchase_task_candidates":
                normalized = normalized.replace(
                    self._LATER_MIGRATION_CANDIDATE_COLUMN_FRAGMENT, "",
                )
            canonical_statements.append(normalized)
            for index in sorted(table.indexes, key=lambda ix: ix.name):
                if index.name in self._LATER_MIGRATION_INDEX_NAMES:
                    continue
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
        """9번째 테이블(purchase_task_csv_import_logs, Gate PT-2에서
        추가된 10번째 purchase_task_email_provider_settings보다 앞)과
        이름이 겹치는 충돌용 테이블을 미리 만들어 두면, Migration은
        앞의 8개 테이블까지 생성한 뒤 거기서 실패해야 한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE purchase_task_csv_import_logs "
                "(conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            error_message = str(ctx.exception)
            self.assertIn("purchase_task_csv_import_logs", error_message)
            self.assertIn("already exists", error_message)

            conn.rollback()

            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            for table_name in EXPECTED_TABLES - {"purchase_task_csv_import_logs"}:
                self.assertNotIn(
                    table_name, tables,
                    f"실패 전에 생성된 {table_name}가 rollback되지 않았다.",
                )
            self.assertIn("purchase_task_csv_import_logs", tables)

            conflict_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(purchase_task_csv_import_logs)",
                )
            }
            self.assertEqual(conflict_columns, {"conflict_marker"})
        finally:
            conn.close()


class ServiceOnMigratedSchemaTestCase(unittest.TestCase):
    """Base.metadata.create_all이 아니라 실제 Migration SQL을 그대로
    적용한 스키마 위에서 PurchaseTaskService 핵심 흐름이 실제로
    동작하는지 확인한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            migration_sql = f.read()

        conn = sqlite3.connect(path)
        conn.executescript(migration_sql)
        conn.executescript(f"BEGIN;\n{AUDIT_LOGS_DDL};\nCOMMIT;\n")
        # 2026-09-08 추가 — migrations/20260908_00_create_purchase_
        # channel_connection_schema.sql이 purchase_tasks/purchase_records에
        # channel_connection_id를 더했다. 이 테스트는 이 Gate PT-1 파일
        # (20260822_01) 하나만 적용하므로, 현재 Model(그 컬럼을 포함)로
        # 실제 쿼리를 실행하려면 그 ALTER TABLE도 함께 적용해야 한다
        # (그렇지 않으면 SELECT 자체가 "no such column"으로 실패한다).
        conn.executescript(
            "BEGIN;\n"
            "ALTER TABLE purchase_tasks ADD COLUMN channel_connection_id INTEGER;\n"
            "ALTER TABLE purchase_records ADD COLUMN channel_connection_id INTEGER;\n"
            "COMMIT;\n",
        )
        # 2026-09-10 Phase 10 추가 — migrations/20260910_04_add_
        # purchase_task_candidate_price_baseline.sql이 purchase_task_
        # candidates에 expected_amount_at_creation을 더했다. 같은
        # 이유로 여기도 함께 적용해야 현재 Model로 실제 쿼리가
        # 성공한다.
        conn.executescript(
            "BEGIN;\n"
            "ALTER TABLE purchase_task_candidates ADD COLUMN "
            "expected_amount_at_creation FLOAT;\n"
            "COMMIT;\n",
        )
        conn.close()

        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, FundingAccount.__table__,
                FundingLedger.__table__, AutomationModeState.__table__,
                EmergencyStop.__table__, ExecutionLimit.__table__,
                ExecutionUsage.__table__, ExecutionPeriodUsage.__table__,
                Role.__table__, User.__table__, Notification.__table__,
                NotificationRead.__table__,
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

        self.service = PurchaseTaskService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_create_and_add_candidate_on_migrated_schema(self):

        task = self.service.create_task(
            self.company.id, source_order_id=1, source_order_item_id=None,
            product_title="무선이어폰", brand="브랜드A", manufacturer="브랜드A",
            model_name="MODEL-1", gtin="1111111111111", capacity="100ml",
            quantity=1, color_or_scent="블랙", idempotency_key="migrated:1",
        )
        self.assertEqual(task.status, "SEARCH_REQUIRED")

        candidate = self.service.add_candidate(
            task.id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
            product_url="https://search.shopping.naver.com/product/migrated",
            estimated_price=10000, estimated_shipping_fee=3000,
        )
        self.assertIsNotNone(candidate.id)
        self.db.refresh(task)
        self.assertEqual(task.status, "CANDIDATES_READY")


if __name__ == "__main__":
    unittest.main()
