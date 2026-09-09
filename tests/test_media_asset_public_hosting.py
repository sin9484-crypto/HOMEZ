"""
=========================================================
Homez OS

File : tests/test_media_asset_public_hosting.py

app/domains/media_asset/public_hosting.py 단위 테스트. 실제 R2
네트워크 호출은 하지 않는다 — boto3 클라이언트를 Mock으로 대체한다.
Credential은 InMemoryCredentialStore(테스트 전용 fixture)만 쓴다.
=========================================================
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.public_hosting import CREDENTIAL_TARGET_NAME
from app.domains.media_asset.public_hosting import MediaHostingNotConfiguredError
from app.domains.media_asset.public_hosting import PROVIDER_CODE
from app.domains.media_asset.public_hosting import R2Credentials
from app.domains.media_asset.public_hosting import R2MediaHostingService
from app.domains.media_asset.public_hosting import build_object_key
from app.domains.media_asset.public_hosting import load_r2_credentials
from app.domains.media_asset.public_hosting import save_r2_credentials
from app.core.exceptions import BadRequestException


def _make_asset(**overrides) -> MediaAsset:

    defaults = dict(
        id=1, company_id=1, owner_type="PRODUCT_CANDIDATE", owner_id=1,
        asset_role="ORIGINAL", purpose="MAIN", display_order=0,
        storage_path="1/seed.jpg", mime_type="image/jpeg",
        file_size_bytes=100, sha256_hex="a" * 64, status="ACTIVE",
        rights_status="VERIFIED",
    )
    defaults.update(overrides)
    return MediaAsset(**defaults)


class BuildObjectKeyTestCase(unittest.TestCase):

    def test_deterministic_for_same_input(self):

        key1 = build_object_key(1, "abc123", "jpg")
        key2 = build_object_key(1, "abc123", "jpg")
        self.assertEqual(key1, key2)

    def test_different_company_yields_different_key(self):

        self.assertNotEqual(
            build_object_key(1, "abc123", "jpg"),
            build_object_key(2, "abc123", "jpg"),
        )


class CredentialRoundTripTestCase(unittest.TestCase):

    def test_save_then_load_round_trips_all_fields(self):

        store = InMemoryCredentialStore()
        creds = R2Credentials(
            account_id="acct123", access_key_id="AKIA...",
            secret_access_key="secret...", bucket_name="homez-images",
            public_base_url="https://pub-xxxx.r2.dev/",
        )
        save_r2_credentials(store, creds)

        loaded = load_r2_credentials(store)

        self.assertEqual(loaded.account_id, "acct123")
        self.assertEqual(loaded.bucket_name, "homez-images")
        # 마지막 슬래시는 저장 시 정규화된다.
        self.assertEqual(loaded.public_base_url, "https://pub-xxxx.r2.dev")

    def test_load_without_save_raises_not_configured(self):

        store = InMemoryCredentialStore()
        with self.assertRaises(MediaHostingNotConfiguredError):
            load_r2_credentials(store)

    def test_secret_never_appears_in_not_configured_error_message(self):

        store = InMemoryCredentialStore()
        try:
            load_r2_credentials(store)
        except MediaHostingNotConfiguredError as exc:
            self.assertNotIn("secret", str(exc).lower())


class R2MediaHostingServiceTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp())
        (self.tmp_dir / "1").mkdir()
        (self.tmp_dir / "1" / "seed.jpg").write_bytes(b"fake-jpeg-bytes")

        self.store = InMemoryCredentialStore()
        save_r2_credentials(self.store, R2Credentials(
            account_id="acct123", access_key_id="AKIA...",
            secret_access_key="secret...", bucket_name="homez-images",
            public_base_url="https://pub-xxxx.r2.dev",
        ))

        self.db = MagicMock()
        self.service = R2MediaHostingService(
            self.db, self.store, media_root=self.tmp_dir,
        )

    def test_already_uploaded_asset_returns_cached_url_without_client(self):

        asset = _make_asset(public_url="https://pub-xxxx.r2.dev/already.jpg")

        with patch.object(self.service, "_build_client") as build_client:
            result = self.service.ensure_public_url(asset)

        build_client.assert_not_called()
        self.assertEqual(result, "https://pub-xxxx.r2.dev/already.jpg")

    def test_unverified_rights_asset_is_no_longer_blocked(self):
        """2026-08-28 사용자 결정 — 증빙 미제출만으로는 공개 업로드를
        차단하지 않는다(이전 하드 차단 대체). 경고·기록은 화면
        레이어와 ImageRightsAcknowledgementService가 담당한다."""

        asset = _make_asset(rights_status="RIGHTS_UNVERIFIED")
        fake_client = MagicMock()

        with patch.object(
            self.service, "_build_client", return_value=fake_client,
        ):
            result = self.service.ensure_public_url(asset)

        fake_client.put_object.assert_called_once()
        self.assertTrue(result.startswith("https://pub-xxxx.r2.dev/"))

    def test_rights_denied_asset_still_blocked(self):
        """2026-08-28 정책 반전 재감사 — RIGHTS_UNVERIFIED는 더 이상
        차단되지 않지만, RIGHTS_DENIED(명시적 사용금지)는 이번 반전과
        무관하게 계속 하드 차단돼야 한다."""

        asset = _make_asset(rights_status="RIGHTS_DENIED")

        with self.assertRaises(BadRequestException):
            self.service.ensure_public_url(asset)

    def test_unsupported_mime_type_is_blocked(self):

        asset = _make_asset(mime_type="image/gif")

        with self.assertRaises(BadRequestException):
            self.service.ensure_public_url(asset)

    def test_successful_upload_sets_url_provider_and_timestamp(self):

        asset = _make_asset()
        fake_client = MagicMock()

        with patch.object(
            self.service, "_build_client", return_value=fake_client,
        ):
            result = self.service.ensure_public_url(asset)

        fake_client.put_object.assert_called_once()
        call_kwargs = fake_client.put_object.call_args.kwargs
        self.assertEqual(call_kwargs["Bucket"], "homez-images")
        self.assertEqual(call_kwargs["Body"], b"fake-jpeg-bytes")
        self.assertEqual(call_kwargs["ContentType"], "image/jpeg")

        expected_key = build_object_key(1, "a" * 64, "jpg")
        self.assertEqual(call_kwargs["Key"], expected_key)
        self.assertEqual(
            result, f"https://pub-xxxx.r2.dev/{expected_key}",
        )
        self.assertEqual(asset.public_url, result)
        self.assertEqual(asset.public_url_provider, PROVIDER_CODE)
        self.assertIsInstance(asset.public_url_uploaded_at, datetime)
        self.db.flush.assert_called_once()

    def test_second_call_reuses_cached_url_and_does_not_reupload(self):

        asset = _make_asset()
        fake_client = MagicMock()

        with patch.object(
            self.service, "_build_client", return_value=fake_client,
        ):
            first = self.service.ensure_public_url(asset)
            second = self.service.ensure_public_url(asset)

        self.assertEqual(first, second)
        fake_client.put_object.assert_called_once()

    def test_credential_target_name_is_namespaced(self):

        self.assertTrue(CREDENTIAL_TARGET_NAME.startswith("HOMEZ:"))


if __name__ == "__main__":
    unittest.main()
