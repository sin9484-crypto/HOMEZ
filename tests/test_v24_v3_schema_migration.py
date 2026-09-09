"""
=========================================================
Homez OS

File : tests/test_v24_v3_schema_migration.py

HOMEZ V2.4 Commerce Safety Layer + V3 Discovery Core
전체 스키마 Migration 정적/적용 검증 + Model 자동 드리프트 감지 +
중간 실패 rollback 검증.

표준 라이브러리 unittest + sqlite3만 사용. 신규 패키지 없음.
homez.db는 사용하지 않고, 테스트 전용 임시 SQLite 파일 DB를 사용한다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from app.core.exceptions import BadRequestException
from app.domains.product_candidate import router as pc_router
from app.domains.product_candidate.schema import ProductCandidateDecisionRequest
from app.domains.product_candidate.schema import ProductCandidateDiscover
from app.domains.product_candidate.service import ProductCandidateService

from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT,
    "migrations",
    "20260728_00_create_v24_v3_schema.sql",
)

EXPECTED_TABLES = {
    "automation_mode_states",
    "emergency_stops",
    "execution_limits",
    "execution_usages",
    "execution_period_usages",
    "product_candidates",
    "product_candidate_evidences",
    "product_candidate_decisions",
}

MODEL_TABLE_ORDER = [
    AutomationModeState,
    EmergencyStop,
    ExecutionLimit,
    ExecutionUsage,
    ExecutionPeriodUsage,
    ProductCandidate,
    ProductCandidateEvidence,
    ProductCandidateDecision,
]


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


class SchemaMigrationStaticTestCase(unittest.TestCase):
    """Migration 파일 내용 자체에 대한 정적 검증."""

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

    def test_exactly_eight_create_table_statements(self):

        creates = re.findall(
            r"^CREATE TABLE (\w+)", self.executed_sql, re.M,
        )
        self.assertEqual(len(creates), 8)
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
            self.assertNotIn(keyword, self.executed_sql.upper())

    def test_migration_matches_sqlalchemy_model_ddl(self):
        """
        8개 Model에서 SQLAlchemy SQLite dialect로 컴파일한 canonical DDL과
        Migration 파일의 실행 DDL(주석·BEGIN·COMMIT 제외)을 대조한다.
        DB 연결 없이 dialect 객체만으로 compile한다.

        2026-08-14 Gate R13 테넌트 격리 감사 — 이 파일(20260728_00_...)은
        이미 실제 homez.db에 APPLIED로 기록되어 checksum이 고정돼
        있어 더 이상 수정할 수 없는데, ProductCandidateDecision
        Model에는 company_id가 추가돼 이 파일이 만들던 시점보다 앞서
        있다(신규 `20260814_01_add_tenant_isolation_company_id.sql`).
        신규 테이블 ProductCandidateSelection은 이 파일과 애초에
        무관하다(별도 신규 CREATE TABLE). 그 테이블의 Model 일치
        여부는 tests/test_gate_r13_tenant_isolation_migration.py::
        MigrationApplyTestCase가 전담 검증한다.

        2026-08-15 V7 Gate 2 — ProductCandidate Model에도 visibility/
        owner_company_id가 추가돼(비공개 후보 지원, 요구사항 2) 이
        파일보다 앞서게 됐다. 그 컬럼들의 Model 일치 여부는 신규
        tests/test_gate2_tenant_isolation_hardening_migration.py가
        전담 검증한다 — 여기서는 손대지 않은 나머지 6개 테이블만
        계속 지킨다.
        """

        dialect = sqlite_dialect.dialect()
        canonical_statements = []

        untouched_models = [
            model_cls for model_cls in MODEL_TABLE_ORDER
            if model_cls not in (ProductCandidateDecision, ProductCandidate)
        ]

        for model_cls in untouched_models:

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

        untouched_table_names = {
            model_cls.__table__.name for model_cls in untouched_models
        }

        executable_body = (
            self.executed_sql
            .replace("BEGIN;", "")
            .replace("COMMIT;", "")
        )
        migration_statements = [
            _normalize_sql(stmt)
            for stmt in executable_body.split(";")
            if stmt.strip()
            and any(
                f" {name} " in f" {_normalize_sql(stmt)} "
                or f" {name} (" in f" {_normalize_sql(stmt)} "
                for name in untouched_table_names
            )
        ]

        missing = [
            stmt for stmt in canonical_statements
            if stmt not in migration_statements
        ]
        extra = [
            stmt for stmt in migration_statements
            if stmt not in canonical_statements
        ]

        self.assertEqual(
            (missing, extra),
            ([], []),
            "Model ↔ Migration DDL 불일치.\n"
            f"missing({len(missing)}개): {missing}\n"
            f"extra({len(extra)}개): {extra}",
        )


class SchemaMigrationApplyTestCase(unittest.TestCase):
    """임시 SQLite 파일 DB에 실제로 Migration을 적용하는 검증. homez.db는 사용하지 않는다."""

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
        product_candidate_evidences와 이름이 겹치는 충돌용 테이블을 미리
        만들어 두면, Migration은 그 앞의 6개 테이블(automation_mode_states
        ~ product_candidates)까지 생성한 뒤 거기서 실패해야 한다. 실패 후
        명시적 rollback으로 그 이전 성공분이 전부 되돌려지는지 확인한다.
        """

        conn = sqlite3.connect(self.db_path)

        try:
            conn.execute(
                "CREATE TABLE product_candidate_evidences "
                "(conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            error_message = str(ctx.exception)
            self.assertIn("product_candidate_evidences", error_message)
            self.assertIn("already exists", error_message)

            conn.rollback()

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }

            for table_name in EXPECTED_TABLES - {"product_candidate_evidences"}:
                self.assertNotIn(
                    table_name, tables,
                    f"실패 전에 생성된 {table_name}가 rollback되지 않았다.",
                )

            self.assertIn("product_candidate_evidences", tables)

            conflict_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(product_candidate_evidences)",
                )
            }
            self.assertEqual(conflict_columns, {"conflict_marker"})

        finally:
            conn.close()


