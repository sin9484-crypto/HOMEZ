"""
=========================================================
Homez OS

File : app/domains/pricing/constants.py

Pricing & Margin Reconciliation 도메인 상수 — V7 Gate 5(2026-08-15).

app/domains/order/constants.py와 동일한 정신 — 상태값/사유 코드를
여러 곳에 하드코딩하지 않고 이 파일 하나로 모은다.
=========================================================
"""

from __future__ import annotations


class PriceChangeStatus:
    """PriceChangeRequest.status — PENDING에서만 다른 상태로 전이한다
    (조건부 UPDATE + rowcount 검증, 재전이 없음 — 결정된 행은 불변)."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

    DECIDED = (APPROVED, REJECTED, CANCELLED)


class MarginType:
    """MarginSnapshot.margin_type — 등록/가격변경 시점 예상 vs 정산
    반영 후 실제(요구사항 2)."""

    EXPECTED = "EXPECTED"
    ACTUAL = "ACTUAL"


class MarginSnapshotReason:
    """MarginSnapshot.reason — 이 스냅샷이 왜 생성됐는지(감사용)."""

    PRICING_INITIALIZED = "PRICING_INITIALIZED"
    ECONOMICS_UPDATE = "ECONOMICS_UPDATE"
    PRICE_CHANGE_APPROVED = "PRICE_CHANGE_APPROVED"
    SETTLEMENT_RECONCILED = "SETTLEMENT_RECONCILED"


class ReconciliationStatus:
    """
    SettlementReconciliation.status(요구사항 4/5).

    PENDING_SETTLEMENT: 아직 이 Order에 연결된 Settlement가 없다.
    MATCHED: 기대 net_amount와 실제 net_amount가 정확히 일치한다.
    MISMATCH: 불일치가 감지됐다 — 자동 보정하지 않는다(CLAUDE.md
      최상위 규칙). 관리자가 확인 후 명시적으로만 해소할 수 있다.
    HELD: 관리자가 조사를 위해 수동으로 보류시켰다(정산 자체와 별개로
      대사 결과 자체를 보류 표시).
    """

    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"
    HELD = "HELD"


# --------------------------------------------------
# 실측이 아니라 마지막으로 알려진 예상값을 대신 사용한 비용 성분
# (요구사항 2 — "실제" 마진이라도 어떤 요소는 추정일 수 있음을 숨기지
# 않는다). MarginSnapshot.estimated_components_json에 이 값들의 부분
# 집합이 JSON 배열로 저장된다.
# --------------------------------------------------

class EstimatedComponent:

    COST_OF_GOODS = "cost_of_goods"
    CHANNEL_FEE = "channel_fee"
    PAYMENT_FEE = "payment_fee"
    SHIPPING_COST = "shipping_cost"
    PACKAGING_COST = "packaging_cost"
    AD_COST = "ad_cost"
    RETURN_RESERVE = "return_reserve"
    TAX = "tax"


__all__ = [
    "PriceChangeStatus",
    "MarginType",
    "MarginSnapshotReason",
    "ReconciliationStatus",
    "EstimatedComponent",
]
