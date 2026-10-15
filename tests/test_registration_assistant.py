"""
=========================================================
Homez OS

File : tests/test_registration_assistant.py

등록 도우미(2026-10-14) 계산부와 배선을 고정한다. 네트워크·DB 없음.

고정하는 규칙:
- 값을 추측하지 않는다 — 온채널 값·이전 확정 값에서만 채우고, 못 채운 필수 칸은 미해결로 남겨 저장을 막는다.
- 옵션 조합마다 아이템 1개, SKU는 유일하다.
- 마진 18% 미달 가격은 BLOCKER로 막는다(8,710원 사례).
- 어린이 연상 표현은 키워드에서 빼고 주의점으로 남긴다.
=========================================================
"""

import unittest
from decimal import Decimal
from pathlib import Path

from app.domains.marketplace_listing.registration_assistant import AssistantRequest
from app.domains.marketplace_listing.registration_assistant import build_items
from app.domains.marketplace_listing.registration_assistant import build_proposal
from app.domains.marketplace_listing.registration_assistant import choose_notice_group
from app.domains.marketplace_listing.registration_assistant import keywords_from_onch_rows
from app.domains.marketplace_listing.registration_assistant import map_notice_information
from app.domains.marketplace_listing.registration_assistant import margin_check
from app.domains.marketplace_listing.registration_assistant import notes_payload
from app.domains.marketplace_listing.registration_assistant import parse_manufacture_month
from app.domains.marketplace_listing.registration_assistant import pick_seller_defaults
from app.domains.marketplace_listing.registration_assistant import safe_keywords

ROOT = Path(__file__).resolve().parents[1]

NOTICE_FIELDS = [
    {"key": f"의류::{label}", "label": label, "required": True, "allow_not_applicable": False}
    for label in (
        "제품 소재", "색상", "치수", "제조자(수입자)", "제조국", "세탁방법 및 취급시 주의사항", "제조연월",
        "품질보증기준", "A/S 책임자와 전화번호",
    )
]
OPTION_FIELDS = [
    {"attribute_type_name": "색상", "required": True, "exposed": True, "input_type": "INPUT", "input_values": [], "data_type": "STRING"},
    {"attribute_type_name": "사이즈", "required": True, "exposed": True, "input_type": "INPUT", "input_values": [], "data_type": "STRING"},
]
METADATA = {
    "display_category_code": "69240", "display_category_name": "위생/생리팬티", "metadata_version": "v1",
    "metadata_fingerprint": "fp", "notice_fields": NOTICE_FIELDS, "purchase_option_fields": OPTION_FIELDS,
}
ONCH_ROWS = [
    ("추천키워드 키워드복사", "#방수 생리팬티 #여성팬티 #여아팬티 #초경 #초등생리팬티 #순면팬티"),
    ("상품정보제공고시 구분", "의류"), ("제품소재", "면"), ("KC 인증유형", "해당사항없음"), ("색상", "블랙,스킨"), ("치수", "M,L,XL"),
    ("제조자/수입자", "유메이글로벌"), ("제조국", "중국"),
    ("세탁방법 및 취급시 주의사항", "찬물에 단독세탁 권장"), ("제조년월일", "2022-03-05"),
    ("품질보증기준", "소비자분쟁해결기준에 따라 보상"),
]
PRIOR_FIELDS = {
    "deliveryMethod": "SEQUENCIAL", "deliveryChargeType": "FREE", "deliveryCharge": 0,
    "deliveryChargeOnReturn": 2500, "returnCharge": 2500, "deliveryCompanyCode": "CJGLS", "vendorUserId": "sin945",
    "brandState": "NO_BRAND", "brand": "브랜드 없음", "brandId": "", "someOtherKey": "x",
}
PRIOR_NOTICE = {"스포츠용품::A/S 책임자와 전화번호": "에브리홈즈 / 01000000000", "스포츠용품::색상": "블랙"}
DRAFT = {
    "product_name": "방수 생리팬티 위생팬티",
    "options": [{"name": "색상", "values": ["블랙", "스킨"]}, {"name": "사이즈", "values": ["M", "L", "XL"]}],
}


