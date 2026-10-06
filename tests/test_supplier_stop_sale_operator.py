"""
=========================================================
Homez OS

File : tests/test_supplier_stop_sale_operator.py

2026-10-05 — 공급처 판매중단 처리의 **운영자 재처리 경로** 격리 검증(Fake HTTP + 격리 DB,
실제 외부 요청 없음). 두 명령을 구분해 확인한다:

  reconcile(조회·대조)  — 외부 변경 요청을 절대 보내지 않는다
  re_execute(재실행)    — 승인된 업체코드·현재 공급처 상태·기능 모드·재시도 한도·조치 필요 상태를
                          다시 검사한다. UNKNOWN은 조회로 미적용이 확인된 뒤에만 한도 안에서 다시 요청한다.
=========================================================
"""

import types
import unittest
from datetime import timedelta
from unittest import mock

import requests

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import UnauthorizedException
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.constants import OrderStatus
from app.domains.purchase_task.channel_action_service import (
    ActionOutcome, CoupangChannelActions,
)
from app.domains.purchase_task.constants import ChannelActionPolicy
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.coupang_channel_control_provider import (
    CoupangChannelControlProvider,
)
from app.domains.purchase_task.supplier_stop_sale_operator_service import (
    SupplierStopSaleOperatorService, wing_user_ids_by_store,
)
from app.domains.purchase_task.supplier_stop_sale_service import (
    REASON_CHANNEL_STOPPED, StopSaleOutcome, StopSaleStep,
)
from tests.test_purchase_task_order_submission_review import NOW  # noqa: F401
from tests.test_supplier_stop_sale import (
    CANCEL_OK, STOP_OK, StopSaleTestBase, _response, _Status,
)
from tests.test_supplier_option_link_service import PRODUCT

VENDOR = "A00012345"
BOX = "642538970006401429"


class _Adapter:
    def __init__(self, status="3", error=None):
        self.status, self.error = status, error

    def lookup_product(self, code):
        if self.error:
            raise self.error
        return _Status(self.status)


class OperatorTestBase(StopSaleTestBase):

    def setUp(self):
        super().setUp()
        self._set_modes(FunctionMode.AUTOMATIC)
        self.link = self._link("SKU-A")
        self.task = self._task("SKU-A", 5001)
        fulfillment = self.db.query(OrderChannelFulfillment).filter_by(
            order_id=self.task.source_order_id).one()
        fulfillment.shipment_box_id = BOX   # 발주서 조회(대조)에는 숫자 박스번호가 필요하다
        self.db.commit()
        # 공급처 현재 상태/승인된 업체코드(자격증명의 업체코드와 같다)
        self.vendor = f"A{self.store_a.id:08d}"

    def _operator(self, *, status="3", lookup_error=None):
        def actions_factory(**kw):
            return CoupangChannelActions(
                self.db, credential_store=self.creds,
                provider_factory=lambda cred: CoupangChannelControlProvider(
                    cred, transport=self.coupang),
                now_factory=lambda: self.clock[0], **kw,
            )

        return SupplierStopSaleOperatorService(
            self.db, adapter_factory=lambda conn: _Adapter(status, lookup_error),
            actions_factory=actions_factory,
        )

    def _reconcile(self, op=None, company=None):
        return (op or self._operator()).reconcile(
            company_id=(company or self.company_a).id, connection_id=self.connection.id,
            product_code=PRODUCT, operator_user_id=7)

    def _re_execute(self, op=None, **kw):
        kw.setdefault("expected_vendor_id", self.vendor)
        return (op or self._operator()).re_execute(
            company_id=self.company_a.id, connection_id=self.connection.id,
            product_code=PRODUCT, operator_user_id=7, **kw)

    def _make_both_unknown(self):
        self.coupang.script["stop"] = [requests.Timeout("t")]
        self.coupang.script["cancel"] = [requests.Timeout("t")]
        self._handle()
        self.assertEqual(len(self.coupang.of("stop")), 1)
        self.assertEqual(len(self.coupang.of("cancel")), 1)

    def _writes(self):
        return len(self.coupang.of("stop")) + len(self.coupang.of("cancel"))

    def _sheet(self, cancelled):
        vid = int(self.link.coupang_vendor_item_id)
        self.coupang.script["sheet"] = [_response(200, {"code": 200, "data": {"orderItems": [
            {"vendorItemId": vid, "shippingCount": 1, "cancelCount": 1 if cancelled else 0}]}})]

    def _on_sale(self, value):
        self.coupang.script["inventory"] = [_response(
            200, {"code": "SUCCESS", "data": {"onSale": value}})]


