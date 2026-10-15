"""
=========================================================
Homez OS

File : tests/test_external_call_enforcement.py

2026-10-16 — 바깥 호출 허용이 **실제로 호출을 막는지**를 기능 쪽에서 확인한다(가짜 HTTP, 외부 호출 없음).
  - 상품 출고일 변경·송장 등록: 허용이 없으면 쿠팡으로 요청이 나가지 않고, 장부에 결과불명이 남지 않는다.
  - 판매중지 같은 안전 장치는 허용을 기다리지 않는다(기록만).
  - 5분 주문 감지: 허용이 없으면 건너뛰고 요청을 올리며, 상시 허용이 있으면 5분마다 돈다.
    실행 환경이 호출을 막은 경우는 실패로 세지 않아 자동 중지로 이어지지 않는다.
  - 허용 테이블이 없으면(Migration 전) 예전 동작 그대로다.
=========================================================
"""

import unittest
from datetime import timedelta

from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.constants import LogOutcome
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.order.auto_collection_scheduler import OrderCollectionTickOutcome
from app.domains.order.collection_model import OrderAutoCollectionState
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_invoice_service import InvoiceServiceBase
from tests.test_coupang_product_days import PRODUCT
from tests.test_coupang_product_days import ServiceBase as DaysBase
from tests.test_coupang_product_days import detail_body
from tests.test_coupang_product_days import ok_put
from tests.test_coupang_invoice_upload import response
from tests.test_order_auto_collection_scheduler import T0
from tests.test_order_detection_health import Base_ as OrderBase


def _install(db):
    bind = db.get_bind()
    from app.domains.automation_safety.model import EmergencyStop
    from app.domains.automation_safety.model import FunctionAutomationState

    # 비상 정지 확인에 필요한 테이블 — 확인할 수 없으면 서비스가 안전 쪽(막음)으로 처리하기 때문이다.
    EmergencyStop.__table__.create(bind=bind, checkfirst=True)
    FunctionAutomationState.__table__.create(bind=bind, checkfirst=True)
    ExternalCallAllowance.__table__.create(bind=bind, checkfirst=True)
    ExternalCallLog.__table__.create(bind=bind, checkfirst=True)


def _grant(db, **kw):
    base = dict(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.PRODUCT_OUTBOUND_DAYS,
                mode=AllowanceMode.COUNTED, count=1, user_id=1)
    base.update(kw)
    return ExternalCallPolicyService(db).grant(1, **base)


