"""
=========================================================
Homez OS

File : tests/test_media_asset_public_hosting_migration.py

2026-08-27 — Migration 38(20260827_01_add_media_asset_public_hosting.sql)
검증. tests/test_v7_pre_live_migration_rehearsal.py의 2026-08-24
재설계 원칙을 그대로 따른다: 실제 운영 DB의 "현재" pending 목록에
의존하지 않는다(이 Migration은 이미 실제 운영 DB에 정식 승인 절차로
적용되어 더 이상 pending이 아니다 — 재적용을 가정하는 테스트는
그 자체로 깨진다). 대신 저장소의 Migration 파일 자체로 "이 Migration이
적용되기 직전" 상태를 빈 임시 DB에 결정론적으로 재현한 뒤, 그 위에서
신선하게 적용해 검증한다. 실제 운영 DB는 오직 읽기 전용 연결로
"이미 정상 적용되어 있고 무결하다"만 별도로 확인한다.
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
_TARGET_FILENAME = "20260827_01_add_media_asset_public_hosting.sql"
_OPERATING_DB_PATH = Path(
    r"C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db",
)
_NEW_COLUMNS = {
    "public_url", "public_url_provider", "public_url_uploaded_at",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:

    return {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _sha256(path: Path) -> str:

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class PublicHostingMigrationFreshApplyTestCase(unittest.TestCase):
    """저장소의 Migration 파일 자체로 "이 Migration이 적용되기 직전"
    상태를 빈 임시 DB에 재현한 뒤, 신선하게 적용해 검증한다. 실제
    운영 DB의 현재 이력과 무관하게 항상 동일하게 재현 가능하다."""

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

        # 검증을 의미 있게 만들기 위한 최소 대표 행(리허설 전용 값 —
        # 실제 업무 데이터 아님).
        conn = sqlite3.connect(str(cls.db_path))
        try:
            conn.execute(
                "INSERT INTO media_assets (id, company_id, owner_type, "
                "owner_id, asset_role, purpose, display_order, "
                "storage_path, mime_type, file_size_bytes, sha256_hex, "
                "status, rights_status, created_at) VALUES "
                "(1, 1, 'PRODUCT_CANDIDATE', 1, 'ORIGINAL', 'MAIN', 0, "
                "'rehearsal/seed.jpg', 'image/jpeg', 100, "
                "hex(randomblob(32)), 'ACTIVE', 'VERIFIED', "
                "'2026-08-27T00:00:00+00:00')",
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

    def test_seeded_row_gets_null_public_url(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            value = conn.execute(
                "SELECT public_url FROM media_assets WHERE id = 1",
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertIsNone(
            value, "ALTER TABLE ADD COLUMN인데 기존 행에 값이 채워짐 — 예상 밖",
        )

    def test_diagnose_reports_target_as_already_applied(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            diagnosis = self.runner.diagnose(conn)
        finally:
            conn.close()

        self.assertEqual(diagnosis["pending"], [])
        self.assertIn(_TARGET_FILENAME, diagnosis["already_applied"])


# 실제 설치환경 진단 — 기본 전체 회귀에서 제외(opt-in: HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1)
@requires_real_install_diagnostics
class OperatingDbAlreadyAppliedInvariantTestCase(unittest.TestCase):
    """실제 운영 DB는 이 파일 어디에서도 복사·쓰기 대상이 아니다 —
    오직 읽기 전용 연결로 "이 Migration이 이미 정상 적용되어 있고
    무결하다"만 확인하고, 그 전후로 파일 해시가 그대로인지
    재확인한다."""

    def test_operating_db_has_migration_38_applied_and_hash_unchanged(self):

        if not _OPERATING_DB_PATH.exists():
            self.skipTest("이 환경에 실제 운영 homez.db가 없습니다.")

        hash_before = _sha256(_OPERATING_DB_PATH)

        conn = sqlite3.connect(
            f"file:{_OPERATING_DB_PATH}?mode=ro", uri=True,
        )
        try:
            conn.execute("PRAGMA query_only = ON")
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            applied_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE status IN ('APPLIED', 'BACKFILLED')",
            ).fetchone()[0]
            # 이 시점 기준값 — 더 늘어나는 것은 정상, 그 이하로 줄어드는
            # 것만 이상 신호다.
            self.assertGreaterEqual(applied_count, 38)

            columns = _columns(conn, "media_assets")
            self.assertTrue(_NEW_COLUMNS.issubset(columns))
        finally:
            conn.close()

        hash_after = _sha256(_OPERATING_DB_PATH)
        self.assertEqual(
            hash_before, hash_after,
            "읽기 전용 연결만 사용했는데도 실제 운영 DB 파일 해시가 "
            "바뀜 — 예상 밖의 쓰기 발생",
        )


if __name__ == "__main__":
    unittest.main()
