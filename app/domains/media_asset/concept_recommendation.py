"""
=========================================================
Homez OS

File : app/domains/media_asset/concept_recommendation.py

2026-10-23 — 이미지 AI I3: **상품 카테고리에 맞는 컨셉 추천** — 순수 규칙(DB·네트워크·AI 없음). 컨셉 명세(I1)의 `good_for`(어울리는 상품)·`avoid_for`(피해야 할 상품)를 읽어 순서를 매긴다.
계획서 §1-3의 "카테고리 추천은 규칙이 정하고 AI는 이유만 설명"을 따른다 — 이 모듈은 이유를 **명세에 적힌 글 그대로** 돌려주므로 AI가 필요 없다.

기준:
  - 카테고리 글(사용자가 넣은 상품 분류·키워드)과 명세의 낱말을 **포함 관계**로 비교한다(공백·대소문자 무시, 두 글자 이상만). 틀릴 수 있는 추측이므로 결과는 **권고**일 뿐 사용자가 모든 컨셉을 직접 고를 수 있다.
  - `avoid_for`에 걸리면 **추천에서 빼고 이유를 보여 준다**(숨기지 않는다). `good_for`에 걸리면 앞으로. 아무것도 안 걸리면 중립(명세 순서 유지).
  - 카테고리를 모르면(빈 글) 모두 중립이고 `category_known=False`로 알린다 — 모르는 것을 추천 근거로 삼지 않는다.
  - `good_for`·`avoid_for`는 설명에서 이끌어 낸 **보수적 기본값**이며 법무·시장 검증을 거치지 않았다(I1 한계 그대로).
  - 용도(MAIN/ADDITIONAL/DETAIL)에 쓸 수 없는 컨셉은 처음부터 후보가 아니다(대표는 흰 배경만).
=========================================================
"""

from __future__ import annotations

import re
from typing import Any

from app.domains.media_asset.image_concepts import ImageConcept
from app.domains.media_asset.image_concepts import PURPOSES

MIN_TERM_CHARS = 2
MAX_HINT_CHARS = 60


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _hits(hint: str, terms: tuple[str, ...]) -> list[str]:
    found = []
    for term in terms:
        t = _norm(term)
        if len(t) >= MIN_TERM_CHARS and len(hint) >= MIN_TERM_CHARS and (t in hint or hint in t):
            found.append(term)
    return found


def recommend(category_hint: Any, purpose: str, concepts: list[ImageConcept]) -> dict[str, Any]:
    """반환: {"category_known": bool, "purpose": str, "recommended": [...], "excluded": [...]}. 각 항목 {code, label_ko, score, reasons[], processing_level}."""

    if purpose not in PURPOSES:
        raise ValueError(f"알 수 없는 이미지 용도입니다: {purpose}")
    hint = _norm(str(category_hint or "")[:MAX_HINT_CHARS])
    known = len(hint) >= MIN_TERM_CHARS
    recommended: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for order, concept in enumerate(c for c in concepts if purpose in c.usable_for):
        row = {"code": concept.code, "label_ko": concept.label_ko, "processing_level": concept.processing_level, "score": 0, "reasons": [], "_order": order}
        if known:
            avoid = _hits(hint, concept.avoid_for)
            good = _hits(hint, concept.good_for)
            if avoid:
                row["reasons"] = [f"피해야 할 상품: {', '.join(avoid)}"]
                excluded.append(row)
                continue
            if good:
                row["score"] = 2
                row["reasons"] = [f"어울리는 상품: {', '.join(good)}"]
        recommended.append(row)
    recommended.sort(key=lambda r: (-r["score"], r["_order"]))
    for row in recommended + excluded:
        row.pop("_order", None)
    return {"category_known": known, "purpose": purpose, "recommended": recommended, "excluded": excluded}


__all__ = ["recommend", "MIN_TERM_CHARS", "MAX_HINT_CHARS"]