class ProductDaysEnforcementTests(DaysBase):

    def setUp(self):
        super().setUp()
        _install(self.db)

    def test_without_an_allowance_nothing_goes_to_coupang(self):
        plan = self.service().plan(1, PRODUCT, 2)
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(plan))
        self.assertFalse(plan.sendable)
        self.assertEqual(self.transport.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).all()
        self.assertEqual([(a.kind, a.purpose) for a in pending], [("READ", "PRODUCT_OUTBOUND_DAYS")])

    def test_with_exact_allowances_the_whole_change_goes_through_and_is_all_used_up(self):
        _grant(self.db, kind=CallKind.READ, count=3)
        _grant(self.db, kind=CallKind.WRITE, count=1)
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        # 실제 사용 순서: 미리보기(조회 1) → 전송 직전 재확인(조회 2) → 변경(변경 1) → 사후 확인(조회 3)
        self.assertTrue(self.service().plan(1, PRODUCT, 2).sendable)
        result = self.service().send(1, 7, PRODUCT, 2, expected_current_days=[1, 1])
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual((self.transport.count("get"), self.transport.count("put")), (3, 1))
        used = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.EXHAUSTED).count()
        self.assertEqual(used, 2)
        outcomes = [log.outcome for log in self.db.query(ExternalCallLog).order_by(ExternalCallLog.id)]
        self.assertEqual(outcomes, [LogOutcome.SUCCEEDED] * 4)

    def test_a_missing_write_allowance_stops_before_the_put_and_leaves_no_ledger_row(self):
        _grant(self.db, kind=CallKind.READ, count=3)
        self.transport.get = [response(200, detail_body(days=(1, 1)))]
        result = self.service().send(1, 7, PRODUCT, 2, expected_current_days=[1, 1])
        self.assertEqual(result["outcome"], "ACTION_REQUIRED")
        self.assertIn("EXTERNAL_ALLOWANCE_REQUIRED", result["detail"])
        self.assertFalse(result["executed"])
        self.assertEqual(self.transport.count("put"), 0)
        self.assertEqual(self.db.query(ChannelActionRequest).count(), 0, "막힌 요청이 결과불명으로 남으면 안 된다")
        self.assertEqual(
            self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING, kind="WRITE").count(), 1,
        )

    def test_after_the_write_is_allowed_the_same_request_can_be_retried_normally(self):
        _grant(self.db, kind=CallKind.READ, count=6)
        self.service().send(1, 7, PRODUCT, 2, expected_current_days=[1, 1])
        _grant(self.db, kind=CallKind.WRITE, count=1)
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        result = self.service().send(1, 7, PRODUCT, 2, expected_current_days=[1, 1])
        self.assertEqual((result["outcome"], result["executed"]), ("SUCCEEDED", True))
        self.assertEqual(self.transport.count("put"), 1)

    def test_the_ledger_still_prevents_a_second_put_even_with_more_allowance(self):
        _grant(self.db, kind=CallKind.READ, count=12)
        _grant(self.db, kind=CallKind.WRITE, count=5)
        self.transport.get = [response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(1, 1))), response(200, detail_body(days=(2, 2)))]
        self.service().send(1, 7, PRODUCT, 2, expected_current_days=[1, 1])
        again = self.service().send(1, 7, PRODUCT, 2, expected_current_days=[2, 2])
        self.assertEqual(again["outcome"], "ALREADY_DONE")
        self.assertEqual(self.transport.count("put"), 1)

    def test_safety_actions_are_logged_but_never_wait_for_an_allowance(self):
        from app.domains.purchase_task.channel_action_service import CoupangChannelActions
        from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
        from datetime import datetime

        actions = CoupangChannelActions(
            self.db, credential_store=self.creds, now_factory=lambda: datetime(2026, 10, 16, 3, 0, 0),
            provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport),
        )
        self.transport.put = [response(200, ok_put())]
        result = actions.stop_sale(company_id=1, store_connection_id=self.store.id, vendor_item_id="12345", triggered_by=1)
        self.assertEqual(result.outcome, "SUCCEEDED")
        self.assertEqual(self.transport.count("put"), 1)
        log = self.db.query(ExternalCallLog).one()
        self.assertEqual((log.outcome, log.purpose, log.allowance_id), (LogOutcome.SUCCEEDED, "SALE_STOP", None))
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)


class InvoiceEnforcementTests(InvoiceServiceBase):

    def setUp(self):
        super().setUp()
        _install(self.db)

    def test_without_a_read_allowance_the_order_sheet_is_not_read(self):
        plan = self.service().plan(self.task.id, 1)
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(plan))
        self.assertEqual(self.transport.calls, [])

    def test_without_a_write_allowance_the_invoice_is_not_posted(self):
        _grant(self.db, kind=CallKind.READ, purpose=CallPurpose.INVOICE_UPLOAD, count=2)
        result = self.service().send(self.task.id, 1, 7, **self.values())
        self.assertEqual(result["outcome"], "ACTION_REQUIRED")
        self.assertIn("EXTERNAL_ALLOWANCE_REQUIRED", result["detail"])
        self.assertEqual(self.transport.count("invoice"), 0)
        self.assertEqual(self.rows(), [])

    def test_with_both_allowances_the_invoice_goes_out_once(self):
        _grant(self.db, kind=CallKind.READ, purpose=CallPurpose.INVOICE_UPLOAD, count=4)
        _grant(self.db, kind=CallKind.WRITE, purpose=CallPurpose.INVOICE_UPLOAD, count=1)
        result = self.service().send(self.task.id, 1, 7, **self.values())
        self.assertEqual((result["outcome"], result["executed"]), ("SUCCEEDED", True))
        self.assertEqual(self.transport.count("invoice"), 1)
        self.assertEqual(self.rows()[0].status, ChannelActionStatus.SUCCEEDED)


