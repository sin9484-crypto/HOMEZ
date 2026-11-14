"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/attribute_extract.py

2026-11-14 — **공급처 글에서 쿠팡 고시 속성 뽑기**(조사 문서 `docs/research/20261109_AI_FOUNDATIONS_AND_GROWTH.md` 3-6, 사용자 지시
"다음 작업 진해해라"). 순수 함수 — DB·네트워크 없음.

무엇을: 사용자가 붙여 넣은 공급처 상세 글·온채널 고시 원문·상품명·옵션에서 제조국·소재·치수·용량·제조자·색상·구성 수량을 뽑아,
쿠팡 정보고시 칸마다 '제안'과 **근거 문장**을 보여 주고 지금 입력값과 대조한다(같음·다름·빈칸·값이 여럿).
원칙:
  - 제안만 한다. 칸에 넣는 것은 사람이 누를 때, 저장은 사람이 할 때다.
  - 근거 없는 값은 없다 — 모든 제안은 원문에서 그대로 잘라 온 근거를 갖는다. AI 제안도 근거가 원문에 실제로 있고 값이 근거 안에 있어야
    받는다(지어낸 값 차단, `validate_ai_attributes`).
  - 연락처류 칸(전화·A/S·고객센터)은 제안하지 않는다(우리가 파는 상품에 공급처 번호를 쓰지 않는다 — `notice_suggestions`와 같은 원칙).
  - 라벨 없는 추측은 용량·구성 수량만, 숫자+단위 모양일 때만, **상품명·옵션에서만** 한다(제조국을 나라 이름만 보고 짐작하지 않는다).
    붙여 넣은 글·설명은 '구성: …'처럼 라벨이 붙은 값만 쓴다 — 긴 글에는 '2개 구매 시 할인' 같은 홍보 숫자가 섞인다.
    '매'(장 수)는 구성 수량이 아니라 내용량이라 세지 않는다(물티슈 100매 10팩 → 10).
  - 구성 수량은 판매 단위 수량(unitCount)과 다르면 '확인 필요'로 알린다 — 첫 온채널 발주 때 '구성 수량 근거'가 막혔던 자리다.
이미지 속 글자는 읽지 않는다(PC에 글자 인식 도구가 없다) — 상세 이미지의 글은 사람이 옮겨 붙인다.
=========================================================
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.domains.product_import.notice_suggestions import _blocked

ORIGIN, MATERIAL, SIZE, VOLUME, MANUFACTURER, COLOR, QUANTITY = "ORIGIN", "MATERIAL", "SIZE", "VOLUME", "MANUFACTURER", "COLOR", "QUANTITY"
KINDS = (ORIGIN, MATERIAL, SIZE, VOLUME, MANUFACTURER, COLOR, QUANTITY)

# 출처(믿을 순서대로 — 공급처가 쓴 글이 먼저, 우리 설명은 마지막)
PASTED, SUPPLIER_NOTICE, TITLE, OPTIONS, DESCRIPTION, AI = "PASTED", "SUPPLIER_NOTICE", "TITLE", "OPTIONS", "DESCRIPTION", "AI"
SOURCES = (PASTED, SUPPLIER_NOTICE, TITLE, OPTIONS, DESCRIPTION, AI)

SAME, DIFFERENT, EMPTY, CONFLICT = "SAME", "DIFFERENT", "EMPTY", "CONFLICT"

MAX_TEXT = 20000
MAX_VALUE = 80
EVIDENCE_PAD = 18

# 글 속 라벨(값 앞에 오는 말)
_TEXT_LABELS = {
    ORIGIN: ("제조국가", "제조국", "원산지", "생산국", "원산국"),
    MATERIAL: ("소재", "재질", "원단", "겉감"),
    SIZE: ("치수", "사이즈", "크기", "규격"),
    VOLUME: ("내용량", "용량", "중량", "무게"),
    MANUFACTURER: ("제조업자", "제조자", "제조사", "제조원", "수입자", "수입원", "수입사"),
    COLOR: ("색상", "컬러", "색깔"),
    QUANTITY: ("구성수량", "구성품", "구성", "입수", "수량"),
}
# 쿠팡 고시 칸 라벨 → 종류(정규화한 라벨에 이 말이 들어 있으면)
_NOTICE_LABELS = (
    (ORIGIN, ("제조국", "원산지")),
    (MATERIAL, ("소재", "재질")),
    (SIZE, ("치수", "크기", "사이즈", "규격")),
    (VOLUME, ("용량", "중량")),
    (MANUFACTURER, ("제조자", "제조업자", "수입자", "제조사")),
    (COLOR, ("색상",)),
    (QUANTITY, ("수량", "구성")),
)

