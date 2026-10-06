"""
=========================================================
Homez OS

File : tests/test_purchase_order_margin_policy_gate.py

2026-10-04 — 발주 쪽 최소마진(18%) 최종 판정 검증. 실제 네트워크·실제
주문·결제 없이 격리 임시 DB와 가짜 Adapter만 쓴다.

- 최종 승인(finalize_approval)이 반올림·float 없이 정확히 18%/바로 아래/
  바로 위를 판정하는가
- 승인 이후 정책이 바뀌면 발주 직전 재검증이 기존 승인을 거부하는가
  (그리고 그때 발주 Adapter가 한 번도 호출되지 않는가)
- 위저드 마진과 발주 마진의 비용 정의 차이가 같은 입력에서 판정을 어떻게
  갈라놓는가
=========================================================
"""

import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from app.core.exceptions import ConflictException
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.margin_calculator import (
    calculate_economics,
)
from app.domains.purchase_task.constants import OrderSubmissionStatus
from app.domains.purchase_task.constants import PurchaseOrderApprovalStatus
from app.domains.purchase_task.constants import ShippingCostConfirmationSource
from app.domains.purchase_task.margin_calculator import CandidateCostInput
from app.domains.purchase_task.margin_calculator import calculate_margin
from app.domains.purchase_task.model import PurchaseOrderApproval
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService
from tests.test_purchase_order_approval_service import (
    OrderApprovalServiceTestCaseBase,
)
from tests.test_purchase_order_submission_service import (
    OrderSubmissionServiceTestCaseBase,
)
from tests.test_purchase_order_submission_service import VALID_KWARGS
from tests.test_purchase_order_submission_service import _FakePointResult
from tests.test_purchase_order_submission_service import _FakeProductOption
from tests.test_purchase_order_submission_service import _FakeProductResult
from tests.test_purchase_order_submission_service import (
    _FakeSalesApplicationResult,
)


class _PolicyMixin:

    def _set_policy(self, company_id, **fields):

        setting = PurchaseTaskPolicyService(self.db).get_or_create_default_settings(
            company_id,
        )
        for key, value in fields.items():
            setattr(setting, key, value)
        self.db.commit()
        return setting


