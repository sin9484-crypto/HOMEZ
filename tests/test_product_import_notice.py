"""
=========================================================
Homez OS

File : tests/test_product_import_notice.py

개선 A9(2026-10-14) — URL로 가져온 공급처 고시를 5단계 정보고시 칸의 "제안"으로 쓰는 로직을 고정한다.
순수 함수만 검사한다(네트워크·DB 없음).
=========================================================
"""

import unittest
from pathlib import Path

from app.domains.product_import.notice_suggestions import NOTICE_EVIDENCE_PREFIX
from app.domains.product_import.notice_suggestions import decode_notice_rows
from app.domains.product_import.notice_suggestions import encode_notice_rows
from app.domains.product_import.notice_suggestions import suggest_notice_values

APP = Path(__file__).resolve().parents[1] / "app"
CONSOLE = (APP / "web" / "console.js").read_text(encoding="utf-8")
KO = (APP / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (APP / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")

# 2026-10-14 실제 온채널 고시 표(러닝 무릎보호대)에서 읽은 값
ONCH_ROWS = [
    ("품명 및 모델명", "오조 슬개건 러닝 무릎보호대"),
    ("제조년월일", "2025년 12월"),
    ("품질보증기준", "없음"),
    ("A/S 책임자와 전화번호", "070-8983-8490"),
    ("KC 인증유형", "해당사항없음"),
    ("KC 인증기관", "해당사항없음"),
    ("KC 인증번호", "해당사항없음"),
    ("크기/중량", "약 15x15cm"),
    ("색상", "블랙"),
    ("재질", "나일론, EVA"),
    ("제품 구성", "2개 1set"),
    ("동일모델의 출시년월", "25년 12월"),
    ("제조자/수입자", "JJ협력사"),
    ("제조국", "중국"),
    ("상품별 세부 사양", "없음"),
]
# 쿠팡 "스포츠용품" 정보고시 칸(실제 화면에서 확인한 12개)
COUPANG_LABELS = [
    "품명 및 모델명", "KC 인증정보", "크기, 중량", "색상", "재질", "제품 구성", "출시년월",
    "제조자(수입자)", "제조국", "상품별 세부 사양", "품질보증기준", "A/S 책임자와 전화번호",
]


class SuggestionTests(unittest.TestCase):

    def test_real_world_labels_are_matched_across_naming_differences(self):
        result = suggest_notice_values(ONCH_ROWS, COUPANG_LABELS)
        self.assertEqual(result["크기, 중량"], "약 15x15cm")
        self.assertEqual(result["제조자(수입자)"], "JJ협력사")
        self.assertEqual(result["출시년월"], "25년 12월")
        self.assertEqual(result["제조국"], "중국")
        self.assertEqual(result["색상"], "블랙")
        self.assertEqual(result["재질"], "나일론, EVA")
        self.assertEqual(result["제품 구성"], "2개 1set")
        self.assertEqual(result["품명 및 모델명"], "오조 슬개건 러닝 무릎보호대")
        self.assertEqual(result["KC 인증정보"], "해당사항없음")

    def test_contact_fields_are_never_suggested_from_the_supplier(self):
        result = suggest_notice_values(ONCH_ROWS, COUPANG_LABELS)
        self.assertNotIn("A/S 책임자와 전화번호", result)
        self.assertNotIn("070-8983-8490", " ".join(result.values()))
        for label in ("소비자상담관련 전화번호", "연락처", "고객센터"):
            self.assertEqual(suggest_notice_values([(label, "02-000-0000")], [label]), {})

    def test_kc_is_suggested_only_when_every_kc_row_says_not_applicable(self):
        rows = [("KC 인증유형", "해당사항없음"), ("KC 인증번호", "XU100-1234")]
        self.assertNotIn("KC 인증정보", suggest_notice_values(rows, ["KC 인증정보"]))
        self.assertNotIn("KC 인증정보", suggest_notice_values([("색상", "블랙")], ["KC 인증정보"]))

    def test_unmatched_fields_are_left_out_not_guessed(self):
        self.assertEqual(suggest_notice_values(ONCH_ROWS, ["영양성분", "원재료명 및 함량"]), {})
        self.assertEqual(suggest_notice_values([], COUPANG_LABELS), {})


class EvidenceEncodingTests(unittest.TestCase):

    def test_round_trip_keeps_rows_but_drops_contact_rows_up_front(self):
        text = encode_notice_rows(ONCH_ROWS)
        self.assertTrue(text.startswith(NOTICE_EVIDENCE_PREFIX))
        self.assertNotIn("070-8983-8490", text)
        decoded = decode_notice_rows(text)
        self.assertIn(("제조국", "중국"), decoded)
        self.assertNotIn("A/S 책임자와 전화번호", [label for label, _ in decoded])

    def test_encoded_text_always_fits_the_evidence_column(self):
        many = [(f"항목{i}", "값" * 150) for i in range(80)]
        text = encode_notice_rows(many)
        self.assertLessEqual(len(text), 2000)
        self.assertGreater(len(decode_notice_rows(text)), 0)

    def test_bad_or_foreign_summaries_decode_to_nothing(self):
        for summary in (None, "", "비공개 등록: product_name=x", NOTICE_EVIDENCE_PREFIX + "{broken", NOTICE_EVIDENCE_PREFIX + "[]"):
            with self.subTest(summary=summary):
                self.assertEqual(decode_notice_rows(summary), [])


class ScreenWiringTests(unittest.TestCase):

    def test_button_fills_only_empty_inputs_and_never_ticks_the_confirmation(self):
        start = CONSOLE.index("function lwAttachNoticeFillButton(")
        block = CONSOLE[start:CONSOLE.index("function lwRenderNoticeFields(", start)]
        self.assertIn("filter((input) => !input.value.trim())", block)
        self.assertIn("!input.value.trim()", block.split("forEach((input) => {")[1])
        self.assertNotIn("lw-policy-notice-confirmed", block)
        self.assertNotIn(".checked = true", block)
        self.assertIn("/product-import/notice-suggestions", block)

    def test_import_screen_sends_the_notice_rows_and_labels_exist(self):
        self.assertIn("notice_rows: (data.notice_rows || [])", CONSOLE)
        for key in ("notice_fill_btn", "notice_fill_nothing_empty", "notice_fill_no_source", "notice_fill_done",
                    "notice_fill_none_matched", "notice_fill_failed"):
            with self.subTest(key=key):
                self.assertIn(f'"lw.{key}"', KO)
                self.assertIn(f'"lw.{key}"', EN)


if __name__ == "__main__":
    unittest.main()
