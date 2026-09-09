from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.order.collection_service import (
    INITIAL_LOOKBACK, OVERLAP, STALE_LOCK_AFTER,
    OrderCollectionCursorService,
)


NOW = datetime(2026, 8, 31, 6, 0, tzinfo=timezone.utc)


class OrderCollectionCursorServiceTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(self.engine, tables=[OrderCollectionCursor.__table__])
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.service = OrderCollectionCursorService(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def test_first_run_uses_bounded_initial_window(self):
        lease = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.assertEqual(lease.created_at_from, NOW - INITIAL_LOOKBACK)
        self.assertEqual(lease.created_at_to, NOW)

    def test_success_advances_and_next_run_overlaps(self):
        first = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.service.succeed(first.cursor_id, first.lock_token, NOW)
        later = NOW + timedelta(minutes=10)
        second = self.service.acquire(1, 10, "ACCEPT", now=later)
        self.assertEqual(second.created_at_from, NOW - OVERLAP)
        self.assertEqual(second.created_at_to, later)

    def test_failure_does_not_advance_cursor(self):
        lease = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.service.fail(lease.cursor_id, lease.lock_token, "TIMEOUT")
        row = self.db.get(OrderCollectionCursor, lease.cursor_id)
        self.assertIsNone(row.last_successful_to)
        self.assertEqual(row.last_error_code, "TIMEOUT")

    def test_concurrent_run_is_blocked(self):
        self.service.acquire(1, 10, "ACCEPT", now=NOW)
        with self.assertRaises(ConflictException):
            self.service.acquire(1, 10, "ACCEPT", now=NOW)

    def test_stale_lock_can_be_recovered(self):
        first = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        later = NOW + STALE_LOCK_AFTER + timedelta(seconds=1)
        recovered = self.service.acquire(1, 10, "ACCEPT", now=later)
        self.assertNotEqual(first.lock_token, recovered.lock_token)

    def test_scope_isolated_by_company_connection_and_status(self):
        leases = {
            self.service.acquire(1, 10, "ACCEPT", now=NOW).cursor_id,
            self.service.acquire(2, 10, "ACCEPT", now=NOW).cursor_id,
            self.service.acquire(1, 11, "ACCEPT", now=NOW).cursor_id,
            self.service.acquire(1, 10, "INSTRUCT", now=NOW).cursor_id,
        }
        self.assertEqual(len(leases), 4)

    def test_future_cursor_fails_closed_without_advancing(self):
        row = OrderCollectionCursor(
            company_id=1, store_connection_id=10, channel_status="ACCEPT",
            last_successful_to=(NOW + timedelta(hours=1)).replace(tzinfo=None),
            run_status="IDLE",
        )
        self.db.add(row)
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "FUTURE_ORDER_COLLECTION_CURSOR"):
            self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.db.refresh(row)
        self.assertEqual(row.run_status, "FAILED")
        self.assertEqual(row.last_error_code, "FUTURE_CURSOR")

    def test_invalid_status_does_not_create_cursor(self):
        with self.assertRaisesRegex(ValueError, "INVALID_ORDER_COLLECTION_STATUS"):
            self.service.acquire(1, 10, "CANCELLED", now=NOW)
        self.assertEqual(self.db.query(OrderCollectionCursor).count(), 0)


if __name__ == "__main__":
    unittest.main()
