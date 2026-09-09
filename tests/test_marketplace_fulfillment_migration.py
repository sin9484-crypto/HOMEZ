"""
=========================================================
Homez OS

File : tests/test_marketplace_fulfillment_migration.py

채널별 판매 방식 선택 — Migration 정적/적용 검증 + Model 자동 드리프트
감지 + 중간 실패 rollback 검증. 표준 라이브러리 unittest + sqlite3만
사용. 신규 패키지 없음. homez.db는 사용하지 않고, 테스트 전용 임시
SQLite 파일 DB를 사용한다.

22) Model↔Migration 일치
23) Migration 중간 실패 rollback
24) 기존 데이터 보호
25) 실제 homez.db 적용 상태(2026-08-01 Gate 5 적용 완료 반영)
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

from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentCapability,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentEligibility,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentSelection,
)
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.marketplace_listing.model import MarketplaceListingDraft
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.marketplace_listing.model import (
    MarketplaceSubmissionApproval,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT,
    "migrations",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
)
REAL_DB_PATH = os.path.join(REPO_ROOT, "homez.db")

EXPECTED_TABLES = {
    "marketplace_channels",
    "marketplace_accounts",
    "marketplace_fulfillment_capabilities",
    "marketplace_listing_drafts",
    "marketplace_listings",
    "marketplace_fulfillment_selections",
    "marketplace_submission_approvals",
    "marketplace_fulfillment_eligibilities",
    "marketplace_submissions",
}

MODEL_TABLE_ORDER = [
    MarketplaceChannel,
    MarketplaceAccount,
    MarketplaceFulfillmentCapability,
    MarketplaceListingDraft,
    MarketplaceListing,
    MarketplaceFulfillmentSelection,
    MarketplaceSubmissionApproval,
    MarketplaceFulfillmentEligibility,
    MarketplaceSubmission,
]


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


class MarketplaceFulfillmentMigrationStaticTestCase(unittest.TestCase):

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

    def test_exactly_nine_create_table_statements(self):

        creates = re.findall(
            r"^CREATE TABLE (\w+)", self.executed_sql, re.M,
        )
        self.assertEqual(len(creates), 9)
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
        """
        2026-08-05 최종 제품화 Phase 4 — MarketplaceListing은 이 정적
        비교에서 제외한다. 이 파일(20260731_00_...)은 이미 실제
        homez.db에 적용되어 checksum이 고정돼 있어 더 이상 수정할 수
        없는데, Model은 플랫폼 상태 동기화 컬럼 4개가 추가돼 이 파일이
        만들던 시점보다 앞서 있다(별도 `20260805_00_add_marketplace_
        listing_status_sync.sql`, 아직 LIVE_GATE_QUEUE 대기). 그 신규
        컬럼·Model 일치 여부는
        test_marketplace_listing_status_sync_migration.py::
        test_final_schema_matches_model_exactly가 이미 전담 검증한다 —
        여기서는 "이미 적용된 9개 테이블은 지금 이 파일과 정확히
        일치한다"만 계속 지킨다.
        """

        dialect = sqlite_dialect.dialect()
        canonical_statements = []

        for model_cls in MODEL_TABLE_ORDER:

            if model_cls is MarketplaceListing:
                continue

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

        # 이후 additive Migration으로 확장된 두 테이블은 원본 Migration
        # 당시 DDL을 별도로 고정한다. 최신 Model의 추가 컬럼을 원본
        # Migration에 역으로 써 넣으면 이미 적용된 checksum 계약을
        # 깨뜨리므로 허용하지 않는다.
        self.assertIn(
            _normalize_sql(
                "CREATE TABLE marketplace_listings ( id INTEGER NOT NULL, "
                "company_id INTEGER NOT NULL, draft_id INTEGER, "
                "product_candidate_id INTEGER NOT NULL, "
                "marketplace_account_id INTEGER NOT NULL, "
                "external_listing_id VARCHAR(100), status VARCHAR(30) NOT "
                "NULL, created_at DATETIME NOT NULL, updated_at DATETIME "
                "NOT NULL, PRIMARY KEY (id), CONSTRAINT "
                "uq_marketplace_listings_candidate_account UNIQUE "
                "(product_candidate_id, marketplace_account_id) )",
            ),
            migration_statements,
        )
        self.assertIn(
            _normalize_sql(
                "CREATE TABLE marketplace_submissions ( id INTEGER NOT NULL, "
                "company_id INTEGER NOT NULL, listing_id INTEGER NOT NULL, "
                "selection_id INTEGER NOT NULL, marketplace_account_id "
                "INTEGER NOT NULL, external_submission_ref VARCHAR(200), "
                "status VARCHAR(30) NOT NULL, safety_decision VARCHAR(20) "
                "NOT NULL, operator_approved_by INTEGER, operator_approved_at "
                "DATETIME, error_reason VARCHAR(1000), idempotency_key "
                "VARCHAR(160) NOT NULL, attempted_at DATETIME NOT NULL, "
                "created_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT "
                "uq_marketplace_submissions_company_idempotency UNIQUE "
                "(company_id, idempotency_key) )",
            ),
            migration_statements,
        )

        # marketplace_listings 자신의 statement들은 위에서 이미 별도로
        # 검증했으니, 아래 전수 diff에서는 제외한다(그러지 않으면 방금
        # 의도적으로 canonical_statements에서 뺀 것들이 매번 "extra"로
        # 잡힌다).
        comparable_migration_statements = [
            stmt for stmt in migration_statements
            if "marketplace_listings " not in stmt
            and "ON marketplace_listings " not in stmt
            and "marketplace_submissions " not in stmt
            and "ON marketplace_submissions " not in stmt
        ]
        canonical_statements = [
            stmt for stmt in canonical_statements
            if "marketplace_submissions " not in stmt
            and "ON marketplace_submissions " not in stmt
        ]

        missing = [
            stmt for stmt in canonical_statements
            if stmt not in comparable_migration_statements
        ]
        extra = [
            stmt for stmt in comparable_migration_statements
            if stmt not in canonical_statements
        ]

        self.assertEqual(
            (missing, extra),
            ([], []),
            "Model ↔ Migration DDL 불일치.\n"
            f"missing({len(missing)}개): {missing}\n"
            f"extra({len(extra)}개): {extra}",
        )


class MarketplaceFulfillmentMigrationApplyTestCase(unittest.TestCase):

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
        marketplace_listing_drafts(4번째 테이블)와 이름이 겹치는
        충돌용 테이블을 미리 만들어 두면, Migration은 그 앞의 3개
        테이블(channels~capabilities)까지 생성한 뒤 거기서 실패해야
        한다. 실패 후 명시적 rollback으로 그 이전 성공분이 전부
        되돌려지는지 확인한다.
        """

        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute(
                "CREATE TABLE marketplace_listing_drafts "
                "(conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            error_message = str(ctx.exception)
            self.assertIn("marketplace_listing_drafts", error_message)
            self.assertIn("already exists", error_message)

            conn.rollback()

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }

            for table_name in EXPECTED_TABLES - {"marketplace_listing_drafts"}:
                self.assertNotIn(
                    table_name, tables,
                    f"실패 전에 생성된 {table_name}가 rollback되지 않았다.",
                )

            self.assertIn("marketplace_listing_drafts", tables)

            conflict_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(marketplace_listing_drafts)",
                )
            }
            self.assertEqual(conflict_columns, {"conflict_marker"})

        finally:
            conn.close()

    def test_migration_preserves_preexisting_unrelated_data(self):
        """24) 기존 데이터 보호 — Migration 적용 전에 존재하던 무관한
        테이블의 데이터가 그대로 남아있어야 한다."""

        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute(
                "CREATE TABLE pre_existing_unrelated (id INTEGER, value TEXT)",
            )
            conn.execute(
                "INSERT INTO pre_existing_unrelated VALUES (1, 'keep-me')",
            )
            conn.commit()

            conn.executescript(self.migration_sql)

            row = conn.execute(
                "SELECT id, value FROM pre_existing_unrelated",
            ).fetchone()
            self.assertEqual(row, (1, "keep-me"))

        finally:
            conn.close()