class FinalApprovalEighteenPercentBoundaryTestCase(
    _PolicyMixin, OrderApprovalServiceTestCaseBase,
):
    """판매금액 20,000 / 수수료 2,000 / 상품가 12,000 기준 — 배송비만 바꿔
    순이익을 3,601 / 3,600 / 3,599원(18.005% / 정확히 18% / 17.995%)으로
    만든다."""

    def _finalize(self, shipping_cost, **policy):

        self._set_policy(self.company_a.id, min_margin_rate=0.18, **policy)
        task = self._create_task(
            coupang_sale_amount=20000.0, coupang_fee_amount=2000.0,
            key=f"t-{shipping_cost}",
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1",
            shipping_cost_amount=shipping_cost,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        return task, self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=12000, current_points=1_000_000, triggered_by=1,
        )

    def test_exactly_eighteen_percent_passes(self):

        _task, approval = self._finalize(2400)
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)
        self.assertEqual(approval.margin_amount_snapshot, 3600)

    def test_just_above_eighteen_percent_passes(self):

        _task, approval = self._finalize(2399)
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)

    def test_just_below_eighteen_percent_blocks_even_though_it_prints_as_18(self):
        """17.995%는 소수 첫째 자리로 찍으면 "18.0%"로 보이지만 실제로는
        기준 미달이다 — 표시가 아니라 정확한 값으로 판정한다."""

        with self.assertRaises(ConflictException) as ctx:
            self._finalize(2401)
        self.assertIn("마진율", str(ctx.exception))
        self.assertIn("18.0%", str(ctx.exception))  # 둘 다 18.0%로 보이지만 차단

    def test_policy_default_return_reserve_now_counts_at_final_gate(self):
        """후보 평가는 정책의 기본 반품위험 충당금을 마진에서 빼는데 최종
        승인은 빼지 않던 불일치를 없앴다 — 같은 입력에서 충당금 100원
        차이가 정확히 18%를 17.5%로 바꿔 차단한다."""

        with self.assertRaises(ConflictException):
            self._finalize(2400, default_return_risk_reserve=100.0)

    def test_missing_fee_is_never_treated_as_zero(self):

        self._set_policy(self.company_a.id, min_margin_rate=0.18)
        task = self._create_task(
            coupang_sale_amount=20000.0, coupang_fee_amount=None, key="t-nofee",
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        with self.assertRaises(ConflictException) as ctx:
            self.service.finalize_approval(
                4, self.company_a.id, task.id,
                item_amount=1000, current_points=1_000_000, triggered_by=1,
            )
        self.assertIn("마진을 계산할 수 없습니다", str(ctx.exception))


class PolicyChangeAfterApprovalTestCase(
    _PolicyMixin, OrderApprovalServiceTestCaseBase,
):

    def _active_approval(self, shipping_cost=2400):

        self._set_policy(self.company_a.id, min_margin_rate=0.0)
        task = self._create_task(
            coupang_sale_amount=20000.0, coupang_fee_amount=2000.0, key="t-pc",
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1",
            shipping_cost_amount=shipping_cost,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        self.service.finalize_approval(
            4, self.company_a.id, task.id,
            item_amount=12000, current_points=1_000_000, triggered_by=1,
        )
        return task

    def _revalidate(self, task):
        return self.service.revalidate_before_submission(
            4, self.company_a.id, task.id, current_product_code="CH1",
            current_item_amount=12000, current_points=1_000_000,
            current_shipping_cost_hint=None,
        )

    def test_approval_survives_when_policy_still_met(self):

        task = self._active_approval()  # 정확히 18%
        self._set_policy(self.company_a.id, min_margin_rate=0.18)
        self.assertEqual(
            self._revalidate(task).status, PurchaseOrderApprovalStatus.ACTIVE,
        )

    def test_old_approval_cannot_bypass_a_raised_policy(self):

        task = self._active_approval()  # 승인 당시 기준 0% → 통과한 승인
        self._set_policy(self.company_a.id, min_margin_rate=0.99)

        with self.assertRaises(ConflictException) as ctx:
            self._revalidate(task)
        self.assertIn("현재 정책", str(ctx.exception))
        reloaded = self.service.get_approval(4, self.company_a.id, task.id)
        self.assertEqual(
            reloaded.status, PurchaseOrderApprovalStatus.INVALIDATED_POLICY_CHANGE,
        )
        self.assertIsNone(
            self.service.get_active_approval_or_none(4, self.company_a.id, task.id),
        )

    def test_snapshot_of_the_past_approval_is_not_rewritten(self):

        task = self._active_approval()
        before = self.service.get_approval(4, self.company_a.id, task.id)
        snapshot = (before.margin_amount_snapshot, before.margin_rate_snapshot)
        self._set_policy(self.company_a.id, min_margin_rate=0.99)
        with self.assertRaises(ConflictException):
            self._revalidate(task)
        after = self.service.get_approval(4, self.company_a.id, task.id)
        self.assertEqual(
            (after.margin_amount_snapshot, after.margin_rate_snapshot), snapshot,
        )

    def test_missing_task_row_fails_closed(self):

        approval = PurchaseOrderApproval(
            company_id=self.company_a.id, connection_id=4, purchase_task_id=987,
            product_code="CH1", status=PurchaseOrderApprovalStatus.ACTIVE,
            shipping_cost_amount=1000, item_amount_snapshot=12000,
            approved_by=1, approved_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        self.db.add(approval)
        self.db.commit()
        with self.assertRaises(Exception):
            self.service.revalidate_before_submission(
                4, self.company_a.id, 987, current_product_code="CH1",
                current_item_amount=12000, current_points=1_000_000,
                current_shipping_cost_hint=None,
            )


class ProviderNotCalledWhenPolicyBlocksTestCase(
    _PolicyMixin, OrderSubmissionServiceTestCaseBase,
):
    """실제 발주 Adapter 호출 직전의 마지막 방어선까지 현재 정책이
    적용되는지 격리 환경에서 증명한다(가짜 Adapter, 실제 주문 없음)."""

    def setUp(self):

        super().setUp()
        self._patch_contract_confirmed()

    def _install_adapter(self, call_log):

        class _FakeAdapter:
            def check_member_point(self_inner):
                return _FakePointResult(point=1_000_000)

            def lookup_product(self_inner, external_product_id):
                return _FakeProductResult(
                    options=(_FakeProductOption("1", Decimal("12000")),),
                )

            def apply_for_sale(self_inner, external_product_id):
                return _FakeSalesApplicationResult(
                    applied_product_code=external_product_id,
                )

            def submit_order(self_inner, request):
                call_log.append(request)
                return "ORDER-FAKE"

        patcher = mock.patch(
            "app.domains.purchase_task.order_submission_service."
            "get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _task_and_approval(
        self, connection, task_id, *, sale=20000.0, fee=2000.0, item_amount=12000,
    ):

        self.db.add(PurchaseTask(
            id=task_id, company_id=self.company_a.id, source_order_id=task_id,
            product_title="정책 게이트 테스트", idempotency_key=f"pg-{task_id}",
            coupang_sale_amount=sale, coupang_fee_amount=fee,
        ))
        self.db.add(PurchaseOrderApproval(
            company_id=self.company_a.id, connection_id=connection.id,
            purchase_task_id=task_id, product_code="CH1234567",
            status=PurchaseOrderApprovalStatus.ACTIVE,
            shipping_cost_amount=2400, shipping_cost_is_free_confirmed=False,
            item_amount_snapshot=item_amount, approved_by=1,
            approved_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        ))
        self.db.commit()

    def test_provider_is_called_once_when_policy_is_met(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_adapter(call_log)
        self._set_policy(self.company_a.id, min_margin_rate=0.18)
        self._task_and_approval(connection, 71)  # 정확히 18%

        attempt = self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-policy-ok",
            purchase_task_id=71, confirm_real_submission=True,
            confirmed_first_application=True, **VALID_KWARGS,
        )
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)
        self.assertEqual(len(call_log), 1)

    def test_provider_is_never_called_when_policy_was_raised_after_approval(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_adapter(call_log)
        self._set_policy(self.company_a.id, min_margin_rate=0.18)
        self._task_and_approval(connection, 72)
        self._set_policy(self.company_a.id, min_margin_rate=0.25)  # 승인 이후 상향

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-policy-raised",
                purchase_task_id=72, confirm_real_submission=True,
                confirmed_first_application=True, **VALID_KWARGS,
            )
        self.assertIn("정책", str(ctx.exception))
        self.assertEqual(call_log, [], "정책 미달이면 발주 Adapter는 호출되지 않아야 한다.")
        approval = (
            self.db.query(PurchaseOrderApproval)
            .filter(PurchaseOrderApproval.purchase_task_id == 72).first()
        )
        self.assertEqual(
            approval.status, PurchaseOrderApprovalStatus.INVALIDATED_POLICY_CHANGE,
        )


    def _submit_two(self, connection, task_id, key):
        return self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key=key,
            purchase_task_id=task_id, confirm_real_submission=True,
            confirmed_first_application=True,
            **{**VALID_KWARGS, "options": [{"id": 1, "qty": 2}]},
        )

    def test_provider_is_never_called_when_quantity_limit_is_tightened_after_approval(self):
        """후보 평가 때는 통과했어도, 승인 이후 상품별 최대 구매수량이 줄어
        실제 구매 수량(2개)이 초과하면 발주 직전에 막히고 Adapter는 호출되지
        않는다."""

        connection = self._make_ready_connection()
        call_log = []
        self._install_adapter(call_log)
        self._set_policy(self.company_a.id, min_margin_rate=0.0)
        # 수량 2 × 단가 12,000 = 24,000이 승인 당시 금액이다.
        self._task_and_approval(
            connection, 81, sale=100000.0, fee=5000.0, item_amount=24000,
        )
        self._set_policy(self.company_a.id, max_quantity_per_product=1)  # 승인 이후 축소

        with self.assertRaises(ConflictException) as ctx:
            self._submit_two(connection, 81, "k-qty-tight")
        self.assertIn("최대 구매수량", str(ctx.exception))
        self.assertEqual(call_log, [], "수량 한도 초과면 발주 Adapter는 호출되지 않아야 한다.")
        approval = (
            self.db.query(PurchaseOrderApproval)
            .filter(PurchaseOrderApproval.purchase_task_id == 81).first()
        )
        self.assertEqual(
            approval.status, PurchaseOrderApprovalStatus.INVALIDATED_POLICY_CHANGE,
        )

    def test_provider_is_called_once_when_quantity_is_within_the_limit(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_adapter(call_log)
        self._set_policy(self.company_a.id, min_margin_rate=0.0)
        self._task_and_approval(
            connection, 82, sale=100000.0, fee=5000.0, item_amount=24000,
        )
        self._set_policy(self.company_a.id, max_quantity_per_product=2)  # 정확히 한도

        attempt = self._submit_two(connection, 82, "k-qty-ok")
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)
        self.assertEqual(len(call_log), 1)

    def test_null_quantity_limit_keeps_its_old_meaning_no_limit(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_adapter(call_log)
        self._set_policy(
            self.company_a.id, min_margin_rate=0.0, max_quantity_per_product=None,
        )
        self._task_and_approval(
            connection, 83, sale=100000.0, fee=5000.0, item_amount=24000,
        )
        attempt = self._submit_two(connection, 83, "k-qty-null")
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)
        self.assertEqual(len(call_log), 1)


class QuantityPolicyApprovalTestCase(_PolicyMixin, OrderApprovalServiceTestCaseBase):
    """최종 승인·발주 직전 재검증의 수량 한도(옵션 수량 합 기준)."""

    def _finalized(self, options, *, key, limit=None):

        self._set_policy(
            self.company_a.id, min_margin_rate=0.0, max_quantity_per_product=limit,
        )
        task = self._create_task(
            coupang_sale_amount=100000.0, coupang_fee_amount=5000.0, key=key,
        )
        self.service.confirm_shipping_cost(
            4, self.company_a.id, task.id, "CH1", shipping_cost_amount=1000,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        return task, lambda: self.service.finalize_approval(
            4, self.company_a.id, task.id, item_amount=10000,
            current_points=1_000_000, triggered_by=1, options=options,
        )

    def test_quantity_exactly_at_the_limit_passes(self):

        _task, finalize = self._finalized(
            [{"id": 1, "qty": 2}, {"id": 2, "qty": 1}], key="q-ok", limit=3,
        )
        self.assertEqual(finalize().status, PurchaseOrderApprovalStatus.ACTIVE)

    def test_quantity_over_the_limit_blocks_final_approval(self):

        _task, finalize = self._finalized(
            [{"id": 1, "qty": 2}, {"id": 2, "qty": 2}], key="q-over", limit=3,
        )
        with self.assertRaises(ConflictException) as ctx:
            finalize()
        self.assertIn("최대 구매수량(3개) 초과", str(ctx.exception))

    def test_null_limit_does_not_apply_the_recommended_defaults_to_quantity(self):

        _task, finalize = self._finalized([{"id": 1, "qty": 999}], key="q-null", limit=None)
        self.assertEqual(finalize().status, PurchaseOrderApprovalStatus.ACTIVE)

    def test_limit_reduced_after_approval_invalidates_it_before_submission(self):

        task, finalize = self._finalized([{"id": 1, "qty": 2}], key="q-late", limit=None)
        finalize()
        self._set_policy(self.company_a.id, max_quantity_per_product=1)

        with self.assertRaises(ConflictException):
            self.service.revalidate_before_submission(
                4, self.company_a.id, task.id, current_product_code="CH1",
                current_item_amount=10000, current_points=1_000_000,
                current_shipping_cost_hint=None,
                current_options=[{"id": 1, "qty": 2}],
            )
        self.assertEqual(
            self.service.get_approval(4, self.company_a.id, task.id).status,
            PurchaseOrderApprovalStatus.INVALIDATED_POLICY_CHANGE,
        )

    def test_unknown_options_skip_the_quantity_check_instead_of_guessing(self):

        task, finalize = self._finalized([{"id": 1, "qty": 2}], key="q-unk", limit=None)
        finalize()
        self._set_policy(self.company_a.id, max_quantity_per_product=1)
        approval = self.service.revalidate_before_submission(
            4, self.company_a.id, task.id, current_product_code="CH1",
            current_item_amount=10000, current_points=1_000_000,
            current_shipping_cost_hint=None, current_options=None,
        )
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.ACTIVE)


class WizardAndPurchaseCostBasisDifferenceTestCase(unittest.TestCase):
    """같은 상품·같은 18% 기준이라도 위저드와 발주는 비용 정의가 달라 판정이
    갈린다는 것을 격리 계산으로 고정한다(둘을 억지로 같게 만들지 않는다)."""

    def test_same_inputs_can_pass_purchase_gate_and_fail_wizard_gate(self):

        sale = Decimal("12900")
        supplier_price = Decimal("5050")
        shipping = Decimal("3000")
        fee_rate = Decimal("0.096")
        fee_amount = sale * fee_rate

        # 발주 계산: 판매가 − 수수료 − (매입가+배송비) − 추가배송비 − 반품충당(원)
        purchase = calculate_margin(
            CandidateCostInput(
                candidate_id=0, estimated_price=supplier_price,
                estimated_shipping_fee=shipping,
            ),
            coupang_sale_amount=sale, coupang_fee_amount=fee_amount,
        )

        # 위저드 계산: 같은 값에 반품준비율 3%, 포장비 1,000원(가정)을 더 포함
        wizard = calculate_economics(EconomicsInputItem(
            marketplace_account_id=1, cost_of_goods=supplier_price,
            sale_price=sale, channel_fee_rate=fee_rate,
            payment_fee_rate=Decimal("0"), shipping_cost=shipping,
            packaging_cost=Decimal("1000"),
            return_reserve_rate=Decimal("0.03"), tax_basis_rate=Decimal("0"),
        ))

        self.assertEqual(purchase.expected_net_profit, Decimal("3611.6"))
        self.assertEqual(wizard.margin_amount, Decimal("2224.60"))
        eighteen = Decimal("0.18") * sale  # 2,322원
        self.assertGreaterEqual(purchase.expected_net_profit, eighteen)
        self.assertLess(wizard.margin_amount, eighteen)

    def test_wizard_return_reserve_rate_is_a_rate_but_purchase_reserve_is_an_amount(self):
        """위저드의 반품준비율(판매가 대비 비율)과 발주 정책의 기본 반품위험
        충당금(원 단위 금액)은 이름만 비슷한 다른 값이라 자동으로 연결하지
        않는다 — 12,900원의 3%는 387원이라는 별개 계산일 뿐이다."""

        self.assertEqual(Decimal("12900") * Decimal("0.03"), Decimal("387.00"))


if __name__ == "__main__":
    unittest.main()
