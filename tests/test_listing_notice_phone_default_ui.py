"""
2026-09-25 — 5단계 정보고시의 소비자상담 전화번호 고정값 기능이 소스에
고정돼 있는지 정적으로 확인한다(서버/브라우저를 띄우지 않는다).
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NoticePhoneDefaultUiTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(
            os.path.join(REPO_ROOT, "app", "web", "console.js"),
            encoding="utf-8",
        ) as f:
            cls.js = f.read()

    def _wire_body(self):

        idx = self.js.index("function lwWirePhoneDefault(grid)")
        return self.js[idx:idx + 2600]

    def test_render_group_wires_phone_default(self):

        idx = self.js.index("const renderGroup = () => {")
        self.assertIn("lwWirePhoneDefault(grid);", self.js[idx:idx + 1800])

    def test_autofill_only_when_empty_and_not_overwriting(self):

        body = self._wire_body()
        self.assertIn("input.value.trim()", body)
        self.assertIn("!stored || input.value.trim()", body)

    def test_uses_listing_defaults_user_setting(self):

        body = self._wire_body()
        self.assertIn('"listing_defaults"', body)
        self.assertIn("consumer_service_phone", body)

    def test_invalid_phone_is_not_saved(self):

        body = self._wire_body()
        self.assertLess(
            body.index("LW_PHONE_PATTERN.test(value)"),
            body.index('userSettingSave("listing_defaults"'),
        )


if __name__ == "__main__":
    unittest.main()
