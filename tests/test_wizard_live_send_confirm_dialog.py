"""
=========================================================
Homez OS

File : tests/test_wizard_live_send_confirm_dialog.py

쿠팡 실제 전송 직전 확인(2026-10-06) — 브라우저 기본 팝업(window.confirm)이 내장 창·
패널에서 보이지 않으면 취소로 처리되어 "쿠팡으로 전송" 버튼이 무응답으로 보였다.
같은 확인을 앱 안의 확인 대화상자(confirmDialog)로 받고, 그 뒤에 비밀번호 재인증을
요구하는 순서가 유지되는지 고정한다. 화면 자체는 구동하지 않는 정적 검사다.
=========================================================
"""

import re
import unittest
from pathlib import Path

CONSOLE_JS = (
    Path(__file__).resolve().parents[1] / "app" / "web" / "console.js"
)


class LiveSendConfirmDialogTests(unittest.TestCase):

    def _send_handler(self):
        source = CONSOLE_JS.read_text(encoding="utf-8")
        start = source.index('content.querySelectorAll(".lw-live-send-btn")')
        end = source.index("send-live", start)
        return source[start:end]

    def test_send_button_no_longer_uses_native_confirm_popup(self):
        # 설명 주석에는 이름이 나올 수 있으므로, 실제 호출 형태만 금지한다.
        self.assertNotIn("window.confirm(", self._send_handler())

    def test_send_button_confirms_in_app_then_asks_recent_auth(self):
        handler = self._send_handler()
        confirm_pos = handler.index("await confirmDialog(")
        cancel_pos = handler.index("if (!sendConfirmed) return;")
        auth_pos = handler.index("await promptRecentAuthToken()")
        self.assertLess(confirm_pos, cancel_pos)
        self.assertLess(cancel_pos, auth_pos)
        self.assertTrue(re.search(r'HomezI18n\.t\("lw\.live_send_confirm"\)', handler))


if __name__ == "__main__":
    unittest.main()
