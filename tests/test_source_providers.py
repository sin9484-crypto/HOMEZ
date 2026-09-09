"""
=========================================================
Homez OS

File : tests/test_source_providers.py

SupplierDiscoveryProvider / SupplierOrderProvider 계약 테스트 —
Fake/Manual/Csv 전부 순수 로컬(네트워크 없음).
=========================================================
"""

import unittest

from app.domains.purchase.supplier_order_providers import (
    CsvSupplierOrderProvider,
)
from app.domains.purchase.supplier_order_providers import (
    FakeSupplierOrderProvider,
)
from app.domains.purchase.supplier_order_providers import (
    ManualSupplierOrderProvider,
)
from app.domains.purchase.supplier_order_providers import (
    SupplierOrderProviderError,
)
from app.domains.purchase.supplier_order_providers import SupplierOrderRequest
from app.domains.purchase.supplier_order_providers import (
    get_supplier_order_provider,
)
from app.domains.source.discovery_providers import (
    CsvSupplierDiscoveryProvider,
)
from app.domains.source.discovery_providers import (
    FakeSupplierDiscoveryProvider,
)
from app.domains.source.discovery_providers import (
    ManualSupplierDiscoveryProvider,
)
from app.domains.source.discovery_providers import (
    SupplierDiscoveryProviderError,
)
from app.domains.source.discovery_providers import SupplierDiscoveryQuery
from app.domains.source.discovery_providers import get_discovery_provider


class SupplierDiscoveryProviderTestCase(unittest.TestCase):

    def test_fake_provider_returns_unverified_result(self):

        provider = FakeSupplierDiscoveryProvider()
        results = provider.search_suppliers(
            SupplierDiscoveryQuery(product_name="샤프란"),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].verification_status, "UNVERIFIED")

    def test_manual_provider_always_empty(self):

        provider = ManualSupplierDiscoveryProvider()
        results = provider.search_suppliers(
            SupplierDiscoveryQuery(product_name="샤프란"),
        )

        self.assertEqual(results, [])

    def test_csv_provider_filters_by_product_name(self):

        provider = CsvSupplierDiscoveryProvider(rows=[
            {
                "supplier_id": 1, "supplier_product_id": "P1",
                "product_name": "샤프란 핑크센세이션", "unit_cost": 3000.0,
                "moq": 4,
            },
            {
                "supplier_id": 2, "supplier_product_id": "P2",
                "product_name": "완전히 다른 상품", "unit_cost": 1000.0,
                "moq": 1,
            },
        ])

        results = provider.search_suppliers(
            SupplierDiscoveryQuery(product_name="샤프란"),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].supplier_id, 1)

    def test_unknown_provider_pending(self):

        with self.assertRaises(SupplierDiscoveryProviderError):
            get_discovery_provider("REAL_B2B_API")


