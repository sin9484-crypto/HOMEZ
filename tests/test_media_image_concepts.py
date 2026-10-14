"""
=========================================================
Homez OS

File : tests/test_media_image_concepts.py

이미지 컨셉(`image_concepts.py`)과 그 적용(대표 이미지 배경, 상세 페이지 섹션별 컨셉, 목록 API)을 고정한다.
Pillow 합성·임시 SQLite만 쓴다 — 네트워크 없음.
=========================================================
"""

import io
import json
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
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset import image_concepts as ic
from app.domains.media_asset.composition import compose_with_background
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.detail_page_generator import DEFAULT_THEME
from app.domains.media_asset.detail_page_generator import DETAIL_SECTION_KEYS
from app.domains.media_asset.detail_page_generator import DetailPageContent
from app.domains.media_asset.detail_page_generator import FeatureHighlight
from app.domains.media_asset.detail_page_generator import SpecRow
from app.domains.media_asset.detail_page_generator import generate_detail_page_image
from app.domains.media_asset.detail_page_generator import render_detail_sections
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.providers import _deterministic_png
from app.domains.user.model import User  # noqa: F401


def hero_png() -> bytes:
    image = Image.new("RGB", (800, 600), (250, 250, 250))
    ImageDraw.Draw(image).ellipse((200, 100, 600, 500), fill=(200, 60, 60))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def cutout_png() -> bytes:
    image = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((100, 100, 400, 400), fill=(20, 80, 200, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def content(**overrides) -> DetailPageContent:
    base = dict(
        brand_name="테스트 브랜드", hero_image_bytes=hero_png(), tagline="가볍고 편안한 데일리",
        feature_highlights=[FeatureHighlight("부드러운 소재", "피부에 닿는 느낌이 좋습니다.")],
        spec_rows=[SpecRow("소재", "면 100%"), SpecRow("제조국", "중국")], usage_text="찬물에 단독 세탁하세요.",
    )
    base.update(overrides)
    return DetailPageContent(**base)


class ConceptDefinitionTests(unittest.TestCase):

    def test_the_default_concept_is_first_and_is_the_plain_white_one(self):
        concepts = ic.list_concepts()
        self.assertEqual(concepts[0].code, ic.DEFAULT_CONCEPT_CODE)
        self.assertEqual(concepts[0].background_kind, "WHITE")
        self.assertGreaterEqual(len(concepts), 5)
        self.assertEqual(len({c.code for c in concepts}), len(concepts))

    def test_every_concept_keeps_text_readable(self):
        for concept in ic.list_concepts():
            with self.subTest(concept=concept.code):
                self.assertGreaterEqual(ic.contrast_ratio(concept.theme.text, concept.theme.bg), 7.0)
                self.assertGreaterEqual(ic.contrast_ratio(concept.theme.muted, concept.theme.bg), 4.5)
                self.assertGreaterEqual(ic.contrast_ratio(concept.theme.accent, concept.theme.bg), 3.0)

    def test_only_the_white_concept_may_be_used_for_the_main_image(self):
        self.assertEqual([c.code for c in ic.list_concepts("MAIN")], ["CLEAN_WHITE"])
        for concept in ic.list_concepts():
            if concept.code != "CLEAN_WHITE":
                with self.assertRaises(BadRequestException):
                    ic.require_usable(concept.code, "MAIN")
        self.assertEqual(len(ic.list_concepts("DETAIL")), len(ic.list_concepts()))
        self.assertEqual(len(ic.list_concepts("ADDITIONAL")), len(ic.list_concepts()))

    def test_unknown_concepts_and_purposes_are_rejected(self):
        with self.assertRaises(BadRequestException):
            ic.get_concept("NEON_RAINBOW")
        with self.assertRaises(BadRequestException):
            ic.require_usable("CLEAN_WHITE", "BANNER")
        with self.assertRaises(BadRequestException):
            ic.list_concepts("BANNER")

    def test_summaries_are_json_serializable_for_the_screen(self):
        summaries = [c.to_summary() for c in ic.list_concepts()]
        self.assertEqual(json.loads(json.dumps(summaries))[0]["code"], "CLEAN_WHITE")
        for summary in summaries:
            self.assertTrue(summary["preview"]["accent"].startswith("#"))


class MainImageConceptTests(unittest.TestCase):

    def test_every_concept_background_composes_a_square_image_with_the_product_in_the_middle(self):
        for concept in ic.list_concepts():
            with self.subTest(concept=concept.code):
                result = compose_with_background(cutout_png(), concept.background_spec())
                image = Image.open(io.BytesIO(result)).convert("RGB")
                self.assertEqual(image.size, (1200, 1200))
                self.assertEqual(image.getpixel((600, 600)), (20, 80, 200))   # 제품은 그대로 가운데에

    def test_the_white_concept_gives_a_white_corner_and_the_others_do_not(self):
        white = Image.open(io.BytesIO(compose_with_background(cutout_png(), ic.get_concept("CLEAN_WHITE").background_spec()))).convert("RGB")
        self.assertEqual(white.getpixel((5, 5)), (255, 255, 255))
        for code in ("SOFT_BEIGE", "FRESH_MINT", "BLUSH_PINK", "CALM_GRAY", "MIDNIGHT_PREMIUM"):
            other = Image.open(io.BytesIO(compose_with_background(cutout_png(), ic.get_concept(code).background_spec()))).convert("RGB")
            self.assertNotEqual(other.getpixel((5, 5)), (255, 255, 255), code)


class DetailPageConceptTests(unittest.TestCase):

    def test_without_a_concept_the_page_is_exactly_as_before(self):
        sections = render_detail_sections(content())
        self.assertEqual([k for k, _ in sections], ["BRAND", "HERO", "FEATURES", "SPECS", "USAGE"])
        for key, image in sections:
            if key != "HERO":
                self.assertEqual(image.getpixel((2, 2)), DEFAULT_THEME.bg, key)
        self.assertTrue(generate_detail_page_image(content()).startswith(b"\xff\xd8"))

    def test_a_page_concept_colors_every_text_section(self):
        concept = ic.get_concept("BLUSH_PINK")
        for key, image in render_detail_sections(content(concept_code="BLUSH_PINK")):
            if key != "HERO":
                self.assertEqual(image.getpixel((2, 2)), concept.theme.bg, key)

    def test_each_section_can_use_its_own_concept(self):
        sections = dict(render_detail_sections(content(
            concept_code="CALM_GRAY",
            section_concepts={"FEATURES": "FRESH_MINT", "USAGE": "MIDNIGHT_PREMIUM"},
        )))
        self.assertEqual(sections["BRAND"].getpixel((2, 2)), ic.get_concept("CALM_GRAY").theme.bg)
        self.assertEqual(sections["FEATURES"].getpixel((2, 2)), ic.get_concept("FRESH_MINT").theme.bg)
        self.assertEqual(sections["SPECS"].getpixel((2, 2)), ic.get_concept("CALM_GRAY").theme.bg)
        self.assertEqual(sections["USAGE"].getpixel((2, 2)), ic.get_concept("MIDNIGHT_PREMIUM").theme.bg)

    def test_the_concept_accent_is_used_unless_the_user_chose_a_custom_color(self):
        concept = ic.get_concept("FRESH_MINT")
        features = dict(render_detail_sections(content(concept_code="FRESH_MINT")))["FEATURES"]
        bar_pixel = features.getpixel((32 + 2, 40))        # 특장점 막대(_PADDING + 2px)
        self.assertEqual(bar_pixel, concept.theme.accent)
        custom = dict(render_detail_sections(content(concept_code="FRESH_MINT", accent_color_hex="#112233")))["FEATURES"]
        self.assertEqual(custom.getpixel((32 + 2, 40)), (0x11, 0x22, 0x33))

    def test_unknown_concepts_and_unknown_sections_are_rejected(self):
        with self.assertRaises(BadRequestException):
            render_detail_sections(content(concept_code="NEON_RAINBOW"))
        with self.assertRaises(BadRequestException):
            render_detail_sections(content(section_concepts={"BANNER": "CLEAN_WHITE"}))
        with self.assertRaises(BadRequestException):
            render_detail_sections(content(section_concepts={"USAGE": "NEON_RAINBOW"}))

    def test_the_section_keys_are_the_documented_five(self):
        self.assertEqual(DETAIL_SECTION_KEYS, ("BRAND", "HERO", "FEATURES", "SPECS", "USAGE"))


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
        self.service = ImageGenerationJobQueueService(self.db, media_root=Path(tempfile.mkdtemp()))
        company = Company(
            name="회사 A", business_number="111-11-11111", ceo="테스트", phone="02-000-0000",
            email="a@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id
        self.hero = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=1, purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("concept-hero"), original_filename="hero.png", company_id=self.company_id,
        )

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def test_the_service_generates_a_detail_page_with_a_concept(self):
        asset = self.service.generate_detail_page(
            hero_asset_id=self.hero.id, brand_name="브랜드", tagline=None,
            feature_highlights=[("특징", "설명")], spec_rows=[("소재", "면")], usage_text="안내",
            accent_color_hex="#2D6CDF", requested_by=1, company_id=self.company_id,
            concept_code="BLUSH_PINK", section_concepts={"SPECS": "CALM_GRAY"},
        )
        self.assertEqual(asset.purpose, "DETAIL")
        self.assertEqual(asset.width, 780)

    def test_the_service_rejects_an_unknown_concept(self):
        with self.assertRaises(BadRequestException):
            self.service.generate_detail_page(
                hero_asset_id=self.hero.id, brand_name="브랜드", tagline=None, feature_highlights=[],
                spec_rows=[], usage_text=None, accent_color_hex="#2D6CDF", requested_by=1,
                company_id=self.company_id, concept_code="NEON_RAINBOW",
            )

    def test_list_routes_expose_concepts_and_sections_read_only(self):
        from app.domains.media_asset import router as module

        user = SimpleNamespace(id=1, company_id=self.company_id)
        everything = module.list_image_concepts(purpose=None, current_user=user)
        self.assertEqual(everything[0]["code"], "CLEAN_WHITE")
        self.assertEqual([c["code"] for c in module.list_image_concepts(purpose="MAIN", current_user=user)], ["CLEAN_WHITE"])
        sections = module.list_detail_sections(current_user=user)
        self.assertEqual([s["key"] for s in sections], list(DETAIL_SECTION_KEYS))
        self.assertEqual(sections[2]["label_ko"], "주요 특장점")

    def test_composition_schema_defaults_keep_the_old_behavior_and_main_needs_white(self):
        from app.domains.media_asset import schema
        from app.domains.media_asset import router as module

        request = schema.BackgroundCompositionRequestSchema(cutout_asset_id=1)
        self.assertIsNone(request.concept_code)
        self.assertEqual(request.purpose, "ADDITIONAL")
        main_pink = schema.BackgroundCompositionRequestSchema(cutout_asset_id=1, concept_code="BLUSH_PINK", purpose="MAIN")
        with self.assertRaises(BadRequestException):
            ic.require_usable(main_pink.concept_code, main_pink.purpose)
        # 같은 규칙을 라우터가 쓰는지 소스로 확인한다.
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertIn("require_usable(data.concept_code, data.purpose).background_spec()", source)


if __name__ == "__main__":
    unittest.main()
