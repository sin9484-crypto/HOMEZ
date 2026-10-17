"""
=========================================================
Homez OS

File : tests/test_product_rejection_advice.py

2026-10-18 — 쿠팡 승인반려 사유 분류 규칙(`product_rejection_advice.py`, 상품 관리 AI P1a, AI 없음)을 고정한다. 외부 호출·DB 없음.

  - 실제로 관측한 문장은 맞는 종류로 분류되고 `observed=True`다. 미관측 문구 규칙은 `observed=False`(화면에서 "예상되는 원인"으로만 표시).
  - 한 사유에 여러 문제가 있으면 모두 돌려준다. 맞는 규칙이 없으면 UNKNOWN 하나(추측하지 않는다). 사유가 비면 빈 목록.
  - 공백·대소문자 차이를 흡수한다. 문자열이 아닌 입력에도 안전하다.
  - 이 모듈은 네트워크·DB·쓰기 코드를 갖지 않는다.
=========================================================
"""

import unittest
from pathlib import Path

from app.domains.order.product_rejection_advice import KNOWN_CODES
from app.domains.order.product_rejection_advice import RULES
from app.domains.order.product_rejection_advice import UNKNOWN
from app.domains.order.product_rejection_advice import classify_rejection

SOURCE = (Path(__file__).resolve().parents[1] / "app/domains/order/product_rejection_advice.py").read_text(encoding="utf-8")


def codes(reason):
    return [a["code"] for a in classify_rejection(reason)]


class ObservedSentencesTests(unittest.TestCase):

    def test_the_real_image_download_rejection(self):
        result = classify_rejection("[기본형]: 상세설명에 포함된 모든 이미지를 다운받지 못했습니다.")
        self.assertEqual(result, [{"code": "IMAGE_DOWNLOAD_FAILED", "observed": True}])

    def test_the_real_option_duplicate_rejection(self):
        self.assertEqual(codes("상품명에서 옵션과 중복된 단어를 지워주세요"), ["OPTION_DUPLICATE_WORD"])
        self.assertTrue(classify_rejection("상품명에서 옵션과 중복된 단어를 지워주세요")[0]["observed"])

    def test_the_real_brand_and_identifier_messages(self):
        self.assertEqual(codes("MPN/GTIN 속성이 누락되었거나 유효하지 않습니다"), ["BRAND_UID_REQUIRED"])
        self.assertEqual(codes("브랜드 ID가 필요합니다. 브랜드를 선택해 주세요"), ["BRAND_UID_REQUIRED"])
        self.assertEqual(codes("Global Trade Item Number 속성 누락"), ["BRAND_UID_REQUIRED"])

    def test_the_observed_flag_matches_what_the_docs_say(self):
        observed = {rule.code: rule.observed for rule in RULES}
        self.assertEqual(observed, {
            "IMAGE_DOWNLOAD_FAILED": True, "OPTION_DUPLICATE_WORD": True, "BRAND_UID_REQUIRED": True,
            "CLAIM_WORDS": False, "CONTENTS_FORMAT": False,
        })


class UnobservedAndUnknownTests(unittest.TestCase):

    def test_unobserved_rules_are_flagged_as_guesses(self):
        for reason, code in (("의료기기로 오인될 수 있는 표현이 포함되어 있습니다", "CLAIM_WORDS"), ("상세설명 HTML 형식이 올바르지 않습니다", "CONTENTS_FORMAT")):
            result = classify_rejection(reason)
            self.assertEqual([a["code"] for a in result], [code], reason)
            self.assertFalse(result[0]["observed"])

    def test_an_unseen_sentence_is_unknown_not_a_guess(self):
        self.assertEqual(classify_rejection("카테고리가 적합하지 않습니다"), [{"code": UNKNOWN, "observed": False}])
        self.assertEqual(classify_rejection("판매 불가 상품입니다"), [{"code": UNKNOWN, "observed": False}])

    def test_empty_reasons_get_no_advice(self):
        for reason in (None, "", "   ", "\n\t", 0, 12, [], {}):
            self.assertEqual(classify_rejection(reason), [], repr(reason))


class MatchingBehaviourTests(unittest.TestCase):

    def test_several_problems_in_one_reason_are_all_returned_in_rule_order(self):
        text = "상품명에서 옵션과 중복된 단어를 지워주세요. 상세설명에 포함된 이미지를 다운받지 못했습니다."
        self.assertEqual(codes(text), ["IMAGE_DOWNLOAD_FAILED", "OPTION_DUPLICATE_WORD"])

    def test_spacing_and_case_do_not_matter(self):
        self.assertEqual(codes("이 미 지 를   다 운 받 지  못했습니다"), ["IMAGE_DOWNLOAD_FAILED"])
        self.assertEqual(codes("mpn 누락"), ["BRAND_UID_REQUIRED"])
        self.assertEqual(codes("Gtin 이 유효하지 않음"), ["BRAND_UID_REQUIRED"])

    def test_unrelated_words_do_not_trigger_a_rule(self):
        for reason in ("옵션 가격이 잘못되었습니다", "이미지가 흐립니다", "브랜드명이 상품명에 없습니다", "상세설명이 너무 짧습니다"):
            self.assertEqual(codes(reason), [UNKNOWN], reason)

    def test_every_rule_code_is_a_known_code_and_unknown_is_last(self):
        self.assertEqual(KNOWN_CODES[-1], UNKNOWN)
        self.assertEqual(len(set(KNOWN_CODES)), len(KNOWN_CODES))
        self.assertEqual(set(KNOWN_CODES[:-1]), {rule.code for rule in RULES})


class SafetyTests(unittest.TestCase):

    def test_the_module_has_no_network_db_or_write_code(self):
        for forbidden in ("import requests", "sqlalchemy", "urllib", "socket", "httpx", "session", ".commit(", "open(", "subprocess"):
            self.assertNotIn(forbidden, SOURCE, forbidden)


if __name__ == "__main__":
    unittest.main()
