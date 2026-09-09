"""
=========================================================
Homez OS

File : tests/test_operational_events_wiring.py

Migration 사후감사 후속(2026-08-23 19차 지시) — Section 5. 실제 코드에서
확인된 9개 dispatch_operational_event() 호출 지점(5개 도메인, 7개
이벤트 코드)을 각 도메인의 기존 테스트 fixture를 그대로 재사용해
검증한다. 숫자·이벤트 이름은 전부 실제 코드를 grep/read로 확인한
값이며 추측하지 않았다:

  1. marketplace_listing/listing_wizard_service.py::validate()
     → LISTING_FINAL_APPROVAL_NEEDED
  2. marketplace_listing/listing_wizard_service.py::submit()
     (_persist_materialized 경유) → LISTING_SUBMISSION_FAILED
  3. pricing/service.py::request_price_change() → PRICE_CHANGE_APPROVAL_NEEDED
  4. pricing/service.py::_upsert_reconciliation() INSERT 분기
     (record_actual_margin() 경유) → RECONCILIATION_REVIEW_NEEDED
  5. pricing/service.py::_upsert_reconciliation() UPDATE 분기 → 동일 이벤트
  6. product_candidate/service.py::verify_private_candidate_info()
     → CANDIDATE_REVIEW_NEEDED
  7. product_candidate/service.py::recommend() → CANDIDATE_REVIEW_NEEDED
  8. return_order/service.py::create_return_order()
     → RETURN_EXCHANGE_APPROVAL_NEEDED
  9. store_connection/service.py::verify_existing() → CHANNEL_CREDENTIAL_EXPIRED

모든 호출 지점이 `self.db.commit()` 이후에 dispatch를 호출한다는 것은
코드를 직접 읽어 이미 확인했다(업무 트랜잭션이 알림 로직보다 먼저
확정됨). 이 파일은 그 결과로 실제 알림 행이 남는지, 신규 알림 테이블이
없어도 본 업무가 깨지지 않는지를 검증한다. Critical 강제활성·민감정보
비노출·비활성화 존중 등 알림 엔진 자체의 공통 계약은
tests/test_notification_delivery_pt3.py가 이미 전담 검증하므로 여기서
반복하지 않는다 — 이 파일은 "각 업무 코드가 엔진을 올바르게 호출하는가"
만 검증한다.
=========================================================
"""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationEmailLog
from app.domains.notification_center.model import NotificationEventPreference
from app.domains.notification_center.model import NotificationPreference
from app.domains.notification_center.model import NotificationRead

_NOTIFICATION_TABLES = [
    Notification.__table__, NotificationRead.__table__,
    NotificationEmailLog.__table__, NotificationPreference.__table__,
    NotificationEventPreference.__table__,
]


def _add_notification_tables(engine) -> None:
    """기존 도메인 fixture의 엔진에 알림 테이블만 추가로 만든다 —
    각 도메인 fixture가 이미 만든 테이블은 절대 다시 만들지 않는다
    (Base.metadata.create_all에 tables=[...]를 명시하면 없는 것만
    생성하고 있는 것은 건드리지 않는다)."""

    Base.metadata.create_all(bind=engine, tables=_NOTIFICATION_TABLES)


# ==========================================================
# 1. marketplace_listing — LISTING_FINAL_APPROVAL_NEEDED /
#    LISTING_SUBMISSION_FAILED
# ==========================================================

from tests.test_listing_wizard_service import ListingWizardServiceTestCase  # noqa: E402


