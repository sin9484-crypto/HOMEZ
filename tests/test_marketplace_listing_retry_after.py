"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_retry_after.py

Gate H(2026-08-07) — Retry-After 종단 간 서버측 계약 검증.

app/domains/marketplace_listing/retry_after.py(파싱/정규화)와
status_sync_service.py에 배선된 영속화·차단 로직을 함께 검증한다.
실제 homez.db·외부 네트워크는 전혀 사용하지 않는다(Fake Provider +
임시 SQLite).
=========================================================
"""

import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import TooManyRequestsException
from app.database.base import Base
from app.domains.automation_safety.model import EmergencyStop
from app.domains.company.model import Company
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentCapability,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentEligibility,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentSelection,
)
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.marketplace_listing.model import MarketplaceListingDraft
from app.domains.marketplace_listing.model import (
    MarketplaceListingStatusEvent,
)
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.marketplace_listing.model import (
    MarketplaceSubmissionApproval,
)
from app.domains.marketplace_listing.retry_after import (
    MAX_RETRY_AFTER_SECONDS,
    combine_retry_available_at,
    compute_retry_available_at,
    parse_retry_after_from_structured_field,
    parse_retry_after_seconds,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.status_sync_service import (
    ListingStatusSyncService,
)
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401


# --------------------------------------------------
# 1. 순수 파싱/정규화 단위 테스트 — 네트워크·DB 불필요
# --------------------------------------------------

class RetryAfterParsingTestCase(unittest.TestCase):

    def test_integer_seconds_string(self):

        self.assertEqual(parse_retry_after_seconds("120"), 120)

    def test_integer_seconds_number(self):

        self.assertEqual(parse_retry_after_seconds(45), 45)

    def test_float_seconds_rounds(self):

        self.assertEqual(parse_retry_after_seconds(45.6), 46)

    def test_http_date_format(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        value = "Fri, 07 Aug 2026 10:02:00 GMT"
        self.assertEqual(parse_retry_after_seconds(value, now=now), 120)

    def test_http_date_in_the_past_is_rejected_not_guessed(self):
        """
        이미 지난 HTTP-date는 음수 초로 환산된다 — 값을 추측해 최소값
        (1초)으로 밀어넣지 않고 명시적으로 거부한다(item 5 "거부하거나
        제한한다" 중 거부 쪽 — 이미 지난 시각을 준 Provider 응답은
        신뢰할 수 없다고 보는 편이 더 안전하다).
        """

        now = datetime(2026, 8, 7, 10, 5, 0)
        value = "Fri, 07 Aug 2026 10:02:00 GMT"  # 이미 지난 시각
        self.assertIsNone(parse_retry_after_seconds(value, now=now))

    def test_invalid_string_rejected(self):

        self.assertIsNone(parse_retry_after_seconds("not-a-valid-value"))

    def test_negative_seconds_rejected(self):

        self.assertIsNone(parse_retry_after_seconds(-30))

    def test_nan_rejected(self):

        self.assertIsNone(parse_retry_after_seconds(float("nan")))

    def test_infinite_value_rejected(self):
        """
        무한대는 유한한 최댓값으로 "clamp"한다는 개념 자체가 성립하지
        않는다 — NaN과 동일하게 명시적으로 거부한다(item 5의 "거부"
        경로). 실제 상한 clamp는 유한하지만 과도하게 큰 값에만
        적용된다(아래 test_excessively_large_value_is_clamped_to_max).
        """

        self.assertIsNone(parse_retry_after_seconds(float("inf")))

    def test_excessively_large_value_is_clamped_to_max(self):

        self.assertEqual(
            parse_retry_after_seconds(999_999_999), MAX_RETRY_AFTER_SECONDS,
        )

    def test_none_and_empty_are_missing(self):

        self.assertIsNone(parse_retry_after_seconds(None))
        self.assertIsNone(parse_retry_after_seconds(""))
        self.assertIsNone(parse_retry_after_seconds("   "))

    def test_structured_field_numeric(self):

        self.assertEqual(parse_retry_after_from_structured_field(90), 90)

    def test_structured_field_string_numeric(self):

        self.assertEqual(parse_retry_after_from_structured_field("90"), 90)

    def test_structured_field_bool_rejected(self):
        """bool은 int의 서브클래스라 실수로 통과하기 쉽다 — 명시적으로 거부."""

        self.assertIsNone(parse_retry_after_from_structured_field(True))

    def test_structured_field_invalid_string_rejected(self):

        self.assertIsNone(parse_retry_after_from_structured_field("soon"))

    def test_out_of_order_combine_keeps_later_value(self):

        earlier = datetime(2026, 8, 7, 10, 2, 0)
        later = datetime(2026, 8, 7, 10, 5, 0)

        self.assertEqual(combine_retry_available_at(later, earlier), later)
        self.assertEqual(combine_retry_available_at(earlier, later), later)
        self.assertEqual(combine_retry_available_at(None, later), later)

    def test_compute_retry_available_at_adds_seconds(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        result = compute_retry_available_at(120, now=now)
        self.assertEqual(result, now + timedelta(seconds=120))


# --------------------------------------------------
# 2~9. end-to-end 서비스 계층 — 임시 SQLite
# --------------------------------------------------

class RetryAfterServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceFulfillmentCapability.__table__,
                MarketplaceListingDraft.__table__,
                MarketplaceListing.__table__,
                MarketplaceListingStatusEvent.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmissionApproval.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__,
                EmergencyStop.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")

        self.service = ListingStatusSyncService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_company(self, label, business_number):

        company = Company(
            name=f"회사 {label}", business_number=business_number,
            ceo="테스트", phone="02-000-0000",
            email=f"{label.lower()}@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        return company

    def _seed_channel(self):

        channel = (
            self.db.query(MarketplaceChannel)
            .filter(MarketplaceChannel.code == "COUPANG")
            .first()
        )
        if channel is None:
            channel = MarketplaceChannel(
                code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
            )
            self.db.add(channel)
            self.db.commit()
        return channel

    def _seed_listing(self, company_id, ref, external_listing_id=None):

        candidate = ProductCandidate(
            candidate_key=f"test:COUPANG:{ref}", source_type="TREND",
            source_reference=ref, market="COUPANG",
            product_name=f"상품 {ref}", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        channel = self._seed_channel()
        account = MarketplaceAccount(
            company_id=company_id, channel_id=channel.id,
            account_code=f"acct-{ref}", account_name=f"계정 {ref}",
        )
        self.db.add(account)
        self.db.commit()

        listing_service = MarketplaceListingService(self.db)
        listing, _dup = listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            company_id,
        )

        if external_listing_id is not None:
            row = (
                self.db.query(MarketplaceListing)
                .filter(MarketplaceListing.id == listing.id)
                .first()
            )
            row.external_listing_id = external_listing_id
            self.db.commit()

        return listing.id

    # ------------------------------------------------
    # 429에만 Retry-After 제공 / 성공·다른 오류에는 rate_limit 상태 불변
    # ------------------------------------------------

    def test_429_without_retry_after_leaves_rate_limit_state_untouched(self):

        listing_id = self._seed_listing(
            self.company_a.id, "no-ra", external_listing_id="TRIGGER_429",
        )

        self.service.refresh_status(listing_id, self.company_a.id, triggered_by=1)

        listing = self._get_listing(listing_id)
        self.assertIsNone(listing.rate_limit_retry_available_at)
        self.assertIsNone(listing.rate_limit_retry_after_seconds)

    def test_429_with_seconds_persists_rate_limit_state(self):

        listing_id = self._seed_listing(
            self.company_a.id, "ra-sec",
            external_listing_id="TRIGGER_429:SECONDS:120",
        )
        now = datetime(2026, 8, 7, 10, 0, 0)

        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )

        listing = self._get_listing(listing_id)
        self.assertEqual(listing.rate_limit_retry_after_seconds, 120)
        self.assertEqual(
            listing.rate_limit_retry_available_at, now + timedelta(seconds=120),
        )

    def test_429_with_http_date_persists_rate_limit_state(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "ra-date",
            external_listing_id=(
                "TRIGGER_429:HTTPDATE:Fri, 07 Aug 2026 10:03:00 GMT"
            ),
        )

        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )

        listing = self._get_listing(listing_id)
        self.assertEqual(listing.rate_limit_retry_after_seconds, 180)

    def test_429_with_structured_field_persists_rate_limit_state(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "ra-struct",
            external_listing_id="TRIGGER_429:STRUCTURED:60",
        )

        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )

        listing = self._get_listing(listing_id)
        self.assertEqual(listing.rate_limit_retry_after_seconds, 60)

    def test_success_does_not_touch_prior_rate_limit_state(self):
        """
        429가 아닌 정상 새로고침이 이전에 기록된 rate limit 상태를
        조용히 지우지 않는다(아직 유효한 대기 시각을 실수로 잃지
        않는다는 계약) — 단, 대기 시각이 이미 지났으므로 실제로는
        차단되지 않고 정상 새로고침이 진행된다.
        """

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "past-rl",
            external_listing_id="TRIGGER_429:SECONDS:60",
        )
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )

        # 대기 시각이 지난 뒤 정상 값으로 재시도.
        listing = self._get_listing(listing_id)
        listing.external_listing_id = "normal-ref"
        self.db.commit()

        later = now + timedelta(minutes=5)
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=later,
        )

        listing = self._get_listing(listing_id)
        # 새로 429가 온 게 아니므로 이전 값이 그대로 남아있다(감사
        # 목적) — 다만 이미 지난 시각이라 차단 효과는 없다.
        self.assertEqual(listing.rate_limit_retry_after_seconds, 60)

    # ------------------------------------------------
    # out-of-order 방어 — 늦게 도착한 429가 대기 시각을 앞당기지 못함
    # ------------------------------------------------

    def test_second_429_with_shorter_wait_does_not_shorten_existing_window(self):
        """
        정상 경로(refresh_status/retry_status_check)는 이미 활성화된
        대기 창 안에서는 Provider 호출 자체를 막는다(_raise_if_rate_
        limited가 먼저 차단) — 그래서 "이미 알려진 창보다 짧은 값이
        나중에 도착"하는 상황은 실제로는 그 게이트를 통과한 요청에서만
        일어날 수 있다(예: 두 요청이 게이트 통과 시점엔 아직 대기
        창이 없었지만, 응답이 순서가 뒤바뀌어 도착하는 경우). 이
        merge 로직 자체는 `_execute_status_check()`(내부 메서드)가
        전담하므로, 그 지점을 직접 호출해 정확히 그 상황을 재현한다.
        """

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "ooo-1",
            external_listing_id="TRIGGER_429:SECONDS:300",
        )
        self.service._execute_status_check(
            self._get_listing(listing_id), self.company_a.id, 1, now,
            source="REFRESH",
        )
        first_available_at = self._get_listing(listing_id).rate_limit_retry_available_at
        self.assertEqual(first_available_at, now + timedelta(seconds=300))

        # 더 짧은 429가 뒤늦게 도착한 상황을 재현한다.
        listing = self._get_listing(listing_id)
        listing.external_listing_id = "TRIGGER_429:SECONDS:10"
        self.db.commit()

        second_call_time = now + timedelta(seconds=31)
        self.service._execute_status_check(
            self._get_listing(listing_id), self.company_a.id, 1,
            second_call_time, source="REFRESH",
        )

        listing = self._get_listing(listing_id)
        # 두 번째 호출 기준 10초 후는 first_available_at보다 이르다 —
        # 그러므로 여전히 첫 번째(더 늦은) 값이 유지돼야 한다.
        self.assertEqual(listing.rate_limit_retry_available_at, first_available_at)

    def test_second_429_with_longer_wait_extends_window(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "ooo-2",
            external_listing_id="TRIGGER_429:SECONDS:60",
        )
        self.service._execute_status_check(
            self._get_listing(listing_id), self.company_a.id, 1, now,
            source="REFRESH",
        )

        listing = self._get_listing(listing_id)
        listing.external_listing_id = "TRIGGER_429:SECONDS:600"
        self.db.commit()

        second_call_time = now + timedelta(seconds=31)
        self.service._execute_status_check(
            self._get_listing(listing_id), self.company_a.id, 1,
            second_call_time, source="REFRESH",
        )

        listing = self._get_listing(listing_id)
        self.assertEqual(
            listing.rate_limit_retry_available_at,
            second_call_time + timedelta(seconds=600),
        )

    # ------------------------------------------------
    # 대기 중 직접 재시도/새로고침 차단 (429, 구조화 body + Retry-After 헤더)
    # ------------------------------------------------

    def test_refresh_blocked_while_rate_limited_with_structured_detail(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "block-refresh",
            external_listing_id="TRIGGER_429:SECONDS:120",
        )
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )

        # 쿨다운과 무관하게(이미 지났다고 가정) rate limit 자체로 막혀야
        # 한다 — status_last_refreshed_at을 과거로 되돌려 로컬 쿨다운은
        # 통과하도록 만든 뒤에도 여전히 차단되는지 확인한다.
        listing = self._get_listing(listing_id)
        listing.status_last_refreshed_at = now - timedelta(hours=1)
        self.db.commit()

        later = now + timedelta(seconds=10)  # 아직 429 대기 시간 안
        with self.assertRaises(TooManyRequestsException) as ctx:
            self.service.refresh_status(
                listing_id, self.company_a.id, triggered_by=1, now=later,
            )

        exc = ctx.exception
        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.detail["error_code"], "RATE_LIMITED")
        self.assertGreater(exc.detail["retry_after_seconds"], 0)
        self.assertIn("retry_available_at", exc.detail)
        self.assertEqual(exc.headers.get("Retry-After"), str(exc.detail["retry_after_seconds"]))

    def test_retry_blocked_while_rate_limited(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "block-retry",
            external_listing_id="TRIGGER_429:SECONDS:120",
        )
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )

        listing = self._get_listing(listing_id)
        listing.status_last_refreshed_at = now - timedelta(hours=1)
        self.db.commit()

        later = now + timedelta(seconds=10)
        with self.assertRaises(TooManyRequestsException) as ctx:
            self.service.retry_status_check(
                listing_id, self.company_a.id, triggered_by=1, now=later,
            )
        self.assertEqual(ctx.exception.detail["error_code"], "RATE_LIMITED")

    def test_no_retry_after_provided_for_non_429_errors(self):
        """429가 아닌 오류(예: 403)에는 Retry-After 계약이 전혀 붙지
        않는다 — 일반 BadRequest/저장 오류 경로와 동일하게 처리된다."""

        listing_id = self._seed_listing(
            self.company_a.id, "not-429", external_listing_id="TRIGGER_403",
        )

        event = self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1,
        )
        self.assertEqual(event.error_code, "FORBIDDEN_403")

        listing = self._get_listing(listing_id)
        self.assertIsNone(listing.rate_limit_retry_available_at)

    # ------------------------------------------------
    # EStop이 Retry-After 여부와 무관하게 최우선으로 차단
    # ------------------------------------------------

    def test_emergency_stop_blocks_retry_regardless_of_rate_limit_state(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "estop-1",
            external_listing_id="TRIGGER_429:SECONDS:120",
        )
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )

        self.db.add(EmergencyStop(
            is_active=True, reason="테스트", set_by=1, set_at=now,
        ))
        self.db.commit()

        # rate limit 대기가 이미 끝난 시점이어도 EStop이 우선 차단해야
        # 한다 — 즉 EStop 메시지여야 하고 Retry-After 구조화 계약이
        # 아니어야 한다.
        later = now + timedelta(hours=1)
        with self.assertRaises(HTTPException) as ctx:
            self.service.retry_status_check(
                listing_id, self.company_a.id, triggered_by=1, now=later,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Emergency Stop", ctx.exception.detail)

    # ------------------------------------------------
    # 회사 격리 — 다른 회사는 조회/재시도 자체가 불가(404), rate limit
    # 상태도 절대 노출되지 않는다.
    # ------------------------------------------------

    def test_other_company_cannot_see_or_affect_rate_limit_state(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "scope-1",
            external_listing_id="TRIGGER_429:SECONDS:120",
        )
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )

        from fastapi import HTTPException as FastAPIHTTPException

        with self.assertRaises(FastAPIHTTPException) as ctx:
            self.service.refresh_status(
                listing_id, self.company_b.id, triggered_by=1, now=now,
            )
        self.assertEqual(ctx.exception.status_code, 404)

        with self.assertRaises(FastAPIHTTPException):
            self.service.retry_status_check(
                listing_id, self.company_b.id, triggered_by=1, now=now,
            )

        # 회사 A 관점에서는 여전히 정상적으로 rate limit 상태가 보인다.
        listing = self.service.repository.get_listing_for_company(
            listing_id, self.company_a.id,
        )
        self.assertIsNotNone(listing.rate_limit_retry_available_at)

    # ------------------------------------------------
    # 서버 재시작 후 복원 — 새 Service/Session 인스턴스로도 DB에 영속된
    # 값을 그대로 읽는다(프로세스 재시작을 새 세션으로 흉내).
    # ------------------------------------------------

    def test_state_survives_new_service_instance_simulating_restart(self):

        now = datetime(2026, 8, 7, 10, 0, 0)
        listing_id = self._seed_listing(
            self.company_a.id, "restart-1",
            external_listing_id="TRIGGER_429:SECONDS:300",
        )
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=now,
        )
        self.db.commit()

        # "재시작"을 새 세션 + 새 서비스 인스턴스로 흉내낸다 — 프로세스
        # 전역 상태(메모리)에 의존하지 않고 DB 컬럼에서만 복원됨을
        # 증명한다.
        new_session = self.SessionLocal()
        try:
            new_service = ListingStatusSyncService(new_session)
            listing = new_service.repository.get_listing_for_company(
                listing_id, self.company_a.id,
            )
            self.assertEqual(
                listing.rate_limit_retry_available_at,
                now + timedelta(seconds=300),
            )
            self.assertEqual(listing.rate_limit_retry_after_seconds, 300)
        finally:
            new_session.close()

    def _get_listing(self, listing_id):

        return (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == listing_id)
            .first()
        )


if __name__ == "__main__":
    unittest.main()
