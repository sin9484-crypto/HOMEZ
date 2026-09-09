"""
=========================================================
Homez OS

File : app/domains/store_connection/adapters/coupang_signing.py

Gate 3(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5) — 쿠팡 WING Open API
HMAC 서명. 공식 1차 문서에서 확인한 내용만 구현한다(추측 없음).

확인 출처(전부 developers.coupang.com, 2026-08-01 fetch):
- https://developers.coupang.com/hc/en-us/articles/360033461914-Creating-HMAC-Signature
  * datetime 형식: "yyMMddTHHmmssZ"(UTC)
  * message = datetime + method + path + query — 구분자 없이 그대로
    이어붙인다(PHP `$datetime.$method.$path.$query`, Python
    `datetime+method+path+query`, C#
    `String.Format("{0}{1}{2}{3}", datetime, method, path, query)`
    세 언어 예제가 전부 동일하게 확인됨).
  * 해시 함수: HmacSHA256(=HMAC-SHA256)
  * Authorization 헤더: "CEA algorithm=HmacSHA256, access-key=
    {accessKey}, signed-date={datetime}, signature={hexdigest}"
  * Content-Type: "application/json;charset=UTF-8"
- https://developers.coupang.com/hc/en-us/articles/27482151049625-How-many-days-is-the-key-valid-for-OpenAPI
  * 키 유효기간: 180일(6개월). 만료 후 재발급 필요.
- https://developers.coupang.com/hc/en-us/articles/20300466525593-Is-it-possible-to-reissue-the-Open-API-Key-when-its-validity-period-expires
  * 재발급 가능("Yes, reissue is possible").

미확정(이 파일에서 구현하지 않음, 추측 금지):
- 정확한 호출 제한(rate limit) 수치 — 공식 문서에서 구체적 숫자를
  찾지 못했다. 429 응답이 오면 RATE_LIMITED로 정규화만 하고, 재시도
  간격을 특정 문서화된 숫자에 맞추지 않는다(범용 지수 백오프만 적용).
- 상품 등록 등 실제 판매 관련 엔드포인트의 요청/응답 스키마 — 이
  파일은 서명 생성만 담당하고, 어떤 구체적 비즈니스 엔드포인트도
  호출하지 않는다.

이 모듈은 순수 함수만 포함한다 — 네트워크 호출이 없다(관련 정적 검증은
tests/test_store_connection_production_adapter.py).
=========================================================
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

ALGORITHM = "HmacSHA256"


def format_signed_date(now: datetime | None = None) -> str:
    """공식 문서의 "yyMMddTHHmmssZ"(UTC) 형식."""

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)

    return now_utc.strftime("%y%m%dT%H%M%SZ")


def build_signing_message(
    signed_date: str, method: str, path: str, query: str = "",
) -> str:
    """
    공식 문서 예제 3개 언어(PHP/Python/C#) 전부 구분자 없는 단순
    이어붙이기였다 — 이 함수도 동일하게 구현한다.
    """

    return f"{signed_date}{method.upper()}{path}{query}"


def compute_signature(secret_key: str, message: str) -> str:

    return hmac.new(
        secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def build_authorization_header(
    access_key: str, secret_key: str, method: str, path: str,
    query: str = "", now: datetime | None = None,
) -> str:
    """
    Secret 원문은 이 함수의 인자로만 잠깐 존재하고 반환값(서명된 헤더
    문자열)에는 포함되지 않는다 — secret_key 자체를 로그에 남기면 안
    된다(호출자 책임).
    """

    signed_date = format_signed_date(now)
    message = build_signing_message(signed_date, method, path, query)
    signature = compute_signature(secret_key, message)

    return (
        f"CEA algorithm={ALGORITHM}, access-key={access_key}, "
        f"signed-date={signed_date}, signature={signature}"
    )


__all__ = [
    "ALGORITHM",
    "format_signed_date",
    "build_signing_message",
    "compute_signature",
    "build_authorization_header",
]
