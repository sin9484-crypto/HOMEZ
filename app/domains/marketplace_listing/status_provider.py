"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/status_provider.py

2026-08-05 최종 제품화 Phase 4 — 플랫폼 등록 상태 조회 Provider 경계.
이번 Phase는 실제 쿠팡·네이버 상태조회 API를 전혀 호출하지 않는다.
FakeListingStatusProvider만 실제로 동작하며, `app/domains/store_
connection/adapters/coupang.py`의 TRIGGER_* 관례를 그대로 재사용한다
— `external_listing_id`(또는 아직 없으면 seller reference)에 특정
접두사를 담아 호출하면 결정론적으로 401/403/404/429/5xx/timeout을
재현한다. 그 외 값은 seed 해시로 상태를 결정론적으로 순환시켜, 같은
listing은 항상 같은 결과를 반환한다(테스트 재현성).

실제 Provider(쿠팡·네이버 공식 상태조회 API)를 연결하려면 이 파일에
ListingStatusProvider를 상속한 새 클래스를 추가하고 PROVIDERS_BY_
CHANNEL에 등록한다 — 이번 Phase는 그 골격만 만든다.
=========================================================
"""

from __future__ import annotations

import hashlib
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domains.marketplace_listing.constants import (
    ListingStatusCheckErrorCode,
)
from app.domains.marketplace_listing.constants import PlatformSyncStatus


class ListingStatusCheckError(Exception):
    """
    상태 조회 자체가 실패했을 때(정상 조회 결과가 아님).

    2026-08-07 Gate H — `retry_after_seconds`는 429(RATE_LIMITED_429)
    일 때만 채워진다. Provider 원문(정수 초/HTTP-date/구조화 필드)의
    파싱은 이 예외를 던지는 쪽(Fake/실제 Provider 구현)이 이미
    `app.domains.marketplace_listing.retry_after`로 정규화해 넘긴다 —
    이 예외는 정규화된 정수 초만 담는다.
    """

    def __init__(
        self, error_code: str, message: str,
        retry_after_seconds: int | None = None,
    ):

        super().__init__(message)
        self.error_code = error_code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ListingStatusCheckResult:

    normalized_status: str
    platform_raw_status: str | None
    # Provider가 "이 상태가 이 시각 기준으로 유효했다"고 주장하는
    # 시각 — 우리가 언제 물었는지(collected_at)와는 다른 축이다.
    # out-of-order 방어의 기준값.
    provider_observed_at: datetime


class ListingStatusProvider(ABC):

    channel_code: str = "ABSTRACT"

    @abstractmethod
    def check_status(
        self, external_listing_id: str | None, now: datetime,
    ) -> ListingStatusCheckResult:
        """
        `now`는 "우리가 지금 조회를 시작한 시각"이다 — 실제 Provider
        구현은 보통 이 값을 그대로 provider_observed_at으로 쓰지 않고
        Provider 응답 자체에 담긴 시각을 파싱해 반환해야 한다(이
        Fake 구현은 결정론성을 위해 대부분 now를 그대로 쓰되, 오래된
        관측을 재현하는 테스트 전용 트리거만 예외).
        """
        ...


_TRIGGER_ERRORS = {
    "TRIGGER_401": ListingStatusCheckErrorCode.UNAUTHORIZED_401,
    "TRIGGER_403": ListingStatusCheckErrorCode.FORBIDDEN_403,
    "TRIGGER_404": ListingStatusCheckErrorCode.NOT_FOUND_404,
    "TRIGGER_429": ListingStatusCheckErrorCode.RATE_LIMITED_429,
    "TRIGGER_5XX": ListingStatusCheckErrorCode.PLATFORM_ERROR_5XX,
    "TRIGGER_TIMEOUT": ListingStatusCheckErrorCode.TIMEOUT,
}

_TRIGGER_ERROR_MESSAGES = {
    ListingStatusCheckErrorCode.UNAUTHORIZED_401: "인증에 실패했습니다(401).",
    ListingStatusCheckErrorCode.FORBIDDEN_403: "접근 권한이 없습니다(403).",
    ListingStatusCheckErrorCode.NOT_FOUND_404: "채널에서 해당 상품을 찾을 수 없습니다(404).",
    ListingStatusCheckErrorCode.RATE_LIMITED_429: "요청이 너무 많습니다(429).",
    ListingStatusCheckErrorCode.PLATFORM_ERROR_5XX: "플랫폼 오류입니다(5xx).",
    ListingStatusCheckErrorCode.TIMEOUT: "응답 시간이 초과되었습니다.",
}

# 결정론적 순환 대상 — DRAFT/UNKNOWN은 "아직 조회한 적 없음"을 뜻하는
# 값이라 정상 조회 결과로는 반환하지 않는다.
_FAKE_STATUS_CYCLE = (
    PlatformSyncStatus.PENDING_APPROVAL,
    PlatformSyncStatus.QUEUED,
    PlatformSyncStatus.PROCESSING,
    PlatformSyncStatus.ACTIVE,
    PlatformSyncStatus.PAUSED,
    PlatformSyncStatus.ENDED,
    PlatformSyncStatus.REJECTED,
    PlatformSyncStatus.FAILED,
)


_STALE_PREFIX = "STALE:"

# 2026-08-07 Gate H — "TRIGGER_429:<MODE>:<원문>" 형태로 세 가지
# Retry-After 형식(정수 초/HTTP-date/구조화 필드)을 결정론적으로
# 재현한다. 실제 Provider를 호출하지 않고도(이 저장소 전체의 원칙)
# 정규화 로직 전체를 end-to-end로 검증할 수 있다. 접두사 없는 기존
# "TRIGGER_429"는 그대로 retry_after_seconds=None으로 동작한다(기존
# 테스트 호환).
_TRIGGER_429_PREFIX = "TRIGGER_429:"


class FakeListingStatusProvider(ListingStatusProvider):

    def __init__(self, channel_code: str):

        self.channel_code = channel_code

    def check_status(
        self, external_listing_id: str | None, now: datetime,
    ) -> ListingStatusCheckResult:

        key = external_listing_id or ""

        if key.startswith(_TRIGGER_429_PREFIX):
            from app.domains.marketplace_listing.retry_after import (
                parse_retry_after_from_structured_field,
                parse_retry_after_seconds,
            )

            remainder = key[len(_TRIGGER_429_PREFIX):]
            mode, _, raw_value = remainder.partition(":")

            if mode == "SECONDS":
                retry_after_seconds = parse_retry_after_seconds(
                    raw_value, now=now,
                )
            elif mode == "HTTPDATE":
                retry_after_seconds = parse_retry_after_seconds(
                    raw_value, now=now,
                )
            elif mode == "STRUCTURED":
                retry_after_seconds = parse_retry_after_from_structured_field(
                    raw_value,
                )
            else:
                # 알 수 없는/의도적으로 잘못된 값 — 파싱 불가로 취급.
                retry_after_seconds = None

            raise ListingStatusCheckError(
                ListingStatusCheckErrorCode.RATE_LIMITED_429,
                _TRIGGER_ERROR_MESSAGES[
                    ListingStatusCheckErrorCode.RATE_LIMITED_429
                ],
                retry_after_seconds=retry_after_seconds,
            )

        if key in _TRIGGER_ERRORS:
            error_code = _TRIGGER_ERRORS[key]
            raise ListingStatusCheckError(
                error_code, _TRIGGER_ERROR_MESSAGES[error_code],
            )

        if not key:
            # 외부 상품번호가 아직 없다(실제 제출 전) — 조회할 대상이
            # 없으므로 추측하지 않고 UNKNOWN을 정상 결과로 돌려준다.
            return ListingStatusCheckResult(
                normalized_status=PlatformSyncStatus.UNKNOWN,
                platform_raw_status=None,
                provider_observed_at=now,
            )

        observed_at = now
        cycle_key = key

        if key.startswith(_STALE_PREFIX):
            # 테스트 전용: "STALE:<ISO8601>:<나머지 키>" — 실제로
            # 지연 도착한(더 오래된) Provider 응답을 결정론적으로
            # 재현한다. 정규화 상태 자체는 <나머지 키>로 그대로
            # 순환시킨다.
            _, iso_ts, cycle_key = key.split(":", 2)
            observed_at = datetime.fromisoformat(iso_ts)

        digest = hashlib.sha256(
            f"{self.channel_code}:{cycle_key}".encode("utf-8"),
        ).digest()
        index = digest[0] % len(_FAKE_STATUS_CYCLE)
        normalized = _FAKE_STATUS_CYCLE[index]

        return ListingStatusCheckResult(
            normalized_status=normalized,
            platform_raw_status=f"FAKE_{self.channel_code}_{normalized}",
            provider_observed_at=observed_at,
        )


PROVIDERS_BY_CHANNEL: dict[str, ListingStatusProvider] = {
    "COUPANG": FakeListingStatusProvider("COUPANG"),
    "NAVER_SMARTSTORE": FakeListingStatusProvider("NAVER_SMARTSTORE"),
}


def get_status_provider(channel_code: str) -> ListingStatusProvider:

    provider = PROVIDERS_BY_CHANNEL.get(channel_code)
    if provider is None:
        raise ListingStatusCheckError(
            ListingStatusCheckErrorCode.UNKNOWN,
            f"알 수 없는 채널입니다: {channel_code}",
        )

    return provider


__all__ = [
    "ListingStatusCheckError",
    "ListingStatusCheckResult",
    "ListingStatusProvider",
    "FakeListingStatusProvider",
    "PROVIDERS_BY_CHANNEL",
    "get_status_provider",
]
