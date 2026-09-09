"""
=========================================================
Homez OS

File : app/domains/product_candidate/repository.py

V3 ProductCandidate Repository

쓰기 메서드는 commit하지 않고 flush만 수행한다(*_no_commit). Transaction
경계(commit/rollback)는 ProductCandidateService가 소유한다 — discover,
analysis 반영, recommend, approve/hold/reject 각각을 최상위에서 정확히
commit 1회로 처리하기 위함이다.

2026-08-14 테넌트 격리 감사(Gate R13) — ProductCandidate 자체(원본
발견 카탈로그)는 전역으로 유지하고, 회사별 승인 상태 투영(Selection)
전용 메서드를 추가했다. ProductCandidateDecision(append-only 결정
이력)도 company_id로 스코프한다.
=========================================================
"""

from sqlalchemy import and_
from sqlalchemy import or_
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.product_candidate.constants import CandidateVisibility
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection


class ProductCandidateRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # --------------------------------------------------
    # Candidate (전역 — 원본 발견 카탈로그, company_id 없음)
    # --------------------------------------------------

    def get(
        self,
        candidate_id: int,
    ) -> ProductCandidate | None:

        return (
            self.db.query(ProductCandidate)
            .filter(ProductCandidate.id == candidate_id)
            .first()
        )

    def get_by_candidate_key(
        self,
        candidate_key: str,
    ) -> ProductCandidate | None:

        return (
            self.db.query(ProductCandidate)
            .filter(ProductCandidate.candidate_key == candidate_key)
            .first()
        )

    def list_candidates(
        self,
        status: str | None = None,
        market: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ProductCandidate]:

        query = self.db.query(ProductCandidate)

        if status is not None:
            query = query.filter(ProductCandidate.status == status)

        if market is not None:
            query = query.filter(ProductCandidate.market == market)

        return (
            query.order_by(ProductCandidate.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    @staticmethod
    def _visibility_filter(company_id: int):
        """
        GLOBAL이거나(전역, 모두에게 보임), PRIVATE이면서 이 회사가
        소유주인 경우만(2026-08-15 V7 Gate 2). 다른 회사의 PRIVATE
        후보는 이 필터로 걸러져 조회 자체가 안 된다(존재 여부도
        노출하지 않는다 — 404와 동일하게 처리된다).
        """

        return or_(
            ProductCandidate.visibility == CandidateVisibility.GLOBAL,
            and_(
                ProductCandidate.visibility == CandidateVisibility.PRIVATE,
                ProductCandidate.owner_company_id == company_id,
            ),
        )

    def get_visible_for_company(
        self,
        candidate_id: int,
        company_id: int,
    ) -> ProductCandidate | None:
        """id 일치 + 이 회사가 볼 수 있는 후보(GLOBAL 또는 자사
        PRIVATE)만 반환한다(2026-08-15 V7 Gate 2)."""

        return (
            self.db.query(ProductCandidate)
            .filter(ProductCandidate.id == candidate_id)
            .filter(self._visibility_filter(company_id))
            .first()
        )

    def list_candidates_for_company(
        self,
        company_id: int,
        status: str | None = None,
        market: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ProductCandidate]:
        """이 회사가 볼 수 있는 후보(GLOBAL 전체 + 자사 PRIVATE)만
        조회한다(2026-08-15 V7 Gate 2)."""

        query = self.db.query(ProductCandidate).filter(
            self._visibility_filter(company_id),
        )

        if status is not None:
            query = query.filter(ProductCandidate.status == status)

        if market is not None:
            query = query.filter(ProductCandidate.market == market)

        return (
            query.order_by(ProductCandidate.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_no_commit(
        self,
        candidate: ProductCandidate,
    ) -> ProductCandidate:

        self.db.add(candidate)
        self.db.flush()

        return candidate

    def save_no_commit(
        self,
        candidate: ProductCandidate,
    ) -> ProductCandidate:

        self.db.add(candidate)
        self.db.flush()

        return candidate

    def update_status_conditional(
        self,
        candidate_id: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
    ) -> int:
        """
        id 일치 + 현재 status가 expected_statuses 중 하나일 때만 조건부
        갱신한다. 반환값은 실제로 갱신된 행 수(rowcount) — 정확히 1이어야
        성공이다(0이면 그 사이 상태가 바뀐 것 — 호출자가 경쟁으로 처리).

        이 메서드는 전역 워크플로우 상태(DISCOVERED/ANALYZED/RECOMMENDED/
        EXPIRED)에만 사용한다 — 회사별 승인(APPROVED/HELD/REJECTED)은
        ProductCandidateSelection 쪽 조건부 UPDATE를 사용한다.
        """

        stmt = (
            update(ProductCandidate)
            .where(ProductCandidate.id == candidate_id)
            .where(ProductCandidate.status.in_(expected_statuses))
            .values(status=new_status)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # Evidence (append-only, 전역)
    # --------------------------------------------------

    def add_evidence_no_commit(
        self,
        evidence: ProductCandidateEvidence,
    ) -> ProductCandidateEvidence:

        self.db.add(evidence)
        self.db.flush()

        return evidence

    def list_evidence(
        self,
        candidate_id: int,
    ) -> list[ProductCandidateEvidence]:

        return (
            self.db.query(ProductCandidateEvidence)
            .filter(ProductCandidateEvidence.candidate_id == candidate_id)
            .order_by(ProductCandidateEvidence.id.asc())
            .all()
        )

    # --------------------------------------------------
    # Decision (append-only, 회사 스코프)
    # --------------------------------------------------

    def add_decision_no_commit(
        self,
        decision: ProductCandidateDecision,
    ) -> ProductCandidateDecision:

        self.db.add(decision)
        self.db.flush()

        return decision

    def list_decisions_for_company(
        self,
        candidate_id: int,
        company_id: int,
    ) -> list[ProductCandidateDecision]:

        return (
            self.db.query(ProductCandidateDecision)
            .filter(ProductCandidateDecision.candidate_id == candidate_id)
            .filter(ProductCandidateDecision.company_id == company_id)
            .order_by(ProductCandidateDecision.id.asc())
            .all()
        )

    # --------------------------------------------------
    # Selection (회사별 현재 상태 투영 — UNIQUE(candidate_id, company_id))
    # --------------------------------------------------

    def get_selection(
        self,
        candidate_id: int,
        company_id: int,
    ) -> ProductCandidateSelection | None:

        return (
            self.db.query(ProductCandidateSelection)
            .filter(ProductCandidateSelection.candidate_id == candidate_id)
            .filter(ProductCandidateSelection.company_id == company_id)
            .first()
        )

    def add_selection_no_commit(
        self,
        selection: ProductCandidateSelection,
    ) -> ProductCandidateSelection:

        self.db.add(selection)
        self.db.flush()

        return selection

    def update_selection_status_conditional(
        self,
        candidate_id: int,
        company_id: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
        memo: str | None = None,
    ) -> int:
        """
        (candidate_id, company_id) 일치 + 현재 status가 expected_statuses
        중 하나일 때만 조건부 갱신. 정확히 1이어야 성공이다.

        memo가 주어지면(2026-08-15 V7 Gate 2) 같은 UPDATE로 최신 메모
        투영도 함께 갱신한다 — 별도 UPDATE로 나누면 상태와 메모가
        서로 다른 시점의 값을 반영할 여지가 생기므로 하나로 묶는다.
        """

        values = {"status": new_status}

        if memo is not None:
            values["memo"] = memo

        stmt = (
            update(ProductCandidateSelection)
            .where(ProductCandidateSelection.candidate_id == candidate_id)
            .where(ProductCandidateSelection.company_id == company_id)
            .where(ProductCandidateSelection.status.in_(expected_statuses))
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount


__all__ = [
    "ProductCandidateRepository",
]
