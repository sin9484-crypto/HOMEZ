"""
=========================================================
Homez OS

File : app/domains/order/return_economics.py

2026-10-23 — 상품 관리 AI P3(반영 C): **반품 한 건의 손익 계산과 처리 체크포인트**. 사용자가 쿠팡 화면·공급처 화면에서 **직접 확인한 금액**만 받아 결정적으로 계산한다(AI가 금액을 정하지 않는다).
DB·네트워크·AI·파일 없음. 저장하지 않는다(요청 값에서 바로 계산).

왜(2026-10-19 반품 사례): 같은 반품인데 고객 환불 6,050원(= 12,050 − 배송비 3,000 − 반품비 3,000)과 공급사 환불 2,310원(= 8,310 − 반품 배송비 6,000 차감)이 **서로 다른 계산**이었고
공급사 답변은 HOMEZ가 읽지 못한다. 고객에게 남긴 돈과 공급사에서 못 돌려받은 돈을 나란히 놓아야 손익이 보인다.

계산:
  고객에게서 남은 돈 = 고객 결제액 − 고객 환불액
  공급사에서 못 돌려받은 돈 = 매입 지급액 − 공급사 환불액
  손익(수수료 정산 전) = 남은 돈 − 못 돌려받은 돈  (쿠팡 수수료 환급액을 알면 더한다)
기준:
  - **모르는 금액은 비워 둔다** — 하나라도 없으면 손익을 만들지 않고 어떤 값이 비었는지(`missing`)만 알린다("모르는 값을 0으로 채우지 않는다").
  - 공급사 환불액과 반품 배송비 차감액을 **둘 다** 주면 서로 맞는지 확인하고, 안 맞으면 어느 쪽도 고치지 않고 `SUPPLIER_AMOUNTS_MISMATCH`로 막는다(금융 값 자동 보정 금지).
  - 금액은 0 이상. 환불액이 지급액보다 크면 `REFUND_ABOVE_PAID`로 막는다.
  - 쿠팡이 고객에게서 뺀 배송비·반품비와 판매 수수료가 판매자 정산에 어떻게 반영되는지는 **아직 확인하지 못했다**(🔴) — 수수료 환급액을 주지 않으면 `SETTLEMENT_UNCONFIRMED`를 항상 붙인다.
체크포인트(순서): ① 공급사 답변 확인 ② 추가 요청(예: 새 안심번호) 처리 ③ 반품 배송비 차감 수용 판단 ④ 윙 수령 확인 ⑤ 정산 확인. 사용자가 준 완료 여부로 상태를 매기고, 모르는 것은 "확인 전"(None)이다.
협의 완료(온채널)는 되돌릴 수 없으므로 ①~③이 끝나기 전에는 ④로 넘어가라고 권하지 않는다.
=========================================================
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

ZERO = Decimal("0")

AMOUNT_FIELDS = ("customer_paid", "customer_refund", "supplier_paid", "supplier_refund")
STEPS = (
    ("SUPPLIER_REPLY_CHECKED", "공급사 답변 확인"),
    ("EXTRA_REQUESTS_HANDLED", "추가 요청 처리(예: 새 안심번호)"),
    ("SHIPPING_DEDUCTION_DECIDED", "반품 배송비 차감 수용 판단"),
    ("WING_RECEIPT_CONFIRMED", "윙 수령 확인"),
    ("SETTLEMENT_CHECKED", "정산 확인"),
)
STEP_KEYS = tuple(code for code, _ in STEPS)


def _amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    return number if number.is_finite() and number >= ZERO else None


def _reason(code: str, level: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "level": level, "params": params}


def calculate(
    customer_paid: Any = None, customer_refund: Any = None, supplier_paid: Any = None, supplier_refund: Any = None,
    supplier_deduction: Any = None, fee_refund: Any = None,
) -> dict[str, Any]:
    """반품 손익. decision: OK(손익 계산됨) / HOLD(모르는 값·모순) — 손실은 reasons의 NET_LOSS(WARN)로 알린다."""

    values = {
        "customer_paid": _amount(customer_paid), "customer_refund": _amount(customer_refund),
        "supplier_paid": _amount(supplier_paid), "supplier_refund": _amount(supplier_refund),
    }
    deduction = _amount(supplier_deduction)
    fee = _amount(fee_refund)
    reasons: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "decision": "OK", "reasons": reasons, "missing": [], "customer_kept": None, "supplier_unrecovered": None, "net_before_fee": None, "net_with_fee_refund": None,
        "supplier_refund_used": None,
    }

    # 공급사 환불액: 직접 준 값이 우선이고, 없으면 지급액 − 차감액으로 구한다(둘 다 주면 서로 맞아야 한다).
    supplier_refund_used = values["supplier_refund"]
    if values["supplier_paid"] is not None and deduction is not None:
        derived = values["supplier_paid"] - deduction
        if derived < ZERO:
            reasons.append(_reason("REFUND_ABOVE_PAID", "HOLD", field="supplier_deduction"))
        elif supplier_refund_used is None:
            supplier_refund_used = derived
        elif supplier_refund_used != derived:
            reasons.append(_reason("SUPPLIER_AMOUNTS_MISMATCH", "HOLD", refund=_plain(supplier_refund_used), derived=_plain(derived)))

    if values["customer_refund"] is not None and values["customer_paid"] is not None and values["customer_refund"] > values["customer_paid"]:
        reasons.append(_reason("REFUND_ABOVE_PAID", "HOLD", field="customer_refund"))
    if supplier_refund_used is not None and values["supplier_paid"] is not None and supplier_refund_used > values["supplier_paid"]:
        reasons.append(_reason("REFUND_ABOVE_PAID", "HOLD", field="supplier_refund"))

    needed = {"customer_paid": values["customer_paid"], "customer_refund": values["customer_refund"], "supplier_paid": values["supplier_paid"], "supplier_refund": supplier_refund_used}
    missing = [name for name, value in needed.items() if value is None]
    if missing:
        result["missing"] = missing
        reasons.append(_reason("AMOUNTS_UNKNOWN", "HOLD", fields=missing))
    reasons.append(_reason("SETTLEMENT_UNCONFIRMED", "INFO") if fee is None else _reason("FEE_REFUND_INCLUDED", "INFO", amount=_plain(fee)))

    if any(r["level"] == "HOLD" for r in reasons):
        result["decision"] = "HOLD"
        return result

    kept = needed["customer_paid"] - needed["customer_refund"]
    unrecovered = needed["supplier_paid"] - needed["supplier_refund"]
    net = kept - unrecovered
    result.update(customer_kept=_plain(kept), supplier_unrecovered=_plain(unrecovered), net_before_fee=_plain(net), supplier_refund_used=_plain(needed["supplier_refund"]))
    if fee is not None:
        result["net_with_fee_refund"] = _plain(net + fee)
    final_net = net + (fee or ZERO)
    if final_net < ZERO:
        reasons.append(_reason("NET_LOSS", "WARN", amount=_plain(-final_net)))
    return result


def _plain(value: Decimal) -> int | float:
    """JSON에 쓰기 좋게: 원 단위로 떨어지면 정수, 아니면 소수 두 자리."""
    quantized = value.quantize(Decimal("0.01"))
    return int(quantized) if quantized == quantized.to_integral_value() else float(quantized)


def checkpoints(done: dict[str, Any] | None) -> dict[str, Any]:
    """처리 단계 목록. 사용자가 준 완료 여부(True=했다, False=아직, 없음=확인 전)로 상태를 매기고 다음 단계를 알린다."""

    done = done if isinstance(done, dict) else {}
    steps = []
    for code, label in STEPS:
        raw = done.get(code)
        steps.append({"code": code, "label": label, "done": raw if isinstance(raw, bool) else None})
    next_step = next((s["code"] for s in steps if s["done"] is not True), None)
    warnings = []
    receipt = next(s for s in steps if s["code"] == "WING_RECEIPT_CONFIRMED")
    earlier_open = [s["code"] for s in steps[:3] if s["done"] is not True]
    if receipt["done"] is True and earlier_open:
        warnings.append({"code": "RECEIPT_BEFORE_NEGOTIATION", "steps": earlier_open})   # 협의가 끝나기 전에 수령 확인을 했다 — 되돌릴 수 없는 단계가 앞서 갔다
    return {"steps": steps, "next_step": next_step, "all_done": next_step is None, "warnings": warnings}


__all__ = ["calculate", "checkpoints", "STEPS", "STEP_KEYS", "AMOUNT_FIELDS"]
