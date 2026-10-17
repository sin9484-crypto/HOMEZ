"""
=========================================================
Homez OS

File : tests/test_product_status_ui.py

2026-10-17 — 쿠팡 상품 상태(승인반려·심사 중)와 반려 사유, 정산 마지막 조회 시각을 쉬운 보기에 보여 주는 화면 규칙을 정적으로 고정한다.

  - 오늘 할 일: 승인반려는 확인할 일(CHECK)로 올리고 심사 중은 진행 중으로 보여 주며, 승인완료·삭제·읽은 적 없음은 올리지 않는다.
  - 반려 사유 창은 사유를 이스케이프해서 그리고, 사유가 없으면 WING 확인을 안내하며, 수정·재제출·쿠팡 호출을 하지 않는다.
  - 상품 목록은 쿠팡에 전송한 상품에만 쿠팡 상태 칩을 붙인다.
  - 승인 센터에 "상품 상태 확인 허용하기"가 있고, 허용 목적 선택지에도 들어 있다.
  - 한국어·영어 문구가 짝이 맞고, 쓰는 키가 모두 있다.
=========================================================
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EASY = ROOT / "app/web/easy"
JS = (EASY / "easy_view.js").read_text(encoding="utf-8")
TEXTS = (EASY / "easy_texts.js").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/domains/easy_view/service.py").read_text(encoding="utf-8")


def block(lang):
    return re.search(r'"%s": \{(.*?)\n  \}' % lang, TEXTS, re.S).group(1)


class TodayRowTests(unittest.TestCase):

    def _rows(self):
        return JS.split("// 2-d2) 쿠팡에 전송한 상품의 심사 상태")[1].split("// 2-e) 상시 허용 만료 임박")[0]

    def test_only_denied_and_in_review_products_get_a_row(self):
        body = self._rows()
        self.assertIn('if (!p.status || p.status === "APPROVED" || p.status === "DELETED") return;', body)
        self.assertIn('group: denied ? "CHECK" : "PROGRESS"', body)

    def test_the_denied_row_opens_the_reason_dialog_and_nothing_else(self):
        body = self._rows()
        self.assertIn("productReasonDialog(p)", body)
        for forbidden in ("api(", "apiFetch", "submit", "resubmit"):
            self.assertNotIn(forbidden, body, forbidden)


class ReasonDialogTests(unittest.TestCase):

    def _dialog(self):
        return JS.split("async function productReasonDialog(")[1].split("// 공급처(온채널) 반품 요청 내용:")[0]

    def test_the_reason_and_names_are_escaped(self):
        body = self._dialog()
        self.assertIn("esc(p.reason)", body)
        self.assertIn("<b>${esc(v)}</b>", body)  # 이름·시각은 kv()가 이스케이프해서 그린다
        self.assertIn('kv(T("easy.pstatus.f_product"), p.product_name)', body)
        self.assertNotIn("${p.reason}", body)

    def test_without_a_reason_the_dialog_points_to_wing(self):
        body = self._dialog()
        self.assertIn('p.reason_state === "NO_REASON"', body)
        self.assertIn("easy.pstatus.no_reason", body)
        self.assertIn("WING", block("ko-KR").split('"easy.pstatus.no_reason"')[1].split("\n")[0])

    def test_the_dialog_makes_no_external_or_write_call(self):
        body = self._dialog()
        for forbidden in ("api(", "apiFetch", "method:", "POST", "PUT"):
            self.assertNotIn(forbidden, body, forbidden)


class ProductListAndApprovalTests(unittest.TestCase):

    def test_the_product_list_chip_only_appears_for_a_known_status(self):
        body = JS.split("function productStatusChip(")[1].split("function startNewProduct()")[0]
        self.assertIn("p.wizard_id === w.id && p.status", body)
        self.assertIn('if (!entry) return "";', body)
        self.assertIn("productStatusChip(w, today)", JS)

    def test_the_approval_center_can_grant_the_product_status_allowance(self):
        self.assertIn('id="ez-ap-grant-pstatus"', JS)
        self.assertIn('purpose: "PRODUCT_STATUS_CHECK"', JS)
        self.assertIn('["PRODUCT_STATUS_CHECK", "easy.purpose.pstatus"]', JS)

    def test_the_settlement_block_shows_the_last_read_time(self):
        body = JS.split("function settlementBlock(")[1].split("async function loadMoney()")[0]
        self.assertIn("today.settlement_checked_at", body)
        self.assertIn("easy.settle.checked", body)
        self.assertIn("easy.settle.not_checked", body)


class ServerShapeTests(unittest.TestCase):

    def test_the_today_payload_carries_the_status_and_the_settlement_time(self):
        self.assertIn('"product_status": self._product_status(submitted)', SERVICE)
        self.assertIn('"settlement_checked_at": self._settlement_checked_at(company_id)', SERVICE)

    def test_the_reason_is_only_shown_for_the_current_denial(self):
        body = SERVICE.split("def _product_status(")[1].split("def _settlement_checked_at(")[0]
        self.assertIn('info.get("reason_for") == info.get("checked_at")', body)
        self.assertIn('denied and', body)


class TextTests(unittest.TestCase):

    KEYS = (
        "denied_title", "denied_sub", "review_title", "review_sub", "chip_denied", "chip_review", "chip_approved", "chip_deleted",
        "reason_btn", "f_status", "f_checked", "f_product", "denied_detail", "review_detail", "dialog_title", "no_reason",
        "reason_pending", "howto",
    )

    def test_the_texts_exist_in_both_languages(self):
        for lang in ("ko-KR", "en-US"):
            text = block(lang)
            for key in self.KEYS:
                self.assertIn(f'"easy.pstatus.{key}"', text, (lang, key))
            for key in ("easy.purpose.pstatus", "easy.ap.grant_pstatus", "easy.settle.checked", "easy.settle.not_checked"):
                self.assertIn(f'"{key}"', text, (lang, key))

    def test_every_pstatus_key_used_by_the_screen_exists(self):
        used = set(re.findall(r'T\("(easy\.pstatus\.[a-z_]+)"', JS)) | set(re.findall(r'"(easy\.pstatus\.[a-z_]+)"', JS))
        existing = set(re.findall(r'"(easy\.pstatus\.[a-z_]+)":', block("ko-KR")))
        self.assertFalse(used - existing, used - existing)


class SyntaxTests(unittest.TestCase):

    def test_the_files_pass_a_node_syntax_check(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 없음")
        for name in ("easy_view.js", "easy_texts.js"):
            result = subprocess.run([node, "--check", str(EASY / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
