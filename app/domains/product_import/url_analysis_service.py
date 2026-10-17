"""
=========================================================
Homez OS

File : app/domains/product_import/url_analysis_service.py

2026-10-18 — URL 상품 분석 · 단가 · 이익 계산(Phase E2). 읽기 → AI 해석(검증) → 계산 → 판정.

두 단계로 나눈다:
  1) `analyze` — URL 페이지를 읽고(기존 읽기와 같은 접근) AI가 사실을 뽑는다. AI 호출은 여기서만, 사용자가 누를 때 1회.
     응답은 evidence 검증을 거쳐 `prefill`(값 + 출처 + 근거)로 돌려준다. 온채널 공식 API 값이 있으면 AI 해석보다 우선한다.
  2) `calculate` — AI 없이 **결정적 계산**만 한다(값을 고치며 몇 번이든 무료). 모르는 값이 있으면 계산하지 않고 보류한다.

금융 원칙: AI는 돈을 계산하지 않는다. 원가·부가세·배송비·수수료율 중 하나라도 확정되지 않으면 0이나 가정값으로 채우지 않고
`HOLD`로 돌려준다. 수수료율은 `channel_fee_source`의 출처(사용자 확인/미확인)만 쓴다. 판정은 규칙이 낸다.
  기준 마진율 = max(회사 최소 목표 마진율, 18%). 경쟁 가격은 쓰지 않는다(원가 + 설정 마진율만).
=========================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field
from decimal import ROUND_HALF_UP
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.external_call.constants import CallPurpose
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from app.domains.marketplace_listing.listing_wizard_schema import EconomicsInputItem
from app.domains.marketplace_listing.margin_calculator import calculate_economics
from app.domains.marketplace_listing.margin_calculator import derive_sale_price_for_margin_rate
from app.domains.marketplace_listing.margin_calculator import min_margin_shortfall
from app.domains.product_import.channel_fee_source import FeeLookup
from app.domains.product_import.channel_fee_source import FeeSourceKind
from app.domains.product_import.channel_fee_source import UserConfirmedFeeStore
from app.domains.product_import.service import ProductImportService
from app.domains.product_import.url_analysis_provider import RISK_SEVERITY
from app.domains.product_import.url_analysis_provider import SYSTEM_PROMPT
from app.domains.product_import.url_analysis_provider import VAT_STATES
from app.domains.product_import.url_analysis_provider import PageFacts
from app.domains.product_import.url_analysis_provider import build_user_prompt
from app.domains.product_import.url_analysis_provider import validate_analysis

FLOOR_MARGIN_RATE = Decimal("0.18")
VAT_RATE = Decimal("0.1")
DEFAULT_RETURN_RESERVE_RATE = Decimal("0.03")
MAX_ANALYSIS_OUTPUT_TOKENS = 1800
_WON = Decimal("1")
_CENT = Decimal("0.01")

AI_CONFIRM_LABELS = {"supply_price": "공급가", "vat_state": "부가세", "shipping_cost": "배송비"}
AI_CONFIRM_FIELDS = frozenset(AI_CONFIRM_LABELS)
HIGH_RISKS = frozenset(code for code, severity in RISK_SEVERITY.items() if severity == "HIGH")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _round_up_10(value: Decimal) -> Decimal:
    return Decimal(math.ceil(value / 10) * 10)


@dataclass
class CalcInput:
    channel: str = "COUPANG"
    category_code: str | None = None
    supply_price: Decimal | None = None
    vat_state: str | None = None
    shipping_cost: Decimal | None = None
    sell_quantity: int = 1
    packaging_cost: Decimal = Decimal("0")
    return_reserve_rate: Decimal = DEFAULT_RETURN_RESERVE_RATE
    payment_fee_rate: Decimal = Decimal("0")
    sale_price: Decimal | None = None
    pack_count: int | None = None
    unit_volume: Decimal | None = None
    unit: str | None = None
    risk_codes: list[str] = field(default_factory=list)
    acknowledged_risks: list[str] = field(default_factory=list)
    # AI가 페이지에서 읽은 그대로 쓰는 값(공급가·부가세·배송비)과, 사용자가 페이지에서 직접 확인했다는 표시
    ai_sourced: list[str] = field(default_factory=list)
    ai_values_confirmed: bool = False


def _item(cost: Decimal, fee_rate: Decimal, inp: CalcInput, sale_price: Decimal, *, fee_delta=Decimal("0"), reserve_delta=Decimal("0")) -> EconomicsInputItem:
    return EconomicsInputItem(
        marketplace_account_id=0, cost_of_goods=cost, sale_price=sale_price,
        channel_fee_rate=min(max(fee_rate + fee_delta, Decimal("0")), Decimal("1")), payment_fee_rate=inp.payment_fee_rate,
        shipping_cost=inp.shipping_cost, packaging_cost=inp.packaging_cost,
        return_reserve_rate=min(max(inp.return_reserve_rate + reserve_delta, Decimal("0")), Decimal("1")), tax_basis_rate=Decimal("0"),
    )


def calculate_report(inp: CalcInput, fee: FeeLookup, company_min_rate: Decimal) -> dict[str, Any]:
    """결정적 계산 + 규칙 판정. 모르는 값이 있으면 계산하지 않고 HOLD를 돌려준다(0·가정으로 채우지 않는다)."""

    min_rate = max(Decimal(company_min_rate), FLOOR_MARGIN_RATE).normalize()  # 0.2200 같은 저장 자릿수를 정리한다
    missing: list[dict[str, str]] = []
    if inp.supply_price is None or inp.supply_price <= 0:
        missing.append({"code": "SUPPLY_PRICE", "message": "공급가(원가)를 확인할 수 없다 — 값을 입력해야 한다"})
    if inp.vat_state not in VAT_STATES:
        missing.append({"code": "VAT_STATE", "message": "공급가의 부가세 포함/별도/면세를 확인할 수 없다 — 선택해야 한다(자동 가정하지 않는다)"})
    if inp.shipping_cost is None:
        missing.append({"code": "SHIPPING_COST", "message": "배송비를 확인할 수 없다 — 값을 입력해야 한다(없으면 0을 직접 입력)"})
    ai_money = sorted(set(inp.ai_sourced) & AI_CONFIRM_FIELDS)
    if ai_money and not inp.ai_values_confirmed:
        missing.append({"code": "AI_CONFIRM", "message": "AI가 페이지에서 읽은 값(" + ", ".join(AI_CONFIRM_LABELS[k] for k in ai_money) + ")을 페이지에서 직접 확인했는지 표시해야 한다 — AI 해석은 틀리거나 페이지의 조작 문구에 속을 수 있다"})
    if fee.source == FeeSourceKind.UNKNOWN or fee.rate is None:
        missing.append({"code": "CHANNEL_FEE", "message": f"{inp.channel} 수수료율이 확인되지 않았다 — 플랫폼에서 확인한 값을 저장해야 한다(등록 전 공식 API로 제공되지 않는 플랫폼)"})

    base = {
        "min_margin_rate": str(min_rate), "fee": fee.to_dict(), "channel": inp.channel,
        "risks": [{"code": c, "severity": RISK_SEVERITY.get(c, "MEDIUM"), "acknowledged": c in inp.acknowledged_risks} for c in inp.risk_codes if c in RISK_SEVERITY],
    }
    if missing:
        return {**base, "verdict": "HOLD", "reasons": ["계산에 필요한 값이 확인되지 않았다"], "missing": missing, "assumptions": [], "scenarios": [], "sensitivity": [], "unit": None}

    sell_qty = max(1, int(inp.sell_quantity))
    vat_multiplier = (Decimal("1") + VAT_RATE) if inp.vat_state == "EXCLUDED" else Decimal("1")
    cost = _money(inp.supply_price * sell_qty * vat_multiplier)
    fee_rate = fee.rate

    assumptions = [
        {"key": "return_reserve_rate", "label": "반품 예비율", "value": str(inp.return_reserve_rate), "confirmed": False},
        {"key": "payment_fee_rate", "label": "결제 수수료율(채널 수수료에 포함으로 가정)", "value": str(inp.payment_fee_rate), "confirmed": False},
        {"key": "packaging_cost", "label": "포장비", "value": str(inp.packaging_cost), "confirmed": False},
        {"key": "tax_basis_rate", "label": "세금(소득세 등) 기준율 — 미반영", "value": "0", "confirmed": False},
    ]
    if inp.vat_state == "EXCLUDED":
        assumptions.append({"key": "vat_cost", "label": "부가세 별도 공급가는 부가세 10%를 더한 금액을 원가로 계산(매입세액 공제 미반영, 보수적)", "value": str(cost), "confirmed": False})
    if fee.scope == "channel_default":
        assumptions.append({"key": "fee_scope", "label": "수수료율은 카테고리별 값이 아니라 플랫폼 공통 값(사용자 확인)", "value": str(fee_rate), "confirmed": False})

    targets = sorted({min_rate, max(min_rate, Decimal("0.25")), max(min_rate, Decimal("0.30"))})
    scenarios: list[dict[str, Any]] = []
    total_units = None
    if inp.pack_count and inp.unit_volume and inp.unit_volume > 0:
        total_units = Decimal(inp.pack_count) * sell_qty * inp.unit_volume
    reasons: list[str] = []
    for target in targets:
        derived = derive_sale_price_for_margin_rate(_item(cost, fee_rate, inp, Decimal("0")), target)
        if derived is None:
            scenarios.append({"target_margin_rate": str(target), "sale_price": None})
            continue
        price = _round_up_10(derived)
        for _ in range(10):  # 10원 올림 뒤에도 정확히 미달이면(반올림 경계) 10원씩 올린다
            if min_margin_shortfall(_item(cost, fee_rate, inp, price), target) <= 0:
                break
            price += 10
        result = calculate_economics(_item(cost, fee_rate, inp, price))
        row = {
            "target_margin_rate": str(target), "sale_price": str(price), "margin_amount": str(result.margin_amount),
            "margin_rate": str(result.margin_rate), "break_even_price": str(result.break_even_price) if result.break_even_price is not None else None,
        }
        if total_units:
            row["price_per_unit"] = str((price / total_units).quantize(_CENT, rounding=ROUND_HALF_UP))
        scenarios.append(row)

    unit = None
    if total_units:
        unit = {"label": inp.unit or "", "total_units": str(total_units), "cost_per_unit": str((cost / total_units).quantize(_CENT, rounding=ROUND_HALF_UP))}

    sensitivity: list[dict[str, str]] = []
    first = next((s for s in scenarios if s.get("sale_price")), None)
    if first:
        price = Decimal(first["sale_price"])
        for label, kwargs in (
            ("수수료율 +2%p", {"fee_delta": Decimal("0.02")}), ("수수료율 -2%p", {"fee_delta": Decimal("-0.02")}),
            ("반품 예비율 +2%p", {"reserve_delta": Decimal("0.02")}), ("반품 예비율 -2%p", {"reserve_delta": Decimal("-0.02")}),
        ):
            res = calculate_economics(_item(cost, fee_rate, inp, price, **kwargs))
            sensitivity.append({"label": label, "margin_rate": str(res.margin_rate)})

    verdict = "CANDIDATE"
    if first is None:
        verdict = "NOT_RECOMMENDED"
        reasons.append("수수료율과 반품 예비율의 합이 너무 커서 어떤 판매가로도 기준 마진율을 달성할 수 없다")
    if inp.sale_price is not None and first is not None:
        short = min_margin_shortfall(_item(cost, fee_rate, inp, inp.sale_price), min_rate)
        entered = calculate_economics(_item(cost, fee_rate, inp, inp.sale_price))
        base["entered_sale_price"] = {"sale_price": str(inp.sale_price), "margin_rate": str(entered.margin_rate), "margin_amount": str(entered.margin_amount), "meets_min": short <= 0}
        if short > 0:
            verdict = "NOT_RECOMMENDED"
            reasons.append(f"입력한 판매가의 마진율 {entered.margin_rate * 100:.2f}%가 기준 {min_rate * 100:.0f}%보다 낮다")
    codes = set(inp.risk_codes)
    if "PROHIBITED" in codes:
        verdict = "NOT_RECOMMENDED"
        reasons.append("금지 품목 표현이 있어 등록 대상이 아니다")
    unconfirmed = sorted(c for c in codes if c in HIGH_RISKS and c not in inp.acknowledged_risks)
    if unconfirmed and verdict == "CANDIDATE":
        verdict = "HOLD"
        reasons.append("확인이 필요한 위험이 남아 있다: " + ", ".join(unconfirmed))
    if verdict == "CANDIDATE":
        reasons.append(f"원가 기준 최소 판매가 {first['sale_price']}원이면 기준 마진율 {min_rate * 100:.0f}% 이상 — 이 가격이 시장에서 통하는지는 직접 판단해야 한다(경쟁 가격은 분석하지 않았다)")
    return {**base, "verdict": verdict, "reasons": reasons, "missing": [], "assumptions": assumptions, "cost_of_goods": str(cost), "scenarios": scenarios, "sensitivity": sensitivity, "unit": unit}


class UrlAnalysisService:

    def __init__(self, db: Session, import_service: ProductImportService, ai: AiDraftService):
        self.db = db
        self.importer = import_service
        self.ai = ai

    # ---------------- 1) 읽기 + AI 해석 ----------------

    def analyze(self, url: str, company_id: int, user_id: int) -> dict[str, Any]:
        response, page_text = self.importer.search_with_text(url, company_id, user_id)
        page = PageFacts(
            title=response.title, description=response.suggested_description, price_text=response.price_text,
            supplier_name=response.supplier_name, origin_text=response.origin_text, manufacturer_text=response.manufacturer_text,
            model_name=response.model_name, notice_rows=tuple((r.label, r.value) for r in response.notice_rows), page_text=page_text,
        )
        result, cost, meta = self.ai.call_llm(
            company_id, user_id, target="URL", system=SYSTEM_PROMPT, user_prompt=build_user_prompt(page),
            ext_purpose=CallPurpose.AI_ANALYSIS, max_output_tokens=MAX_ANALYSIS_OUTPUT_TOKENS,
        )
        analysis = validate_analysis(result.text, page)
        usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "cost_krw": cost, "ok": bool(analysis.facts), "purpose": "ANALYSIS"}
        month = self.ai.record_usage(company_id, user_id, "URL", result.model, usage)
        prefill, conflicts = self.build_prefill(response, analysis.facts)
        return {
            "page": {
                "source_url": response.source_url, "source_kind": response.source_kind, "title": response.title,
                "suggested_product_name": response.suggested_product_name, "price_text": response.price_text,
                "supplier_name": response.supplier_name, "warnings": response.warnings,
            },
            "analysis": analysis.to_dict(), "prefill": prefill, "conflicts": conflicts,
            "usage": {**usage, "model": result.model, **month, "monthly_limit_krw": meta["limit"]},
        }

    @staticmethod
    def build_prefill(response, facts: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """계산에 쓸 값을 출처와 함께 정리한다 — 공식 API 값이 AI 해석보다 우선한다."""

        prefill: dict[str, Any] = {}
        conflicts: list[str] = []
        offer = response.offer
        ai_price = facts.get("supply_price")
        if offer is not None and offer.supply_price is not None:
            prefill["supply_price"] = {"value": str(offer.supply_price), "source": "ONCH_API"}
            if ai_price and Decimal(ai_price["value"]) != offer.supply_price:
                conflicts.append(f"페이지에서 AI가 읽은 공급가 {ai_price['value']}원이 공식 API 공급가 {offer.supply_price}원과 달라 공식 값을 썼다")
        elif ai_price:
            prefill["supply_price"] = {"value": str(ai_price["value"]), "source": "AI_PAGE", "evidence": ai_price["evidence"]}
        ai_ship = facts.get("shipping_fee")
        if offer is not None and offer.base_shipping_cost is not None:
            prefill["shipping_cost"] = {"value": str(offer.base_shipping_cost), "source": "ONCH_API"}
            if ai_ship and ai_ship["value"] != offer.base_shipping_cost:
                conflicts.append(f"AI가 읽은 배송비 {ai_ship['value']}원이 공식 API 배송비 {offer.base_shipping_cost}원과 달라 공식 값을 썼다")
        elif ai_ship:
            prefill["shipping_cost"] = {"value": str(ai_ship["value"]), "source": "AI_PAGE", "evidence": ai_ship["evidence"]}
        vat = facts.get("vat")
        if offer is not None and offer.tax_exempt is True:
            prefill["vat_state"] = {"value": "EXEMPT", "source": "ONCH_API"}
        elif vat:
            prefill["vat_state"] = {"value": vat["state"], "source": "AI_PAGE", "evidence": vat["evidence"]}
        for key in ("pack_count", "min_order_qty"):
            if facts.get(key):
                prefill[key] = {"value": str(facts[key]["value"]), "source": "AI_PAGE", "evidence": facts[key]["evidence"]}
        if facts.get("unit_volume"):
            uv = facts["unit_volume"]
            prefill["unit_volume"] = {"value": str(uv["value"]), "unit": uv["unit"], "source": "AI_PAGE", "evidence": uv["evidence"]}
        return prefill, conflicts

    # ---------------- 2) 결정적 계산 ----------------

    def calculate(self, company_id: int, user_id: int, inp: CalcInput) -> dict[str, Any]:
        try:
            min_rate = ChannelPolicyService(self.db).get_effective_min_target_margin_rate(company_id)
        except Exception:  # noqa: BLE001 — 설정을 못 읽어도 확정 정책의 바닥(18%)으로 계산한다
            min_rate = FLOOR_MARGIN_RATE
        fee = UserConfirmedFeeStore(self.db).lookup(user_id, company_id, inp.channel, inp.category_code)
        if inp.sell_quantity < 1 or inp.sell_quantity > 1000:
            raise BadRequestException("판매 수량은 1~1000이어야 합니다.")
        return calculate_report(inp, fee, Decimal(min_rate))
