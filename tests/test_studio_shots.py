"""
2026-11-26 — 촬영장 화보·상세 정보 카드(사용자 "지시를 원하지말고 스스로 생각해라 피카소라고 생각하고").

  - 화보: 상품 완전 불투명 픽셀은 원본 그대로(보존 1.0), 1000 캔버스 가운데·밑면 80%·최대 1.4배, 하늘빛 화보는 밑에 반사,
    스타일마다 그림자 방향(투톤=오른쪽 앞, 창살=왼쪽 앞), 모르는 스타일 거절.
  - 카드: 가로 780, 금지 표현 거절, 낱말 가운데서 줄을 끊지 않음, 사용법은 세 단계.
  - 서비스: 원본은 그대로, 화보는 추가 이미지·카드는 상세 이미지로 저장, 편집 기록(배경 바꾸기·상세 이미지).
"""

from __future__ import annotations

import io
import unittest

from PIL import Image
from PIL import ImageDraw

from app.core.exceptions import BadRequestException
from app.domains.media_asset import image_edit_record as ier
from app.domains.media_asset import info_cards as ic
from app.domains.media_asset import studio_shots as st
from app.domains.media_asset.studio_service import StudioService
from tests.test_image_edit_record import Base_

COLOUR = (40, 90, 170)


def bottle_png(size=(300, 600), box=(100, 50, 200, 550)) -> bytes:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), COLOUR + (255,)), box[:2])
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def rgb(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


class ShotTests(unittest.TestCase):

    def test_every_style_keeps_the_product_and_places_it_large_in_the_middle(self):
        for style in st.STYLES:
            shot = st.compose(bottle_png(), style)
            self.assertEqual(shot.preservation, 1.0, style)
            image = rgb(shot.png_bytes)
            self.assertEqual(image.size, st.CANVAS)
            self.assertEqual(image.getpixel((500, 500)), COLOUR, style)
            product, offset, _ = st.place(Image.open(io.BytesIO(bottle_png())))
            self.assertEqual(product.size, (134, 670))                          # 높이 67%(670)가 1.4배(700)보다 먼저
            self.assertEqual(offset[1] + product.height, int(1000 * st.FLOOR_LINE))

    def test_the_podium_shot_stands_the_product_on_the_podium_top(self):
        image = rgb(st.compose(bottle_png(), st.PODIUM_ARCH).png_bytes)
        base = int(1000 * 0.76)
        self.assertEqual(image.getpixel((500, base - 5)), COLOUR)               # 받침대 윗면 바로 위까지 상품
        self.assertNotEqual(image.getpixel((500, base + 6)), COLOUR)            # 그 아래는 받침대
        top = base - 600
        self.assertEqual(image.getpixel((500, top + 5)), COLOUR)                # 상품 높이 60%(받침대 위라 낮게)
        arch = st.background(st.PODIUM_ARCH).getpixel((500, top - 20))
        self.assertGreater(arch[2], arch[0] + 20)                                # 상품 뒤는 하늘빛 아치

    def test_the_aqua_shot_has_a_reflection_below_the_product(self):
        image = rgb(st.compose(bottle_png(), st.AQUA_REFLECT).png_bytes)
        plain = st.background(st.AQUA_REFLECT)
        below = (500, int(1000 * st.FLOOR_LINE) + 110)                          # 그림자가 닿지 않는 곳(반사만)
        self.assertLess(image.getpixel(below)[0], plain.getpixel(below)[0] - 8)    # 바닥에 상품 색이 옅게 비친다
        none = rgb(st.compose(bottle_png(), st.TWO_TONE_SUN).png_bytes)
        self.assertNotIn(COLOUR, [none.getpixel((500, int(1000 * st.FLOOR_LINE) + y)) for y in (40, 80)])

    def test_shadows_fall_away_from_each_style_light(self):
        def sides(style):
            image, plain = rgb(st.compose(bottle_png(), style).png_bytes), st.background(style)
            y = int(1000 * st.FLOOR_LINE) + 25
            darker = lambda x: sum(plain.getpixel((x, y))) - sum(image.getpixel((x, y)))   # noqa: E731
            return darker(500 - 130), darker(500 + 130)
        left, right = sides(st.TWO_TONE_SUN)
        self.assertGreater(right, left + 5)                                     # 왼쪽 빛 → 오른쪽 앞
        left, right = sides(st.WINDOW_SHADOW)
        self.assertGreater(left, right + 5)                                     # 오른쪽 빛 → 왼쪽 앞

    def test_a_shot_that_changed_product_pixels_is_never_returned(self):
        original = st.preservation
        st.preservation = lambda product, offset, composed: (0.99, 100)
        try:
            with self.assertRaises(st.StudioError):
                st.compose(bottle_png(), st.AQUA_REFLECT)
        finally:
            st.preservation = original

    def test_unknown_style_and_empty_cutout_are_refused(self):
        with self.assertRaises(st.StudioError):
            st.compose(bottle_png(), "NEON")
        empty = io.BytesIO()
        Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(empty, format="PNG")
        with self.assertRaises(st.StudioError):
            st.compose(empty.getvalue(), st.AQUA_REFLECT)


class CardTests(unittest.TestCase):

    def test_cards_have_the_detail_width(self):
        self.assertEqual(rgb(ic.points_card(bottle_png())).width, ic.WIDTH)
        self.assertEqual(rgb(ic.steps_card()).width, ic.WIDTH)

    def test_banned_words_are_never_printed(self):
        with self.assertRaises(ic.CardError):
            ic.points_card(bottle_png(), headline="살균되는 바디워시")
        with self.assertRaises(ic.CardError):
            ic.steps_card(note="피부 치료에 좋아요")
        with self.assertRaises(ic.CardError):
            ic.points_card(bottle_png(), specs=("최고 품질",))
        with self.assertRaises(ic.CardError):
            ic.steps_card(steps=ic.DEFAULT_STEPS[:2])

    def test_lines_break_between_words(self):
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = ic._font(False, 22)                                               # noqa: SLF001
        lines = ic._wrap(draw, "피부에 적당량 뿌리기 그리고 수건으로 닦아 내기", font, 200)   # noqa: SLF001
        self.assertGreater(len(lines), 1)
        self.assertEqual(" ".join(lines), "피부에 적당량 뿌리기 그리고 수건으로 닦아 내기")
        self.assertEqual(ic._wrap(draw, "NO STICKY", font, 400), ["NO STICKY"])   # noqa: SLF001


class ServiceTests(Base_):

    def test_shots_and_cards_are_saved_with_their_steps(self):
        cutout = self.upload(bottle_png())
        before = (self.media_root / cutout.storage_path).read_bytes()
        service = StudioService(self.db, queue=self.queue)
        shots = service.shots(self.company_id, 1, cutout.id, list(st.STYLES))
        self.assertEqual([s.purpose for s in shots], ["ADDITIONAL"] * len(st.STYLES))
        self.assertEqual({s.source_asset_id for s in shots}, {cutout.id})
        self.assertEqual(self.kinds(shots[0]), [ier.ORIGINAL, ier.BACKGROUND_SWAP])
        cards = service.cards(self.company_id, 1, cutout.id)
        self.assertEqual([c.purpose for c in cards], ["DETAIL", "DETAIL"])
        self.assertEqual(self.kinds(cards[0]), [ier.ORIGINAL, ier.DETAIL_PAGE])
        self.assertEqual((self.media_root / cutout.storage_path).read_bytes(), before)

    def test_bad_requests_are_refused(self):
        cutout = self.upload(bottle_png())
        service = StudioService(self.db, queue=self.queue)
        for styles in ([], ["NEON"], [st.AQUA_REFLECT, st.AQUA_REFLECT]):
            with self.assertRaises(BadRequestException):
                service.shots(self.company_id, 1, cutout.id, styles)
        with self.assertRaises(Exception):
            service.shots(self.other_company_id, 1, cutout.id, [st.AQUA_REFLECT])


if __name__ == "__main__":
    unittest.main()
