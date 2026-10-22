"""
=========================================================
Homez OS

File : tests/test_scene_backgrounds.py

2026-10-23 — 이미지 AI I3: 로컬 장면 배경(`media_asset/scene_backgrounds.py`, L1 — 생성이 아니다)을 고정한다. Pillow만 — 네트워크·AI·파일 0.

  - 결정적: 같은 코드·크기·색이면 같은 바이트. 코드마다 다른 그림. 모든 질감 픽셀의 밝기 배율이 정해진 세기 안에 있다(제품이 묻히지 않는다).
  - 질감은 평균 색이 바탕색에 가깝고 평평하지 않으며, 조명은 가운데가 가장자리보다 밝고 따뜻한/차가운 색조가 맞다.
  - 알 수 없는 코드·잘못된 크기·잘못된 색은 거절한다. 글자·표식을 그리는 코드가 없다.
=========================================================
"""

import hashlib
import re
import unittest
from pathlib import Path

from PIL import Image

from app.core.exceptions import BadRequestException
from app.domains.media_asset import scene_backgrounds as sb

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/media_asset/scene_backgrounds.py").read_text(encoding="utf-8")
BASE_HEX = {"LINEN": "#E8DFD0", "WOOD": "#C9A67C", "PAPER": "#F3EDE2"}


def digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def rgb(hex_):
    v = hex_.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


class DeterminismTests(unittest.TestCase):

    def test_same_input_same_bytes(self):
        for code in sb.SCENE_CODES:
            a = sb.render_scene(code, (240, 200), BASE_HEX.get(code, "#E8DFD0"))
            b = sb.render_scene(code, (240, 200), BASE_HEX.get(code, "#E8DFD0"))
            self.assertEqual(digest(a), digest(b), code)

    def test_every_code_is_a_different_picture_and_size_and_color_matter(self):
        pictures = {code: digest(sb.render_scene(code, (200, 200), "#E8DFD0")) for code in sb.SCENE_CODES}
        self.assertEqual(len(set(pictures.values())), len(sb.SCENE_CODES))
        self.assertNotEqual(digest(sb.render_scene("LINEN", (200, 200), "#E8DFD0")), digest(sb.render_scene("LINEN", (200, 200), "#D0DFE8")))
        self.assertNotEqual(digest(sb.render_scene("WOOD", (200, 200))), digest(sb.render_scene("WOOD", (202, 200))))

    def test_output_is_opaque_rgb_of_the_requested_size(self):
        for code in sb.SCENE_CODES:
            for size in ((16, 16), (64, 48), (333, 211)):
                image = sb.render_scene(code, size)
                self.assertEqual((image.mode, image.size), ("RGB", size), (code, size))


class TextureTests(unittest.TestCase):

    def test_every_pixel_stays_within_the_declared_strength(self):
        for code in sb.TEXTURES:
            base = rgb(BASE_HEX[code])
            strength = sb.TEXTURE_STRENGTH[code]
            image = sb.render_scene(code, (180, 180), BASE_HEX[code])
            for channel, value in enumerate(base):
                low, high = int(value * (1 - strength)) - 1, int(value * (1 + strength) + 1) + 1
                band = image.getchannel(channel)
                self.assertGreaterEqual(min(band.getdata()), max(0, low), (code, channel))
                self.assertLessEqual(max(band.getdata()), min(255, high), (code, channel))

    def test_the_average_color_stays_near_the_base_and_the_texture_is_not_flat(self):
        for code in sb.TEXTURES:
            base = rgb(BASE_HEX[code])
            image = sb.render_scene(code, (200, 200), BASE_HEX[code])
            average = sb.average_color(image)
            for a, b in zip(average, base):
                self.assertLess(abs(a - b), b * 0.08 + 2, (code, average, base))
            self.assertGreater(len(set(image.getchannel(0).getdata())), 4, code)        # 평평한 색이 아니다

    def test_the_strengths_are_pinned_and_ordered(self):
        self.assertEqual(sb.TEXTURE_STRENGTH, {"LINEN": 0.14, "WOOD": 0.16, "PAPER": 0.05})
        self.assertLess(sb.TEXTURE_STRENGTH["PAPER"], sb.TEXTURE_STRENGTH["LINEN"])
        self.assertLess(max(sb.TEXTURE_STRENGTH.values()), 0.2)       # 제품이 묻히지 않을 만큼만 변한다

    def test_wood_has_a_horizontal_grain_and_linen_a_regular_weave(self):
        wood = sb.render_scene("WOOD", (240, 240), BASE_HEX["WOOD"]).convert("L")
        rows = [sum(wood.crop((0, y, 240, y + 1)).getdata()) / 240 for y in range(240)]
        cols = [sum(wood.crop((x, 0, x + 1, 240)).getdata()) / 240 for x in range(240)]
        spread = lambda values: max(values) - min(values)  # noqa: E731
        self.assertGreater(spread(rows), spread(cols) * 2)            # 줄(가로 결) 사이의 밝기 차가 열 사이보다 훨씬 크다
        linen = sb.render_scene("LINEN", (240, 240), BASE_HEX["LINEN"]).convert("L")
        row = [linen.getpixel((x, 0)) for x in range(240)]
        self.assertGreater(len(set(row)), 1)


