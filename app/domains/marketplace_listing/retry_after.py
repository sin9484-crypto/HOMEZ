"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/retry_after.py

Gate H(2026-08-07) — Retry-After 정규화.

Provider(쿠팡·네이버 등)가 429와 함께 알려주는 "언제 다시 시도해도
되는지"는 세 가지 형태로 올 수 있다.
  - Retry-After 헤더의 초 단위 정수(예: "120")
  - Retry-After 헤더의 HTTP-date(RFC 7231, 예:
    "Wed, 21 Oct 2026 07:28:00 GMT")
  - 응답 본문(JSON)의 구조화 필드(예: {"retry_after": 120})

Provider별 원문 파싱은 이 파일이 전담한다 — Domain(status_sync_
service.py 등)에는 정규화된 정수 초(`retry_after_seconds`)와 그로부터
계산한 절대 시각(`retry_available_at`, naive UTC — 이 저장소의 기존
datetime 관례를 그대로 따른다)만 전달한다. Provider 원문 문자열은
여기서 소비되고 밖으로 나가지 않는다.

신뢰할 수 없는 값(음수·NaN·무한대·파싱 불가)은 조용히 통과시키지
않고 None으로 거부한다 — 호출자가 "값을 못 받았다"로 처리하게 한다.
너무 큰 값은 상한으로 잘라낸다(무기한 차단 방지).
=========================================================
"""

from __future__ import annotations

import math
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from email.utils import parsedate_to_datetime

# 최소 1초 — 0초 이하 값은 "즉시 재시도 가능"과 구분이 안 돼 실수로
# 무제한 재시도를 허용할 위험이 있다.
MIN_RETRY_AFTER_SECONDS = 1

# 최대 24시간 — Provider가 비정상적으로 큰 값(또는 오타·공격성 값)을
# 보내도 무기한 차단으로 이어지지 않게 상한을 둔다.
MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60


def _clamp(seconds: float | int | None) -> int | None:

    if seconds is None:
        return None

    if isinstance(seconds, bool):
        # bool은 int의 서브클래스라 isinstance(x, (int, float))를
        # 그냥 통과한다 — 여기서 명시적으로 거부한다.
        return None

    if isinstance(seconds, float) and (math.isnan(seconds) or math.isinf(seconds)):
        return None

    if seconds < 0:
        return None

    return max(
        MIN_RETRY_AFTER_SECONDS,
        min(int(round(seconds)), MAX_RETRY_AFTER_SECONDS),
    )


def parse_retry_after_seconds(
    value: str | int | float | None, *, now: datetime | None = None,
) -> int | None:
    """
    Retry-After 헤더 값을 파싱한다 — 정수/실수 초, 초 단위 문자열,
    또는 HTTP-date(RFC 7231) 전부 지원한다. `now`는 HTTP-date를 초
    단위로 환산할 때만 쓰인다(naive UTC로 취급).
    """

    if value is None:
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _clamp(value)

    text = str(value).strip()
    if not text:
        return None

    try:
        return _clamp(float(text))
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None

    if parsed is None:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    reference = now or datetime.utcnow()
    delta_seconds = (parsed - reference).total_seconds()

    return _clamp(delta_seconds)


def parse_retry_after_from_structured_field(value) -> int | None:
    """
    응답 본문(JSON)에 담긴 구조화 필드(예: `retry_after`)를 파싱한다 —
    HTTP-date는 여기 해당하지 않는다(구조화 필드는 항상 숫자 초 값
    이라고 전제한다 — 이 필드에 날짜 문자열을 넣는 Provider는 아직
    확인된 바 없다).
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return _clamp(value)

    if isinstance(value, str):
        try:
            return _clamp(float(value.strip()))
        except ValueError:
            return None

    return None


def compute_retry_available_at(
    retry_after_seconds: int, *, now: datetime | None = None,
) -> datetime:

    now = now or datetime.utcnow()
    return now + timedelta(seconds=retry_after_seconds)


def combine_retry_available_at(
    existing: datetime | None, candidate: datetime,
) -> datetime:
    """
    out-of-order 방어 — 늦게 도착한 429 응답이 이미 알려진 더 늦은
    대기 시각을 앞당기지 못하게 항상 더 늦은 쪽을 취한다(item 14/15).
    """

    if existing is None:
        return candidate

    return max(existing, candidate)


__all__ = [
    "MIN_RETRY_AFTER_SECONDS",
    "MAX_RETRY_AFTER_SECONDS",
    "parse_retry_after_seconds",
    "parse_retry_after_from_structured_field",
    "compute_retry_available_at",
    "combine_retry_available_at",
]
