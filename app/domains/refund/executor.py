"""
=========================================================
Homez OS

File : app/domains/refund/executor.py

2026-09-10 Phase 8 — 환불 실행 계약과 Fake 구현. `app/domains/
payment/gateway.py`와 동일한 이유·동일한 설계: 실제 환불 실행은
이 세션의 절대 경계 밖이라 `FakeRefundExecutor`만 존재한다 — 실제
네트워크 호출이 전혀 없다.

`RefundExecutor.execute()`는 "돈이 실제로 돌아갔다"를 표현하는
행위다 — `RefundService.mark_executed()`는 `Refund.status`가
APPROVED(사람이 이미 승인한 상태)일 때만 이 executor를 호출한다.
=========================================================
"""

from __future__ import annotations

import secrets
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass


class RefundExecutionError(Exception):
    pass


@dataclass
class FakeExecutionResult:

    fake_execution_id: str
    fake_status: str  # "COMPLETED" (Fake는 항상 성공만 시뮬레이션한다)
    amount: float
    currency: str


class RefundExecutor(ABC):

    @abstractmethod
    def execute(
        self, refund_id: int, amount: float, currency: str,
    ) -> FakeExecutionResult:
        ...


class FakeRefundExecutor(RefundExecutor):

    def execute(
        self, refund_id: int, amount: float, currency: str,
    ) -> FakeExecutionResult:

        if amount <= 0:
            raise RefundExecutionError(
                "환불 금액은 0보다 커야 합니다.",
            )

        return FakeExecutionResult(
            fake_execution_id=f"fake_refund_exec_{secrets.token_hex(12)}",
            fake_status="COMPLETED",
            amount=amount,
            currency=currency,
        )


__all__ = [
    "RefundExecutionError",
    "FakeExecutionResult",
    "RefundExecutor",
    "FakeRefundExecutor",
]
