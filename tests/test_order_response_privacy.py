import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.core.exceptions import UnauthorizedException
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.domains.order.router import get_order_sensitive_detail
from app.domains.order.router import list_order_ingestion_events
from app.domains.order.schema import OrderResponse


NOW = datetime(2026, 8, 31, 12, 0, 0)


def _order():
    return SimpleNamespace(
        id=7, company_id=3, channel_code="COUPANG",
        channel_order_id="ORDER-1", order_number="H-1", status="PAID",
        buyer_name="홍길동", receiver_name="김철수",
        receiver_phone="010-1234-5678",
        receiver_address="서울특별시 강남구 테헤란로 1",
        receiver_zipcode="06236", total_amount=10000.0, ordered_at=NOW,
        channel_sync_status="SYNCED", channel_last_synced_at=None,
        channel_last_sync_error=None, created_at=NOW, updated_at=NOW,
    )


class OrderResponsePrivacyTestCase(unittest.TestCase):
    def setUp(self):
        reset_recent_auth_state_for_tests()
        self.addCleanup(reset_recent_auth_state_for_tests)
        self.user = SimpleNamespace(id=11, company_id=3)

    def test_general_order_response_masks_all_delivery_identity_fields(self):
        response = OrderResponse.model_validate(_order())
        self.assertEqual(response.buyer_name, "홍*동")
        self.assertEqual(response.receiver_name, "김*수")
        self.assertEqual(response.receiver_phone, "*******5678")
        self.assertTrue(response.receiver_address.startswith("서울특별시 "))
        self.assertNotIn("테헤란로", response.receiver_address)
        self.assertEqual(response.receiver_zipcode, "*****")
        revalidated = OrderResponse.model_validate(response.model_dump())
        self.assertEqual(revalidated.receiver_phone, "*******5678")

    def test_ingestion_event_response_never_contains_raw_payload(self):
        event = SimpleNamespace(
            id=1, company_id=3, channel_code="COUPANG",
            channel_order_id="ORDER-1", status="COLLECTED", order_id=7,
            raw_payload='{"receiver_phone":"010-1234-5678"}',
            normalized_snapshot='{"safe":true}', error_code=None,
            error_summary=None, created_at=NOW,
        )
        with patch(
            "app.domains.order.router.OrderService.list_ingestion_events",
            return_value=[event],
        ):
            responses = list_order_ingestion_events(
                7, current_user=self.user, db=object(),
            )
        dumped = responses[0].model_dump()
        self.assertNotIn("raw_payload", dumped)
        self.assertNotIn("normalized_snapshot", dumped)
        self.assertTrue(dumped["has_raw_payload"])
        self.assertTrue(dumped["has_normalized_snapshot"])

    def test_sensitive_detail_requires_recent_auth(self):
        # 2026-09-10 Phase 11 — VIEW_SENSITIVE_DATA 권한 게이트가
        # 추가됐다(app/domains/order/router.py). 이 테스트는 그
        # 이후의 재인증 게이트를 검증하는 것이 목적이므로 권한
        # 체크는 통과한 것으로 가정한다(권한 자체의 동작은 별도
        # 테스트가 검증한다 — role_permission 도메인 테스트 참고).
        with patch("app.domains.order.router.require_permission"):
            with self.assertRaises(UnauthorizedException):
                get_order_sensitive_detail(
                    7, current_user=self.user, db=object(),
                    recent_auth_token=None,
                )

    def test_sensitive_detail_returns_original_once_then_consumes_token(self):
        token, _ = issue_recent_auth_token(self.user.id)
        with patch(
            "app.domains.order.router.OrderService.get_order",
            return_value=_order(),
        ), patch("app.domains.order.router.require_permission"):
            result = get_order_sensitive_detail(
                7, current_user=self.user, db=object(),
                recent_auth_token=token,
            )
            self.assertEqual(result.receiver_phone, "010-1234-5678")
            self.assertEqual(result.receiver_address, "서울특별시 강남구 테헤란로 1")
            with self.assertRaises(UnauthorizedException):
                get_order_sensitive_detail(
                    7, current_user=self.user, db=object(),
                    recent_auth_token=token,
                )


if __name__ == "__main__":
    unittest.main()