class SupplierOrderProviderTestCase(unittest.TestCase):

    def test_fake_provider_accepts_and_is_idempotent(self):

        provider = FakeSupplierOrderProvider()
        request = SupplierOrderRequest(
            purchase_id=1, supplier_id=1, idempotency_key="po-1",
            items=(("SKU-1", 2, 3000.0),),
        )

        first = provider.create_order(request)
        second = provider.create_order(request)

        self.assertEqual(first.status, "ACCEPTED")
        self.assertEqual(first.supplier_order_id, second.supplier_order_id)
        self.assertEqual(first.confirmed_price, 6000.0)

    def test_fake_provider_get_and_cancel(self):

        provider = FakeSupplierOrderProvider()
        request = SupplierOrderRequest(
            purchase_id=2, supplier_id=1, idempotency_key="po-2",
            items=(("SKU-2", 1, 1000.0),),
        )
        created = provider.create_order(request)

        fetched = provider.get_order(created.supplier_order_id)
        self.assertEqual(fetched.status, "ACCEPTED")

        cancelled = provider.cancel_order(created.supplier_order_id)
        self.assertEqual(cancelled.status, "REJECTED")
        self.assertEqual(provider.get_status(created.supplier_order_id), "REJECTED")

    def test_manual_provider_raises(self):

        provider = ManualSupplierOrderProvider()
        request = SupplierOrderRequest(
            purchase_id=3, supplier_id=1, idempotency_key="po-3",
            items=(("SKU-3", 1, 500.0),),
        )

        with self.assertRaises(SupplierOrderProviderError):
            provider.create_order(request)

    def test_csv_provider_exports_rows(self):

        provider = CsvSupplierOrderProvider()
        request = SupplierOrderRequest(
            purchase_id=4, supplier_id=9, idempotency_key="po-4",
            items=(("SKU-4", 3, 2000.0),),
        )
        result = provider.create_order(request)

        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(len(provider.exported_rows), 1)
        self.assertEqual(provider.exported_rows[0]["supplier_sku"], "SKU-4")

    def test_unknown_provider_pending(self):

        with self.assertRaises(SupplierOrderProviderError):
            get_supplier_order_provider("REAL_EDI")

    def test_onchannel_provider_registered(self):

        provider = get_supplier_order_provider("ONCHANNEL")
        self.assertEqual(provider.code, "ONCHANNEL")


class OnchannelSupplierOrderProviderTestCase(unittest.TestCase):
    """
    2026-09-08 V7 통합 매입 순서 6번 — 온채널 Provider는 실제 API
    기술 문서가 없어 모든 메서드가 의도적으로 미구현(fail-closed)
    상태다. 이 테스트는 (1) 어떤 메서드도 실제 네트워크를 호출하지
    않고, (2) 자격증명 저장 여부를 정확히 보고하되, (3) 자격증명
    원문 값 자체는 예외 메시지 어디에도 포함되지 않는지 확인한다.
    실제 Windows Credential Manager는 절대 건드리지 않는다
    (InMemoryCredentialStore만 사용).
    """

    def setUp(self):

        from app.core.windows_credential_store import InMemoryCredentialStore
        from app.domains.purchase.supplier_order_providers import (
            OnchannelSupplierOrderProvider,
        )

        self.provider_cls = OnchannelSupplierOrderProvider
        self.empty_store = InMemoryCredentialStore()
        self.populated_store = InMemoryCredentialStore()
        self.populated_store.save(
            OnchannelSupplierOrderProvider.CREDENTIAL_REFERENCE,
            {"auth_key": "super-secret-value-should-never-leak", "allowed_ip": "1.2.3.4"},
        )

    def test_all_methods_raise_without_network_and_report_missing_credential(self):

        provider = self.provider_cls(credential_store=self.empty_store)
        request = SupplierOrderRequest(
            purchase_id=1, supplier_id=1, idempotency_key="oc-1",
            items=(("SKU-OC-1", 1, 1000.0),),
        )

        for call in (
            lambda: provider.create_order(request),
            lambda: provider.get_order("SO-1"),
            lambda: provider.cancel_order("SO-1"),
            lambda: provider.get_status("SO-1"),
            lambda: provider.get_tracking("SO-1"),
        ):
            with self.assertRaises(SupplierOrderProviderError) as ctx:
                call()
            self.assertIn("자격증명 없음", str(ctx.exception))

    def test_reports_credential_saved_without_leaking_value(self):

        provider = self.provider_cls(credential_store=self.populated_store)
        request = SupplierOrderRequest(
            purchase_id=1, supplier_id=1, idempotency_key="oc-2",
            items=(("SKU-OC-1", 1, 1000.0),),
        )

        with self.assertRaises(SupplierOrderProviderError) as ctx:
            provider.create_order(request)

        message = str(ctx.exception)
        self.assertIn("자격증명 저장됨", message)
        self.assertNotIn("super-secret-value-should-never-leak", message)


if __name__ == "__main__":
    unittest.main()
