"""
=========================================================
Homez OS

File : app/domains/purchase_task/email_provider.py

작업 G — 이메일 발송 Provider 계약. app/domains/account_recovery/
providers.py의 확립된 패턴(ABC + is_configured 게이트 + Null/Fake
구현)을 그대로 재사용한다. 실제 SMTP/이메일 API 연동은 이번 범위에
없다 — 미구성 상태를 있는 그대로 알린다(성공한 척하지 않는다).
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EmailMessage:

    to_email: str
    subject: str
    body_text: str
    event_type: str
    locale: str


class PurchaseTaskEmailProvider(ABC):

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """실제 발송 가능한 상태인지(자격증명·설정 완비 여부)."""

    @abstractmethod
    def send(self, message: EmailMessage) -> bool:
        """발송 성공 시 True. 실패는 예외로 알린다(호출자가
        PurchaseTaskEmailLog에 FAILED로 기록)."""


class NullPurchaseTaskEmailProvider(PurchaseTaskEmailProvider):
    """운영 Provider가 아직 구성되지 않은 기본값 — 항상 is_configured
    False."""

    @property
    def is_configured(self) -> bool:
        return False

    def send(self, message: EmailMessage) -> bool:
        raise RuntimeError(
            "NullPurchaseTaskEmailProvider는 실제로 발송할 수 없습니다 "
            "— is_configured를 먼저 확인해야 합니다.",
        )


class FakePurchaseTaskEmailProvider(PurchaseTaskEmailProvider):
    """테스트 전용 In-Memory Provider — 실제 네트워크 호출 없음."""

    def __init__(self, configured: bool = True, fail_next: bool = False):

        self._configured = configured
        self.fail_next = fail_next
        self.sent_messages: list[EmailMessage] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    def send(self, message: EmailMessage) -> bool:

        if not self._configured:
            raise RuntimeError("FakePurchaseTaskEmailProvider가 비활성 상태입니다.")

        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("FakePurchaseTaskEmailProvider 발송 실패(시뮬레이션).")

        self.sent_messages.append(message)
        return True


__all__ = [
    "EmailMessage",
    "PurchaseTaskEmailProvider",
    "NullPurchaseTaskEmailProvider",
    "FakePurchaseTaskEmailProvider",
]
