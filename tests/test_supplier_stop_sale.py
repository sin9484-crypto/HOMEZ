"""
=========================================================
Homez OS

File : tests/test_supplier_stop_sale.py

2026-10-05 사용자 확정 — 공급처가 상품 판매중단을 **명시**해 발주 불가가 확정되면 대응 판매
옵션 판매중지 → 쿠팡 고객 주문 취소 → HOMEZ 내부 주문 취소로 이어지는 흐름의 격리 검증.

실제 외부 호출은 없다: 쿠팡 쪽은 실제 `CoupangChannelActions`(작업 장부 + Provider)를
**Fake HTTP** 위에서 돌리고, 주문 취소는 실제 `OrderService`를 격리 DB에서 실행한다.
검증 범위: 정상 처리, 회사·판매계정·상품 격리, 판매중단과 오류·품절·결과불명의 구분,
중복 사건, 모드·비상정지, 외부 성공 후 내부 반영(순서), 외부 성공 후 내부 실패 재실행,
출고·발주·결제·혼합·박스 상태에 따른 대상 제외, 오류 분류별 재시도 규칙.
=========================================================
"""

import json
import unittest
from datetime import timedelta
from unittest import mock

import requests

from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.automation_safety.service import SafetyService
from app.domains.funding.model import FundingHold
from app.domains.inventory.model import InventoryReservation
from app.domains.notification_center.event_catalog import EVENT_CATALOG
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.constants import OrderStatus
from app.domains.order.model import Order
from app.domains.order.model import OrderStatusEvent
from app.domains.order.service import OrderService
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import CapabilitySupport
from app.domains.purchase_task.constants import ChannelActionPolicy
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import OrderSubmissionStatus
from app.domains.purchase_task.constants import SupplierOptionLinkStatus
from app.domains.purchase_task.coupang_channel_control_provider import (
    CoupangChannelControlProvider,
)
from app.domains.purchase_task.model import ChannelActionRequest
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.model import PurchaseRecord
from app.domains.purchase_task.model import SupplierOptionLink
from app.domains.purchase_task.supplier_option_link_service import SupplierComponent
from app.domains.purchase_task.supplier_stop_sale_service import (
    REASON_BLOCKED, REASON_CHANNEL_ACTION_REQUIRED, REASON_CHANNEL_RETRY_WAIT,
    REASON_CHANNEL_STOPPED, REASON_CHANNEL_UNKNOWN, StopSaleOutcome, StopSaleStep,
    SupplierStopSaleService, UnconfiguredChannelActions,
)
from tests.test_purchase_task_order_submission_review import NOW
from tests.test_supplier_option_link_service import LinkTestCaseBase, PRODUCT


