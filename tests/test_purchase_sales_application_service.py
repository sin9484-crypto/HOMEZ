"""
=========================================================
Homez OS

File : tests/test_purchase_sales_application_service.py

2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수" 확정) —
PurchaseSalesApplicationService 격리 테스트. 실제 네트워크·실제
Windows Credential Manager를 전혀 건드리지 않는다(InMemoryCredentialStore
+ 가짜 Adapter만 사용). 승인 게이트, 접수 성공/거절/결과불명 분류,
이미 접수 확인된 행의 재호출 생략, 실패 후 같은 행 위에서의 재시도,
다중 계정·회사 격리를 다룬다.
=========================================================
"""

import os
import tempfile
import unittest
import unittest.mock as mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.constants import SalesApplicationStatus
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent
from app.domains.purchase_task.model import PurchaseSalesApplicationAttempt
from app.domains.purchase_task.sales_application_service import (
    PurchaseSalesApplicationService,
)
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용


class _FakeSalesApplicationResult:

    def __init__(self, *, submitted=True, applied_product_code=None, detail="FAKE"):
        self.support = "SUPPORTED"
        self.submitted = submitted
        self.applied_product_code = applied_product_code
        self.detail = detail


class SalesApplicationServiceTestCaseBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, PurchaseChannelConnection.__table__,
                PurchaseChannelConnectionEvent.__table__,
                PurchaseSalesApplicationAttempt.__table__,
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="부산",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.credential_store = InMemoryCredentialStore()
        self.connection_service = PurchaseChannelConnectionService(
            self.db, credential_store=self.credential_store,
        )
        self.service = PurchaseSalesApplicationService(
            self.db, credential_store=self.credential_store,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _make_ready_connection(self, *, company=None) -> PurchaseChannelConnection:

        from datetime import datetime

        company = company or self.company_a
        connection = self.connection_service.create_connection(
            company.id, mall_code="ONCHANNEL", account_label="판매신청 테스트 계정",
        )
        self.connection_service.save_credential(
            connection.id, company.id, auth_key="test-jwt",
        )
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        row.status = "CONNECTED"
        row.verified_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return row

    def _install_fake_adapter(self, *, result=None, error=None, call_log=None):

        class _FakeAdapter:
            def apply_for_sale(self_inner, external_product_id):
                if call_log is not None:
                    call_log.append(external_product_id)
                if error is not None:
                    raise error
                return result

        patcher = mock.patch(
            "app.domains.purchase_task.sales_application_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class ConfirmationGateTestCase(SalesApplicationServiceTestCaseBase):

    def test_missing_confirm_flag_rejected_before_any_network_work(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_fake_adapter(result="CH1", call_log=call_log)

        with self.assertRaises(BadRequestException):
            self.service.ensure_sales_application_submitted(
                connection.id, self.company_a.id, "CH1234567",
            )

        self.assertEqual(call_log, [])
        self.assertEqual(
            self.db.query(PurchaseSalesApplicationAttempt).count(), 0,
            "승인 게이트를 통과하지 못했으면 시도 행 자체가 생기면 안 된다.",
        )


class SuccessAndFailureClassificationTestCase(SalesApplicationServiceTestCaseBase):

    def test_successful_submission_records_submitted_with_applied_product_code(self):

        connection = self._make_ready_connection()
        self._install_fake_adapter(
            result=_FakeSalesApplicationResult(applied_product_code="CH1234567"),
        )

        attempt = self.service.ensure_sales_application_submitted(
            connection.id, self.company_a.id, "CH1234567",
            confirm_real_submission=True,
        )

        self.assertEqual(attempt.status, SalesApplicationStatus.SUBMITTED)
        self.assertEqual(attempt.applied_product_code, "CH1234567")
        self.assertIsNone(attempt.failure_detail)
        self.assertIsNotNone(attempt.finished_at)

    def test_explicit_rejection_records_rejected_not_unknown(self):

        from app.domains.purchase_task.onchannel_client import OnchannelValidationError

        connection = self._make_ready_connection()
        self._install_fake_adapter(
            error=OnchannelValidationError("이미 신청된 상품입니다(409)."),
        )

        with self.assertRaises(OnchannelValidationError):
            self.service.ensure_sales_application_submitted(
                connection.id, self.company_a.id, "CH1234567",
                confirm_real_submission=True,
            )

        attempt = self.service.get_attempt(connection.id, self.company_a.id, "CH1234567")
        self.assertEqual(attempt.status, SalesApplicationStatus.REJECTED)

    def test_network_timeout_records_result_unknown_not_rejected(self):

        from app.domains.purchase_task.onchannel_client import OnchannelNetworkError

        connection = self._make_ready_connection()
        self._install_fake_adapter(error=OnchannelNetworkError("타임아웃"))

        with self.assertRaises(OnchannelNetworkError):
            self.service.ensure_sales_application_submitted(
                connection.id, self.company_a.id, "CH1234567",
                confirm_real_submission=True,
            )

        attempt = self.service.get_attempt(connection.id, self.company_a.id, "CH1234567")
        self.assertEqual(attempt.status, SalesApplicationStatus.RESULT_UNKNOWN)

    def test_not_submitted_result_without_exception_records_result_unknown(self):
        """Adapter가 예외 없이 submitted=False를 돌려주는 경우(예:
        미지원 Adapter의 UNKNOWN support) — 이것도 "접수 확인 안 됨"
        이므로 RESULT_UNKNOWN이다, REJECTED가 아니다(온채널이 명시적
        으로 거부한 것이 아니라 그냥 확인이 안 된 것이기 때문)."""

        connection = self._make_ready_connection()
        self._install_fake_adapter(
            result=_FakeSalesApplicationResult(submitted=False, detail="미지원"),
        )

        attempt = self.service.ensure_sales_application_submitted(
            connection.id, self.company_a.id, "CH1234567",
            confirm_real_submission=True,
        )
        self.assertEqual(attempt.status, SalesApplicationStatus.RESULT_UNKNOWN)


class AlreadySubmittedSkipsReapplicationTestCase(SalesApplicationServiceTestCaseBase):

    def test_already_submitted_does_not_call_adapter_again(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_fake_adapter(
            result=_FakeSalesApplicationResult(applied_product_code="CH1234567"),
            call_log=call_log,
        )

        first = self.service.ensure_sales_application_submitted(
            connection.id, self.company_a.id, "CH1234567",
            confirm_real_submission=True,
        )
        self.assertEqual(first.status, SalesApplicationStatus.SUBMITTED)
        self.assertEqual(len(call_log), 1)

        second = self.service.ensure_sales_application_submitted(
            connection.id, self.company_a.id, "CH1234567",
            confirm_real_submission=True,
        )
        self.assertEqual(second.id, first.id)
        self.assertEqual(
            len(call_log), 1,
            "이미 SUBMITTED로 접수 확인된 상품은 재호출하지 않는다.",
        )

    def test_is_sales_application_confirmed_reflects_gate_state(self):

        connection = self._make_ready_connection()
        self.assertFalse(
            self.service.is_sales_application_confirmed(
                connection.id, self.company_a.id, "CH1234567",
            ),
        )

        self._install_fake_adapter(
            result=_FakeSalesApplicationResult(applied_product_code="CH1234567"),
        )
        self.service.ensure_sales_application_submitted(
            connection.id, self.company_a.id, "CH1234567",
            confirm_real_submission=True,
        )

        self.assertTrue(
            self.service.is_sales_application_confirmed(
                connection.id, self.company_a.id, "CH1234567",
            ),
        )


class RetryAfterFailureReusesSameRowTestCase(SalesApplicationServiceTestCaseBase):
    """발주와 달리 판매신청은 금전·중복 위험이 없다 — REJECTED/
    RESULT_UNKNOWN이었던 행을 새 idempotency_key 없이 같은
    (company_id, connection_id, product_code) 행 위에서 재시도할
    수 있어야 한다(model.py의 PurchaseSalesApplicationAttempt
    docstring 근거)."""

    def test_retry_after_rejected_reuses_same_row_and_can_succeed(self):

        from app.domains.purchase_task.onchannel_client import OnchannelValidationError

        connection = self._make_ready_connection()
        self._install_fake_adapter(error=OnchannelValidationError("일시적 거부"))

        with self.assertRaises(OnchannelValidationError):
            self.service.ensure_sales_application_submitted(
                connection.id, self.company_a.id, "CH1234567",
                confirm_real_submission=True,
            )
        first_attempt = self.service.get_attempt(
            connection.id, self.company_a.id, "CH1234567",
        )
        self.assertEqual(first_attempt.status, SalesApplicationStatus.REJECTED)

        self._install_fake_adapter(
            result=_FakeSalesApplicationResult(applied_product_code="CH1234567"),
        )
        second_attempt = self.service.ensure_sales_application_submitted(
            connection.id, self.company_a.id, "CH1234567",
            confirm_real_submission=True,
        )

        self.assertEqual(
            second_attempt.id, first_attempt.id,
            "재시도는 새 행이 아니라 같은 행을 갱신해야 한다.",
        )
        self.assertEqual(second_attempt.status, SalesApplicationStatus.SUBMITTED)
        self.assertEqual(
            self.db.query(PurchaseSalesApplicationAttempt)
            .filter(
                PurchaseSalesApplicationAttempt.company_id == self.company_a.id,
                PurchaseSalesApplicationAttempt.connection_id == connection.id,
                PurchaseSalesApplicationAttempt.product_code == "CH1234567",
            ).count(),
            1,
            "같은 상품·연결 조합은 항상 행 하나만 존재해야 한다.",
        )


class MultiTenantIsolationTestCase(SalesApplicationServiceTestCaseBase):

    def test_other_company_cannot_use_first_companys_connection(self):

        connection = self._make_ready_connection(company=self.company_a)
        self._install_fake_adapter(
            result=_FakeSalesApplicationResult(applied_product_code="CH1"),
        )

        with self.assertRaises(Exception):
            self.service.ensure_sales_application_submitted(
                connection.id, self.company_b.id, "CH1",
                confirm_real_submission=True,
            )

    def test_same_product_code_different_company_both_tracked_independently(self):

        connection_a = self._make_ready_connection(company=self.company_a)
        connection_b = self._make_ready_connection(company=self.company_b)
        self._install_fake_adapter(
            result=_FakeSalesApplicationResult(applied_product_code="CH1"),
        )

        attempt_a = self.service.ensure_sales_application_submitted(
            connection_a.id, self.company_a.id, "CH1",
            confirm_real_submission=True,
        )
        attempt_b = self.service.ensure_sales_application_submitted(
            connection_b.id, self.company_b.id, "CH1",
            confirm_real_submission=True,
        )

        self.assertNotEqual(attempt_a.id, attempt_b.id)
        self.assertEqual(attempt_a.status, SalesApplicationStatus.SUBMITTED)
        self.assertEqual(attempt_b.status, SalesApplicationStatus.SUBMITTED)


if __name__ == "__main__":
    unittest.main()