def request(**overrides):
    base = dict(
        display_category_code="69240", sale_price=10130, stock=10, unit_cost=3700, shipping_cost=3000,
        image_rights_confirmed=True, images=[{"imageOrder": 0, "imageType": "REPRESENTATION", "vendorPath": "https://x/y.png"}],
        contents=[{"contentsType": "TEXT"}], confirm_notice=True,
    )
    base.update(overrides)
    return AssistantRequest(**base)


def proposal(req=None, defaults=None, rows=None, draft=None, outbound="25330376", ret="1002691662"):
    return build_proposal(
        wizard_draft=draft or DRAFT, candidate_id=6, source_reference="ONCHANNEL:CH1837300", account_id=1,
        onch_rows=rows or ONCH_ROWS, metadata=METADATA,
        seller_defaults=defaults if defaults is not None else pick_seller_defaults(PRIOR_FIELDS, PRIOR_NOTICE),
        outbound_code=outbound, return_center_code=ret, request=req or request(),
    )


def codes(p):
    return {n.code for n in p.notes}


class PureHelperTests(unittest.TestCase):

    def test_manufacture_month_is_formatted_and_unreadable_values_are_kept(self):
        self.assertEqual(parse_manufacture_month("2022-03-05")[0], "2022년 03월")
        self.assertEqual(parse_manufacture_month("미상"), ("미상", None))
        self.assertEqual(parse_manufacture_month("2022-13-01")[1], None)

    def test_seller_defaults_take_only_seller_level_values(self):
        picked = pick_seller_defaults(PRIOR_FIELDS, PRIOR_NOTICE)
        self.assertEqual(picked["deliveryCompanyCode"], "CJGLS")
        self.assertEqual(picked["brandState"], "NO_BRAND")
        self.assertNotIn("someOtherKey", picked)
        self.assertIn("as책임자와전화번호", picked["_seller_notice"])
        self.assertNotIn("색상", picked["_seller_notice"])

    def test_brand_is_not_copied_unless_it_was_a_confirmed_no_brand(self):
        picked = pick_seller_defaults({**PRIOR_FIELDS, "brandState": "OFFICIAL_BRAND"}, {})
        self.assertNotIn("brandState", picked)

    def test_child_terms_leave_the_keywords_and_are_reported(self):
        kept, dropped = safe_keywords(keywords_from_onch_rows(ONCH_ROWS))
        self.assertEqual(kept, ["방수 생리팬티", "여성팬티", "순면팬티"])
        self.assertEqual(set(dropped), {"여아팬티", "초경", "초등생리팬티"})

    def test_margin_is_measured_on_the_sale_price_not_on_cost(self):
        low = margin_check(request(sale_price=8710))
        self.assertAlmostEqual(float(low[0]), 633.82, places=1)
        self.assertLess(low[1], Decimal("0.18"))
        high = margin_check(request(sale_price=10130))
        self.assertGreaterEqual(high[1], Decimal("0.18"))
        self.assertIsNone(margin_check(request(unit_cost=None)))


class NoticeMappingTests(unittest.TestCase):

    def map(self, seller=None, overrides=None, rows=None):
        return map_notice_information(NOTICE_FIELDS, rows or ONCH_ROWS, seller if seller is not None else {"as책임자와전화번호": "에브리홈즈 / 010"}, overrides or {}, "의류")

    def test_values_come_from_the_onch_page_or_the_seller_and_nothing_is_guessed(self):
        info, missing, _ = self.map()
        self.assertEqual(info["의류::제품 소재"], "면")
        self.assertEqual(info["의류::제조자(수입자)"], "유메이글로벌")
        self.assertEqual(info["의류::제조국"], "중국")
        self.assertEqual(info["의류::제조연월"], "2022년 03월")
        self.assertEqual(info["의류::A/S 책임자와 전화번호"], "에브리홈즈 / 010")
        self.assertEqual(missing, [])

    def test_a_field_with_no_source_is_unresolved_not_invented(self):
        rows = [r for r in ONCH_ROWS if r[0] != "품질보증기준"]
        info, missing, _ = self.map(rows=rows)
        self.assertEqual(missing, ["의류::품질보증기준"])
        self.assertNotIn("의류::품질보증기준", info)

    def test_no_seller_value_means_the_as_field_stays_unresolved(self):
        _, missing, _ = self.map(seller={})
        self.assertEqual(missing, ["의류::A/S 책임자와 전화번호"])

    def test_user_override_wins(self):
        info, _, _ = self.map(overrides={"의류::제품 소재": "모달 95%, 스판 5%"})
        self.assertEqual(info["의류::제품 소재"], "모달 95%, 스판 5%")

    def test_an_old_manufacture_date_raises_a_caution(self):
        _, _, notes = self.map()
        self.assertIn("STALE_MANUFACTURE_DATE", {n.code for n in notes})


