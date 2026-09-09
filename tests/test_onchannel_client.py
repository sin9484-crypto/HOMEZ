"""
=========================================================
Homez OS

File : tests/test_onchannel_client.py

Gate PT-3(2026-09-08 후속) — app/domains/purchase_task/
onchannel_client.py 격리 테스트. 실제 네트워크를 전혀 열지 않는다
(가짜 http_get 주입) — 인증실패·권한부족·존재하지않음·호출제한·
응답형식오류·네트워크오류를 각각 구분하는지, 그리고 JWT·개인정보가
예외 메시지에 절대 노출되지 않는지 확인한다.
=========================================================
"""

import unittest

from app.domains.purchase_task.onchannel_client import OnchannelApiClient
from app.domains.purchase_task.onchannel_client import OnchannelApiError
from app.domains.purchase_task.onchannel_client import OnchannelAuthenticationError
from app.domains.purchase_task.onchannel_client import OnchannelNetworkError
from app.domains.purchase_task.onchannel_client import OnchannelNotFoundError
from app.domains.purchase_task.onchannel_client import OnchannelOrderOption
from app.domains.purchase_task.onchannel_client import OnchannelOrderRegistrationRequest
from app.domains.purchase_task.onchannel_client import OnchannelPermissionError
from app.domains.purchase_task.onchannel_client import OnchannelRateLimitedError
from app.domains.purchase_task.onchannel_client import OnchannelResponseFormatError
from app.domains.purchase_task.onchannel_client import OnchannelValidationError
from app.domains.purchase_task.onchannel_client import mask_pii
from app.domains.purchase_task.onchannel_client import mask_secret


class _FakeResponse:

    def __init__(self, status_code, json_body=None, raise_on_json=False):

        self.status_code = status_code
        self._json_body = json_body
        self._raise_on_json = raise_on_json

    def json(self):

        if self._raise_on_json:
            raise ValueError("no json object could be decoded")
        return self._json_body


def _fake_get_factory(response):

    calls = []

    def fake_get(url, *, headers, params=None, timeout=None):
        calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return response

    fake_get.calls = calls
    return fake_get


PRODUCT_DETAIL_SUCCESS_BODY = {
    "status": 200,
    "meta": {"timestamp": "2026-09-08 00:00:00", "api_type": "openapi"},
    "result": {
        "id": 1, "prd_code": "CH1234567", "product_nm": "테스트 상품",
        "prd_state": 1,
        "options": [
            {"num": 111, "option_nm": "기본", "option_price": 10000, "amount": 5},
        ],
    },
}

PRODUCT_LIST_SUCCESS_BODY = {
    "status": 200,
    "meta": {"timestamp": "2026-09-08 00:00:00", "api_type": "openapi"},
    "result": {
        "items": [
            {
                "id": 1, "product_code": "CH1234567", "name": "테스트 상품",
                "status": 1,
                "options": [{"id": 111, "name": "기본", "qty": 5}],
            },
        ],
        "total_cnt": 1, "page": 1, "page_size": 30, "last_page": 1,
    },
}

ORDER_DETAIL_SUCCESS_BODY = {
    "status": 200,
    "meta": {"timestamp": "2026-09-08 00:00:00", "api_type": "openapi"},
    "result": {
        "order_code": "MO_1", "product_code": "CH1234567",
        "product_name": "테스트 상품", "order_price": 13000,
        "status": 2, "detail_status": "배송준비중",
        "deliverys": [
            {"name": "CJ대한통운", "tracking_number": "123456", "created_at": "2026-09-08"},
        ],
    },
}

ORDER_LIST_SUCCESS_BODY = {
    "status": 200,
    "meta": {"timestamp": "2026-09-08 00:00:00", "api_type": "openapi"},
    "result": {
        "items": [
            {
                "order_code": "MO_1", "product_code": "CH1234567",
                "product_name": "테스트 상품", "order_price": 13000,
                "status": 2, "detail_status": "배송준비중",
            },
        ],
        "total_cnt": 1, "page": 1, "page_size": 30, "last_page": 1,
    },
}

ERROR_BODY_401 = {
    "status": 401, "meta": {"timestamp": "x", "api_type": "openapi"},
    "error": {"code": 401, "message": "잘못 된 접근입니다."},
}


