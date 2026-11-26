"""
2026-11-26 — 쿠팡 등록 상품의 대표·추가 이미지·상세설명 사진 교체(사용자 "전체 이미지 변경 대표로 쓸 이미지도 고려해서 만들어라").

  - 본문은 상품 조회 응답 그대로에서 옵션마다 images·contents와 승인 요청 표시만 바뀐다. 그 밖이 달라지거나 옵션 번호가 없으면 막는다.
  - 사진 검사(이 회사·이 상품 후보·활성·용도·형식·크기·대표 정사각형 1000 이상·중복·장수)와 등록상품번호–상품 후보 연결을 본다.
  - 미리보기는 업로드하지 않는다. 보낼 때도 조건(확인·상태·허용·연결)이 안 맞으면 업로드 전에 멈춘다. 같은 요청은 두 번 보내지 않는다.
  - 격리 SQLite + 가짜 HTTP + 가짜 공개 저장소(외부 호출 없음).
"""

from __future__ import annotations

import copy
import json
import unittest

from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import PURPOSE_TARGETS
from app.domains.media_asset.model import MediaAsset
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_product_image_service import CoupangProductImageService
from app.domains.purchase_task.coupang_product_image_service import build_images_body
from app.domains.purchase_task.coupang_product_image_service import images_body_guard
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_product_name import FakeTransport as _NameTransport
from tests.test_coupang_product_name import PRODUCT
from tests.test_coupang_product_name import ok_put
from tests.test_coupang_product_name import product as name_product
from tests.test_coupang_stock import REQ
from tests.test_coupang_stock import ServiceBase as _StockBase

CANDIDATE = 3


def product(status="승인완료"):
    body = name_product(status=status)
    for item in body["data"]["items"]:
        item["images"] = [{"imageOrder": 0, "imageType": "REPRESENTATION", "cdnPath": "old/main.jpg"}]
        item["contents"] = [{"contentsType": "TEXT", "contentDetails": [{"detailType": "TEXT", "content": "<html>old</html>"}]}]
    return body


class FakeHosting:
    def __init__(self):
        self.uploaded = []

    def ensure_public_url(self, asset):
        self.uploaded.append(asset.id)
        return f"https://pub.example/{asset.id}.png"


class PureTests(unittest.TestCase):

    def test_only_images_contents_and_the_request_flag_change(self):
        data = product()["data"]
        before = copy.deepcopy(data)
        body = build_images_body(data, "https://u/m.png", ["https://u/a1.png", "https://u/a2.png"], ["https://u/c1.png"])
        self.assertEqual(data, before)
        self.assertIsNone(images_body_guard(data, body))
        for item in body["items"]:
            self.assertEqual([(i["imageOrder"], i["imageType"], i["vendorPath"]) for i in item["images"]],
                             [(0, "REPRESENTATION", "https://u/m.png"), (1, "DETAIL", "https://u/a1.png"), (2, "DETAIL", "https://u/a2.png")])
            self.assertEqual(item["contents"], [{"contentsType": "TEXT", "contentDetails": [
                {"detailType": "TEXT", "content": "<html><div><img src='https://u/c1.png' /></div></html>"}]}])
        self.assertIs(body["requested"], True)

    def test_the_guard_refuses_any_other_change(self):
        data = product()["data"]
        body = build_images_body(data, "m", [], ["c"])
        for change in (lambda b: b.__setitem__("displayProductName", "다른 이름"), lambda b: b["items"][0].__setitem__("salePrice", 1),
                       lambda b: b["items"].pop(), lambda b: b["items"][0].pop("sellerProductItemId")):
            broken = copy.deepcopy(body)
            change(broken)
            self.assertIsNotNone(images_body_guard(data, broken))

    def test_the_new_purpose_is_a_coupang_read_and_write(self):
        self.assertIn(CallPurpose.PRODUCT_IMAGE_UPDATE, CallPurpose.ENFORCED)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.PRODUCT_IMAGE_UPDATE], "쿠팡 상품 사진 수정")
        self.assertEqual(PURPOSE_TARGETS[CallPurpose.PRODUCT_IMAGE_UPDATE][0], ("COUPANG",))
        self.assertIn(ChannelActionType.PRODUCT_IMAGE_UPDATE, ChannelActionType.ALL)


class Base(_StockBase):

    def setUp(self):
        super().setUp()
        self.transport = _NameTransport()
        self.transport.get = [response(200, product()) for _ in range(3)]
        self.transport.put = [response(200, ok_put())]
        MediaAsset.__table__.create(bind=self.engine)
        self.db.execute(text("CREATE TABLE supplier_option_links (id INTEGER PRIMARY KEY, company_id INTEGER, channel_sku VARCHAR(100), "
                             "coupang_seller_product_id VARCHAR(30))"))
        self.db.execute(text("INSERT INTO supplier_option_links (company_id, channel_sku, coupang_seller_product_id) VALUES (1, :s, :p)"),
                        {"s": f"PC{CANDIDATE}-CH1-1", "p": PRODUCT})
        self.db.commit()
        self.main = self.asset("MAIN", 1000, 1000)
        self.extra = [self.asset("ADDITIONAL", 1200, 1200) for _ in range(2)]
        self.detail = self.asset("DETAIL", 780, 3754)
        self.hosting = FakeHosting()

    def asset(self, purpose, w, h, owner=CANDIDATE, status="ACTIVE", rights="VERIFIED", mime="image/png", company=1):
        row = MediaAsset(company_id=company, owner_type="PRODUCT_CANDIDATE", owner_id=owner, asset_role="GENERATED", purpose=purpose, display_order=0,
                         storage_path=f"media/{len(self.db.query(MediaAsset).all())}.png", mime_type=mime, file_size_bytes=10,
                         sha256_hex="0" * 64, width=w, height=h, status=status, rights_status=rights)
        self.db.add(row)
        self.db.commit()
        return row

    def service(self):
        stock = super().service()
        return CoupangProductImageService(self.db, actions_factory=stock._actions_factory, safety=self.safety, hosting_factory=lambda s: self.hosting)

    def args(self, **kw):
        base = dict(main_asset_id=self.main.id, additional_asset_ids=[a.id for a in self.extra], content_asset_ids=[self.detail.id])
        base.update(kw)
        return base

    def plan(self, **kw):
        request_ref = kw.pop("request_ref", REQ)
        return self.service().plan(1, PRODUCT, CANDIDATE, request_ref=request_ref, **self.args(**kw))

    def codes(self, plan):
        return {b.code for b in plan.blockers}