class ReconcileTestCase(OperatorTestBase):

    def test_reconcile_never_sends_an_external_change_even_when_not_applied_is_confirmed(self):

        self._make_both_unknown()
        writes = self._writes()
        self._on_sale(True)        # 판매중지가 적용되지 않았음이 확인됨
        self._sheet(False)         # 주문 취소가 적용되지 않았음이 확인됨
        report = self._reconcile()

        self.assertEqual(self._writes(), writes, "대조는 쓰기 요청을 보내지 않는다")
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.RETRY_WAIT])
        self.assertEqual(
            self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.RETRY_WAIT])
        # 재시도 가능으로만 되돌렸고 실제 재요청은 별도 명령이다
        statuses = {k: v.status for k, v in self._journal().items()}
        self.assertEqual(set(statuses.values()), {ChannelActionStatus.RETRYABLE})
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)
        self.assertIn("SUPPLIER_STOP_SALE_OPERATOR_RECONCILE", self._audit_actions())

    def test_reconcile_applies_confirmed_facts_to_link_and_internal_order(self):

        self._make_both_unknown()
        self._on_sale(False)
        self._sheet(True)
        report = self._reconcile()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(
            self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        self.db.refresh(self.link)
        self.assertEqual(self.link.status_reason, REASON_CHANNEL_STOPPED)
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.CANCELLED)
        self.assertEqual(len(self.coupang.of("stop")), 1)
        self.assertEqual(len(self.coupang.of("cancel")), 1)

    def test_internal_cancel_after_a_confirmed_coupang_cancel_respects_the_cancellation_mode(self):

        self._make_both_unknown()
        self._on_sale(False)
        self._sheet(True)
        self._set_modes(FunctionMode.MANUAL, FunctionCode.CANCELLATION)  # 수동: 승인 없이는 준비만
        report = self._reconcile()
        # 운영자가 명령을 직접 실행했으므로 승인 범위이지만, 수동 모드는 승인으로 열리지 않는다
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.PREPARED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)

    def test_reconcile_without_any_request_record_is_not_applicable(self):

        report = self._reconcile()
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.NOT_APPLICABLE])
        self.assertEqual(
            self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.NOT_APPLICABLE])

    def test_unreadable_status_stays_unknown(self):

        self._make_both_unknown()
        self.coupang.script["inventory"] = [_response(500, {})]
        self.coupang.script["sheet"] = [_response(500, {})]
        report = self._reconcile()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.UNKNOWN])
        self.assertEqual(
            self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.UNKNOWN])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)

    def test_emergency_stop_keeps_reads_and_diagnosis_but_every_change_stays_blocked(self):
        """확정 정책: 긴급 중지 시 변경은 멈추고 조회·진단은 유지한다(OPERATION_SETTINGS 232·296행)."""

        self._make_both_unknown()
        writes = self._writes()
        reads_before = len(self.coupang.of("inventory")) + len(self.coupang.of("sheet"))
        self.safety.activate_emergency_stop("test", set_by=1, is_admin=True)
        self._on_sale(False)     # 판매중지가 적용됐음이 확인됨
        self._sheet(True)        # 고객 주문 취소도 쿠팡에서 확인됨
        report = self._reconcile()

        # 조회·대조는 비상정지 중에도 실행된다
        self.assertEqual(
            len(self.coupang.of("inventory")) + len(self.coupang.of("sheet")), reads_before + 2)
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(
            self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        # 변경은 계속 차단: 외부 변경 요청 0, 내부 주문 취소 반영은 비상정지로 보류
        self.assertEqual(self._writes(), writes)
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.BLOCKED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)
        self.assertNotIn("ORDER_CANCELLED", self._audit_actions())

    def test_emergency_stop_still_blocks_the_external_change_command(self):

        self.safety.activate_emergency_stop("test", set_by=1, is_admin=True)
        report = self._re_execute()   # 공급처 현재 상태 조회(읽기)는 허용, 변경은 차단
        self.assertEqual(self._writes(), 0)
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.BLOCKED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)

    def test_other_company_cannot_reconcile_or_re_execute_this_connection(self):

        self._make_both_unknown()
        calls = len(self.coupang.calls)
        with self.assertRaises(NotFoundException):
            self._reconcile(company=self.company_b)
        with self.assertRaises(NotFoundException):
            self._operator().re_execute(
                company_id=self.company_b.id, connection_id=self.connection.id,
                product_code=PRODUCT, operator_user_id=7, expected_vendor_id=self.vendor)
        self.assertEqual(len(self.coupang.calls), calls)


