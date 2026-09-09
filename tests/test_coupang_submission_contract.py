"""
=========================================================
Homez OS

File : tests/test_coupang_submission_contract.py

2026-08-30 V7 안정화 지시 — 필수 테스트 목록 중 이 파일이 다루는
항목: 1(7단계=최종 검증 동일), 2(vendorUserId 누락 차단), 3(택배사
코드 누락 차단), 4(빈 contents 배열 차단), 6(구매옵션 값/단위 오류
차단), 7(메타데이터 fingerprint 불일치 차단), 8(미확정 브랜드 차단),
9(공식 브랜드+Enrollment 저장), 15(실제 Provider가 테스트에서 절대
호출되지 않음 — FakeCoupangBrandProvider만 사용).

나머지(5, 10~14)는 다른 기존 파일이 이미 검증하거나(예: 10번 회사
격리는 tenant_isolation 계열 파일, 14번 회귀는 실제 DB 조회로) 이번
세션에서 별도로 다룬다 — 이 파일 하나로 15개를 전부 담지 않는다.
=========================================================
"""

import unittest

from app.domains.marketplace_listing.category_metadata import (
    notice_input_fingerprint,
)
from app.domains.marketplace_listing.coupang_brand_provider import (
    BrandSearchResult,
    FakeCoupangBrandProvider,
    brand_lookup_fingerprint,
)
from app.domains.marketplace_listing.coupang_live_payload import (
    build_coupang_live_payload,
)
from app.domains.marketplace_listing.coupang_submission_contract import (
    validate_coupang_submission_contract,
)

