"""
=========================================================
Homez OS

File : app/domains/trend_discovery/keyword_rule.py

2026-10-31 — AI 통합 계획 3단계: 상품명에서 **트렌드 검색어**를 뽑는 규칙(AI 없음, 외부 호출 없음).
지금까지 트렌드 갱신은 상품명 전체("백산수 생수 500ml x 20PET")를 그대로 검색어로 보냈다. 검색어 트렌드는 사람이 실제로
치는 짧은 말 기준이라, 용량·수량·괄호·상품코드가 붙은 이름은 대부분 '데이터 없음'으로 끝난다.

규칙은 **잡음만 지운다**: 괄호 안 내용, 상품코드, 용량·수량·묶음, 판촉 문구, 색상, 브랜드(후보에 브랜드가 적혀 있을 때만).
어느 단어가 핵심인지는 고르지 않는다(규칙으로 맞히기 어렵다) — 단어가 많으면 '줄여 주세요'로 알리고, 사람이 확인·수정한다.
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_KEYWORD_CHARS = 40
LONG_WORD_COUNT = 3

_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|（[^）]*）|【[^】]*】|<[^>]*>|\{[^}]*\}")
_CODE_PREFIX = re.compile(r"(?<!\S)[A-Za-z]{0,4}\d{3,}[_\-](?=\S)")
_CODE_TOKEN = re.compile(r"^[A-Za-z]{0,4}[\-_]?\d{4,}[A-Za-z]{0,3}$")
_UNITS = (
    "ml|mℓ|l|ℓ|g|kg|mg|cm|mm|m|oz|매|개입|개|입|팩|p|pet|병|캔|롤|장|켤레|족|세트|set|박스|box|봉|포|ea|구|단|종|부|인용|인치|호|겹|컵|정|캡슐"
)
_MULTI = re.compile(r"(?<![A-Za-z])[x×*]\s*\d+(?:[.,]\d+)?(?![\d.,])\s*(?:" + _UNITS + r")?(?![가-힣A-Za-z])", re.IGNORECASE)
_PLUS = re.compile(r"(?<![\d.])\d+\s*\+\s*\d+(?![\d.])")
_QTY = re.compile(r"(?<![A-Za-z가-힣\d.])\d+(?:[.,]\d+)?(?![\d.,])\s*(?:" + _UNITS + r")?(?![가-힣A-Za-z])", re.IGNORECASE)
_SEPARATORS = re.compile(r"[/,|·_~!?#&]+")
_HAS_WORD = re.compile(r"[0-9A-Za-z가-힣]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

PROMO_WORDS = frozenset({
    "무료배송", "당일발송", "당일출고", "특가", "초특가", "최저가", "대용량", "본품", "정품", "신상", "신상품", "최신형", "증정", "사은품",
})
COLOR_WORDS = frozenset({
    "블랙", "화이트", "그레이", "회색", "네이비", "베이지", "아이보리", "브라운", "카키", "레드", "핑크", "블루", "그린", "옐로우",
    "퍼플", "오렌지", "민트", "차콜", "실버", "골드", "검정", "흰색",
})


@dataclass(frozen=True)
class KeywordSuggestion:
    """규칙이 제안한 검색어. `removed`는 지운 조각(사람이 확인할 수 있게), `needs_shortening`은 단어가 많다는 알림."""

    keyword: str
    removed: tuple[str, ...]
    word_count: int
    needs_shortening: bool

    def as_dict(self) -> dict:

        return {
            "keyword": self.keyword, "removed": list(self.removed),
            "word_count": self.word_count, "needs_shortening": self.needs_shortening,
        }


def _cut(pattern: re.Pattern, text: str, removed: list[str]) -> str:

    for match in pattern.finditer(text):
        piece = match.group(0).strip()
        if piece:
            removed.append(piece)
    return pattern.sub(" ", text)


def extract_search_keyword(product_name: str | None, brand_hint: str | None = None) -> KeywordSuggestion:
    """상품명 → 검색어 제안(결정적). 지울 것이 없으면 상품명을 그대로(공백만 정리) 돌려준다."""

    removed: list[str] = []
    text = _CONTROL.sub(" ", str(product_name or ""))
    text = _cut(_BRACKETS, text, removed)
    text = _cut(_CODE_PREFIX, text, removed)
    text = _cut(_MULTI, text, removed)
    text = _cut(_PLUS, text, removed)
    text = _cut(_QTY, text, removed)
    text = _SEPARATORS.sub(" ", text)

    brand = " ".join(str(brand_hint or "").split()).casefold()
    words: list[str] = []
    seen: set[str] = set()
    for word in text.split():
        folded = word.casefold()
        if not _HAS_WORD.search(word):
            continue
        if word in PROMO_WORDS or word in COLOR_WORDS or _CODE_TOKEN.match(word) or (brand and folded == brand):
            removed.append(word)
            continue
        if folded in seen:
            continue
        seen.add(folded)
        words.append(word)

    needs_shortening = len(words) > LONG_WORD_COUNT
    while words and len(" ".join(words)) > MAX_KEYWORD_CHARS:
        removed.append(words.pop())
        needs_shortening = True
    keyword = " ".join(words)
    return KeywordSuggestion(keyword=keyword, removed=tuple(removed), word_count=len(words), needs_shortening=needs_shortening)


def normalize_user_keyword(value: str | None) -> str:
    """사람이 확인·수정한 검색어를 정리한다. 비었거나 너무 길면 ValueError(자르거나 고쳐 쓰지 않는다)."""

    keyword = " ".join(_CONTROL.sub(" ", str(value or "")).split())
    if not keyword:
        raise ValueError("검색어가 비어 있습니다.")
    if len(keyword) > MAX_KEYWORD_CHARS:
        raise ValueError(f"검색어는 {MAX_KEYWORD_CHARS}자 이하로 줄여 주세요.")
    return keyword


__all__ = [
    "KeywordSuggestion",
    "LONG_WORD_COUNT",
    "MAX_KEYWORD_CHARS",
    "extract_search_keyword",
    "normalize_user_keyword",
]