class OrderDetectionEnforcementTests(OrderBase):

    def setUp(self):
        super().setUp()
        _install(self.db)
        self.automatic()

    def calls_factory(self, calls):
        from tests.test_order_auto_collection_scheduler import _Provider
        from tests.test_order_auto_collection_scheduler import _empty_success_result

        return lambda _cred: _Provider(_empty_success_result(), on_collect=lambda kw: calls.append(kw))

    def standing(self, per_hour=14):
        return _grant(self.db, purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING, count=None, per_hour_limit=per_hour, hours=24)

    def test_no_allowance_means_no_call_a_request_is_raised_and_it_is_not_a_failure(self):
        calls = []
        result = self.tick(self.calls_factory(calls))
        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.SKIPPED_NO_ALLOWANCE)
        self.assertEqual(calls, [])
        state = self.db.query(OrderAutoCollectionState).one()
        self.assertEqual((state.last_status, state.consecutive_failure_count), ("SKIPPED_NO_ALLOWANCE", 0))
        self.assertEqual(self.db.query(ExternalCallAllowance).filter_by(status="PENDING", purpose="ORDER_DETECTION").count(), 1)
        self.assertEqual(self.safety.get_function_mode(1, FunctionCode.ORDER_COLLECTION), FunctionMode.AUTOMATIC)

    def test_repeated_ticks_do_not_pile_up_requests(self):
        calls = []
        for minutes in range(0, 30, 5):
            self.tick(self.calls_factory(calls), now=T0 + timedelta(minutes=minutes))
        self.assertEqual(self.db.query(ExternalCallAllowance).filter_by(status="PENDING").count(), 1)

    def test_with_a_standing_allowance_it_runs_every_five_minutes_and_logs_each_run(self):
        self.standing()
        calls = []
        outcomes = [
            self.tick(self.calls_factory(calls), now=T0 + timedelta(minutes=m)).entries[0].outcome for m in (0, 2, 5, 7, 10)
        ]
        self.assertEqual(outcomes, ["SUCCEEDED", "NOT_DUE", "SUCCEEDED", "NOT_DUE", "SUCCEEDED"])
        self.assertEqual(len(calls), 3)
        logs = self.db.query(ExternalCallLog).filter_by(purpose="ORDER_DETECTION").all()
        self.assertEqual([log.outcome for log in logs], [LogOutcome.SUCCEEDED] * 3)

    def test_the_hourly_cap_stops_runaway_polling(self):
        self.standing(per_hour=2)
        calls = []
        outcomes = [self.tick(self.calls_factory(calls), now=T0 + timedelta(minutes=5 * i)).entries[0].outcome for i in range(4)]
        self.assertEqual(outcomes, ["SUCCEEDED", "SUCCEEDED", "SKIPPED_NO_ALLOWANCE", "SKIPPED_NO_ALLOWANCE"])
        self.assertEqual(len(calls), 2)

    def test_an_environment_block_is_not_counted_as_a_failure_and_never_demotes_the_function(self):
        self.standing()
        for i in range(6):
            result = self.tick(self.raising_factory(PermissionError("blocked")), now=T0 + timedelta(minutes=5 * i))
            self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.SKIPPED_CALL_BLOCKED)
        state = self.db.query(OrderAutoCollectionState).one()
        self.assertEqual(state.consecutive_failure_count, 0)
        self.assertEqual(state.last_error_summary, "ORDER_PROVIDER_CALL_BLOCKED")
        self.assertEqual(self.safety.get_function_mode(1, FunctionCode.ORDER_COLLECTION), FunctionMode.AUTOMATIC)

    def test_a_real_provider_failure_still_counts_and_demotes_after_three(self):
        self.standing()
        import requests

        for i in range(3):
            self.tick(self.raising_factory(requests.ConnectionError("down")), now=T0 + timedelta(minutes=5 * i))
        self.assertEqual(self.db.query(OrderAutoCollectionState).one().consecutive_failure_count, 3)
        self.assertEqual(self.safety.get_function_mode(1, FunctionCode.ORDER_COLLECTION), FunctionMode.ERROR)

    def test_the_manual_check_needs_no_allowance_but_is_logged(self):
        calls = []
        entry = self.trigger(self.calls_factory(calls))
        self.assertEqual(entry.outcome, OrderCollectionTickOutcome.SUCCEEDED)
        self.assertEqual(len(calls), 1)
        log = self.db.query(ExternalCallLog).one()
        self.assertEqual((log.purpose, log.outcome), ("ORDER_DETECTION", LogOutcome.SUCCEEDED))

    def test_without_the_tables_the_old_behavior_is_unchanged(self):
        ExternalCallLog.__table__.drop(bind=self.db.get_bind())
        ExternalCallAllowance.__table__.drop(bind=self.db.get_bind())
        calls = []
        result = self.tick(self.calls_factory(calls))
        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.SUCCEEDED)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
