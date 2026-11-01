"""
=========================================================
Homez OS

File : app/domains/notification_center/brief_email_service.py

2026-11-01 — 사용자 결정 D5: 아침 브리핑 이메일 설정(메일 서버·보내는 주소·받는 주소·켜기). 설정은 사용자별로 저장한다.

  - 비밀번호는 Windows 자격증명 저장소에만 둔다. DB·감사 기록·응답에 남기지 않고, 저장 여부만 알려 준다.
  - 켜면 '아침 브리핑' 이벤트의 이메일 즉시 발송을 켠다(기존 알림 설정과 같은 값). 끄면 앱 안 알림만 남는다.
  - 비밀번호·서버·주소가 다 있어야 켤 수 있다. 아침 브리핑 외의 알림은 이 설정으로 이메일이 나가지 않는다.
  - 시험 발송은 저장된 받는 주소로만, 승인 센터 허용 1회를 쓴다.
=========================================================
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ServiceUnavailableException
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError
from app.domains.notification_center.email_provider import NotificationEmailMessage
from app.domains.notification_center.smtp_provider import SECURITY_SSL
from app.domains.notification_center.smtp_provider import SECURITY_STARTTLS
from app.domains.notification_center.smtp_provider import SmtpConfig
from app.domains.notification_center.smtp_provider import SmtpNotificationEmailProvider
from app.domains.user_settings.repository import UserSettingRepository
from app.domains.user_settings.schema import SETTING_KEY_NOTIFICATION_PREFERENCES
from app.domains.user_settings.service import UserSettingService

BRIEF_EVENT = "DAILY_BRIEFING"
SETTINGS_SUBKEY = "brief_email"
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{1,251})[A-Za-z0-9]$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_PRINTABLE = re.compile(r"^[\x20-\x7e]{1,300}$")
_LOGIN = re.compile(r"^[\x21-\x7e]{1,254}$")


def credential_target(user_id: int) -> str:
    return f"HOMEZ/EMAIL/SMTP/{user_id}"


def email_gate(db: Session, company_id: int, user_id: int):
    from app.domains.external_call.constants import CallKind
    from app.domains.external_call.constants import CallPurpose
    from app.domains.external_call.constants import ExternalSystem
    from app.domains.external_call.gate import ExternalCallGate

    return ExternalCallGate(
        db, company_id, ExternalSystem.EMAIL, CallKind.WRITE, CallPurpose.BRIEF_EMAIL,
        actor_user_id=user_id, method="SMTP",
    )


class BriefEmailService:

    def __init__(self, db: Session, credential_store: CredentialStore | None = None, *, smtp_factory=None, ssl_factory=None):
        self.db = db
        self._store = credential_store
        self._smtp_factory, self._ssl_factory = smtp_factory, ssl_factory

    def store(self) -> CredentialStore:
        if self._store is None:
            from app.core.windows_credential_store import WindowsCredentialStore

            self._store = WindowsCredentialStore()
        return self._store

    # ---------------- 설정 ----------------
    def _blob(self, user_id: int, company_id: int) -> tuple[dict, int]:
        row = UserSettingRepository(self.db).get_for_user(user_id, company_id, SETTING_KEY_NOTIFICATION_PREFERENCES)
        if row is None:
            return {}, 0
        try:
            value = json.loads(row.setting_value)
        except ValueError:
            return {}, row.version
        return (value if isinstance(value, dict) else {}), row.version

    def settings(self, user_id: int, company_id: int) -> dict:
        value = self._blob(user_id, company_id)[0].get(SETTINGS_SUBKEY)
        return value if isinstance(value, dict) else {}

    def _password(self, user_id: int) -> str | None:
        try:
            payload = self.store().read(credential_target(user_id))
        except (CredentialNotFoundError, CredentialStoreError):
            return None
        password = payload.get("password") if isinstance(payload, dict) else None
        return password if isinstance(password, str) and password else None

    def _password_saved(self, user_id: int) -> bool:
        try:
            return bool(self.store().exists(credential_target(user_id)))
        except CredentialStoreError:
            return False

    def last_delivery(self, user_id: int, company_id: int) -> dict | None:
        from app.domains.notification_center.model import NotificationEmailLog

        row = (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.company_id == company_id, NotificationEmailLog.user_id == user_id, NotificationEmailLog.event_code == BRIEF_EVENT)
            .order_by(NotificationEmailLog.id.desc())
            .first()
        )
        if row is None:
            return None
        return {"status": row.status, "at": row.created_at.isoformat(timespec="seconds") if row.created_at else None, "detail": row.error_detail}

    def status(self, user_id: int, company_id: int) -> dict[str, Any]:
        s = self.settings(user_id, company_id)
        saved = self._password_saved(user_id)
        complete = bool(s.get("host") and s.get("port") and s.get("security") and s.get("sender") and s.get("to") and saved)
        return {
            "enabled": bool(s.get("enabled")) and complete, "complete": complete, "password_saved": saved,
            "host": s.get("host") or "", "port": s.get("port") or 587, "security": s.get("security") or SECURITY_STARTTLS,
            "login": s.get("login") or "", "sender": s.get("sender") or "", "to": s.get("to") or "",
            "last": self.last_delivery(user_id, company_id),
        }

    def save(
        self, user_id: int, company_id: int, *, host: str, port: int, security: str, login: str, sender: str, to: str,
        enabled: bool, password: str | None = None,
    ) -> dict[str, Any]:
        from app.domains.notification_center.repository import NotificationDeliveryRepository

        host, login, sender, to = (host or "").strip(), (login or "").strip(), (sender or "").strip(), (to or "").strip()
        if not _HOST.match(host):
            raise BadRequestException("메일 서버 주소 형식이 올바르지 않습니다.")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise BadRequestException("포트는 1~65535 사이의 숫자여야 합니다.")
        if security not in (SECURITY_STARTTLS, SECURITY_SSL):
            raise BadRequestException("암호화 방식은 STARTTLS 또는 SSL만 쓸 수 있습니다.")
        if login and not _LOGIN.match(login):
            raise BadRequestException("로그인 아이디 형식이 올바르지 않습니다.")
        if not _EMAIL.match(sender) or not _EMAIL.match(to):
            raise BadRequestException("보내는 주소·받는 주소는 이메일 형식이어야 합니다.")
        if password:
            if not _PRINTABLE.match(password):
                raise BadRequestException("비밀번호 형식이 올바르지 않습니다.")
            try:
                self.store().save(credential_target(user_id), {"password": password})
            except CredentialStoreError:
                raise ServiceUnavailableException("비밀번호를 안전 저장소에 저장하지 못했습니다.") from None
        if enabled and not self._password_saved(user_id):
            raise BadRequestException("비밀번호를 저장해야 이메일을 켤 수 있습니다.")

        blob, version = self._blob(user_id, company_id)
        blob[SETTINGS_SUBKEY] = {"enabled": bool(enabled), "host": host, "port": port, "security": security, "login": login, "sender": sender, "to": to}
        try:
            UserSettingService(UserSettingRepository(self.db)).put(user_id, company_id, SETTING_KEY_NOTIFICATION_PREFERENCES, blob, version, 1)
        except ConflictException:
            self.db.rollback()
            raise
        NotificationDeliveryRepository(self.db).upsert_event_preference(company_id, user_id, BRIEF_EVENT, email_immediate_override=bool(enabled))
        return self.status(user_id, company_id)

    # ---------------- 보내기 ----------------
    def _provider(self, user_id: int, company_id: int, s: dict) -> SmtpNotificationEmailProvider:
        config = SmtpConfig(host=s.get("host") or "", port=int(s.get("port") or 0), security=s.get("security") or "", login=s.get("login") or "", sender=s.get("sender") or "")
        kwargs = {}
        if self._smtp_factory is not None:
            kwargs["smtp_factory"] = self._smtp_factory
        if self._ssl_factory is not None:
            kwargs["ssl_factory"] = self._ssl_factory
        return SmtpNotificationEmailProvider(config, self._password(user_id), email_gate(self.db, company_id, user_id), **kwargs)

    def delivery_for(self, user_id: int, company_id: int) -> tuple[SmtpNotificationEmailProvider, str] | None:
        """(Provider, 받는 주소). 켜 두지 않았으면 None — 이때는 키 저장소도 열지 않는다."""

        s = self.settings(user_id, company_id)
        if not s.get("enabled") or not s.get("to"):
            return None
        return self._provider(user_id, company_id, s), s["to"]

    def send_test(self, user_id: int, company_id: int) -> dict[str, Any]:
        s = self.settings(user_id, company_id)
        if not s.get("to") or not s.get("host"):
            raise BadRequestException("메일 서버와 받는 주소를 먼저 저장해 주세요.")
        provider = self._provider(user_id, company_id, s)
        if not provider.is_configured:
            raise BadRequestException("비밀번호를 먼저 저장해 주세요.")
        message = NotificationEmailMessage(
            to_email=s["to"], subject="[HOMEZ] 아침 브리핑 이메일 시험",
            body_text="HOMEZ 아침 브리핑 이메일 설정 시험입니다. 이 이메일에서는 승인·결제할 수 없습니다.",
            event_code="TEST", locale="ko-KR",
        )
        try:
            provider.send(message)
        except RuntimeError as exc:
            return {"sent": False, "reason": str(exc)}
        return {"sent": True, "reason": None}


__all__ = ["BRIEF_EVENT", "BriefEmailService", "credential_target", "email_gate"]