def _response(status, body=None, headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode("utf-8") if body is not None else b"x"
    response.headers.update(headers or {})
    return response


STOP_OK = _response(200, {"code": "SUCCESS", "message": "Sale has been suspended."})
CANCEL_OK = _response(200, {"code": "200", "message": "Success",
                            "data": {"receiptMap": {}, "orderId": 1, "failedItemIds": []}})


class FakeCoupang:
    """쿠팡 Fake HTTP — 종류별 시나리오 큐(마지막 항목 반복)와 호출 기록."""

    def __init__(self):
        self.calls = []
        self.script = {
            "stop": [STOP_OK], "cancel": [CANCEL_OK],
            "inventory": [_response(200, {"code": "SUCCESS", "data": {"onSale": True}})],
            "sheet": [_response(200, {"code": 200, "data": {"orderItems": []}})],
        }

    def request(self, method, url, *, headers, json_body, timeout):
        kind = ("stop" if url.endswith("/sales/stop") else
                "cancel" if url.endswith("/cancel") else
                "inventory" if url.endswith("/inventories") else "sheet")
        self.calls.append({"kind": kind, "method": method, "url": url, "body": json_body})
        queue = self.script[kind]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def of(self, kind):
        return [c for c in self.calls if c["kind"] == kind]


class _Status:
    """공급처 상세 응답 대역(필요한 속성만)."""

    def __init__(self, status, support=CapabilitySupport.SUPPORTED):
        self.status, self.support = status, support


class StopSaleTestBase(LinkTestCaseBase):

    def setUp(self):
        super().setUp()
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                FunctionAutomationState.__table__, PurchaseOrderSubmissionAttempt.__table__,
                OrderStatusEvent.__table__, FundingHold.__table__,
                InventoryReservation.__table__, ChannelActionRequest.__table__,
            ],
        )
        self.safety = SafetyService(self.db)
        self.coupang = FakeCoupang()
        self.creds = InMemoryCredentialStore()
        self.clock = [NOW]
        self._vendor_seq = 9000
        for store in (self.store_a, self.store_b):
            self._enable_credentials(store)

    # ---- 픽스처 ----

    def _enable_credentials(self, store, *, wing=True):
        ref = f"HOMEZ_TEST:store:{store.id}"
        store.credential_reference = ref
        # 실제 DB처럼 판매 연결의 seller_identifier(사용자 입력 이름)와 업체코드는 다른 값이다.
        payload = {"access_key": "AK", "secret_key": "SK",
                   "vendor_id": f"A{store.id:08d}"}
        if wing:
            payload["wing_user_id"] = "wing-test"
        self.creds.save(ref, payload)
        self.db.commit()

    def _actions(self):
        return CoupangChannelActions(
            self.db, credential_store=self.creds,
            provider_factory=lambda cred: CoupangChannelControlProvider(
                cred, transport=self.coupang),
            now_factory=lambda: self.clock[0],
        )

    def _set_modes(self, mode, *codes):
        for code in codes or (FunctionCode.INVENTORY_RESPONSE, FunctionCode.CANCELLATION):
            self.safety.set_function_mode(
                self.company_a.id, code, mode, set_by=1, is_admin=True,
            )

    def _link(self, sku, *, store=None, connection=None, product=PRODUCT, option="OPT1",
              company=None, with_ids=True):
        company = company or self.company_a
        link = self.links.save_link(
            company.id, store_connection_id=(store or self.store_a).id, channel_sku=sku,
            purchase_connection_id=(connection or self.connection).id,
            components=[SupplierComponent(product, option, 1)], confirmed_by=1,
        )
        if with_ids:
            self._vendor_seq += 1
            link.coupang_seller_product_id = f"{self._vendor_seq + 500}"
            link.coupang_vendor_item_id = str(self._vendor_seq)
            self.db.commit()
        return link

    def _service(self, actions=None, **kw):
        return SupplierStopSaleService(
            self.db, channel_actions=actions or self._actions(), **kw)

    def _handle(self, service=None, *, status="3", approved_by=None, company=None,
                connection=None, product=PRODUCT, **kw):
        service = service or self._service()
        return service.handle_explicit_stop_sale(
            company_id=(company or self.company_a).id,
            connection_id=(connection or self.connection).id, product_code=product,
            supplier_status=status, triggered_by=7, approved_by=approved_by, **kw,
        )

    def _task(self, sku, order_no, **kw):
        """쿠팡 주문번호는 숫자여야 한다(취소 API 경로)."""
        return self._order_task(sku=sku, channel_order_id=str(order_no), **kw)

    def _order_status(self, order_id):
        self.db.expire_all()
        return self.db.query(Order).filter(Order.id == order_id).one().status

    def _audit_actions(self):
        return [r[0] for r in self.db.execute(
            __import__("sqlalchemy").text("SELECT action FROM audit_logs ORDER BY id"),
        ).fetchall()]

    def _journal(self):
        self.db.expire_all()
        return {r.target_key: r for r in self.db.query(ChannelActionRequest).all()}

    def _outcomes(self, report, step):
        return [r.outcome for r in report.by_step(step)]


