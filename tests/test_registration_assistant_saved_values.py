"""
=========================================================
Homez OS

File : tests/test_registration_assistant_saved_values.py

2026-10-17 — 등록 도우미 Phase A: URL로 가져오기로 만들지 않은 위저드(수동 생성·복제)도, 5단계에 이미 저장해 둔 값을 근거로
같은 규칙(`build_proposal`)으로 다시 검증·제안한다. 네트워크·DB 없음.

고정하는 규칙:
- 새 값을 만들지 않는다 — 저장된 고시 값에서 (라벨, 값) 행만 만들고 빈 값·내부 키는 뺀다.
- 고정값·이전 등록이 우선이고, 저장된 값은 비어 있는 칸만 채운다. 무브랜드 기록은 저장된 상태가 NO_BRAND일 때만 이어 받는다.
- 이미 올린 이미지·상세설명·사용권 선언은 제안을 저장할 때 지워지지 않는다(사용자가 새로 넘긴 값이 우선).
- 근거가 전혀 없으면 여전히 거부한다(추측하지 않는다).
=========================================================
"""

import unittest
from pathlib import Path

from app.domains.marketplace_listing.registration_assistant import AssistantRequest
from app.domains.marketplace_listing.registration_assistant import build_items
from app.domains.marketplace_listing.registration_assistant import build_proposal
from app.domains.marketplace_listing.registration_assistant_service import carry_saved_content
from app.domains.marketplace_listing.registration_assistant_service import keep_saved_item_identity
from app.domains.marketplace_listing.registration_assistant_service import merge_saved_defaults
from app.domains.marketplace_listing.registration_assistant_service import rows_from_saved_selection
from app.domains.marketplace_listing.registration_assistant_service import saved_entry_for_account

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "app/domains/marketplace_listing/registration_assistant_service.py").read_text(encoding="utf-8")

NOTICE_KEYS = ("용량(중량)", "제조국", "사용기한 또는 개봉 후 사용기간")
NOTICE_FIELDS = [
    {"key": f"화장품::{label}", "label": label, "required": True, "allow_not_applicable": False} for label in NOTICE_KEYS
]
METADATA = {
    "display_category_code": "56213", "display_category_name": "바디워시", "metadata_version": "v1",
    "metadata_fingerprint": "fp", "notice_fields": NOTICE_FIELDS,
    "purchase_option_fields": [
        {"attribute_type_name": "개당 용량", "required": True, "exposed": True, "input_type": "INPUT", "input_values": [], "data_type": "NUMBER"},
    ],
}
IMAGES = [{"imageOrder": 0, "imageType": "REPRESENTATION", "vendorPath": "https://x/y.jpg"}]
CONTENTS = [{"contentsType": "IMAGE", "contentDetails": [{"detailType": "IMAGE", "content": "<img src=\"https://x/z.jpg\">"}]}]
SAVED = {
    "marketplace_account_id": 1,
    "outbound_shipping_place_code": "111", "return_center_code": "222",
    "required_fields": {
        "deliveryMethod": "SEQUENCIAL", "deliveryChargeType": "FREE", "deliveryCharge": 0, "deliveryChargeOnReturn": 3000,
        "returnCharge": 3000, "deliveryCompanyCode": "CJGLS", "vendorUserId": "sin945",
        "brandState": "NO_BRAND", "brand": "브랜드 없음", "brandId": "",
        "images": IMAGES, "contents": CONTENTS, "contentsSourceAssetIds": [2], "liveImageRightsConfirmed": True,
    },
    "channel_policy_attributes": {
        "notice_information": {
            "__notice_category_name": "화장품", "화장품::용량(중량)": "200ml", "화장품::제조국": "대한민국",
            "화장품::사용기한 또는 개봉 후 사용기간": "제조일로부터 3년", "화장품::빈칸": "   ",
        },
    },
}
DRAFT = {"product_name": "레이펄스 워터리스 바디워시 본품", "options": [{"name": "개당 용량", "values": ["200"]}]}


