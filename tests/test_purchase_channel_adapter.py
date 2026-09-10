"""
=========================================================
Homez OS

File : tests/test_purchase_channel_adapter.py

Gate PT-3(2026-09-08, item 7) — app/domains/purchase_task/
channel_adapter.py 격리 테스트. 네트워크 호출 없음, 실제 Windows
Credential Manager를 건드리지 않는다(InMemoryCredentialStore만 사용).
=========================================================
"""

import unittest
from datetime import datetime

from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.purchase.supplier_order_providers import (
    OnchannelSupplierOrderProvider,
)
from app.domains.purchase_task.channel_adapter import CapabilitySupport
from app.domains.purchase_task.channel_adapter import ChannelCapability
from app.domains.purchase_task.channel_adapter import ChannelConnectionStatus
from app.domains.purchase_task.channel_adapter import FakePurchaseChannelAdapter
from app.domains.purchase_task.channel_adapter import OnchannelChannelAdapter
from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapter
from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError
from app.domains.purchase_task.channel_adapter import get_purchase_channel_adapter


class _BareAdapter(PurchaseChannelAdapter):
    """아무 메서드도 override하지 않은 매입처 — "미확인"만 정직하게
    반환하는지 확인하는 베이스라인."""

    mall_code = "BARE_UNVERIFIED"


class BaseAdapterDefaultsTestCase(unittest.TestCase):
    """확인하지 않은 매입처는 8개 기능 전부 UNKNOWN이어야 한다 —
    NOT_SUPPORTED로 성급하게 단정하지 않는다(미확인 ≠ 미지원)."""

    def setUp(self):

        self.adapter = _BareAdapter()

    def test_capability_matrix_is_all_unknown(self):

        matrix = self.adapter.capability_matrix()
        self.assertEqual(set(matrix.keys()), set(ChannelCapability.ALL))
        for capability, support in matrix.items():
            self.assertEqual(
                support, CapabilitySupport.UNKNOWN,
                f"{capability}가 UNKNOWN이 아닙니다 — 확인하지 않은 매입처는 전부 미확인이어야 합니다.",
            )

    def test_check_connection_defaults_to_not_connected(self):

        result = self.adapter.check_connection(account_label=None)
        self.assertEqual(result.status, ChannelConnectionStatus.NOT_CONNECTED)
        self.assertIsNone(result.account_label)

        with_account = self.adapter.check_connection(account_label="테스트 계정")
        self.assertEqual(with_account.status, ChannelConnectionStatus.LOGIN_REQUIRED)
        self.assertEqual(with_account.account_label, "테스트 계정")

    def test_all_eight_methods_report_unknown_not_a_guess(self):

        self.assertEqual(
            self.adapter.check_login_requirement().support, CapabilitySupport.UNKNOWN,
        )
        self.assertIsNone(self.adapter.check_login_requirement().requires_login)

        product = self.adapter.lookup_product("anything")
        self.assertEqual(product.support, CapabilitySupport.UNKNOWN)
        self.assertEqual(product.options, ())

        order_form = self.adapter.prepare_order_form("anything", None, 1)
        self.assertEqual(order_form.support, CapabilitySupport.UNKNOWN)
        self.assertIsNone(order_form.estimated_total_amount)
        self.assertFalse(order_form.mutates_remote_state)

        payment = self.adapter.check_payment_executability()
        self.assertEqual(payment.support, CapabilitySupport.UNKNOWN)
        self.assertEqual(payment.supported_payment_methods, ())

        order = self.adapter.lookup_order("anything")
        self.assertEqual(order.support, CapabilitySupport.UNKNOWN)

        tracking = self.adapter.lookup_tracking("anything")
        self.assertEqual(tracking.support, CapabilitySupport.UNKNOWN)

        cancel = self.adapter.check_cancel_support()
        self.assertEqual(cancel.support, CapabilitySupport.UNKNOWN)


