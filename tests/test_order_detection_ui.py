"""
=========================================================
Homez OS

File : tests/test_order_detection_ui.py

2026-10-16 — 주문 자동 감지 화면(`loadOrderCollectionOps`)의 정리를 고정한다.
 - 주기는 5분 고정이라 입력칸·저장 버튼이 없다.
 - 상단에 한 줄 상태(health)가 보인다.
 - 켜져 있지 않으면 "켜기"(재인증 필요)가, 켜져 있으면 "일시중지"가 보인다 — 예전에는 수동일 때 켜는 버튼이 없었다.
=========================================================
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")


def body_of(name):
    start = JS.index(f"async function {name}(")
    nxt = re.search(r"\n  (?:async )?function \w+\(|\n  const VIEW_LOADERS", JS[start + 20:])
    return JS[start:start + 20 + nxt.start()]


class OrderDetectionScreenTests(unittest.TestCase):

    def test_there_is_no_interval_input_or_save_button(self):
        body = body_of("loadOrderCollectionOps")
        self.assertNotIn("odo-interval-input", body)
        self.assertNotIn("odo-interval-save-btn", body)
        self.assertNotIn("/orders/collection-ops/interval", body)
        self.assertIn('HomezI18n.t("odo.interval_fixed"', body)

    def test_the_health_line_is_shown_first(self):
        body = body_of("loadOrderCollectionOps")
        self.assertLess(body.index("odo-health"), body.index('odo.status_heading'))
        self.assertIn("data.health_message", body)

    def test_off_modes_get_an_enable_button_and_on_gets_pause(self):
        body = body_of("loadOrderCollectionOps")
        self.assertIn('const canEnable = data.function_mode !== "AUTOMATIC";', body)
        self.assertIn('"odo.enable_btn"', body)
        self.assertIn("odo-resume-btn", body)
        self.assertIn("odo-pause-btn", body)

    def test_enabling_still_asks_for_the_password_again(self):
        body = body_of("loadOrderCollectionOps")
        resume = body.split('const resumeBtn = el("odo-resume-btn");')[1]
        self.assertLess(resume.index("promptRecentAuthToken()"), resume.index("/orders/collection-ops/resume"))
        self.assertIn('"X-Recent-Auth-Token": token', resume)

    def test_every_health_code_the_server_can_return_has_a_label_in_both_languages(self):
        for code in ("ok", "waiting", "off", "paused", "error", "failing", "blocked", "unknown"):
            for text in (KO, EN):
                self.assertIn(f'"odo.health.{code}":', text, code)
        for key in ("odo.interval_fixed", "odo.enable_btn", "odo.enable_confirm_body"):
            self.assertIn(f'"{key}":', KO)
            self.assertIn(f'"{key}":', EN)

    def test_server_health_codes_match_the_screen_labels(self):
        server = (ROOT / "app/domains/order/auto_collection_scheduler.py").read_text(encoding="utf-8")
        codes = set(re.findall(r'return "([A-Z_]+)",', server.split("def describe_health")[1].split("def _record_counts")[0]))
        self.assertEqual(codes, {"ERROR", "PAUSED", "OFF", "FAILING", "BLOCKED", "WAITING", "OK", "NEEDS_ALLOWANCE", "ENV_BLOCKED"})
        for code in codes:
            self.assertIn(f'"odo.health.{code.lower()}":', KO)


if __name__ == "__main__":
    unittest.main()