class ItemTests(unittest.TestCase):

    def test_one_item_per_option_combination_with_unique_skus(self):
        items, unresolved, _ = build_items(DRAFT["options"], OPTION_FIELDS, sku_prefix="PC6-CH1", sale_price=10130, stock=10)
        self.assertEqual(unresolved, [])
        self.assertEqual(len(items), 6)
        self.assertEqual(len({i["externalVendorSku"] for i in items}), 6)
        self.assertEqual(items[0]["optionAttributes"], {"색상": "블랙", "사이즈": "M"})
        self.assertTrue(all(i["salePrice"] == 10130 and i["maximumBuyCount"] == 10 for i in items))

    def test_a_required_purchase_option_with_no_draft_option_is_unresolved(self):
        _, unresolved, _ = build_items([{"name": "색상", "values": ["블랙"]}], OPTION_FIELDS, sku_prefix="P", sale_price=1, stock=1)
        self.assertEqual(unresolved, ["구매옵션:사이즈"])

    def test_an_option_outside_the_category_definitions_is_unresolved(self):
        _, unresolved, _ = build_items(
            [{"name": "색상", "values": ["블랙"]}, {"name": "사이즈", "values": ["M"]}, {"name": "무늬", "values": ["a"]}],
            OPTION_FIELDS, sku_prefix="P", sale_price=1, stock=1,
        )
        self.assertIn("구매옵션 정의에 없는 옵션:무늬", unresolved)

    def test_select_values_must_be_in_the_allowed_list(self):
        definitions = [{**OPTION_FIELDS[0], "input_type": "SELECT", "input_values": ["블랙"]}, OPTION_FIELDS[1]]
        _, unresolved, _ = build_items([{"name": "색상", "values": ["핑크"]}, {"name": "사이즈", "values": ["M"]}], definitions, sku_prefix="P", sale_price=1, stock=1)
        self.assertEqual(unresolved, ["구매옵션 허용값 밖:색상=핑크"])


class MultiNoticeGroupTests(unittest.TestCase):
    """실제 카테고리 69240은 고시 카테고리가 여러 개(기타 재화·어린이제품·의류·패션잡화)다 — 하나만 고른다."""

    FIELDS = NOTICE_FIELDS + [
        {"key": "어린이제품::사용연령", "label": "사용연령", "required": True},
        {"key": "기타 재화::인증/허가 사항", "label": "인증/허가 사항", "required": True},
    ]

    def test_the_group_equal_to_the_onch_classification_is_chosen(self):
        group, note = choose_notice_group(self.FIELDS, ONCH_ROWS, None)
        self.assertEqual(group, "의류")
        self.assertEqual(note.code, "NOTICE_GROUP_FROM_ONCH")

    def test_no_match_means_blocker_not_a_guess(self):
        rows = [r for r in ONCH_ROWS if r[0] != "상품정보제공고시 구분"]
        group, note = choose_notice_group(self.FIELDS, rows, None)
        self.assertIsNone(group)
        self.assertEqual(note.level, "BLOCKER")

    def test_an_explicit_request_wins_and_an_unknown_one_blocks(self):
        self.assertEqual(choose_notice_group(self.FIELDS, ONCH_ROWS, "어린이제품")[0], "어린이제품")
        self.assertIsNone(choose_notice_group(self.FIELDS, ONCH_ROWS, "없는그룹")[0])

    def test_only_the_chosen_group_is_saved_and_other_groups_do_not_block(self):
        metadata = {**METADATA, "notice_fields": self.FIELDS}
        p = build_proposal(
            wizard_draft=DRAFT, candidate_id=6, source_reference="ONCHANNEL:CH1837300", account_id=1,
            onch_rows=ONCH_ROWS, metadata=metadata, seller_defaults=pick_seller_defaults(PRIOR_FIELDS, PRIOR_NOTICE),
            outbound_code="1", return_center_code="2", request=request(),
        )
        self.assertTrue(p.ready, p.unresolved)
        attrs = p.selection["channel_policy_attributes"]
        self.assertEqual(attrs["notice_information"]["__notice_category_name"], "의류")
        self.assertTrue(all(k.startswith("의류::") for k in attrs["notice_required_field_keys"]))
        self.assertFalse(any(k.startswith(("어린이제품", "기타 재화")) for k in attrs["notice_information"] if k != "__notice_category_name"))