class SuccessParsingTestCase(unittest.TestCase):

    def test_get_product_parses_fields(self):

        fake_get = _fake_get_factory(_FakeResponse(200, PRODUCT_DETAIL_SUCCESS_BODY))
        client = OnchannelApiClient(auth_key="test-jwt-abc", http_get=fake_get)

        product = client.get_product("CH1234567")

        self.assertEqual(product.product_code, "CH1234567")
        self.assertEqual(product.title, "테스트 상품")
        self.assertEqual(len(product.options), 1)
        self.assertEqual(product.options[0].option_id, 111)
        self.assertEqual(product.options[0].price, 10000)

    def test_get_product_sends_bearer_auth_header(self):

        fake_get = _fake_get_factory(_FakeResponse(200, PRODUCT_DETAIL_SUCCESS_BODY))
        client = OnchannelApiClient(auth_key="secret-jwt-value", http_get=fake_get)

        client.get_product("CH1234567")

        self.assertEqual(
            fake_get.calls[0]["headers"]["Authorization"], "Bearer secret-jwt-value",
        )
        self.assertEqual(fake_get.calls[0]["url"], "https://api.onch3.co.kr/openapi/seller/product/CH1234567")

    def test_list_products_parses_items(self):

        fake_get = _fake_get_factory(_FakeResponse(200, PRODUCT_LIST_SUCCESS_BODY))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        products = client.list_products(page=1)

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].product_code, "CH1234567")

    def test_get_order_parses_delivery(self):

        fake_get = _fake_get_factory(_FakeResponse(200, ORDER_DETAIL_SUCCESS_BODY))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        order = client.get_order("MO_1")

        self.assertEqual(order.order_code, "MO_1")
        self.assertEqual(len(order.deliverys), 1)
        self.assertEqual(order.deliverys[0].tracking_number, "123456")

    def test_list_orders_requires_date_range_params(self):

        fake_get = _fake_get_factory(_FakeResponse(200, ORDER_LIST_SUCCESS_BODY))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        orders = client.list_orders(start_at="2026-09-01", end_at="2026-09-08", page=1)

        self.assertEqual(len(orders), 1)
        self.assertEqual(fake_get.calls[0]["params"]["start_at"], "2026-09-01")
        self.assertEqual(fake_get.calls[0]["params"]["end_at"], "2026-09-08")


class ErrorClassificationTestCase(unittest.TestCase):
    """인증실패·권한부족·존재하지않음·호출제한·응답형식오류를 각각
    다른 예외 타입으로 구분하는지 확인한다 — 지시문 1번 핵심."""

    def test_401_raises_authentication_error(self):

        fake_get = _fake_get_factory(_FakeResponse(401, ERROR_BODY_401))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)
        with self.assertRaises(OnchannelAuthenticationError):
            client.get_product("CH1")

    def test_403_raises_permission_error(self):

        body = {"error": {"code": 403, "message": "권한이 없습니다."}}
        fake_get = _fake_get_factory(_FakeResponse(403, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)
        with self.assertRaises(OnchannelPermissionError):
            client.get_product("CH1")

    def test_404_raises_not_found_error(self):

        body = {"error": {"code": 404, "message": "찾을 수 없습니다."}}
        fake_get = _fake_get_factory(_FakeResponse(404, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)
        with self.assertRaises(OnchannelNotFoundError):
            client.get_product("CH-UNKNOWN")

    def test_429_raises_rate_limited_error(self):

        body = {"error": {"code": 429, "message": "너무 많은 요청입니다."}}
        fake_get = _fake_get_factory(_FakeResponse(429, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)
        with self.assertRaises(OnchannelRateLimitedError):
            client.get_product("CH1")

    def test_400_and_409_raise_validation_error(self):

        for code in (400, 409):
            body = {"error": {"code": code, "message": "잘못된 요청"}}
            fake_get = _fake_get_factory(_FakeResponse(code, body))
            client = OnchannelApiClient(auth_key="k", http_get=fake_get)
            with self.assertRaises(OnchannelValidationError):
                client.get_product("CH1")

    def test_non_json_response_raises_format_error(self):

        fake_get = _fake_get_factory(_FakeResponse(200, raise_on_json=True))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)
        with self.assertRaises(OnchannelResponseFormatError):
            client.get_product("CH1")

    def test_missing_result_field_raises_format_error(self):

        fake_get = _fake_get_factory(_FakeResponse(200, {"status": 200, "meta": {}}))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)
        with self.assertRaises(OnchannelResponseFormatError):
            client.get_product("CH1")

    def test_missing_required_nested_field_raises_format_error(self):
        """result는 있지만 스펙이 필수라고 명시한 하위 필드(prd_code)가
        없는 경우 — "형식은 맞지만 내용이 스펙과 다름"도 구분한다."""

        fake_get = _fake_get_factory(_FakeResponse(200, {"result": {"product_nm": "이름만 있음"}}))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)
        with self.assertRaises(OnchannelResponseFormatError):
            client.get_product("CH1")

    def test_network_exception_raises_network_error(self):

        def raising_get(*args, **kwargs):
            raise TimeoutError("연결 시간 초과")

        client = OnchannelApiClient(auth_key="k", http_get=raising_get)
        with self.assertRaises(OnchannelNetworkError):
            client.get_product("CH1")

    def test_unclassified_status_code_still_raises_base_error(self):

        fake_get = _fake_get_factory(_FakeResponse(500, {"error": {"message": "서버 오류"}}))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)
        with self.assertRaises(OnchannelApiError):
            client.get_product("CH1")


