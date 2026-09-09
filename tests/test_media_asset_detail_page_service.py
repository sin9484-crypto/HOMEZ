"""
=========================================================
Homez OS

File : tests/test_media_asset_detail_page_service.py

ImageGenerationJobQueueService.generate_detail_page() 검증 —
app/domains/media_asset/job_queue_service.py::compose_background()와
동일한 패턴(tests/test_media_asset_image_workflow.py 참고). 실제
Pillow 렌더링·실제 파일 저장을 사용한다 — 네트워크 호출 없음.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset.constants import ImageJobStatus
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.constants import MediaAssetRole
from app.domains.media_asset.job_queue_service import (
    ImageGenerationJobQueueService,
)
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.providers import _deterministic_png
from app.domains.user.model import User  # noqa: F401

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class GenerateDetailPageServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                MediaAsset.__table__,
                ImageGenerationJob.__table__,
                ImageGenerationResult.__table__,
                ImageGenerationDailyUsage.__table__,
            ],
        )

        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.media_root = Path(tempfile.mkdtemp())
        self.service = ImageGenerationJobQueueService(
            self.db, media_root=self.media_root,
        )

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")

        self.hero = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=1,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("detail-page-hero-1"),
            original_filename="hero.png", company_id=self.company_a.id,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_company(self, label, business_number):

        company = Company(
            name=f"회사 {label}", business_number=business_number,
            ceo="테스트", phone="02-000-0000",
            email=f"{label.lower()}@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()

        return company

    def test_generates_generated_asset_with_correct_lineage(self):

        result = self.service.generate_detail_page(
            hero_asset_id=self.hero.id,
            brand_name="에브리홈즈",
            tagline="주방을 더 편하게",
            feature_highlights=[("흡수력", "빠르게 흡수합니다")],
            spec_rows=[("소재", "부직포")],
            usage_text="세척 후 건조하세요.",
            accent_color_hex="#2D6CDF",
            requested_by=1, company_id=self.company_a.id,
        )

        self.assertEqual(result.asset_role, MediaAssetRole.GENERATED)
        self.assertEqual(result.source_asset_id, self.hero.id)
        self.assertEqual(result.purpose, "DETAIL")
        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertEqual(result.width, 780)
        self.assertGreater(result.height, 0)

    def test_generated_asset_is_a_valid_readable_image(self):

        result = self.service.generate_detail_page(
            hero_asset_id=self.hero.id, brand_name="브랜드",
            tagline=None, feature_highlights=[], spec_rows=[],
            usage_text=None, accent_color_hex="#2D6CDF",
            requested_by=1, company_id=self.company_a.id,
        )

        stored_path = self.media_root / result.storage_path
        img = Image.open(stored_path)
        self.assertEqual(img.width, 780)

    def test_inherits_rights_status_from_hero(self):

        self.service.confirm_rights_verified(
            self.hero.id, self.company_a.id, "SELF_CAPTURED", 1,
        )
        self.db.refresh(self.hero)
        self.assertEqual(self.hero.rights_status, "VERIFIED")

        result = self.service.generate_detail_page(
            hero_asset_id=self.hero.id, brand_name="브랜드",
            tagline=None, feature_highlights=[], spec_rows=[],
            usage_text=None, accent_color_hex="#2D6CDF",
            requested_by=1, company_id=self.company_a.id,
        )

        self.assertEqual(result.rights_status, "VERIFIED")

    def test_nonexistent_hero_asset_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.generate_detail_page(
                hero_asset_id=999999, brand_name="브랜드",
                tagline=None, feature_highlights=[], spec_rows=[],
                usage_text=None, accent_color_hex="#2D6CDF",
                requested_by=1, company_id=self.company_a.id,
            )

    def test_hero_asset_from_other_company_raises_not_found(self):
        """tenant 격리 — 다른 회사 자산을 hero로 쓸 수 없다."""

        with self.assertRaises(NotFoundException):
            self.service.generate_detail_page(
                hero_asset_id=self.hero.id, brand_name="브랜드",
                tagline=None, feature_highlights=[], spec_rows=[],
                usage_text=None, accent_color_hex="#2D6CDF",
                requested_by=1, company_id=self.company_b.id,
            )

    def test_invalid_input_records_failed_job_and_raises(self):

        with self.assertRaises(BadRequestException):
            self.service.generate_detail_page(
                hero_asset_id=self.hero.id, brand_name="   ",
                tagline=None, feature_highlights=[], spec_rows=[],
                usage_text=None, accent_color_hex="#2D6CDF",
                requested_by=1, company_id=self.company_a.id,
            )

        jobs = self.db.query(ImageGenerationJob).filter(
            ImageGenerationJob.company_id == self.company_a.id,
            ImageGenerationJob.provider_code == "PILLOW_LOCAL",
        ).all()
        self.assertTrue(jobs)
        self.assertEqual(jobs[-1].status, ImageJobStatus.FAILED)

    def test_too_tall_content_is_rejected_and_original_untouched(self):

        with self.assertRaises(BadRequestException):
            self.service.generate_detail_page(
                hero_asset_id=self.hero.id, brand_name="브랜드",
                tagline=None, feature_highlights=[], spec_rows=[],
                usage_text="긴 사용법 설명입니다. " * 2000,
                accent_color_hex="#2D6CDF",
                requested_by=1, company_id=self.company_a.id,
            )

        self.db.refresh(self.hero)
        self.assertEqual(self.hero.status, "ACTIVE")


if __name__ == "__main__":
    unittest.main()
