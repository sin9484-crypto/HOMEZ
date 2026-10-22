"""
=========================================================
Homez OS

File : tests/test_concept_inspection.py

2026-10-23 — 이미지 AI I2: 컨셉 결과 검사(`media_asset/concept_inspection.py`, 순수 Pillow)를 고정한다. 네트워크·DB·파일 0.

  - 제품 보존: 합성 규칙(축소만·중앙 정렬)을 그대로 따라가 제품 불투명 픽셀이 같은 값인 비율을 잰다 — 제품 위에 뭔가 얹히면 FAIL.
  - 배경 대비: 제품과 배경이 비슷하면 WARN. 대표 규격: 대표일 때만(정사각·1,000px 이상·흰 가장자리), 아니면 SKIPPED.
  - 글자 없음은 "코드가 그리지 않는다"(BY_CONSTRUCTION)이고 정책 적합은 NOT_CHECKED — 검사한 것처럼 PASS로 쓰지 않는다. FAIL이 있으면 쓸 수 없다.
=========================================================
"""

import io
import re
import unittest
from pathlib import Path

from PIL import Image
from PIL import ImageDraw

from app.domains.media_asset import concept_inspection as ci
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.composition import compose_with_background

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/media_asset/concept_inspection.py").read_text(encoding="utf-8")
ALL_CHECKS = ("PRODUCT_PRESERVATION", "BACKGROUND_CONTRAST", "MAIN_IMAGE_SPEC", "NO_TEXT_OVERLAY", "POLICY_FLAGS")


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def cutout(size=(500, 500), color=(20, 80, 200, 255), margin=100):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((margin, margin, size[0] - margin, size[1] - margin), fill=color)
    return png(image)


def compose(cutout_bytes, spec=None, canvas=(1200, 1200)):
    return compose_with_background(cutout_bytes, spec or BackgroundSpec(kind=BackgroundKind.WHITE), canvas)


def by_code(results):
    return {r.code: r for r in results}