class FakePurchaseChannelAdapterTestCase(unittest.TestCase):
    """FAKE Adapter는 결정론적이고 항상 SUPPORTED만 반환한다 — 테스트
    전용, 실 매입처 취급 금지."""

    def test_credential_registered_without_verified_at_is_unverified_not_connected(self):
        """2026-09-08 정정 — 자격증명 등록만으로는 CONNECTED가 아니다."""

        adapter = FakePurchaseChannelAdapter(connected=True)
        connection = adapter.check_connection(account_label="fake-1")
        self.assertEqual(connection.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)
        self.assertTrue(connection.credential_registered)
        self.assertFalse(connection.verified)
        self.assertIsNone(connection.verified_at)

    def test_verified_at_present_reports_connected_and_full_support(self):

        adapter = FakePurchaseChannelAdapter(connected=True)
        now = datetime.utcnow()
        connection = adapter.check_connection(account_label="fake-1", verified_at=now)
        self.assertEqual(connection.status, ChannelConnectionStatus.CONNECTED)
        self.assertTrue(connection.verified)
        self.assertEqual(connection.verified_at, now)

        matrix = adapter.capability_matrix()
        for support in matrix.values():
            self.assertEqual(support, CapabilitySupport.SUPPORTED)

    def test_disconnected_scenario_reports_login_required(self):

        adapter = FakePurchaseChannelAdapter(connected=False)
        connection = adapter.check_connection(account_label="fake-1")
        self.assertEqual(connection.status, ChannelConnectionStatus.LOGIN_REQUIRED)
        self.assertFalse(connection.credential_registered)
        self.assertTrue(adapter.check_login_requirement().requires_login)

    def test_no_account_at_all_reports_not_connected(self):

        adapter = FakePurchaseChannelAdapter(connected=False)
        connection = adapter.check_connection(account_label=None)
        self.assertEqual(connection.status, ChannelConnectionStatus.NOT_CONNECTED)

    def test_order_form_is_read_only_estimate(self):

        adapter = FakePurchaseChannelAdapter()
        result = adapter.prepare_order_form("FAKE-PRD-1", "opt-1", 2)
        self.assertFalse(result.mutates_remote_state)
        self.assertEqual(result.estimated_total_amount, result.estimated_item_amount + result.estimated_shipping_fee)


