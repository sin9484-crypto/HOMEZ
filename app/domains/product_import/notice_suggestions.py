"""
=========================================================
Homez OS

File : app/domains/product_import/notice_suggestions.py

URL 상품 가져오기(2026-10-14, 개선 A9) — 공급처 페이지의 상품정보제공고시 값을 쿠팡 정보고시 입력칸의 "제안"으로 쓴다.

원칙:
  - 제안일 뿐이다. 빈 칸에만 채우고 기존 입력을 덮어쓰지 않으며, "정보고시 확인" 체크는 사용자가 직접 한다.
  - 우리가 파는 상품의 연락처를 공급처 번호로 채우지 않는다 — A/S 책임자·전화번호·연락처류 라벨은 제안하지 않는다.
  - KC 인증정보는 공급처의 KC 항목이 전부 "해당사항없음"일 때만 "해당사항없음"을 제안한다(일부라도 번호가 있으면 사람이 본다).
순수 함수(DB·네트워크 없음).
=========================================================
"""

from __future__ import annotations

import json
import re

NOTICE_EVIDENCE_PREFIX = "URL_IMPORT_NOTICE:"
_MAX_SUMMARY = 2000  # product_candidate_evidences.payload_summary 길이 한도
_MAX_VALUE = 160

_NEVER_SUGGEST = ("전화", "연락", "a/s", "as책임자", "고객센터", "상담")
_SYNONYMS = {
    "출시년월": ("동일모델의출시년월", "출시년월"),
    "제조년월일소비기한또는품질유지기한": ("제조년월일", "제조년월"),
    "제조자수입자": ("제조자수입자", "제조자", "수입자", "제조원", "제조사"),
    "원산지": ("제조국", "원산지"),
    "제조국": ("제조국", "원산지"),
    "품명및모델명": ("품명및모델명", "모델명", "품명"),
    "제품명": ("제품명", "품명및모델명"),
}


def _norm(label: str) -> str:
    return re.sub(r"[\s,/()·\-_:：.]", "", label or "").lower()


def _blocked(label: str) -> bool:
    normalized = _norm(label)
    return any(token in normalized for token in _NEVER_SUGGEST)


def encode_notice_rows(rows: list[tuple[str, str]]) -> str:
    """후보 근거(evidence) 한 건에 담을 문자열. 연락처류는 처음부터 담지 않고, 길이 한도를 넘으면 뒤에서부터 버린다."""

    kept = [
        [label[:60], value[:_MAX_VALUE]] for label, value in rows
        if label and value and not _blocked(label)
    ]
    while kept:
        text = NOTICE_EVIDENCE_PREFIX + json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= _MAX_SUMMARY:
            return text
        kept.pop()
    return NOTICE_EVIDENCE_PREFIX + "[]"


def decode_notice_rows(summary: str | None) -> list[tuple[str, str]]:
    if not summary or not summary.startswith(NOTICE_EVIDENCE_PREFIX):
        return []
    try:
        data = json.loads(summary[len(NOTICE_EVIDENCE_PREFIX):])
    except ValueError:
        return []
    return [(str(a), str(b)) for a, b in data if isinstance(a, str) and isinstance(b, str)]


def suggest_notice_values(rows: list[tuple[str, str]], field_labels: list[str]) -> dict[str, str]:
    """쿠팡 정보고시 칸 라벨 → 제안 값. 못 맞춘 칸은 결과에 넣지 않는다(추측 금지)."""

    by_label = {}
    for label, value in rows:
        if not _blocked(label):
            by_label.setdefault(_norm(label), value)

    suggestions: dict[str, str] = {}
    for field_label in field_labels:
        if _blocked(field_label):
            continue
        key = _norm(field_label)
        if key.startswith("kc인증"):
            kc_values = [v for k, v in by_label.items() if k.startswith("kc인증")]
            if kc_values and all(_norm(v) == "해당사항없음" for v in kc_values):
                suggestions[field_label] = "해당사항없음"
            continue
        for candidate in (key, *_SYNONYMS.get(key, ())):
            value = by_label.get(_norm(candidate))
            if value:
                suggestions[field_label] = value
                break
    return suggestions