class OptionNameMappingTests(unittest.TestCase):

    DEFS = [OPTION_FIELDS[0], {**OPTION_FIELDS[1], "attribute_type_name": "패션의류/잡화 사이즈", "input_type": "SELECT",
                               "input_values": ["M", "L", "XL"]}]

    def test_a_unique_suffix_match_is_mapped_and_reported(self):
        items, unresolved, aliases = build_items(DRAFT["options"], self.DEFS, sku_prefix="P", sale_price=1, stock=1)
        self.assertEqual(unresolved, [])
        self.assertEqual(aliases, {"사이즈": "패션의류/잡화 사이즈"})
        self.assertEqual(items[0]["optionAttributes"], {"색상": "블랙", "패션의류/잡화 사이즈": "M"})

    def test_an_ambiguous_match_is_not_guessed(self):
        defs = self.DEFS + [{**OPTION_FIELDS[1], "attribute_type_name": "속옷 사이즈"}]
        _, unresolved, aliases = build_items(DRAFT["options"], defs, sku_prefix="P", sale_price=1, stock=1)
        self.assertEqual(aliases, {})
        self.assertIn("구매옵션 정의에 없는 옵션:사이즈", unresolved)

    def test_mapped_values_are_still_checked_against_the_allowed_list(self):
        draft = [{"name": "색상", "values": ["블랙"]}, {"name": "사이즈", "values": ["XXL"]}]
        _, unresolved, _ = build_items(draft, self.DEFS, sku_prefix="P", sale_price=1, stock=1)
        self.assertEqual(unresolved, ["구매옵션 허용값 밖:패션의류/잡화 사이즈=XXL"])


class ProposalTests(unittest.TestCase):

    def test_a_complete_input_is_ready_and_carries_the_confirmation_code(self):
        p = proposal()
        self.assertTrue(p.ready, p.unresolved)
        self.assertEqual(p.item_count, 6)
        selection = p.selection
        self.assertEqual(selection["fulfillment_mode"], "SELLER_FULFILLED")
        self.assertEqual(selection["channel_policy_confirmed_evidence_rule_codes"], ["CATEGORY_NOTICE_INFO_REQUIRED"])
        attrs = selection["channel_policy_attributes"]
        self.assertEqual(attrs["origin_country"], "CHINA")
        self.assertEqual(attrs["official_category_code"], "69240")
        self.assertEqual(attrs["product_identifier"], "PC6-CH1837300-01")
        self.assertEqual(selection["required_fields"]["brandState"], "NO_BRAND")
        self.assertEqual(selection["required_fields"]["deliveryCompanyCode"], "CJGLS")
        self.assertEqual(selection["required_fields"]["displayCategoryCode"], 69240)

    def test_purchase_options_are_filled_for_the_policy_rule_even_with_per_item_options(self):
        # 채널 정책 PRODUCT_IDENTIFICATION_REQUIRED는 purchase_options가 비면 제출을 막는다(2026-10-15 #14).
        attrs = proposal().selection["channel_policy_attributes"]
        self.assertEqual(attrs["purchase_options"], {"색상": "블랙", "사이즈": "M"})

    def test_the_notice_confirmation_is_not_added_unless_the_user_asked(self):
        p = proposal(request(confirm_notice=False))
        self.assertEqual(p.selection["channel_policy_confirmed_evidence_rule_codes"], [])

    def test_missing_rights_images_and_contents_block_saving(self):
        p = proposal(request(image_rights_confirmed=False, images=[], contents=[]))
        self.assertFalse(p.ready)
        self.assertEqual(
            {"이미지 사용권 확인(사용자 선언)", "대표 이미지 공개 주소(R2 업로드)", "상세설명 contents(R2 업로드)"} - set(p.unresolved), set(),
        )

    def test_no_prior_success_means_the_seller_values_are_unresolved_not_guessed(self):
        p = proposal(defaults={})
        self.assertFalse(p.ready)
        self.assertIn("판매자 공통:deliveryCompanyCode", p.unresolved)
        self.assertIn("브랜드 확인(이전 무브랜드 기록 없음)", p.unresolved)

    def test_missing_logistics_places_block_saving(self):
        self.assertIn("출고지·반품지 선택", proposal(outbound=None).unresolved)

    def test_an_unknown_origin_is_unresolved(self):
        rows = [(a, "베트남" if a == "제조국" else b) for a, b in ONCH_ROWS]
        self.assertIn("원산지(제조국)", proposal(rows=rows).unresolved)

    def test_a_price_under_the_minimum_margin_blocks(self):
        p = proposal(request(sale_price=8710))
        self.assertFalse(p.ready)
        self.assertIn("MARGIN_BELOW_MIN", codes(p))
        self.assertIn("MARGIN_OK", codes(proposal()))

    def test_unique_peculiarities_are_recorded_as_notes(self):
        p = proposal(request(onch_api_found=False))
        self.assertTrue({"CHILD_TERMS_DROPPED", "STALE_MANUFACTURE_DATE", "FEE_RATE_ASSUMED", "OPTION_SKU_PLACEHOLDER",
                         "CLAIM_TERMS_IN_IMAGES", "ONCH_API_NOT_FOUND"} <= codes(p))
        self.assertEqual(p.draft_keywords, ["방수 생리팬티", "여성팬티", "순면팬티"])

    def test_many_options_get_a_size_caution(self):
        draft = {**DRAFT, "options": [{"name": "색상", "values": [f"c{i}" for i in range(8)]}, {"name": "사이즈", "values": ["M", "L", "XL"]}]}
        p = proposal(draft=draft)
        self.assertEqual(p.item_count, 24)
        self.assertIn("MANY_OPTIONS", codes(p))

    def test_a_child_term_in_the_product_name_blocks(self):
        p = proposal(draft={**DRAFT, "product_name": "초등 생리팬티"})
        self.assertIn("CHILD_TERM_IN_NAME", codes(p))
        self.assertFalse(p.ready)

    def test_the_notes_record_is_json_with_a_stable_prefix(self):
        text = notes_payload(proposal(), wizard_id=14, applied=False, when="2026-10-14T00:00:00")
        self.assertTrue(text.startswith("REGISTRATION_NOTES:{"))
        self.assertIn("MARGIN_OK", text)


