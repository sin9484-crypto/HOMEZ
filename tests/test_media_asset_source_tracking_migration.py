"""
=========================================================
Homez OS

File : tests/test_media_asset_source_tracking_migration.py

2026-08-28 — 이미지 가져오기 출처 추적 Migration
(20260828_01_add_media_asset_source_tracking.sql) 검증.
tests/test_media_asset_public_hosting_migration.py의 원칙을 그대로
따른다: 저장소의 Migration 파일 자체로 "적용되기 직전" 상태를 빈
임시 DB에 재현한 뒤 신선하게 적용해 검증한다. 이 Migration은 아직
실제 운영 DB에 적용하지 않았다 — 그래서 여기서는 "이미 적용됨"이
아니라 "아직 적용 안 됨"을 확인한다(사용자 승인 없는 운영 DB
Migration 금지 원칙 준수).
=========================================================
"""

import hashlib
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support.real_install_gate import requires_real_install_diagnostics
from app.database.migration_runner import MigrationRunner

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"
_TARGET_FILENAME = "20260828_01_add_media_asset_source_tracking.sql"
_OPERATING_DB_PATH = Path(
    r"C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db",
)
_DEV_DB_PATH = Path(r"C:\Users\Daum pc\Homez-OS\homez.db")
_NEW_COLUMNS = {"source_url", "source_domain", "source_classification"}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:

    return {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _sha256(path: Path) -> str:

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class SourceTrackingMigrationFreshApplyTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.db_path = Path(path)

        prior_files = sorted(
            p.name for p in _MIGRATIONS_DIR.glob("*.sql")
            if p.name < _TARGET_FILENAME
        )
        assert prior_files, "선행 Migration 파일을 찾지 못함 — 저장소 상태 이상"

        prior_dir = Path(tempfile.mkdtemp())
        try:
            for fn in prior_files:
                shutil.copy(_MIGRATIONS_DIR / fn, prior_dir / fn)
            prior_runner = MigrationRunner(cls.db_path, prior_dir)
            conn = sqlite3.connect(str(cls.db_path))
            try:
                applied_prior = prior_runner.apply_pending(conn)
            finally:
                conn.close()
            assert applied_prior == prior_files, (
                "기준선 적용 결과가 선행 파일 목록과 다름 — 검증 전제 무효"
            )
        finally:
            shutil.rmtree(prior_dir, ignore_errors=True)

        conn = sqlite3.connect(str(cls.db_path))
        try:
            conn.execute(
                "INSERT INTO media_assets (id, company_id, owner_type, "
                "owner_id, asset_role, purpose, display_order, "
                "storage_path, mime_type, file_size_bytes, sha256_hex, "
                "status, rights_status, created_at) VALUES "
                "(1, 1, 'PRODUCT_CANDIDATE', 1, 'ORIGINAL', 'MAIN', 0, "
                "'rehearsal/seed.jpg', 'image/jpeg', 100, "
                "hex(randomblob(32)), 'ACTIVE', 'RIGHTS_UNVERIFIED', "
                "'2026-08-28T00:00:00+00:00')",
            )
            conn.commit()
        finally:
            conn.close()

        cls.isolated_migrations_dir = Path(tempfile.mkdtemp())
        shutil.copy(
            _MIGRATIONS_DIR / _TARGET_FILENAME,
            cls.isolated_migrations_dir / _TARGET_FILENAME,
        )

        conn = sqlite3.connect(str(cls.db_path))
        try:
            cls.media_assets_columns_before = _columns(conn, "media_assets")
            cls.schema_migrations_before = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations",
            ).fetchone()[0]
        finally:
            conn.close()

        cls.runner = MigrationRunner(
            cls.db_path, cls.isolated_migrations_dir,
        )
        conn = sqlite3.connect(str(cls.db_path))
        try:
            cls.applied = cls.runner.apply_pending(conn)
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):

        if cls.db_path.exists():
            os.remove(cls.db_path)
        shutil.rmtree(cls.isolated_migrations_dir, ignore_errors=True)

    def test_target_file_applied(self):

        self.assertEqual(self.applied, [_TARGET_FILENAME])

    def test_integrity_and_foreign_key_check_pass(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.assertEqual(
                conn.execute("PRAGMA integrity_check").fetchone()[0], "ok",
            )
            self.assertEqual(
                conn.execute("PRAGMA foreign_key_check").fetchall(), [],
            )
        finally:
            conn.close()

    def test_exactly_three_new_columns_added(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            columns_after = _columns(conn, "media_assets")
        finally:
            conn.close()

        self.assertEqual(
            columns_after - self.media_assets_columns_before, _NEW_COLUMNS,
        )

    def test_schema_migrations_gained_exactly_one_row(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            after = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations",
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(after - self.schema_migrations_before, 1)

    def test_seeded_row_gets_null_source_url_and_default_classification(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT source_url, source_domain, source_classification "
                "FROM media_assets WHERE id = 1",
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertEqual(row[2], "UNKNOWN")

    def test_source_domain_index_created(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            indexes = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'",
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn("ix_media_assets_source_domain", indexes)

    def test_diagnose_reports_target_as_already_applied(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            diagnosis = self.runner.diagnose(conn)
        finally:
            conn.close()

        self.assertEqual(diagnosis["pending"], [])
        self.assertIn(_TARGET_FILENAME, diagnosis["already_applied"])


class RealDbMigrationApprovalDriftTestCase(unittest.TestCase):
    """2026-08-30 V7 후속 안정화 — 운영 DB 감사 로그(id=5,6, 2026-08-29
    T05:07:09Z)로 이 Migration이 사용자 승인 절차를 거쳐 실제로
    적용된 사실을 확인했다("영원히 미적용"을 강제하던 이전 버전의
    assertFalse는 이제 거짓이다 — 실패 재현 확인됨). 이제 지켜야
    하는 불변식은 "새 컬럼이 있다면 반드시 승인 감사로그가 함께
    있어야 한다"이다. 이 테스트 자신은 어떤 DB에도 쓰기를 하지
    않는다(호출 전후 해시 불변 확인)."""

    # 실제 설치환경 진단 — 기본 전체 회귀에서 제외(opt-in: HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1)
    @requires_real_install_diagnostics
    def test_new_columns_present_only_with_approved_audit_log(self):

        for path in (_OPERATING_DB_PATH, _DEV_DB_PATH):
            if not path.exists():
                continue
            hash_before = _sha256(path)
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                conn.execute("PRAGMA query_only = ON")
                columns = _columns(conn, "media_assets")
                if _NEW_COLUMNS & columns:
                    has_audit_table = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='audit_logs'",
                    ).fetchone()
                    self.assertIsNotNone(
                        has_audit_table,
                        f"{path}에 새 컬럼은 있지만 audit_logs 테이블이 "
                        "없어 승인 경로를 확인할 수 없습니다.",
                    )
                    approved_count = conn.execute(
                        "SELECT COUNT(*) FROM audit_logs WHERE "
                        "action='MIGRATION_APPLIED' AND description LIKE ?",
                        (f"%{_TARGET_FILENAME}%",),
                    ).fetchone()[0]
                    self.assertGreater(
                        approved_count, 0,
                        f"{path}에 새 컬럼이 있지만 이 Migration의 "
                        "MIGRATION_APPLIED 승인 감사 로그가 없습니다 — "
                        "승인 없이 적용된 흔적일 수 있습니다.",
                    )
            finally:
                conn.close()
            hash_after = _sha256(path)
            self.assertEqual(hash_before, hash_after)


if __name__ == "__main__":
    unittest.main()
