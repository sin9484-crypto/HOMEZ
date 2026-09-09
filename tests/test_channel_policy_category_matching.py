"""Regression tests for category policy keyword matching."""

import unittest

from app.domains.channel_policy.engine import _category_matches


class ChannelPolicyCategoryMatchingTest(unittest.TestCase):

    def test_short_prohibited_term_does_not_match_inside_unrelated_word(self):
        self.assertFalse(_category_matches(
            ["포"], "주방용품 > 주방잡화 > 행주",
            "국산 삼색 부직포 주방행주 40매",
        ))

    def test_short_prohibited_term_still_matches_complete_token(self):
        self.assertTrue(_category_matches(
            ["포"], "무기류", "장식용 포 모형",
        ))

    def test_long_policy_phrase_keeps_substring_matching(self):
        self.assertTrue(_category_matches(
            ["전자담배"], None, "전자담배용 액세서리",
        ))


if __name__ == "__main__":
    unittest.main()