class SecretAndPiiMaskingTestCase(unittest.TestCase):
    """지시문 4번 — 합성 개인정보로 마스킹을 검증한다. JWT·개인정보
    원문이 예외 메시지에 절대 남지 않아야 한다."""

    def test_mask_secret_never_exposes_full_value(self):

        masked = mask_secret("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.super-secret-body")
        self.assertNotIn("super-secret-body", masked)
        self.assertTrue(masked.startswith("eyJh"))

    def test_mask_secret_handles_empty(self):

        self.assertEqual(mask_secret(None), "(empty)")
        self.assertEqual(mask_secret(""), "(empty)")

    def test_mask_pii_never_exposes_full_name_or_phone(self):

        masked_name = mask_pii("홍길동")
        masked_phone = mask_pii("010-1234-5678")
        self.assertNotIn("길동", masked_name)
        self.assertNotIn("1234-5678", masked_phone)
        self.assertTrue(masked_name.startswith("홍"))

    def test_authentication_error_message_never_contains_auth_key(self):
        """실제 JWT 값을 넣어 401을 재현해도, 예외 메시지 어디에도
        그 값이 없어야 한다."""

        secret_jwt = "eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SIGNATURE-should-never-leak"
        fake_get = _fake_get_factory(_FakeResponse(401, ERROR_BODY_401))
        client = OnchannelApiClient(auth_key=secret_jwt, http_get=fake_get)

        with self.assertRaises(OnchannelAuthenticationError) as ctx:
            client.get_product("CH1")

        self.assertNotIn(secret_jwt, str(ctx.exception))
        self.assertNotIn("SIGNATURE-should-never-leak", str(ctx.exception))


class OrderRegistrationRequestDesignTestCase(unittest.TestCase):
    """지시문 4번 — 발주 요청은 아직 전송하지 않지만(POST 메서드
    자체가 없다), 그 요청 바디의 "설계"가 스펙이 요구하는 필드
    이상을 담지 않는지·개인정보가 repr에 노출되지 않는지 합성
    데이터로 검증한다."""

    def setUp(self):

        self.synthetic_request = OnchannelOrderRegistrationRequest(
            product_code="CH1231221",
            recv_name="테스트수령인",
            recv_tell="02-111-1111",
            recv_mobile="010-1111-1111",
            zipcode="12345",
            address="서울시 종로구 테스트로 1",
            options=(OnchannelOrderOption(id=22431922, qty=1),),
            sale_code="HOMEZ-TEST-ORDER-1",
        )

    def test_to_request_body_matches_spec_required_fields_exactly(self):

        body = self.synthetic_request.to_request_body()

        for field in (
            "product_code", "recv_name", "recv_tell", "recv_mobile",
            "zipcode", "address", "options",
        ):
            self.assertIn(field, body)

        # 스펙에 없는 필드(이메일·회원등급·주문이력 등)가 섞여 들어갈
        # 방법 자체가 없다 — dataclass 필드 목록이 곧 계약이다.
        allowed_fields = {
            "product_code", "recv_name", "recv_tell", "recv_mobile",
            "zipcode", "address", "address_detail", "comment",
            "sale_code", "site_name", "options",
        }
        self.assertTrue(set(body.keys()).issubset(allowed_fields))

    def test_to_request_body_options_serialize_without_pii(self):

        body = self.synthetic_request.to_request_body()
        self.assertEqual(body["options"], [{"id": 22431922, "qty": 1}])

    def test_repr_never_exposes_recipient_pii(self):
        """dataclass 기본 repr을 그대로 뒀다면 이 테스트가 실패했을
        것이다 — 개인정보가 log/print/예외 traceback에 그대로 찍히는
        사고를 구조적으로 막는 회귀 테스트."""

        rendered = repr(self.synthetic_request)

        self.assertNotIn("테스트수령인", rendered)
        self.assertNotIn("02-111-1111", rendered)
        self.assertNotIn("010-1111-1111", rendered)
        self.assertNotIn("서울시 종로구 테스트로 1", rendered)
        # 개인정보가 아닌 필드는 그대로 보여도 된다(디버깅에 필요).
        self.assertIn("CH1231221", rendered)

    def test_str_also_uses_masked_repr(self):

        rendered = str(self.synthetic_request)
        self.assertNotIn("테스트수령인", rendered)

    def test_optional_fields_omitted_when_blank(self):

        minimal = OnchannelOrderRegistrationRequest(
            product_code="CH1", recv_name="A", recv_tell="02-1", recv_mobile="010-1",
            zipcode="1", address="addr", options=(OnchannelOrderOption(id=1, qty=1),),
        )
        body = minimal.to_request_body()
        for optional in ("address_detail", "comment", "sale_code", "site_name"):
            self.assertNotIn(optional, body)


if __name__ == "__main__":
    unittest.main()
