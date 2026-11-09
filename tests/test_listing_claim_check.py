"""
=========================================================
Homez OS

File : tests/test_listing_claim_check.py

2026-11-09 — 상세·상품명 '주장 점검기'(사용자 승인 "2번 진행"): 순수 함수 규칙과 사전검사 경고 연결을 고정한다.
  - 경고만 한다(차단 없음). 표현 목록은 사용자 확정 전 초안이다(RULES_CONFIRMED=False, 문구에도 적는다).
  - 상품명의 기존 의료·효능 단어는 기존 경고(PRODUCT_NAME_CLAIM_WORDS)와 겹쳐 세지 않는다.
  - 상세 설명의 두 자리 이상 숫자 중 공급처 자료(상품명·옵션·SKU·고시)에 없는 것만 '근거 확인'으로 알린다.
  - 사전검사 화면은 문구 자리({words}·{numbers})에 서버 값을 넣는다(이전에는 자리 표시가 그대로 보였다).
네트워크·실제 DB 0.
=========================================================
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing import listing_wizard_precheck
from app.domains.marketplace_listing.listing_claim_check import RULES_CONFIRMED
from app.domains.marketplace_listing.listing_claim_check import RULES_VERSION
from app.domains.marketplace_listing.listing_claim_check import check_listing_text
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection

ROOT = Path(__file__).resolve().parents[1]
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KEYS = ("listing_claim_medical", "listing_claim_absolute", "listing_claim_certification", "description_unsourced_numbers")


class RuleTests(unittest.TestCase):

    def test_each_category_is_found_in_description_and_keywords(self):
        r = check_listing_text("방수 생리팬티", ["항균 팬티", "국내 최초"], "KC 인증을 받은 제품으로 생리통 완화에 도움을 줍니다. 100% 만족!")
        self.assertEqual(r.words["MEDICAL"], ["완화", "항균", "생리통"])
        self.assertEqual(r.words["ABSOLUTE"], ["100%", "최초"])
        self.assertEqual(r.words["CERTIFICATION"], ["인증", "KC"])
        self.assertEqual(r.fields["MEDICAL"], ["keywords", "description"])
        self.assertEqual(r.fields["ABSOLUTE"], ["keywords", "description"])

    def test_plain_text_has_no_findings(self):
        r = check_listing_text("방수 생리팬티 위생팬티", ["생리팬티", "위생팬티"], "편하게 입을 수 있는 방수 팬티입니다. 색상은 블랙과 스킨이 있습니다.")
        self.assertEqual((r.words, r.unsourced_numbers), ({}, []))

    def test_old_medical_words_in_the_product_name_are_left_to_the_existing_warning(self):
        r = check_listing_text("무릎연골보호 밴드", [], "")
        self.assertNotIn("MEDICAL", r.words)
        r = check_listing_text("항균 생리팬티", [], "")
        self.assertEqual((r.words["MEDICAL"], r.fields["MEDICAL"]), (["항균"], ["product_name"]))   # 새로 더한 단어는 상품명에서도 센다
        self.assertEqual(check_listing_text("최고급 팬티", [], "").fields["ABSOLUTE"], ["product_name"])

    def test_words_appear_once_in_list_order(self):
        r = check_listing_text("", ["항균", "항균"], "항균 항균 살균")
        self.assertEqual(r.words["MEDICAL"], ["살균", "항균"])

    def test_numbers_not_in_the_supplier_data_are_flagged(self):
        source = "사이즈 M L XL\n소재 나일론 85% 스판덱스 15%\n구성 3,000원"
        r = check_listing_text("생리팬티 3매", [], "나일론 85%, 스판덱스 15%로 3,000원 상당. 흡수량 120ml, 2가지 색, 1.5배 넓은 면적", source)
        self.assertEqual(r.unsourced_numbers, ["120", "1.5"])          # 85·15·3000은 근거가 있고 한 자리(2)는 세지 않는다
        self.assertEqual(check_listing_text("", [], "", source).unsourced_numbers, [])
        self.assertEqual(len(check_listing_text("", [], " ".join(str(n) for n in range(10, 40))).unsourced_numbers), 10)

    def test_odd_inputs_do_not_break_it(self):
        r = check_listing_text(None, None, None)
        self.assertEqual((r.words, r.unsourced_numbers), ({}, []))
        self.assertEqual(check_listing_text(123, "항균", ["최고"]).words, {"MEDICAL": ["항균"], "ABSOLUTE": ["최고"]})

    def test_the_list_is_marked_as_an_unconfirmed_draft(self):
        self.assertFalse(RULES_CONFIRMED)
        self.assertIn("draft", RULES_VERSION)
        out = check_listing_text("", [], "최고").to_dict()
        self.assertEqual((out["rules_confirmed"], out["rules_version"]), (False, RULES_VERSION))


class PrecheckTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[Company.__table__, ProductCandidate.__table__, ProductCandidateSelection.__table__,
                                                            MarketplaceChannel.__table__, MarketplaceAccount.__table__])
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        company = Company(name="주장 점검 테스트", business_number="888-88-88888", ceo="t", phone="02", email="f@e.c", address="서울")
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id
        for patcher in (
            mock.patch.object(listing_wizard_precheck, "is_restricted_mode", return_value=False),
            mock.patch.object(listing_wizard_precheck.SafetyService, "is_emergency_stop_active", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def run_precheck(self, draft, selections=None):
        wizard = ListingWizard(
            company_id=self.company_id, created_by_user_id=1, current_step="PRECHECK", status="DRAFT", source_type="MANUAL",
            draft_json=json.dumps(draft, ensure_ascii=False), channel_selections_json=json.dumps(selections or [], ensure_ascii=False),
        )
        return listing_wizard_precheck.run_precheck(wizard, self.db, self.company_id)

    def issues(self, draft, selections=None):
        return {i.code: i for i in self.run_precheck(draft, selections).issues}

    def test_warnings_are_non_blocking_and_carry_the_words(self):
        issues = self.issues({"product_name": "방수 생리팬티", "keywords": ["항균"], "description": "최고의 품질, 특허 받은 원단. 흡수량 120ml"})
        for code, words in (("LISTING_CLAIM_MEDICAL", "항균"), ("LISTING_CLAIM_ABSOLUTE", "최고"), ("LISTING_CLAIM_CERTIFICATION", "특허")):
            issue = issues[code]
            self.assertEqual((issue.blocking, issue.severity, issue.step, issue.params), (False, "WARNING", "DRAFT", {"words": words}), code)
            self.assertEqual(issue.localized_message_key, f"listing_wizard.precheck.{code.lower()}")
        self.assertEqual(issues["LISTING_CLAIM_MEDICAL"].field, "keywords")
        numbers = issues["DESCRIPTION_UNSOURCED_NUMBERS"]
        self.assertEqual((numbers.blocking, numbers.field, numbers.params), (False, "description", {"numbers": "120"}))

    def test_supplier_options_and_saved_notice_count_as_evidence(self):
        draft = {"product_name": "생리팬티", "description": "흡수량 120ml, 나일론 85%",
                 "options": [{"name": "흡수량", "values": ["120ml"]}], "sku_list": []}
        selections = [{"marketplace_account_id": 1, "channel_policy_attributes": {"notice_information": {"소재::소재": "나일론 85%", "__x": "999"}}}]
        self.assertNotIn("DESCRIPTION_UNSOURCED_NUMBERS", self.issues(draft, selections))
        self.assertIn("DESCRIPTION_UNSOURCED_NUMBERS", self.issues(draft))

    def test_the_warnings_never_change_the_overall_result(self):
        plain = self.run_precheck({"product_name": "방수 생리팬티", "description": "편한 팬티"})
        claimy = self.run_precheck({"product_name": "방수 생리팬티", "description": "최고 항균 특허 99.9%"})
        self.assertEqual(plain.status, claimy.status)
        self.assertEqual({i.code for i in claimy.issues if i.blocking}, {i.code for i in plain.issues if i.blocking})

    def test_no_draft_means_no_claim_warnings(self):
        wizard = ListingWizard(company_id=self.company_id, created_by_user_id=1, current_step="PRECHECK", status="DRAFT", source_type="MANUAL", draft_json=None)
        codes = {i.code for i in listing_wizard_precheck.run_precheck(wizard, self.db, self.company_id).issues}
        self.assertFalse({c for c in codes if c.startswith("LISTING_CLAIM_") or c == "DESCRIPTION_UNSOURCED_NUMBERS"})


class TextAndScreenTests(unittest.TestCase):

    def test_messages_exist_in_both_languages_with_the_same_placeholders_and_say_draft(self):
        for key in KEYS:
            full = f'"listing_wizard.precheck.{key}"'
            ko = re.search(re.escape(full) + r': "((?:[^"\\]|\\.)*)"', KO).group(1)
            en = re.search(re.escape(full) + r': "((?:[^"\\]|\\.)*)"', EN).group(1)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko)), sorted(re.findall(r"\{(\w+)\}", en)), key)
            self.assertIn("차단하지 않습니다", ko, key)
            if key.startswith("listing_claim_"):
                self.assertIn("사용자 확정 전 초안", ko, key)

    def test_both_precheck_renderers_fill_the_placeholders(self):
        self.assertEqual(CONSOLE.count("HomezI18n.t(issue.localized_message_key, issue.params || undefined)"), 2)
        self.assertNotIn("HomezI18n.t(issue.localized_message_key)", CONSOLE)


if __name__ == "__main__":
    unittest.main()
