"""
=========================================================
Homez OS

File : app/domains/diagnostics/redaction.py

Gate Y-5(2026-08-12) — 진단 내보내기가 절대 실제 비밀정보를 포함하지
않도록 하는 이중 방어선.

1) known_secret_values(): `app/core/config.py`의 실제 비밀값을 담는
   9개 필드(SECRET_KEY 등)를 명시적으로 나열해, 그 "실제 값"이 어디서
   왔든(예: 로그에 실수로 찍힌 경우) 텍스트에서 통째로 사라지게
   한다 — 필드명 패턴이 아니라 런타임 값 자체로 매칭하므로 오탐지가
   없다(정확히 그 비밀값 문자열만 지운다).
2) redact_text()의 정규식 패턴: known_secret_values로 못 잡는 경우
   (예: 이 앱이 모르는 제3자 응답에 박힌 토큰)에 대비해
   "password=", "token:", "api_key=" 같은 key=value 형태를 일반적으로
   탐지해 값 부분만 지운다.

`_MIN_REDACTABLE_LENGTH`는 빈 문자열이나 매우 짧은 기본값(예: 로컬
개발 기본값 "dev")까지 전부 값 통째로 치환해 로그를 못 쓰게 만드는
것을 막기 위한 안전장치다 — 8자 미만은 known_secret_values 대상에서
제외한다(짧은 값은 애초에 실제 운영 비밀로 쓰기엔 너무 약해 정책상
허용되지 않는다는 전제와도 일치한다. app/core/password_policy.py
참고).
=========================================================
"""

from __future__ import annotations

import re

REDACTED_PLACEHOLDER = "***REDACTED***"

_MIN_REDACTABLE_LENGTH = 8

KNOWN_SECRET_SETTINGS_FIELDS = (
    "SECRET_KEY",
    "JWT_SECRET_KEY",
    "REFRESH_SECRET_KEY",
    "API_KEY_SECRET",
    "PASSWORD_PEPPER",
    "REDIS_PASSWORD",
    "SMTP_PASSWORD",
    "SMS_API_KEY",
    "SMS_API_SECRET",
)

_KEY_VALUE_SECRET_PATTERN = re.compile(
    r'(?i)(password|secret|token|api[_-]?key|credential|pepper)'
    r'(\s*["\']?\s*[:=]\s*["\']?)'
    r'([^\s"\',;]{3,})',
)


def known_secret_values(settings) -> list[str]:

    values = []

    for field_name in KNOWN_SECRET_SETTINGS_FIELDS:
        value = getattr(settings, field_name, None)
        if isinstance(value, str) and len(value) >= _MIN_REDACTABLE_LENGTH:
            values.append(value)

    return values


def redact_text(
    text: str,
    *,
    known_values: list[str] | None = None,
) -> str:

    if not text:
        return text

    redacted = text

    for value in known_values or []:
        if value:
            redacted = redacted.replace(value, REDACTED_PLACEHOLDER)

    redacted = _KEY_VALUE_SECRET_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED_PLACEHOLDER}",
        redacted,
    )

    return redacted


__all__ = [
    "REDACTED_PLACEHOLDER",
    "KNOWN_SECRET_SETTINGS_FIELDS",
    "known_secret_values",
    "redact_text",
]
