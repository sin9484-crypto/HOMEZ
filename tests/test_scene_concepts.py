"""
=========================================================
Homez OS

File : tests/test_scene_concepts.py

2026-10-23 — 이미지 AI I3: 로컬 장면(L1) 컨셉 5종과 `BackgroundKind.SCENE` 합성, 명세 검증 규칙을 고정한다. 임시 메모리 이미지만 쓴다 — DB·네트워크 0.

  - 레지스트리: 처음 6종(L0)은 그대로이고 뒤에 L1 장면 컨셉 5종이 붙는다. 장면 컨셉은 대표(MAIN)에 쓸 수 없고, 제품 보존·배경 대비·글자 없음 검사를 요구하며, 생성형(L2)·소품·AI 표기가 필요 없다.
  - 명세 검증: L0에 장면 배경, 모르는 장면 코드, 장면 종류 불일치, 장면 아닌데 장면 코드, 질감 바탕색 없음·조명에 색, 끝 색 지정은 모두 문제로 잡는다(고쳐 쓰지 않는다).
  - 합성: 장면 배경은 장면 코드가 있어야 하고 알 수 없는 배경 종류는 여전히 거절한다. 합성해도 제품 픽셀은 그대로다.
=========================================================
"""

import io
import unittest
from dataclasses import replace

from PIL import Image
from PIL import ImageDraw

from app.core.exceptions import BadRequestException
from app.domains.media_asset import image_concepts as ic
from app.domains.media_asset import scene_backgrounds as sb
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.composition import compose_with_background

L1_CODES = ["LINEN_NATURAL", "WOOD_WARM", "PAPER_SOFT", "SPOTLIGHT_WARM", "SPOTLIGHT_COOL"]
FIRST_SIX = ["CLEAN_WHITE", "SOFT_BEIGE", "FRESH_MINT", "BLUSH_PINK", "CALM_GRAY", "MIDNIGHT_PREMIUM"]


