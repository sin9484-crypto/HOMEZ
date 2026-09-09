"""
=========================================================
Homez OS

File : app/core/sensitive_data.py

2026-08-30 V7 후속 안정화 Phase 4 — 민감정보 중앙 마스킹 유틸리티.

이 세션에서 확인된 사실: raw_response_snippet(coupang_live_provider.py)
은 이미 길이 제한 + "쿠팡이 우리 access_key/secret_key를 응답 본문에
되돌려줄 이유가 없다"는 근거로 안전하다고 판단돼 왔다 — 하지만 그
판단은 "길이만 제한하면 안전하다"는 가정에 의존했고, 이번 지시는
그 가정만으로 안전하다고 간주하지 않도록 명시적으로 요구한다. 이
모듈은 길이 제한과 별개로, 알려진 패턴(전화번호/토큰 형태 문자열)을
적극적으로 마스킹하는 별도 방어선을 추가한다.

원칙: "허용 필드 기반" — 화면·로그에 내보낼 dict는 기본적으로
아무 것도 통과시키지 않고, 명시적으로 허용한 키만 통과시킨다
(deny-by-default가 아니라 실수로 새 필드가 추가돼도 조용히 새어
나가지 않게 하는 allow-by-default의 반대 방향).
=========================================================
"""

from __future__ import annotations

import re
from typing import Any

_PHONE_PATTERN = re.compile(r"\d{2,4}[-\s]?\d{3,4}[-\s]?\d{4}")
# JWT 형태(header.payload.signature, 각 segment가 base64url) —
# Access/Refresh Token 원문이 로그 문자열에 실수로 섞여도 잡아낸다.
_JWT_PATTERN = re.compile(r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")


def mask_phone(value: str | None) -> str | None:
    """마지막 4자리만 남기고 마스킹한다(운영자 상세보기가 아닌 일반
    화면·로그 전용 — 운영자 상세보기는 이 함수를 거치지 않는다)."""

    if not value:
        return value
    if "*" in value:
        return value

    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "*" * len(digits)

    return "*" * (len(digits) - 4) + digits[-4:]


def mask_name(value: str | None) -> str | None:
    """주문 목록처럼 원문 이름이 필요하지 않은 화면용 마스킹."""
    if not value:
        return value
    if len(value) == 1:
        return "*"
    if len(value) == 2:
        return value[0] + "*"
    return value[0] + "*" * (len(value) - 2) + value[-1]


def mask_zipcode(value: str | None) -> str | None:
    """일반 응답에서는 우편번호 전체를 가린다."""
    return "*" * len(value) if value else value


def mask_address(value: str | None, *, keep_prefix_chars: int = 6) -> str | None:
    """앞부분(시/도·구 수준)만 남기고 나머지를 마스킹한다 — 완전히
    지우면 운영자가 배송 지역대를 확인할 수 없어 문제 진단이
    불가능해지므로, 상세 번지수만 가린다."""

    if not value:
        return value

    if len(value) <= keep_prefix_chars:
        return value[0] + "*" * (len(value) - 1) if len(value) > 1 else value

    return value[:keep_prefix_chars] + "*" * (len(value) - keep_prefix_chars)


def mask_secret(value: str | None) -> str | None:
    """Credential/Token류 — 값 존재 여부만 남기고 완전히 가린다(부분
    노출조차 하지 않는다 — 전화번호/주소와 다른 등급)."""

    if not value:
        return value

    return "***REDACTED***"


def redact_free_text(text: str | None) -> str | None:
    """로그·오류 메시지처럼 구조화되지 않은 자유 텍스트에서 알려진
    민감정보 패턴(전화번호 형태, JWT 형태)을 찾아 마스킹한다. 이
    함수를 통과했다고 "완전히 안전하다"고 보증하지 않는다 — 알려진
    패턴만 잡는 방어선 하나일 뿐이다(주소처럼 패턴화되지 않는 자유
    텍스트는 잡지 못한다 — 그런 값은 애초에 자유 텍스트 필드에 담지
    않고 구조화된 필드로 다뤄야 한다)."""

    if not text:
        return text

    masked = _JWT_PATTERN.sub("***REDACTED_TOKEN***", text)
    masked = _PHONE_PATTERN.sub(
        lambda m: mask_phone(m.group(0)) or m.group(0), masked,
    )

    return masked


def redact_dict(data: dict[str, Any], allowed_keys: frozenset[str]) -> dict[str, Any]:
    """허용 필드 기반 — `allowed_keys`에 없는 키는 결과에서 아예
    제외한다(값을 마스킹하는 게 아니라 키 자체가 없어진다). 새 필드가
    상위 도메인에 추가돼도, 이 화이트리스트를 함께 갱신하지 않으면
    자동으로 제외된다 — 실수로 새는 방향이 아니라 실수로 숨는
    방향으로 fail-closed."""

    return {k: v for k, v in data.items() if k in allowed_keys}


# 2026-08-30 후속 지시 — "주소를 추측하는 정규식은 추가하지 않는다.
# 구조화된 입력·응답에서 확인된 실제 민감값을 치환하는 방식으로
# 구현한다." 요구를 위한 필드 계약. 쿠팡 Payload/응답에서 연락처·
# 주소류로 확인된 키 이름만 나열한다 — 패턴 추측이 아니라 이미 알고
# 있는 필드 이름 목록이다.
KNOWN_CONTACT_ADDRESS_KEYS = frozenset({
    "companyContactNumber",
    "returnAddress",
    "returnAddressDetail",
    "returnZipCode",
    "afterServiceContactNumber",
})


def collect_known_sensitive_values(
    source: Any, keys: frozenset[str],
) -> list[str]:
    """`source`(dict/list가 섞인 구조화된 데이터, 예: 쿠팡에 보낸
    요청 payload 또는 쿠팡이 돌려준 응답 data)를 재귀적으로 훑어,
    키 이름이 `keys`에 있는 항목의 "값"만 모아 문자열 리스트로
    반환한다. 정규식으로 패턴을 추측하지 않는다 — 이미 구조적으로
    확인된 필드의 실제 값만 수집한다."""

    values: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in keys and isinstance(value, (str, int)) and not isinstance(value, bool):
                    text = str(value).strip()
                    if text:
                        values.append(text)
                elif isinstance(value, (dict, list)):
                    _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(source)
    return values


def redact_known_values(text: str | None, values: list[str]) -> str | None:
    """`text` 안에서 `values`의 각 항목과 정확히 일치하는 부분
    문자열만 치환한다(패턴 추측이 아니라 이미 알고 있는 실제 값의
    정확한 문자열 일치). 짧은 값이 긴 값의 부분 문자열로 먼저
    치환돼 뒤엉키는 것을 피하기 위해 긴 값부터 치환하고, 오탐을
    줄이기 위해 4자 미만 값은 치환 대상에서 제외한다."""

    if not text:
        return text

    masked = text
    for value in sorted(set(values), key=len, reverse=True):
        if len(value) < 4:
            continue
        masked = masked.replace(value, "[REDACTED_KNOWN_VALUE]")
    return masked


__all__ = [
    "mask_name",
    "mask_phone",
    "mask_address",
    "mask_zipcode",
    "mask_secret",
    "redact_free_text",
    "redact_dict",
    "KNOWN_CONTACT_ADDRESS_KEYS",
    "collect_known_sensitive_values",
    "redact_known_values",
]