class ListingWizardNotificationTestCase(ListingWizardServiceTestCase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def test_validate_reaching_ready_for_approval_dispatches_listing_final_approval_needed(self):

        candidate, channel, account, media = self._full_setup()
        wizard = self._advance_to_ready_for_approval(candidate, account, media)
        self.assertEqual(wizard.status, "READY_FOR_APPROVAL")

        notif = (
            self.db.query(Notification)
            .filter(Notification.company_id == self.company_id)
            .order_by(Notification.id.desc())
            .first()
        )
        self.assertIsNotNone(notif)
        self.assertEqual(notif.category, "product")

        log = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company_id,
                NotificationEmailLog.event_code == "LISTING_FINAL_APPROVAL_NEEDED",
            )
            .first()
        )
        self.assertIsNotNone(log)
        self.assertIn(f"listing-final-approval:{wizard.id}", log.idempotency_key)

    def test_validate_is_safe_when_notification_tables_absent(self):
        """알림 테이블이 아예 없는 (구버전 스키마) DB에서도 validate()
        자체는 정상적으로 성공해야 한다 — dispatch_operational_event()의
        has_table() 방어 가드를 실사용 흐름으로 재확인."""

        # 신규 파일 DB에 "알림 테이블만 제외"한 동일 테이블 목록을
        # 그대로 다시 만든다 — ListingWizardServiceTestCase.setUp()의
        # tables=[...] 목록과 완전히 동일하되 notification 계열만 뺀다.
        from app.domains.company.model import Company
        from app.domains.product_candidate.model import ProductCandidate
        from app.domains.product_candidate.model import ProductCandidateSelection
        from app.domains.marketplace_listing.model import MarketplaceChannel
        from app.domains.marketplace_listing.model import MarketplaceAccount
        from app.domains.marketplace_listing.model import MarketplaceFulfillmentCapability
        from app.domains.marketplace_listing.model import MarketplaceListingDraft
        from app.domains.marketplace_listing.model import MarketplaceListing
        from app.domains.marketplace_listing.model import MarketplaceFulfillmentSelection
        from app.domains.marketplace_listing.model import MarketplaceSubmissionApproval
        from app.domains.marketplace_listing.model import MarketplaceFulfillmentEligibility
        from app.domains.marketplace_listing.model import MarketplaceSubmission
        from app.domains.marketplace_listing.model import ListingWizard
        from app.domains.media_asset.model import MediaAsset
        from app.domains.automation_safety.model import AutomationModeState
        from app.domains.automation_safety.model import EmergencyStop
        from app.domains.automation_safety.model import ExecutionLimit
        from app.domains.automation_safety.model import ExecutionUsage
        from app.domains.automation_safety.model import ExecutionPeriodUsage
        from app.domains.channel_policy.model import ChannelPolicyRule
        from app.domains.channel_policy.model import CompanyChannelPolicySettings
        from app.domains.channel_policy.model import ChannelPolicyEvaluation
        from app.domains.channel_policy.service import ChannelPolicyService
        from app.domains.marketplace_listing.listing_wizard_service import (
            ListingWizardService,
        )

        fd2, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd2)
        engine2 = create_engine(f"sqlite:///{path2}")
        Base.metadata.create_all(
            bind=engine2,
            tables=[
                Company.__table__, ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                MarketplaceChannel.__table__, MarketplaceAccount.__table__,
                MarketplaceFulfillmentCapability.__table__,
                MarketplaceListingDraft.__table__, MarketplaceListing.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmissionApproval.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__, ListingWizard.__table__,
                MediaAsset.__table__, AutomationModeState.__table__,
                EmergencyStop.__table__, ExecutionLimit.__table__,
                ExecutionUsage.__table__, ExecutionPeriodUsage.__table__,
                ChannelPolicyRule.__table__, CompanyChannelPolicySettings.__table__,
                ChannelPolicyEvaluation.__table__,
            ],
        )
        # notification_* 테이블을 의도적으로 만들지 않는다.
        with engine2.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            ))

        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine2)
        db2 = session_local()
        try:
            ChannelPolicyService(db2).seed_rule_catalog()

            company = Company(
                name="알림없는회사", business_number="999-99-99999",
                ceo="테스트", phone="02-000-0000", email="noev@example.com",
                address="서울",
            )
            db2.add(company)
            db2.commit()

            old_db, old_service = self.db, self.service
            self.db = db2
            self.service = ListingWizardService(db2)
            self.company_id = company.id
            try:
                candidate, channel, account, media = self._full_setup()
                wizard = self._advance_to_ready_for_approval(candidate, account, media)
                self.assertEqual(wizard.status, "READY_FOR_APPROVAL")
            finally:
                self.db, self.service = old_db, old_service
        finally:
            db2.close()
            engine2.dispose()
            if os.path.exists(path2):
                os.remove(path2)


# ==========================================================
# 2. pricing — PRICE_CHANGE_APPROVAL_NEEDED / RECONCILIATION_REVIEW_NEEDED
# ==========================================================

from tests.test_pricing_core import PricingTestCaseBase  # noqa: E402
from app.domains.pricing.schema import PriceChangeCreate  # noqa: E402


