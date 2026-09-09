import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.domains.marketplace_listing.category_metadata import (
    CategoryMetadata, NoticeFieldDefinition, metadata_fingerprint,
    notice_input_fingerprint, validate_notice_information,
    validate_purchase_options, validate_saved_notice_contract,
)
from app.domains.marketplace_listing.coupang_category_metadata_provider import (
    CoupangCategoryMetadataProvider,
)
from app.domains.marketplace_listing.listing_wizard_router import recommend_category


class CategoryMetadataContractTests(unittest.TestCase):
    def setUp(self):
        self.metadata = CategoryMetadata(
            display_category_code="12345",
            display_category_name="주방행주",
            version="v1",
            notice_fields=(
                NoticeFieldDefinition("기타 재화::품명 및 모델명", "품명 및 모델명"),
                NoticeFieldDefinition("기타 재화::제조국", "제조국"),
            ),
        )

    def test_missing_required_notice_is_rejected(self):
        self.assertEqual(
            validate_notice_information(self.metadata, {"기타 재화::제조국": "대한민국"}),
            ["기타 재화::품명 및 모델명"],
        )

    def test_complete_saved_contract_passes(self):
        notice = {
            "기타 재화::품명 및 모델명": "국산 삼색 부직포 주방행주 40매",
            "기타 재화::제조국": "대한민국",
        }
        meta_hash = metadata_fingerprint(self.metadata)
        attrs = {
            "official_category_code": "12345",
            "category_metadata_version": "v1",
            "category_metadata_fingerprint": meta_hash,
            "notice_information": notice,
            "notice_required_field_keys": list(notice),
            "notice_confirmed_at": "2026-08-24T00:00:00",
            "notice_confirmed_by_user_id": 1,
            "notice_input_fingerprint": notice_input_fingerprint(
                "12345", "v1", meta_hash, notice,
            ),
        }
        self.assertEqual(validate_saved_notice_contract(attrs), [])

    def test_changed_notice_invalidates_fingerprint(self):
        notice = {"기타 재화::제조국": "대한민국"}
        attrs = {
            "official_category_code": "12345", "category_metadata_version": "v1",
            "category_metadata_fingerprint": "meta", "notice_information": notice,
            "notice_required_field_keys": list(notice),
            "notice_confirmed_at": "x", "notice_confirmed_by_user_id": 1,
            "notice_input_fingerprint": notice_input_fingerprint("12345", "v1", "meta", notice),
        }
        attrs["notice_information"]["기타 재화::제조국"] = "미확인"
        self.assertIn("notice_input_fingerprint", validate_saved_notice_contract(attrs))

    def test_official_response_is_normalized_without_credentials(self):
        """
        2026-08-29 V7 종합 감사 Phase 4(V7-COUPANG-META-001/002 수정)
        — 이전에는 attributes[]에서 attributeTypeName/required만
        저장하고 dataType/basicUnit/usableUnits/inputType/inputValues/
        groupNumber/exposed를 전부 버렸다(감사에서 코드로 확인된
        결함). requiredDocumentNames/certifications 응답 자체도 아예
        저장하지 않았다. 이 fixture는 공식 문서(Category Metadata
        Query)가 실제로 반환하는 모양 그대로 응답을 구성해, 그 값들이
        전부 보존되는지 직접 검증한다 — 단순히 필드명만 바꿔 통과시킨
        것이 아니다.
        """
        provider = CoupangCategoryMetadataProvider({"access_key": "AK", "secret_key": "SK"})
        provider._request = lambda method, path, body=None: {
            "data": {
                "noticeCategories": [{
                    "noticeCategoryName": "기타 재화",
                    "noticeCategoryDetailNames": [{
                        "noticeCategoryDetailName": "품명 및 모델명",
                        "required": "MANDATORY",
                    }],
                }],
                "attributes": [{
                    "attributeTypeName": "수량",
                    "dataType": "NUMBER",
                    "basicUnit": "개",
                    "usableUnits": ["개", "세트"],
                    "required": "MANDATORY",
                    "inputType": "SELECT",
                    "inputValues": ["1개", "4개", "1세트"],
                    "groupNumber": "1",
                    "exposed": "EXPOSED",
                }],
                "requiredDocumentNames": [{
                    "templateName": "안전확인신고필증",
                    "required": "MANDATORY",
                }],
                "certifications": [{
                    "certificationType": "KC_KID_CERTIFICATION",
                    "name": "KC인증 어린이제품 안전인증",
                    "dataType": "CODE",
                    "required": "MANDATORY",
                }],
            },
        }
        metadata = provider.get("12345")
        self.assertEqual(metadata.notice_fields[0].key, "기타 재화::품명 및 모델명")
        self.assertTrue(metadata.notice_fields[0].required)

        option = metadata.purchase_option_fields[0]
        self.assertEqual(option.attribute_type_name, "수량")
        self.assertEqual(option.data_type, "NUMBER")
        self.assertEqual(option.basic_unit, "개")
        self.assertEqual(option.usable_units, ("개", "세트"))
        self.assertTrue(option.required)
        self.assertEqual(option.input_type, "SELECT")
        self.assertEqual(option.input_values, ("1개", "4개", "1세트"))
        self.assertEqual(option.group_number, "1")
        self.assertTrue(option.exposed)

        doc = metadata.required_documents[0]
        self.assertEqual(doc.template_name, "안전확인신고필증")
        self.assertEqual(doc.required, "MANDATORY")

        cert = metadata.certifications[0]
        self.assertEqual(cert.certification_type, "KC_KID_CERTIFICATION")
        self.assertEqual(cert.name, "KC인증 어린이제품 안전인증")
        self.assertEqual(cert.data_type, "CODE")
        self.assertEqual(cert.required, "MANDATORY")

        self.assertNotIn("AK", repr(metadata))
        self.assertNotIn("SK", repr(metadata))

    def test_non_exposed_attribute_is_distinguished_from_exposed(self):
        """exposed=NONE(검색전용옵션)은 exposed=False로 정확히
        구분돼야 한다 — 이전 코드는 이 구분 자체가 불가능했다."""

        provider = CoupangCategoryMetadataProvider({"access_key": "AK", "secret_key": "SK"})
        provider._request = lambda method, path, body=None: {
            "data": {
                "noticeCategories": [],
                "attributes": [
                    {
                        "attributeTypeName": "구매옵션색상",
                        "required": "MANDATORY", "inputType": "SELECT",
                        "inputValues": ["레드", "블루"], "exposed": "EXPOSED",
                    },
                    {
                        "attributeTypeName": "검색전용소재",
                        "required": "OPTIONAL", "inputType": "INPUT",
                        "exposed": "NONE",
                    },
                ],
            },
        }
        metadata = provider.get("12345")
        exposed_names = {
            opt.attribute_type_name
            for opt in metadata.purchase_option_fields if opt.exposed
        }
        self.assertEqual(exposed_names, {"구매옵션색상"})

    @patch("app.domains.product_candidate.repository.ProductCandidateRepository.get_visible_for_company")
    @patch("app.domains.marketplace_listing.listing_wizard_router._coupang_metadata_provider")
    @patch("app.domains.marketplace_listing.listing_wizard_router.ListingWizardService.get")
    def test_recommendation_uses_visible_candidate_product_name(
        self, get_wizard, provider_factory, get_candidate,
    ):
        get_wizard.return_value = SimpleNamespace(product_candidate_id=44)
        get_candidate.return_value = SimpleNamespace(
            product_name="국산 삼색 부직포 주방행주 40매",
            category_hint="주방용품", brand_hint="무브랜드",
        )
        provider = provider_factory.return_value
        provider.recommend.return_value = {
            "display_category_code": "12345",
            "display_category_name": "주방행주",
        }

        result = recommend_category(
            4, current_user=SimpleNamespace(company_id=1), db=object(),
        )

        provider.recommend.assert_called_once_with(
            "국산 삼색 부직포 주방행주 40매", "주방용품", "무브랜드",
        )
        self.assertEqual(result["display_category_code"], "12345")


