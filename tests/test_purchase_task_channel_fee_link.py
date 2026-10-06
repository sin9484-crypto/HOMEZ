"""
=========================================================
Homez OS

File : tests/test_purchase_task_channel_fee_link.py

2026-10-04 — 자동 생성 매입 작업(ORDER_AUTO)의 예상 수수료 연결만
검증한다: 주문 상품 → 채널 매핑 → listing → 승인된 위저드의 **승인 당시**
채널수수료율. 기본은 비활성(사용자가 발주 예상비용 적용을 승인하기 전)이고,
활성이어도 연결이 끊기거나 모호하면 수수료를 비워 두어(0 가정 금지) 최종
승인은 계속 차단돼야 한다. 실제 네트워크·주문·결제 없음.
=========================================================
"""

import json
import logging
from datetime import datetime
from unittest import mock

from sqlalchemy import text

from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.purchase_task import channel_fee_resolver
from app.domains.purchase_task.channel_fee_resolver import resolve_channel_fee
from app.domains.purchase_task.constants import ShippingCostConfirmationSource
from app.domains.purchase_task.model import PurchaseOrderApproval
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.order_approval_service import (
    PurchaseOrderApprovalService,
)
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService
from tests.test_purchase_task_order_sync import OrderSyncTestCaseBase


class _FeeLinkBase(OrderSyncTestCaseBase):

    def setUp(self):

        super().setUp()
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ListingWizard.__table__, PurchaseOrderApproval.__table__,
                PurchaseOrderSubmissionAttempt.__table__,
            ],
        )
        self._wizard_seq = 0

    def _wizard(
        self, mapping, *, status, fee_rate, account_id=None,
        approved_snapshot="same", with_fingerprint=True, opt_in=True,
        company_id=None,
    ):
        """approved_snapshot: 'same'(승인 당시 입력 = 현재 입력) / 'edited'(승인
        후 현재 입력이 바뀜) / 'none'(승인 패키지 없음)."""

        listing = self.db.get(MarketplaceListing, mapping.marketplace_listing_id)
        self._wizard_seq += 1
        entry = {
            "marketplace_account_id": account_id or listing.marketplace_account_id,
            "cost_of_goods": "5050", "sale_price": "10000",
            "channel_fee_rate": fee_rate,
            "use_channel_fee_for_purchase_estimate": opt_in,
        }
        current = [entry]
        if approved_snapshot == "same":
            package = {"economics_input": [dict(entry)]}
        elif approved_snapshot == "edited":
            package = {"economics_input": [{**entry, "channel_fee_rate": "0.05"}]}
        else:
            package = {}
        wizard = ListingWizard(
            company_id=company_id or self.company_id, created_by_user_id=1,
            current_step="RESULTS", status=status, source_type="MANUAL",
            selected_media_asset_ids_json="[]", channel_selections_json="[]",
            economics_input_json=json.dumps(current),
            approval_package_json=json.dumps(package),
            approval_fingerprint=("f" * 64) if with_fingerprint else None,
            approval_history_json="[]",
            materialized_listing_ids_json=json.dumps([{
                "listing_id": listing.id,
                "marketplace_account_id": listing.marketplace_account_id,
                "status": "PENDING",
            }]),
            version=1, creation_idempotency_key=f"fee-link-{self._wizard_seq}",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        self.db.add(wizard)
        self.db.commit()
        return wizard

    def _task_for(self, **collect_kwargs):

        result = self._collect(**collect_kwargs)
        return self.pt_repository.get_task_by_source_order_item(
            self.company_id, result["items"][0].id,
        )

    def _mapped_out_of_stock_sku(self):

        sku = self._create_sku(initial_qty=0)
        return self._map_sku_to_channel(sku)


class NotApprovedForPurchaseTestCase(_FeeLinkBase):
    """운영자가 이 판매계정에 대해 "발주 예상비용에도 사용"을 선택·승인하지
    않은 상태 — 위저드 8단계 승인만으로는 수수료를 채우지 않는다."""

    def test_approved_wizard_without_the_opt_in_never_fills_the_fee(self):

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096", opt_in=False,
        )
        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_opt_in_missing_from_old_snapshot_means_not_approved(self):
        """이 선택이 생기기 전에 승인된 스냅샷(키 자체가 없음)은 미승인이다."""

        mapping = self._mapped_out_of_stock_sku()
        wizard = self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        for column in ("economics_input_json", "approval_package_json"):
            raw = json.loads(getattr(wizard, column))
            entries = raw["economics_input"] if column == "approval_package_json" else raw
            for e in entries:
                e.pop("use_channel_fee_for_purchase_estimate")
            setattr(wizard, column, json.dumps(raw))
        self.db.commit()

        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_opt_in_only_in_current_input_not_in_the_approved_snapshot_is_ignored(self):
        """승인 뒤에 선택만 켠 경우(승인 당시 스냅샷은 꺼짐)는 쓰지 않는다."""

        mapping = self._mapped_out_of_stock_sku()
        wizard = self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096", opt_in=False,
        )
        current = json.loads(wizard.economics_input_json)
        current[0]["use_channel_fee_for_purchase_estimate"] = True
        wizard.economics_input_json = json.dumps(current)
        self.db.commit()

        self.assertIsNone(self._task_for().coupang_fee_amount)


