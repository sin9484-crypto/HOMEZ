from __future__ import annotations

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.order.adapters.coupang_collection import CoupangOrderCollectionResult
from app.domains.order.adapters.coupang_collection import CoupangOrderPage
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.collection_model import OrderSkuResolution
from app.domains.order.coupang_collection_service import (
    CoupangOrderCollectionService,
)
from app.domains.store_connection.model import StoreConnection
from app.domains.order.model import Order
from tests.test_coupang_order_normalizer import order


class _Provider:
    def __init__(self, result, credentials_seen):
        self.result = result
        self.credentials_seen = credentials_seen

    def collect(self, **_kwargs):
        return self.result


class CoupangOrderCollectionServiceTest(unittest.TestCase):
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
        self.store.save("cred-1", {
            "vendor_id": "A0001", "access_key": "access",
            "secret_key": "secret",
        })
        self.connection = StoreConnection(
            company_id=1, marketplace_code="COUPANG", display_name="쿠팡",
            seller_identifier="seller", credential_reference="cred-1",
            masked_credential_hint="***", connection_status="CONNECTED",
            credential_version=1, created_by=1,
            creation_idempotency_key="create-1",
            creation_request_fingerprint="a" * 64,
        )
        self.db.add(self.connection)
        self.db.commit()
        self.db.refresh(self.connection)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def service(self, result):
        seen = []

        def factory(credentials):
            seen.append(dict(credentials))
            return _Provider(result, seen)

        return CoupangOrderCollectionService(
            self.db, self.store, provider_factory=factory,
        ), seen

    def test_success_collects_and_advances_last_collection_position(self):
        result = CoupangOrderCollectionResult(
            True, pages=(CoupangOrderPage((order(),), None),), http_status=200,
        )
        service, seen = self.service(result)
        response = service.run(1, self.connection.id, "ACCEPT")
        self.assertEqual(response.status, "SUCCEEDED")
        self.assertEqual(response.saved_fulfillment_count, 1)
        self.assertEqual(response.new_fulfillment_count, 1)
        self.assertEqual(response.new_unresolved_item_count, 1)
        self.assertEqual(self.db.query(Order).count(), 1)
        self.assertEqual(self.db.query(OrderChannelFulfillment).one().order_id, self.db.query(Order).one().id)
        self.assertEqual(seen[0]["vendor_id"], "A0001")
        position = self.db.query(OrderCollectionCursor).one()
        self.assertIsNotNone(position.last_successful_to)
        self.assertEqual(position.run_status, "IDLE")

    def test_provider_failure_does_not_advance_position(self):
        service, _ = self.service(CoupangOrderCollectionResult(
            False, error_code="RATE_LIMITED", error_summary="safe",
        ))
        response = service.run(1, self.connection.id, "ACCEPT")
        self.assertEqual(response.status, "FAILED")
        position = self.db.query(OrderCollectionCursor).one()
        self.assertIsNone(position.last_successful_to)
        self.assertEqual(position.last_error_code, "RATE_LIMITED")

    def test_one_malformed_order_is_partial_and_retriable(self):
        malformed = order(receiver={"name": "PRIVATE"})
        result = CoupangOrderCollectionResult(
            True,
            pages=(CoupangOrderPage((order(), malformed), None),),
            http_status=200,
        )
        service, _ = self.service(result)
        response = service.run(1, self.connection.id, "ACCEPT")
        self.assertEqual(response.status, "PARTIAL")
        self.assertEqual(response.saved_fulfillment_count, 1)
        self.assertEqual(response.failed_order_count, 1)
        self.assertNotIn("PRIVATE", repr(response))
        position = self.db.query(OrderCollectionCursor).one()
        self.assertIsNone(position.last_successful_to)

    def test_recollection_is_idempotent(self):
        result = CoupangOrderCollectionResult(
            True, pages=(CoupangOrderPage((order(),), None),), http_status=200,
        )
        service, _ = self.service(result)
        first = service.run(1, self.connection.id, "ACCEPT")
        second = service.run(1, self.connection.id, "ACCEPT")
        self.assertEqual(first.new_fulfillment_count, 1)
        self.assertEqual(second.duplicate_fulfillment_count, 1)
        self.assertEqual(self.db.query(OrderChannelFulfillment).count(), 1)
        self.assertEqual(self.db.query(UnresolvedOrderItem).count(), 1)

    def test_same_order_across_different_statuses_does_not_duplicate(self):
        result = CoupangOrderCollectionResult(
            True, pages=(CoupangOrderPage((order(),), None),), http_status=200,
        )
        service, _ = self.service(result)
        first = service.run(1, self.connection.id, "ACCEPT")
        second = service.run(1, self.connection.id, "INSTRUCT")
        self.assertEqual(first.new_fulfillment_count, 1)
        self.assertEqual(second.duplicate_fulfillment_count, 1)
        self.assertEqual(second.new_fulfillment_count, 0)
        self.assertEqual(self.db.query(Order).count(), 1)
        self.assertEqual(self.db.query(OrderChannelFulfillment).count(), 1)

    def test_run_reports_recovery_review_count_and_still_advances_checkpoint(self):
        result = CoupangOrderCollectionResult(
            True,
            pages=(CoupangOrderPage((order(status="FINAL_DELIVERY"),), None),),
            http_status=200,
        )
        service, _ = self.service(result)
        response = service.run(1, self.connection.id, "FINAL_DELIVERY")
        self.assertEqual(response.status, "SUCCEEDED")
        self.assertEqual(response.recovery_review_count, 1)
        self.assertEqual(len(response.recovery_candidates), 1)
        self.assertEqual(self.db.query(Order).count(), 0)
        position = self.db.query(OrderCollectionCursor).one()
        self.assertIsNotNone(position.last_successful_to)

    def test_page_limit_exceeded_does_not_advance_checkpoint(self):
        result = CoupangOrderCollectionResult(
            False, error_code="PAGE_LIMIT_EXCEEDED",
            error_summary="쿠팡 주문 조회의 안전 페이지 한도를 초과했습니다.",
            http_status=200,
        )
        service, _ = self.service(result)
        response = service.run(1, self.connection.id, "ACCEPT")
        self.assertEqual(response.status, "FAILED")
        self.assertEqual(response.error_codes, ("PAGE_LIMIT_EXCEEDED",))
        position = self.db.query(OrderCollectionCursor).one()
        self.assertIsNone(position.last_successful_to)

    def test_window_override_requires_timezone_aware_datetimes(self):
        from datetime import datetime as _dt

        service, _ = self.service(CoupangOrderCollectionResult(True, pages=(), http_status=200))
        with self.assertRaises(BadRequestException):
            service.run(
                1, self.connection.id, "ACCEPT",
                window_override=(_dt(2026, 9, 17), _dt(2026, 9, 17, 1)),
            )

    def test_window_override_rejects_reversed_range(self):
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        service, _ = self.service(CoupangOrderCollectionResult(True, pages=(), http_status=200))
        with self.assertRaises(BadRequestException):
            service.run(
                1, self.connection.id, "ACCEPT",
                window_override=(
                    _dt(2026, 9, 17, 1, tzinfo=_tz.utc),
                    _dt(2026, 9, 17, tzinfo=_tz.utc),
                ),
            )

    def test_request_accept_but_response_status_differs_blocks_new_order(self):
        """요청 파라미터는 ACCEPT였지만 응답 레코드 자체의 raw_status가
        DELIVERING(알려진 값이지만 신규생성 비대상)이면, 처음 보는
        주문이라도 신규 Order를 만들지 않고 복구 검토로만 분류한다 —
        호출 파라미터를 신뢰하지 않는다는 요구사항의 핵심 시나리오."""

        result = CoupangOrderCollectionResult(
            True,
            pages=(CoupangOrderPage((order(status="DELIVERING"),), None),),
            http_status=200,
        )
        service, _ = self.service(result)
        response = service.run(1, self.connection.id, "ACCEPT")
        self.assertEqual(response.status, "SUCCEEDED")
        self.assertEqual(response.recovery_review_count, 1)
        self.assertEqual(self.db.query(Order).count(), 0)

    def test_unknown_status_from_provider_response_is_fail_closed(self):
        """요청은 ACCEPT였지만 응답 레코드 자체의 상태가 다르고, 그
        상태가 어떤 알려진 값도 아니면 저장하지 않고 실패로 집계해
        체크포인트를 전진시키지 않는다 — 다음 재시도에서 다시 잡힌다."""

        result = CoupangOrderCollectionResult(
            True,
            pages=(CoupangOrderPage((order(status="SOME_FUTURE_STATUS"),), None),),
            http_status=200,
        )
        service, _ = self.service(result)
        response = service.run(1, self.connection.id, "ACCEPT")
        self.assertEqual(response.status, "PARTIAL")
        self.assertIn("UNKNOWN_ORDER_STATUS", response.error_codes)
        self.assertEqual(self.db.query(Order).count(), 0)
        position = self.db.query(OrderCollectionCursor).one()
        self.assertIsNone(position.last_successful_to)

    def test_company_isolation_and_connection_state_are_fail_closed(self):
        service, _ = self.service(CoupangOrderCollectionResult(True))
        with self.assertRaises(NotFoundException):
            service.run(2, self.connection.id, "ACCEPT")
        self.connection.connection_status = "ERROR"
        self.db.commit()
        with self.assertRaises(BadRequestException):
            service.run(1, self.connection.id, "ACCEPT")


if __name__ == "__main__":
    unittest.main()
