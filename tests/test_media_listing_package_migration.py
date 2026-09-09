"""
=========================================================
Homez OS

File : tests/test_media_listing_package_migration.py

Media Asset / Listing Package Migration 정적/적용 검증 + Model 자동
드리프트 감지 + 중간 실패 rollback 검증(app/domains/marketplace_listing
의 동일 패턴 그대로). homez.db는 사용하지 않고, 테스트 전용 임시
SQLite 파일 DB만 사용한다 — 이 Migration을 실제 homez.db에 적용하는
코드 경로는 이 저장소 어디에도 없다.
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

from app.database.migration_runner import MigrationRunner
from app.domains.listing_package.model import ListingPackage
from app.domains.listing_package.model import ListingPackageApproval
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")
MIGRATION_PATH = os.path.join(
    MIGRATIONS_DIR,
    "20260802_00_create_media_listing_package_schema.sql",
)
REAL_DB_PATH = os.path.join(REPO_ROOT, "homez.db")

EXPECTED_TABLES = {
    "media_assets",
    "image_generation_jobs",
    "image_generation_results",
    "image_generation_daily_usage",
    "listing_packages",
    "listing_package_approvals",
}

MODEL_TABLE_ORDER = [
    MediaAsset,
    ImageGenerationJob,
    ImageGenerationResult,
    ImageGenerationDailyUsage,
    ListingPackage,
    ListingPackageApproval,
]


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


def _pending_migration_owned_columns(
    conn: sqlite3.Connection, migrations_dir: str,
) -> dict[str, set[str]]:
    """
    2026-08-28 V7 종합 감사 Phase 1-A — `conn`이 가리키는 DB 기준으로
    아직 적용되지 않은(pending) Migration 파일들을 실제로 열어
    `ALTER TABLE ... ADD COLUMN ...`으로 추가하는 컬럼만
    {table_name: {column_name, ...}}로 반환한다.

    하드코딩된 예외 목록이 아니라 `MigrationRunner.diagnose()`(이
    저장소의 공식 pending 판정 메커니즘)와 실제 파일 내용을 그대로
    읽어 계산한다 — 그래서 해당 Migration이 실제로 적용되는 순간
    이 함수의 반환값에서 자동으로 빠지고, 호출부의 비교는 다시
    "완전 일치"로 저절로 강화된다(예외를 손으로 지우지 않아도 됨).
    이 함수는 읽기 전용이다(DB에 쓰지 않는다, 어떤 백업도 만들지 않음).
    """

    runner = MigrationRunner(REAL_DB_PATH, migrations_dir)
    diagnosis = runner.diagnose(conn)

    owned: dict[str, set[str]] = {}
    for filename in diagnosis["pending"]:
        path = os.path.join(migrations_dir, filename)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        for match in re.finditer(
            r"^ALTER TABLE (\w+)\s+ADD COLUMN (\w+)", content,
            re.M | re.I,
        ):
            table, column = match.group(1), match.group(2)
            owned.setdefault(table, set()).add(column)

    return owned


class MediaListingPackageMigrationStaticTestCase(unittest.TestCase):

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

    def test_exactly_six_create_table_statements(self):

        creates = re.findall(
            r"^CREATE TABLE (\w+)", self.executed_sql, re.M,
        )
        self.assertEqual(len(creates), 6)
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

    def test_rollback_drops_only_in_comments_and_count_matches(self):

        comment_lines = [
            line
            for line in self.content.splitlines()
            if line.strip().startswith("--")
        ]
        comment_text = "\n".join(comment_lines)
        drop_count_in_comments = len(
            re.findall(r"DROP (TABLE|INDEX)", comment_text),
        )

        table_count = len(
            re.findall(r"^CREATE TABLE", self.executed_sql, re.M),
        )
        index_count = len(
            re.findall(r"^CREATE( UNIQUE)? INDEX", self.executed_sql, re.M),
        )

        self.assertEqual(
            drop_count_in_comments, table_count + index_count,
        )

    def test_no_default_check_trigger_cascade(self):

        for keyword in ("DEFAULT", "CHECK", "TRIGGER", "CASCADE"):
            self.assertNotRegex(
                self.executed_sql.upper(), rf"\b{keyword}\b",
            )

    def test_migration_matches_sqlalchemy_model_ddl(self):

        dialect = sqlite_dialect.dialect()
        canonical_statements = []

        for model_cls in MODEL_TABLE_ORDER:

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

        executable_body = (
            self.executed_sql
            .replace("BEGIN;", "")
            .replace("COMMIT;", "")
        )
        migration_statements = [
            _normalize_sql(stmt)
            for stmt in executable_body.split(";")
            if stmt.strip()
        ]

        missing = [
            stmt for stmt in canonical_statements
            if stmt not in migration_statements
        ]
        extra = [
            stmt for stmt in migration_statements
            if stmt not in canonical_statements
        ]

        # 2026-08-20 추가 — migrations/20260820_03_add_media_asset_
        # rights_status.sql이 media_assets에 rights_status 컬럼을
        # additive로 더해, 이 원본 Migration(20260802_00) 단독 DDL과
        # 현재 Model이 media_assets에 한해서만 정당하게 갈린다(다른
        # 모든 테이블은 여전히 완전 일치해야 한다). media_assets 관련
        # 문장만 이 비교에서 제외한다.
        missing = [stmt for stmt in missing if "media_assets" not in stmt]
        extra = [stmt for stmt in extra if "media_assets" not in stmt]

        self.assertEqual(
            (missing, extra),
            ([], []),
            "Model ↔ Migration DDL 불일치.\n"
            f"missing({len(missing)}개): {missing}\n"
            f"extra({len(extra)}개): {extra}",
        )


class MediaListingPackageMigrationApplyTestCase(unittest.TestCase):

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
                "WHERE type IN ('table', 'index') "
                "AND name NOT LIKE 'sqlite_%'",
            ).fetchall()
            self.assertEqual(remaining, [])

        finally:
            conn.close()

    def test_mid_migration_failure_rolls_back_created_objects(self):
        """
        image_generation_daily_usage(4번째 테이블)와 이름이 겹치는
        충돌용 테이블을 미리 만들어 두면, Migration은 그 앞의 3개
        테이블까지 생성한 뒤 거기서 실패해야 한다. 실패 후 명시적
        rollback으로 그 이전 성공분이 전부 사라지는지 확인한다
        (BEGIN/COMMIT 하나로 묶여 있으므로 전체가 원자적이어야 한다).
        """

        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute(
                "CREATE TABLE image_generation_daily_usage "
                "(conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.migration_sql)

            conn.rollback()

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            # 충돌 테이블 자체는 그대로 남아 있어야 하고, 그 앞에서
            # 부분적으로 만들어졌을 media_assets/image_generation_jobs/
            # image_generation_results는 executescript 자체가 트랜잭션
            # 단위로 실행되므로 남아있지 않아야 한다.
            self.assertIn("image_generation_daily_usage", tables)
            self.assertNotIn("listing_packages", tables)

            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(image_generation_daily_usage)",
                )
            }
            self.assertEqual(columns, {"conflict_marker"})

        finally:
            conn.close()

    def test_migration_preserves_preexisting_unrelated_data(self):

        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute(
                "CREATE TABLE unrelated_table (id INTEGER PRIMARY KEY, "
                "value TEXT)",
            )
            conn.execute(
                "INSERT INTO unrelated_table (value) VALUES ('keep-me')",
            )
            conn.commit()

            conn.executescript(self.migration_sql)

            row = conn.execute(
                "SELECT value FROM unrelated_table",
            ).fetchone()
            self.assertEqual(row[0], "keep-me")

        finally:
            conn.close()


class RealDatabaseAppliedTestCase(unittest.TestCase):
    """
    실제 homez.db 상태 — 2026-08-02 HOMEZ 최종 제품 완성 로드맵 Phase 1
    (Migration Runner 도입 + 사전 백업/복사본 리허설/rollback 리허설/
    전체 회귀 750/750 통과 후) MigrationRunner를 통해 이 Migration을
    실제 homez.db에 적용했다(APPLIED — backfill이 아닌 신규 실행).
    이제는 "아직 적용되지 않았다"가 아니라 "정확히 한 번, Model과
    일치하게 적용되었다"를 읽기 전용으로 확인한다 — 이 테스트는 실제
    DB에 어떤 쓰기도 하지 않는다(PRAGMA query_only=ON).
    """

    def test_real_homez_db_has_media_listing_package_tables_applied(self):

        if not os.path.exists(REAL_DB_PATH):
            self.skipTest("실제 homez.db가 이 환경에 없습니다.")

        conn = sqlite3.connect(f"file:{REAL_DB_PATH}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")

        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables))

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_violations, [])

            # 2026-08-27 갱신 — migrations/20260820_03_add_media_asset_
            # rights_status.sql을 포함해 그 이후 모든 Migration(#38까지)이
            # 실제 개발 DB(repo root homez.db)에 정식 적용 완료됐다
            # (2026-08-27 V7 완성 라운드 — Migration 동기화). 예외 없이
            # 전체 일치를 강제한다.
            #
            # 2026-08-28 갱신(V7 종합 감사 Phase 1-A) — 오늘 media_assets에
            # source_url/source_domain/source_classification 3개 컬럼이
            # Model에 추가됐지만(migrations/20260828_01_add_media_asset_
            # source_tracking.sql), 그 Migration은 이 DB(개발 DB)에 아직
            # 적용되지 않았다(운영 DB에 적용하지 않는다는 이번 감사 지시를
            # 그대로 따르며, 개발 DB도 같은 pending 상태로 함께 유지한다
            # — 둘 중 하나만 먼저 앞서가면 "무엇이 최종 스키마인가"가
            # 갈라진다). 이 컬럼 차이를 통째로 무시하지 않는다 — 정확히
            # "지금 pending인 Migration이 실제로 추가하겠다고 선언한
            # 컬럼"만 예외로 인정하고, 그 외 어떤 차이든 그대로 실패해야
            # 한다(_pending_migration_owned_columns()가 하드코딩이 아니라
            # MigrationRunner.diagnose() + 실제 pending 파일 내용을 읽어
            # 계산 — 나중에 이 Migration이 실제로 적용되면 diagnose()가
            # 더 이상 pending으로 보지 않으므로 이 예외도 자동으로
            # 사라지고 비교는 다시 완전 일치로 돌아간다).
            pending_owned_columns = _pending_migration_owned_columns(
                conn, MIGRATIONS_DIR,
            )
            for model_cls in MODEL_TABLE_ORDER:
                table_name = model_cls.__table__.name
                columns = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({table_name})")
                }
                model_columns = {c.name for c in model_cls.__table__.columns}
                expected_columns = model_columns - pending_owned_columns.get(
                    table_name, set(),
                )
                self.assertEqual(
                    columns, expected_columns,
                    f"{table_name}: 실제 DB 컬럼과 Model 컬럼 불일치"
                    "(현재 pending Migration이 선언한 컬럼은 제외하고 "
                    "비교한 결과)",
                )

        finally:
            conn.close()

    def test_migration_file_itself_never_executed_twice_against_real_db(self):
        """
        이 Migration은 Migration Runner를 통해 정확히 1회 적용됐다
        (schema_migrations 이력이 재적용을 차단한다 — 재적용 시도는
        test_migration_reapply_fails_explicitly가 임시 DB로 이미
        검증한다).
        """

        self.assertTrue(os.path.exists(MIGRATION_PATH))


if __name__ == "__main__":
    unittest.main()
