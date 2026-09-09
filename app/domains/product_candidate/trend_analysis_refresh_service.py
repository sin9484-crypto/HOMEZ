"""
=========================================================
Homez OS

File : app/domains/product_candidate/trend_analysis_refresh_service.py

2026-09-06 — trend_discovery(실 Adapter 도입)와 product_candidate.
apply_trend_analysis()를 잇는 오케스트레이션. 두 Domain 모두 이
연결을 스스로 만들지 않는다(product_candidate는 trend_discovery를
몰라야 하고, trend_discovery는 product_candidate를 몰라야 한다는
기존 경계를 유지) — 이 얇은 계층 하나만 양쪽을 안다.

이 서비스는 "이미 존재하는 후보 하나"를 다시 분석하는 수동 실행
엔드포인트 전용이다. 전체 후보를 훑는 배치/스케줄러는 이번 범위가
아니다(이 저장소에 스케줄러 자체가 없다는 기존 감사 결과 — 별도
과제).
=========================================================
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domains.product_candidate.service import ProductCandidateService
from app.domains.trend_discovery.adapter import TrendSourceAdapter
from app.domains.trend_discovery.service import TrendDiscoveryService


@dataclass(frozen=True)
class TrendAnalysisRefreshResult:

    status: str  # APPLIED / NO_SIGNAL
    candidate_id: int
    trend_score: float | None = None
    confidence: float | None = None
    reason: str | None = None


class TrendAnalysisRefreshService:

    def __init__(self, db: Session, trend_adapter: TrendSourceAdapter):

        self.db = db
        self.candidate_service = ProductCandidateService(db)
        self.trend_adapter = trend_adapter
        self.trend_discovery_service = TrendDiscoveryService()

    def refresh(
        self, candidate_id: int, company_id: int,
    ) -> TrendAnalysisRefreshResult:
        """
        후보의 product_name을 키워드 삼아 실제 트렌드 신호를 조회하고,
        신호가 있으면 점수를 계산해 apply_trend_analysis()로 반영한다.
        신호가 없으면(Adapter가 None 반환 — 자격증명 없음/키워드 데이터
        없음/네트워크 실패 등 모든 사유 포함) 아무것도 바꾸지 않고
        NO_SIGNAL을 그대로 보고한다 — 실패를 조용히 성공으로 위장하지
        않는다.
        """

        candidate = self.candidate_service.get_visible_for_company(
            candidate_id, company_id,
        )

        signal = self.trend_adapter.fetch(candidate.product_name)
        if signal is None:
            return TrendAnalysisRefreshResult(
                status="NO_SIGNAL", candidate_id=candidate_id,
                reason=(
                    "트렌드 신호를 가져오지 못했습니다 — 자격증명 미설정, "
                    "해당 키워드 데이터 없음, 네트워크 오류 중 하나입니다."
                ),
            )

        result = self.trend_discovery_service.evaluate(signal)

        self.candidate_service.apply_trend_analysis(
            candidate_id=candidate_id,
            company_id=company_id,
            trend_score=result.trend_score,
            confidence=result.confidence,
            evidence_text="; ".join(result.evidence) if result.evidence else (
                f"트렌드 분석(source={signal.source})"
            ),
            correlation_id=str(uuid.uuid4()),
        )

        return TrendAnalysisRefreshResult(
            status="APPLIED", candidate_id=candidate_id,
            trend_score=result.trend_score, confidence=result.confidence,
        )


__all__ = [
    "TrendAnalysisRefreshResult",
    "TrendAnalysisRefreshService",
]
