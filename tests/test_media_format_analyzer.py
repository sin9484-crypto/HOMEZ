"""
=========================================================
Homez OS

File : tests/test_media_format_analyzer.py

이미지 형식 분석기(`app/domains/media_asset/format_analyzer.py`, AI 설계 P1)를 고정한다.
Pillow로 만든 합성 이미지만 쓴다 — 네트워크·DB·실제 상품 이미지 없음.
=========================================================
"""

import io
import json
import random
import time
import unittest
from pathlib import Path

from PIL import Image
from PIL import ImageDraw

from app.domains.media_asset import format_analyzer as fa

MODULE_SOURCE = (Path(__file__).resolve().parents[1] / "app" / "domains" / "media_asset" / "format_analyzer.py").read_text(encoding="utf-8")


def to_bytes(image: Image.Image, fmt: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, quality=95) if fmt == "JPEG" else image.save(buffer, format=fmt)
    return buffer.getvalue()


def noise_block(width: int, height: int, seed: int, cell: int = 24) -> Image.Image:
    """사진처럼 색이 덩어리로 바뀌는 패턴(완전한 무작위 잡음은 축소하면 회색으로 평균돼 사진과 다르다)."""

    rng = random.Random(seed)
    small = Image.new("RGB", (max(1, width // cell), max(1, height // cell)))
    small.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(small.width * small.height)])
    return small.resize((width, height), Image.NEAREST)


def text_block(width: int, height: int, thickness: int = 16) -> Image.Image:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    y = 10
    while y + thickness < height:
        draw.rectangle((20, y, width - 40, y + thickness), fill="black")
        y += 40   # 줄 사이 여백은 섹션 경계(40px)보다 좁다
    return image


def tall_page(blocks: list[tuple[Image.Image, int]], width: int = 780) -> tuple[Image.Image, list[int]]:
    """(블록, 앞 여백) 목록을 세로로 쌓는다. 각 블록의 시작 y를 함께 돌려준다."""

    total = sum(gap + block.height for block, gap in blocks) + 200
    page = Image.new("RGB", (width, total), "white")
    y = 0
    starts = []
    for block, gap in blocks:
        y += gap
        starts.append(y)
        page.paste(block, (0, y))
        y += block.height
    return page, starts


class SingleImageTests(unittest.TestCase):

    def test_white_square_main_image(self):
        image = Image.new("RGB", (1000, 1000), "white")
        ImageDraw.Draw(image).ellipse((300, 300, 700, 700), fill=(200, 30, 30))
        profile = fa.analyze_image_bytes(to_bytes(image))
        self.assertEqual((profile.width, profile.height), (1000, 1000))
        self.assertEqual(profile.mime_type, "image/jpeg")
        self.assertEqual(profile.aspect_ratio, 1.0)
        self.assertEqual(profile.background_kind, "WHITE")
        self.assertGreater(profile.white_ratio, 0.7)
        self.assertFalse(profile.is_tall)
        self.assertEqual(profile.sections, ())
        self.assertEqual(fa.check_coupang_rules(profile, "MAIN"), [])

    def test_solid_color_background(self):
        profile = fa.analyze_image_bytes(to_bytes(Image.new("RGB", (800, 800), (235, 220, 190)), "PNG"))
        self.assertEqual(profile.background_kind, "SOLID")
        self.assertEqual(profile.background_color, "#ebdcbe")

    def test_complex_background(self):
        profile = fa.analyze_image_bytes(to_bytes(noise_block(400, 400, 1), "PNG"))
        self.assertEqual(profile.background_kind, "COMPLEX")
        self.assertEqual(profile.mime_type, "image/png")

    def test_transparent_png_is_judged_on_white(self):
        image = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((200, 200, 400, 400), fill=(10, 10, 200, 255))
        profile = fa.analyze_image_bytes(to_bytes(image, "PNG"))
        self.assertEqual(profile.background_kind, "WHITE")

    def test_palette_lists_dominant_colors_with_shares(self):
        image = Image.new("RGB", (1000, 1000), "white")
        ImageDraw.Draw(image).rectangle((0, 0, 999, 399), fill=(0, 0, 255))   # 40% 파랑
        profile = fa.analyze_image_bytes(to_bytes(image, "PNG"))
        colors = dict(profile.palette)
        self.assertAlmostEqual(colors["#ffffff"], 0.6, delta=0.03)
        self.assertAlmostEqual(colors["#0000ff"], 0.4, delta=0.03)


class TallImageSectionTests(unittest.TestCase):

    def _profile(self):
        page, starts = tall_page([
            (noise_block(780, 800, 7), 0),
            (text_block(780, 600), 100),
            (noise_block(780, 780, 9), 120),
        ])
        return fa.analyze_image_bytes(to_bytes(page, "PNG")), starts

    def test_sections_are_split_at_wide_blank_bands_only(self):
        profile, starts = self._profile()
        self.assertTrue(profile.is_tall)
        self.assertEqual(len(profile.sections), 3)   # 글자 줄 사이 24px 여백은 경계가 아니다
        for section, expected in zip(profile.sections, starts):
            self.assertAlmostEqual(section.start, expected, delta=16)

    def test_kind_hint_separates_photo_like_from_graphic(self):
        profile, _ = self._profile()
        self.assertEqual([s.kind_hint for s in profile.sections], ["PHOTO_LIKE", "GRAPHIC_OR_TEXT", "PHOTO_LIKE"])

    def test_content_ratio_is_higher_for_thick_text_than_for_thin_text(self):
        page, _ = tall_page([(text_block(780, 600, thickness=26), 0), (text_block(780, 600, thickness=8), 120)] * 2)
        profile = fa.analyze_image_bytes(to_bytes(page, "PNG"))
        thick, thin = profile.sections[0], profile.sections[1]
        self.assertGreater(thick.content_ratio, thin.content_ratio)
        self.assertEqual({thick.kind_hint, thin.kind_hint}, {"GRAPHIC_OR_TEXT"})

    def test_a_single_block_without_gaps_is_one_section(self):
        profile = fa.analyze_image_bytes(to_bytes(noise_block(780, 3000, 3), "PNG"))
        self.assertTrue(profile.is_tall)
        self.assertEqual(len(profile.sections), 1)

    def test_tiny_specks_are_not_sections(self):
        page = Image.new("RGB", (780, 3000), "white")
        ImageDraw.Draw(page).rectangle((300, 1000, 480, 1020), fill="black")   # 높이 20px < 최소 60px
        profile = fa.analyze_image_bytes(to_bytes(page, "PNG"))
        self.assertEqual(profile.sections, ())

    def test_a_very_tall_image_is_analyzed_quickly(self):
        page = Image.new("RGB", (1000, 20000), "white")
        draw = ImageDraw.Draw(page)
        for index in range(10):
            draw.rectangle((100, index * 2000 + 200, 900, index * 2000 + 1600), fill=(40 + index * 10, 80, 120))
        started = time.monotonic()
        profile = fa.analyze_image_bytes(to_bytes(page, "PNG"))
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(len(profile.sections), 10)
        self.assertEqual(profile.height, 20000)


class RuleCheckTests(unittest.TestCase):

    def _profile(self, width, height, color="white"):
        return fa.analyze_image_bytes(to_bytes(Image.new("RGB", (width, height), color)))

    def test_main_image_rules(self):
        small = fa.check_coupang_rules(self._profile(400, 400), "MAIN")
        self.assertEqual([f.code for f in small if f.severity == "ERROR"], ["MIN_SIZE"])
        wide = fa.check_coupang_rules(self._profile(1000, 800), "MAIN")
        self.assertIn("NOT_SQUARE", [f.code for f in wide])
        beige = fa.check_coupang_rules(self._profile(800, 800, (235, 220, 190)), "MAIN")
        self.assertIn("BACKGROUND_NOT_WHITE", [f.code for f in beige])

    def test_detail_image_rules(self):
        ok = fa.check_coupang_rules(self._profile(780, 1200), "DETAIL")
        self.assertEqual(ok, [])
        wrong_width = fa.check_coupang_rules(self._profile(1000, 1200), "DETAIL")
        self.assertEqual([f.code for f in wrong_width], ["DETAIL_WIDTH"])
        too_tall = fa.check_coupang_rules(self._profile(780, 6000), "DETAIL")
        self.assertEqual([f.code for f in too_tall if f.severity == "ERROR"], ["DETAIL_TOO_TALL"])

    def test_unknown_purpose_is_rejected(self):
        with self.assertRaises(fa.ImageFormatAnalysisError):
            fa.check_coupang_rules(self._profile(800, 800), "BANNER")


class FailClosedAndContractTests(unittest.TestCase):

    def test_unreadable_input_is_rejected_not_guessed(self):
        for bad in (b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20):
            with self.subTest(size=len(bad)):
                with self.assertRaises(fa.ImageFormatAnalysisError):
                    fa.analyze_image_bytes(bad)

    def test_truncated_image_is_rejected(self):
        data = to_bytes(noise_block(300, 300, 5), "PNG")
        with self.assertRaises(fa.ImageFormatAnalysisError):
            fa.analyze_image_bytes(data[: len(data) // 2])

    def test_unsupported_format_is_rejected(self):
        with self.assertRaises(fa.ImageFormatAnalysisError):
            fa.analyze_image_bytes(to_bytes(Image.new("RGB", (100, 100), "white"), "BMP"))

    def test_same_bytes_give_the_same_json_serializable_result(self):
        data = to_bytes(noise_block(780, 2000, 11), "PNG")
        first = fa.analyze_image_bytes(data).to_dict()
        second = fa.analyze_image_bytes(data).to_dict()
        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(first))["analyzer_version"], fa.ANALYZER_VERSION)

    def test_module_has_no_network_or_database_dependency(self):
        for forbidden in ("requests", "urllib", "socket", "sqlalchemy", "httpx", "subprocess"):
            self.assertNotIn(f"import {forbidden}", MODULE_SOURCE)
            self.assertNotIn(f"from {forbidden}", MODULE_SOURCE)


class SummaryTests(unittest.TestCase):

    def _profiles(self):
        main = fa.analyze_image_bytes(to_bytes(Image.new("RGB", (1000, 1000), "white")))
        tall, _ = tall_page([(noise_block(780, 1300, 1), 0), (noise_block(780, 1300, 2), 100)])
        return [main, main, fa.analyze_image_bytes(to_bytes(tall, "PNG"))]

    def test_summary_counts_shapes_and_flags_a_small_sample(self):
        summary = fa.summarize_profiles(self._profiles())
        self.assertEqual(summary["sample_size"], 3)
        self.assertTrue(summary["low_sample"])
        self.assertEqual(summary["shapes"], {"SQUARE": 2, "TALL": 1})
        self.assertEqual(summary["sizes"], {"1000x1000": 2})
        self.assertEqual(summary["tall_images"], 1)
        self.assertEqual(summary["sections_per_tall_image"]["max"], 2)
        self.assertEqual(summary["photo_like_share"], 1.0)

    def test_empty_summary_is_explicit(self):
        self.assertEqual(fa.summarize_profiles([]), {"sample_size": 0, "low_sample": True})

    def test_a_large_sample_is_not_flagged_low(self):
        profile = fa.analyze_image_bytes(to_bytes(Image.new("RGB", (800, 800), "white")))
        self.assertFalse(fa.summarize_profiles([profile] * fa.LOW_SAMPLE_SIZE)["low_sample"])


if __name__ == "__main__":
    unittest.main()
