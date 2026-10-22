"""
=========================================================
Homez OS

File : tests/test_batch_adjust.py

2026-10-23 — 이미지 AI R2: 일괄 밝기·색온도 보정의 순수 계산(`media_asset/batch_adjust.py`)을 고정한다. Pillow만 — 네트워크·DB·파일 0.

  - 값 검증: 정수·−50~+50·둘 다 0이면 거절(범위를 넘는 값을 조용히 깎지 않는다).
  - 계산: 밝기는 모든 채널에 같은 배율, 색온도는 빨강·파랑을 반대로(±0.6%/퍼센트). 0~255로 자른다. 256 계단 그라데이션으로 모든 값을 식과 대조한다.
  - 알파는 그대로, 완전 투명 픽셀의 색도 그대로. 형식: 알파가 있으면 PNG, JPEG는 JPEG, 그 밖은 PNG. 크기 불변, 같은 입력이면 같은 바이트.
  - 글자·워터마크를 더하지 않는다(색만 바뀐다).
=========================================================
"""

import io
import re
import unittest
from pathlib import Path

from PIL import Image

from app.core.exceptions import BadRequestException
from app.domains.media_asset import batch_adjust as ba
from app.domains.media_asset.batch_adjust import Adjustment
from app.domains.media_asset.batch_adjust import adjust_image
from app.domains.media_asset.batch_adjust import validate_adjustment

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/media_asset/batch_adjust.py").read_text(encoding="utf-8")


def encode(image, fmt="PNG", **kw):
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kw)
    return buffer.getvalue()


def decode(data):
    return Image.open(io.BytesIO(data))


def solid(color, size=(8, 6), mode="RGB"):
    return Image.new(mode, size, color)


def expected(value, gain):
    return min(255, max(0, int(value * gain + 0.5)))


class ValidationTests(unittest.TestCase):

    def test_accepts_integers_in_range_and_the_boundaries(self):
        for brightness, warmth in ((10, 0), (0, -5), (50, 50), (-50, -50), (1, -1)):
            adjustment = validate_adjustment(brightness, warmth)
            self.assertEqual((adjustment.brightness, adjustment.warmth), (brightness, warmth))

    def test_rejects_out_of_range_values_instead_of_clamping(self):
        for brightness, warmth in ((51, 0), (-51, 0), (0, 51), (0, -51), (100, 0), (0, 1000)):
            with self.assertRaises(BadRequestException, msg=str((brightness, warmth))):
                validate_adjustment(brightness, warmth)

    def test_rejects_non_integers(self):
        for bad in (True, False, 1.5, "10", None, [1], 10.0):
            with self.assertRaises(BadRequestException, msg=repr(bad)):
                validate_adjustment(bad, 5)
            with self.assertRaises(BadRequestException, msg=repr(bad)):
                validate_adjustment(5, bad)

    def test_both_zero_means_nothing_to_do(self):
        with self.assertRaises(BadRequestException):
            validate_adjustment(0, 0)
        with self.assertRaises(BadRequestException):
            adjust_image(encode(solid((1, 2, 3))), Adjustment())

    def test_constants_and_label(self):
        self.assertEqual((ba.MIN_PERCENT, ba.MAX_PERCENT, ba.MAX_BATCH, ba.JPEG_QUALITY), (-50, 50, 20, 92))
        self.assertAlmostEqual(ba.WARMTH_GAIN_PER_PERCENT, 0.006)
        self.assertEqual(Adjustment(10, -5).label(), "b+10w-5")
        self.assertEqual(Adjustment(0, 20).label(), "b+0w+20")
        self.assertTrue(Adjustment().is_noop())
        self.assertFalse(Adjustment(1, 0).is_noop())


class ColorMathTests(unittest.TestCase):

    def pixel(self, brightness, warmth, color=(100, 120, 140)):
        out, mime = adjust_image(encode(solid(color)), Adjustment(brightness, warmth))
        self.assertEqual(mime, "image/png")
        return decode(out).convert("RGB").getpixel((3, 3))

    def test_brightness_scales_every_channel_equally(self):
        self.assertEqual(self.pixel(20, 0), (120, 144, 168))
        self.assertEqual(self.pixel(-50, 0), (50, 60, 70))
        self.assertEqual(self.pixel(50, 0), (150, 180, 210))
        self.assertEqual(self.pixel(10, 0, (255, 0, 17)), (255, 0, 19))   # 위로 255에서 잘린다

    def test_warmth_raises_red_and_lowers_blue_and_cool_does_the_opposite(self):
        warm = self.pixel(0, 50)
        self.assertEqual(warm, (130, 120, 98))      # 빨강 ×1.3, 초록 그대로, 파랑 ×0.7
        cool = self.pixel(0, -50)
        self.assertEqual(cool, (70, 120, 182))      # 빨강 ×0.7, 파랑 ×1.3
        self.assertGreater(warm[0], 100)
        self.assertLess(warm[2], 140)

    def test_both_adjustments_combine(self):
        self.assertEqual(self.pixel(20, 10), (127, 144, 158))   # 밝기 ×1.2, 따뜻함 ×1.06/×0.94

    def test_every_input_value_matches_the_formula(self):
        gradient = Image.new("RGB", (256, 1))
        gradient.putdata([(v, v, v) for v in range(256)])
        for brightness, warmth in ((20, 0), (-30, 15), (0, -40), (50, 50), (-50, -50)):
            out, _ = adjust_image(encode(gradient), Adjustment(brightness, warmth))
            image = decode(out).convert("RGB")
            gain = 1.0 + brightness / 100.0
            warm = warmth * ba.WARMTH_GAIN_PER_PERCENT
            for v in range(256):
                self.assertEqual(image.getpixel((v, 0)), (expected(v, gain * (1 + warm)), expected(v, gain), expected(v, gain * (1 - warm))), (brightness, warmth, v))

    def test_values_are_clipped_not_wrapped(self):
        self.assertEqual(self.pixel(50, 50, (200, 200, 100)), (255, 255, 105))   # 빨강 390·초록 300은 255로 잘리고 파랑 105는 그대로
        self.assertEqual(self.pixel(-50, -50, (0, 0, 0)), (0, 0, 0))

    def test_black_stays_black_and_white_brightens_only_to_white(self):
        self.assertEqual(self.pixel(30, 0, (0, 0, 0)), (0, 0, 0))
        self.assertEqual(self.pixel(30, 0, (255, 255, 255)), (255, 255, 255))