def request(**overrides):
    base = dict(display_category_code="56213", sale_price=12900, stock=10, unit_cost=5050, shipping_cost=3000)
    base.update(overrides)
    return AssistantRequest(**base)


class SavedRowsTests(unittest.TestCase):

    def test_rows_come_only_from_the_saved_notice_values(self):
        rows = rows_from_saved_selection(SAVED)
        self.assertIn(("상품정보제공고시 구분", "화장품"), rows)
        self.assertIn(("용량(중량)", "200ml"), rows)
        labels = [label for label, _ in rows]
        self.assertNotIn("빈칸", labels)
        self.assertFalse(any(label.startswith("__") for label in labels))

    def test_nothing_saved_means_no_rows_and_so_no_guess(self):
        self.assertEqual(rows_from_saved_selection({}), [])
        self.assertEqual(rows_from_saved_selection({"channel_policy_attributes": {"notice_information": {}}}), [])

    def test_the_entry_is_picked_by_account_and_bad_json_is_empty(self):
        import json

        text = json.dumps([{"marketplace_account_id": 2, "x": 1}, SAVED])
        self.assertEqual(saved_entry_for_account(text, 1)["outbound_shipping_place_code"], "111")
        self.assertEqual(saved_entry_for_account(text, 3), {})
        self.assertEqual(saved_entry_for_account("not json", 1), {})
        self.assertEqual(saved_entry_for_account(None, 1), {})


class MergeDefaultsTests(unittest.TestCase):

    def test_fixed_values_win_and_saved_values_only_fill_blanks(self):
        merged, used = merge_saved_defaults({"vendorUserId": "fixed01", "deliveryCompanyCode": "HANJIN"}, SAVED)
        self.assertEqual(merged["vendorUserId"], "fixed01")
        self.assertEqual(merged["deliveryCompanyCode"], "HANJIN")
        self.assertEqual(merged["deliveryMethod"], "SEQUENCIAL")
        self.assertIn("deliveryMethod", used)
        self.assertNotIn("vendorUserId", used)

    def test_no_brand_is_carried_only_when_the_saved_state_is_no_brand(self):
        merged, used = merge_saved_defaults({}, SAVED)
        self.assertEqual((merged["brandState"], merged["brand"]), ("NO_BRAND", "브랜드 없음"))
        self.assertIn("brandState", used)
        official = {"required_fields": {"brandState": "OFFICIAL_BRAND", "brand": "", "brandId": "KR-1", "officialBrandName": "X"}}
        merged, used = merge_saved_defaults({}, official)
        self.assertNotIn("brandState", merged)
        self.assertNotIn("brandState", used)

    def test_an_existing_no_brand_record_is_not_overwritten(self):
        merged, _ = merge_saved_defaults({"brandState": "NO_BRAND", "brand": "다른 표기"}, SAVED)
        self.assertEqual(merged["brand"], "다른 표기")


class CarryContentTests(unittest.TestCase):

    def test_uploaded_images_contents_and_rights_are_kept(self):
        carried_request, carried = carry_saved_content(request(), SAVED)
        self.assertEqual(carried_request.images, IMAGES)
        self.assertEqual(carried_request.contents, CONTENTS)
        self.assertEqual(carried_request.contents_source_asset_ids, [2])
        self.assertTrue(carried_request.image_rights_confirmed)
        self.assertEqual(carried, ["대표 이미지", "상세설명", "이미지 사용권 선언"])

    def test_what_the_user_passes_now_wins_over_the_saved_value(self):
        mine = [{"imageOrder": 0, "imageType": "REPRESENTATION", "vendorPath": "https://x/new.jpg"}]
        carried_request, carried = carry_saved_content(request(images=mine), SAVED)
        self.assertEqual(carried_request.images, mine)
        self.assertNotIn("대표 이미지", carried)

    def test_nothing_saved_changes_nothing(self):
        same, carried = carry_saved_content(request(), {})
        self.assertEqual(carried, [])
        self.assertEqual(same.images, [])
        self.assertFalse(same.image_rights_confirmed)