class ReExecuteTestCase(OperatorTestBase):

    def test_requires_an_approved_vendor_code_and_never_sends_without_one(self):

        for blank in ("", "   ", None):
            with self.assertRaises(BadRequestException):
                self._re_execute(expected_vendor_id=blank)
        self.assertEqual(self.coupang.calls, [])

    def test_refuses_when_the_current_supplier_state_is_not_an_explicit_stop_sale(self):

        for op in (
            self._operator(status="1"), self._operator(status="2"), self._operator(status="4"),
            self._operator(status="5"), self._operator(status=None),
            self._operator(lookup_error=TimeoutError("t")),
            self._operator(lookup_error=PermissionError("auth")),
        ):
            with self.assertRaises(ConflictException):
                self._re_execute(op)
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)

    def test_executes_once_for_the_approved_vendor_and_a_mismatching_code_sends_nothing(self):

        mismatch = self._re_execute(expected_vendor_id="A99999999")
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._outcomes(mismatch, StopSaleStep.SALE_STOP), [StopSaleOutcome.ACTION_REQUIRED])

        report = self._re_execute()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.CANCELLED)
        again = self._re_execute()   # 같은 명령을 반복해도 다시 보내지 않는다
        self.assertEqual(self._outcomes(again, StopSaleStep.SALE_STOP), [StopSaleOutcome.ALREADY_DONE])
        self.assertEqual(len(self.coupang.of("stop")), 1)
        self.assertEqual(len(self.coupang.of("cancel")), 1)
        self.assertIn("SUPPLIER_STOP_SALE_OPERATOR_REEXECUTE", self._audit_actions())

    def test_function_modes_and_emergency_stop_are_not_bypassed_by_the_operator(self):

        self._set_modes(FunctionMode.MANUAL)
        report = self._re_execute()
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.PREPARED])

        self._set_modes(FunctionMode.PAUSED)
        self.assertEqual(
            self._outcomes(self._re_execute(), StopSaleStep.SALE_STOP), [StopSaleOutcome.BLOCKED])
        self.assertEqual(self.coupang.calls, [])

        self._set_modes(FunctionMode.SEMI_AUTOMATIC)   # 반자동: 운영자 명령이 곧 사람 승인
        report = self._re_execute()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(len(self.coupang.of("stop")), 1)

    def test_retry_window_attempt_limit_and_action_required_are_rechecked(self):

        self.coupang.script["stop"] = [_response(429, {"code": 429}, {})]
        first = self._re_execute()
        self.assertEqual(self._outcomes(first, StopSaleStep.SALE_STOP), [StopSaleOutcome.RETRY_WAIT])
        self._re_execute()   # 간격 안 — 다시 보내지 않는다
        self.assertEqual(len(self.coupang.of("stop")), 1)

        for delay in ChannelActionPolicy.RETRY_DELAYS_SECONDS:
            self.clock[0] += timedelta(seconds=delay + 1)
            self._re_execute()
        self.assertEqual(len(self.coupang.of("stop")), ChannelActionPolicy.MAX_ATTEMPTS)
        self.clock[0] += timedelta(days=3)
        exhausted = self._re_execute()   # 한도 소진 — 자동 반복 없음
        self.assertEqual(self._outcomes(exhausted, StopSaleStep.SALE_STOP), [StopSaleOutcome.ACTION_REQUIRED])
        self.assertEqual(len(self.coupang.of("stop")), ChannelActionPolicy.MAX_ATTEMPTS)

        # 사람이 원인을 해결했다고 명시(retry_action_required)해야만 조치 필요 요청이 다시 나간다
        self.coupang.script["stop"] = [STOP_OK]
        fixed = self._re_execute(retry_action_required=True)
        self.assertEqual(self._outcomes(fixed, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(len(self.coupang.of("stop")), ChannelActionPolicy.MAX_ATTEMPTS + 1)

    def test_unknown_is_resent_only_after_a_read_confirms_it_was_not_applied(self):

        self._make_both_unknown()
        # 읽기 조회가 실패하면 결과불명 유지 — 재요청 없음
        self.coupang.script["inventory"] = [_response(500, {})]
        self.coupang.script["sheet"] = [_response(500, {})]
        report = self._re_execute()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.UNKNOWN])
        self.assertEqual(self._writes(), 2)

        # 이미 판매중지(onSale=false)로 확인되면 성공으로 확정 — 재요청 없음
        self._on_sale(False)
        self._sheet(True)
        self.coupang.script["stop"] = [STOP_OK]
        self.coupang.script["cancel"] = [CANCEL_OK]
        report = self._re_execute()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._writes(), 2)
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.CANCELLED)

    def test_unknown_not_applied_is_resent_within_the_budget(self):

        self.coupang.script["stop"] = [requests.Timeout("t"), STOP_OK]
        self._handle()                              # 1회: 결과불명
        self._on_sale(True)                         # 조회: 아직 판매 중(미적용)
        report = self._re_execute()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(len(self.coupang.of("stop")), 2)