class PreservationTests(unittest.TestCase):

    def test_a_normal_composition_preserves_the_product_exactly(self):
        source = cutout()
        for spec in (BackgroundSpec(kind=BackgroundKind.WHITE), BackgroundSpec(kind=BackgroundKind.SOLID_COLOR, color_hex="#F1F3F5"),
                     BackgroundSpec(kind=BackgroundKind.GRADIENT, color_hex="#FBF7EF", color_hex_end="#EADFCB")):
            measured = ci.product_preservation(source, compose(source, spec))
            self.assertEqual(measured["ratio"], 1.0, spec.kind)
            self.assertGreater(measured["pixels"], 1000)

    def test_it_follows_the_placement_rule_for_big_and_small_photos(self):
        # 캔버스보다 큰 누끼는 축소, 작은 누끼는 확대 없이 중앙 — 두 경우 모두 1.0이어야 한다(배치 규칙이 어긋나면 비율이 떨어진다).
        for size in ((1600, 900), (900, 1600), (300, 200), (1200, 1200), (1, 1)):
            source = cutout(size, margin=max(0, min(size) // 5))
            self.assertEqual(ci.product_preservation(source, compose(source))["ratio"], 1.0, size)

    def test_painting_over_the_product_is_detected(self):
        source = cutout()
        composed = Image.open(io.BytesIO(compose(source))).convert("RGB")
        ImageDraw.Draw(composed).rectangle((500, 500, 700, 700), fill=(255, 0, 0))
        measured = ci.product_preservation(source, png(composed))
        self.assertLess(measured["ratio"], 0.99)
        results = ci.inspect(source, png(composed), ("PRODUCT_PRESERVATION",), "ADDITIONAL")
        self.assertEqual(results[0].status, "FAIL")

    def test_a_tint_over_the_whole_picture_is_detected(self):
        source = cutout()
        tinted = Image.blend(Image.open(io.BytesIO(compose(source))).convert("RGB"), Image.new("RGB", (1200, 1200), (255, 128, 0)), 0.2)
        self.assertLess(ci.product_preservation(source, png(tinted))["ratio"], 0.5)

    def test_a_fully_transparent_cutout_has_nothing_to_preserve(self):
        empty = png(Image.new("RGBA", (100, 100), (0, 0, 0, 0)))
        self.assertEqual(ci.product_preservation(empty, compose(empty)), {"ratio": 0.0, "pixels": 0})
        self.assertEqual(ci.inspect(empty, compose(empty), ("PRODUCT_PRESERVATION",), "ADDITIONAL")[0].status, "FAIL")

    def test_semi_transparent_edges_do_not_count(self):
        soft = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        for x in range(50, 150):
            for y in range(50, 150):
                soft.putpixel((x, y), (200, 50, 50, 255 if 60 <= x < 140 and 60 <= y < 140 else 128))
        measured = ci.product_preservation(png(soft), compose(png(soft)))
        self.assertEqual(measured["pixels"], 80 * 80)
        self.assertEqual(measured["ratio"], 1.0)


class ContrastTests(unittest.TestCase):

    def test_clear_contrast_passes_and_a_similar_background_warns(self):
        source = cutout(color=(20, 30, 60, 255))
        results = by_code(ci.inspect(source, compose(source), ("BACKGROUND_CONTRAST",), "ADDITIONAL"))
        self.assertEqual(results["BACKGROUND_CONTRAST"].status, "PASS")
        self.assertGreater(results["BACKGROUND_CONTRAST"].detail["ratio"], 5)
        pale = cutout(color=(250, 250, 250, 255))
        warn = ci.inspect(pale, compose(pale), ("BACKGROUND_CONTRAST",), "ADDITIONAL")[0]
        self.assertEqual(warn.status, "WARN")
        self.assertLess(warn.detail["ratio"], 1.3)

    def test_a_warning_does_not_make_the_photo_unusable(self):
        pale = cutout(color=(250, 250, 250, 255))
        results = ci.inspect(pale, compose(pale), ("PRODUCT_PRESERVATION", "BACKGROUND_CONTRAST"), "ADDITIONAL")
        self.assertEqual([r.status for r in results], ["PASS", "WARN"])
        self.assertTrue(ci.usable(results))

    def test_contrast_math(self):
        self.assertAlmostEqual(ci.contrast_between((0, 0, 0), (255, 255, 255)), 21.0, places=1)
        self.assertEqual(ci.contrast_between((100, 100, 100), (100, 100, 100)), 1.0)
        self.assertEqual(ci.contrast_between((10, 20, 30), (200, 210, 220)), ci.contrast_between((200, 210, 220), (10, 20, 30)))

    def test_boundaries_are_pinned(self):
        self.assertEqual((ci.PRESERVATION_MIN, ci.CONTRAST_MIN, ci.MAIN_MIN_SIDE, ci.WHITE_MIN_CHANNEL), (0.99, 1.3, 1000, 250))

    def test_a_cutout_without_opaque_pixels_fails_the_contrast_check(self):
        empty = png(Image.new("RGBA", (50, 50), (0, 0, 0, 0)))
        result = ci.inspect(empty, compose(empty), ("BACKGROUND_CONTRAST",), "ADDITIONAL")[0]
        self.assertEqual((result.status, result.detail), ("FAIL", {"reason": "NO_OPAQUE_PIXELS"}))


class MainSpecTests(unittest.TestCase):

    def test_white_square_1200_passes_for_main(self):
        source = cutout()
        result = ci.inspect(source, compose(source), ("MAIN_IMAGE_SPEC",), "MAIN")[0]
        self.assertEqual(result.status, "PASS")
        self.assertEqual((result.detail["width"], result.detail["height"], result.detail["edge_min_channel"]), (1200, 1200, 255))

    def test_boundary_sizes(self):
        source = cutout()
        self.assertEqual(ci.inspect(source, compose(source, canvas=(1000, 1000)), ("MAIN_IMAGE_SPEC",), "MAIN")[0].status, "PASS")
        self.assertEqual(ci.inspect(source, compose(source, canvas=(999, 999)), ("MAIN_IMAGE_SPEC",), "MAIN")[0].status, "FAIL")
        self.assertEqual(ci.inspect(source, compose(source, canvas=(1200, 1100)), ("MAIN_IMAGE_SPEC",), "MAIN")[0].status, "FAIL")

    def test_a_non_white_edge_fails_and_off_white_edge_is_the_boundary(self):
        source = cutout()
        beige = compose(source, BackgroundSpec(kind=BackgroundKind.SOLID_COLOR, color_hex="#F7F1E6"))
        self.assertEqual(ci.inspect(source, beige, ("MAIN_IMAGE_SPEC",), "MAIN")[0].status, "FAIL")
        near = compose(source, BackgroundSpec(kind=BackgroundKind.SOLID_COLOR, color_hex="#FAFAFA"))
        self.assertEqual(ci.inspect(source, near, ("MAIN_IMAGE_SPEC",), "MAIN")[0].status, "PASS")        # 250은 통과
        under = compose(source, BackgroundSpec(kind=BackgroundKind.SOLID_COLOR, color_hex="#F9FAFA"))
        self.assertEqual(ci.inspect(source, under, ("MAIN_IMAGE_SPEC",), "MAIN")[0].status, "FAIL")      # 249는 실패

    def test_it_is_skipped_for_other_purposes_not_passed(self):
        source = cutout()
        for purpose in ("ADDITIONAL", "DETAIL"):
            result = ci.inspect(source, compose(source), ("MAIN_IMAGE_SPEC",), purpose)[0]
            self.assertEqual((result.status, result.detail), ("SKIPPED", {"reason": "NOT_MAIN_PURPOSE"}))


class HonestStatusTests(unittest.TestCase):

    def test_what_cannot_be_checked_is_not_reported_as_passed(self):
        source = cutout()
        results = by_code(ci.inspect(source, compose(source), ALL_CHECKS, "ADDITIONAL"))
        self.assertEqual(results["NO_TEXT_OVERLAY"].status, "BY_CONSTRUCTION")
        self.assertEqual(results["POLICY_FLAGS"].status, "NOT_CHECKED")
        self.assertEqual(results["PRODUCT_PRESERVATION"].status, "PASS")

    def test_only_the_required_checks_run_in_the_requested_order(self):
        source = cutout()
        results = ci.inspect(source, compose(source), ("BACKGROUND_CONTRAST", "PRODUCT_PRESERVATION"), "ADDITIONAL")
        self.assertEqual([r.code for r in results], ["BACKGROUND_CONTRAST", "PRODUCT_PRESERVATION"])
        self.assertEqual(ci.inspect(source, compose(source), (), "ADDITIONAL"), [])

    def test_an_unknown_check_is_listed_as_not_checked(self):
        source = cutout()
        result = ci.inspect(source, compose(source), ("SOMETHING_NEW",), "ADDITIONAL")[0]
        self.assertEqual((result.code, result.status), ("SOMETHING_NEW", "NOT_CHECKED"))

    def test_usable_means_no_fail(self):
        make = lambda *statuses: [ci.CheckResult("X", s, {}) for s in statuses]  # noqa: E731
        self.assertTrue(ci.usable(make("PASS", "WARN", "BY_CONSTRUCTION", "NOT_CHECKED", "SKIPPED")))
        self.assertTrue(ci.usable([]))
        self.assertFalse(ci.usable(make("PASS", "FAIL")))

    def test_results_are_plain_dicts(self):
        result = ci.CheckResult("PRODUCT_PRESERVATION", "PASS", {"ratio": 1.0})
        data = result.to_dict()
        self.assertEqual(data, {"code": "PRODUCT_PRESERVATION", "status": "PASS", "detail": {"ratio": 1.0}})
        data["detail"]["ratio"] = 0
        self.assertEqual(result.detail["ratio"], 1.0)


class PurityTests(unittest.TestCase):

    def test_no_network_database_or_files(self):
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", SOURCE, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "sqlalchemy", "subprocess", "pathlib", "numpy", "os", "boto3"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)


if __name__ == "__main__":
    unittest.main()
