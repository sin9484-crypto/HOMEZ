"""
=========================================================
Homez OS

File : tests/test_synthetic_tall_image_helper.py

tests/synthetic_tall_image_helper.py 자체를 검증한다. 이 헬퍼가
tests/test_long_detail_image_ingest.py와
tests/test_media_asset_image_workflow.py의 개인 Desktop 이미지
의존성(샤프란 참고 사진)을 대체하므로, 헬퍼 자체가 실제 요구사항
(정확한 치수, 결정성, 분할 시 조각 간 완전한 유일성)을 만족하는지
별도로 고정해 둔다 — 특히 "조각 간 유일성"은 최초 구현(반복되는
색 줄무늬)에서 실제로 깨졌던 것을 이 세션에서 발견·수정한
회귀 방지 대상이다.
=========================================================
"""

import hashlib
import unittest

from PIL import Image

from app.domains.media_asset.image_split import split_tall_image
from tests.synthetic_tall_image_helper import SAFFRON_REFERENCE_HEIGHT
from tests.synthetic_tall_image_helper import SAFFRON_REFERENCE_WIDTH
from tests.synthetic_tall_image_helper import build_synthetic_tall_product_jpeg


class SyntheticTallImageHelperTestCase(unittest.TestCase):

    def test_default_dimensions_match_original_saffron_reference(self):
        # 기존 테스트의 하드코딩된 assertion(예: seg.width == 780)이
        # 그대로 유지되려면 이 상수·기본값이 실제 참고 사진과 정확히
        # 같은 치수여야 한다.
        self.assertEqual(SAFFRON_REFERENCE_WIDTH, 780)
        self.assertEqual(SAFFRON_REFERENCE_HEIGHT, 15548)

    def test_produces_valid_jpeg_with_requested_dimensions(self):

        data = build_synthetic_tall_product_jpeg(width=100, height=2000)

        import io
        img = Image.open(io.BytesIO(data))
        self.assertEqual(img.format, "JPEG")
        self.assertEqual(img.size, (100, 2000))

    def test_generation_is_deterministic(self):

        first = build_synthetic_tall_product_jpeg()
        second = build_synthetic_tall_product_jpeg()

        self.assertEqual(first, second)

    def test_file_size_is_well_within_long_detail_upload_limit(self):

        data = build_synthetic_tall_product_jpeg()

        self.assertLess(len(data), 20 * 1024 * 1024)

    def test_split_segments_are_all_pairwise_distinct(self):
        """2026-08-31 회귀 방지 — 최초 구현은 반복되는 색 줄무늬를
        썼는데, 그 결과 서로 다른 두 조각이 완전히 같은 PNG 바이트로
        압축돼 `_persist_generated_asset()`의 storage_path 기준 콘텐츠
        중복 제거 로직이 두 번째 조각에 대해 새 행 대신 첫 조각의
        기존 행(과 그 display_order)을 돌려주는 문제가 실제로
        재현됐다. 연속 그라디언트로 바꿔 해결했다 — 이 테스트가 그
        전제(조각 checksum 전부 유일)를 다시 깨지 않게 고정한다."""

        data = build_synthetic_tall_product_jpeg()
        segments = split_tall_image(data, segment_height=1200)

        self.assertGreater(len(segments), 1)
        digests = [hashlib.sha256(s).hexdigest() for s in segments]
        self.assertEqual(
            len(digests), len(set(digests)),
            "합성 이미지의 분할 조각 중 완전히 동일한 바이트를 가진 "
            "조각이 있습니다 — 콘텐츠 기준 중복 제거 로직과 충돌할 "
            "수 있습니다.",
        )


if __name__ == "__main__":
    unittest.main()
