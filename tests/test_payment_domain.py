"""
=========================================================
Homez OS

File : tests/test_payment_domain.py

2026-09-10 Phase 7(HOMEZ_USER_OPERATION_SETTINGS.md 4·5·11번) —
결제수단 등록·관리, 자동결제 한도, 자동결제 허용 판정 검증.
실제 homez.db·실제 Provider·실제 네트워크 호출은 전혀 없다 —
`FakePaymentProvider`와 `InMemoryCredentialStore`만 쓴다.
=========================================================
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.bootstrap import bootstrap_environment
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.payment.constants import PaymentMethodType
from app.domains.payment.gateway import FakePaymentProvider
from app.domains.payment.gateway import PaymentGatewayError
from app.domains.payment.service import PaymentService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class PaymentDomainTestCase(unittest.TestCase):

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
        self.assertFalse(
            result.migration_approval_required,
            "payment_methods/payment_auto_limits Migration이 임시 DB에 "
            "깨끗하게 적용돼야 한다.",
        )

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="결제도메인테스트 회사", business_number="666-66-66666",
            ceo="테스트", phone="02-000-0000",
            email="payment@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="paymentadmin",
            email="paymentadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.credential_store = InMemoryCredentialStore()
        self.gateway = FakePaymentProvider()
        self.service = PaymentService(
            self.db, self.credential_store, self.gateway,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    # ------------------------------
    # 결제수단 등록
    # ------------------------------

    def test_register_method_creates_active_method(self):

        method = self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={"number": "4111111111111111", "cvc": "123"},
            display_name="국민카드 **** 1111",
        )

        self.assertTrue(method.active)
        self.assertEqual(method.method_type, PaymentMethodType.CARD)

    def test_register_method_never_stores_raw_card_number_in_db(self):

        raw_card_number = "4111111111111111"

        self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={"number": raw_card_number, "cvc": "999"},
            display_name="국민카드 **** 1111",
        )

        rows = self.db.execute(
            text("SELECT * FROM payment_methods"),
        ).fetchall()
        self.assertEqual(len(rows), 1)

        for row in rows:
            row_text = json.dumps([str(v) for v in row], ensure_ascii=False)
            self.assertNotIn(raw_card_number, row_text)
            self.assertNotIn("999", row_text)

    def test_register_method_stores_only_token_ref_in_credential_store(self):

        method = self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={"number": "4111111111111111", "cvc": "123"},
            display_name="국민카드 **** 1111",
        )

        stored = self.credential_store.read(method.credential_target_name)
        self.assertIn("token_ref", stored)
        self.assertNotIn("4111111111111111", json.dumps(stored))
        self.assertTrue(stored["token_ref"].startswith("fake_tok_"))

    def test_register_method_rejects_non_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.register_method(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=False, method_type=PaymentMethodType.CARD,
                raw_details={}, display_name="x",
            )

    def test_register_method_rejects_unknown_type(self):

        with self.assertRaises(BadRequestException):
            self.service.register_method(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, method_type="BITCOIN",
                raw_details={}, display_name="x",
            )

    def test_register_method_rejects_blank_display_name(self):

        with self.assertRaises(BadRequestException):
            self.service.register_method(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, method_type=PaymentMethodType.CARD,
                raw_details={}, display_name="   ",
            )

    def test_register_with_make_default_clears_previous_default(self):

        first = self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={}, display_name="카드1", make_default=True,
        )
        second = self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.BANK_TRANSFER,
            raw_details={}, display_name="계좌1", make_default=True,
        )

        methods = {
            m.id: m for m in self.service.list_methods(self.company.id)
        }
        self.assertFalse(methods[first.id].is_default)
        self.assertTrue(methods[second.id].is_default)

    # ------------------------------
    # 조회·비활성화·기본지정
    # ------------------------------

    def test_list_methods_excludes_inactive_by_default(self):

        method = self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={}, display_name="카드1",
        )
        self.service.deactivate_method(
            company_id=self.company.id, method_id=method.id, is_admin=True,
        )

        self.assertEqual(
            len(self.service.list_methods(self.company.id)), 0,
        )
        self.assertEqual(
            len(
                self.service.list_methods(
                    self.company.id, include_inactive=True,
                ),
            ),
            1,
        )

    def test_deactivate_method_removes_credential_and_clears_default(self):

        method = self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={}, display_name="카드1", make_default=True,
        )
        target_name = method.credential_target_name

        result = self.service.deactivate_method(
            company_id=self.company.id, method_id=method.id, is_admin=True,
        )

        self.assertFalse(result.active)
        self.assertFalse(result.is_default)
        self.assertFalse(self.credential_store.exists(target_name))

    def test_deactivate_nonexistent_method_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.deactivate_method(
                company_id=self.company.id, method_id=999999,
                is_admin=True,
            )

    def test_deactivate_method_rejects_non_admin(self):

        method = self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={}, display_name="카드1",
        )

        with self.assertRaises(ForbiddenException):
            self.service.deactivate_method(
                company_id=self.company.id, method_id=method.id,
                is_admin=False,
            )

    def test_set_default_method_rejects_inactive_method(self):

        method = self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={}, display_name="카드1",
        )
        self.service.deactivate_method(
            company_id=self.company.id, method_id=method.id, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            self.service.set_default_method(
                company_id=self.company.id, method_id=method.id,
                is_admin=True,
            )

    def test_methods_are_isolated_per_company(self):

        other_company = Company(
            name="다른회사", business_number="777-77-77777",
            ceo="테스트", phone="02-000-0000",
            email="other@example.com", address="테스트",
        )
        self.db.add(other_company)
        self.db.commit()

        self.service.register_method(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, method_type=PaymentMethodType.CARD,
            raw_details={}, display_name="카드1",
        )

        self.assertEqual(
            len(self.service.list_methods(other_company.id)), 0,
        )

    # ------------------------------
    # 자동결제 한도
    # ------------------------------

    def test_set_auto_limit_creates_new_row(self):

        limit = self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=50_000,
            daily_limit_amount=100_000,
        )

        self.assertEqual(limit.per_transaction_limit_amount, 50_000)
        self.assertEqual(limit.daily_limit_amount, 100_000)

    def test_set_auto_limit_rejects_non_positive_amounts(self):

        with self.assertRaises(BadRequestException):
            self.service.set_auto_limit(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, per_transaction_limit_amount=0,
                daily_limit_amount=100_000,
            )

    def test_set_auto_limit_rejects_per_transaction_over_daily(self):

        with self.assertRaises(BadRequestException):
            self.service.set_auto_limit(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, per_transaction_limit_amount=200_000,
                daily_limit_amount=100_000,
            )

    def test_set_auto_limit_rejects_non_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_auto_limit(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=False, per_transaction_limit_amount=1,
                daily_limit_amount=2,
            )

    def test_get_auto_limit_returns_latest_after_multiple_sets(self):

        self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=10_000,
            daily_limit_amount=20_000,
        )
        self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=30_000,
            daily_limit_amount=60_000,
        )

        latest = self.service.get_auto_limit(self.company.id)
        self.assertEqual(latest.per_transaction_limit_amount, 30_000)

    def test_auto_limit_history_is_append_only(self):

        self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=10_000,
            daily_limit_amount=20_000,
        )
        self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=30_000,
            daily_limit_amount=60_000,
        )

        count = self.db.execute(
            text(
                "SELECT COUNT(*) FROM payment_auto_limits "
                "WHERE company_id = :cid",
            ),
            {"cid": self.company.id},
        ).scalar()
        self.assertEqual(count, 2)

    # ------------------------------
    # 자동결제 허용 판정 — 실행하지 않는다, 판정만
    # ------------------------------

    def test_auto_payment_denied_when_no_limit_set(self):

        allowed, reason = self.service.verify_auto_payment_allowed(
            self.company.id, 1000,
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_auto_payment_denied_when_function_mode_is_default_manual(self):

        self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=50_000,
            daily_limit_amount=100_000,
        )

        allowed, reason = self.service.verify_auto_payment_allowed(
            self.company.id, 1000,
        )
        self.assertFalse(allowed)
        self.assertIn("자동 모드", reason)

    def test_auto_payment_allowed_when_automatic_and_within_limit(self):

        self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=50_000,
            daily_limit_amount=100_000,
        )

        safety = SafetyService(self.db)
        safety.set_function_mode(
            self.company.id, FunctionCode.PAYMENT, FunctionMode.AUTOMATIC,
            set_by=self.admin.id, is_admin=True,
        )

        allowed, reason = self.service.verify_auto_payment_allowed(
            self.company.id, 30_000,
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_auto_payment_denied_when_over_per_transaction_limit(self):

        self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=50_000,
            daily_limit_amount=100_000,
        )

        safety = SafetyService(self.db)
        safety.set_function_mode(
            self.company.id, FunctionCode.PAYMENT, FunctionMode.AUTOMATIC,
            set_by=self.admin.id, is_admin=True,
        )

        allowed, reason = self.service.verify_auto_payment_allowed(
            self.company.id, 60_000,
        )
        self.assertFalse(allowed)
        self.assertIn("건당 한도", reason)

    def test_other_function_modes_do_not_allow_auto_payment(self):

        self.service.set_auto_limit(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, per_transaction_limit_amount=50_000,
            daily_limit_amount=100_000,
        )

        safety = SafetyService(self.db)
        for mode in (
            FunctionMode.MANUAL, FunctionMode.SEMI_AUTOMATIC,
            FunctionMode.PAUSED,
        ):
            safety.set_function_mode(
                self.company.id, FunctionCode.PAYMENT, mode,
                set_by=self.admin.id, is_admin=True,
            )
            allowed, _ = self.service.verify_auto_payment_allowed(
                self.company.id, 1000,
            )
            self.assertFalse(allowed, f"mode={mode}일 때는 항상 거부돼야 한다")


class FakePaymentProviderTestCase(unittest.TestCase):
    """Fake Provider 자체 — 실제 네트워크 호출이 전혀 없는 시뮬레이션."""

    def setUp(self):

        self.provider = FakePaymentProvider()

    def test_tokenize_returns_opaque_token_not_raw_input(self):

        token = self.provider.tokenize(
            PaymentMethodType.CARD,
            {"number": "4111111111111111", "cvc": "123"},
        )
        self.assertNotIn("4111111111111111", token)
        self.assertTrue(token.startswith("fake_tok_"))

    def test_tokenize_is_unique_per_call(self):

        t1 = self.provider.tokenize(PaymentMethodType.CARD, {})
        t2 = self.provider.tokenize(PaymentMethodType.CARD, {})
        self.assertNotEqual(t1, t2)

    def test_charge_returns_fake_succeeded_result(self):

        result = self.provider.charge("fake_tok_card_abc", 10_000, "KRW")
        self.assertEqual(result.fake_status, "SUCCEEDED")
        self.assertEqual(result.amount, 10_000)
        self.assertTrue(result.fake_charge_id.startswith("fake_charge_"))

    def test_charge_rejects_non_positive_amount(self):

        with self.assertRaises(PaymentGatewayError):
            self.provider.charge("fake_tok_card_abc", 0, "KRW")


if __name__ == "__main__":
    unittest.main()
