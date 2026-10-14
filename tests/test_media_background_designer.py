"""
=========================================================
Homez OS

File : tests/test_media_background_designer.py

적응형 배경 설계(`background_designer.py`) — 제품 색에 따라 배경이 달라지고, 제품이 배경에 묻히지 않으며,
대표 이미지는 흰 배경만 허용되는지 고정한다. Pillow 합성 누끼·임시 SQLite만 쓴다 — 네트워크 없음.
=========================================================
"""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from PIL import ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset import background_designer as bd
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.user.model import User  # noqa: F401


def cutout(color, size=(500, 400), shape="ellipse") -> bytes:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (40, 40, size[0] - 40, size[1] - 40)
    draw.ellipse(box, fill=color + (255,)) if shape == "ellipse" else draw.rectangle(box, fill=color + (255,))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


RED = (200, 40, 40)
WHITE_PRODUCT = (248, 248, 246)
BLACK_PRODUCT = (25, 25, 28)
BLUE = (30, 80, 200)


class ProductAnalysisTests(unittest.TestCase):

    def test_a_red_product_is_saturated_warm_and_neither_light_nor_dark(self):
        profile = bd.analyze_product(cutout(RED))
        self.assertFalse(profile.is_light)
        self.assertFalse(profile.is_dark)
        self.assertFalse(profile.is_neutral)
        self.assertLess(profile.dominant_hue, 0.05)
        self.assertEqual(profile.colors[0][0], "#C82828")
        self.assertAlmostEqual(sum(share for _c, share in profile.colors), 1.0, delta=0.01)

    def test_white_and_black_products_are_detected(self):
        self.assertTrue(bd.analyze_product(cutout(WHITE_PRODUCT)).is_light)
        self.assertTrue(bd.analyze_product(cutout(BLACK_PRODUCT)).is_dark)
        self.assertTrue(bd.analyze_product(cutout(BLACK_PRODUCT)).is_neutral)

    def test_only_opaque_pixels_count_not_the_transparent_margin(self):
        profile = bd.analyze_product(cutout(BLUE, size=(900, 900)))
        self.assertEqual(profile.colors[0][0], "#1E50C8")

    def test_an_image_without_a_product_is_rejected(self):
        empty = io.BytesIO()
        Image.new("RGBA", (200, 200), (0, 0, 0, 0)).save(empty, format="PNG")
        with self.assertRaises(BadRequestException):
            bd.analyze_product(empty.getvalue())
        with self.assertRaises(BadRequestException):
            bd.analyze_product(b"not an image")
        with self.assertRaises(BadRequestException):
            bd.analyze_product(b"")


