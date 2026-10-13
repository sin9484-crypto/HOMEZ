"""
=========================================================
Homez OS

File : tests/test_console_no_browser_popups.py

콘솔에서 브라우저 기본 팝업(window.confirm / window.prompt)을 쓰지 않도록 고정한다(A5, 2026-10-14).

내장 패널·임베디드 창에서는 이 팝업이 보이지 않아 버튼이 무반응이 되었다(쿠팡 전송·상세설명 구성·위저드 삭제에서
실제로 겪음). 같은 용도의 앱 안 확인창 도우미(askConfirm / askText)로 바꾼 것이 유지되는지 검사한다.
예외: Naver 자격증명(Client ID/Secret) 입력 prompt 2곳은 비밀값을 화면에 그대로 보이는 입력칸에 넣을 수 없어
별도 마스킹 입력창이 생길 때까지 남긴다 — 그 외에 새로 늘어나면 실패한다.
외부 호출·DB 없음.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CONSOLE = (Path(__file__).resolve().parents[1] / "app" / "web" / "console.js").read_text(encoding="utf-8")

ALLOWED_PROMPT_KEYS = (
    "candidate_detail.naver_credential_client_id_prompt",
    "candidate_detail.naver_credential_client_secret_prompt",
)


def _code_lines():
    for number, line in enumerate(CONSOLE.splitlines(), start=1):
        if line.lstrip().startswith("//"):
            continue
        yield number, line


class NoBrowserPopupTests(unittest.TestCase):

    def test_no_window_confirm_remains(self):
        offenders = [n for n, line in _code_lines() if "window.confirm(" in line]
        self.assertEqual(offenders, [], f"window.confirm 사용 줄: {offenders}")

    def test_window_prompt_is_only_used_for_the_naver_credential_secret_entry(self):
        offenders = [
            (n, line.strip()) for n, line in _code_lines()
            if "window.prompt(" in line and not any(key in line for key in ALLOWED_PROMPT_KEYS)
        ]
        self.assertEqual(offenders, [])

    def test_helpers_exist_and_use_the_in_app_dialog(self):
        self.assertIn("async function askConfirm(", CONSOLE)
        self.assertIn("async function askText(", CONSOLE)
        self.assertRegex(CONSOLE, r"async function askText\([^)]*\)[^{]*\{[\s\S]{0,400}requireReason: true")

    def test_every_helper_call_is_awaited(self):
        # await 없이 부르면 Promise(항상 truthy)가 되어 확인 없이 진행된다 — 가장 위험한 실수.
        for number, line in _code_lines():
            for match in re.finditer(r"(?<!await )(?<!function )\b(askConfirm|askText)\(", line):
                self.fail(f"await 없는 호출: {number}행 {line.strip()}")


class HelperBehaviourTests(unittest.TestCase):

    def _run(self, confirm_result: dict) -> dict:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js를 찾을 수 없어 동작 검증을 건너뜁니다.")
        start = CONSOLE.index("async function askConfirm(")
        end = CONSOLE.index("// -----", start)
        helpers = CONSOLE[start:end]
        script = """
'use strict';
const HomezI18n = { t: (k) => k };
const seen = [];
const confirmDialog = async (opts) => { seen.push(opts); return %s; };
%s
(async () => {
  const yes = await askConfirm('m');
  const text = await askText('m');
  console.log(JSON.stringify({ yes, text, requireReason: seen[1].requireReason, body: seen[0].body }));
})();
""" % (json.dumps(confirm_result), helpers)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            result = subprocess.run(
                [node, path], capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_cancel_gives_false_and_null(self):
        out = self._run({"confirmed": False, "value": None})
        self.assertIs(out["yes"], False)
        self.assertIsNone(out["text"])
        self.assertTrue(out["requireReason"])

    def test_accept_gives_true_and_the_typed_value(self):
        out = self._run({"confirmed": True, "value": "사유"})
        self.assertIs(out["yes"], True)
        self.assertEqual(out["text"], "사유")
        self.assertEqual(out["body"], "m")


if __name__ == "__main__":
    unittest.main()
