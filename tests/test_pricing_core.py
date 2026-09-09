"""
=========================================================
Homez OS

File : tests/test_pricing_core.py

V7 Gate 5(2026-08-15) — Pricing & Margin Reconciliation 핵심 기능
검증(요구사항 1/3/7). 임시 SQLite 파일 DB만 사용한다 — 실제 homez.db
는 이 테스트 전체에서 전혀 접근하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.core.security import hash_password
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    user_can,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_ECONOMICS_VIEW,
)
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.permission.model import Permission
from app.domains.pricing.constants import MarginSnapshotReason
from app.domains.pricing.constants import MarginType
from app.domains.pricing.constants import PriceChangeStatus
from app.domains.pricing.model import MarginSnapshot
from app.domains.pricing.model import PriceChangeRequest
from app.domains.pricing.model import PriceChangeStatusEvent
from app.domains.pricing.model import ProductPricing
from app.domains.pricing.model import SettlementReconciliation
from app.domains.pricing.router import _serialize_pricing
from app.domains.pricing.schema import EconomicsInputsUpdate
from app.domains.pricing.schema import PriceChangeCreate
from app.domains.pricing.schema import PriceChangeDecision
from app.domains.pricing.schema import ProductPricingInitCreate
from app.domains.pricing.service import PricingService
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User


class PricingTestCaseBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                Role.__table__,
                Permission.__table__,
                RolePermission.__table__,
                User.__table__,
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceListing.__table__,
                ProductPricing.__table__,
                PriceChangeRequest.__table__,
                PriceChangeStatusEvent.__table__,
                MarginSnapshot.__table__,
                SettlementReconciliation.__table__,
            ],
        )

        # 2026-08-15 V7 Gate 8 — approve_price_change()가 이제
        # write_audit_log()를 쓴다(가격변경승인은 재고 수동조정과
        # 동일한 민감도로 취급). audit_logs는 ORM Model이 없는
        # raw-SQL 전용 테이블이라(app/core/audit_db.py 참고)
        # Base.metadata.create_all()로는 생기지 않는다 — inventory
        # 테스트(tests/test_inventory_core.py)와 동일한 DDL을 그대로
        # 재사용해 수동으로 만든다.
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, "
                "description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()
        self.company_id = self.company.id

        self.admin_role = Role(name="Administrator", code="ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.admin_role, self.viewer_role])
        self.db.commit()

        self.economics_permission = Permission(
            name=LISTING_ECONOMICS_VIEW, code=LISTING_ECONOMICS_VIEW,
            active=True,
        )
        self.db.add(self.economics_permission)
        self.db.commit()

        self.admin_user = User(
            username="admin1", email="admin1@example.com",
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=self.admin_role.id, company_id=self.company_id,
            is_active=True,
        )
        self.viewer_user = User(
            username="viewer1", email="viewer1@example.com",
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=self.viewer_role.id, company_id=self.company_id,
            is_active=True,
        )
        self.db.add_all([self.admin_user, self.viewer_user])
        self.db.commit()

        channel = MarketplaceChannel(
            code="FAKE", name="가짜채널", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel.id,
            account_code="acc1", account_name="계정1",
        )
        self.db.add(account)
        self.db.commit()

        listing = MarketplaceListing(
            company_id=self.company_id, product_candidate_id=1,
            marketplace_account_id=account.id, status="DRAFT",
        )
        self.db.add(listing)
        self.db.commit()
        self.listing_id = listing.id

        self.service = PricingService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _grant_economics_view(self, role: Role):

        self.db.add(RolePermission(
            role_id=role.id, permission_id=self.economics_permission.id,
        ))
        self.db.commit()

    def _init_pricing(
        self, sale_price="10000", cost="5000", channel_fee_rate="0.1",
        payment_fee_rate="0.03",
    ) -> ProductPricing:

        return self.service.initialize_pricing(
            self.company_id,
            ProductPricingInitCreate(
                listing_id=self.listing_id,
                initial_sale_price=Decimal(sale_price),
                cost_of_goods=Decimal(cost),
                shipping_cost=Decimal("500"),
                packaging_cost=Decimal("100"),
                ad_cost=Decimal("200"),
                channel_fee_rate=Decimal(channel_fee_rate),
                payment_fee_rate=Decimal(payment_fee_rate),
                return_reserve_rate=Decimal("0.01"),
                tax_basis_rate=Decimal("0.0"),
            ),
            self.admin_user.id,
        )


# ====================================================
# 요구사항 1 — 원가/배송비/수수료/광고비/세금 반영 마진 계산
# ====================================================

class PricingInitializationTestCase(PricingTestCaseBase):

    def test_initialize_pricing_matches_margin_calculator_directly(self):
        """
        margin_calculator.calculate_economics()를 직접 호출한 결과와
        서비스가 계산한 결과가 정확히 일치해야 한다(재구현이 아니라
        재사용임을 증명).
        """

        from app.domains.marketplace_listing.listing_wizard_schema import (
            EconomicsInputItem,
        )
        from app.domains.marketplace_listing.margin_calculator import (
            calculate_economics,
        )

        pricing = self._init_pricing()

        expected = calculate_economics(EconomicsInputItem(
            marketplace_account_id=self.listing_id,
            cost_of_goods=Decimal("5000"),
            sale_price=Decimal("10000"),
            channel_fee_rate=Decimal("0.1"),
            payment_fee_rate=Decimal("0.03"),
            shipping_cost=Decimal("500"),
            packaging_cost=Decimal("100"),
            ad_cost=Decimal("200"),
            return_reserve_rate=Decimal("0.01"),
            tax_basis_rate=Decimal("0.0"),
        ))

        self.assertEqual(
            Decimal(pricing.expected_margin_amount), expected.margin_amount,
        )
        self.assertEqual(
            Decimal(pricing.expected_margin_rate), expected.margin_rate,
        )
        self.assertEqual(
            Decimal(pricing.expected_total_cost), expected.total_cost,
        )

        # EXPECTED MarginSnapshot이 함께 남아야 한다(요구사항 2).
        snapshots = self.service.list_margin_snapshots(
            self.listing_id, self.company_id, MarginType.EXPECTED,
        )
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(
            snapshots[0].reason, MarginSnapshotReason.PRICING_INITIALIZED,
        )

    def test_initialize_pricing_is_idempotent_per_listing(self):

        first = self._init_pricing()
        second = self._init_pricing(sale_price="99999")

        self.assertEqual(first.id, second.id)
        self.assertEqual(
            Decimal(second.current_sale_price), Decimal("10000"),
        )

    def test_update_economics_inputs_recomputes_expected_margin(self):

        pricing = self._init_pricing()
        original_margin_amount = Decimal(pricing.expected_margin_amount)
        original_version = pricing.version

        updated = self.service.update_economics_inputs(
            pricing.id, self.company_id,
            EconomicsInputsUpdate(
                cost_of_goods=Decimal("6000"), shipping_cost=Decimal("500"),
                packaging_cost=Decimal("100"), ad_cost=Decimal("200"),
                channel_fee_rate=Decimal("0.1"),
                payment_fee_rate=Decimal("0.03"),
                return_reserve_rate=Decimal("0.01"),
                tax_basis_rate=Decimal("0.0"),
            ),
            self.admin_user.id,
        )

        self.assertEqual(Decimal(updated.cost_of_goods), Decimal("6000"))
        self.assertLess(
            Decimal(updated.expected_margin_amount), original_margin_amount,
        )
        self.assertEqual(updated.version, original_version + 1)

        snapshots = self.service.list_margin_snapshots(
            self.listing_id, self.company_id, MarginType.EXPECTED,
        )
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(
            snapshots[0].reason, MarginSnapshotReason.ECONOMICS_UPDATE,
        )

    def test_update_economics_inputs_unknown_pricing_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.update_economics_inputs(
                999999, self.company_id,
                EconomicsInputsUpdate(cost_of_goods=Decimal("1")),
                self.admin_user.id,
            )


# ====================================================
# 요구사항 3 — 가격 변경 승인 흐름 + 이력
# ====================================================

class PriceChangeApprovalTestCase(PricingTestCaseBase):

    def test_request_then_approve_applies_price_and_records_history(self):

        pricing = self._init_pricing()

        request = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("12000"), reason="원가 상승",
                idempotency_key="pc:1",
            ),
            self.admin_user.id,
        )
        self.assertEqual(request.status, PriceChangeStatus.PENDING)

        refreshed_pricing = self.service.get_pricing(
            pricing.id, self.company_id,
        )
        self.assertEqual(
            refreshed_pricing.pending_price_change_id, request.id,
        )

        approved = self.service.approve_price_change(
            request.id, self.company_id, self.admin_user.id,
            PriceChangeDecision(decision_reason="승인"),
        )
        self.assertEqual(approved.status, PriceChangeStatus.APPROVED)
        self.assertEqual(approved.decided_by, self.admin_user.id)

        final_pricing = self.service.get_pricing(pricing.id, self.company_id)
        self.assertEqual(
            Decimal(final_pricing.current_sale_price), Decimal("12000"),
        )
        self.assertIsNone(final_pricing.pending_price_change_id)

        # append-only 이력 — REQUESTED/PENDING → APPROVED 2개 행.
        events = self.service.list_status_events(
            request.id, self.company_id,
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].new_status, PriceChangeStatus.PENDING)
        self.assertEqual(events[1].new_status, PriceChangeStatus.APPROVED)

        # EXPECTED MarginSnapshot이 새 가격 기준으로 하나 더 남는다.
        latest_expected = self.service.list_margin_snapshots(
            self.listing_id, self.company_id, MarginType.EXPECTED,
        )[0]
        self.assertEqual(latest_expected.revenue, Decimal("12000.00"))
        self.assertEqual(
            latest_expected.reason,
            MarginSnapshotReason.PRICE_CHANGE_APPROVED,
        )

    def test_request_price_change_still_works_when_pricing_inventory_capability_deactivated(
        self,
    ):
        """Audit(2026-08-21, AG-0) — 가격 변경 요청은 운영자가 직접
        수행하는 핵심 업무다. PRICING_INVENTORY AI Capability가
        비활성이어도 정상 동작해야 한다(이전 라운드의 잘못된 게이트를
        되돌린 회귀 방지 테스트)."""

        from tests.ai_governance_test_helpers import deactivated_capability

        self._init_pricing()

        with deactivated_capability("PRICING_INVENTORY"):
            request = self.service.request_price_change(
                self.company_id, self.listing_id,
                PriceChangeCreate(
                    requested_sale_price=Decimal("12000"),
                    reason="테스트", idempotency_key="pc:manual-flow-unblocked",
                ),
                self.admin_user.id,
            )

        self.assertEqual(request.status, PriceChangeStatus.PENDING)

    def test_duplicate_pending_request_is_blocked(self):

        self._init_pricing()

        self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("11000"),
                idempotency_key="pc:1",
            ),
            self.admin_user.id,
        )

        with self.assertRaises(ConflictException):
            self.service.request_price_change(
                self.company_id, self.listing_id,
                PriceChangeCreate(
                    requested_sale_price=Decimal("13000"),
                    idempotency_key="pc:2",
                ),
                self.admin_user.id,
            )

    def test_request_price_change_is_idempotent_on_same_key(self):

        self._init_pricing()

        first = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("11000"),
                idempotency_key="pc:same",
            ),
            self.admin_user.id,
        )
        second = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("11000"),
                idempotency_key="pc:same",
            ),
            self.admin_user.id,
        )

        self.assertEqual(first.id, second.id)

    def test_approve_fails_if_economics_changed_after_request(self):
        """
        요청 시점과 승인 시점 사이 원가가 바뀌면 fingerprint 불일치로
        승인이 거부되고 재요청을 요구해야 한다 — 낡은 전제로 가격을
        승인하지 않는다.
        """

        pricing = self._init_pricing()

        request = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("12000"),
                idempotency_key="pc:1",
            ),
            self.admin_user.id,
        )

        self.service.update_economics_inputs(
            pricing.id, self.company_id,
            EconomicsInputsUpdate(
                cost_of_goods=Decimal("7000"), shipping_cost=Decimal("500"),
                packaging_cost=Decimal("100"), ad_cost=Decimal("200"),
                channel_fee_rate=Decimal("0.1"),
                payment_fee_rate=Decimal("0.03"),
                return_reserve_rate=Decimal("0.01"),
                tax_basis_rate=Decimal("0.0"),
            ),
            self.admin_user.id,
        )

        with self.assertRaises(ConflictException):
            self.service.approve_price_change(
                request.id, self.company_id, self.admin_user.id, None,
            )

        # 원가 변경이 가격에 잘못 반영되지 않아야 한다.
        untouched = self.service.get_pricing(pricing.id, self.company_id)
        self.assertEqual(
            Decimal(untouched.current_sale_price), Decimal("10000"),
        )
        self.assertEqual(untouched.pending_price_change_id, request.id)

    def test_reapprove_already_approved_is_idempotent_and_does_not_reapply(
        self,
    ):

        pricing = self._init_pricing()

        request = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("12000"),
                idempotency_key="pc:1",
            ),
            self.admin_user.id,
        )
        self.service.approve_price_change(
            request.id, self.company_id, self.admin_user.id, None,
        )

        snapshot_count_before = len(self.service.list_margin_snapshots(
            self.listing_id, self.company_id, MarginType.EXPECTED,
        ))

        again = self.service.approve_price_change(
            request.id, self.company_id, self.admin_user.id, None,
        )
        self.assertEqual(again.status, PriceChangeStatus.APPROVED)

        snapshot_count_after = len(self.service.list_margin_snapshots(
            self.listing_id, self.company_id, MarginType.EXPECTED,
        ))
        self.assertEqual(snapshot_count_before, snapshot_count_after)

    def test_reject_price_change_does_not_change_price(self):

        pricing = self._init_pricing()

        request = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("12000"),
                idempotency_key="pc:1",
            ),
            self.admin_user.id,
        )
        rejected = self.service.reject_price_change(
            request.id, self.company_id, self.admin_user.id,
            PriceChangeDecision(decision_reason="가격 인상 반려"),
        )
        self.assertEqual(rejected.status, PriceChangeStatus.REJECTED)

        untouched = self.service.get_pricing(pricing.id, self.company_id)
        self.assertEqual(
            Decimal(untouched.current_sale_price), Decimal("10000"),
        )
        self.assertIsNone(untouched.pending_price_change_id)

        # 거절 후에는 새 요청을 만들 수 있어야 한다(진행 중 요청 없음).
        new_request = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("11500"),
                idempotency_key="pc:2",
            ),
            self.admin_user.id,
        )
        self.assertEqual(new_request.status, PriceChangeStatus.PENDING)

    def test_only_requester_can_cancel(self):

        self._init_pricing()

        request = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("12000"),
                idempotency_key="pc:1",
            ),
            self.admin_user.id,
        )

        with self.assertRaises(ForbiddenException):
            self.service.cancel_price_change(
                request.id, self.company_id, self.viewer_user.id,
            )

        cancelled = self.service.cancel_price_change(
            request.id, self.company_id, self.admin_user.id,
        )
        self.assertEqual(cancelled.status, PriceChangeStatus.CANCELLED)

    def test_approve_non_pending_raises_bad_request(self):

        self._init_pricing()

        request = self.service.request_price_change(
            self.company_id, self.listing_id,
            PriceChangeCreate(
                requested_sale_price=Decimal("12000"),
                idempotency_key="pc:1",
            ),
            self.admin_user.id,
        )
        self.service.reject_price_change(
            request.id, self.company_id, self.admin_user.id, None,
        )

        with self.assertRaises(BadRequestException):
            self.service.approve_price_change(
                request.id, self.company_id, self.admin_user.id, None,
            )


# ====================================================
# 요구사항 7 — 경제성 정보 접근 제한(economics_view 재사용)
# ====================================================

class EconomicsViewPermissionTestCase(PricingTestCaseBase):

    def test_admin_always_sees_economics_fields(self):

        self._init_pricing()
        pricing = self.service.get_pricing_by_listing(
            self.listing_id, self.company_id,
        )

        payload = _serialize_pricing(self.db, self.admin_user, pricing)
        self.assertIn("current_sale_price", payload)
        self.assertIn("expected_margin_amount", payload)

    def test_viewer_without_grant_never_sees_economics_fields(self):

        self._init_pricing()
        pricing = self.service.get_pricing_by_listing(
            self.listing_id, self.company_id,
        )

        payload = _serialize_pricing(self.db, self.viewer_user, pricing)
        self.assertNotIn("current_sale_price", payload)
        self.assertNotIn("expected_margin_amount", payload)
        self.assertNotIn("cost_of_goods", payload)
        # 비금액 필드는 그대로 보여야 한다.
        self.assertIn("listing_id", payload)
        self.assertIn("version", payload)

    def test_viewer_with_grant_sees_economics_fields(self):

        self._grant_economics_view(self.viewer_role)

        self._init_pricing()
        pricing = self.service.get_pricing_by_listing(
            self.listing_id, self.company_id,
        )

        payload = _serialize_pricing(self.db, self.viewer_user, pricing)
        self.assertIn("current_sale_price", payload)
        self.assertIn("expected_margin_amount", payload)

    def test_user_can_helper_matches_role_permission_state(self):

        self.assertTrue(
            user_can(self.db, self.admin_user, LISTING_ECONOMICS_VIEW),
        )
        self.assertFalse(
            user_can(self.db, self.viewer_user, LISTING_ECONOMICS_VIEW),
        )
        self._grant_economics_view(self.viewer_role)
        self.assertTrue(
            user_can(self.db, self.viewer_user, LISTING_ECONOMICS_VIEW),
        )


if __name__ == "__main__":
    unittest.main()
