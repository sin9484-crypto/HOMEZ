"""
=========================================================
Homez OS

File : tests/test_media_asset_detail_page_generator.py

app/domains/media_asset/detail_page_generator.py 단위 테스트. 실제
Windows 표준 폰트(맑은 고딕)에 의존한다 — 이 프로젝트는 Windows
데스크톱 전용이므로 CI/개발 환경 모두 Windows다.
=========================================================
"""

import io
import unittest

from PIL import Image

from app.core.exceptions import BadRequestException
from app.domains.media_asset.detail_page_generator import DETAIL_IMAGE_WIDTH
from app.domains.media_asset.detail_page_generator import DetailPageContent
from app.domains.media_asset.detail_page_generator import DetailPageTooTallError
from app.domains.media_asset.detail_page_generator import FeatureHighlight
from app.domains.media_asset.detail_page_generator import SpecRow
from app.domains.media_asset.detail_page_generator import (
    generate_detail_page_image,
)


def _solid_jpeg_bytes(size=(1000, 1000), color=(200, 50, 50)) -> bytes:

    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")

    return buf.getvalue()


class GenerateDetailPageImageTestCase(unittest.TestCase):

    def test_minimal_content_produces_valid_jpeg_at_target_width(self):

        content = DetailPageContent(
            brand_name="테스트 브랜드",
            hero_image_bytes=_solid_jpeg_bytes(),
        )
        result = generate_detail_page_image(content)
        img = Image.open(io.BytesIO(result))

        self.assertEqual(img.format, "JPEG")
        self.assertEqual(img.width, DETAIL_IMAGE_WIDTH)
        self.assertGreater(img.height, 0)

    def test_full_content_all_sections_render_without_error(self):

        content = DetailPageContent(
            brand_name="에브리홈즈 키친",
            tagline="주방을 더 편하게",
            hero_image_bytes=_solid_jpeg_bytes(),
            feature_highlights=[
                FeatureHighlight("흡수력 3배", "삼중 압축 부직포로 물기를 빠르게 흡수합니다."),
                FeatureHighlight("강한 내구성", "반복 세탁에도 형태가 유지됩니다."),
                FeatureHighlight("국내 생산", "KC 인증을 받은 국내 생산 제품입니다."),
            ],
            spec_rows=[
                SpecRow("소재", "폴리에스터 부직포"),
                SpecRow("구성", "40매"),
            ],
            usage_text="세제 없이 물로만 세척 후 그늘에서 건조해 주세요.",
        )
        result = generate_detail_page_image(content)
        img = Image.open(io.BytesIO(result))

        self.assertEqual(img.width, DETAIL_IMAGE_WIDTH)

    def test_empty_brand_name_is_rejected(self):

        content = DetailPageContent(
            brand_name="   ", hero_image_bytes=_solid_jpeg_bytes(),
        )
        with self.assertRaises(BadRequestException):
            generate_detail_page_image(content)

    def test_missing_hero_image_is_rejected(self):

        content = DetailPageContent(brand_name="브랜드", hero_image_bytes=b"")
        with self.assertRaises(BadRequestException):
            generate_detail_page_image(content)

    def test_invalid_accent_color_is_rejected(self):

        content = DetailPageContent(
            brand_name="브랜드", hero_image_bytes=_solid_jpeg_bytes(),
            accent_color_hex="not-a-color",
        )
        with self.assertRaises(BadRequestException):
            generate_detail_page_image(content)

    def test_more_than_three_feature_highlights_is_silently_truncated(self):

        content = DetailPageContent(
            brand_name="브랜드", hero_image_bytes=_solid_jpeg_bytes(),
            feature_highlights=[
                FeatureHighlight(f"특징{i}", f"설명{i}") for i in range(6)
            ],
        )
        # 예외 없이 성공해야 하고(잘라내기), 총 높이가 지나치게
        # 커지지 않아야 한다(6개를 전부 렌더링했다면 훨씬 커졌을 것).
        result_truncated = generate_detail_page_image(content)
        img_truncated = Image.open(io.BytesIO(result_truncated))

        content_three = DetailPageContent(
            brand_name="브랜드", hero_image_bytes=_solid_jpeg_bytes(),
            feature_highlights=[
                FeatureHighlight(f"특징{i}", f"설명{i}") for i in range(3)
            ],
        )
        result_three = generate_detail_page_image(content_three)
        img_three = Image.open(io.BytesIO(result_three))

        self.assertEqual(img_truncated.height, img_three.height)

    def test_extremely_long_usage_text_exceeding_max_height_is_blocked(self):

        content = DetailPageContent(
            brand_name="브랜드", hero_image_bytes=_solid_jpeg_bytes(),
            usage_text="아주 긴 사용법 설명입니다. " * 2000,
        )
        with self.assertRaises(DetailPageTooTallError):
            generate_detail_page_image(content)

    def test_hero_image_wider_than_target_is_scaled_down_proportionally(self):

        content = DetailPageContent(
            brand_name="브랜드",
            hero_image_bytes=_solid_jpeg_bytes(size=(2000, 1000)),
        )
        result = generate_detail_page_image(content)
        img = Image.open(io.BytesIO(result))

        self.assertEqual(img.width, DETAIL_IMAGE_WIDTH)

    def test_result_is_deterministic_for_same_input(self):

        content = DetailPageContent(
            brand_name="브랜드", hero_image_bytes=_solid_jpeg_bytes(),
            spec_rows=[SpecRow("소재", "면")],
        )
        result1 = generate_detail_page_image(content)
        result2 = generate_detail_page_image(content)

        img1 = Image.open(io.BytesIO(result1))
        img2 = Image.open(io.BytesIO(result2))
        self.assertEqual(img1.size, img2.size)
        self.assertEqual(img1.tobytes(), img2.tobytes())


if __name__ == "__main__":
    unittest.main()
