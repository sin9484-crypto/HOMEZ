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

2026-10-31 — AI 통합 계획 3단계: 검색어를 상품명 전체가 아니라 **검색어 규칙**(`trend_discovery.keyword_rule`)이 잡음을 지운
값으로 보낸다. 사람이 확인·수정한 검색어를 넘기면 그것을 그대로 쓴다(규칙은 제안만). 쓴 검색어와 출처(규칙/사람 확인)는
결과와 근거 기록에 남긴다. 검색어가 비면 조회하지 않는다(NO_KEYWORD).
=========================================================
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.trend_discovery.adapter import TrendSourceAdapter
from app.domains.trend_discovery.keyword_rule import extract_search_keyword
from app.domains.trend_discovery.keyword_rule import normalize_user_keyword
from app.domains.trend_discovery.service import TrendDiscoveryService

KEYWORD_BY_RULE = "RULE"
KEYWORD_BY_PERSON = "PERSON"


@dataclass(frozen=True)
class TrendAnalysisRefreshResult:

    status: str  # APPLIED / NO_SIGNAL / NO_KEYWORD
    candidate_id: int
    trend_score: float | None = None
    confidence: float | None = None
    reason: str | None = None
    keyword: str | None = None
    keyword_basis: str | None = None  # RULE / PERSON


class TrendAnalysisRefreshService:

    def __init__(self, db: Session, trend_adapter: TrendSourceAdapter):

        self.db = db
        self.candidate_service = ProductCandidateService(db)
        self.trend_adapter = trend_adapter
        self.trend_discovery_service = TrendDiscoveryService()

    def refresh(
        self, candidate_id: int, company_id: int, keyword: str | None = None,
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

        if keyword is not None:
            try:
                search_keyword = normalize_user_keyword(keyword)
            except ValueError as exc:
                raise BadRequestException(str(exc)) from exc
            basis = KEYWORD_BY_PERSON
        else:
            search_keyword = extract_search_keyword(
                candidate.product_name, candidate.brand_hint,
            ).keyword
            basis = KEYWORD_BY_RULE

        if not search_keyword:
            return TrendAnalysisRefreshResult(
                status="NO_KEYWORD", candidate_id=candidate_id,
                reason="상품명에서 검색어를 만들지 못했습니다 — 검색어를 직접 적어 주세요.",
                keyword_basis=basis,
            )

        signal = self.trend_adapter.fetch(search_keyword)
        if signal is None:
            return TrendAnalysisRefreshResult(
                status="NO_SIGNAL", candidate_id=candidate_id,
                reason=(
                    "트렌드 신호를 가져오지 못했습니다 — 자격증명 미설정, "
                    "해당 키워드 데이터 없음, 네트워크 오류 중 하나입니다."
                ),
                keyword=search_keyword, keyword_basis=basis,
            )

        result = self.trend_discovery_service.evaluate(signal)

        self.candidate_service.apply_trend_analysis(
            candidate_id=candidate_id,
            company_id=company_id,
            trend_score=result.trend_score,
            confidence=result.confidence,
            evidence_text=(
                f"검색어 '{search_keyword}'"
                f"({'사람 확인' if basis == KEYWORD_BY_PERSON else '규칙'}); "
                + ("; ".join(result.evidence) if result.evidence else (
                    f"트렌드 분석(source={signal.source})"
                ))
            ),
            correlation_id=str(uuid.uuid4()),
        )

        return TrendAnalysisRefreshResult(
            status="APPLIED", candidate_id=candidate_id,
            trend_score=result.trend_score, confidence=result.confidence,
            keyword=search_keyword, keyword_basis=basis,
        )


__all__ = [
    "KEYWORD_BY_PERSON",
    "KEYWORD_BY_RULE",
    "TrendAnalysisRefreshResult",
    "TrendAnalysisRefreshService",
]
