"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/status_sync_service.py

2026-08-05 최종 제품화 Phase 4 — 플랫폼 등록 상태 동기화.

핵심 원칙:
  - `MarketplaceListing.status`(HOMEZ 내부 제출 구조 검증 상태)는 절대
    건드리지 않는다 — 이 서비스는 오직 platform_sync_status/
    platform_raw_status/status_last_refreshed_at/platform_status_
    observed_at만 갱신한다.
  - 새로고침은 항상 append-only 이벤트를 남긴다(반영됐든 안 됐든).
  - 조회 자체가 실패하면 UNKNOWN을 이벤트에 남기고 이전에 알려진
    정규화 상태를 덮어쓰지 않는다 — "모른다"를 "성공"으로 추론하지
    않는다.
  - out-of-order 방어: Provider가 주장하는 관측 시각(provider_
    observed_at)이 이미 알려진 값보다 과거면 반영하지 않는다.
  - terminal 상태(ENDED) 방어: 자동 새로고침으로는 ENDED에서 벗어날
    수 없다 — 운영자의 별도 확인이 필요하다.

2026-08-05 CTO 반려 반영 — 오류 분류:
  - 명시적 rate limit 위반만 429(TooManyRequestsException).
  - 조건부 UPDATE 경쟁 패배·저장소 제약 충돌은 409(ConflictException).
  - SQLite "database is locked"/"database table is locked"류의
    재시도 가능한 저장소 오류는 제한된 횟수만 재시도 후 503
    (ServiceUnavailableException, "DATABASE_BUSY").
  - 그 외 예상 밖의 SQLAlchemy/프로그래밍 오류는 절대 429로 감추지
    않고 그대로 전파한다(테스트가 실패해야 실제 결함을 알 수 있다).
=========================================================
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import ServiceUnavailableException
from app.core.exceptions import TooManyRequestsException
from app.domains.marketplace_listing.constants import (
    STATUS_REFRESH_MIN_INTERVAL_SECONDS,
)
from app.domains.marketplace_listing.constants import (
    ListingStatusCheckErrorCode,
)
from app.domains.marketplace_listing.constants import PlatformSyncStatus
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListingStatusEvent
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.marketplace_listing.retry_after import (
    combine_retry_available_at,
)
from app.domains.marketplace_listing.retry_after import (
    compute_retry_available_at,
)
from app.domains.marketplace_listing.status_provider import (
    ListingStatusCheckError,
)
from app.domains.marketplace_listing.status_provider import (
    get_status_provider,
)

_CSV_COLUMN_KEYS = [
    "listing_id", "channel_code", "product_name", "external_listing_id",
    "platform_sync_status", "platform_raw_status",
    "status_last_refreshed_at", "status_last_refresh_error_code",
    # 2026-08-07 Gate H — Retry-After 3개 컬럼. Provider 원문은 절대
    # 넣지 않는다 — 여기 나오는 값은 전부
    # app/domains/marketplace_listing/retry_after.py가 정규화한
    # 정수 초/naive UTC ISO 문자열뿐이다.
    "rate_limit_retry_after_seconds", "rate_limit_retry_available_at",
    "rate_limit_currently_retryable",
]

# ko-KR/en-US 헤더 — 값 자체(TRUE/FALSE, 상태 코드 등)는 여전히
# 언어중립 문자열로 둔다(다른 시스템과의 재수입·기존 계약 호환).
_CSV_HEADER_LABELS = {
    "ko-KR": {
        "listing_id": "Listing ID",
        "channel_code": "채널",
        "product_name": "상품명",
        "external_listing_id": "외부 상품번호",
        "platform_sync_status": "플랫폼 동기화 상태",
        "platform_raw_status": "플랫폼 원본 상태",
        "status_last_refreshed_at": "마지막 새로고침 시각",
        "status_last_refresh_error_code": "오류 코드",
        "rate_limit_retry_after_seconds": "재시도 대기(초)",
        "rate_limit_retry_available_at": "재시도 가능 시각(UTC)",
        "rate_limit_currently_retryable": "현재 재시도 가능",
    },
    "en-US": {
        "listing_id": "Listing ID",
        "channel_code": "Channel",
        "product_name": "Product Name",
        "external_listing_id": "External Listing ID",
        "platform_sync_status": "Platform Sync Status",
        "platform_raw_status": "Platform Raw Status",
        "status_last_refreshed_at": "Last Refreshed At",
        "status_last_refresh_error_code": "Error Code",
        "rate_limit_retry_after_seconds": "Retry After (seconds)",
        "rate_limit_retry_available_at": "Retry Available At (UTC)",
        "rate_limit_currently_retryable": "Currently Retryable",
    },
}