class PricingPriceChangeNotificationTestCase(PricingTestCaseBase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def test_request_price_change_dispatches_price_change_approval_needed(self):

        self._init_pricing()

        request = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("12000"), reason="원가 상승",
                idempotency_key="notif-pc:1",
            ),
            self.admin_user.id,
        )

        log = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company_id,
                NotificationEmailLog.event_code == "PRICE_CHANGE_APPROVAL_NEEDED",
            )
            .first()
        )
        self.assertIsNotNone(log)
        self.assertIn(f"price-change-approval:{request.id}", log.idempotency_key)

        notif = (
            self.db.query(Notification)
            .filter(
                Notification.company_id == self.company_id,
                Notification.user_id == self.admin_user.id,
            )
            .first()
        )
        self.assertIsNotNone(notif)
        # company_id 격리 — 다른 회사 계정으로는 절대 보이지 않는다.
        other_notifs = (
            self.db.query(Notification)
            .filter(Notification.company_id == self.company_id + 999)
            .count()
        )
        self.assertEqual(other_notifs, 0)


from tests.test_pricing_reconciliation import ReconciliationTestCaseBase  # noqa: E402


class ReconciliationNotificationTestCase(ReconciliationTestCaseBase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def test_mismatch_reconciliation_dispatches_reconciliation_review_needed(self):
        """INSERT 분기(최초 대사) — pricing/service.py:1186-1198."""

        order, item = self._create_order(
            order_id_hint="NOTIF-ORDER-1", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)
        self._create_deposited_settlement(order, gross=9000.0, fee=900.0)

        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )
        self.assertEqual(
            result["reconciliation"].status.value
            if hasattr(result["reconciliation"].status, "value")
            else result["reconciliation"].status,
            "MISMATCH",
        )

        log = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company_id,
                NotificationEmailLog.event_code == "RECONCILIATION_REVIEW_NEEDED",
            )
            .first()
        )
        self.assertIsNotNone(log)

    def test_matched_reconciliation_does_not_dispatch(self):
        """MISMATCH가 아니면(MATCHED) 알림이 전혀 생기지 않아야 한다 —
        불필요한 알림 스팸 방지."""

        order, item = self._create_order(
            order_id_hint="NOTIF-ORDER-2", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)
        self._create_deposited_settlement(order, gross=10000.0, fee=1400.0)

        self.pricing_service.record_actual_margin(order.id, self.company_id)

        log_count = (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.event_code == "RECONCILIATION_REVIEW_NEEDED")
            .count()
        )
        self.assertEqual(log_count, 0)

    def test_reconciliation_repeat_mismatch_uses_distinct_idempotency_keys(self):
        """실제 코드를 읽어 확인한 사실: 최초 대사(INSERT 분기,
        pricing/service.py:1188)의 idempotency_key는
        `reconciliation-review:{id}:MISMATCH`이고, 이후 재대사(UPDATE
        분기, :1230)는 `reconciliation-review:{id}:MISMATCH:{variance}`
        — variance 접미사가 붙는 별도 포맷이다. 그래서 같은 주문을
        같은 조건으로 두 번 재대사해도 "중복 차단"이 아니라 "서로
        다른 키의 알림 2건"이 되는 것이 코드가 실제로 의도한 동작이다
        — 재대사 자체를 하나의 새로운 확인 필요 사건으로 본다는 뜻.
        이 테스트는 그 동작을 있는 그대로 고정한다(추측으로 "1건이어야
        한다"고 기대하지 않는다)."""

        order, item = self._create_order(
            order_id_hint="NOTIF-ORDER-3", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)
        self._create_deposited_settlement(order, gross=9000.0, fee=900.0)

        self.pricing_service.record_actual_margin(order.id, self.company_id)
        self.pricing_service.record_actual_margin(order.id, self.company_id)

        logs = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company_id,
                NotificationEmailLog.event_code == "RECONCILIATION_REVIEW_NEEDED",
            )
            .all()
        )
        self.assertEqual(len(logs), 2)
        keys = {log.idempotency_key for log in logs}
        self.assertEqual(len(keys), 2)  # 두 키가 서로 달라야 한다(우연한 중복이 아님)

        # 세 번째 재대사(동일 조건)는 UPDATE 분기 키와 정확히 같은 키를
        # 다시 만들어내므로 — 진짜 idempotency 차단이 여기서 확인된다.
        self.pricing_service.record_actual_margin(order.id, self.company_id)
        log_count_after_third = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company_id,
                NotificationEmailLog.event_code == "RECONCILIATION_REVIEW_NEEDED",
            )
            .count()
        )
        self.assertEqual(log_count_after_third, 2)


# ==========================================================
# 3. product_candidate — CANDIDATE_REVIEW_NEEDED (2개 호출 지점)
# ==========================================================

