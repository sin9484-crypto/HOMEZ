"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_claim_check.py

2026-11-09 — 상세·상품명 '주장 점검기'(조사 문서 `docs/research/20261109_AI_FOUNDATIONS_AND_GROWTH.md` 3-2, 사용자 승인 "2번 진행").
AI가 쓴 글이든 사람이 쓴 글이든 **위저드에 저장된 상품명·검색어·상세 설명**을 같은 규칙으로 본다. 순수 함수 — DB·네트워크·AI 없음.

기준:
  - **경고만 한다(차단하지 않는다).** 표시광고법은 AI를 써도 거짓·과장·기만 기준이 같다(조사 1-10). 단어가 있다고 곧 위반은 아니므로
    사람이 근거(시험 성적서·인증서·공급처 자료)를 확인하게 알려 줄 뿐이다.
  - **표현 목록은 초안이다**(`RULES_CONFIRMED = False`). 사용자가 확정하면 이 값과 목록을 함께 바꾼다. 확정 전이라는 점을 화면 문구에도 적는다.
  - 상품명의 의료·효능 단어는 기존 검사(`listing_wizard_precheck.CLAIM_WORDS` → `PRODUCT_NAME_CLAIM_WORDS`)가 이미 경고하므로 여기서 다시 세지 않는다.
  - 상세 설명의 숫자 중 공급처 자료(상품명·옵션·고시·공급처 고시 근거)에 없는 것은 '근거 확인'으로 알린다. 한 자리 숫자는 세지 않는다
    ("2가지 색"처럼 흔해 경고가 너무 많아진다 — 한계로 기록).
  - 같은 입력이면 항상 같은 결과, 단어는 목록 순서대로 한 번씩만 낸다.
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from app.domains.marketplace_listing.listing_wizard_precheck import CLAIM_WORDS

RULES_VERSION = "listing_claims.draft-20261109"
RULES_CONFIRMED = False

# 의료·효능 — 기존 CLAIM_WORDS(쿠팡 반려 사례 기반)에 위생·건강 표현을 더했다. 위생용품(생리팬티 등)은 항균·살균 표현의 근거가 특히 필요하다.
MEDICAL_WORDS: tuple[str, ...] = CLAIM_WORDS + (
    "살균", "항균", "멸균", "항바이러스", "면역", "해독", "디톡스", "혈액순환", "염증", "소염", "아토피", "생리통", "탈모", "약효",
)
# 절대·최상급 — 객관적 근거 없이 쓰면 과장 광고로 볼 수 있다.
ABSOLUTE_WORDS: tuple[str, ...] = (
    "100%", "완벽", "최고", "최초", "유일", "1위", "무조건", "평생", "영구", "부작용 없", "부작용없",
)
# 인증·시험·성분 — 인증서·시험 성적서가 있어야 쓸 수 있는 표현.
CERTIFICATION_WORDS: tuple[str, ...] = (
    "인증", "특허", "임상", "식약처", "FDA", "KC", "피부과", "무독성", "무해", "친환경", "유기농", "천연", "무형광", "저자극",
)

CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("MEDICAL", MEDICAL_WORDS),
    ("ABSOLUTE", ABSOLUTE_WORDS),
    ("CERTIFICATION", CERTIFICATION_WORDS),
)
FIELDS = ("product_name", "keywords", "description")

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass
class ClaimCheckResult:
    # 분류 → 단어 목록(목록 순서, 중복 없음), 분류 → 단어가 나온 필드
    words: dict[str, list[str]] = field(default_factory=dict)
    fields: dict[str, list[str]] = field(default_factory=dict)
    unsourced_numbers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules_version": RULES_VERSION, "rules_confirmed": RULES_CONFIRMED,
            "words": self.words, "fields": self.fields, "unsourced_numbers": self.unsourced_numbers,
        }


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value if v is not None)
    return "" if value is None else str(value)


def _numbers(text: str) -> list[str]:
    out: list[str] = []
    for raw in _NUMBER.findall(text or ""):
        value = raw.replace(",", "")
        if len(value.replace(".", "")) < 2 or value in out:
            continue
        out.append(value)
    return out


def check_listing_text(product_name: Any, keywords: Any, description: Any, source_text: str = "") -> ClaimCheckResult:
    """상품명·검색어·상세 설명에서 근거 확인이 필요한 표현과, 상세 설명의 근거 없는 숫자를 찾는다."""

    texts = {"product_name": _text(product_name), "keywords": _text(keywords), "description": _text(description)}
    result = ClaimCheckResult()
    for category, words in CATEGORIES:
        found: list[str] = []
        where: list[str] = []
        for word in words:
            for name in FIELDS:
                if category == "MEDICAL" and name == "product_name" and word in CLAIM_WORDS:
                    continue   # 상품명의 기존 의료·효능 단어는 PRODUCT_NAME_CLAIM_WORDS가 이미 경고한다
                if word and word in texts[name]:
                    if word not in found:
                        found.append(word)
                    if name not in where:
                        where.append(name)
        if found:
            result.words[category] = found
            result.fields[category] = [n for n in FIELDS if n in where]
    known = set(_numbers(source_text) + _numbers(texts["product_name"]))
    result.unsourced_numbers = [n for n in _numbers(texts["description"]) if n not in known][:10]
    return result


__all__ = [
    "ABSOLUTE_WORDS", "CATEGORIES", "CERTIFICATION_WORDS", "MEDICAL_WORDS", "RULES_CONFIRMED", "RULES_VERSION",
    "ClaimCheckResult", "check_listing_text",
]