_DEFAULT_CSV_LOCALE = "ko-KR"

# Excel(Windows)이 확장자만 보고 시스템 로캘로 잘못 해석해 한글이
# 깨지는 것을 막기 위한 UTF-8 BOM — 응답 본문 맨 앞에 그대로 붙인다.
_CSV_UTF8_BOM = "﻿"

# 2026-08-05 CTO 반려 반영 — CSV 내보내기가 무제한 행을 만들지 않도록
# 상한을 둔다. 초과 시 조용히 잘라내지 않고 명시적으로 차단한다.
_MAX_CSV_EXPORT_ROWS = 5000

# SQLite 드라이버가 재시도 가능한(=일시적) 잠금 경쟁일 때만 내는 문구.
# 그 외 OperationalError(문법 오류·존재하지 않는 컬럼 등 프로그래밍
# 오류)는 여기 해당하지 않으므로 재시도하지 않고 그대로 전파한다.
_RETRYABLE_OPERATIONAL_ERROR_SUBSTRINGS = (
    "database is locked",
    "database table is locked",
)

_MAX_DATABASE_BUSY_RETRIES = 3
_DATABASE_BUSY_RETRY_BASE_SECONDS = 0.05


def _is_retryable_database_busy(exc: OperationalError) -> bool:

    message = str(getattr(exc, "orig", None) or exc).lower()

    return any(
        substring in message
        for substring in _RETRYABLE_OPERATIONAL_ERROR_SUBSTRINGS
    )


def _raise_if_rate_limited_by_elapsed(
    last_refreshed_at: datetime | None, now: datetime,
) -> None:
    """
    Gate U-5A-2(2026-08-10) — `elapsed < MIN_INTERVAL`이면 429를 던진다.
    `refresh_status()`/`retry_status_check()`의 사전 확인(빠른 경로,
    Provider 호출을 피하는 최적화)과 `_execute_status_check()`의 조건부
    UPDATE 실패 뒤 재확인(같은 커넥션, race-free한 최종 판정) 양쪽에서
    똑같이 재사용한다 — 판정 로직이 두 곳에 따로 존재하면 서로 다른
    기준으로 갈릴 위험이 있다(실측: 246개 테스트 묶음을 1회 실행하는
    동안 경쟁 패자가 재시도 3회 안에도 이 조건을 스스로 재확인하지
    못해 ConflictException만 반복해서 받는 결함을 재현했다 — 사전
    확인만으로는 race-free를 보장하지 못한다는 뜻).
    """

    if last_refreshed_at is None:
        return

    elapsed = (now - last_refreshed_at).total_seconds()
    if elapsed >= STATUS_REFRESH_MIN_INTERVAL_SECONDS:
        return

    remaining = int(STATUS_REFRESH_MIN_INTERVAL_SECONDS - elapsed)
    raise TooManyRequestsException(
        f"상태 새로고침은 {STATUS_REFRESH_MIN_INTERVAL_SECONDS}초에 "
        f"한 번만 가능합니다 — {remaining}초 후 다시 시도하세요.",
    )


