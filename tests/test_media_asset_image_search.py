"""
=========================================================
Homez OS

File : tests/test_media_asset_image_search.py

ImageSearchProvider(app/domains/media_asset/image_search_providers.py)
+ 라우터의 selectable 계산 로직(permission_status가 VERIFIED_ALLOWED
일 때만 selectable=true) 검증.
=========================================================
"""

import unittest

from app.domains.media_asset.image_search_providers import (
    DisabledImageSearchProvider,
)
from app.domains.media_asset.image_search_providers import (
    FakeImageSearchProvider,
)
from app.domains.media_asset.image_search_providers import (
    ImagePermissionStatus,
)
from app.domains.media_asset.image_search_providers import (
    ImageSearchProviderError,
)
from app.domains.media_asset.image_search_providers import ImageSearchQuery
from app.domains.media_asset.image_search_providers import (
    get_image_search_provider,
)


class ImageSearchProviderTestCase(unittest.TestCase):

    def test_fake_provider_returns_unknown_permission(self):

        provider = FakeImageSearchProvider()
        results = provider.search(ImageSearchQuery(product_name="샤프란"))

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].permission_status, ImagePermissionStatus.UNKNOWN,
        )

    def test_fake_results_are_not_selectable(self):

        provider = FakeImageSearchProvider()
        results = provider.search(ImageSearchQuery(product_name="샤프란"))

        selectable = results[0].permission_status in ImagePermissionStatus.SELECTABLE
        self.assertFalse(selectable)

    def test_verified_allowed_is_selectable(self):

        self.assertIn(
            ImagePermissionStatus.VERIFIED_ALLOWED,
            ImagePermissionStatus.SELECTABLE,
        )
        for status in (
            ImagePermissionStatus.UNKNOWN,
            ImagePermissionStatus.SUPPLIER_PERMISSION_REQUIRED,
            ImagePermissionStatus.MANUFACTURER_PERMISSION_REQUIRED,
            ImagePermissionStatus.PROHIBITED,
        ):
            self.assertNotIn(status, ImagePermissionStatus.SELECTABLE)

    def test_disabled_provider_raises(self):

        provider = DisabledImageSearchProvider()

        with self.assertRaises(ImageSearchProviderError):
            provider.search(ImageSearchQuery(product_name="샤프란"))

    def test_get_provider_fake_and_unknown(self):

        provider = get_image_search_provider("FAKE")
        self.assertIsInstance(provider, FakeImageSearchProvider)

        with self.assertRaises(ImageSearchProviderError):
            get_image_search_provider("REAL_INTERNET_SEARCH")


if __name__ == "__main__":
    unittest.main()