class ChannelFeeLinkTestCase(_FeeLinkBase):
    """운영자가 이 판매계정에 대해 선택하고 8단계 승인을 받은 경우의 경계."""

    def test_fee_amount_comes_from_approved_snapshot_and_quantity_is_not_doubled(self):

        mapping = self._mapped_out_of_stock_sku()
        wizard = self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")

        task = self._task_for(quantity=2, unit_price=15000.0)
        self.assertEqual(task.coupang_sale_amount, 30000.0)  # 줄 합계(15,000×2)
        self.assertEqual(task.coupang_fee_amount, 2880.0)  # 30,000 × 9.6%, 수량 재곱 없음
        audit = self.db.execute(text(
            "SELECT description FROM audit_logs "
            "WHERE action = 'PURCHASE_TASK_FEE_FROM_APPROVED_WIZARD'"
        )).fetchall()
        self.assertEqual(len(audit), 1)
        self.assertIn(f"#{wizard.id}", audit[0][0])
        self.assertIn("예상값", audit[0][0])

    def test_wizard_not_yet_approved_is_not_a_fee_source(self):

        mapping = self._mapped_out_of_stock_sku()
        for status in (WizardStatus.NEEDS_CORRECTION, WizardStatus.READY_FOR_APPROVAL):
            self._wizard(mapping, status=status, fee_rate="0.096")

        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_input_edited_after_approval_is_never_used(self):
        """승인 후 현재 입력이 승인 당시 스냅샷과 달라졌으면 어느 쪽도
        쓰지 않는다."""

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096",
            approved_snapshot="edited",
        )
        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_missing_approval_snapshot_or_fingerprint_is_not_a_source(self):

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096",
            approved_snapshot="none",
        )
        self._wizard(
            mapping, status=WizardStatus.SUCCEEDED, fee_rate="0.096",
            with_fingerprint=False,
        )
        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_unconfirmed_rate_is_never_treated_as_zero(self):

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate=None)

        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_no_wizard_keeps_previous_behavior(self):

        self._mapped_out_of_stock_sku()
        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_two_candidate_wizards_are_ambiguous_even_with_the_same_rate(self):

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        self._wizard(mapping, status=WizardStatus.SUCCEEDED, fee_rate="0.096")

        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_rate_for_another_account_is_not_used(self):

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096",
            account_id=99999,
        )
        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_company_a_approval_does_not_apply_to_company_b(self):
        """회사 A의 승인된 위저드·매핑은 회사 B의 같은 SKU/채널 SKU 조회에
        절대 쓰이지 않는다(회사 경계)."""

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        args = dict(
            inventory_sku_id=mapping.inventory_sku_id, channel_code="COUPANG",
            channel_sku="CH-SKU-1", sale_amount=10000,
        )
        self.assertIsNotNone(resolve_channel_fee(self.db, self.company_id, **args))
        for other_company in (self.company_id + 1, 99999):
            self.assertIsNone(resolve_channel_fee(self.db, other_company, **args))

    def test_a_wizard_of_another_company_is_never_a_source(self):

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096",
            company_id=self.company_id + 1,
        )
        self.assertIsNone(self._task_for().coupang_fee_amount)

    def test_other_company_and_other_channel_do_not_match(self):

        mapping = self._mapped_out_of_stock_sku()
        sku_id = mapping.inventory_sku_id
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")

        kwargs = dict(
            inventory_sku_id=sku_id, channel_code="COUPANG",
            channel_sku="CH-SKU-1", sale_amount=10000,
        )
        self.assertIsNotNone(resolve_channel_fee(self.db, self.company_id, **kwargs))
        self.assertIsNone(resolve_channel_fee(self.db, self.company_id + 1, **kwargs))
        self.assertIsNone(resolve_channel_fee(
            self.db, self.company_id, **{**kwargs, "channel_code": "NAVER"},
        ))
        self.assertIsNone(resolve_channel_fee(
            self.db, self.company_id, **{**kwargs, "channel_sku": "OTHER"},
        ))

    def test_revoking_the_approval_stops_new_tasks_but_never_rewrites_old_ones(self):
        """승인을 취소하면(상태가 승인 이후가 아니게 됨) 새 작업에는 쓰지 않고,
        이미 만들어진 작업의 수수료 금액은 소급 변경하지 않는다."""

        mapping = self._mapped_out_of_stock_sku()
        wizard = self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        before = self._task_for(channel_order_id="CO-A", unit_price=10000.0)
        self.assertEqual(before.coupang_fee_amount, 960.0)

        wizard.status = WizardStatus.NEEDS_CORRECTION  # 승인 취소 후 수정 중
        self.db.commit()

        after = self._task_for(channel_order_id="CO-B", unit_price=10000.0)
        self.assertIsNone(after.coupang_fee_amount)
        self.db.refresh(before)
        self.assertEqual(before.coupang_fee_amount, 960.0)

    def test_missing_table_is_reported_and_leaves_the_session_usable(self):
        """누락된 테이블만 구분해 경고를 남기고 None을 돌려준다. 다른 DB
        오류는 삼키지 않으며, 세션은 이후에도 정상 사용 가능해야 한다."""

        mapping = self._mapped_out_of_stock_sku()
        sku_id = mapping.inventory_sku_id
        self.db.execute(text("DROP TABLE listing_wizards"))
        self.db.commit()

        with self.assertLogs(channel_fee_resolver.logger, level=logging.WARNING) as logs:
            resolved = resolve_channel_fee(
                self.db, self.company_id, inventory_sku_id=sku_id,
                channel_code="COUPANG", channel_sku="CH-SKU-1", sale_amount=10000,
            )
        self.assertIsNone(resolved)
        self.assertIn("listing_wizards", logs.output[0])
        # 세션이 오류 상태로 남지 않았다.
        self.assertEqual(
            self.db.execute(text("SELECT COUNT(*) FROM companies")).scalar(), 1,
        )

    def test_other_database_errors_are_not_swallowed(self):

        mapping = self._mapped_out_of_stock_sku()
        sku_id = mapping.inventory_sku_id
        with mock.patch.object(
            self.db, "query", side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                resolve_channel_fee(
                    self.db, self.company_id, inventory_sku_id=sku_id,
                    channel_code="COUPANG", channel_sku="CH-SKU-1",
                    sale_amount=10000,
                )

    def _finalize(self, task, *, item_amount, shipping=1000):

        PurchaseTaskPolicyService(self.db).get_or_create_default_settings(
            self.company_id,
        ).min_margin_rate = 0.18
        self.db.commit()
        service = PurchaseOrderApprovalService(self.db)
        service.confirm_shipping_cost(
            4, self.company_id, task.id, "CH1", shipping_cost_amount=shipping,
            source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
            confirmed_by=1,
        )
        return service.finalize_approval(
            4, self.company_id, task.id, item_amount=item_amount,
            current_points=1_000_000, triggered_by=1,
        )

    def test_linked_fee_lets_the_final_gate_judge_margin_not_fail_on_missing_data(self):

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        task = self._task_for(unit_price=10000.0)  # 수수료 960

        approval = self._finalize(task, item_amount=6000)  # 순이익 2,040 = 20.4%
        self.assertEqual(approval.margin_amount_snapshot, 2040)

        task2 = self._task_for(channel_order_id="CO-2", unit_price=10000.0)
        with self.assertRaises(ConflictException) as ctx:
            self._finalize(task2, item_amount=7000)  # 순이익 1,040 = 10.4%
        self.assertIn("마진율", str(ctx.exception))
        self.assertNotIn("계산할 수 없습니다", str(ctx.exception))

    def test_without_a_link_the_final_gate_still_blocks_as_before(self):

        self._mapped_out_of_stock_sku()
        task = self._task_for(unit_price=10000.0)
        self.assertIsNone(task.coupang_fee_amount)

        with self.assertRaises(ConflictException) as ctx:
            self._finalize(task, item_amount=1000)
        self.assertIn("마진을 계산할 수 없습니다", str(ctx.exception))
