"""
=========================================================
Homez OS

File : tests/test_db_logical_compare.py

`tools/db_logical_compare.py`(원본↔백업 사본 논리 대조) 집중 테스트(2026-10-06, 68차). 임시 SQLite
파일만 쓰며 실제 DB는 열지 않는다. 핵심: 바이트가 달라도 논리가 같으면 같다고 하고, 바이트 해시가
같아 보이는 상황과 무관하게 스키마·Migration 이력·행 내용·논리 참조 차이를 잡는다.
=========================================================
"""

import hashlib
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("db_logical_compare", ROOT / "tools" / "db_logical_compare.py")
cmp = importlib.util.module_from_spec(_spec)
sys.modules["db_logical_compare"] = cmp
_spec.loader.exec_module(cmp)

REFS = (("child", "parent_id", "parent", "id"),)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LogicalCompareTestCase(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="homez_dbcmp_"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.src = self.dir / "src.db"
        conn = sqlite3.connect(self.src)
        conn.executescript("""
            CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY, filename TEXT, checksum TEXT);
            CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER, note TEXT);
            CREATE TABLE noise (id INTEGER PRIMARY KEY, blob TEXT);
            INSERT INTO schema_migrations (filename, checksum) VALUES ('001.sql','aaa'),('002.sql','bbb');
            INSERT INTO parent VALUES (1,'p1'),(2,'p2');
            INSERT INTO child VALUES (1,1,'a'),(2,2,'b');
        """)
        # 빈 페이지(free list)를 만들어 파일 바이트가 사본과 반드시 달라지게 한다
        conn.executemany("INSERT INTO noise (blob) VALUES (?)", [("x" * 200,)] * 800)
        conn.commit()
        conn.execute("DELETE FROM noise")
        conn.commit()
        conn.close()

    def _copy_with(self, name, sql=None, method="vacuum"):
        dst = self.dir / name
        src = sqlite3.connect(self.src)
        if method == "vacuum":
            src.execute(f"VACUUM INTO '{dst.as_posix()}'")
        else:
            out = sqlite3.connect(dst)
            src.backup(out)
            out.close()
        src.close()
        if sql:
            conn = sqlite3.connect(dst)
            conn.executescript(sql)
            conn.commit()
            conn.close()
        return dst

    def test_same_data_with_different_bytes_is_logically_equal(self):
        dst = self._copy_with("vacuumed.db")
        self.assertNotEqual(_sha(self.src), _sha(dst), "전제: 바이트 해시는 달라야 이 시험이 의미 있다")
        result = cmp.compare(self.src, dst, REFS)
        self.assertTrue(result.equal, result.differences)
        backup_api = self._copy_with("backup_api.db", method="backup")
        self.assertTrue(cmp.compare(self.src, backup_api, REFS).equal)

    def test_a_single_changed_cell_is_detected_even_with_identical_row_counts(self):
        dst = self._copy_with("changed.db", "UPDATE child SET note='CHANGED' WHERE id=2;")
        result = cmp.compare(self.src, dst, REFS)
        self.assertFalse(result.equal)
        self.assertTrue(any("child" in d and "내용 지문" in d for d in result.differences), result.differences)

    def test_missing_or_extra_rows_and_tables_are_detected(self):
        missing_row = self._copy_with("missing.db", "DELETE FROM parent WHERE id=2;")
        self.assertTrue(any("parent" in d and "행 수" in d for d in cmp.compare(self.src, missing_row, REFS).differences))
        missing_table = self._copy_with("notable.db", "DROP TABLE noise;")
        self.assertTrue(any("noise" in d and "없음" in d for d in cmp.compare(self.src, missing_table, REFS).differences))

    def test_schema_and_migration_history_differences_are_detected(self):
        schema = self._copy_with("schema.db", "CREATE INDEX ix_child_note ON child (note);")
        self.assertTrue(any("스키마" in d for d in cmp.compare(self.src, schema, REFS).differences))
        history = self._copy_with("hist.db", "UPDATE schema_migrations SET checksum='zzz' WHERE filename='002.sql';")
        diffs = cmp.compare(self.src, history, REFS).differences
        self.assertTrue(any("Migration 이력" in d for d in diffs), diffs)
        dropped = self._copy_with("hist2.db", "DELETE FROM schema_migrations WHERE filename='002.sql';")
        self.assertTrue(any("Migration 이력" in d for d in cmp.compare(self.src, dropped, REFS).differences))

    def test_orphan_rows_in_logical_references_are_counted_and_compared(self):
        # 행 수는 같게 유지하면서 참조만 끊는다: child.parent_id를 존재하지 않는 부모로 바꾼다
        dst = self._copy_with("orphan.db", "UPDATE child SET parent_id=99 WHERE id=1;")
        diffs = cmp.compare(self.src, dst, REFS).differences
        self.assertTrue(any("고아 행 수" in d for d in diffs), diffs)

    def test_report_contains_counts_and_digests_but_no_cell_values(self):
        conn = sqlite3.connect(self.src)
        conn.execute("INSERT INTO parent VALUES (3, 'SECRET-LOOKING-VALUE-123')")
        conn.commit(); conn.close()
        dst = self._copy_with("copy2.db")
        result = cmp.compare(self.src, dst, REFS)
        text = json.dumps(result.report(), ensure_ascii=False)
        self.assertNotIn("SECRET-LOOKING-VALUE-123", text)
        self.assertIn("total_rows", text)

    def test_databases_are_opened_read_only_and_never_modified(self):
        before = (_sha(self.src), self.src.stat().st_mtime_ns)
        dst = self._copy_with("ro.db")
        after_dst = _sha(dst)
        cmp.compare(self.src, dst, REFS)
        self.assertEqual((_sha(self.src), self.src.stat().st_mtime_ns), before)
        self.assertEqual(_sha(dst), after_dst)
        conn = cmp.open_readonly(self.src)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO parent VALUES (9,'x')")
        conn.close()

    def test_corrupt_target_is_reported_not_silently_accepted(self):
        bad = self.dir / "bad.db"
        bad.write_bytes(self.src.read_bytes()[:4096] + b"\0" * 8192)   # 잘리고 오염된 파일
        with self.assertRaises(sqlite3.DatabaseError):
            result = cmp.compare(self.src, bad, REFS)
            if not result.equal:
                raise sqlite3.DatabaseError("not equal")


if __name__ == "__main__":
    unittest.main()
