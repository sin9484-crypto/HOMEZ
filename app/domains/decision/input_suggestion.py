"""
=========================================================
Homez OS

File : app/domains/decision/input_suggestion.py

2026-10-29 — 의사결정 AI 평가 입력 **제안**(D1 평가 기준 초안 6장). 실제 후보는 점수 칸이 비어 있어 그대로 평가하면 데이터 부족만
나온다. 이미 있는 후보 사전 평가(상품 서칭 AI S2: 금지 품목·상표 단어·효능 표현·규제 분류·KC·마진 분석 스냅샷)를 정해진 표로
의사결정 AI의 보강 입력 숫자로 옮겨 **제안만** 한다 — 저장하지 않고, 평가를 실행하지 않는다. 사용자가 보고 고친 뒤 평가를 요청한다.

변환표(D1 6장과 같아야 한다):
  - policy_prohibited_score(안전할수록 높음): 금지 품목(BLOCK) 0 / 효능·아동·규제 분류·KC(HOLD) 50 / 걸린 것 없고 고시 근거 있음 90 /
    고시 근거 없음 → 제안하지 않음(모름).
  - brand_ip_risk_score(안전할수록 높음): 고위험 상표 단어 30 / 없음 70(단어 검사일 뿐 권리 확인이 아니라 만점을 주지 않는다).
  - expected_margin_rate: 마진 분석이 '후보 가능'이고 판매가 시나리오에 마진율(m)이 있을 때 첫 시나리오의 m. 그 밖에는 제안하지 않는다.
  - 공급 안정성·배송·반품·가격 경쟁력·매출·자금은 근거 자료가 없어 제안하지 않는다(사람이 입력하거나 비워 둔다).
=========================================================
"""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

POLICY_BLOCK_CODES = ("PROHIBITED_ITEM",)
POLICY_CAUTION_CODES = ("CLAIM_WORDS", "CHILD_TERMS", "REGULATED_CATEGORY", "KC_CERT")
BRAND_CODES = ("BRAND_IP_WORD",)

POLICY_SCORE_BLOCK = Decimal("0")
POLICY_SCORE_CAUTION = Decimal("50")
POLICY_SCORE_CLEAR = Decimal("90")
BRAND_SCORE_WORD = Decimal("30")
BRAND_SCORE_NO_WORD = Decimal("70")

# 사람이 넣어야 하는 보강 입력(자료가 없어 제안하지 않는 것)
NOT_SUGGESTED = (
    "supply_stability_score", "inventory_shipping_risk_score", "return_claim_risk_score",
    "price_competitiveness_score", "gross_revenue", "required_funding", "available_funding",
)


def _rate(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not rate.is_finite() or rate < 0 or rate >= 1:
        return None
    return rate


def suggest_inputs(reasons: list[dict] | None, snapshot: dict | None) -> dict:
    """후보 사전 평가 이유 목록과 마진 분석 스냅샷 → {"inputs": {...}, "basis": [...], "notes": [...]}. 순수 함수."""

    codes = {str(r.get("code")) for r in (reasons or []) if isinstance(r, dict)}
    inputs: dict[str, Decimal] = {}
    basis: list[dict] = []
    notes: list[str] = []
    checked = reasons is not None  # 사전 평가가 아예 없으면 '걸린 것 없음'이 아니라 '모름'이다

    hit_block = sorted(codes & set(POLICY_BLOCK_CODES))
    hit_caution = sorted(codes & set(POLICY_CAUTION_CODES))
    if not checked:
        notes.append("후보 사전 평가가 없어 정책 안전도·상표 위험을 제안하지 않았습니다(모름).")
    elif hit_block:
        inputs["policy_prohibited_score"] = POLICY_SCORE_BLOCK
        basis.append({"field": "policy_prohibited_score", "value": str(POLICY_SCORE_BLOCK), "codes": hit_block, "note": "금지 품목 표현"})
    elif hit_caution:
        inputs["policy_prohibited_score"] = POLICY_SCORE_CAUTION
        basis.append({"field": "policy_prohibited_score", "value": str(POLICY_SCORE_CAUTION), "codes": hit_caution,
                      "note": "효능·아동 표현 또는 규제 분류·KC 인증 확인 필요"})
    elif "NO_NOTICE" in codes:
        notes.append("상품정보제공고시 근거가 없어 정책 안전도를 제안하지 않았습니다(모름).")
    else:
        inputs["policy_prohibited_score"] = POLICY_SCORE_CLEAR
        basis.append({"field": "policy_prohibited_score", "value": str(POLICY_SCORE_CLEAR), "codes": [],
                      "note": "금지 품목·효능·규제 분류 검사에 걸린 것 없음(고시 근거 있음)"})

    if not checked:
        pass
    elif codes & set(BRAND_CODES):
        inputs["brand_ip_risk_score"] = BRAND_SCORE_WORD
        basis.append({"field": "brand_ip_risk_score", "value": str(BRAND_SCORE_WORD), "codes": list(BRAND_CODES), "note": "고위험 상표 단어"})
    else:
        inputs["brand_ip_risk_score"] = BRAND_SCORE_NO_WORD
        basis.append({"field": "brand_ip_risk_score", "value": str(BRAND_SCORE_NO_WORD), "codes": [],
                      "note": "고위험 상표 단어 없음 — 단어 검사일 뿐 판매 권리 확인은 아님"})

    verdict = (snapshot or {}).get("verdict") if isinstance(snapshot, dict) else None
    if verdict == "CANDIDATE":
        scenarios = [s for s in (snapshot.get("scenarios") or []) if isinstance(s, dict) and s.get("p")]
        rate = _rate(scenarios[0].get("m")) if scenarios else None
        if rate is not None:
            inputs["expected_margin_rate"] = rate
            basis.append({"field": "expected_margin_rate", "value": str(rate), "codes": ["MARGIN_OK"],
                          "note": f"마진 분석의 판매가 {scenarios[0].get('p')}원 기준 마진율"})
        else:
            notes.append("마진 분석은 '후보 가능'이지만 마진율 숫자가 없어 제안하지 않았습니다.")
    elif verdict == "NOT_RECOMMENDED":
        notes.append("마진 분석이 '비추천'입니다 — 기준 마진율을 달성할 수 없어 마진을 제안하지 않았습니다.")
    else:
        notes.append("마진 분석이 없거나 값이 비어 있습니다 — URL 분석에서 마진을 계산하면 제안할 수 있습니다.")

    if "DUPLICATE_OF" in codes:
        notes.append("이미 같은 상품 후보가 있습니다 — 원본 후보를 먼저 확인하세요.")

    return {"inputs": inputs, "basis": basis, "notes": notes, "not_suggested": list(NOT_SUGGESTED)}


__all__ = ["suggest_inputs", "NOT_SUGGESTED", "POLICY_BLOCK_CODES", "POLICY_CAUTION_CODES", "BRAND_CODES"]
