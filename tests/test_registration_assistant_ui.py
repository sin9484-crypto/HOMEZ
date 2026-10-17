"""
=========================================================
Homez OS

File : tests/test_registration_assistant_ui.py

2026-10-17 — 5단계 "등록 도우미" 패널(console.js)의 화면 규칙을 정적으로 고정한다(소스 검사 + Node 구문 검사).

- 패널은 5단계 렌더 끝에서 붙고, 도우미 API 두 개(제안·저장)만 부른다 — 쿠팡 등록·제출·승인은 부르지 않는다.
- 쿠팡 조회가 들어가는 두 버튼은 누르기 전에 반드시 확인창을 거친다(확인 → 요청 순서).
- 제안을 저장하려면 먼저 제안을 봐야 하고(저장 버튼은 처음엔 꺼져 있다), 막힘이 있으면 저장 버튼이 켜지지 않는다.
- 서버가 돌려준 문구는 이스케이프해서 그린다. 한국어·영어 문구가 짝이 맞고, 쓰는 키가 모두 있다.
=========================================================
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")


def assistant_body():
    return CONSOLE.split("function lwAttachRegistrationAssistant(")[1].split("// A9(2026-10-14)")[0]


class WiringTests(unittest.TestCase):

    def test_the_panel_is_attached_at_the_end_of_the_step_5_render(self):
        step = CONSOLE.split("async function lwRenderFulfillmentStep(")[1].split("lwLoadChannelPolicyStatus(block")[0]
        self.assertIn("lwAttachRegistrationAssistant(content, w);", step)

    def test_only_the_two_assistant_routes_and_the_wizard_reload_are_called(self):
        calls = re.findall(r"apiFetch\(`([^`]+)`", assistant_body())
        self.assertEqual(
            sorted(set(calls)),
            sorted({
                "/listing-wizards/${w.id}/registration-assistant/propose",
                "/listing-wizards/${w.id}/registration-assistant/apply",
                "/listing-wizards/${w.id}",
            }),
        )
        for forbidden in ("/submit", "/approve", "/bulk-submit", "live-send", "coupang/", "/marketplace-listings/"):
            self.assertNotIn(forbidden, assistant_body(), forbidden)

    def test_each_external_lookup_is_confirmed_before_the_request(self):
        body = assistant_body()
        for route in ("registration-assistant/propose", "registration-assistant/apply"):
            handler_start = body.index(route)
            before = body[:handler_start]
            self.assertIn("await askConfirm(", before)
        propose = body.split('data-lw-assist-propose]").addEventListener')[1].split("applyBtn.addEventListener")[0]
        self.assertLess(propose.index("await askConfirm("), propose.index("registration-assistant/propose"))
        apply = body.split("applyBtn.addEventListener")[1]
        self.assertLess(apply.index("await askConfirm("), apply.index("registration-assistant/apply"))

    def test_saving_needs_a_ready_proposal_first(self):
        body = assistant_body()
        self.assertIn("data-lw-assist-apply disabled", body)
        self.assertIn("applyBtn.disabled = !data.ready;", body)

    def test_server_messages_are_escaped_when_drawn(self):
        body = assistant_body()
        self.assertIn("escapeHtml(n.message)", body)
        self.assertIn("escapeHtml(u)", body)
        self.assertIn("escapeHtml(err.message", body)
        self.assertNotIn("${n.message}", body)
        self.assertNotIn("${err.message}", body)

    def test_the_wizard_is_reloaded_and_the_step_redrawn_after_a_successful_save(self):
        body = assistant_body()
        self.assertIn("if (data.applied)", body)
        self.assertIn("lwState.wizard = await apiFetch(", body)
        self.assertIn("await lwRenderStep();", body)

    def test_the_version_is_sent_only_for_the_save_request(self):
        body = assistant_body()
        self.assertIn("if (withVersion) body.expected_version = lwState.wizard.version;", body)
        self.assertIn("requestBody(false)", body)
        self.assertIn("requestBody(true)", body)


class TextTests(unittest.TestCase):

    def _keys(self, source):
        return set(re.findall(r'"(lw\.assistant_[a-z_]+)":', source))

    def test_korean_and_english_texts_match(self):
        self.assertEqual(self._keys(KO), self._keys(EN))
        self.assertGreaterEqual(len(self._keys(KO)), 24)

    def test_every_key_the_panel_uses_exists(self):
        used = set(re.findall(r'HomezI18n\.t\("(lw\.assistant_[a-z_]+)"', assistant_body()))
        used |= {f"lw.assistant_level_{level}" for level in ("blocker", "caution", "info")}
        used |= {"lw.assistant_ready", "lw.assistant_not_ready"}
        self.assertFalse(used - self._keys(KO), used - self._keys(KO))

    def test_the_confirmation_says_it_is_a_coupang_lookup_and_not_a_registration(self):
        self.assertIn("조회 1회", KO.split('"lw.assistant_propose_confirm_body"')[1].split("\n")[0])
        self.assertIn("등록하지는 않아요", KO.split('"lw.assistant_apply_confirm_body"')[1].split("\n")[0])


class SyntaxTests(unittest.TestCase):

    def test_the_files_pass_a_node_syntax_check(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 없음")
        for path in ("app/web/console.js", "app/web/i18n/ko-KR.js", "app/web/i18n/en-US.js"):
            result = subprocess.run([node, "--check", str(ROOT / path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
