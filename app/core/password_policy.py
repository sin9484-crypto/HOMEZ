"""
=========================================================
Homez OS

File : app/core/password_policy.py

HOMEZ 전역 비밀번호 정책 검증 — `app/core/config.py`에 이미 정의된
`PASSWORD_MIN_LENGTH`/`PASSWORD_MAX_LENGTH`/`PASSWORD_REQUIRE_UPPER`/
`PASSWORD_REQUIRE_LOWER`/`PASSWORD_REQUIRE_NUMBER`/
`PASSWORD_REQUIRE_SPECIAL` 설정을 그대로 사용한다.

`scripts/create_homez_admin.py`가 자체적으로 동일한 로직을 이미
가지고 있다(이미 승인·테스트된 도구라 이번 작업에서 건드리지 않음) —
이 모듈은 신규로 추가되는 Desktop 최초 설정/계정 관리 API가 그
스크립트와 "동일한" 정책을 쓰도록 공유하는 목적이다. 두 구현의
검증 규칙은 완전히 동일하다(같은 설정 필드, 같은 조건).
=========================================================
"""

from __future__ import annotations

import string


def validate_password_policy(password: str, settings) -> list[str]:

    errors: list[str] = []

    if len(password) < settings.PASSWORD_MIN_LENGTH:
        errors.append(f"비밀번호는 최소 {settings.PASSWORD_MIN_LENGTH}자 이상이어야 합니다.")

    if len(password) > settings.PASSWORD_MAX_LENGTH:
        errors.append(f"비밀번호는 최대 {settings.PASSWORD_MAX_LENGTH}자를 넘을 수 없습니다.")

    if settings.PASSWORD_REQUIRE_UPPER and not any(c.isupper() for c in password):
        errors.append("비밀번호에 대문자를 최소 1개 포함해야 합니다.")

    if settings.PASSWORD_REQUIRE_LOWER and not any(c.islower() for c in password):
        errors.append("비밀번호에 소문자를 최소 1개 포함해야 합니다.")

    if settings.PASSWORD_REQUIRE_NUMBER and not any(c.isdigit() for c in password):
        errors.append("비밀번호에 숫자를 최소 1개 포함해야 합니다.")

    if settings.PASSWORD_REQUIRE_SPECIAL and not any(
        c in string.punctuation for c in password
    ):
        errors.append("비밀번호에 특수문자를 최소 1개 포함해야 합니다.")

    return errors


__all__ = ["validate_password_policy"]
