"""
=========================================================
Homez OS

File : tests/test_price_stock_safety_domain.py

2026-09-10 Phase 10(HOMEZ_USER_OPERATION_SETTINGS.md 2·7번) — 가상재고
임계값 게이트, 가격 검토주기 설정 검증.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.price_stock_safety.constants import DEFAULT_PRICE_REVIEW_CYCLE_DAYS
from app.domains.price_stock_safety.service import PriceStockSafetyService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class PriceStockSafetyDomainTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)
        self.assertFalse(result.migration_approval_required)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="가격재고안전 테스트 회사", business_number="121-21-21212",
            ceo="테스트", phone="02-000-0000",
            email="pss@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="pssadmin",
            email="pssadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.service = PriceStockSafetyService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    # ------------------------------
    # 가상재고 임계값
    # ------------------------------

    def test_threshold_defaults_to_none_unset(self):

        self.assertIsNone(
            self.service.get_virtual_stock_threshold(self.company.id),
        )

    def test_set_threshold_updates_value(self):

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=5,
        )
        self.assertEqual(
            self.service.get_virtual_stock_threshold(self.company.id), 5,
        )

    def test_set_threshold_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_virtual_stock_threshold(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=False, threshold_quantity=5,
            )

    def test_set_threshold_rejects_negative(self):

        with self.assertRaises(BadRequestException):
            self.service.set_virtual_stock_threshold(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, threshold_quantity=-1,
            )

    def test_threshold_history_is_append_only_latest_wins(self):

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=5,
        )
        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=10,
        )
        self.assertEqual(
            self.service.get_virtual_stock_threshold(self.company.id), 10,
        )

    # ------------------------------
    # 가상재고 게이트 판정
    # ------------------------------

    def test_unset_threshold_always_allows(self):

        allowed, reason = self.service.check_virtual_stock_allows_auto_order(
            self.company.id, 0,
        )
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_stock_above_threshold_is_allowed(self):

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=5,
        )
        allowed, _ = self.service.check_virtual_stock_allows_auto_order(
            self.company.id, 10,
        )
        self.assertTrue(allowed)

    def test_stock_at_or_below_threshold_is_denied(self):

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=5,
        )
        allowed, reason = self.service.check_virtual_stock_allows_auto_order(
            self.company.id, 5,
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

        allowed, _ = self.service.check_virtual_stock_allows_auto_order(
            self.company.id, 3,
        )
        self.assertFalse(allowed)

    def test_negative_displayed_stock_rejected(self):

        with self.assertRaises(BadRequestException):
            self.service.check_virtual_stock_allows_auto_order(
                self.company.id, -1,
            )

    def test_thresholds_are_isolated_per_company(self):

        other_company = Company(
            name="다른회사PSS", business_number="131-31-31313",
            ceo="테스트", phone="02-000-0000",
            email="other-pss@example.com", address="테스트",
        )
        self.db.add(other_company)
        self.db.commit()

        self.service.set_virtual_stock_threshold(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, threshold_quantity=100,
        )

        self.assertIsNone(
            self.service.get_virtual_stock_threshold(other_company.id),
        )

    # ------------------------------
    # 가격 검토주기
    # ------------------------------

    def test_review_cycle_defaults_to_document_initial_value(self):

        self.assertEqual(
            self.service.get_review_cycle_days(self.company.id),
            DEFAULT_PRICE_REVIEW_CYCLE_DAYS,
        )
        self.assertEqual(DEFAULT_PRICE_REVIEW_CYCLE_DAYS, 7)

    def test_set_review_cycle_overrides_default(self):

        self.service.set_review_cycle_days(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, review_cycle_days=14,
        )
        self.assertEqual(
            self.service.get_review_cycle_days(self.company.id), 14,
        )

    def test_set_review_cycle_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_review_cycle_days(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=False, review_cycle_days=14,
            )

    def test_set_review_cycle_rejects_non_positive(self):

        with self.assertRaises(BadRequestException):
            self.service.set_review_cycle_days(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, review_cycle_days=0,
            )

    # ------------------------------
    # 캐시 유효시간(TTL) — Phase 9D(8-5)/9E(8-6)
    # ------------------------------

    def test_price_cache_ttl_defaults_to_30_minutes(self):

        from app.domains.price_stock_safety.constants import (
            DEFAULT_PRICE_CACHE_TTL_MINUTES,
        )

        self.assertEqual(DEFAULT_PRICE_CACHE_TTL_MINUTES, 30)
        self.assertEqual(
            self.service.get_price_cache_ttl_minutes(self.company.id),
            DEFAULT_PRICE_CACHE_TTL_MINUTES,
        )

    def test_stock_cache_ttl_defaults_to_10_minutes(self):

        from app.domains.price_stock_safety.constants import (
            DEFAULT_STOCK_CACHE_TTL_MINUTES,
        )

        self.assertEqual(DEFAULT_STOCK_CACHE_TTL_MINUTES, 10)
        self.assertEqual(
            self.service.get_stock_cache_ttl_minutes(self.company.id),
            DEFAULT_STOCK_CACHE_TTL_MINUTES,
        )

    def test_price_and_stock_ttl_are_independently_configurable(self):

        self.service.set_price_cache_ttl_minutes(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, ttl_minutes=45,
        )
        self.assertEqual(
            self.service.get_price_cache_ttl_minutes(self.company.id), 45,
        )
        # 재고 TTL은 가격 TTL 변경의 영향을 받지 않는다(별도 테이블).
        from app.domains.price_stock_safety.constants import (
            DEFAULT_STOCK_CACHE_TTL_MINUTES,
        )
        self.assertEqual(
            self.service.get_stock_cache_ttl_minutes(self.company.id),
            DEFAULT_STOCK_CACHE_TTL_MINUTES,
        )

    def test_set_price_cache_ttl_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_price_cache_ttl_minutes(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=False, ttl_minutes=45,
            )

    def test_set_stock_cache_ttl_rejects_non_positive(self):

        with self.assertRaises(BadRequestException):
            self.service.set_stock_cache_ttl_minutes(
                company_id=self.company.id, user_id=self.admin.id,
                is_admin=True, ttl_minutes=0,
            )

    # ------------------------------
    # 가격/재고 조회 캐시 — Phase 9A(7-8)
    # ------------------------------

    def test_no_cached_price_before_any_store(self):

        self.assertIsNone(
            self.service.get_cached_price(self.company.id, 1, "CH1", "opt-1"),
        )

    def test_stored_price_is_retrievable_before_expiry(self):

        self.service.store_price_quote(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", option_id="opt-1", price_amount=12000,
        )
        cached = self.service.get_cached_price(
            self.company.id, 1, "CH1", "opt-1",
        )
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0], 12000)

    def test_price_cache_expires_after_ttl(self):
        """Fake clock — confirmed_at을 과거로 고정해 TTL 경계를
        결정적으로 검증한다(실제 대기 없음)."""

        from datetime import datetime, timedelta

        self.service.set_price_cache_ttl_minutes(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, ttl_minutes=30,
        )
        long_ago = datetime.utcnow() - timedelta(minutes=31)
        self.service.store_price_quote(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", option_id="opt-1", price_amount=12000,
            confirmed_at=long_ago,
        )

        self.assertIsNone(
            self.service.get_cached_price(self.company.id, 1, "CH1", "opt-1"),
        )

    def test_price_cache_still_valid_just_before_ttl_boundary(self):

        from datetime import datetime, timedelta

        self.service.set_price_cache_ttl_minutes(
            company_id=self.company.id, user_id=self.admin.id,
            is_admin=True, ttl_minutes=30,
        )
        just_inside = datetime.utcnow() - timedelta(minutes=29)
        self.service.store_price_quote(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", option_id="opt-1", price_amount=12000,
            confirmed_at=just_inside,
        )

        cached = self.service.get_cached_price(
            self.company.id, 1, "CH1", "opt-1",
        )
        self.assertIsNotNone(cached)

    def test_stock_cache_expires_independently_of_price_cache(self):
        """가격은 아직 유효한데 재고만 만료된 상태가 정상적으로
        존재할 수 있어야 한다(TTL이 다르므로)."""

        from datetime import datetime, timedelta

        self.service.store_price_quote(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", option_id="opt-1", price_amount=12000,
        )
        long_ago = datetime.utcnow() - timedelta(minutes=11)
        self.service.store_stock_quote(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", option_id="opt-1", in_stock=True,
            confirmed_at=long_ago,
        )

        self.assertIsNotNone(
            self.service.get_cached_price(self.company.id, 1, "CH1", "opt-1"),
        )
        self.assertIsNone(
            self.service.get_cached_stock(self.company.id, 1, "CH1", "opt-1"),
        )

    def test_cache_is_isolated_by_connection_product_and_option(self):

        self.service.store_price_quote(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", option_id="opt-1", price_amount=1000,
        )
        self.service.store_price_quote(
            company_id=self.company.id, connection_id=2,
            product_code="CH1", option_id="opt-1", price_amount=2000,
        )
        self.service.store_price_quote(
            company_id=self.company.id, connection_id=1,
            product_code="CH2", option_id="opt-1", price_amount=3000,
        )
        self.service.store_price_quote(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", option_id="opt-2", price_amount=4000,
        )

        self.assertEqual(
            self.service.get_cached_price(self.company.id, 1, "CH1", "opt-1")[0],
            1000,
        )
        self.assertEqual(
            self.service.get_cached_price(self.company.id, 2, "CH1", "opt-1")[0],
            2000,
        )
        self.assertEqual(
            self.service.get_cached_price(self.company.id, 1, "CH2", "opt-1")[0],
            3000,
        )
        self.assertEqual(
            self.service.get_cached_price(self.company.id, 1, "CH1", "opt-2")[0],
            4000,
        )

    # ------------------------------
    # 가상재고 0 제안 — Phase 9F(8-19)
    # ------------------------------

    def _notification_rows(self):

        from sqlalchemy import text

        return self.db.execute(
            text(
                "SELECT user_id FROM notification_email_logs "
                "WHERE event_code = 'VIRTUAL_STOCK_ZERO_PROPOSED'",
            ),
        ).fetchall()

    def test_propose_zero_stock_creates_pending_proposal_and_notifies(self):

        from app.domains.price_stock_safety.model import (
            VirtualStockZeroProposalStatus,
        )

        proposal = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="판매 가능 여부 확인 불가",
        )

        self.assertEqual(proposal.status, VirtualStockZeroProposalStatus.PENDING)
        self.assertEqual(len(self._notification_rows()), 1)
        self.assertEqual(self._notification_rows()[0][0], self.admin.id)

    def test_propose_zero_stock_rejects_blank_reason(self):

        with self.assertRaises(BadRequestException):
            self.service.propose_zero_stock(
                company_id=self.company.id, connection_id=1,
                product_code="CH1", reason="   ",
            )

    def test_repeated_proposal_for_same_product_does_not_duplicate_or_renotify(self):

        first = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="1차 확인 불가",
        )
        second = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="2차 확인 불가",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self._notification_rows()), 1)

    def test_has_pending_proposal_true_after_propose(self):

        self.assertFalse(
            self.service.has_pending_zero_stock_proposal(
                self.company.id, "CH1",
            ),
        )
        self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="확인 불가",
        )
        self.assertTrue(
            self.service.has_pending_zero_stock_proposal(
                self.company.id, "CH1",
            ),
        )

    def test_has_pending_proposal_false_across_different_products(self):

        self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="확인 불가",
        )
        self.assertFalse(
            self.service.has_pending_zero_stock_proposal(
                self.company.id, "CH2",
            ),
        )

    def test_resolve_requires_admin(self):

        proposal = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="확인 불가",
        )
        with self.assertRaises(ForbiddenException):
            self.service.resolve_zero_stock_proposal(
                proposal.id, self.company.id, is_admin=False,
                resolved_by=self.admin.id, approve=True,
                resolution_note="확인함",
            )

    def test_resolve_requires_non_blank_note(self):

        proposal = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="확인 불가",
        )
        with self.assertRaises(BadRequestException):
            self.service.resolve_zero_stock_proposal(
                proposal.id, self.company.id, is_admin=True,
                resolved_by=self.admin.id, approve=True,
                resolution_note="  ",
            )

    def test_resolve_approve_clears_pending_and_blocks_reappear(self):

        from app.domains.price_stock_safety.model import (
            VirtualStockZeroProposalStatus,
        )

        proposal = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="확인 불가",
        )
        resolved = self.service.resolve_zero_stock_proposal(
            proposal.id, self.company.id, is_admin=True,
            resolved_by=self.admin.id, approve=True,
            resolution_note="가상재고 0으로 실제 반영 완료",
        )

        self.assertEqual(resolved.status, VirtualStockZeroProposalStatus.APPROVED)
        self.assertFalse(
            self.service.has_pending_zero_stock_proposal(
                self.company.id, "CH1",
            ),
        )

        # 재고 확인이 나중에 성공해도 자동으로 복구되지 않는다 — 새
        # propose 호출은 새 PENDING 행을 만든다(과거 승인 행과 별개).
        new_proposal = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="다시 확인 불가",
        )
        self.assertNotEqual(new_proposal.id, resolved.id)

    def test_resolve_reject_does_not_reset_pending_flag_incorrectly(self):

        from app.domains.price_stock_safety.model import (
            VirtualStockZeroProposalStatus,
        )

        proposal = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="확인 불가",
        )
        resolved = self.service.resolve_zero_stock_proposal(
            proposal.id, self.company.id, is_admin=True,
            resolved_by=self.admin.id, approve=False,
            resolution_note="실제로는 정상 판매 가능 확인됨",
        )

        self.assertEqual(resolved.status, VirtualStockZeroProposalStatus.REJECTED)
        self.assertFalse(
            self.service.has_pending_zero_stock_proposal(
                self.company.id, "CH1",
            ),
        )

    def test_resolve_already_resolved_proposal_rejected(self):

        proposal = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="확인 불가",
        )
        self.service.resolve_zero_stock_proposal(
            proposal.id, self.company.id, is_admin=True,
            resolved_by=self.admin.id, approve=True,
            resolution_note="처리 완료",
        )

        with self.assertRaises(BadRequestException):
            self.service.resolve_zero_stock_proposal(
                proposal.id, self.company.id, is_admin=True,
                resolved_by=self.admin.id, approve=True,
                resolution_note="다시 처리",
            )

    def test_list_zero_stock_proposals_filters_by_status(self):

        from app.domains.price_stock_safety.model import (
            VirtualStockZeroProposalStatus,
        )

        p1 = self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH1", reason="확인 불가",
        )
        self.service.propose_zero_stock(
            company_id=self.company.id, connection_id=1,
            product_code="CH2", reason="확인 불가",
        )
        self.service.resolve_zero_stock_proposal(
            p1.id, self.company.id, is_admin=True,
            resolved_by=self.admin.id, approve=True,
            resolution_note="처리 완료",
        )

        pending = self.service.list_zero_stock_proposals(
            self.company.id, status=VirtualStockZeroProposalStatus.PENDING,
        )
        self.assertEqual([p.product_code for p in pending], ["CH2"])


if __name__ == "__main__":
    unittest.main()
