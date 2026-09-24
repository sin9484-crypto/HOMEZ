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


def _fake_post_factory(response):

    calls = []

    def fake_post(url, *, headers, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return response

    fake_post.calls = calls
    return fake_post


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

    def test_get_product_parses_extends_info_shipping_fields(self):
        """2026-09-11 후속(반자동 완료 라운드, Phase 3 재감사) —
        이전 조사가 놓쳤던 extends_info(배송비 제안값)를 실제로
        파싱하는지 확인한다."""

        body = {
            "status": 200,
            "result": {
                "id": 1, "prd_code": "CH1234567", "product_nm": "테스트 상품",
                "prd_state": 1, "options": [],
                "extends_info": {
                    "send_type": "개별 배송비", "quantity": 1,
                    "send_price": 3000, "jeju_send_price": 5000,
                    "etc_send_price": 6000,
                },
            },
        }
        fake_get = _fake_get_factory(_FakeResponse(200, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        product = client.get_product("CH1234567")

        self.assertIsNotNone(product.shipping_info)
        self.assertEqual(product.shipping_info.send_type, "개별 배송비")
        self.assertEqual(product.shipping_info.quantity_threshold, 1)
        self.assertEqual(product.shipping_info.base_shipping_cost, 3000)
        self.assertEqual(product.shipping_info.jeju_shipping_cost, 5000)
        self.assertEqual(product.shipping_info.remote_area_shipping_cost, 6000)

    def test_get_product_missing_extends_info_leaves_shipping_info_none(self):
        """extends_info 자체가 없는 상품 응답 — 0으로 추측하지 않고
        None으로 남긴다."""

        fake_get = _fake_get_factory(_FakeResponse(200, PRODUCT_DETAIL_SUCCESS_BODY))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        product = client.get_product("CH1234567")

        self.assertIsNone(product.shipping_info)

    def test_get_product_malformed_extends_info_fields_become_none_not_zero(self):
        """send_price가 정수가 아니거나(null·문자열) 필드 자체가
        없으면 그 항목만 None — 다른 정상 필드까지 버리지 않고,
        그 항목을 0으로 대체하지도 않는다."""

        body = {
            "status": 200,
            "result": {
                "id": 1, "prd_code": "CH1", "product_nm": "x",
                "prd_state": 1, "options": [],
                "extends_info": {
                    "send_type": "수량별 배송비",
                    "send_price": None,
                    "jeju_send_price": "확인불가",
                    # etc_send_price 필드 자체가 없음
                },
            },
        }
        fake_get = _fake_get_factory(_FakeResponse(200, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        product = client.get_product("CH1")

        self.assertEqual(product.shipping_info.send_type, "수량별 배송비")
        self.assertIsNone(product.shipping_info.base_shipping_cost)
        self.assertIsNone(product.shipping_info.jeju_shipping_cost)
        self.assertIsNone(product.shipping_info.remote_area_shipping_cost)
        self.assertIsNone(product.shipping_info.quantity_threshold)

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


class NewProductFieldParsingTestCase(unittest.TestCase):
    """2026-09-24 후속(상품등록 차단 항목 해소 라운드) — disc_price·
    recom_cus_price·return_comment·img_url·sec_tax·prd_char1·gosi_info
    파싱. 정상·누락·null·잘못된 타입을 각각 구분하며, 누락/null/잘못된
    타입을 0·False·빈 문자열 같은 '정상값'으로 대체하지 않는지 확인한다."""

    def test_normal_values_are_parsed(self):

        body = {
            "status": 200,
            "result": {
                "id": 1, "prd_code": "CH1", "product_nm": "테스트 상품",
                "prd_state": 1,
                "return_comment": "7일 이내 반품 가능",
                "img_url": "https://img.onch3.co.kr/a.jpg",
                "sec_tax": "Y",
                "prd_char1": "N",
                "gosi_info": {"품명": "바디워시", "제조국": "대한민국"},
                "options": [
                    {
                        "num": 111, "option_nm": "기본", "option_price": 10000,
                        "amount": 5, "disc_price": 9500, "recom_cus_price": 12000,
                    },
                ],
            },
        }
        fake_get = _fake_get_factory(_FakeResponse(200, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        product = client.get_product("CH1")

        self.assertEqual(product.status, 1)
        self.assertEqual(product.return_comment, "7일 이내 반품 가능")
        self.assertEqual(product.img_url, "https://img.onch3.co.kr/a.jpg")
        self.assertIs(product.tax_exempt, True)
        self.assertIs(product.minor_sale_prohibited, False)
        self.assertEqual(product.notice_info_raw, {"품명": "바디워시", "제조국": "대한민국"})
        self.assertEqual(product.options[0].disc_price, 9500)
        self.assertEqual(product.options[0].recom_cus_price, 12000)

    def test_missing_fields_become_none_not_defaults(self):
        """필드 자체가 응답에 없으면 None — 0/False/빈 dict로 대체하지
        않는다."""

        fake_get = _fake_get_factory(_FakeResponse(200, PRODUCT_DETAIL_SUCCESS_BODY))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        product = client.get_product("CH1234567")

        self.assertIsNone(product.return_comment)
        self.assertIsNone(product.img_url)
        self.assertIsNone(product.tax_exempt)
        self.assertIsNone(product.minor_sale_prohibited)
        self.assertIsNone(product.notice_info_raw)
        self.assertIsNone(product.options[0].disc_price)
        self.assertIsNone(product.options[0].recom_cus_price)

    def test_explicit_null_values_become_none(self):

        body = {
            "status": 200,
            "result": {
                "id": 1, "prd_code": "CH1", "product_nm": "x", "prd_state": 1,
                "return_comment": None, "img_url": None,
                "sec_tax": None, "prd_char1": None, "gosi_info": None,
                "options": [
                    {
                        "num": 1, "option_nm": "기본", "option_price": 1000,
                        "amount": 1, "disc_price": None, "recom_cus_price": None,
                    },
                ],
            },
        }
        fake_get = _fake_get_factory(_FakeResponse(200, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        product = client.get_product("CH1")

        self.assertIsNone(product.return_comment)
        self.assertIsNone(product.img_url)
        self.assertIsNone(product.tax_exempt)
        self.assertIsNone(product.minor_sale_prohibited)
        self.assertIsNone(product.notice_info_raw)
        self.assertIsNone(product.options[0].disc_price)
        self.assertIsNone(product.options[0].recom_cus_price)

    def test_wrong_type_values_become_none_not_a_crash(self):
        """disc_price가 문자열, sec_tax/prd_char1이 'Y'/'N' 외 값,
        gosi_info가 dict가 아닌 경우 — 예외를 내지 않고 해석 불가로
        남긴다(추측 변환하지 않는다)."""

        body = {
            "status": 200,
            "result": {
                "id": 1, "prd_code": "CH1", "product_nm": "x", "prd_state": 1,
                "return_comment": 12345, "img_url": ["not", "a", "string"],
                "sec_tax": "1", "prd_char1": "yes", "gosi_info": "문자열입니다",
                "options": [
                    {
                        "num": 1, "option_nm": "기본", "option_price": 1000,
                        "amount": 1, "disc_price": "9500원", "recom_cus_price": True,
                    },
                ],
            },
        }
        fake_get = _fake_get_factory(_FakeResponse(200, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        product = client.get_product("CH1")

        self.assertIsNone(product.return_comment)
        self.assertIsNone(product.img_url)
        self.assertIsNone(product.tax_exempt)
        self.assertIsNone(product.minor_sale_prohibited)
        self.assertIsNone(product.notice_info_raw)
        self.assertIsNone(product.options[0].disc_price)
        self.assertIsNone(product.options[0].recom_cus_price)

    def test_boolean_is_not_mistaken_for_int(self):
        """disc_price=True(bool)는 int의 서브클래스지만 정상 가격으로
        오인하지 않는다 — 기존 check_member_point 회귀 케이스와 동일한
        원칙."""

        body = {
            "status": 200,
            "result": {
                "id": 1, "prd_code": "CH1", "product_nm": "x", "prd_state": 1,
                "options": [
                    {
                        "num": 1, "option_nm": "기본", "option_price": 1000,
                        "amount": 1, "disc_price": False,
                    },
                ],
            },
        }
        fake_get = _fake_get_factory(_FakeResponse(200, body))
        client = OnchannelApiClient(auth_key="k", http_get=fake_get)

        product = client.get_product("CH1")

        self.assertIsNone(product.options[0].disc_price)


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


class SalesApplicationTestCase(unittest.TestCase):
    """2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수"
    확정) — apply_for_sale()의 요청 바디·응답 해석·오류 분류를
    검증한다. 실제 네트워크를 전혀 열지 않는다(가짜 http_post 주입).
    승인 여부를 이 응답에서 읽으려 시도하지 않는다는 것도 함께
    확인한다(그런 필드 자체가 응답 dataclass에 없다 — 이 메서드가
    반환하는 것은 문자열 prd_code 하나뿐이다)."""

    def test_apply_for_sale_sends_prd_code_and_bearer_auth(self):

        body = {
            "status": 200, "meta": {"timestamp": "x", "api_type": "openapi"},
            "result": {"prd_code": "CH1894996"},
        }
        fake_post = _fake_post_factory(_FakeResponse(200, body))
        client = OnchannelApiClient(auth_key="secret-jwt", http_post=fake_post)

        applied_code = client.apply_for_sale("CH1894996")

        self.assertEqual(applied_code, "CH1894996")
        self.assertEqual(fake_post.calls[0]["json"], {"prd_code": "CH1894996"})
        self.assertEqual(
            fake_post.calls[0]["headers"]["Authorization"], "Bearer secret-jwt",
        )
        self.assertEqual(
            fake_post.calls[0]["url"],
            "https://api.onch3.co.kr/openapi/seller/product/apply",
        )

    def test_apply_for_sale_missing_prd_code_raises_format_error(self):

        fake_post = _fake_post_factory(_FakeResponse(200, {"result": {}}))
        client = OnchannelApiClient(auth_key="k", http_post=fake_post)
        with self.assertRaises(OnchannelResponseFormatError):
            client.apply_for_sale("CH1")

    def test_apply_for_sale_409_raises_validation_error(self):
        """409는 "이미 신청된 상품 재신청" 정황으로 추정될 뿐 공식
        확정 사실이 아니다(docs 참고) — 그래서 이 클라이언트는 409를
        "이미 신청됨"으로 특별 취급하지 않고, 다른 명시적 거부와
        동일하게 OnchannelValidationError로만 던진다."""

        body = {"error": {"code": 409, "message": "이미 신청된 상품입니다."}}
        fake_post = _fake_post_factory(_FakeResponse(409, body))
        client = OnchannelApiClient(auth_key="k", http_post=fake_post)
        with self.assertRaises(OnchannelValidationError):
            client.apply_for_sale("CH1")

    def test_apply_for_sale_401_raises_authentication_error(self):

        fake_post = _fake_post_factory(_FakeResponse(401, ERROR_BODY_401))
        client = OnchannelApiClient(auth_key="k", http_post=fake_post)
        with self.assertRaises(OnchannelAuthenticationError):
            client.apply_for_sale("CH1")

    def test_apply_for_sale_network_exception_raises_network_error(self):

        def raising_post(*args, **kwargs):
            raise TimeoutError("연결 시간 초과")

        client = OnchannelApiClient(auth_key="k", http_post=raising_post)
        with self.assertRaises(OnchannelNetworkError):
            client.apply_for_sale("CH1")


if __name__ == "__main__":
    unittest.main()
