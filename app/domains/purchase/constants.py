"""
=========================================================
Homez OS

File : app/domains/purchase/constants.py

Purchase 도메인 상수 — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from __future__ import annotations


class PurchaseStatus:
    """
    공급처 발주 상태 머신 — REQUESTED에서 시작해 CONFIRMED를 거쳐야만
    RECEIVED(입고 확정, Inventory.restock() + Funding.confirm_
    supplier_payment() 트리거)로 진행할 수 있다.
    """

    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        REQUESTED: {CONFIRMED, CANCELLED},
        CONFIRMED: {RECEIVED, CANCELLED},
        RECEIVED: set(),
        CANCELLED: set(),
    }


class PurchaseSubmissionStatus:
    """
    공급처 발주 전송 상태(2026-08-20 추가, additive) — Purchase.status
    와 별개의 축이다. Purchase.status=CONFIRMED(사용자 승인 완료)
    이후에만 전송을 시작할 수 있다.
    """

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"

    ALL = (PENDING, SUBMITTED, PARTIALLY_ACCEPTED, REJECTED, FAILED)
    TERMINAL = (SUBMITTED, PARTIALLY_ACCEPTED, REJECTED, FAILED)


__all__ = [
    "PurchaseStatus",
    "PurchaseSubmissionStatus",
]
