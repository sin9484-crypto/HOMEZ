"""
=========================================================
Homez OS

File : tests/test_coupang_seller_wing.py

2026-10-27 — 쿠팡 답변·확인의 WING 아이디를 판매자 고정값(`user_settings.listing_defaults.wing_user_id`)에서 찾는 규칙을 고정한다.
자격증명 값이 먼저이고, 고정값은 그 판매 연결의 회사에서 하나로 정해질 때만 쓴다(형식이 틀리거나 사용자마다 다르면 보내지 않는다).
격리 SQLite + 가짜 HTTP(외부 호출 없음).
=========================================================
"""

import json
import unittest
from datetime import datetime

from app.database.base import Base
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_call_center_confirm_service import CoupangCallCenterConfirmService
from app.domains.purchase_task.coupang_call_center_reply_service import CoupangCallCenterReplyService
from app.domains.purchase_task.coupang_channel_control_provider import CoupangChannelControlProvider
from app.domains.purchase_task.coupang_inquiry_reply_service import CoupangInquiryReplyService
from app.domains.purchase_task.coupang_seller_wing import actions_with_seller_wing
from app.domains.purchase_task.coupang_seller_wing import seller_wing_user_id
from app.domains.user_settings.model import UserSetting
from tests.test_coupang_call_center_confirm import ServiceBase as _ConfirmBase
from tests.test_coupang_call_center_confirm import transfers
from tests.test_coupang_call_center_reply import INQ
from tests.test_coupang_inquiry_reply import WING
from tests.test_coupang_invoice_upload import response
from tests.test_coupang_stock import REF
from tests.test_coupang_stock import VENDOR

SAVED = "sin_wing"


class Base_(_ConfirmBase):

    def setUp(self):
        super().setUp()
        Base.metadata.create_all(bind=self.engine, tables=[UserSetting.__table__])
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR})   # 실제와 같이 WING 아이디 없음

    def save_defaults(self, user_id, value, company=1):
        raw = value if isinstance(value, str) and value.startswith("{") else json.dumps(value)
        self.db.add(UserSetting(company_id=company, user_id=user_id, setting_key="listing_defaults", setting_value=raw))
        self.db.commit()

    def service(self):
        def factory(session):
            return CoupangChannelActions(
                session, credential_store=self.creds, now_factory=lambda: datetime(2026, 10, 27, 10, 0, 0),
                provider_factory=lambda cred: CoupangChannelControlProvider(cred, transport=self.transport),
                wing_user_id=lambda store_id: seller_wing_user_id(session, store_id),
            )
        return CoupangCallCenterConfirmService(self.db, actions_factory=factory, safety=self.safety)

    def sent_body(self):
        posts = [r for r in self.transport.requests if r[0] == "post"]
        self.assertEqual(len(posts), 1, self.transport.requests)
        return posts[0][3]


class LookupTests(Base_):

    def test_the_one_saved_value_of_the_store_company_is_used(self):
        self.save_defaults(1, {"wing_user_id": f"  {SAVED} ", "delivery_company_code": "CJGLS"})
        self.assertEqual(seller_wing_user_id(self.db, self.store.id), SAVED)

    def test_same_value_from_several_users_is_one_value(self):
        self.save_defaults(1, {"wing_user_id": SAVED})
        self.save_defaults(2, {"wing_user_id": SAVED})
        self.assertEqual(seller_wing_user_id(self.db, self.store.id), SAVED)

    def test_different_values_are_never_picked_from(self):
        self.save_defaults(1, {"wing_user_id": SAVED})
        self.save_defaults(2, {"wing_user_id": "other_wing"})
        self.assertIsNone(seller_wing_user_id(self.db, self.store.id))

    def test_other_companies_and_other_settings_are_ignored(self):
        self.save_defaults(1, {"wing_user_id": SAVED}, company=2)
        self.assertIsNone(seller_wing_user_id(self.db, self.store.id))
        self.db.add(UserSetting(company_id=1, user_id=3, setting_key="ui_preferences", setting_value=json.dumps({"wing_user_id": SAVED})))
        self.db.commit()
        self.assertIsNone(seller_wing_user_id(self.db, self.store.id))

    def test_malformed_values_are_not_used(self):
        for user_id, value in enumerate(([SAVED], {"wing_user_id": "a b"}, {"wing_user_id": "ab"}, {"wing_user_id": 12345},
                                         {"wing_user_id": "x" * 41}, {"wing_user_id": "한글아이디"}, "{not json"), start=10):
            self.save_defaults(user_id, value)
        self.assertIsNone(seller_wing_user_id(self.db, self.store.id))
        self.save_defaults(99, {"wing_user_id": SAVED})
        self.assertEqual(seller_wing_user_id(self.db, self.store.id), SAVED)   # 형식이 틀린 값은 '다른 값'으로도 세지 않는다

    def test_unknown_or_non_coupang_stores_have_no_value(self):
        self.save_defaults(1, {"wing_user_id": SAVED})
        other = self.add_store("네이버", "n1", marketplace="NAVER_SMARTSTORE")
        self.assertIsNone(seller_wing_user_id(self.db, other.id))
        self.assertIsNone(seller_wing_user_id(self.db, 9999))


class SendTests(Base_):

    def test_the_saved_value_fills_in_when_the_credential_has_none(self):
        self.save_defaults(1, {"wing_user_id": SAVED})
        self.transport.get = [response(200, transfers(int(INQ))), response(200, transfers())]
        result = self.send()
        self.assertEqual((result["outcome"], result["verified"]), ("SUCCEEDED", True))
        self.assertEqual(self.sent_body(), {"confirmBy": SAVED})
        self.assertNotIn("wing_user_id", self.creds.read(REF))                    # 자격증명에 쓰지 않는다(메모리에서만 합친다)

    def test_the_credential_value_wins(self):
        self.creds.save(REF, {"access_key": "AK", "secret_key": "SK", "vendor_id": VENDOR, "wing_user_id": WING})
        self.save_defaults(1, {"wing_user_id": SAVED})
        self.transport.get = [response(200, transfers(int(INQ))), response(200, transfers())]
        self.send()
        self.assertEqual(self.sent_body(), {"confirmBy": WING})

    def test_no_or_ambiguous_value_sends_nothing(self):
        self.transport.get = [response(200, transfers(int(INQ)))]
        result = self.send()
        self.assertEqual((result["outcome"], result["executed"]), ("ACTION_REQUIRED", False))
        self.save_defaults(1, {"wing_user_id": SAVED})
        self.save_defaults(2, {"wing_user_id": "other_wing"})
        self.transport.get = [response(200, transfers(int(INQ)))]
        self.assertEqual(self.send(retry_action_required=True)["outcome"], "ACTION_REQUIRED")
        self.assertEqual(self.transport.count("post"), 0)


class WiringTests(Base_):

    def test_the_reply_and_confirm_services_use_the_saved_value_by_default(self):
        for cls in (CoupangInquiryReplyService, CoupangCallCenterReplyService, CoupangCallCenterConfirmService):
            self.assertIs(cls(self.db, safety=self.safety)._actions_factory, actions_with_seller_wing, cls.__name__)
        self.save_defaults(1, {"wing_user_id": SAVED})
        actions = actions_with_seller_wing(self.db)
        self.assertEqual(actions._wing_user_id(self.store.id), SAVED)


if __name__ == "__main__":
    unittest.main()
