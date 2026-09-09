"""
=========================================================
Homez OS

File : tests/test_naver_datalab_trend_adapter.py

2026-09-06 최초 작성, 2026-09-07 NAVER API HUB 실 스펙 반영 —
NaverDataLabTrendAdapter 검증. 실제 네트워크는 절대 호출하지 않는다
— http_post 주입 지점을 통해 항상 가짜 응답 객체로 대체한다
(CoupangOrderCollectionService의 provider_factory 주입과 동일한
테스트 철학). 사용량 카운터도 항상 임시 파일 경로를 명시적으로
주입해, 실제 개발 PC의 config 디렉터리를 절대 건드리지 않는다.
=========================================================
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.core.api_usage_tracker import ApiUsageTracker
from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter
from tests.ai_governance_test_helpers import deactivated_capability


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class NaverDataLabTrendAdapterTest(unittest.TestCase):

    CRED_NAME = "naver-datalab-test"

    def setUp(self):
        self.store = InMemoryCredentialStore()
        self.store.save(self.CRED_NAME, {
            "client_id": "test-client-id", "client_secret": "test-secret",
        })
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        self.usage_path = Path(path)
        self.usage_tracker = ApiUsageTracker(self.usage_path)

    def tearDown(self):
        if self.usage_path.exists():
            self.usage_path.unlink()
        tmp = self.usage_path.with_suffix(".tmp")
        if tmp.exists():
            tmp.unlink()

    def _adapter(self, http_post, usage_tracker=None):
        return NaverDataLabTrendAdapter(
            self.store, self.CRED_NAME, http_post=http_post,
            usage_tracker=usage_tracker or self.usage_tracker,
        )

    def test_successful_fetch_returns_trend_signal(self):
        captured = {}

        def fake_post(url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return _FakeResponse(200, {
                "results": [{
                    "title": "무선청소기",
                    "keywords": ["무선청소기"],
                    "data": [
                        {"period": "2026-06-01", "ratio": 10.5},
                        {"period": "2026-06-08", "ratio": 15.2},
                        {"period": "2026-06-15", "ratio": 22.7},
                    ],
                }],
            })

        signal = self._adapter(fake_post).fetch("무선청소기")

        self.assertIsNotNone(signal)
        self.assertEqual(signal.keyword, "무선청소기")
        self.assertEqual(signal.market, "NAVER_SHOPPING")
        self.assertEqual(signal.search_volume_series, [10.5, 15.2, 22.7])
        self.assertEqual(signal.source, "naver_api_hub_search_trend")

        self.assertEqual(captured["url"], NaverDataLabTrendAdapter.ENDPOINT)
        self.assertIn("naverapihub.apigw.ntruss.com", captured["url"])
        self.assertEqual(captured["headers"]["X-NCP-APIGW-API-KEY-ID"], "test-client-id")
        self.assertEqual(captured["headers"]["X-NCP-APIGW-API-KEY"], "test-secret")
        self.assertNotIn("X-Naver-Client-Id", captured["headers"])
        self.assertEqual(
            captured["json"]["keywordGroups"],
            [{"groupName": "무선청소기", "keywords": ["무선청소기"]}],
        )

    def test_missing_credential_returns_none(self):
        empty_store = InMemoryCredentialStore()
        adapter = NaverDataLabTrendAdapter(
            empty_store, "does-not-exist", http_post=lambda **kw: None,
            usage_tracker=self.usage_tracker,
        )
        self.assertIsNone(adapter.fetch("아무거나"))

    def test_empty_keyword_returns_none_without_network_call(self):
        called = []

        def fake_post(**kwargs):
            called.append(True)
            return _FakeResponse(200, {"results": []})

        self.assertIsNone(self._adapter(fake_post).fetch(""))
        self.assertIsNone(self._adapter(fake_post).fetch("   "))
        self.assertEqual(called, [])

    def test_non_200_status_returns_none(self):
        adapter = self._adapter(lambda **kw: _FakeResponse(429, {}))
        self.assertIsNone(adapter.fetch("과부하테스트"))

    def test_empty_results_returns_none(self):
        adapter = self._adapter(lambda **kw: _FakeResponse(200, {"results": []}))
        self.assertIsNone(adapter.fetch("결과없음"))

    def test_malformed_response_returns_none_not_exception(self):
        adapter = self._adapter(
            lambda **kw: _FakeResponse(200, {"results": [{"data": "이상한형식"}]}),
        )
        self.assertIsNone(adapter.fetch("이상한응답"))

    def test_network_exception_returns_none_not_raised(self):
        def raising_post(**kwargs):
            raise ConnectionError("네트워크 끊김")

        adapter = self._adapter(raising_post)
        self.assertIsNone(adapter.fetch("네트워크오류"))

    def test_inactive_capability_blocks_fetch(self):
        from app.domains.ai_governance.constants import CapabilityCode
        from app.domains.ai_governance.service import InactiveCapabilityError

        adapter = self._adapter(lambda **kw: _FakeResponse(200, {"results": []}))
        with deactivated_capability(CapabilityCode.PRODUCT_DISCOVERY):
            with self.assertRaises(InactiveCapabilityError):
                adapter.fetch("차단테스트")

    # ---------------- 2026-09-07 신규: 무료 할당량(월 30,000회) ----------------

    def test_successful_call_records_usage(self):
        adapter = self._adapter(
            lambda **kw: _FakeResponse(200, {
                "results": [{"data": [{"period": "2026-06-01", "ratio": 1.0}]}],
            }),
        )
        adapter.fetch("사용량기록테스트")
        check = self.usage_tracker.is_within_limit(
            NaverDataLabTrendAdapter.USAGE_BUCKET,
            daily_limit=None, monthly_limit=None,
        )
        self.assertEqual(check.monthly_count, 1)

    def test_monthly_limit_reached_blocks_call_without_network(self):
        called = []

        def fake_post(**kwargs):
            called.append(True)
            return _FakeResponse(200, {"results": []})

        # 30,000번 실제로 record_call()을 반복하면(각 호출마다 파일
        # 전체를 읽고 쓰므로) 테스트가 몇 분씩 걸린다 — 이미 한도에
        # 도달한 상태를 곧바로 파일에 써서 시뮬레이션한다(카운터
        # 로직 자체는 다른 테스트들이 이미 record_call()로 검증함).
        import json as _json
        from datetime import date as _date

        today = _date.today()
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.usage_path, "w", encoding="utf-8") as f:
            _json.dump({
                NaverDataLabTrendAdapter.USAGE_BUCKET: {
                    "daily": {"date": today.isoformat(), "count": 0},
                    "monthly": {
                        "month": f"{today.year:04d}-{today.month:02d}",
                        "count": NaverDataLabTrendAdapter.MONTHLY_CALL_LIMIT,
                    },
                },
            }, f)

        adapter = self._adapter(fake_post)
        result = adapter.fetch("한도초과테스트")

        self.assertIsNone(result)
        self.assertEqual(called, [])  # 한도 도달 시 실제 네트워크 호출 자체를 하지 않음

    def test_failed_network_call_still_counts_against_usage(self):
        """실패한 호출도 네이버 쪽 집계에는 이미 포함되므로, 호출
        시도 자체를 카운트해야 한다(성공 여부와 무관)."""

        def raising_post(**kwargs):
            raise ConnectionError("네트워크 끊김")

        adapter = self._adapter(raising_post)
        adapter.fetch("실패해도카운트")

        check = self.usage_tracker.is_within_limit(
            NaverDataLabTrendAdapter.USAGE_BUCKET,
            daily_limit=None, monthly_limit=None,
        )
        self.assertEqual(check.monthly_count, 1)


if __name__ == "__main__":
    unittest.main()
