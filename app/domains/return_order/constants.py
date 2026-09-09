"""
=========================================================
Homez OS

File : app/domains/return_order/constants.py

ReturnOrder 도메인 상수 — V7 Gate 4(2026-08-15) 신규(기존 완전 빈
스캐폴딩 도메인을 처음으로 채운다).
=========================================================
"""

from __future__ import annotations


class ReturnOrderType:

    RETURN = "RETURN"
    EXCHANGE = "EXCHANGE"

    ALL = (RETURN, EXCHANGE)


class ReturnOrderStatus:

    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    RECEIVED = "RECEIVED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        REQUESTED: {APPROVED, REJECTED},
        APPROVED: {RECEIVED, REJECTED},
        RECEIVED: {COMPLETED},
        COMPLETED: set(),
        REJECTED: set(),
    }


__all__ = [
    "ReturnOrderType",
    "ReturnOrderStatus",
]
