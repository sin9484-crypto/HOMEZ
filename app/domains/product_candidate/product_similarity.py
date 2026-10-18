"""
=========================================================
Homez OS

File : app/domains/product_candidate/product_similarity.py

2026-10-18 — 상품 서칭 AI S1: 상품명을 **정규화**하고 두 상품이 **같은 상품인지/구성만 다른 상품인지/비슷할 뿐인지** 규칙으로 가려내 묶는다. 순수 함수, AI 없음.

공급처마다 같은 상품을 다른 이름으로 올린다("[무료배송] 러닝 무릎보호대 2P 블랙" / "블랙 무릎 보호대 러닝용 2개입"). 이름만 보고 같은 상품을 여러 번
등록하거나, 용량·수량만 다른 상품을 같은 것으로 합치는 실수를 막기 위한 **판단 근거**를 만든다 — 합치기·삭제·등록은 하지 않고 사용자에게 알려 줄 뿐이다.

정규화:
  - 구성 수량(개입·P·세트·매·롤·켤레…), 용량(ml·L→ml), 무게(g·kg→g), 길이(mm·cm·m→mm), 사이즈, 색상, 모델 번호를 이름에서 뽑아 **규격(specs)** 으로 분리한다.
  - 광고성 말(무료배송·특가·당일발송…)과 규격·색상·사이즈·모델 번호를 지운 나머지를 **핵심 이름(core)** 으로 삼는다(토큰 정렬 + 공백 제거).
관계(`relation`):
  - DUPLICATE : 핵심 이름이 거의 같고 규격이 충돌하지 않으며 같은 상품이라고 볼 근거(같은 규격·같은 모델 번호·아주 높은 이름 일치)가 있다.
  - VARIANT   : 핵심 이름은 거의 같지만 규격(구성 수량·용량·무게·길이·색상·사이즈·모델 번호)이 **다르다** — 같은 상품군의 다른 구성이다.
  - SIMILAR   : 이름이 비슷하지만 같은 상품이라고 단정할 근거가 부족하다(규격 정보 없음 등) — 사람이 봐야 한다.
  - DIFFERENT : 그 밖.
한 쪽에만 있는 규격은 충돌이 아니다(모르는 것을 다르다고 단정하지 않는다). 모든 판단에는 이유 코드가 붙는다.
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any

DUPLICATE = "DUPLICATE"
VARIANT = "VARIANT"
SIMILAR = "SIMILAR"
DIFFERENT = "DIFFERENT"
RELATION_RANK = {DUPLICATE: 0, VARIANT: 1, SIMILAR: 2, DIFFERENT: 3}

CORE_NEAR_SAME = 0.85      # 핵심 이름이 거의 같다고 보는 기준
CORE_VERY_HIGH = 0.97      # 규격 정보가 없어도 같은 상품으로 볼 만큼 높은 일치(사실상 같은 핵심 이름 — 낱말 하나 차이는 해당하지 않는다)
CORE_SIMILAR = 0.60        # 비슷하다고 보는 기준
SAME_MODEL_FLOOR = 0.90    # 같은 모델 번호면 핵심 이름 점수를 이 값 이상으로 본다
MAX_GROUP_ITEMS = 400      # 묶기 대상 상한(쌍 비교는 n²)

NOISE_WORDS = frozenset({
    "무료배송", "무배", "당일발송", "당일출고", "빠른배송", "특가", "할인", "행사", "이벤트", "인기", "추천", "신상", "신상품", "베스트", "핫딜",
    "도매", "사은품", "공급", "정품", "무료", "국내", "해외",
})
COLOR_WORDS = frozenset({
    "블랙", "화이트", "그레이", "네이비", "베이지", "핑크", "블루", "레드", "그린", "옐로우", "브라운", "아이보리", "카키", "퍼플", "오렌지", "실버", "골드",
    "검정", "흰색", "회색", "남색", "파랑", "빨강", "초록", "노랑", "민트", "와인", "차콜",
})
SIZE_WORDS = frozenset({"s", "m", "l", "xl", "xxl", "xxxl", "free", "프리"})

_COUNT_UNITS = (
    # 개입·개·세트·팩·P는 모두 "낱개 수량"으로 본다("2P"와 "2개입"은 같은 구성). 매·롤·켤레·병·캔·박스는 단위가 달라 따로 둔다.
    ("piece", r"개입|개들이|개|ea|pcs|pc|piece|pieces|세트|set|팩|pack"),
    ("sheet", r"매|장|sheet|sheets"),
    ("roll", r"롤|roll"),
    ("pair", r"켤레|족|pair"),
    ("bottle", r"병|bottle"),
    ("can", r"캔"),
    ("box", r"박스|box"),
)
_COUNT_PATTERNS = [(unit, re.compile(rf"(\d+)\s*(?:{pattern})(?![a-z가-힣])")) for unit, pattern in _COUNT_UNITS]
_COUNT_P = re.compile(r"(?<![a-z0-9])(\d+)\s*p(?![a-z])")        # 2P, 3p (팩)
_VOLUME = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|cc|l|리터)(?![a-z])")
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|킬로그램|그램|킬로)(?![a-z])")
_LENGTH = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm|m)(?![a-z])")
_BRACKETS = re.compile(r"[\[【\(（][^\]】\)）]*[\]】\)）]")
_MODEL = re.compile(r"(?<![a-z0-9])(?=[a-z0-9-]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9][a-z0-9-]{3,}(?![a-z0-9])")
_SPLIT = re.compile(r"[^0-9a-z가-힣]+")


@dataclass(frozen=True)
class NormalizedProduct:
    core: str                      # 핵심 이름(정렬한 토큰을 공백 없이 이은 문자열)
    tokens: tuple[str, ...]
    specs: dict[str, Any]          # count:<단위>, volume_ml, weight_g, length_mm, size(목록), colors(목록),
    models: frozenset[str]

    def to_dict(self) -> dict[str, Any]:
        return {"core": self.core, "tokens": list(self.tokens), "specs": self.specs, "models": sorted(self.models)}


def _number(text: str) -> float:
    return float(text)


def _bracket_text(match: re.Match) -> str:
    """괄호 안 글은 광고성 말이 많아 버리되, 색상·사이즈가 들어 있으면("(블랙)", "[XL]") 그 글은 살린다."""

    inner = match.group(0)[1:-1]
    words = set(_SPLIT.split(inner))
    return f" {inner} " if words & (COLOR_WORDS | SIZE_WORDS) else " "


def normalize(name: str | None, extra: str | None = None) -> NormalizedProduct:
    """상품명(과 선택 보조 글: 옵션 문구 등)을 핵심 이름 + 규격으로 나눈다. `extra`는 규격 추출에만 쓰고 핵심 이름에는 넣지 않는다."""

    # " | 사이트이름" 꼬리는 상품명이 아니라 페이지 제목에 붙은 사이트 이름이라 버린다(파이프는 이름 안에서 거의 쓰이지 않는다).
    text = re.split(r"\s\|\s", str(name or ""))[0].lower()
    specs: dict[str, Any] = {}

    def take(regex: re.Pattern, source: str, handler) -> str:
        def repl(match: re.Match) -> str:
            handler(match)
            return " "
        return regex.sub(repl, source)

    def collect(source: str) -> str:
        def add_count(unit):
            def handler(m):
                specs.setdefault(f"count:{unit}", int(m.group(1)))
            return handler

        for unit, regex in _COUNT_PATTERNS:
            source = take(regex, source, add_count(unit))
        source = take(_COUNT_P, source, add_count("piece"))

        def volume(m):
            value, unit = _number(m.group(1)), m.group(2)
            specs.setdefault("volume_ml", round(value * (1000 if unit in ("l", "리터") else 1), 3))

        def weight(m):
            value, unit = _number(m.group(1)), m.group(2)
            specs.setdefault("weight_g", round(value * (1000 if unit in ("kg", "킬로그램", "킬로") else 1), 3))

        def length(m):
            value, unit = _number(m.group(1)), m.group(2)
            specs.setdefault("length_mm", round(value * {"mm": 1, "cm": 10, "m": 1000}[unit], 3))

        source = take(_VOLUME, source, volume)
        source = take(_WEIGHT, source, weight)
        source = take(_LENGTH, source, length)
        return source

    # 1) 괄호 안 글도 규격을 담는 경우가 많으니 규격 추출은 괄호째로 먼저 한다.
    core_text = collect(text)
    if extra:
        collect(str(extra).lower())  # 보조 글은 규격만 얻는다(반환 글은 쓰지 않는다)
    core_text = _BRACKETS.sub(_bracket_text, core_text)

    models = frozenset(m.group(0) for m in _MODEL.finditer(core_text))
    for model in models:
        core_text = core_text.replace(model, " ")

    tokens, colors, sizes = [], set(), set()
    for token in _SPLIT.split(core_text):
        if not token:
            continue
        if token in SIZE_WORDS:
            sizes.add(token)
        elif token in COLOR_WORDS:
            colors.add(token)
        elif token in NOISE_WORDS or len(token) < 2 or token.isdigit():
            continue
        else:
            tokens.append(token)
    if colors:
        specs["colors"] = sorted(colors)
    if sizes:
        specs["size"] = sorted(sizes)
    ordered = tuple(sorted(set(tokens)))
    return NormalizedProduct(core="".join(ordered), tokens=ordered, specs=specs, models=models)


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def core_score(a: NormalizedProduct, b: NormalizedProduct) -> float:
    """핵심 이름 일치 정도(0~1): 글자 쌍(bigram) Dice와 토큰 Jaccard 중 큰 값. 띄어쓰기·어순 차이에 강하다."""

    if not a.core or not b.core:
        return 0.0
    if a.core == b.core:
        return 1.0
    ba, bb = _bigrams(a.core), _bigrams(b.core)
    dice = (2 * len(ba & bb) / (len(ba) + len(bb))) if ba and bb else 0.0
    ta, tb = set(a.tokens), set(b.tokens)
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return max(dice, jaccard)


def compare_specs(a: NormalizedProduct, b: NormalizedProduct) -> tuple[list[str], list[str]]:
    """(같은 규격 이름 목록, 충돌하는 규격 이름 목록). 한 쪽에만 있는 규격은 어느 쪽에도 넣지 않는다."""

    same, conflicts = [], []
    for key in sorted(set(a.specs) | set(b.specs)):
        if key not in a.specs or key not in b.specs:
            continue
        va, vb = a.specs[key], b.specs[key]
        if key in ("colors", "size"):
            if set(va) & set(vb):
                same.append(key)
            else:
                conflicts.append(key)  # 겹치는 색·사이즈가 하나도 없을 때만 충돌
        elif va == vb:
            same.append(key)
        else:
            conflicts.append(key)
    if a.models and b.models:
        (same if a.models & b.models else conflicts).append("model")
    return same, conflicts


def compare(a: NormalizedProduct, b: NormalizedProduct) -> dict[str, Any]:
    """두 상품의 관계와 근거. 근거 코드: NAME_NEAR_SAME·NAME_PARTLY_SAME·SAME_MODEL·SAME_SPECS·NO_SPEC_INFO·SPEC_DIFFERS:<규격>."""

    score = core_score(a, b)
    same, conflicts = compare_specs(a, b)
    shared_model = bool(a.models and b.models and a.models & b.models)
    if shared_model:
        score = max(score, SAME_MODEL_FLOOR)
    reasons: list[str] = []
    if score >= CORE_NEAR_SAME:
        reasons.append("NAME_NEAR_SAME")
        if shared_model:
            reasons.append("SAME_MODEL")
        if conflicts:
            relation = VARIANT
            reasons += [f"SPEC_DIFFERS:{key}" for key in conflicts]
        else:
            spec_same = [key for key in same if key != "model"]
            if spec_same:
                reasons.append("SAME_SPECS")
            if shared_model or spec_same or score >= CORE_VERY_HIGH:
                relation = DUPLICATE
            else:
                relation = SIMILAR
                reasons.append("NO_SPEC_INFO")
    elif score >= CORE_SIMILAR:
        relation = SIMILAR
        reasons.append("NAME_PARTLY_SAME")
        reasons += [f"SPEC_DIFFERS:{key}" for key in conflicts]
    else:
        relation, reasons = DIFFERENT, []
    return {"relation": relation, "core_score": round(score, 2), "reasons": reasons, "same_specs": same, "conflicts": conflicts}


class _Union:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def group_products(items: list[tuple[Any, NormalizedProduct]]) -> list[dict[str, Any]]:
    """여러 상품을 상품군(family)으로 묶는다. DUPLICATE·VARIANT 관계로 이어진 것끼리 한 군이고, 군 안에서 DUPLICATE로 이어진 것은 `duplicate_sets`로 따로 보인다.
    SIMILAR 관계는 군을 합치지 않고 `similar_pairs`로만 알린다. 대상이 MAX_GROUP_ITEMS를 넘으면 앞쪽(먼저 들어온 것)만 비교한다."""

    items = items[:MAX_GROUP_ITEMS]
    ids = [item_id for item_id, _ in items]
    products = dict(items)
    family, dup = _Union(ids), _Union(ids)
    pairs: list[dict[str, Any]] = []
    for (id_a, na), (id_b, nb) in combinations(items, 2):
        result = compare(na, nb)
        if result["relation"] == DIFFERENT:
            continue
        pairs.append({"a": id_a, "b": id_b, **result})
        if result["relation"] in (DUPLICATE, VARIANT):
            family.union(id_a, id_b)
        if result["relation"] == DUPLICATE:
            dup.union(id_a, id_b)
    by_root: dict[Any, list[Any]] = {}
    for item_id in ids:
        by_root.setdefault(family.find(item_id), []).append(item_id)
    out = []
    similar_only = [p for p in pairs if p["relation"] == SIMILAR]
    for members in by_root.values():
        if len(members) < 2:
            continue
        sets: dict[Any, list[Any]] = {}
        for member in members:
            sets.setdefault(dup.find(member), []).append(member)
        out.append({
            "members": members,
            "duplicate_sets": [s for s in sets.values() if len(s) > 1],
            "pairs": [p for p in pairs if p["a"] in members and p["b"] in members and p["relation"] in (DUPLICATE, VARIANT)],
            "specs": {member: products[member].specs for member in members},
        })
    if similar_only:
        grouped = {m for g in out for m in g["members"]}
        extra = [p for p in similar_only if not (p["a"] in grouped and p["b"] in grouped)]
        if extra:
            out.append({"members": [], "duplicate_sets": [], "pairs": [], "specs": {}, "similar_pairs": extra})
    out.sort(key=lambda g: (-len(g["members"]), g["members"][:1]))
    return out
