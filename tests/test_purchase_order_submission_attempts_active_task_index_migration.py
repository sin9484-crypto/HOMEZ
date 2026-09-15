"""
=========================================================
Homez OS

File : tests/test_purchase_order_submission_attempts_active_task_index_migration.py

2026-09-15 전면 감사 후속(Phase 3, 업무 주문 단위 중복 방지 강화) —
migrations/20260915_01_add_purchase_order_submission_attempts_active_
task_index.sql 검증. 임시 SQLite 파일에 저장소의 실제 Migration
전체를(이 신규 파일 포함) 순서대로 재생시켜, 처음부터 끝까지 클린
적용이 가능한지·재적용이 명시적으로 실패하는지·부분 UNIQUE INDEX가
실제로 "같은 purchase_task_id에 대한 동시 다른 idempotency_key
요청"을 DB 레벨에서 거부하는지, 반대로 정당한 케이스(다른 작업,
또는 이전 시도가 종결된 뒤의 새 시도)는 막지 않는지 확인한다.
실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import MigrationRunner

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = (
    "20260915_01_add_purchase_order_submission_attempts_active_task_index.sql"
)

_INSERT_SQL = (
    "INSERT INTO purchase_order_submission_attempts ("
    "company_id, connection_id, purchase_task_id, idempotency_key, "
    "mall_code, product_code, options_json, status, unknown_resolution_status, "
    "started_at, created_at, updated_at"
    ") VALUES (?, ?, ?, ?, 'ONCHANNEL', 'CH1', '[]', ?, ?, "
    "'2026-09-15T00:00:00', '2026-09-15T00:00:00', '2026-09-15T00:00:00')"
)


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
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_violations, [])

            indexes = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='purchase_order_submission_attempts'",
                ).fetchall()
            }
            self.assertIn(
                "uq_purchase_order_submission_attempts_active_task", indexes,
            )
        finally:
            conn.close()

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


class ActiveTaskUniqueIndexBehaviorTestCase(unittest.TestCase):
    """이 인덱스가 실제로 강제하는 규칙을 직접 재현한다 — DB 파일
    하나를 서로 다른 sqlite3 커넥션 두 개로 열어, 애플리케이션 레벨
    사전 검사(_has_blocking_task_attempt)를 거치지 않고 곧바로 두
    번째 INSERT를 시도했을 때도 DB 자체가 거부하는지 확인한다(이것이
    바로 "동시 요청 두 개가 동시에 아직 없음을 관측하는" TOCTOU
    경쟁 상태의 최종 방어선이다)."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = path
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)
        conn = sqlite3.connect(self.db_path)
        try:
            runner.apply_pending(conn)
        finally:
            conn.close()

    def test_second_pending_attempt_for_same_task_with_different_key_rejected(self):
        """같은 purchase_task_id, 다른 idempotency_key(예: 옵션이
        다른 요청) — (company_id, idempotency_key) UNIQUE로는 막을 수
        없는 케이스지만, 이 부분 인덱스가 대신 막아야 한다."""

        conn1 = sqlite3.connect(self.db_path)
        conn2 = sqlite3.connect(self.db_path)
        try:
            conn1.execute(
                _INSERT_SQL, (1, 4, 100, "key-a", "PENDING", "UNRESOLVED"),
            )
            conn1.commit()

            with self.assertRaises(sqlite3.IntegrityError) as ctx:
                conn2.execute(
                    _INSERT_SQL, (1, 4, 100, "key-b", "PENDING", "UNRESOLVED"),
                )
                conn2.commit()
            self.assertIn("purchase_task_id", str(ctx.exception))
        finally:
            conn1.close()
            conn2.close()

    def test_different_purchase_task_ids_not_blocked(self):
        """다른 업무 주문이면 동시에 PENDING이어도 정당하다 — 이
        인덱스가 회사 전체를 잠그지 않는다는 것을 확인한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                _INSERT_SQL, (1, 4, 100, "key-a", "PENDING", "UNRESOLVED"),
            )
            conn.execute(
                _INSERT_SQL, (1, 4, 101, "key-b", "PENDING", "UNRESOLVED"),
            )
            conn.commit()
        finally:
            conn.close()

    def test_new_attempt_allowed_after_prior_attempt_rejected(self):
        """이전 시도가 REJECTED(종결, 차단 대상 아님)로 끝났다면 같은
        purchase_task_id에 새 PENDING 시도를 만드는 것은 정당하다 —
        이 인덱스가 영구 잠금이 되어서는 안 된다."""

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                _INSERT_SQL, (1, 4, 100, "key-a", "REJECTED", "UNRESOLVED"),
            )
            conn.commit()
            conn.execute(
                _INSERT_SQL, (1, 4, 100, "key-b", "PENDING", "UNRESOLVED"),
            )
            conn.commit()
        finally:
            conn.close()

    def test_new_attempt_allowed_after_unknown_resolved_as_not_confirmed(self):
        """RESULT_UNKNOWN이었지만 사람이 ORDER_NOT_CONFIRMED로 확정한
        뒤에는 같은 작업에 새 시도가 가능해야 한다 — resolve_unknown_
        attempt()가 실제로 쓰는 상태 조합과 정확히 같은 값으로
        검증한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                _INSERT_SQL,
                (1, 4, 100, "key-a", "RESULT_UNKNOWN", "ORDER_NOT_CONFIRMED"),
            )
            conn.commit()
            conn.execute(
                _INSERT_SQL, (1, 4, 100, "key-b", "PENDING", "UNRESOLVED"),
            )
            conn.commit()
        finally:
            conn.close()

    def test_unresolved_unknown_still_blocks_new_attempt(self):
        """RESULT_UNKNOWN인데 아직 아무도 확정하지 않았다면(기본값
        UNRESOLVED) 여전히 차단 대상이어야 한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                _INSERT_SQL,
                (1, 4, 100, "key-a", "RESULT_UNKNOWN", "UNRESOLVED"),
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    _INSERT_SQL, (1, 4, 100, "key-b", "PENDING", "UNRESOLVED"),
                )
                conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
