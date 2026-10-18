"""
=========================================================
Homez OS

File : app/domains/product_candidate/candidate_evaluation_service.py

2026-10-18 — 상품 서칭 AI S2: 이 회사가 볼 수 있는 후보 전체를 사전 평가(`candidate_evaluation.evaluate`)해 후보 큐 순서로 돌려준다. **읽기 전용**(DB 쓰기·외부 호출·AI 없음).

  - 입력: 후보 이름·분류, 후보를 만들 때 보관한 고시 표(`URL_IMPORT_NOTICE:`), URL 분석 화면이 남긴 마진 분석 스냅샷(`AI_ANALYSIS_REPORT:`, 가장 나중 것),
    같은 상품 후보 정보(S1 유사도 규칙: 먼저 만들어진 후보가 원본, 나중 것은 중복).
  - 출력: 후보마다 결정(READY/HOLD/EXCLUDE)과 이유, 이 회사 기준 상태. 정렬은 후보 가능 → 보류 → 제외 권고, 같으면 최근 것 먼저.
후보의 상태·점수·근거는 바꾸지 않는다 — 승인·보류·거절은 사용자가 기존 후보 화면에서 한다. 회사 격리는 `list_candidates_for_company`를 그대로 쓴다.
=========================================================
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domains.product_candidate import candidate_evaluation as ev
from app.domains.product_candidate import product_similarity as ps
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.product_import.notice_suggestions import NOTICE_EVIDENCE_PREFIX
from app.domains.product_import.notice_suggestions import decode_notice_rows

CANDIDATE_LIMIT = ps.MAX_GROUP_ITEMS


class CandidateEvaluationService:

    def __init__(self, db: Session):
        self.db = db
        self.candidates = ProductCandidateService(db)

    def _evidence(self, candidate_ids: list[int]) -> tuple[dict[int, list[tuple[str, str]]], dict[int, dict]]:
        """후보별 고시 표(가장 나중 것)와 마진 분석 스냅샷(가장 나중 것)."""

        if not candidate_ids:
            return {}, {}
        rows = (
            self.db.query(ProductCandidateEvidence.candidate_id, ProductCandidateEvidence.payload_summary)
            .filter(
                ProductCandidateEvidence.candidate_id.in_(candidate_ids),
                or_(ProductCandidateEvidence.payload_summary.like(f"{NOTICE_EVIDENCE_PREFIX}%"), ProductCandidateEvidence.payload_summary.like(f"{ev.SNAPSHOT_PREFIX}%")),
            )
            .order_by(ProductCandidateEvidence.id.asc())
            .all()
        )
        notices: dict[int, list[tuple[str, str]]] = {}
        snapshots: dict[int, dict] = {}
        for candidate_id, summary in rows:
            if summary.startswith(NOTICE_EVIDENCE_PREFIX):
                notices[candidate_id] = decode_notice_rows(summary)
            else:
                parsed = ev.parse_snapshot(summary)
                if parsed is not None:
                    snapshots[candidate_id] = parsed  # 가장 나중 것이 남는다(읽을 수 없는 스냅샷은 이전 것을 지우지 않는다)
        return notices, snapshots

    def _status(self, candidate_id: int, company_id: int) -> str:
        try:
            return self.candidates.get_effective_status_for_company(candidate_id, company_id)
        except Exception:  # noqa: BLE001 — 상태를 못 읽어도 평가는 돌려준다
            return ""

    def evaluate_all(self, company_id: int) -> dict[str, Any]:
        candidates = self.candidates.list_candidates_for_company(company_id, limit=CANDIDATE_LIMIT)
        ids = [c.id for c in candidates]
        notices, snapshots = self._evidence(ids)

        # 같은 상품 후보: S1 규칙으로 비교하고, 번호가 작은(먼저 만들어진) 쪽을 원본으로 본다.
        items = [(c.id, ps.normalize(c.product_name, _spec_text(notices.get(c.id)))) for c in candidates]
        by_id = {c.id: c for c in candidates}
        duplicate_of: dict[int, list[dict[str, Any]]] = {}
        variants: dict[int, list[dict[str, Any]]] = {}
        statuses = {c.id: self._status(c.id, company_id) for c in candidates}
        for group in ps.group_products(items):
            for pair in group["pairs"]:
                a, b = pair["a"], pair["b"]
                older, newer = (a, b) if a < b else (b, a)
                if pair["relation"] == ps.DUPLICATE:
                    duplicate_of.setdefault(newer, []).append({"candidate_id": older, "status": statuses.get(older, "")})
                else:
                    variants.setdefault(a, []).append({"candidate_id": b})
                    variants.setdefault(b, []).append({"candidate_id": a})

        results = []
        for candidate in candidates:
            result = ev.evaluate(
                name=candidate.product_name, category_hint=candidate.category_hint, notice_rows=notices.get(candidate.id),
                snapshot=snapshots.get(candidate.id), duplicate_of=duplicate_of.get(candidate.id), variants=variants.get(candidate.id),
            )
            snapshot = snapshots.get(candidate.id) or {}
            results.append({
                "candidate_id": candidate.id, "product_name": candidate.product_name, "status": statuses.get(candidate.id, ""),
                "decision": result["decision"], "reasons": result["reasons"], "analysis_verdict": snapshot.get("verdict"),
            })
        results.sort(key=lambda r: (ev.DECISION_RANK[r["decision"]], -r["candidate_id"]))
        counts = {key: sum(1 for r in results if r["decision"] == key) for key in (ev.READY, ev.HOLD, ev.EXCLUDE)}
        return {"checked": len(results), "truncated": len(results) >= CANDIDATE_LIMIT, "counts": counts, "items": results}


def _spec_text(rows) -> str:
    from app.domains.product_candidate.candidate_similarity_service import CandidateSimilarityService

    return CandidateSimilarityService.spec_text(rows or [])