class WingUserIdScopeTestCase(OperatorTestBase):

    def test_wizard_value_is_used_only_for_the_connection_its_account_resolves_to(self):

        entries = [
            {"marketplace_account_id": 1, "required_fields": {"vendorUserId": "wing-a"}},
            {"marketplace_account_id": 2, "required_fields": {"vendorUserId": "wing-b"}},
            {"marketplace_account_id": 3, "required_fields": {"vendorUserId": "  "}},
            {"marketplace_account_id": 4, "required_fields": {}},
            {"marketplace_account_id": 9, "required_fields": {"vendorUserId": "wing-unresolved"}},
            "garbage",
        ]
        resolve = {1: 11, 2: 22, 3: 33, 4: 44}.get
        self.assertEqual(wing_user_ids_by_store(entries, resolve), {11: "wing-a", 22: "wing-b"})

    def test_cancel_uses_the_value_only_for_the_matching_store_and_is_never_stored(self):

        self.creds.save(f"HOMEZ_TEST:store:{self.store_a.id}", {
            "access_key": "AK", "secret_key": "SK", "vendor_id": self.vendor})  # WING ID 없음

        def actions(resolver):
            return CoupangChannelActions(
                self.db, credential_store=self.creds, wing_user_id=resolver,
                provider_factory=lambda cred: CoupangChannelControlProvider(
                    cred, transport=self.coupang),
                now_factory=lambda: self.clock[0])

        kwargs = dict(
            company_id=self.company_a.id, store_connection_id=self.store_a.id,
            channel_order_id="5001", shipment_box_id=BOX, items=[(self.link.coupang_vendor_item_id, 1)])
        # 다른 판매 연결의 값만 있으면(일치 없음) 쓰지 않는다
        none = actions(lambda sid: "wing-other" if sid != self.store_a.id else None).cancel_order(**kwargs)
        self.assertEqual(none.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.coupang.of("cancel"), [])
        # 일치하는 연결의 값은 그 요청에만 쓴다
        done = actions(lambda sid: "wing-match" if sid == self.store_a.id else None).cancel_order(**kwargs)
        self.assertEqual(done.outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.coupang.of("cancel")[0]["body"]["userId"], "wing-match")
        self.assertNotIn("wing_user_id", self.creds.read(f"HOMEZ_TEST:store:{self.store_a.id}"))


