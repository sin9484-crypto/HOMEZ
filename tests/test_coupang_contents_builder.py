"""
=========================================================
Homez OS

File : tests/test_coupang_contents_builder.py

app/domains/marketplace_listing/coupang_contents_builder.py 단위
테스트 — tests/test_coupang_image_autofill.py와 동일한 패턴(임시
SQLite DB, InMemoryCredentialStore, R2MediaHostingService는 Mock).
실제 R2 네트워크 호출은 없다.

2026-08-31 Phase 7.6 감사 — company_id 격리뿐 아니라 (1) 이 wizard의
상품 후보(product_candidate_id) 소유 여부, (2) purpose == DETAIL
여부, (3) status == ACTIVE 여부, (4) 중복 id 여부까지 막는지 전부
검증한다.
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
from app.domains.marketplace_listing.coupang_contents_builder import (
    build_contents_from_media_assets,
)
from app.domains.marketplace_listing.coupang_submission_contract import (
    validate_coupang_submission_contract,
)
from app.domains.media_asset.model import MediaAsset

_CONTENTS_RELATED_CODES = frozenset({
    "CONTENTS_REQUIRED", "INVALID_CONTENTS_ENTRY", "INVALID_CONTENTS_TYPE",
    "CONTENT_DETAILS_REQUIRED", "INVALID_CONTENT_DETAIL_ENTRY",
    "INVALID_CONTENT_DETAIL_TYPE", "CONTENT_TEXT_REQUIRED",
    "CONTENT_IMAGE_URL_REQUIRED", "CONTENT_IMAGE_URL_MUST_BE_PUBLIC",
})

_PATCH_TARGET = (
    "app.domains.marketplace_listing.coupang_contents_builder."
    "R2MediaHostingService"
)

# 이 테스트 파일의 기본 wizard가 속한 상품 후보 id — 대부분의 자산은
# 이 값을 owner_id로 갖는다(정상 케이스). "다른 상품 후보" 케이스는
# 이 값과 다른 owner_id를 명시적으로 준다.
_CANDIDATE_ID = 1


def _add_asset(db, **overrides):

    defaults = dict(
        company_id=1, owner_type="PRODUCT_CANDIDATE", owner_id=_CANDIDATE_ID,
        asset_role="GENERATED", purpose="DETAIL", display_order=0,
        mime_type="image/jpeg", file_size_bytes=10, status="ACTIVE",
        rights_status="VERIFIED",
    )
    defaults.update(overrides)
    asset = MediaAsset(**defaults)
    db.add(asset)
    return asset


class CoupangContentsBuilderTestCase(unittest.TestCase):

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

        for i in (1, 2):
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

    def _build(self, media_asset_ids, company_id=1, candidate_id=_CANDIDATE_ID):

        return build_contents_from_media_assets(
            self.db, company_id, candidate_id, media_asset_ids, self.store,
        )

    def test_empty_selection_returns_empty_list(self):

        result = self._build([])
        self.assertEqual(result, [])

    def test_single_detail_image_builds_one_content_entry(self):

        with patch(_PATCH_TARGET) as service_cls:
            service_cls.return_value.ensure_public_url.return_value = (
                "https://pub-x.r2.dev/1.jpg"
            )
            result = self._build([1])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["contentsType"], "IMAGE")
        self.assertEqual(len(result[0]["contentDetails"]), 1)
        self.assertEqual(result[0]["contentDetails"][0]["detailType"], "IMAGE")
        self.assertEqual(
            result[0]["contentDetails"][0]["content"],
            '<img src="https://pub-x.r2.dev/1.jpg">',
        )

    def test_multiple_images_build_one_entry_each_in_order(self):

        with patch(_PATCH_TARGET) as service_cls:
            service_cls.return_value.ensure_public_url.side_effect = [
                "https://pub-x.r2.dev/1.jpg",
                "https://pub-x.r2.dev/2.jpg",
            ]
            result = self._build([1, 2])

        self.assertEqual(len(result), 2)
        self.assertIn("1.jpg", result[0]["contentDetails"][0]["content"])
        self.assertIn("2.jpg", result[1]["contentDetails"][0]["content"])

    def test_selection_order_is_preserved_even_when_reversed(self):
        """저장 순서가 조회 결과 순서가 아니라 사용자가 실제로 고른
        순서를 따라야 한다."""

        with patch(_PATCH_TARGET) as service_cls:
            service_cls.return_value.ensure_public_url.side_effect = [
                "https://pub-x.r2.dev/2.jpg",
                "https://pub-x.r2.dev/1.jpg",
            ]
            result = self._build([2, 1])

        self.assertIn("2.jpg", result[0]["contentDetails"][0]["content"])
        self.assertIn("1.jpg", result[1]["contentDetails"][0]["content"])

    def test_nonexistent_media_asset_id_is_rejected(self):

        with patch(_PATCH_TARGET):
            with self.assertRaises(BadRequestException):
                self._build([9999])

    def test_media_asset_from_other_company_is_rejected(self):
        """company_id 필터를 우회해 다른 회사 자산을 쓸 수 없어야 한다
        — tenant 격리(image_autofill과 동일한 정책)."""

        _add_asset(
            self.db, id=100, company_id=2, storage_path="2/other.jpg",
            sha256_hex="b" * 64,
        )
        self.db.commit()

        with patch(_PATCH_TARGET):
            with self.assertRaises(BadRequestException):
                self._build([100])

    def test_media_asset_from_different_product_candidate_is_rejected(self):
        """같은 회사 소유라도 이 wizard가 가리키는 상품 후보의
        이미지가 아니면 막아야 한다 — 회사 안에서도 상품이 섞이지
        않게 한다(2026-08-31 Phase 7.6 감사에서 발견)."""

        _add_asset(
            self.db, id=200, owner_id=_CANDIDATE_ID + 1,
            storage_path="2/other-product.jpg", sha256_hex="c" * 64,
        )
        self.db.commit()

        with patch(_PATCH_TARGET):
            with self.assertRaisesRegex(BadRequestException, "상품 후보"):
                self._build([200])

    def test_non_detail_purpose_asset_is_rejected(self):
        """대표(MAIN)·썸네일 이미지는 상세설명에 쓸 수 없다(2026-08-31
        Phase 7.6 감사에서 발견 — 최초 버전은 purpose를 전혀
        확인하지 않았다)."""

        _add_asset(
            self.db, id=300, purpose="MAIN",
            storage_path="1/main.jpg", sha256_hex="d" * 64,
        )
        self.db.commit()

        with patch(_PATCH_TARGET):
            with self.assertRaisesRegex(BadRequestException, "DETAIL"):
                self._build([300])

    def test_orphaned_asset_is_rejected(self):
        """ORPHANED로 표시된 자산은 실제로 남아있는 파일이 아닐 수
        있다 — 상세설명에 쓸 수 없다(2026-08-31 Phase 7.6 감사에서
        발견 — 최초 버전은 status를 전혀 확인하지 않았다)."""

        _add_asset(
            self.db, id=400, status="ORPHANED",
            storage_path="1/orphan.jpg", sha256_hex="e" * 64,
        )
        self.db.commit()

        with patch(_PATCH_TARGET):
            with self.assertRaises(BadRequestException):
                self._build([400])

    def test_deleted_asset_is_rejected(self):

        _add_asset(
            self.db, id=401, status="DELETED",
            storage_path="1/deleted.jpg", sha256_hex="f" * 64,
        )
        self.db.commit()

        with patch(_PATCH_TARGET):
            with self.assertRaises(BadRequestException):
                self._build([401])

    def test_duplicate_ids_in_same_request_are_rejected(self):
        """같은 id를 두 번 보내면 조용히 한 번만 반영하지 않고
        요청 자체를 막는다 — 사용자가 실제로 몇 개를 골랐는지와
        결과 개수가 항상 일치해야 한다."""

        with patch(_PATCH_TARGET):
            with self.assertRaises(BadRequestException):
                self._build([1, 1, 2])

    def test_no_arbitrary_html_beyond_img_tag(self):
        """계약이 요구하는 정확히 `<img src="...">` 형태만 만든다 —
        임의 HTML을 붙이지 않는다."""

        with patch(_PATCH_TARGET) as service_cls:
            service_cls.return_value.ensure_public_url.return_value = (
                "https://pub-x.r2.dev/1.jpg"
            )
            result = self._build([1])

        content = result[0]["contentDetails"][0]["content"]
        self.assertEqual(content, '<img src="https://pub-x.r2.dev/1.jpg">')

    def test_built_contents_satisfies_real_submission_contract(self):
        """build_contents_from_media_assets()가 만든 결과를 실제
        coupang_submission_contract.py 검증기에 그대로 넣었을 때
        contents 관련 차단 사유가 하나도 없어야 한다 — "선택한 상세
        이미지가 쿠팡 items[].contents 계약에 맞는 구조로 저장되는지
        확인한다"는 요구사항을 직접 검증한다(다른 필드 — 브랜드·
        items 등 — 는 이 테스트의 관심사가 아니므로 함께 비어 있어도
        무방하다)."""

        with patch(_PATCH_TARGET) as service_cls:
            service_cls.return_value.ensure_public_url.return_value = (
                "https://pub-x.r2.dev/1.jpg"
            )
            contents = self._build([1])

        result = validate_coupang_submission_contract(
            draft={"product_name": "테스트 상품"},
            required_fields={"contents": contents},
            channel_policy_attributes={},
        )

        contents_issue_codes = {
            issue.code for issue in result.issues
            if issue.code in _CONTENTS_RELATED_CODES
        }
        self.assertEqual(contents_issue_codes, set())

    def test_uses_provided_credential_store(self):

        with patch(_PATCH_TARGET) as service_cls:
            service_cls.return_value.ensure_public_url.return_value = (
                "https://pub-x.r2.dev/1.jpg"
            )
            self._build([1])

        service_cls.assert_called_once_with(self.db, self.store)


if __name__ == "__main__":
    unittest.main()
