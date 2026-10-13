"""
=========================================================
Homez OS

File : tests/test_wizard_improvements_20261014.py

러닝 무릎보호대 등록(2026-10-13~14)에서 겪은 오류의 보완 4건을 고정한다.
(상세: docs/HOMEZ_V7_REGISTRATION_LESSONS_20261014.md)

 A3) "무브랜드로 확인"이 입력 칸이 비면 아무 반응이 없던 문제 → 이유를 보여 준다
 A4) 긴 상세이미지 조각이 생성 시각 순으로 섞여 표시되던 문제 → 원본 뒤에 조각을 모아 정렬
 A6) 쿠팡 호출이 막혔을 때 "Internal Server Error (500)"만 보이던 문제 → 원인 유형이 담긴 503 안내
 A7) 쿠팡 등록 확인 실패 문구가 원인을 말하지 않던 문제 → 오류 유형·다음 행동·"등록되지 않음이 아님" 안내

외부 호출 없이 정적 파일과 순수 함수만 검사한다.
=========================================================
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.domains.marketplace_listing.coupang_live_provider import ProductListResult
from app.domains.marketplace_listing.listing_wizard_live_service import safe_list_products
from app.domains.marketplace_listing.listing_wizard_router import _coupang_call_failed_message

APP = Path(__file__).resolve().parents[1] / "app"
CONSOLE = (APP / "web" / "console.js").read_text(encoding="utf-8")
KO = (APP / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (APP / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")


class NoBrandConfirmFeedbackTests(unittest.TestCase):

    def test_empty_note_shows_a_reason_instead_of_silently_returning(self):
        self.assertIn('class="field-error lw-brand-no-brand-error"', CONSOLE)
        self.assertIn('HomezI18n.t("lw.brand_no_brand_input_required")', CONSOLE)
        # 예전의 조용한 무시 형태(`if (!value) return;`)가 남아 있으면 안 된다.
        start = CONSOLE.index("[data-lw-brand-no-brand-confirm]", CONSOLE.index("function lwWireBrandStatePanel"))
        self.assertNotIn("if (!value) return;", CONSOLE[start:start + 900])

    def test_message_exists_in_both_locales(self):
        self.assertIn('"lw.brand_no_brand_input_required"', KO)
        self.assertIn('"lw.brand_no_brand_input_required"', EN)


class MediaOrderTests(unittest.TestCase):

    def _order(self, assets):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js를 찾을 수 없어 동작 검증을 건너뜁니다.")
        start = CONSOLE.index("function lwOrderMediaAssets(list)")
        end = CONSOLE.index("async function lwRenderMediaStep", start)
        script = CONSOLE[start:end] + "\nconsole.log(JSON.stringify(lwOrderMediaAssets(%s).map((a) => a.id)));\n" % json.dumps(assets)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
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

    def test_segments_follow_their_source_not_creation_order(self):
        # 실제로 겪은 순서: 원본 7·14와 그 조각이 생성 시각으로 섞여 내려왔다.
        assets = [
            {"id": 4}, {"id": 5}, {"id": 6},
            {"id": 7}, {"id": 8, "source_asset_id": 7},
            {"id": 14}, {"id": 15, "source_asset_id": 14},
            {"id": 9, "source_asset_id": 7}, {"id": 16, "source_asset_id": 14},
            {"id": 10, "source_asset_id": 7}, {"id": 17, "source_asset_id": 14},
        ]
        self.assertEqual(self._order(assets), [4, 5, 6, 7, 8, 9, 10, 14, 15, 16, 17])

    def test_originals_without_segments_keep_id_order(self):
        self.assertEqual(self._order([{"id": 6}, {"id": 4}, {"id": 5}]), [4, 5, 6])

    def test_render_uses_the_ordering_helper(self):
        self.assertIn("assets = lwOrderMediaAssets(assets);", CONSOLE)


class CoupangCallFailureMessageTests(unittest.TestCase):

    def test_message_names_the_error_type_but_not_the_raw_message(self):
        secret = "ACCESS-KEY-should-never-appear"
        text = _coupang_call_failed_message("출고지 조회", RuntimeError(secret))
        self.assertIn("RuntimeError", text)
        self.assertIn("출고지 조회", text)
        self.assertNotIn(secret, text)

    def test_registration_lookup_failure_explains_what_to_do_and_is_not_absence(self):
        class _Boom:
            def list_products(self, name):
                raise RuntimeError("secret-url-and-key")

        result = safe_list_products(_Boom(), "러닝 무릎보호대 슬개건밴드")
        self.assertEqual(result.outcome, "UNKNOWN")
        self.assertEqual(result.error_code, "LOOKUP_FAILED")
        self.assertIn("RuntimeError", result.error_summary)
        self.assertIn("등록되지 않음", result.error_summary)  # "…'등록되지 않음'이 아닙니다"
        self.assertNotIn("secret-url-and-key", result.error_summary)

    def test_success_result_is_passed_through_untouched(self):
        class _Ok:
            def list_products(self, name):
                return ProductListResult(outcome="NOT_FOUND")

        self.assertEqual(safe_list_products(_Ok(), "x").outcome, "NOT_FOUND")

    def test_routes_convert_unexpected_failures_to_503_not_500(self):
        router = (APP / "domains" / "marketplace_listing" / "listing_wizard_router.py").read_text(encoding="utf-8")
        for label in ("출고지 조회", "반품지 조회", "브랜드 검색"):
            with self.subTest(label=label):
                self.assertIn(f'_coupang_call_failed_message("{label}", exc)', router)


if __name__ == "__main__":
    unittest.main()
