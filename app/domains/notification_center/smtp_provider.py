"""
=========================================================
Homez OS

File : app/domains/notification_center/smtp_provider.py

2026-11-01 — 사용자 결정 D5: 아침 브리핑을 이메일로도 받는다. 사용자가 정한 메일 서버(SMTP)로 실제 발송하는 Provider.
이 Provider는 아침 브리핑(`MorningBriefService`)과 설정 화면의 시험 발송에서만 만든다 — 다른 알림의 이메일 경로
(`router._get_notification_email_provider`)는 그대로 Null이라, 다른 알림은 이메일로 나가지 않는다.

  - 암호화 필수: STARTTLS 또는 SSL만. 평문 접속·인증서 검증 끄기는 없다.
  - 승인 센터: 한 통마다 허용(`EMAIL`/`WRITE`/`BRIEF_EMAIL`, 횟수형) 1회. 허용이 없거나 비상 정지면 접속하지 않는다.
  - 한 통에 접속 한 번: 호출자(`_send_email_now`)가 실패하면 같은 메시지로 3번까지 다시 부르지만, 여기서는 첫 실패를 그대로 돌려준다
    (비밀번호가 틀렸을 때 연달아 로그인해 메일 계정이 잠기지 않도록, 허용 횟수를 한 통에 하나만 쓰도록).
  - 오류 글에는 고정 코드와 SMTP 응답 번호만 남긴다(서버 응답 원문·계정·비밀번호를 기록에 남기지 않는다).
=========================================================
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

from app.domains.notification_center.email_provider import NotificationEmailMessage
from app.domains.notification_center.email_provider import NotificationEmailProvider

SECURITY_STARTTLS = "STARTTLS"
SECURITY_SSL = "SSL"
SMTP_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class SmtpConfig:

    host: str
    port: int
    security: str
    login: str
    sender: str


def mask_address(address: str) -> str:
    local, _, domain = str(address or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return f"SMTP_AUTH_FAILED:{exc.smtp_code}"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"SMTP_REJECTED:{exc.smtp_code}"
    if isinstance(exc, ssl.SSLError):
        return "SMTP_TLS_FAILED"
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "SMTP_TLS_NOT_SUPPORTED"
    return f"SMTP_ERROR:{type(exc).__name__}"


class SmtpNotificationEmailProvider(NotificationEmailProvider):

    def __init__(self, config: SmtpConfig, password: str | None, gate, *, smtp_factory: Callable | None = None, ssl_factory: Callable | None = None):
        self.config = config
        self._password = password
        self._gate = gate
        self._smtp_factory = smtp_factory or smtplib.SMTP
        self._ssl_factory = ssl_factory or smtplib.SMTP_SSL
        self._failed_message = None
        self._failed_error: RuntimeError | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.config.host and self.config.sender and self._password)

    def _deliver(self, message: NotificationEmailMessage) -> None:
        mail = EmailMessage()
        mail["Subject"] = message.subject
        mail["From"] = self.config.sender
        mail["To"] = message.to_email
        mail.set_content(message.body_text)
        context = ssl.create_default_context()
        if self.config.security == SECURITY_SSL:
            client = self._ssl_factory(self.config.host, self.config.port, timeout=SMTP_TIMEOUT_SECONDS, context=context)
        elif self.config.security == SECURITY_STARTTLS:
            client = self._smtp_factory(self.config.host, self.config.port, timeout=SMTP_TIMEOUT_SECONDS)
        else:
            raise ValueError("암호화 방식이 없다")
        with client:
            if self.config.security == SECURITY_STARTTLS:
                client.starttls(context=context)
            client.login(self.config.login or self.config.sender, self._password)
            client.send_message(mail)

    def send(self, message: NotificationEmailMessage) -> None:
        from app.domains.external_call.service import ExternalCallNotAllowed

        if self._failed_message is message:
            raise self._failed_error
        try:
            try:
                ticket = self._gate.open(target=mask_address(message.to_email))
            except ExternalCallNotAllowed as exc:
                raise RuntimeError("EXTERNAL_CALL_BLOCKED_ESTOP" if exc.reason == "ESTOP_ACTIVE" else "EXTERNAL_ALLOWANCE_REQUIRED") from None
            try:
                self._deliver(message)
            except Exception as exc:  # noqa: BLE001 — 서버 응답 원문은 남기지 않는다
                self._gate.close(ticket, succeeded=False)
                raise RuntimeError(_safe_error(exc)) from None
            self._gate.close(ticket, succeeded=True)
        except RuntimeError as error:
            self._failed_message, self._failed_error = message, error
            raise


__all__ = ["SECURITY_SSL", "SECURITY_STARTTLS", "SmtpConfig", "SmtpNotificationEmailProvider", "mask_address"]