class OperatorApiTestCase(unittest.TestCase):

    def _routes(self):
        from app.domains.purchase_task import router as module

        return {r.path: r for r in module.router.routes if "supplier-stop-sale" in r.path}

    def test_routes_are_admin_only_and_registered_before_dynamic_task_routes(self):
        from app.core.guard import AdminGuard
        from app.domains.purchase_task import router as module

        routes = self._routes()
        self.assertEqual(set(routes), {
            "/purchase-tasks/supplier-stop-sale/reconcile",
            "/purchase-tasks/supplier-stop-sale/re-execute"})
        for path, route in routes.items():
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(AdminGuard, calls, path)
        ordered = [r.path for r in module.router.routes]
        first_dynamic = next(i for i, p in enumerate(ordered) if p.startswith("/purchase-tasks/{task_id}"))
        for path in routes:
            self.assertLess(ordered.index(path), first_dynamic)

    def test_re_execute_requires_a_recent_auth_token_and_uses_the_users_company_only(self):
        from app.domains.purchase_task import router as module
        from app.domains.purchase_task.schema import (
            SupplierStopSaleReExecuteRequest, SupplierStopSaleReconcileRequest,
        )

        user = types.SimpleNamespace(id=7, company_id=3)
        data = SupplierStopSaleReExecuteRequest(
            connection_id=5, product_code="CH1", expected_vendor_id="A1", source_wizard_id=2)
        with self.assertRaises(UnauthorizedException):
            module.re_execute_supplier_stop_sale(data, current_user=user, db=mock.Mock(), recent_auth_token=None)

        fake_report = mock.Mock(product_code="CH1", confirmed=True, results=[])
        fake_report.summary.return_value = "s"
        fake_report.incomplete.return_value = []
        with mock.patch.object(module, "consume_recent_auth_token", return_value=True), mock.patch(
            "app.domains.purchase_task.supplier_stop_sale_operator_service."
            "SupplierStopSaleOperatorService.re_execute", return_value=fake_report,
        ) as run:
            module.re_execute_supplier_stop_sale(
                data, current_user=user, db=mock.Mock(), recent_auth_token="t")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["company_id"], 3)          # 본문이 아니라 로그인 사용자의 회사
        self.assertEqual(kwargs["operator_user_id"], 7)
        self.assertEqual(kwargs["expected_vendor_id"], "A1")
        self.assertEqual(kwargs["source_wizard_id"], 2)

        # 조회·대조는 최근 인증 없이 읽기만 한다
        with mock.patch(
            "app.domains.purchase_task.supplier_stop_sale_operator_service."
            "SupplierStopSaleOperatorService.reconcile", return_value=fake_report,
        ) as read:
            module.reconcile_supplier_stop_sale(
                SupplierStopSaleReconcileRequest(connection_id=5, product_code="CH1"),
                current_user=user, db=mock.Mock())
        self.assertEqual(read.call_args.kwargs["company_id"], 3)


if __name__ == "__main__":
    unittest.main()