class ProductCandidateRouterOnMigratedSchemaTestCase(unittest.TestCase):
    """
    실제 Migration SQL을 적용한 임시 DB 위에서 ProductCandidate 라우터의
    목록·상세·근거·결정이력 조회 + 승인·보류·거절 경로를 검증한다.

    httpx가 설치되어 있지 않아 FastAPI TestClient를 사용할 수 없으므로
    (패키지 설치는 금지 범위), 라우터 엔드포인트 함수를 ASGI 계층 없이
    직접 호출하는 방식으로 대신한다. Base.metadata.create_all이 아니라
    실제 migrations/20260728_00_create_v24_v3_schema.sql을 그대로
    executescript로 적용한 스키마를 사용한다(Migration 자체의 실사용
    검증까지 포함).
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            migration_sql = f.read()

        conn = sqlite3.connect(path)
        conn.executescript(migration_sql)

        # 2026-08-14 Gate R13 — 이 파일이 만드는 스키마는 company_id
        # 이전 원본 그대로 유지해야 하므로(checksum 고정), product_
        # candidate 관련 두 변경분만 이 테스트 전용으로 별도 적용한다
        # (전체 20260814_01 파일은 coupang/decision/settlement 테이블도
        # 건드리는데, 이 임시 DB는 v24_v3 스키마만 있어 그대로 실행할
        # 수 없다 — 실제 homez.db 리허설은
        # tests/test_gate_r13_tenant_isolation_migration.py가 전담).
        conn.executescript(
            "BEGIN;\n"
            "ALTER TABLE product_candidate_decisions "
            "ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0;\n"
            "CREATE INDEX ix_product_candidate_decisions_company_id "
            "ON product_candidate_decisions (company_id);\n"
            "CREATE TABLE product_candidate_selections (\n"
            "\tid INTEGER NOT NULL,\n"
            "\tcandidate_id INTEGER NOT NULL,\n"
            "\tcompany_id INTEGER NOT NULL,\n"
            "\tstatus VARCHAR(20) NOT NULL,\n"
            "\tdecided_at DATETIME NOT NULL,\n"
            "\tupdated_at DATETIME NOT NULL,\n"
            "\tPRIMARY KEY (id),\n"
            "\tCONSTRAINT uq_product_candidate_selections_candidate_company "
            "UNIQUE (candidate_id, company_id)\n"
            ");\n"
            "CREATE INDEX ix_product_candidate_selections_candidate_id "
            "ON product_candidate_selections (candidate_id);\n"
            "CREATE INDEX ix_product_candidate_selections_company_id "
            "ON product_candidate_selections (company_id);\n"
            "CREATE INDEX ix_product_candidate_selections_id "
            "ON product_candidate_selections (id);\n"
            "COMMIT;\n",
        )

        # 2026-08-15 V7 Gate 2 — product_candidates에 visibility/
        # owner_company_id가, product_candidate_selections에 memo가
        # 추가됐다(비공개 후보 지원 + 최신 메모 투영, 요구사항 2). 이
        # 레거시 Migration은 그 전에 이 테이블들을 만들었으므로 위와
        # 동일한 이유로 국소 ADD COLUMN을 반영한다(신규 Migration
        # 파일의 실제 ALTER 문과 동일한 형태) — 그러지 않으면 아래
        # 라우터 경로(discover/approve/hold/reject 등 실제 ORM
        # INSERT/UPDATE)가 "no such column"으로 실패한다.
        conn.executescript(
            "BEGIN;\n"
            "ALTER TABLE product_candidates "
            "ADD COLUMN visibility VARCHAR(20) NOT NULL "
            "DEFAULT 'GLOBAL';\n"
            "ALTER TABLE product_candidates "
            "ADD COLUMN owner_company_id INTEGER;\n"
            "ALTER TABLE product_candidate_selections "
            "ADD COLUMN memo VARCHAR(1000);\n"
            "COMMIT;\n",
        )
        conn.close()

        self.engine = create_engine(f"sqlite:///{path}")
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )

    def tearDown(self):

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _to_recommended(self, service, db, ref):

        candidate, _ = service.discover(
            ProductCandidateDiscover(
                source_type="COUPANG_API",
                source_reference=ref,
                market="COUPANG",
                product_name=f"Migration 검증 상품 {ref}",
            ),
            correlation_id=f"migrated-{ref}",
        )
        service.apply_trend_analysis(
            candidate.id, company_id=1, trend_score=0.6, confidence=0.7,
            evidence_text="상승", correlation_id=f"trend-{ref}",
        )
        service.recommend(candidate.id, company_id=1, correlation_id=f"rec-{ref}")

        return candidate

    def test_list_get_evidence_decisions_on_migrated_schema(self):

        db = self.SessionLocal()
        fake_admin = SimpleNamespace(id=1, is_admin=True, company_id=1)

        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                ProductCandidateDiscover(
                    source_type="COUPANG_API",
                    source_reference="MIGRATED-LIST-1",
                    market="COUPANG",
                    product_name="목록 조회 검증 상품",
                ),
                correlation_id="migrated-list",
            )

            listed = pc_router.list_candidates(
                status=None, market=None, skip=0, limit=100,
                current_user=fake_admin, db=db,
            )
            self.assertEqual(len(listed), 1)

            fetched = pc_router.get_candidate(
                candidate.id, current_user=fake_admin, db=db,
            )
            self.assertEqual(fetched.status, "DISCOVERED")

            evidence = pc_router.get_candidate_evidence(
                candidate.id, current_user=fake_admin, db=db,
            )
            self.assertEqual(len(evidence), 1)

            decisions = pc_router.get_candidate_decisions(
                candidate.id, current_user=fake_admin, db=db,
            )
            self.assertEqual(decisions, [])

            # DISCOVERED 상태에서 승인 시도 — 라우터를 통해서도 차단되어야 함
            with self.assertRaises(BadRequestException):
                pc_router.approve_candidate(
                    candidate.id,
                    ProductCandidateDecisionRequest(memo="x"),
                    current_user=fake_admin,
                    db=db,
                )

        finally:
            db.close()

    def test_approve_path_on_migrated_schema(self):

        db = self.SessionLocal()
        fake_admin = SimpleNamespace(id=1, is_admin=True, company_id=1)

        try:
            service = ProductCandidateService(db)
            candidate = self._to_recommended(service, db, "APPROVE-1")

            approved = pc_router.approve_candidate(
                candidate.id,
                ProductCandidateDecisionRequest(memo="승인합니다"),
                current_user=fake_admin,
                db=db,
            )
            self.assertEqual(approved.status, "RECOMMENDED")
            self.assertEqual(approved.company_status, "APPROVED")

            decisions = pc_router.get_candidate_decisions(
                candidate.id, current_user=fake_admin, db=db,
            )
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["action"], "APPROVE")

        finally:
            db.close()

    def test_hold_path_on_migrated_schema(self):

        db = self.SessionLocal()
        fake_admin = SimpleNamespace(id=1, is_admin=True, company_id=1)

        try:
            service = ProductCandidateService(db)
            candidate = self._to_recommended(service, db, "HOLD-1")

            held = pc_router.hold_candidate(
                candidate.id,
                ProductCandidateDecisionRequest(memo="추가 검토 필요"),
                current_user=fake_admin,
                db=db,
            )
            self.assertEqual(held.status, "RECOMMENDED")
            self.assertEqual(held.company_status, "HELD")

        finally:
            db.close()

    def test_reject_path_on_migrated_schema(self):

        db = self.SessionLocal()
        fake_admin = SimpleNamespace(id=1, is_admin=True, company_id=1)

        try:
            service = ProductCandidateService(db)
            candidate = self._to_recommended(service, db, "REJECT-1")

            rejected = pc_router.reject_candidate(
                candidate.id,
                ProductCandidateDecisionRequest(memo="위험 높음"),
                current_user=fake_admin,
                db=db,
            )
            self.assertEqual(rejected.status, "RECOMMENDED")
            self.assertEqual(rejected.company_status, "REJECTED")

        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
