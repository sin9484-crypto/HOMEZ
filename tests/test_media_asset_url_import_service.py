"""
=========================================================
Homez OS

File : tests/test_media_asset_url_import_service.py

Section 1(2026-08-28) — URL/상품페이지 이미지를 실제 MediaAsset으로
저장하는 서비스 계층(url_import_service.py) 검증. FakeTransport +
fixture 바이트만 쓴다(실제 네트워크 없음). 임시 SQLite DB +
tempfile 미디어 루트만 사용하고 실제 운영/개발 DB는 건드리지 않는다.
=========================================================
"""

import io
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset.constants import (
    MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE,
)
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.product_page_extraction import ImageCandidate
from app.domains.media_asset.url_import import FetchedResponse
from app.domains.media_asset.url_import import UrlImportError
from app.domains.media_asset.url_import_service import UrlImageImportService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.user.model import User  # noqa: F401
from tests.test_media_asset_product_page_extraction import FIXTURE_HTML
from tests.test_media_asset_url_import import FakeTransport
from tests.test_media_asset_url_import import _real_jpeg_bytes


class UrlImageImportServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProductCandidate.__table__,
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

        self.company = self._seed_company()
        self.candidate = self._seed_candidate()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_company(self):

        company = Company(
            name="URL 가져오기 테스트 회사", business_number="555-55-66666",
            ceo="테스트", phone="02-000-0000",
            email="urlimport@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        return company

    def _seed_candidate(self):

        candidate = ProductCandidate(
            candidate_key="url-import-test:1", source_type="MANUAL",
            source_reference="url-import-test", market="COUPANG",
            product_name="URL 가져오기 테스트 상품", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()
        return candidate

    def _service(self, transport: FakeTransport) -> UrlImageImportService:

        return UrlImageImportService(self.db, transport, media_root=self.media_root)

    # ------------------------------------------------
    # 단건 가져오기
    # ------------------------------------------------

    def test_imports_and_persists_source_metadata(self):

        body = _real_jpeg_bytes(120, 90)
        transport = FakeTransport({
            "http://8.8.8.8/photo.jpg": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "image/jpeg"},
                body=body,
            ),
        })
        service = self._service(transport)

        asset = service.import_image_from_url(
            owner_type="PRODUCT_CANDIDATE",
            owner_id=self.candidate.id,
            purpose="MAIN",
            display_order=0,
            source_url="http://8.8.8.8/photo.jpg",
            source_classification="SUPPLIER",
            company_id=self.company.id,
        )

        self.assertIsNotNone(asset.id)
        self.assertEqual(asset.source_url, "http://8.8.8.8/photo.jpg")
        self.assertEqual(asset.source_domain, "8.8.8.8")
        self.assertEqual(asset.source_classification, "SUPPLIER")
        self.assertEqual(asset.mime_type, "image/jpeg")
        self.assertEqual((asset.width, asset.height), (120, 90))
        # 2026-08-28 정책 반전 — URL 가져오기도 자동으로 VERIFIED가
        # 되지 않는다(로컬 업로드와 동일).
        self.assertEqual(asset.rights_status, "RIGHTS_UNVERIFIED")

        stored_path = self.media_root / asset.storage_path
        self.assertTrue(stored_path.exists())

    def test_duplicate_content_dedups_to_existing_asset(self):

        body = _real_jpeg_bytes(80, 80)
        transport = FakeTransport({
            "http://8.8.8.8/a.jpg": FetchedResponse(
                status_code=200, headers={"Content-Type": "image/jpeg"}, body=body,
            ),
            "http://8.8.4.4/b.jpg": FetchedResponse(
                status_code=200, headers={"Content-Type": "image/jpeg"}, body=body,
            ),
        })
        service = self._service(transport)

        first = service.import_image_from_url(
            owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
            purpose="MAIN", display_order=0,
            source_url="http://8.8.8.8/a.jpg", source_classification="UNKNOWN",
            company_id=self.company.id,
        )
        second = service.import_image_from_url(
            owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
            purpose="MAIN", display_order=1,
            source_url="http://8.8.4.4/b.jpg", source_classification="UNKNOWN",
            company_id=self.company.id,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(
            self.db.query(MediaAsset).count(), 1,
            "동일 내용(sha256 동일)은 새 행을 만들지 않아야 한다.",
        )

    def test_unknown_owner_type_rejected(self):

        transport = FakeTransport({})
        service = self._service(transport)

        with self.assertRaises(BadRequestException):
            service.import_image_from_url(
                owner_type="NOT_A_REAL_TYPE", owner_id=1,
                purpose="MAIN", display_order=0,
                source_url="http://8.8.8.8/a.jpg", source_classification="UNKNOWN",
                company_id=self.company.id,
            )
        self.assertEqual(transport.calls, [])

    def test_unknown_source_classification_rejected(self):

        transport = FakeTransport({})
        service = self._service(transport)

        with self.assertRaises(BadRequestException):
            service.import_image_from_url(
                owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
                purpose="MAIN", display_order=0,
                source_url="http://8.8.8.8/a.jpg",
                source_classification="NOT_A_REAL_CLASSIFICATION",
                company_id=self.company.id,
            )
        self.assertEqual(transport.calls, [])

    def test_active_image_count_limit_enforced(self):

        transport_responses = {}
        for i in range(MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE + 1):
            url = f"http://8.8.8.8/img{i}.jpg"
            transport_responses[url] = FetchedResponse(
                status_code=200, headers={"Content-Type": "image/jpeg"},
                body=_real_jpeg_bytes(20 + i, 20 + i),
            )
        transport = FakeTransport(transport_responses)
        service = self._service(transport)

        for i in range(MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE):
            service.import_image_from_url(
                owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
                purpose="MAIN", display_order=i,
                source_url=f"http://8.8.8.8/img{i}.jpg",
                source_classification="UNKNOWN", company_id=self.company.id,
            )

        with self.assertRaises(BadRequestException):
            service.import_image_from_url(
                owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
                purpose="MAIN", display_order=MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE,
                source_url=(
                    f"http://8.8.8.8/img"
                    f"{MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE}.jpg"
                ),
                source_classification="UNKNOWN", company_id=self.company.id,
            )

    def test_ssrf_blocked_url_raises_and_persists_nothing(self):

        transport = FakeTransport({})
        service = self._service(transport)

        with self.assertRaises(UrlImportError):
            service.import_image_from_url(
                owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
                purpose="MAIN", display_order=0,
                source_url="http://192.168.1.1/a.jpg",
                source_classification="UNKNOWN", company_id=self.company.id,
            )
        self.assertEqual(self.db.query(MediaAsset).count(), 0)

    def test_html_disguised_as_image_raises_and_persists_nothing(self):

        transport = FakeTransport({
            "http://8.8.8.8/fake.jpg": FetchedResponse(
                status_code=200, headers={"Content-Type": "image/jpeg"},
                body=b"<html><body>Access Denied</body></html>",
            ),
        })
        service = self._service(transport)

        with self.assertRaises(UrlImportError):
            service.import_image_from_url(
                owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
                purpose="MAIN", display_order=0,
                source_url="http://8.8.8.8/fake.jpg",
                source_classification="UNKNOWN", company_id=self.company.id,
            )
        self.assertEqual(self.db.query(MediaAsset).count(), 0)

    def test_dimension_policy_still_applies_to_url_imports(self):
        """url_import.py는 픽셀폭탄만 방어하고 로컬 업로드와 동일한
        MIN/MAX 해상도 정책까지는 보지 않는다 — 저장 직전
        validate_image_bytes()가 그 정책을 다시 적용해야 한다(둘 다
        동일 정책을 쓴다는 요구사항)."""

        buf = io.BytesIO()
        Image.new("RGB", (1, 1)).save(buf, format="JPEG")
        transport = FakeTransport({
            "http://8.8.8.8/tiny.jpg": FetchedResponse(
                status_code=200, headers={"Content-Type": "image/jpeg"},
                body=buf.getvalue(),
            ),
        })
        service = self._service(transport)

        with self.assertRaises(BadRequestException):
            service.import_image_from_url(
                owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
                purpose="MAIN", display_order=0,
                source_url="http://8.8.8.8/tiny.jpg",
                source_classification="UNKNOWN", company_id=self.company.id,
            )
        self.assertEqual(self.db.query(MediaAsset).count(), 0)

    # ------------------------------------------------
    # 배치 가져오기 — 부분 실패 허용
    # ------------------------------------------------

    def test_batch_partial_failure_does_not_block_other_items(self):

        good_body = _real_jpeg_bytes(50, 50)
        transport = FakeTransport({
            "http://8.8.8.8/good1.jpg": FetchedResponse(
                status_code=200, headers={"Content-Type": "image/jpeg"}, body=good_body,
            ),
            "http://8.8.8.8/missing.jpg": FetchedResponse(
                status_code=404, headers={}, body=b"",
            ),
            "http://8.8.4.4/good2.jpg": FetchedResponse(
                status_code=200, headers={"Content-Type": "image/jpeg"},
                body=_real_jpeg_bytes(60, 60),
            ),
        })
        service = self._service(transport)

        results = service.import_images_from_urls_batch(
            owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
            purpose="MAIN",
            source_urls=[
                "http://8.8.8.8/good1.jpg",
                "http://8.8.8.8/missing.jpg",
                "http://8.8.4.4/good2.jpg",
            ],
            source_classification="UNKNOWN", company_id=self.company.id,
        )

        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].success)
        self.assertIsNotNone(results[0].asset)
        self.assertFalse(results[1].success)
        self.assertIsNone(results[1].asset)
        self.assertIsNotNone(results[1].error_message)
        self.assertTrue(results[2].success)

        self.assertEqual(self.db.query(MediaAsset).count(), 2)

    def test_batch_private_host_blocked_without_stopping_batch(self):

        transport = FakeTransport({
            "http://8.8.8.8/good.jpg": FetchedResponse(
                status_code=200, headers={"Content-Type": "image/jpeg"},
                body=_real_jpeg_bytes(40, 40),
            ),
        })
        service = self._service(transport)

        results = service.import_images_from_urls_batch(
            owner_type="PRODUCT_CANDIDATE", owner_id=self.candidate.id,
            purpose="MAIN",
            source_urls=["http://169.254.169.254/secret", "http://8.8.8.8/good.jpg"],
            source_classification="UNKNOWN", company_id=self.company.id,
        )

        self.assertFalse(results[0].success)
        self.assertTrue(results[1].success)

    # ------------------------------------------------
    # 상품 페이지 후보 — 저장 없음
    # ------------------------------------------------

    def test_list_product_page_candidates_persists_nothing(self):

        transport = FakeTransport({
            "https://shop.example.com/products/1": FetchedResponse(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=FIXTURE_HTML.encode("utf-8"),
            ),
        })
        service = self._service(transport)

        from unittest.mock import patch

        def _fake_dns(*_args, **_kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        with patch("socket.getaddrinfo", side_effect=_fake_dns):
            candidates = service.list_product_page_candidates(
                "https://shop.example.com/products/1",
            )

        self.assertGreater(len(candidates), 0)
        self.assertTrue(all(isinstance(c, ImageCandidate) for c in candidates))
        self.assertEqual(self.db.query(MediaAsset).count(), 0)


if __name__ == "__main__":
    unittest.main()