class OnchannelChannelAdapterTestCase(unittest.TestCase):
    """Onchannel은 연결 상태 확인만 실제로 지원하고 나머지 7개는
    여전히 UNKNOWN이어야 한다 — 공식 기술 문서가 없다는 사실을
    화면에서도 정직하게 드러내는 것이 이 테스트의 목적."""

    def setUp(self):

        self.store = InMemoryCredentialStore()
        self.adapter = OnchannelChannelAdapter(credential_store=self.store)

    def test_reports_not_connected_when_no_credential_saved(self):
        """2026-09-08 재정정(id=3/4 사고) — CREDENTIAL 방식은
        LOGIN_REQUIRED(로그인 필요) 개념이 없다. 자격증명이 없으면
        account_label이 있어도 항상 NOT_CONNECTED("자격증명 미등록")다
        — credential_capable=True 매입처는 자격증명 존재가 CONNECTED의
        전제조건이라는 규칙이 account_label 유무보다 먼저 적용된다."""

        result = self.adapter.check_connection(account_label="사업자 계정 A")
        self.assertEqual(result.status, ChannelConnectionStatus.NOT_CONNECTED)

    def test_credential_registered_alone_is_unverified_not_connected(self):
        """2026-09-08 사용자 정정 — 이게 이번 정정의 핵심 회귀 테스트다:
        Credential Manager에 값이 있다는 사실만으로는 CONNECTED가
        아니라 REGISTERED_UNVERIFIED("등록됨 · 연결 미확인")여야 한다."""

        self.store.save(
            OnchannelSupplierOrderProvider.CREDENTIAL_REFERENCE,
            {"auth_key": "super-secret-value-should-never-leak", "allowed_ip": "1.2.3.4"},
        )
        result = self.adapter.check_connection(account_label="사업자 계정 A")
        self.assertEqual(result.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)
        self.assertTrue(result.credential_registered)
        self.assertFalse(result.verified)
        self.assertIsNone(result.verified_at)
        self.assertNotIn("super-secret-value-should-never-leak", result.detail)

    def test_verified_at_from_caller_reports_connected_without_leaking_credential_value(self):
        """verified_at은 Adapter가 스스로 만들지 않고, 호출자(서비스
        계층)가 PurchaseChannelConnection Model의 값을 넘겨줄 때만
        존재한다 — 여기서는 그 호출 계약을 직접 흉내낸다."""

        self.store.save(
            OnchannelSupplierOrderProvider.CREDENTIAL_REFERENCE,
            {"auth_key": "super-secret-value-should-never-leak", "allowed_ip": "1.2.3.4"},
        )
        confirmed_at = datetime.utcnow()
        result = self.adapter.check_connection(
            account_label="사업자 계정 A", verified_at=confirmed_at,
        )
        self.assertEqual(result.status, ChannelConnectionStatus.CONNECTED)
        self.assertTrue(result.verified)
        self.assertEqual(result.verified_at, confirmed_at)
        self.assertNotIn("super-secret-value-should-never-leak", result.detail)

    def test_capability_matrix_matches_confirmed_spec_vs_still_unknown(self):
        """2026-09-08 후속 — 실제 공식 스펙 확보 이후 이 값이 바뀌었다.
        연결확인·상품조회·주문조회·배송조회는 이제 SUPPORTED, 취소는
        스펙에 없어 NOT_SUPPORTED, 나머지(로그인필요여부/주문서작성·
        금액확인/결제실행가능여부)는 여전히 UNKNOWN이다 — 코드가
        준비됐다고 전부 SUPPORTED로 바뀌지 않았음을 확인한다."""

        matrix = self.adapter.capability_matrix()
        supported = {
            ChannelCapability.CONNECTION_CHECK,
            ChannelCapability.PRODUCT_OPTION_PRICE_STOCK_LOOKUP,
            ChannelCapability.EXISTING_ORDER_LOOKUP,
            ChannelCapability.SHIPPING_TRACKING_LOOKUP,
        }
        for capability in supported:
            self.assertEqual(matrix[capability], CapabilitySupport.SUPPORTED)

        self.assertEqual(
            matrix[ChannelCapability.CANCEL_SUPPORT_CHECK], CapabilitySupport.NOT_SUPPORTED,
        )

        still_unknown = set(ChannelCapability.ALL) - supported - {
            ChannelCapability.CANCEL_SUPPORT_CHECK,
        }
        for capability in still_unknown:
            self.assertEqual(
                matrix[capability], CapabilitySupport.UNKNOWN,
                f"{capability}는 아직 확인되지 않았어야 합니다.",
            )

    def test_lookup_product_without_credential_raises_not_a_silent_unknown(self):
        """자격증명이 없으면 조용히 UNKNOWN을 반환하지 않고 명확한
        예외를 던진다 — 자격증명 존재만으로 연결 성공을 표시하지
        않는다는 원칙의 반대 방향(자격증명 부재를 흐리지 않는다)."""

        with self.assertRaises(PurchaseChannelAdapterError):
            self.adapter.lookup_product("anything")


