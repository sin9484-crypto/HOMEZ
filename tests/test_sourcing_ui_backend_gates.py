"""
=========================================================
Homez OS

File : tests/test_sourcing_ui_backend_gates.py

작업 1·2·4(2026-08-21) — 공급처 검색·연결·발주 UI가 실제로 딛고
서는 백엔드 게이트를 검증한다. httpx 미설치로 TestClient를 쓸 수
없어(tests/test_company_router_security.py와 동일한 관례) 라우터
함수를 직접 호출한다. 실제 homez.db는 사용하지 않는다.

1) SourcingViewGuard — ADMIN은 SUPPLIER_VIEW Permission grant 없이도
   통과, VIEWER 역할은 grant가 있어야만 통과(없으면 403) — 신규
   Permission 코드를 만들지 않고 기존 SUPPLIER_VIEW를 재사용한
   설계의 실제 동작 검증.
2) _redact_relation_for_viewer — 비특권 조회자에게 계약 연락처·
   결제조건·Credential 참조를 숨기는지.
3) list_out_of_stock_order_items — 회사 A/B 격리.
4) PurchaseService.retry_submission_to_supplier — FAILED 상태에서만
   재시도 가능(멱등성/중복발주 방지), 재시도 시 다른 Provider로
   전환 가능(MANUAL 실패 후 FAKE 재시도), 회사 격리, 저장소 레벨
   원자적 단일 claim(reclaim_failed_submission_conditional)의 동시성
   안전성.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.automation_safety.model import EmergencyStop
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.service import FundingService
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku
from app.domains.inventory.schema import InventoryChannelMappingCreate
from app.domains.inventory.schema import InventorySkuCreate
from app.domains.inventory.service import InventoryService
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.order.constants import OrderItemStatus
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.order.model import OrderStatusEvent
from app.domains.order.schema import OrderChannelCollectRequest
from app.domains.order.schema import OrderChannelItemPayload
from app.domains.order.service import OrderService
from app.domains.permission.model import Permission
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.purchase.constants import PurchaseSubmissionStatus
from app.domains.purchase.model import Purchase
from app.domains.purchase.model import PurchaseItem
from app.domains.purchase.repository import PurchaseRepository
from app.domains.purchase.schema import PurchaseCreate
from app.domains.purchase.schema import PurchaseItemCreate
from app.domains.purchase.service import PurchaseService
from app.domains.purchase.supplier_order_providers import FakeSupplierOrderProvider
from app.domains.purchase.supplier_order_providers import FakeSupplierOrderScenario
from app.domains.purchase.supplier_order_providers import SupplierOrderRequest
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.source.model import CompanySupplierRelation
from app.domains.source.model import SupplierProductLink
from app.domains.source.router import SourcingViewGuard
from app.domains.source.router import _redact_relation_for_viewer
from app.domains.source.router import list_out_of_stock_order_items
from app.domains.source.router import list_sourcing_product_candidates
from app.domains.source.schema import CompanySupplierRelationCreate
from app.domains.source.schema import SupplierProductLinkCreate
from app.domains.source.service import SourceService
from app.domains.supplier.model import Supplier
from app.domains.user.model import User
from app.core.exceptions import NotFoundException

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class SourcingViewGuardTestCase(unittest.TestCase):
    """SUPPLIER_VIEW 재사용 Guard의 실제 통과/차단 동작."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, Permission.__table__,
                RolePermission.__table__, User.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company = Company(name="테스트 회사", active=True)
        self.db.add(self.company)
        self.db.commit()

        self.admin_role = Role(name="Admin", code="ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.admin_role, self.viewer_role])
        self.db.commit()

        self.supplier_view_permission = Permission(
            name="공급처 조회", code="SUPPLIER_VIEW",
        )
        self.db.add(self.supplier_view_permission)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(self, role_id, username):

        user = User(
            username=username, email=f"{username}@test.com",
            password_hash="x", role_id=role_id,
            company_id=self.company.id, is_active=True,
        )
        self.db.add(user)
        self.db.commit()

        return user

    def test_admin_passes_without_permission_grant(self):

        admin = self._create_user(self.admin_role.id, "admin1")

        result = SourcingViewGuard(current_user=admin, db=self.db)
        self.assertIs(result, admin)

    def test_viewer_without_grant_is_blocked(self):

        viewer = self._create_user(self.viewer_role.id, "viewer1")

        with self.assertRaises(HTTPException) as ctx:
            SourcingViewGuard(current_user=viewer, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_viewer_with_supplier_view_grant_passes(self):

        viewer = self._create_user(self.viewer_role.id, "viewer2")
        self.db.add(RolePermission(
            role_id=self.viewer_role.id,
            permission_id=self.supplier_view_permission.id,
        ))
        self.db.commit()

        result = SourcingViewGuard(current_user=viewer, db=self.db)
        self.assertIs(result, viewer)


class RelationRedactionTestCase(unittest.TestCase):
    """_redact_relation_for_viewer() — DB 없이 순수 함수만 검증한다."""

    def _make_relation(self):

        return SimpleNamespace(
            id=1, company_id=1, supplier_id=1, approval_status="APPROVED",
            created_by=1, created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            contact_name="김담당", contact_phone="010-1234-5678",
            contact_email="contact@supplier.test",
            payment_terms="NET30", credential_reference="vault://ref/1",
            notes="비공개 메모",
        )

    def test_privileged_sees_all_fields(self):

        relation = self._make_relation()
        result = _redact_relation_for_viewer(relation, is_privileged=True)

        self.assertEqual(result["contact_name"], "김담당")
        self.assertEqual(result["payment_terms"], "NET30")
        self.assertEqual(result["credential_reference"], "vault://ref/1")
        self.assertEqual(result["notes"], "비공개 메모")

    def test_non_privileged_sees_redacted_fields(self):

        relation = self._make_relation()
        result = _redact_relation_for_viewer(relation, is_privileged=False)

        self.assertIsNone(result["contact_name"])
        self.assertIsNone(result["contact_phone"])
        self.assertIsNone(result["contact_email"])
        self.assertIsNone(result["payment_terms"])
        self.assertIsNone(result["credential_reference"])
        self.assertIsNone(result["notes"])

        # 비-계약 필드는 그대로 노출되어야 한다(승인 여부는 계약
        # 정보가 아니라 워크플로 상태다).
        self.assertEqual(result["approval_status"], "APPROVED")
        self.assertEqual(result["supplier_id"], 1)


class OutOfStockOrderItemsIsolationTestCase(unittest.TestCase):
    """list_out_of_stock_order_items() — 회사 A/B 격리."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, User.__table__,
                Order.__table__, OrderItem.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(name="회사 A", active=True)
        self.company_b = Company(name="회사 B", active=True)
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.admin_role = Role(name="Admin", code="ADMIN")
        self.db.add(self.admin_role)
        self.db.commit()

        self.user_a = User(
            username="usera", email="usera@test.com", password_hash="x",
            role_id=self.admin_role.id, company_id=self.company_a.id,
            is_active=True,
        )
        self.user_b = User(
            username="userb", email="userb@test.com", password_hash="x",
            role_id=self.admin_role.id, company_id=self.company_b.id,
            is_active=True,
        )
        self.db.add_all([self.user_a, self.user_b])
        self.db.commit()

        self._seed_order_item(self.company_a.id, "A-OOS", OrderItemStatus.OUT_OF_STOCK)
        self._seed_order_item(self.company_a.id, "A-PENDING", OrderItemStatus.PENDING)
        self._seed_order_item(self.company_b.id, "B-OOS", OrderItemStatus.OUT_OF_STOCK)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_order_item(self, company_id, label, status):

        order = Order(
            company_id=company_id, channel_code="FAKE",
            channel_order_id=f"ORDER-{label}", order_number=f"O-{label}",
            buyer_name="구매자", receiver_name="수취인",
            receiver_phone="010-0000-0000", receiver_address="서울시",
            receiver_zipcode="00000", ordered_at=datetime.utcnow(),
        )
        self.db.add(order)
        self.db.commit()

        item = OrderItem(
            company_id=company_id, order_id=order.id, inventory_sku_id=1,
            channel_sku=f"CH-{label}", sku_code_snapshot=f"SKU-{label}",
            product_name_snapshot=f"상품 {label}", quantity=1,
            unit_price=1000, status=status,
        )
        self.db.add(item)
        self.db.commit()

    def test_returns_only_own_company_out_of_stock_items(self):

        result = list_out_of_stock_order_items(
            current_user=self.user_a, db=self.db,
        )

        labels = {r.product_name_snapshot for r in result}
        self.assertEqual(labels, {"상품 A-OOS"})

    def test_other_company_out_of_stock_item_is_not_visible(self):

        result = list_out_of_stock_order_items(
            current_user=self.user_b, db=self.db,
        )

        labels = {r.product_name_snapshot for r in result}
        self.assertEqual(labels, {"상품 B-OOS"})
        self.assertNotIn("상품 A-OOS", labels)


class PurchaseRetrySubmissionTestCase(unittest.TestCase):
    """PurchaseService.retry_submission_to_supplier() — FAILED 전용
    재시도, Provider 전환 재시도, 회사 격리, 저장소 레벨 원자적
    claim."""

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
                MarketplaceListing.__table__,
                InventorySku.__table__,
                InventoryReservation.__table__,
                InventoryLedgerEvent.__table__,
                InventoryChannelMapping.__table__,
                FundingAccount.__table__,
                FundingHold.__table__,
                SupplierPayment.__table__,
                FundingLedger.__table__,
                Order.__table__,
                OrderItem.__table__,
                OrderIngestionEvent.__table__,
                OrderStatusEvent.__table__,
                Purchase.__table__,
                PurchaseItem.__table__,
                Supplier.__table__,
                SupplierProductLink.__table__,
                CompanySupplierRelation.__table__,
                EmergencyStop.__table__,
            ],
        )

        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.order_service = OrderService(self.db)
        self.purchase_service = PurchaseService(self.db)
        self.purchase_repository = PurchaseRepository(self.db)
        self.inventory_service = InventoryService(self.db)
        self.funding_service = FundingService(self.db)
        self.source_service = SourceService(self.db)

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")

        self.channel = MarketplaceChannel(
            code="FAKE", name="가짜채널", doc_verification_status="VERIFIED",
        )
        self.db.add(self.channel)
        self.db.commit()

        self.account = MarketplaceAccount(
            company_id=self.company_a.id, channel_id=self.channel.id,
            account_code="acc1", account_name="계정1",
        )
        self.db.add(self.account)
        self.db.commit()

        self.funding_service.create_account(
            FundingAccountCreate(total_funding=100_000.0), self.company_a.id,
        )

        self.supplier = Supplier(name="테스트 공급처", is_active=True)
        self.db.add(self.supplier)
        self.db.commit()

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

    def _approve_relation(self):

        relation = self.source_service.create_relation(
            CompanySupplierRelationCreate(supplier_id=self.supplier.id),
            self.company_a.id, created_by=1,
        )
        self.source_service.set_relation_approval(
            relation.id, self.company_a.id, approve=True,
        )

    def _make_confirmed_purchase(self, unit_cost=3000.0, key_suffix="retry"):

        candidate = ProductCandidate(
            candidate_key=f"{key_suffix}:1", source_type="TREND",
            source_reference=key_suffix, market="FAKE",
            product_name="재시도 테스트 상품", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        sku = self.inventory_service.create_sku(
            InventorySkuCreate(
                product_candidate_id=candidate.id,
                sku_code=f"SKU-{key_suffix}", option_label=f"SKU-{key_suffix}",
                initial_qty=0, safety_stock=0,
            ),
            self.company_a.id,
        )

        listing = MarketplaceListing(
            company_id=self.company_a.id,
            product_candidate_id=candidate.id,
            marketplace_account_id=self.account.id, status="DRAFT",
        )
        self.db.add(listing)
        self.db.commit()

        self.inventory_service.create_channel_mapping(
            sku.id, self.company_a.id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="FAKE", channel_sku=f"CH-{key_suffix}",
            ),
        )

        collect_result = self.order_service.collect_channel_order(
            self.company_a.id,
            OrderChannelCollectRequest(
                channel_code="FAKE", channel_order_id=f"ORDER-{key_suffix}",
                buyer_name="김구매", receiver_name="김수취",
                receiver_phone="010-0000-0000",
                receiver_address="서울시", receiver_zipcode="00000",
                ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku=f"CH-{key_suffix}", quantity=2,
                        unit_price=9000, product_name="재시도 테스트 상품",
                    ),
                ],
            ),
            triggered_by=1,
        )
        item = collect_result["items"][0]

        self.source_service.create_link(
            SupplierProductLinkCreate(
                product_candidate_id=candidate.id,
                supplier_id=self.supplier.id,
                supplier_sku=f"SUP-{key_suffix}",
                unit_cost=unit_cost, moq=1,
            ),
            self.company_a.id, created_by=1,
        )

        purchase = self.purchase_service.create_purchase(
            self.company_a.id,
            PurchaseCreate(
                order_id=collect_result["order"].id,
                supplier_id=self.supplier.id,
                items=[
                    PurchaseItemCreate(
                        order_item_id=item.id, unit_cost=unit_cost,
                    ),
                ],
                idempotency_key=f"retry-test-{key_suffix}",
            ),
            triggered_by=1,
        )
        self.purchase_service.confirm_purchase(
            purchase.id, self.company_a.id, triggered_by=1,
        )

        return self.purchase_service.get_purchase(purchase.id, self.company_a.id)

    def test_retry_blocked_when_not_yet_submitted(self):

        purchase = self._make_confirmed_purchase(key_suffix="not-failed")
        self._approve_relation()

        with self.assertRaises(BadRequestException):
            self.purchase_service.retry_submission_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )

    def test_retry_blocked_after_successful_submission(self):
        """이미 SUBMITTED(공급처가 응답 완료)된 발주는 재시도 대상이
        아니다 — 중복 발주 방지의 핵심 주장."""

        purchase = self._make_confirmed_purchase(key_suffix="already-submitted")
        self._approve_relation()

        self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )

        with self.assertRaises(BadRequestException):
            self.purchase_service.retry_submission_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )

    def test_retry_succeeds_after_failure_with_different_provider(self):
        """MANUAL Provider는 create_order()가 항상 예외를 던지므로
        FAILED 상태를 결정론적으로 재현할 수 있다 — 그 뒤 FAKE로
        재시도하면(Provider 전환 재시도) 성공해야 한다."""

        purchase = self._make_confirmed_purchase(key_suffix="failed-then-retry")
        self._approve_relation()

        failed = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "MANUAL", triggered_by=1,
        )
        self.assertEqual(
            failed.submission_status, PurchaseSubmissionStatus.FAILED,
        )

        retried = self.purchase_service.retry_submission_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )

        self.assertEqual(
            retried.submission_status, PurchaseSubmissionStatus.SUBMITTED,
        )
        self.assertEqual(retried.submission_provider_code, "FAKE")
        self.assertIsNotNone(retried.supplier_order_id)

    def test_retry_reblocks_on_price_change_after_failure(self):
        """재시도도 최초 전송과 동일한 검사를 전부 다시 통과해야
        한다 — 실패 이후 가격이 바뀌었으면 재시도도 차단된다."""

        purchase = self._make_confirmed_purchase(
            unit_cost=3000.0, key_suffix="price-changed-retry",
        )
        self._approve_relation()

        self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "MANUAL", triggered_by=1,
        )

        link = self.db.query(SupplierProductLink).filter(
            SupplierProductLink.company_id == self.company_a.id,
        ).first()
        link.unit_cost = 9999.0
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.purchase_service.retry_submission_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )

    def test_retry_blocks_cross_company(self):

        purchase = self._make_confirmed_purchase(key_suffix="cross-company")
        self._approve_relation()

        self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "MANUAL", triggered_by=1,
        )

        with self.assertRaises(Exception):
            self.purchase_service.retry_submission_to_supplier(
                purchase.id, self.company_b.id, "FAKE", triggered_by=1,
            )

    def test_reclaim_failed_submission_is_atomic_single_claim(self):
        """reclaim_failed_submission_conditional()을 저장소 레벨에서
        직접 검증한다 — 동시에 두 요청이 같은 FAILED 발주를 재시도
        하려 하면 정확히 하나만 rowcount=1로 성공하고(UPDATE...WHERE
        submission_status='FAILED'가 즉시 PENDING으로 바꿔버리므로),
        나머지(같은 조건으로 재시도)는 rowcount=0으로 실패해야
        한다."""

        purchase = self._make_confirmed_purchase(key_suffix="atomic-reclaim")
        self._approve_relation()

        self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "MANUAL", triggered_by=1,
        )
        current = self.purchase_service.get_purchase(
            purchase.id, self.company_a.id,
        )
        self.assertEqual(
            current.submission_status, PurchaseSubmissionStatus.FAILED,
        )

        first = self.purchase_repository.reclaim_failed_submission_conditional(
            purchase.id, self.company_a.id, "fingerprint-1",
        )
        self.db.commit()
        second = self.purchase_repository.reclaim_failed_submission_conditional(
            purchase.id, self.company_a.id, "fingerprint-2",
        )
        self.db.commit()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_retry_blocked_when_failure_is_non_retryable(self):
        """2026-08-21 5차 지시(작업 3) — FAKE의 NON_RETRYABLE_FAILURE
        시나리오로 만든 FAILED 발주는 재시도 자체가 서버에서 차단돼야
        한다(클라이언트 버튼 비활성만으로는 API 직접 호출을 막지
        못하므로 서버가 두 번째 방어선이다)."""

        purchase = self._make_confirmed_purchase(key_suffix="non-retryable")
        self._approve_relation()

        failed = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            test_scenario=FakeSupplierOrderScenario.NON_RETRYABLE_FAILURE,
        )
        self.assertEqual(
            failed.submission_status, PurchaseSubmissionStatus.FAILED,
        )
        self.assertFalse(failed.submission_retryable)

        with self.assertRaises(BadRequestException) as ctx:
            self.purchase_service.retry_submission_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )
        self.assertIn("NOT_RETRYABLE", str(ctx.exception))

    def test_retry_blocked_before_retry_after_elapses(self):
        """RETRY_AFTER 시나리오로 만든 FAILED 발주는 대기 시간이
        지나기 전에는 재시도가 차단돼야 한다."""

        purchase = self._make_confirmed_purchase(key_suffix="retry-after-wait")
        self._approve_relation()

        failed = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            test_scenario=FakeSupplierOrderScenario.RETRY_AFTER,
        )
        self.assertEqual(
            failed.submission_status, PurchaseSubmissionStatus.FAILED,
        )
        self.assertEqual(
            failed.submission_retry_after_seconds,
            FakeSupplierOrderScenario.RETRY_AFTER_SECONDS,
        )

        with self.assertRaises(BadRequestException) as ctx:
            self.purchase_service.retry_submission_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )
        self.assertIn("RETRY_AFTER_NOT_ELAPSED", str(ctx.exception))

    def test_retry_succeeds_after_retry_after_elapses(self):
        """대기 시간이 실제로 지난 뒤에는(submitted_at을 과거로 되돌려
        재현) 재시도가 정상적으로 성공해야 한다."""

        purchase = self._make_confirmed_purchase(key_suffix="retry-after-elapsed")
        self._approve_relation()

        self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            test_scenario=FakeSupplierOrderScenario.RETRY_AFTER,
        )

        row = self.db.query(Purchase).filter(Purchase.id == purchase.id).first()
        row.submitted_at = datetime.utcnow() - timedelta(
            seconds=FakeSupplierOrderScenario.RETRY_AFTER_SECONDS + 1,
        )
        self.db.commit()

        retried = self.purchase_service.retry_submission_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )
        self.assertEqual(
            retried.submission_status, PurchaseSubmissionStatus.SUBMITTED,
        )
        self.assertEqual(retried.supplier_order_id, f"FAKE-SO-purchase-{purchase.id}")

    def test_partial_scenario_records_accepted_and_rejected_quantities(self):

        purchase = self._make_confirmed_purchase(key_suffix="partial-scenario")
        self._approve_relation()

        result = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            test_scenario=FakeSupplierOrderScenario.PARTIAL,
        )
        self.assertEqual(
            result.submission_status,
            PurchaseSubmissionStatus.PARTIALLY_ACCEPTED,
        )
        self.assertIsNotNone(result.accepted_quantities_json)
        self.assertIsNotNone(result.rejected_quantities_json)

    def test_default_full_accept_scenario_is_unaffected_by_new_field(self):
        """test_scenario를 아예 넘기지 않으면(기존 호출부와 동일)
        기존 전량 접수 동작이 정확히 그대로여야 한다 — 프로덕션 기본
        동작을 절대 바꾸지 않는다는 요구사항의 회귀 방지."""

        purchase = self._make_confirmed_purchase(key_suffix="default-unaffected")
        self._approve_relation()

        result = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )
        self.assertEqual(
            result.submission_status, PurchaseSubmissionStatus.SUBMITTED,
        )
        self.assertTrue(result.supplier_order_id.startswith("FAKE-SO-"))

    def _audit_log_action_count_on_disk(self, action: str) -> int:
        """write_audit_log()가 실제로 디스크에 commit됐는지(같은 세션
        안에서만 보이는 미확정 flush가 아닌지) 검증하기 위해, 테스트가
        쓰고 있는 self.db와는 별개의 새 커넥션으로 같은 파일을 읽는다
        — write_audit_log()를 commit() 이전에 호출해야 한다는 계약을
        실제로 어기면(job_queue_service.py에서 이미 한 번 있었던 결함
        패턴), 이 새 커넥션에는 감사로그 행이 보이지 않아야 한다."""

        fresh_conn = self.engine.connect()
        try:
            row = fresh_conn.exec_driver_sql(
                "SELECT COUNT(*) FROM audit_logs WHERE action = ?",
                (action,),
            ).fetchone()
            return row[0]
        finally:
            fresh_conn.close()

    def test_submit_audit_log_is_durably_committed(self):

        purchase = self._make_confirmed_purchase(key_suffix="audit-submit")
        self._approve_relation()

        self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )

        self.assertEqual(
            self._audit_log_action_count_on_disk(
                "PURCHASE_SUBMITTED_TO_SUPPLIER",
            ),
            1,
        )

    def test_submit_failure_audit_log_is_durably_committed(self):

        purchase = self._make_confirmed_purchase(key_suffix="audit-submit-fail")
        self._approve_relation()

        self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "MANUAL", triggered_by=1,
        )

        self.assertEqual(
            self._audit_log_action_count_on_disk(
                "PURCHASE_SUBMITTED_TO_SUPPLIER_FAILED",
            ),
            1,
        )

    def test_retry_audit_log_is_durably_committed(self):

        purchase = self._make_confirmed_purchase(key_suffix="audit-retry")
        self._approve_relation()

        self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "MANUAL", triggered_by=1,
        )
        self.purchase_service.retry_submission_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )

        # submit(실패) 1건 + retry(성공) 1건 — 재시도도 동일한
        # audit_action_prefix를 쓰므로 총 2건이 실제로 디스크에
        # 남아야 한다.
        self.assertEqual(
            self._audit_log_action_count_on_disk(
                "PURCHASE_SUBMITTED_TO_SUPPLIER",
            ),
            1,
        )
        self.assertEqual(
            self._audit_log_action_count_on_disk(
                "PURCHASE_SUBMITTED_TO_SUPPLIER_FAILED",
            ),
            1,
        )


class FakeSupplierOrderProviderScenarioTestCase(unittest.TestCase):
    """2026-08-21 5차 지시(작업 3) — FakeSupplierOrderProvider의
    결정적 테스트 시나리오. DB가 전혀 필요 없는 순수 단위 테스트다."""

    def _make_request(self, test_scenario=None):

        return SupplierOrderRequest(
            purchase_id=1, supplier_id=1, idempotency_key="scenario-test",
            items=(("SUP-SKU-1", 4, 1000.0),),
            test_scenario=test_scenario,
        )

    def test_default_scenario_is_full_accept(self):

        provider = FakeSupplierOrderProvider()
        result = provider.create_order(self._make_request())

        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(result.accepted_quantities, {"SUP-SKU-1": 4})
        self.assertEqual(result.rejected_quantities, {})
        self.assertEqual(result.confirmed_price, 4000.0)
        self.assertIsNotNone(result.supplier_order_id)

    def test_explicit_full_accept_scenario_matches_default(self):

        provider = FakeSupplierOrderProvider()
        result = provider.create_order(
            self._make_request(FakeSupplierOrderScenario.FULL_ACCEPT),
        )

        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(result.accepted_quantities, {"SUP-SKU-1": 4})

    def test_partial_scenario_splits_quantities_deterministically(self):

        provider = FakeSupplierOrderProvider()
        result = provider.create_order(
            self._make_request(FakeSupplierOrderScenario.PARTIAL),
        )

        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(result.accepted_quantities, {"SUP-SKU-1": 2})
        self.assertEqual(result.rejected_quantities, {"SUP-SKU-1": 2})
        self.assertEqual(result.confirmed_price, 2000.0)

    def test_retryable_failure_scenario(self):

        provider = FakeSupplierOrderProvider()
        result = provider.create_order(
            self._make_request(FakeSupplierOrderScenario.RETRYABLE_FAILURE),
        )

        self.assertEqual(result.status, "FAILED")
        self.assertTrue(result.retryable)
        self.assertIsNone(result.retry_after_seconds)
        self.assertIsNone(result.supplier_order_id)

    def test_retry_after_scenario(self):

        provider = FakeSupplierOrderProvider()
        result = provider.create_order(
            self._make_request(FakeSupplierOrderScenario.RETRY_AFTER),
        )

        self.assertEqual(result.status, "FAILED")
        self.assertTrue(result.retryable)
        self.assertEqual(
            result.retry_after_seconds,
            FakeSupplierOrderScenario.RETRY_AFTER_SECONDS,
        )

    def test_non_retryable_failure_scenario(self):

        provider = FakeSupplierOrderProvider()
        result = provider.create_order(
            self._make_request(FakeSupplierOrderScenario.NON_RETRYABLE_FAILURE),
        )

        self.assertEqual(result.status, "FAILED")
        self.assertFalse(result.retryable)
        self.assertIsNone(result.retry_after_seconds)


class SourceCreateLinkRequiresApprovalTestCase(unittest.TestCase):
    """2026-08-21 5차 지시(작업 1) — "승인된 후보만 공급처 상품 연결
    대상으로 선택할 수 있게 한다" 요구사항의 실제 서비스 레벨 강제."""

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
                Supplier.__table__,
                SupplierProductLink.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = SourceService(self.db)

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111", ceo="t",
            phone="t", email="a@test.com", address="t",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222", ceo="t",
            phone="t", email="b@test.com", address="t",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.supplier = Supplier(name="테스트 공급처", is_active=True)
        self.db.add(self.supplier)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_candidate(self, owner_company_id, key):

        candidate = ProductCandidate(
            candidate_key=key, source_type="MANUAL", market="COUPANG",
            source_reference=key, product_name="테스트 상품",
            status=CandidateStatus.ANALYZED, visibility="PRIVATE",
            owner_company_id=owner_company_id,
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    def _make_link_data(self, candidate_id):

        return SupplierProductLinkCreate(
            product_candidate_id=candidate_id, supplier_id=self.supplier.id,
            supplier_sku="SUP-SKU", unit_cost=1000.0, moq=1,
        )

    def test_create_link_blocked_when_not_yet_decided(self):
        """이 회사가 아직 결정(승인/보류/거절)한 적이 없는 후보는
        ANALYZED로 남아 있으므로 연결 생성이 차단된다."""

        candidate = self._seed_candidate(self.company_a.id, "not-decided")

        with self.assertRaises(BadRequestException):
            self.service.create_link(
                self._make_link_data(candidate.id), self.company_a.id,
                created_by=1,
            )

    def test_create_link_blocked_when_held(self):

        candidate = self._seed_candidate(self.company_a.id, "held")
        self.db.add(ProductCandidateSelection(
            candidate_id=candidate.id, company_id=self.company_a.id,
            status="HELD",
        ))
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.create_link(
                self._make_link_data(candidate.id), self.company_a.id,
                created_by=1,
            )

    def test_create_link_succeeds_when_approved(self):

        candidate = self._seed_candidate(self.company_a.id, "approved")
        self.db.add(ProductCandidateSelection(
            candidate_id=candidate.id, company_id=self.company_a.id,
            status=CandidateStatus.APPROVED,
        ))
        self.db.commit()

        link = self.service.create_link(
            self._make_link_data(candidate.id), self.company_a.id,
            created_by=1,
        )
        self.assertEqual(link.product_candidate_id, candidate.id)

    def test_create_link_blocked_for_other_company_private_candidate(self):
        """회사 B의 PRIVATE 후보는 회사 A가 승인 여부와 무관하게 아예
        보이지 않는다(존재 자체가 404로 숨는다)."""

        candidate = self._seed_candidate(self.company_b.id, "other-company")
        self.db.add(ProductCandidateSelection(
            candidate_id=candidate.id, company_id=self.company_b.id,
            status=CandidateStatus.APPROVED,
        ))
        self.db.commit()

        with self.assertRaises(NotFoundException):
            self.service.create_link(
                self._make_link_data(candidate.id), self.company_a.id,
                created_by=1,
            )


class SourcingProductCandidatesEndpointTestCase(unittest.TestCase):
    """2026-08-21 5차 지시(작업 1) — GET /sourcing/product-candidates
    (list_sourcing_product_candidates) 최소 조회 엔드포인트."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, User.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111", ceo="t",
            phone="t", email="a@test.com", address="t",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222", ceo="t",
            phone="t", email="b@test.com", address="t",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.admin_role = Role(name="Admin", code="ADMIN")
        self.db.add(self.admin_role)
        self.db.commit()

        self.user_a = User(
            username="usera", email="usera@test.com", password_hash="x",
            role_id=self.admin_role.id, company_id=self.company_a.id,
            is_active=True,
        )
        self.db.add(self.user_a)
        self.db.commit()

        # GLOBAL, 아직 미결정 — company_a에서 보이지만 승인은 아님.
        self.candidate_global = ProductCandidate(
            candidate_key="global:1", source_type="TREND", market="FAKE",
            source_reference="global-1", product_name="전역 후보",
            status=CandidateStatus.ANALYZED, visibility="GLOBAL",
            brand_hint="브랜드A", category_hint="카테고리A",
        )
        # PRIVATE, company_a 소유 + APPROVED.
        self.candidate_a_approved = ProductCandidate(
            candidate_key="a:1", source_type="MANUAL", market="COUPANG",
            source_reference="a-1", product_name="회사A 승인 후보",
            status=CandidateStatus.ANALYZED, visibility="PRIVATE",
            owner_company_id=self.company_a.id,
        )
        # PRIVATE, company_b 소유 — company_a에는 절대 보이면 안 됨.
        self.candidate_b_private = ProductCandidate(
            candidate_key="b:1", source_type="MANUAL", market="COUPANG",
            source_reference="b-1", product_name="회사B 전용 후보",
            status=CandidateStatus.ANALYZED, visibility="PRIVATE",
            owner_company_id=self.company_b.id,
        )
        self.db.add_all([
            self.candidate_global, self.candidate_a_approved,
            self.candidate_b_private,
        ])
        self.db.commit()

        self.db.add(ProductCandidateSelection(
            candidate_id=self.candidate_a_approved.id,
            company_id=self.company_a.id, status=CandidateStatus.APPROVED,
        ))
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_returns_only_company_visible_candidates(self):

        results = list_sourcing_product_candidates(
            current_user=self.user_a, db=self.db,
        )

        names = {r.product_name for r in results}
        self.assertIn("전역 후보", names)
        self.assertIn("회사A 승인 후보", names)
        self.assertNotIn("회사B 전용 후보", names)

    def test_response_reflects_effective_approval_status(self):

        results = list_sourcing_product_candidates(
            current_user=self.user_a, db=self.db,
        )
        by_name = {r.product_name: r for r in results}

        self.assertFalse(by_name["전역 후보"].is_approved)
        self.assertEqual(by_name["전역 후보"].status, CandidateStatus.ANALYZED)
        self.assertTrue(by_name["회사A 승인 후보"].is_approved)
        self.assertEqual(
            by_name["회사A 승인 후보"].status, CandidateStatus.APPROVED,
        )

    def test_approved_only_filter(self):

        results = list_sourcing_product_candidates(
            approved_only=True, current_user=self.user_a, db=self.db,
        )

        names = {r.product_name for r in results}
        self.assertEqual(names, {"회사A 승인 후보"})

    def test_minimal_fields_only(self):

        results = list_sourcing_product_candidates(
            current_user=self.user_a, db=self.db,
        )
        global_result = next(
            r for r in results if r.product_name == "전역 후보"
        )

        self.assertEqual(global_result.brand, "브랜드A")
        self.assertEqual(global_result.category, "카테고리A")
        # 원가·마진·내부 점수 등은 이 응답 모델 자체에 필드가 없다
        # (Pydantic 모델 정의로 이미 보장 — 여기서는 그 계약을
        # 명시적으로 재확인한다).
        response_fields = set(type(global_result).model_fields.keys())
        self.assertEqual(
            response_fields,
            {"id", "product_name", "brand", "category", "status", "is_approved"},
        )
        for forbidden in (
            "margin_score", "trend_score", "risk_score", "confidence",
            "owner_company_id", "unit_cost", "cost",
        ):
            self.assertNotIn(forbidden, response_fields)


class CompanySupplierRelationCredentialBoundaryTestCase(unittest.TestCase):
    """
    Audit(2026-08-21, CTO 후속 지시) — "company_supplier_relations에는
    Credential 원문이 아니라 credential_reference만 허용"을 스키마
    형태로 명시적으로 재확인한다(이미 model.py/schema.py가 이렇게
    설계돼 있었다 — 이 테스트는 향후 회귀를 막기 위한 고정 장치다).
    """

    def test_create_request_schema_has_no_raw_credential_field(self):

        from app.domains.source.schema import CompanySupplierRelationCreate

        field_names = set(CompanySupplierRelationCreate.model_fields.keys())
        self.assertIn("credential_reference", field_names)
        for forbidden in ("api_key", "api_secret", "password", "credential"):
            self.assertNotIn(forbidden, field_names)

    def test_response_schema_has_no_raw_credential_field(self):

        from app.domains.source.schema import CompanySupplierRelationResponse

        field_names = set(CompanySupplierRelationResponse.model_fields.keys())
        self.assertIn("credential_reference", field_names)
        for forbidden in ("api_key", "api_secret", "password", "credential"):
            self.assertNotIn(forbidden, field_names)


class SupplierSearchCapabilityGateTestCase(unittest.TestCase):
    """
    Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry 실연결
    증거(SUPPLIER_RECOMMENDATION). `POST /sourcing/search`(router.py
    ::search_suppliers())가 실제 Provider를 호출하는 유일한 진입점.
    """

    def test_search_suppliers_blocks_when_capability_deactivated(self):

        from app.domains.ai_governance.service import InactiveCapabilityError
        from app.domains.source.router import search_suppliers
        from app.domains.source.schema import SupplierDiscoverySearchRequest
        from tests.ai_governance_test_helpers import deactivated_capability

        data = SupplierDiscoverySearchRequest(
            provider_code="FAKE", product_name="무선 이어폰",
        )

        with deactivated_capability("SUPPLIER_RECOMMENDATION"):
            with self.assertRaises(InactiveCapabilityError):
                search_suppliers(data, current_user=None)

    def test_search_suppliers_succeeds_when_capability_active(self):

        from app.domains.source.router import search_suppliers
        from app.domains.source.schema import SupplierDiscoverySearchRequest

        data = SupplierDiscoverySearchRequest(
            provider_code="FAKE", product_name="무선 이어폰",
        )

        results = search_suppliers(data, current_user=None)
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main()
