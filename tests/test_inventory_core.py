"""
=========================================================
Homez OS

File : tests/test_inventory_core.py

V7 Gate 3(2026-08-15) — Inventory 핵심(SKU/원장/예약/채널매핑) 기능
검증. 임시 SQLite 파일 DB만 사용한다 — 실제 homez.db는 이 테스트
전체에서 전혀 접근하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.inventory.constants import InventoryLedgerEventType
from app.domains.inventory.constants import InventoryReservationStatus
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku
from app.domains.inventory.schema import InventoryAdjustRequest
from app.domains.inventory.schema import InventoryChannelMappingCreate
from app.domains.inventory.schema import InventoryReserveRequest
from app.domains.inventory.schema import InventoryRestockRequest
from app.domains.inventory.schema import InventorySkuCreate
from app.domains.inventory.service import InventoryService
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)

# app/domains/inventory/model.py는 pre-pivot 호환용 레거시 `Inventory`
# 클래스를 그대로 보존한다(파일 상단 주석 참고 — app/domains/product/
# service.py·policy.py가 여전히 이를 직접 import한다). 그 클래스가
# `relationship("Product", ...)`/`relationship("Supplier", ...)`
# 문자열 참조를 갖고 있어, 이 Base 공유 레지스트리에서 SQLAlchemy가
# 아무 매핑 클래스나 처음 인스턴스화하는 순간 전체 레지스트리를
# configure하려 시도하며 Product/Category/Brand/Supplier도 함께
# 해석 가능해야 한다 — 이 테스트 파일이 실제로 쓰지 않아도 반드시
# import해 등록해야 한다(marketplace_listing 동시성 테스트가 이미 쓰는
# `# noqa: F401` 관례와 동일한 이유).
from app.domains.brand.model import Brand  # noqa: F401
from app.domains.category.model import Category  # noqa: F401
from app.domains.product.model import Product  # noqa: F401
from app.domains.supplier.model import Supplier  # noqa: F401

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class InventoryTestCaseBase(unittest.TestCase):

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
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )

        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="서울",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.candidate = ProductCandidate(
            candidate_key="test:COUPANG:INV-1", source_type="TREND",
            source_reference="INV-1", market="COUPANG",
            product_name="테스트 상품", status="APPROVED",
        )
        self.db.add(self.candidate)
        self.db.commit()

        self.service = InventoryService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_sku(self, company_id=None, initial_qty=10, sku_code="SKU-1"):

        return self.service.create_sku(
            InventorySkuCreate(
                product_candidate_id=self.candidate.id,
                sku_code=sku_code,
                option_label="기본",
                initial_qty=initial_qty,
                safety_stock=2,
            ),
            company_id or self.company_a.id,
        )


class SkuCreationTestCase(InventoryTestCaseBase):

    def test_create_sku_requires_approved_candidate(self):

        unapproved = ProductCandidate(
            candidate_key="test:COUPANG:INV-2", source_type="TREND",
            source_reference="INV-2", market="COUPANG",
            product_name="미승인 상품", status="DISCOVERED",
        )
        self.db.add(unapproved)
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.create_sku(
                InventorySkuCreate(
                    product_candidate_id=unapproved.id,
                    sku_code="SKU-X",
                ),
                self.company_a.id,
            )

    def test_create_sku_with_initial_qty_writes_restocked_ledger(self):

        sku = self._create_sku(initial_qty=10)

        self.assertEqual(sku.available_qty, 10)
        self.assertEqual(sku.reserved_qty, 0)

        ledger = self.service.list_ledger(sku.id, self.company_a.id)
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0].event_type, InventoryLedgerEventType.RESTOCKED)
        self.assertEqual(ledger[0].quantity_delta, 10)
        self.assertEqual(ledger[0].available_after, 10)

    def test_duplicate_sku_code_same_company_rejected(self):

        self._create_sku(sku_code="SKU-DUP")

        with self.assertRaises(BadRequestException):
            self._create_sku(sku_code="SKU-DUP")

    def test_duplicate_sku_code_different_company_allowed(self):

        self._create_sku(company_id=self.company_a.id, sku_code="SKU-SAME")
        sku_b = self._create_sku(company_id=self.company_b.id, sku_code="SKU-SAME")

        self.assertIsNotNone(sku_b.id)

    def test_company_isolation_get_sku(self):

        sku = self._create_sku(company_id=self.company_a.id)

        with self.assertRaises(NotFoundException):
            self.service.get_sku(sku.id, self.company_b.id)


class ReserveReleaseConsumeTestCase(InventoryTestCaseBase):

    def test_reserve_reduces_available_increases_reserved(self):

        sku = self._create_sku(initial_qty=10)

        reservation = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=4, idempotency_key="res-1"),
            triggered_by=1,
        )

        self.assertEqual(reservation.status, InventoryReservationStatus.RESERVED)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 6)
        self.assertEqual(updated.reserved_qty, 4)

        ledger = self.service.list_ledger(sku.id, self.company_a.id)
        reserved_events = [
            e for e in ledger if e.event_type == InventoryLedgerEventType.RESERVED
        ]
        self.assertEqual(len(reserved_events), 1)
        self.assertEqual(reserved_events[0].quantity_delta, -4)
        self.assertEqual(reserved_events[0].available_after, 6)
        self.assertEqual(reserved_events[0].reserved_after, 4)

    def test_reserve_still_works_when_pricing_inventory_capability_deactivated(self):
        """Audit(2026-08-21, AG-0) — 재고 예약은 사람이 직접 수행하는
        핵심 CRUD다(AI 추천 기능이 아니다). PRICING_INVENTORY AI
        Capability가 비활성이어도 정상 동작해야 한다(이전 라운드에서
        여기 게이트를 잘못 연결했던 것을 되돌린 회귀 방지 테스트)."""

        from tests.ai_governance_test_helpers import deactivated_capability

        sku = self._create_sku(initial_qty=10)

        with deactivated_capability("PRICING_INVENTORY"):
            reservation = self.service.reserve(
                sku.id, self.company_a.id,
                InventoryReserveRequest(
                    quantity=1, idempotency_key="res-manual-flow-unblocked",
                ),
                triggered_by=1,
            )

        self.assertEqual(reservation.status, InventoryReservationStatus.RESERVED)

    def test_reserve_insufficient_stock_fails_closed_no_partial_state(self):

        sku = self._create_sku(initial_qty=3)

        with self.assertRaises(BadRequestException):
            self.service.reserve(
                sku.id, self.company_a.id,
                InventoryReserveRequest(quantity=5, idempotency_key="res-fail"),
                triggered_by=1,
            )

        # 실패한 예약 시도가 어떤 상태도 바꾸지 않았어야 한다(자동 보정
        # 없이 완전 롤백, 요구사항 5).
        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 3)
        self.assertEqual(updated.reserved_qty, 0)

        self.assertEqual(
            len(self.service.list_reservations(sku.id, self.company_a.id)), 0,
        )
        self.assertEqual(len(self.service.list_ledger(sku.id, self.company_a.id)), 1)  # 초기입고 1건뿐

    def test_reserve_never_goes_negative(self):
        """가용재고가 0이 될 때까지 연속 예약해도 available_qty는 음수가 될 수 없다."""

        sku = self._create_sku(initial_qty=5)

        self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=5, idempotency_key="res-all"),
            triggered_by=1,
        )
        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 0)

        with self.assertRaises(BadRequestException):
            self.service.reserve(
                sku.id, self.company_a.id,
                InventoryReserveRequest(quantity=1, idempotency_key="res-overflow"),
                triggered_by=1,
            )

        updated_again = self.service.get_sku(sku.id, self.company_a.id)
        self.assertGreaterEqual(updated_again.available_qty, 0)

    def test_reserve_idempotent_replay_returns_same_reservation(self):

        sku = self._create_sku(initial_qty=10)

        first = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=3, idempotency_key="res-idem"),
            triggered_by=1,
        )
        second = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=3, idempotency_key="res-idem"),
            triggered_by=1,
        )

        self.assertEqual(first.id, second.id)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 7)  # 한 번만 차감됐어야 함
        self.assertEqual(updated.reserved_qty, 3)

    def test_release_restores_available(self):

        sku = self._create_sku(initial_qty=10)
        reservation = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=4, idempotency_key="res-2"),
            triggered_by=1,
        )

        released = self.service.release(
            reservation.id, self.company_a.id, triggered_by=1,
        )
        self.assertEqual(released.status, InventoryReservationStatus.RELEASED)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 10)
        self.assertEqual(updated.reserved_qty, 0)

    def test_double_release_blocked_state_machine(self):

        sku = self._create_sku(initial_qty=10)
        reservation = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=4, idempotency_key="res-3"),
            triggered_by=1,
        )
        self.service.release(reservation.id, self.company_a.id, triggered_by=1)

        with self.assertRaises(BadRequestException):
            self.service.release(reservation.id, self.company_a.id, triggered_by=1)

        # 재고 상태는 최초 해제 시점 그대로여야 한다(중복 해제로
        # available이 두 번 늘어나지 않음).
        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 10)

    def test_consume_reduces_reserved_only(self):

        sku = self._create_sku(initial_qty=10)
        reservation = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=4, idempotency_key="res-4"),
            triggered_by=1,
        )

        consumed = self.service.consume(
            reservation.id, self.company_a.id, triggered_by=1,
        )
        self.assertEqual(consumed.status, InventoryReservationStatus.CONSUMED)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        # available은 RESERVE 시점에 이미 차감됨(6) — CONSUME은 reserved만 감소.
        self.assertEqual(updated.available_qty, 6)
        self.assertEqual(updated.reserved_qty, 0)

    def test_consume_then_release_blocked(self):

        sku = self._create_sku(initial_qty=10)
        reservation = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=4, idempotency_key="res-5"),
            triggered_by=1,
        )
        self.service.consume(reservation.id, self.company_a.id, triggered_by=1)

        with self.assertRaises(BadRequestException):
            self.service.release(reservation.id, self.company_a.id, triggered_by=1)

    def test_double_consume_blocked(self):

        sku = self._create_sku(initial_qty=10)
        reservation = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=4, idempotency_key="res-6"),
            triggered_by=1,
        )
        self.service.consume(reservation.id, self.company_a.id, triggered_by=1)

        with self.assertRaises(BadRequestException):
            self.service.consume(reservation.id, self.company_a.id, triggered_by=1)


class RestockAdjustTestCase(InventoryTestCaseBase):

    def test_restock_increases_available(self):

        sku = self._create_sku(initial_qty=10)

        ledger = self.service.restock(
            sku.id, self.company_a.id,
            InventoryRestockRequest(quantity=5, idempotency_key="restock-1"),
            triggered_by=1,
        )
        self.assertEqual(ledger.event_type, InventoryLedgerEventType.RESTOCKED)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 15)

    def test_restock_idempotent_replay(self):

        sku = self._create_sku(initial_qty=10)

        self.service.restock(
            sku.id, self.company_a.id,
            InventoryRestockRequest(quantity=5, idempotency_key="restock-dup"),
            triggered_by=1,
        )
        self.service.restock(
            sku.id, self.company_a.id,
            InventoryRestockRequest(quantity=5, idempotency_key="restock-dup"),
            triggered_by=1,
        )

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 15)  # 한 번만 반영

    def test_adjust_requires_reason(self):

        sku = self._create_sku(initial_qty=10)

        with self.assertRaises(Exception):
            InventoryAdjustRequest(
                quantity_delta=-2, reason="", idempotency_key="adj-empty",
            )

    def test_adjust_blank_reason_rejected_at_service_level(self):

        sku = self._create_sku(initial_qty=10)

        with self.assertRaises(BadRequestException):
            self.service.adjust_stock(
                sku.id, self.company_a.id,
                InventoryAdjustRequest(
                    quantity_delta=-2, reason="   ",
                    idempotency_key="adj-blank",
                ),
                triggered_by=1,
            )

    def test_adjust_negative_delta_fail_closed_when_would_go_negative(self):

        sku = self._create_sku(initial_qty=3)

        with self.assertRaises(BadRequestException):
            self.service.adjust_stock(
                sku.id, self.company_a.id,
                InventoryAdjustRequest(
                    quantity_delta=-10, reason="파손 확인",
                    idempotency_key="adj-neg",
                ),
                triggered_by=1,
            )

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 3)  # 변경 없음

    def test_adjust_writes_audit_log(self):

        sku = self._create_sku(initial_qty=10)

        self.service.adjust_stock(
            sku.id, self.company_a.id,
            InventoryAdjustRequest(
                quantity_delta=-3, reason="실사 재고 차이",
                idempotency_key="adj-audit",
            ),
            triggered_by=42,
        )

        row = self.db.execute(
            text(
                "SELECT company_id, user_id, action, entity, entity_id, "
                "description FROM audit_logs WHERE action = "
                "'INVENTORY_ADJUSTED'",
            ),
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], self.company_a.id)
        self.assertEqual(row[1], 42)
        self.assertEqual(row[3], "inventory_sku")
        self.assertEqual(row[4], str(sku.id))
        self.assertIn("실사 재고 차이", row[5])

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 7)

    def test_adjust_idempotent_replay_does_not_double_apply(self):

        sku = self._create_sku(initial_qty=10)

        self.service.adjust_stock(
            sku.id, self.company_a.id,
            InventoryAdjustRequest(
                quantity_delta=-3, reason="실사 재고 차이",
                idempotency_key="adj-idem",
            ),
            triggered_by=1,
        )
        self.service.adjust_stock(
            sku.id, self.company_a.id,
            InventoryAdjustRequest(
                quantity_delta=-3, reason="실사 재고 차이",
                idempotency_key="adj-idem",
            ),
            triggered_by=1,
        )

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 7)  # 한 번만 반영

        count = self.db.execute(
            text(
                "SELECT COUNT(*) FROM audit_logs WHERE action = "
                "'INVENTORY_ADJUSTED'",
            ),
        ).fetchone()[0]
        self.assertEqual(count, 1)  # 재생 경로는 새 audit_log를 쓰지 않는다


class LedgerAppendOnlyTestCase(InventoryTestCaseBase):

    def test_ledger_never_shrinks_and_snapshots_are_consistent(self):

        sku = self._create_sku(initial_qty=10)

        reservation = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=4, idempotency_key="lg-1"),
            triggered_by=1,
        )
        self.service.release(reservation.id, self.company_a.id, triggered_by=1)
        self.service.restock(
            sku.id, self.company_a.id,
            InventoryRestockRequest(quantity=2, idempotency_key="lg-restock"),
            triggered_by=1,
        )

        ledger = self.service.list_ledger(sku.id, self.company_a.id)
        # 초기입고(1) + RESERVED(1) + RELEASED(1) + RESTOCKED(1) = 4
        self.assertEqual(len(ledger), 4)

        event_types = sorted(e.event_type for e in ledger)
        self.assertEqual(
            event_types,
            sorted([
                InventoryLedgerEventType.RESTOCKED,
                InventoryLedgerEventType.RESERVED,
                InventoryLedgerEventType.RELEASED,
                InventoryLedgerEventType.RESTOCKED,
            ]),
        )

        # 최신 스냅샷이 최종 재고 상태와 일치해야 한다.
        latest = max(ledger, key=lambda e: e.id)
        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(latest.available_after, updated.available_qty)
        self.assertEqual(latest.reserved_after, updated.reserved_qty)


class ChannelMappingTestCase(InventoryTestCaseBase):

    def _create_listing(self, company_id):

        channel = MarketplaceChannel(
            code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        account = MarketplaceAccount(
            company_id=company_id, channel_id=channel.id,
            account_code="acc1", account_name="계정1",
        )
        self.db.add(account)
        self.db.commit()

        listing = MarketplaceListing(
            company_id=company_id,
            product_candidate_id=self.candidate.id,
            marketplace_account_id=account.id,
            status="DRAFT",
        )
        self.db.add(listing)
        self.db.commit()

        return listing

    def test_create_mapping_and_sync_success(self):

        sku = self._create_sku(initial_qty=10)
        listing = self._create_listing(self.company_a.id)

        mapping = self.service.create_channel_mapping(
            sku.id, self.company_a.id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="COUPANG",
                channel_sku="CPSKU-OK",
            ),
        )
        self.assertEqual(mapping.last_sync_status, "PENDING")

        synced = self.service.sync_channel_stock(
            mapping.id, self.company_a.id, triggered_by=1,
        )
        self.assertEqual(synced.last_sync_status, "SYNCED")
        self.assertIsNotNone(synced.last_synced_at)

        ledger = self.service.list_ledger(sku.id, self.company_a.id)
        sync_events = [
            e for e in ledger
            if e.event_type == InventoryLedgerEventType.CHANNEL_SYNC
        ]
        self.assertEqual(len(sync_events), 1)
        self.assertEqual(sync_events[0].quantity_delta, 0)
        self.assertEqual(sync_events[0].channel_code, "COUPANG")

    def test_sync_fake_provider_failure_trigger(self):

        sku = self._create_sku(initial_qty=10)
        listing = self._create_listing(self.company_a.id)

        mapping = self.service.create_channel_mapping(
            sku.id, self.company_a.id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="COUPANG",
                channel_sku="TRIGGER_5XX",
            ),
        )

        synced = self.service.sync_channel_stock(
            mapping.id, self.company_a.id, triggered_by=1,
        )
        self.assertEqual(synced.last_sync_status, "FAILED")
        self.assertIsNotNone(synced.last_sync_error)

    def test_duplicate_mapping_same_sku_listing_rejected(self):

        sku = self._create_sku(initial_qty=10)
        listing = self._create_listing(self.company_a.id)

        self.service.create_channel_mapping(
            sku.id, self.company_a.id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="COUPANG",
                channel_sku="CPSKU-1",
            ),
        )

        with self.assertRaises(BadRequestException):
            self.service.create_channel_mapping(
                sku.id, self.company_a.id,
                InventoryChannelMappingCreate(
                    marketplace_listing_id=listing.id,
                    channel_code="COUPANG",
                    channel_sku="CPSKU-2",
                ),
            )

    def test_mapping_to_other_company_listing_rejected(self):

        sku = self._create_sku(company_id=self.company_a.id)
        listing_b = self._create_listing(self.company_b.id)

        with self.assertRaises(NotFoundException):
            self.service.create_channel_mapping(
                sku.id, self.company_a.id,
                InventoryChannelMappingCreate(
                    marketplace_listing_id=listing_b.id,
                    channel_code="COUPANG",
                    channel_sku="CPSKU-CROSS",
                ),
            )

    def test_sync_blocked_by_emergency_stop(self):

        sku = self._create_sku(initial_qty=10)
        listing = self._create_listing(self.company_a.id)

        mapping = self.service.create_channel_mapping(
            sku.id, self.company_a.id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="COUPANG",
                channel_sku="CPSKU-ESTOP",
            ),
        )

        SafetyService(self.db).activate_emergency_stop(
            reason="테스트 비상정지", set_by=1, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            self.service.sync_channel_stock(
                mapping.id, self.company_a.id, triggered_by=1,
            )


class ExternallyProcuredReservationTestCase(InventoryTestCaseBase):
    """
    2026-09-07 V7 통합 매입 감사 후속(HOMEZ_V7_PROCUREMENT_LEDGER_
    AUDIT_20260907.md §8, 사용자 확정) —
    `reserve_externally_procured()`("개별 조달용 가짜 예약")가 실제
    공용 재고(available_qty/reserved_qty)에 절대 영향을 주지 않으면서
    `release()`/`consume()`와 정상적으로 상호작용하는지 검증한다.
    """

    def _reserve_external(self, sku, *, quantity=1, key="ext-1", ref_id=999):

        from app.domains.inventory.constants import (
            InventoryReservationReferenceType,
        )

        reservation = self.service.reserve_externally_procured(
            sku.id, self.company_a.id,
            InventoryReserveRequest(
                quantity=quantity, idempotency_key=key, reference_id=ref_id,
            ),
            triggered_by=1,
        )
        self.assertEqual(
            reservation.reference_type,
            InventoryReservationReferenceType.PURCHASE_TASK_EXTERNAL_PROCUREMENT,
        )
        return reservation

    def test_reserve_externally_procured_does_not_touch_sku_quantities(self):

        sku = self._create_sku(initial_qty=10)

        reservation = self._reserve_external(sku, quantity=3)

        self.assertEqual(reservation.status, InventoryReservationStatus.RESERVED)
        self.assertEqual(reservation.reference_id, 999)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 10)
        self.assertEqual(updated.reserved_qty, 0)

    def test_reserve_externally_procured_ignores_caller_supplied_reference_type(self):

        from app.domains.inventory.constants import (
            InventoryReservationReferenceType,
        )

        sku = self._create_sku(initial_qty=10)

        reservation = self.service.reserve_externally_procured(
            sku.id, self.company_a.id,
            InventoryReserveRequest(
                quantity=1, idempotency_key="ext-override",
                reference_type="something_else", reference_id=1,
            ),
            triggered_by=1,
        )

        self.assertEqual(
            reservation.reference_type,
            InventoryReservationReferenceType.PURCHASE_TASK_EXTERNAL_PROCUREMENT,
        )

    def test_reserve_externally_procured_idempotent_replay(self):

        sku = self._create_sku(initial_qty=10)

        first = self._reserve_external(sku, quantity=2, key="ext-replay")
        second = self._reserve_external(sku, quantity=2, key="ext-replay")

        self.assertEqual(first.id, second.id)

    def test_consume_externally_procured_does_not_touch_sku_quantities(self):

        sku = self._create_sku(initial_qty=10)
        reservation = self._reserve_external(sku, quantity=3)

        consumed = self.service.consume(
            reservation.id, self.company_a.id, triggered_by=1,
        )

        self.assertEqual(consumed.status, InventoryReservationStatus.CONSUMED)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 10)
        self.assertEqual(updated.reserved_qty, 0)

    def test_release_externally_procured_does_not_touch_sku_quantities(self):

        sku = self._create_sku(initial_qty=10)
        reservation = self._reserve_external(sku, quantity=3)

        released = self.service.release(
            reservation.id, self.company_a.id, triggered_by=1,
        )

        self.assertEqual(released.status, InventoryReservationStatus.RELEASED)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 10)
        self.assertEqual(updated.reserved_qty, 0)

    def test_consume_externally_procured_does_not_leak_into_real_reservations(self):
        """같은 SKU에 진짜 예약과 가짜 예약이 공존할 때, 가짜 예약을
        consume()해도 진짜 예약분의 reserved_qty가 잘못 깎이지
        않아야 한다(consume_conditional()의 WHERE reserved_qty >=
        quantity 가드를 잘못 건드리면 재현되는 정확히 그 버그)."""

        sku = self._create_sku(initial_qty=10)

        real = self.service.reserve(
            sku.id, self.company_a.id,
            InventoryReserveRequest(quantity=4, idempotency_key="real-1"),
            triggered_by=1,
        )
        fake = self._reserve_external(sku, quantity=100, key="ext-huge")

        self.service.consume(fake.id, self.company_a.id, triggered_by=1)

        updated = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(updated.available_qty, 6)
        self.assertEqual(updated.reserved_qty, 4)

        # 진짜 예약은 여전히 정상적으로 consume 가능해야 한다.
        self.service.consume(real.id, self.company_a.id, triggered_by=1)
        final = self.service.get_sku(sku.id, self.company_a.id)
        self.assertEqual(final.available_qty, 6)
        self.assertEqual(final.reserved_qty, 0)

    def test_ledger_records_zero_delta_for_externally_procured_events(self):

        sku = self._create_sku(initial_qty=10)
        reservation = self._reserve_external(sku, quantity=3)
        self.service.consume(reservation.id, self.company_a.id, triggered_by=1)

        ledger = self.service.list_ledger(sku.id, self.company_a.id)
        external_events = [e for e in ledger if e.reservation_id == reservation.id]

        self.assertEqual(len(external_events), 2)  # RESERVED + CONSUMED
        for event in external_events:
            self.assertEqual(event.quantity_delta, 0)
            self.assertIsNotNone(event.reason)


if __name__ == "__main__":
    unittest.main()