class AdapterRegistryTestCase(unittest.TestCase):

    def test_known_codes_resolve(self):

        self.assertIsInstance(
            get_purchase_channel_adapter("FAKE_CHANNEL"), FakePurchaseChannelAdapter,
        )
        self.assertIsInstance(
            get_purchase_channel_adapter("ONCHANNEL"), OnchannelChannelAdapter,
        )

    def test_unknown_code_raises(self):

        with self.assertRaises(PurchaseChannelAdapterError):
            get_purchase_channel_adapter("NOT_A_REAL_MALL")

    def test_credential_reference_passed_through_for_onchannel_only(self):

        onch = get_purchase_channel_adapter("ONCHANNEL", credential_reference="conn-9")
        self.assertEqual(onch._credential_reference, "conn-9")

        fake = get_purchase_channel_adapter("FAKE_CHANNEL", credential_reference="ignored")
        self.assertIsInstance(fake, FakePurchaseChannelAdapter)

    def test_credential_store_passed_through_is_the_same_instance(self):
        """2026-09-08 후속 — 격리 검증 중 실제 Windows Credential
        Manager 오염 사고 재발 방지 회귀 테스트. 호출자가 credential_
        store를 넘기면, 만들어진 Adapter는 그 저장소를 "그대로"(같은
        객체) 써야 한다 — 넘기지 않으면 Adapter가 몰래 자기만의 실제
        WindowsCredentialStore()를 새로 만들어, 호출자가 격리했다고
        믿는 것과 실제로 조회하는 저장소가 어긋난다."""

        fake_store = InMemoryCredentialStore()
        onch = get_purchase_channel_adapter(
            "ONCHANNEL", credential_reference="conn-9", credential_store=fake_store,
        )
        self.assertIs(onch._credential_store, fake_store)

    def test_no_credential_store_argument_still_works(self):
        """credential_store를 안 넘기는 기존 호출부(하위 호환)도 여전히
        동작한다 — 이때만 Adapter가 자기 기본값(실제 저장소)을 쓴다."""

        onch = get_purchase_channel_adapter("ONCHANNEL", credential_reference="conn-9")
        self.assertIsNotNone(onch._credential_store)


