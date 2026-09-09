"""
2026-08-29 Pre-Live 감사 Phase C — listing_wizard_router.py의 3개 쿠팡
Provider 팩토리 함수에 추가한 HOMEZ_TEST_FAKE_COUPANG_PROVIDER 환경변수
훅 계약. installer/homez.iss의 HOMEZ_TEST_FORCE_* 패턴과 동일한 원칙:
이 정확한 이름·값("1")이 아니면 절대 개입하지 않는다.
"""

import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ServiceUnavailableException
from app.database.base import Base
from app.domains.store_connection.model import StoreConnection  # noqa: F401 (Base.metadata 등록용)
from app.domains.marketplace_listing.coupang_test_fakes import (
    FakeCoupangCategoryMetadataProvider,
    FakeCoupangLiveProductProvider,
    FakeCoupangLogisticsProvider,
)
from app.domains.marketplace_listing.listing_wizard_router import (
    _coupang_live_product_provider,
    _coupang_logistics_provider,
    _coupang_metadata_provider,
)


class CoupangTestFakeProviderHookTestCase(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)
        self.addCleanup(os.environ.pop, "HOMEZ_TEST_FAKE_COUPANG_PROVIDER", None)

    def test_env_var_absent_uses_real_path_and_requires_store_connection(self):
        # StoreConnection이 없는 빈 DB이므로, 실제 경로를 탔다면 반드시
        # 이 예외로 fail-closed된다 — Fake가 조용히 대신 쓰이지 않았음을
        # 이 예외 발생 자체로 증명한다.
        os.environ.pop("HOMEZ_TEST_FAKE_COUPANG_PROVIDER", None)
        for factory in (
            _coupang_metadata_provider, _coupang_logistics_provider,
            _coupang_live_product_provider,
        ):
            with self.assertRaises(ServiceUnavailableException):
                factory(self.db, company_id=1)

    def test_env_var_not_exactly_one_uses_real_path(self):
        for bad_value in ("true", "yes", "0", ""):
            os.environ["HOMEZ_TEST_FAKE_COUPANG_PROVIDER"] = bad_value
            with self.assertRaises(ServiceUnavailableException):
                _coupang_metadata_provider(self.db, company_id=1)

    def test_env_var_exactly_one_returns_fake_metadata_provider(self):
        os.environ["HOMEZ_TEST_FAKE_COUPANG_PROVIDER"] = "1"
        provider = _coupang_metadata_provider(self.db, company_id=1)
        self.assertIsInstance(provider, FakeCoupangCategoryMetadataProvider)

    def test_env_var_exactly_one_returns_fake_logistics_provider(self):
        os.environ["HOMEZ_TEST_FAKE_COUPANG_PROVIDER"] = "1"
        provider = _coupang_logistics_provider(self.db, company_id=1)
        self.assertIsInstance(provider, FakeCoupangLogisticsProvider)

    def test_env_var_exactly_one_returns_fake_live_product_provider(self):
        os.environ["HOMEZ_TEST_FAKE_COUPANG_PROVIDER"] = "1"
        provider = _coupang_live_product_provider(self.db, company_id=1)
        self.assertIsInstance(provider, FakeCoupangLiveProductProvider)

    def test_fake_providers_never_touch_the_db(self):
        # Fake 경로는 StoreConnection/Credential을 전혀 조회하지 않는다
        # — db를 None으로 넘겨도 예외 없이 동작해야 이 사실이 증명된다.
        os.environ["HOMEZ_TEST_FAKE_COUPANG_PROVIDER"] = "1"
        _coupang_metadata_provider(None, company_id=1)
        _coupang_logistics_provider(None, company_id=1)
        _coupang_live_product_provider(None, company_id=1)


if __name__ == "__main__":
    unittest.main()