class ExplicitStopSaleFlowTestCase(StopSaleTestBase):

    def test_explicit_stop_sale_stops_the_option_cancels_on_coupang_then_cancels_internally(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        link = self._link("SKU-A")
        task = self._task("SKU-A", 5001, quantity=2)

        order_service = OrderService(self.db)
        seen = {}
        real_cancel = order_service.cancel_order

        def spy(*args, **kwargs):
            # 내부 취소가 일어나는 시점에 쿠팡 취소 요청은 이미 성공해 있어야 한다.
            seen["cancel_calls_before_internal"] = len(self.coupang.of("cancel"))
            return real_cancel(*args, **kwargs)

        order_service.cancel_order = spy
        report = self._handle(self._service(order_service=order_service))

        self.assertTrue(report.confirmed)
        self.db.refresh(link)
        self.assertEqual(link.status, SupplierOptionLinkStatus.NEEDS_REVIEW)
        self.assertEqual(link.status_reason, REASON_CHANNEL_STOPPED)
        stops = self.coupang.of("stop")
        self.assertEqual(len(stops), 1)
        self.assertTrue(stops[0]["url"].endswith(f"/vendor-items/{link.coupang_vendor_item_id}/sales/stop"))
        cancels = self.coupang.of("cancel")
        self.assertEqual(len(cancels), 1)
        self.assertTrue(cancels[0]["url"].endswith("/orders/5001/cancel"))
        self.assertEqual(cancels[0]["body"]["vendorItemIds"], [int(link.coupang_vendor_item_id)])
        self.assertEqual(cancels[0]["body"]["receiptCounts"], [2])
        self.assertEqual(cancels[0]["body"]["middleCancelCode"], "CCTTER")
        self.assertEqual(seen["cancel_calls_before_internal"], 1)
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.CANCELLED)

        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        # 환불은 쿠팡 문서에 취소와의 관계가 없어 요청도 완료 표시도 하지 않는다
        self.assertEqual(self._outcomes(report, StopSaleStep.REFUND), [StopSaleOutcome.NOT_REQUESTED])
        self.assertTrue(report.channel_done(StopSaleStep.SALE_STOP))
        self.assertTrue(report.channel_done(StopSaleStep.CHANNEL_ORDER_CANCEL))
        for expected in (
            "SUPPLIER_STOP_SALE_DETECTED", "SUPPLIER_STOP_SALE_LINK_BLOCKED",
            "SUPPLIER_STOP_SALE_CHANNEL_STOP_SUCCEEDED",
            "SUPPLIER_STOP_SALE_CHANNEL_CANCEL_SUCCEEDED",
            "SUPPLIER_STOP_SALE_ORDER_CANCELLED", "ORDER_CANCELLED",
        ):
            self.assertIn(expected, self._audit_actions())

    def test_repeating_the_same_event_sends_nothing_twice_even_after_a_restart(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        self._link("SKU-A")
        task = self._task("SKU-A", 5001)

        self._handle()
        events = self.db.query(OrderStatusEvent).filter_by(order_id=task.source_order_id).count()
        second = self._handle(self._service(self._actions()))   # 새 서비스·새 실행기 = 재시작
        third = self._handle(self._service(self._actions()))

        self.assertEqual(len(self.coupang.of("stop")), 1, "판매중지 요청은 한 번만")
        self.assertEqual(len(self.coupang.of("cancel")), 1, "쿠팡 주문 취소 요청은 한 번만")
        self.assertEqual(
            self.db.query(OrderStatusEvent).filter_by(order_id=task.source_order_id).count(),
            events, "내부 주문 취소 이벤트는 한 번만",
        )
        for report in (second, third):
            self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.ALREADY_DONE])
            self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.ALREADY_DONE])
        self.assertEqual(self._audit_actions().count("SUPPLIER_STOP_SALE_ORDER_CANCELLED"), 1)
        self.assertEqual(self._audit_actions().count("ORDER_CANCELLED"), 1)

    def test_only_status_3_is_an_explicit_stop_sale(self):

        explicit = SupplierStopSaleService.is_explicit_stop_sale
        self.assertTrue(explicit(_Status("3")))
        self.assertTrue(explicit(_Status(3)))
        self.assertTrue(explicit(_Status(" 3 ")))
        # 단종(2)·일시품절(4)·품절(5)·정상(1)·값 없음·알 수 없는 값은 판매중단이 아니다.
        for not_stop in (None, "", "1", "2", "4", "5", "0", "abc", "STOP"):
            self.assertFalse(explicit(_Status(not_stop)), repr(not_stop))
        # 조회가 성공하지 못한 응답(오류·미지원·형식 불명)은 상태 값이 있어도 판매중단이 아니다.
        for support in (CapabilitySupport.UNKNOWN, CapabilitySupport.NOT_SUPPORTED, "ERROR", None):
            self.assertFalse(explicit(_Status("3", support=support)), repr(support))
        self.assertFalse(explicit(object()))

    def test_non_explicit_statuses_change_nothing(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        link = self._link("SKU-A")
        task = self._task("SKU-A", 5001)
        for status in (None, "1", "2", "4", "5", "unknown"):
            report = self._handle(status=status)
            self.assertFalse(report.confirmed, repr(status))
            self.assertEqual(report.results, [])
        self.db.refresh(link)
        self.assertEqual(link.status, SupplierOptionLinkStatus.ACTIVE)
        self.assertIsNone(link.status_reason)
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING)
        self.assertNotIn("SUPPLIER_STOP_SALE_DETECTED", self._audit_actions())

    def test_detect_and_handle_ignores_lookup_errors_and_unsupported_results(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        self._link("SKU-A")
        service = self._service()

        class Adapter:
            def __init__(self, result=None, error=None):
                self.result, self.error = result, error

            def lookup_product(self, code):
                if self.error:
                    raise self.error
                return self.result

        for adapter in (
            Adapter(error=TimeoutError("t")), Adapter(error=PermissionError("auth")),
            Adapter(_Status("3", support=CapabilitySupport.UNKNOWN)),
            Adapter(_Status("4")), Adapter(_Status("5")), Adapter(_Status(None)),
        ):
            self.assertIsNone(service.detect_and_handle(
                adapter, company_id=self.company_a.id, connection_id=self.connection.id,
                product_code=PRODUCT))
        self.assertEqual(self.coupang.calls, [])
        report = service.detect_and_handle(
            Adapter(_Status("3")), company_id=self.company_a.id,
            connection_id=self.connection.id, product_code=PRODUCT)
        self.assertTrue(report.confirmed)
        self.assertEqual(len(self.coupang.of("stop")), 1)

    def test_notification_message_never_hides_a_missing_refund_or_unreflected_steps(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        self._link("SKU-A")
        self._task("SKU-A", 5001)
        report = self._handle()
        message = SupplierStopSaleService.build_notification_message(PRODUCT, report)
        self.assertIn("쿠팡 주문 취소 반영됨", message)
        # 취소가 반영돼도 환불을 요청·확인하지 않았다는 사실이 같은 문장에 남는다
        self.assertIn("환불은 요청하지도 확인하지도 않았습니다", message)
        self.assertIn("REFUND:NOT_REQUESTED", message)

        # 준비만 한 경우는 반영됐다고 쓰지 않는다
        manual = SupplierStopSaleService.build_notification_message(
            PRODUCT, self._handle(self._service(UnconfiguredChannelActions()), approved_by=None))
        self.assertIn("쿠팡 판매중지 미반영", manual)
        self.assertNotIn("쿠팡 판매중지 반영됨", manual)

    def test_notification_event_is_registered(self):

        self.assertTrue(EVENT_CATALOG["SUPPLIER_STOP_SALE_CONFIRMED"].wired)


class ScopeIsolationTestCase(StopSaleTestBase):

    def test_only_this_company_store_connection_and_product_are_touched(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        mine = self._link("SKU-MINE")
        mine_task = self._task("SKU-MINE", 5001)

        # 같은 회사의 다른 판매 계정(스토어)에 같은 공급처 상품이 연결된 정상 사례
        other_store = self._create_store(self.company_a, seller="SELLER-2")
        self._enable_credentials(other_store)
        other_store_link = self._link("SKU-MINE", store=other_store)
        other_store_task = self._task("SKU-MINE", 5002, store=other_store)
        # 같은 판매 계정이지만 다른 공급처 상품코드에 연결된 옵션
        other_product_link = self._link("SKU-OTHERPROD", product="CH9999999")
        other_product_task = self._task("SKU-OTHERPROD", 5003)
        # 다른 회사의 같은 SKU
        company_b_conn = self._create_ready_connection(company=self.company_b)
        company_b_link = self._link(
            "SKU-MINE", store=self.store_b, connection=company_b_conn, company=self.company_b,
        )
        company_b_task = self._task(
            "SKU-MINE", 5004, store=self.store_b, company=self.company_b,
            connection=company_b_conn,
        )

        report = self._handle()

        self.assertTrue(report.confirmed)
        self.db.expire_all()
        for targeted in (mine, other_store_link):
            self.assertEqual(
                self.db.get(SupplierOptionLink, targeted.id).status_reason, REASON_CHANNEL_STOPPED)
        self.assertEqual(self._order_status(mine_task.source_order_id), OrderStatus.CANCELLED)
        self.assertEqual(self._order_status(other_store_task.source_order_id), OrderStatus.CANCELLED)
        for untouched in (other_product_link, company_b_link):
            row = self.db.get(SupplierOptionLink, untouched.id)
            self.assertEqual(row.status, SupplierOptionLinkStatus.ACTIVE, untouched.id)
            self.assertIsNone(row.status_reason)
        self.assertEqual(self._order_status(other_product_task.source_order_id), OrderStatus.PENDING)
        self.assertEqual(self._order_status(company_b_task.source_order_id), OrderStatus.PENDING)

        stopped = {c["url"].split("/vendor-items/")[1].split("/")[0] for c in self.coupang.of("stop")}
        self.assertEqual(stopped, {mine.coupang_vendor_item_id, other_store_link.coupang_vendor_item_id})
        cancelled_orders = {c["url"].split("/orders/")[1].split("/")[0] for c in self.coupang.of("cancel")}
        self.assertEqual(cancelled_orders, {"5001", "5002"})
        # 요청은 각 판매 계정의 자격증명(업체코드)으로, 그 계정의 옵션번호로 보냈다
        # (같은 SKU라도 계정이 다르면 vendorItemId가 다르다 — SKU만으로 섞으면 안 된다)
        vendors = {c["body"]["vendorId"] for c in self.coupang.of("cancel")}
        self.assertEqual(vendors, {f"A{self.store_a.id:08d}", f"A{other_store.id:08d}"})
        by_order = {
            c["url"].split("/orders/")[1].split("/")[0]: c["body"] for c in self.coupang.of("cancel")
        }
        self.assertEqual(by_order["5001"]["vendorItemIds"], [int(mine.coupang_vendor_item_id)])
        self.assertEqual(by_order["5001"]["vendorId"], f"A{self.store_a.id:08d}")
        self.assertEqual(
            by_order["5002"]["vendorItemIds"], [int(other_store_link.coupang_vendor_item_id)])
        self.assertEqual(by_order["5002"]["vendorId"], f"A{other_store.id:08d}")
        self.assertNotEqual(
            mine.coupang_vendor_item_id, other_store_link.coupang_vendor_item_id)

    def test_connection_of_another_company_is_ignored(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        link = self._link("SKU-A")
        self._task("SKU-A", 5001)

        report = self._handle(company=self.company_b)  # 회사 B가 회사 A의 연결 id로 호출

        self.assertFalse(report.confirmed)
        self.assertEqual(report.results, [])
        self.db.refresh(link)
        self.assertEqual(link.status, SupplierOptionLinkStatus.ACTIVE)
        self.assertEqual(self.coupang.calls, [])

    def test_no_link_or_no_link_store_is_left_for_review_without_touching_anything(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        task = self._task("SKU-NOLINK", 5001)
        report = self._handle()
        self.assertTrue([r for r in report.results if r.outcome == StopSaleOutcome.NEEDS_REVIEW])
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING)

        self.db.commit()  # 열린 읽기 트랜잭션이 DROP을 막지 않게 한다
        SupplierOptionLink.__table__.drop(self.engine)  # 옵션 연결 저장소가 없는 DB
        report = self._handle()
        self.assertTrue([r for r in report.results if r.outcome == StopSaleOutcome.NEEDS_REVIEW])
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING)

    def test_link_without_a_vendor_item_id_cannot_be_targeted_and_keeps_orders(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        link = self._link("SKU-A", with_ids=False)
        task = self._task("SKU-A", 5001)
        report = self._handle()
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.NEEDS_REVIEW])
        # 옵션번호가 없으면 취소 요청도 만들 수 없다 — 내부 상태도 바꾸지 않는다
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.NEEDS_REVIEW])
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING)
        self.db.refresh(link)
        self.assertEqual(link.status_reason, REASON_BLOCKED)  # 구매 쪽 차단은 유지