class WiringTests(unittest.TestCase):

    def test_the_routes_use_the_edit_permission_and_the_shared_validation_path(self):
        router = (ROOT / "app/domains/marketplace_listing/listing_wizard_router.py").read_text(encoding="utf-8")
        self.assertIn('"/{wizard_id}/registration-assistant/propose"', router)
        self.assertIn('"/{wizard_id}/registration-assistant/apply"', router)
        propose = router.split("def registration_assistant_propose")[1].split("@router.post")[0]
        apply_ = router.split("def registration_assistant_apply")[1].split("@router.post")[0]
        for block in (propose, apply_):
            self.assertIn("_assistant_metadata(", block)
        self.assertNotIn("update_fulfillment", propose)  # propose는 저장하지 않는다.

    def test_apply_records_a_channel_policy_evaluation_after_saving(self):
        service = (ROOT / "app/domains/marketplace_listing/registration_assistant_service.py").read_text(encoding="utf-8")
        self.assertIn("ChannelPolicyService(self.db).evaluate_and_record(", service)
        # 저장(update_fulfillment) 뒤, 근거 기록 앞에서 평가한다.
        self.assertLess(service.index("service.update_fulfillment("), service.index("evaluate_and_record("))
        self.assertLess(service.index("evaluate_and_record("), service.index("notes_payload(proposal"))

    def test_apply_saves_only_through_the_existing_fulfillment_validation(self):
        service = (ROOT / "app/domains/marketplace_listing/registration_assistant_service.py").read_text(encoding="utf-8")
        self.assertIn("service.update_fulfillment(", service)
        self.assertIn("if proposal.ready:", service)
        self.assertIn("self.db.rollback()", service)
        # 시도 기록은 저장 성공 여부와 무관하게 남긴다(append-only 근거).
        self.assertIn("notes_payload(proposal", service)
        self.assertNotIn("channel_selections_json =", service)  # 위저드 컬럼을 직접 쓰지 않는다.


if __name__ == "__main__":
    unittest.main()
