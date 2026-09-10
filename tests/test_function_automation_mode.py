"""
=========================================================
Homez OS

File : tests/test_function_automation_mode.py

HOMEZ 사용자 운영 기준 2·3·4·13·14번 — "전역 단일 자동화 상태를
기능별 상태로 분리한다. 각 기능에 수동, 반자동, 자동, 중지 모드를
둔다. 상품 발굴, 상품 등록, 주문 수집, 매입 발주, 결제, 가격 변경,
재고 대응, 취소, 반품, 환불을 독립적으로 제어한다." Phase 3 구현을
검증한다.

실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.database.base import Base
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"
MIGRATION_PATH = MIGRATIONS_DIR / "20260909_01_create_function_automation_state_schema.sql"


class FunctionAutomationModeTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, FunctionAutomationState.__table__],
        )

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(name="회사 A")
        self.company_b = Company(name="회사 B")
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.service = SafetyService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # --------------------------------------------------
    # 기본값·전체 목록
    # --------------------------------------------------

    def test_unset_function_defaults_to_manual(self):

        mode = self.service.get_function_mode(self.company_a.id, FunctionCode.PAYMENT)
        self.assertEqual(mode, FunctionMode.MANUAL)

    def test_all_ten_functions_present_with_defaults(self):

        modes = self.service.get_all_function_modes(self.company_a.id)
        self.assertEqual(len(modes), 10)
        self.assertEqual(set(modes.keys()), set(FunctionCode.ALL))
        self.assertTrue(all(m == FunctionMode.MANUAL for m in modes.values()))

    def test_function_code_list_matches_operation_settings_ten_functions(self):
        """문서 원문(상품 발굴, 상품 등록, 주문 수집, 매입 발주, 결제,
        가격 변경, 재고 대응, 취소, 반품, 환불)과 정확히 같은 10개,
        같은 순서인지 확인한다."""

        expected_labels = [
            "상품 발굴", "상품 등록", "주문 수집", "매입 발주", "결제",
            "가격 변경", "재고 대응", "취소", "반품", "환불",
        ]
        actual_labels = [FunctionCode.LABELS_KO[c] for c in FunctionCode.ALL]
        self.assertEqual(actual_labels, expected_labels)

    # --------------------------------------------------
    # 설정·권한
    # --------------------------------------------------

    def test_set_function_mode_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_function_mode(
                self.company_a.id, FunctionCode.PRODUCT_LISTING,
                FunctionMode.AUTOMATIC, set_by=1, is_admin=False,
            )

    def test_set_function_mode_rejects_unknown_function_code(self):

        with self.assertRaises(BadRequestException):
            self.service.set_function_mode(
                self.company_a.id, "NOT_A_REAL_FUNCTION",
                FunctionMode.AUTOMATIC, set_by=1, is_admin=True,
            )

    def test_set_function_mode_rejects_error_as_user_selection(self):
        """ERROR는 시스템이 스스로 전이시키는 상태이지, 사용자가
        API로 직접 요청해서 만들 수 있는 상태가 아니다."""

        with self.assertRaises(BadRequestException):
            self.service.set_function_mode(
                self.company_a.id, FunctionCode.PAYMENT,
                FunctionMode.ERROR, set_by=1, is_admin=True,
            )

    def test_set_function_mode_persists_and_is_read_back(self):

        self.service.set_function_mode(
            self.company_a.id, FunctionCode.PRICE_CHANGE,
            FunctionMode.SEMI_AUTOMATIC, set_by=1, is_admin=True,
            reason="테스트",
        )

        mode = self.service.get_function_mode(self.company_a.id, FunctionCode.PRICE_CHANGE)
        self.assertEqual(mode, FunctionMode.SEMI_AUTOMATIC)

    # --------------------------------------------------
    # 독립성 — 기능별, 회사별
    # --------------------------------------------------

    def test_changing_one_function_does_not_affect_others(self):

        self.service.set_function_mode(
            self.company_a.id, FunctionCode.PRODUCT_LISTING,
            FunctionMode.AUTOMATIC, set_by=1, is_admin=True,
        )

        self.assertEqual(
            self.service.get_function_mode(self.company_a.id, FunctionCode.PRODUCT_LISTING),
            FunctionMode.AUTOMATIC,
        )
        # 나머지 9개는 여전히 기본값(MANUAL)이어야 한다.
        for code in FunctionCode.ALL:
            if code == FunctionCode.PRODUCT_LISTING:
                continue
            self.assertEqual(
                self.service.get_function_mode(self.company_a.id, code),
                FunctionMode.MANUAL,
                f"{code}가 영향을 받으면 안 된다",
            )

    def test_changing_one_companys_function_does_not_affect_another_company(self):

        self.service.set_function_mode(
            self.company_a.id, FunctionCode.PAYMENT,
            FunctionMode.AUTOMATIC, set_by=1, is_admin=True,
        )

        self.assertEqual(
            self.service.get_function_mode(self.company_a.id, FunctionCode.PAYMENT),
            FunctionMode.AUTOMATIC,
        )
        self.assertEqual(
            self.service.get_function_mode(self.company_b.id, FunctionCode.PAYMENT),
            FunctionMode.MANUAL,
            "회사 B는 회사 A의 변경과 완전히 독립적이어야 한다",
        )

    def test_repeated_changes_keep_only_latest_as_current(self):

        for mode in (
            FunctionMode.SEMI_AUTOMATIC, FunctionMode.AUTOMATIC,
            FunctionMode.PAUSED, FunctionMode.MANUAL,
        ):
            self.service.set_function_mode(
                self.company_a.id, FunctionCode.ORDER_COLLECTION,
                mode, set_by=1, is_admin=True,
            )

        self.assertEqual(
            self.service.get_function_mode(self.company_a.id, FunctionCode.ORDER_COLLECTION),
            FunctionMode.MANUAL,
        )
        # append-only 이력 4건이 전부 남아있어야 한다(감사 목적).
        history_count = (
            self.db.query(FunctionAutomationState)
            .filter(
                FunctionAutomationState.company_id == self.company_a.id,
                FunctionAutomationState.function_code == FunctionCode.ORDER_COLLECTION,
            )
            .count()
        )
        self.assertEqual(history_count, 4)

    # --------------------------------------------------
    # 시스템 강등(ERROR) 경로
    # --------------------------------------------------

    def test_demote_function_to_error_does_not_require_admin_flag_but_marks_system(self):

        state = self.service.demote_function_to_error(
            self.company_a.id, FunctionCode.INVENTORY_RESPONSE,
            reason="재고 API 스키마 불일치 감지",
        )

        self.assertEqual(state.mode, FunctionMode.ERROR)
        self.assertEqual(state.set_by, 0, "시스템이 건 것은 set_by=0으로 구분돼야 한다")
        self.assertEqual(
            self.service.get_function_mode(self.company_a.id, FunctionCode.INVENTORY_RESPONSE),
            FunctionMode.ERROR,
        )

    def test_is_function_automatic_only_true_when_automatic(self):

        self.assertFalse(
            self.service.is_function_automatic(self.company_a.id, FunctionCode.CANCELLATION),
        )

        self.service.set_function_mode(
            self.company_a.id, FunctionCode.CANCELLATION,
            FunctionMode.AUTOMATIC, set_by=1, is_admin=True,
        )
        self.assertTrue(
            self.service.is_function_automatic(self.company_a.id, FunctionCode.CANCELLATION),
        )

        self.service.demote_function_to_error(
            self.company_a.id, FunctionCode.CANCELLATION, reason="오류",
        )
        self.assertFalse(
            self.service.is_function_automatic(self.company_a.id, FunctionCode.CANCELLATION),
            "ERROR로 강등되면 자동 실행 판정도 즉시 False가 돼야 한다",
        )


class FunctionAutomationMigrationTestCase(unittest.TestCase):
    """Migration 파일 자체가 임시 DB에 실제로 깨끗하게 적용되는지
    확인한다(app/database/migration_runner.py를 통해)."""

    def test_migration_file_exists_and_defines_expected_table(self):

        self.assertTrue(MIGRATION_PATH.exists(), f"{MIGRATION_PATH} 파일이 없습니다.")

        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE function_automation_states", sql)
        self.assertIn("company_id", sql)
        self.assertIn("function_code", sql)

    def test_migration_applies_cleanly_to_fresh_temp_db(self):

        from app.database.migration_runner import MigrationRunner
        import sqlite3

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        try:
            runner = MigrationRunner(path, str(MIGRATIONS_DIR))
            conn = sqlite3.connect(path)
            try:
                runner.apply_pending(conn)
                tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'",
                    ).fetchall()
                }
                self.assertIn("function_automation_states", tables)

                columns = {
                    row[1] for row in conn.execute(
                        "PRAGMA table_info(function_automation_states)",
                    ).fetchall()
                }
                self.assertEqual(
                    columns,
                    {"id", "company_id", "function_code", "mode", "set_by", "reason", "set_at"},
                )
            finally:
                conn.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
