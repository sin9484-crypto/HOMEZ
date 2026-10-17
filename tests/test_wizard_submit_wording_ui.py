"""
=========================================================
Homez OS

File : tests/test_wizard_submit_wording_ui.py

2026-10-17 — 연동 안정화 A4: 9단계 "제출"이 쿠팡에 보내는 것이 아니라 HOMEZ 안의 준비라는 것을 화면 문구로 분명히 한다.

사용자가 "지금 등록 실행"이라는 이름 때문에 제출이 곧 쿠팡 등록이라고 오해하기 쉬웠다. 실제 쿠팡 전송은 10단계 결과 화면의
"쿠팡으로 전송"(전송 전 검사 → 비밀번호 재확인)에서만 일어난다(2026-10-17 제출 #19: 제출 05:09:50, 쿠팡 POST 05:10:14).

고정하는 규칙:
- 제출 버튼 이름에 "등록 실행"처럼 쿠팡 등록으로 읽히는 말이 없고, 버튼 아래 안내가 쿠팡에는 아직 보내지 않음과 다음 행동을 말한다.
- 안내는 승인이 1시간만 유효함을 알린다. 한국어·영어가 짝이 맞는다.
- 제출 버튼 핸들러는 쿠팡 전송 API(`send-live`)를 부르지 않는다 — 제출과 전송은 다른 단계다.
=========================================================
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")


def line(source, key):
    return re.search(r'"%s": "(.*)",' % re.escape(key), source).group(1)


class WordingTests(unittest.TestCase):

    def test_the_button_does_not_read_as_registering_on_coupang(self):
        label = line(KO, "lw.execution_submit_btn")
        self.assertNotIn("등록 실행", label)
        self.assertIn("제출", label)
        self.assertNotIn("Register now", line(EN, "lw.execution_submit_btn"))

    def test_the_hint_says_nothing_is_sent_yet_and_what_to_do_next(self):
        hint = line(KO, "lw.execution_submit_hint")
        for phrase in ("쿠팡에는 아직 아무것도 보내지 않아요", "전송 전 검사", "쿠팡으로 전송", "1시간"):
            self.assertIn(phrase, hint, phrase)
        hint_en = line(EN, "lw.execution_submit_hint")
        for phrase in ("Nothing is sent to Coupang yet", "Send to Coupang", "one hour"):
            self.assertIn(phrase, hint_en, phrase)

    def test_the_hint_is_shown_under_the_button(self):
        body = CONSOLE.split("function lwRenderExecutionStep(")[1].split("// ---- 10단계")[0]
        self.assertLess(body.index('id="lw-submit-btn"'), body.index('id="lw-execution-hint"'))
        self.assertIn('HomezI18n.t("lw.execution_submit_hint")', body)

    def test_submitting_never_calls_the_send_endpoint(self):
        body = CONSOLE.split("function lwRenderExecutionStep(")[1].split("// ---- 10단계")[0]
        self.assertIn("/submit`", body)
        for forbidden in ("send-live", "live-preflight", "seller-products"):
            self.assertNotIn(forbidden, body, forbidden)

    def test_the_send_button_text_is_unchanged_so_the_next_step_is_findable(self):
        self.assertEqual(line(KO, "lw.live_send_btn"), "쿠팡으로 전송")
        self.assertEqual(line(EN, "lw.live_send_btn"), "Send to Coupang")


if __name__ == "__main__":
    unittest.main()
