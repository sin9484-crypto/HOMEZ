"""
=========================================================
Homez OS

File : tests/test_api_usage_tracker.py

2026-09-07 — ApiUsageTracker 검증. 실제 앱 데이터 경로를 쓰지 않고
매 테스트마다 임시 파일 경로를 사용한다.
=========================================================
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from app.core.api_usage_tracker import ApiUsageTracker


class ApiUsageTrackerTest(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)  # 파일이 없는 상태에서 시작하는 걸 검증하고 싶음
        self.storage_path = Path(path)
        self.tracker = ApiUsageTracker(self.storage_path)

    def tearDown(self):
        if self.storage_path.exists():
            self.storage_path.unlink()
        tmp = self.storage_path.with_suffix(".tmp")
        if tmp.exists():
            tmp.unlink()

    def test_no_file_yet_is_within_limit(self):
        check = self.tracker.is_within_limit(
            "bucket_a", daily_limit=10, monthly_limit=100,
        )
        self.assertTrue(check.within_limit)
        self.assertEqual(check.daily_count, 0)
        self.assertEqual(check.monthly_count, 0)

    def test_record_call_increments_daily_and_monthly(self):
        self.tracker.record_call("bucket_a")
        self.tracker.record_call("bucket_a")
        check = self.tracker.is_within_limit(
            "bucket_a", daily_limit=None, monthly_limit=None,
        )
        self.assertEqual(check.daily_count, 2)
        self.assertEqual(check.monthly_count, 2)

    def test_daily_limit_blocks_once_reached(self):
        for _ in range(3):
            self.tracker.record_call("bucket_a")
        check = self.tracker.is_within_limit(
            "bucket_a", daily_limit=3, monthly_limit=None,
        )
        self.assertFalse(check.within_limit)

    def test_monthly_limit_blocks_once_reached(self):
        for _ in range(5):
            self.tracker.record_call("bucket_a")
        check = self.tracker.is_within_limit(
            "bucket_a", daily_limit=None, monthly_limit=5,
        )
        self.assertFalse(check.within_limit)

    def test_under_limit_still_allowed(self):
        self.tracker.record_call("bucket_a")
        check = self.tracker.is_within_limit(
            "bucket_a", daily_limit=10, monthly_limit=100,
        )
        self.assertTrue(check.within_limit)

    def test_buckets_are_independent(self):
        for _ in range(5):
            self.tracker.record_call("bucket_a")
        check_a = self.tracker.is_within_limit(
            "bucket_a", daily_limit=5, monthly_limit=None,
        )
        check_b = self.tracker.is_within_limit(
            "bucket_b", daily_limit=5, monthly_limit=None,
        )
        self.assertFalse(check_a.within_limit)
        self.assertTrue(check_b.within_limit)

    def test_daily_count_resets_on_new_day_but_monthly_persists(self):
        with mock.patch.object(
            ApiUsageTracker, "_today", return_value=date(2099, 1, 15),
        ):
            self.tracker.record_call("bucket_a")
            self.tracker.record_call("bucket_a")

        # 같은 달(2099-01), 다음 날(16일)로 이동 — 일일 카운트만 리셋되고
        # 월간 카운트는 그대로 유지돼야 한다.
        with mock.patch.object(
            ApiUsageTracker, "_today", return_value=date(2099, 1, 16),
        ):
            check = self.tracker.is_within_limit(
                "bucket_a", daily_limit=None, monthly_limit=None,
            )
        self.assertEqual(check.daily_count, 0)  # 날짜가 바뀌어 일일 카운트 리셋
        self.assertEqual(check.monthly_count, 2)  # 같은 달이라 월간 카운트는 유지

    def test_persists_across_tracker_instances(self):
        self.tracker.record_call("bucket_a")
        new_tracker = ApiUsageTracker(self.storage_path)
        check = new_tracker.is_within_limit(
            "bucket_a", daily_limit=None, monthly_limit=None,
        )
        self.assertEqual(check.daily_count, 1)

    def test_corrupted_file_does_not_crash_and_fails_safe_to_zero(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        check = self.tracker.is_within_limit(
            "bucket_a", daily_limit=10, monthly_limit=100,
        )
        self.assertTrue(check.within_limit)
        self.assertEqual(check.daily_count, 0)


if __name__ == "__main__":
    unittest.main()
