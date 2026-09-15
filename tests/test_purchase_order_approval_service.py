"""
=========================================================
Homez OS

File : tests/test_purchase_order_approval_service.py

2026-09-11 후속(반자동 완료 라운드, Phase 5·7) —
PurchaseOrderApprovalService 격리 테스트. 실제 네트워크를 전혀
열지 않는다(순수 DB 로직). 배송비 수동 확인 입력 검증, 최종 승인
게이트(한도·마진·잔여포인트), 만료·무효화, 하루 한도 집계, 발주
직전 재대조를 다룬다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.purchase_task.constants import (
    PurchaseOrderApprovalStatus,
    ShippingCostConfirmationSource,
)
from app.domains.purchase_task.model import PurchaseOrderApproval
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskPolicySetting
from app.domains.purchase_task.order_approval_service import (
    PurchaseOrderApprovalService,
)
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용


class OrderApprovalServiceTestCaseBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, PurchaseTask.__table__,
                PurchaseTaskPolicySetting.__table__,
                PurchaseOrderApproval.__table__,
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.db.add(self.company_a)
        self.db.commit()

        self.service = PurchaseOrderApprovalService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_task(
        self, *, coupang_sale_amount=30000.0, coupang_fee_amount=3000.0,
        key="task-1",
    ) -> PurchaseTask:

        task = PurchaseTask(
            company_id=self.company_a.id, source_order_id=1,
            product_title="테스트 상품", idempotency_key=key,
            coupang_sale_amount=coupang_sale_amount,
            coupang_fee_amount=coupang_fee_amount,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task


class ShippingCostValidationTestCase(OrderApprovalServiceTestCaseBase):

    def test_negative_amount_rejected(self):

        task = self._create_task()
        with self.assertRaises(BadRequestException):
            self.service.confirm_shipping_cost(
                4, self.company_a.id, task.id, "CH1",
                shipping_cost_amount=-100, source=ShippingCostConfirmationSource.OTHER_USER_CONFIRMED,
                confirmed_by=1,
            )

    def test_bool_amount_rejected(self):

        task = self._create_task()
        with self.assertRaises(BadRequestException):
            self.service.confirm_shipping_cost(
                4, self.company_a.id, task.id, "CH1",
                shipping_cost_amount=True, source=ShippingCostConfirmationSource.OTHER_USER_CONFIRMED,
                confirmed_by=1,
            )

    def test_float_amount_rejected(self):

        task = self._create_task()
        with self.assertRaises(BadRequestException):
            self.service.confirm_shipping_cost(
                4, self.company_a.id, task.id, "CH1",
                shipping_cost_amount=3000.5, source=ShippingCostConfirmationSource.OTHER_USER_CONFIRMED,
                confirmed_by=1,
            )

    def test_string_amount_rejected(self):

        task = self._create_task()
        with self.assertRaises(BadRequestException):
            self.service.confirm_shipping_cost(
                4, self.company_a.id, task.id, "CH1",
                shipping_cost_amount="3000", source=ShippingCostConfirmationSource.OTHER_USER_CONFIRMED,
                confirmed_by=1,
            )

    def test_zero_without_free_shipping_flag_rejected(self):

        task = self._create_task()
        with self.assertRaises(BadRequestException):
            self.service.confirm_shipping_cost(
                4, self.company_a.id, task.id, "CH1",
                shipping_cost_amount=0, is_free_shipping_confirmed=False,
                source=ShippingCostConfirmationSource.OTHER_USER_CONFIRMED,
                confirmed_by=1,
            )

    def test_zero_with_free_shipping_flag_accepted(self):

        task = self._create_task()
        approval = self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1",
            shipping_cost_amount=0, is_free_shipping_confirmed=True,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        self.assertEqual(approval.shipping_cost_amount, 0)
        self.assertTrue(approval.shipping_cost_is_free_confirmed)

    def test_unknown_source_rejected(self):

        task = self._create_task()
        with self.assertRaises(BadRequestException):
            self.service.confirm_shipping_cost(
                4, self.company_a.id, task.id, "CH1",
                shipping_cost_amount=3000, source="UNKNOWN_SOURCE",
                confirmed_by=1,
            )

    def test_missing_confirmed_by_rejected(self):
        """자동 모드는 이 경로를 쓸 수 없다는 구조적 보장 — 실제
        사용자 ID 없이는 기록조차 되지 않는다."""

        task = self._create_task()
        with self.assertRaises(BadRequestException):
            self.service.confirm_shipping_cost(
                4, self.company_a.id, task.id, "CH1",
                shipping_cost_amount=3000, source=ShippingCostConfirmationSource.OTHER_USER_CONFIRMED,
                confirmed_by=None,
            )

    def test_valid_input_records_evidence_fields(self):

        task = self._create_task()
        approval = self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1",
            shipping_cost_amount=3500,
            source=ShippingCostConfirmationSource.SUPPLIER_NOTICE,
            basis_memo="공급사 카카오톡 안내 캡처", confirmed_by=7,
        )
        self.assertEqual(approval.shipping_cost_amount, 3500)
        self.assertEqual(approval.shipping_cost_source, ShippingCostConfirmationSource.SUPPLIER_NOTICE)
        self.assertEqual(approval.shipping_cost_basis_memo, "공급사 카카오톡 안내 캡처")
        self.assertEqual(approval.shipping_cost_confirmed_by, 7)
        self.assertIsNotNone(approval.shipping_cost_confirmed_at)
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.PENDING_SHIPPING_COST)


class FinalizeApprovalGateTestCase(OrderApprovalServiceTestCaseBase):

    def _confirm_shipping(self, task, *, amount=3000):

        return self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1",
            shipping_cost_amount=amount,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )

    def test_finalize_without_shipping_confirmed_first_raises(self):

        task = self._create_task()
        with self.assertRaises(ConflictException):
            self.service.finalize_approval(
                4, self.company_a.id, task.id,
                item_amount=10000, current_points=1_000_000, triggered_by=1,
            )

    def test_finalize_succeeds_and_sets_expiry(self):

        task = self._create_task(coupang_sale_amount=30000.0, coupang_fee_amount=3000.0)
        self._confirm_shipping(task, amount=3000)

        before = datetime.utcnow()
        approval = self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=10000, current_points=1_000_000, triggered_by=1,
        )

        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)
        self.assertEqual(approval.approved_by, 1)
        self.assertEqual(approval.required_points_snapshot, 13000)
        self.assertIsNotNone(approval.expires_at)
        self.assertGreater(approval.expires_at, before + timedelta(minutes=9))
        self.assertLess(approval.expires_at, before + timedelta(minutes=11))

    def test_per_order_limit_exceeded_blocks(self):

        task = self._create_task(coupang_sale_amount=300000.0, coupang_fee_amount=30000.0)
        self._confirm_shipping(task, amount=3000)

        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task.id,
                # 상품가+배송비 = 200000, RECOMMENDED_PER_ORDER_MAX=100000 초과
                item_amount=197000, current_points=10_000_000, triggered_by=1,
            )
        self.assertIn("건당 발주 한도", str(ctx.exception))

    def test_min_residual_points_not_met_blocks(self):

        task = self._create_task()
        self._confirm_shipping(task, amount=1000)

        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task.id,
                item_amount=5000,
                # 발주 후 잔액 = 100000-6000 = 94000 < 최소 100000
                current_points=100_000, triggered_by=1,
            )
        self.assertIn("최소 잔여", str(ctx.exception))

    def test_max_concurrent_tasks_exceeded_blocks(self):
        """2026-09-12 후속(Phase 4 잔여 격차 해소 — policy_service.
        evaluate()는 동시 진행 작업 수를 확인하지만 이 최종 게이트는
        재확인하지 않던 결함). BUDGET_HOLDING 상태의 작업 2건이 이미
        있는데 한도를 1건으로 설정하면 세 번째 작업의 최종 승인이
        막혀야 한다."""

        from app.domains.purchase_task.constants import PurchaseTaskStatus

        setting = PurchaseTaskPolicySetting(
            company_id=self.company_a.id,
            min_net_profit=0, min_margin_rate=0,
            max_price_increase_rate=0.05, require_return_allowed=True,
            min_match_confidence=0.98, budget_reservation_hours=24,
            max_concurrent_tasks=1,
        )
        self.db.add(setting)

        open_task_1 = self._create_task(key="open-1")
        open_task_1.status = PurchaseTaskStatus.PURCHASE_READY
        open_task_2 = self._create_task(key="open-2")
        open_task_2.status = PurchaseTaskStatus.PURCHASE_READY
        self.db.commit()

        task = self._create_task(key="finalizing")
        self._confirm_shipping(task, amount=1000)

        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task.id,
                item_amount=5000, current_points=1_000_000, triggered_by=1,
            )
        self.assertIn("동시 진행 작업 한도", str(ctx.exception))

    def test_missing_coupang_amounts_blocks_as_evidence_required(self):
        """원 주문 판매금액·수수료가 없으면 마진을 0으로 추정하지
        않고 차단한다."""

        task = self._create_task(coupang_sale_amount=None, coupang_fee_amount=None)
        self._confirm_shipping(task, amount=1000)

        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task.id,
                item_amount=5000, current_points=1_000_000, triggered_by=1,
            )
        self.assertIn("마진을 계산할 수 없습니다", str(ctx.exception))

    def test_margin_rate_below_minimum_blocks(self):
        """마진율이 15% 권장 기준에 못 미치면 차단 — 판매금액 대비
        원가가 너무 높은 경우."""

        task = self._create_task(coupang_sale_amount=10000.0, coupang_fee_amount=1000.0)
        self._confirm_shipping(task, amount=1000)

        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task.id,
                # 원가 8500 + 배송비 1000, 매출 10000 - 수수료 1000 - 원가 8500
                # - 배송비 1000 = -500 (마진 음수)
                item_amount=8500, current_points=1_000_000, triggered_by=1,
            )
        self.assertTrue(
            "마진율" in str(ctx.exception) or "순이익금" in str(ctx.exception),
        )

    def test_shipping_reinput_invalidates_active_approval(self):

        task = self._create_task()
        self._confirm_shipping(task, amount=3000)
        approval = self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=10000, current_points=1_000_000, triggered_by=1,
        )
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)

        # 배송비를 다시 입력(다른 값)하면 이전 ACTIVE 승인이 즉시
        # PENDING_SHIPPING_COST로 되돌아간다(재승인 전까지 발주 불가).
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1",
            shipping_cost_amount=5000,
            source=ShippingCostConfirmationSource.ONCHANNEL_SUPPORT_ANSWER,
            confirmed_by=1,
        )
        reloaded = self.service.get_approval(4, self.company_a.id, task.id)
        self.assertEqual(reloaded.status, PurchaseOrderApprovalStatus.PENDING_SHIPPING_COST)
        self.assertIsNone(
            self.service.get_active_approval_or_none(4, self.company_a.id, task.id),
        )


class ExpiryAndRevalidationTestCase(OrderApprovalServiceTestCaseBase):

    def test_active_approval_within_window_is_valid(self):

        task = self._create_task()
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=5000, current_points=1_000_000, triggered_by=1,
        )
        self.assertIsNotNone(
            self.service.get_active_approval_or_none(4, self.company_a.id, task.id),
        )

    def test_expired_approval_is_not_active(self):

        task = self._create_task()
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        approval = self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=5000, current_points=1_000_000, triggered_by=1,
        )
        # 시간을 되돌릴 수 없으므로 만료 시각을 직접 과거로 조작해
        # "10분이 지났다"를 재현한다.
        approval.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()

        self.assertIsNone(
            self.service.get_active_approval_or_none(4, self.company_a.id, task.id),
        )
        reloaded = self.service.get_approval(4, self.company_a.id, task.id)
        self.assertEqual(reloaded.status, PurchaseOrderApprovalStatus.EXPIRED)

    def test_revalidate_before_submission_blocks_on_price_change(self):

        task = self._create_task()
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=5000, current_points=1_000_000, triggered_by=1,
        )

        with self.assertRaises(ConflictException):
            self.service.revalidate_before_submission(
                4, self.company_a.id, task.id,
                current_product_code="CH1",
                current_item_amount=5500,  # 가격이 500원 인상됨
                current_points=1_000_000,
                current_shipping_cost_hint=None,
            )
        reloaded = self.service.get_approval(4, self.company_a.id, task.id)
        self.assertEqual(
            reloaded.status, PurchaseOrderApprovalStatus.INVALIDATED_PRICE_CHANGE,
        )

    def test_revalidate_before_submission_passes_when_unchanged(self):

        task = self._create_task()
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=5000, current_points=1_000_000, triggered_by=1,
        )

        approval = self.service.revalidate_before_submission(
            4, self.company_a.id, task.id,
            current_product_code="CH1",
            current_item_amount=5000, current_points=1_000_000,
            current_shipping_cost_hint=1000,
        )
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)

    def test_revalidate_without_any_approval_blocks(self):

        task = self._create_task()
        with self.assertRaises(ConflictException):
            self.service.revalidate_before_submission(
                4, self.company_a.id, task.id,
                current_product_code="CH1",
                current_item_amount=5000, current_points=1_000_000,
                current_shipping_cost_hint=None,
            )


class ConsumedApprovalImmutabilityTestCase(OrderApprovalServiceTestCaseBase):

    def _active_approval(self):
        task = self._create_task()
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        approval = self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=5000, current_points=1_000_000, triggered_by=1,
        )
        return task, approval

    def test_consumed_approval_cannot_be_rewritten_by_shipping_reconfirmation(self):
        task, approval = self._active_approval()
        self.service.mark_consumed(approval)

        with self.assertRaises(ConflictException):
            self.service.confirm_shipping_cost(
                4, self.company_a.id, task.id, "CH1",
                shipping_cost_amount=1200,
                source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
                confirmed_by=1,
            )

        self.db.refresh(approval)
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.CONSUMED)
        self.assertEqual(approval.shipping_cost_amount, 1000)

    def test_approval_cannot_be_used_for_a_different_product(self):
        task, approval = self._active_approval()

        with self.assertRaises(ConflictException):
            self.service.revalidate_before_submission(
                4, self.company_a.id, task.id,
                current_product_code="OTHER-PRODUCT",
                current_item_amount=5000,
                current_points=1_000_000,
                current_shipping_cost_hint=1000,
            )

        self.db.refresh(approval)
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)


class OptionBindingTestCase(OrderApprovalServiceTestCaseBase):
    """2026-09-15 후속(전면 감사 Phase 2, 승인-실행 결합 완성) —
    독립 감사 IA-005의 잔여 부분: 승인 시점 옵션 구성과 실제 발주
    시점 옵션이 달라도(상품코드는 같은데 옵션만 바뀐 경우) 기존
    코드는 이를 구분하지 못했다. 옵션 스냅샷을 승인 시점에 저장하고
    실행 직전 재검증에서 대조한다."""

    def _confirm_shipping(self, task, *, amount=3000):

        return self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1",
            shipping_cost_amount=amount,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )

    def test_different_options_at_submission_blocked(self):

        task = self._create_task(coupang_sale_amount=30000.0, coupang_fee_amount=3000.0)
        self._confirm_shipping(task, amount=3000)
        self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=10000, current_points=1_000_000, triggered_by=1,
            options=[{"id": "OPT1", "qty": 1}, {"id": "OPT2", "qty": 2}],
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.revalidate_before_submission(
                4, self.company_a.id, task.id,
                current_product_code="CH1", current_item_amount=10000,
                current_points=1_000_000, current_shipping_cost_hint=3000,
                current_options=[{"id": "OPT1", "qty": 5}],  # 수량이 다름
            )
        self.assertIn("옵션", str(ctx.exception))

    def test_same_options_in_different_order_still_passes(self):
        """옵션을 고르는 순서만 다른 경우(실제 구성은 동일)는 통과해야
        한다 — 순서 자체는 실제 변경이 아니다."""

        task = self._create_task(coupang_sale_amount=30000.0, coupang_fee_amount=3000.0)
        self._confirm_shipping(task, amount=3000)
        self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=10000, current_points=1_000_000, triggered_by=1,
            options=[{"id": "OPT1", "qty": 1}, {"id": "OPT2", "qty": 2}],
        )

        approval = self.service.revalidate_before_submission(
            4, self.company_a.id, task.id,
            current_product_code="CH1", current_item_amount=10000,
            current_points=1_000_000, current_shipping_cost_hint=3000,
            current_options=[{"id": "OPT2", "qty": 2}, {"id": "OPT1", "qty": 1}],
        )
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)

    def test_no_snapshot_skips_option_check(self):
        """옵션을 넘기지 않고 승인한 기존 방식(하위호환)은 옵션
        재검증 자체를 생략한다 — 추측으로 막지 않는다."""

        task = self._create_task(coupang_sale_amount=30000.0, coupang_fee_amount=3000.0)
        self._confirm_shipping(task, amount=3000)
        self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=10000, current_points=1_000_000, triggered_by=1,
        )

        approval = self.service.revalidate_before_submission(
            4, self.company_a.id, task.id,
            current_product_code="CH1", current_item_amount=10000,
            current_points=1_000_000, current_shipping_cost_hint=3000,
            current_options=[{"id": "ANYTHING", "qty": 99}],
        )
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)


class DailyLimitAggregationTestCase(OrderApprovalServiceTestCaseBase):

    def test_consumed_and_active_rows_within_24h_counted(self):
        """2026-09-14 전면 감사 후속(Phase 5.1 결함 수정) — 이전에는
        이 테스트 이름이 `test_only_consumed_rows_within_24h_counted`
        였고 "ACTIVE는 합계에서 제외돼야 한다"는 것을 검증했다. 그
        기대값 자체가 실제 결함이었다: 여러 작업을 짧은 시간 안에
        각각 승인하면(각 호출이 서로의 아직 CONSUMED 안 된 ACTIVE
        금액을 못 보므로) 일간·월간 한도를 실제로 넘겨도 전부
        통과하는 것을 임시 DB로 직접 재현해 확인했다
        (docs/audits/20260914_FULL_AUDIT.md Phase 5.1). 이제 CONSUMED와
        "아직 만료되지 않은 ACTIVE"를 함께 합산하도록 고쳤으므로,
        이 테스트도 고쳐진 동작을 검증하도록 바꾼다(결함을 가리기
        위해 기대값을 바꾼 것이 아니라, 기대값 자체가 결함이었다)."""

        task1 = self._create_task(key="t1")
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task1.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        approval1 = self.service.finalize_approval(
            4, self.company_a.id, task1.id,
            item_amount=5000, current_points=1_000_000, triggered_by=1,
        )
        self.service.mark_consumed(approval1)

        task2 = self._create_task(key="t2")
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task2.id, "CH1", shipping_cost_amount=2000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        # 아직 ACTIVE일 뿐 CONSUMED는 아니다 — 그래도 만료 전이므로
        # 합계에 포함돼야 한다(수정된 동작).
        self.service.finalize_approval(
            4, self.company_a.id, task2.id,
            item_amount=8000, current_points=1_000_000, triggered_by=1,
        )

        total = self.service._sum_consumed_amount_today(4, self.company_a.id)
        self.assertEqual(total, 16000)  # task1(5000+1000) + task2(8000+2000)

    def test_expired_active_rows_not_counted(self):
        """만료된 ACTIVE 승인(사람이 재승인하지 않고 그대로 방치한
        건)은 더 이상 곧 소비될 예정이 아니므로 합계에서 제외돼야
        한다 — CONSUMED + "미만료" ACTIVE만 합산한다는 계약의
        경계값 검증."""

        task = self._create_task(key="t-expired")
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        approval = self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=5000, current_points=1_000_000, triggered_by=1,
        )
        # 실제로 시간이 지나기를 기다리지 않고, 이미 만료된 것처럼
        # 강제로 되돌린다(격리 테스트 — 실 시계에 의존하지 않는다).
        approval.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()

        total = self.service._sum_consumed_amount_today(4, self.company_a.id)
        self.assertEqual(total, 0)

    def test_daily_limit_exceeded_blocks(self):
        """RECOMMENDED_* 기본값 대신 이 회사 전용의 낮은 하루 한도를
        명시적으로 설정해, 건당 한도와 무관하게 하루 한도만 단독으로
        검증한다(마진은 넉넉하게 잡아 다른 사유로 막히지 않게 한다)."""

        setting = PurchaseTaskPolicySetting(
            company_id=self.company_a.id,
            min_net_profit=0, min_margin_rate=0,
            max_price_increase_rate=0.05, require_return_allowed=True,
            min_match_confidence=0.98, budget_reservation_hours=24,
            per_order_max_amount=1_000_000,
            daily_purchase_limit_amount=150_000,
        )
        self.db.add(setting)
        self.db.commit()

        task1 = self._create_task(
            key="t1", coupang_sale_amount=200000.0, coupang_fee_amount=20000.0,
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task1.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        approval1 = self.service.finalize_approval(
            4, self.company_a.id, task1.id,
            item_amount=90000, current_points=10_000_000, triggered_by=1,
        )
        self.service.mark_consumed(approval1)  # 오늘 소비: 91000원

        task2 = self._create_task(
            key="t2", coupang_sale_amount=200000.0, coupang_fee_amount=20000.0,
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task2.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task2.id,
                # 91000(오늘 이미 소비) + 90000(이번 건) = 181000 > 150000
                item_amount=89000, current_points=10_000_000, triggered_by=1,
            )
        self.assertIn("하루 발주 한도", str(ctx.exception))


class MonthlyLimitAggregationTestCase(OrderApprovalServiceTestCaseBase):
    """2026-09-12 후속(V7 기준선 정리, Phase 4 자동결제 한도 실행경로
    감사) — monthly_purchase_budget_amount가 PurchaseTask 생성 시점의
    policy_service.evaluate()에서만 확인되고 실제 발주 직전 게이트
    (finalize_approval)에서는 재확인되지 않던 결함을 수정한 회귀
    테스트. per_order_max·daily_limit은 넉넉하게(또는 기본값 그대로)
    두어 월간 한도만 단독으로 검증한다."""

    def test_monthly_limit_exceeded_blocks(self):

        setting = PurchaseTaskPolicySetting(
            company_id=self.company_a.id,
            min_net_profit=0, min_margin_rate=0,
            max_price_increase_rate=0.05, require_return_allowed=True,
            min_match_confidence=0.98, budget_reservation_hours=24,
            per_order_max_amount=10_000_000,
            daily_purchase_limit_amount=10_000_000,
            monthly_purchase_budget_amount=150_000,
        )
        self.db.add(setting)
        self.db.commit()

        task1 = self._create_task(
            key="t1", coupang_sale_amount=200000.0, coupang_fee_amount=20000.0,
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task1.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        approval1 = self.service.finalize_approval(
            4, self.company_a.id, task1.id,
            item_amount=90000, current_points=10_000_000, triggered_by=1,
        )
        self.service.mark_consumed(approval1)  # 이번 달 소비: 91000원

        task2 = self._create_task(
            key="t2", coupang_sale_amount=200000.0, coupang_fee_amount=20000.0,
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task2.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task2.id,
                # 91000(이번 달 이미 소비) + 90000(이번 건) = 181000 > 150000
                item_amount=89000, current_points=10_000_000, triggered_by=1,
            )
        self.assertIn("월간 발주 한도", str(ctx.exception))

    def test_monthly_limit_uses_recommended_default_when_unset(self):
        """monthly_purchase_budget_amount만 설정하지 않았으면(None)
        '무제한'이 아니라 RECOMMENDED_MONTHLY_PURCHASE_BUDGET_AMOUNT
        (50만원)를 기본 상한으로 강제해야 한다. per_order_max·
        daily_limit은 이 건 하나가 절대 걸리지 않도록 넉넉히 열어
        둬서, 월간 기본값만 단독으로 검증한다."""

        setting = PurchaseTaskPolicySetting(
            company_id=self.company_a.id,
            min_net_profit=0, min_margin_rate=0,
            max_price_increase_rate=0.05, require_return_allowed=True,
            min_match_confidence=0.98, budget_reservation_hours=24,
            per_order_max_amount=10_000_000,
            daily_purchase_limit_amount=10_000_000,
            monthly_purchase_budget_amount=None,
        )
        self.db.add(setting)
        self.db.commit()

        task = self._create_task(
            coupang_sale_amount=40_000_000.0, coupang_fee_amount=1_000_000.0,
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task.id,
                # per_order_max·daily_limit(각 1000만원)는 통과하지만
                # RECOMMENDED_MONTHLY_PURCHASE_BUDGET_AMOUNT(50만원)는
                # 초과 — 이 값이 실제로 강제됨을 증명한다.
                item_amount=3_500_000, current_points=100_000_000, triggered_by=1,
            )
        self.assertIn("월간 발주 한도", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