class PlanTests(Base):

    def test_a_ready_plan_reads_once_and_uploads_nothing(self):
        plan = self.plan()
        self.assertTrue(plan.sendable, plan.to_dict())
        self.assertEqual(plan.mode, "CHANGE_REQUEST")
        self.assertEqual(self.transport.calls, [("get", "GET")])
        self.assertEqual(self.hosting.uploaded, [])
        self.assertNotIn("data", json.dumps(plan.to_dict(), ensure_ascii=False).replace("data_", ""))

    def test_photo_rules_block_before_any_request(self):
        cases = [
            dict(main_asset_id=self.asset("MAIN", 900, 900).id),                          # 대표 1000 미만
            dict(main_asset_id=self.asset("MAIN", 1000, 1200).id),                        # 대표 정사각형 아님
            dict(main_asset_id=self.extra[0].id),                                          # 용도 틀림
            dict(additional_asset_ids=[self.asset("ADDITIONAL", 400, 400).id]),            # 너무 작음
            dict(additional_asset_ids=[self.asset("ADDITIONAL", 1200, 1200, owner=99).id]),   # 다른 상품
            dict(additional_asset_ids=[self.asset("ADDITIONAL", 1200, 1200, status="ARCHIVED").id]),
            dict(additional_asset_ids=[self.asset("ADDITIONAL", 1200, 1200, rights="RIGHTS_DENIED").id]),
            dict(additional_asset_ids=[self.asset("ADDITIONAL", 1200, 1200, mime="image/webp").id]),
            dict(additional_asset_ids=[self.asset("ADDITIONAL", 1200, 1200, company=2).id]),
            dict(additional_asset_ids=[self.extra[0].id, self.extra[0].id]),               # 중복
            dict(additional_asset_ids=[self.asset("ADDITIONAL", 1200, 1200).id for _ in range(10)]),   # 10장
            dict(content_asset_ids=[]),
            dict(main_asset_id="7"),
        ]
        for kw in cases:
            self.transport.calls.clear()
            plan = self.plan(**kw)
            self.assertFalse(plan.sendable, kw)
            self.assertTrue(plan.blockers, kw)
            self.assertEqual(self.transport.calls, [], kw)

    def test_a_product_number_not_linked_to_this_candidate_is_refused(self):
        plan = self.service().plan(1, "16411745286", CANDIDATE, request_ref=REQ, **self.args())
        self.assertIn("PRODUCT_MISMATCH", self.codes(plan))
        self.assertEqual(self.transport.calls, [])

    def test_status_and_gates(self):
        self.transport.get = [response(200, product(status="승인대기중"))]
        self.assertIn("NOT_EDITABLE_STATUS", self.codes(self.plan()))
        self.safety.mode = "SUGGEST_ONLY"
        self.safety.estop = True
        plan = self.plan()
        self.assertTrue(plan.gate_blockers)


class SendTests(Base):

    def send(self, **kw):
        confirm = kw.pop("confirm_review", True)
        return self.service().send(1, 1, PRODUCT, CANDIDATE, request_ref=REQ, confirm_review=confirm, **self.args(**kw))

    def test_a_confirmed_send_uploads_then_sends_once_with_only_photos_changed(self):
        result = self.send()
        self.assertEqual(result["outcome"], "SUCCEEDED", result)
        self.assertEqual(sorted(self.hosting.uploaded), sorted([self.main.id, self.extra[0].id, self.extra[1].id, self.detail.id]))
        methods = [c for c in self.transport.calls]
        self.assertEqual(methods, [("get", "GET"), ("put", "PUT"), ("get", "GET")])
        sent = [r[3] for r in self.transport.requests if r[1] == "PUT"]
        self.assertEqual(len(sent), 1)
        self.assertIsNone(images_body_guard(product()["data"], sent[0]))
        self.assertEqual(sent[0]["items"][0]["images"][0], {"imageOrder": 0, "imageType": "REPRESENTATION", "vendorPath": f"https://pub.example/{self.main.id}.png"})
        self.assertIn(f"https://pub.example/{self.detail.id}.png", sent[0]["items"][1]["contents"][0]["contentDetails"][0]["content"])

    def test_nothing_is_uploaded_without_the_review_confirmation_or_when_blocked(self):
        with self.assertRaises(BadRequestException):
            self.send(confirm_review=False)
        self.transport.get = [response(200, product(status="승인대기중"))]
        with self.assertRaises(BadRequestException):
            self.send()
        self.safety.estop = True
        with self.assertRaises(ForbiddenException):
            self.send()
        self.assertEqual(self.hosting.uploaded, [])
        self.assertNotIn(("put", "PUT"), self.transport.calls)

    def test_the_same_request_is_not_sent_twice(self):
        self.send()
        self.transport.get = [response(200, product()) for _ in range(3)]
        again = self.send()
        self.assertFalse(again["executed"])
        self.assertEqual(sum(1 for c in self.transport.calls if c == ("put", "PUT")), 1)


if __name__ == "__main__":
    unittest.main()