class ListingStatusSyncService:
    """
    2026-08-05 CTO 반려 반영(item 4) — EStop/AutomationMode 적용 범위
    명확화:
      - **조회**(`get_status_history`/`list_listings`/`export_csv`)는
        Emergency Stop이 활성화되어 있어도 항상 동작한다 — 운영자가
        비상 정지 중에도 현재 상태를 볼 수 있어야 하며, 캐시된 값을
        "다시 읽기"만 하는 행위는 외부 상태를 바꾸지 않는다.
      - **상태 변경 시도**(`refresh_status`/`retry_status_check`)는
        Provider를 호출해 실제로 캐시를 갱신할 수 있는 행위이므로,
        `submission_service.py`와 동일하게 Emergency Stop이 활성화되어
        있으면 차단한다.
      - `AutomationMode`(RECOMMEND_ONLY/AUTO 등)는 여기 적용하지 않는다
        — 이 두 메서드는 항상 인증된 운영자의 명시적 요청 한 번당
        한 번만 실행되는 동기 API이지, 시스템이 사람 개입 없이 스스로
        반복 실행하는 자동화 루프가 아니기 때문이다(AutomationMode는
        "자동화가 사람 없이 행동해도 되는가"를 규율하는 개념 — 여기엔
        애초에 해당하지 않는다).
    """

    def __init__(self, db: Session):

        self.db = db
        self.repository = MarketplaceListingRepository(db)

    def _raise_if_emergency_stop_active(self) -> None:

        from app.domains.automation_safety.service import SafetyService

        if SafetyService(self.db).is_emergency_stop_active():
            raise BadRequestException(
                "Emergency Stop이 활성화되어 있어 상태 새로고침을 "
                "실행할 수 없습니다.",
            )

    def _raise_if_rate_limited(self, listing, now: datetime) -> None:
        """
        Gate H(2026-08-07) — Provider가 이전 429에서 실제로 알려준
        대기 시각(rate_limit_retry_available_at)이 아직 지나지
        않았으면 Provider를 호출하기 전에 직접 차단한다(item 16 —
        대기 중 직접 API 재호출도 429로 차단). 429가 아닌 다른 오류에는
        이 헤더/구조화 body를 절대 붙이지 않는다(item 10) — 이
        메서드는 오직 "실제로 아직 대기 중"인 경우에만 예외를 던진다.
        """

        available_at = listing.rate_limit_retry_available_at
        if available_at is None or now >= available_at:
            return

        remaining_seconds = max(
            1, int((available_at - now).total_seconds()),
        )

        raise TooManyRequestsException(
            detail={
                "error_code": "RATE_LIMITED",
                "retry_after_seconds": remaining_seconds,
                "retry_available_at": available_at.isoformat(),
            },
            headers={"Retry-After": str(remaining_seconds)},
        )

    # --------------------------------------------------
    # 새로고침
    # --------------------------------------------------

    def refresh_status(
        self, listing_id: int, company_id: int, triggered_by: int | None,
        now: datetime | None = None,
    ) -> MarketplaceListingStatusEvent:

        now = now or datetime.utcnow()

        self._raise_if_emergency_stop_active()

        listing = self.repository.get_listing_for_company(
            listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException("MarketplaceListing을 찾을 수 없습니다.")

        # 0) Gate H — Provider가 실제로 알려준 rate limit 대기 시각이
        #    아직 지나지 않았으면 로컬 쿨다운보다 먼저 차단한다.
        self._raise_if_rate_limited(listing, now)

        # 1) 명시적 rate limit — 빠른 경로(Provider 호출을 피하는 최적화).
        #    이 판정이 놓쳐도(경쟁 상황에서 stale 값을 읽어도) 안전하다
        #    — 최종 판정은 항상 아래 _execute_status_check()의 조건부
        #    UPDATE 실패 시 재확인(같은 커넥션, race-free)이 담당한다.
        _raise_if_rate_limited_by_elapsed(listing.status_last_refreshed_at, now)

        return self._execute_status_check(
            listing, company_id, triggered_by, now, source="REFRESH",
        )

    # --------------------------------------------------
    # 재시도(item 4) — 실패한 새로고침 전용. 성공적으로 반영된
    # 상태(=재시도할 대상이 없는 상태)에서는 절대 실행되지 않는다.
    # --------------------------------------------------

    def retry_status_check(
        self, listing_id: int, company_id: int, triggered_by: int | None,
        now: datetime | None = None,
    ) -> MarketplaceListingStatusEvent:
        """
        규칙(CTO 반려 item 4 그대로):
          - 마지막 이벤트가 성공(반영됨)이었다면 재시도 대상이 없다 —
            terminal success 상태에서는 절대 실행하지 않는다.
          - rate limit(atomic claim)은 refresh_status와 동일한 조건부
            UPDATE 경로를 그대로 재사용한다 — Provider를 호출하기 전에
            "지금 새로고침 가능한 상태인지"를 먼저 확인하는 것 자체가
            동시 중복 재시도를 막는 원자적 claim이다(같은 rate-limit
            윈도 안에서 두 번째 재시도 요청은 429로 막힌다 — 별도
            "in-progress" 상태 컬럼 없이도 새 in-progress job이
            중복 생성되지 않는다).
          - 이전 실패 사유는 지우지 않는다 — append-only 이벤트로 새
            attempt_number를 부여해 별도 행에 기록한다(캐시가 실제로
            갱신될 때만 platform_sync_status가 바뀐다).
        """

        now = now or datetime.utcnow()

        self._raise_if_emergency_stop_active()

        listing = self.repository.get_listing_for_company(
            listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException("MarketplaceListing을 찾을 수 없습니다.")

        # 0) Gate H — Provider가 실제로 알려준 rate limit 대기 시각이
        #    아직 지나지 않았으면 로컬 쿨다운보다 먼저 차단한다(item 16
        #    — 대기 중 직접 재시도 호출도 429로 차단).
        self._raise_if_rate_limited(listing, now)

        last_events = self.repository.list_status_events_for_listing(
            listing_id, company_id,
        )
        last_event = last_events[-1] if last_events else None

        if last_event is None or last_event.error_code is None:
            raise BadRequestException(
                "재시도할 실패한 새로고침 기록이 없습니다 — 이미 정상"
                "반영된 상태이거나 아직 새로고침을 시도한 적이 없습니다.",
            )

        _raise_if_rate_limited_by_elapsed(listing.status_last_refreshed_at, now)

        return self._execute_status_check(
            listing, company_id, triggered_by, now, source="RETRY",
        )

    def _execute_status_check(
        self, listing, company_id: int, triggered_by: int | None,
        now: datetime, source: str,
    ) -> MarketplaceListingStatusEvent:

        listing_id = listing.id
        channel_code = self._channel_code_for_listing(listing)
        provider = get_status_provider(channel_code)

        previous_events = self.repository.list_status_events_for_listing(
            listing_id, company_id,
        )
        previous_last_event = previous_events[-1] if previous_events else None
        attempt_number = (
            previous_last_event.attempt_number + 1
            if previous_last_event is not None
            and previous_last_event.error_code is not None
            else 1
        )

        previous_status = listing.platform_sync_status
        previous_raw_status = listing.platform_raw_status
        previous_observed_at = listing.platform_status_observed_at
        expected_last_refreshed_at = listing.status_last_refreshed_at

        # 2) Provider 조회 자체의 성공/실패.
        retry_after_seconds = None
        try:
            result = provider.check_status(listing.external_listing_id, now)
            observed_status = result.normalized_status
            observed_raw_status = result.platform_raw_status
            observed_at = result.provider_observed_at
            check_error_code = None

        except ListingStatusCheckError as exc:
            observed_status = PlatformSyncStatus.UNKNOWN
            observed_raw_status = None
            observed_at = None
            check_error_code = exc.error_code
            retry_after_seconds = exc.retry_after_seconds

        # 2-b) Gate H — 429이고 Provider가 실제로 유효한 Retry-After를
        #      줬을 때만 rate limit 상태를 갱신한다. 파싱 불가/누락이면
        #      기존에 알고 있던(어쩌면 더 늦은) 대기 상태를 그대로 둔다
        #      (추측하지 않는다 — item 5/9). out-of-order 방어로 항상
        #      더 늦은 시각만 남는다(item 14/15).
        new_rate_limit_retry_after_seconds = listing.rate_limit_retry_after_seconds
        new_rate_limit_retry_available_at = listing.rate_limit_retry_available_at
        update_rate_limit_state = False

        if (
            check_error_code == ListingStatusCheckErrorCode.RATE_LIMITED_429
            and retry_after_seconds is not None
        ):
            candidate_available_at = compute_retry_available_at(
                retry_after_seconds, now=now,
            )
            new_rate_limit_retry_available_at = combine_retry_available_at(
                listing.rate_limit_retry_available_at, candidate_available_at,
            )
            new_rate_limit_retry_after_seconds = retry_after_seconds
            update_rate_limit_state = True

        # 3) 반영 여부 판단 — event에는 항상 "관측한 그대로" 남기고,
        #    listing 캐시(platform_sync_status 등)만 선택적으로 갱신한다.
        applied = True
        event_error_code = check_error_code

        if check_error_code is not None:
            applied = False
        elif (
            previous_status in PlatformSyncStatus.TERMINAL
            and observed_status != previous_status
        ):
            applied = False
            event_error_code = ListingStatusCheckErrorCode.TERMINAL_STATE_LOCKED
        elif (
            previous_observed_at is not None
            and observed_at is not None
            and observed_at < previous_observed_at
        ):
            applied = False
            event_error_code = (
                ListingStatusCheckErrorCode.STALE_OBSERVATION_IGNORED
            )

        if applied:
            cache_status = observed_status
            cache_raw_status = observed_raw_status
            cache_observed_at = observed_at
        else:
            cache_status = previous_status
            cache_raw_status = previous_raw_status
            cache_observed_at = previous_observed_at

        # 4) 조건부 UPDATE + append-only 이벤트 — 재시도 가능한 저장소
        #    잠금만 제한된 횟수로 흡수하고, 그 외 예외는 명확한 계약으로
        #    변환하거나(경쟁 패배/제약 충돌 → 409) 그대로 전파한다.
        # (db_retry_attempt는 SQLite DATABASE_BUSY 재시도 횟수이지,
        # 위에서 계산한 도메인 개념의 attempt_number와는 다른 축이다.)
        db_retry_attempt = 0
        while True:
            db_retry_attempt += 1
            try:
                rowcount = (
                    self.repository
                    .update_listing_platform_status_conditional(
                        listing_id, company_id,
                        expected_last_refreshed_at=expected_last_refreshed_at,
                        platform_sync_status=cache_status,
                        platform_raw_status=cache_raw_status,
                        status_last_refreshed_at=now,
                        status_last_refresh_error_code=check_error_code,
                        platform_status_observed_at=cache_observed_at,
                        rate_limit_retry_after_seconds=(
                            new_rate_limit_retry_after_seconds
                        ),
                        rate_limit_retry_available_at=(
                            new_rate_limit_retry_available_at
                        ),
                        update_rate_limit_state=update_rate_limit_state,
                    )
                )

                if rowcount != 1:
                    # Gate U-5A-2 — rollback 전에, 지금 이 커넥션에서
                    # 직접 재조회한다(같은 트랜잭션 경계 안이므로 다른
                    # 커넥션의 stale read 문제와 무관하다 — race-free한
                    # 최종 판정). 경쟁에서 진 이유가 실제로 rate-limit
                    # 윈도 안이면(거의 항상 그렇다 — 이 UPDATE가 실패
                    # 했다는 것 자체가 방금 다른 새로고침이 이겼다는
                    # 뜻이므로) 429로, 그 외(드문 예외적 상황)에는
                    # 기존과 동일하게 409로 응답한다.
                    current_listing = self.repository.get_listing_for_company(
                        listing_id, company_id,
                    )
                    self.db.rollback()

                    if current_listing is not None:
                        _raise_if_rate_limited_by_elapsed(
                            current_listing.status_last_refreshed_at, now,
                        )

                    raise ConflictException(
                        "다른 새로고침 요청이 먼저 이 연결의 상태를 "
                        "갱신했습니다 — 새로고침 후 다시 시도하세요.",
                    )

                event = self.repository.add_status_event_no_commit(
                    MarketplaceListingStatusEvent(
                        company_id=company_id,
                        listing_id=listing_id,
                        submission_id=None,
                        source=source,
                        previous_status=previous_status,
                        normalized_status=observed_status,
                        platform_raw_status=observed_raw_status,
                        provider_observed_at=observed_at,
                        error_code=event_error_code,
                        applied=applied,
                        attempt_number=attempt_number,
                        triggered_by=triggered_by,
                        created_at=now,
                    ),
                )

                event_id = event.id
                event_created_at = event.created_at

                self.db.commit()
                break

            except ConflictException:
                raise

            except IntegrityError as exc:
                self.db.rollback()
                raise ConflictException(
                    "저장소 제약 조건과 충돌해 이 새로고침을 반영하지 "
                    "못했습니다.",
                ) from exc

            except OperationalError as exc:
                self.db.rollback()

                if not _is_retryable_database_busy(exc):
                    # 잠금 경쟁이 아닌 OperationalError(문법 오류·존재하지
                    # 않는 컬럼 등 프로그래밍 결함)는 429/503 어느 쪽으로도
                    # 감추지 않는다 — rollback만 하고 원본 예외를 그대로
                    # 전파해 테스트가 시끄럽게 실패하게 둔다.
                    raise

                if db_retry_attempt < _MAX_DATABASE_BUSY_RETRIES:
                    time.sleep(
                        _DATABASE_BUSY_RETRY_BASE_SECONDS * db_retry_attempt,
                    )
                    continue

                raise ServiceUnavailableException(
                    "저장소가 일시적으로 사용 중입니다(DATABASE_BUSY) — "
                    "잠시 후 다시 시도하세요.",
                ) from exc

        return MarketplaceListingStatusEvent(
            id=event_id,
            company_id=company_id,
            listing_id=listing_id,
            submission_id=None,
            source=source,
            previous_status=previous_status,
            normalized_status=observed_status,
            platform_raw_status=observed_raw_status,
            provider_observed_at=observed_at,
            error_code=event_error_code,
            applied=applied,
            attempt_number=attempt_number,
            triggered_by=triggered_by,
            created_at=event_created_at,
        )

    def _channel_code_for_listing(self, listing) -> str:

        account = (
            self.db.query(MarketplaceAccount)
            .filter(MarketplaceAccount.id == listing.marketplace_account_id)
            .first()
        )
        if account is None:
            raise NotFoundException("MarketplaceAccount를 찾을 수 없습니다.")

        channel = (
            self.db.query(MarketplaceChannel)
            .filter(MarketplaceChannel.id == account.channel_id)
            .first()
        )
        if channel is None:
            raise NotFoundException("MarketplaceChannel을 찾을 수 없습니다.")

        return channel.code

    # --------------------------------------------------
    # 조회
    # --------------------------------------------------

    def get_status_history(
        self, listing_id: int, company_id: int,
    ) -> list[MarketplaceListingStatusEvent]:

        listing = self.repository.get_listing_for_company(
            listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException("MarketplaceListing을 찾을 수 없습니다.")

        return self.repository.list_status_events_for_listing(
            listing_id, company_id,
        )

    def list_listings(
        self,
        company_id: int,
        *,
        channel_code: str | None = None,
        marketplace_account_id: int | None = None,
        fulfillment_mode: str | None = None,
        platform_sync_status: str | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[tuple]:

        return self.repository.list_listings_filtered(
            company_id,
            channel_code=channel_code,
            marketplace_account_id=marketplace_account_id,
            fulfillment_mode=fulfillment_mode,
            platform_sync_status=platform_sync_status,
            updated_from=updated_from,
            updated_to=updated_to,
            search=search,
            limit=limit,
        )

    def export_csv(
        self, company_id: int, *, locale: str = _DEFAULT_CSV_LOCALE,
        now: datetime | None = None, **filters,
    ) -> str:
        """
        2026-08-05 CTO 반려 반영:
          - 최대 내보내기 행수를 강제한다 — 조용히 자르지 않고, 초과
            시 필터를 좁히라는 명시적 오류로 차단한다(불완전한 CSV를
            "전체"인 것처럼 내려보내지 않는다).
          - Excel이 UTF-8(한글 포함)을 자동 인식하도록 UTF-8 BOM을
            맨 앞에 붙인다 — BOM이 없으면 Excel(Windows)이 시스템
            로캘로 잘못 해석해 한글이 깨질 수 있다.

        2026-08-07 Gate H — `locale`("ko-KR"/"en-US", 그 외 값은
        ko-KR로 폴백)에 따라 헤더 문구만 바뀐다(값 자체는 언어중립).
        `rate_limit_currently_retryable`은 이 호출 시점(`now`)
        기준으로 한 번 계산해 내려준다 — Provider 원문은 어디에도
        포함하지 않는다.
        """

        header_labels = _CSV_HEADER_LABELS.get(
            locale, _CSV_HEADER_LABELS[_DEFAULT_CSV_LOCALE],
        )
        now = now or datetime.utcnow()

        # 상한+1을 조회해 "정확히 상한인지" "초과했는지"를 한 번의
        # 쿼리로 구분한다(별도 COUNT 쿼리 불필요).
        rows = self.list_listings(
            company_id, limit=_MAX_CSV_EXPORT_ROWS + 1, **filters,
        )
        if len(rows) > _MAX_CSV_EXPORT_ROWS:
            raise BadRequestException(
                f"내보낼 수 있는 최대 행수({_MAX_CSV_EXPORT_ROWS}개)를 "
                "초과했습니다 — 필터를 좁혀 다시 시도하세요.",
            )

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([header_labels[key] for key in _CSV_COLUMN_KEYS])

        for listing, channel_code, product_name in rows:
            retry_available_at = listing.rate_limit_retry_available_at
            currently_retryable = (
                retry_available_at is None or now >= retry_available_at
            )
            writer.writerow([
                _csv_safe(listing.id),
                _csv_safe(channel_code),
                _csv_safe(product_name),
                _csv_safe(listing.external_listing_id or ""),
                _csv_safe(listing.platform_sync_status),
                _csv_safe(listing.platform_raw_status or ""),
                _csv_safe(
                    listing.status_last_refreshed_at.isoformat()
                    if listing.status_last_refreshed_at else "",
                ),
                _csv_safe(listing.status_last_refresh_error_code or ""),
                _csv_safe(
                    listing.rate_limit_retry_after_seconds
                    if listing.rate_limit_retry_after_seconds is not None
                    else "",
                ),
                _csv_safe(
                    retry_available_at.isoformat()
                    if retry_available_at else "",
                ),
                _csv_safe("TRUE" if currently_retryable else "FALSE"),
            ])

        return _CSV_UTF8_BOM + buffer.getvalue()


# --------------------------------------------------
# CSV Formula Injection 방어
#
# 2026-08-05 CTO 반려 반영 — 상품명·오류 메시지 등은 사용자/Provider가
# 사실상 자유롭게 채운 문자열이다. Excel/Google Sheets는 셀 값이
# `=`/`+`/`-`/`@`로 시작하면 수식으로 해석해 CSV를 열 때 임의 코드
# 실행(DDE 등)으로 이어질 수 있다 — 그런 값 앞에 작은따옴표를 붙여
# "텍스트"로 강제한다(엑셀 표준 완화 대책). csv.writer가 콤마·쌍따옴표·
# 개행은 이미 RFC 4180대로 escape하므로 여기서는 선행 문자만 처리한다.
# --------------------------------------------------

_FORMULA_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:

    text = str(value)

    if text and text[0] in _FORMULA_INJECTION_PREFIXES:
        return "'" + text

    return text


__all__ = [
    "ListingStatusSyncService",
]
