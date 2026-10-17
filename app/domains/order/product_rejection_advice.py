"""
=========================================================
Homez OS

File : app/domains/order/product_rejection_advice.py

2026-10-18 — 쿠팡 승인반려 사유 문구를 **규칙으로 분류**해 고치는 방법 안내의 종류(코드)를 돌려준다(상품 관리 AI P1a, AI 없음).

원칙:
  - 사유 원문은 그대로 보여 주고(화면), 이 모듈은 "어떤 종류의 안내를 붙일지"만 정한다. 문구는 화면 텍스트(`easy_texts.js`)에 있다.
  - **관측한 문구**(`observed=True`): 실제 쿠팡 응답·거절에서 본 문장 기준 규칙. **미관측 문구**(`observed=False`): 쿠팡이 쓸 법한 키워드 기준의
    조심스러운 규칙 — 화면에서 "예상되는 원인"으로만 표시한다(단정하지 않는다).
  - 어느 규칙에도 맞지 않으면 `UNKNOWN`: 앱이 원인을 추측하지 않고 원문 + WING 확인 안내만 한다.
  - 쿠팡에 쓰지 않는다. 상품 수정·재제출은 이 앱이 하지 않으며(WING에서 직접), 이 파일에는 네트워크·DB 접근이 없다.

관측한 문장(2026-10 기준):
  - IMAGE_DOWNLOAD_FAILED : "[기본형]: 상세설명에 포함된 모든 이미지를 다운받지 못했습니다."(레이펄스 16417638496 승인반려 이력, 2026-10-17)
  - OPTION_DUPLICATE_WORD : "상품명에서 옵션과 중복된 단어를 지워주세요"(상품 16411745286, 사전검사 규칙 PRODUCT_NAME_REPEATS_OPTION의 근거)
  - BRAND_UID_REQUIRED    : "MPN/GTIN 속성이 누락되었거나 유효하지 않습니다"(백산수 제출 #7), "브랜드 ID가 필요합니다…"(러닝 무릎보호대 제출 #13)
=========================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RejectionRule:
    code: str
    patterns: tuple[re.Pattern, ...]
    observed: bool


def _p(*regexes: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(r, re.IGNORECASE) for r in regexes)


# 공백을 모두 지운 소문자 문자열에 적용한다.
RULES: tuple[RejectionRule, ...] = (
    RejectionRule("IMAGE_DOWNLOAD_FAILED", _p(r"이미지.{0,12}(다운받지|다운로드|내려받지|불러오지|가져오지)"), observed=True),
    RejectionRule("OPTION_DUPLICATE_WORD", _p(r"옵션.{0,10}중복"), observed=True),
    RejectionRule("BRAND_UID_REQUIRED", _p(r"브랜드.{0,6}id", r"mpn", r"gtin", r"manufacturerpartnumber", r"globaltradeitemnumber"), observed=True),
    RejectionRule("CLAIM_WORDS", _p(r"(의료기기|의약품|의료|효능|질병|치료).{0,15}(표현|오인|광고|금지|불가)"), observed=False),
    RejectionRule("CONTENTS_FORMAT", _p(r"상세설명.{0,12}(형식|html|태그|올바르지)"), observed=False),
)

KNOWN_CODES = tuple(rule.code for rule in RULES) + (UNKNOWN,)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def classify_rejection(reason: Any) -> list[dict[str, Any]]:
    """사유 문구 → 안내 종류 목록(규칙 순서). 사유가 비어 있으면 빈 목록, 맞는 규칙이 없으면 UNKNOWN 하나."""

    squashed = _squash(reason if isinstance(reason, str) else "")
    if not squashed:
        return []
    found = [
        {"code": rule.code, "observed": rule.observed}
        for rule in RULES
        if any(pattern.search(squashed) for pattern in rule.patterns)
    ]
    return found or [{"code": UNKNOWN, "observed": False}]