class SuggestionTests(unittest.TestCase):

    def test_the_background_changes_with_the_product_color(self):
        red = bd.suggest_backgrounds(bd.analyze_product(cutout(RED)))
        blue = bd.suggest_backgrounds(bd.analyze_product(cutout(BLUE)))
        self.assertNotEqual({s.color_hex for s in red}, {s.color_hex for s in blue})
        red_complement = next(s for s in red if s.code == "ADAPT_COMPLEMENT").color_hex
        blue_complement = next(s for s in blue if s.code == "ADAPT_COMPLEMENT").color_hex
        self.assertNotEqual(red_complement, blue_complement)

    def test_the_first_suggestion_always_stands_out_from_the_product(self):
        for color in (RED, BLUE, WHITE_PRODUCT, BLACK_PRODUCT, (120, 120, 120), (240, 200, 60)):
            with self.subTest(color=color):
                suggestions = bd.suggest_backgrounds(bd.analyze_product(cutout(color)))
                self.assertGreaterEqual(suggestions[0].min_delta_e, bd.LOW_CONTRAST_DELTA_E)
                self.assertFalse(suggestions[0].low_contrast)
                scores = [s.contrast for s in suggestions]
                self.assertEqual(scores, sorted(scores, reverse=True))

    def test_a_white_product_gets_dark_backgrounds_and_a_black_product_gets_light_ones(self):
        white_codes = [s.code for s in bd.suggest_backgrounds(bd.analyze_product(cutout(WHITE_PRODUCT)))]
        self.assertIn("ADAPT_DEEP", white_codes)
        self.assertNotIn("ADAPT_SPOTLIGHT", white_codes)
        black_codes = [s.code for s in bd.suggest_backgrounds(bd.analyze_product(cutout(BLACK_PRODUCT)))]
        self.assertIn("ADAPT_SPOTLIGHT", black_codes)
        self.assertNotIn("ADAPT_DEEP", black_codes)

    def test_a_white_background_hides_a_white_product_and_that_is_reported(self):
        profile = bd.analyze_product(cutout(WHITE_PRODUCT))
        suggestions = bd.suggest_backgrounds(profile, "ADDITIONAL", count=8)
        white = next(s for s in suggestions if s.code == "ADAPT_WHITE")
        self.assertTrue(white.low_contrast)
        self.assertTrue(white.shadow_recommended)
        self.assertGreater(suggestions[0].contrast, white.contrast)

    def test_the_main_image_only_gets_the_white_background(self):
        suggestions = bd.suggest_backgrounds(bd.analyze_product(cutout(RED)), "MAIN")
        self.assertEqual([s.code for s in suggestions], ["ADAPT_WHITE"])
        self.assertEqual(suggestions[0].color_hex, "#FFFFFF")

    def test_variation_changes_the_hue_family_and_is_deterministic(self):
        profile = bd.analyze_product(cutout(RED))
        first = bd.suggest_backgrounds(profile, variation=0)
        again = bd.suggest_backgrounds(profile, variation=0)
        other = bd.suggest_backgrounds(profile, variation=4)
        self.assertEqual([s.to_dict() for s in first], [s.to_dict() for s in again])
        self.assertNotEqual(
            next(s for s in first if s.code == "ADAPT_COMPLEMENT").color_hex,
            next(s for s in other if s.code == "ADAPT_COMPLEMENT").color_hex,
        )

    def test_identical_looking_backgrounds_are_dropped(self):
        suggestions = bd.suggest_backgrounds(bd.analyze_product(cutout((128, 128, 128))), count=8)
        looks = [(s.kind, s.color_hex, s.color_hex_end) for s in suggestions]
        self.assertEqual(len(looks), len(set(looks)))
        flat = [bd._from_hex(s.color_hex) for s in suggestions if s.kind in ("WHITE", "SOLID")]
        for i, a in enumerate(flat):
            for b in flat[i + 1:]:
                self.assertGreaterEqual(bd.delta_e(a, b), 6)      # 단색끼리는 서로 뚜렷이 달라야 한다

    def test_a_small_light_patch_does_not_make_the_whole_background_look_buried(self):
        """검정 제품 안쪽의 작은 흰 안감(약 8%)이 연한 배경에 묻혀도 배경 전체를 "묻힘"으로 경고하지 않는다."""

        image = Image.new("RGBA", (500, 400), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((40, 40, 460, 360), fill=BLACK_PRODUCT + (255,))
        draw.rectangle((220, 150, 280, 220), fill=(238, 239, 243, 255))        # 작은 흰 안감
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        profile = bd.analyze_product(buffer.getvalue())
        light = [s for s in bd.suggest_backgrounds(profile, count=8) if s.code in ("ADAPT_COMPLEMENT", "ADAPT_TONAL", "ADAPT_ANALOGOUS")]
        self.assertTrue(light)
        for suggestion in light:
            self.assertFalse(suggestion.low_contrast, suggestion.code)

    def test_a_product_that_is_mostly_buried_is_flagged(self):
        profile = bd.analyze_product(cutout((236, 236, 238)))           # 거의 흰 제품
        white = next(s for s in bd.suggest_backgrounds(profile, count=8) if s.code == "ADAPT_WHITE")
        self.assertTrue(white.low_contrast)

    def test_a_bold_option_is_offered_for_variety_and_is_vivid(self):
        for color in (RED, BLACK_PRODUCT, WHITE_PRODUCT):
            with self.subTest(color=color):
                suggestions = bd.suggest_backgrounds(bd.analyze_product(cutout(color)), count=8)
                bold = next((s for s in suggestions if s.code == "ADAPT_BOLD"), None)
                self.assertIsNotNone(bold)
                self.assertEqual(bold.kind, "RADIAL")
                self.assertGreaterEqual(bd.delta_e(bd._from_hex(bold.color_hex), (255, 255, 255)), 20)

    def test_invalid_arguments_are_rejected(self):
        profile = bd.analyze_product(cutout(RED))
        for kwargs in ({"purpose": "BANNER"}, {"variation": 12}, {"variation": -1}, {"count": 0}, {"count": 9}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(BadRequestException):
                    bd.suggest_backgrounds(profile, **kwargs)

    def test_delta_e_is_zero_for_equal_colors_and_large_for_black_versus_white(self):
        self.assertEqual(bd.delta_e((10, 20, 30), (10, 20, 30)), 0)
        self.assertGreater(bd.delta_e((0, 0, 0), (255, 255, 255)), 95)


class RenderTests(unittest.TestCase):

    def setUp(self):
        self.product = cutout(RED)
        self.profile = bd.analyze_product(self.product)

    def _render(self, code, **kwargs):
        suggestion = bd.get_suggestion(self.profile, code, "ADDITIONAL")
        return Image.open(io.BytesIO(bd.render_suggestion(self.product, suggestion, **kwargs))).convert("RGB"), suggestion

    def test_every_kind_renders_a_1200_square_with_the_product_untouched_in_the_middle(self):
        for code in ("ADAPT_COMPLEMENT", "ADAPT_SPOTLIGHT", "ADAPT_NEUTRAL", "ADAPT_WHITE"):
            with self.subTest(code=code):
                image, _ = self._render(code)
                self.assertEqual(image.size, (1200, 1200))
                self.assertEqual(image.getpixel((600, 600)), RED)       # 제품 픽셀은 그대로

    def test_the_background_color_matches_the_suggestion(self):
        image, suggestion = self._render("ADAPT_NEUTRAL")
        self.assertEqual(image.getpixel((5, 5)), bd._from_hex(suggestion.color_hex))
        white, _ = self._render("ADAPT_WHITE")
        self.assertEqual(white.getpixel((5, 5)), (255, 255, 255))

    def test_gradient_and_radial_backgrounds_actually_vary(self):
        gradient, _ = self._render("ADAPT_COMPLEMENT")
        self.assertNotEqual(gradient.getpixel((5, 5)), gradient.getpixel((5, 1190)))
        radial, _ = self._render("ADAPT_SPOTLIGHT")
        self.assertNotEqual(radial.getpixel((600, 60)), radial.getpixel((5, 5)))

    def test_the_product_is_never_enlarged_and_keeps_its_margin(self):
        small = cutout(RED, size=(200, 150))
        suggestion = bd.get_suggestion(bd.analyze_product(small), "ADAPT_NEUTRAL", "ADDITIONAL")
        image = Image.open(io.BytesIO(bd.render_suggestion(small, suggestion))).convert("RGB")
        self.assertEqual(image.getpixel((600, 600)), RED)
        self.assertEqual(image.getpixel((600 + 150, 600)), bd._from_hex(suggestion.color_hex))   # 제품 폭(120) 바깥은 배경

    def test_a_large_product_is_scaled_to_the_fill_ratio(self):
        big = cutout(BLUE, size=(2000, 2000), shape="rect")
        suggestion = bd.get_suggestion(bd.analyze_product(big), "ADAPT_NEUTRAL", "ADDITIONAL")
        image = Image.open(io.BytesIO(bd.render_suggestion(big, suggestion, fill_ratio=0.5))).convert("RGB")
        self.assertEqual(image.getpixel((600, 600)), BLUE)
        self.assertEqual(image.getpixel((600 + 360, 600)), bd._from_hex(suggestion.color_hex))   # 0.5*1200/2 = 300 바깥

    def test_a_shadow_darkens_the_area_under_the_product_only_when_asked(self):
        plain, _ = self._render("ADAPT_WHITE", shadow=False)
        shadowed, _ = self._render("ADAPT_WHITE", shadow=True)
        under = (600, 600 + 172)            # 제품 아래쪽 가장자리(+160) 바로 밖 — 그림자가 닿는 곳
        self.assertEqual(plain.getpixel(under), (255, 255, 255))
        self.assertLess(sum(shadowed.getpixel(under)), sum(plain.getpixel(under)))
        self.assertEqual(shadowed.getpixel((5, 5)), (255, 255, 255))
        self.assertEqual(shadowed.getpixel((600, 600)), RED)

    def test_a_small_crop_can_be_enlarged_only_up_to_the_allowed_limit(self):
        small = cutout(RED, size=(240, 200), shape="rect")             # 제품 160x120
        suggestion = bd.get_suggestion(bd.analyze_product(small), "ADAPT_NEUTRAL", "ADDITIONAL")
        default = Image.open(io.BytesIO(bd.render_suggestion(small, suggestion))).convert("RGB")
        enlarged = Image.open(io.BytesIO(bd.render_suggestion(small, suggestion, max_upscale=2.0))).convert("RGB")
        background = bd._from_hex(suggestion.color_hex)
        self.assertEqual(default.getpixel((600 + 100, 600)), background)       # 기본: 폭 160 → 반폭 80 바깥은 배경
        self.assertEqual(enlarged.getpixel((600 + 100, 600)), RED)             # 2배: 폭 320 → 반폭 160 안쪽은 제품
        self.assertEqual(enlarged.getpixel((600 + 170, 600)), background)      # 2배 한도를 넘어서는 확대는 없다

    def test_the_upscale_limit_is_validated(self):
        suggestion = bd.get_suggestion(self.profile, "ADAPT_WHITE", "ADDITIONAL")
        for value in (0.9, 2.6):
            with self.assertRaises(BadRequestException):
                bd.render_suggestion(self.product, suggestion, max_upscale=value)

    def test_invalid_render_arguments_are_rejected(self):
        suggestion = bd.get_suggestion(self.profile, "ADAPT_WHITE", "ADDITIONAL")
        with self.assertRaises(BadRequestException):
            bd.render_suggestion(self.product, suggestion, fill_ratio=0.1)
        with self.assertRaises(BadRequestException):
            bd.get_suggestion(self.profile, "ADAPT_NOPE", "ADDITIONAL")
        with self.assertRaises(BadRequestException):          # 대표 이미지에서는 흰 배경 외 제안이 없다
            bd.get_suggestion(self.profile, "ADAPT_COMPLEMENT", "MAIN")


AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
    "description VARCHAR(500), ip_address VARCHAR(50))"
)


class ServiceAndRouteTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, MediaAsset.__table__, ImageGenerationJob.__table__,
            ImageGenerationResult.__table__, ImageGenerationDailyUsage.__table__,
        ])
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.media_root = Path(tempfile.mkdtemp())
        self._previous_media_root = os.environ.get("HOMEZ_TEST_MEDIA_ROOT")
        os.environ["HOMEZ_TEST_MEDIA_ROOT"] = str(self.media_root)    # 라우터가 만드는 서비스도 같은 임시 폴더를 쓴다
        self.service = ImageGenerationJobQueueService(self.db, media_root=self.media_root)
        companies = []
        for label, number in (("A", "111-11-11111"), ("B", "222-22-22222")):
            company = Company(
                name=f"회사 {label}", business_number=number, ceo="테스트", phone="02-000-0000",
                email=f"{label.lower()}@test.com", address="서울",
            )
            self.db.add(company)
            companies.append(company)
        self.db.commit()
        self.company_a, self.company_b = companies[0].id, companies[1].id
        self.cutout = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=1, purpose="MAIN", display_order=0,
            image_bytes=cutout(RED), original_filename="cutout.png", company_id=self.company_a,
        )

    def tearDown(self):
        if self._previous_media_root is None:
            os.environ.pop("HOMEZ_TEST_MEDIA_ROOT", None)
        else:
            os.environ["HOMEZ_TEST_MEDIA_ROOT"] = self._previous_media_root
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def test_suggestions_are_read_only_and_describe_the_product(self):
        before = self.db.query(MediaAsset).count()
        result = self.service.suggest_adaptive_backgrounds(self.cutout.id, "ADDITIONAL", 5, 0, self.company_a)
        self.assertEqual(self.db.query(MediaAsset).count(), before)
        self.assertEqual(len(result["suggestions"]), 5)
        self.assertEqual(result["product"]["colors"][0][0], "#C82828")

    def test_compose_adaptive_saves_a_derived_asset_and_names_it_after_the_background(self):
        asset = self.service.compose_adaptive(
            self.cutout.id, "ADAPT_COMPLEMENT", "ADDITIONAL", 0, True, 0.86, 1, self.company_a,
        )
        self.assertEqual((asset.width, asset.height), (1200, 1200))
        self.assertEqual(asset.source_asset_id, self.cutout.id)
        self.assertEqual(asset.original_filename, "adaptive-ADAPT_COMPLEMENT-ADDITIONAL.png")
        self.assertEqual(asset.rights_status, self.cutout.rights_status)

    def test_main_purpose_only_accepts_the_white_background(self):
        with self.assertRaises(BadRequestException):
            self.service.compose_adaptive(self.cutout.id, "ADAPT_COMPLEMENT", "MAIN", 0, False, 0.86, 1, self.company_a)
        asset = self.service.compose_adaptive(self.cutout.id, "ADAPT_WHITE", "MAIN", 0, False, 0.86, 1, self.company_a)
        self.assertEqual(asset.original_filename, "adaptive-ADAPT_WHITE-MAIN.png")

    def test_another_company_cannot_use_the_asset(self):
        with self.assertRaises(NotFoundException):
            self.service.suggest_adaptive_backgrounds(self.cutout.id, "ADDITIONAL", 5, 0, self.company_b)
        with self.assertRaises(NotFoundException):
            self.service.compose_adaptive(self.cutout.id, "ADAPT_WHITE", "MAIN", 0, False, 0.86, 1, self.company_b)

    def test_routes_and_schemas(self):
        from app.domains.media_asset import router as module
        from app.domains.media_asset import schema

        user = SimpleNamespace(id=1, company_id=self.company_a)
        data = schema.BackgroundSuggestionRequestSchema(cutout_asset_id=self.cutout.id)
        self.assertEqual((data.purpose, data.count, data.variation), ("ADDITIONAL", 6, 0))
        result = module.suggest_background(data, current_user=user, db=self.db)
        self.assertGreaterEqual(len(result["suggestions"]), 1)
        composed = module.compose_adaptive_background(
            schema.AdaptiveCompositionRequestSchema(
                cutout_asset_id=self.cutout.id, suggestion_code=result["suggestions"][0]["code"],
            ),
            current_user=user, db=self.db,
        )
        self.assertEqual(composed.width, 1200)
        with self.assertRaises(Exception):
            schema.AdaptiveCompositionRequestSchema(cutout_asset_id=1, suggestion_code="X", fill_ratio=2.0)
        with self.assertRaises(Exception):
            schema.BackgroundSuggestionRequestSchema(cutout_asset_id=1, variation=99)

    def test_the_composition_schema_defaults_to_no_enlargement_and_validates_the_limit(self):
        from app.domains.media_asset import schema

        self.assertEqual(
            schema.AdaptiveCompositionRequestSchema(cutout_asset_id=1, suggestion_code="ADAPT_WHITE").max_upscale, 1.0,
        )
        with self.assertRaises(Exception):
            schema.AdaptiveCompositionRequestSchema(cutout_asset_id=1, suggestion_code="X", max_upscale=3.0)

    def test_the_service_passes_the_upscale_limit_through(self):
        small = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=1, purpose="MAIN", display_order=1,
            image_bytes=cutout(BLUE, size=(240, 200), shape="rect"), original_filename="small.png", company_id=self.company_a,
        )
        plain = self.service.compose_adaptive(small.id, "ADAPT_NEUTRAL", "ADDITIONAL", 0, False, 0.86, 1, self.company_a)
        grown = self.service.compose_adaptive(small.id, "ADAPT_NEUTRAL", "ADDITIONAL", 0, False, 0.86, 1, self.company_a, 2.0)
        self.assertNotEqual(plain.sha256_hex, grown.sha256_hex)

    def test_the_concept_route_names_the_asset_after_the_concept(self):
        from app.domains.media_asset import router as module
        from app.domains.media_asset import schema

        user = SimpleNamespace(id=1, company_id=self.company_a)
        asset = module.compose_background(
            schema.BackgroundCompositionRequestSchema(
                cutout_asset_id=self.cutout.id, concept_code="BLUSH_PINK", purpose="ADDITIONAL",
            ),
            current_user=user, db=self.db,
        )
        self.assertEqual(asset.original_filename, "concept-BLUSH_PINK-ADDITIONAL.png")


if __name__ == "__main__":
    unittest.main()