class ModeAndEmergencyStopTestCase(StopSaleTestBase):

    def _setup_order(self):
        link = self._link("SKU-A")
        task = self._task("SKU-A", 5001)
        return link, task

    def test_manual_mode_only_prepares_and_prepared_is_never_success(self):

        link, task = self._setup_order()  # 기본 모드: MANUAL
        report = self._handle()
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING)
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.PREPARED])
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.PREPARED])
        self.assertFalse(report.channel_done(StopSaleStep.SALE_STOP))
        self.assertTrue(report.incomplete())
        self.db.refresh(link)
        self.assertEqual(link.status, SupplierOptionLinkStatus.NEEDS_REVIEW)  # 내부 안전 조치
        self.assertEqual(link.status_reason, REASON_BLOCKED)

    def test_unconfigured_channel_actions_prepare_but_never_report_success(self):

        self._set_modes(FunctionMode.AUTOMATIC)
        link, task = self._setup_order()
        report = self._handle(self._service(UnconfiguredChannelActions()))
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.PREPARED])
        self.assertEqual(self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.PREPARED])
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING)
        self.assertNotIn("SUPPLIER_STOP_SALE_CHANNEL_STOP_SUCCEEDED", self._audit_actions())
        self.db.refresh(link)
        self.assertNotEqual(link.status_reason, REASON_CHANNEL_STOPPED)

    def test_paused_error_and_emergency_stop_block_execution(self):

        link, task = self._setup_order()
        for mode in (FunctionMode.PAUSED, FunctionMode.ERROR):
            self.db.query(FunctionAutomationState).delete()
            self.db.commit()
            if mode == FunctionMode.ERROR:
                self.safety.set_function_mode(
                    self.company_a.id, FunctionCode.INVENTORY_RESPONSE, FunctionMode.AUTOMATIC,
                    set_by=1, is_admin=True,
                )
                self.safety.demote_function_to_error(
                    self.company_a.id, FunctionCode.INVENTORY_RESPONSE, reason="test",
                )
                self._set_modes(FunctionMode.PAUSED, FunctionCode.CANCELLATION)
            else:
                self._set_modes(FunctionMode.PAUSED)
            report = self._handle()
            self.assertEqual(self.coupang.calls, [], mode)
            self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING, mode)
            self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.BLOCKED], mode)

        self.db.query(FunctionAutomationState).delete()
        self.db.commit()
        self._set_modes(FunctionMode.AUTOMATIC)
        self.safety.activate_emergency_stop("test", set_by=1, is_admin=True)
        report = self._handle()
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING)
        self.assertEqual(
            set(self._outcomes(report, StopSaleStep.SALE_STOP))
            | set(self._outcomes(report, StopSaleStep.ORDER_CANCEL)),
            {StopSaleOutcome.BLOCKED},
        )

    def test_semi_automatic_needs_a_person_to_approve(self):

        link, task = self._setup_order()
        self._set_modes(FunctionMode.SEMI_AUTOMATIC)

        self._handle()  # 승인 없음 → 준비만
        self.assertEqual(self.coupang.calls, [])
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING)

        self._handle(approved_by=1)  # 사람 승인 후 같은 사건을 다시 처리
        self.assertEqual(len(self.coupang.of("stop")), 1)
        self.assertEqual(len(self.coupang.of("cancel")), 1)
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.CANCELLED)