_QTY_ONLY_RE = re.compile(r"^(\d{1,3})\s?(?:개입|개|입|팩|병|캔|롤|봉|박스|세트)?$")
_VOLUME_RE = re.compile(r"(?<![\d.])(\d{1,5}(?:\.\d{1,3})?)\s?(ml|mL|ML|L|ℓ|리터|kg|KG|Kg|g)(?![A-Za-z가-힣])")
_QTY_RE = re.compile(r"(?:[xX×*]\s?(\d{1,3})(?:\s?(?:개입|개|입|팩|병|캔|롤|봉))?(?![\d가-힣])|(?<![\d.])(\d{1,3})\s?(?:개입|입|팩|병|캔|롤|봉|박스|세트)(?![가-힣])|(?<![\d.])(\d{1,3})\s?개(?![월년가-힣]))")


def norm_label(label: str) -> str:
    return re.sub(r"[\s,/()·\-_:：.]", "", label or "").lower()


def norm_value(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def kind_of_notice_label(label: str) -> str | None:
    if _blocked(label):
        return None
    key = norm_label(label)
    for kind, words in _NOTICE_LABELS:
        if any(w in key for w in words):
            return kind
    return None


@dataclass(frozen=True)
class Found:
    kind: str
    value: str
    evidence: str
    source: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value, "evidence": self.evidence, "source": self.source}


