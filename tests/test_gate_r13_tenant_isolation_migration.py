"""
=========================================================
Homez OS

File : tests/test_gate_r13_tenant_isolation_migration.py

Gate R13(2026-08-14) — 테넌트 격리 Migration
(migrations/20260814_01_add_tenant_isolation_company_id.sql) 검증.

기존 15개 Migration을 순서대로 적용해 실제 homez.db와 동일한 스키마
상태를 임시 DB에 재현한 뒤, 이 신규 Migration을 적용해 성공/재적용
실패/rollback/Model↔DDL 일치를 확인한다. 실제 homez.db는 이 테스트
전체에서 전혀 사용하지 않는다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import DuplicateApplicationError
from app.database.migration_runner import MigrationRunner
from app.database.migration_runner import compute_checksum
from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate
from app.domains.decision.model import DecisionAuditLog
from app.domains.decision.model import DecisionEvaluation
from app.domains.decision.model import DecisionReview
from app.domains.decision.model import DecisionScore
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.settlement.model import MarketplaceSettlement

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

# 실제 homez.db의 schema_migrations 이력 순서(2026-08-14 읽기 전용
# 재확인 결과 그대로) — 이 신규 Migration 직전까지 전부 적용.
PRIOR_MIGRATIONS = [
    "20260727_00_create_funding_settlement_schema.sql",
    "20260727_add_settlement_ledger_unique_index.sql",
    "20260728_00_create_v24_v3_schema.sql",
    "20260729_00_create_coupang_integration_schema.sql",
    "20260730_00_create_decision_ai_schema.sql",
    "20260730_01_create_auth_session_schema.sql",
    "20260730_02_create_store_connection_schema.sql",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
    "20260802_00_create_media_listing_package_schema.sql",
    "20260802_01_create_account_recovery_schema.sql",
    "20260803_00_create_account_registration_schema.sql",
    "20260805_00_add_marketplace_listing_status_sync.sql",
    "20260807_00_add_marketplace_listing_rate_limit.sql",
    "20260808_00_create_listing_wizard_schema.sql",
    "20260810_00_create_audit_logs_schema.sql",
    "20260814_00_create_user_settings_schema.sql",
]

NEW_MIGRATION = "20260814_01_add_tenant_isolation_company_id.sql"

# ALTER 기반으로 company_id를 추가한 테이블 → Model 매핑(단순 컬럼 비교).
ALTER_TABLE_MODELS = {
    "coupang_product_options": CoupangProductOption,
    "coupang_product_notices": CoupangProductNotice,
    "coupang_profit_estimates": CoupangProfitEstimate,
    "decision_scores": DecisionScore,
    "decision_audit_logs": DecisionAuditLog,
    "product_candidate_decisions": ProductCandidateDecision,
}

# DROP+CREATE로 재생성한 테이블(복합 UNIQUE) → Model 매핑.
REBUILT_TABLE_MODELS = {
    "coupang_marketplace_products": CoupangMarketplaceProduct,
    "coupang_dry_run_attempts": CoupangDryRunAttempt,
    "coupang_integration_decisions": CoupangIntegrationDecision,
    "decision_evaluations": DecisionEvaluation,
    "decision_reviews": DecisionReview,
    "marketplace_settlements": MarketplaceSettlement,
}

# 신규 테이블.
NEW_TABLE_MODELS = {
    "product_candidate_selections": ProductCandidateSelection,
}

# 2026-08-15 V7 Gate 2 — decision_evaluations(company_policy_id/
# policy_source 추가, policy_id nullable 전환)와
# product_candidate_selections(memo 추가)는 이 R13 Migration이 만든
# 상태보다 Model이 더 앞서 있다(신규
# migrations/20260815_00_gate2_tenant_isolation_hardening.sql로 그
# 차이를 메운다). 이 파일은 "R13 Migration 적용 직후" 상태만 검증하는
# 것이 목적이므로, 그 두 테이블은 여기서 제외하고 Model 일치 여부는
# tests/test_gate2_tenant_isolation_hardening_migration.py가 전담
# 검증한다(test_v23_schema_migration.py/test_v24_v3_schema_migration.py
# 가 이미 확립한 것과 동일한 패턴 — "손댄 테이블은 그 다음 Migration의
# 전담 테스트로 이관").
GATE2_SUPERSEDED_TABLES = {
    "decision_evaluations",
    "product_candidate_selections",
}


def _read(filename: str) -> str:

    path = os.path.join(MIGRATIONS_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


def _db_columns(conn, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _extract_rollback_sql(content: str) -> str:
    """
    파일 하단의 "-- Rollback (실행하지 않음 ...)" 주석 블록에서 실제
    SQL만 뽑아낸다(각 줄 맨 앞의 "-- " 접두사를 제거).
    """

    lines = content.splitlines()
    rollback_lines = []
    in_sql = False

    for line in lines:
        stripped = line.strip()

        if not in_sql:
            # 실제 SQL은 "-- BEGIN;"부터 시작한다(그 앞은 rollback
            # 취지를 설명하는 한국어 prose 주석이라 SQL로 취급하지
            # 않는다).
            if stripped == "-- BEGIN;":
                in_sql = True
                rollback_lines.append("BEGIN;")
            continue

        if stripped.startswith("-- "):
            rollback_lines.append(stripped[3:])
        elif stripped == "--":
            continue

    return "\n".join(rollback_lines)


class MigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.content = _read(NEW_MIGRATION)

        non_comment_lines = [
            line for line in cls.content.splitlines()
            if not line.strip().startswith("--")
        ]
        cls.executed_sql = "\n".join(non_comment_lines)

    def test_exactly_one_begin_and_commit(self):

        self.assertEqual(
            len(re.findall(r"^BEGIN;", self.executed_sql, re.M)), 1,
        )
        self.assertEqual(
            len(re.findall(r"^COMMIT;", self.executed_sql, re.M)), 1,
        )

    def test_no_foreign_keys_or_if_not_exists(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertNotIn("REFERENCES", self.executed_sql.upper())
        self.assertNotIn("IF NOT EXISTS", self.executed_sql.upper())

    def test_touches_only_expected_tables(self):

        expected = (
            set(ALTER_TABLE_MODELS)
            | set(REBUILT_TABLE_MODELS)
            | set(NEW_TABLE_MODELS)
        )

        altered = set(
            re.findall(
                r"^ALTER TABLE (\w+) ADD COLUMN", self.executed_sql, re.M,
            ),
        )
        dropped = set(
            re.findall(r"^DROP TABLE (\w+);", self.executed_sql, re.M),
        )
        # REBUILT_TABLE_MODELS 6개는 MigrationRunner 호환을 위해
        # "<이름>_new로 CREATE → 원본 DROP → RENAME" 기법을 쓴다(위
        # 파일 헤더 주석 (B) 참고) — 여기서는 "_new" 접미사를 제거해
        # 최종 테이블 이름 기준으로 비교한다.
        created_raw = set(
            re.findall(r"^CREATE TABLE (\w+) \(", self.executed_sql, re.M),
        )
        created = {
            name[:-4] if name.endswith("_new") else name
            for name in created_raw
        }
        renamed = set(
            re.findall(
                r"^ALTER TABLE (\w+)_new RENAME TO \w+;",
                self.executed_sql, re.M,
            ),
        )

        self.assertEqual(altered, set(ALTER_TABLE_MODELS))
        self.assertEqual(dropped, set(REBUILT_TABLE_MODELS))
        self.assertEqual(
            created, set(REBUILT_TABLE_MODELS) | set(NEW_TABLE_MODELS),
        )
        self.assertEqual(renamed, set(REBUILT_TABLE_MODELS))
        self.assertEqual(altered | dropped | created, expected)

    def test_does_not_modify_prior_migration_files(self):
        """
        이미 실제 homez.db에 APPLIED로 기록된 15개 파일은 checksum이
        고정돼 있다 — 이번 작업에서 손대지 않았어야 한다(git 추적
        대상이므로 이 테스트는 파일이 여전히 존재하는지만 확인한다).
        """

        for filename in PRIOR_MIGRATIONS:
            path = os.path.join(MIGRATIONS_DIR, filename)
            self.assertTrue(
                os.path.exists(path), f"{filename} 파일이 없습니다.",
            )


class MigrationApplyTestCase(unittest.TestCase):
    """
    실제 homez.db와 동일한 스키마 상태(기존 15개 Migration 순서대로
    적용)를 임시 DB에 재현한 뒤, 신규 Migration을 적용해 검증한다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.prior_sql = [_read(name) for name in PRIOR_MIGRATIONS]
        self.new_sql = _read(NEW_MIGRATION)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _apply_prior_migrations(self, conn):

        for sql in self.prior_sql:
            conn.executescript(sql)

    def test_full_chain_applies_cleanly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])
        finally:
            conn.close()

    def test_reapply_fails_explicitly(self):
        """
        재적용 시도는 안전하게 실패해야 한다(DROP TABLE 대상 테이블이
        이미 없거나, ALTER ADD COLUMN 대상 컬럼이 이미 있어 SQLite가
        거부한다) — 조용히 무시되지 않는다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()

    def test_alter_table_columns_match_model(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            for table_name, model_cls in ALTER_TABLE_MODELS.items():
                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_rebuilt_table_columns_match_model(self):
        """
        2026-08-15 V7 Gate 2 — decision_evaluations는
        GATE2_SUPERSEDED_TABLES라 제외한다(위 상수 정의 참고). 이
        메서드가 검증하는 "R13 적용 직후" 스키마 자체는 전혀 바뀌지
        않았다 — 단지 그 스키마와 비교할 대상을 "지금의 최신 Model"
        대신 별도로 마련해야 하는데, 그건 Gate 2 전담 테스트의 역할
        이다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            for table_name, model_cls in REBUILT_TABLE_MODELS.items():
                if table_name in GATE2_SUPERSEDED_TABLES:
                    continue

                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_new_table_columns_match_model(self):
        """
        2026-08-15 V7 Gate 2 — product_candidate_selections는
        GATE2_SUPERSEDED_TABLES라 제외한다(memo 컬럼이 이 R13
        Migration 이후에 추가됨). 위 test_rebuilt_table_columns_match_
        model과 동일한 사유.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            remaining = {
                name: model_cls
                for name, model_cls in NEW_TABLE_MODELS.items()
                if name not in GATE2_SUPERSEDED_TABLES
            }

            for table_name, model_cls in remaining.items():
                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_composite_unique_constraints_enforced(self):
        """
        (company_id, idempotency_key) 복합 UNIQUE가 실제로 회사별
        독립인지 — 같은 idempotency_key라도 company_id가 다르면 둘 다
        삽입되고, 같은 (company_id, idempotency_key) 조합은 거부됨을
        실제 INSERT로 확인한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO coupang_marketplace_products "
                "(id, company_id, marketplace, sales_method, "
                "product_candidate_id, external_vendor_sku, "
                "seller_product_name, safety_stock, "
                "marketplace_exposure_stock, stock_review_required, "
                "available_stock, overseas_purchase_agency, pcc_needed, "
                "status, validation_status, idempotency_key, created_at, "
                "updated_at) VALUES "
                "(1, 1, 'COUPANG', 'MARKETPLACE', 1, 'sku-a', '상품A', "
                "0, 0, 0, 0, 0, 0, 'DRAFT', 'NOT_VALIDATED', "
                "'shared-key', '2026-01-01', '2026-01-01')",
            )
            # 다른 회사가 같은 idempotency_key를 쓰는 것은 허용된다.
            conn.execute(
                "INSERT INTO coupang_marketplace_products "
                "(id, company_id, marketplace, sales_method, "
                "product_candidate_id, external_vendor_sku, "
                "seller_product_name, safety_stock, "
                "marketplace_exposure_stock, stock_review_required, "
                "available_stock, overseas_purchase_agency, pcc_needed, "
                "status, validation_status, idempotency_key, created_at, "
                "updated_at) VALUES "
                "(2, 2, 'COUPANG', 'MARKETPLACE', 1, 'sku-b', '상품B', "
                "0, 0, 0, 0, 0, 0, 'DRAFT', 'NOT_VALIDATED', "
                "'shared-key', '2026-01-01', '2026-01-01')",
            )
            conn.commit()

            count = conn.execute(
                "SELECT COUNT(*) FROM coupang_marketplace_products "
                "WHERE idempotency_key = 'shared-key'",
            ).fetchone()[0]
            self.assertEqual(count, 2)

            # 같은 회사가 같은 key를 재사용하면 UNIQUE 위반.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO coupang_marketplace_products "
                    "(id, company_id, marketplace, sales_method, "
                    "product_candidate_id, external_vendor_sku, "
                    "seller_product_name, safety_stock, "
                    "marketplace_exposure_stock, stock_review_required, "
                    "available_stock, overseas_purchase_agency, "
                    "pcc_needed, status, validation_status, "
                    "idempotency_key, created_at, updated_at) VALUES "
                    "(3, 1, 'COUPANG', 'MARKETPLACE', 1, 'sku-c', "
                    "'상품C', 0, 0, 0, 0, 0, 0, 'DRAFT', "
                    "'NOT_VALIDATED', 'shared-key', '2026-01-01', "
                    "'2026-01-01')",
                )
        finally:
            conn.close()

    def test_product_candidate_selection_unique_per_company(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO product_candidates "
                "(id, candidate_key, source_type, source_reference, "
                "market, product_name, status, discovered_at, "
                "created_at, updated_at) VALUES "
                "(1, 'key-1', 'TREND', 'ref-1', 'COUPANG', '상품', "
                "'RECOMMENDED', '2026-01-01', '2026-01-01', "
                "'2026-01-01')",
            )
            conn.execute(
                "INSERT INTO product_candidate_selections "
                "(id, candidate_id, company_id, status, decided_at, "
                "updated_at) VALUES "
                "(1, 1, 1, 'APPROVED', '2026-01-01', '2026-01-01')",
            )
            # 다른 회사는 같은 candidate에 대해 독립적으로 결정 가능.
            conn.execute(
                "INSERT INTO product_candidate_selections "
                "(id, candidate_id, company_id, status, decided_at, "
                "updated_at) VALUES "
                "(2, 1, 2, 'REJECTED', '2026-01-01', '2026-01-01')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO product_candidate_selections "
                    "(id, candidate_id, company_id, status, decided_at, "
                    "updated_at) VALUES "
                    "(3, 1, 1, 'REJECTED', '2026-01-01', '2026-01-01')",
                )
        finally:
            conn.close()


class MigrationRollbackTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.prior_sql = [_read(name) for name in PRIOR_MIGRATIONS]
        self.new_content = _read(NEW_MIGRATION)
        self.new_sql = "\n".join(
            line for line in self.new_content.splitlines()
            if not line.strip().startswith("--")
        )
        self.rollback_sql = _extract_rollback_sql(self.new_content)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_rollback_restores_pre_migration_schema(self):

        conn = sqlite3.connect(self.db_path)
        try:
            for sql in self.prior_sql:
                conn.executescript(sql)

            # 롤백 비교 기준 스냅샷(신규 Migration 적용 직전 상태).
            before_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }

            conn.executescript(self.new_sql)

            self.assertTrue(self.rollback_sql.strip())
            conn.executescript(self.rollback_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            after_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertEqual(before_tables, after_tables)

            # company_id가 rollback 이후 제거됐는지 확인(ALTER 기반 6개).
            for table_name in ALTER_TABLE_MODELS:
                cols = {
                    row[1]
                    for row in conn.execute(
                        f"PRAGMA table_info({table_name})",
                    ).fetchall()
                }
                self.assertNotIn(
                    "company_id", cols,
                    f"{table_name}: rollback 후에도 company_id가 남아있음.",
                )

            # 재생성 테이블도 company_id 없이 원본 UNIQUE로 복원됐는지.
            for table_name in REBUILT_TABLE_MODELS:
                cols = {
                    row[1]
                    for row in conn.execute(
                        f"PRAGMA table_info({table_name})",
                    ).fetchall()
                }
                self.assertNotIn(
                    "company_id", cols,
                    f"{table_name}: rollback 후에도 company_id가 남아있음.",
                )

            self.assertNotIn(
                "product_candidate_selections", after_tables,
            )
        finally:
            conn.close()


class OfficialMigrationRunnerTestCase(unittest.TestCase):
    """
    이 저장소의 공식 MigrationRunner(app/database/migration_runner.py)
    + 실제 migrations/ 디렉터리(17개 파일 전체, 새 파일 포함)를 그대로
    사용한 리허설. 실제 homez.db는 사용하지 않는다 — db_path만 임시
    파일이고, migrations_dir은 실제 저장소 경로다.

    **리허설 중 실제로 발견한 한계(수정하지 않고 있는 그대로 보고,
    CTO 결정 대기)**: raw sqlite3.executescript로는 신규 Migration이
    100% 성공하지만(위 MigrationApplyTestCase 전부 통과), 공식
    Runner의 apply_pending() 사전 충돌 검사는 이 파일을
    DuplicateApplicationError로 거부한다. 원인: 이 Migration은
    "<이름>_new로 CREATE → 원본 DROP → RENAME" 기법으로 테이블 이름
    충돌은 피했지만(파일 헤더 주석 (B) 참고), 그 위에 다시 만드는
    인덱스(예: ix_coupang_marketplace_products_id)는 여전히 최종
    이름을 그대로 쓴다 — Runner의 `_extract_target_objects()`/
    `apply_pending()` 사전 검사는 파일 전체를 한 번에 정적 스캔해
    "CREATE INDEX <이름>"이 하나라도 나오면 그 이름이 **현재(스크립트
    실행 전) DB**에 이미 있는지만 본다. 같은 파일 안에서 그 앞에
    `DROP TABLE`이 있어서 실제 실행 시점에는 그 인덱스가 이미
    사라진다는 사실(순서/인과관계)을 반영하지 않는다 — 순수 텍스트
    수준의 "전체 파일 대 현재 DB 스냅샷" 비교이기 때문이다. 즉 "테이블
    재생성(DROP+CREATE 계열)을 포함하는 Migration"이라는 새로운
    유형 자체가 이 Runner에서 처음 나온 것이고, 그 사전 검사 로직이
    아직 이 유형을 지원하지 않는다.

    이 한계를 피하려면 인덱스 이름도 임시로 바꿔야 하는데, 그러면
    최종 스키마의 인덱스 이름이 Model과 달라져(SQLAlchemy Model의
    index=True가 관례적으로 만드는 이름과 불일치) 이번 감사의 다른
    드리프트 테스트(Model↔DB 컬럼/제약 비교)와 새로운 불일치를
    만든다 — 그래서 그 우회로 대신 이 사실을 있는 그대로 보고하는
    쪽을 선택했다. 근본 수정(각 CREATE TABLE/INDEX 대상을 그 앞의
    DROP TABLE/DROP INDEX로 상쇄해서 충돌 후보에서 제외하는 것)은
    app/database/migration_runner.py 자체를 고쳐야 하는데, 이 파일은
    이번 Gate R13 Whitelist(coupang/decision/product_candidate/
    funding/settlement) 밖의 공용 인프라라 임의로 손대지 않았다 —
    별도 승인 후 진행해야 한다.

    **결론: 이 신규 Migration을 실제 homez.db에 적용할 때는(CTO 본인
    수행) 현재 코드 상태로는 공식 MigrationRunner.apply_pending()이
    아니라, 이 Migration 파일이 이미 검증된 raw 적용 경로(sqlite3
    executescript, 위 MigrationApplyTestCase가 무결성/재적용실패/
    rollback까지 전부 검증함)를 사용하거나, MigrationRunner의 충돌
    검사를 먼저 보강해야 한다.**
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.runner = MigrationRunner(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
        )

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_prior_migrations_apply_cleanly_via_runner(self):
        """
        신규 Migration 이전 16개(기존 15개 + 오늘자 user_settings)는
        공식 Runner로 정상 적용된다 — Runner 자체나 기존 파일들의
        문제가 아니라, 신규 파일의 재생성(rebuild) 패턴 특유의 문제임을
        분리해서 확인한다.

        2026-08-15 V7 Gate 2 — 실제 migrations/ 디렉터리에 이 R13
        파일보다 나중 파일(20260815_00_gate2_tenant_isolation_
        hardening.sql)이 추가됐다. `self.runner.plan_pending(conn)`은
        디렉터리 전체를 스캔하므로, 예전처럼 "NEW_MIGRATION(R13 자신)
        하나만 제외"하면 Gate 2 파일까지 prior_plan에 섞여 들어가
        이 테스트의 원래 의도("R13 직전까지의 16개만")가 깨진다. 이
        테스트의 의도는 파일 목록을 하드코딩한 PRIOR_MIGRATIONS
        자체이므로, "PRIOR_MIGRATIONS에 포함된 것만" 필터링하도록
        고쳐 그 의도를 그대로 유지한다(Gate 2 이후 또 새 Migration이
        추가돼도 이 필터는 다시 깨지지 않는다).
        """

        conn = sqlite3.connect(self.db_path)
        try:
            for filename in PRIOR_MIGRATIONS:
                content = os.path.join(MIGRATIONS_DIR, filename)
                self.assertTrue(os.path.exists(content))

            # PRIOR_MIGRATIONS에 명시된 16개만 골라 적용한다(R13 자신과
            # 그 이후에 추가된 어떤 Migration도 여기 섞이지 않는다).
            plan = self.runner.plan_pending(conn)
            prior_plan = [
                p for p in plan if p.name in PRIOR_MIGRATIONS
            ]
            self.assertEqual(
                [p.name for p in prior_plan], PRIOR_MIGRATIONS,
            )

            self.runner.ensure_history_table(conn)
            for path in prior_plan:
                conn.executescript(path.read_text(encoding="utf-8"))
                checksum = compute_checksum(path)
                conn.execute(
                    "INSERT INTO schema_migrations (filename, checksum, "
                    "applied_at, status, execution_ms, notes) VALUES "
                    "(?, ?, datetime('now'), 'APPLIED', 0, 'test')",
                    (path.name, checksum),
                )
            conn.commit()

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            # 참고: 이 시점에서 diagnose()를 다시 호출하면
            # PartialApplicationError가 발생한다 — 신규 Migration의
            # 대상 인덱스 이름 일부가 "이미 존재"(구 테이블 소속)로,
            # 나머지 신규 테이블/컬럼은 "아직 없음"으로 갈려 보이기
            # 때문이다. 이것도 위 test_new_migration_is_rejected_by_
            # runner_index_collision_precheck와 동일한 근본 원인
            # (DROP 인과관계를 모르는 정적 스캔)이 diagnose() 경로에서
            # 나타난 것 — 별도로 재확인하지 않는다(중복 문서화 방지).
        finally:
            conn.close()

    def test_new_migration_applies_cleanly_via_runner_after_precheck_fix(
        self,
    ):
        """
        2026-08-14 수정 확인 — 이전에는 신규 Migration을 공식
        Runner.apply_pending()으로 적용하면 DuplicateApplicationError가
        발생했다(raw executescript로는 100% 성공하는 동일 SQL인데도,
        Runner의 정적 사전 검사가 "같은 파일 안에서 DROP TABLE로 먼저
        제거된 뒤 재사용되는 인덱스 이름"을 이해하지 못해 오탐한
        것 — 재현·근거는 CTO 보고 및 git 이력 참고).

        `app/database/migration_runner.py`에
        `_extract_superseded_targets()`를 추가해 국소 수정했다 — 같은
        파일 안에서 CREATE TABLE/INDEX보다 앞선 줄에 그 대상(테이블
        자신, 또는 인덱스가 속한 테이블)을 DROP TABLE한 적이 있으면
        "이미 존재하는 충돌"로 취급하지 않는다. 이 테스트는 그 수정
        이후 실제로 공식 Runner 경로가 이 Migration을 예외 없이
        정상 적용하고, 그 결과 스키마가 Model과 정확히 일치하며,
        schema_migrations에 APPLIED로 정확히 1행만 기록됨을 확인한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            applied = self.runner.apply_pending(conn)
            self.assertIn(NEW_MIGRATION, applied)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])

            # 재생성 6개 테이블 + ALTER 6개 테이블 + 신규 1개 테이블
            # 전부 company_id 컬럼을 실제로 갖고 있는지 재확인한다
            # (Runner 경로로 적용된 결과가 raw executescript 리허설
            # 결과와 동일해야 한다).
            for table_name in {**ALTER_TABLE_MODELS, **REBUILT_TABLE_MODELS}:
                columns = {
                    row[1] for row in conn.execute(
                        f"PRAGMA table_info({table_name})",
                    ).fetchall()
                }
                self.assertIn(
                    "company_id", columns,
                    f"{table_name}에 company_id 컬럼이 없습니다.",
                )

            for table_name in NEW_TABLE_MODELS:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                self.assertIsNotNone(row, f"{table_name} 테이블이 없습니다.")

            history_row = conn.execute(
                "SELECT status FROM schema_migrations WHERE filename=?",
                (NEW_MIGRATION,),
            ).fetchone()
            self.assertIsNotNone(history_row)
            self.assertEqual(history_row[0], "APPLIED")

            # 같은 파일을 다시 적용 시도해도 SQL이 재실행되지 않아야
            # 한다(이력에 checksum이 일치하는 채로 이미 있으므로
            # diagnose()가 already_applied로 분류해 조용히 건너뛴다 —
            # 이번 수정이 정당한 재적용 방지 자체를 느슨하게 만들지
            # 않았는지 확인. exception이 아니라 빈 리스트가 정상이다
            # — DuplicateApplicationError는 "같은 apply_pending() 호출
            # 안에서 서로 다른 두 미적용 파일이 대상을 충돌시키는"
            # 별도 시나리오에서만 발생한다, tests/test_migration_runner.py
            # 참고).
            applied_again = self.runner.apply_pending(conn)
            self.assertEqual(applied_again, [])

            diagnosis = self.runner.diagnose(conn)
            self.assertIn(NEW_MIGRATION, diagnosis["already_applied"])
            self.assertNotIn(NEW_MIGRATION, diagnosis["pending"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
