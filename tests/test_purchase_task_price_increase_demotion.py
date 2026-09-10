"""
=========================================================
Homez OS

File : tests/test_purchase_task_price_increase_demotion.py

HOMEZ 사용자 운영 기준 3·13번 — "가격 또는 재고가 변경되면 새로운
판매와 발주를 중지하고 사용자에게 알린다", "가격 인상... 이 발생하면
관련 자동 기능만 중지한다." Phase 4 구현(가격 인상 감지 → PRICE_CHANGE
기능 자동 강등 + 통지)을 검증한다.

**2026-09-10 Phase 10에서 해결된 한계(기록 보존)**: 이 테스트는
`PurchaseTaskService._demote_price_change_on_increase()`를 직접
호출해서, "이 메서드가 호출되면" 강등·통지·멱등성이 올바르게
동작하는지만 검증한다 — 이 격리된 검증 자체는 여전히 유효하고
유용해 이 파일은 그대로 남겨둔다.

Phase 4 당시에는 이 메서드를 실제로 트리거하는
`PurchaseTaskPolicyService.evaluate()`의 `PRICE_INCREASE_RATE_EXCEEDED`
판정이, 그 판정에 필요한 `PurchaseTaskPolicyCheckInput.
expected_amount_at_creation` 필드를 채우는 호출부가 저장소 어디에도
없어 절대 발생하지 않았다. **Phase 10에서 이 격차를 실제로 메웠다**
— `PurchaseTaskCandidate.expected_amount_at_creation`(신규 컬럼,
`migrations/20260910_04_add_purchase_task_candidate_price_baseline.sql`)
을 추가해 `evaluate_and_prepare()`가 후보를 처음 평가할 때 가격
기준선을 1회만 기록하고, 이후 재평가 때마다 이 기준선과 비교해
인상률을 판정한다. 이 end-to-end 경로 자체의 검증은
`tests/test_purchase_task_service.py::PriceIncreaseBaselineTestCase`
가 담당한다(`evaluate_and_prepare()`를 실제로 두 번 호출해 기준선
고정·인상 감지·강등까지 전부 확인) — 이 파일의 격리 테스트와
역할을 분담한다.

실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database.bootstrap import bootstrap_environment
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.purchase_task.service import PurchaseTaskService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class PriceIncreaseDemotionTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)
        self.assertFalse(result.migration_approval_required)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="가격인상테스트 회사", business_number="333-33-33333",
            ceo="테스트", phone="02-000-0000",
            email="price@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="priceadmin",
            email="priceadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.service = PurchaseTaskService(self.db)
        self.fake_task = SimpleNamespace(id=999, product_title="테스트 상품")

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def test_demotes_price_change_function_to_error(self):

        self.service._demote_price_change_on_increase(self.company.id, self.fake_task)

        safety = SafetyService(self.db)
        self.assertEqual(
            safety.get_function_mode(self.company.id, FunctionCode.PRICE_CHANGE),
            FunctionMode.ERROR,
        )

    def test_other_functions_unaffected(self):

        self.service._demote_price_change_on_increase(self.company.id, self.fake_task)

        safety = SafetyService(self.db)
        for code in FunctionCode.ALL:
            if code == FunctionCode.PRICE_CHANGE:
                continue
            self.assertEqual(
                safety.get_function_mode(self.company.id, code),
                FunctionMode.MANUAL,
            )

    def test_notifies_company_super_admin(self):

        self.service._demote_price_change_on_increase(self.company.id, self.fake_task)

        rows = self.db.execute(
            text(
                "SELECT user_id, event_code FROM notification_email_logs "
                "WHERE event_code = 'FUNCTION_AUTOMATION_DEMOTED_TO_ERROR'",
            ),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], self.admin.id)

    def test_idempotent_does_not_repeat_demotion_or_notification(self):
        """같은 후보를 여러 번 재평가해도, 이미 ERROR 상태면 이력도
        알림도 다시 쌓지 않는다."""

        self.service._demote_price_change_on_increase(self.company.id, self.fake_task)
        self.service._demote_price_change_on_increase(self.company.id, self.fake_task)
        self.service._demote_price_change_on_increase(self.company.id, self.fake_task)

        history_count = self.db.execute(
            text(
                "SELECT COUNT(*) FROM function_automation_states "
                "WHERE company_id = :cid AND function_code = :fc",
            ),
            {"cid": self.company.id, "fc": FunctionCode.PRICE_CHANGE},
        ).scalar()
        self.assertEqual(history_count, 1)

        notif_count = self.db.execute(
            text(
                "SELECT COUNT(*) FROM notification_email_logs "
                "WHERE event_code = 'FUNCTION_AUTOMATION_DEMOTED_TO_ERROR'",
            ),
        ).scalar()
        self.assertEqual(notif_count, 1)

    def test_audit_log_records_the_demotion(self):

        self.service._demote_price_change_on_increase(self.company.id, self.fake_task)

        # function_automation_states 자체가 append-only 감사 이력이다
        # (audit_logs 테이블과 별도) — mode=ERROR, set_by=0인 행이
        # 남아있는지 직접 확인한다.
        state_row = self.db.execute(
            text(
                "SELECT mode, set_by, reason FROM function_automation_states "
                "WHERE company_id = :cid AND function_code = :fc",
            ),
            {"cid": self.company.id, "fc": FunctionCode.PRICE_CHANGE},
        ).fetchone()
        self.assertEqual(state_row[0], FunctionMode.ERROR)
        self.assertEqual(state_row[1], 0, "시스템이 건 것은 set_by=0이어야 한다")
        self.assertIn("가격 인상", state_row[2])


if __name__ == "__main__":
    unittest.main()
