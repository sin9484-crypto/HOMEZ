"""
=========================================================
Homez OS

File : tests/test_coupang_image_autofill.py

app/domains/marketplace_listing/coupang_image_autofill.py 단위
테스트 — 임시 SQLite DB(app/database/base.py 기반)와 InMemory
CredentialStore만 사용한다. 실제 R2 네트워크 호출은 없다 —
coupang_image_autofill 모듈 안에서 참조하는
R2MediaHostingService 자체를 Mock으로 대체한다.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.marketplace_listing.coupang_image_autofill import (
    autofill_coupang_images,
)
from app.domains.media_asset.model import MediaAsset

_PATCH_TARGET = (
    "app.domains.marketplace_listing.coupang_image_autofill."
    "R2MediaHostingService"
)


def _add_asset(db, **overrides):

    defaults = dict(
        company_id=1, owner_type="PRODUCT_CANDIDATE", owner_id=1,
        asset_role="ORIGINAL", purpose="MAIN", display_order=0,
        mime_type="image/jpeg", file_size_bytes=10, status="ACTIVE",
        rights_status="VERIFIED",
    )
    defaults.update(overrides)
    asset = MediaAsset(**defaults)
    db.add(asset)
    return asset


class CoupangImageAutofillTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(
            self.engine, tables=[MediaAsset.__table__],
        )
        Session = sessionmaker(bind=self.engine)
        self.db = Session()

        self.store = InMemoryCredentialStore()

        for i in (1, 2, 3):
            _add_asset(
                self.db, id=i, storage_path=f"1/{i}.jpg",
                sha256_hex=f"{'a' * 63}{i}",
            )
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            os.remove(self.db_path)

    def test_empty_selection_returns_empty_list(self):

        result = autofill_coupang_images(self.db, 1, [], self.store)
        self.assertEqual(result, [])

    def test_first_asset_is_representation_rest_are_detail(self):

        with patch(_PATCH_TARGET) as service_cls:
            fake_service = service_cls.return_value
            fake_service.ensure_public_url.side_effect = [
                "https://pub-x.r2.dev/1.jpg",
                "https://pub-x.r2.dev/2.jpg",
                "https://pub-x.r2.dev/3.jpg",
            ]

            result = autofill_coupang_images(
                self.db, 1, [1, 2, 3], self.store,
            )

        self.assertEqual(len(result), 3)
        self.assertEqual(
            result[0],
            {
                "imageOrder": 0, "imageType": "REPRESENTATION",
                "vendorPath": "https://pub-x.r2.dev/1.jpg",
            },
        )
        self.assertEqual(result[1]["imageType"], "DETAIL")
        self.assertEqual(result[1]["imageOrder"], 1)
        self.assertEqual(result[2]["imageType"], "DETAIL")
        self.assertEqual(result[2]["imageOrder"], 2)

    def test_nonexistent_media_asset_id_is_rejected(self):

        with patch(_PATCH_TARGET):
            with self.assertRaises(BadRequestException):
                autofill_coupang_images(self.db, 1, [9999], self.store)

    def test_media_asset_from_other_company_is_rejected(self):
        """company_id 필터를 우회해 다른 회사 자산을 쓸 수 없어야 한다
        — tenant 격리."""

        _add_asset(
            self.db, id=100, company_id=2, storage_path="2/other.jpg",
            sha256_hex="b" * 64,
        )
        self.db.commit()

        with patch(_PATCH_TARGET):
            with self.assertRaises(BadRequestException):
                autofill_coupang_images(self.db, 1, [100], self.store)

    def test_selection_beyond_ten_is_truncated_before_lookup(self):
        """11개 이상 선택해도 처음 10개만 조회 대상이 된다 — 11번째
        이후 존재하지 않는 id가 섞여 있어도 잘려나가 영향을 주지
        않는다."""

        ids = [1, 2, 3] + list(range(9000, 9020))  # 3 + 20개 = 23개
        with patch(_PATCH_TARGET) as service_cls:
            fake_service = service_cls.return_value
            fake_service.ensure_public_url.return_value = (
                "https://pub-x.r2.dev/x.jpg"
            )
            with self.assertRaises(BadRequestException) as ctx:
                autofill_coupang_images(self.db, 1, ids, self.store)

        # 앞 10개(ids[:10])만 조회 대상이므로, missing 목록도 그 10개
        # 안에서만 나와야 한다(1,2,3은 존재 → missing은 9000~9006, 7개).
        message = str(ctx.exception)
        self.assertIn("9000", message)
        self.assertNotIn("9019", message)

    def test_uses_provided_credential_store(self):

        with patch(_PATCH_TARGET) as service_cls:
            service_cls.return_value.ensure_public_url.return_value = (
                "https://pub-x.r2.dev/1.jpg"
            )
            autofill_coupang_images(self.db, 1, [1], self.store)

        service_cls.assert_called_once_with(self.db, self.store)


if __name__ == "__main__":
    unittest.main()