LIVE_IMAGES = [{
    "imageOrder": 0, "imageType": "REPRESENTATION",
    "vendorPath": "https://images.example.test/product.png",
}]
LIVE_NOTICES = [{
    "noticeCategoryName": "기타 재화",
    "noticeCategoryDetailNames": [{
        "noticeCategoryDetailName": "품명 및 모델명", "content": "테스트 상품",
    }],
}]
LIVE_CONTENTS = [{
    "contentsType": "TEXT",
    "contentDetails": [{"content": "테스트 상품 상세 설명", "detailType": "TEXT"}],
}]
NOTICE_ATTRIBUTES = {
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


def _valid_required_fields(**overrides):
    base = {
        "displayCategoryCode": 80754,
        "notices": LIVE_NOTICES,
        "images": LIVE_IMAGES,
        "contents": LIVE_CONTENTS,
        "liveImageRightsConfirmed": True,
        "items": [{"itemName": "기본", "externalVendorSku": "SKU-BASE"}],
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


DRAFT = {"product_name": "테스트 상품"}


class SingleSubmissionContractEquivalenceTestCase(unittest.TestCase):
    """필수 테스트 1번 — 7단계 사전검사와 최종 Payload 검증이 정확히
    같은 판정을 내려야 한다. 이 계약을 두 번 호출해(precheck가 하는
    것과 payload builder가 하는 것을 각각 모사) 결과가 항상 일치하는지
    직접 비교한다 — 둘이 서로 다른 함수를 부르지 않는다는 것 자체가
    이번 안정화의 핵심이므로, 같은 입력에 두 번 호출해도 동일 결과가
    나오는 결정성(deterministic)도 함께 확인한다."""

    def test_ready_and_not_ready_results_agree_between_two_call_sites(self):
        for required, channel_policy, expect_ready in (
            (_valid_required_fields(), NOTICE_ATTRIBUTES, True),
            (_valid_required_fields(vendorUserId=None), NOTICE_ATTRIBUTES, False),
        ):
            precheck_like = validate_coupang_submission_contract(
                draft=DRAFT, required_fields=required,
                channel_policy_attributes=channel_policy,
            )
            payload, blockers = build_coupang_live_payload(
                draft=DRAFT, required_fields=required,
                channel_policy_attributes=channel_policy,
            )
            self.assertEqual(precheck_like.ready, expect_ready)
            self.assertEqual(precheck_like.ready, payload is not None)
            self.assertEqual(
                sorted(precheck_like.blocking_codes), sorted(blockers),
            )


class RequiredFieldBlockingTestCase(unittest.TestCase):

    def test_missing_vendor_user_id_blocks(self):
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(vendorUserId=""),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertFalse(result.ready)
        self.assertIn("VENDOR_USER_ID_REQUIRED", result.blocking_codes)

    def test_missing_delivery_company_code_blocks(self):
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(deliveryCompanyCode=""),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertFalse(result.ready)
        self.assertIn("DELIVERY_COMPANY_CODE_REQUIRED", result.blocking_codes)

    def test_empty_contents_array_blocks(self):
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(contents=[]),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertFalse(result.ready)
        self.assertIn("CONTENTS_REQUIRED", result.blocking_codes)

    def test_purchase_option_wrong_value_blocks(self):
        field_definitions = [{
            "attribute_type_name": "색상", "input_type": "SELECT",
            "input_values": ["레드", "블루"], "required": True, "exposed": True,
        }]
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(),
            channel_policy_attributes={
                **NOTICE_ATTRIBUTES,
                "purchase_option_field_definitions": field_definitions,
                "purchase_options": {"색상": "존재하지않는색"},
            },
        )
        self.assertFalse(result.ready)
        self.assertIn("PURCHASE_OPTION_REQUIRED", result.blocking_codes)

    def test_metadata_fingerprint_mismatch_blocks(self):
        tampered = dict(NOTICE_ATTRIBUTES)
        tampered["notice_input_fingerprint"] = "tampered" + "0" * 56
        result = validate_coupang_submission_contract(
            draft=DRAFT, required_fields=_valid_required_fields(),
            channel_policy_attributes=tampered,
        )
        self.assertFalse(result.ready)
        self.assertIn("NOTICE_CONTRACT_INCOMPLETE", result.blocking_codes)


class ItemNameUnitCountContractTestCase(unittest.TestCase):
    """2026-08-31 Phase 7.6 — 쿠팡 공식 문서(Product Creation) 원문
    "Input for each item so that there is no overlap"로 확정된
    itemName 중복 금지 계약."""

    def test_duplicate_item_name_across_options_blocks(self):
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(items=[
                {
                    "itemName": "같은이름", "externalVendorSku": "SKU-A",
                    "optionAttributes": {"색상": "레드"},
                },
                {
                    "itemName": "같은이름", "externalVendorSku": "SKU-B",
                    "optionAttributes": {"색상": "블루"},
                },
            ]),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertFalse(result.ready)
        self.assertIn("DUPLICATE_ITEM_NAME", result.blocking_codes)

    def test_distinct_item_names_across_options_pass(self):
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(items=[
                {
                    "itemName": "레드", "externalVendorSku": "SKU-A",
                    "optionAttributes": {"색상": "레드"},
                },
                {
                    "itemName": "블루", "externalVendorSku": "SKU-B",
                    "optionAttributes": {"색상": "블루"},
                },
            ]),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertNotIn("DUPLICATE_ITEM_NAME", result.blocking_codes)


class BrandThreeStateContractTestCase(unittest.TestCase):

    def test_unresolved_brand_blocks(self):
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(brandState="UNRESOLVED"),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertFalse(result.ready)
        self.assertIn("BRAND_UNRESOLVED", result.blocking_codes)

    def test_official_brand_with_complete_lookup_data_passes_and_is_reflected(self):
        # 필수 테스트 9번 — 공식 브랜드 + Enrollment 상태가 실제로
        # payload에 반영되는지(brandId 포함) 확인한다. 조회 자체는
        # FakeCoupangBrandProvider로만 수행한다 — 실제 브랜드 검색
        # API는 이 테스트에서도, 다른 어떤 테스트에서도 호출되지
        # 않는다(필수 테스트 15번).
        provider = FakeCoupangBrandProvider({
            "홈즈": [BrandSearchResult(
                brand_id="BR-001", official_brand_name="HOMEZ 공식",
                enrollment_status="ENROLLED",
            )],
        })
        results = provider.search_brand("홈즈")
        self.assertEqual(provider.calls, ["홈즈"])
        chosen = results[0]
        fingerprint = brand_lookup_fingerprint("홈즈", chosen)

        required = _valid_required_fields(
            brandState="OFFICIAL_BRAND",
            brandId=chosen.brand_id,
            officialBrandName=chosen.official_brand_name,
            brandEnrollmentStatus=chosen.enrollment_status,
            brandLookupFingerprint=fingerprint,
        )
        result = validate_coupang_submission_contract(
            draft=DRAFT, required_fields=required,
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertTrue(result.ready, result.issues)

        payload, blockers = build_coupang_live_payload(
            draft=DRAFT, required_fields=required,
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertEqual(blockers, [])
        self.assertEqual(payload["brand"], "HOMEZ 공식")
        self.assertEqual(payload["brandId"], "BR-001")

    def test_official_brand_missing_lookup_evidence_blocks(self):
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(
                brandState="OFFICIAL_BRAND", brandId="BR-001",
                officialBrandName="HOMEZ 공식",
                # brandLookupFingerprint 누락 — 조회 없이 값만 위조해
                # 넣는 경로를 막는다.
            ),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertFalse(result.ready)
        self.assertIn("BRAND_OFFICIAL_DATA_INCOMPLETE", result.blocking_codes)


if __name__ == "__main__":
    unittest.main()