def _clean(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t:：-=,.;·|")
    return value[:MAX_VALUE].strip()


def _evidence(text: str, start: int, end: int) -> str:
    a, b = max(0, start - EVIDENCE_PAD), min(len(text), end + EVIDENCE_PAD)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def _labeled(text: str, source: str) -> list[Found]:
    out: list[Found] = []
    for kind, labels in _TEXT_LABELS.items():
        alt = "|".join(sorted(map(re.escape, labels), key=len, reverse=True))
        # 콜론이 있으면 / | 줄바꿈 ; 까지(쉼표는 값 안에 올 수 있다 — "M, L, XL"), 콜론이 없으면 쉼표·마침표에서도 끊는다.
        pattern = re.compile(r"(?<![가-힣])(?:%s)\s*(?:(?P<colon>[:：])\s*(?P<v1>[^/|\n;]{1,%d})|\s(?P<v2>[^,./|\n;]{1,%d}))" % (alt, MAX_VALUE, MAX_VALUE))
        for m in pattern.finditer(text):
            raw = m.group("v1") if m.group("colon") else m.group("v2")
            value = _clean(raw or "")
            if not value or value in ("및", "등"):
                continue
            if m.group("colon") is None and len(value) > 30:
                continue  # 콜론 없는 긴 글은 문장의 일부일 가능성이 크다
            if kind == QUANTITY:
                only = _QTY_ONLY_RE.match(value)
                if only:
                    value = str(int(only.group(1)))  # '6병'·'6' → '6'(같은 수량을 둘로 보지 않는다)
            out.append(Found(kind, value, _evidence(text, m.start(), m.end()), source))
    return out


def _unlabeled(text: str, source: str) -> list[Found]:
    out: list[Found] = []
    for m in _VOLUME_RE.finditer(text):
        out.append(Found(VOLUME, f"{m.group(1)}{m.group(2)}", _evidence(text, m.start(), m.end()), source))
    for m in _QTY_RE.finditer(text):
        n = next(g for g in m.groups() if g)
        if 1 <= int(n) <= 500:
            out.append(Found(QUANTITY, str(int(n)), _evidence(text, m.start(), m.end()), source))
    return out


def extract(*, pasted: str = "", supplier_notice: list[tuple[str, str]] | None = None, title: str = "",
            options: list[str] | None = None, description: str = "") -> list[Found]:
    """출처마다 뽑아 (종류, 정규화 값, 출처)로 겹침을 지운다. 순서는 출처 순서 그대로."""

    found: list[Found] = []
    pasted = str(pasted or "")[:MAX_TEXT]
    if pasted.strip():
        found += _labeled(pasted, PASTED)
    for label, value in (supplier_notice or []):
        kind = kind_of_notice_label(str(label))
        value = _clean(str(value or ""))
        if kind and value:
            found.append(Found(kind, value, f"{label}: {value}"[:200], SUPPLIER_NOTICE))
    if str(title or "").strip():
        found += _labeled(title, TITLE) + _unlabeled(title, TITLE)
    for text in (options or []):
        if str(text or "").strip():
            found += _labeled(str(text), OPTIONS) + _unlabeled(str(text), OPTIONS)
    if str(description or "").strip():
        found += _labeled(str(description)[:MAX_TEXT], DESCRIPTION)
    seen: set[tuple[str, str, str]] = set()
    unique: list[Found] = []
    for f in found:
        key = (f.kind, norm_value(f.value), f.source)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def compare(found: list[Found], current: dict[str, str]) -> list[dict]:
    """고시 칸(라벨 → 지금 값)마다 제안을 붙인다. 같은 종류 값이 여럿이면 CONFLICT(사람이 고른다)."""

    rows: list[dict] = []
    for label, now in current.items():
        kind = kind_of_notice_label(label)
        if kind is None:
            continue
        hits = [f for f in found if f.kind == kind]
        if not hits:
            continue
        values: dict[str, list[Found]] = {}
        for f in hits:
            values.setdefault(norm_value(f.value), []).append(f)
        now_text = str(now or "").strip()
        if len(values) > 1:
            state = CONFLICT
        elif not now_text:
            state = EMPTY
        else:
            state = SAME if norm_value(now_text) in values else DIFFERENT
        rows.append({
            "label": label, "kind": kind, "current": now_text, "state": state,
            "suggestions": [{"value": fs[0].value, "sources": sorted({f.source for f in fs}, key=SOURCES.index), "evidence": fs[0].evidence}
                            for fs in values.values()],
        })
    return rows


def quantity_check(found: list[Found], unit_counts: list[int]) -> dict | None:
    """글에 적힌 구성 수량과 판매 단위 수량이 다르면 알린다(어느 쪽이 맞는지는 사람이 본다)."""

    quantities = sorted({int(f.value) for f in found if f.kind == QUANTITY and f.value.isdigit() and f.source != DESCRIPTION})
    counts = sorted({int(c) for c in unit_counts if isinstance(c, int) and not isinstance(c, bool) and c > 0})
    if not quantities or not counts:
        return None
    if set(quantities) <= set(counts):
        return None
    return {"text_quantities": quantities, "unit_counts": counts,
            "evidence": [f.evidence for f in found if f.kind == QUANTITY and f.source != DESCRIPTION][:3]}


# ---------------------------------------------------------------------------
# AI로 더 뽑기(선택) — 응답 검증. 근거가 보낸 글에 그대로 있고 값이 근거 안에 있을 때만 받는다.
# ---------------------------------------------------------------------------

AI_SYSTEM = (
    "너는 한국 온라인 쇼핑몰 상품 정보에서 표시 속성을 옮겨 적는 도우미다. 주어진 글에 적혀 있는 것만 옮긴다. 추측하거나 보태지 않는다.\n"
    "종류: ORIGIN(제조국·원산지), MATERIAL(소재·재질), SIZE(치수·크기), VOLUME(용량·중량), MANUFACTURER(제조자·수입자), COLOR(색상), QUANTITY(구성 수량, 숫자만).\n"
    "각 항목마다 value와, 그 값이 들어 있는 원문 그대로의 짧은 구절 quote(200자 이내)를 준다. 글에 없으면 그 종류는 빼라.\n"
    '응답은 JSON 하나만: {"attributes": [{"kind": "ORIGIN", "value": "중국", "quote": "제조국: 중국"}]}'
)
MAX_AI_ITEMS = 20


def ai_user_prompt(*, pasted: str, title: str, options: list[str]) -> tuple[str, str]:
    """(프롬프트, 근거 대조에 쓸 원문). 보내는 것은 붙여 넣은 글·상품명·옵션뿐(연락처 칸·판매자 정보 없음)."""

    body = {"product_name": str(title or "")[:200], "options": [str(o)[:80] for o in (options or [])][:30], "text": str(pasted or "")[:6000]}
    source = "\n".join([body["product_name"], *body["options"], body["text"]])
    return json.dumps(body, ensure_ascii=False), source


def validate_ai_attributes(raw: str, source_text: str) -> tuple[list[Found], list[dict]]:
    """(받은 것, 버린 것과 이유). JSON이 아니면 모두 버린다."""

    text = str(raw or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except (ValueError, TypeError):
        return [], [{"reason": "NOT_JSON"}]
    items = data.get("attributes") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], [{"reason": "NOT_JSON"}]
    source_norm = norm_value(source_text)
    kept: list[Found] = []
    refused: list[dict] = []
    for item in items[:MAX_AI_ITEMS]:
        if not isinstance(item, dict):
            refused.append({"reason": "BAD_ITEM"})
            continue
        kind, value, quote = item.get("kind"), item.get("value"), item.get("quote")
        if kind not in KINDS or not isinstance(value, str) or not isinstance(quote, str):
            refused.append({"reason": "BAD_ITEM", "kind": str(kind)[:20]})
            continue
        value, quote = _clean(value), re.sub(r"\s+", " ", quote).strip()[:200]
        if not value or not quote:
            refused.append({"reason": "BAD_ITEM", "kind": kind})
        elif norm_value(quote) not in source_norm:
            refused.append({"reason": "QUOTE_NOT_IN_TEXT", "kind": kind, "value": value})
        elif norm_value(value) not in norm_value(quote):
            refused.append({"reason": "VALUE_NOT_IN_QUOTE", "kind": kind, "value": value})
        elif kind == QUANTITY and not value.isdigit():
            refused.append({"reason": "QUANTITY_NOT_NUMBER", "kind": kind, "value": value})
        else:
            kept.append(Found(kind, value, quote, AI))
    return kept, refused


__all__ = [
    "AI_SYSTEM", "CONFLICT", "DIFFERENT", "EMPTY", "Found", "KINDS", "SAME", "SOURCES", "ai_user_prompt", "compare", "extract",
    "kind_of_notice_label", "quantity_check", "validate_ai_attributes",
]
