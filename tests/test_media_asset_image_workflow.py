"""
=========================================================
Homez OS

File : tests/test_media_asset_image_workflow.py

2026-08-20 CTO 지시 — 상품 등록 3단계 이미지 Workflow(업로드/누끼/
배경 합성) 신규 기능 검증. 실제 외부 Provider(REMBG/실제 검색)는
호출하지 않는다 — FAKE Provider와 순수 로컬 Pillow 합성만 검증한다.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec
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


class MediaAssetImageWorkflowTestCase(unittest.TestCase):

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

    # ------------------------------------------------
    # 업로드
    # ------------------------------------------------

    def test_upload_creates_original_asset(self):

        image_bytes = _deterministic_png("saffron-real-photo-1")

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=1,
            purpose="MAIN",
            display_order=0,
            image_bytes=image_bytes,
            original_filename="saffron_front.jpg",
            company_id=self.company_a.id,
        )

        self.assertEqual(asset.asset_role, MediaAssetRole.ORIGINAL)
        self.assertIsNone(asset.source_asset_id)
        self.assertEqual(asset.company_id, self.company_a.id)
        self.assertEqual(asset.original_filename, "saffron_front.jpg")

    def test_upload_rejects_invalid_bytes(self):

        with self.assertRaises(BadRequestException):
            self.service.upload_original_asset(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
                owner_id=1, purpose="MAIN", display_order=0,
                image_bytes=b"not-an-image",
                original_filename=None, company_id=self.company_a.id,
            )

    def test_upload_rejects_unknown_owner_type(self):

        with self.assertRaises(BadRequestException):
            self.service.upload_original_asset(
                owner_type="NOT_A_REAL_TYPE",
                owner_id=1, purpose="MAIN", display_order=0,
                image_bytes=_deterministic_png("x"),
                original_filename=None, company_id=self.company_a.id,
            )

    def test_upload_enforces_active_image_limit(self):

        for i in range(30):
            self.service.upload_original_asset(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
                owner_id=2, purpose="DETAIL", display_order=i,
                image_bytes=_deterministic_png(f"img-{i}"),
                original_filename=None, company_id=self.company_a.id,
            )

        with self.assertRaises(BadRequestException):
            self.service.upload_original_asset(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
                owner_id=2, purpose="DETAIL", display_order=31,
                image_bytes=_deterministic_png("img-31"),
                original_filename=None, company_id=self.company_a.id,
            )

    def test_upload_same_bytes_is_idempotent(self):

        image_bytes = _deterministic_png("dup-test")

        first = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=3,
            purpose="MAIN", display_order=0, image_bytes=image_bytes,
            original_filename=None, company_id=self.company_a.id,
        )
        second = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=3,
            purpose="MAIN", display_order=0, image_bytes=image_bytes,
            original_filename=None, company_id=self.company_a.id,
        )

        self.assertEqual(first.id, second.id)

    # ------------------------------------------------
    # 원본 바이트 조회(2026-08-20 3차 지시 — UI 썸네일/미리보기 전용)
    # ------------------------------------------------

    def test_get_media_asset_file_bytes_returns_original_content(self):

        image_bytes = _deterministic_png("thumb-source")

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=4,
            purpose="MAIN", display_order=0, image_bytes=image_bytes,
            original_filename="thumb.png", company_id=self.company_a.id,
        )

        fetched_bytes, mime_type = self.service.get_media_asset_file_bytes(
            asset.id, self.company_a.id,
        )

        self.assertEqual(fetched_bytes, image_bytes)
        self.assertEqual(mime_type, asset.mime_type)

    def test_get_media_asset_file_bytes_blocks_other_company(self):

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=5,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("company-a-only"),
            original_filename=None, company_id=self.company_a.id,
        )

        with self.assertRaises(NotFoundException):
            self.service.get_media_asset_file_bytes(
                asset.id, self.company_b.id,
            )

    # ------------------------------------------------
    # 배경 제거(누끼, FAKE Provider)
    # ------------------------------------------------

    def test_remove_background_fake_provider_creates_generated_asset(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=10,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("saffron-for-cutout"),
            original_filename=None, company_id=self.company_a.id,
        )

        cutout = self.service.remove_background(
            source_asset_id=original.id, provider_code="FAKE",
            requested_by=1, company_id=self.company_a.id,
        )

        self.assertEqual(cutout.asset_role, MediaAssetRole.GENERATED)
        self.assertEqual(cutout.source_asset_id, original.id)
        self.assertEqual(cutout.mime_type, "image/png")

        # 원본은 그대로 남아있어야 한다(수정되지 않음).
        untouched = self.service.repository.get_media_asset_for_company(
            original.id, self.company_a.id,
        )
        self.assertEqual(untouched.asset_role, MediaAssetRole.ORIGINAL)
        self.assertEqual(untouched.id, original.id)

    def test_remove_background_records_job_provenance(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=11,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("provenance-test"),
            original_filename=None, company_id=self.company_a.id,
        )

        self.service.remove_background(
            source_asset_id=original.id, provider_code="FAKE",
            requested_by=7, company_id=self.company_a.id,
        )

        jobs = (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == self.company_a.id)
            .filter(ImageGenerationJob.provider_code == "FAKE")
            .all()
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].status, ImageJobStatus.SUCCEEDED)
        self.assertEqual(jobs[0].requested_by, 7)
        self.assertIn(str(original.id), jobs[0].request_payload_json)

    def test_remove_background_disabled_provider_fails_and_keeps_original(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=12,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("disabled-test"),
            original_filename=None, company_id=self.company_a.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.remove_background(
                source_asset_id=original.id, provider_code="DISABLED",
                requested_by=1, company_id=self.company_a.id,
            )

        untouched = self.service.repository.get_media_asset_for_company(
            original.id, self.company_a.id,
        )
        self.assertEqual(untouched.asset_role, MediaAssetRole.ORIGINAL)

        failed_jobs = (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == self.company_a.id)
            .filter(ImageGenerationJob.provider_code == "DISABLED")
            .all()
        )
        self.assertEqual(len(failed_jobs), 1)
        self.assertEqual(failed_jobs[0].status, ImageJobStatus.FAILED)

    def test_remove_background_blocks_cross_company_asset(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=13,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("company-a-only"),
            original_filename=None, company_id=self.company_a.id,
        )

        with self.assertRaises(NotFoundException):
            self.service.remove_background(
                source_asset_id=original.id, provider_code="FAKE",
                requested_by=1, company_id=self.company_b.id,
            )

    # ------------------------------------------------
    # 수동 편집(Fabric.js MVP, 2026-08-29 Phase 5) — 편집 자체는
    # 브라우저에서 끝난 최종 이미지 1장만 서버가 받는다.
    # ------------------------------------------------

    def test_manual_edit_creates_generated_asset_and_keeps_original(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=20,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("edit-source"),
            original_filename=None, company_id=self.company_a.id,
        )

        edited = self.service.save_manual_edit(
            source_asset_id=original.id,
            image_bytes=_deterministic_png("edit-result"),
            requested_by=1, company_id=self.company_a.id,
        )

        self.assertEqual(edited.asset_role, MediaAssetRole.GENERATED)
        self.assertEqual(edited.source_asset_id, original.id)
        self.assertEqual(edited.owner_type, original.owner_type)
        self.assertEqual(edited.owner_id, original.owner_id)
        self.assertEqual(edited.purpose, original.purpose)

        untouched = self.service.repository.get_media_asset_for_company(
            original.id, self.company_a.id,
        )
        self.assertEqual(untouched.asset_role, MediaAssetRole.ORIGINAL)
        self.assertEqual(untouched.id, original.id)
        self.assertNotEqual(edited.id, original.id)

    def test_manual_edit_records_job_provenance(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=21,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("edit-provenance"),
            original_filename=None, company_id=self.company_a.id,
        )

        self.service.save_manual_edit(
            source_asset_id=original.id,
            image_bytes=_deterministic_png("edit-provenance-result"),
            requested_by=9, company_id=self.company_a.id,
        )

        jobs = (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == self.company_a.id)
            .filter(ImageGenerationJob.provider_code == "LOCAL_MANUAL_EDIT")
            .all()
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].status, ImageJobStatus.SUCCEEDED)
        self.assertEqual(jobs[0].requested_by, 9)
        self.assertIn(str(original.id), jobs[0].request_payload_json)

    def test_manual_edit_rejects_invalid_bytes_and_keeps_original(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=22,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("edit-invalid-source"),
            original_filename=None, company_id=self.company_a.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.save_manual_edit(
                source_asset_id=original.id, image_bytes=b"not-an-image",
                requested_by=1, company_id=self.company_a.id,
            )

        untouched = self.service.repository.get_media_asset_for_company(
            original.id, self.company_a.id,
        )
        self.assertEqual(untouched.asset_role, MediaAssetRole.ORIGINAL)

        failed_jobs = (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.company_id == self.company_a.id)
            .filter(ImageGenerationJob.provider_code == "LOCAL_MANUAL_EDIT")
            .all()
        )
        self.assertEqual(len(failed_jobs), 1)
        self.assertEqual(failed_jobs[0].status, ImageJobStatus.FAILED)

    def test_manual_edit_blocks_cross_company_source_asset(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=23,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("edit-company-a-only"),
            original_filename=None, company_id=self.company_a.id,
        )

        with self.assertRaises(NotFoundException):
            self.service.save_manual_edit(
                source_asset_id=original.id,
                image_bytes=_deterministic_png("edit-company-b-attempt"),
                requested_by=1, company_id=self.company_b.id,
            )

    # ------------------------------------------------
    # 배경 합성(Pillow)
    # ------------------------------------------------

    def test_compose_white_background(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=20,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("compose-source"),
            original_filename=None, company_id=self.company_a.id,
        )
        cutout = self.service.remove_background(
            source_asset_id=original.id, provider_code="FAKE",
            requested_by=1, company_id=self.company_a.id,
        )

        composed = self.service.compose_background(
            cutout_asset_id=cutout.id,
            background_spec=BackgroundSpec(kind=BackgroundKind.WHITE),
            requested_by=1, company_id=self.company_a.id,
        )

        self.assertEqual(composed.asset_role, MediaAssetRole.GENERATED)
        self.assertEqual(composed.source_asset_id, cutout.id)
        self.assertEqual(composed.width, 1200)
        self.assertEqual(composed.height, 1200)

    def test_compose_solid_color_and_gradient(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=21,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("compose-source-2"),
            original_filename=None, company_id=self.company_a.id,
        )
        cutout = self.service.remove_background(
            source_asset_id=original.id, provider_code="FAKE",
            requested_by=1, company_id=self.company_a.id,
        )

        solid = self.service.compose_background(
            cutout_asset_id=cutout.id,
            background_spec=BackgroundSpec(
                kind=BackgroundKind.SOLID_COLOR, color_hex="#F5F7FA",
            ),
            requested_by=1, company_id=self.company_a.id,
        )
        self.assertEqual(solid.asset_role, MediaAssetRole.GENERATED)

        gradient = self.service.compose_background(
            cutout_asset_id=cutout.id,
            background_spec=BackgroundSpec(
                kind=BackgroundKind.GRADIENT,
                color_hex="#FFFFFF", color_hex_end="#DDDDDD",
            ),
            requested_by=1, company_id=self.company_a.id,
        )
        self.assertEqual(gradient.asset_role, MediaAssetRole.GENERATED)
        self.assertNotEqual(solid.sha256_hex, gradient.sha256_hex)

    def test_compose_rejects_invalid_color(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=22,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("compose-source-3"),
            original_filename=None, company_id=self.company_a.id,
        )
        cutout = self.service.remove_background(
            source_asset_id=original.id, provider_code="FAKE",
            requested_by=1, company_id=self.company_a.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.compose_background(
                cutout_asset_id=cutout.id,
                background_spec=BackgroundSpec(
                    kind=BackgroundKind.SOLID_COLOR, color_hex="not-a-color",
                ),
                requested_by=1, company_id=self.company_a.id,
            )

    # ------------------------------------------------
    # 긴 이미지 분할
    # ------------------------------------------------

    def test_split_long_image_creates_segments(self):

        import io

        from PIL import Image

        tall = Image.new("RGB", (780, 3600), (200, 100, 50))
        buf = io.BytesIO()
        tall.save(buf, format="PNG")

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=40,
            purpose="DETAIL", display_order=0,
            image_bytes=buf.getvalue(), original_filename="tall.png",
            company_id=self.company_a.id,
        )

        segments = self.service.split_long_image(
            original.id, segment_height=1200, requested_by=1,
            company_id=self.company_a.id,
        )

        self.assertEqual(len(segments), 3)
        for seg in segments:
            self.assertEqual(seg.asset_role, MediaAssetRole.GENERATED)
            self.assertEqual(seg.source_asset_id, original.id)

        untouched = self.service.repository.get_media_asset_for_company(
            original.id, self.company_a.id,
        )
        self.assertEqual(untouched.asset_role, MediaAssetRole.ORIGINAL)

    def test_split_function_handles_tall_reference_image_directly(self):
        """780×15,548px 세로 이미지로 분할 로직 자체를 검증한다. 이
        치수는 현재 MAX_IMAGE_HEIGHT(4096px) 상한 때문에 일반 업로드
        경로(upload_original_asset)를 통과하지 못한다 — 이건 실제로
        확인된 제약이며 이 테스트가 그 사실을 그대로 고정한다(상한을
        임의로 올리지 않음, 결과 보고에 명시). 분할 함수 자체
        (split_tall_image)는 파일 크기 제한과 무관한 순수 함수라
        원본 바이트로 직접 검증한다.

        2026-08-31 Phase 8 차단 해소 작업 — 이전에는 개발자 개인
        Desktop의 실제 참고 사진("샤프란")에 의존해, 그 파일이 없는
        환경에서는 항상 skip됐다. 검증 대상(치수·MAX_IMAGE_HEIGHT
        초과 거부·분할 조각 수·조각당 크기 상한)은 실제 사진의 픽셀
        내용에 의존하지 않으므로, 같은 치수의 합성 JPEG으로
        교체했다(tests/synthetic_tall_image_helper.py) — assertion
        강도는 그대로 유지했다."""

        from app.domains.media_asset.image_split import split_tall_image
        from tests.synthetic_tall_image_helper import (
            build_synthetic_tall_product_jpeg,
        )

        image_bytes = build_synthetic_tall_product_jpeg()

        with self.assertRaises(BadRequestException):
            self.service.upload_original_asset(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=41,
                purpose="DETAIL", display_order=0,
                image_bytes=image_bytes, original_filename="tall_reference.jpg",
                company_id=self.company_a.id,
            )

        segments = split_tall_image(image_bytes, segment_height=1200)

        self.assertGreater(len(segments), 1)
        for seg in segments:
            self.assertLessEqual(len(seg), 10 * 1024 * 1024)

    def test_compose_blocks_cross_company_asset(self):

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=23,
            purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("company-a-cutout-source"),
            original_filename=None, company_id=self.company_a.id,
        )
        cutout = self.service.remove_background(
            source_asset_id=original.id, provider_code="FAKE",
            requested_by=1, company_id=self.company_a.id,
        )

        with self.assertRaises(NotFoundException):
            self.service.compose_background(
                cutout_asset_id=cutout.id,
                background_spec=BackgroundSpec(kind=BackgroundKind.WHITE),
                requested_by=1, company_id=self.company_b.id,
            )


if __name__ == "__main__":
    unittest.main()