from tests.test_product_candidate_analysis_workflow import (  # noqa: E402
    ProductCandidateAnalysisWorkflowTestCase,
)


class ProductCandidateNotificationTestCase(ProductCandidateAnalysisWorkflowTestCase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def test_verify_private_candidate_info_dispatches_candidate_review_needed(self):

        candidate = self._create_private()
        self.service.verify_private_candidate_info(
            candidate.id, company_id=1, correlation_id="notif-a1",
        )

        log = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == 1,
                NotificationEmailLog.event_code == "CANDIDATE_REVIEW_NEEDED",
            )
            .first()
        )
        self.assertIsNotNone(log)


from tests.test_product_candidate import (  # noqa: E402
    ProductCandidateTestCase as _BaseProductCandidateTestCase,
)


class ProductCandidateRecommendNotificationTestCase(_BaseProductCandidateTestCase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def test_recommend_dispatches_candidate_review_needed(self):

        from app.domains.product_candidate.service import ProductCandidateService

        db = self.SessionLocal()
        try:
            service = ProductCandidateService(db)
            candidate, _ = service.discover(
                self._discover_data(), correlation_id="c1",
            )
            service.apply_trend_analysis(
                candidate.id, company_id=1, trend_score=0.8, confidence=0.9,
                evidence_text="상승 추세", correlation_id="c2",
            )
            recommended, _events = service.recommend(
                candidate.id, company_id=1, correlation_id="c3",
            )
            self.assertEqual(recommended.status, "RECOMMENDED")

            log = (
                db.query(NotificationEmailLog)
                .filter(
                    NotificationEmailLog.company_id == 1,
                    NotificationEmailLog.event_code == "CANDIDATE_REVIEW_NEEDED",
                )
                .first()
            )
            self.assertIsNotNone(log)
        finally:
            db.close()


# ==========================================================
# 4. return_order — RETURN_EXCHANGE_APPROVAL_NEEDED
# ==========================================================

from tests.test_order_fulfillment_core import (  # noqa: E402
    OrderFulfillmentTestCaseBase,
)
from app.domains.return_order.constants import ReturnOrderType  # noqa: E402
from app.domains.return_order.schema import ReturnOrderCreate  # noqa: E402


class ReturnOrderNotificationTestCase(OrderFulfillmentTestCaseBase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def _ship(self, order, item, key="notif-ship:1"):

        from app.domains.shipment.schema import ShipmentCreate

        return self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item.id], courier="CJ",
                invoice_number=f"INV-{key}", idempotency_key=key,
            ),
            triggered_by=1,
        )

    def test_create_return_order_dispatches_return_exchange_approval_needed(self):

        sku, result = self._full_happy_path_to_reserved_item(quantity=2)
        order = result["order"]
        item = result["items"][0]
        shipment = self._ship(order, item)

        return_order = self.return_service.create_return_order(
            self.company_id,
            ReturnOrderCreate(
                order_item_id=item.id, shipment_id=shipment.id,
                return_type=ReturnOrderType.RETURN, quantity=2,
                reason="단순변심", idempotency_key="notif-ret:1",
            ),
            triggered_by=1,
        )

        log = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company_id,
                NotificationEmailLog.event_code == "RETURN_EXCHANGE_APPROVAL_NEEDED",
            )
            .first()
        )
        self.assertIsNotNone(log)
        self.assertIn(f"return-approval:{return_order.id}", log.idempotency_key)

        notif = (
            self.db.query(Notification)
            .filter(
                Notification.company_id == self.company_id,
                Notification.user_id == 1,
            )
            .first()
        )
        self.assertIsNotNone(notif)


# ==========================================================
# 5. store_connection — CHANNEL_CREDENTIAL_EXPIRED
# ==========================================================

from tests.test_store_connection import (  # noqa: E402
    StoreConnectionTestCase, VALID_COUPANG_FIELDS,
)


