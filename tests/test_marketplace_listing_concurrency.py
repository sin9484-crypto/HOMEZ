"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_concurrency.py

채널별 판매 방식 선택 — 동시성 검증(27): 실스레드 + 별도 커넥션으로
동일 idempotency_key 경쟁 시 정확히 하나만 성공하는지 확인한다
(tests/test_coupang_concurrency.py와 동일 패턴).

2026-07-31 재감사 반영: 제출 동시성 테스트는 이제 승인(approval)을
경쟁 이전에 별도로 미리 받아두고, submit() 자체의 새 시그니처(승인
파라미터 없음)로 경쟁시킨다.
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.media_asset.model import MediaAsset
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import FulfillmentMode
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
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.marketplace_listing.model import (
    MarketplaceSubmissionApproval,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentSelectionCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalDecisionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalRequestRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.submission_service import (
    SubmissionService,
)
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)
from tests.channel_policy_test_helpers import seed_channel_eligible_policy

VALID_COUPANG_SELLER_FULFILLED_FIELDS = {
    "deliveryMethod": "AGENT_BUY",
    "deliveryChargeType": "FREE",
    "deliveryCharge": "0",
    "deliveryChargeOnReturn": "2500",
    "returnCharge": "2500",
    "returnCenterCode": "RC001",
    "outboundShippingPlaceCode": "OS001",
    "returnChargeName": "반품지",
    "companyContactNumber": "0212345678",
    "returnZipCode": "12345",
    "returnAddress": "서울시 강남구",
    "items": [{"itemName": "기본형", "externalVendorSku": "SKU-001"}],
    "originalPrice": "10000",
    "salePrice": "9000",
    "maximumBuyCount": 100,
}


class MarketplaceListingConcurrencyTestCase(unittest.TestCase):

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
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmissionApproval.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__,
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
                MediaAsset.__table__,
                ChannelPolicyRule.__table__,
                CompanyChannelPolicySettings.__table__,
                ChannelPolicyEvaluation.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )

        seed_db = self.SessionLocal()
        try:
            company = Company(
                name="테스트 회사", business_number="888-88-88888",
                ceo="테스트", phone="02-000-0000",
                email="concurrency@example.com", address="서울",
            )
            seed_db.add(company)
            seed_db.commit()
            self.company_id = company.id

            candidate = ProductCandidate(
                candidate_key="test:COUPANG:RACE-1", source_type="TREND",
                source_reference="RACE-1", market="COUPANG",
                product_name="경쟁 테스트 상품", status="APPROVED",
            )
            seed_db.add(candidate)
            seed_db.commit()
            self.candidate_id = candidate.id

            channel = MarketplaceChannel(
                code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
            )
            seed_db.add(channel)
            seed_db.commit()

            account = MarketplaceAccount(
                company_id=self.company_id, channel_id=channel.id,
                account_code="a1", account_name="계정1",
            )
            seed_db.add(account)
            seed_db.commit()
            self.account_id = account.id

            capability = MarketplaceFulfillmentCapability(
                channel_id=channel.id,
                fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                is_supported=True, requires_eligibility_check=False,
                requires_account_contract=False,
                external_display_name="판매자배송", policy_version="1.0.0",
                schema_name="coupang_seller_fulfilled", schema_version="1.0.0",
                doc_source_reference="test", status=CapabilityStatus.VERIFIED,
                verified_at=datetime.utcnow(),
            )
            seed_db.add(capability)
            seed_db.commit()

            listing_service = MarketplaceListingService(seed_db)
            listing, _dup = listing_service.create_listing(
                MarketplaceListingCreateRequest(
                    product_candidate_id=self.candidate_id,
                    marketplace_account_id=self.account_id,
                ),
                self.company_id,
            )
            self.listing_id = listing.id

            safety = SafetyService(seed_db)
            safety.set_mode(
                AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
            )

        finally:
            seed_db.close()

    def tearDown(self):

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _threaded_engine(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        return engine2, sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

    def test_concurrent_fulfillment_selection_same_key_creates_one_row(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = MarketplaceListingService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    selection, _dup, _events = service.select_fulfillment_mode(
                        MarketplaceFulfillmentSelectionCreateRequest(
                            listing_id=self.listing_id,
                            fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                            required_fields=VALID_COUPANG_SELLER_FULFILLED_FIELDS,
                            idempotency_key="selection-race-key",
                        ),
                        selected_by=1, company_id=self.company_id,
                    )
                    results[name] = selection.id
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            selection_ids = list(results.values())
            self.assertEqual(len(set(selection_ids)), 1)

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(MarketplaceFulfillmentSelection)
                    .filter(
                        MarketplaceFulfillmentSelection.idempotency_key
                        == "selection-race-key",
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()

        finally:
            engine2.dispose()

    def test_concurrent_submission_same_key_creates_one_row(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            setup_db = SessionLocal2()
            listing_service = MarketplaceListingService(setup_db)
            selection, _dup, _events = listing_service.select_fulfillment_mode(
                MarketplaceFulfillmentSelectionCreateRequest(
                    listing_id=self.listing_id,
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                    required_fields=VALID_COUPANG_SELLER_FULFILLED_FIELDS,
                    idempotency_key="pre-race-selection",
                ),
                selected_by=1, company_id=self.company_id,
            )
            selection_id = selection.id

            # 승인은 제출과 분리된 별도 요청이므로, 경쟁 이전에 미리
            # 받아둔다 — 이 테스트가 검증하려는 것은 승인 자체의
            # 동시성이 아니라 submit() 자체의 idempotency_key 경쟁이다.
            approval_service = ApprovalService(setup_db)
            pending, _dup = approval_service.request_approval(
                MarketplaceSubmissionApprovalRequestRequest(
                    listing_id=self.listing_id, selection_id=selection_id,
                    planned_quantity=10, unit_price=Decimal("9000"),
                    unit_cost_of_goods=Decimal("5000"),
                    expected_logistics_cost=Decimal("500"),
                    idempotency_key="pre-race-approval-request",
                ),
                requested_by=1, company_id=self.company_id,
            )
            approval_service.approve(
                pending.id,
                MarketplaceSubmissionApprovalDecisionRequest(
                    expires_at=datetime.utcnow() + timedelta(days=1),
                    idempotency_key="pre-race-approval-decision",
                ),
                approved_by=99, company_id=self.company_id,
            )
            seed_channel_eligible_policy(
                setup_db, company_id=self.company_id,
                product_candidate_id=self.candidate_id,
                channel="COUPANG", selection_id=selection_id,
            )
            setup_db.close()

            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                safety = SafetyService(thread_db)
                service = SubmissionService(thread_db, safety_service=safety)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    submission = service.submit(
                        MarketplaceSubmissionRequest(
                            listing_id=self.listing_id,
                            selection_id=selection_id,
                            idempotency_key="submission-race-key",
                        ),
                        self.company_id,
                    )
                    results[name] = submission.id
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            submission_ids = list(results.values())
            self.assertEqual(len(set(submission_ids)), 1)

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(MarketplaceSubmission)
                    .filter(
                        MarketplaceSubmission.idempotency_key
                        == "submission-race-key",
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()

        finally:
            engine2.dispose()


if __name__ == "__main__":
    unittest.main()
