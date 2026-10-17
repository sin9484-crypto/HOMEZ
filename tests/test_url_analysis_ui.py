"""
=========================================================
Homez OS

File : tests/test_url_analysis_ui.py

2026-10-18 — URL 상품 분석(Phase E5) 화면 배선을 소스 수준에서 고정한다(바깥 호출 0).

  - AI 분석은 확인창을 거친 뒤에만 요청한다. 부가세는 선택 전에는 비어 있고(자동 선택 없음) 계산 요청에도 기본값이 들어가지 않는다.
  - 수수료율은 화면에 하드코딩하지 않는다. 저장된 값이 없으면 저장 칸을 보여 주고, 저장 뒤에 다시 계산한다.
  - 계산은 서버의 /calculate, 분석은 /analyze만 쓴다. 초안 생성 때 계산 스냅샷을 함께 보낸다. 직접 계산(URL 없음) 영역이 있다.
  - 한국어·영어 문구가 짝을 이룬다.
=========================================================
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")


def block():
    start = CONSOLE.index("// ---- AI 분석 · 단가 · 이익 계산 (2026-10-18, Phase E5) ----")
    return CONSOLE[start:CONSOLE.index("// ---- /AI 분석 · 단가 · 이익 계산 ----", start)]


class PanelTests(unittest.TestCase):

    def test_analysis_asks_first_and_uses_only_the_analyze_endpoint(self):
        src = block()
        self.assertLess(src.index("askConfirm("), src.index('"/product-import/analyze"'))
        self.assertIn("source_url", src)
        self.assertEqual(src.count('"/product-import/analyze"'), 1)

    def test_calculation_uses_the_server_and_never_a_default_vat(self):
        src = block()
        self.assertIn('"/product-import/calculate"', src)
        self.assertIn('<option value="">', src)                       # 첫 선택지는 비어 있다
        self.assertNotIn('vat_state: "', src)                         # 요청 본문에 부가세 기본값을 넣지 않는다
        self.assertIn('if (val("vat_state")) body.vat_state', src)     # 선택했을 때만 보낸다
        self.assertIn('if (val("supply_price")) body.supply_price', src)
        self.assertIn('if (val("shipping_cost")) body.shipping_cost', src)  # 비어 있으면 보내지 않아 서버가 보류한다

    def test_the_fee_rate_is_not_hard_coded_and_a_missing_one_asks_for_a_saved_value(self):
        src = block()
        self.assertNotIn("10.8", src)
        self.assertNotIn("0.108", src)
        self.assertIn("/product-import/channel-fees", src)
        self.assertLess(src.index("PUT"), src.index("await calc()"))   # 저장 뒤에 다시 계산한다
        self.assertIn("USER_CONFIRMED", src)

    def test_ai_read_money_values_need_a_confirmation_that_editing_cancels(self):
        src = block()
        self.assertIn("data-aia-ai-confirm", src)
        self.assertIn("ai_sourced: Array.from(aiSourced)", src)
        self.assertIn("ai_values_confirmed:", src)
        self.assertIn("aiSourced.delete(name)", src)   # 사용자가 값을 직접 고치면 더 이상 'AI가 읽은 값'이 아니다

    def test_high_risks_need_an_explicit_acknowledgement(self):
        src = block()
        self.assertIn('r.severity === "HIGH"', src)
        self.assertIn("data-aia-ack", src)
        self.assertIn("acknowledged_risks", src)

    def test_manual_calculation_without_a_url_exists_and_shares_the_same_form(self):
        src = block()
        self.assertIn("lwAiaEnsureManual", src)
        self.assertEqual(CONSOLE.count("lwAiaEnsureManual();"), 1)    # 열 때 한 번 보장한다
        self.assertIn("lwAiaMountCalc(details.querySelector", src)

    def test_the_snapshot_goes_with_the_draft_and_nothing_is_stored_in_the_browser(self):
        self.assertIn("analysis_report: lwUrlImportState.report || null,", CONSOLE)
        for storage in ("localStorage", "sessionStorage", "document.cookie"):
            self.assertNotIn(storage, block())

    def test_the_panel_is_mounted_inside_the_url_result(self):
        self.assertIn('<div id="lw-aia-host"></div>', CONSOLE)
        self.assertIn('lwAiaAttach(el("lw-aia-host"), data);', CONSOLE)

    def test_the_results_say_competitor_prices_were_not_analyzed_and_list_assumptions(self):
        src = block()
        self.assertIn("no_competitor", src)
        self.assertIn("assume_title", src)


    def test_percentages_are_converted_without_float_noise_and_times_are_shown_locally(self):
        src = block()
        self.assertIn("aiaPct(", src)
        self.assertNotIn("/ 100)", src.replace("(Number(percent) / 100)", ""))   # 날것의 /100 변환이 남아 있지 않다
        self.assertIn("lsParseUtc(fee.confirmed_at)", src)                        # UTC 시각을 그대로 보이지 않는다


class TextTests(unittest.TestCase):

    def used_keys(self):
        src = block()
        keys = set(re.findall(r'aiaT\("([a-z_0-9]+)"', src))
        keys |= set(re.findall(r'"((?:verdict|src)_[a-z_]+)"', src))
        keys |= {f"vat_{v}" for v in ("included", "excluded", "exempt")}
        return keys

    def test_every_used_text_exists_in_both_languages(self):
        keys = self.used_keys()
        self.assertGreaterEqual(len(keys), 50)
        for key in keys:
            self.assertIn(f'"lw.aia_{key}":', KO, key)
            self.assertIn(f'"lw.aia_{key}":', EN, key)

    def test_both_languages_have_the_same_keys(self):
        self.assertEqual(set(re.findall(r'"(lw\.aia_[a-z_0-9]+)":', KO)), set(re.findall(r'"(lw\.aia_[a-z_0-9]+)":', EN)))

    def test_no_template_variable_is_used_twice_in_one_text(self):
        # i18n 치환은 같은 자리표시를 첫 번째만 바꾼다 — 한 문구에 같은 {이름}을 두 번 쓰면 화면에 그대로 남는다(가상 공간 시험에서 발견)
        for source in (KO, EN):
            for key in set(re.findall(r'"(lw\.aia_[a-z_0-9]+)":', source)):
                text = re.search(rf'"{re.escape(key)}": "([^"]*)"', source).group(1)
                names = re.findall(r"\{(\w+)\}", text)
                self.assertEqual(len(names), len(set(names)), key)

    def test_every_template_variable_matches_between_languages(self):
        for key in set(re.findall(r'"(lw\.aia_[a-z_0-9]+)":', KO)):
            ko = re.search(rf'"{re.escape(key)}": "([^"]*)"', KO).group(1)
            en = re.search(rf'"{re.escape(key)}": "([^"]*)"', EN).group(1)
            self.assertEqual(set(re.findall(r"\{(\w+)\}", ko)), set(re.findall(r"\{(\w+)\}", en)), key)


if __name__ == "__main__":
    unittest.main()
