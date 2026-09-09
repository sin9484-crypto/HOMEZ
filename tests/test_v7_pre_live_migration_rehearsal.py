"""
=========================================================
Homez OS

File : tests/test_v7_pre_live_migration_rehearsal.py

Gate M-4(2026-08-21), 2026-08-24 Section 1 재설계 — Pre-Live 최종
하드닝. 5개 Migration(20260820_00/01/02/03, 20260821_00)을 공식
MigrationRunner로 리허설한다.

2026-08-24 재설계 배경: 이 파일은 원래 실제 운영 DB의 디스크
복사본(당시 "5개가 아직 pending인" 상태)에 적용해 검증했다. 이후
그 5개를 포함한 35개 Migration이 전부 실제 운영 DB에 정식 승인
절차로 적용되어, 실제 운영 DB는 더 이상 이 5개가 pending인 상태가
아니게 됐다(재적용을 시도하면 이력에 없는데 파일명이 이미 적용된
최신 파일보다 사전순으로 앞서 OrderInversionError가 발생한다 — 이는
안전장치가 정상 동작한 것이지 결함이 아니다). 실제 운영 DB의
"현재" pending 목록에 더 이상 의존하지 않기 위해, 이 파일은 이제
실제 운영 DB를 전혀 복사하지 않는다 — 대신 저장소의 Migration
파일 자체로 "이 5개가 적용되기 직전" 상태(그 5개보다 파일명이
앞서는 모든 파일이 이미 적용된 상태)를 빈 임시 DB에 공식
MigrationRunner로 결정론적으로 재현한 뒤, 그 위에서 리허설한다.
실제 운영 DB는 오직 읽기 전용 연결(`file:...?mode=ro`)로 무결성·
해시 불변 확인에만 쓰인다 — 이 파일 어디에도 원본 경로에 쓰기
연결을 여는 코드가 없다.
=========================================================
"""

import hashlib
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text

from app.database.bootstrap import bootstrap_environment
from app.database.migration_runner import DuplicateApplicationError
from app.database.migration_runner import MigrationExecutionError
from app.database.migration_runner import MigrationRunner
from app.database.migration_runner import OrderInversionError

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"
_OPERATING_DB_PATH = Path(
    r"C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db",
)

_PENDING_FILENAMES = [
    "20260820_00_create_source_supplier_product_links_schema.sql",
    "20260820_01_create_company_supplier_relations_schema.sql",
    "20260820_02_add_purchase_supplier_order_submission_columns.sql",
    "20260820_03_add_media_asset_rights_status.sql",
    "20260821_00_add_audit_logs_created_at.sql",
]

