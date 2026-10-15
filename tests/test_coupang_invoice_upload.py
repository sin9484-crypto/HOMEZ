"""
=========================================================
Homez OS

File : tests/test_coupang_invoice_upload.py

2026-10-15 — 쿠팡 송장 등록(발주서 송장 업로드)의 공급자·작업 장부 규칙을 고정한다. 공식 문서("송장업로드 처리")의 요청
형식과 응답 판정(responseCode + 건별 responseList)을 그대로 따르고, 판정이 애매하면 결과불명으로 두어 자동 재요청이 일어나지
않게 한다. 격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.model import ChannelActionRequest
from app.domains.store_connection.model import StoreConnection

VENDOR = "A00012345"
REF = "HOMEZ_TEST:store_connection:invoice"


def response(status, body=None, headers=None):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode("utf-8") if body is not None else b"x"
    r.headers.update(headers or {})
    return r


def ok_body(box="111"):
    return {"code": 200, "message": "OK", "data": {
        "responseCode": 0, "responseMessage": "", "responseList": [
            {"shipmentBoxId": int(box), "succeed": True, "resultCode": "OK", "retryRequired": False, "resultMessage": ""}]}}


def fail_body(code="INVALID_STATUS", retry=False, response_code=99, message="x"):
    return {"code": 200, "message": "OK", "data": {
        "responseCode": response_code, "responseMessage": "", "responseList": [
            {"shipmentBoxId": 111, "succeed": False, "resultCode": code, "retryRequired": retry, "resultMessage": message}]}}


def sheet_body(status="INSTRUCT", invoice=None, items=None):
    data = {"shipmentBoxId": 111, "orderId": 999, "status": status,
            "orderItems": items if items is not None else [{"vendorItemId": 5469001088, "shippingCount": 1, "cancelCount": 0}]}
    if invoice:
        data.update({"invoiceNumber": invoice, "deliveryCompanyName": "롯데택배"})
    return {"code": "200", "message": "OK", "data": data}


class FakeTransport:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.requests = []
        self.invoice = [response(200, ok_body())]
        self.sheet = [response(200, sheet_body())]

    def request(self, method, url, *, headers, json_body, timeout):
        kind = "invoice" if url.endswith("/orders/invoices") else "sheet"
        with self.lock:
            self.calls.append((kind, method))
            self.requests.append((kind, method, url, json_body, headers))
            queue = self.invoice if kind == "invoice" else self.sheet
            outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def count(self, kind):
        return len([c for c in self.calls if c[0] == kind])


def provider(transport):
    return CoupangChannelControlProvider(
        {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR}, transport=transport,
    )


UPLOAD = dict(order_id="999", shipment_box_id="111", vendor_item_ids=["5469001088"],
              delivery_company_code="HYUNDAI", invoice_number="259606480560")


class UploadRequestTests(unittest.TestCase):

    def test_the_request_follows_the_official_shape_for_a_whole_box_shipment(self):
        t = FakeTransport()
        result = provider(t).upload_invoice(**UPLOAD)
        self.assertEqual(result.kind, CallKind.SUCCEEDED)
        kind, method, url, body, headers = t.requests[0]
        self.assertEqual((kind, method), ("invoice", "POST"))
        self.assertTrue(url.endswith(f"/v2/providers/openapi/apis/api/v4/vendors/{VENDOR}/orders/invoices"))
        self.assertEqual(body["vendorId"], VENDOR)
        dto = body["orderSheetInvoiceApplyDtos"][0]
        self.assertEqual(dto, {
            "shipmentBoxId": 111, "orderId": 999, "vendorItemId": 5469001088, "deliveryCompanyCode": "HYUNDAI",
            "invoiceNumber": "259606480560", "splitShipping": False, "preSplitShipped": False, "estimatedShippingDate": "",
        })
        self.assertIn("Authorization", headers)

    def test_every_active_item_of_the_box_is_sent_one_entry_each(self):
        t = FakeTransport()
        t.invoice = [response(200, {"code": 200, "data": {"responseCode": 0, "responseList": [
            {"shipmentBoxId": 111, "succeed": True, "resultCode": "OK"}, {"shipmentBoxId": 111, "succeed": True, "resultCode": "OK"}]}})]
        provider(t).upload_invoice(**{**UPLOAD, "vendor_item_ids": ["1", "2"]})
        self.assertEqual([d["vendorItemId"] for d in t.requests[0][3]["orderSheetInvoiceApplyDtos"]], [1, 2])

    def test_split_shipping_is_never_requested(self):
        t = FakeTransport()
        provider(t).upload_invoice(**UPLOAD)
        for dto in t.requests[0][3]["orderSheetInvoiceApplyDtos"]:
            self.assertFalse(dto["splitShipping"])
            self.assertFalse(dto["preSplitShipped"])
            self.assertEqual(dto["estimatedShippingDate"], "")

    def test_a_malformed_target_is_refused_locally_without_any_request(self):
        t = FakeTransport()
        p = provider(t)
        for override in ({"order_id": "99x"}, {"shipment_box_id": ""}, {"vendor_item_ids": []}, {"vendor_item_ids": ["a"]},
                         {"delivery_company_code": "롯데택배"}, {"delivery_company_code": "hyundai"}, {"delivery_company_code": ""},
                         {"invoice_number": "12"}, {"invoice_number": "1234 5678"}, {"invoice_number": ""}):
            result = p.upload_invoice(**{**UPLOAD, **override})
            self.assertEqual(result.kind, CallKind.ACTION_REQUIRED, override)
            self.assertIsNone(result.http_status)
        self.assertEqual(t.calls, [])

    def test_the_courier_code_is_never_guessed_from_a_name(self):
        result = provider(FakeTransport()).upload_invoice(**{**UPLOAD, "delivery_company_code": "롯데택배"})
        self.assertEqual(result.error_class, "INVALID_COURIER")


class ResponseJudgementTests(unittest.TestCase):

    def judge(self, outcome):
        t = FakeTransport()
        t.invoice = [outcome]
        return provider(t).upload_invoice(**UPLOAD)

    def test_success_needs_the_overall_code_and_every_entry_to_agree(self):
        self.assertEqual(self.judge(response(200, ok_body())).kind, CallKind.SUCCEEDED)

    def test_an_explicit_rejection_needs_a_person_and_is_never_retried_automatically(self):
        for code in ("INVALID_STATUS", "NOT_FOUND_SHIPMENT_BOX", "NOT_ALLOW_INVOICE_NUMBER_RE_UPLOAD", "ORDER_DELIVERY_CANCELED",
                     "DUPLICATE_INVOICE_NUMBER", "INVALID_INVOICE_NUMBER", "PERMISSION_DENIED"):
            for retry in (False, True):  # retryRequired=true여도 자동 재요청하지 않는다
                result = self.judge(response(200, fail_body(code, retry)))
                self.assertEqual(result.kind, CallKind.ACTION_REQUIRED, (code, retry))
                self.assertEqual(result.error_class, "REJECTED")
                self.assertIn(code, result.detail)

    def test_an_undefined_error_may_have_been_applied_so_it_is_unknown(self):
        result = self.judge(response(200, fail_body("UNDEFINED_ERROR_OCCUR", True)))
        self.assertEqual(result.kind, CallKind.UNKNOWN)

    def test_contradictory_results_are_unknown_not_success(self):
        # 전체는 성공(0)인데 건별은 실패, 또는 건별 succeed가 비어 있음
        self.assertEqual(self.judge(response(200, fail_body("INVALID_STATUS", False, response_code=0))).kind, CallKind.UNKNOWN)
        body = ok_body()
        body["data"]["responseList"][0].pop("succeed")
        self.assertEqual(self.judge(response(200, body)).kind, CallKind.UNKNOWN)
        body = ok_body()
        body["data"]["responseCode"] = 1  # 부분실패라는데 실패 건이 없다
        self.assertEqual(self.judge(response(200, body)).kind, CallKind.UNKNOWN)

    def test_an_unparsable_or_empty_200_is_unknown(self):
        for outcome in (response(200, {"code": 200}), response(200, {"data": {"responseCode": 0}}),
                        response(200, {"data": {"responseCode": 0, "responseList": []}}), response(200, None)):
            self.assertEqual(self.judge(outcome).kind, CallKind.UNKNOWN)

    def test_http_failures_follow_the_same_classes_as_the_other_channel_actions(self):
        self.assertEqual(self.judge(response(429, {"message": "x"}, {"Retry-After": "7"})).kind, CallKind.RETRYABLE)
        self.assertEqual(self.judge(response(400, {"message": "택배사 코드 오류"})).kind, CallKind.ACTION_REQUIRED)
        self.assertEqual(self.judge(response(401, {})).kind, CallKind.ACTION_REQUIRED)
        self.assertEqual(self.judge(response(302, {})).kind, CallKind.ACTION_REQUIRED)
        self.assertEqual(self.judge(response(500, {})).kind, CallKind.UNKNOWN)
        self.assertEqual(self.judge(requests.Timeout()).kind, CallKind.UNKNOWN)
        self.assertEqual(self.judge(requests.ConnectionError()).kind, CallKind.UNKNOWN)

    def test_the_detail_never_carries_secrets_or_the_invoice_number(self):
        result = self.judge(response(200, fail_body("INVALID_STATUS", False, message="x")))
        for secret in ("AK", "SK", "259606480560"):
            self.assertNotIn(secret, result.detail)


class OrderSheetReadTests(unittest.TestCase):

    def read(self, outcome):
        t = FakeTransport()
        t.sheet = [outcome]
        return provider(t).read_order_sheet("111"), t

    def test_status_items_and_invoice_are_read(self):
        sheet, t = self.read(response(200, sheet_body("DEPARTURE", invoice="259606480560")))
        self.assertTrue(sheet.ok)
        self.assertEqual((sheet.status, sheet.invoice_number, sheet.delivery_company_name), ("DEPARTURE", "259606480560", "롯데택배"))
        self.assertEqual(sheet.items, (("5469001088", 1, 0),))
        self.assertEqual(t.calls, [("sheet", "GET")])

    def test_an_order_without_an_invoice_reads_as_none(self):
        sheet, _ = self.read(response(200, sheet_body("INSTRUCT")))
        self.assertIsNone(sheet.invoice_number)

    def test_a_bad_or_failed_read_is_a_failure_not_a_value(self):
        for outcome in (response(404, {"message": "no"}), response(200, {"data": {"status": "", "orderItems": []}}),
                        response(200, {"data": {"orderItems": "x", "status": "INSTRUCT"}}), response(500, {})):
            sheet, _ = self.read(outcome)
            self.assertFalse(sheet.ok)
            self.assertIsNone(sheet.status)

    def test_the_invoice_reconciliation_compares_numbers_and_keeps_failures_unknown(self):
        t = FakeTransport()
        p = provider(t)
        t.sheet = [response(200, sheet_body("DEPARTURE", invoice="259606480560"))]
        self.assertTrue(p.read_invoice_registered("111", "259606480560").value)
        self.assertFalse(p.read_invoice_registered("111", "999999999999").value)
        t.sheet = [response(200, sheet_body("INSTRUCT"))]
        self.assertIs(p.read_invoice_registered("111", "259606480560").value, False)
        t.sheet = [response(500, {})]
        result = p.read_invoice_registered("111", "259606480560")
        self.assertFalse(result.ok)
        self.assertIsNone(result.value)


class LedgerTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.path = Path(path)
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(bind=self.engine, tables=[StoreConnection.__table__, ChannelActionRequest.__table__])
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.addCleanup(self._cleanup)
        store = StoreConnection(
            company_id=1, marketplace_code="COUPANG", display_name="쿠팡", seller_identifier="홈즈쿠팡",
            connection_status="CONNECTED", created_by=1, creation_idempotency_key="s1",
            creation_request_fingerprint="f" * 64, credential_reference=REF,
        )
        self.db.add(store)
        self.db.commit()
        self.store = store
        self.creds = InMemoryCredentialStore()
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR})
        self.transport = FakeTransport()
        self.clock = [datetime(2026, 10, 15, 12, 0, 0)]

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        try:
            self.path.unlink()
        except OSError:
            pass

    def actions(self):
        return CoupangChannelActions(
            self.db, credential_store=self.creds, now_factory=lambda: self.clock[0],
            provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport),
        )

    def send(self, actions=None, **kw):
        args = dict(company_id=1, store_connection_id=self.store.id, channel_order_id="999", shipment_box_id="111",
                    vendor_item_ids=["5469001088"], delivery_company_code="HYUNDAI", invoice_number="259606480560")
        args.update(kw)
        return (actions or self.actions()).register_invoice(**args)

    def row(self):
        self.db.expire_all()
        return self.db.query(ChannelActionRequest).one()

    def test_the_same_invoice_is_sent_once_even_after_a_restart(self):
        first = self.send()
        second = self.send(self.actions())
        self.assertEqual((first.outcome, first.executed), (ActionOutcome.SUCCEEDED, True))
        self.assertEqual((second.outcome, second.executed), (ActionOutcome.ALREADY_DONE, False))
        self.assertEqual(self.transport.count("invoice"), 1)
        row = self.row()
        self.assertEqual((row.action_type, row.target_key, row.status), (ChannelActionType.INVOICE_UPLOAD, "order:999:box:111", ChannelActionStatus.SUCCEEDED))

    def test_concurrent_sends_issue_exactly_one_request(self):
        barrier = threading.Barrier(6)
        results = []

        def worker():
            db = sessionmaker(bind=self.engine, expire_on_commit=False)()
            actions = CoupangChannelActions(
                db, credential_store=self.creds, now_factory=lambda: self.clock[0],
                provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport),
            )
            barrier.wait()
            results.append(self.send(actions).outcome)
            db.close()

        threads = [threading.Thread(target=worker) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(self.transport.count("invoice"), 1)
        self.assertEqual(results.count(ActionOutcome.SUCCEEDED), 1)

    def test_a_rejection_blocks_blind_repetition_until_a_person_retries(self):
        self.transport.invoice = [response(200, fail_body("INVALID_STATUS"))]
        first = self.send()
        again = self.send(self.actions())
        self.assertEqual(first.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual((again.outcome, again.executed), (ActionOutcome.ACTION_REQUIRED, False))
        self.assertEqual(self.transport.count("invoice"), 1)
        # 사람이 원인(예: 상품준비중 처리)을 해결한 뒤 명시적으로 다시 실행하면 한 번 더 보낸다.
        self.transport.invoice = [response(200, ok_body())]
        retried = self.send(self.actions(), retry_action_required=True)
        self.assertEqual((retried.outcome, retried.executed), (ActionOutcome.SUCCEEDED, True))
        self.assertEqual(self.transport.count("invoice"), 2)

    def test_an_unknown_result_is_never_resent_and_is_settled_by_reading_the_order_sheet(self):
        self.transport.invoice = [requests.Timeout()]
        first = self.send()
        self.assertEqual(first.outcome, ActionOutcome.UNKNOWN)
        self.assertEqual(self.send(self.actions()).executed, False)
        self.assertEqual(self.transport.count("invoice"), 1)
        # 쿠팡 발주서에 같은 송장번호가 있으면 적용된 것으로 확정한다.
        self.transport.sheet = [response(200, sheet_body("DEPARTURE", invoice="259606480560"))]
        settled = self.actions().check_invoice_registered(
            company_id=1, store_connection_id=self.store.id, channel_order_id="999", shipment_box_id="111",
            invoice_number="259606480560")
        self.assertEqual(settled.outcome, ActionOutcome.SUCCEEDED)
        self.assertEqual(self.row().status, ChannelActionStatus.SUCCEEDED)
        self.assertEqual(self.transport.count("invoice"), 1)

    def test_an_invalid_request_leaves_no_ledger_row_and_makes_no_call(self):
        result = self.send(delivery_company_code="롯데택배")
        self.assertEqual(result.outcome, ActionOutcome.ACTION_REQUIRED)
        self.assertEqual(self.db.query(ChannelActionRequest).count(), 0)
        self.assertEqual(self.transport.calls, [])

    def test_invoice_and_cancel_for_the_same_box_do_not_block_each_other(self):
        self.send()
        self.db.add(ChannelActionRequest(
            company_id=1, store_connection_id=self.store.id, action_type=ChannelActionType.ORDER_CANCEL,
            target_key="order:999:box:111", status="SUCCEEDED", attempt_count=1, requested_at=self.clock[0]))
        self.db.commit()  # 유일 제약이 (회사, 동작, 대상)이라 동작이 다르면 충돌하지 않는다.
        self.assertEqual(self.db.query(ChannelActionRequest).count(), 2)

    def test_reading_the_order_sheet_writes_nothing(self):
        sheet, failure = self.actions().read_order_sheet(company_id=1, store_connection_id=self.store.id, shipment_box_id="111")
        self.assertIsNone(failure)
        self.assertTrue(sheet.ok)
        self.assertEqual(self.db.query(ChannelActionRequest).count(), 0)
        self.assertEqual(self.transport.calls, [("sheet", "GET")])


if __name__ == "__main__":
    unittest.main()