class StoreConnectionNotificationTestCase(StoreConnectionTestCase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def test_verify_existing_credential_expired_dispatches_channel_credential_expired(self):
        """저장된 자격증명이 이후 만료/거부되는 상황을 InMemoryCredentialStore에
        직접 TRIGGER_401(fixture adapter가 UNAUTHORIZED_401로 정규화하는
        값, tests/test_store_connection.py의 기존 계약과 동일)을 심어
        재현한다 — 실제 쿠팡 API 호출은 어디에도 없다."""

        conn, _dup = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS)

        bad_fields = dict(VALID_COUPANG_FIELDS)
        bad_fields["access_key"] = "TRIGGER_401"
        self.store.save(conn.credential_reference, bad_fields)

        resp = self.service.verify_existing(conn.id, self.company.id)
        self.assertFalse(resp.success)

        log = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company.id,
                NotificationEmailLog.event_code == "CHANNEL_CREDENTIAL_EXPIRED",
            )
            .first()
        )
        self.assertIsNotNone(log)
        self.assertIn(f"channel-credential:{conn.id}", log.idempotency_key)

    def test_verify_existing_success_does_not_dispatch(self):

        conn, _dup = self._verify_and_create("COUPANG", VALID_COUPANG_FIELDS)

        resp = self.service.verify_existing(conn.id, self.company.id)
        self.assertTrue(resp.success)

        log_count = (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.event_code == "CHANNEL_CREDENTIAL_EXPIRED")
            .count()
        )
        self.assertEqual(log_count, 0)


# ==========================================================
# 6. marketplace_listing submit() — CHANNEL_POLICY_VIOLATION
#    (Section 3, 2026-08-24)
# ==========================================================

from app.domains.marketplace_listing.schema import (  # noqa: E402
    MarketplaceSubmissionRequest,
)
from tests.test_marketplace_submission import (  # noqa: E402
    MarketplaceSubmissionTestCase, VALID_COUPANG_SELLER_FULFILLED_FIELDS,
    _next_key,
)


class ChannelPolicyViolationNotificationTestCase(MarketplaceSubmissionTestCase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def _blocked_listing(self):

        from app.domains.channel_policy.constants import ChannelPolicyResult
        from app.domains.channel_policy.model import ChannelPolicyEvaluation

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        self._approve(listing, selection)

        # 실제 정책 엔진(카테고리/필수필드 매칭)을 다시 재현하지 않고,
        # "최신 저장 평가가 BLOCKED"라는 이 테스트가 검증하려는 조건만
        # 직접 만든다 — 제출 게이트(current_valid_channel_policy)는
        # BLOCKED 결과를 fingerprint 일치 여부와 무관하게 즉시
        # 무효로 취급하므로, 이 한 행만으로 게이트를 확실히 막을 수
        # 있다.
        self.db.add(ChannelPolicyEvaluation(
            company_id=self.company_id,
            product_candidate_id=listing.product_candidate_id,
            channel=channel.code, result=ChannelPolicyResult.CHANNEL_POLICY_BLOCKED,
            policy_profile_version="TEST-BLOCKED", rule_results_json="[]",
            input_fingerprint="deliberately-mismatched-fingerprint",
            evaluated_by=1,
        ))
        self.db.commit()

        return listing, selection

    def test_blocked_evaluation_dispatches_channel_policy_violation(self):

        listing, selection = self._blocked_listing()

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )
        self.assertEqual(result.status, "FAILED")

        log = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company_id,
                NotificationEmailLog.event_code == "CHANNEL_POLICY_VIOLATION",
            )
            .first()
        )
        self.assertIsNotNone(log)
        self.assertIn(f"channel-policy-violation:{listing.id}", log.idempotency_key)

    def test_repeated_submit_attempts_do_not_duplicate_notification(self):

        listing, selection = self._blocked_listing()

        for _ in range(3):
            self.submission_service.submit(
                MarketplaceSubmissionRequest(
                    listing_id=listing.id, selection_id=selection.id,
                    idempotency_key=_next_key("sub"),
                ),
                self.company_id,
            )

        count = (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.event_code == "CHANNEL_POLICY_VIOLATION")
            .count()
        )
        self.assertEqual(
            count, 1,
            "동일 BLOCKED 평가 상태로 반복 제출했는데 알림이 중복 발송됨",
        )

    def test_missing_evaluation_does_not_dispatch(self):
        """평가 자체가 아예 없는 경우(단순 미평가)는 '위반'이 아니므로
        알림을 발송하지 않는다 — 승인 없는 제출 차단과 동일 계열의
        기존 테스트(test_submission_without_any_approval_is_blocked)가
        이미 검증하는 시나리오지만, 그 경로가 알림을 만들지 않는다는
        점만 별도로 확인한다."""

        self._allow_automation()
        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id)
        listing, selection = self._listing_with_selection(
            candidate.id, account.id,
        )
        self._approve(listing, selection)

        # _approve()가 이미 SUBMITTABLE 평가를 심어 두므로, 그 평가를
        # 지워 "미평가" 상태로 되돌린다.
        from app.domains.channel_policy.model import ChannelPolicyEvaluation
        self.db.query(ChannelPolicyEvaluation).delete()
        self.db.commit()

        result = self.submission_service.submit(
            MarketplaceSubmissionRequest(
                listing_id=listing.id, selection_id=selection.id,
                idempotency_key=_next_key("sub"),
            ),
            self.company_id,
        )
        self.assertEqual(result.status, "FAILED")

        count = (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.event_code == "CHANNEL_POLICY_VIOLATION")
            .count()
        )
        self.assertEqual(count, 0)