def cutout_png(color=(20, 30, 70, 255), size=(400, 400)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((80, 80, size[0] - 80, size[1] - 80), fill=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class RegistryTests(unittest.TestCase):

    def test_the_first_six_are_unchanged_and_five_scene_concepts_follow(self):
        codes = [c.code for c in ic.list_concepts()]
        self.assertEqual(codes, FIRST_SIX + L1_CODES)
        self.assertEqual(ic.validate_registry(), {})

    def test_scene_concepts_are_additional_and_detail_only_local_and_require_their_checks(self):
        for code in L1_CODES:
            c = ic.get_concept(code)
            self.assertEqual((c.background_kind, c.processing_level), (BackgroundKind.SCENE, "L1"), code)
            self.assertEqual(c.usable_for, ("ADDITIONAL", "DETAIL"), code)
            self.assertEqual(c.checks, ("PRODUCT_PRESERVATION", "BACKGROUND_CONTRAST", "NO_TEXT_OVERLAY"), code)
            self.assertEqual((c.props_allowed, c.needs_generated_label, c.l2_approved, c.version), (False, False, False, 1), code)
            self.assertIn(c.scene_code, sb.SCENE_CODES, code)
            self.assertEqual(c.background_spec().scene_code, c.scene_code, code)
            self.assertTrue(c.mood and c.good_for, code)
            with self.assertRaises(Exception):
                ic.require_usable(code, "MAIN")

    def test_texture_and_light_scene_kinds_and_colors(self):
        for code in ("LINEN_NATURAL", "WOOD_WARM", "PAPER_SOFT"):
            c = ic.get_concept(code)
            self.assertEqual((c.scene_kind, bool(c.color_hex)), (ic.SCENE_MATERIAL_TEXTURE, True), code)
            self.assertIn(c.scene_code, sb.TEXTURES)
        for code in ("SPOTLIGHT_WARM", "SPOTLIGHT_COOL"):
            c = ic.get_concept(code)
            self.assertEqual((c.scene_kind, c.color_hex, c.color_hex_end), (ic.SCENE_STUDIO_LIGHT, None, None), code)
            self.assertIn(c.scene_code, sb.LIGHTS)

    def test_the_main_image_list_and_counts(self):
        self.assertEqual([c.code for c in ic.list_concepts("MAIN")], ["CLEAN_WHITE"])
        self.assertEqual(len(ic.list_concepts("ADDITIONAL")), 11)
        self.assertEqual(len(ic.list_concepts("DETAIL")), 11)

    def test_the_summary_carries_the_scene_code_and_stays_json_friendly(self):
        import json

        summaries = {c.code: c.to_summary() for c in ic.list_concepts()}
        self.assertEqual(summaries["WOOD_WARM"]["scene_code"], "WOOD")
        self.assertEqual(summaries["CLEAN_WHITE"]["scene_code"], "")
        json.dumps(list(summaries.values()), ensure_ascii=False)


class SpecValidationTests(unittest.TestCase):

    def problems(self, code, **changes):
        return ic.validate_spec(replace(ic.get_concept(code), **changes))

    def test_a_good_scene_concept_has_no_problems(self):
        for code in L1_CODES:
            self.assertEqual(ic.validate_spec(ic.get_concept(code)), [], code)

    def test_each_scene_rule_is_enforced(self):
        cases = (
            ("LINEN_NATURAL", dict(processing_level="L0"), "L1 이상"),
            ("LINEN_NATURAL", dict(scene_code="MARBLE"), "알려진 장면 코드"),
            ("LINEN_NATURAL", dict(scene_code=""), "알려진 장면 코드"),
            ("LINEN_NATURAL", dict(scene_kind=ic.SCENE_STUDIO_LIGHT), "질감 장면"),
            ("SPOTLIGHT_WARM", dict(scene_kind=ic.SCENE_MATERIAL_TEXTURE), "조명 장면"),
            ("LINEN_NATURAL", dict(color_hex=None), "바탕색"),
            ("LINEN_NATURAL", dict(color_hex="#GGGGGG"), "바탕색"),
            ("SPOTLIGHT_WARM", dict(color_hex="#FFFFFF"), "색을 지정하지 않는다"),
            ("LINEN_NATURAL", dict(color_hex_end="#FFFFFF"), "끝 색"),
        )
        for code, changes, expected in cases:
            found = self.problems(code, **changes)
            self.assertTrue(any(expected in p for p in found), (code, changes, found))

    def test_a_scene_code_on_a_non_scene_concept_is_a_problem(self):
        found = self.problems("SOFT_BEIGE", scene_code="LINEN")
        self.assertTrue(any("장면(SCENE) 배경에서만" in p for p in found), found)

    def test_other_background_kinds_still_pass_their_old_rules(self):
        self.assertEqual(ic.validate_spec(ic.get_concept("SOFT_BEIGE")), [])
        found = self.problems("SOFT_BEIGE", background_kind="EXISTING_ASSET")
        self.assertTrue(any("기존 이미지 배경은 컨셉이 아니다" in p for p in found), found)

    def test_a_broken_registry_is_reported_with_the_concept_code(self):
        registry = dict(ic.CONCEPTS)
        registry["LINEN_NATURAL"] = replace(ic.get_concept("LINEN_NATURAL"), scene_code="MARBLE")
        self.assertIn("LINEN_NATURAL", ic.validate_registry(registry))


class CompositionTests(unittest.TestCase):

    def test_a_scene_spec_needs_a_scene_code(self):
        with self.assertRaisesRegex(BadRequestException, "scene_code"):
            compose_with_background(cutout_png(), BackgroundSpec(kind=BackgroundKind.SCENE, color_hex="#E8DFD0"))

    def test_unknown_kinds_and_unknown_scene_codes_are_still_rejected(self):
        with self.assertRaises(BadRequestException):
            compose_with_background(cutout_png(), BackgroundSpec(kind="HOLOGRAM"))
        with self.assertRaises(BadRequestException):
            compose_with_background(cutout_png(), BackgroundSpec(kind=BackgroundKind.SCENE, scene_code="MARBLE", color_hex="#E8DFD0"))

    def test_every_scene_concept_composes_with_the_product_unchanged(self):
        cutout = cutout_png()
        for code in L1_CODES:
            composed = Image.open(io.BytesIO(compose_with_background(cutout, ic.get_concept(code).background_spec()))).convert("RGB")
            self.assertEqual(composed.size, (1200, 1200), code)
            self.assertEqual(composed.getpixel((600, 600)), (20, 30, 70), code)             # 제품 한가운데는 그대로
            self.assertNotEqual(composed.getpixel((5, 5)), (20, 30, 70), code)

    def test_the_default_color_is_used_when_a_texture_has_no_color(self):
        no_color = Image.open(io.BytesIO(compose_with_background(cutout_png(), BackgroundSpec(kind=BackgroundKind.SCENE, scene_code="PAPER")))).convert("RGB")
        explicit = Image.open(io.BytesIO(compose_with_background(cutout_png(), BackgroundSpec(kind=BackgroundKind.SCENE, scene_code="PAPER", color_hex="#E8DFD0")))).convert("RGB")
        self.assertEqual(no_color.getpixel((5, 5)), explicit.getpixel((5, 5)))
        other = Image.open(io.BytesIO(compose_with_background(cutout_png(), BackgroundSpec(kind=BackgroundKind.SCENE, scene_code="PAPER", color_hex="#102030")))).convert("RGB")
        self.assertNotEqual(no_color.getpixel((5, 5)), other.getpixel((5, 5)))

    def test_the_same_input_gives_the_same_picture(self):
        spec = ic.get_concept("WOOD_WARM").background_spec()
        self.assertEqual(compose_with_background(cutout_png(), spec), compose_with_background(cutout_png(), spec))

    def test_the_old_background_kinds_are_unaffected(self):
        white = Image.open(io.BytesIO(compose_with_background(cutout_png(), BackgroundSpec(kind=BackgroundKind.WHITE)))).convert("RGB")
        self.assertEqual(white.getpixel((5, 5)), (255, 255, 255))
        self.assertIn(BackgroundKind.SCENE, BackgroundKind.ALL)


if __name__ == "__main__":
    unittest.main()