class EndToEndProposalTests(unittest.TestCase):

    def _proposal(self, saved=SAVED, **req):
        defaults, _ = merge_saved_defaults({}, saved)
        carried_request, _ = carry_saved_content(request(confirm_notice=True, **req), saved)
        return build_proposal(
            wizard_draft=DRAFT, candidate_id=3, source_reference="ONCHANNEL:CH1147184", account_id=1,
            onch_rows=rows_from_saved_selection(saved), metadata=METADATA, seller_defaults=defaults,
            outbound_code=saved.get("outbound_shipping_place_code"), return_center_code=saved.get("return_center_code"),
            request=carried_request,
        )

    def test_a_manual_wizard_with_saved_values_is_proposed_without_losing_uploads(self):
        proposal = self._proposal()
        self.assertTrue(proposal.ready, proposal.unresolved)
        fields = proposal.selection["required_fields"]
        self.assertEqual(fields["images"], IMAGES)
        self.assertEqual(fields["contents"], CONTENTS)
        self.assertTrue(fields["liveImageRightsConfirmed"])
        self.assertEqual(fields["brandState"], "NO_BRAND")
        self.assertEqual(proposal.selection["channel_policy_attributes"]["origin_country"], "KOREA")
        self.assertEqual(proposal.selection["outbound_shipping_place_code"], "111")

    def test_a_missing_saved_origin_still_blocks_instead_of_guessing(self):
        thin = {**SAVED, "channel_policy_attributes": {"notice_information": {"__notice_category_name": "화장품", "화장품::용량(중량)": "200ml"}}}
        proposal = self._proposal(saved=thin)
        self.assertFalse(proposal.ready)
        self.assertIn("원산지(제조국)", proposal.unresolved)


