"""
=========================================================
Homez OS

File : tests/test_order_unknown_resolution_and_tracking_refresh.py

2026-09-11 후속(운영 전 최종 검증 라운드) — 이번 라운드에서 새로
구현한 3개 기능을 검증한다: (1) RESULT_UNKNOWN 발주 시도 수동
확인·확정, (2) 발주 시도 이력 조회(순서·불변성·회사 격리),
(3) 송장 다시 조회(단일/복수 송장, 반복 클릭 방지, 발주 성공
상태 비변경). 서버가 idempotency_key를 결정론적으로 계산하는
동작(compute_idempotency_key)도 함께 검증한다.
=========================================================
"""

import json
import os
import tempfile
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.constants import OrderSubmissionStatus
from app.domains.purchase_task.constants import PurchaseOrderApprovalStatus
from app.domains.purchase_task.constants import TrackingRefreshResult
from app.domains.purchase_task.constants import UnknownResolutionStatus
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent
from app.domains.purchase_task.model import PurchaseOrderApproval
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.model import PurchaseOrderUnknownResolutionEvent
from app.domains.purchase_task.model import PurchaseSalesApplicationAttempt
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo
from app.domains.purchase_task.order_submission_service import (
    PurchaseOrderSubmissionService,
)
from app.domains.purchase_task.service import PurchaseTaskService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용

VALID_KWARGS = dict(
    product_code="CH1234567",
    options=[{"id": 1, "qty": 1}],
    recv_name="홍길동", recv_tell="02-000-0000", recv_mobile="010-0000-0000",
    zipcode="00000", address="서울시 어딘가",
)


class _FakeSalesApplicationResult:

    def __init__(self, *, submitted=True, applied_product_code=None, detail="FAKE"):
        self.support = "SUPPORTED"
        self.submitted = submitted
        self.applied_product_code = applied_product_code
        self.detail = detail


