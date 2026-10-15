"""
=========================================================
Homez OS

File : tests/test_order_detection_health.py

2026-10-16 — 주문 자동 감지의 "실패처럼 보이던" 문제를 고정한다.
  1) 주기는 5분으로 고정이다(저장값이 달라도, 입력이 달라도).
  2) 성공하면 이전 오류·건너뜀 사유를 지운다(예전에는 성공한 뒤에도 ORDER_PROVIDER_INTERNAL_ERROR가 남았다).
  3) 쿠팡 조회 중 예외는 원인 종류별 코드로 남긴다(예전에는 전부 INTERNAL_ERROR).
  4) 화면용 한 줄 상태(describe_health)는 기능 모드와 마지막 결과를 합쳐 판단한다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.automation_safety.service import SafetyService
from app.domains.order.auto_collection_scheduler import FIXED_INTERVAL_MINUTES
from app.domains.order.auto_collection_scheduler import OrderCollectionTickOutcome
from app.domains.order.auto_collection_scheduler import describe_health
from app.domains.order.auto_collection_scheduler import get_or_create_auto_collection_state
from app.domains.order.auto_collection_scheduler import run_order_collection_tick
from app.domains.order.auto_collection_scheduler import set_interval_minutes
from app.domains.order.auto_collection_scheduler import trigger_company_now
from app.domains.order.collection_model import OrderAutoCollectionState
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.order.collection_model import OrderSkuResolution
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.coupang_collection_service import classify_provider_exception
from app.domains.order.model import Order
from app.domains.platform_alert.model import PlatformAlertDeliveryLog
from app.domains.platform_alert.model import PlatformAlertRecipient
from app.domains.store_connection.model import StoreConnection
from tests.test_order_auto_collection_scheduler import T0
from tests.test_order_auto_collection_scheduler import _empty_success_result
from tests.test_order_auto_collection_scheduler import _NOT_RESTRICTED
from tests.test_order_auto_collection_scheduler import _Provider


class Base_(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(self.engine, tables=[
            StoreConnection.__table__, OrderChannelFulfillment.__table__, UnresolvedOrderItem.__table__,
            OrderCollectionCursor.__table__, OrderSkuResolution.__table__, Order.__table__,
            OrderAutoCollectionState.__table__, FunctionAutomationState.__table__, EmergencyStop.__table__,
            PlatformAlertRecipient.__table__, PlatformAlertDeliveryLog.__table__,
        ])
        self.db = sessionmaker(bind=self.engine)()
        self.store = InMemoryCredentialStore()
        self.safety = SafetyService(self.db)
        self.store.save("cred-1", {"vendor_id": "cred-1", "access_key": "a", "secret_key": "s"})
        self.db.add(StoreConnection(
            company_id=1, marketplace_code="COUPANG", display_name="쿠팡", seller_identifier="cred-1",
            credential_reference="cred-1", masked_credential_hint="***", connection_status="CONNECTED",
            credential_version=1, created_by=1, creation_idempotency_key="c1", creation_request_fingerprint="a" * 64,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def automatic(self, mode=FunctionMode.AUTOMATIC):
        self.safety.set_function_mode(1, FunctionCode.ORDER_COLLECTION, mode, set_by=1, is_admin=True)

    def tick(self, factory, now=T0):
        return run_order_collection_tick(
            self.db, self.store, now=now, provider_factory=factory, is_restricted_mode_check=_NOT_RESTRICTED,
        )

    def trigger(self, factory, now=T0):
        return trigger_company_now(
            self.db, self.store, 1, now=now, provider_factory=factory, is_restricted_mode_check=_NOT_RESTRICTED,
        )

    @staticmethod
    def ok_factory():
        return lambda _cred: _Provider(_empty_success_result())

    @staticmethod
    def raising_factory(exc):
        class _Raiser:
            def collect(self, **_kw):
                raise exc
        return lambda _cred: _Raiser()


class FixedIntervalTests(Base_):

    def test_the_interval_is_five_minutes_and_cannot_be_changed(self):
        self.assertEqual(FIXED_INTERVAL_MINUTES, 5)
        self.assertEqual(set_interval_minutes(self.db, 1, 5).interval_minutes, 5)
        for bad in (1, 2, 4, 6, 10, 1440, 0, -5):
            with self.assertRaises(BadRequestException, msg=bad):
                set_interval_minutes(self.db, 1, bad)
        self.assertEqual(get_or_create_auto_collection_state(self.db, 1).interval_minutes, 5)

    def test_a_stored_value_other_than_five_is_corrected_when_read(self):
        state = get_or_create_auto_collection_state(self.db, 1)
        state.interval_minutes = 1
        self.db.commit()
        self.assertEqual(get_or_create_auto_collection_state(self.db, 1).interval_minutes, 5)

    def test_the_due_check_uses_five_minutes_even_if_the_row_says_otherwise(self):
        self.automatic()
        calls = []
        factory = lambda _cred: _Provider(_empty_success_result(), on_collect=lambda kw: calls.append(kw))  # noqa: E731
        self.tick(factory, now=T0)
        state = self.db.query(OrderAutoCollectionState).one()
        state.interval_minutes = 1  # 저장값을 일부러 어긋나게 만든다
        self.db.commit()
        result = self.tick(factory, now=T0 + timedelta(minutes=2))
        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.NOT_DUE)
        result = self.tick(factory, now=T0 + timedelta(minutes=5))
        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.SUCCEEDED)
        self.assertEqual(len(calls), 2)


class StaleErrorTests(Base_):

    def seed_stale(self):
        state = get_or_create_auto_collection_state(self.db, 1)
        state.last_error_summary = "ORDER_PROVIDER_INTERNAL_ERROR"
        state.last_skip_reason = "function_mode_not_automatic"
        state.last_status = OrderCollectionTickOutcome.FAILED
        state.consecutive_failure_count = 2
        self.db.commit()

    def test_a_successful_tick_clears_the_old_error_and_skip_reason(self):
        self.automatic()
        self.seed_stale()
        result = self.tick(self.ok_factory())
        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.SUCCEEDED)
        state = self.db.query(OrderAutoCollectionState).one()
        self.assertIsNone(state.last_error_summary)
        self.assertIsNone(state.last_skip_reason)
        self.assertEqual((state.last_status, state.consecutive_failure_count), ("SUCCEEDED", 0))

    def test_a_successful_manual_trigger_clears_them_too(self):
        self.seed_stale()
        entry = self.trigger(self.ok_factory())
        self.assertEqual(entry.outcome, OrderCollectionTickOutcome.SUCCEEDED)
        state = self.db.query(OrderAutoCollectionState).one()
        self.assertIsNone(state.last_error_summary)
        self.assertIsNone(state.last_skip_reason)

    def test_a_skip_does_not_erase_a_real_error_record(self):
        self.seed_stale()
        # 자동이 꺼진 상태의 tick은 건너뛰기만 기록한다(오류 기록을 지우지 않는다).
        self.tick(self.ok_factory())
        state = self.db.query(OrderAutoCollectionState).one()
        self.assertEqual(state.last_status, OrderCollectionTickOutcome.SKIPPED_NOT_AUTOMATIC)
        self.assertEqual(state.last_error_summary, "ORDER_PROVIDER_INTERNAL_ERROR")


class ExceptionClassificationTests(Base_):

    def test_classification_by_exception_type(self):
        self.assertEqual(classify_provider_exception(requests.Timeout()), "ORDER_PROVIDER_TIMEOUT")
        self.assertEqual(classify_provider_exception(requests.ConnectionError()), "ORDER_PROVIDER_NETWORK_ERROR")
        self.assertEqual(classify_provider_exception(PermissionError("x")), "ORDER_PROVIDER_CALL_BLOCKED")
        self.assertEqual(classify_provider_exception(ValueError("x")), "ORDER_PROVIDER_INTERNAL_ERROR")

    def test_the_specific_code_reaches_the_screen_state_and_secrets_stay_out(self):
        secret = "https://api-gateway.coupang.com/?accessKey=SUPERSECRET"
        entry = self.trigger(self.raising_factory(PermissionError(secret)))
        self.assertEqual(entry.outcome, OrderCollectionTickOutcome.FAILED)
        state = self.db.query(OrderAutoCollectionState).one()
        self.assertEqual(state.last_error_summary, "ORDER_PROVIDER_CALL_BLOCKED")
        cursor = self.db.query(OrderCollectionCursor).filter_by(channel_status="ACCEPT").one()
        self.assertEqual(cursor.last_error_code, "ORDER_PROVIDER_CALL_BLOCKED")
        for stored in (state.last_error_summary, cursor.last_error_code, str(entry.detail)):
            self.assertNotIn("SUPERSECRET", str(stored))

    def test_a_timeout_is_recorded_as_a_timeout(self):
        self.trigger(self.raising_factory(requests.Timeout("slow")))
        self.assertEqual(self.db.query(OrderAutoCollectionState).one().last_error_summary, "ORDER_PROVIDER_TIMEOUT")


class HealthTests(Base_):

    def state(self, **kwargs):
        state = get_or_create_auto_collection_state(self.db, 1)
        for key, value in kwargs.items():
            setattr(state, key, value)
        return state

    def test_off_when_the_function_is_manual_even_if_the_last_run_succeeded(self):
        state = self.state(last_status="SUCCEEDED", last_succeeded_at=T0)
        code, message = describe_health(FunctionMode.MANUAL, state)
        self.assertEqual(code, "OFF")
        self.assertIn("꺼져", message)

    def test_paused_and_error_modes(self):
        state = self.state()
        self.assertEqual(describe_health(FunctionMode.PAUSED, state)[0], "PAUSED")
        self.assertEqual(describe_health(FunctionMode.ERROR, state)[0], "ERROR")

    def test_automatic_modes(self):
        self.assertEqual(describe_health(FunctionMode.AUTOMATIC, self.state())[0], "WAITING")
        self.assertEqual(
            describe_health(FunctionMode.AUTOMATIC, self.state(last_status="SUCCEEDED", last_succeeded_at=T0))[0], "OK",
        )
        failing = self.state(last_status="FAILED", consecutive_failure_count=1, last_error_summary="ORDER_PROVIDER_TIMEOUT")
        code, message = describe_health(FunctionMode.AUTOMATIC, failing)
        self.assertEqual(code, "FAILING")
        self.assertIn("ORDER_PROVIDER_TIMEOUT", message)
        blocked = self.state(last_status="SKIPPED_VALIDATION_BLOCKED", consecutive_failure_count=0, last_succeeded_at=T0)
        self.assertEqual(describe_health(FunctionMode.AUTOMATIC, blocked)[0], "BLOCKED")

    def test_a_real_automatic_run_ends_up_ok(self):
        self.automatic()
        self.tick(self.ok_factory())
        code, _ = describe_health(self.safety.get_function_mode(1, FunctionCode.ORDER_COLLECTION), self.db.query(OrderAutoCollectionState).one())
        self.assertEqual(code, "OK")


if __name__ == "__main__":
    unittest.main()
