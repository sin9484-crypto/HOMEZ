"""
=========================================================
Homez OS

File : app/domains/currency/constants.py

2026-09-10 Phase 9(HOMEZ_USER_OPERATION_SETTINGS.md 6번 — "초기 환율
변동 허용률: 2%, 추후 환율을 자동 모니터링하는 기능을 개발한다")
— High 결함 #8("환율(currency/exchange) 도메인이 완전히 빈
스캐폴딩")을 메운다.
=========================================================
"""

from __future__ import annotations

# 문서 원문 그대로의 초기값 — 회사가 별도로 설정한 적이 없을 때만
# 쓰는 기본값이다(app/domains/currency/service.py::
# ExchangeRateService.get_tolerance_percent 참고).
DEFAULT_EXCHANGE_RATE_TOLERANCE_PERCENT = 2.0

# 실제로 값을 채운 적 있는 통화만 나열한다 — HOMEZ가 실제로 다루는
# 것으로 확인된 시장(국내 KRW, 문서 4-2가 언급한 해외 매입처 대금
# 통화 후보)만 포함한다. 이 목록에 없는 통화 코드로 record_rate()를
# 호출해도 막지 않는다(ISO 4217 코드 형식만 검증) — "아직 안 써본
# 통화"를 시스템이 미리 차단할 이유가 없다.
KNOWN_CURRENCIES = ("KRW", "USD", "CNY", "JPY", "EUR")

__all__ = [
    "DEFAULT_EXCHANGE_RATE_TOLERANCE_PERCENT",
    "KNOWN_CURRENCIES",
]