class OrderResolutionTestCaseBase(unittest.TestCase):

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
                PurchaseOrderSubmissionAttempt.__table__,
                PurchaseOrderUnknownResolutionEvent.__table__,
                PurchaseSalesApplicationAttempt.__table__,
                PurchaseOrderApproval.__table__,
                EmergencyStop.__table__, FunctionAutomationState.__table__,
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
        self.service = PurchaseOrderSubmissionService(
            self.db, credential_store=self.credential_store,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _make_ready_connection(self, *, company=None):

        company = company or self.company_a
        connection = self.connection_service.create_connection(
            company.id, mall_code="ONCHANNEL", account_label="발주 테스트 계정",
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

    def _patch_contract_confirmed(self):

        patcher = mock.patch(
            "app.domains.purchase_task.channel_connection_service."
            "is_onchannel_order_contract_fully_confirmed",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_point_balance_gate_passes(self):

        patcher = mock.patch(
            "app.domains.purchase_task.order_submission_service."
            "PurchaseOrderSubmissionService._verify_point_balance_and_shipping_or_block",
            return_value=mock.Mock(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _install_fake_adapter(self, *, result=None, error=None):

        self._patch_point_balance_gate_passes()

        class _FakeAdapter:
            def submit_order(self_inner, request):
                if error is not None:
                    raise error
                return result

            def apply_for_sale(self_inner, external_product_id):
                return _FakeSalesApplicationResult(
                    applied_product_code=external_product_id,
                )

        patcher = mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_unknown_attempt(self, *, connection, task_id=100) -> PurchaseOrderSubmissionAttempt:
        """RESULT_UNKNOWN 상태의 발주 시도 1건을 실제로 submit_order()를
        통해 만든다(가짜로 행만 INSERT하지 않는다 — 실제 분류 로직을
        거쳐야 이후 게이트 테스트가 의미가 있다)."""

        self._patch_contract_confirmed()
        from app.domains.purchase_task.onchannel_client import OnchannelNetworkError

        self._install_fake_adapter(
            error=OnchannelNetworkError("네트워크 오류(결과 불명): Timeout"),
        )
        with self.assertRaises(OnchannelNetworkError):
            self.service.submit_order(
                connection.id, self.company_a.id,
                idempotency_key=f"pt-{task_id}-unknown-1",
                confirm_real_submission=True, purchase_task_id=task_id,
                **VALID_KWARGS,
            )
        attempt = self.service.get_attempt(
            connection.id, self.company_a.id, f"pt-{task_id}-unknown-1",
        )
        self.assertEqual(attempt.status, OrderSubmissionStatus.RESULT_UNKNOWN)
        return attempt


class IdempotencyKeyComputationTestCase(OrderResolutionTestCaseBase):

    def test_key_is_deterministic_for_same_combination(self):

        connection = self._make_ready_connection()
        key1 = self.service.compute_idempotency_key(
            self.company_a.id, connection.id, 5, "CH1", [{"id": 1, "qty": 2}],
        )
        key2 = self.service.compute_idempotency_key(
            self.company_a.id, connection.id, 5, "CH1", [{"id": 1, "qty": 2}],
        )
        self.assertEqual(key1, key2, "같은 조합은 (아직 시도가 없다면) 같은 키를 받아야 한다.")

    def test_key_advances_after_a_prior_attempt_exists(self):

        connection = self._make_ready_connection()
        key1 = self.service.compute_idempotency_key(
            self.company_a.id, connection.id, 5, "CH1", [{"id": 1, "qty": 2}],
        )
        attempt = PurchaseOrderSubmissionAttempt(
            company_id=self.company_a.id, connection_id=connection.id,
            purchase_task_id=5, idempotency_key=key1, mall_code="ONCHANNEL",
            product_code="CH1", options_json=json.dumps([{"id": 1, "qty": 2}]),
            status=OrderSubmissionStatus.REJECTED,
        )
        self.db.add(attempt)
        self.db.commit()

        key2 = self.service.compute_idempotency_key(
            self.company_a.id, connection.id, 5, "CH1", [{"id": 1, "qty": 2}],
        )
        self.assertNotEqual(key1, key2, "이전 시도가 있으면 새 키를 받아야 한다.")

    def test_different_options_get_different_keys(self):

        connection = self._make_ready_connection()
        key1 = self.service.compute_idempotency_key(
            self.company_a.id, connection.id, 5, "CH1", [{"id": 1, "qty": 1}],
        )
        key2 = self.service.compute_idempotency_key(
            self.company_a.id, connection.id, 5, "CH1", [{"id": 1, "qty": 2}],
        )
        self.assertNotEqual(key1, key2)


class UnresolvedUnknownBlocksRetryTestCase(OrderResolutionTestCaseBase):

    def test_unresolved_unknown_blocks_new_attempt_for_same_task(self):

        connection = self._make_ready_connection()
        self._make_unknown_attempt(connection=connection, task_id=200)

        self._patch_contract_confirmed()
        self._install_fake_adapter(result="SHOULD-NOT-BE-CALLED")

        with self.assertRaises(ConflictException):
            self.service.submit_order(
                connection.id, self.company_a.id,
                idempotency_key="pt-200-retry-1",
                confirm_real_submission=True, purchase_task_id=200,
                **VALID_KWARGS,
            )

    def test_resolution_unblocks_new_attempt(self):

        connection = self._make_ready_connection()
        attempt = self._make_unknown_attempt(connection=connection, task_id=201)

        self.service.resolve_unknown_attempt(
            attempt.id, self.company_a.id,
            resolution=UnknownResolutionStatus.ORDER_NOT_CONFIRMED,
            basis="온채널 관리자 화면에서 주문 목록에 없음을 확인함",
            resolved_by=1,
        )

        self._patch_contract_confirmed()
        self._install_fake_adapter(result="ORDER-AFTER-RESOLUTION")

        new_attempt = self.service.submit_order(
            connection.id, self.company_a.id,
            idempotency_key="pt-201-retry-1",
            confirm_real_submission=True, purchase_task_id=201,
            **VALID_KWARGS,
        )
        self.assertEqual(new_attempt.status, OrderSubmissionStatus.SUCCEEDED)

    def test_still_unclear_resolution_still_blocks(self):

        connection = self._make_ready_connection()
        attempt = self._make_unknown_attempt(connection=connection, task_id=202)

        self.service.resolve_unknown_attempt(
            attempt.id, self.company_a.id,
            resolution=UnknownResolutionStatus.STILL_UNCLEAR,
            resolved_by=1,
        )

        self._patch_contract_confirmed()
        self._install_fake_adapter(result="SHOULD-NOT-BE-CALLED")

        with self.assertRaises(ConflictException):
            self.service.submit_order(
                connection.id, self.company_a.id,
                idempotency_key="pt-202-retry-1",
                confirm_real_submission=True, purchase_task_id=202,
                **VALID_KWARGS,
            )

    def test_task_without_any_attempt_is_not_blocked(self):

        connection = self._make_ready_connection()
        self.assertFalse(
            self.service._has_blocking_task_attempt(999, self.company_a.id),
        )


class ResolveUnknownAttemptTestCase(OrderResolutionTestCaseBase):

    def test_order_confirmed_requires_order_code(self):

        connection = self._make_ready_connection()
        attempt = self._make_unknown_attempt(connection=connection, task_id=300)

        with self.assertRaises(BadRequestException):
            self.service.resolve_unknown_attempt(
                attempt.id, self.company_a.id,
                resolution=UnknownResolutionStatus.ORDER_CONFIRMED,
                resolved_by=1,
            )

    def test_order_not_confirmed_requires_basis(self):

        connection = self._make_ready_connection()
        attempt = self._make_unknown_attempt(connection=connection, task_id=301)

        with self.assertRaises(BadRequestException):
            self.service.resolve_unknown_attempt(
                attempt.id, self.company_a.id,
                resolution=UnknownResolutionStatus.ORDER_NOT_CONFIRMED,
                resolved_by=1,
            )

    def test_only_result_unknown_attempts_can_be_resolved(self):

        connection = self._make_ready_connection()
        self._patch_contract_confirmed()
        self._install_fake_adapter(result="ORDER-OK")
        attempt = self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="pt-302-1",
            confirm_real_submission=True, purchase_task_id=302, **VALID_KWARGS,
        )
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)

        with self.assertRaises(ConflictException):
            self.service.resolve_unknown_attempt(
                attempt.id, self.company_a.id,
                resolution=UnknownResolutionStatus.ORDER_CONFIRMED,
                order_code="X", resolved_by=1,
            )

    def test_other_company_cannot_resolve(self):

        connection = self._make_ready_connection()
        attempt = self._make_unknown_attempt(connection=connection, task_id=303)

        with self.assertRaises(NotFoundException):
            self.service.resolve_unknown_attempt(
                attempt.id, self.company_b.id,
                resolution=UnknownResolutionStatus.ORDER_NOT_CONFIRMED,
                basis="근거", resolved_by=1,
            )

    def test_resolution_writes_append_only_event_and_does_not_erase_prior(self):

        connection = self._make_ready_connection()
        attempt = self._make_unknown_attempt(connection=connection, task_id=304)

        self.service.resolve_unknown_attempt(
            attempt.id, self.company_a.id,
            resolution=UnknownResolutionStatus.STILL_UNCLEAR, resolved_by=1,
        )
        self.service.resolve_unknown_attempt(
            attempt.id, self.company_a.id,
            resolution=UnknownResolutionStatus.ORDER_NOT_CONFIRMED,
            basis="다시 확인함", resolved_by=1,
        )

        events = (
            self.db.query(PurchaseOrderUnknownResolutionEvent)
            .filter(PurchaseOrderUnknownResolutionEvent.attempt_id == attempt.id)
            .order_by(PurchaseOrderUnknownResolutionEvent.id.asc())
            .all()
        )
        self.assertEqual(len(events), 2, "두 번 확정하면 이벤트도 2건 남아야 한다(덮어쓰지 않음).")
        self.assertEqual(events[0].resolution_status, UnknownResolutionStatus.STILL_UNCLEAR)
        self.assertEqual(events[1].resolution_status, UnknownResolutionStatus.ORDER_NOT_CONFIRMED)

        refreshed = self.service.get_attempt(connection.id, self.company_a.id, attempt.idempotency_key)
        self.assertEqual(refreshed.unknown_resolution_status, UnknownResolutionStatus.ORDER_NOT_CONFIRMED)

    def test_order_confirmed_records_order_code(self):

        connection = self._make_ready_connection()
        attempt = self._make_unknown_attempt(connection=connection, task_id=305)

        resolved = self.service.resolve_unknown_attempt(
            attempt.id, self.company_a.id,
            resolution=UnknownResolutionStatus.ORDER_CONFIRMED,
            order_code="OC-CONFIRMED-1", resolved_by=7,
        )
        self.assertEqual(resolved.unknown_resolved_order_code, "OC-CONFIRMED-1")
        self.assertEqual(resolved.unknown_resolved_by, 7)
        self.assertIsNotNone(resolved.unknown_resolved_at)

    def test_order_confirmed_consumes_matching_active_approval(self):
        connection = self._make_ready_connection()
        attempt = self._make_unknown_attempt(connection=connection, task_id=306)
        approval = PurchaseOrderApproval(
            company_id=self.company_a.id, connection_id=connection.id,
            purchase_task_id=306, product_code="CH1234567",
            status=PurchaseOrderApprovalStatus.ACTIVE,
            shipping_cost_amount=0, shipping_cost_is_free_confirmed=True,
        )
        self.db.add(approval)
        self.db.commit()

        self.service.resolve_unknown_attempt(
            attempt.id, self.company_a.id,
            resolution=UnknownResolutionStatus.ORDER_CONFIRMED,
            order_code="OC-CONFIRMED-2", resolved_by=7,
        )

        self.db.refresh(approval)
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.CONSUMED)


class AttemptHistoryTestCase(OrderResolutionTestCaseBase):

    def test_history_is_ordered_oldest_first(self):

        connection = self._make_ready_connection()
        first = self.service._create_locked_attempt(
            connection_id=connection.id, company_id=self.company_a.id,
            purchase_task_id=400, idempotency_key="pt-400-1",
            mall_code="ONCHANNEL", product_code="CH1234567",
            options=[{"id": 1, "qty": 1}], triggered_by=1,
        )
        first.status = OrderSubmissionStatus.REJECTED
        self.db.commit()
        self.service._create_locked_attempt(
            connection_id=connection.id, company_id=self.company_a.id,
            purchase_task_id=400, idempotency_key="pt-400-2",
            mall_code="ONCHANNEL", product_code="CH9999999",
            options=[{"id": 2, "qty": 1}], triggered_by=1,
        )

        history = self.service.list_attempts(400, self.company_a.id)
        self.assertEqual(len(history), 2)
        self.assertLessEqual(history[0].started_at, history[1].started_at)

    def test_other_company_never_sees_attempts(self):

        connection = self._make_ready_connection()
        self._patch_contract_confirmed()
        self._install_fake_adapter(result="OC-1")
        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="pt-401-1",
            confirm_real_submission=True, purchase_task_id=401, **VALID_KWARGS,
        )

        history = self.service.list_attempts(401, self.company_b.id)
        self.assertEqual(history, [])


class TrackingRefreshTestCaseBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, FundingAccount.__table__,
                FundingLedger.__table__, PurchaseTask.__table__,
                PurchaseTaskTrackingInfo.__table__,
                PurchaseOrderSubmissionAttempt.__table__,
                PurchaseChannelConnection.__table__,
                PurchaseChannelConnectionEvent.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            ))

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()

        self.connection = PurchaseChannelConnection(
            company_id=self.company.id, mall_code="ONCHANNEL",
            account_label="테스트 계정", status="CONNECTED",
            connection_method="CREDENTIAL",
            idempotency_key="conn:1",
        )
        self.db.add(self.connection)
        self.db.commit()

        self.task = PurchaseTask(
            company_id=self.company.id, source_order_id=1,
            product_title="무선이어폰", quantity=1,
            purchase_deadline=datetime.utcnow() + timedelta(days=3),
            idempotency_key="pt-track:1",
            channel_connection_id=self.connection.id,
        )
        self.db.add(self.task)
        self.db.commit()

        self.service = PurchaseTaskService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _add_succeeded_attempt(self, order_code="OC-REAL-1"):

        attempt = PurchaseOrderSubmissionAttempt(
            company_id=self.company.id, connection_id=self.connection.id,
            purchase_task_id=self.task.id, idempotency_key="pt-track:1-a1",
            mall_code="ONCHANNEL", product_code="CH1",
            options_json="[]", status=OrderSubmissionStatus.SUCCEEDED,
            external_order_code=order_code,
        )
        self.db.add(attempt)
        self.db.commit()
        return attempt

    def _patch_lookup_tracking(self, *, result=None, error=None):

        patcher = mock.patch.object(
            PurchaseChannelConnectionService, "lookup_tracking",
            side_effect=error, return_value=result,
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class TrackingRefreshTestCase(TrackingRefreshTestCaseBase):

    def test_no_order_code_available_blocks(self):

        with self.assertRaises(ConflictException):
            self.service.refresh_tracking_live(self.task.id, self.company.id)

    def test_single_tracking_updates_and_logs_change(self):

        self._add_succeeded_attempt()
        from app.domains.purchase_task.schema import TrackingLookupResponse

        self._patch_lookup_tracking(
            result=TrackingLookupResponse(
                support="SUPPORTED", courier="CJ대한통운",
                tracking_number="111222333", delivery_status="IN_TRANSIT",
                detail="ok",
            ),
        )

        tracking = self.service.refresh_tracking_live(self.task.id, self.company.id)
        self.assertEqual(tracking.tracking_number, "111222333")
        self.assertEqual(tracking.courier, "CJ대한통운")
        self.assertEqual(tracking.last_live_refresh_result, TrackingRefreshResult.UPDATED)
        self.assertIsNotNone(tracking.last_live_refresh_at)

    def test_multiple_deliveries_does_not_overwrite_existing_value(self):

        self._add_succeeded_attempt()
        existing = PurchaseTaskTrackingInfo(
            company_id=self.company.id, purchase_task_id=self.task.id,
            courier="한진택배", tracking_number="OLD-VALUE",
        )
        self.db.add(existing)
        self.db.commit()

        from app.domains.purchase_task.schema import TrackingLookupResponse
        self._patch_lookup_tracking(
            result=TrackingLookupResponse(
                support="SUPPORTED", courier=None, tracking_number=None,
                delivery_status=None, detail="복수 송장 감지",
                multiple_deliveries_detected=True,
            ),
        )

        tracking = self.service.refresh_tracking_live(self.task.id, self.company.id)
        self.assertEqual(tracking.tracking_number, "OLD-VALUE", "복수 송장이면 기존 값을 지키고 임의 선택하지 않는다.")
        self.assertEqual(tracking.last_live_refresh_result, TrackingRefreshResult.MULTIPLE_DELIVERIES)

    def test_lookup_failure_does_not_touch_purchase_task_status(self):

        self._add_succeeded_attempt()
        original_status = self.task.status

        from app.domains.purchase_task.schema import TrackingLookupResponse
        self._patch_lookup_tracking(
            result=TrackingLookupResponse(
                support="UNKNOWN", courier=None, tracking_number=None,
                delivery_status=None, detail="응답 해석 불가",
            ),
        )

        self.service.refresh_tracking_live(self.task.id, self.company.id)
        self.db.refresh(self.task)
        self.assertEqual(self.task.status, original_status, "조회 실패가 발주/작업 성공 상태를 바꾸면 안 된다.")

    def test_repeated_click_within_interval_is_blocked(self):

        self._add_succeeded_attempt()
        from app.domains.purchase_task.schema import TrackingLookupResponse
        self._patch_lookup_tracking(
            result=TrackingLookupResponse(
                support="SUPPORTED", courier="CJ대한통운",
                tracking_number="111222333", delivery_status="IN_TRANSIT",
                detail="ok",
            ),
        )

        self.service.refresh_tracking_live(self.task.id, self.company.id)
        with self.assertRaises(ConflictException):
            self.service.refresh_tracking_live(self.task.id, self.company.id)


if __name__ == "__main__":
    unittest.main()
