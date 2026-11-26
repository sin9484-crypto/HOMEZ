"""
2026-11-26 — 상황 장면 #175~#179를 본 사용자 "부 자연스럽다" 뒤의 고침.

  - 투명 용기 몸통이 장면을 비추게(`clear_container.see_through`): 고른 높이 범위의 옅은(투명) 픽셀만 불투명도를 낮추고 색을 되돌린다.
    뚜껑(범위 밖)·글씨(진함)는 그대로. 흰 배경 위에 다시 놓으면 원본과 거의 같고, 어두운 장면 위에서는 장면이 비친다.
    바닥 세우기 합성이 지우는 옅은 안개(알파 64 미만)보다 짙게 남긴다.
  - 장면 빛 맞추기(`composition.scene_light_gains`·`match_scene_light`): 하얗게 날아간 창·하늘은 빼고 장면 색을 재며, 채널별 곱과 좌우 기울기만.
    상품 보존 검사는 같은 계산을 다시 해서 1.0이고, 빛 맞춤이 아닌 변화(다시 그리기)는 여전히 잡는다.
  - 바닥 세우기 높이 한도 0.50.
  - 서비스·견본: 비치게 만든 누끼(누끼의 파생)를 견본이 찾아 쓴다. 화면 함수·문구.
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest

from PIL import Image
from PIL import ImageDraw

from app.core.exceptions import BadRequestException
from app.domains.media_asset import clear_container as cc
from app.domains.media_asset import composition as comp
from app.domains.media_asset import image_edit_record as ier
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.composition import Placement
from app.domains.media_asset.concept_inspection import product_preservation
from app.domains.media_asset.concept_preview_service import ConceptPreviewService
from tests.test_image_edit_record import Base_
from tests.test_image_edit_record import EN
from tests.test_image_edit_record import JS
from tests.test_image_edit_record import KO
from tests.test_image_edit_record import NODE
from tests.test_reference_generation_ui import function_source

CAP = (252, 252, 252)
BODY = (238, 241, 245)
PRINT = (20, 30, 120)


def clear_bottle_png(size=(200, 400)) -> bytes:
    """흰 뚜껑(위 20%) + 투명 몸통(옅은 회청색) + 몸통 가운데 진한 글씨 상자. 배경은 투명."""

    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 20, 129, 99), fill=CAP + (255,))
    draw.rectangle((40, 100, 159, 379), fill=BODY + (255,))
    draw.rectangle((70, 200, 129, 239), fill=PRINT + (255,))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def rgba(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def on(colour, data: bytes) -> Image.Image:
    image = rgba(data)
    ground = Image.new("RGBA", image.size, colour + (255,))
    ground.alpha_composite(image)
    return ground.convert("RGB")


class SeeThroughTests(unittest.TestCase):

    def setUp(self):
        self.made = cc.see_through(clear_bottle_png(), 0.25, 1.0)        # 상품 높이 360 중 위 25%(뚜껑 80줄 포함)는 그대로

    def test_only_the_clear_body_becomes_see_through(self):
        image = rgba(self.made)
        self.assertEqual(image.getpixel((100, 50)), CAP + (255,))        # 뚜껑(범위 밖)
        self.assertEqual(image.getpixel((100, 220)), PRINT + (255,))     # 글씨(진함)
        body_alpha = image.getpixel((50, 300))[3]
        self.assertLess(body_alpha, 255)
        self.assertGreaterEqual(body_alpha, int(cc.GLASS_ALPHA_MIN * 255))
        self.assertEqual(image.getpixel((5, 5))[3], 0)                   # 바깥은 그대로 투명

    def test_on_white_it_looks_like_the_original_and_on_a_scene_the_scene_shows(self):
        original, made = on((255, 255, 255), clear_bottle_png()), on((255, 255, 255), self.made)
        for xy in ((50, 300), (100, 150), (100, 220), (100, 50)):
            self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(original.getpixel(xy), made.getpixel(xy))), xy)
        dark = on((90, 60, 30), self.made).getpixel((50, 300))
        self.assertLess(dark[0], BODY[0] - 60)                          # 몸통 너머 어두운 장면이 비친다
        self.assertEqual(on((90, 60, 30), self.made).getpixel((100, 220)), PRINT)

    def test_the_glass_is_never_erased_by_the_grounded_haze_rule(self):
        self.assertGreaterEqual(int(cc.GLASS_ALPHA_MIN * 255), comp.HAZE_ALPHA_MAX)
        prepared = comp.prepare_cutout(rgba(self.made), Placement.GROUNDED)
        self.assertGreater(prepared.getpixel((50, 300))[3], 0)

    def test_bad_input_is_refused(self):
        opaque = io.BytesIO()
        Image.new("RGB", (50, 50), (200, 200, 200)).save(opaque, format="PNG")
        with self.assertRaises(cc.SeeThroughError):
            cc.see_through(opaque.getvalue(), 0.2, 0.9)
        for y_from, y_to in ((0.5, 0.5), (0.6, 0.4), (0.5, 0.53), (-0.1, 0.5), (0.2, 1.2), ("a", 0.5)):
            with self.assertRaises(cc.SeeThroughError, msg=(y_from, y_to)):
                cc.see_through(clear_bottle_png(), y_from, y_to)
        dark = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        dark.paste(Image.new("RGBA", (20, 20), (10, 10, 10, 255)), (10, 10))
        buffer = io.BytesIO()
        dark.save(buffer, format="PNG")
        with self.assertRaises(cc.SeeThroughError):
            cc.see_through(buffer.getvalue(), 0.0, 1.0)                  # 비치게 할 옅은 부분이 없다


class SceneLightTests(unittest.TestCase):

    def test_blown_out_whites_are_ignored_and_a_warm_scene_warms_the_product(self):
        scene = Image.new("RGB", (400, 400), (230, 205, 170))
        scene.paste(Image.new("RGB", (400, 150), (255, 255, 255)), (0, 0))       # 하얗게 날아간 창(위쪽의 절반이 넘는다)
        gains = comp.scene_light_gains(scene, 300)
        self.assertEqual(gains[0], 1.0)
        self.assertLess(gains[2], gains[1])
        self.assertLess(gains[2], 1.0)
        self.assertGreaterEqual(min(gains), 1 - comp.LIGHT_TINT * (1 - comp.LIGHT_GAIN_MIN))
        self.assertEqual(comp.scene_light_gains(Image.new("RGB", (400, 400), (255, 255, 255)), 300), (1.0, 1.0, 1.0))
        self.assertEqual(comp.scene_light_gains(scene, 20), (1.0, 1.0, 1.0))      # 위쪽이 너무 좁으면 그대로

    def test_light_comes_from_the_left_and_alpha_is_untouched(self):
        product = Image.new("RGBA", (101, 10), (200, 200, 200, 255))
        product.putpixel((50, 5), (200, 200, 200, 128))
        lit = comp.match_scene_light(product, (1.0, 1.0, 1.0))
        self.assertEqual(lit.getpixel((0, 0)), (200, 200, 200, 255))
        right = lit.getpixel((100, 0))[0]
        self.assertEqual(right, round(200 * round(255 * (1 - comp.LIGHT_FALLOFF)) / 255))
        self.assertEqual(lit.getpixel((50, 5))[3], 128)

    def test_preservation_still_measures_every_product_pixel_and_still_catches_a_redraw(self):
        cutout = cc.see_through(clear_bottle_png(), 0.25, 1.0)
        scene = Image.new("RGB", (1200, 1200), (225, 200, 165))
        buffer = io.BytesIO()
        scene.save(buffer, format="PNG")
        composed = comp.compose_with_background(cutout, BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=buffer.getvalue()),
                                                placement=Placement.GROUNDED)
        measured = product_preservation(cutout, composed, placement=Placement.GROUNDED)
        self.assertEqual(measured["ratio"], 1.0)
        self.assertGreater(measured["pixels"], 0)
        broken = Image.open(io.BytesIO(composed)).convert("RGB")
        plan = comp.plan_placement(comp.prepare_cutout(rgba(cutout), Placement.GROUNDED), broken.size, Placement.GROUNDED)
        ImageDraw.Draw(broken).rectangle((plan.offset[0], plan.offset[1], plan.offset[0] + plan.size[0], plan.offset[1] + plan.size[1] // 3), fill=(9, 9, 9))
        out = io.BytesIO()
        broken.save(out, format="PNG")
        self.assertLess(product_preservation(cutout, out.getvalue(), placement=Placement.GROUNDED)["ratio"], 0.9)

    def test_grounded_products_stay_at_most_half_the_height(self):
        self.assertEqual(comp.GROUNDED_MAX_HEIGHT, 0.50)
        tall = Image.new("RGBA", (300, 1000), (0, 0, 0, 0))
        tall.paste(Image.new("RGBA", (200, 900), (9, 9, 9, 255)), (50, 50))
        self.assertEqual(comp.plan_placement(tall, comp.CANVAS_SIZE, Placement.GROUNDED).size[1], 600)


class ServiceTests(Base_):

    def test_the_service_makes_a_new_cutout_and_keeps_the_source(self):
        cutout = self.upload(clear_bottle_png())
        before = (self.media_root / cutout.storage_path).read_bytes()
        clear = self.queue.see_through(cutout.id, 0.25, 1.0, 1, self.company_id)
        self.assertEqual((clear.asset_role, clear.source_asset_id, clear.rights_status), ("GENERATED", cutout.id, "VERIFIED"))
        self.assertEqual((self.media_root / cutout.storage_path).read_bytes(), before)
        self.assertEqual(self.kinds(clear), [ier.ORIGINAL, ier.CUTOUT])
        self.assertLess(rgba((self.media_root / clear.storage_path).read_bytes()).getpixel((50, 300))[3], 255)

    def test_grandchild_cutout_is_found_from_the_original_photo(self):
        photo = io.BytesIO()
        Image.new("RGB", (200, 400), (255, 255, 255)).save(photo, format="PNG")
        original = self.upload(photo.getvalue())
        first = self.derived(original, "BACKGROUND_REMOVAL", provider="REMBG")
        # 첫 누끼를 투명 병 누끼로 바꿔 두고(시험용 파일 쓰기), 그 누끼에서 비치게 만든다
        (self.media_root / first.storage_path).write_bytes(clear_bottle_png())
        grandchild = self.queue.see_through(first.id, 0.25, 1.0, 1, self.company_id)
        data = ConceptPreviewService(self.db, queue=self.queue).previews(self.company_id, original.id, "ADDITIONAL")
        self.assertEqual(data["cutout_asset_id"], grandchild.id)

    def test_a_failure_leaves_the_photo_as_it_is(self):
        opaque = io.BytesIO()
        Image.new("RGB", (60, 60), (200, 200, 200)).save(opaque, format="PNG")
        original = self.upload(opaque.getvalue())
        with self.assertRaises(BadRequestException) as caught:
            self.queue.see_through(original.id, 0.2, 0.9, 1, self.company_id)
        self.assertIn("원본은 그대로", str(caught.exception.detail))
        self.assertEqual(self.kinds(original), [ier.ORIGINAL])


class ScreenTests(unittest.TestCase):

    def test_messages_exist_in_both_languages(self):
        for key in ("clear_title", "clear_note", "clear_from", "clear_to", "clear_btn", "clear_err_range"):
            self.assertIn(f'"lw.{key}"', KO, key)
            self.assertIn(f'"lw.{key}"', EN, key)
        self.assertIn('"/media-assets/see-through"', JS)

    @unittest.skipUnless(NODE, "node 없음")
    def test_range_check_and_request_body(self):
        script = function_source("lwClearProblem") + function_source("lwClearBody") + """
console.log(JSON.stringify([lwClearProblem({from: 25, to: 100}), lwClearProblem({from: 50, to: 52}), lwClearProblem({from: 60, to: 40}),
  lwClearProblem({from: -1, to: 40}), lwClearProblem({from: 0, to: 101}), lwClearBody("171", {from: 25, to: 100})]));"""
        out = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
        self.assertEqual(out[:5], [None, "clear_err_range", "clear_err_range", "clear_err_range", "clear_err_range"])
        self.assertEqual(out[5], {"source_asset_id": 171, "y_from": 0.25, "y_to": 1.0})


if __name__ == "__main__":
    unittest.main()
