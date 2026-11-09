"""
=========================================================
Homez OS

File : app/domains/ai_learning/draft_outcome_diff.py

2026-11-09 — AI 초안 평가 고리(조사 문서 `docs/research/20261109_AI_FOUNDATIONS_AND_GROWTH.md` 3-1): **AI 초안과 사람이 실제로 쓴 글의 차이**를
숫자로 만드는 순수 함수. DB·네트워크·AI 없음.

기준:
  - "잘했다/못했다"를 판정하지 않는다. 바뀐 정도(바뀐 비율·더한 글자·지운 글자·지운 문장·더한 문장)만 센다.
    고친 비율이 낮다고 AI가 잘한 것은 아니다(사람이 대충 확인했을 수도 있다) — 사람이 기록을 읽고 문제를 분류하는 데 쓰는 자료다.
  - 저장 전에 개인정보로 보이는 것을 가린다(`mask`). 가리기 규칙은 고객 응대 초안이 AI에 보내기 전에 쓰는 것과 같다
    (`app/domains/order/cs_reply_draft.py::mask_personal_info` — 새 규칙을 만들지 않는다).
  - 같은 입력이면 항상 같은 결과다.
=========================================================
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# 필드별 최대 저장 길이(글자). 위저드 상세 설명 2,000자·답변 1,000자보다 넉넉하게 둔다.
MAX_FIELD_CHARS = 4000

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")


def mask(text: Any) -> str:
    """개인정보로 보이는 것(주소·URL·이메일·전화·긴 숫자·라벨 뒤 이름)을 가린다. 문자열이 아니면 빈 글."""

    from app.domains.order.cs_reply_draft import mask_personal_info

    if not isinstance(text, str):
        return ""
    return mask_personal_info(text).text[:MAX_FIELD_CHARS]


def field_text(value: Any) -> str:
    """필드 값을 비교용 글로 바꾼다. 목록(검색어)은 ', '로 잇는다. 앞뒤 공백·연속 공백은 하나로 줄인다."""

    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    if value is None:
        return ""
    return re.sub(r"[ \t]+", " ", str(value)).strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def field_diff(draft: Any, final: Any) -> dict[str, Any]:
    """초안 → 최종의 차이. `changed_percent`는 0(같음)~100(전부 다름) 정수."""

    a, b = field_text(draft), field_text(final)
    if a == b:
        return {"same": True, "changed_percent": 0, "added_chars": 0, "removed_chars": 0, "removed_sentences": [], "added_sentences": []}
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    changed = round((1 - matcher.ratio()) * 100)
    sa, sb = _sentences(a), _sentences(b)
    return {
        "same": False,
        "changed_percent": max(1, min(100, changed)),   # 다르면 최소 1 — 반올림으로 0이 되어 '같음'처럼 보이지 않게 한다
        "added_chars": added,
        "removed_chars": removed,
        "removed_sentences": [s for s in sa if s not in sb][:10],
        "added_sentences": [s for s in sb if s not in sa][:10],
    }


def compare(draft_fields: dict[str, Any], final_fields: dict[str, Any] | None) -> dict[str, Any]:
    """필드별 차이와 전체 요약. 최종이 없으면(아직 보내지 않음) 비교하지 않는다."""

    if final_fields is None:
        return {"compared": False, "fields": {}, "changed_percent": None}
    fields = {key: field_diff(draft_fields.get(key), final_fields.get(key)) for key in draft_fields}
    total_draft = sum(len(field_text(draft_fields.get(k))) for k in draft_fields) or 1
    # 전체 바뀐 비율은 필드 길이로 가중한다(긴 상세 설명이 짧은 상품명보다 더 반영된다).
    weighted = sum(fields[k]["changed_percent"] * len(field_text(draft_fields.get(k))) for k in fields) / total_draft
    any_changed = any(not f["same"] for f in fields.values())
    overall = round(weighted)
    if any_changed:
        overall = max(1, overall)
    return {"compared": True, "fields": fields, "changed_percent": overall}


__all__ = ["MAX_FIELD_CHARS", "compare", "field_diff", "field_text", "mask"]
