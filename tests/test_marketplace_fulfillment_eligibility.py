"""
=========================================================
Homez OS

File : tests/test_marketplace_fulfillment_eligibility.py

채널·계정별 자격 검증 — fail-closed 검증:
6) 로켓그로스 자격 미확인 거부
7) 로켓배송 직매입 계약 미확인 거부
9) UNKNOWN 자격 fail-closed
10) 자격 만료 후 제출 거부
20) 채널별 감사 로그(자격 이력 부분)
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import ContractStatus
from app.domains.marketplace_listing.constants import EligibilityState
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.eligibility_service import (
    EligibilityService,
)
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
from app.domains.marketplace_listing.schema import (
    MarketplaceEligibilityCheckRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceEligibilityManualReviewRequest,
)
from app.domains.product_candidate.model import ProductCandidate
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

_COUNTER = 0


def _next_key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


class MarketplaceFulfillmentEligibilityTestCase(unittest.TestCase):

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
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceFulfillmentCapability.__table__,
                MarketplaceListingDraft.__table__,
                MarketplaceListing.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        company = Company(
            name="테스트 회사", business_number="555-55-55555", ceo="테스트",
            phone="02-000-0000", email="elig@example.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        self.service = EligibilityService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _channel(self, code="COUPANG", name="쿠팡") -> MarketplaceChannel:

        channel = MarketplaceChannel(
            code=code, name=name, doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        return channel

    def _account(self, channel_id: int, **kwargs) -> MarketplaceAccount:

        account = MarketplaceAccount(
            company_id=self.company_id,
            channel_id=channel_id,
            account_code=kwargs.get("account_code", "acct-1"),
            account_name=kwargs.get("account_name", "계정1"),
            direct_purchase_contract_status=kwargs.get(
                "direct_purchase_contract_status", ContractStatus.NONE,
            ),
            direct_purchase_contract_expires_at=kwargs.get(
                "direct_purchase_contract_expires_at",
            ),
        )
        self.db.add(account)
        self.db.commit()

        return account

    def _capability(
        self, channel_id: int, mode: str, requires_eligibility=True,
        requires_contract=False, status=CapabilityStatus.VERIFIED,
    ) -> MarketplaceFulfillmentCapability:

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel_id,
            fulfillment_mode=mode,
            is_supported=True,
            requires_eligibility_check=requires_eligibility,
            requires_account_contract=requires_contract,
            external_display_name=mode,
            policy_version="1.0.0",
            doc_source_reference="test",
            status=status,
            verified_at=datetime.utcnow() if status == CapabilityStatus.VERIFIED else None,
        )
        self.db.add(capability)
        self.db.commit()

        return capability

    # ----------------------------------------------------
    # 6) 로켓그로스 자격 미확인 거부
    # ----------------------------------------------------

    def test_rocket_growth_without_eligibility_check_is_manual_review(self):

        channel = self._channel()
        account = self._account(channel.id)
        self._capability(
            channel.id, FulfillmentMode.MARKETPLACE_FULFILLED,
            requires_eligibility=True,
        )

        result = self.service.check(
            MarketplaceEligibilityCheckRequest(
                marketplace_account_id=account.id,
                fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                idempotency_key=_next_key("elig"),
            ),
            self.company_id,
        )

        # 자동 확인 경로가 없으므로 자동으로 VERIFIED가 되지 않는다.
        self.assertEqual(result.state, EligibilityState.MANUAL_REVIEW_REQUIRED)
        self.assertFalse(
            self.service.is_usable(
                account.id, FulfillmentMode.MARKETPLACE_FULFILLED,
                self.company_id,
            ),
        )

    # ----------------------------------------------------
    # 7) 로켓배송 직매입 계약 미확인 거부
    # ----------------------------------------------------

    def test_direct_purchase_without_verified_contract_is_rejected(self):

        channel = self._channel()
        account = self._account(
            channel.id,
            direct_purchase_contract_status=ContractStatus.PENDING,
        )
        self._capability(
            channel.id, FulfillmentMode.DIRECT_PURCHASE,
            requires_eligibility=True, requires_contract=True,
        )

        result = self.service.check(
            MarketplaceEligibilityCheckRequest(
                marketplace_account_id=account.id,
                fulfillment_mode=FulfillmentMode.DIRECT_PURCHASE,
                idempotency_key=_next_key("elig"),
            ),
            self.company_id,
        )

        self.assertEqual(result.state, EligibilityState.REJECTED)
        self.assertFalse(
            self.service.is_usable(
                account.id, FulfillmentMode.DIRECT_PURCHASE, self.company_id,
            ),
        )

    def test_direct_purchase_with_verified_contract_is_usable(self):

        channel = self._channel()
        account = self._account(
            channel.id,
            direct_purchase_contract_status=ContractStatus.VERIFIED,
            direct_purchase_contract_expires_at=(
                datetime.utcnow() + timedelta(days=365)
            ),
        )
        self._capability(
            channel.id, FulfillmentMode.DIRECT_PURCHASE,
            requires_eligibility=True, requires_contract=True,
        )

        result = self.service.check(
            MarketplaceEligibilityCheckRequest(
                marketplace_account_id=account.id,
                fulfillment_mode=FulfillmentMode.DIRECT_PURCHASE,
                idempotency_key=_next_key("elig"),
            ),
            self.company_id,
        )

        self.assertEqual(result.state, EligibilityState.VERIFIED)
        self.assertTrue(
            self.service.is_usable(
                account.id, FulfillmentMode.DIRECT_PURCHASE, self.company_id,
            ),
        )

    # ----------------------------------------------------
    # 9) UNKNOWN 자격 fail-closed
    # ----------------------------------------------------

    def test_no_history_means_unknown_not_usable(self):

        channel = self._channel()
        account = self._account(channel.id)
        self._capability(
            channel.id, FulfillmentMode.MARKETPLACE_FULFILLED,
        )

        state = self.service.current_state(
            account.id, FulfillmentMode.MARKETPLACE_FULFILLED, self.company_id,
        )

        self.assertEqual(state, EligibilityState.UNKNOWN)
        self.assertFalse(
            self.service.is_usable(
                account.id, FulfillmentMode.MARKETPLACE_FULFILLED,
                self.company_id,
            ),
        )

    def test_capability_not_verified_yields_unknown(self):

        channel = self._channel()
        account = self._account(channel.id)
        self._capability(
            channel.id, FulfillmentMode.MARKETPLACE_FULFILLED,
            status=CapabilityStatus.DRAFT,
        )

        result = self.service.check(
            MarketplaceEligibilityCheckRequest(
                marketplace_account_id=account.id,
                fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                idempotency_key=_next_key("elig"),
            ),
            self.company_id,
        )

        self.assertEqual(result.state, EligibilityState.UNKNOWN)

    # ----------------------------------------------------
    # 10) 자격 만료 후 제출 거부
    # ----------------------------------------------------

    def test_expired_eligibility_is_not_usable(self):

        channel = self._channel()
        account = self._account(channel.id)
        capability = self._capability(
            channel.id, FulfillmentMode.MARKETPLACE_FULFILLED,
        )

        # 수동 확인으로 VERIFIED를 확정하되 이미 만료된 시각으로 설정.
        self.service.manual_review(
            MarketplaceEligibilityManualReviewRequest(
                marketplace_account_id=account.id,
                fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                new_state=EligibilityState.VERIFIED,
                review_reason="테스트 승인",
                expires_at=datetime.utcnow() - timedelta(days=1),
                idempotency_key=_next_key("review"),
            ),
            reviewer_id=1, company_id=self.company_id,
        )

        state = self.service.current_state(
            account.id, FulfillmentMode.MARKETPLACE_FULFILLED, self.company_id,
        )
        self.assertEqual(state, EligibilityState.EXPIRED)
        self.assertFalse(
            self.service.is_usable(
                account.id, FulfillmentMode.MARKETPLACE_FULFILLED,
                self.company_id,
            ),
        )

    def test_manual_review_verified_requires_expiry(self):
        """VERIFIED로 확정하려면 expires_at이 필수다(무기한 자동 유효 금지)."""

        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id, FulfillmentMode.MARKETPLACE_FULFILLED)

        with self.assertRaises(BadRequestException):
            self.service.manual_review(
                MarketplaceEligibilityManualReviewRequest(
                    marketplace_account_id=account.id,
                    fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                    new_state=EligibilityState.VERIFIED,
                    review_reason="테스트",
                    expires_at=None,
                    idempotency_key=_next_key("review"),
                ),
                reviewer_id=1, company_id=self.company_id,
            )

    # ----------------------------------------------------
    # 20) 채널별 감사 로그(자격 이력)
    # ----------------------------------------------------

    def test_eligibility_history_is_append_only_audit_trail(self):

        channel = self._channel()
        account = self._account(channel.id)
        self._capability(channel.id, FulfillmentMode.MARKETPLACE_FULFILLED)

        self.service.check(
            MarketplaceEligibilityCheckRequest(
                marketplace_account_id=account.id,
                fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                idempotency_key=_next_key("elig"),
            ),
            self.company_id,
        )
        self.service.manual_review(
            MarketplaceEligibilityManualReviewRequest(
                marketplace_account_id=account.id,
                fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                new_state=EligibilityState.VERIFIED,
                review_reason="검토 완료",
                expires_at=datetime.utcnow() + timedelta(days=90),
                idempotency_key=_next_key("review"),
            ),
            reviewer_id=42, company_id=self.company_id,
        )

        history = self.service.history(
            account.id, FulfillmentMode.MARKETPLACE_FULFILLED, self.company_id,
        )

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].state, EligibilityState.MANUAL_REVIEW_REQUIRED)
        self.assertEqual(history[1].state, EligibilityState.VERIFIED)
        self.assertEqual(history[1].reviewer_id, 42)
        self.assertEqual(history[1].review_reason, "검토 완료")

    def test_eligibility_is_never_reused_across_accounts(self):

        channel = self._channel()
        account_verified = self._account(channel.id, account_code="a1")
        account_other = self._account(channel.id, account_code="a2")
        self._capability(channel.id, FulfillmentMode.MARKETPLACE_FULFILLED)

        self.service.manual_review(
            MarketplaceEligibilityManualReviewRequest(
                marketplace_account_id=account_verified.id,
                fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                new_state=EligibilityState.VERIFIED,
                review_reason="계정1 승인",
                expires_at=datetime.utcnow() + timedelta(days=90),
                idempotency_key=_next_key("review"),
            ),
            reviewer_id=1, company_id=self.company_id,
        )

        self.assertTrue(
            self.service.is_usable(
                account_verified.id, FulfillmentMode.MARKETPLACE_FULFILLED,
                self.company_id,
            ),
        )
        self.assertFalse(
            self.service.is_usable(
                account_other.id, FulfillmentMode.MARKETPLACE_FULFILLED,
                self.company_id,
            ),
        )


if __name__ == "__main__":
    unittest.main()
