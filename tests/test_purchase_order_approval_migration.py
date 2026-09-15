"""
=========================================================
Homez OS

File : tests/test_purchase_order_approval_migration.py

2026-09-11 후속(반자동 완료 라운드, Phase 5·7) —
migrations/20260911_00_create_purchase_order_approval_schema.sql
검증. 임시 SQLite 파일에 저장소의 실제 Migration 전체를(이 신규
파일 포함) 순서대로 재생시켜, 처음부터 끝까지 클린 적용이 가능한지·
재적용이 명시적으로 실패하는지·Model과 DDL이 일치하는지·
(company_id, connection_id, purchase_task_id) UNIQUE 제약이 실제로
동작하는지·기존 purchase_task_policy_settings 행이 보존되는지
확인한다. 실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import os
import re
import shutil
import sqlite3
import tempfile
import unittest

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from app.database.migration_runner import MigrationRunner
from app.domains.purchase_task.model import PurchaseOrderApproval

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = "20260911_00_create_purchase_order_approval_schema.sql"


def _db_columns(conn: sqlite3.Connection, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


class FullChainReplayTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = path
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        self.assertTrue(
            os.path.exists(os.path.join(MIGRATIONS_DIR, NEW_MIGRATION)),
            f"{NEW_MIGRATION} 파일이 없습니다.",
        )
        self.runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)

    def test_full_migration_history_applies_cleanly_including_new_file(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("purchase_order_approvals", tables)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_new_table_matches_model_exactly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "purchase_order_approvals")
        finally:
            conn.close()

        self.assertEqual(
            cols, _model_columns(PurchaseOrderApproval),
            "purchase_order_approvals: DB 컬럼과 Model이 다릅니다.",
        )

    def test_policy_settings_gains_two_new_nullable_columns(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "purchase_task_policy_settings")
        finally:
            conn.close()

        self.assertIn("min_residual_points", cols)
        self.assertIn("order_approval_validity_minutes", cols)
        self.assertTrue(cols["min_residual_points"], "nullable이어야 한다.")
        self.assertTrue(cols["order_approval_validity_minutes"], "nullable이어야 한다.")

    def test_existing_policy_setting_rows_preserved(self):
        """기존 purchase_task_policy_settings 행이 있어도 이번
        ALTER TABLE ADD COLUMN이 데이터를 보존하는지 확인한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            # 신규 Migration 직전까지만 적용(기존 데이터 시뮬레이션).
            # 2026-09-11 정정 — 원래 "이 파일 하나만 제외하면 된다"는
            # 가정(이 파일이 저장소에서 사전순으로 가장 마지막이라는
            # 전제)이었으나, 이후 20260911_01이 추가되면서 그 전제가
            # 깨져 OrderInversionError로 실패했다(다른 3개 테스트
            # 파일에서 반복된 것과 동일한 클래스의 결함). "이 파일
            # 하나"가 아니라 "이 파일 이후 사전순으로 모든 파일"을
            # 제외하도록 자동 계산 방식으로 바꿔, 앞으로 새 Migration이
            # 추가돼도 이 테스트가 매번 깨지지 않게 한다.
            all_files = sorted(
                f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")
            )
            self.assertIn(NEW_MIGRATION, all_files)
            prior_only_dir = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, prior_only_dir, True)
            for name in all_files:
                if name >= NEW_MIGRATION:
                    continue
                shutil.copyfile(
                    os.path.join(MIGRATIONS_DIR, name),
                    os.path.join(prior_only_dir, name),
                )
            prior_runner = MigrationRunner(self.db_path, prior_only_dir)
            prior_runner.apply_pending(conn)

            conn.execute(
                "INSERT INTO purchase_task_policy_settings ("
                "company_id, min_net_profit, min_margin_rate, "
                "max_price_increase_rate, require_return_allowed, "
                "min_match_confidence, budget_reservation_hours, "
                "default_additional_shipping_fee, default_return_risk_reserve, "
                "created_at, updated_at"
                ") VALUES (1, 0, 0, 0.05, 1, 0.98, 24, 0, 0, "
                "'2026-09-01T00:00:00', '2026-09-01T00:00:00')",
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            row = conn.execute(
                "SELECT company_id, min_residual_points, "
                "order_approval_validity_minutes "
                "FROM purchase_task_policy_settings WHERE company_id = 1",
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "기존 행이 사라지면 안 된다.")
        self.assertEqual(row[0], 1)
        self.assertIsNone(row[1], "신규 컬럼은 기존 행에서 NULL이어야 한다(0으로 채우지 않음).")
        self.assertIsNone(row[2])

    def test_reapplying_full_chain_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            second_run = self.runner.apply_pending(conn)
            self.assertEqual(len(second_run or []), 0)
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            with open(
                os.path.join(MIGRATIONS_DIR, NEW_MIGRATION), encoding="utf-8",
            ) as f:
                sql_text = f.read()
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(sql_text)
        finally:
            conn.close()

    def test_duplicate_task_within_same_connection_rejected_by_db(self):
        """이 테이블의 핵심 — (company_id, connection_id,
        purchase_task_id) UNIQUE 제약이 같은 작업에 대한 승인 행이
        둘 이상 생기지 않도록 스키마 레벨에서 보증한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            conn.execute(
                "INSERT INTO purchase_order_approvals ("
                "company_id, connection_id, purchase_task_id, product_code, "
                "status, shipping_cost_is_free_confirmed, "
                "created_at, updated_at"
                ") VALUES (1, 4, 100, 'CH1', "
                "'PENDING_SHIPPING_COST', 0, "
                "'2026-09-11T00:00:00', '2026-09-11T00:00:00')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO purchase_order_approvals ("
                    "company_id, connection_id, purchase_task_id, product_code, "
                    "status, shipping_cost_is_free_confirmed, "
                    "created_at, updated_at"
                    ") VALUES (1, 4, 100, 'CH1', "
                    "'PENDING_SHIPPING_COST', 0, "
                    "'2026-09-11T00:00:01', '2026-09-11T00:00:01')",
                )
        finally:
            conn.close()


class ModelDdlCanonicalDiffTestCase(unittest.TestCase):
    """homez-migration-safety 원칙 — SQLAlchemy CreateTable을 sqlite
    dialect로 컴파일한 canonical DDL과 이 Migration 파일 안의 CREATE
    TABLE 문을 공백만 정규화해 비교한다.

    2026-09-15 전면 감사 후속 — Model은 항상 "현재" 전체 스키마를
    반영하므로, 이 CREATE TABLE 이후에 추가된 ALTER TABLE ADD COLUMN
    Migration(예: 20260915_00의 options_snapshot_json)이 생기면 이
    테스트가 매번 깨진다(다른 4개 Migration 테스트 파일에서 반복된
    것과 동일한 클래스의 결함). NEW_MIGRATION보다 사전순으로 뒤인
    Migration 파일들에서 이 테이블에 대한 "ADD COLUMN"을 자동으로
    찾아, 그 컬럼들을 canonical DDL 생성 시 제외한다 — 손으로
    컬럼명을 나열하지 않는다."""

    _ADD_COLUMN_RE = re.compile(
        r"ALTER\s+TABLE\s+purchase_order_approvals\s+ADD\s+COLUMN\s+"
        r"(\w+)",
        re.IGNORECASE,
    )

    @staticmethod
    def _normalize(sql: str) -> str:

        return " ".join(sql.split()).rstrip(";").strip()

    @classmethod
    def _columns_added_by_later_migrations(cls) -> set[str]:

        added: set[str] = set()
        for name in sorted(os.listdir(MIGRATIONS_DIR)):
            if not name.endswith(".sql") or name <= NEW_MIGRATION:
                continue
            with open(
                os.path.join(MIGRATIONS_DIR, name), encoding="utf-8",
            ) as f:
                added.update(cls._ADD_COLUMN_RE.findall(f.read()))
        return added

    @classmethod
    def _strip_columns(cls, normalized_ddl: str, column_names: set[str]) -> str:
        """정규화된(공백 한 칸으로 축약된) CREATE TABLE 문자열에서
        지정한 컬럼 정의(", 이름 타입") 구간만 제거한다. 이 Migration
        이후에 ADD COLUMN으로 추가된, 제약 없는 단순 컬럼에만
        사용하므로 "이름 다음 토큰까지"만 제거해도 충분하다."""

        result = normalized_ddl
        for name in column_names:
            result = re.sub(
                rf",\s*{re.escape(name)}\s+\w+(?:\s+NOT\s+NULL)?",
                "", result, count=1,
            )
        return result

    def test_table_ddl_matches_model(self):

        with open(
            os.path.join(MIGRATIONS_DIR, NEW_MIGRATION), encoding="utf-8",
        ) as f:
            migration_sql = f.read()

        added_later = self._columns_added_by_later_migrations()
        canonical = str(
            CreateTable(PurchaseOrderApproval.__table__).compile(
                dialect=sqlite_dialect.dialect(),
            ),
        )
        canonical_as_of_creation = self._strip_columns(
            self._normalize(canonical), added_later,
        )

        start = migration_sql.index(
            "CREATE TABLE purchase_order_approvals (",
        )
        end = migration_sql.index(");", start) + 1
        actual = migration_sql[start:end]

        self.assertEqual(self._normalize(actual), canonical_as_of_creation)


if __name__ == "__main__":
    unittest.main()