class OnchannelRealLookupTestCase(unittest.TestCase):
    """2026-09-08 후속 — 실제 공식 스펙 확보 이후 구현한 상품·주문
    조회. 실제 네트워크는 절대 열지 않는다(가짜 http_get 주입)."""

    def _fake_get(self, status_code, body):

        class _Resp:
            def __init__(self):
                self.status_code = status_code
            def json(self):
                return body

        def get(url, *, headers, params=None, timeout=None):
            self.last_call = {"url": url, "headers": headers}
            return _Resp()

        return get

    def setUp(self):

        self.store = InMemoryCredentialStore()

    def test_lookup_product_requires_credential(self):

        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
        )
        with self.assertRaises(PurchaseChannelAdapterError):
            adapter.lookup_product("CH1")

    def test_lookup_product_success_maps_fields(self):

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {
            "result": {
                "prd_code": "CH1234567", "product_nm": "테스트 상품",
                "prd_state": 1,
                "options": [
                    {"num": 1, "option_nm": "기본", "option_price": 5000, "amount": 3},
                ],
            },
        }
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.lookup_product("CH1234567")

        self.assertEqual(result.support, CapabilitySupport.SUPPORTED)
        self.assertEqual(result.external_product_id, "CH1234567")
        self.assertEqual(result.title, "테스트 상품")
        self.assertEqual(len(result.options), 1)
        self.assertEqual(result.options[0].in_stock, True)

    def test_lookup_product_uses_this_connections_own_credential_not_others(self):
        """회사별 자격증명 참조 일관성 — 다른 연결의 자격증명이 섞이지
        않는다."""

        self.store.save("conn-1", {"auth_key": "jwt-for-conn-1", "allowed_ip": ""})
        self.store.save("conn-2", {"auth_key": "jwt-for-conn-2", "allowed_ip": ""})
        seen_headers = {}

        def get(url, *, headers, params=None, timeout=None):
            seen_headers["Authorization"] = headers["Authorization"]
            class _Resp:
                status_code = 200
                def json(self_inner):
                    return {"result": {"prd_code": "CH1", "product_nm": "x", "options": []}}
            return _Resp()

        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-2", http_get=get,
        )
        adapter.lookup_product("CH1")

        self.assertEqual(seen_headers["Authorization"], "Bearer jwt-for-conn-2")

    def test_lookup_order_success_maps_fields(self):

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {
            "result": {
                "order_code": "MO_1", "product_code": "CH1",
                "product_name": "테스트", "order_price": 13000,
                "status": 2, "detail_status": "배송준비중", "deliverys": [],
            },
        }
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.lookup_order("MO_1")

        self.assertEqual(result.support, CapabilitySupport.SUPPORTED)
        self.assertEqual(result.external_order_number, "MO_1")
        self.assertEqual(result.amount, 13000)

    def test_lookup_tracking_single_delivery_returns_it(self):

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {
            "result": {
                "order_code": "MO_1", "product_code": "CH1",
                "product_name": "테스트", "order_price": 13000,
                "status": 2, "detail_status": "배송중",
                "deliverys": [
                    {"name": "CJ대한통운", "tracking_number": "111", "created_at": "x"},
                ],
            },
        }
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.lookup_tracking("MO_1")

        self.assertEqual(result.support, CapabilitySupport.SUPPORTED)
        self.assertEqual(result.tracking_number, "111")
        self.assertFalse(result.multiple_deliveries_detected)

    def test_lookup_tracking_multiple_deliveries_does_not_auto_select(self):
        """2026-09-10 후속(온채널 공식 답변 — "부분배송·복수송장
        미지원" 확정) — 단일 송장 정책의 핵심 회귀 테스트. 실제
        응답에 2건 이상이 오면 자동으로 하나를 고르지 않는다."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {
            "result": {
                "order_code": "MO_1", "product_code": "CH1",
                "product_name": "테스트", "order_price": 13000,
                "status": 2, "detail_status": "배송중",
                "deliverys": [
                    {"name": "CJ대한통운", "tracking_number": "111", "created_at": "x"},
                    {"name": "롯데택배", "tracking_number": "222", "created_at": "y"},
                ],
            },
        }
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.lookup_tracking("MO_1")

        self.assertEqual(result.support, CapabilitySupport.SUPPORTED)
        self.assertTrue(result.multiple_deliveries_detected)
        self.assertIsNone(result.tracking_number)
        self.assertIsNone(result.courier)

    def test_lookup_tracking_no_delivery_yet(self):

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {
            "result": {
                "order_code": "MO_1", "product_code": "CH1",
                "product_name": "테스트", "order_price": 13000,
                "status": 1, "detail_status": "상품준비중", "deliverys": [],
            },
        }
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.lookup_tracking("MO_1")

        self.assertEqual(result.support, CapabilitySupport.SUPPORTED)
        self.assertIsNone(result.tracking_number)
        self.assertFalse(result.multiple_deliveries_detected)

    def test_check_member_point_normal_response(self):
        """2026-09-08 후속(발주·결제 계약 조사) — 정상 응답. member_id
        는 마스킹돼서만 나오고, point는 실제 관측값 그대로 온다."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {"result": {"member_id": "sin9484", "point": 0}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.check_member_point()

        self.assertEqual(result.support, CapabilitySupport.SUPPORTED)
        self.assertNotEqual(result.member_id_masked, "sin9484")
        self.assertNotIn("sin9484", result.member_id_masked)
        self.assertEqual(result.point, 0)
        self.assertTrue(result.point_interpretable)
        self.assertEqual(set(result.observed_fields), {"member_id", "point"})

    def test_check_member_point_missing_fields_not_defaulted(self):
        """누락 — point 필드 자체가 없으면 0으로 대체하지 않는다."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {"result": {}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.check_member_point()

        self.assertIsNone(result.point)
        self.assertFalse(result.point_interpretable)
        self.assertIsNone(result.member_id_masked)
        self.assertEqual(result.observed_fields, ())

    def test_check_member_point_null_point_not_defaulted(self):
        """null — point가 명시적으로 null이면 역시 0으로 대체하지
        않는다(필드가 존재는 하지만 해석 불가)."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {"result": {"member_id": "sin9484", "point": None}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.check_member_point()

        self.assertIsNone(result.point)
        self.assertFalse(result.point_interpretable)
        self.assertIn("point", result.observed_fields)

    def test_check_member_point_wrong_type_not_defaulted(self):
        """잘못된 타입 — point가 문자열이면 해석 불가로 남긴다."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {"result": {"member_id": "sin9484", "point": "0원"}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.check_member_point()

        self.assertIsNone(result.point)
        self.assertFalse(result.point_interpretable)

    def test_check_member_point_boolean_point_not_treated_as_int(self):
        """bool은 int의 서브클래스라 실수로 True/False가 1/0
        포인트로 오인될 수 있다 — 명시적으로 제외한다."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {"result": {"point": True}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.check_member_point()

        self.assertIsNone(result.point)
        self.assertFalse(result.point_interpretable)

    def test_check_member_point_extra_unknown_fields_recorded_not_dropped_silently(self):
        """추가 필드 — 알려지지 않은 필드가 와도 관측 목록에는
        남기되(계약 확대 없이), point/member_id 해석에는 영향 없다."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {"result": {"member_id": "sin9484", "point": 500, "grade": "VIP", "extra": {}}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.check_member_point()

        self.assertEqual(result.point, 500)
        self.assertIn("grade", result.observed_fields)
        self.assertIn("extra", result.observed_fields)

    def test_check_member_point_explicit_error_propagates(self):
        """명시적 오류(401 등)는 그대로 예외로 전파한다 — 포인트=0으로
        조용히 처리하지 않는다."""

        from app.domains.purchase_task.onchannel_client import OnchannelAuthenticationError

        self.store.save("conn-1", {"auth_key": "bad-jwt", "allowed_ip": ""})
        body = {"error": {"code": 401, "message": "잘못된 접근입니다."}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(401, body),
        )

        with self.assertRaises(OnchannelAuthenticationError):
            adapter.check_member_point()

    def test_lookup_order_propagates_typed_error_on_401(self):

        from app.domains.purchase_task.onchannel_client import OnchannelAuthenticationError

        self.store.save("conn-1", {"auth_key": "bad-jwt", "allowed_ip": ""})
        body = {"error": {"code": 401, "message": "잘못 된 접근입니다."}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(401, body),
        )

        with self.assertRaises(OnchannelAuthenticationError):
            adapter.lookup_order("MO_1")

    def test_list_products_success_maps_fields(self):
        """2026-09-08 후속 — item 7 승인된 상품 목록 조회
        (`GET seller/product?page=1&page_size=1`) 전용 경로."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {
            "result": {
                "items": [
                    {"product_code": "CH1234567", "name": "테스트 상품", "status": 1},
                ],
                "total_count": 1,
            },
        }
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_get=self._fake_get(200, body),
        )

        result = adapter.list_products(page=1, page_size=1)

        self.assertEqual(result.support, CapabilitySupport.SUPPORTED)
        self.assertEqual(result.total_returned, 1)
        self.assertEqual(result.items[0].external_product_id, "CH1234567")
        self.assertEqual(result.items[0].title, "테스트 상품")

    def test_list_products_sends_page_and_page_size_params(self):

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        seen_params = {}

        def get(url, *, headers, params=None, timeout=None):
            seen_params.update(params or {})
            class _Resp:
                status_code = 200
                def json(self_inner):
                    return {"result": {"items": [], "total_count": 0}}
            return _Resp()

        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1", http_get=get,
        )
        adapter.list_products(page=1, page_size=1)

        self.assertEqual(seen_params.get("page"), 1)
        self.assertEqual(seen_params.get("page_size"), 1)

    def test_list_products_without_credential_raises(self):

        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-missing",
        )
        with self.assertRaises(PurchaseChannelAdapterError):
            adapter.list_products(page=1, page_size=1)

    def test_capability_matrix_reflects_confirmed_spec(self):

        adapter = OnchannelChannelAdapter(credential_store=self.store)
        matrix = adapter.capability_matrix()

        self.assertEqual(
            matrix[ChannelCapability.PRODUCT_OPTION_PRICE_STOCK_LOOKUP],
            CapabilitySupport.SUPPORTED,
        )
        self.assertEqual(
            matrix[ChannelCapability.EXISTING_ORDER_LOOKUP], CapabilitySupport.SUPPORTED,
        )
        self.assertEqual(
            matrix[ChannelCapability.SHIPPING_TRACKING_LOOKUP], CapabilitySupport.SUPPORTED,
        )
        # 스펙 18개 엔드포인트 어디에도 취소 API가 없다 — 추측으로
        # 지원한다고 하지 않는다.
        self.assertEqual(
            matrix[ChannelCapability.CANCEL_SUPPORT_CHECK], CapabilitySupport.NOT_SUPPORTED,
        )
        # 결제 실행 가능 여부는 여전히 미확인.
        self.assertEqual(
            matrix[ChannelCapability.PAYMENT_EXECUTABILITY_CHECK], CapabilitySupport.UNKNOWN,
        )


