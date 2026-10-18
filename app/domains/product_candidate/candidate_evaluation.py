"""
=========================================================
Homez OS

File : app/domains/product_candidate/candidate_evaluation.py

2026-10-18 — 상품 서칭 AI S2: 후보 **사전 평가 v2**. 후보 한 건의 이름·고시 표·마진 분석 스냅샷·같은 상품 후보 정보로 "후보 가능 / 보류 / 제외 권고"와 이유를 규칙으로 낸다.
순수 함수, AI 없음, 쓰기·외부 호출 없음. **권고일 뿐**이다 — 후보 승인·보류·거절은 사용자가 기존 후보 화면에서 한다.

점검(이유 코드와 수준 BLOCK/HOLD/INFO/OK):
  - PROHIBITED_ITEM(BLOCK)      금지 품목 표현(콘텐츠 정책 체크리스트)
  - BRAND_IP_WORD(HOLD)         고위험 상표 단어
  - CLAIM_WORDS(HOLD)           효능·의료 오인 표현(쿠팡이 반려한 단어) / CHILD_TERMS(HOLD) 아동 대상 표현
  - REGULATED_CATEGORY(HOLD)    고시 구분이 화장품·식품·의료기기·전기용품·어린이제품 등 규제 분류 / KC_CERT(HOLD) 고시에 KC 인증 유형이 "해당 없음"이 아니다
  - MARGIN_NOT_ANALYZED(HOLD)   마진 분석(URL 분석 스냅샷)이 없다 — 이익 계산 없이는 후보 가능으로 올리지 않는다
    MARGIN_ANALYSIS_HOLD(HOLD)  분석은 있으나 계산에 필요한 값이 비어 있다(공급가·부가세·배송비·수수료율 등)
    MARGIN_ANALYSIS_UNFIT(BLOCK) 분석 결과 기준 마진율을 달성할 수 없거나 입력한 판매가가 기준에 못 미친다(비추천)
    MARGIN_OK(OK)               분석 결과 기준 마진율 달성 가능(필요 판매가 표시)
  - DUPLICATE_OF(HOLD)          이미 같은 상품 후보가 있다(먼저 만들어진 쪽이 원본) / VARIANT_EXISTS(INFO) 구성만 다른 후보가 있다
  - NO_NOTICE(INFO)             고시 근거(가져온 고시 표)가 없다
결정: BLOCK이 하나라도 있으면 EXCLUDE, 없고 HOLD가 있으면 HOLD, 모두 통과하면 READY.
마진 분석이 없거나 비어 있으면 READY가 될 수 없다 — "모르는 비용을 괜찮다고 보지 않는다"(Phase E와 같은 원칙).
=========================================================
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.domains.marketplace_listing.listing_wizard_precheck import claim_words_in
from app.domains.marketplace_listing.llm_draft_provider import CHILD_TERMS
from app.domains.media_asset.content_policy_check import run_content_policy_checklist

BLOCK, HOLD, INFO, OK = "BLOCK", "HOLD", "INFO", "OK"
READY, EXCLUDE = "READY", "EXCLUDE"
DECISION_RANK = {READY: 0, HOLD: 1, EXCLUDE: 2}
SNAPSHOT_PREFIX = "AI_ANALYSIS_REPORT:"

# 고시 구분(상품정보제공고시 구분) 문구에 이 낱말이 있으면 규제 분류로 본다.
REGULATED_WORDS = (
    # 위에서부터 처음 맞는 것을 쓴다 — "건강기능식품"이 "식품"으로 읽히지 않게 더 구체적인 것을 앞에 둔다.
    ("HEALTH_FOOD", ("건강기능",)), ("COSMETIC", ("화장품",)), ("MEDICAL", ("의료기기", "의약")),
    ("FOOD", ("식품", "농수산", "음료")), ("ELECTRIC", ("전기용품", "전자제품", "전기", "가전")), ("CHILD", ("어린이", "완구", "유아")),
)
_NO_VALUES = ("해당없음", "해당사항없음", "해당안됨", "없음", "-", "n/a", "na", "미해당")


def _squash(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def parse_snapshot(payload_summary: str | None) -> dict | None:
    """URL 분석 화면이 후보 근거로 남긴 마진 분석 스냅샷(`AI_ANALYSIS_REPORT:(…) {json}`)을 읽는다. 형식이 다르면 None."""

    if not payload_summary or not payload_summary.startswith(SNAPSHOT_PREFIX):
        return None
    start = payload_summary.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(payload_summary[start:])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _reason(code: str, level: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "level": level, "params": params}


def _name_checks(name: str, category_hint: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    policy = run_content_policy_checklist(name, category_hint, None)
    if policy.blocking_flags:
        out.append(_reason("PROHIBITED_ITEM", BLOCK, words=list(policy.blocking_flags[:3])))
    if policy.warning_flags:
        out.append(_reason("BRAND_IP_WORD", HOLD, words=list(policy.warning_flags[:3])))
    claims = claim_words_in(name)
    if claims:
        out.append(_reason("CLAIM_WORDS", HOLD, words=claims[:4]))
    child = [word for word in CHILD_TERMS if word in (name or "")]
    if child:
        out.append(_reason("CHILD_TERMS", HOLD, words=child[:3]))
    return out


def _notice_checks(notice_rows: list[tuple[str, str]] | None) -> list[dict[str, Any]]:
    if not notice_rows:
        return [_reason("NO_NOTICE", INFO)]
    out: list[dict[str, Any]] = []
    for label, value in notice_rows:
        label_s, value_s = _squash(label), _squash(value)
        if "고시구분" in label_s or "제공고시" in label_s:
            for kind, words in REGULATED_WORDS:
                if any(word in value_s for word in words):
                    out.append(_reason("REGULATED_CATEGORY", HOLD, kind=kind, value=str(value)[:40]))
                    break
        if "kc" in label_s and value_s and value_s not in _NO_VALUES and not value_s.startswith("해당"):
            out.append(_reason("KC_CERT", HOLD, value=str(value)[:40]))
    return out


def _margin_checks(snapshot: dict | None) -> list[dict[str, Any]]:
    if snapshot is None:
        return [_reason("MARGIN_NOT_ANALYZED", HOLD)]
    verdict = snapshot.get("verdict")
    if verdict == "CANDIDATE":
        scenarios = [s for s in (snapshot.get("scenarios") or []) if isinstance(s, dict) and s.get("p")]
        return [_reason("MARGIN_OK", OK, price=scenarios[0]["p"] if scenarios else None, rate=snapshot.get("min_margin_rate"))]
    if verdict == "NOT_RECOMMENDED":
        return [_reason("MARGIN_ANALYSIS_UNFIT", BLOCK, reasons=[str(r)[:80] for r in (snapshot.get("reasons") or [])[:2]])]
    if verdict == "HOLD":
        missing = [str(m) for m in (snapshot.get("missing") or [])]
        return [_reason("MARGIN_ANALYSIS_HOLD", HOLD, missing=missing[:6], reasons=[str(r)[:80] for r in (snapshot.get("reasons") or [])[:2]])]
    return [_reason("MARGIN_NOT_ANALYZED", HOLD)]  # 알 수 없는 판정은 분석이 없는 것으로 본다


def _duplicate_checks(duplicate_of: list[dict[str, Any]] | None, variants: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = [_reason("DUPLICATE_OF", HOLD, candidate_id=d["candidate_id"], status=d.get("status", "")) for d in (duplicate_of or [])[:3]]
    if variants:
        out.append(_reason("VARIANT_EXISTS", INFO, candidate_ids=[v["candidate_id"] for v in variants[:5]]))
    return out


def evaluate(
    *, name: str, category_hint: str | None = None, notice_rows: list[tuple[str, str]] | None = None, snapshot: dict | None = None,
    duplicate_of: list[dict[str, Any]] | None = None, variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """후보 한 건의 사전 평가. 이유는 심각한 것(BLOCK → HOLD → INFO → OK) 순서로 돌려준다."""

    reasons = _name_checks(name, category_hint) + _notice_checks(notice_rows) + _margin_checks(snapshot) + _duplicate_checks(duplicate_of, variants)
    order = {BLOCK: 0, HOLD: 1, INFO: 2, OK: 3}
    reasons.sort(key=lambda r: order[r["level"]])
    levels = {r["level"] for r in reasons}
    decision = EXCLUDE if BLOCK in levels else HOLD if HOLD in levels else READY
    return {"decision": decision, "reasons": reasons}
