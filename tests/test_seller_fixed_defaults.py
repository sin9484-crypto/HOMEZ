"""
=========================================================
Homez OS

File : tests/test_seller_fixed_defaults.py

판매자 고정값(WING 아이디·택배사, 2026-10-15)을 고정한다 — 매번 입력·확인하지 않도록 사용자 설정(listing_defaults)에 한 번
저장해 두고, 칸이 비어 있을 때만 자동 입력하며, 등록 도우미는 이 값을 이전 등록 값보다 먼저 쓴다. 네트워크·DB 없음.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app.domains.marketplace_listing.registration_assistant import AssistantRequest
from app.domains.marketplace_listing.registration_assistant import build_proposal
from app.domains.marketplace_listing.registration_assistant import pick_seller_defaults

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app" / "web" / "console.js").read_text(encoding="utf-8")
KO = (ROOT / "app" / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app" / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

PRIOR = {"deliveryCompanyCode": "CJGLS", "vendorUserId": "prior-id", "brandState": "NO_BRAND", "brand": "브랜드 없음"}


def function_source(name: str) -> str:
    match = re.search(r"function " + re.escape(name) + r"\(", CONSOLE)
    assert match, name
    start = match.start()
    i = CONSOLE.index("{", match.end())
    depth, mode, stack = 0, None, []
    while i < len(CONSOLE):
        ch = CONSOLE[i]
        if mode is None:
            if ch == "/" and CONSOLE[i + 1] == "/":
                i = CONSOLE.index("\n", i)
                continue
            if ch in "\"'`":
                mode = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if stack and depth == stack[-1]:
                    stack.pop()
                    mode = "`"
                elif depth == 0:
                    return CONSOLE[start: i + 1]
        else:
            if ch == "\\":
                i += 2
                continue
            if mode == "`" and ch == "$" and CONSOLE[i + 1] == "{":
                stack.append(depth)
                depth += 1
                mode = None
                i += 2
                continue
            if ch == mode:
                mode = None
        i += 1
    raise AssertionError("unbalanced: " + name)


class BackendPriorityTests(unittest.TestCase):

    def test_a_saved_fixed_value_beats_the_previous_registration(self):
        picked = pick_seller_defaults(PRIOR, {}, {"wing_user_id": "fixed-wing", "delivery_company_code": "HANJIN"})
        self.assertEqual(picked["vendorUserId"], "fixed-wing")
        self.assertEqual(picked["deliveryCompanyCode"], "HANJIN")
        self.assertEqual(sorted(picked["_fixed_used"]), ["delivery_company_code", "wing_user_id"])

    def test_without_fixed_values_the_previous_registration_is_used(self):
        picked = pick_seller_defaults(PRIOR, {})
        self.assertEqual(picked["vendorUserId"], "prior-id")
        self.assertEqual(picked["_fixed_used"], [])

    def test_malformed_fixed_values_are_ignored_not_trusted(self):
        picked = pick_seller_defaults(PRIOR, {}, {"wing_user_id": "bad id with spaces!", "delivery_company_code": "cj gls"})
        self.assertEqual(picked["vendorUserId"], "prior-id")
        self.assertEqual(picked["deliveryCompanyCode"], "CJGLS")
        self.assertEqual(picked["_fixed_used"], [])

    def test_fixed_values_work_even_with_no_previous_registration(self):
        picked = pick_seller_defaults({}, {}, {"wing_user_id": "only-fixed", "delivery_company_code": "CJGLS"})
        self.assertEqual(picked["vendorUserId"], "only-fixed")

    def test_the_fixed_consumer_phone_fills_the_seller_notice(self):
        picked = pick_seller_defaults({}, {}, {"consumer_service_phone": "010-0000-0000"})
        self.assertIn("소비자상담관련전화번호", picked["_seller_notice"])
        self.assertEqual(
            pick_seller_defaults({}, {}, {"consumer_service_phone": "<script>"})["_seller_notice"], {},
        )

    def test_the_proposal_notes_that_fixed_values_were_used(self):
        metadata = {"display_category_name": "x", "metadata_version": "v", "metadata_fingerprint": "f", "notice_fields": [],
                    "purchase_option_fields": []}
        proposal = build_proposal(
            wizard_draft={"product_name": "상품", "options": []}, candidate_id=1, source_reference="ONCHANNEL:CH1",
            account_id=1, onch_rows=[("제조국", "중국")], metadata=metadata,
            seller_defaults=pick_seller_defaults(PRIOR, {}, {"wing_user_id": "fixed-wing"}),
            outbound_code="1", return_center_code="2",
            request=AssistantRequest(display_category_code="1", sale_price=1000),
        )
        codes = {n.code for n in proposal.notes}
        self.assertIn("FIXED_DEFAULTS_USED", codes)
        self.assertEqual(proposal.selection["required_fields"]["vendorUserId"], "fixed-wing")
        self.assertNotIn("_fixed_used", proposal.selection["required_fields"])

    def test_the_service_reads_the_fixed_values_for_the_acting_user_and_falls_back_safely(self):
        source = (ROOT / "app/domains/marketplace_listing/registration_assistant_service.py").read_text(encoding="utf-8")
        self.assertIn('"listing_defaults"', source)
        self.assertIn("user_id=actor_user_id", source)
        self.assertIn("except Exception", source.split("def _fixed_defaults")[1].split("def propose")[0])
        router = (ROOT / "app/domains/marketplace_listing/listing_wizard_router.py").read_text(encoding="utf-8")
        self.assertIn("user_id=current_user.id", router)


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class UiValidatorTests(unittest.TestCase):

    def _run(self, expression: str):
        harness = (
            "const LW_COUPANG_DELIVERY_COMPANIES = [{code:'CJGLS'},{code:'HANJIN'}];\n"
            + "const LW_WING_ID_PATTERN = /^[A-Za-z0-9._-]{3,40}$/;\n"
            + function_source("lwSellerFixedFields") + "\n"
            + "const f = lwSellerFixedFields(); console.log(JSON.stringify(" + expression + "));"
        )
        proc = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_wing_id_validation(self):
        self.assertEqual(self._run("[f[0].validate('sin945'), f[0].validate('ab'), f[0].validate('a b c'), f[0].validate('')]"),
                         [True, False, False, False])

    def test_courier_must_be_a_known_code(self):
        self.assertEqual(self._run("[f[1].validate('CJGLS'), f[1].validate('NOPE'), f[1].validate('')]"), [True, False, False])

    def test_the_two_fields_map_to_the_expected_inputs_and_keys(self):
        self.assertEqual(self._run("f.map(x => [x.selector, x.key])"),
                         [[".lw-vendor-user-id", "wing_user_id"], [".lw-delivery-company", "delivery_company_code"]])


class UiWiringTests(unittest.TestCase):

    def test_the_fields_are_wired_after_the_fulfillment_step_renders(self):
        self.assertIn("lwWireSellerFixedDefaults(content);", CONSOLE)
        self.assertLess(CONSOLE.index("lwWireSellerFixedDefaults(content);"),
                        CONSOLE.index('content.querySelectorAll("[data-lw-image-auto-upload]")'))

    def test_autofill_never_overwrites_a_value_the_user_already_has(self):
        body = function_source("lwWireSellerFixedDefaults")
        self.assertIn("input.value.trim()", body)
        self.assertIn("!stored || input.value.trim() || !input.isConnected || !field.validate(stored)", body)

    def test_an_invalid_value_is_never_saved(self):
        body = function_source("lwWireSellerFixedDefaults")
        self.assertLess(body.index("field.validate(value)"), body.index('userSettingSave("listing_defaults"'))

    def test_only_the_listing_defaults_key_is_used_and_no_credentials_are_stored(self):
        body = function_source("lwWireSellerFixedDefaults") + function_source("lwSellerFixedFields")
        self.assertEqual(set(re.findall(r'userSetting(?:Fetch|Save)\("([a-z_]+)"', body)), {"listing_defaults"})
        for forbidden in ("password", "token", "secret", "credential"):
            self.assertNotIn(forbidden, body.lower())

    def test_each_field_is_wired_once(self):
        self.assertIn("input.dataset.lwFixedWired", function_source("lwWireSellerFixedDefaults"))

    def test_translations_exist_in_both_languages_and_are_used(self):
        ko = set(re.findall(r'"(lw\.fixed_default_[a-z_]+)"', KO))
        en = set(re.findall(r'"(lw\.fixed_default_[a-z_]+)"', EN))
        self.assertEqual(ko, en)
        used = set(re.findall(r'"(lw\.fixed_default_[a-z_]+)"', CONSOLE))
        self.assertEqual(used - ko, set(), "화면이 쓰는데 번역이 없는 키")
        self.assertEqual(ko - used, set(), "번역만 있고 화면이 쓰지 않는 키")


if __name__ == "__main__":
    unittest.main()
