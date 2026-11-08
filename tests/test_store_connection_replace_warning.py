"""
=========================================================
Homez OS

File : tests/test_store_connection_replace_warning.py

2026-11-08 — 쿠팡 키를 다시 발급받고 새 연결(#2)을 만들자 옛 연결(#1)에 묶인 공급처 연결 3건과 주문 감지 위치가
따라오지 않았다(새 주문이 '연결 없음'으로 멈추는 상태). 같은 마켓 연결이 이미 있으면 새 연결 창의 첫 단계와 저장 단계에
'자격증명 교체'를 권하는 안내가 나오는지 고정한다. 실제 함수를 node로 실행하고 번역은 실제 ko-KR 문구를 쓴다. 바깥 호출 0.
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
KO = (ROOT / "app" / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app" / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
KEY = "sc.wizard.existing_connection_warning"


def console_function(name):
    start = CONSOLE.index(f"function {name}(")
    return CONSOLE[start:CONSOLE.index("\n  }\n", start)] + "\n  }\n"


def message(source, key):
    found = re.search(r'^\s*"' + re.escape(key) + r'":\s*"((?:[^"\\]|\\.)*)",?\s*$', source, re.M)
    return json.loads(f'"{found.group(1)}"') if found else None


def render(wizard):
    templates = {KEY: message(KO, KEY), "sc.coupang.title": message(KO, "sc.coupang.title"),
                 "sc.naver.title": message(KO, "sc.naver.title")}
    script = (
        "const TEMPLATES = " + json.dumps(templates, ensure_ascii=False) + ";\n"
        "const HomezI18n = { t: (k, vars) => (TEMPLATES[k] || k).replace(/\\{(\\w+)\\}/g, (_, n) => (vars && n in vars ? vars[n] : `{${n}}`)) };\n"
        "const SC_MARKETPLACE_LABEL_KEY = { COUPANG: 'sc.coupang.title', NAVER_SMARTSTORE: 'sc.naver.title' };\n"
        + console_function("escapeHtml") + console_function("scSameMarketplaceConnections")
        + console_function("scExistingConnectionWarning")
        + "const w = " + json.dumps(wizard, ensure_ascii=False) + ";\n"
        "if (w.connections !== undefined) w.existingSameMarket = scSameMarketplaceConnections(w.connections, w.marketplaceCode);\n"
        "console.log(JSON.stringify(scExistingConnectionWarning(w)));\n"
    )
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


COUPANG_1 = {"id": 1, "marketplace_code": "COUPANG", "display_name": "홈즈", "connection_status": "DISABLED"}
NAVER_3 = {"id": 3, "marketplace_code": "NAVER_SMARTSTORE", "display_name": "스토어", "connection_status": "CONNECTED"}


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ExistingConnectionWarningTests(unittest.TestCase):

    def test_creating_beside_an_existing_same_market_connection_points_to_rotate(self):
        html = render({"mode": "create", "marketplaceCode": "COUPANG", "connections": [COUPANG_1, NAVER_3]})
        self.assertIn('id="sc-existing-connection-warning"', html)
        self.assertIn("banner-warn", html)
        self.assertIn("&#39;홈즈&#39;", html)                       # 이름은 이스케이프된 따옴표로 묶인다
        self.assertNotIn("스토어", html)                             # 다른 마켓 연결은 적지 않는다
        self.assertIn("자격증명 교체", html)
        self.assertIn("연결 없음", html)
        self.assertNotIn("{", html)                                  # 자리표시자가 모두 채워졌다

    def test_a_disabled_or_credential_deleted_connection_still_counts(self):
        # 이번 사례에서 옛 연결은 Credential 삭제 → 비활성화 상태였다. 교체는 그 상태에서도 연결을 되살린다.
        for status in ("DISABLED", "NOT_CONFIGURED", "ERROR", "CONNECTED"):
            html = render({"mode": "create", "marketplaceCode": "COUPANG", "connections": [dict(COUPANG_1, connection_status=status)]})
            self.assertIn("sc-existing-connection-warning", html, status)

    def test_no_warning_without_a_same_market_connection(self):
        self.assertEqual(render({"mode": "create", "marketplaceCode": "COUPANG", "connections": [NAVER_3]}), "")
        self.assertEqual(render({"mode": "create", "marketplaceCode": "COUPANG", "connections": []}), "")
        self.assertEqual(render({"mode": "create", "marketplaceCode": "COUPANG", "connections": None}), "")

    def test_no_warning_while_rotating(self):
        self.assertEqual(render({"mode": "rotate", "marketplaceCode": "COUPANG", "connections": [COUPANG_1]}), "")

    def test_connection_names_are_escaped(self):
        html = render({"mode": "create", "marketplaceCode": "COUPANG",
                       "connections": [dict(COUPANG_1, display_name='<img src=x onerror="a()">')]})
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)


class WiringTests(unittest.TestCase):

    def test_both_languages_have_the_message_with_both_placeholders(self):
        for source in (KO, EN):
            text = message(source, KEY)
            self.assertIsNotNone(text)
            self.assertIn("{marketplace}", text)
            self.assertIn("{names}", text)
        self.assertIn(message(KO, "sc.connections.action_rotate"), message(KO, KEY))   # 안내가 실제 버튼 이름을 쓴다
        self.assertIn(message(EN, "sc.connections.action_rotate"), message(EN, KEY))

    def test_the_list_is_remembered_and_only_create_uses_it(self):
        load = console_function("loadStoreConnectionView")
        self.assertIn('scConnections = connections || [];', load)
        self.assertLess(load.index("scConnections = connections"), load.index("connections.length === 0"))  # 빈 목록도 기억한다
        opener = console_function("scOpenWizard")
        self.assertIn('existingSameMarket: mode === "create" ? scSameMarketplaceConnections(scConnections, marketplaceCode) : []', opener)

    def test_the_warning_shows_on_the_first_and_the_save_step(self):
        step = console_function("scRenderWizardStep")
        self.assertIn('if (scWizard.stepIndex === 0 || step.type === "save") {', step)
        self.assertIn('body.insertAdjacentHTML("afterbegin", scExistingConnectionWarning(scWizard));', step)
        self.assertGreater(step.index("insertAdjacentHTML"), step.index('step.type === "save") {\n      body.innerHTML'))  # 단계 내용을 그린 뒤 얹는다


if __name__ == "__main__":
    unittest.main()
