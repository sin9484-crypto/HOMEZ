"""
=========================================================
Homez OS

File : app/domains/platform_alert/provider.py

2026-09-16 개인 베타 잔여 작업(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
10-18) — 서버 관리자 알림 발송 Provider 계약.
`notification_center/email_provider.py`와 동일한 철학: 기본값은
아무것도 실제로 보내지 않는 `NullPlatformAlertProvider`이고, 실제
이메일/SMS Adapter(SMTP·문자 발송사 등)는 이 Phase에서 연결하지
않는다(공통 규칙: "외부 이메일·문자 Provider는 Fake로만"). 테스트는
`FakePlatformAlertProvider`만 사용한다 — 실제 네트워크 없음.
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass

from app.domains.platform_alert.model import PLATFORM_ALERT_CONTACT_TYPE_EMAIL


@dataclass
class PlatformAlertMessage:

    channel: str  # PLATFORM_ALERT_CONTACT_TYPE_EMAIL 또는 _SMS
    to: str
    subject: str
    body: str
    event_code: str


class PlatformAlertProvider(ABC):

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """실제 발송 가능한 상태인지. False면 호출자는 그 사실을
        정직하게 상태(NO_PROVIDER_CONFIGURED)로 남겨야 한다."""

    @abstractmethod
    def send(self, message: PlatformAlertMessage) -> None:
        """실패 시 예외를 던진다(호출자가 재시도/기록을 담당)."""


class NullPlatformAlertProvider(PlatformAlertProvider):
    """운영 Provider가 아직 연결되지 않은 기본값 — 실제 SMTP/SMS
    Credential 등록·연결은 별도 사용자 승인 전까지 수행하지 않는다."""

    @property
    def is_configured(self) -> bool:
        return False

    def send(self, message: PlatformAlertMessage) -> None:
        raise RuntimeError(
            "NullPlatformAlertProvider는 실제로 발송할 수 없습니다 — "
            "is_configured를 먼저 확인해야 합니다.",
        )


class FakePlatformAlertProvider(PlatformAlertProvider):
    """테스트 전용 In-Memory Provider. 실제 네트워크 호출 없음.
    `fail_next`를 True로 두면 다음 1회 send()만 실패시킨다(부분
    실패 시나리오 검증용)."""

    def __init__(self, configured: bool = True, fail_next: bool = False):

        self._configured = configured
        self.fail_next = fail_next
        self.sent_messages: list[PlatformAlertMessage] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    def send(self, message: PlatformAlertMessage) -> None:

        if not self._configured:
            raise RuntimeError("FakePlatformAlertProvider가 비활성 상태입니다.")

        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("FakePlatformAlertProvider 강제 실패(테스트).")

        self.sent_messages.append(message)


__all__ = [
    "PlatformAlertMessage",
    "PlatformAlertProvider",
    "NullPlatformAlertProvider",
    "FakePlatformAlertProvider",
    "PLATFORM_ALERT_CONTACT_TYPE_EMAIL",
]
