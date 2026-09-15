"""
=========================================================
Homez OS

File : app/domains/price_stock_safety/constants.py

2026-09-10 Phase 10 — 문서 원문 초기값.
=========================================================
"""

from __future__ import annotations

# 문서 47번 "가격은 주 1회 정기 검토한다" — 초기값.
DEFAULT_PRICE_REVIEW_CYCLE_DAYS = 7

# 2026-09-15 Phase 9D(8-5) — "가격정보 기본 유효시간은 30분".
DEFAULT_PRICE_CACHE_TTL_MINUTES = 30

# 2026-09-15 Phase 9E(8-6) — "재고정보 기본 유효시간은 10분".
DEFAULT_STOCK_CACHE_TTL_MINUTES = 10

__all__ = [
    "DEFAULT_PRICE_REVIEW_CYCLE_DAYS",
    "DEFAULT_PRICE_CACHE_TTL_MINUTES",
    "DEFAULT_STOCK_CACHE_TTL_MINUTES",
]
