"""
=========================================================
Homez OS

File : tests/test_option_link_console_ui.py

2026-09-21 옵션 연결 — 콘솔 화면(정적) 계약 검사. 브라우저 동작은 격리 E2E로 따로
검증하고, 여기서는 화면 문구와 서버 코드가 어긋나 "키 이름이 그대로 노출"되는 사고를
막는다:
  - 화면이 쓰는 모든 `olT("...")` 키가 ko-KR/en-US 양쪽에 있다
  - 서버가 돌려줄 수 있는 연결 상태·준비 미완료 사유·식별자 부착 결과 코드마다 화면
    문구가 있다
  - 공급 옵션은 자유 입력이 아니라 조회 결과에서 고르는 <select>다(이름 추측 금지)
=========================================================
"""

import os
import re
import unittest

from tests.test_i18n import _load_js_object_literal

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO, "app", "web")
SERVICE = os.path.join(REPO, "app", "domains", "purchase_task", "supplier_option_link_service.py")
WIZARD_SERVICE = os.path.join(
    REPO, "app", "domains", "marketplace_listing", "listing_wizard_option_link_service.py",
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class OptionLinkConsoleContractTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.js = _read(os.path.join(WEB, "console.js"))
        cls.ko = _load_js_object_literal(os.path.join(WEB, "i18n", "ko-KR.js"), "HOMEZ_I18N_CATALOG_KO_KR")
        cls.en = _load_js_object_literal(os.path.join(WEB, "i18n", "en-US.js"), "HOMEZ_I18N_CATALOG_EN_US")
        cls.service = _read(SERVICE)
        cls.wizard_service = _read(WIZARD_SERVICE)

    def _ol_keys_in_js(self):
        keys = set()
        for call in re.findall(r"olT\(([^)]*?)\)", self.js):
            keys.update(re.findall(r'"([a-z_.]+)"', call))
        return keys

    def test_every_referenced_key_exists_in_both_catalogs(self):
        keys = self._ol_keys_in_js()
        self.assertGreater(len(keys), 30)
        for key in sorted(keys):
            with self.subTest(key=key):
                self.assertIn(f"ol.{key}", self.ko)
                self.assertIn(f"ol.{key}", self.en)

    def test_every_server_link_state_has_a_label(self):
        states = {"ACTIVE", "NEEDS_REVIEW", "DISABLED", "MISSING", "NO_LINK",
                  "IDENTIFIER_CONFLICT", "UNAVAILABLE", "STORE_UNRESOLVED", "NO_ORDER_ITEM"}
        # 서버 상수와 어긋나지 않는지 — 서비스가 정의한 상태 문자열이 위 집합에 다 들어 있어야 한다.
        for name in re.findall(r'^STATE_[A-Z_]+ = "([A-Z_]+)"', self.service, re.M):
            self.assertIn(name, states)
        for state in states:
            with self.subTest(state=state):
                self.assertIn(f"ol.state.{state.lower()}", self.ko)
                self.assertIn(f"ol.state.{state.lower()}", self.en)

    def test_every_not_ready_reason_and_sync_outcome_has_a_label(self):
        reasons = set(re.findall(r'reason = "([A-Z_]+)"', self.service))
        reasons |= set(re.findall(r'"reason": "([A-Z_]+)"', self.service))
        reasons |= set(re.findall(r'reason = "([A-Z_]+)"', self.wizard_service))
        self.assertTrue({"LINK_MISSING", "IDS_UNCONFIRMED", "SCOPE_UNCONFIRMED",
                         "NOT_REGISTERED"} <= reasons, reasons)
        for reason in sorted(reasons):
            with self.subTest(reason=reason):
                self.assertIn(f"ol.wiz_reason.{reason.lower()}", self.ko)
                self.assertIn(f"ol.wiz_reason.{reason.lower()}", self.en)

        outcomes = set(re.findall(r'outcomes\[sku\] = "([A-Z_]+)"', self.service))
        outcomes |= {"ATTACHED", "ALREADY_ATTACHED"}
        for outcome in sorted(outcomes):
            with self.subTest(outcome=outcome):
                self.assertIn(f"ol.sync_outcome.{outcome.lower()}", self.ko)
                self.assertIn(f"ol.sync_outcome.{outcome.lower()}", self.en)

    def test_supplier_option_is_chosen_from_lookup_results_not_typed(self):
        picker = self.js[self.js.index("function olPickerHtml"):self.js.index("function olWirePicker")]
        self.assertIn('<select id="${prefix}-option" disabled>', picker)
        self.assertNotIn('id="${prefix}-option"><input', picker)
        self.assertNotRegex(picker, r'<input[^>]*id="\$\{prefix\}-option"')
        wiring = self.js[self.js.index("function olWirePicker"):self.js.index("// ---------------- 매입 검토 화면")]
        # 상품코드나 매입 계정이 바뀌면 이전 조회 결과(옵션 선택)는 무효화된다.
        self.assertIn('codeEl.addEventListener("input", invalidate)', wiring)
        self.assertIn('connEl.addEventListener("change", invalidate)', wiring)
        # 저장은 조회 결과에서 고른 옵션이 있을 때만 가능하다.
        self.assertIn("if (!looked || !optionEl.value) return;", wiring)

    def test_screen_sends_no_sales_account_or_sku_for_the_task_save(self):
        """주문 화면의 저장 요청은 서버가 판매 계정·판매자 SKU를 정하므로 보내지 않는다."""

        start = self.js.index("await apiFetch(`/purchase-tasks/${task.id}/supplier-option-link`, {\n          method: \"PUT\"")
        body = self.js[start:start + 400]
        for forbidden in ("store_connection_id", "channel_sku", "purchase_connection_id"):
            self.assertNotIn(forbidden, body)

    def test_readiness_pill_uses_only_the_server_all_ready_flag(self):
        self.assertIn("r.all_ready", self.js)
        self.assertNotIn("selected_ready ?", self.js)


if __name__ == "__main__":
    unittest.main()
