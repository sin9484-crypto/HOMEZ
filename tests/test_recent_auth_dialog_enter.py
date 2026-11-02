"""
=========================================================
Homez OS

File : tests/test_recent_auth_dialog_enter.py

2026-11-02 — 비밀번호 재확인 창의 Enter 결함을 고정한다. 제출 버튼 없는 `<form method="dialog">`는 Enter로 창만 닫혀
작업이 사라진 것처럼 보였고, 남은 처리기 때문에 다음 확인 때 이전 작업까지 함께 실행됐다(허용이 2건씩 생김).
실제 함수를 node로 실행하고 화면 부품만 가짜로 둔다. 바깥 호출 0.
=========================================================
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = (ROOT / "app" / "web" / "console.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "web" / "console.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def console_function(name):
    start = CONSOLE.index(f"function {name}(")
    return CONSOLE[start:CONSOLE.index("\n  }\n", start)] + "\n  }\n"


HARNESS = r"""
class Part extends EventTarget {
  constructor() { super(); this.value = ""; this.hidden = false; this.disabled = false; this.textContent = ""; this.open = false; this.closes = 0; this.form = null; }
  showModal() { this.open = true; }
  close() {
    this.closes += 1;
    if (!this.open) return;
    this.open = false;
    this.dispatchEvent(new Event("close"));
  }
  querySelector() { return this.form; }
  focus() {}
}
const parts = {};
const el = (id) => (parts[id] = parts[id] || new Part());
const dialog = el("recent-auth-dialog");
dialog.form = new Part();
const form = dialog.form;
class ApiError extends Error { constructor(status) { super("api"); this.status = status; } }
const HomezI18n = { t: (k) => k };
const renderRecentAuthSummary = () => {};
const calls = [];
let reply = async () => ({ recent_auth_token: "token" + calls.length });
const apiFetch = async (path, options) => { calls.push({ path, body: JSON.parse(options.body) }); return reply(); };
const enter = () => {
  const e = new Event("submit", { cancelable: true });
  form.dispatchEvent(e);
  return e.defaultPrevented;
};
const tick = () => new Promise((r) => setTimeout(r, 5));
const track = (p) => { const box = { done: false, value: undefined }; p.then((v) => { box.done = true; box.value = v; }); return box; };
"""

SCENARIOS = {
    "enter_confirms_once": r"""
        const a = track(promptRecentAuthToken({}));
        el("ra-password").value = "pw";
        const prevented = enter();
        await tick();
        out({ prevented, calls, done: a.done, value: a.value, open: dialog.open, cleared: el("ra-password").value });
    """,
    "enter_without_password_stays_open": r"""
        const a = track(promptRecentAuthToken({}));
        const prevented = enter();
        await tick();
        out({ prevented, calls, done: a.done, open: dialog.open, error: el("ra-error").textContent });
    """,
    "enter_then_click_while_waiting_sends_once": r"""
        let release;
        reply = () => new Promise((r) => { release = () => r({ recent_auth_token: "t" }); });
        const a = track(promptRecentAuthToken({}));
        el("ra-password").value = "pw";
        enter();
        enter();
        el("ra-ok").dispatchEvent(new Event("click"));
        await tick();
        release();
        await tick();
        out({ calls: calls.length, done: a.done, value: a.value });
    """,
    "closing_some_other_way_ends_the_old_prompt": r"""
        const a = track(promptRecentAuthToken({}));
        dialog.close();
        await tick();
        const b = track(promptRecentAuthToken({}));
        el("ra-password").value = "pw";
        el("ra-ok").dispatchEvent(new Event("click"));
        await tick();
        out({ a: a.value, aDone: a.done, b: b.value, calls: calls.length });
    """,
    "a_cancelled_prompt_does_not_fire_later": r"""
        const a = track(promptRecentAuthToken({}));
        el("ra-cancel").dispatchEvent(new Event("click"));
        await tick();
        const b = track(promptRecentAuthToken({}));
        el("ra-password").value = "pw";
        enter();
        await tick();
        out({ a: a.value, b: b.value, calls: calls.length });
    """,
    "a_wrong_password_keeps_it_open_and_retry_works": r"""
        reply = async () => { throw new ApiError(401); };
        const a = track(promptRecentAuthToken({}));
        el("ra-password").value = "bad";
        enter();
        await tick();
        const after = { done: a.done, open: dialog.open, error: el("ra-error").textContent, disabled: el("ra-ok").disabled };
        reply = async () => ({ recent_auth_token: "good" });
        el("ra-password").value = "pw";
        enter();
        await tick();
        out({ after, value: a.value, calls: calls.length });
    """,
    "cancel_while_waiting_does_not_touch_the_next_prompt": r"""
        let release;
        reply = () => new Promise((r) => { release = () => r({ recent_auth_token: "late" }); });
        const a = track(promptRecentAuthToken({}));
        el("ra-password").value = "pw";
        enter();
        await tick();
        const first = release;
        el("ra-cancel").dispatchEvent(new Event("click"));
        await tick();
        reply = async () => ({ recent_auth_token: "second" });
        const b = track(promptRecentAuthToken({}));
        first();
        await tick();
        out({ a: a.value, openForSecond: dialog.open, bDone: b.done });
    """,
    "a_late_error_after_cancel_stays_out_of_the_next_prompt": r"""
        let fail;
        reply = () => new Promise((_, rej) => { fail = () => rej(new ApiError(401)); });
        track(promptRecentAuthToken({}));
        el("ra-password").value = "pw";
        enter();
        await tick();
        const first = fail;
        el("ra-cancel").dispatchEvent(new Event("click"));
        await tick();
        track(promptRecentAuthToken({}));
        first();
        await tick();
        out({ error: el("ra-error").textContent, open: dialog.open });
    """,
    "guard_blocks_only_forms_without_a_submit_button": r"""
        const run = (attrs, hasSubmit) => {
          const event = { target: { getAttribute: (n) => attrs[n] ?? null, querySelector: () => (hasSubmit ? {} : null) }, prevented: false, preventDefault() { this.prevented = true; } };
          blockImplicitDialogSubmit(event);
          return event.prevented;
        };
        out({ noSubmit: run({ method: "dialog" }, false), withSubmit: run({ method: "dialog" }, true), other: run({ method: "post" }, false), bad: (blockImplicitDialogSubmit({ target: null, preventDefault() { throw new Error("x"); } }), false) });
    """,
}


def run_scenario(name):
    script = (
        HARNESS + console_function("blockImplicitDialogSubmit") + console_function("promptRecentAuthToken")
        + "\nconst out = (v) => console.log(JSON.stringify(v));\n(async () => {\n" + SCENARIOS[name] + "\n})();\n"
    )
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class PromptBehaviourTests(unittest.TestCase):

    def test_enter_confirms_exactly_once_and_clears_the_password(self):
        r = run_scenario("enter_confirms_once")
        self.assertTrue(r["prevented"])
        self.assertEqual(r["calls"], [{"path": "/auth/recent-auth", "body": {"current_password": "pw"}}])
        self.assertEqual((r["done"], r["value"], r["open"], r["cleared"]), (True, "token1", False, ""))

    def test_enter_without_a_password_explains_and_stays_open(self):
        r = run_scenario("enter_without_password_stays_open")
        self.assertTrue(r["prevented"])
        self.assertEqual((r["calls"], r["done"], r["open"]), ([], False, True))
        self.assertEqual(r["error"], "ma.error_current_password_required")

    def test_repeated_presses_while_waiting_send_one_request(self):
        r = run_scenario("enter_then_click_while_waiting_sends_once")
        self.assertEqual((r["calls"], r["done"], r["value"]), (1, True, "t"))

    def test_a_dialog_closed_some_other_way_cannot_fire_on_the_next_confirm(self):
        r = run_scenario("closing_some_other_way_ends_the_old_prompt")
        self.assertEqual((r["aDone"], r["a"], r["calls"]), (True, None, 1))
        self.assertEqual(r["b"], "token1")

    def test_a_cancelled_prompt_stays_cancelled(self):
        r = run_scenario("a_cancelled_prompt_does_not_fire_later")
        self.assertEqual((r["a"], r["b"], r["calls"]), (None, "token1", 1))

    def test_a_wrong_password_keeps_the_same_prompt_alive(self):
        r = run_scenario("a_wrong_password_keeps_it_open_and_retry_works")
        self.assertEqual(r["after"], {"done": False, "open": True, "error": "ma.error_invalid_password", "disabled": False})
        self.assertEqual((r["value"], r["calls"]), ("good", 2))

    def test_a_late_reply_after_cancel_does_not_close_the_next_prompt(self):
        r = run_scenario("cancel_while_waiting_does_not_touch_the_next_prompt")
        self.assertIsNone(r["a"])
        self.assertTrue(r["openForSecond"])
        self.assertFalse(r["bDone"])

    def test_a_late_error_after_cancel_stays_out_of_the_next_prompt(self):
        r = run_scenario("a_late_error_after_cancel_stays_out_of_the_next_prompt")
        self.assertEqual((r["error"], r["open"]), ("", True))

    def test_the_guard_only_stops_dialog_forms_without_a_submit_button(self):
        r = run_scenario("guard_blocks_only_forms_without_a_submit_button")
        self.assertEqual(r, {"noSubmit": True, "withSubmit": False, "other": False, "bad": False})


class SourceTests(unittest.TestCase):

    def test_the_guard_is_installed_for_the_whole_page_in_the_capture_phase(self):
        self.assertIn('document.addEventListener("submit", blockImplicitDialogSubmit, true);', CONSOLE)

    def test_the_dialogs_this_guard_protects_still_have_no_submit_button(self):
        for dialog_id in ("recent-auth-dialog", "migration-approval-dialog", "pt-cancel-return-dialog", "pt-email-pref-dialog"):
            start = HTML.index(f'<dialog id="{dialog_id}"')
            body = HTML[start:HTML.index("</dialog>", start)]
            self.assertIn('method="dialog"', body, dialog_id)
            self.assertIsNone(re.search(r'<button(?![^>]*type="button")', body), dialog_id)


if __name__ == "__main__":
    unittest.main()