class LightTests(unittest.TestCase):

    def test_the_center_is_brighter_than_the_corners(self):
        for code in sb.LIGHTS:
            image = sb.render_scene(code, (300, 300)).convert("L")
            self.assertGreater(image.getpixel((150, 150)), image.getpixel((0, 0)) + 20, code)
            for corner in ((0, 0), (299, 0), (0, 299), (299, 299)):
                self.assertAlmostEqual(image.getpixel(corner), image.getpixel((0, 0)), delta=6, msg=(code, corner))   # 좌우·상하가 대칭

    def test_warm_is_red_over_blue_and_cool_is_blue_over_red(self):
        warm = sb.render_scene("SPOTLIGHT_WARM", (200, 200))
        cool = sb.render_scene("SPOTLIGHT_COOL", (200, 200))
        for point in ((100, 100), (5, 5), (195, 195)):
            r, g, b = warm.getpixel(point)
            self.assertGreater(r, b + 15, point)
            r, g, b = cool.getpixel(point)
            self.assertGreater(b, r + 8, point)

    def test_lights_ignore_the_base_color(self):
        a = sb.render_scene("SPOTLIGHT_WARM", (100, 100), "#000000")
        b = sb.render_scene("SPOTLIGHT_WARM", (100, 100), "#FFFFFF")
        self.assertEqual(digest(a), digest(b))


class ValidationTests(unittest.TestCase):

    def test_unknown_codes_sizes_and_colors_are_refused(self):
        for code in ("", "MARBLE", "linen", None):
            with self.assertRaises(BadRequestException, msg=repr(code)):
                sb.render_scene(code, (100, 100))
        for size in ((15, 100), (100, 15), (4097, 100), (100, 4097), (0, 0), (-5, 100), (100,), "100x100", (100.5, 100), (True, 100), None):
            with self.assertRaises(BadRequestException, msg=repr(size)):
                sb.render_scene("LINEN", size)
        for color in ("#12345", "#GGGGGG", "E8DFD", "", None, "#1234567"):
            with self.assertRaises(BadRequestException, msg=repr(color)):
                sb.render_scene("LINEN", (100, 100), color)

    def test_the_size_boundaries_are_allowed(self):
        self.assertEqual(sb.render_scene("PAPER", (16, 16)).size, (16, 16))
        self.assertEqual(sb.render_scene("PAPER", (4096, 16)).size, (4096, 16))
        self.assertEqual((sb.MIN_SIDE, sb.MAX_SIDE), (16, 4096))

    def test_hex_with_or_without_hash_and_any_case(self):
        a = sb.render_scene("PAPER", (64, 64), "#f3ede2")
        self.assertEqual(digest(a), digest(sb.render_scene("PAPER", (64, 64), "F3EDE2")))

    def test_the_full_size_render_is_fast_enough(self):
        import time

        started = time.time()
        for code in sb.SCENE_CODES:
            sb.render_scene(code, (1200, 1200), BASE_HEX.get(code, "#E8DFD0"))
        self.assertLess(time.time() - started, 15)


class PurityTests(unittest.TestCase):

    def test_no_network_ai_files_or_text_drawing(self):
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", SOURCE, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "sqlalchemy", "subprocess", "pathlib", "numpy", "os", "boto3"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)
        code = SOURCE.split('"""', 2)[2]
        for word in ("ImageDraw", "ImageFont", ".text(", "open(", "watermark"):
            self.assertNotIn(word, code, word)

    def test_the_seed_is_fixed_so_results_do_not_depend_on_the_clock(self):
        self.assertIn("random.Random(seed)", SOURCE)
        self.assertNotIn("time.", SOURCE.split('"""', 2)[2])
        self.assertNotIn("random.random()", SOURCE)

    def test_the_seeds_are_pinned_because_changing_one_changes_every_saved_picture_of_that_scene(self):
        # 같은 컨셉을 다시 만들면 같은 자산으로 합쳐지는 것(멱등)은 이 값이 그대로라는 전제다. 바꾸려면 새 컨셉 버전으로 다뤄야 한다.
        self.assertEqual(sb._SEED, {sb.LINEN: 11, sb.WOOD: 23, sb.PAPER: 37, sb.SPOTLIGHT_WARM: 41, sb.SPOTLIGHT_COOL: 53})


if __name__ == "__main__":
    unittest.main()
