"""
=========================================================
Homez OS

File : app/domains/account_recovery/providers.py

비밀번호 재설정 이메일 발송 — 인터페이스 + Fake(테스트/미구성) 구현만
제공한다. 실제 SMTP/이메일 API 연동은 이번 범위에 없다 — 운영
Provider가 없으면 "구성되지 않음"을 있는 그대로 알린다(성공한
척하지 않는다).
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod


class PasswordResetDeliveryProvider(ABC):
    """
    실제 이메일 발송 Provider(예: SMTP, SES, SendGrid 등)가 구현해야
    하는 계약. `is_configured`가 False면 호출자는 이 Provider를 아예
    사용하지 않고 "이메일 복구 미구성" 경로로 빠져야 한다 — READY로
    잘못 표시하지 않기 위한 방어적 설계다.
    """

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """실제 발송 가능한 상태인지(자격증명·설정 완비 여부)."""

    @abstractmethod
    def send_password_reset_email(
        self,
        *,
        to_email: str,
        reset_link: str,
    ) -> bool:
        """
        비밀번호 재설정 링크 발송. 발송 성공 시 True. 이 메서드 자체가
        계정 존재 여부를 드러내는 예외를 던지지 않아야 한다 — 실패도
        호출자 쪽에서는 항상 동일한 일반 응답으로 흡수된다.
        """

    @abstractmethod
    def send_forgot_id_email(
        self,
        *,
        to_email: str,
        masked_login_id: str,
    ) -> bool:
        """아이디 찾기 — 마스킹된 로그인 ID 안내 발송."""


class NullPasswordResetDeliveryProvider(PasswordResetDeliveryProvider):
    """
    운영 Provider가 아직 구성되지 않은 기본값. `is_configured`가 항상
    False라서, 서비스 계층이 "이메일 복구가 구성되지 않았습니다"
    안내로 정확히 분기하게 만든다 — 성공한 것처럼 속이지 않는다.
    """

    @property
    def is_configured(self) -> bool:
        return False

    def send_password_reset_email(
        self,
        *,
        to_email: str,
        reset_link: str,
    ) -> bool:
        raise RuntimeError(
            "NullPasswordResetDeliveryProvider는 실제로 발송할 수 없습니다 "
            "— is_configured를 먼저 확인해야 합니다.",
        )

    def send_forgot_id_email(
        self,
        *,
        to_email: str,
        masked_login_id: str,
    ) -> bool:
        raise RuntimeError(
            "NullPasswordResetDeliveryProvider는 실제로 발송할 수 없습니다 "
            "— is_configured를 먼저 확인해야 합니다.",
        )


class FakePasswordResetDeliveryProvider(PasswordResetDeliveryProvider):
    """
    테스트 전용 In-Memory Provider. 실제 네트워크 호출을 전혀 하지
    않고, 발송 "했다고 기록만" 남긴다(테스트가 발송 여부/수신자만
    검증할 수 있도록) — 토큰 원문 등 민감 값은 기록하지 않는다.
    """

    def __init__(self, configured: bool = True):

        self._configured = configured
        self.sent_messages: list[dict] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    def send_password_reset_email(
        self,
        *,
        to_email: str,
        reset_link: str,
    ) -> bool:

        if not self._configured:
            raise RuntimeError("FakePasswordResetDeliveryProvider가 비활성 상태입니다.")

        self.sent_messages.append(
            {
                "kind": "PASSWORD_RESET",
                "to_email": to_email,
                # reset_link는 토큰 원문을 포함하므로 테스트 자산에도
                # 남기지 않는다 — 링크를 "받았다"는 사실만 기록한다.
                "link_received": bool(reset_link),
            },
        )

        return True

    def send_forgot_id_email(
        self,
        *,
        to_email: str,
        masked_login_id: str,
    ) -> bool:

        if not self._configured:
            raise RuntimeError("FakePasswordResetDeliveryProvider가 비활성 상태입니다.")

        self.sent_messages.append(
            {
                "kind": "FORGOT_ID",
                "to_email": to_email,
                "masked_login_id": masked_login_id,
            },
        )

        return True


__all__ = [
    "PasswordResetDeliveryProvider",
    "NullPasswordResetDeliveryProvider",
    "FakePasswordResetDeliveryProvider",
]
