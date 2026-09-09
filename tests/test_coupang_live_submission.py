import copy
import copy
import json
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException, NotFoundException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.marketplace_listing.category_metadata import (
    notice_input_fingerprint,
)
from app.domains.marketplace_listing.coupang_live_payload import (
    build_coupang_live_payload,
)
from app.domains.marketplace_listing.coupang_live_provider import (
    CANONICAL_STATUS_MAP,
    STATUS_NAMES,
    CoupangLiveProductProvider,
    LiveSubmissionResult,
    ProductStatusResult,
)
from app.domains.marketplace_listing.listing_wizard_live_service import (
    ListingWizardLiveService,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
    FulfillmentSelectionInput,
    WizardApproveRequest,
    WizardChannelsUpdateRequest,
    WizardDraftUpdateRequest,
    WizardEconomicsUpdateRequest,
    WizardFulfillmentUpdateRequest,
    WizardMediaUpdateRequest,
    WizardSourceUpdateRequest,
    WizardSubmitRequest,
)
from app.domains.marketplace_listing.constants import FulfillmentMode
from tests.test_listing_wizard_service import (
    ListingWizardServiceTestCase,
    VALID_CHANNEL_POLICY_ATTRIBUTES,
    VALID_REQUIRED_FIELDS,
)


LIVE_IMAGES = [{
    "imageOrder": 0,
    "imageType": "REPRESENTATION",
    "vendorPath": "https://images.example.test/product.png",
}]
LIVE_NOTICES = [{
    "noticeCategoryName": "기타 재화",
    "noticeCategoryDetailNames": [{
        "noticeCategoryDetailName": "품명 및 모델명",
        "content": "테스트 상품",
    }],
}]
# 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 공식 문서(Product Creation)
# 확인 결과 items[].contents는 필수(✓) 필드다. 정확한 내부 구조(TEXT/
# IMAGE/HTML 타입 스키마)는 이번 세션에서 공식 원문으로 확정하지
# 못했으므로 HOMEZ는 여전히 구조를 검증하지 않는다(추측 금지) — 이
# fixture는 "비어 있지 않음"만 검증하는 목적의 최소 예시다.
# 2026-08-29 — 공식 문서(Product Creation) WebFetch로 확인한 정확한
# contents[] 구조(contentsType/contentDetails[].{content,detailType}).
# 이전 fixture는 이 계약을 확인하기 전 만든 임시 모양이었다 —
# 확인된 계약으로 교체한다(추측이 아니라 원문 예시 그대로).
LIVE_CONTENTS = [{
    "contentsType": "TEXT",
    "contentDetails": [{"content": "테스트 상품 상세 설명", "detailType": "TEXT"}],
}]
# 2026-08-30 V7 안정화 Phase 1 — 단일 제출 계약(coupang_submission_
# contract.py) 통합 이후 vendorUserId/deliveryCompanyCode/brandState/
# 고시정보 fingerprint까지 build_coupang_live_payload() 자신이 직접
# 검사한다(감사 F-01 재발 방지 — 7단계와 여기가 다른 검사를 하지
# 않는다). 기존 fixture는 이 4가지가 없어 실제로는 이미 실 Live 검증
# 이전 상태를 나타내던 것이었다 — 값을 약화하지 않고 실제 성공했던
# 값(vendorUserId=sin945)과 동일한 모양으로 보강한다.
LIVE_NOTICE_CONTRACT_ATTRIBUTES = {
    "official_category_code": "80754",
    "category_metadata_version": "test-meta-v1",
    "category_metadata_fingerprint": "test-meta-fingerprint",
    "notice_information": {"기타 재화::품명 및 모델명": "테스트 상품"},
    "notice_required_field_keys": ["기타 재화::품명 및 모델명"],
    "notice_confirmed_at": "2026-08-24T00:00:00",
    "notice_confirmed_by_user_id": 1,
    "notice_input_fingerprint": notice_input_fingerprint(
        "80754", "test-meta-v1", "test-meta-fingerprint",
        {"기타 재화::품명 및 모델명": "테스트 상품"},
    ),
}


def _live_channel_policy_attributes(**overrides):
    merged = dict(LIVE_NOTICE_CONTRACT_ATTRIBUTES)
    merged.update(overrides)
    return merged


def _base_live_required_fields(**overrides):
    """2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 테스트 상품(국산
    삼색 부직포 주방행주 40매 38x38cm) 기준 최소 유효 required_fields.
    호출마다 새 dict를 반환한다(테스트 간 공유 상태 없음)."""

    base = {
        "displayCategoryCode": 80754,
        "notices": LIVE_NOTICES,
        "images": LIVE_IMAGES,
        "contents": LIVE_CONTENTS,
        "liveImageRightsConfirmed": True,
        "items": [{"itemName": "삼색 혼합 40매", "externalVendorSku": "HOMEZ-DISHCLOTH-40"}],
        "originalPrice": 12900,
        "salePrice": 12900,
        "maximumBuyCount": 10,
        "deliveryMethod": "SEQUENCIAL",
        "deliveryChargeType": "NOT_FREE",
        "deliveryCharge": 3000,
        "deliveryChargeOnReturn": 3000,
        "returnCharge": 3000,
        "returnCenterCode": "RET",
        "outboundShippingPlaceCode": "12345678",
        "returnChargeName": "반품지",
        "companyContactNumber": "0200000000",
        "returnZipCode": "00000",
        "returnAddress": "서울",
        "vendorUserId": "sin945",
        "deliveryCompanyCode": "CJGLS",
        "brandState": "NO_BRAND",
        "brand": "HOMEZ",
    }
    base.update(overrides)
    return base


class _Response:
    def __init__(self, status_code=200, body=None, invalid_json=False, text=None):
        self.status_code = status_code
        self._body = body
        self._invalid_json = invalid_json
        # 2026-08-30 상태 파싱 결함 수정 회귀 테스트 — _snippet()이
        # response.text를 실제로 마스킹하는지 검증하려면 fake Response도
        # .text를 가져야 한다. 명시적으로 주지 않으면 body를 그대로
        # JSON 문자열화한다(실제 쿠팡 응답 바이트와 유사한 형태).
        self.text = (
            text if text is not None
            else ("" if body is None else json.dumps(body, ensure_ascii=False))
        )

    def json(self):
        if self._invalid_json:
            raise ValueError("invalid")
        return self._body


class _Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.response

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.response


