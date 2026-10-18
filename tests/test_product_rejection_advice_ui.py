"""
=========================================================
Homez OS

File : tests/test_product_rejection_advice_ui.py

2026-10-18 — 반려 사유 "고치는 방법" 안내(상품 관리 AI P1a)가 쉬운 보기 서비스·화면에 붙는 방식을 고정한다. 외부 호출 0.

  - 서비스: 현재 사유가 있는 반려 상품에만 안내가 붙고, 사유가 없거나 지난 것이면 빈 목록이다.
  - 화면: 안내 문구는 모두 이스케이프하고, 관측 여부에 따라 "이 사유는/예상되는 원인"을 구분하며, 호출·수정·재제출 코드가 없다.
  - 문구: 모든 안내 종류(UNKNOWN 포함)가 한국어·영어로 제목과 첫 단계를 갖고, 두 언어의 단계 수가 같다.
=========================================================
"""

import re
import unittest
from pathlib import Path
from unittest import mock

from app.domains.easy_view.service import EasyViewService
from app.domains.order.product_rejection_advice import KNOWN_CODES

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/domains/easy_view/service.py").read_text(encoding="utf-8")


def block(lang):
    return re.search(r'"%s": \{(.*?)\n  \}' % lang, TEXTS, re.S).group(1)


def product_status(latest, submitted=None, cached=None):
    service = object.__new__(EasyViewService)
    service.db = None
    submitted = submitted or [{"seller_product_id": "111", "product_name": "상품", "wizard_id": 1}]
    with mock.patch("app.domains.easy_view.service.ProductStatusMonitorService.latest_by_product", return_value=latest),             mock.patch("app.domains.easy_view.service.ProductRejectionAiService.latest_cached", return_value=cached or {}):
        return service._product_status(submitted)


DENIED = {"status": "DENIED", "raw": "승인반려", "checked_at": "T1", "reason_for": "T1", "reason_state": "OK",
          "reason": "[기본형]: 상세설명에 포함된 모든 이미지를 다운받지 못했습니다."}


class ServicePayloadTests(unittest.TestCase):

    def test_a_current_reason_gets_its_advice(self):
        item = product_status({"111": DENIED})[0]
        self.assertEqual(item["advice"], [{"code": "IMAGE_DOWNLOAD_FAILED", "observed": True}])
        self.assertEqual(item["reason"], DENIED["reason"])  # 원문은 그대로 둔다

    def test_no_reason_or_a_stale_reason_gets_no_advice(self):
        self.assertEqual(product_status({"111": {**DENIED, "reason": "", "reason_state": "NO_REASON"}})[0]["advice"], [])
        self.assertEqual(product_status({"111": {**DENIED, "reason_for": "OLD"}})[0]["advice"], [])  # 지난 상태의 사유
        self.assertEqual(product_status({})[0]["advice"], [])                                         # 읽은 적 없음
        self.assertEqual(product_status({"111": {**DENIED, "status": "APPROVED"}})[0]["advice"], [])  # 반려가 아님

    def test_an_unseen_reason_is_unknown(self):
        item = product_status({"111": {**DENIED, "reason": "카테고리가 적합하지 않습니다"}})[0]
        self.assertEqual(item["advice"], [{"code": "UNKNOWN", "observed": False}])

    def test_the_service_only_reads_and_classifies(self):
        self.assertIn('"advice": classify_rejection(info.get("reason")) if reason_current else []', SERVICE)


class DialogTests(unittest.TestCase):

    def _dialog(self):
        return JS.split("async function productReasonDialog(")[1].split("// 공급처(온채널) 반품 요청 내용:")[0]

    def test_advice_text_is_escaped_and_observed_vs_guess_is_shown(self):
        body = self._dialog()
        self.assertIn("esc(tryT(`easy.advice.${a.code}.t`", body)
        self.assertIn("esc(t)", body)  # 각 단계 문구
        self.assertIn('T(a.observed ? "easy.advice.observed" : "easy.advice.guess")', body)
        self.assertIn('a.code === "UNKNOWN" ? ""', body)  # 모르는 사유에는 '이 사유는/예상' 머리말을 붙이지 않는다

    def test_the_dialog_still_makes_no_call_and_edits_nothing(self):
        body = self._dialog()
        for forbidden in ("api(", "apiFetch", "fetch(", "submit", "resubmit", "method:"):
            self.assertNotIn(forbidden, body, forbidden)


class TextTests(unittest.TestCase):

    def test_every_advice_kind_has_a_title_and_steps_in_both_languages(self):
        for lang in ("ko-KR", "en-US"):
            text = block(lang)
            for code in KNOWN_CODES:
                self.assertIn(f'"easy.advice.{code}.t":', text, (lang, code))
                self.assertIn(f'"easy.advice.{code}.s1":', text, (lang, code))
            for key in ("easy.advice.observed", "easy.advice.guess"):
                self.assertIn(f'"{key}":', text, (lang, key))

    def test_both_languages_have_the_same_step_counts(self):
        def steps(lang):
            found = re.findall(r'"easy\.advice\.([A-Z_]+)\.s(\d)":', block(lang))
            counts = {}
            for code, _n in found:
                counts[code] = counts.get(code, 0) + 1
            return counts

        self.assertEqual(steps("ko-KR"), steps("en-US"))
        self.assertTrue(all(count <= 4 for count in steps("ko-KR").values()))  # 화면이 s1~s4까지만 읽는다

    def test_the_advice_never_promises_that_the_app_edits_or_resubmits(self):
        for lang in ("ko-KR", "en-US"):
            joined = " ".join(re.findall(r'"easy\.advice\.[A-Za-z_.0-9]+": "([^"]*)"', block(lang)))
            for forbidden in ("자동으로 수정", "대신 고쳐", "재제출해 드", "automatically fix", "resubmit for you"):
                self.assertNotIn(forbidden, joined, forbidden)


if __name__ == "__main__":
    unittest.main()