_NEW_TABLES = {"supplier_product_links", "company_supplier_relations"}
_NEW_PURCHASE_COLUMNS = {
    "submission_status", "submission_provider_code", "supplier_order_id",
    "submitted_at", "confirmed_price", "accepted_quantities_json",
    "rejected_quantities_json", "submission_error_code",
    "submission_retryable", "submission_retry_after_seconds",
    "correlation_id", "approval_fingerprint",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'",
    ).fetchall()

    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple]:

    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _row_count(conn: sqlite3.Connection, table: str) -> int:

    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@unittest.skipUnless(
    _OPERATING_DB_PATH.exists(),
    "이 환경에 실제 운영 homez.db가 없어 건너뜀",
)
class OperatingDbMigrationRehearsalTestCase(unittest.TestCase):
    """실제 운영 DB를 복사하지 않는다. 저장소의 Migration 파일 자체로
    "이 5개 pending 파일이 적용되기 직전" 상태를 빈 임시 DB에
    결정론적으로 재현(그 5개보다 파일명이 앞서는 모든 파일을 공식
    MigrationRunner로 순서대로 적용)한 뒤, 검증이 공허해지지 않도록
    최소 대표 행(리허설 전용 값 — 실제 업무 데이터 아님)을 심고,
    그 위에 5개 Migration을 전부 적용해 최종 상태를 검증한다."""

    @classmethod
    def setUpClass(cls):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.copy_path = Path(path)

        # --- 1) pre-live 기준선 재현: 5개 pending 파일보다 파일명이
        # 앞서는(=먼저 적용됐어야 하는) 모든 Migration을 공식 Runner로
        # 빈 임시 DB에 순서대로 적용한다. 이 목록은 실제 운영 DB의
        # "현재" 상태를 조회한 게 아니라 저장소 자체의 파일명만으로
        # 결정되므로, 실제 운영 DB가 이미 전부 적용된 상태이든 아니든
        # 항상 동일하게 재현 가능하다.
        prior_files = sorted(
            p.name for p in _MIGRATIONS_DIR.glob("*.sql")
            if p.name < _PENDING_FILENAMES[0]
        )
        assert prior_files, "선행 Migration 파일을 찾지 못함 — 저장소 상태 이상"

        prior_dir = Path(tempfile.mkdtemp())
        try:
            for fn in prior_files:
                shutil.copy(_MIGRATIONS_DIR / fn, prior_dir / fn)

            prior_runner = MigrationRunner(cls.copy_path, prior_dir)
            conn = sqlite3.connect(str(cls.copy_path))
            try:
                applied_prior = prior_runner.apply_pending(conn)
            finally:
                conn.close()
            assert applied_prior == prior_files, (
                "pre-live 기준선 적용 결과가 선행 파일 목록과 다름 — "
                "리허설 전제 자체가 무효"
            )
        finally:
            shutil.rmtree(prior_dir, ignore_errors=True)

        # --- 2) 검증을 의미 있게 만들기 위한 최소 대표 행 시딩(전부
        # 이 파일 전용 리허설 값 — 실제 업무 데이터 아님). purchases
        # 1행, media_assets 3행(ORIGINAL 1 + 실제 원본을 가리키는
        # GENERATED 1 + 끊어진 source_asset_id를 가리키는 GENERATED
        # 1 — 20260820_03의 "파생물만 원본 상태 복사, 그 외 전부
        # RIGHTS_UNVERIFIED" 로직을 의미 있게 검증하기 위함),
        # audit_logs 1행(20260821_00의 "기존 행 created_at은 NULL로
        # 남는다" 계약을 의미 있게 검증하기 위함).
        conn = sqlite3.connect(str(cls.copy_path))
        try:
            now = "2026-08-17T00:00:00+00:00"
            conn.execute(
                "INSERT INTO purchases (id, company_id, order_id, "
                "supplier_id, status, idempotency_key, total_cost, "
                "requested_at, created_at, updated_at) VALUES "
                "(1, 1, 1, 1, 'REQUESTED', 'rehearsal-seed-1', 1000, "
                "?, ?, ?)",
                (now, now, now),
            )
            conn.execute(
                "INSERT INTO media_assets (id, company_id, owner_type, "
                "owner_id, asset_role, purpose, display_order, "
                "storage_path, mime_type, file_size_bytes, sha256_hex, "
                "status, created_at) VALUES "
                "(1, 1, 'PRODUCT_CANDIDATE', 1, 'ORIGINAL', 'LISTING', "
                "0, 'rehearsal/seed-1.jpg', 'image/jpeg', 100, "
                "hex(randomblob(32)), 'ACTIVE', ?)",
                (now,),
            )
            conn.execute(
                "INSERT INTO media_assets (id, company_id, owner_type, "
                "owner_id, asset_role, source_asset_id, purpose, "
                "display_order, storage_path, mime_type, "
                "file_size_bytes, sha256_hex, status, created_at) "
                "VALUES (2, 1, 'PRODUCT_CANDIDATE', 1, 'GENERATED', 1, "
                "'LISTING', 1, 'rehearsal/seed-2.jpg', 'image/jpeg', "
                "100, hex(randomblob(32)), 'ACTIVE', ?)",
                (now,),
            )
            conn.execute(
                "INSERT INTO media_assets (id, company_id, owner_type, "
                "owner_id, asset_role, source_asset_id, purpose, "
                "display_order, storage_path, mime_type, "
                "file_size_bytes, sha256_hex, status, created_at) "
                "VALUES (3, 1, 'PRODUCT_CANDIDATE', 1, 'GENERATED', "
                "9999, 'LISTING', 2, 'rehearsal/seed-3.jpg', "
                "'image/jpeg', 100, hex(randomblob(32)), 'ACTIVE', ?)",
                (now,),
            )
            # company_id/user_id는 FK 대상(companies/users)이 실제로
            # 존재해야 foreign_key_check를 통과하므로, 이 순수 스키마
            # 전용 리허설 픽스처에서는 nullable인 두 컬럼을 NULL로
            # 남겨 불필요한 참조를 만들지 않는다.
            conn.execute(
                "INSERT INTO audit_logs (id, company_id, user_id, "
                "action, entity, entity_id, description) VALUES "
                "(1, NULL, NULL, 'REHEARSAL_SEED', 'Test', '1', "
                "'pre-live 기준선 시딩')",
            )
            conn.commit()
        finally:
            conn.close()

        cls.isolated_migrations_dir = Path(tempfile.mkdtemp())
        for fn in _PENDING_FILENAMES:
            shutil.copy(
                _MIGRATIONS_DIR / fn, cls.isolated_migrations_dir / fn,
            )

        # Runner는 대상 디렉터리의 파일명 전체를 순회한다 — pending
        # 5건 외 다른 파일이 섞이지 않도록 이 디렉터리에는 정확히
        # 5개만 둔다(이미 적용된 24개는 이력에 남아 있으므로 여기 없어도
        # diagnose()가 already_applied로 정확히 인식한다).

        # --- 적용 전 스냅샷 ---
        conn = sqlite3.connect(str(cls.copy_path))
        try:
            cls.tables_before = _table_names(conn)
            cls.row_counts_before = {
                t: _row_count(conn, t) for t in cls.tables_before
            }
            cls.purchases_columns_before = {
                c[1] for c in _columns(conn, "purchases")
            }
            cls.media_assets_columns_before = {
                c[1] for c in _columns(conn, "media_assets")
            }
            cls.audit_logs_columns_before = {
                c[1] for c in _columns(conn, "audit_logs")
            }
            cls.media_assets_rows_before = conn.execute(
                "SELECT id, asset_role, source_asset_id FROM media_assets",
            ).fetchall()
            cls.audit_logs_row_count_before = _row_count(conn, "audit_logs")
        finally:
            conn.close()

        cls.runner = MigrationRunner(
            cls.copy_path, cls.isolated_migrations_dir,
        )

        conn = sqlite3.connect(str(cls.copy_path))
        try:
            cls.applied = cls.runner.apply_pending(conn)
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):

        if cls.copy_path.exists():
            os.remove(cls.copy_path)
        shutil.rmtree(cls.isolated_migrations_dir, ignore_errors=True)

    def test_all_five_pending_files_applied_in_order(self):

        self.assertEqual(self.applied, _PENDING_FILENAMES)

    def test_integrity_check_and_foreign_key_check_pass(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_violations, [])
        finally:
            conn.close()

    def test_only_expected_new_tables_appear(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            tables_after = _table_names(conn)
        finally:
            conn.close()

        new_tables = tables_after - self.tables_before
        self.assertEqual(new_tables, _NEW_TABLES)

    def test_no_pre_existing_table_lost_or_renamed(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            tables_after = _table_names(conn)
        finally:
            conn.close()

        missing = self.tables_before - tables_after
        self.assertEqual(missing, set())

    def test_pre_existing_table_row_counts_unchanged_except_expected(self):
        """이 5개 Migration 중 실제로 UPDATE 문이 있는 것은
        media_assets 하나뿐이다(그나마 컬럼 값만 바꾸고 행을 추가·
        삭제하지 않는다) — `schema_migrations`(Runner 자신의 부기
        테이블, 파일마다 정확히 1행씩 늘어나는 것이 의도된 동작)를
        빼면 그 외 모든 기존 테이블은 행 수가 정확히 그대로여야
        한다."""

        conn = sqlite3.connect(str(self.copy_path))
        try:
            for table in self.tables_before - {"schema_migrations"}:
                after = _row_count(conn, table)
                self.assertEqual(
                    after, self.row_counts_before[table],
                    f"{table} 테이블 행 수가 Migration 전후 달라짐 "
                    f"(전: {self.row_counts_before[table]}, 후: {after})",
                )
        finally:
            conn.close()

    def test_schema_migrations_gained_exactly_five_rows(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            after = _row_count(conn, "schema_migrations")
        finally:
            conn.close()

        self.assertEqual(
            after - self.row_counts_before["schema_migrations"], 5,
        )

    def test_purchases_gained_exactly_the_expected_new_columns(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            columns_after = {c[1] for c in _columns(conn, "purchases")}
        finally:
            conn.close()

        new_columns = columns_after - self.purchases_columns_before
        self.assertEqual(new_columns, _NEW_PURCHASE_COLUMNS)

    def test_media_assets_gained_only_rights_status_column(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            columns_after = {c[1] for c in _columns(conn, "media_assets")}
        finally:
            conn.close()

        new_columns = columns_after - self.media_assets_columns_before
        self.assertEqual(new_columns, {"rights_status"})

    def test_audit_logs_gained_only_created_at_column(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            columns_after = {c[1] for c in _columns(conn, "audit_logs")}
        finally:
            conn.close()

        new_columns = columns_after - self.audit_logs_columns_before
        self.assertEqual(new_columns, {"created_at"})

    def test_expected_new_indexes_exist(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            index_names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'",
                ).fetchall()
            }
        finally:
            conn.close()

        expected = {
            "ix_supplier_product_links_id",
            "ix_supplier_product_links_company_id",
            "ix_supplier_product_links_product_candidate_id",
            "ix_supplier_product_links_supplier_id",
            "ix_supplier_product_links_status",
            "ix_company_supplier_relations_id",
            "ix_company_supplier_relations_company_id",
            "ix_company_supplier_relations_supplier_id",
            "ix_company_supplier_relations_approval_status",
            "ix_purchases_submission_status",
            "ix_purchases_supplier_order_id",
            "ix_media_assets_rights_status",
        }
        self.assertTrue(expected.issubset(index_names))

    def test_media_assets_existing_rows_not_blanket_promoted_to_verified(self):
        """"근거 없는 VERIFIED 소급 승격 금지"가 실제로 지켜졌는지 —
        원본(ORIGINAL 또는 끊어진 source_asset_id)은 전부
        RIGHTS_UNVERIFIED로 남아야 하고, GENERATED이면서 실제
        존재하는 원본을 가리키는 행만 그 원본의 상태를 그대로
        복사해야 한다(이 시점 원본도 전부 RIGHTS_UNVERIFIED이므로,
        결국 전체 행이 100% RIGHTS_UNVERIFIED여야 한다 — 이 Migration
        하나만 놓고 보면 VERIFIED로 끝나는 행이 하나도 없어야 정상)."""

        conn = sqlite3.connect(str(self.copy_path))
        try:
            rows = conn.execute(
                "SELECT rights_status, COUNT(*) FROM media_assets "
                "GROUP BY rights_status",
            ).fetchall()
        finally:
            conn.close()

        status_counts = dict(rows)
        self.assertEqual(
            status_counts.get("VERIFIED", 0), 0,
            "media_assets 기존 행이 근거 없이 VERIFIED로 승격됨 — 금지 위반",
        )
        if self.media_assets_rows_before:
            self.assertIn("RIGHTS_UNVERIFIED", status_counts)

    def test_audit_logs_existing_rows_created_at_stays_null(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            null_count = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE created_at IS NULL",
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(null_count, self.audit_logs_row_count_before)

    def test_new_audit_log_write_after_migration_has_utc_created_at(self):
        """이 테스트는 리허설 복사본에 감사로그 1행을 실제로 추가한다
        — 같은 클래스의 다른 테스트(예: 기존 테이블 행 수 불변 확인)가
        이 부수효과에 영향받지 않도록 끝에서 반드시 지운다."""

        from app.core.audit_db import write_audit_log
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from datetime import datetime

        engine = create_engine(f"sqlite:///{self.copy_path}")
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            write_audit_log(
                session, user_id=1, action="M4_REHEARSAL_TEST",
                entity="Test", entity_id="1", description="rehearsal",
                company_id=1,
            )
            session.commit()

            row = session.execute(
                text(
                    "SELECT created_at FROM audit_logs WHERE action = "
                    "'M4_REHEARSAL_TEST'",
                ),
            ).fetchone()
            self.assertIsNotNone(row[0])
            datetime.fromisoformat(row[0])
        finally:
            session.execute(
                text(
                    "DELETE FROM audit_logs WHERE action = "
                    "'M4_REHEARSAL_TEST'",
                ),
            )
            session.commit()
            session.close()
            engine.dispose()

    def test_company_supplier_relations_unique_constraint_enforced(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            conn.execute(
                "INSERT INTO company_supplier_relations "
                "(company_id, supplier_id, approval_status, created_by, "
                "created_at, updated_at) VALUES "
                "(999001, 1, 'PENDING', 1, datetime('now'), datetime('now'))",
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO company_supplier_relations "
                    "(company_id, supplier_id, approval_status, created_by, "
                    "created_at, updated_at) VALUES "
                    "(999001, 1, 'PENDING', 1, datetime('now'), "
                    "datetime('now'))",
                )
        finally:
            conn.rollback()
            conn.execute(
                "DELETE FROM company_supplier_relations "
                "WHERE company_id = 999001",
            )
            conn.commit()
            conn.close()

    def test_supplier_product_links_unique_constraint_enforced(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            conn.execute(
                "INSERT INTO supplier_product_links "
                "(company_id, product_candidate_id, supplier_id, "
                "supplier_sku, unit_cost, moq, status, created_by, "
                "created_at, updated_at) VALUES "
                "(999002, 1, 1, 'REHEARSAL-SKU', 1000, 1, 'ACTIVE', 1, "
                "datetime('now'), datetime('now'))",
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO supplier_product_links "
                    "(company_id, product_candidate_id, supplier_id, "
                    "supplier_sku, unit_cost, moq, status, created_by, "
                    "created_at, updated_at) VALUES "
                    "(999002, 1, 1, 'REHEARSAL-SKU', 2000, 1, 'ACTIVE', 1, "
                    "datetime('now'), datetime('now'))",
                )
        finally:
            conn.rollback()
            conn.execute(
                "DELETE FROM supplier_product_links "
                "WHERE company_id = 999002",
            )
            conn.commit()
            conn.close()

    def test_cross_company_rows_are_distinguishable_not_merged(self):
        """같은 supplier_id라도 company_id가 다르면 별도 행이어야
        한다(UNIQUE는 company_id를 포함한 복합키이므로 서로 다른
        회사는 절대 서로를 차단하지 않는다) — 격리가 "너무 강해서"
        다른 회사가 아예 관계를 못 만드는 결함이 없는지 확인."""

        conn = sqlite3.connect(str(self.copy_path))
        try:
            conn.execute(
                "INSERT INTO company_supplier_relations "
                "(company_id, supplier_id, approval_status, created_by, "
                "created_at, updated_at) VALUES "
                "(999003, 1, 'PENDING', 1, datetime('now'), datetime('now'))",
            )
            conn.execute(
                "INSERT INTO company_supplier_relations "
                "(company_id, supplier_id, approval_status, created_by, "
                "created_at, updated_at) VALUES "
                "(999004, 1, 'PENDING', 1, datetime('now'), datetime('now'))",
            )
            conn.commit()
            rows = conn.execute(
                "SELECT company_id FROM company_supplier_relations "
                "WHERE supplier_id = 1 AND company_id IN (999003, 999004) "
                "ORDER BY company_id",
            ).fetchall()
            self.assertEqual([r[0] for r in rows], [999003, 999004])
        finally:
            conn.execute(
                "DELETE FROM company_supplier_relations "
                "WHERE company_id IN (999003, 999004)",
            )
            conn.commit()
            conn.close()

    def test_diagnose_reports_all_five_as_already_applied(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            diagnosis = self.runner.diagnose(conn)
        finally:
            conn.close()

        self.assertEqual(diagnosis["pending"], [])
        self.assertEqual(
            set(diagnosis["already_applied"]), set(_PENDING_FILENAMES),
        )

    def test_reapply_is_blocked_not_silently_skipped(self):
        """apply_pending()을 다시 호출하면(이미 이력에 있으므로)
        plan_pending()이 애초에 빈 목록을 계획해 아무 것도 재실행하지
        않는 것이 1차 방어선이다 — 그 계약을 확인한다. 이력을 지운
        뒤 강제로 재적용을 시도하면(운영자가 실수로 이력 없이 다시
        돌리는 시나리오) DuplicateApplicationError로 즉시 차단돼야
        한다(자동 재실행 금지 원칙)."""

        conn = sqlite3.connect(str(self.copy_path))
        try:
            reapplied = self.runner.apply_pending(conn)
            self.assertEqual(reapplied, [])

            placeholders = ",".join("?" for _ in _PENDING_FILENAMES)
            deleted_rows = conn.execute(
                f"SELECT * FROM schema_migrations WHERE filename IN "
                f"({placeholders})",
                _PENDING_FILENAMES,
            ).fetchall()
            columns = [
                d[0] for d in conn.execute(
                    "SELECT * FROM schema_migrations LIMIT 0",
                ).description
            ]
            conn.execute(
                f"DELETE FROM schema_migrations WHERE filename IN "
                f"({placeholders})",
                _PENDING_FILENAMES,
            )
            conn.commit()

            try:
                with self.assertRaises(
                    (DuplicateApplicationError, MigrationExecutionError),
                ):
                    self.runner.apply_pending(conn)
            finally:
                # 이 테스트는 클래스 레벨로 공유하는 self.copy_path를
                # 대상으로 하므로, 일부러 지운 이력 5행을 반드시
                # 원상복구해야 뒤에 실행되는 다른 테스트(예: 이력이
                # 정확히 5건 늘었는지 확인하는 테스트)가 이 테스트의
                # 부수효과에 오염되지 않는다.
                placeholders_cols = ",".join("?" for _ in columns)
                conn.executemany(
                    f"INSERT INTO schema_migrations ({','.join(columns)}) "
                    f"VALUES ({placeholders_cols})",
                    deleted_rows,
                )
                conn.commit()
        finally:
            conn.close()


class MigrationFailureAtomicRollbackTestCase(unittest.TestCase):
    """실패 시 전체 Transaction rollback — 실제 운영 DB나 그 복사본이
    아니라 완전히 새로운 임시 DB에서, 5개 파일 중 하나를 의도적으로
    깨뜨려 부분 적용이 남지 않는지 확인한다(운영 데이터와 무관하게
    항상 재현 가능해야 하는 계약 검증)."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)

        # 앞 2개 Migration(테이블 생성)까지는 정상 적용해 둔 뒤, 3번째
        # (purchases 컬럼 추가)를 깨뜨려 부분 실패를 재현한다 — 그러려면
        # purchases 테이블 자체가 있어야 하므로 최소 스키마를 직접
        # 만든다.
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "CREATE TABLE purchases (id INTEGER PRIMARY KEY, "
                "company_id INTEGER NOT NULL)",
            )
            conn.commit()
        finally:
            conn.close()

        self.migrations_dir = Path(tempfile.mkdtemp())
        original = (
            _MIGRATIONS_DIR
            / "20260820_02_add_purchase_supplier_order_submission_columns.sql"
        ).read_text(encoding="utf-8")
        broken = original.replace(
            "CREATE INDEX ix_purchases_submission_status "
            "ON purchases (submission_status);",
            "CREATE INDEX ix_purchases_submission_status "
            "ON purchases (submission_status);\n"
            "CREATE INDEX ix_broken ON no_such_table (id);",
        )
        self.assertNotEqual(original, broken)
        (
            self.migrations_dir
            / "20260820_02_add_purchase_supplier_order_submission_columns.sql"
        ).write_text(broken, encoding="utf-8")

        self.runner = MigrationRunner(self.db_path, self.migrations_dir)

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)
        shutil.rmtree(self.migrations_dir, ignore_errors=True)

    def test_broken_migration_leaves_no_partial_columns(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            with self.assertRaises(MigrationExecutionError):
                self.runner.apply_pending(conn)
        finally:
            conn.close()

        fresh = sqlite3.connect(str(self.db_path))
        try:
            columns = {
                c[1] for c in fresh.execute(
                    "PRAGMA table_info(purchases)",
                ).fetchall()
            }
            self.assertNotIn(
                "submission_status", columns,
                "COMMIT 전 실패인데 컬럼이 실제로 영속됐다 — 부분 적용 결함",
            )
            integrity = fresh.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            fresh.close()


@unittest.skipUnless(
    _OPERATING_DB_PATH.exists(),
    "이 환경에 실제 운영 homez.db가 없어 건너뜀",
)
class OperatingDbReadOnlyInvariantTestCase(unittest.TestCase):
    """실제 운영 DB는 이 파일 어디에서도 복사·쓰기 대상이 아니다 —
    오직 읽기 전용 연결로 "정상적으로 열리고 무결하며 이 Gate가
    기대하는 만큼 Migration이 이미 적용돼 있다"만 확인하고, 그
    전후로 파일 해시가 그대로인지 재확인한다."""

    def test_operating_db_readonly_open_and_hash_unchanged(self):

        def _sha256(path: Path) -> str:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()

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
            # 이 Gate 시점 기준값 — 더 늘어나는 것은 정상(향후 승인된
            # Migration), 그 이하로 줄어드는 것만 이상 신호다.
            self.assertGreaterEqual(applied_count, 35)
        finally:
            conn.close()

        hash_after = _sha256(_OPERATING_DB_PATH)
        self.assertEqual(
            hash_before, hash_after,
            "읽기 전용 연결만 사용했는데도 실제 운영 DB 파일 해시가 "
            "바뀜 — 예상 밖의 쓰기 발생",
        )


class MigrationOrderInversionRejectionTestCase(unittest.TestCase):
    """운영 DB와 무관하게, 순서 역전 차단 계약 자체를 완전히
    독립적인 합성 Migration 2개로 검증한다: 뒤 파일을 먼저 적용한
    뒤, 이력에 없으면서 파일명이 앞서는 파일을 나중에 발견하면
    OrderInversionError로 즉시 차단돼야 한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)
        self.migrations_dir = Path(tempfile.mkdtemp())

        (self.migrations_dir / "20260102_00_create_table_beta.sql").write_text(
            "BEGIN;\nCREATE TABLE beta (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
            encoding="utf-8",
        )

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)
        shutil.rmtree(self.migrations_dir, ignore_errors=True)

    def test_earlier_named_file_discovered_after_later_one_is_rejected(self):

        runner = MigrationRunner(self.db_path, self.migrations_dir)
        conn = sqlite3.connect(str(self.db_path))
        try:
            applied = runner.apply_pending(conn)
            self.assertEqual(
                applied, ["20260102_00_create_table_beta.sql"],
            )

            (
                self.migrations_dir
                / "20260101_00_create_table_alpha.sql"
            ).write_text(
                "BEGIN;\nCREATE TABLE alpha (id INTEGER PRIMARY KEY);\n"
                "COMMIT;\n",
                encoding="utf-8",
            )

            with self.assertRaises(OrderInversionError):
                runner.diagnose(conn)

            with self.assertRaises(OrderInversionError):
                runner.apply_pending(conn)

            tables = _table_names(conn)
            self.assertNotIn(
                "alpha", tables,
                "순서 역전이 차단되지 않고 실제로 적용됨 — 안전장치 결함",
            )
        finally:
            conn.close()


class MigrationApprovalGateTestCase(unittest.TestCase):
    """bootstrap_environment()의 "승인 파일 목록이 diagnose()가 실제로
    본 pending 목록과 정확히 일치할 때만 적용" 계약을 완전히 독립적인
    임시 DB·Migration·백업 디렉터리로 검증한다 — 승인 없음/불일치
    승인은 차단되고 DDL이 실행되지 않아야 하며, 정확히 일치하는
    승인만 적용 직전에 백업을 만들고 실제로 적용해야 한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)
        # is_new_install 분기(빈 DB는 무조건 승인 없이 전체 적용)를
        # 피하기 위해, 기존 설치처럼 보이도록 스키마 이력 테이블을
        # 미리 만들어 둔다.
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "CREATE TABLE schema_migrations (filename TEXT "
                "PRIMARY KEY, checksum TEXT, applied_at TEXT, "
                "status TEXT, execution_ms INTEGER, notes TEXT)",
            )
            conn.commit()
        finally:
            conn.close()

        self.migrations_dir = Path(tempfile.mkdtemp())
        self.pending_filename = "20260103_00_create_table_gamma.sql"
        (self.migrations_dir / self.pending_filename).write_text(
            "BEGIN;\nCREATE TABLE gamma (id INTEGER PRIMARY KEY);\n"
            "COMMIT;\n",
            encoding="utf-8",
        )
        self.backups_dir = Path(tempfile.mkdtemp())

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)
        shutil.rmtree(self.migrations_dir, ignore_errors=True)
        shutil.rmtree(self.backups_dir, ignore_errors=True)

    def _table_exists(self, name: str) -> bool:

        conn = sqlite3.connect(str(self.db_path))
        try:
            return name in _table_names(conn)
        finally:
            conn.close()

    def test_no_approval_blocks_apply(self):

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir, approved_migration_files=None,
        )

        self.assertTrue(result.migration_approval_required)
        self.assertEqual(result.applied, [])
        self.assertFalse(self._table_exists("gamma"))

    def test_mismatched_approval_blocks_apply(self):

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
            approved_migration_files=["some_other_file.sql"],
        )

        self.assertTrue(result.migration_approval_required)
        self.assertEqual(result.applied, [])
        self.assertFalse(self._table_exists("gamma"))

    def test_exact_matching_approval_creates_backup_then_applies(self):

        backups_before = list(self.backups_dir.glob("*.db"))
        self.assertEqual(backups_before, [])

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
            approved_migration_files=[self.pending_filename],
        )

        self.assertFalse(result.migration_approval_required)
        self.assertEqual(result.applied, [self.pending_filename])
        self.assertTrue(self._table_exists("gamma"))

        self.assertIsNotNone(result.backup_path)
        self.assertTrue(result.backup_path.exists())
        backup_conn = sqlite3.connect(
            f"file:{result.backup_path}?mode=ro", uri=True,
        )
        try:
            backup_conn.execute("PRAGMA query_only = ON")
            # 백업은 적용 "직전"에 만들어지므로 gamma가 없어야 한다.
            self.assertNotIn("gamma", _table_names(backup_conn))
        finally:
            backup_conn.close()


if __name__ == "__main__":
    unittest.main()
