"""
=========================================================
Homez OS

File : tests/test_notification_delivery_migration.py

Gate PT-3(2026-08-23 17차 지시) 검증 —
migrations/20260823_00_create_notification_delivery_schema.sql 정적
검증 + 임시 SQLite 파일 적용/재적용실패/rollback/중간실패rollback +
Model↔DDL 자동 드리프트 감지. 실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import hashlib
import os
import re
import sqlite3
import tempfile
import unittest

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from tests.support.real_install_gate import (
    real_install_diagnostics_enabled,
    requires_real_install_diagnostics,
)
from app.domains.notification_center.model import NotificationEmailLog
from app.domains.notification_center.model import NotificationEventPreference
from app.domains.notification_center.model import NotificationPreference

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations", "20260823_00_create_notification_delivery_schema.sql",
)

EXPECTED_TABLES = {
    "notification_email_logs", "notification_preferences",
    "notification_event_preferences",
}

MODEL_TABLE_ORDER = [
    NotificationEmailLog, NotificationPreference, NotificationEventPreference,
]


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


# 실제 homez.db가 이 회귀 실행 도중 어떤 테스트로도 변경되지 않는지
# 확인하기 위한 self-calibrating 기준선(2026-09-09 Phase 0 재설계).
#
# 이전 설계는 SHA-256 값을 코드에 하드코딩해두고, 승인된 실제 변경
# (Migration 적용, 사용자 승인 하 데이터 수정 등)이 있을 때마다 그
# 상수를 손으로 최신값으로 갱신하는 방식이었다 — 이 파일의 changelog
# 주석에 2026-08-27/09-07/09-08(2회)/09-09까지 다섯 번의 수동 갱신
# 이력이 남아있었다. 이 방식은 구조적으로 두 가지 결함이 있었다:
# (1) 정당한 변경이 있을 때마다 사람이 개입해 상수를 고쳐야만 통과
#     하는 테스트라 유지비용이 계속 든다.
# (2) 더 중요하게, "하드코딩된 값을 지금 값으로 바꿔치기"하는 수정이
#     "실제로 아무도 부당하게 건드리지 않았다"는 검증과 구분되지
#     않는다 — 값을 갱신하는 행위 자체가 이 테스트가 원래 잡아야 할
#     문제(회귀 도중 실제 DB가 조용히 바뀌는 것)를 가릴 수 있다.
#
# 이제는 하드코딩된 값 대신, 이 테스트 모듈이 import되는 시점(=
# unittest discover가 전체 스위트를 수집하는 시점, 즉 어떤 테스트도
# 아직 실행되지 않은 시점)에 실제 DB 해시를 직접 계산해 기준선으로
# 삼는다. 그 뒤 테스트 메서드가 "지금" 다시 계산한 해시와 이 기준선을
# 비교한다 — 이 값은 매 실행마다 그 실행 시작 시점의 실제 상태로
# 스스로 다시 맞춰지므로, 정당한 변경 후 사람이 상수를 고칠 필요가
# 없다. 이 테스트가 여전히 잡아내는 것은 정확히 하나뿐이다: "이
# 회귀가 시작된 뒤부터 끝날 때까지, 그 실행 자체가 실제 homez.db를
# 건드렸는가."
REAL_DB_PATHS = (
    r"C:\Users\Daum pc\Homez-OS\homez.db",
)


def _hash_file(path: str) -> str:

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _capture_real_db_hashes() -> dict[str, str]:

    return {
        path: _hash_file(path)
        for path in REAL_DB_PATHS
        if os.path.exists(path)
    }


# import(=unittest 수집) 시점에 실제 DB 파일을 읽지 않는다 — 실제 설치환경 진단을 명시적으로
# 켠 실행(HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1)에서만 기준 해시를 계산한다.
REAL_DB_BASELINE_HASHES_AT_MODULE_IMPORT = (
    _capture_real_db_hashes() if real_install_diagnostics_enabled() else {}
)


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

    def test_exactly_three_create_table_statements(self):

        creates = re.findall(r"^CREATE TABLE (\w+)", self.executed_sql, re.M)
        self.assertEqual(len(creates), 3)
        self.assertEqual(set(creates), EXPECTED_TABLES)

    def test_no_create_table_if_not_exists(self):

        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)

    def test_no_foreign_keys(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertIsNone(re.search(r"\bREFERENCES\b", self.executed_sql.upper()))

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

        for keyword in ("CHECK", "TRIGGER", "CASCADE", "DEFAULT"):
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
                    _normalize_sql(str(CreateIndex(index).compile(dialect=dialect))),
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
        """3번째 테이블(notification_event_preferences)과 이름이
        겹치는 충돌용 테이블을 미리 만들어 두면, Migration은 앞의
        2개 테이블까지 생성한 뒤 거기서 실패해야 한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE notification_event_preferences "
                "(conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            error_message = str(ctx.exception)
            self.assertIn("notification_event_preferences", error_message)
            self.assertIn("already exists", error_message)

            conn.rollback()

            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            for table_name in EXPECTED_TABLES - {"notification_event_preferences"}:
                self.assertNotIn(
                    table_name, tables,
                    f"실패 전에 생성된 {table_name}가 rollback되지 않았다.",
                )
            self.assertIn("notification_event_preferences", tables)

            conflict_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(notification_event_preferences)",
                )
            }
            self.assertEqual(conflict_columns, {"conflict_marker"})
        finally:
            conn.close()


# 실제 설치환경 진단 — 기본 전체 회귀에서 제외(opt-in: HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1)
@requires_real_install_diagnostics
class RealHomezDbUntouchedTestCase(unittest.TestCase):
    """이 Migration은 실제 DB 어디에도 적용하지 않는다 — 이 회귀
    실행이 시작된 시점(이 모듈이 import된 시점)의 실제 homez.db
    해시와, 지금(이 테스트가 실행되는 시점) 해시가 같은지 확인한다.

    2026-09-09 Phase 0 재설계: 과거에는 SHA-256 값을 코드에 하드코딩
    해두고 정당한 변경이 있을 때마다 사람이 손으로 그 상수를 최신값
    으로 갱신했다(이 changelog에 다섯 차례의 수동 갱신 이력이 있었다).
    "고정값을 현재 값으로 교체"하는 이 유지보수 패턴 자체가, 이
    테스트가 원래 잡아야 할 문제(회귀 도중 실제 DB가 조용히 바뀌는
    것)를 가릴 수 있는 구조적 결함이었다. 지금은 모듈 상단의
    `REAL_DB_BASELINE_HASHES_AT_MODULE_IMPORT`가 이 파일이 import되는
    시점(=어떤 테스트도 아직 실행되지 않은 시점)에 스스로 계산한
    값이므로, 이 테스트는 "회귀 실행 도중 실제 homez.db가 바뀌었는가"
    만 순수하게 검증하며 더 이상 하드코딩된 값을 손으로 갱신할 필요가
    없다."""

    def test_real_db_hash_unchanged_after_module_tests(self):

        baseline = REAL_DB_BASELINE_HASHES_AT_MODULE_IMPORT

        for path, expected_hash in baseline.items():
            if not os.path.exists(path):
                continue
            self.assertEqual(
                _hash_file(path), expected_hash,
                f"{path}의 SHA-256이 이 회귀 실행 시작 시점과 달라졌다 "
                "— 이 실행 도중 어떤 테스트가 실제 DB를 변경했을 "
                "가능성이 있다.",
            )


if __name__ == "__main__":
    unittest.main()