class SalesApplicationAdapterTestCase(unittest.TestCase):
    """2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수"
    확정) — apply_for_sale()의 Adapter 계층 배선을 검증한다. 실제
    네트워크는 열지 않는다(가짜 http_post 주입)."""

    def _fake_post(self, status_code, body):

        class _Resp:
            def __init__(self):
                self.status_code = status_code
            def json(self):
                return body

        def post(url, *, headers, json=None, timeout=None):
            self.last_call = {"url": url, "headers": headers, "json": json}
            return _Resp()

        return post

    def setUp(self):

        self.store = InMemoryCredentialStore()

    def test_apply_for_sale_requires_credential(self):

        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
        )
        with self.assertRaises(PurchaseChannelAdapterError):
            adapter.apply_for_sale("CH1")

    def test_apply_for_sale_success_does_not_claim_approval(self):
        """support=SUPPORTED·submitted=True까지만 확인한다 — 이
        결과에 "승인됨"을 뜻하는 필드가 아예 없다는 것 자체가
        설계 검증이다(SalesApplicationResult에 approved 필드가
        없다)."""

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {"result": {"prd_code": "CH1894996"}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_post=self._fake_post(200, body),
        )

        result = adapter.apply_for_sale("CH1894996")

        self.assertEqual(result.support, CapabilitySupport.SUPPORTED)
        self.assertTrue(result.submitted)
        self.assertEqual(result.applied_product_code, "CH1894996")
        self.assertFalse(hasattr(result, "approved"))
        self.assertEqual(self.last_call["json"], {"prd_code": "CH1894996"})

    def test_apply_for_sale_uses_this_connections_own_credential(self):

        self.store.save("conn-1", {"auth_key": "jwt-for-conn-1", "allowed_ip": ""})
        self.store.save("conn-2", {"auth_key": "jwt-for-conn-2", "allowed_ip": ""})
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-2",
            http_post=self._fake_post(200, {"result": {"prd_code": "CH1"}}),
        )

        adapter.apply_for_sale("CH1")

        self.assertEqual(self.last_call["headers"]["Authorization"], "Bearer jwt-for-conn-2")

    def test_apply_for_sale_propagates_typed_error_on_409(self):

        from app.domains.purchase_task.onchannel_client import OnchannelValidationError

        self.store.save("conn-1", {"auth_key": "test-jwt", "allowed_ip": ""})
        body = {"error": {"code": 409, "message": "이미 신청된 상품입니다."}}
        adapter = OnchannelChannelAdapter(
            credential_store=self.store, credential_reference="conn-1",
            http_post=self._fake_post(409, body),
        )

        with self.assertRaises(OnchannelValidationError):
            adapter.apply_for_sale("CH1")


if __name__ == "__main__":
    unittest.main()
