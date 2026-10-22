"""
=========================================================
Homez OS

File : app/domains/channel_policy/policy_change_watch.py

2026-10-23 — 약관·정책 AI T3: **플랫폼 정책 변경 감시(수동 스냅샷 비교)** — 순수 함수(DB·네트워크·AI 없음, 저장 없음).
사용자가 공식 공지·정책 페이지에서 직접 복사한 이전 글과 이번 글을 넣으면 ① 같은지·달라졌는지를 해시로 정하고 ② 달라진 줄(추가·삭제)을 보여 주고
③ 달라진 줄에 들어 있는 낱말로 **영향받을 수 있는 규칙 후보**(채널 정책 규칙 카탈로그의 규칙 코드, 또는 카탈로그에 없는 영역)를 알려 준다.

기준:
  - **페이지를 이 코드가 가져오지 않는다.** 공식 공지를 자동으로 읽는 것이 플랫폼 약관상 허용되는지 확인하지 못했다(T0 미확인) — 그래서 사람이 복사해 넣는 방식만 둔다.
  - 이전 글·이번 글 중 하나라도 비어 있으면 **비교하지 못함(UNKNOWN)** 이다 — "변경 없음"으로 쓰지 않는다.
  - 영향 규칙은 **후보**일 뿐이다(낱말 일치). 규칙 카탈로그는 자동으로 바뀌지 않고, 사람이 공식 문서를 읽고 판단해 반영한다. 낱말이 안 걸렸어도 영향이 없다는 뜻이 아니다.
  - 정규화는 공백·줄바꿈·전각 문자만 맞춘다(내용은 바꾸지 않는다). 글이 너무 길면 거절한다(잘라서 비교하지 않는다 — 잘린 비교는 틀릴 수 있다).
  - 외부에서 가져온 글은 **데이터**다 — 이 모듈은 글의 내용을 실행하거나 따르지 않고 문자열로만 비교한다.
=========================================================
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from app.core.exceptions import BadRequestException

MAX_TEXT_CHARS = 200_000
MAX_LINES_SHOWN = 60
MAX_LINE_CHARS = 300
PLATFORMS = ("COUPANG", "NAVER_SMARTSTORE")

UNCHANGED, CHANGED, UNKNOWN = "UNCHANGED", "CHANGED", "UNKNOWN"

# 낱말 → 영향 후보. rule_code는 channel_policy/rule_catalog.py 의 규칙 코드와 같아야 한다(시험이 대조한다). rule_code=None은 규칙 카탈로그에 아직 없는 영역이다.
IMPACT_KEYWORDS: tuple[dict[str, Any], ...] = (
    {"area": "판매 금지·제한 품목", "rule_codes": ("PROHIBITED_CATEGORY_ABSOLUTE", "NAVER_PROHIBITED_CATEGORY_PLACEHOLDER"),
     "words": ("판매 불가", "판매불가", "판매 금지", "판매금지", "판매할 수 없", "판매 할 수 없", "취급 금지", "금지 품목", "제한 품목", "취급주의")},
    {"area": "원산지·제조국", "rule_codes": ("PROHIBITED_ORIGIN_COUNTRY", "RESTRICTED_ORIGIN_COUNTRY_EVIDENCE"), "words": ("원산지", "제조국", "수입 제한")},
    {"area": "인증·허가·신고", "rule_codes": ("RESTRICTED_CATEGORY_CERTIFICATION_REQUIRED",), "words": ("인증", "허가", "신고", "kc", "전기용품", "어린이제품")},
    {"area": "성인 상품 표시", "rule_codes": ("ADULT_ONLY_LABEL_REQUIRED",), "words": ("성인", "19세", "청소년 유해")},
    {"area": "상품 식별번호(바코드·품번)", "rule_codes": ("PRODUCT_IDENTIFICATION_REQUIRED",), "words": ("바코드", "품번", "식별번호", "모델명", "gtin")},
    {"area": "상품정보 고시", "rule_codes": ("CATEGORY_NOTICE_INFO_REQUIRED", "NAVER_CATEGORY_NOTICE_INFO_PLACEHOLDER"), "words": ("고시정보", "상품정보제공고시", "상품 정보 고시")},
    {"area": "배송 방식", "rule_codes": ("NAVER_DELIVERY_TYPE_REQUIRED",), "words": ("배송 유형", "배송유형", "delivery")},
    {"area": "이미지 규정(대표·AI 생성 이미지·워터마크)", "rule_codes": (), "words": ("이미지", "대표 이미지", "워터마크", "ai 생성", "인공지능")},
    {"area": "수수료·정산", "rule_codes": (), "words": ("수수료", "정산", "판매 수수료", "정산 주기")},
    {"area": "반품·환불·취소", "rule_codes": (), "words": ("반품", "환불", "취소", "교환")},
    {"area": "브랜드·지식재산", "rule_codes": (), "words": ("브랜드", "상표", "지식재산", "위조", "정품")},
    {"area": "배송 기한·지연", "rule_codes": (), "words": ("배송 기한", "발송 기한", "출고", "지연")},
)


def normalize_lines(text: str) -> list[str]:
    """전각 문자·공백을 맞추고 빈 줄을 뺀 줄 목록. 내용은 바꾸지 않는다."""

    lines = []
    for raw in unicodedata.normalize("NFKC", text).splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def fingerprint(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _check(text: Any, label: str) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        raise BadRequestException(f"{label}은(는) 글이어야 합니다.")
    if len(text) > MAX_TEXT_CHARS:
        raise BadRequestException(f"{label}이(가) 너무 깁니다({MAX_TEXT_CHARS:,}자 이하). 잘라서 비교하면 틀릴 수 있어 거절합니다.")
    return text


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "…"


def impact_candidates(changed_lines: list[str]) -> list[dict[str, Any]]:
    """달라진 줄에 낱말이 걸린 영역별 후보. 낱말은 대소문자를 구분하지 않는다. 같은 영역은 한 번만, 어떤 낱말·줄 수로 걸렸는지 함께 준다."""

    found: list[dict[str, Any]] = []
    lowered = [line.lower() for line in changed_lines]
    for entry in IMPACT_KEYWORDS:
        hits: dict[str, int] = {}
        for word in entry["words"]:
            count = sum(1 for line in lowered if word in line)
            if count:
                hits[word] = count
        if hits:
            found.append({
                "area": entry["area"], "rule_codes": list(entry["rule_codes"]), "matched_words": sorted(hits), "lines": max(hits.values()),
                "in_rule_catalog": bool(entry["rule_codes"]),
            })
    return found


def compare(platform: str, previous_text: Any, current_text: Any) -> dict[str, Any]:
    if platform not in PLATFORMS:
        raise BadRequestException("알 수 없는 플랫폼입니다.")
    previous = normalize_lines(_check(previous_text, "이전 글"))
    current = normalize_lines(_check(current_text, "이번 글"))
    base = {"platform": platform, "previous_lines": len(previous), "current_lines": len(current)}
    if not previous or not current:
        return {**base, "status": UNKNOWN, "reason": "PREVIOUS_EMPTY" if not previous else "CURRENT_EMPTY", "previous_hash": None, "current_hash": None,
                "added": [], "removed": [], "added_count": 0, "removed_count": 0, "truncated": False, "impact_candidates": [], "needs_human_review": True,
                "note": "비교할 글이 비어 있어 달라졌는지 알 수 없습니다 — '변경 없음'이 아닙니다."}
    previous_hash, current_hash = fingerprint(previous), fingerprint(current)
    if previous_hash == current_hash:
        return {**base, "status": UNCHANGED, "reason": None, "previous_hash": previous_hash, "current_hash": current_hash, "added": [], "removed": [],
                "added_count": 0, "removed_count": 0, "truncated": False, "impact_candidates": [], "needs_human_review": False,
                "note": "넣은 두 글은 공백·줄바꿈을 맞춘 뒤 같습니다. 복사한 범위 밖의 변경은 알 수 없습니다."}
    previous_set, current_set = set(previous), set(current)
    added = [line for line in current if line not in previous_set]
    removed = [line for line in previous if line not in current_set]
    reordered_only = not added and not removed      # 줄은 같고 순서·중복만 다른 경우
    return {
        **base, "status": CHANGED, "reason": "ORDER_OR_DUPLICATES_ONLY" if reordered_only else None, "previous_hash": previous_hash, "current_hash": current_hash,
        "added": [_clip(x) for x in added[:MAX_LINES_SHOWN]], "removed": [_clip(x) for x in removed[:MAX_LINES_SHOWN]],
        "added_count": len(added), "removed_count": len(removed), "truncated": len(added) > MAX_LINES_SHOWN or len(removed) > MAX_LINES_SHOWN,
        "impact_candidates": impact_candidates(added + removed), "needs_human_review": True,
        "note": "달라진 줄과 영향 후보는 낱말 일치로 찾은 것입니다. 규칙은 자동으로 바뀌지 않으며, 공식 문서를 직접 읽고 판단해 반영해야 합니다.",
    }


__all__ = ["compare", "normalize_lines", "fingerprint", "impact_candidates", "IMPACT_KEYWORDS", "PLATFORMS", "MAX_TEXT_CHARS", "MAX_LINES_SHOWN", "UNCHANGED", "CHANGED", "UNKNOWN"]
