"""
=========================================================
Homez OS

File : tests/test_wizard_delete_confirm_dialog.py

위저드 삭제 확인창(A5, 2026-10-14)을 고정한다.

브라우저 기본 팝업(window.confirm/window.prompt)은 내장 패널·임베디드 창에서 보이지 않아 삭제 아이콘이
무반응이었다(상세설명 구성·쿠팡 전송 버튼과 같은 결함). 단건 삭제·선택 삭제·전체 삭제를 앱 안 확인창
(confirmDialog)으로 바꾼 것이 유지되는지, 취소하면 삭제 호출이 0회인지 Node로 실제 실행해 확인한다.
외부 호출·DB 없음.
=========================================================
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
CONSOLE = (APP / "web" / "console.js").read_text(encoding="utf-8")
CSS = (APP / "web" / "console.css").read_text(encoding="utf-8")
KO = (APP / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (APP / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    start = CONSOLE.index(f"async function {name}(")
    end = CONSOLE.index("\n  async function ", start + 10)
    return CONSOLE[start:end]


class DeleteConfirmStaticTests(unittest.TestCase):

    def test_delete_flows_no_longer_use_browser_popups(self):
        for name in ("lwDeleteSingleWizard", "lwBulkDeleteSelected", "lwDeleteAllMatchingFilter"):
            with self.subTest(function=name):
                body = _function_body(name)
                self.assertNotIn("window.confirm(", body)
                self.assertNotIn("window.prompt(", body)
                self.assertIn("await confirmDialog({", body)

    def test_delete_all_still_requires_typing_the_word_and_recent_auth(self):
        body = _function_body("lwDeleteAllMatchingFilter")
        self.assertIn("requireReason: true", body)
        self.assertIn('typed !== "삭제"', body)
        self.assertIn("promptRecentAuthToken()", body)
        self.assertLess(body.index("requireReason: true"), body.index("promptRecentAuthToken()"))

    def test_multiline_note_is_rendered_with_line_breaks(self):
        self.assertIn("#confirm-body { white-space: pre-line; }", CSS)

    def test_labels_exist_in_both_locales(self):
        for key in ("lw.delete_confirm_title", "lw.delete_confirm_ok"):
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', KO)
                self.assertIn(f'"{key}"', EN)


class DeleteConfirmBehaviourTests(unittest.TestCase):
    """단건 삭제 핸들러 본문을 그대로 꺼내 Node로 실행한다 — 취소 시 DELETE 호출 0회, 동의 시 정확히 1회."""

    def _run(self, confirmed: bool) -> dict:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js를 찾을 수 없어 동작 검증을 건너뜁니다.")
        body = _function_body("lwDeleteSingleWizard")
        harness = """
'use strict';
const BODY = %s;
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
(async () => {
  const calls = [];
  const HomezI18n = { t: (k) => k };
  const lwStatusLabel = (s) => s;
  const lwGenerateRequestId = (p) => p + '-x';
  const lwListState = { selectedIds: new Set([7]) };
  const toast = () => {};
  const lwRenderList = async () => {};
  const withButtonGuard = async (_btn, fn) => fn();
  const confirmDialog = async () => ({ confirmed: %s, value: '' });
  const apiFetch = async (url, opts) => { calls.push((opts && opts.method) || 'GET'); return { draft: {} }; };
  // 본문은 (id, row, btn)을 쓰므로 인자를 받는 래핑 함수로 만들어 호출한다.
  const wrapper = new AsyncFunction('HomezI18n', 'lwStatusLabel', 'lwGenerateRequestId', 'lwListState', 'toast',
    'lwRenderList', 'withButtonGuard', 'confirmDialog', 'apiFetch', 'id', 'row', 'btn',
    BODY.replace(/^async function lwDeleteSingleWizard\\(id, row, btn\\) \\{/, '').replace(/\\}\\s*$/, ''));
  await wrapper(HomezI18n, lwStatusLabel, lwGenerateRequestId, lwListState, toast, lwRenderList,
    withButtonGuard, confirmDialog, apiFetch, 7, { id: 7, current_step: 'SOURCE', status: 'DRAFT', version: 3 }, {});
  console.log(JSON.stringify({ deleteCalls: calls.filter((m) => m === 'DELETE').length }));
})().catch((e) => { console.error(e); process.exit(1); });
""" % (json.dumps(body), "true" if confirmed else "false")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(harness)
            path = f.name
        try:
            result = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_cancel_makes_no_delete_call(self):
        self.assertEqual(self._run(False)["deleteCalls"], 0)

    def test_accept_makes_exactly_one_delete_call(self):
        self.assertEqual(self._run(True)["deleteCalls"], 1)


if __name__ == "__main__":
    unittest.main()
