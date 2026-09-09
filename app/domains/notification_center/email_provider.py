"""
=========================================================
Homez OS

File : app/domains/notification_center/email_provider.py

Gate PT-3(2026-08-23 17차 지시) — 중앙 알림 이벤트 이메일 발송
Provider 계약. purchase_task/email_provider.py와 동일한 철학(Null이
기본값, Fake는 테스트 전용, 실제 SMTP/Transactional Adapter 연결은
이번 라운드 범위 밖)을 따르되, 이 파일은 purchase_task 도메인이
아닌 중앙 notification_center 카탈로그 이벤트 전용이다 — 두 시스템은
의도적으로 분리돼 있다(이미 완성된 purchase_task 쪽을 건드리지
않는다는 원칙).
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass


@dataclass
class NotificationEmailMessage:

    to_email: str
    subject: str
    body_text: str
    event_code: str
    locale: str


class NotificationEmailProvider(ABC):

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """실제 발송 가능한 상태인지. False면 호출자는
        PROVIDER_NOT_CONFIGURED로 정직하게 분기해야 한다."""

    @abstractmethod
    def send(self, message: NotificationEmailMessage) -> None:
        """실패 시 예외를 던진다(호출자가 재시도/기록을 담당)."""


class NullNotificationEmailProvider(NotificationEmailProvider):
    """운영 Provider가 아직 연결되지 않은 기본값 — 이 Gate가 끝난
    뒤에도 실제 SMTP Credential 입력·연결은 별도 승인 전까지
    수행하지 않는다."""

    @property
    def is_configured(self) -> bool:
        return False

    def send(self, message: NotificationEmailMessage) -> None:
        raise RuntimeError(
            "NullNotificationEmailProvider는 실제로 발송할 수 없습니다 "
            "— is_configured를 먼저 확인해야 합니다.",
        )


class FakeNotificationEmailProvider(NotificationEmailProvider):
    """테스트 전용 In-Memory Provider. 실제 네트워크 호출 없음."""

    def __init__(self, configured: bool = True, fail_next: bool = False):

        self._configured = configured
        self.fail_next = fail_next
        self.sent_messages: list[NotificationEmailMessage] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    def send(self, message: NotificationEmailMessage) -> None:

        if not self._configured:
            raise RuntimeError("FakeNotificationEmailProvider가 비활성 상태입니다.")

        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("FakeNotificationEmailProvider 강제 실패(테스트).")

        self.sent_messages.append(message)


__all__ = [
    "NotificationEmailMessage",
    "NotificationEmailProvider",
    "NullNotificationEmailProvider",
    "FakeNotificationEmailProvider",
]
