"""
=========================================================
Homez OS

File : tests/test_wizard_policy_origin_options.py

상품 등록 위저드 채널 정책 원산지 선택지 — 중국 추가(2026-10-06).

백산수처럼 수입(중국산) 상품을 사실대로 등록할 수 있도록 원산지 선택지에
CHINA를 둔다. 선택지가 대한민국 하나뿐이면 수입 상품을 KOREA로 잘못 입력하게
된다. 정책 엔진이 CHINA를 금지·제한 원산지로 취급하지 않는지도 함께 고정한다.

실제 homez.db는 사용하지 않는다 — 전부 정적 파일 검사.
=========================================================
"""

import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "app" / "web"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


class WizardPolicyOriginOptionsTests(unittest.TestCase):

    def test_console_origin_select_has_korea_and_china(self):
        source = _read("console.js")
        self.assertIn('value="KOREA"', source)
        self.assertIn('value="CHINA"', source)
        self.assertIn('HomezI18n.t("lw.policy_origin_china")', source)

    def test_origin_china_label_exists_in_both_locales(self):
        self.assertIn('"lw.policy_origin_china": "중국"', _read("i18n/ko-KR.js"))
        self.assertIn('"lw.policy_origin_china": "China"', _read("i18n/en-US.js"))


if __name__ == "__main__":
    unittest.main()