class OrderSeparationTestCase(StopSaleTestBase):

    def setUp(self):
        super().setUp()
        self._set_modes(FunctionMode.AUTOMATIC)
        self.link = self._link("SKU-A")

    def _attempt(self, task, status):
        self.db.add(PurchaseOrderSubmissionAttempt(
            company_id=self.company_a.id, connection_id=self.connection.id,
            purchase_task_id=task.id, idempotency_key=f"k-{task.id}-{status}",
            mall_code="ONCHANNEL", product_code=PRODUCT, options_json="[]", status=status,
            started_at=NOW,
        ))
        self.db.commit()

    def test_already_ordered_in_flight_unknown_or_shipped_orders_are_not_cancelled(self):

        cases = {}
        for n, (label, order_status, attempt) in enumerate((
            ("succeeded", None, OrderSubmissionStatus.SUCCEEDED),
            ("in_flight", None, OrderSubmissionStatus.IN_FLIGHT),
            ("unknown", None, OrderSubmissionStatus.RESULT_UNKNOWN),
            ("pending", None, OrderSubmissionStatus.PENDING),
            ("shipped", OrderStatus.SHIPPED, None),
        )):
            task = self._task("SKU-A", 6000 + n)
            if attempt:
                self._attempt(task, attempt)
            if order_status:
                order = self.db.get(Order, task.source_order_id)
                order.status = order_status
                self.db.commit()
            cases[label] = task

        report = self._handle()

        for label, task in cases.items():
            self.assertNotEqual(self._order_status(task.source_order_id), OrderStatus.CANCELLED, label)
            target = f"order:{task.source_order_id}"
            outcome = [r.outcome for r in report.results
                       if r.target == target and r.step == StopSaleStep.ORDER_CANCEL]
            self.assertEqual(outcome, [StopSaleOutcome.NEEDS_STATUS_CHECK], label)
        self.assertEqual(len(self.coupang.of("stop")), 1)   # 판매중지는 주문과 무관하게 한 번
        self.assertEqual(self.coupang.of("cancel"), [], "공급처 발주가 있는 주문은 쿠팡에 취소 요청도 하지 않는다")

    def test_a_rejected_attempt_does_not_make_the_order_ordered(self):
        """공급처가 명시적으로 거절한(REJECTED) 시도는 발주가 일어나지 않았다는 뜻이다."""

        task = self._task("SKU-A", 5001)
        self._attempt(task, OrderSubmissionStatus.REJECTED)
        self._handle()
        self.assertEqual(self._order_status(task.source_order_id), OrderStatus.CANCELLED)

    def test_order_with_supplier_payment_is_flagged_not_cancelled_and_not_refunded(self):

        task = self._task("SKU-A", 5001)
        self.db.add(PurchaseRecord(
            company_id=self.company_a.id, purchase_task_id=task.id,
            shopping_mall_code="ONCHANNEL", external_order_number="ONCH-1",
            actual_amount=5050.0, purchased_at=NOW, recorded_by=1, idempotency_key="rec-1",
        ))
        self.db.commit()
        report = self._handle()
        self.assertNotEqual(self._order_status(task.source_order_id), OrderStatus.CANCELLED)
        self.assertEqual(self.coupang.of("cancel"), [])
        refund = [r for r in report.by_step(StopSaleStep.REFUND) if r.outcome == StopSaleOutcome.NEEDS_REVIEW]
        self.assertEqual(len(refund), 1)

    def test_order_with_other_items_is_not_cancelled_as_a_whole(self):

        task = self._task("SKU-A", 5001)
        order = self.db.get(Order, task.source_order_id)
        self._create_order_item(order, product_name="다른 품목").channel_sku = "SKU-OTHER"
        self.db.commit()
        report = self._handle()
        self.assertNotEqual(self._order_status(order.id), OrderStatus.CANCELLED)
        self.assertEqual(self.coupang.of("cancel"), [])
        self.assertIn(StopSaleOutcome.NEEDS_REVIEW, self._outcomes(report, StopSaleStep.ORDER_CANCEL))

    def test_coupang_box_state_and_shape_decide_whether_an_automatic_cancel_is_allowed(self):

        # 상품준비중(INSTRUCT)·배송 시작 이후는 사람이 확인한다(출고 중지라 이미 포장됐을 수 있다)
        for n, raw in enumerate(("INSTRUCT", "DEPARTURE", "DELIVERING", "FINAL_DELIVERY")):
            task = self._task("SKU-A", 7000 + n)
            fulfillment = self.db.query(OrderChannelFulfillment).filter_by(
                order_id=task.source_order_id).one()
            fulfillment.raw_status = raw
            self.db.commit()
            report = self._handle()
            self.assertEqual(self._order_status(task.source_order_id), OrderStatus.PENDING, raw)
            self.assertIn(StopSaleOutcome.NEEDS_STATUS_CHECK, self._outcomes(report, StopSaleStep.ORDER_CANCEL), raw)
        self.assertEqual(self.coupang.of("cancel"), [])

        # 박스가 없거나 둘 이상이면 취소 대상 박스를 확정할 수 없다
        no_box = self._task("SKU-A", 7100)
        self.db.query(OrderChannelFulfillment).filter_by(order_id=no_box.source_order_id).delete()
        two_boxes = self._task("SKU-A", 7101)
        self.db.add(OrderChannelFulfillment(
            company_id=self.company_a.id, store_connection_id=self.store_a.id,
            order_id=two_boxes.source_order_id, channel_order_id="7101", shipment_box_id="BOX-2",
            raw_status="ACCEPT", ordered_at=NOW))
        self.db.commit()
        self._handle()
        self.assertEqual(self._order_status(no_box.source_order_id), OrderStatus.PENDING)
        self.assertEqual(self._order_status(two_boxes.source_order_id), OrderStatus.PENDING)
        self.assertEqual(self.coupang.of("cancel"), [])


