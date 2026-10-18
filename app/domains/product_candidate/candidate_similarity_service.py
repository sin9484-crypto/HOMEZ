"""
=========================================================
Homez OS

File : app/domains/product_candidate/candidate_similarity_service.py

2026-10-18 — 상품 서칭 AI S1: 이 회사가 볼 수 있는 상품 후보에서 **같은 상품·같은 상품군·비슷한 상품**을 찾아 알려 준다. **읽기 전용**(DB 쓰기·외부 호출 없음).

  - `similar_to`: 새로 만들려는 상품(URL 가져오기 결과 등)과 이미 있는 후보를 비교해 가까운 순서로 돌려준다 — 같은 상품을 두 번 등록하는 일을 막는 알림용.
  - `families`: 이미 있는 후보 전체를 상품군으로 묶어 보여 준다 — 중복 등록·구성만 다른 상품을 한눈에 확인.
후보를 합치거나 지우거나 상태를 바꾸지 않는다(그 결정은 사용자가 후보 화면에서 한다). 회사 격리는 기존 `list_candidates_for_company`(GLOBAL + 자사 PRIVATE)를 그대로 쓴다.
상태는 이 회사 기준(`get_effective_status_for_company`)으로 보여 준다(거절·보류한 상품도 "이미 본 상품"이라는 정보라 포함한다).
=========================================================
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.domains.product_candidate import product_similarity as ps
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.product_import.notice_suggestions import NOTICE_EVIDENCE_PREFIX
from app.domains.product_import.notice_suggestions import decode_notice_rows

MAX_SIMILAR = 5
CANDIDATE_LIMIT = ps.MAX_GROUP_ITEMS
# 고시 표에서 구성·용량을 알려 주는 항목만 규격 추출에 쓴다(재질·제조국 같은 항목의 숫자가 규격으로 읽히지 않게).
SPEC_LABEL_WORDS = ("용량", "중량", "수량", "구성", "크기", "사이즈", "규격", "색상", "모델")


class CandidateSimilarityService:

    def __init__(self, db: Session):
        self.db = db
        self.candidates = ProductCandidateService(db)

    @staticmethod
    def spec_text(rows) -> str:
        """고시 (항목, 값) 목록에서 구성·용량 같은 규격 항목의 값만 이어 붙인다."""

        return " ".join(str(value) for label, value in rows if any(word in str(label) for word in SPEC_LABEL_WORDS))[:600]

    def _spec_text_by_candidate(self, candidate_ids: list[int]) -> dict[int, str]:
        """후보를 만들 때 보관한 고시 표(후보 근거)에서 후보별 규격 글. 없으면 빈 값 — 이름만으로 비교한다."""

        if not candidate_ids:
            return {}
        rows = (
            self.db.query(ProductCandidateEvidence.candidate_id, ProductCandidateEvidence.payload_summary)
            .filter(ProductCandidateEvidence.candidate_id.in_(candidate_ids), ProductCandidateEvidence.payload_summary.like(f"{NOTICE_EVIDENCE_PREFIX}%"))
            .order_by(ProductCandidateEvidence.id.asc())
            .all()
        )
        latest = {candidate_id: summary for candidate_id, summary in rows}  # 같은 후보의 가장 나중 근거가 남는다
        return {candidate_id: self.spec_text(decode_notice_rows(summary)) for candidate_id, summary in latest.items()}

    def _load(self, company_id: int) -> list:
        return self.candidates.list_candidates_for_company(company_id, limit=CANDIDATE_LIMIT)

    def _status(self, candidate_id: int, company_id: int) -> str:
        try:
            return self.candidates.get_effective_status_for_company(candidate_id, company_id)
        except Exception:  # noqa: BLE001 — 상태를 못 읽어도 비교 결과는 돌려준다
            return ""

    def similar_to(self, company_id: int, name: str | None, extra: str | None = None, *, exclude_reference: str | None = None) -> list[dict[str, Any]]:
        """`name`과 관계가 DUPLICATE·VARIANT·SIMILAR인 후보를 가까운 순서(관계 → 이름 일치)로 최대 5개."""

        target = ps.normalize(name, extra)
        if not target.core and not target.models:
            return []
        found: list[dict[str, Any]] = []
        loaded = self._load(company_id)
        extras = self._spec_text_by_candidate([c.id for c in loaded])
        for candidate in loaded:
            if exclude_reference and candidate.source_reference == exclude_reference:
                continue  # 같은 주소로 이미 가져온 후보는 후보 생성 쪽 멱등 규칙이 다룬다
            result = ps.compare(target, ps.normalize(candidate.product_name, extras.get(candidate.id)))
            if result["relation"] == ps.DIFFERENT:
                continue
            found.append({
                "candidate_id": candidate.id, "product_name": candidate.product_name,
                "status": self._status(candidate.id, company_id), "relation": result["relation"],
                "core_score": result["core_score"], "reasons": result["reasons"],
            })
        found.sort(key=lambda r: (ps.RELATION_RANK[r["relation"]], -r["core_score"], r["candidate_id"]))
        return found[:MAX_SIMILAR]

    def families(self, company_id: int) -> dict[str, Any]:
        """회사가 볼 수 있는 후보를 상품군으로 묶는다. 각 군의 구성원은 후보 번호·이름·상태·규격과 함께 돌려준다."""

        candidates = self._load(company_id)
        extras = self._spec_text_by_candidate([c.id for c in candidates])
        items = [(c.id, ps.normalize(c.product_name, extras.get(c.id))) for c in candidates]
        by_id = {c.id: c for c in candidates}
        groups = ps.group_products(items)
        out = []
        for group in groups:
            members = [{
                "candidate_id": member, "product_name": by_id[member].product_name,
                "status": self._status(member, company_id), "specs": group["specs"].get(member, {}),
            } for member in group["members"]]
            entry = {"members": members, "duplicate_sets": group["duplicate_sets"], "pairs": group["pairs"]}
            if group.get("similar_pairs"):
                entry["similar_pairs"] = [{**p, "a_name": by_id[p["a"]].product_name, "b_name": by_id[p["b"]].product_name} for p in group["similar_pairs"]]
            out.append(entry)
        return {"checked": len(candidates), "truncated": len(candidates) >= CANDIDATE_LIMIT, "families": out}
