"""
=========================================================
Homez OS

File : app/domains/product_candidate/discovery_flow_service.py

2026-10-31 — AI 통합 계획 3단계: '판정기'를 '탐색기'로. 후보 하나하나가 **탐색 흐름**
(검색어 → 트렌드 점수 → 신제품 판정 → 판매 판단 평가 → 사람 검토) 어디까지 왔는지와 다음 할 일을 한 번에 보여 준다.

읽기 전용이다 — 아무것도 저장하지 않고 외부 호출도 없다. 각 단계 실행은 기존 진입점(트렌드 갱신·신제품 판정·평가하기·
검토 화면)이 하고, 승인은 사람이 한다. 다음 할 일은 '제안'일 뿐 순서를 강제하지 않는다(트렌드 키가 없어도 평가는 할 수 있다).
=========================================================
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.decision.constants import EvaluationStatus
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.trend_discovery.keyword_rule import extract_search_keyword

NEXT_REVIEW = "REVIEW"
NEXT_EVALUATE = "EVALUATE"
NEXT_NEW_PRODUCT = "NEW_PRODUCT"
NEXT_TREND = "TREND"
DONE_REVIEWED = "DONE_REVIEWED"
DONE_APPROVED = "DONE_APPROVED"
CLOSED = "CLOSED"

# 사람이 먼저 볼 순서: 검토 대기 → 평가 → 신제품 → 트렌드 → 끝난 것
NEXT_ORDER = (NEXT_REVIEW, NEXT_EVALUATE, NEXT_NEW_PRODUCT, NEXT_TREND, DONE_REVIEWED, DONE_APPROVED, CLOSED)

NEW_DONE = "DONE"
NEW_TODO = "TODO"
NEW_UNKNOWN_DATE = "UNKNOWN_DATE"


def _float(value):

    return None if value is None else float(value)


def next_step(candidate_status: str, company_status: str | None, trend_score, release_date, is_new_product,
              decision_status: str | None) -> str:
    """다음 할 일 판정(결정적). 끝난 상태가 먼저, 그다음 검토 대기·평가 여부, 그다음 빈 분석 단계."""

    if company_status == CandidateStatus.APPROVED:
        return DONE_APPROVED
    if company_status == CandidateStatus.REJECTED or candidate_status == CandidateStatus.EXPIRED:
        return CLOSED
    if decision_status == EvaluationStatus.PENDING_REVIEW:
        return NEXT_REVIEW
    if decision_status == EvaluationStatus.REVIEWED:
        return DONE_REVIEWED
    if trend_score is None:
        return NEXT_TREND
    if release_date is not None and is_new_product is None:
        return NEXT_NEW_PRODUCT
    return NEXT_EVALUATE


class DiscoveryFlowService:

    def __init__(self, db: Session):

        self.db = db
        self.candidate_service = ProductCandidateService(db)

    def _latest_decisions(self, company_id: int, candidate_ids: list[int]) -> dict[int, object]:

        from app.domains.decision.model import DecisionEvaluation

        if not candidate_ids:
            return {}
        rows = self.db.execute(
            select(DecisionEvaluation)
            .where(DecisionEvaluation.company_id == company_id)
            .where(DecisionEvaluation.candidate_id.in_(candidate_ids))
            .order_by(DecisionEvaluation.id.desc()),
        ).scalars().all()
        latest: dict[int, object] = {}
        for row in rows:
            latest.setdefault(row.candidate_id, row)
        return latest

    def list_flow(self, company_id: int, limit: int = 200) -> dict:
        """이 회사가 볼 수 있는 후보의 탐색 흐름. 다음 할 일 순서 → 최근 후보 순."""

        candidates = self.candidate_service.list_candidates_for_company(company_id, limit=limit)
        decisions = self._latest_decisions(company_id, [c.id for c in candidates])
        rows = []
        for candidate in candidates:
            company_status = self.candidate_service.get_effective_status_for_company(candidate.id, company_id)
            decision = decisions.get(candidate.id)
            suggestion = extract_search_keyword(candidate.product_name, candidate.brand_hint)
            if candidate.is_new_product is not None:
                new_state = NEW_DONE
            elif candidate.release_date is None:
                new_state = NEW_UNKNOWN_DATE
            else:
                new_state = NEW_TODO
            rows.append({
                "candidate_id": candidate.id,
                "product_name": candidate.product_name,
                "market": candidate.market,
                "status": candidate.status,
                "company_status": company_status,
                "keyword": suggestion.as_dict(),
                "trend_score": _float(candidate.trend_score),
                "release_date": candidate.release_date.isoformat() if candidate.release_date else None,
                "is_new_product": candidate.is_new_product,
                "novelty_score": _float(candidate.novelty_score),
                "new_product_state": new_state,
                "decision": None if decision is None else {
                    "id": decision.id, "status": decision.status, "recommendation": decision.recommendation,
                    "total_score": _float(decision.total_score), "blocked_by_safety": bool(decision.blocked_by_safety),
                },
                "next": next_step(candidate.status, company_status, candidate.trend_score, candidate.release_date,
                                  candidate.is_new_product, decision.status if decision is not None else None),
            })
        rows.sort(key=lambda r: (NEXT_ORDER.index(r["next"]), -r["candidate_id"]))
        counts = {key: sum(1 for r in rows if r["next"] == key) for key in NEXT_ORDER}
        return {"rows": rows, "counts": counts, "truncated": len(candidates) >= limit}


__all__ = [
    "CLOSED",
    "DONE_APPROVED",
    "DONE_REVIEWED",
    "DiscoveryFlowService",
    "NEXT_EVALUATE",
    "NEXT_NEW_PRODUCT",
    "NEXT_ORDER",
    "NEXT_REVIEW",
    "NEXT_TREND",
    "next_step",
]
