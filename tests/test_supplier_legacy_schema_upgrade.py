"""
=========================================================
Homez OS

File : tests/test_supplier_legacy_schema_upgrade.py

CP-1(2026-08-21, CTO 지시) — suppliers 운영 스키마 드리프트 검증.
실제 운영 DB(그리고 개발 DB)의 suppliers 테이블은
migrations/20260816_00_create_v7_gate9_core_foundation_schema.sql이
만든 예전 8개 컬럼(회사별 비공개 공급처 등록부)뿐이었고, 현재
app/domains/supplier/model.py::Supplier는 20개 컬럼짜리 "전역 공개
카탈로그" 모델이다 — 이 갭 때문에 실제로
`GET /sourcing/suppliers/public` 호출이
`sqlite3.OperationalError: no such column: suppliers.code`로 500을
반환했다(2026-08-21 Pre-Live 라운드에서 격리 E2E 중 재현 확인).

이 파일은 **반드시 MigrationRunner로 실제 migrations/*.sql을 순서
대로 적용해 DB를 만든다** — `Base.metadata.create_all()`은 항상
Model 기준 최신 스키마를 즉시 만들어버려 이런 드리프트를 원천적으로
잡아낼 수 없다(그래서 지금까지 이 결함이 전체 회귀에서 한 번도
드러나지 않았다). `test_create_all_would_not_have_caught_this`가
그 사실을 실제로 증명하는 영구 회귀 문서 테스트다 — 앞으로 누군가
supplier 관련 테스트를 create_all() 기반으로 되돌리면 이 테스트가
그 차이를 눈에 보이게 한다.
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.migration_runner import MigrationRunner
from app.domains.supplier.model import Supplier

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = Path(REPO_ROOT) / "migrations"

FOUNDATION_MIGRATION_NAME = "20260816_00_create_v7_gate9_core_foundation_schema.sql"
NEW_MIGRATION_NAME = "20260821_01_add_supplier_public_directory_columns.sql"

_LEGACY_ONLY_COLUMNS = {
    "company_id", "business_number", "ceo", "phone", "email",
    "address", "active",
}


def _db_columns(conn: sqlite3.Connection, table: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


class SupplierLegacySchemaUpgradeTestCase(unittest.TestCase):
    """실제 운영 DB와 동일한 레거시 8컬럼 suppliers 상태를 재현한
    뒤(2단계: 20260816_00까지만 별도 적용 + 레거시 행 직접 시딩),
    저장소의 전체 migrations/를 마저 적용해(공식 실행 경로와 동일)
    기존 행 보존·안전한 기본값·재적용 안전성을 검증한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)

        all_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        upto_foundation = [
            p for p in all_files if p.name <= FOUNDATION_MIGRATION_NAME
        ]
        self.stage1_dir = Path(tempfile.mkdtemp())
        for p in upto_foundation:
            shutil.copy(p, self.stage1_dir / p.name)

        stage1_runner = MigrationRunner(self.db_path, self.stage1_dir)
        conn = sqlite3.connect(str(self.db_path))
        try:
            stage1_runner.ensure_history_table(conn)
            applied = stage1_runner.apply_pending(conn)
            self.assertTrue(
                any(FOUNDATION_MIGRATION_NAME in str(p) for p in applied),
            )

            # suppliers.company_id는 레거시 스키마에서 companies(id)를
            # 실제로 FK 참조한다 — fixture도 진짜 companies 행을 먼저
            # 만들어야 foreign_key_check가 정직하게 통과한다.
            conn.execute(
                "INSERT INTO companies (id, name, active) VALUES "
                "(100, '레거시회사A', 1)",
            )
            conn.execute(
                "INSERT INTO companies (id, name, active) VALUES "
                "(200, '레거시회사B', 1)",
            )

            # 실제 존재할 수 있는 "회사별 비공개 공급처 등록부" 레거시
            # 행을 2건 시딩한다(회사 100은 active=1, 회사 200은
            # active=0) — 회사 A/B 격리 시나리오와 동일한 모양.
            conn.execute(
                "INSERT INTO suppliers (id, company_id, name, "
                "business_number, ceo, phone, email, address, active) "
                "VALUES (1, 100, '레거시공급처A', '111-11-11111', "
                "'홍길동', '010-1111-1111', 'a@legacy.example', "
                "'서울시 A', 1)",
            )
            conn.execute(
                "INSERT INTO suppliers (id, company_id, name, "
                "business_number, ceo, phone, email, address, active) "
                "VALUES (2, 200, '레거시공급처B', '222-22-22222', "
                "'김철수', '010-2222-2222', 'b@legacy.example', "
                "'서울시 B', 0)",
            )
            conn.commit()
        finally:
            conn.close()

        self.runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)
        shutil.rmtree(self.stage1_dir, ignore_errors=True)

    def test_new_migration_file_is_discovered(self):

        names = [p.name for p in self.runner.list_migration_files()]
        self.assertIn(NEW_MIGRATION_NAME, names)

    def test_apply_preserves_existing_legacy_rows(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            applied = self.runner.apply_pending(conn)
            self.assertIn(NEW_MIGRATION_NAME, applied)

            rows = conn.execute(
                "SELECT id, company_id, name, business_number, ceo, "
                "phone, email, address, active FROM suppliers "
                "ORDER BY id",
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                rows[0],
                (1, 100, "레거시공급처A", "111-11-11111", "홍길동",
                 "010-1111-1111", "a@legacy.example", "서울시 A", 1),
            )
            self.assertEqual(
                rows[1],
                (2, 200, "레거시공급처B", "222-22-22222", "김철수",
                 "010-2222-2222", "b@legacy.example", "서울시 B", 0),
            )
        finally:
            conn.close()

    def test_new_columns_default_safely_regardless_of_legacy_active(self):
        """레거시 active 값(1건은 1, 1건은 0)과 무관하게 신규
        is_active는 둘 다 0(비공개)이어야 한다 — 그대로 복사하면
        회사별 비공개 등록 행이 전역 공개 디렉터리에 즉시 노출되는
        회사 격리 위반이 된다."""

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            rows = conn.execute(
                "SELECT is_active, is_verified, code, created_at, "
                "trust_score, supplier_type, api_key, api_secret "
                "FROM suppliers ORDER BY id",
            ).fetchall()
            self.assertEqual(len(rows), 2)
            for row in rows:
                (is_active, is_verified, code, created_at,
                 trust_score, supplier_type, api_key, api_secret) = row
                self.assertEqual(is_active, 0)
                self.assertEqual(is_verified, 0)
                self.assertIsNone(code)
                self.assertIsNone(created_at)
                self.assertEqual(trust_score, 0)
                self.assertEqual(supplier_type, "GENERAL")
                self.assertIsNone(api_key)
                self.assertIsNone(api_secret)
        finally:
            conn.close()

    def test_model_columns_all_present_legacy_columns_preserved(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            model_cols = {c.name for c in Supplier.__table__.columns}
            db_cols = set(_db_columns(conn, "suppliers").keys())

            missing_from_db = model_cols - db_cols
            self.assertEqual(
                missing_from_db, set(),
                f"Model 컬럼이 DB에 없음: {missing_from_db}",
            )

            legacy_only = db_cols - model_cols
            self.assertEqual(legacy_only, _LEGACY_ONLY_COLUMNS)
        finally:
            conn.close()

    def test_unique_code_index_allows_multiple_null_legacy_rows(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            count = conn.execute(
                "SELECT COUNT(*) FROM suppliers WHERE code IS NULL",
            ).fetchone()[0]
            self.assertEqual(count, 2)
        finally:
            conn.close()

    def test_integrity_and_foreign_key_check_pass(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            self.assertEqual(
                conn.execute("PRAGMA integrity_check").fetchone()[0], "ok",
            )
            self.assertEqual(
                conn.execute("PRAGMA foreign_key_check").fetchall(), [],
            )
        finally:
            conn.close()

    def test_reapply_all_migrations_is_safe_noop(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)
            second_pass = self.runner.apply_pending(conn)
            self.assertEqual(second_pass, [])
        finally:
            conn.close()

    def test_public_directory_endpoint_no_longer_500s_and_hides_private_fields(self):
        """2026-08-21 격리 E2E에서 실제로 재현된 500 결함
        (`no such column: suppliers.code`)이 해소됐는지 실제 엔드포인트
        함수를 직접 호출해 확인한다(이 저장소는 httpx 미설치로
        TestClient를 쓰지 않는다 — 기존 관례와 동일하게 라우터 함수를
        직접 호출). 응답에 api_key/api_secret/description/
        metadata_json 등 비공개 필드가 전혀 포함되지 않는지도 함께
        확인한다."""

        from app.domains.source.router import list_public_suppliers
        from app.domains.source.schema import SupplierPublicDirectoryResponse

        engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)
            conn.close()

            # 회사 100의 레거시 행(id=1)만 명시적으로 공개 전환하면서,
            # 절대 노출되면 안 되는 비공개 필드에도 실제 값을 채운다
            # (레거시 행이라 원래 NULL이었던 자리) — 이 값이 응답에
            # 새어나오지 않는지가 이 테스트의 핵심이다.
            db.execute(
                Supplier.__table__.update()
                .where(Supplier.id == 1)
                .values(
                    is_active=True, code="LEGACY-A",
                    api_key="SHOULD-NEVER-LEAK",
                    api_secret="SHOULD-NEVER-LEAK",
                    description="내부용 메모", metadata_json='{"x":1}',
                ),
            )
            db.commit()

            # 이 호출 자체가 500을 던지지 않는다는 것이 곧 원래 결함
            # (`no such column: suppliers.code`)의 해소 증거다.
            result = list_public_suppliers(current_user=None, db=db)

            self.assertEqual(len(result), 1)
            supplier = result[0]
            self.assertEqual(supplier.id, 1)
            self.assertEqual(supplier.code, "LEGACY-A")

            # FastAPI가 실제로 response_model=list[SupplierPublicDirectory
            # Response]로 직렬화할 때와 동일하게, 스키마를 통과시킨
            # 결과에 비공개 필드가 물리적으로 존재하지 않는지 확인한다
            # (raw ORM 객체가 아니라 실제 HTTP 응답에 나갈 모양 기준).
            serialized = SupplierPublicDirectoryResponse.model_validate(
                supplier,
            ).model_dump()

            self.assertEqual(
                set(serialized.keys()),
                {"id", "name", "code", "supplier_type", "website",
                 "is_active", "is_verified"},
            )
            for private_field in (
                "api_key", "api_secret", "description", "metadata_json",
                "search_keywords", "trust_score", "delivery_score",
            ):
                self.assertNotIn(private_field, serialized)
            self.assertNotIn("SHOULD-NEVER-LEAK", str(serialized))
        finally:
            db.close()
            engine.dispose()

    def test_create_all_would_not_have_caught_this(self):
        """영구 회귀 문서화 — `Base.metadata.create_all()`은 Model
        기준 최신 스키마를 즉시 만들어 레거시 8컬럼 상태를 절대
        재현하지 못한다는 것을 실제로 증명한다. 앞으로 누군가
        supplier 관련 테스트를 create_all() 기반으로 되돌리면 이
        테스트의 두 결과가 달라진다는 사실 자체가 "그 방식으로는 이
        Migration 결함을 잡을 수 없다"는 경고다."""

        fd, create_all_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            engine = create_engine(f"sqlite:///{create_all_path}")
            Base.metadata.create_all(bind=engine)

            conn = sqlite3.connect(create_all_path)
            try:
                create_all_cols = set(_db_columns(conn, "suppliers").keys())
            finally:
                conn.close()
            engine.dispose()

            # create_all()은 Model 컬럼만 즉시 만든다 — 레거시 컬럼이
            # 전혀 없다(드리프트를 재현할 수 없다는 증거).
            self.assertEqual(
                create_all_cols & _LEGACY_ONLY_COLUMNS, set(),
                "create_all()이 레거시 컬럼을 포함했다 — 이 테스트의 "
                "전제가 깨졌다.",
            )

            migration_conn = sqlite3.connect(str(self.db_path))
            try:
                self.runner.ensure_history_table(migration_conn)
                self.runner.apply_pending(migration_conn)
                migration_cols = set(
                    _db_columns(migration_conn, "suppliers").keys(),
                )
            finally:
                migration_conn.close()

            # 실제 Migration 경로만 레거시 컬럼을 보존한다 — 이 차이가
            # 곧 "왜 create_all() 기반 테스트가 이 결함을 놓쳤는가"의
            # 실증이다.
            self.assertTrue(
                _LEGACY_ONLY_COLUMNS.issubset(migration_cols),
            )
            self.assertFalse(
                _LEGACY_ONLY_COLUMNS.issubset(create_all_cols),
            )
        finally:
            if os.path.exists(create_all_path):
                os.remove(create_all_path)


class SupplierCredentialFieldUnreachableTestCase(unittest.TestCase):
    """
    CA-4(2026-08-21 CTO 지시) — Supplier.api_key/api_secret은 공개
    디렉터리 원칙과 충돌하는 설계 잔재다(model.py 주석 참고). DB
    없이 구조적으로 "이 필드를 채우는 도달 가능한 HTTP 경로가 없다"
    는 것을 코드 레벨로 증명한다 — 누군가 `supplier_router`를 다시
    마운트하면 이 테스트가 즉시 깨진다.
    """

    def test_legacy_supplier_router_is_not_mounted_in_app(self):

        main_source = (Path(REPO_ROOT) / "app" / "main.py").read_text(
            encoding="utf-8",
        )
        # import는 있어도 되지만(주석에 남겨둘 수 있음), 실제
        # app.include_router(supplier_router) 호출이 주석 아닌
        # 실행 코드로 존재하면 안 된다.
        active_lines = [
            line for line in main_source.splitlines()
            if "supplier_router" in line and not line.strip().startswith("#")
        ]
        mounted = any(
            "include_router" in line and "supplier_router" in line
            for line in active_lines
        )
        self.assertFalse(
            mounted,
            "supplier_router가 app.main에 실제로 마운트되어 있습니다 — "
            "Supplier.api_key/api_secret이 쓰기 가능해졌으므로 CA-4 "
            "재검토가 필요합니다.",
        )

    def test_legacy_api_router_dead_file_is_not_imported_anywhere(self):
        """app/api/router.py도 supplier_router를 참조하지만, 그 파일
        자체가 앱 어디에서도 import되지 않는 죽은 코드임을 확인한다
        (실수로 다시 연결되면 이 테스트가 깨진다)."""

        app_dir = Path(REPO_ROOT) / "app"
        importers = []
        for path in app_dir.rglob("*.py"):
            if path == app_dir / "api" / "router.py":
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "app.api.router" in text or "from app.api import router" in text:
                importers.append(str(path))

        self.assertEqual(
            importers, [],
            f"app/api/router.py를 import하는 파일이 생겼습니다: {importers} "
            "— supplier_router 도달 가능성을 다시 확인해야 합니다.",
        )

    def test_public_directory_response_schema_never_declares_credential_fields(self):

        from app.domains.source.schema import SupplierPublicDirectoryResponse

        field_names = set(SupplierPublicDirectoryResponse.model_fields.keys())
        self.assertNotIn("api_key", field_names)
        self.assertNotIn("api_secret", field_names)

    def test_orm_assignment_of_api_key_raises_structured_error(self):
        """Audit(2026-08-21, CTO 후속 지시) — 도달 불가능하다는 사실
        만으로는 방어가 아니다. 실제로 값을 대입하려는 시도를 ORM
        레벨에서 구조화 오류로 막는지 직접 확인한다."""

        from app.core.exceptions import BadRequestException
        import app.domains.product.model  # noqa: F401 (Supplier.products 관계 해석용)
        from app.domains.supplier.model import Supplier

        supplier = Supplier(name="테스트 공급처")

        with self.assertRaises(BadRequestException) as ctx:
            supplier.api_key = "sk-live-실제값"
        self.assertIn("SUPPLIER_CREDENTIAL_WRITE_BLOCKED", str(ctx.exception.detail))

    def test_orm_assignment_of_api_secret_raises_structured_error(self):

        from app.core.exceptions import BadRequestException
        import app.domains.product.model  # noqa: F401 (Supplier.products 관계 해석용)
        from app.domains.supplier.model import Supplier

        supplier = Supplier(name="테스트 공급처")

        with self.assertRaises(BadRequestException):
            supplier.api_secret = "secret-실제값"

    def test_constructing_with_none_credential_fields_is_allowed(self):
        """None(미설정)은 계속 허용돼야 한다 — 기존 행 로드·생성이
        깨지면 안 된다."""

        import app.domains.product.model  # noqa: F401 (Supplier.products 관계 해석용)
        from app.domains.supplier.model import Supplier

        supplier = Supplier(name="테스트 공급처", api_key=None, api_secret=None)
        self.assertIsNone(supplier.api_key)
        self.assertIsNone(supplier.api_secret)


if __name__ == "__main__":
    unittest.main()