class CoupangLiveProviderTestCase(unittest.TestCase):
    def _provider(self, response=None, error=None):
        session = _Session(response=response, error=error)
        provider = CoupangLiveProductProvider(
            {"vendor_id": "A0001", "access_key": "access", "secret_key": "secret"},
            session=session,
            now_factory=lambda: datetime(2026, 8, 26, tzinfo=timezone.utc),
        )
        return provider, session

    def test_success_requires_and_returns_seller_product_id(self):
        # 공식 문서(Product Creation) 실제 응답 구조 — sellerProductId는
        # response.data.data에 순수 숫자로 온다("sellerProductId" 키가
        # 아니다), 바깥 data는 {"code","message","data"} 객체다.
        provider, session = self._provider(
            _Response(body={
                "code": "200", "message": "",
                "data": {"code": "SUCCESS", "message": "", "data": 123456},
            }),
        )
        result = provider.create_product({"sellerProductName": "상품"})
        self.assertEqual(result.outcome, "SUBMITTED")
        self.assertEqual(result.external_reference, "123456")
        sent = session.calls[0][1]
        self.assertEqual(sent["json"]["vendorId"], "A0001")
        self.assertNotIn("secret", json.dumps(sent))

    def test_flat_success_with_scalar_data_returns_seller_product_id(self):
        # 2026-08-29 HOMEZ V7 Live 검증(실제 사용자 계정, 실제 상품
        # "국산 삼색 부직포 주방행주 40매 38x38cm")에서 실제로 관측된
        # 성공 응답 — 위 test_success_requires_and_returns_seller_
        # product_id()가 가정했던 "data가 다시 {"code","data"} 객체"
        # 중첩은 이번에 실제로 반증됐다. 실제 형태는 중첩 없이 바로
        # {"code":"SUCCESS","message":...,"data":<sellerProductId
        # 숫자>}이고, message에 성공과 무관한 경고(Brand Enrollment
        # 안내 등)가 함께 올 수 있다 — outcome은 여전히 SUBMITTED여야
        # 한다(경고는 error_summary가 아니라 그냥 무시, HOMEZ가
        # 자체적으로 재해석하지 않는다).
        provider, _session = self._provider(
            _Response(200, {
                "code": "SUCCESS",
                "message": (
                    "[Brand Name은 존재하지만 해당 셀러가 enroll하지 "
                    "않았습니다. Wing에서 Brand Enrollment를 완료해 "
                    "주세요.]"
                ),
                "data": 90000000001, "details": None, "errorItems": None,
            }),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "SUBMITTED")
        self.assertEqual(result.external_reference, "90000000001")

    def test_inner_code_not_success_is_failed_with_reason(self):
        # 2026-08-28 실사 발견 결함 재발 방지 — HTTP 200이어도 안쪽
        # data.code가 SUCCESS가 아니면 쿠팡이 실제로 거부한 것이므로
        # 모호한 UNKNOWN이 아니라 사유(message)와 함께 FAILED여야 한다.
        provider, _session = self._provider(
            _Response(200, {
                "code": "200", "message": "",
                "data": {
                    "code": "ERROR",
                    "message": "판매자 상품명이 정책에 위반됩니다.",
                    "data": None,
                },
            }),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "FAILED")
        self.assertEqual(result.error_code, "ERROR")
        self.assertEqual(result.error_summary, "판매자 상품명이 정책에 위반됩니다.")

    def test_flat_outer_error_is_failed_with_reason(self):
        # 2026-08-28 실사 확인 — 검증 실패 시 쿠팡은 HTTP 200이어도
        # 중첩 없이 바로 {"code":"ERROR","message":"...","data":null}을
        # 반환한다(실제 프로덕션에서 관측된 정확한 형태). 중첩을
        # 가정하지 않고 바깥 code만으로도 즉시 FAILED + 사유가 나와야
        # 한다.
        provider, _session = self._provider(
            _Response(200, {
                "code": "ERROR",
                "message": (
                    "'1 번 옵션 의 최대구매수량기간' 값을 확인해 주세요"
                    "|'vendorUserId' 값을 확인해 주세요"
                ),
                "data": None, "details": None, "errorItems": None,
            }),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "FAILED")
        self.assertEqual(result.error_code, "ERROR")
        self.assertIn("vendorUserId", result.error_summary)

    def test_client_rejection_is_failed(self):
        provider, _session = self._provider(
            _Response(400, {"code": "INVALID_REQUEST", "message": "bad field"}),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "FAILED")
        self.assertEqual(result.error_code, "INVALID_REQUEST")

    def test_timeout_and_server_error_are_unknown_without_retry(self):
        timeout_provider, timeout_session = self._provider(
            error=requests.Timeout(),
        )
        self.assertEqual(timeout_provider.create_product({}).outcome, "UNKNOWN")
        self.assertEqual(len(timeout_session.calls), 1)

        server_provider, server_session = self._provider(_Response(503, {}))
        self.assertEqual(server_provider.create_product({}).outcome, "UNKNOWN")
        self.assertEqual(len(server_session.calls), 1)

    def test_missing_id_and_invalid_json_are_unknown(self):
        missing, _session = self._provider(_Response(200, {
            "code": "200", "message": "",
            "data": {"code": "SUCCESS", "message": "", "data": None},
        }))
        invalid, _session2 = self._provider(_Response(200, invalid_json=True))
        self.assertEqual(missing.create_product({}).outcome, "UNKNOWN")
        self.assertEqual(missing.create_product({}).error_code, "MISSING_SELLER_PRODUCT_ID")
        self.assertEqual(invalid.create_product({}).outcome, "UNKNOWN")

    def test_status_query_returns_recognized_status_name(self):
        # 2026-08-29 Phase 4(V7-COUPANG-STATUS-001) — 실제 호출은
        # 하지 않는다. 이 테스트는 fake session으로만 검증한다.
        provider, session = self._provider(
            _Response(200, {"code": "200", "message": "", "data": {
                "sellerProductId": 123456, "statusName": "APPROVED",
            }}),
        )
        result = provider.get_product_status("123456")
        self.assertEqual(result.outcome, "FOUND")
        self.assertEqual(result.status_name, "APPROVED")
        sent_args = session.calls[0][0]
        self.assertIn("123456", sent_args[0])

    def test_status_query_404_is_not_found(self):
        provider, _session = self._provider(_Response(404, {}))
        result = provider.get_product_status("999999")
        self.assertEqual(result.outcome, "NOT_FOUND")

    def test_status_query_unrecognized_status_name_is_unknown(self):
        provider, _session = self._provider(
            _Response(200, {"code": "200", "message": "", "data": {
                "statusName": "SOME_FUTURE_STATUS",
            }}),
        )
        result = provider.get_product_status("123456")
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertEqual(result.error_code, "UNRECOGNIZED_STATUS")

    def test_status_query_timeout_is_unknown_without_retry(self):
        provider, session = self._provider(error=requests.Timeout())
        result = provider.get_product_status("123456")
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertEqual(result.error_code, "TIMEOUT")
        self.assertEqual(len(session.calls), 1)

    # 2026-08-30 V7 후속 안정화 — 상태 파싱 결함 수정 회귀 테스트.
    # 여기서 쓰는 sellerProductId("123456")는 이 파일의 기존 상태 조회
    # 테스트들이 이미 쓰던 합성 값이다. 실제 sellerProductId(감사 증거
    # 문서에만 보존, 코드에는 미기재)나 실제 전화번호/주소는 이 테스트의
    # 목적(statusName 매핑·마스킹 로직 검증)과 무관하므로 쓰지 않는다 — 합성 값으로도
    # 매핑/마스킹 동작을 동일하게 검증할 수 있고, 실제 값을 코드베이스에
    # 남기지 않는 편이 더 안전하다.

    def test_status_query_korean_approved_maps_to_canonical_and_preserves_raw(self):
        # 2026-08-30 읽기 전용 GET 1회(사용자 명시 승인)로 실제 확인된
        # 값 — 쿠팡이 statusName을 한글 "승인완료"로 돌려준다. 이전에는
        # STATUS_NAMES(영문만)에 없어 UNKNOWN/UNRECOGNIZED_STATUS로
        # 오판정됐다.
        provider, _session = self._provider(
            _Response(200, {"code": "200", "message": "", "data": {
                "sellerProductId": 123456, "statusName": "승인완료",
            }}),
        )
        result = provider.get_product_status("123456")
        self.assertEqual(result.outcome, "FOUND")
        self.assertEqual(result.status_name, "APPROVED")
        self.assertEqual(result.raw_status_name, "승인완료")

    def test_status_query_english_approved_still_maps_to_canonical_and_preserves_raw(self):
        # 기존(영문) 경로가 이번 수정으로 깨지지 않는지 확인 — canonical
        # 값도 원본 값도 그대로 "APPROVED"여야 한다(identity mapping).
        provider, _session = self._provider(
            _Response(200, {"code": "200", "message": "", "data": {
                "sellerProductId": 123456, "statusName": "APPROVED",
            }}),
        )
        result = provider.get_product_status("123456")
        self.assertEqual(result.outcome, "FOUND")
        self.assertEqual(result.status_name, "APPROVED")
        self.assertEqual(result.raw_status_name, "APPROVED")

    def test_status_query_unrecognized_korean_status_stays_unknown_fail_closed(self):
        # 이 지시의 명시적 금지 — "승인완료" 외 나머지 6개 상태의 실제
        # 한글 표기는 관측된 바 없으므로 추측해서 매핑하지 않는다. 이
        # 테스트의 "배송준비중"은 실제 관측값이 아니라 "추측 매핑이
        # 없으면 UNKNOWN으로 fail-closed 된다"만 증명하기 위한 가상의
        # 미인식 문자열이다. 다만 미인식이어도 원본 문자열
        # (raw_status_name)은 버리지 않고 보존해야 한다.
        provider, _session = self._provider(
            _Response(200, {"code": "200", "message": "", "data": {
                "statusName": "배송준비중",
            }}),
        )
        result = provider.get_product_status("123456")
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertEqual(result.error_code, "UNRECOGNIZED_STATUS")
        self.assertIsNone(result.status_name)
        self.assertEqual(result.raw_status_name, "배송준비중")

    def test_canonical_status_map_only_adds_confirmed_korean_value(self):
        # 회귀 방지 — 이후 누군가 나머지 6개 상태의 한글 표기를 실사
        # 없이 "추정"으로 채워 넣으면 이 테스트가 실패해야 한다.
        korean_keys = [k for k in CANONICAL_STATUS_MAP if k not in STATUS_NAMES]
        self.assertEqual(korean_keys, ["승인완료"])
        self.assertEqual(CANONICAL_STATUS_MAP["승인완료"], "APPROVED")

    # 2026-08-30 상태 응답 개인정보 마스킹 수정 — denylist(정규식으로
    # "그럴듯한 패턴"을 추측해 지우는 방식)가 아니라 allowlist(code/
    # message/sellerProductId/statusName만 옮겨 담기)로 바꾼 것을
    # 검증한다. 아래 합성 데이터는 실제 노출 사고(전화번호/주소/
    # 상세주소/우편번호)의 필드 "종류"만 재현한 가짜 값이며, 실제
    # sellerProductId(감사 증거 문서에만 보존, 코드에는 미기재)나 실제
    # 전화번호/주소는 쓰지 않는다 — allowlist는 필드 이름만으로 구조적으로 걸러내므로 합성 값으로도
    # 동일하게 검증되고, 실제 개인정보를 코드베이스에 다시 남기지
    # 않는 편이 더 안전하다. statusName을 미인식 값으로 둔 것은
    # raw_response_snippet이 채워지는 경로(UNKNOWN)를 타게 하기 위함일
    # 뿐, 실제 관측된 상태명이 아니다.
    _SYNTHETIC_LEAKY_STATUS_DATA = {
        "statusName": "SOME_FUTURE_STATUS",
        "sellerProductId": 999999999,
        "companyContactNumber": "01000009999",
        "returnAddress": "서울특별시 강남구 테스트로 12",
        "returnAddressDetail": "999호",
        "returnZipCode": "00000",
        "afterServiceContactNumber": "01099998888",
        "accessKey": "AKIAFAKE00000000TEST",
        "secretKey": "fakeSecretKeyForTestOnlyDoNotUse0000",
        "someBrandNewFieldCoupangMightAddLater": "미래에 추가될 수 있는 필드",
    }

    def _leaky_status_provider(self, message="테스트 문의 전화 01011112222 있음"):
        return self._provider(
            _Response(200, {
                "code": "SUCCESS", "message": message,
                "data": dict(self._SYNTHETIC_LEAKY_STATUS_DATA),
            }),
        )

    def test_status_query_does_not_leak_unmasked_phone_number(self):
        provider, _session = self._leaky_status_provider()
        result = provider.get_product_status("123456")
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertNotIn("01000009999", result.raw_response_snippet or "")
        self.assertNotIn("01099998888", result.raw_response_snippet or "")

    def test_status_query_does_not_leak_korean_address(self):
        provider, _session = self._leaky_status_provider()
        result = provider.get_product_status("123456")
        self.assertNotIn("서울특별시", result.raw_response_snippet or "")
        self.assertNotIn("강남구", result.raw_response_snippet or "")

    def test_status_query_does_not_leak_address_detail(self):
        provider, _session = self._leaky_status_provider()
        result = provider.get_product_status("123456")
        self.assertNotIn("999호", result.raw_response_snippet or "")

    def test_status_query_does_not_leak_zip_code(self):
        provider, _session = self._leaky_status_provider()
        result = provider.get_product_status("123456")
        # returnZipCode 값("00000") 자체가 너무 흔한 부분 문자열이라
        # 직접 in 검사는 오탐 위험이 있다 — 대신 JSON으로 파싱해 키가
        # 아예 없음을 구조적으로 확인한다.
        parsed = json.loads(result.raw_response_snippet or "{}")
        self.assertNotIn("returnZipCode", parsed.get("data", {}))

    def test_status_query_drops_unrecognized_future_data_field(self):
        # allowlist의 핵심 — denylist였다면 "이름을 모르는 새 필드"는
        # 절대 걸러낼 수 없다. allowlist는 이름을 몰라도 구조적으로
        # 빠진다.
        provider, _session = self._leaky_status_provider()
        result = provider.get_product_status("123456")
        self.assertNotIn("someBrandNewFieldCoupangMightAddLater", result.raw_response_snippet or "")
        self.assertNotIn("미래에 추가될 수 있는 필드", result.raw_response_snippet or "")

    def test_status_query_snippet_keeps_only_allowlisted_fields(self):
        provider, _session = self._leaky_status_provider(message="")
        result = provider.get_product_status("123456")
        parsed = json.loads(result.raw_response_snippet or "{}")
        self.assertEqual(set(parsed.keys()) - {"code", "message"}, {"data"})
        self.assertEqual(
            set(parsed["data"].keys()), {"sellerProductId", "statusName"},
        )
        self.assertEqual(parsed["data"]["sellerProductId"], 999999999)
        self.assertEqual(parsed["data"]["statusName"], "SOME_FUTURE_STATUS")

    def test_status_query_masks_phone_number_inside_message(self):
        provider, _session = self._leaky_status_provider(
            message="문의는 01011112222 로 연락 바랍니다",
        )
        result = provider.get_product_status("123456")
        self.assertNotIn("01011112222", result.raw_response_snippet or "")

    def test_status_query_drops_credential_shaped_strings(self):
        provider, _session = self._leaky_status_provider()
        result = provider.get_product_status("123456")
        snippet = result.raw_response_snippet or ""
        self.assertNotIn("AKIAFAKE00000000TEST", snippet)
        self.assertNotIn("fakeSecretKeyForTestOnlyDoNotUse0000", snippet)
        self.assertNotIn("accessKey", snippet)
        self.assertNotIn("secretKey", snippet)

    def test_status_query_message_with_data_address_value_is_redacted_via_known_value(self):
        # 2026-08-30 후속 지시 — "redact_free_text()만으로 주소를
        # 처리했다고 주장하지 않는다." 이 값은 전화번호/JWT 정규식
        # (redact_free_text)으로는 절대 잡히지 않는 순수 한글 주소
        # 문자열이다 — data에 있던 것과 message가 정확히 일치하는
        # known-value 경로로만 지워질 수 있다. 이게 "주소 정규식을
        # 새로 추가하지 않고도" 요구를 만족한다는 증거다. 합성 주소만
        # 쓴다.
        provider, _session = self._provider(
            _Response(200, {
                "code": "SUCCESS",
                "message": "반품지 서울특별시테스트구가짜로1 확인이 필요합니다.",
                "data": {
                    "statusName": "SOME_FUTURE_STATUS",
                    "returnAddress": "서울특별시테스트구가짜로1",
                },
            }),
        )
        result = provider.get_product_status("123456")
        self.assertNotIn(
            "서울특별시테스트구가짜로1", result.raw_response_snippet or "",
        )

    # ---- 2026-08-30 후속 지시 — 엄격한 성공 판정(create_product) ----

    def test_create_product_missing_top_level_code_is_unknown(self):
        # 과거 결함: None이 허용 튜플에 포함돼 있어 code가 아예 없는
        # 응답도 data만 있으면 SUBMITTED로 통과할 수 있었다. 이제는
        # UNKNOWN(MISSING_SUCCESS_CODE)이어야 한다 — 거부(FAILED)로도
        # 단정하지 않는다.
        provider, _session = self._provider(
            _Response(200, {"data": 90000000101}),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertEqual(result.error_code, "MISSING_SUCCESS_CODE")

    def test_create_product_null_code_is_unknown(self):
        provider, _session = self._provider(
            _Response(200, {"code": None, "data": 90000000102}),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertEqual(result.error_code, "MISSING_SUCCESS_CODE")

    def test_create_product_accepts_only_confirmed_success_codes(self):
        for code in ("SUCCESS", "200", 200):
            provider, _session = self._provider(
                _Response(200, {"code": code, "data": 90000000103}),
            )
            result = provider.create_product({})
            self.assertEqual(result.outcome, "SUBMITTED", f"code={code!r}")

    def test_seller_product_id_validator_boundary_matrix(self):
        # 2026-08-30 후속 지시(반복 지적) — 이전 버전은 문자열을
        # ""/"0"과만 비교해 "abc"/"12x"/"+1"/"-1"/"01.0" 같은 값을
        # 그대로 통과시켰다(실사 근거 없는 결함). _is_valid_seller_
        # product_id()를 직접 호출해 이 지시가 명시한 유효/차단 값을
        # 정확히 그대로 검증한다 — 정수/문자열의 모든 지정 경계값.
        validator = CoupangLiveProductProvider._is_valid_seller_product_id

        valid = [1, 2, 999999999, "1", "123"]
        for value in valid:
            self.assertTrue(validator(value), f"expected VALID for {value!r}")

        invalid = [
            0, -1, -999,           # 0, 음수
            True, False,           # bool
            1.0, 1.5, 0.0, -1.0,   # float(정수형 float 포함 — int가 아니다)
            "", " ", "\t",         # 빈 문자열과 공백
            "abc", "12x",          # 숫자가 아닌 문자가 섞임
            "+1", "-1",            # 부호
            "01.0",                # 소수점
            {}, {"x": 1},          # dict
            [], [1, 2],            # list
            None,                  # null
        ]
        for value in invalid:
            self.assertFalse(validator(value), f"expected INVALID for {value!r}")

    def test_create_product_seller_product_id_type_boundaries(self):
        # 위 단위 테스트가 검증기 자체를 정확히 확인했다면, 여기서는
        # create_product() 전체 경로에 실제로 배선돼 있는지(SUCCESS
        # code가 있어도 무효한 sellerProductId면 SUBMITTED가 아니라
        # UNKNOWN(MISSING_SELLER_PRODUCT_ID)로 이어지는지)만 확인한다.
        # dict는 이미 존재하는 별도 계약(중첩 봉투 {"code","data"})으로
        # 먼저 해석되므로(data.get("code")가 없으면 즉시 FAILED) 이
        # 경계 테스트와는 다른 분기라 따로 확인한다.
        invalid_scalar_values = [
            True, False, "", " ", 0, -1, "0", "-1", "+1", "01.0",
            "abc", "12x", [1, 2],
        ]
        for bad in invalid_scalar_values:
            provider, _session = self._provider(
                _Response(200, {"code": "SUCCESS", "data": bad}),
            )
            result = provider.create_product({})
            self.assertEqual(result.outcome, "UNKNOWN", f"value={bad!r}")
            self.assertEqual(result.error_code, "MISSING_SELLER_PRODUCT_ID")

        provider, _session = self._provider(
            _Response(200, {"code": "SUCCESS", "data": {"x": 1}}),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "FAILED")

        for good in ("90000000104", 90000000105):
            provider, _session = self._provider(
                _Response(200, {"code": "SUCCESS", "data": good}),
            )
            result = provider.create_product({})
            self.assertEqual(result.outcome, "SUBMITTED", f"value={good!r}")

    def test_create_product_success_with_message_captures_masked_warning(self):
        provider, _session = self._provider(
            _Response(200, {
                "code": "SUCCESS",
                "message": "[테스트] Brand Enrollment 확인이 필요합니다.",
                "data": 90000000106,
            }),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "SUBMITTED")
        self.assertEqual(
            result.warning_summary, "[테스트] Brand Enrollment 확인이 필요합니다.",
        )
        self.assertEqual(result.provider_response_code, "SUCCESS")

    def test_create_product_success_without_message_has_no_warning(self):
        provider, _session = self._provider(
            _Response(200, {"code": "SUCCESS", "message": "", "data": 90000000107}),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "SUBMITTED")
        self.assertIsNone(result.warning_summary)

    def test_create_product_message_with_payload_phone_and_address_is_redacted(self):
        # 2026-08-30 후속 지시 — 우리가 실제로 보낸 요청 payload의
        # 연락처/주소 값이 오류 message에 그대로 echo되면, 정규식
        # 추측이 아니라 우리가 이미 알고 있는 값과 정확히 일치하는
        # 부분만 지운다. 합성 값만 쓴다(실제 전화번호/주소 아님).
        payload = {
            "companyContactNumber": "0299998888",
            "returnAddress": "서울특별시 테스트구 가짜로 1",
            "returnAddressDetail": "1234호",
            "returnZipCode": "00000",
        }
        provider, _session = self._provider(
            _Response(200, {
                "code": "ERROR",
                "message": (
                    "전화번호 0299998888 형식과 주소 서울특별시 테스트구 "
                    "가짜로 1 1234호 우편번호 00000을(를) 확인해 주세요."
                ),
                "data": None,
            }),
        )
        result = provider.create_product(payload)
        self.assertEqual(result.outcome, "FAILED")
        self.assertNotIn("0299998888", result.error_summary or "")
        self.assertNotIn("서울특별시 테스트구 가짜로 1", result.error_summary or "")
        self.assertNotIn("1234호", result.error_summary or "")

    def test_create_product_snippet_excludes_details_errorItems_and_unknown_fields(self):
        provider, _session = self._provider(
            _Response(400, {
                "code": "ERROR",
                "message": "[테스트] 거부 사유",
                "data": None,
                "details": {"internal": "should not leak"},
                "errorItems": [{"field": "x", "reason": "y"}],
                "someBrandNewField": "should not leak either",
            }),
        )
        result = provider.create_product({})
        self.assertEqual(result.outcome, "FAILED")
        snippet = result.raw_response_snippet or ""
        self.assertNotIn("details", snippet)
        self.assertNotIn("errorItems", snippet)
        self.assertNotIn("should not leak", snippet)
        self.assertNotIn("someBrandNewField", snippet)

    def test_create_product_snippet_drops_credential_shaped_strings(self):
        provider, _session = self._provider(
            _Response(400, {
                "code": "ERROR", "message": "거부됨",
                "accessKey": "AKIAFAKE00000000TEST",
                "secretKey": "fakeSecretForTestOnly0000",
            }),
        )
        result = provider.create_product({})
        snippet = result.raw_response_snippet or ""
        self.assertNotIn("AKIAFAKE00000000TEST", snippet)
        self.assertNotIn("fakeSecretForTestOnly0000", snippet)
        self.assertNotIn("accessKey", snippet)


class CoupangLivePayloadTestCase(unittest.TestCase):
    def test_local_or_missing_image_is_fail_closed(self):
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품"},
            required_fields={
                "displayCategoryCode": 80754,
                "notices": LIVE_NOTICES,
                "items": [{"itemName": "기본", "externalVendorSku": "SKU"}],
            },
            channel_policy_attributes={},
        )
        self.assertIsNone(payload)
        self.assertIn("PUBLIC_IMAGE_URL_REQUIRED", blockers)

    def test_private_or_local_image_url_is_rejected(self):
        for image_url in (
            "http://127.0.0.1/image.png",
            "http://192.168.0.10/image.png",
            "https://localhost/image.png",
        ):
            _payload, blockers = build_coupang_live_payload(
                draft={"product_name": "상품"},
                required_fields={
                    "displayCategoryCode": 80754,
                    "notices": LIVE_NOTICES,
                    "items": [{"itemName": "기본", "externalVendorSku": "SKU"}],
                    "images": [{
                        "imageOrder": 0, "imageType": "REPRESENTATION",
                        "vendorPath": image_url,
                    }],
                    "liveImageRightsConfirmed": True,
                },
                channel_policy_attributes={},
            )
            self.assertIn("PUBLIC_IMAGE_URL_REQUIRED", blockers)

    def test_public_representation_image_builds_payload(self):
        required = {
            "displayCategoryCode": 80754,
            "notices": LIVE_NOTICES,
            "images": LIVE_IMAGES,
            "contents": LIVE_CONTENTS,
            "liveImageRightsConfirmed": True,
            "items": [{"itemName": "기본", "externalVendorSku": "SKU"}],
            "originalPrice": 12900,
            "salePrice": 12900,
            "maximumBuyCount": 10,
            "deliveryMethod": "SEQUENCIAL",
            "deliveryChargeType": "NOT_FREE",
            "deliveryCharge": 3000,
            "deliveryChargeOnReturn": 3000,
            "returnCharge": 3000,
            "returnCenterCode": "RET",
            "outboundShippingPlaceCode": "12345678",
            "returnChargeName": "반품지",
            "companyContactNumber": "0200000000",
            "returnZipCode": "00000",
            "returnAddress": "서울",
            "vendorUserId": "sin945",
            "deliveryCompanyCode": "CJGLS",
            "brandState": "NO_BRAND",
            "brand": "HOMEZ",
        }
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required,
            channel_policy_attributes=_live_channel_policy_attributes(),
        )
        self.assertEqual(blockers, [])
        self.assertEqual(payload["displayCategoryCode"], 80754)
        self.assertEqual(payload["items"][0]["images"], LIVE_IMAGES)

    def test_missing_contents_is_fail_closed(self):
        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 공식 문서(Product
        # Creation) 확인 결과 items[].contents는 필수(✓) 필드다. 실제
        # 백업 DB 증거(Wizard #6)에서도 이 상품의 contents가 비어
        # 있었다 — 조용히 빈 배열을 허용하던 결함의 재발 방지 테스트.
        required = _base_live_required_fields()
        del required["contents"]
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required, channel_policy_attributes={},
        )
        self.assertIsNone(payload)
        self.assertIn("CONTENTS_REQUIRED", blockers)

    def test_non_numeric_outbound_shipping_place_code_is_blocked(self):
        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — Section 3 필수
        # 시나리오 7번(잘못된 출고지 타입). 공식 문서 확인 결과
        # outboundShippingPlaceCode는 Number다 — 문자열이 숫자로
        # 파싱되지 않으면(공백·영문 등) 제출 직전에 명시적으로
        # 차단해야 하고, 예외를 삼켜 조용히 통과시키면 안 된다.
        required = _base_live_required_fields(outboundShippingPlaceCode="OS-INVALID")
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required, channel_policy_attributes={},
        )
        self.assertIsNone(payload)
        self.assertIn("OUTBOUND_SHIPPING_PLACE_CODE_INVALID", blockers)

    def test_whitespace_outbound_shipping_place_code_is_blocked(self):
        required = _base_live_required_fields(outboundShippingPlaceCode="   ")
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required, channel_policy_attributes={},
        )
        self.assertIsNone(payload)
        self.assertIn("OUTBOUND_SHIPPING_PLACE_CODE_REQUIRED", blockers)

    def test_numeric_string_outbound_shipping_place_code_is_coerced_to_int(self):
        required = _base_live_required_fields(outboundShippingPlaceCode="88001234")
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required,
            channel_policy_attributes=_live_channel_policy_attributes(),
        )
        self.assertEqual(blockers, [])
        self.assertEqual(payload["outboundShippingPlaceCode"], 88001234)
        self.assertIsInstance(payload["outboundShippingPlaceCode"], int)

    def test_manufacture_is_reflected_in_payload(self):
        required = _base_live_required_fields(manufacture="에브리홈즈")
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required,
            channel_policy_attributes=_live_channel_policy_attributes(),
        )
        self.assertEqual(blockers, [])
        self.assertEqual(payload["manufacture"], "에브리홈즈")

    def test_per_item_price_and_stock_override_multiple_options(self):
        # 2026-08-29 — Section 6 집중 테스트 4번("옵션 조합별 가격·
        # 재고·SKU"). 공식 문서 확인 결과 originalPrice/salePrice/
        # maximumBuyCount/unitCount는 items[] 각 항목 레벨 필드다.
        required = _base_live_required_fields(items=[
            {
                "itemName": "레드 40매", "externalVendorSku": "SKU-RED",
                "originalPrice": 12900, "salePrice": 10900,
                "maximumBuyCount": 5, "unitCount": 40,
            },
            {
                "itemName": "블루 40매", "externalVendorSku": "SKU-BLUE",
                "originalPrice": 13900, "salePrice": 11900,
                "maximumBuyCount": 8, "unitCount": 40,
            },
        ])
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required,
            channel_policy_attributes=_live_channel_policy_attributes(),
        )
        self.assertEqual(blockers, [])
        red, blue = payload["items"]
        self.assertEqual(red["salePrice"], 10900)
        self.assertEqual(red["maximumBuyCount"], 5)
        self.assertEqual(blue["salePrice"], 11900)
        self.assertEqual(blue["maximumBuyCount"], 8)

    def test_item_without_override_falls_back_to_product_level_price(self):
        required = _base_live_required_fields(items=[
            {"itemName": "기본", "externalVendorSku": "SKU-BASE"},
        ])
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required,
            channel_policy_attributes=_live_channel_policy_attributes(),
        )
        self.assertEqual(blockers, [])
        self.assertEqual(payload["items"][0]["salePrice"], required["salePrice"])

    def test_duplicate_sku_across_items_is_blocked(self):
        # 2026-08-29 — Section 6 집중 테스트 5번("중복 SKU 차단").
        required = _base_live_required_fields(items=[
            {"itemName": "레드 40매", "externalVendorSku": "SKU-DUP"},
            {"itemName": "블루 40매", "externalVendorSku": "SKU-DUP"},
        ])
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required, channel_policy_attributes={},
        )
        self.assertIsNone(payload)
        self.assertIn("DUPLICATE_SKU", blockers)

    def test_missing_required_exposed_purchase_option_blocks_payload(self):
        # 2026-08-29 — 실제 백업 DB 증거(Wizard #6)에서 확인된
        # V7-COUPANG-ATTR-001의 제출 직전 fail-closed 재확인.
        required = _base_live_required_fields()
        channel_policy_attributes = {
            "purchase_option_field_definitions": [{
                "attribute_type_name": "색상", "input_type": "SELECT",
                "input_values": ["레드", "블루", "삼색 혼합"],
                "required": True, "exposed": True,
            }],
            "purchase_options": {},
        }
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required,
            channel_policy_attributes=channel_policy_attributes,
        )
        self.assertIsNone(payload)
        self.assertTrue(any(b.startswith("PURCHASE_OPTION_REQUIRED") for b in blockers))

    def test_per_item_option_attributes_produce_distinct_payload_attributes(self):
        # 2026-08-29 — Section 3 필수 시나리오 2번(다중 옵션 정상 등록).
        # 각 item이 자기 자신의 optionAttributes를 가지면, payload의
        # 각 item.attributes[]가 서로 다른 값으로 정확히 분리돼야 한다
        # (공유 purchase_options 하나로 뭉뚱그리지 않는다).
        required = _base_live_required_fields(items=[
            {
                "itemName": "레드 40매", "externalVendorSku": "SKU-RED",
                "optionAttributes": {"색상": "레드", "구성": "40매"},
            },
            {
                "itemName": "블루 80매", "externalVendorSku": "SKU-BLUE",
                "optionAttributes": {"색상": "블루", "구성": "80매"},
            },
        ])
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required,
            channel_policy_attributes=_live_channel_policy_attributes(),
        )
        self.assertEqual(blockers, [])
        red, blue = payload["items"]
        self.assertEqual(
            {a["attributeTypeName"]: a["attributeValueName"] for a in red["attributes"]},
            {"색상": "레드", "구성": "40매"},
        )
        self.assertEqual(
            {a["attributeTypeName"]: a["attributeValueName"] for a in blue["attributes"]},
            {"색상": "블루", "구성": "80매"},
        )

    def test_valid_purchase_options_are_included_as_attributes(self):
        required = _base_live_required_fields()
        channel_policy_attributes = _live_channel_policy_attributes(
            purchase_option_field_definitions=[{
                "attribute_type_name": "색상", "input_type": "SELECT",
                "input_values": ["레드", "블루", "삼색 혼합"],
                "required": True, "exposed": True,
            }],
            purchase_options={"색상": "삼색 혼합"},
        )
        payload, blockers = build_coupang_live_payload(
            draft={"product_name": "상품", "brand": "HOMEZ"},
            required_fields=required,
            channel_policy_attributes=channel_policy_attributes,
        )
        self.assertEqual(blockers, [])
        self.assertIn(
            {"attributeTypeName": "색상", "attributeValueName": "삼색 혼합"},
            payload["items"][0]["attributes"],
        )


