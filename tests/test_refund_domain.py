"""
=========================================================
Homez OS

File : tests/test_refund_domain.py

2026-09-10 Phase 8(HOMEZ_USER_OPERATION_SETTINGS.md 8·9번) — 환불
상태 관리·승인 게이트 검증. "returns proceed only after user notice
+ approval"이 코드로 강제되는지가 이 파일의 핵심 검증 대상이다.
실제 homez.db·실제 Executor·실제 네트워크 호출은 전혀 없다 —
`FakeRefundExecutor`만 쓴다.
=========================================================
"""

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.database.bootstrap import bootstrap_environment
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.refund.constants import RefundStatus
from app.domains.refund.constants import RefundType
from app.domains.refund.executor import FakeRefundExecutor
from app.domains.refund.executor import RefundExecutionError
from app.domains.refund.service import RefundService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class RefundDomainTestCase(unittest.TestCase):

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
            "refunds/refund_status_events Migration이 임시 DB에 "
            "깨끗하게 적용돼야 한다.",
        )

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="환불도메인테스트 회사", business_number="888-88-88888",
            ceo="테스트", phone="02-000-0000",
            email="refund@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="refundadmin",
            email="refundadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.executor = FakeRefundExecutor()
        self.service = RefundService(self.db, self.executor)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def _create_refund(self, **overrides):

        defaults = dict(
            company_id=self.company.id, order_id=1001,
            return_order_id=None,
            refund_type=RefundType.CUSTOMER_REFUND,
            amount=30_000, currency="KRW", reason="고객 단순 변심",
            requested_by=self.admin.id,
            idempotency_key=str(uuid.uuid4()),
        )
        defaults.update(overrides)
        return self.service.create_refund_request(**defaults)

    # ------------------------------
    # 생성 — 항상 AWAITING_APPROVAL
    # ------------------------------

    def test_create_refund_starts_awaiting_approval(self):

        refund = self._create_refund()

        self.assertEqual(refund.status, RefundStatus.AWAITING_APPROVAL)
        self.assertIsNone(refund.approved_at)
        self.assertIsNone(refund.executed_at)

    def test_create_refund_rejects_unknown_type(self):

        with self.assertRaises(BadRequestException):
            self._create_refund(refund_type="CRYPTO_REFUND")

    def test_create_refund_rejects_non_positive_amount(self):

        with self.assertRaises(BadRequestException):
            self._create_refund(amount=0)

    def test_create_refund_rejects_blank_reason(self):

        with self.assertRaises(BadRequestException):
            self._create_refund(reason="   ")

    def test_create_refund_enforces_idempotency_key_uniqueness(self):

        key = str(uuid.uuid4())
        self._create_refund(idempotency_key=key)

        with self.assertRaises(Exception):
            self._create_refund(idempotency_key=key)

    # ------------------------------
    # 승인 — 항상 사람이 명시적으로 호출해야 한다("returns proceed
    # only after user notice + approval"의 핵심 검증)
    # ------------------------------

    def test_no_automatic_path_reaches_approved_status(self):
        """생성만으로는 절대 APPROVED에 도달하지 않는다 — 이 테스트가
        실패한다면 "승인 후에만 진행" 원칙이 깨진 것이다."""

        refund = self._create_refund()
        self.assertNotEqual(refund.status, RefundStatus.APPROVED)

    def test_approve_requires_admin(self):

        refund = self._create_refund()

        with self.assertRaises(ForbiddenException):
            self.service.approve_refund(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=False,
            )

    def test_approve_transitions_to_approved(self):

        refund = self._create_refund()

        approved = self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        self.assertEqual(approved.status, RefundStatus.APPROVED)
        self.assertEqual(approved.approved_by, self.admin.id)
        self.assertIsNotNone(approved.approved_at)

    def test_approve_records_status_event(self):

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        events = self.service.repository.list_status_events(refund.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].new_status, RefundStatus.APPROVED)
        self.assertEqual(events[0].triggered_by, self.admin.id)

    def test_approve_twice_fails_second_time(self):
        """이미 APPROVED인 건을 다시 승인하려 하면 거부돼야 한다
        (동시 요청 경쟁 방지의 논리적 귀결)."""

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            self.service.approve_refund(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

    def test_approve_nonexistent_refund_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.approve_refund(
                refund_id=999999, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

    # ------------------------------
    # 거부
    # ------------------------------

    def test_reject_transitions_to_rejected(self):

        refund = self._create_refund()

        rejected = self.service.reject_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True, reason="근거 부족",
        )

        self.assertEqual(rejected.status, RefundStatus.REJECTED)

    def test_reject_requires_admin(self):

        refund = self._create_refund()

        with self.assertRaises(ForbiddenException):
            self.service.reject_refund(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=False, reason="x",
            )

    def test_rejected_refund_cannot_be_approved(self):

        refund = self._create_refund()
        self.service.reject_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True, reason="근거 부족",
        )

        with self.assertRaises(BadRequestException):
            self.service.approve_refund(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

    # ------------------------------
    # 실행 확정 — 오직 APPROVED에서만, Fake Executor만 호출
    # ------------------------------

    def test_mark_executed_requires_approved_status(self):

        refund = self._create_refund()  # 아직 AWAITING_APPROVAL

        with self.assertRaises(BadRequestException):
            self.service.mark_executed(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

    def test_mark_executed_transitions_to_executed(self):

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        executed = self.service.mark_executed(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        self.assertEqual(executed.status, RefundStatus.EXECUTED)
        self.assertIsNotNone(executed.executed_at)

    def test_mark_executed_requires_admin(self):

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        with self.assertRaises(ForbiddenException):
            self.service.mark_executed(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=False,
            )

    def test_mark_executed_twice_fails_second_time(self):

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )
        self.service.mark_executed(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            self.service.mark_executed(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

    def test_mark_executed_blocks_silent_retry_after_uncertain_execution(self):
        """2026-09-15 전면 감사 후속(Phase 4, IA-011) — Executor 호출은
        성공했지만(execution_attempt_started_at이 durable하게 남음)
        그 뒤 로컬 상태 전이가 확정되지 못한 채 끝난 상황을 재현한다
        (프로세스 중단을 transition_status_conditional 예외로 흉내낸다).
        다음 mark_executed() 호출은 사람의 명시적 확인 없이 Executor를
        다시 부르면 안 된다(이중 환불 위험) — ConflictException으로
        막히고, 확인 플래그를 주면 정상 진행돼야 한다."""

        import unittest.mock as mock

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        with mock.patch.object(
            self.service.repository, "transition_status_conditional",
            side_effect=RuntimeError("시뮬레이션된 프로세스 중단"),
        ):
            with self.assertRaises(RuntimeError):
                self.service.mark_executed(
                    refund_id=refund.id, company_id=self.company.id,
                    user_id=self.admin.id, is_admin=True,
                )

        stuck = self.service.get(refund.id, self.company.id)
        self.assertEqual(stuck.status, RefundStatus.APPROVED)
        self.assertIsNotNone(
            stuck.execution_attempt_started_at,
            "Executor 호출 직전에 남긴 시도 마커는 그대로 남아 있어야 한다.",
        )

        with self.assertRaises(ConflictException):
            self.service.mark_executed(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

        executed = self.service.mark_executed(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
            confirm_retry_after_uncertain_execution=True,
        )
        self.assertEqual(executed.status, RefundStatus.EXECUTED)

    def test_mark_executed_clean_rejection_does_not_require_confirmation(self):
        """실행 시도 자체가 Executor에 실제로 도달하기 전에 확정적으로
        거부된 경우(RefundExecutionError)는 "결과불명"이 아니다 — 시도
        마커를 지워, 다음 mark_executed() 호출이 사람의 별도 확인 없이
        바로 진행될 수 있어야 한다."""

        import unittest.mock as mock

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        with mock.patch.object(
            self.service.executor, "execute",
            side_effect=RefundExecutionError("시뮬레이션된 확정 거부"),
        ):
            with self.assertRaises(RefundExecutionError):
                self.service.mark_executed(
                    refund_id=refund.id, company_id=self.company.id,
                    user_id=self.admin.id, is_admin=True,
                )

        rejected_attempt = self.service.get(refund.id, self.company.id)
        self.assertIsNone(rejected_attempt.execution_attempt_started_at)

        executed = self.service.mark_executed(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )
        self.assertEqual(executed.status, RefundStatus.EXECUTED)

    def test_mark_executed_blocked_by_emergency_stop(self):
        """2026-09-15 전면 감사 후속(Phase 8, 실행 게이트 배선) —
        이미 승인된 환불이라도 비상정지가 활성화되어 있으면 실행을
        진행하지 않는다(purchase_task 발주 실행과 동일한 최종
        방어선)."""

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=self.admin.id, is_admin=True,
        )

        with self.assertRaises(ConflictException):
            self.service.mark_executed(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

        unchanged = self.service.get(refund.id, self.company.id)
        self.assertEqual(unchanged.status, RefundStatus.APPROVED)

    def test_mark_executed_blocked_when_refund_function_paused(self):
        """REFUND 기능이 PAUSED 상태면 이미 승인된 환불도 실행되지
        않는다."""

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        SafetyService(self.db).set_function_mode(
            self.company.id, FunctionCode.REFUND, FunctionMode.PAUSED,
            set_by=self.admin.id, is_admin=True,
        )

        with self.assertRaises(ConflictException):
            self.service.mark_executed(
                refund_id=refund.id, company_id=self.company.id,
                user_id=self.admin.id, is_admin=True,
            )

        unchanged = self.service.get(refund.id, self.company.id)
        self.assertEqual(unchanged.status, RefundStatus.APPROVED)

    def test_mark_executed_allowed_when_refund_function_automatic(self):
        """AUTOMATIC은 차단 대상이 아니다 — 실행 게이트가 과잉
        차단하지 않는지 함께 확인한다."""

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        SafetyService(self.db).set_function_mode(
            self.company.id, FunctionCode.REFUND, FunctionMode.AUTOMATIC,
            set_by=self.admin.id, is_admin=True,
        )

        executed = self.service.mark_executed(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )
        self.assertEqual(executed.status, RefundStatus.EXECUTED)

    def test_full_lifecycle_status_events_are_ordered(self):

        refund = self._create_refund()
        self.service.approve_refund(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )
        self.service.mark_executed(
            refund_id=refund.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        events = self.service.repository.list_status_events(refund.id)
        self.assertEqual(
            [e.new_status for e in events],
            [RefundStatus.APPROVED, RefundStatus.EXECUTED],
        )

    # ------------------------------
    # 조회·격리
    # ------------------------------

    def test_refunds_are_isolated_per_company(self):

        other_company = Company(
            name="다른회사환불", business_number="999-99-99999",
            ceo="테스트", phone="02-000-0000",
            email="other-refund@example.com", address="테스트",
        )
        self.db.add(other_company)
        self.db.commit()

        self._create_refund()

        self.assertEqual(
            len(self.service.list_for_company(other_company.id)), 0,
        )

    def test_list_for_company_filters_by_status(self):

        r1 = self._create_refund()
        r2 = self._create_refund()
        self.service.approve_refund(
            refund_id=r1.id, company_id=self.company.id,
            user_id=self.admin.id, is_admin=True,
        )

        awaiting = self.service.list_for_company(
            self.company.id, status=RefundStatus.AWAITING_APPROVAL,
        )
        self.assertEqual([r.id for r in awaiting], [r2.id])

    def test_supplier_reclaim_type_is_supported(self):

        refund = self._create_refund(
            refund_type=RefundType.SUPPLIER_RECLAIM,
        )
        self.assertEqual(refund.refund_type, RefundType.SUPPLIER_RECLAIM)


class FakeRefundExecutorTestCase(unittest.TestCase):

    def setUp(self):

        self.executor = FakeRefundExecutor()

    def test_execute_returns_fake_completed_result(self):

        result = self.executor.execute(1, 10_000, "KRW")
        self.assertEqual(result.fake_status, "COMPLETED")
        self.assertEqual(result.amount, 10_000)
        self.assertTrue(
            result.fake_execution_id.startswith("fake_refund_exec_"),
        )

    def test_execute_rejects_non_positive_amount(self):

        with self.assertRaises(RefundExecutionError):
            self.executor.execute(1, 0, "KRW")


if __name__ == "__main__":
    unittest.main()
