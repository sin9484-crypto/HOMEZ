from __future__ import annotations

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.order.adapters.coupang_collection import ALLOWED_STATUSES
from app.domains.order.adapters.coupang_collection import CoupangOrderCollectionResult
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.collection_model import OrderSkuResolution
from app.domains.order.model import Order
from app.domains.order.multi_channel_collection_service import (
    OrderMultiChannelCollectionService,
)
from app.domains.store_connection.model import StoreConnection


class _Provider:
    def __init__(self, result):
        self.result = result

    def collect(self, **_kwargs):
        return self.result


class OrderMultiChannelCollectionServiceTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(
            self.engine,
            tables=[
                StoreConnection.__table__, OrderChannelFulfillment.__table__,
                UnresolvedOrderItem.__table__, OrderCollectionCursor.__table__,
                OrderSkuResolution.__table__, Order.__table__,
            ],
        )
        self.db = sessionmaker(bind=self.engine)()
        self.store = InMemoryCredentialStore()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def _make_connection(self, *, company_id=1, cred_name, status="CONNECTED",
                          marketplace_code="COUPANG", idem="create-1"):
        self.store.save(cred_name, {
            "vendor_id": "A0001", "access_key": "access", "secret_key": "secret",
        })
        connection = StoreConnection(
            company_id=company_id, marketplace_code=marketplace_code,
            display_name=marketplace_code, seller_identifier=cred_name,
            credential_reference=cred_name, masked_credential_hint="***",
            connection_status=status, credential_version=1, created_by=1,
            creation_idempotency_key=idem,
            creation_request_fingerprint="a" * 64,
        )
        self.db.add(connection)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def _empty_success_factory(self):
        result = CoupangOrderCollectionResult(True, pages=(), http_status=200)
        return lambda _credentials: _Provider(result)

    def test_single_connection_runs_every_allowed_status(self):
        self._make_connection(cred_name="cred-1")
        service = OrderMultiChannelCollectionService(
            self.db, self.store, provider_factory=self._empty_success_factory(),
        )
        result = service.run_all(1)
        self.assertEqual(result.total_connections, 1)
        self.assertEqual(len(result.entries), len(ALLOWED_STATUSES))
        self.assertEqual(result.succeeded_runs, len(ALLOWED_STATUSES))
        self.assertEqual(result.failed_runs, 0)
        self.assertEqual(result.skipped_marketplace_codes, ())

    def test_statuses_param_narrows_to_accept_only(self):
        """2026-09-17 Phase 7A 사후 감사 — 신규 주문 자동 감지처럼
        특정 상태만 필요한 호출부가 `statuses={"ACCEPT"}`로 범위를
        좁힐 수 있는지 확인한다. 생략 시(기존 "전체 통합 수집" 수동
        버튼) 동작은 test_single_connection_runs_every_allowed_status
        가 이미 고정한다."""

        self._make_connection(cred_name="cred-1")
        calls = []

        def factory(_credentials):
            return _Provider(
                CoupangOrderCollectionResult(True, pages=(), http_status=200),
            )

        service = OrderMultiChannelCollectionService(
            self.db, self.store, provider_factory=factory,
        )
        result = service.run_all(1, statuses={"ACCEPT"})
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].channel_status, "ACCEPT")

    def test_unknown_status_in_statuses_param_is_rejected(self):
        self._make_connection(cred_name="cred-1")
        service = OrderMultiChannelCollectionService(
            self.db, self.store, provider_factory=self._empty_success_factory(),
        )
        with self.assertRaises(ValueError):
            service.run_all(1, statuses={"NOT_A_REAL_STATUS"})

    def test_multiple_connections_are_all_collected(self):
        self._make_connection(cred_name="cred-1", idem="create-1")
        self._make_connection(cred_name="cred-2", idem="create-2")
        service = OrderMultiChannelCollectionService(
            self.db, self.store, provider_factory=self._empty_success_factory(),
        )
        result = service.run_all(1)
        self.assertEqual(result.total_connections, 2)
        self.assertEqual(len(result.entries), 2 * len(ALLOWED_STATUSES))
        connection_ids = {e.store_connection_id for e in result.entries}
        self.assertEqual(len(connection_ids), 2)

    def test_disconnected_connection_is_excluded(self):
        self._make_connection(cred_name="cred-1", status="ERROR")
        service = OrderMultiChannelCollectionService(
            self.db, self.store, provider_factory=self._empty_success_factory(),
        )
        result = service.run_all(1)
        self.assertEqual(result.total_connections, 0)
        self.assertEqual(result.entries, ())

    def test_unregistered_marketplace_is_skipped_not_silently_ignored(self):
        self._make_connection(
            cred_name="cred-1", marketplace_code="NAVER_SMARTSTORE",
        )
        service = OrderMultiChannelCollectionService(
            self.db, self.store, provider_factory=self._empty_success_factory(),
        )
        result = service.run_all(1)
        self.assertEqual(result.total_connections, 1)
        self.assertEqual(result.entries, ())
        self.assertIn("NAVER_SMARTSTORE", result.skipped_marketplace_codes)

    def test_one_connection_failure_does_not_block_the_other(self):
        self._make_connection(cred_name="cred-good", idem="create-1")
        self._make_connection(cred_name="cred-bad", idem="create-2")

        good_provider = _Provider(
            CoupangOrderCollectionResult(True, pages=(), http_status=200),
        )
        bad_provider = _Provider(
            CoupangOrderCollectionResult(
                False, error_code="RATE_LIMITED", error_summary="safe",
            ),
        )

        def factory(credentials):
            return good_provider if credentials["vendor_id"] == "A0001" and \
                credentials.get("access_key") == "access-good" else bad_provider

        # Distinguish the two connections via their stored credential.
        self.store.save("cred-good", {
            "vendor_id": "A0001", "access_key": "access-good", "secret_key": "s",
        })
        self.store.save("cred-bad", {
            "vendor_id": "A0001", "access_key": "access-bad", "secret_key": "s",
        })

        service = OrderMultiChannelCollectionService(
            self.db, self.store, provider_factory=factory,
        )
        result = service.run_all(1)
        self.assertEqual(result.total_connections, 2)
        self.assertEqual(result.succeeded_runs, len(ALLOWED_STATUSES))
        self.assertEqual(result.failed_runs, len(ALLOWED_STATUSES))

    def test_company_isolation(self):
        self._make_connection(company_id=1, cred_name="cred-1")
        service = OrderMultiChannelCollectionService(
            self.db, self.store, provider_factory=self._empty_success_factory(),
        )
        result = service.run_all(2)
        self.assertEqual(result.total_connections, 0)


if __name__ == "__main__":
    unittest.main()