class AlphaAndFormatTests(unittest.TestCase):

    def rgba(self):
        image = Image.new("RGBA", (6, 6), (100, 100, 100, 255))
        image.putpixel((0, 0), (10, 20, 30, 0))        # 완전 투명(색이 숨어 있다)
        image.putpixel((1, 0), (100, 100, 100, 128))   # 반투명
        return image

    def test_alpha_is_untouched_and_fully_transparent_color_is_kept(self):
        out, mime = adjust_image(encode(self.rgba()), Adjustment(20, 10))
        result = decode(out)
        self.assertEqual((mime, result.mode), ("image/png", "RGBA"))
        self.assertEqual(result.getpixel((0, 0)), (10, 20, 30, 0))          # 색·알파 모두 그대로
        self.assertEqual(result.getpixel((1, 0)), (127, 120, 113, 128))      # 반투명은 색만 바뀌고 알파는 그대로
        self.assertEqual(result.getpixel((3, 3)), (127, 120, 113, 255))
        self.assertEqual(sorted(a for _, _, _, a in result.getdata()), sorted(a for _, _, _, a in self.rgba().getdata()))

    def test_formats(self):
        rgb = solid((100, 100, 100))
        out, mime = adjust_image(encode(rgb, "JPEG", quality=95), Adjustment(10, 0))
        self.assertEqual((mime, decode(out).format), ("image/jpeg", "JPEG"))
        out, mime = adjust_image(encode(rgb, "PNG"), Adjustment(10, 0))
        self.assertEqual((mime, decode(out).format), ("image/png", "PNG"))
        out, mime = adjust_image(encode(rgb, "WEBP"), Adjustment(10, 0))
        self.assertEqual((mime, decode(out).format), ("image/png", "PNG"))     # 그 밖의 형식은 PNG로 낸다
        out, mime = adjust_image(encode(solid(128, mode="L")), Adjustment(10, 0))
        self.assertEqual((mime, decode(out).mode), ("image/png", "RGB"))        # 회색조는 RGB로 올려서 처리

    def test_palette_png_with_transparency_keeps_its_transparency(self):
        palette = Image.new("P", (4, 4), 0)
        palette.putpalette([200, 100, 50] + [0] * 765)
        out, mime = adjust_image(encode(palette, "PNG", transparency=0), Adjustment(10, 0))
        self.assertEqual(decode(out).mode, "RGBA")
        self.assertEqual(decode(out).getpixel((1, 1))[3], 0)

    def test_dimensions_never_change(self):
        for size in ((1, 1), (7, 13), (300, 200)):
            out, _ = adjust_image(encode(solid((9, 9, 9), size)), Adjustment(5, 5))
            self.assertEqual(decode(out).size, size)

    def test_the_result_is_deterministic(self):
        data = encode(self.rgba())
        first = adjust_image(data, Adjustment(15, -10))
        self.assertEqual(first, adjust_image(data, Adjustment(15, -10)))
        self.assertNotEqual(first[0], adjust_image(data, Adjustment(16, -10))[0])
        self.assertNotEqual(first[0], adjust_image(data, Adjustment(15, -9))[0])
        jpeg = encode(solid((90, 80, 70)), "JPEG", quality=90)
        self.assertEqual(adjust_image(jpeg, Adjustment(10, 0)), adjust_image(jpeg, Adjustment(10, 0)))

    def test_only_colors_change_no_marks_are_added(self):
        # 단색 사진은 보정 뒤에도 단색이다 — 글자·워터마크·표식이 들어가면 색이 둘 이상이 된다.
        out, _ = adjust_image(encode(solid((100, 110, 120), (40, 40))), Adjustment(25, 25))
        self.assertEqual(len(set(decode(out).convert("RGB").getdata())), 1)

    def test_unreadable_images_are_refused(self):
        for bad in (b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"x" * 20):
            with self.assertRaises(BadRequestException, msg=repr(bad[:10])):
                adjust_image(bad, Adjustment(10, 0))


class PurityTests(unittest.TestCase):

    def test_no_network_database_files_or_new_dependencies(self):
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", SOURCE, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "sqlalchemy", "subprocess", "pathlib", "numpy", "random", "os"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)

    def test_no_text_drawing(self):
        for word in ("ImageDraw", "ImageFont", ".text(", "watermark", "textbbox"):
            self.assertNotIn(word, SOURCE.split('"""', 2)[2], word)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (ValidationTests, ColorMathTests, AlphaAndFormatTests, PurityTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
