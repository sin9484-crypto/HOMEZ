"""
=========================================================
Homez OS

File : tests/test_coupang_product_name.py

2026-10-25 — 쿠팡 쓰기 3번: 등록 상품의 상품명 수정 규칙을 고정한다. 공식 "상품 수정(승인필요)" `PUT .../seller-products`(본문 = 상품 조회 응답 전체에서
바꿀 값만 고친 것, 옵션마다 sellerProductItemId 필수, 승인 뒤 반영)과 상품 조회 `GET .../seller-products/{id}`를 따른다.
이름 두 칸·승인 요청 표시 말고는 아무것도 바꾸지 않고, 승인완료·승인반려 상품만, 사용자가 본 지금 이름과 같고 승인 뒤 반영을 확인했을 때만 한 번 보낸다.
격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import copy
import json
import unittest
from pathlib import Path

import requests
from pydantic import ValidationError
from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.external_call.constants import CallPurpose
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import CallKind
from app.domains.purchase_task.coupang_product_name_service import CHANGED_KEYS_ALLOWED
from app.domains.purchase_task.coupang_product_name_service import CoupangProductNameService
from app.domains.purchase_task.coupang_product_name_service import body_guard
from app.domains.purchase_task.coupang_product_name_service import build_name_body
from app.domains.purchase_task.coupang_product_name_service import clean_name
from app.domains.purchase_task.model import ChannelActionRequest
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_stock import REQ
from tests.test_coupang_stock import FakeTransport as _StockTransport
from tests.test_coupang_stock import ServiceBase as _StockBase
from tests.test_coupang_stock import provider

PRODUCT = "15000123456"
OLD = "레이펄스 워터리스 바디워시 3개입"
NEW = "레이펄스 물 없이 쓰는 바디워시 200ml 3개입"


def product(display=OLD, seller="바디워시 관리용", status="승인완료", items=None, pid=PRODUCT):
    return {
        "code": "SUCCESS", "message": "",
        "data": {
            "sellerProductId": int(pid), "statusName": status, "displayCategoryCode": 56137,
            "sellerProductName": seller, "displayProductName": display, "brand": "레이펄스", "vendorId": "A00012345",
            "requested": False, "deliveryChargeType": "FREE",
            "items": items if items is not None else [
                {"sellerProductItemId": 900001, "vendorItemId": 70012345678, "itemName": "1개", "salePrice": 15000,
                 "attributes": [{"attributeTypeName": "수량", "attributeValueName": "3개"}], "outboundShippingTimeDay": 2},
                {"sellerProductItemId": 900002, "vendorItemId": 70012345679, "itemName": "2개", "salePrice": 28000,
                 "attributes": [{"attributeTypeName": "수량", "attributeValueName": "6개"}], "outboundShippingTimeDay": 2},
            ],
            "notices": [{"noticeCategoryName": "화장품", "noticeCategoryDetailName": "용량", "content": "200ml"}],
        },
    }


def ok_put():
    return {"code": "200", "message": "", "data": {"code": "SUCCESS", "message": "", "data": int(PRODUCT)}}


class FakeTransport(_StockTransport):
    def __init__(self):
        super().__init__()
        self.get = [response(200, product())]
        self.put = [response(200, ok_put())]


class PureTests(unittest.TestCase):

    def test_names_are_trimmed_but_never_rewritten(self):
        self.assertEqual(clean_name("  새 이름  "), "새 이름")
        self.assertEqual(clean_name("가" * 100), "가" * 100)
        for bad in ("", "   ", "가" * 101, "줄\n바꿈", "탭\t문자", "\x7f", None, 3, b"x"):
            self.assertIsNone(clean_name(bad), repr(bad))

    def test_the_body_is_the_read_product_with_only_the_names_changed(self):
        data = product()["data"]
        before = copy.deepcopy(data)
        body = build_name_body(data, NEW, None)
        self.assertEqual(data, before)                                          # 원본은 그대로
        self.assertEqual((body["displayProductName"], body["sellerProductName"], body["requested"]), (NEW, "바디워시 관리용", True))
        self.assertEqual({k for k in body if body[k] != data.get(k)}, {"displayProductName", "requested"})
        self.assertEqual(body["items"], data["items"])
        self.assertEqual(build_name_body(data, NEW, "관리명2")["sellerProductName"], "관리명2")
        self.assertIsNone(body_guard(data, body))
        body["items"][0]["salePrice"] = 1                                      # 본문을 바꿔도 읽은 원본은 그대로(깊은 복사)
        body["notices"].append({"x": 1})
        self.assertEqual(data, before)
        self.assertEqual(body_guard(data, body), "BODY_CHANGED_OTHER_FIELDS")    # 옵션 값이 달라진 본문은 막힌다

    def test_the_guard_refuses_any_other_change(self):
        data = product()["data"]
        body = build_name_body(data, NEW, None)
        for mutate in (lambda b: b.__setitem__("brand", "다른"), lambda b: b.pop("notices"), lambda b: b.__setitem__("extra", 1),
                       lambda b: b["items"][0].__setitem__("salePrice", 1), lambda b: b["items"].pop()):
            bad = copy.deepcopy(body)
            mutate(bad)
            self.assertEqual(body_guard(data, bad), "BODY_CHANGED_OTHER_FIELDS")
        self.assertEqual(CHANGED_KEYS_ALLOWED, frozenset({"displayProductName", "sellerProductName", "requested"}))

    def test_the_guard_refuses_options_without_an_option_number(self):
        for items in ([], [{"vendorItemId": 1}], [{"sellerProductItemId": None}], [{"sellerProductItemId": "x"}]):
            data = product(items=items)["data"]
            self.assertIn(body_guard(data, build_name_body(data, NEW, None)), ("BODY_NO_OPTIONS", "BODY_OPTION_ID_MISSING"), items)
        data = product()["data"]
        data.pop("items")
        self.assertEqual(body_guard(data, build_name_body(data, NEW, None)), "BODY_NO_OPTIONS")


class ProviderTests(unittest.TestCase):

    def test_reading_returns_the_whole_product(self):
        t = FakeTransport()
        snap = provider(t).read_product_full(PRODUCT)
        self.assertTrue(snap.ok)
        self.assertEqual((snap.status_name, snap.display_name, snap.seller_name), ("승인완료", OLD, "바디워시 관리용"))
        self.assertEqual(snap.data, product()["data"])
        self.assertTrue(t.requests[0][2].endswith(f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{PRODUCT}"))

    def test_an_unreadable_product_is_a_failure(self):
        for outcome in (response(500, {}), response(400, {"message": "없음"}), response(200, {"code": "ERROR", "data": product()["data"]}),
                        response(200, {"code": "SUCCESS", "data": None}), response(200, product(pid="15000999999")),
                        response(200, product(items=[])), response(200, product(items=["x"])), requests.Timeout()):
            t = FakeTransport()
            t.get = [outcome]
            snap = provider(t).read_product_full(PRODUCT)
            self.assertFalse(snap.ok, outcome)
            self.assertIsNone(snap.data, outcome)
        self.assertFalse(provider(FakeTransport()).read_product_full("abc").ok)

    def test_the_update_sends_the_body_to_the_official_path(self):
        t = FakeTransport()
        body = build_name_body(product()["data"], NEW, None)
        self.assertEqual(provider(t).update_product(PRODUCT, body).kind, CallKind.SUCCEEDED)
        kind, method, url, sent = t.requests[0]
        self.assertEqual((kind, method), ("put", "PUT"))
        self.assertTrue(url.endswith("/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"), url)
        self.assertEqual(sent, body)

    def test_a_body_without_option_numbers_or_for_another_product_is_never_sent(self):
        t = FakeTransport()
        p = provider(t)
        good = build_name_body(product()["data"], NEW, None)
        no_ids = build_name_body(product(items=[{"vendorItemId": 1}])["data"], NEW, None)
        for pid, body in (("abc", good), (PRODUCT, no_ids), ("15000999999", good), (PRODUCT, None), (PRODUCT, [])):
            self.assertEqual(p.update_product(pid, body).kind, CallKind.ACTION_REQUIRED, (pid, body))
        self.assertEqual(t.calls, [])
        self.assertEqual(p.validate_product_update(PRODUCT, no_ids).error_class, "INVALID_BODY")

    def test_the_response_judgement(self):
        cases = [
            (response(200, ok_put()), CallKind.SUCCEEDED),
            (response(200, {"code": "SUCCESS", "data": {"code": "SUCCESS", "data": 1}}), CallKind.SUCCEEDED),
            (response(200, {"code": "200", "data": {"code": "ERROR", "message": "필수 속성 누락"}}), CallKind.ACTION_REQUIRED),
            (response(200, {"code": "ERROR", "message": "x"}), CallKind.ACTION_REQUIRED),
            (response(200, {"code": "200", "data": 1}), CallKind.UNKNOWN),                  # 안쪽 성공 표시가 없으면 단정하지 않는다
            (response(200, {"code": "200"}), CallKind.UNKNOWN),
            (response(400, {"message": "Mandatory attribute(s) of the category is missing."}), CallKind.ACTION_REQUIRED),
            (response(429, {}), CallKind.RETRYABLE),
            (response(500, {}), CallKind.UNKNOWN),
            (requests.Timeout(), CallKind.UNKNOWN),
        ]
        body = build_name_body(product()["data"], NEW, None)
        for outcome, expected in cases:
            t = FakeTransport()
            t.put = [outcome]
            self.assertEqual(provider(t).update_product(PRODUCT, body).kind, expected, outcome)

    def test_the_applied_check_never_reads_an_unchanged_name_as_not_sent(self):
        t = FakeTransport()
        t.get = [response(200, product(display=NEW))]
        result = provider(t).read_product_name_applied(PRODUCT, NEW)
        self.assertEqual((result.ok, result.value), (True, True))
        t.get = [response(200, product(display=OLD, status="승인대기"))]
        result = provider(t).read_product_name_applied(PRODUCT, NEW)
        self.assertEqual((result.ok, result.value), (False, None))                 # 승인 대기일 수 있다 — 판단하지 않는다
        t.get = [response(500, {})]
        self.assertEqual(provider(t).read_product_name_applied(PRODUCT, NEW).ok, False)


class ServiceBase(_StockBase):

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()

    def service(self):
        stock = super().service()
        return CoupangProductNameService(self.db, actions_factory=stock._actions_factory, safety=self.safety)

    def plan(self, display=NEW, **kw):
        kw.setdefault("request_ref", REQ)
        return self.service().plan(1, PRODUCT, display, **kw)


class PlanTests(ServiceBase):

    def test_a_ready_product_shows_the_current_names_and_status(self):
        plan = self.plan()
        self.assertTrue(plan.sendable, plan.to_dict())
        d = plan.to_dict()
        self.assertEqual(d["coupang"], {"status_name": "승인완료", "display_name": OLD, "seller_name": "바디워시 관리용", "option_count": 2})
        self.assertEqual(d["mode"], "CHANGE_REQUEST")
        self.assertNotIn("body", d)                                             # 상품 본문은 화면에 보내지 않는다
        self.assertEqual(self.transport.calls, [("get", "GET")])
        self.assertEqual(self.rows(), [])

    def test_a_rejected_product_can_be_fixed_and_resubmitted(self):
        self.transport.get = [response(200, product(status="승인반려"))]
        plan = self.plan()
        self.assertTrue(plan.sendable)
        self.assertEqual(plan.mode, "RESUBMIT")

    def test_other_statuses_are_refused(self):
        for status in ("임시저장", "승인대기", "SAVED", "IN_REVIEW", "APPROVING", "DELETED", "", "모름"):
            self.transport.get = [response(200, product(status=status))]
            self.assertIn("NOT_EDITABLE_STATUS", self.codes(self.plan()), status)

    def test_partial_approval_is_not_assumed_editable(self):
        self.transport.get = [response(200, product(status="PARTIAL_APPROVED"))]
        self.assertIn("NOT_EDITABLE_STATUS", self.codes(self.plan()))

    def test_local_only_preview_makes_no_request(self):
        self.assertFalse(self.plan(live=False).sendable)
        self.assertEqual(self.transport.calls, [])

    def test_invalid_input_blocks_before_any_request(self):
        self.assertIn("INVALID_DISPLAY_NAME", self.codes(self.plan(display="")))
        self.assertIn("INVALID_DISPLAY_NAME", self.codes(self.plan(display="가" * 101)))
        self.assertIn("INVALID_DISPLAY_NAME", self.codes(self.plan(display="줄\n바꿈")))
        self.assertIn("INVALID_DISPLAY_NAME", self.codes(self.plan(display=None)))
        self.assertIn("INVALID_SELLER_NAME", self.codes(self.plan(seller_name="가" * 101)))
        self.assertIn("INVALID_SELLER_NAME", self.codes(self.plan(seller_name="탭\t문자")))
        self.assertIn("INVALID_SELLER_NAME", self.codes(self.plan(seller_name=5)))
        self.assertIn("INVALID_REQUEST_REF", self.codes(self.plan(request_ref="short")))
        for pid in ("abc", "1" * 21, ""):
            self.assertIn("INVALID_SELLER_PRODUCT_ID", self.codes(self.service().plan(1, pid, NEW)))
        self.assertEqual(self.transport.calls, [])

    def test_an_empty_seller_name_means_keep_it(self):
        for empty in (None, "", "   "):
            plan = self.plan(seller_name=empty)
            self.assertTrue(plan.sendable, empty)
            self.assertIsNone(plan.seller_name)
            self.assertEqual(plan.body["sellerProductName"], "바디워시 관리용")

    def test_the_same_names_are_reported_not_resent(self):
        self.transport.get = [response(200, product(display=NEW))]
        self.assertTrue(self.plan().already_applied)
        self.transport.get = [response(200, product(display=NEW))]
        self.assertFalse(self.plan(seller_name="다른 관리명").already_applied)  # 등록상품명이 다르면 바꿀 것이 있다
        self.transport.get = [response(200, product(display=NEW))]
        self.assertTrue(self.plan(seller_name="바디워시 관리용").already_applied)

    def test_a_failed_read_or_options_without_numbers_block(self):
        self.transport.get = [response(500, {})]
        self.assertIn("PRODUCT_READ_FAILED", self.codes(self.plan()))
        self.transport.get = [response(200, product(items=[{"vendorItemId": 1, "itemName": "x"}]))]
        plan = self.plan()
        self.assertIn("BODY_OPTION_ID_MISSING", self.codes(plan))
        self.assertIsNone(plan.body)

    def test_store_resolution_never_guesses(self):
        from app.domains.store_connection.model import StoreConnection

        self.db.query(StoreConnection).delete()
        self.db.commit()
        self.assertIn("NO_COUPANG_STORE", self.codes(self.plan()))
        self.add_store("a", "k1")
        self.add_store("b", "k2")
        self.assertIn("MULTIPLE_COUPANG_STORES", self.codes(self.plan()))
        self.assertEqual(self.transport.calls, [])

    def test_gate_reasons_are_shown_without_hiding_the_data_checks(self):
        self.safety.mode, self.safety.estop = AutomationMode.RECOMMEND_ONLY, True
        plan = self.plan()
        self.assertEqual({b.code for b in plan.gate_blockers}, {"ESTOP_ACTIVE", "OPERATOR_APPROVAL_MODE_REQUIRED"})
        self.assertFalse(plan.sendable)
        self.assertTrue(plan.live_checked)

    def test_the_plan_never_contains_credentials(self):
        from tests.test_coupang_stock import REF

        body = json.dumps(self.plan().to_dict(), ensure_ascii=False)
        for secret in ("AK", "SK", "secret", "access_key", REF, "A00012345"):
            self.assertNotIn(secret, body)


class SendTests(ServiceBase):

    def send(self, display=NEW, **override):
        args = dict(expected_display_name=OLD, request_ref=REQ, confirm_review=True)
        args.update(override)
        return self.service().send(1, 7, PRODUCT, display, **args)

    def sent_bodies(self):
        return [r[3] for r in self.transport.requests if r[0] == "put"]

    def test_a_confirmed_send_goes_out_once_with_only_the_names_changed(self):
        self.transport.get = [response(200, product()), response(200, product(display=NEW))]
        result = self.send(seller_name="새 관리명")
        self.assertEqual((result["outcome"], result["executed"], result["verified"]), ("SUCCEEDED", True, True))
        self.assertEqual(result["after"], {"display_name": NEW, "status_name": "승인완료"})
        bodies = self.sent_bodies()
        self.assertEqual(len(bodies), 1)
        original = product()["data"]
        self.assertEqual({k for k in bodies[0] if bodies[0][k] != original.get(k)}, {"displayProductName", "sellerProductName", "requested"})
        self.assertEqual((bodies[0]["displayProductName"], bodies[0]["sellerProductName"], bodies[0]["requested"]), (NEW, "새 관리명", True))
        row = self.rows()[0]
        self.assertEqual((row.action_type, row.status, row.triggered_by), ("PRODUCT_NAME_UPDATE", ChannelActionStatus.SUCCEEDED, 7))
        self.assertTrue(row.target_key.startswith(f"sp:{PRODUCT}:name:"))
        self.assertTrue(row.target_key.endswith(f":req:{REQ}"))

    def test_an_unchanged_name_after_success_is_reported_as_waiting_not_failed(self):
        self.transport.get = [response(200, product()), response(200, product(status="승인대기"))]
        result = self.send()
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", None))
        self.assertEqual(result["after"], {"display_name": OLD, "status_name": "승인대기"})

    def test_a_failed_verification_read_is_reported_as_unknown(self):
        self.transport.get = [response(200, product()), response(500, {})]
        result = self.send()
        self.assertEqual((result["verified"], result["after"]), (None, None))

    def test_a_rejection_is_not_reread_or_repeated_without_a_person(self):
        self.transport.put = [response(200, {"code": "200", "data": {"code": "ERROR", "message": "필수 속성 누락"}})]
        first = self.send()
        self.assertEqual((first["outcome"], first["verified"], first["after"]), ("ACTION_REQUIRED", None, None))
        self.assertEqual(self.transport.count("get"), 1)
        second = self.send()
        self.assertEqual((second["outcome"], second["executed"]), ("ACTION_REQUIRED", False))
        self.assertEqual(self.transport.count("put"), 1)
        self.transport.put = [response(200, ok_put())]
        self.transport.get = [response(200, product()), response(200, product(display=NEW))]
        third = self.send(retry_action_required=True)
        self.assertEqual((third["outcome"], third["executed"]), ("SUCCEEDED", True))

    def test_the_same_request_cannot_be_sent_twice(self):
        self.transport.get = [response(200, product()), response(200, product(status="승인대기"))]
        self.send()
        self.transport.get = [response(200, product(status="승인대기"))]
        with self.assertRaises(BadRequestException):                           # 승인대기 상품은 다시 보내지 않는다
            self.send()
        self.transport.get = [response(200, product())]
        again = self.send()
        self.assertEqual((again["outcome"], again["executed"]), ("ALREADY_DONE", False))
        self.assertEqual(self.transport.count("put"), 1)

    def test_a_different_current_name_than_the_user_saw_refuses_to_send(self):
        for expected in ("다른 이름", OLD + " ", None, 1):
            with self.assertRaises(ConflictException):
                self.send(expected_display_name=expected)
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))

    def test_the_review_must_be_confirmed(self):
        for confirm in (False, "yes", 1, None):
            with self.assertRaises(BadRequestException):
                self.send(confirm_review=confirm)
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))

    def test_a_non_editable_status_is_never_sent(self):
        self.transport.get = [response(200, product(status="임시저장"))]
        with self.assertRaises(BadRequestException) as caught:
            self.send()
        self.assertIn("NOT_EDITABLE_STATUS", str(getattr(caught.exception, "detail", caught.exception)))
        self.assertEqual(self.transport.count("put"), 0)

    def test_a_bad_request_ref_is_refused_before_reading(self):
        for ref in ("", "short", "has space 123", "x" * 41, None):
            with self.assertRaises(BadRequestException):
                self.send(request_ref=ref)
        self.assertEqual(self.transport.calls, [])

    def test_mode_and_emergency_stop_forbid_sending(self):
        self.safety.mode = AutomationMode.RECOMMEND_ONLY
        with self.assertRaises(ForbiddenException):
            self.send()
        self.safety.mode, self.safety.estop = AutomationMode.OPERATOR_APPROVAL, True
        with self.assertRaises(ForbiddenException):
            self.send()
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))

    def test_the_hook_runs_after_all_checks_and_a_failing_hook_sends_nothing(self):
        order = []
        original = self.transport.request

        def tracking(method, url, **kw):
            order.append("put" if method == "PUT" else "get")
            return original(method, url, **kw)

        self.transport.request = tracking
        self.transport.get = [response(200, product()), response(200, product(display=NEW))]
        self.send(before_send=lambda: order.append("hook"))
        self.assertLess(order.index("get"), order.index("hook"))
        self.assertLess(order.index("hook"), order.index("put"))

        other = FakeTransport()
        self.transport = other
        self.db.query(ChannelActionRequest).delete()
        self.db.commit()

        def deny():
            raise PermissionError("no")

        with self.assertRaises(PermissionError):
            self.send(request_ref="req-20261025-0009", before_send=deny)
        self.assertEqual((other.count("put"), self.rows()), (0, []))

    def test_an_unknown_outcome_is_settled_only_by_reading_and_never_resent(self):
        self.transport.put = [requests.Timeout()]
        unknown = self.send()
        self.assertEqual((unknown["outcome"], unknown["verified"]), ("UNKNOWN", None))
        self.assertEqual(self.transport.count("get"), 1)
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(self.transport.count("put"), 1)
        self.transport.get = [response(200, product(display=OLD, status="승인대기"))]
        self.assertEqual(self.service().reconcile(1, PRODUCT, NEW, REQ)["outcome"], "UNKNOWN")     # 이름이 그대로면 단정하지 않는다
        self.transport.get = [response(200, product(display=NEW))]
        self.assertEqual(self.service().reconcile(1, PRODUCT, NEW, REQ)["outcome"], "SUCCEEDED")
        self.assertEqual(self.transport.count("put"), 1)
        for args in ((PRODUCT, NEW, "bad"), ("abc", NEW, REQ), (PRODUCT, "", REQ), (PRODUCT, "가" * 101, REQ)):
            with self.assertRaises(BadRequestException):
                self.service().reconcile(1, *args)

    def test_an_audit_record_is_written_for_a_send(self):
        self.transport.get = [response(200, product()), response(200, product(display=NEW))]
        self.send()
        rows = self.db.execute(text("SELECT action, entity, entity_id, user_id, description FROM audit_logs")).fetchall()
        self.assertEqual([tuple(r)[:4] for r in rows], [("COUPANG_PRODUCT_NAME_UPDATE", "coupang_seller_product", PRODUCT, 7)])
        self.assertIn(f"{OLD} → {NEW}", rows[0][4])
        self.assertIn("CHANGE_REQUEST", rows[0][4])


class AllowanceTests(ServiceBase):

    def setUp(self):
        super().setUp()
        from tests.test_external_call_enforcement import _install

        _install(self.db)

    def grant(self, kind, count, purpose=None):
        from tests.test_external_call_enforcement import _grant

        return _grant(self.db, kind=kind, purpose=purpose or CallPurpose.PRODUCT_NAME_UPDATE, count=count)

    def test_without_an_allowance_nothing_goes_to_coupang(self):
        from app.domains.external_call.constants import AllowanceStatus
        from app.domains.external_call.model import ExternalCallAllowance

        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.plan()))
        self.assertEqual(self.transport.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).all()
        self.assertEqual([(a.kind, a.purpose) for a in pending], [("READ", "PRODUCT_NAME_UPDATE")])

    def test_other_allowances_do_not_open_name_changes(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        for purpose in (CallPurpose.PRICE_UPDATE, CallPurpose.PRODUCT_OUTBOUND_DAYS):
            self.grant(ExtKind.READ, 3, purpose=purpose)
            self.grant(ExtKind.WRITE, 1, purpose=purpose)
        self.assertIn("EXTERNAL_CALL_NOT_ALLOWED", self.codes(self.plan()))
        self.assertEqual(self.transport.calls, [])

    def test_exact_allowances_cover_one_whole_change(self):
        from app.domains.external_call.constants import AllowanceStatus
        from app.domains.external_call.constants import CallKind as ExtKind
        from app.domains.external_call.model import ExternalCallAllowance

        self.grant(ExtKind.READ, 3)
        self.grant(ExtKind.WRITE, 1)
        self.transport.get = [response(200, product()), response(200, product()), response(200, product(display=NEW))]
        self.assertTrue(self.plan().sendable)
        result = self.service().send(1, 7, PRODUCT, NEW, expected_display_name=OLD, request_ref=REQ, confirm_review=True)
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual((self.transport.count("get"), self.transport.count("put")), (3, 1))
        self.assertEqual(self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.EXHAUSTED).count(), 2)

    def test_a_missing_write_allowance_stops_before_the_put(self):
        from app.domains.external_call.constants import CallKind as ExtKind

        self.grant(ExtKind.READ, 3)
        result = self.service().send(1, 7, PRODUCT, NEW, expected_display_name=OLD, request_ref=REQ, confirm_review=True)
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.assertIn("EXTERNAL_ALLOWANCE_REQUIRED", result["detail"])
        self.assertEqual((self.transport.count("put"), self.rows()), (0, []))


class WiringTests(unittest.TestCase):

    ROOT = Path(__file__).resolve().parents[1]

    def test_routes_are_admin_only_and_the_preview_never_sends(self):
        router = (self.ROOT / "app/domains/purchase_task/router.py").read_text(encoding="utf-8")
        for name in ("preview_coupang_product_name", "send_coupang_product_name", "reconcile_coupang_product_name"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)
        preview = router.split("def preview_coupang_product_name(")[1].split("@router.post")[0]
        self.assertNotIn(".send(", preview)
        self.assertLess(router.index('"/coupang-products/{seller_product_id}/name/preview"'), router.index('"/{task_id}/coupang-invoice/preview"'))

    def test_the_request_schema_is_strict(self):
        from app.domains.purchase_task.coupang_product_name_schema import CoupangProductNameSendRequest

        good = dict(display_name=NEW, expected_display_name=OLD, request_ref=REQ, confirm_review=True)
        CoupangProductNameSendRequest(**good)
        CoupangProductNameSendRequest(**{**good, "seller_name": "관리명"})
        for bad in ({"extra": 1}, {"display_name": ""}, {"display_name": "가" * 101}, {"display_name": 5}, {"seller_name": "가" * 101},
                    {"expected_display_name": 1}, {"request_ref": "short"}, {"confirm_review": "yes"}, {"retry_action_required": 1}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                CoupangProductNameSendRequest(**{**good, **bad})

    def test_the_action_type_and_allowance_purpose_are_registered(self):
        from app.domains.external_call.constants import AllowanceMode
        from app.domains.external_call.constants import CallKind as ExtKind
        from app.domains.external_call.service import _DEFAULT_REQUEST

        self.assertIn(ChannelActionType.PRODUCT_NAME_UPDATE, ChannelActionType.ALL)
        self.assertLessEqual(len(ChannelActionType.PRODUCT_NAME_UPDATE), 30)
        self.assertIn(CallPurpose.PRODUCT_NAME_UPDATE, CallPurpose.ALL)
        self.assertIn(CallPurpose.PRODUCT_NAME_UPDATE, CallPurpose.ENFORCED)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.PRODUCT_NAME_UPDATE], "쿠팡 상품명 수정")
        self.assertEqual(CoupangChannelActions._EXTERNAL_PURPOSE[ChannelActionType.PRODUCT_NAME_UPDATE], (CallPurpose.PRODUCT_NAME_UPDATE, "PUT"))
        self.assertEqual(_DEFAULT_REQUEST[(CallPurpose.PRODUCT_NAME_UPDATE, ExtKind.READ)], (AllowanceMode.COUNTED, 3, None))
        self.assertEqual(_DEFAULT_REQUEST[(CallPurpose.PRODUCT_NAME_UPDATE, ExtKind.WRITE)], (AllowanceMode.COUNTED, 1, None))
        key = CoupangChannelActions.product_name_key("1" * 20, "가" * 100, "x" * 40)
        self.assertLessEqual(len(key), 200)
        self.assertNotIn("가", key)                                              # 이름 원문은 키에 넣지 않는다
        self.assertNotEqual(CoupangChannelActions.product_name_key(PRODUCT, "A", REQ), CoupangChannelActions.product_name_key(PRODUCT, "B", REQ))


if __name__ == "__main__":
    unittest.main()
