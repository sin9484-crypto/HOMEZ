"""
=========================================================
Homez OS

File : app/domains/purchase_task/margin_watch.py

2026-10-23 — 상품 관리 AI P2(반영 B): **구매 작업의 마진 점검**(읽기 전용). 구매 작업에 이미 저장된 경제성(`coupang_sale_amount`·`coupang_fee_amount`·`expected_net_profit`·`expected_margin_rate`)으로
"마진이 비어 있거나 기준에 못 미치는 건"을 찾고, 쿠팡 윙의 **자동가격조정 최저가를 얼마 이상으로 두어야 하는지**(마진 하한가)를 숫자로 알려 준다.

왜(2026-10-21 점검): 윙에 자동가격조정이 켜진 상품이 4개 있었다. 자동조정이 가격을 마진 하한 아래로 내리면 손실이 나는데, 윙 설정은 API로 읽을 수 없어 규칙으로 감지할 수 없다 →
**HOMEZ가 아는 숫자(하한가)를 사용자가 윙 최저가 칸에 넣도록 안내**한다(쓰기 없음, 확인은 사람이).

기준:
  - 순수 규칙(`evaluate_task`·`summarize`)은 DB·네트워크·AI가 없다. 서비스는 읽기만 한다.
  - **모르는 경제성은 정상이 아니다**: 판매액·수수료·순이익 중 하나라도 없거나 음수·0이면 `ECONOMICS_UNKNOWN`(HOLD), 하한가를 만들지 않는다(Phase E·S2·S3와 같은 원칙).
  - **금융 오류를 자동 보정하지 않는다**(CLAUDE.md): 저장된 마진율이 순이익÷판매액과 1%p 넘게 다르면 `STORED_MARGIN_MISMATCH`로 표시만 하고 어느 쪽도 고치지 않는다. 수수료가 판매액보다 크거나 비용이 음수가 되는 값도 `ECONOMICS_INCONSISTENT`(HOLD)로 막는다.
  - 마진 판정은 저장된 순이익÷판매액으로 한다(저장된 마진율이 아니라 원본 금액). 기준 마진율은 호출하는 쪽이 넘기며 기본 18%(HOMEZ 최소 마진).
  - **하한가 = 총비용 ÷ (1 − 수수료율 − 기준 마진율)**, 총비용 = 판매액 − 수수료 − 순이익(매입·배송·반품 예비가 모두 들어 있다). 반품 예비는 판매가에 비례하므로 이 값은 **근사**다(수십 원 오차) —
    그래서 **원 단위 올림**(안전한 쪽)으로 안내하고 화면에도 "대략"이라고 쓴다.
  - 권고일 뿐이다 — 가격·윙 설정은 바꾸지 않는다.
=========================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

BLOCK, HOLD, WARN, OK = "BLOCK", "HOLD", "WARN", "OK"
DEFAULT_MIN_MARGIN = Decimal("0.18")
MISMATCH_TOLERANCE = Decimal("0.01")   # 저장된 마진율과 계산한 마진율의 허용 차이(1%p)
ONE = Decimal("1")
ZERO = Decimal("0")


@dataclass(frozen=True)
class TaskEconomics:
    task_id: int
    title: str
    status: str
    sale: Decimal | None
    fee: Decimal | None
    profit: Decimal | None
    stored_margin: Decimal | None = None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    return number if number.is_finite() else None


def to_economics(task_id: int, title: str, status: str, sale: Any, fee: Any, profit: Any, stored_margin: Any = None) -> TaskEconomics:
    return TaskEconomics(task_id, title, status, _to_decimal(sale), _to_decimal(fee), _to_decimal(profit), _to_decimal(stored_margin))


def _reason(code: str, level: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "level": level, "params": params}


def evaluate_task(task: TaskEconomics, min_margin: Decimal = DEFAULT_MIN_MARGIN) -> dict[str, Any]:
    """구매 작업 한 건의 경제성 점검. decision: OK / WARN(기준 미달·표시 불일치) / HOLD(모름·모순) / BLOCK(손실)."""

    reasons: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "task_id": task.task_id, "title": task.title, "status": task.status, "decision": OK, "reasons": reasons,
        "margin_percent": None, "floor_price": None,
    }
    if not (isinstance(min_margin, Decimal) and min_margin.is_finite() and ZERO <= min_margin < ONE):
        reasons.append(_reason("ASSUMPTIONS_INVALID", HOLD))
        result["decision"] = HOLD
        return result

    if task.sale is None or task.fee is None or task.profit is None or task.sale <= ZERO or task.fee < ZERO:
        reasons.append(_reason("ECONOMICS_UNKNOWN", HOLD))
        result["decision"] = HOLD
        return result

    fee_rate = task.fee / task.sale
    total_cost = task.sale - task.fee - task.profit
    if total_cost <= ZERO or fee_rate >= ONE:
        reasons.append(_reason("ECONOMICS_INCONSISTENT", HOLD))   # 수수료·순이익이 판매액을 넘어서는 값 — 고치지 않고 막는다
        result["decision"] = HOLD
        return result

    margin = task.profit / task.sale
    result["margin_percent"] = float((margin * 100).quantize(Decimal("0.01")))
    denominator = ONE - fee_rate - min_margin
    if denominator > ZERO:
        result["floor_price"] = int(math.ceil(total_cost / denominator))   # 올림 — 안전한 쪽

    if margin < ZERO:
        reasons.append(_reason("LOSS", BLOCK, percent=result["margin_percent"]))
    elif margin < min_margin:
        reasons.append(_reason("MARGIN_BELOW_TARGET", WARN, percent=result["margin_percent"], target_percent=float((min_margin * 100).quantize(Decimal("0.01")))))
    if task.stored_margin is not None and abs(task.stored_margin - margin) > MISMATCH_TOLERANCE:
        reasons.append(_reason("STORED_MARGIN_MISMATCH", WARN, stored_percent=float((task.stored_margin * 100).quantize(Decimal("0.01"))), computed_percent=result["margin_percent"]))

    levels = {r["level"] for r in reasons}
    result["decision"] = BLOCK if BLOCK in levels else WARN if WARN in levels else OK
    return result


def summarize(tasks: list[TaskEconomics], min_margin: Decimal = DEFAULT_MIN_MARGIN) -> dict[str, Any]:
    """여러 건을 점검해 요약한다. 순서는 BLOCK → HOLD → WARN → OK, 같은 묶음에서는 작업 번호 큰 것(최근) 먼저(결정적)."""

    rank = {BLOCK: 0, HOLD: 1, WARN: 2, OK: 3}
    rows = sorted((evaluate_task(t, min_margin) for t in tasks), key=lambda r: (rank[r["decision"]], -r["task_id"]))
    counts = {d: sum(1 for r in rows if r["decision"] == d) for d in (BLOCK, HOLD, WARN, OK)}
    return {"rows": rows, "counts": counts, "min_margin_percent": float((min_margin * 100).quantize(Decimal("0.01"))) if isinstance(min_margin, Decimal) and min_margin.is_finite() else None}


__all__ = ["TaskEconomics", "to_economics", "evaluate_task", "summarize", "DEFAULT_MIN_MARGIN", "MISMATCH_TOLERANCE", "BLOCK", "HOLD", "WARN", "OK"]
