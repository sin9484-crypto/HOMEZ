"""
=========================================================
Homez OS

File : app/core/audit_log.py

인증·로그아웃 감사 기록 — 경량 로거 기반.

`app/domains/audit/**`(model/repository/service)는 현재 전부 빈
스캐폴딩이라(2026-07-30 확인), 이번 Desktop 로그인 흐름 작업에서 그
전체 Domain을 새로 만드는 것은 범위를 벗어난다. 대신 기존
`app/core/logger.py`의 rotating 파일 로거를 재사용해 로그인 성공/
실패/로그아웃 이벤트를 구조화된 한 줄로 기록한다.

비밀번호, 토큰, 비밀키, 해시 값은 어떤 이유로도 기록하지 않는다.
=========================================================
"""

from app.core.logger import get_logger

_audit_logger = get_logger("homez.auth.audit")

_REDACTED_KEYS = {"password", "token", "access_token", "refresh_token", "secret", "hash"}


def log_auth_event(
    event: str,
    *,
    username: str | None = None,
    user_id: int | None = None,
    reason: str | None = None,
    client_ip: str | None = None,
    **extra: object,
) -> None:
    """
    인증 관련 이벤트 1건을 기록한다. 호출자가 실수로 민감 필드를
    넘기더라도(`password`, `token` 등) 값 자체는 기록하지 않고
    "[REDACTED]"로 대체한다 — 방어적 이중 안전장치.
    """

    fields = {
        "event": event,
        "username": username,
        "user_id": user_id,
        "reason": reason,
        "client_ip": client_ip,
    }

    for key, value in extra.items():
        if key.lower() in _REDACTED_KEYS:
            fields[key] = "[REDACTED]"
        else:
            fields[key] = value

    rendered = " ".join(
        f"{key}={value}" for key, value in fields.items() if value is not None
    )

    _audit_logger.info("AUTH_EVENT %s", rendered)


__all__ = ["log_auth_event"]
