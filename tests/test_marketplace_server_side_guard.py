"""
=========================================================
Homez OS

File : tests/test_marketplace_server_side_guard.py

UI 우회 방어 검증:
8) UI disabled 조작 후 서버 요청도 거부
21) 권한 없는 변경 차단
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.guard import admin_guard
from app.database.base import Base
from app.domains.company.model import Company
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
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentSelectionCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

_COUNTER = 0


def _next_key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


class MarketplaceServerSideGuardTestCase(unittest.TestCase):
    """
    8) UI가 "선택 불가"로 비활성화한 방식을 devtools 등으로 우회해
       요청해도, 서비스는 UI와 동일한 이유로 거부한다 — UI 비활성화
       "만"으로 보호하지 않는다.
    21) FastAPI 라우터 자체가 admin_guard 없이는 호출될 수 없다(권한
        없는 변경 차단) — TestClient로 인증 없이 직접 호출해 확인한다.
    """

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
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        company = Company(
            name="테스트 회사", business_number="101-01-01011", ceo="테스트",
            phone="02-000-0000", email="guard@example.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        self.service = MarketplaceListingService(self.db)

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

    def _account(self, channel_id: int) -> MarketplaceAccount:

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel_id,
            account_code="a1", account_name="계정1",
        )
        self.db.add(account)
        self.db.commit()

        return account

    def _candidate(self) -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key="test:COUPANG:CAND-1", source_type="TREND",
            source_reference="CAND-1", market="COUPANG",
            product_name="테스트 상품", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    # ----------------------------------------------------
    # 8) UI disabled 조작 후 서버 요청도 거부
    # ----------------------------------------------------

    def test_disabled_direct_purchase_is_rejected_even_via_direct_call(self):
        """
        UI는 직매입 계약 미인증 계정에서 이 라디오를 disabled로
        렌더링하지만(app/web/console.js), 이 테스트는 그 UI 계층을
        건너뛰고 서비스 메서드를 직접 호출해도(devtools로 값만 바꿔
        보내는 것과 동일한 상황) 서버가 거부하는지 검증한다.
        """

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel.id,
            fulfillment_mode=FulfillmentMode.DIRECT_PURCHASE,
            # 정책 조사 결과 그대로 — is_supported=False
            is_supported=False,
            requires_eligibility_check=True,
            requires_account_contract=True,
            external_display_name="로켓배송(직매입)",
            policy_version="1.0.0",
            doc_source_reference="test",
            status=CapabilityStatus.VERIFIED,
            verified_at=datetime.utcnow(),
        )
        self.db.add(capability)
        self.db.commit()

        listing, _dup = self.service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )

        with self.assertRaises(BadRequestException):
            self.service.select_fulfillment_mode(
                MarketplaceFulfillmentSelectionCreateRequest(
                    listing_id=listing.id,
                    fulfillment_mode=FulfillmentMode.DIRECT_PURCHASE,
                    required_fields={"contract_ref": "x"},
                    idempotency_key=_next_key("sel"),
                ),
                selected_by=1, company_id=self.company_id,
            )

    def test_capability_not_yet_verified_is_rejected(self):
        """캡빌리티가 아직 DRAFT(미인증)면 UI에 노출되면 안 되고,
        서버도 거부해야 한다."""

        candidate = self._candidate()
        channel = self._channel()
        account = self._account(channel.id)

        capability = MarketplaceFulfillmentCapability(
            channel_id=channel.id,
            fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
            is_supported=True,
            requires_eligibility_check=True,
            requires_account_contract=False,
            external_display_name="로켓그로스",
            policy_version="1.0.0",
            doc_source_reference="test",
            status=CapabilityStatus.DRAFT,
        )
        self.db.add(capability)
        self.db.commit()

        listing, _dup = self.service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            self.company_id,
        )

        with self.assertRaises(BadRequestException):
            self.service.select_fulfillment_mode(
                MarketplaceFulfillmentSelectionCreateRequest(
                    listing_id=listing.id,
                    fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                    required_fields={"a": "b"},
                    idempotency_key=_next_key("sel"),
                ),
                selected_by=1, company_id=self.company_id,
            )

    # ----------------------------------------------------
    # 21) 권한 없는 변경 차단
    # ----------------------------------------------------

    def test_all_marketplace_listing_routes_require_admin_guard(self):
        """
        tests/test_homez_console.py::AdminGuardCoverageTestCase와 동일한
        정적 검증 패턴 — 모든 /marketplace-listings/* 엔드포인트의
        Dependant 체인에 admin_guard(또는 Gate R-2에서 의도적으로
        Permission 기반으로 전환한 읽기 전용 3개 엔드포인트의 경우
        ListingWizardPermissionGuard)가 실제로 걸려 있는지 확인한다(인증
        우회 가능성 차단). httpx 없이도 라우팅 계층 자체를 검증할 수
        있어 실제 서버 기동 없이도 신뢰도 있게 확인 가능하다.

        Gate R-2(2026-08-09) — 상품등록 마법사가 화면을 그리는 데
        필요한 읽기 전용 조회 API(채널 목록·자격 목록·계정 목록)
        3개는 admin_guard에서 LISTING_WIZARD_VIEW Permission 기반
        Guard로 전환됐다(VIEWER도 읽기는 가능해야 하므로) — 나머지
        모든 쓰기 엔드포인트는 그대로 admin_guard다.
        """

        from app.domains.marketplace_listing.router import router

        self.assertGreaterEqual(len(router.routes), 10)

        permission_guard_paths = {
            "/marketplace-listings/channels",
            "/marketplace-listings/channels/{channel_id}/capabilities",
            "/marketplace-listings/channels/{channel_id}/accounts",
        }

        for route in router.routes:
            calls = [d.call for d in route.dependant.dependencies]

            if route.path in permission_guard_paths:
                self.assertNotIn(
                    admin_guard, calls,
                    f"{route.path} 은(는) Gate R-2에서 Permission 기반 "
                    "읽기 전용으로 전환됐어야 합니다 — admin_guard가 "
                    "남아 있습니다.",
                )
                self.assertTrue(
                    any(
                        getattr(c, "__qualname__", "").startswith(
                            "ListingWizardPermissionGuard.",
                        )
                        for c in calls
                    ),
                    f"{route.path} 이(가) ListingWizardPermissionGuard "
                    "없이 노출되어 있습니다.",
                )
                continue

            self.assertIn(
                admin_guard, calls,
                f"{route.path} 이(가) admin_guard 없이 노출되어 있습니다 "
                "(권한 없는 변경이 차단되지 않습니다).",
            )


if __name__ == "__main__":
    unittest.main()