class GroupedOptionTests(unittest.TestCase):
    """쿠팡 카테고리 56213: 개당 중량/개당 용량은 같은 묶음(group_number 1)이라 둘 중 하나만 채우면 된다."""

    DEFS = [
        {"attribute_type_name": "수량", "required": True, "exposed": True, "input_type": "INPUT", "input_values": [], "group_number": "NONE"},
        {"attribute_type_name": "개당 중량", "required": True, "exposed": True, "input_type": "INPUT", "input_values": [], "group_number": "1"},
        {"attribute_type_name": "개당 용량", "required": True, "exposed": True, "input_type": "INPUT", "input_values": [], "group_number": "1"},
    ]

    def _build(self, option_names):
        options = [{"name": name, "values": ["200"]} for name in option_names]
        return build_items(options, self.DEFS, sku_prefix="PC3-CH1147184", sale_price=12900, stock=10)

    def test_one_member_of_the_group_is_enough(self):
        items, unresolved, _ = self._build(["개당 용량"])
        self.assertEqual(unresolved, [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["optionAttributes"], {"개당 용량": "200", "수량": "1"})
        _, unresolved, _ = self._build(["개당 중량"])
        self.assertEqual(unresolved, [])

    def test_an_empty_group_is_reported_once_as_choose_one(self):
        items, unresolved, _ = self._build([])
        self.assertEqual(items, [])
        self.assertEqual(unresolved, ["구매옵션(택1):개당 중량/개당 용량"])

    def test_ungrouped_required_options_are_still_required_one_by_one(self):
        defs = [dict(d) for d in self.DEFS] + [
            {"attribute_type_name": "색상", "required": True, "exposed": True, "input_type": "INPUT", "input_values": [], "group_number": "NONE"},
        ]
        _, unresolved, _ = build_items([{"name": "개당 용량", "values": ["200"]}], defs, sku_prefix="P", sale_price=1000, stock=1)
        self.assertEqual(unresolved, ["구매옵션:색상"])

    def test_a_manual_wizard_with_saved_values_and_a_grouped_category_is_ready(self):
        metadata = dict(METADATA, purchase_option_fields=self.DEFS)
        defaults, _ = merge_saved_defaults({}, SAVED)
        carried_request, _ = carry_saved_content(request(confirm_notice=True), SAVED)
        proposal = build_proposal(
            wizard_draft=DRAFT, candidate_id=3, source_reference="ONCHANNEL:CH1147184", account_id=1,
            onch_rows=rows_from_saved_selection(SAVED), metadata=metadata, seller_defaults=defaults,
            outbound_code="111", return_center_code="222", request=carried_request,
        )
        self.assertTrue(proposal.ready, proposal.unresolved)
        self.assertEqual(proposal.item_count, 1)


class SavedSkuTests(unittest.TestCase):
    """저장돼 있던 판매자 SKU는 재고 SKU·채널 매핑·공급 옵션 연결의 열쇠라 도우미가 바꾸면 안 된다."""

    SAVED_WITH_ITEM = {**SAVED, "required_fields": {**SAVED["required_fields"], "items": [
        {"itemName": "기본형", "externalVendorSku": "PC3-CH1147184-3945580", "unitCount": 1, "salePrice": 12900, "originalPrice": 12900, "maximumBuyCount": 1},
    ]}}

    def _proposal(self, saved):
        defaults, _ = merge_saved_defaults({}, saved)
        carried_request, _ = carry_saved_content(request(confirm_notice=True), saved)
        proposal = build_proposal(
            wizard_draft=DRAFT, candidate_id=3, source_reference="ONCHANNEL:CH1147184", account_id=1,
            onch_rows=rows_from_saved_selection(saved), metadata=METADATA, seller_defaults=defaults,
            outbound_code="111", return_center_code="222", request=carried_request,
        )
        keep_saved_item_identity(proposal, saved)
        return proposal

    def test_a_single_saved_item_keeps_its_sku_and_name(self):
        proposal = self._proposal(self.SAVED_WITH_ITEM)
        item = proposal.selection["required_fields"]["items"][0]
        self.assertEqual(item["externalVendorSku"], "PC3-CH1147184-3945580")
        self.assertEqual(item["itemName"], "기본형")
        self.assertEqual(proposal.selection["channel_policy_attributes"]["product_identifier"], "PC3-CH1147184-3945580")
        codes = [n.code for n in proposal.notes]
        self.assertIn("SAVED_SKU_KEPT", codes)
        self.assertNotIn("OPTION_SKU_PLACEHOLDER", codes)

    def test_without_saved_items_the_placeholder_rule_and_its_caution_stay(self):
        proposal = self._proposal(SAVED)
        self.assertEqual(proposal.selection["required_fields"]["items"][0]["externalVendorSku"], "PC3-CH1147184-01")
        self.assertIn("OPTION_SKU_PLACEHOLDER", [n.code for n in proposal.notes])

    def test_a_multi_option_change_of_skus_is_warned_not_hidden(self):
        saved = {**self.SAVED_WITH_ITEM, "required_fields": {**self.SAVED_WITH_ITEM["required_fields"], "items": [
            {"itemName": "a", "externalVendorSku": "OLD-1"}, {"itemName": "b", "externalVendorSku": "OLD-2"},
        ]}}
        proposal = self._proposal(saved)
        self.assertIn("SAVED_SKUS_CHANGE", [n.code for n in proposal.notes])


class WiringTests(unittest.TestCase):

    def test_the_service_falls_back_to_saved_values_only_when_there_is_no_import_evidence(self):
        body = SERVICE.split("def propose(")[1].split("def apply(")[0]
        self.assertLess(body.index("self._onch_rows(candidate.id)"), body.index("rows_from_saved_selection(saved)"))
        self.assertIn("if not rows:", body)
        self.assertIn("raise BadRequestException", body)

    def test_propose_keeps_saved_sku_identity_before_returning(self):
        body = SERVICE.split("def propose(")[1].split("def apply(")[0]
        self.assertIn("keep_saved_item_identity(proposal, saved)", body)

    def test_apply_still_saves_through_the_existing_validation_path(self):
        body = SERVICE.split("def apply(")[1]
        self.assertIn("service.update_fulfillment(", body)
        self.assertIn("proposal.ready", body)


if __name__ == "__main__":
    unittest.main()
