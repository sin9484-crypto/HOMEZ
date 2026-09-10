"""
=========================================================
Homez OS

File : app/domains/currency/model.py

2026-09-10 Phase 9 — ExchangeRate(환율, append-only) +
ExchangeRateToleranceSetting(허용률, append-only). 둘 다
`function_automation_states`/`payment_auto_limits`와 동일한
append-only 패턴(최신 행이 현재 값) — 값이 바뀐 이력 자체가 감사
기록이다.

환율은 **수동 입력만** 지원한다 — 실제 외부 환율 API를 호출하는
코드는 이 세션 어디에도 없다(절대 경계: 실제 외부 API 호출 금지).
"추후 환율을 자동 모니터링하는 기능을 개발한다"(문서 6번)는 이
Phase 범위 밖으로 명시적으로 남긴다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class ExchangeRate(Base):
    """
    base_currency 1단위 = rate * quote_currency. 예: base=USD,
    quote=KRW, rate=1350.0 이면 1달러=1350원. (base_currency,
    quote_currency) 조합별로 가장 최근 행(id 최대)이 현재 환율이다.
    """

    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    base_currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
    )

    quote_currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
    )

    rate: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    # 예: "MANUAL_ADMIN_ENTRY" — 수동 입력만 지원(모듈 docstring 참고).
    source: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    # 논리 참조 (users.id); 시스템 시드값 등은 None.
    recorded_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class ExchangeRateToleranceSetting(Base):
    """
    회사별 환율 변동 허용률(%). 값이 없으면(행이 하나도 없으면)
    `app/domains/currency/constants.py::
    DEFAULT_EXCHANGE_RATE_TOLERANCE_PERCENT`(문서 원문 초기값 2%)를
    쓴다 — PaymentAutoLimit(값이 없으면 항상 거부, fail-closed)과는
    의도적으로 다른 기본값 정책이다: 이 값은 "차단 여부를 결정하는
    금액 한도"가 아니라 "얼마나 관대하게 볼지"를 결정하는 허용률
    이고, 문서 자신이 이미 명확한 초기값(2%)을 못박아 뒀기 때문에
    그 값을 그대로 쓰는 것이 fail-closed보다 더 정직한 기본값이다.
    """

    __tablename__ = "exchange_rate_tolerance_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    tolerance_percent: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    # 논리 참조 (users.id)
    set_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    set_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


__all__ = ["ExchangeRate", "ExchangeRateToleranceSetting"]
