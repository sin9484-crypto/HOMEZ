"""
=========================================================
Homez OS

File : tests/test_channel_action_service.py

2026-10-05 — 판매채널 외부 변경 작업 장부(`channel_action_requests`)와 쿠팡 실행기
(`CoupangChannelActions`)의 중복 방지·재시도·결과불명 규칙 검증. 격리 SQLite 파일 DB +
Fake HTTP(외부 호출 없음). 동시 실행은 스레드별 독립 세션으로 같은 DB 파일을 공유해 확인한다.
=========================================================
"""

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import requests
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.purchase_task.channel_action_service import (
    ActionOutcome, ChannelActionJournal, CoupangChannelActions,
)
from app.domains.purchase_task.constants import ChannelActionPolicy
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.coupang_channel_control_provider import (
    CoupangChannelControlProvider,
)
from app.domains.purchase_task.model import ChannelActionRequest
from app.domains.store_connection.model import StoreConnection

VENDOR = "A00012345"
REF = "HOMEZ_TEST:store_connection:test"


def _response(status, body=None, headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode("utf-8") if body is not None else b"x"
    response.headers.update(headers or {})
    return response


class CountingTransport:
    """호출을 세고 종류별 응답 시나리오를 돌려준다(스레드 안전)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.bodies = []
        self.script = {"stop": [_response(200, {"code": "SUCCESS"})], "cancel": [], "inventory": [], "sheet": []}

    def request(self, method, url, *, headers, json_body, timeout):
        self.bodies.append(json_body)
        kind = ("stop" if url.endswith("/sales/stop") else
                "cancel" if url.endswith("/cancel") else
                "inventory" if url.endswith("/inventories") else "sheet")
        with self.lock:
            self.calls.append((kind, method))
            queue = self.script[kind]
            outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def count(self, kind):
        return len([c for c in self.calls if c[0] == kind])


class ActionTestBase(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.path = Path(path)
        self.engine = create_engine(
            f"sqlite:///{self.path}", connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(
            bind=self.engine, tables=[StoreConnection.__table__, ChannelActionRequest.__table__],
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.addCleanup(self._cleanup)
        self.store = self._store(1)
        self.creds = InMemoryCredentialStore()
        self.creds.save(REF, {
            "access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR, "wing_user_id": "wing",
        })
        # 판매 연결의 seller_identifier는 사용자가 입력한 이름이다 — 업체코드와 같다고 가정하지 않는다.
        self.transport = CountingTransport()
        self.clock = [datetime(2026, 10, 5, 12, 0, 0)]

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        try:
            self.path.unlink()
        except OSError:
            pass

    def _store(self, company_id, seller="홈즈쿠팡", ref=REF):
        store = StoreConnection(
            company_id=company_id, marketplace_code="COUPANG", display_name="쿠팡",
            seller_identifier=seller, connection_status="CONNECTED", created_by=1,
            creation_idempotency_key=f"s-{company_id}-{seller}",
            creation_request_fingerprint="f" * 64, credential_reference=ref,
        )
        self.db.add(store)
        self.db.commit()
        return store

    def actions(self, db=None, **kw):
        return CoupangChannelActions(
            db or self.db, credential_store=self.creds,
            provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport),
            now_factory=lambda: self.clock[0], **kw,
        )

    def stop(self, actions=None, **kw):
        return (actions or self.actions()).stop_sale(
            company_id=1, store_connection_id=self.store.id, vendor_item_id="5469001088", **kw)

    def row(self):
        self.db.expire_all()
        return self.db.query(ChannelActionRequest).one()


class DuplicatePreventionTestCase(ActionTestBase):

    def test_same_request_is_sent_once_and_rerun_is_already_done(self):
        first = self.stop()
        second = self.stop(self.actions())  # 새 실행기 = 프로세스 재시작과 같은 효과
        self.assertEqual((first.outcome, first.executed), (ActionOutcome.SUCCEEDED, True))
        self.assertEqual((second.outcome, second.executed), (ActionOutcome.ALREADY_DONE, False))
        self.assertEqual(self.transport.count("stop"), 1)
        self.assertEqual(self.row().status, ChannelActionStatus.SUCCEEDED)

    def test_database_unique_constraint_is_the_last_line_of_defence(self):
        self.stop()
        self.db.add(ChannelActionRequest(
            company_id=1, store_connection_id=self.store.id, action_type="SALE_STOP",
            target_key="vi:5469001088", status="REQUESTING", attempt_count=1,
            requested_at=self.clock[0]))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_concurrent_executions_send_exactly_one_request(self):
        barrier = threading.Barrier(8)
        results = []

        def worker():
            session = self.Session()
            try:
                barrier.wait(timeout=10)
                results.append(self.stop(self.actions(session)))
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(len(results), 8)
        self.assertEqual(self.transport.count("stop"), 1)
        executed = [r for r in results if r.executed]
        self.assertEqual(len(executed), 1)
        # 나머지는 이미 완료됐거나 다른 요청이 진행 중(결과불명으로 취급)이다 — 새 요청은 아니다.
        self.assertTrue(all(r.outcome in (ActionOutcome.ALREADY_DONE, ActionOutcome.UNKNOWN)
                            for r in results if not r.executed))

    def test_external_success_followed_by_internal_save_failure_is_not_resent(self):
        """요청은 성공했는데 장부 확정(finish)이 실패한 경우 — 재실행이 같은 요청을 다시 보내면 안 된다."""

        with mock.patch.object(ChannelActionJournal, "finish", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.stop()
        self.assertEqual(self.transport.count("stop"), 1)
        self.assertEqual(self.row().status, ChannelActionStatus.REQUESTING)  # 요청 전에 확정돼 있었다

        again = self.stop(self.actions())
        self.assertEqual((again.outcome, again.executed), (ActionOutcome.UNKNOWN, False))
        self.assertEqual(self.transport.count("stop"), 1, "재실행은 다시 요청하지 않는다")

        # 조회로 대조해 적용을 확인하면 성공으로 확정된다(역시 새 요청 없음).
        self.transport.script["inventory"] = [_response(200, {"code": "SUCCESS", "data": {"onSale": False}})]
        self.clock[0] += timedelta(seconds=600)  # 오래 남은 REQUESTING = 요청 프로세스 소실
        checked = self.actions().check_sale_stopped(
            company_id=1, store_connection_id=self.store.id, vendor_item_id="5469001088")
        self.assertEqual(checked.outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.stop(self.actions()).outcome, ActionOutcome.ALREADY_DONE)
        self.assertEqual(self.transport.count("stop"), 1)

    def test_fresh_requesting_row_is_not_reconciled_while_a_request_may_be_in_flight(self):
        with mock.patch.object(ChannelActionJournal, "finish", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                self.stop()
        self.transport.script["inventory"] = [_response(200, {"code": "SUCCESS", "data": {"onSale": True}})]
        checked = self.actions().check_sale_stopped(
            company_id=1, store_connection_id=self.store.id, vendor_item_id="5469001088")
        self.assertEqual(checked.outcome, ActionOutcome.UNKNOWN)
        self.assertEqual(self.transport.count("inventory"), 0, "진행 중일 수 있는 요청은 조회·판정하지 않는다")
        self.assertEqual(self.row().status, ChannelActionStatus.REQUESTING)


class RetryPolicyTestCase(ActionTestBase):

    def test_rate_limit_retries_only_after_the_interval_and_only_up_to_the_limit(self):
        self.transport.script["stop"] = [_response(429, {"code": 429}, {})]
        first = self.stop()
        self.assertEqual(first.outcome, ActionOutcome.RETRY_WAIT)
        self.assertEqual(self.row().status, ChannelActionStatus.RETRYABLE)
        self.assertEqual(self.row().attempt_count, 1)

        waiting = self.stop()
        self.assertEqual((waiting.outcome, waiting.executed), (ActionOutcome.RETRY_WAIT, False))
        self.assertEqual(self.transport.count("stop"), 1, "간격 안에서는 다시 보내지 않는다")

        self.clock[0] += timedelta(seconds=ChannelActionPolicy.RETRY_DELAYS_SECONDS[0] + 1)
        second = self.stop()
        self.assertEqual((second.outcome, second.executed), (ActionOutcome.RETRY_WAIT, True))
        self.assertEqual(self.row().attempt_count, 2)

        self.clock[0] += timedelta(seconds=ChannelActionPolicy.RETRY_DELAYS_SECONDS[1] + 1)
        third = self.stop()  # 3번째(마지막) 시도도 429
        self.assertEqual(third.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.row().status, ChannelActionStatus.ACTION_REQUIRED)
        self.assertEqual(self.row().last_error_class, "RETRY_EXHAUSTED")
        calls = self.transport.count("stop")
        self.clock[0] += timedelta(days=3)
        self.assertEqual(self.stop().outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.transport.count("stop"), calls, "한도 소진 뒤에는 자동 반복하지 않는다")
        self.assertEqual(calls, ChannelActionPolicy.MAX_ATTEMPTS)

    def test_retry_after_header_extends_the_wait(self):
        self.transport.script["stop"] = [_response(429, {}, {"Retry-After": "7200"})]
        self.stop()
        self.assertGreaterEqual(
            (self.row().next_retry_at - self.clock[0]).total_seconds(), 7200)

    def test_request_and_permission_errors_are_never_repeated_automatically(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.db.query(ChannelActionRequest).delete()
                self.db.commit()
                self.transport.calls.clear()
                self.transport.script["stop"] = [_response(status, {"code": status, "message": "m"})]
                first = self.stop()
                self.assertEqual(first.outcome, ActionOutcome.ACTION_REQUIRED)
                for _ in range(3):
                    self.clock[0] += timedelta(days=1)
                    again = self.stop(self.actions())
                    self.assertEqual((again.outcome, again.executed), (ActionOutcome.ACTION_REQUIRED, False))
                self.assertEqual(self.transport.count("stop"), 1)

    def test_explicit_human_retry_after_the_cause_is_fixed_sends_again(self):
        self.transport.script["stop"] = [_response(403, {"code": 403}), _response(200, {"code": "SUCCESS"})]
        self.assertEqual(self.stop().outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.stop().outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.transport.count("stop"), 1)
        fixed = self.stop(retry_action_required=True)
        self.assertEqual((fixed.outcome, fixed.executed), (ActionOutcome.SUCCEEDED, True))
        self.assertEqual(self.transport.count("stop"), 2)


class UnknownResultTestCase(ActionTestBase):

    def test_server_error_and_timeout_are_unknown_and_never_resent(self):
        for outcome in (_response(500, {"code": 500}), requests.Timeout("t")):
            with self.subTest(outcome=repr(outcome)):
                self.db.query(ChannelActionRequest).delete()
                self.db.commit()
                self.transport.calls.clear()
                self.transport.script["stop"] = [outcome]
                first = self.stop()
                self.assertEqual((first.outcome, first.executed), (ActionOutcome.UNKNOWN, True))
                for _ in range(3):
                    self.clock[0] += timedelta(days=1)
                    again = self.stop(self.actions())
                    self.assertEqual((again.outcome, again.executed), (ActionOutcome.UNKNOWN, False))
                self.assertEqual(self.transport.count("stop"), 1)

    def test_reconciliation_confirms_applied_or_not_applied_and_unreadable_stays_unknown(self):
        self.transport.script["stop"] = [_response(503, {})]
        self.stop()
        check = lambda: self.actions().check_sale_stopped(  # noqa: E731
            company_id=1, store_connection_id=self.store.id, vendor_item_id="5469001088")

        self.transport.script["inventory"] = [_response(500, {})]
        self.assertEqual(check().outcome, ActionOutcome.UNKNOWN)
        self.assertEqual(self.row().status, ChannelActionStatus.UNKNOWN)

        # 미적용이 확인되면 재시도 가능으로 되돌리되 시도 횟수는 유지한다
        self.transport.script["inventory"] = [_response(200, {"code": "SUCCESS", "data": {"onSale": True}})]
        self.assertEqual(check().outcome, ActionOutcome.RETRY_WAIT)
        self.assertEqual(self.row().status, ChannelActionStatus.RETRYABLE)
        self.assertEqual(self.row().attempt_count, 1)
        self.transport.script["stop"] = [_response(200, {"code": "SUCCESS"})]
        resent = self.stop()
        self.assertEqual((resent.outcome, resent.executed), (ActionOutcome.SUCCEEDED, True))
        self.assertEqual(self.row().attempt_count, 2)

    def test_reconciliation_that_finds_it_applied_never_resends(self):
        self.transport.script["stop"] = [requests.Timeout("t")]
        self.stop()
        self.transport.script["inventory"] = [_response(200, {"code": "SUCCESS", "data": {"onSale": False}})]
        checked = self.actions().check_sale_stopped(
            company_id=1, store_connection_id=self.store.id, vendor_item_id="5469001088")
        self.assertEqual(checked.outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.stop().outcome, ActionOutcome.ALREADY_DONE)
        self.assertEqual(self.transport.count("stop"), 1)


class PreconditionTestCase(ActionTestBase):

    def test_no_request_and_no_journal_row_when_the_connection_cannot_be_trusted(self):
        # 다른 회사의 연결
        other = self._store(2, seller="A00099999")
        r = self.actions().stop_sale(company_id=1, store_connection_id=other.id,
                                     vendor_item_id="5469001088")
        self.assertEqual(r.outcome, ActionOutcome.ACTION_REQUIRED)
        # 승인된 시험이 업체코드를 지정했는데 자격증명의 업체코드가 다름
        mismatch = self.stop(self.actions(expected_vendor_id="A00099999"))
        self.assertEqual(mismatch.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertIn("업체코드", mismatch.detail)
        # 자격증명 없음
        self.store.credential_reference = "HOMEZ_TEST:missing"
        self.db.commit()
        self.assertEqual(self.stop().outcome, ActionOutcome.ACTION_REQUIRED)
        # 연결 해제 상태
        self.store.credential_reference = REF
        self.store.connection_status = "DISCONNECTED"
        self.db.commit()
        self.assertEqual(self.stop().outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.db.query(ChannelActionRequest).count(), 0)

    def test_missing_journal_table_blocks_external_requests(self):
        ChannelActionRequest.__table__.drop(self.engine)
        self.db.commit()
        result = self.stop()
        self.assertEqual(result.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertIn("Migration", result.detail)
        self.assertEqual(self.transport.calls, [])

    def test_different_targets_do_not_share_state(self):
        a = self.actions()
        a.stop_sale(company_id=1, store_connection_id=self.store.id, vendor_item_id="1001")
        a.stop_sale(company_id=1, store_connection_id=self.store.id, vendor_item_id="1002")
        self.assertEqual(self.transport.count("stop"), 2)
        self.assertEqual(self.db.query(ChannelActionRequest).count(), 2)
        keys = {r.target_key for r in self.db.query(ChannelActionRequest).all()}
        self.assertEqual(keys, {"vi:1001", "vi:1002"})


class OrderCancelActionTestCase(ActionTestBase):

    def setUp(self):
        super().setUp()
        self.transport.script["cancel"] = [_response(200, {
            "code": "200", "message": "Success", "data": {"orderId": 5001, "failedItemIds": []}})]

    def cancel(self, actions=None, box="9001", **kw):
        return (actions or self.actions()).cancel_order(
            company_id=1, store_connection_id=self.store.id, channel_order_id="5001",
            shipment_box_id=box, items=[("1001", 1)], **kw)

    def test_cancel_is_sent_once_per_box_and_rerun_is_already_done(self):
        self.assertEqual(self.cancel().outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.cancel(self.actions()).outcome, ActionOutcome.ALREADY_DONE)
        self.assertEqual(self.transport.count("cancel"), 1)
        # 같은 주문의 다른 박스는 별개 요청이다
        self.assertEqual(self.cancel(box="9002").outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.transport.count("cancel"), 2)

    def test_cancel_timeout_is_unknown_and_reconciled_by_reading_the_order_sheet(self):
        self.transport.script["cancel"] = [requests.Timeout("t")]
        self.assertEqual(self.cancel().outcome, ActionOutcome.UNKNOWN)
        self.assertEqual(self.cancel(self.actions()).outcome, ActionOutcome.UNKNOWN)
        self.assertEqual(self.transport.count("cancel"), 1)
        self.clock[0] += timedelta(seconds=1)
        self.transport.script["sheet"] = [_response(200, {"code": 200, "data": {"orderItems": [
            {"vendorItemId": 1001, "shippingCount": 1, "cancelCount": 1}]}})]
        checked = self.actions().check_order_cancelled(
            company_id=1, store_connection_id=self.store.id, channel_order_id="5001",
            shipment_box_id="9001", items=[("1001", 1)])
        self.assertEqual(checked.outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.cancel(self.actions()).outcome, ActionOutcome.ALREADY_DONE)
        self.assertEqual(self.transport.count("cancel"), 1)

    def test_connection_name_is_not_compared_with_the_vendor_code_but_an_approved_code_is(self):
        """판매 연결의 seller_identifier는 사용자가 입력한 이름('홈즈쿠팡')이다 — 업체코드와 같다고
        가정해 막으면 실제 DB에서는 영원히 쓸 수 없다. 승인된 시험이 업체코드를 지정했을 때만 비교한다."""

        self.assertNotEqual(self.store.seller_identifier, VENDOR)
        self.assertEqual(self.cancel().outcome, ActionOutcome.SUCCEEDED)   # 이름≠코드여도 진행
        approved = self.actions(expected_vendor_id=VENDOR)
        self.assertEqual(self.cancel(approved, box="9002").outcome, ActionOutcome.SUCCEEDED)
        wrong = self.actions(expected_vendor_id="A00099999")
        refused = self.cancel(wrong, box="9003")
        self.assertEqual(refused.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.transport.count("cancel"), 2)

    def test_explicit_wing_user_id_is_used_for_that_request_only_and_never_stored(self):
        """상품등록 vendorUserId와 주문취소 userId는 같은 WING 로그인 ID다. 자격증명에 계정 단위
        보관 칸이 없으므로 승인된 시험이 명시 지정한 값 1건을 그 요청에만 쓰고 저장·복사하지 않는다."""

        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR})
        self.assertEqual(self.cancel().outcome, ActionOutcome.ACTION_REQUIRED)   # 지정 없음 → 요청 안 함
        self.assertEqual(self.transport.count("cancel"), 0)

        done = self.cancel(self.actions(wing_user_id="explicit-id"))
        self.assertEqual(done.outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.transport.bodies[-1]["userId"], "explicit-id")
        # 자격증명 저장소에는 복사되지 않았다 — 다음 요청(지정 없음)은 다시 막힌다
        self.assertNotIn("wing_user_id", self.creds.read(REF))
        again = self.cancel(self.actions(), box="9002")
        self.assertEqual(again.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.transport.count("cancel"), 1)

    def test_credential_wing_user_id_takes_precedence_over_an_explicit_one(self):
        self.cancel(self.actions(wing_user_id="explicit-id"))
        self.assertEqual(self.transport.bodies[-1]["userId"], "wing")

    def test_missing_wing_user_id_is_action_required_without_sending(self):
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR})
        result = self.cancel()
        self.assertEqual(result.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertIn("MISSING_WING_USER_ID", result.detail)
        self.assertEqual(self.transport.count("cancel"), 0)
        # 외부 요청이 없었으므로 장부에 남기지 않는다 — 원인을 고치면 사람의 재승인 없이 바로 실행된다.
        self.assertEqual(self.db.query(ChannelActionRequest).count(), 0)
        self.creds.save(REF, {
            "access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR, "wing_user_id": "wing"})
        self.assertEqual(self.cancel().outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.transport.count("cancel"), 1)


if __name__ == "__main__":
    unittest.main()