class RealDatabaseAppliedTestCase(unittest.TestCase):
    """
    25) 실제 homez.db 상태 — 2026-08-01 Gate 5에서 이 Migration을 실제
    homez.db에 적용했다(CTO 최종 승인 + Gate 1~4 Preflight/백업/리허설/
    회귀 전부 통과 후, 정확히 1회). 이제는 "아직 적용되지 않았다"가
    아니라 "정확히 한 번, model과 일치하게 적용되었다"를 읽기 전용으로
    확인한다 — 이 테스트는 실제 DB에 어떤 쓰기도 하지 않는다
    (PRAGMA query_only=ON).
    """

    # 2026-08-05 최종 제품화 Phase 4 — marketplace_listings에 플랫폼
    # 상태 동기화 컬럼 5개를 Model에 추가했다. 이 파일
    # (20260731_00_...)은 이미 실제 homez.db에 BACKFILLED로 적용되어
    # checksum이 고정돼 있어 그 자리에서 수정할 수 없고, 새 컬럼은 별도
    # `migrations/20260805_00_add_marketplace_listing_status_sync.sql`
    # 에 있다. 2026-08-05 CTO 재검증 지시 Gate B 사후감사에서, 이
    # Migration이 표준 Desktop bootstrap 과정 중 실제 homez.db에 이미
    # 적용되어 있음을 확인했다(실행 주체는 미확정 — 자세한 내용은
    # docs/V6_EXECUTION_LEDGER.md "긴급 발견 2"/"Gate B 종결" 참고).
    # 사용자 승인에 따라 이 적용 상태를 그대로 유지하기로 했으므로,
    # 실제 DB 컬럼과 Model 컬럼이 이제 정확히 일치해야 한다 — 예전
    # exclusion(적용 전 임시 방편)을 제거한다.
    #
    # 2026-08-07 Gate H — marketplace_listings에 컬럼 2개
    # (rate_limit_retry_after_seconds/rate_limit_retry_available_at)를
    # 추가하는 `migrations/20260807_00_add_marketplace_listing_
    # rate_limit.sql`이 새로 생겼었고, 한동안 이 두 컬럼을 pending으로
    # 제외해 두었다.
    #
    # 2026-08-09 Gate O — 이 Migration을 실제 homez.db에 적용
    # 완료했다(백업+검증 완료, 판정 LIVE_MIGRATION_APPLIED — 자세한
    # 내용은 docs/V6_EXECUTION_LEDGER.md "Gate O 결과" 참고). 위 주석이
    # 스스로 지시한 대로 "적용되는 순간 이 줄을 지워야 한다" — Gate
    # Q-2(2026-08-09) 전체 회귀 중 이 exclusion이 아직 제거되지 않은
    # 채 남아 있었던 것을 발견해 지금 제거한다(적용 자체는 이보다
    # 앞서 끝나 있었고, 이 두 컬럼을 뺀 채로 비교하던 이 테스트만
    # 뒤늦게 실제 상태를 반영하지 못하고 있었다 — Q-2에서 만든 코드와는
    # 무관한 사전 존재 결함).
    #
    # 2026-08-27 갱신 — migrations/20260826_00_add_marketplace_
    # submission_live_tracking.sql(request_fingerprint/correlation_id/
    # external_http_status)도 실제 개발·운영 DB에 정식 적용 완료됐다.
    # 위와 동일한 원칙으로 exclusion을 제거한다.
    #
    # 2026-08-30 후속 지시 — migrations/20260830_02_add_marketplace_
    # submission_provider_warning.sql이 marketplace_submissions에
    # provider_warning_summary/provider_response_code 2개 컬럼을
    # 추가하도록 Model에는 이미 반영했지만, 이번 지시(운영·개발 DB
    # Migration 적용 금지)에 따라 실제 DB에는 아직 적용하지 않았다.
    # 위 주석들의 확립된 관례 그대로 "적용되는 순간 이 줄을 지워야
    # 한다" — Migration이 실제로 승인·적용되면 이 exclusion을
    # 제거한다.
    #
    # 2026-09-07 갱신 — NAVER API HUB 작업 중 발견한, 이 Migration을
    # 포함해 8월 말부터 미적용 상태로 쌓여 있던 Migration 7개
    # (20260830_02 포함)를 사용자 승인 아래 백업+임시DB 리허설+
    # 실제 적용 절차로 실제 homez.db에 적용 완료했다(자세한 내용은
    # docs/HOMEZ_PROJECT_STATE.md "2026-09-07 후속 3" 참고). 위 주석들의
    # 확립된 관례 그대로 exclusion을 제거한다.
    _PENDING_COLUMNS_BY_TABLE: dict[str, set[str]] = {}

    def test_real_homez_db_has_marketplace_listing_tables_applied(self):

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

            for model_cls in MODEL_TABLE_ORDER:
                table_name = model_cls.__table__.name
                columns = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({table_name})")
                }
                model_columns = {c.name for c in model_cls.__table__.columns}
                model_columns -= self._PENDING_COLUMNS_BY_TABLE.get(
                    table_name, set(),
                )
                self.assertEqual(
                    columns, model_columns,
                    f"{table_name}: 실제 DB 컬럼과 Model 컬럼 불일치",
                )

        finally:
            conn.close()

    def test_migration_file_itself_never_executed_twice_against_real_db(self):
        """
        이 Migration은 Gate 5에서 정확히 1회 적용됐다. 재적용 코드
        경로는 이 저장소 어디에도 없다(수동 승인 절차로만 적용
        가능했고, 이미 적용됐으니 재적용 자체가 CREATE TABLE 충돌로
        즉시 실패한다는 것은 test_migration_reapply_fails_explicitly가
        임시 DB로 이미 검증한다).
        """

        self.assertTrue(os.path.exists(MIGRATION_PATH))


if __name__ == "__main__":
    unittest.main()