class _FakeProvider:
    def __init__(self, result=None, status_result=None):
        self.result = result or LiveSubmissionResult(
            outcome="SUBMITTED", external_reference="CP-123",
        )
        self.status_result = status_result
        self.calls = []
        self.status_calls = []

    def create_product(self, payload):
        self.calls.append(payload)
        return self.result

    def get_product_status(self, seller_product_id):
        self.status_calls.append(seller_product_id)
        return self.status_result


class ListingWizardLiveServiceTestCase(ListingWizardServiceTestCase):
    def _submitted_wizard(self, live_ready=False):
        candidate, _channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)
        if live_ready:
            entries = copy.deepcopy(json.loads(wizard.channel_selections_json))
            fields = entries[0]["required_fields"]
            fields["displayCategoryCode"] = 80754
            fields["notices"] = LIVE_NOTICES
            fields["images"] = LIVE_IMAGES
            fields["contents"] = LIVE_CONTENTS
            fields["liveImageRightsConfirmed"] = True
            # 2026-08-29 — 공식 문서 확인 결과 outboundShippingPlaceCode는
            # Number다. 기본 VALID_REQUIRED_FIELDS의 "OS001"은 그
            # 필드 자체를 테스트하지 않는 다른 다수 테스트를 위한
            # placeholder라 그대로 두고, 실제 쿠팡 전송 payload까지
            # 도달하는 이 fixture에서만 숫자 문자열로 교정한다.
            fields["outboundShippingPlaceCode"] = "88001"
            # 2026-08-30 V7 안정화 Phase 1 — 단일 제출 계약 통합 이후
            # vendorUserId/deliveryCompanyCode/브랜드 3상태도 실제
            # 전송 payload 조건이 됐다(감사 F-01/F-02). 실제 성공했던
            # 값과 동일한 모양으로 채운다.
            fields["vendorUserId"] = "sin945"
            fields["deliveryCompanyCode"] = "CJGLS"
            fields["brandState"] = "NO_BRAND"
            fields["brand"] = "HOMEZ"
            wizard.channel_selections_json = json.dumps(entries, ensure_ascii=False)
            self.db.commit()

        preview = self.service.approval_preview(wizard.id, self.company_id)
        approved = self.service.approve(
            wizard.id, self.company_id, approved_by=99,
            recent_auth_token=self._recent_auth_token(99),
            data=WizardApproveRequest(
                expected_version=preview.version,
                approval_nonce=preview.approval_nonce,
                expected_fingerprint=preview.fingerprint,
                product_image_match_confirmed=True,
            ),
        )
        submitted = self.service.submit(
            wizard.id, self.company_id, approved_by=99,
            data=WizardSubmitRequest(
                expected_version=approved.version, execution_mode="SUBMIT",
            ),
        )
        result = self.service.results(submitted.id, self.company_id).channels[0]
        return submitted, result

    def test_missing_public_image_blocks_at_precheck_before_approval(self):
        # 2026-08-30 V7 안정화(감사 F-01) — 단일 제출 계약 통합 이전에는
        # 대표 이미지가 없어도 7단계 사전검사(느슨한 Draft 검증만 수행)
        # 는 통과하고, 실제 전송 직전(build_coupang_live_payload)에서만
        # 막혔다. 지금은 두 지점이 같은 계약을 쓰므로 이미지가 없으면
        # 승인 자체가 불가능해야 한다 — 그 결함의 재발 방지 회귀
        # 테스트(이전 test_preflight_blocks_missing_public_image_
        # before_provider를 대체. preflight 시점이 아니라 그보다 훨씬
        # 이전인 사전검사 시점에 막히는 것이 이번 안정화의 핵심이다).
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        wizard = self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version,
                product_candidate_id=candidate.id,
            ),
        )
        wizard = self.service.update_draft(
            wizard.id, self.company_id,
            WizardDraftUpdateRequest(
                expected_version=wizard.version, product_name="테스트 상품",
                brand="HOMEZ", category="생활용품",
            ),
        )
        wizard = self.service.update_media(
            wizard.id, self.company_id,
            WizardMediaUpdateRequest(
                expected_version=wizard.version,
                selected_media_asset_ids=[media.id],
            ),
        )
        wizard = self.service.update_channels(
            wizard.id, self.company_id,
            WizardChannelsUpdateRequest(
                expected_version=wizard.version,
                marketplace_account_ids=[account.id],
            ),
        )
        self._cache_coupang_logistics(wizard.id)
        required_fields_without_image = dict(VALID_REQUIRED_FIELDS)
        del required_fields_without_image["images"]
        wizard = self.service.update_fulfillment(
            wizard.id, self.company_id,
            WizardFulfillmentUpdateRequest(
                expected_version=wizard.version,
                selections=[FulfillmentSelectionInput(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    outbound_shipping_place_code="88001",
                    return_center_code="RET-TEST-1",
                    required_fields=required_fields_without_image,
                    channel_policy_attributes=VALID_CHANNEL_POLICY_ATTRIBUTES,
                )],
            ),
        )
        wizard = self.service.update_economics(
            wizard.id, self.company_id,
            WizardEconomicsUpdateRequest(
                expected_version=wizard.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="5000", sale_price="9000",
                    channel_fee_rate="0.1",
                )],
            ),
        )
        result = self.service.validate(wizard.id, self.company_id, wizard.version)
        self.assertEqual(result.status, "NEEDS_CORRECTION")
        self.assertTrue(any(
            issue.code == "PUBLIC_IMAGE_URL_REQUIRED" for issue in result.issues
        ))

    def test_preflight_enforces_company_mode_approval_and_estop(self):
        wizard, result = self._submitted_wizard(live_ready=True)
        live = ListingWizardLiveService(self.db)
        self.assertTrue(live.preflight(
            wizard.id, result.submission_id, self.company_id,
        ).ready)
        with self.assertRaises(NotFoundException):
            live.preflight(wizard.id, result.submission_id, self.other_company_id)

        SafetyService(self.db).set_mode(
            AutomationMode.RECOMMEND_ONLY, set_by=1, is_admin=True,
        )
        blocked = live.preflight(wizard.id, result.submission_id, self.company_id)
        self.assertIn("OPERATOR_APPROVAL_MODE_REQUIRED", blocked.blockers)

        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )
        SafetyService(self.db).activate_emergency_stop(
            "test", set_by=1, is_admin=True,
        )
        blocked = live.preflight(wizard.id, result.submission_id, self.company_id)
        self.assertIn("ESTOP_ACTIVE", blocked.blockers)

    def test_success_persists_external_reference_and_duplicate_is_blocked(self):
        wizard, result = self._submitted_wizard(live_ready=True)
        provider = _FakeProvider()
        live = ListingWizardLiveService(self.db)
        sent = live.send(
            wizard.id, result.submission_id, self.company_id, provider,
        )
        self.assertEqual(sent.external_reference, "CP-123")
        self.assertTrue(sent.correlation_id)
        self.assertEqual(len(provider.calls), 1)
        submission = live.marketplace.get_submission_for_company(
            result.submission_id, self.company_id,
        )
        self.assertEqual(submission.correlation_id, sent.correlation_id)
        self.assertEqual(len(submission.request_fingerprint), 64)
        refreshed = self.service.results(wizard.id, self.company_id).channels[0]
        self.assertEqual(refreshed.status, "SUBMITTED")
        with self.assertRaises(ForbiddenException):
            live.send(wizard.id, result.submission_id, self.company_id, provider)
        self.assertEqual(len(provider.calls), 1)

    def test_persist_failure_after_provider_success_leaves_forensic_audit_trail(self):
        """
        2026-08-31 V7 필수 작업 2번(제출 장부 정합화 완성) 감사에서
        발견한 결함의 회귀 테스트 — create_product()가 이미 성공
        (sellerProductId 포함) 응답을 돌려준 뒤, 그 결과를 반영하는
        UPDATE 자체가 실패하면(디스크 오류 등) 그 sellerProductId는
        예외와 함께 완전히 사라지고 제출 행은 SUBMITTING에 영구히
        멈췄다 — 나중에 정합화할 근거가 DB 어디에도 남지 않았다.
        이제는 그 경우에도 감사 로그에 최소한의 증거(sellerProductId
        포함)가 남아야 한다.
        """
        wizard, result = self._submitted_wizard(live_ready=True)
        provider = _FakeProvider(
            result=LiveSubmissionResult(
                outcome="SUBMITTED", external_reference="CP-LOST-ON-PERSIST-FAILURE",
            ),
        )
        live = ListingWizardLiveService(self.db)

        call_count = {"n": 0}
        original_update = live.marketplace.update_submission_status_conditional

        def _flaky_update(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return original_update(*args, **kwargs)
            raise RuntimeError("SIMULATED_DISK_IO_ERROR")

        with patch.object(
            live.marketplace, "update_submission_status_conditional",
            side_effect=_flaky_update,
        ):
            with self.assertRaises(RuntimeError):
                live.send(
                    wizard.id, result.submission_id, self.company_id, provider,
                )

        # 상태를 임의로 SUBMITTED로 바꾸지 않는다 — SUBMITTING에 그대로
        # 남아 사람이 확인해야 한다는 신호를 유지한다(추정 금지).
        submission = live.marketplace.get_submission_for_company(
            result.submission_id, self.company_id,
        )
        self.assertEqual(submission.status, "SUBMITTING")
        self.assertIsNone(submission.external_submission_ref)

        rows = self.db.execute(
            text(
                "SELECT action, description FROM audit_logs "
                "WHERE entity_id = :entity_id "
                "AND action = 'COUPANG_PRODUCT_SUBMISSION_PERSIST_FAILED'",
            ),
            {"entity_id": str(result.submission_id)},
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn("CP-LOST-ON-PERSIST-FAILURE", rows[0][1])
        self.assertIn("SUBMITTED", rows[0][1])

    def test_success_with_warning_persists_and_surfaces_in_results(self):
        # 2026-08-30 후속 지시(성공 경고 보존) — 성공(SUBMITTED)이어도
        # 확인 필요 안내가 있으면 marketplace_submissions에 영구
        # 보존되고, results() API가 매번 그 DB 값을 다시 읽어 화면에
        # 돌려줘야 한다(화면을 다시 열어도 사라지지 않는다). 기존
        # error_reason과 섞이지 않는지도 함께 확인한다. 합성 값만
        # 쓴다.
        wizard, result = self._submitted_wizard(live_ready=True)
        provider = _FakeProvider(
            result=LiveSubmissionResult(
                outcome="SUBMITTED", external_reference="CP-WARN-001",
                warning_summary="[테스트] 브랜드 Enrollment 확인이 필요합니다.",
                provider_response_code="SUCCESS",
            ),
        )
        live = ListingWizardLiveService(self.db)
        sent = live.send(
            wizard.id, result.submission_id, self.company_id, provider,
        )
        self.assertEqual(sent.outcome, "SUBMITTED")
        self.assertEqual(
            sent.warning_summary, "[테스트] 브랜드 Enrollment 확인이 필요합니다.",
        )

        submission = live.marketplace.get_submission_for_company(
            result.submission_id, self.company_id,
        )
        self.assertEqual(submission.status, "SUBMITTED")
        self.assertIsNone(submission.error_reason)
        self.assertEqual(
            submission.provider_warning_summary,
            "[테스트] 브랜드 Enrollment 확인이 필요합니다.",
        )
        self.assertEqual(submission.provider_response_code, "SUCCESS")

        # 화면을 다시 여는 것과 같은 동작 — results()를 다시 호출해도
        # DB에서 매번 새로 읽어 동일한 값을 돌려줘야 한다.
        refreshed = self.service.results(wizard.id, self.company_id).channels[0]
        self.assertEqual(refreshed.status, "SUBMITTED")
        self.assertEqual(
            refreshed.provider_warning_summary,
            "[테스트] 브랜드 Enrollment 확인이 필요합니다.",
        )

    def test_provider_warning_summary_respects_company_isolation(self):
        # 회사 격리 — 새 필드도 기존 company_id 스코프를 그대로
        # 따른다(get_submission_for_company가 이미 company_id로
        # 필터링한다 — 새로 추가된 쿼리 경로 없음).
        wizard, result = self._submitted_wizard(live_ready=True)
        provider = _FakeProvider(
            result=LiveSubmissionResult(
                outcome="SUBMITTED", external_reference="CP-ISO-001",
                warning_summary="[테스트] 회사 A 전용 경고",
            ),
        )
        live = ListingWizardLiveService(self.db)
        live.send(wizard.id, result.submission_id, self.company_id, provider)

        cross_company = live.marketplace.get_submission_for_company(
            result.submission_id, self.other_company_id,
        )
        self.assertIsNone(cross_company)

    def test_check_status_blocks_when_never_submitted(self):
        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결(V7-COUPANG-STATUS-001
        # Service 연결) — 아직 쿠팡에 제출되어 sellerProductId를 받은
        # 적이 없으면 조회할 대상 자체가 없다.
        wizard, result = self._submitted_wizard(live_ready=True)
        live = ListingWizardLiveService(self.db)
        provider = _FakeProvider()
        with self.assertRaisesRegex(
            BadRequestException, "SELLER_PRODUCT_ID_NOT_AVAILABLE",
        ):
            live.check_status(
                wizard.id, result.submission_id, self.company_id, provider,
            )
        self.assertEqual(provider.status_calls, [])

    def test_check_status_queries_provider_with_persisted_seller_product_id(self):
        wizard, result = self._submitted_wizard(live_ready=True)
        live = ListingWizardLiveService(self.db)
        send_provider = _FakeProvider(
            result=LiveSubmissionResult(outcome="SUBMITTED", external_reference="CP-999"),
        )
        live.send(wizard.id, result.submission_id, self.company_id, send_provider)

        status_provider = _FakeProvider(
            status_result=ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )
        status = live.check_status(
            wizard.id, result.submission_id, self.company_id, status_provider,
        )
        self.assertEqual(status.status_name, "APPROVED")
        self.assertEqual(status_provider.status_calls, ["CP-999"])

    def test_check_status_partial_approved_is_not_treated_as_success(self):
        # 2026-08-29 — Section 3 필수 시나리오 11번(PARTIAL_APPROVED).
        # 알 수 없는/부분 상태를 성공으로 오판하지 않는지 확인한다.
        wizard, result = self._submitted_wizard(live_ready=True)
        live = ListingWizardLiveService(self.db)
        send_provider = _FakeProvider(
            result=LiveSubmissionResult(outcome="SUBMITTED", external_reference="CP-PARTIAL"),
        )
        live.send(wizard.id, result.submission_id, self.company_id, send_provider)
        status_provider = _FakeProvider(
            status_result=ProductStatusResult(outcome="FOUND", status_name="PARTIAL_APPROVED"),
        )
        status = live.check_status(
            wizard.id, result.submission_id, self.company_id, status_provider,
        )
        self.assertEqual(status.status_name, "PARTIAL_APPROVED")
        self.assertNotEqual(status.status_name, "APPROVED")

    def test_check_status_denied_is_reported_as_denied(self):
        # 2026-08-29 — Section 3 필수 시나리오 12번(DENIED).
        wizard, result = self._submitted_wizard(live_ready=True)
        live = ListingWizardLiveService(self.db)
        send_provider = _FakeProvider(
            result=LiveSubmissionResult(outcome="SUBMITTED", external_reference="CP-DENIED"),
        )
        live.send(wizard.id, result.submission_id, self.company_id, send_provider)
        status_provider = _FakeProvider(
            status_result=ProductStatusResult(outcome="FOUND", status_name="DENIED"),
        )
        status = live.check_status(
            wizard.id, result.submission_id, self.company_id, status_provider,
        )
        self.assertEqual(status.status_name, "DENIED")

    def test_check_status_unrecognized_status_is_never_treated_as_success(self):
        # "알 수 없는 상태는 성공으로 간주하지 않는다" — Provider
        # 자체는 이미 Phase 4에서 UNRECOGNIZED_STATUS를 처리하지만,
        # Service 계층에서도 이 결과를 그대로 통과시켜(성공으로
        # 조작하지 않고) 호출자가 판단하게 하는지 재확인한다.
        wizard, result = self._submitted_wizard(live_ready=True)
        live = ListingWizardLiveService(self.db)
        send_provider = _FakeProvider(
            result=LiveSubmissionResult(outcome="SUBMITTED", external_reference="CP-UNKNOWN"),
        )
        live.send(wizard.id, result.submission_id, self.company_id, send_provider)
        status_provider = _FakeProvider(
            status_result=ProductStatusResult(
                outcome="UNKNOWN", error_code="UNRECOGNIZED_STATUS",
            ),
        )
        status = live.check_status(
            wizard.id, result.submission_id, self.company_id, status_provider,
        )
        self.assertEqual(status.outcome, "UNKNOWN")
        self.assertIsNone(status.status_name)

    def test_concurrent_second_send_is_blocked_before_second_provider_call(self):
        wizard, result = self._submitted_wizard(live_ready=True)
        second_db = self.SessionLocal()
        second_live = ListingWizardLiveService(second_db)
        second_provider = _FakeProvider()

        class RacingProvider:
            def __init__(self):
                self.calls = 0

            def create_product(inner_self, payload):
                inner_self.calls += 1
                with self.assertRaises(ForbiddenException):
                    second_live.send(
                        wizard.id, result.submission_id,
                        self.company_id, second_provider,
                    )
                return LiveSubmissionResult(
                    outcome="SUBMITTED", external_reference="CP-RACE-WINNER",
                    http_status=200,
                )

        try:
            winner = RacingProvider()
            ListingWizardLiveService(self.db).send(
                wizard.id, result.submission_id, self.company_id, winner,
            )
            self.assertEqual(winner.calls, 1)
            self.assertEqual(second_provider.calls, [])
        finally:
            second_db.close()

    def test_expired_approval_blocks_before_provider(self):
        wizard, result = self._submitted_wizard(live_ready=True)
        live = ListingWizardLiveService(self.db)
        submission = live.marketplace.get_submission_for_company(
            result.submission_id, self.company_id,
        )
        approval = live.approvals.repository.get_latest_approval(
            submission.listing_id, submission.selection_id, self.company_id,
        )
        approval.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()
        provider = _FakeProvider()
        preflight = live.preflight(
            wizard.id, result.submission_id, self.company_id,
        )
        self.assertIn("VALID_APPROVAL_REQUIRED", preflight.blockers)
        with self.assertRaises(ForbiddenException):
            live.send(wizard.id, result.submission_id, self.company_id, provider)
        self.assertEqual(provider.calls, [])

    def test_wizard_payload_change_after_approval_blocks_before_provider(self):
        wizard, result = self._submitted_wizard(live_ready=True)
        draft = json.loads(wizard.draft_json)
        draft["product_name"] = "승인 이후 변경된 상품명"
        wizard.draft_json = json.dumps(draft, ensure_ascii=False)
        self.db.commit()

        provider = _FakeProvider()
        live = ListingWizardLiveService(self.db)
        preflight = live.preflight(
            wizard.id, result.submission_id, self.company_id,
        )
        self.assertIn("APPROVED_PAYLOAD_CHANGED", preflight.blockers)
        with self.assertRaises(ForbiddenException):
            live.send(
                wizard.id, result.submission_id, self.company_id, provider,
            )
        self.assertEqual(provider.calls, [])

    def test_ambiguous_result_is_unknown_and_never_automatically_retried(self):
        wizard, result = self._submitted_wizard(live_ready=True)
        provider = _FakeProvider(LiveSubmissionResult(
            outcome="UNKNOWN", error_code="TIMEOUT",
            error_summary="결과 확인 필요",
        ))
        live = ListingWizardLiveService(self.db)
        live.send(wizard.id, result.submission_id, self.company_id, provider)
        refreshed = self.service.results(wizard.id, self.company_id).channels[0]
        self.assertEqual(refreshed.status, "UNKNOWN")
        with self.assertRaises(ForbiddenException):
            live.send(wizard.id, result.submission_id, self.company_id, provider)
        self.assertEqual(len(provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
