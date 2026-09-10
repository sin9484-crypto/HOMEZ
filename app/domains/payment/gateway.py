"""
=========================================================
Homez OS

File : app/domains/payment/gateway.py

2026-09-10 Phase 7 — 결제 Provider 계약과 Fake 구현.

**이 세션(그리고 이번 Phase)에서 실제 결제 Provider는 절대 구현하지
않는다** — HOMEZ V7 개인 베타 연속 구현 지시서의 "절대 중단 경계"가
"실제 외부 API 호출", "실제 결제·발주·환불"을 명시적으로 금지한다.
`FakePaymentProvider`는 실제 네트워크 호출을 전혀 하지 않고, 항상
로컬에서만 값을 만들어낸다 — `requests`/`httpx`/`urllib` 어떤 것도
import하지 않는다(이 파일 자체를 읽으면 바로 확인 가능하게, 의도적
으로 그런 구조를 만들지 않았다).

`PaymentProvider`는 미래에 실제 Provider(예: 토스페이먼츠, PayPal
SDK 등)를 붙일 때 구현해야 할 인터페이스 계약이다 — 지금은
`FakePaymentProvider` 하나만 이 계약을 구현한다.
=========================================================
"""

from __future__ import annotations

import secrets
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass


class PaymentGatewayError(Exception):
    pass


@dataclass
class FakeChargeResult:
    """
    Fake Provider가 반환하는 "결제 실행 시뮬레이션" 결과. 필드 이름에
    항상 `fake_`를 접두어로 붙여, 로그나 화면에 실수로 노출돼도 실제
    결제 결과로 오인되지 않게 한다.
    """

    fake_charge_id: str
    fake_status: str  # "SUCCEEDED" (Fake Provider는 항상 성공만 시뮬레이션한다)
    amount: float
    currency: str


class PaymentProvider(ABC):
    """
    미래의 실제 Provider도 이 계약을 구현해야 한다. `tokenize()`는
    원문 결제수단 정보(카드번호 등)를 받아 Provider 토큰만 반환하고
    원문은 어디에도 보관하지 않는다 — 호출자(PaymentMethodService)도
    이 반환값만 저장한다.
    """

    @abstractmethod
    def tokenize(
        self, method_type: str, raw_details: dict,
    ) -> str:
        ...

    @abstractmethod
    def charge(
        self, token_ref: str, amount: float, currency: str,
    ) -> FakeChargeResult:
        ...


class FakePaymentProvider(PaymentProvider):
    """
    실제 네트워크 호출이 전혀 없는 검증 전용 구현. `tokenize()`가
    받은 `raw_details`는 이 메서드 안에서만 잠깐 존재하고 반환되지
    않는다 — 호출자가 이 반환값(토큰 문자열) 외에 원문을 다시 얻을
    방법이 없다.
    """

    def tokenize(
        self, method_type: str, raw_details: dict,
    ) -> str:

        return f"fake_tok_{method_type.lower()}_{secrets.token_hex(12)}"

    def charge(
        self, token_ref: str, amount: float, currency: str,
    ) -> FakeChargeResult:

        if amount <= 0:
            raise PaymentGatewayError(
                "결제 금액은 0보다 커야 합니다.",
            )

        return FakeChargeResult(
            fake_charge_id=f"fake_charge_{secrets.token_hex(12)}",
            fake_status="SUCCEEDED",
            amount=amount,
            currency=currency,
        )


__all__ = [
    "PaymentGatewayError",
    "FakeChargeResult",
    "PaymentProvider",
    "FakePaymentProvider",
]
