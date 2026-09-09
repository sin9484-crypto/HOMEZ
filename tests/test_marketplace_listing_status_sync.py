"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_status_sync.py

2026-08-05 최종 제품화 Phase 4 — 플랫폼 등록 상태 동기화 검증.
Fake Provider만 사용, 외부 네트워크 호출 0건. 실제 homez.db는 사용하지
않는다(임시 SQLite 파일).
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import ServiceUnavailableException
from app.core.exceptions import TooManyRequestsException
from app.database.base import Base
from app.domains.automation_safety.model import EmergencyStop
from app.domains.company.model import Company
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.constants import (
    ListingStatusCheckErrorCode,
)
from app.domains.marketplace_listing.constants import PlatformSyncStatus
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
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.status_provider import (
    get_status_provider,
)
from app.domains.marketplace_listing.status_sync_service import (
    _CSV_HEADER_LABELS,
)
from app.domains.marketplace_listing.status_sync_service import (
    _MAX_DATABASE_BUSY_RETRIES,
)
from app.domains.marketplace_listing.status_sync_service import (
    ListingStatusSyncService,
)
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401


class ListingStatusSyncTestCase(unittest.TestCase):

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
                EmergencyStop.__table__,
                MarketplaceSubmission.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")

        self.listing_a_id = self._seed_listing(self.company_a.id, "L-A")

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

    def _seed_listing(self, company_id, ref):

        candidate = ProductCandidate(
            candidate_key=f"test:COUPANG:{ref}", source_type="TREND",
            source_reference=ref, market="COUPANG",
            product_name=f"상품 {ref}", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        channel = (
            self.db.query(MarketplaceChannel)
            .filter(MarketplaceChannel.code == "COUPANG")
            .first()
        )
        if channel is None:
            channel = MarketplaceChannel(
                code="COUPANG", name="쿠팡",
                doc_verification_status="VERIFIED",
            )
            self.db.add(channel)
            self.db.commit()

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

        return listing.id

    def _set_external_listing_id(self, listing_id, value):

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == listing_id)
            .first()
        )
        listing.external_listing_id = value
        self.db.commit()

    # ----------------------------------------------------
    # 새로고침 — 성공/실패 분류
    # ----------------------------------------------------

    def test_refresh_without_external_id_returns_unknown(self):

        event_row = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )

        self.assertEqual(event_row.normalized_status, PlatformSyncStatus.UNKNOWN)
        self.assertIsNone(event_row.error_code)
        self.assertEqual(event_row.source, "REFRESH")

    def test_refresh_with_deterministic_id_normalizes_and_stores_raw_status(self):

        self._set_external_listing_id(self.listing_a_id, "external-ref-123")

        event_row = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )

        self.assertIn(event_row.normalized_status, PlatformSyncStatus.ALL)
        self.assertIsNotNone(event_row.platform_raw_status)
        self.assertIsNone(event_row.error_code)

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        self.assertEqual(listing.platform_sync_status, event_row.normalized_status)
        self.assertEqual(listing.platform_raw_status, event_row.platform_raw_status)
        self.assertIsNotNone(listing.status_last_refreshed_at)

    def test_same_external_id_always_normalizes_to_same_status(self):
        """Fake Provider는 같은 입력에 항상 같은 결과를 반환해야 한다."""

        self._set_external_listing_id(self.listing_a_id, "stable-ref")

        ev1 = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
            now=datetime.utcnow(),
        )

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        listing.status_last_refreshed_at = None
        self.db.commit()

        ev2 = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
            now=datetime.utcnow() + timedelta(hours=1),
        )

        self.assertEqual(ev1.normalized_status, ev2.normalized_status)
        self.assertEqual(ev1.platform_raw_status, ev2.platform_raw_status)

    def test_401_403_404_429_5xx_timeout_each_map_to_distinct_error_code(self):

        cases = {
            "TRIGGER_401": "UNAUTHORIZED_401",
            "TRIGGER_403": "FORBIDDEN_403",
            "TRIGGER_404": "NOT_FOUND_404",
            "TRIGGER_429": "RATE_LIMITED_429",
            "TRIGGER_5XX": "PLATFORM_ERROR_5XX",
            "TRIGGER_TIMEOUT": "TIMEOUT",
        }

        for trigger, expected_code in cases.items():
            listing_id = self._seed_listing(self.company_a.id, trigger)
            self._set_external_listing_id(listing_id, trigger)

            event_row = self.service.refresh_status(
                listing_id, self.company_a.id, triggered_by=1,
            )

            self.assertEqual(event_row.error_code, expected_code, trigger)
            self.assertEqual(
                event_row.normalized_status, PlatformSyncStatus.UNKNOWN, trigger,
            )

    def test_check_failure_never_overwrites_previously_known_status(self):

        self._set_external_listing_id(self.listing_a_id, "stable-ref-2")

        first = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )
        self.assertNotEqual(first.normalized_status, PlatformSyncStatus.UNKNOWN)

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        listing.status_last_refreshed_at = None
        listing.external_listing_id = "TRIGGER_429"
        self.db.commit()

        second = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )
        self.assertEqual(second.normalized_status, PlatformSyncStatus.UNKNOWN)
        self.assertEqual(second.error_code, "RATE_LIMITED_429")

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        # 조회 실패가 이전에 알려진 정규화 상태를 지우지 않았다.
        self.assertEqual(listing.platform_sync_status, first.normalized_status)
        self.assertEqual(listing.status_last_refresh_error_code, "RATE_LIMITED_429")

    def test_history_is_append_only_and_ordered(self):

        self._set_external_listing_id(self.listing_a_id, "ref-history")

        for i in range(3):
            listing = (
                self.db.query(MarketplaceListing)
                .filter(MarketplaceListing.id == self.listing_a_id)
                .first()
            )
            listing.status_last_refreshed_at = None
            self.db.commit()
            self.service.refresh_status(
                self.listing_a_id, self.company_a.id, triggered_by=1,
            )

        history = self.service.get_status_history(
            self.listing_a_id, self.company_a.id,
        )
        self.assertEqual(len(history), 3)
        self.assertEqual([e.id for e in history], sorted(e.id for e in history))

    # ----------------------------------------------------
    # Rate limit
    # ----------------------------------------------------

    def test_second_refresh_within_cooldown_is_rejected_with_429(self):

        self._set_external_listing_id(self.listing_a_id, "ref-cooldown")

        self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )

        with self.assertRaises(TooManyRequestsException):
            self.service.refresh_status(
                self.listing_a_id, self.company_a.id, triggered_by=1,
            )

    def test_refresh_after_cooldown_elapses_succeeds(self):

        self._set_external_listing_id(self.listing_a_id, "ref-cooldown-2")

        first = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )

        later = first.created_at + timedelta(seconds=31)
        second = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1, now=later,
        )
        self.assertIsNotNone(second.id)

    def test_concurrent_refresh_exactly_one_succeeds(self):
        """
        2026-08-14 비결정적 실패 원인 조사 후 재작성.

        기존 코드는 워커 스레드 안에서 매 시도마다 `self.company_a.id`를
        읽었다. `self.company_a`는 메인 스레드 Session(`self.db`)에
        바인딩된 ORM 객체이고, setUp()에서 일어난 여러 commit() 때문에
        이미 expire된 상태로 남아 있었다 — expire된 속성에 접근하면
        SQLAlchemy가 그 자리에서 암묵적으로 SELECT를 재실행해 새로고침
        한다. `sqlalchemy.event`의 `before_cursor_execute` 훅으로
        직접 확인한 결과, 이 SELECT가 "워커 스레드 안에서"
        `self.db`/`self.engine`(메인 스레드 전용, 스레드 안전이
        보장되지 않는 Session)에 대해 실행되고 있었다 — 두 워커
        스레드가 동시에 같은 Session 객체를 건드리는 실제 데이터
        경쟁이었다. 이전에 "Windows + 파일 기반 SQLite 드라이버/OS
        잡음"으로 추정해 3회 재시도로 흡수하던 IndexError/
        ObjectDeletedError 등은 전부 이 경쟁의 부수 효과였다 — SQLite
        잠금 규칙과는 무관했다.

        수정: `company_id`를 스레드 시작 "전에" 일반 int로 캡처해,
        워커가 공유 ORM 객체(`self.company_a`)를 절대 다시 참조하지
        않게 한다. 근본 원인을 제거했으므로 더 이상 재시도로 흡수할
        예외가 없어야 한다 — 그래서 범용 예외 재시도 루프도 제거했다
        (예상 밖 예외를 조용히 숨기지 않기 위해). `TooManyRequests
        Exception`(경쟁 패자의 정상 결과)만 기대되는 결과로 받아들이고,
        그 외 예외는 메인 스레드에서 원본 트레이스백 그대로 다시
        던져 테스트를 실패시킨다.
        """

        self._set_external_listing_id(self.listing_a_id, "ref-race")

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        SessionLocal2 = sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

        # 스레드 시작 "전"에 안전하게 캡처한다 — 워커 스레드는 이
        # 일반 int만 참조하고, self.company_a(메인 스레드 Session에
        # 바인딩된 ORM 객체)는 절대 건드리지 않는다(위 docstring 참고).
        company_id = self.company_a.id
        listing_id = self.listing_a_id

        try:
            results = {}
            unexpected_errors = {}
            barrier = threading.Barrier(2)

            def worker(name):

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                thread_db = SessionLocal2()
                service = ListingStatusSyncService(thread_db)
                try:
                    ev = service.refresh_status(
                        listing_id, company_id, triggered_by=1,
                    )
                    results[name] = ("ok", ev.id)
                except TooManyRequestsException:
                    results[name] = ("429", None)
                except Exception as e:  # noqa: BLE001
                    # 예상 밖 예외는 재시도로 삼키지 않는다 — 메인
                    # 스레드가 원본 트레이스백 그대로 다시 던지게
                    # 기록만 남긴다.
                    unexpected_errors[name] = e
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            if unexpected_errors:
                # 재현성을 위해 스레드 이름으로 정렬해 항상 같은
                # 순서로 첫 번째 예외를 그대로 다시 던진다.
                _, first_error = sorted(unexpected_errors.items())[0]
                raise first_error

            outcomes = [v[0] for v in results.values()]
            self.assertEqual(outcomes.count("ok"), 1, results)
            self.assertEqual(outcomes.count("429"), 1, results)
        finally:
            engine2.dispose()

    # ----------------------------------------------------
    # 회사 격리
    # ----------------------------------------------------

    def test_other_company_cannot_refresh_or_view_history(self):

        with self.assertRaises(NotFoundException):
            self.service.refresh_status(
                self.listing_a_id, self.company_b.id, triggered_by=1,
            )

        with self.assertRaises(NotFoundException):
            self.service.get_status_history(
                self.listing_a_id, self.company_b.id,
            )

    def test_other_company_cannot_retry(self):
        """
        2026-08-05 CTO 재검증 지시 Gate D — 재시도 엔드포인트도 조회·
        새로고침·이력과 동일하게 회사 경계를 넘으면 존재 자체가 드러나지
        않아야 한다(403이 아니라 404).
        """

        self._set_external_listing_id(self.listing_a_id, "TRIGGER_5XX")
        self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )

        with self.assertRaises(NotFoundException):
            self.service.retry_status_check(
                self.listing_a_id, self.company_b.id, triggered_by=1,
            )

    def test_list_listings_scoped_to_company(self):

        self._seed_listing(self.company_b.id, "L-B")

        rows_a = self.service.list_listings(self.company_a.id)
        rows_b = self.service.list_listings(self.company_b.id)

        self.assertTrue(all(r[0].company_id == self.company_a.id for r in rows_a))
        self.assertTrue(all(r[0].company_id == self.company_b.id for r in rows_b))

    # ----------------------------------------------------
    # 필터·검색·CSV
    # ----------------------------------------------------

    def test_filter_by_platform_sync_status(self):

        self._set_external_listing_id(self.listing_a_id, "filter-ref")
        ev = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )

        matches = self.service.list_listings(
            self.company_a.id, platform_sync_status=ev.normalized_status,
        )
        self.assertTrue(any(r[0].id == self.listing_a_id for r in matches))

        no_matches = self.service.list_listings(
            self.company_a.id,
            platform_sync_status="__NEVER_MATCHES__",
        )
        self.assertEqual(no_matches, [])

    def test_search_by_product_name(self):

        rows = self.service.list_listings(self.company_a.id, search="L-A")
        self.assertTrue(any(r[0].id == self.listing_a_id for r in rows))

        rows_none = self.service.list_listings(
            self.company_a.id, search="__NO_SUCH_PRODUCT__",
        )
        self.assertEqual(rows_none, [])

    def test_export_csv_contains_header_and_row(self):
        """
        2026-08-07 Gate H: CSV 헤더가 ko-KR/en-US로 지역화됐다 —
        기본값(ko-KR) 기준 지역화 문구로 확인한다.
        """

        csv_text = self.service.export_csv(self.company_a.id)

        lines = csv_text.strip().splitlines()
        ko_header = _CSV_HEADER_LABELS["ko-KR"]
        self.assertIn(ko_header["listing_id"], lines[0])
        self.assertIn(ko_header["platform_sync_status"], lines[0])
        self.assertEqual(len(lines), 2)  # header + 1 listing

    # ----------------------------------------------------
    # 2026-08-05 CTO 반려 반영 — 정밀 오류 분류 (item 1)
    # ----------------------------------------------------

    def _find_ref_with_status(self, prefix, target_status, exclude=False):
        """
        FakeListingStatusProvider는 결정론적(sha256 해시 순환)이므로,
        실제 랜덤 없이 원하는(또는 원하지 않는) 정규화 상태를 내는
        external_listing_id를 찾을 수 있다 — 하드코딩된 매직 ref
        대신 실제 Provider 로직으로 직접 찾아 결합도를 낮춘다.
        """

        provider = get_status_provider("COUPANG")
        for i in range(20):
            candidate = f"{prefix}-{i}"
            result = provider.check_status(candidate, datetime.utcnow())
            matches = result.normalized_status == target_status
            if matches != exclude:
                return candidate

        raise AssertionError("적합한 결정론적 ref를 찾지 못했습니다.")

    def test_conditional_update_conflict_raises_409_not_429(self):
        """
        조건부 UPDATE 경쟁 패배(rowcount != 1)는 429가 아니라 409여야
        한다 — CTO 반려 지적: 이전 코드는 이 경우를 429로 감췄었다.
        """

        self._set_external_listing_id(self.listing_a_id, "conflict-ref")

        listing_before = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        status_before = listing_before.platform_sync_status
        refreshed_before = listing_before.status_last_refreshed_at
        history_count_before = len(
            self.service.get_status_history(
                self.listing_a_id, self.company_a.id,
            ),
        )

        with patch.object(
            self.service.repository,
            "update_listing_platform_status_conditional",
            return_value=0,
        ):
            with self.assertRaises(ConflictException):
                self.service.refresh_status(
                    self.listing_a_id, self.company_a.id, triggered_by=1,
                )

        # 실패 시 기존 listing/이력이 전혀 바뀌지 않아야 한다.
        listing_after = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        self.assertEqual(listing_after.platform_sync_status, status_before)
        self.assertEqual(
            listing_after.status_last_refreshed_at, refreshed_before,
        )
        history_count_after = len(
            self.service.get_status_history(
                self.listing_a_id, self.company_a.id,
            ),
        )
        self.assertEqual(history_count_after, history_count_before)

    def test_database_locked_retries_then_succeeds(self):

        self._set_external_listing_id(self.listing_a_id, "busy-retry-ref")

        locked_error = OperationalError(
            "UPDATE marketplace_listings ...", {},
            Exception("database is locked"),
        )

        real_method = (
            self.service.repository.update_listing_platform_status_conditional
        )

        def _side_effect(*args, **kwargs):
            # 처음 두 번은 재시도 가능한 잠금 오류, 세 번째 호출에서만
            # 실제 메서드를 그대로 실행해 정상 성공 경로를 재현한다.
            if mocked.call_count < 3:
                raise locked_error
            return real_method(*args, **kwargs)

        with patch.object(
            self.service.repository,
            "update_listing_platform_status_conditional",
            side_effect=_side_effect,
        ) as mocked:
            event_row = self.service.refresh_status(
                self.listing_a_id, self.company_a.id, triggered_by=1,
            )

            self.assertEqual(mocked.call_count, 3)

        self.assertIsNotNone(event_row.id)

    def test_database_locked_retries_exhausted_raises_503(self):

        self._set_external_listing_id(self.listing_a_id, "busy-exhausted-ref")

        locked_error = OperationalError(
            "UPDATE marketplace_listings ...", {},
            Exception("database is locked"),
        )

        with patch.object(
            self.service.repository,
            "update_listing_platform_status_conditional",
            side_effect=locked_error,
        ) as mocked:
            with self.assertRaises(ServiceUnavailableException):
                self.service.refresh_status(
                    self.listing_a_id, self.company_a.id, triggered_by=1,
                )

            self.assertEqual(mocked.call_count, _MAX_DATABASE_BUSY_RETRIES)

    def test_integrity_error_raises_409(self):

        self._set_external_listing_id(self.listing_a_id, "integrity-ref")

        integrity_error = IntegrityError(
            "UPDATE marketplace_listings ...", {},
            Exception("UNIQUE constraint failed"),
        )

        with patch.object(
            self.service.repository,
            "update_listing_platform_status_conditional",
            side_effect=integrity_error,
        ):
            with self.assertRaises(ConflictException):
                self.service.refresh_status(
                    self.listing_a_id, self.company_a.id, triggered_by=1,
                )

    def test_unexpected_operational_error_propagates_raw_not_429_or_503(self):
        """
        잠금 경쟁이 아닌 OperationalError(예: 존재하지 않는 컬럼 —
        프로그래밍 결함)는 429/409/503 어느 것으로도 변환되지 않고
        원본 그대로 전파돼야 한다 — 조용히 감추면 실제 결함을 놓친다.
        """

        self._set_external_listing_id(self.listing_a_id, "programmer-bug-ref")

        broken_error = OperationalError(
            "UPDATE marketplace_listings ...", {},
            Exception("no such column: nonexistent_column"),
        )

        with patch.object(
            self.service.repository,
            "update_listing_platform_status_conditional",
            side_effect=broken_error,
        ) as mocked:
            with self.assertRaises(OperationalError):
                self.service.refresh_status(
                    self.listing_a_id, self.company_a.id, triggered_by=1,
                )

            # 재시도 대상이 아니므로 정확히 1회만 호출돼야 한다.
            self.assertEqual(mocked.call_count, 1)

    def test_unexpected_generic_exception_propagates_raw_not_429(self):

        self._set_external_listing_id(self.listing_a_id, "mapper-bug-ref")

        with patch.object(
            self.service.repository,
            "update_listing_platform_status_conditional",
            side_effect=RuntimeError("mapper misconfigured"),
        ):
            with self.assertRaises(RuntimeError):
                self.service.refresh_status(
                    self.listing_a_id, self.company_a.id, triggered_by=1,
                )

    # ----------------------------------------------------
    # 2026-08-05 CTO 반려 반영 — 상태 전이 안정성 (item 2)
    # ----------------------------------------------------

    def test_stale_provider_observation_is_ignored(self):
        """
        Provider가 더 오래된(과거) 관측 시각을 주장하는 응답을 나중에
        보내와도(지연 도착) 이미 알려진 더 최신 관측을 덮어쓰면 안
        된다 — out-of-order 방어.
        """

        self._set_external_listing_id(self.listing_a_id, "order-ref")

        t1 = datetime(2026, 1, 1, 0, 0, 0)
        first = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1, now=t1,
        )
        self.assertTrue(first.applied)

        stale_observed_at = t1 - timedelta(days=1)
        self._set_external_listing_id(
            self.listing_a_id,
            f"STALE:{stale_observed_at.isoformat()}:order-ref",
        )

        second = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
            now=t1 + timedelta(seconds=31),
        )

        self.assertFalse(second.applied)
        self.assertEqual(
            second.error_code,
            ListingStatusCheckErrorCode.STALE_OBSERVATION_IGNORED,
        )

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        self.assertEqual(listing.platform_sync_status, first.normalized_status)
        self.assertEqual(listing.platform_status_observed_at, t1)

        history = self.service.get_status_history(
            self.listing_a_id, self.company_a.id,
        )
        self.assertEqual(len(history), 2)  # append-only — 무시돼도 기록은 남는다.

    def test_terminal_ended_status_blocks_automatic_regression(self):
        """
        ENDED는 자동 새로고침으로 되돌아갈 수 없는 terminal 상태다 —
        운영자의 별도 확인 없이 조용히 재개된 것처럼 표시하지 않는다.
        """

        non_ended_ref = self._find_ref_with_status(
            "term", PlatformSyncStatus.ENDED, exclude=True,
        )
        self._set_external_listing_id(self.listing_a_id, non_ended_ref)

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        listing.platform_sync_status = PlatformSyncStatus.ENDED
        listing.platform_status_observed_at = datetime(2026, 1, 1)
        listing.status_last_refreshed_at = None
        self.db.commit()

        result = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
            now=datetime(2026, 1, 1, 1, 0, 0),
        )

        self.assertFalse(result.applied)
        self.assertEqual(
            result.error_code,
            ListingStatusCheckErrorCode.TERMINAL_STATE_LOCKED,
        )
        # event 자체에는 실제 관측값이 그대로 남는다(추측 은폐 금지).
        self.assertNotEqual(result.normalized_status, PlatformSyncStatus.ENDED)

        listing_after = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        self.assertEqual(
            listing_after.platform_sync_status, PlatformSyncStatus.ENDED,
        )

    def test_reingesting_identical_observation_is_idempotent(self):
        """
        같은 관측(같은 provider_observed_at)이 다시 도착해도(중복
        수집) 캐시가 깨지지 않고 그대로 재적용되며(더 과거가 아니므로
        stale 아님), 두 번째 이벤트도 append-only로 정상 기록된다.
        """

        fixed_observed_at = datetime(2026, 2, 1, 0, 0, 0)
        self._set_external_listing_id(
            self.listing_a_id,
            f"STALE:{fixed_observed_at.isoformat()}:idem-ref",
        )

        first = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
            now=fixed_observed_at,
        )
        self.assertTrue(first.applied)

        second = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
            now=fixed_observed_at + timedelta(seconds=31),
        )

        self.assertTrue(second.applied)
        self.assertEqual(second.normalized_status, first.normalized_status)

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        self.assertEqual(
            listing.platform_status_observed_at, fixed_observed_at,
        )

        history = self.service.get_status_history(
            self.listing_a_id, self.company_a.id,
        )
        self.assertEqual(len(history), 2)

    def test_status_update_and_history_write_share_one_transaction(self):
        """
        listing 캐시 UPDATE와 이력 이벤트 INSERT는 하나의 Transaction
        이어야 한다 — 이력 기록이 실패하면 캐시 UPDATE도 함께
        rollback돼야 한다(부분 반영 금지).
        """

        self._set_external_listing_id(self.listing_a_id, "atomic-ref")

        listing_before = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        status_before = listing_before.platform_sync_status

        history_insert_error = IntegrityError(
            "INSERT INTO marketplace_listing_status_events ...", {},
            Exception("NOT NULL constraint failed"),
        )

        with patch.object(
            self.service.repository,
            "add_status_event_no_commit",
            side_effect=history_insert_error,
        ):
            with self.assertRaises(ConflictException):
                self.service.refresh_status(
                    self.listing_a_id, self.company_a.id, triggered_by=1,
                )

        listing_after = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == self.listing_a_id)
            .first()
        )
        # 이력 INSERT가 실패했으므로 캐시 UPDATE도 커밋되지 않아야 한다.
        self.assertEqual(listing_after.platform_sync_status, status_before)

    def test_event_company_id_always_matches_caller_not_forgeable(self):

        self._set_external_listing_id(self.listing_a_id, "isolation-ref")

        event_row = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )

        self.assertEqual(event_row.company_id, self.company_a.id)

    # ----------------------------------------------------
    # 2026-08-05 CTO 반려 반영 — item 4: 재시도 전용 엔드포인트
    # ----------------------------------------------------

    def test_retry_rejected_when_no_prior_failure(self):
        """마지막 이벤트가 없거나 성공(반영됨)이면 재시도 대상이 없다."""

        with self.assertRaises(BadRequestException):
            self.service.retry_status_check(
                self.listing_a_id, self.company_a.id, triggered_by=1,
            )

        self._set_external_listing_id(self.listing_a_id, "retry-success-ref")
        first = self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )
        self.assertIsNone(first.error_code)

        with self.assertRaises(BadRequestException):
            self.service.retry_status_check(
                self.listing_a_id, self.company_a.id, triggered_by=1,
                now=first.created_at + timedelta(seconds=31),
            )

    def test_retry_succeeds_after_prior_failure_and_records_attempt_number(self):

        listing_id = self._seed_listing(self.company_a.id, "TRIGGER_429")
        self._set_external_listing_id(listing_id, "TRIGGER_429")

        t1 = datetime(2026, 3, 1, 0, 0, 0)
        first = self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=t1,
        )
        self.assertEqual(first.error_code, "RATE_LIMITED_429")
        self.assertEqual(first.attempt_number, 1)

        self._set_external_listing_id(listing_id, "retry-recover-ref")
        t2 = t1 + timedelta(seconds=31)
        retry_result = self.service.retry_status_check(
            listing_id, self.company_a.id, triggered_by=1, now=t2,
        )

        self.assertEqual(retry_result.source, "RETRY")
        self.assertEqual(retry_result.attempt_number, 2)
        self.assertIsNone(retry_result.error_code)
        self.assertTrue(retry_result.applied)

    def test_retry_respects_rate_limit_no_duplicate_in_progress(self):
        """
        같은 cooldown 윈도 안의 두 번째 재시도 요청은 429로 거부된다 —
        진행 중인 재시도가 중복 생성되지 않는다는 것과 동등한 보장.
        """

        listing_id = self._seed_listing(self.company_a.id, "TRIGGER_5XX")
        self._set_external_listing_id(listing_id, "TRIGGER_5XX")

        t1 = datetime(2026, 3, 2, 0, 0, 0)
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=t1,
        )

        with self.assertRaises(TooManyRequestsException):
            self.service.retry_status_check(
                listing_id, self.company_a.id, triggered_by=1,
                now=t1 + timedelta(seconds=5),
            )

    def test_retry_does_not_overwrite_previous_failure_event(self):
        """
        재시도가 실패해도(계속 실패) 이전 실패 이벤트는 그대로 남고,
        새 실패가 별도 행으로 append된다 — 이전 실패 사유를 덮어쓰지
        않는다.
        """

        listing_id = self._seed_listing(self.company_a.id, "TRIGGER_TIMEOUT")
        self._set_external_listing_id(listing_id, "TRIGGER_TIMEOUT")

        t1 = datetime(2026, 3, 3, 0, 0, 0)
        first = self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=t1,
        )

        t2 = t1 + timedelta(seconds=31)
        second = self.service.retry_status_check(
            listing_id, self.company_a.id, triggered_by=1, now=t2,
        )

        self.assertEqual(second.attempt_number, 2)
        self.assertEqual(second.error_code, "TIMEOUT")

        history = self.service.get_status_history(
            listing_id, self.company_a.id,
        )
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].id, first.id)
        self.assertEqual(history[0].error_code, "TIMEOUT")
        self.assertEqual(history[1].id, second.id)

    def test_retry_blocked_during_emergency_stop(self):

        from app.domains.automation_safety.model import EmergencyStop

        listing_id = self._seed_listing(self.company_a.id, "TRIGGER_403")
        self._set_external_listing_id(listing_id, "TRIGGER_403")

        t1 = datetime(2026, 3, 4, 0, 0, 0)
        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1, now=t1,
        )

        self.db.add(EmergencyStop(is_active=True, reason="test", set_by=1))
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.retry_status_check(
                listing_id, self.company_a.id, triggered_by=1,
                now=t1 + timedelta(seconds=31),
            )

    def test_refresh_blocked_during_emergency_stop(self):

        from app.domains.automation_safety.model import EmergencyStop

        self.db.add(EmergencyStop(is_active=True, reason="test", set_by=1))
        self.db.commit()

        self._set_external_listing_id(self.listing_a_id, "estop-ref")

        with self.assertRaises(BadRequestException):
            self.service.refresh_status(
                self.listing_a_id, self.company_a.id, triggered_by=1,
            )

    def test_status_history_and_list_listings_work_during_emergency_stop(self):
        """조회(읽기 전용)는 Emergency Stop 중에도 계속 동작해야 한다."""

        from app.domains.automation_safety.model import EmergencyStop

        self._set_external_listing_id(self.listing_a_id, "read-during-estop-ref")
        self.service.refresh_status(
            self.listing_a_id, self.company_a.id, triggered_by=1,
        )

        self.db.add(EmergencyStop(is_active=True, reason="test", set_by=1))
        self.db.commit()

        history = self.service.get_status_history(
            self.listing_a_id, self.company_a.id,
        )
        self.assertGreaterEqual(len(history), 1)

        rows = self.service.list_listings(self.company_a.id)
        self.assertTrue(any(r[0].id == self.listing_a_id for r in rows))


if __name__ == "__main__":
    unittest.main()