class PurchaseOptionValidationTests(unittest.TestCase):
    """
    2026-08-29 쿠팡 상품등록 핵심 차단 해결(Section 6 집중 테스트
    1~3번) — 실제 백업 DB 증거(Wizard #6, 2026-08-26)에서 구매옵션이
    Category Metadata의 실제 attributeTypeName/inputType/inputValues와
    무관한 자유 텍스트로 저장된 것을 확인한 결함(V7-COUPANG-ATTR-001)
    의 직접 재발 방지 테스트다. 테스트 상품: 국산 삼색 부직포
    주방행주 40매 38x38cm(사용자 지정 실제 검증 상품).
    """

    def setUp(self):
        self.definitions = [
            {
                "attribute_type_name": "색상", "input_type": "SELECT",
                "input_values": ["레드", "블루", "삼색 혼합"],
                "required": True, "exposed": True,
            },
            {
                "attribute_type_name": "크기", "input_type": "INPUT",
                "input_values": [], "required": True, "exposed": True,
            },
            {
                # exposed=False(검색전용옵션)는 구매옵션이 아니므로
                # 필수여도 검사 대상에서 제외해야 한다.
                "attribute_type_name": "검색전용소재", "input_type": "INPUT",
                "input_values": [], "required": True, "exposed": False,
            },
            {
                # required=False는 비어 있어도 통과해야 한다.
                "attribute_type_name": "선택옵션", "input_type": "INPUT",
                "input_values": [], "required": False, "exposed": True,
            },
        ]

        # 2026-08-29 Live 검증 재발 방지 — 단위(usable_units)가 있는
        # NUMBER 속성 전용 fixture(기존 self.definitions와 분리 —
        # 다른 테스트의 missing 목록을 건드리지 않기 위함). 공식 문서
        # 원문 예시가 {"attributeTypeName": "수량", "attributeValueName":
        # "1개"}이므로, 값에 단위가 포함된 형태를 받아들여야 한다
        # (실제 재현: 순수 "40"만 요구하면 실제 쿠팡 라이브 제출이
        # 거부됨 — Wizard #6, 2026-08-29 실제 오류 응답으로 확인).
        self.number_unit_definitions = [{
            "attribute_type_name": "수량", "input_type": "INPUT",
            "data_type": "NUMBER", "usable_units": ["개", "박스", "세트"],
            "input_values": [], "required": True, "exposed": True,
        }]

    def test_input_type_free_text_is_accepted(self):
        missing = validate_purchase_options(self.definitions, {
            "색상": "삼색 혼합", "크기": "38x38cm",
        })
        self.assertEqual(missing, [])

    def test_select_type_rejects_value_outside_official_input_values(self):
        missing = validate_purchase_options(self.definitions, {
            "색상": "노랑", "크기": "38x38cm",
        })
        self.assertEqual(missing, ["색상"])

    def test_select_type_accepts_official_input_value(self):
        missing = validate_purchase_options(self.definitions, {
            "색상": "삼색 혼합", "크기": "38x38cm",
        })
        self.assertNotIn("색상", missing)

    def test_missing_required_exposed_attribute_is_blocked(self):
        missing = validate_purchase_options(self.definitions, {"색상": "레드"})
        self.assertEqual(missing, ["크기"])

    def test_non_exposed_required_attribute_is_not_checked(self):
        # "검색전용소재"는 required=True지만 exposed=False라 실제
        # 구매옵션이 아니다 — 값이 없어도 차단하면 안 된다.
        missing = validate_purchase_options(self.definitions, {
            "색상": "레드", "크기": "38x38cm",
        })
        self.assertNotIn("검색전용소재", missing)

    def test_non_required_attribute_missing_does_not_block(self):
        missing = validate_purchase_options(self.definitions, {
            "색상": "레드", "크기": "38x38cm",
        })
        self.assertNotIn("선택옵션", missing)

    def test_empty_purchase_options_blocks_all_required_exposed_fields(self):
        missing = validate_purchase_options(self.definitions, {})
        self.assertEqual(sorted(missing), ["색상", "크기"])

    def test_number_field_accepts_value_with_official_unit_suffix(self):
        """공식 문서 원문 예시({"attributeTypeName": "수량",
        "attributeValueName": "1개"})와 동일한 형태 — 값 끝에
        usable_units 중 하나가 붙어 있으면 통과해야 한다."""

        missing = validate_purchase_options(
            self.number_unit_definitions, {"수량": "40개"},
        )
        self.assertEqual(missing, [])

    def test_number_field_still_accepts_bare_number_without_unit(self):
        """단위 없이 순수 숫자만 보내는 기존 동작도 계속 허용한다
        (하위 호환 — usable_units가 선택 사항일 수 있는 카테고리도
        있으므로 단위 접미사를 강제하지 않는다)."""

        missing = validate_purchase_options(
            self.number_unit_definitions, {"수량": "40"},
        )
        self.assertEqual(missing, [])

    def test_number_field_rejects_non_numeric_value(self):
        missing = validate_purchase_options(
            self.number_unit_definitions, {"수량": "많음"},
        )
        self.assertEqual(missing, ["수량"])

    def test_number_field_rejects_unit_only_value(self):
        """단위만 있고 숫자 부분이 없으면(예: "개") 여전히 거부해야
        한다 — 접미사를 뗀 나머지가 빈 문자열이라 float() 실패."""

        missing = validate_purchase_options(
            self.number_unit_definitions, {"수량": "개"},
        )
        self.assertEqual(missing, ["수량"])


if __name__ == "__main__":
    unittest.main()