class FailureClassificationTestCase(StopSaleTestBase):

    def setUp(self):
        super().setUp()
        self._set_modes(FunctionMode.AUTOMATIC)
        self.link = self._link("SKU-A")
        self.task = self._task("SKU-A", 5001)

    def test_rate_limited_stop_is_retried_within_limits_while_cancel_proceeds_independently(self):

        self.coupang.script["stop"] = [_response(429, {"code": 429}, {})]
        report = self._handle()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.RETRY_WAIT])
        self.db.refresh(self.link)
        self.assertEqual(self.link.status_reason, REASON_CHANNEL_RETRY_WAIT)
        # 판매중지가 막혀도 고객 주문 취소(별도 동작)는 진행·기록된다
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.CANCELLED)

        self._handle()  # 간격 안의 재처리 — 다시 보내지 않는다
        self.assertEqual(len(self.coupang.of("stop")), 1)

        self.clock[0] += timedelta(seconds=ChannelActionPolicy.RETRY_DELAYS_SECONDS[0] + 1)
        self.coupang.script["stop"] = [STOP_OK]
        report = self._handle()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(len(self.coupang.of("stop")), 2)
        self.assertEqual(len(self.coupang.of("cancel")), 1)
        self.db.refresh(self.link)
        self.assertEqual(self.link.status_reason, REASON_CHANNEL_STOPPED)

    def test_permission_and_request_errors_are_not_retried_until_a_person_retries(self):

        self.coupang.script["stop"] = [_response(403, {"code": 403, "message": "forbidden"}), STOP_OK]
        report = self._handle()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.ACTION_REQUIRED])
        self.db.refresh(self.link)
        self.assertEqual(self.link.status_reason, REASON_CHANNEL_ACTION_REQUIRED)
        for _ in range(3):
            self.clock[0] += timedelta(days=2)
            again = self._handle()
            self.assertEqual(self._outcomes(again, StopSaleStep.SALE_STOP), [StopSaleOutcome.ACTION_REQUIRED])
        self.assertEqual(len(self.coupang.of("stop")), 1)

        fixed = self._handle(retry_action_required=True)  # 권한을 해결한 뒤 사람이 다시 실행
        self.assertEqual(self._outcomes(fixed, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(len(self.coupang.of("stop")), 2)

    def test_unknown_stop_is_not_resent_and_is_confirmed_by_reading_the_sale_status(self):

        self.coupang.script["stop"] = [requests.Timeout("t")]
        report = self._handle()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.UNKNOWN])
        self.assertFalse(report.channel_done(StopSaleStep.SALE_STOP))
        self.db.refresh(self.link)
        self.assertEqual(self.link.status_reason, REASON_CHANNEL_UNKNOWN)
        self.assertEqual(self.coupang.of("inventory"), [], "방금 난 결과불명은 같은 처리 안에서 바로 판정하지 않는다")

        # 다음 처리: 판매 상태 조회로 대조 — 이미 판매중지(onSale=false)면 성공으로 확정, 재요청 없음
        self.clock[0] += timedelta(seconds=ChannelActionPolicy.RETRY_DELAYS_SECONDS[0])
        self.coupang.script["inventory"] = [_response(200, {"code": "SUCCESS", "data": {"onSale": False}})]
        again = self._handle()
        self.assertEqual(self._outcomes(again, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(len(self.coupang.of("stop")), 1)
        self.db.refresh(self.link)
        self.assertEqual(self.link.status_reason, REASON_CHANNEL_STOPPED)

    def test_unreadable_status_keeps_the_unknown_and_still_never_resends(self):

        self.coupang.script["stop"] = [_response(502, {})]
        self._handle()
        self.coupang.script["inventory"] = [_response(500, {})]
        again = self._handle()
        self.assertEqual(self._outcomes(again, StopSaleStep.SALE_STOP), [StopSaleOutcome.UNKNOWN])
        self.assertEqual(len(self.coupang.of("stop")), 1)

    def test_missing_wing_user_id_blocks_the_cancel_without_touching_the_internal_order(self):

        self._enable_credentials(self.store_a, wing=False)
        report = self._handle()
        self.assertEqual(self.coupang.of("cancel"), [])
        self.assertEqual(self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.ACTION_REQUIRED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [])
        # 판매중지는 별개 동작이라 성공한다 — 부분 성공이 사용자에게 보인다
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.SUCCEEDED])
        self.assertTrue(report.incomplete())

        # 원인을 고치면(사람의 재승인 플래그 없이) 바로 처리된다 — 외부 요청이 없었기 때문이다
        self._enable_credentials(self.store_a)
        self._handle()
        self.assertEqual(len(self.coupang.of("cancel")), 1)
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.CANCELLED)

    def test_failed_coupang_cancel_leaves_the_order_untouched_and_is_not_repeated(self):

        self.coupang.script["cancel"] = [_response(400, {
            "code": "400", "message": "Order status is not Payment Completed"})]
        report = self._handle()
        self.assertEqual(self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.ACTION_REQUIRED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)
        for _ in range(2):
            self._handle()
        self.assertEqual(len(self.coupang.of("cancel")), 1)
        self.assertNotIn("ORDER_CANCELLED", self._audit_actions())

    def test_cancel_timeout_is_unknown_never_resent_and_confirmed_by_reading_the_order_sheet(self):

        # 조회로 대조하려면 박스번호가 숫자여야 한다(쿠팡 발주서 조회 경로)
        fulfillment = self.db.query(OrderChannelFulfillment).filter_by(
            order_id=self.task.source_order_id).one()
        fulfillment.shipment_box_id = "642538970006401429"
        self.db.commit()

        self.coupang.script["cancel"] = [requests.Timeout("t")]
        report = self._handle()
        self.assertEqual(self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.UNKNOWN])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)
        self.assertEqual(self.coupang.of("sheet"), [], "방금 난 결과불명은 같은 처리 안에서 바로 판정하지 않는다")
        # 다음 처리에서 조회로 대조하지만 읽지 못하면(응답에 품목 없음) 결과불명 그대로이고 재요청하지 않는다
        self._handle()
        self.assertEqual(len(self.coupang.of("cancel")), 1)
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)

        # 다음 처리: 발주서 조회 결과가 "요청 품목 모두 취소됨"이면 성공으로 확정 → 내부 반영, 재요청 없음
        vendor_id = int(self.link.coupang_vendor_item_id)
        self.coupang.script["sheet"] = [_response(200, {"code": 200, "data": {"orderItems": [
            {"vendorItemId": vendor_id, "shippingCount": 1, "cancelCount": 1}]}})]
        self.clock[0] += timedelta(seconds=ChannelActionPolicy.RETRY_DELAYS_SECONDS[0])
        again = self._handle()
        self.assertEqual(self._outcomes(again, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.CANCELLED)
        self.assertEqual(len(self.coupang.of("cancel")), 1)

    def test_cancel_timeout_with_a_still_active_order_is_resent_only_after_the_read_confirms_it(self):

        fulfillment = self.db.query(OrderChannelFulfillment).filter_by(
            order_id=self.task.source_order_id).one()
        fulfillment.shipment_box_id = "642538970006401429"
        self.db.commit()
        self.coupang.script["cancel"] = [requests.Timeout("t"), CANCEL_OK]
        self._handle()
        vendor_id = int(self.link.coupang_vendor_item_id)
        self.coupang.script["sheet"] = [_response(200, {"code": 200, "data": {"orderItems": [
            {"vendorItemId": vendor_id, "shippingCount": 1, "cancelCount": 0, "canceled": False}]}})]
        self.clock[0] += timedelta(seconds=ChannelActionPolicy.RETRY_DELAYS_SECONDS[0])
        again = self._handle()
        self.assertEqual(len(self.coupang.of("sheet")), 1)
        self.assertEqual(len(self.coupang.of("cancel")), 2)  # 미적용이 확인된 뒤에만 다시 요청
        self.assertEqual(self._outcomes(again, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.CANCELLED)

    def test_coupang_cancel_succeeds_but_internal_cancel_fails_then_rerun_only_retries_internally(self):

        orders = mock.Mock()
        orders.cancel_order.side_effect = BadRequestException("내부 취소 불가")
        report = self._handle(self._service(order_service=orders))
        self.assertEqual(self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.FAILED])
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.PENDING)
        self.assertIn("SUPPLIER_STOP_SALE_ORDER_FAILED", self._audit_actions())
        row = next(iter(v for k, v in self._journal().items() if k.startswith("order:")))
        self.assertEqual(row.status, ChannelActionStatus.SUCCEEDED)  # 외부 성공은 장부에 남아 있다

        rerun = self._handle()  # 실제 OrderService로 재처리
        self.assertEqual(self._outcomes(rerun, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.ALREADY_DONE])
        self.assertEqual(self._outcomes(rerun, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(len(self.coupang.of("cancel")), 1, "쿠팡에는 다시 요청하지 않는다")
        self.assertEqual(self._order_status(self.task.source_order_id), OrderStatus.CANCELLED)

    def test_unexpected_internal_exception_after_coupang_success_is_recorded_as_unknown(self):

        orders = mock.Mock()
        orders.cancel_order.side_effect = RuntimeError("boom")
        report = self._handle(self._service(order_service=orders))
        outcomes = self._outcomes(report, StopSaleStep.ORDER_CANCEL)
        self.assertEqual(outcomes, [StopSaleOutcome.UNKNOWN])
        self.assertNotIn(StopSaleOutcome.SUCCEEDED, outcomes)

    def test_each_step_is_recorded_separately_for_a_partial_success(self):

        self.coupang.script["stop"] = [_response(401, {"code": 401, "message": "auth"})]
        report = self._handle()
        self.assertEqual(self._outcomes(report, StopSaleStep.SALE_STOP), [StopSaleOutcome.ACTION_REQUIRED])
        self.assertEqual(self._outcomes(report, StopSaleStep.CHANNEL_ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        self.assertEqual(self._outcomes(report, StopSaleStep.ORDER_CANCEL), [StopSaleOutcome.SUCCEEDED])
        incomplete_steps = {r.step for r in report.incomplete()}
        self.assertIn(StopSaleStep.SALE_STOP, incomplete_steps)
        self.assertIn(StopSaleStep.REFUND, incomplete_steps)


if __name__ == "__main__":
    unittest.main()
