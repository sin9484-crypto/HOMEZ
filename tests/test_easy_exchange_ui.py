"""
=========================================================
Homez OS

File : tests/test_easy_exchange_ui.py

2026-10-27 — 쉬운 보기 승인 화면의 "쿠팡 교환 처리"(쿠팡 쓰기 8번)를 고정한다. 실제 `easy_view.js`의 함수를 Node에서 실행하고,
서버 호출 순서·문구 짝은 소스에서 확인한다. 네트워크·DB 없음.

  - 교환마다 할 수 있는 처리만 보인다: 거부는 쿠팡이 거부 가능으로 알린 교환만, 회수 송장은 회수연동전이고 송장이 없을 때만.
  - 거부 사유는 매진·고객 철회 두 가지. 확인 칸 → 한 번 더 묻기 → 전송. 목록에서 본 상태(거부는 교환 상태, 회수 송장은 회수 상태)를 함께 보낸다.
  - 쿠팡이 준 글(상품명·사유)은 이스케이프한다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

NODE = shutil.which("node")
LIST = function_source("exchangeList")
SEND = function_source("exchangeSend")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:exch\.[a-z_.]+|pick\.[a-z_]+|ret\.[a-z_]+|stock\.[a-z_]+|inq\.[a-z_]+))": ("(?:[^"\\]|\\.)*")', block)}


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class FunctionTests(unittest.TestCase):

    def test_status_labels(self):
        harness = (
            "const TABLE = JSON.parse(process.env.TABLE_JSON);\n"
            "const T = (key) => TABLE[key] !== undefined ? TABLE[key] : key;\n"
            + function_source("tryT") + "\n" + function_source("exchangeStatusLabel")
            + '\nconsole.log(JSON.stringify(["RECEIPT", "PROGRESS", "REJECT", "NEW", null].map(exchangeStatusLabel)));'
        )
        proc = subprocess.run([NODE, "-e", harness], env={**os.environ, "TABLE_JSON": json.dumps(table("ko-KR"))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout.strip()), ["교환 접수", "교환 진행", "교환 거부", "NEW", "—"])


class SourceTests(unittest.TestCase):

    def test_the_section_exists_after_the_return_section(self):
        for marker in ('id="ez-exch-anchor"', 'id="ez-exch-load"'):
            self.assertIn(marker, JS, marker)
        self.assertLess(JS.index('id="ez-pick-anchor"'), JS.index('id="ez-exch-anchor"'))

    def test_only_the_possible_actions_are_offered(self):
        self.assertIn("const reject = r.rejectable ?", LIST)
        self.assertIn('const canPickup = r.collect_status === "BeforeDirection" && !(r.return_invoices || []).length;', LIST)
        self.assertIn('<option value="SOLDOUT">', LIST)
        self.assertIn('<option value="WITHDRAW">', LIST)
        self.assertEqual(len(re.findall(r"<option value=", LIST)), 2)
        self.assertIn('T("easy.exch.nothing")', LIST)

    def test_coupang_text_is_escaped(self):
        self.assertIn('esc(i.order_item || "—")', LIST)
        self.assertIn('esc(i.target_item || "—")', LIST)
        self.assertIn('reason: r.reason || "—"', LIST)          # T(...) 결과 전체를 esc로 감싼다
        self.assertIn('${esc(T("easy.exch.meta"', LIST)

    def test_the_send_needs_the_box_then_asks_then_posts_with_the_seen_status(self):
        reject = SEND[:SEND.index("} else {")]
        pickup = SEND[SEND.index("} else {"):SEND.index("btn.disabled = true")]
        self.assertLess(reject.index("confirmEl && confirmEl.checked"), reject.index("B.askConfirm("))
        self.assertIn("expected_status: btn.dataset.ezExchStatus", reject)
        self.assertLess(pickup.index("pickupInputProblem(id, courier, invoice"), pickup.index("confirmEl && confirmEl.checked"))
        self.assertLess(pickup.index("confirmEl && confirmEl.checked"), pickup.index("B.askConfirm("))
        self.assertIn("expected_status: btn.dataset.ezExchCollect", pickup)
        self.assertIn("/coupang-exchanges/${encodeURIComponent(id)}/send", SEND)
        self.assertIn("btn.disabled = true", SEND)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.exch\.[a-z_]+)"', JS)) | {f"easy.exch.st.{s}" for s in ("receipt", "progress", "success", "reject", "cancel")}
        for key in sorted(used | set(re.findall(r'"(easy\.(?:pick|ret|stock|inq)\.[a-z_]+)"', LIST + SEND))):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        own = {k for k in ko if k.startswith("easy.exch.")}
        self.assertEqual(own, {k for k in en if k.startswith("easy.exch.")})
        self.assertEqual(own - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("이뤄지지 않는다", ko["easy.exch.reject_confirm"])
        self.assertIn("윙에서", ko["easy.exch.where"])
        self.assertIn("같은 허용", ko["easy.exch.where"])


if __name__ == "__main__":
    unittest.main()
