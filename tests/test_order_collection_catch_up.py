"""
=========================================================
Homez OS

File : tests/test_order_collection_catch_up.py

주문 수집 시험(2026-10-14)에서 발견한 결함 두 가지를 고정한다.

 1) 수집 커서가 24시간보다 오래 쉬면 조회 구간이 쿠팡 한도(24시간 미만)를 넘어 `TIME_RANGE_TOO_LARGE`로 영구히 막혔다
    → 23시간 단위로 나눠 순서대로 따라잡는다(건너뛰지 않으므로 주문을 놓치지 않는다).
 2) 쿠팡을 부르기 전에 로컬에서 거부된 요청도 시험 예산으로 셌다 → 외부 호출이 아니므로 예산을 쓰지 않는다.

임시 SQLite·가짜 Provider만 쓴다 — 외부 호출 없음.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.order import auto_collection_scheduler as scheduler
from app.domains.order.adapters.coupang_collection import MAX_WINDOW
from app.domains.order.adapters.coupang_collection import CoupangOrderCollectionProvider
from app.domains.order.adapters.coupang_collection import CoupangOrderCollectionResult
from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.order.collection_service import CATCH_UP_CHUNK
from app.domains.order.collection_service import OVERLAP
from app.domains.order.collection_service import OrderCollectionCursorService

NOW = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - timedelta(days=27)  # 2026-09-17 — 실제 시험에서 막혔던 간격


class CatchUpWindowTests(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(self.engine, tables=[OrderCollectionCursor.__table__])
        self.db = sessionmaker(bind=self.engine)()
        self.service = OrderCollectionCursorService(self.db)
        self.db.add(OrderCollectionCursor(
            company_id=1, store_connection_id=10, channel_status="ACCEPT", run_status="IDLE",
            last_successful_to=LONG_AGO.replace(tzinfo=None),
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def test_a_long_gap_is_cut_below_the_coupang_limit(self):
        lease = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.assertLess(lease.created_at_to - lease.created_at_from, MAX_WINDOW)
        self.assertEqual(lease.created_at_from, LONG_AGO - OVERLAP)
        self.assertEqual(lease.created_at_to - lease.created_at_from, CATCH_UP_CHUNK)

    def test_preview_and_acquire_agree_so_the_confirmation_shows_the_real_window(self):
        preview = self.service.preview_window(1, 10, "ACCEPT", now=NOW)
        lease = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.assertEqual(preview, (lease.created_at_from, lease.created_at_to))

    def test_repeated_runs_catch_up_without_skipping_any_time(self):
        covered_to = None
        windows = 0
        while windows < 60:
            lease = self.service.acquire(1, 10, "ACCEPT", now=NOW)
            self.assertLess(lease.created_at_to - lease.created_at_from, MAX_WINDOW)
            if covered_to is not None:
                # 다음 구간은 이전 구간이 끝난 곳(또는 그 이전, 겹침)에서 시작해야 한다 — 빈틈이 없어야 한다.
                self.assertLessEqual(lease.created_at_from, covered_to)
            covered_to = lease.created_at_to
            self.service.succeed(lease.cursor_id, lease.lock_token, lease.created_at_to)
            windows += 1
            if lease.created_at_to >= NOW:
                break
        self.assertEqual(covered_to, NOW)
        self.assertLess(windows, 60)
        self.assertGreaterEqual(windows, 28)  # 27일 ÷ 23시간

    def test_a_failed_chunk_does_not_move_the_cursor(self):
        lease = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.service.fail(lease.cursor_id, lease.lock_token, "TIMEOUT")
        again = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.assertEqual(again.created_at_from, lease.created_at_from)

    def test_a_short_gap_is_unchanged(self):
        row = self.db.query(OrderCollectionCursor).one()
        row.last_successful_to = (NOW - timedelta(minutes=10)).replace(tzinfo=None)
        self.db.commit()
        lease = self.service.acquire(1, 10, "ACCEPT", now=NOW)
        self.assertEqual(lease.created_at_from, NOW - timedelta(minutes=10) - OVERLAP)
        self.assertEqual(lease.created_at_to, NOW)


class LocalRejectionBudgetTests(unittest.TestCase):

    class _Inner:
        """쿠팡 Provider를 흉내낸다 — 실제 어댑터의 precheck를 그대로 써서 거부 규칙이 같다."""

        def __init__(self):
            self.collect_calls = 0
            self._real = CoupangOrderCollectionProvider.__new__(CoupangOrderCollectionProvider)

        def precheck(self, **kwargs):
            return CoupangOrderCollectionProvider.precheck(self._real, **kwargs)

        def collect(self, **kwargs):
            self.collect_calls += 1
            return CoupangOrderCollectionResult(True)

    def _wrapper(self, inner):
        return scheduler._BudgetGuardedProvider(inner, db=None, company_id=1, store_connection_id=1, channel_status="ACCEPT")

    def test_a_request_rejected_locally_does_not_use_the_budget(self):
        inner = self._Inner()
        with mock.patch.object(scheduler, "_reserve_test_budget_call", return_value=True) as reserve:
            result = self._wrapper(inner).collect(
                created_at_from=LONG_AGO, created_at_to=NOW, status="ACCEPT",
            )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "TIME_RANGE_TOO_LARGE")
        reserve.assert_not_called()
        self.assertEqual(inner.collect_calls, 0)

    def test_a_valid_request_still_reserves_exactly_one_call(self):
        inner = self._Inner()
        with mock.patch.object(scheduler, "_reserve_test_budget_call", return_value=True) as reserve:
            self._wrapper(inner).collect(
                created_at_from=NOW - timedelta(hours=1), created_at_to=NOW, status="ACCEPT",
            )
        reserve.assert_called_once()
        self.assertEqual(inner.collect_calls, 1)

    def test_an_exhausted_budget_still_blocks_a_valid_request(self):
        inner = self._Inner()
        with mock.patch.object(scheduler, "_reserve_test_budget_call", return_value=False):
            result = self._wrapper(inner).collect(
                created_at_from=NOW - timedelta(hours=1), created_at_to=NOW, status="ACCEPT",
            )
        self.assertFalse(result.success)
        self.assertEqual(inner.collect_calls, 0)

    def test_provider_precheck_matches_the_old_inline_rules(self):
        real = CoupangOrderCollectionProvider.__new__(CoupangOrderCollectionProvider)
        pre = lambda **kw: CoupangOrderCollectionProvider.precheck(real, **kw)  # noqa: E731
        self.assertIsNone(pre(created_at_from=NOW - timedelta(hours=23), created_at_to=NOW, status="ACCEPT"))
        self.assertEqual(pre(created_at_from=NOW - MAX_WINDOW, created_at_to=NOW, status="ACCEPT").error_code, "TIME_RANGE_TOO_LARGE")
        self.assertEqual(pre(created_at_from=NOW, created_at_to=NOW, status="ACCEPT").error_code, "INVALID_TIME_RANGE")
        self.assertEqual(pre(created_at_from=NOW.replace(tzinfo=None), created_at_to=NOW, status="ACCEPT").error_code, "TIMEZONE_REQUIRED")
        self.assertEqual(pre(created_at_from=NOW - timedelta(hours=1), created_at_to=NOW, status="NOPE").error_code, "INVALID_STATUS")


if __name__ == "__main__":
    unittest.main()
