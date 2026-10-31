"""
=========================================================
Homez OS

File : app/domains/product_candidate/new_product_analysis_refresh_service.py

2026-10-31 — AI 통합 계획 3단계: new_product_discovery(15일 신제품 규칙)와 product_candidate.apply_new_product_analysis()를
잇는 얇은 연결 계층. trend_analysis_refresh_service와 같은 이유로 두 Domain은 서로를 모르고 이 계층만 양쪽을 안다.
지금까지 신제품 규칙은 어떤 진입점에도 연결되어 있지 않았다.

출시일은 후보에 이미 적힌 `release_date`만 쓴다. 출시일 자료원(D4: 공급처 상품 등록일을 쓸지)은 사용자 결정이 남아 있어
여기서 날짜를 추정하거나 다른 값으로 채우지 않는다.
  - 출시일 없음 → UNKNOWN(아무것도 바꾸지 않는다 — '모름'을 0점으로 저장하면 추천 단계의 점수 조건을 잘못 통과시킨다)
  - 미래 출시일 → FUTURE_DATE(아무것도 바꾸지 않는다 — 데이터 오류일 수 있어 사람이 확인)
  - 그 밖 → 규칙 결과를 근거와 함께 저장(APPLIED)
=========================================================
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.new_product_discovery.service import NewProductDiscoveryService
from app.domains.new_product_discovery.service import NewProductEvaluationError
from app.domains.product_candidate.service import ProductCandidateService


@dataclass(frozen=True)
class NewProductRefreshResult:

    status: str  # APPLIED / UNKNOWN / FUTURE_DATE
    candidate_id: int
    is_new_product: bool | None = None
    age_days: int | None = None
    novelty_score: float | None = None
    reason: str | None = None

    def as_dict(self) -> dict:

        return {
            "status": self.status, "candidate_id": self.candidate_id, "is_new_product": self.is_new_product,
            "age_days": self.age_days, "novelty_score": self.novelty_score, "reason": self.reason,
        }


class NewProductAnalysisRefreshService:

    def __init__(self, db: Session):

        self.db = db
        self.candidate_service = ProductCandidateService(db)
        self.new_product_service = NewProductDiscoveryService()

    def refresh(
        self, candidate_id: int, company_id: int, now: datetime | None = None,
    ) -> NewProductRefreshResult:
        """후보의 출시일로 15일 규칙을 돌린다. 회사 격리는 get_visible_for_company()가 강제한다."""

        candidate = self.candidate_service.get_visible_for_company(
            candidate_id, company_id,
        )

        if candidate.release_date is None:
            return NewProductRefreshResult(
                status="UNKNOWN", candidate_id=candidate_id,
                reason=(
                    "출시일을 몰라 신제품 여부를 판단하지 않았습니다 — "
                    "출시일 자료원이 정해지면 채워집니다."
                ),
            )

        try:
            result = self.new_product_service.evaluate(candidate.release_date, now)
        except NewProductEvaluationError as exc:
            return NewProductRefreshResult(
                status="FUTURE_DATE", candidate_id=candidate_id, reason=str(exc),
            )

        self.candidate_service.apply_new_product_analysis(
            candidate_id=candidate_id,
            company_id=company_id,
            is_new_product=result.is_new_product,
            novelty_score=result.novelty_score,
            confidence=result.confidence,
            evidence_text="; ".join(result.evidence),
            correlation_id=str(uuid.uuid4()),
        )

        return NewProductRefreshResult(
            status="APPLIED", candidate_id=candidate_id,
            is_new_product=result.is_new_product, age_days=result.age_days,
            novelty_score=result.novelty_score,
        )


__all__ = [
    "NewProductRefreshResult",
    "NewProductAnalysisRefreshService",
]