# ==========================================================
# 7. retail_purchase run_policy_check() — MARGIN_BELOW_MINIMUM
#    (Section 3, 2026-08-24)
# ==========================================================

from tests.test_retail_purchase_service import (  # noqa: E402
    RetailPurchaseServiceTestCaseBase,
)


class MarginBelowMinimumNotificationTestCase(RetailPurchaseServiceTestCaseBase):

    def setUp(self):

        super().setUp()
        _add_notification_tables(self.engine)

    def test_margin_shortfall_dispatches_margin_below_minimum(self):

        order = self._create_request(key="rp:margin-notif")
        self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(
                coupang_sale_amount=Decimal("15000"),
                retail_actual_amount=Decimal("13000"),
                shipping_fee=Decimal("3000"),
                coupang_fee_amount=Decimal("1000"),
            ),
        )

        log = (
            self.db.query(NotificationEmailLog)
            .filter(
                NotificationEmailLog.company_id == self.company.id,
                NotificationEmailLog.event_code == "MARGIN_BELOW_MINIMUM",
            )
            .first()
        )
        self.assertIsNotNone(log)
        self.assertIn(f"margin-below-minimum:{order.id}", log.idempotency_key)

    def test_repeated_identical_shortfall_does_not_duplicate_notification(self):

        order = self._create_request(key="rp:margin-notif-dup")
        bad_input = self._good_policy_input(
            coupang_sale_amount=Decimal("15000"),
            retail_actual_amount=Decimal("13000"),
            shipping_fee=Decimal("3000"),
            coupang_fee_amount=Decimal("1000"),
        )

        for _ in range(3):
            order.status = "PROPOSED"
            self.db.commit()
            self.service.run_policy_check(order.id, self.company.id, bad_input)

        count = (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.event_code == "MARGIN_BELOW_MINIMUM")
            .count()
        )
        self.assertEqual(
            count, 1,
            "동일한 가격/원가/배송비/수수료로 반복 판정했는데 알림이 중복 발송됨",
        )

    def test_shortfall_recovers_then_drops_again_dispatches_new_notification(self):

        order = self._create_request(key="rp:margin-notif-recover")
        self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(
                coupang_sale_amount=Decimal("15000"),
                retail_actual_amount=Decimal("13000"),
                shipping_fee=Decimal("3000"),
                coupang_fee_amount=Decimal("1000"),
            ),
        )

        order.status = "PROPOSED"
        self.db.commit()

        # 매입가가 낮아져 마진이 회복된 뒤 정책 통과(BLOCK 아님) —
        # 그 다음 다시 다른 수치로 미달시킨다.
        self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )

        order.status = "PROPOSED"
        self.db.commit()

        self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(
                coupang_sale_amount=Decimal("14000"),
                retail_actual_amount=Decimal("13000"),
                shipping_fee=Decimal("3000"),
                coupang_fee_amount=Decimal("1000"),
            ),
        )

        count = (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.event_code == "MARGIN_BELOW_MINIMUM")
            .count()
        )
        self.assertEqual(
            count, 2,
            "값이 다른 2번의 서로 다른 미달 상태인데 알림이 2건이 아님",
        )

    def test_missing_cost_evidence_does_not_dispatch_margin_below_minimum(self):
        """값을 몰라서 계산 자체를 못하는 EVIDENCE_REQUIRED는 실제
        계산된 마진 미달이 아니므로 발송하지 않는다(허위 알림 금지) —
        coupang_sale_amount가 없으면 net_profit/margin_rate 계산
        자체를 건너뛰므로(0으로 대체하지 않음) margin_reasons에
        해당하는 사유가 전혀 생기지 않는다."""

        order = self._create_request(key="rp:margin-notif-evidence")
        self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(coupang_sale_amount=None),
        )

        count = (
            self.db.query(NotificationEmailLog)
            .filter(NotificationEmailLog.event_code == "MARGIN_BELOW_MINIMUM")
            .count()
        )
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
