"""
=========================================================
Homez OS

File : tests/test_listing_wizard_draft_step_preserves_options.py

2026-09-24 실사용 라운드 — 2단계(상품 초안) 화면에는 옵션/SKU
입력란이 없는데도 저장 핸들러가 options/sku_list를 무조건 빈
배열로 보내 이미 저장된 옵션·SKU를 지워버리던 결함의 회귀 방지.
실제 화면(Wizard#1)에서 재현·수정·재확인했다(별도 문서 기록) —
이 테스트는 그 수정이 소스에 고정돼 있는지만 정적으로 확인한다
(다른 *_ui.py 테스트와 동일 패턴, 서버/브라우저를 띄우지 않는다).
=========================================================
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DraftStepPreservesOptionsTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def _draft_save_body(self):

        idx = self.js.index("function lwRenderDraftStep(content)")
        chunk = self.js[idx:idx + 5000]
        body_idx = chunk.index("lwSaveCurrentStep = async")
        return chunk[body_idx:body_idx + 2500]

    def test_draft_step_has_no_options_input(self):
        """이 화면은 옵션/SKU를 직접 편집하지 않는다는 전제 자체를
        고정한다 — 만약 나중에 입력란이 생기면 이 테스트가 깨져
        아래 보존 로직을 재검토하게 만든다."""

        idx = self.js.index("function lwRenderDraftStep(content)")
        render_body = self.js[idx:idx + 2000]
        self.assertNotIn('id="lw-draft-options"', render_body)
        self.assertNotIn('id="lw-draft-sku', render_body)

    def test_draft_save_preserves_existing_options_and_sku(self):

        save_body = self._draft_save_body()
        self.assertNotRegex(
            save_body, r"options:\s*\[\]",
            "2단계 저장이 options를 빈 배열로 하드코딩하면 이미 "
            "저장된 옵션이 사라진다 — draft.options를 그대로 보존해야 한다.",
        )
        self.assertNotRegex(
            save_body, r"sku_list:\s*\[\]",
            "2단계 저장이 sku_list를 빈 배열로 하드코딩하면 이미 "
            "저장된 SKU가 사라진다 — draft.sku_list를 그대로 보존해야 한다.",
        )
        self.assertIn("options: draft.options", save_body)
        self.assertIn("sku_list: draft.sku_list", save_body)


if __name__ == "__main__":
    unittest.main()
