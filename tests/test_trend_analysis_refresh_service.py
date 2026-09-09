"""
=========================================================
Homez OS

File : tests/test_trend_analysis_refresh_service.py

2026-09-06 — TrendAnalysisRefreshService(trend_discovery ↔
product_candidate 연결) 검증. 실제 네이버 API는 호출하지 않는다 —
TrendSourceAdapter 계약을 만족하는 가짜 Adapter로 대체한다.
=========================================================
"""

from __future__ import annotations

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.product_candidate.trend_analysis_refresh_service import (
    TrendAnalysisRefreshService,
)
from app.domains.trend_discovery.adapter import TrendSignal
from app.domains.trend_discovery.adapter import TrendSourceAdapter

COMPANY_A = 1
COMPANY_B = 2


class _StubTrendAdapter(TrendSourceAdapter):
    def __init__(self, signals: dict[str, TrendSignal | None]):
        self._signals = signals
        self.fetch_calls: list[str] = []

    def fetch(self, keyword: str) -> TrendSignal | None:
        self.fetch_calls.append(keyword)
        return self._signals.get(keyword)


class TrendAnalysisRefreshServiceTest(unittest.TestCase):

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ProductCandidate.__table__,
                ProductCandidateEvidence.__table__,
                ProductCandidateDecision.__table__,
                ProductCandidateSelection.__table__,
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.candidate_service = ProductCandidateService(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_candidate(self, company_id: int, product_name: str) -> ProductCandidate:
        data = ProductCandidatePrivateCreate(
            source_reference=f"ref-{product_name}",
            market="COUPANG", product_name=product_name,
        )
        candidate, _events = self.candidate_service.register_private_candidate(
            data, company_id=company_id, correlation_id="c1",
        )
        return candidate

    def test_signal_found_applies_trend_score_and_advances_status(self):
        candidate = self._seed_candidate(COMPANY_A, "무선청소기")
        adapter = _StubTrendAdapter({
            "무선청소기": TrendSignal(
                keyword="무선청소기", market="NAVER_SHOPPING",
                search_volume_series=[10, 20, 40, 80],
                source="naver_datalab_official_api",
            ),
        })
        service = TrendAnalysisRefreshService(self.db, adapter)

        result = service.refresh(candidate.id, COMPANY_A)

        self.assertEqual(result.status, "APPLIED")
        self.assertIsNotNone(result.trend_score)
        self.assertEqual(adapter.fetch_calls, ["무선청소기"])

        self.db.expire_all()
        refreshed = self.db.get(ProductCandidate, candidate.id)
        self.assertEqual(refreshed.trend_score, result.trend_score)
        self.assertEqual(refreshed.status, CandidateStatus.ANALYZED)

    def test_no_signal_leaves_candidate_unchanged(self):
        candidate = self._seed_candidate(COMPANY_A, "존재하지않는상품명")
        adapter = _StubTrendAdapter({})  # 아무 키워드도 매칭 안 됨
        service = TrendAnalysisRefreshService(self.db, adapter)

        result = service.refresh(candidate.id, COMPANY_A)

        self.assertEqual(result.status, "NO_SIGNAL")
        self.assertIsNone(result.trend_score)
        self.assertIsNotNone(result.reason)

        self.db.expire_all()
        unchanged = self.db.get(ProductCandidate, candidate.id)
        self.assertIsNone(unchanged.trend_score)
        self.assertEqual(unchanged.status, CandidateStatus.DISCOVERED)

    def test_company_isolation_blocks_other_companys_candidate(self):
        candidate = self._seed_candidate(COMPANY_A, "회사A전용상품")
        adapter = _StubTrendAdapter({
            "회사A전용상품": TrendSignal(
                keyword="회사A전용상품", market="NAVER_SHOPPING",
                search_volume_series=[10, 20, 30],
            ),
        })
        service = TrendAnalysisRefreshService(self.db, adapter)

        with self.assertRaises(NotFoundException):
            service.refresh(candidate.id, COMPANY_B)

        self.assertEqual(adapter.fetch_calls, [])


if __name__ == "__main__":
    unittest.main()
