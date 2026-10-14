"""
=========================================================
Homez OS

File : tests/test_purchase_task_fee_refresh.py

2026-10-14 — 작업이 만들어진 뒤에 "발주 예상비용에도 사용" 선택과 8단계 승인이
이루어졌을 때, 비어 있는 예상 수수료만 서버가 승인된 수수료율로 채우는 보완
(`PurchaseTaskService.refresh_missing_channel_fee`)을 고정한다.

원칙: 이미 값이 있는 수수료는 절대 덮어쓰지 않고(소급 변경 금지), 진행된 작업·후보·예산
예약이 있는 작업·자동 생성이 아닌 작업은 바꾸지 않으며, 규칙이 맞지 않으면 아무것도
바꾸지 않는다(0 가정 금지). 외부 호출 없음 — 임시 SQLite만 쓴다.
=========================================================
"""

import json
import unittest

from sqlalchemy import text

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.purchase_task.channel_fee_resolver import resolve_channel_fee
from app.domains.purchase_task.constants import PurchaseTaskCreationSource
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.service import PurchaseTaskService
from tests.test_purchase_task_channel_fee_link import _FeeLinkBase


class FeeRefreshTestCase(_FeeLinkBase):

    def setUp(self):

        super().setUp()
        self.service = PurchaseTaskService(self.db)

    def _task_without_fee(self, **collect_kwargs):
        """승인된 위저드가 없는 상태에서 만들어진 작업 — 수수료가 비어 있다."""

        mapping = self._mapped_out_of_stock_sku()
        task = self._task_for(**collect_kwargs)
        self.assertIsNone(task.coupang_fee_amount)
        self.assertEqual(task.status, PurchaseTaskStatus.SEARCH_REQUIRED)
        return mapping, task

    def _audit_rows(self):
        return self.db.execute(text(
            "SELECT description FROM audit_logs "
            "WHERE action = 'PURCHASE_TASK_FEE_FROM_APPROVED_WIZARD'"
        )).fetchall()

    # ---------------- 정상 ----------------

    def test_fills_the_empty_fee_after_the_opt_in_approval_and_records_why(self):

        mapping, task = self._task_without_fee(quantity=2, unit_price=15000.0)
        version = task.version
        wizard = self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")

        refreshed = self.service.refresh_missing_channel_fee(
            task.id, self.company_id, triggered_by=1,
        )

        self.assertEqual(refreshed.coupang_fee_amount, 2880.0)  # 30,000 × 9.6%, 수량 재곱 없음
        self.assertEqual(refreshed.version, version + 1)
        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn(f"#{wizard.id}", rows[0][0])
        self.assertIn("예상값", rows[0][0])

    def test_a_second_call_never_overwrites_the_filled_value(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        self.service.refresh_missing_channel_fee(task.id, self.company_id)

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.assertEqual(len(self._audit_rows()), 1)

    # ---------------- 바꾸지 않는 경우 ----------------

    def test_without_an_opt_in_wizard_nothing_changes(self):

        mapping, task = self._task_without_fee()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096", opt_in=False,
        )
        version = task.version

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)

        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)
        self.assertEqual(task.version, version)
        self.assertEqual(self._audit_rows(), [])

    def test_two_opt_in_wizards_are_ambiguous_and_change_nothing(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        self._wizard(mapping, status=WizardStatus.SUCCEEDED, fee_rate="0.096")

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_an_existing_fee_is_never_overwritten(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        task.coupang_fee_amount = 777.0
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.db.refresh(task)
        self.assertEqual(task.coupang_fee_amount, 777.0)

    def test_a_task_that_already_has_a_candidate_is_not_touched(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        self.service.add_candidate(
            task.id, self.company_id, shopping_mall_code="OTHER",
            product_url="https://example.test/p/1",
        )

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_a_task_that_moved_past_search_required_is_not_touched(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        task.status = PurchaseTaskStatus.CANDIDATES_READY
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_a_task_with_a_budget_reservation_is_not_touched(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        task.budget_reservation_id = 1
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_only_automatically_created_tasks_are_eligible(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        task.creation_source = PurchaseTaskCreationSource.MANUAL
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_a_missing_sale_amount_is_not_guessed(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")
        task.coupang_sale_amount = None
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)

    def test_an_unconfirmed_rate_is_never_treated_as_zero(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate=None)

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    # ---------------- 회사 경계 ----------------

    def test_another_company_cannot_refresh_the_task(self):

        mapping, task = self._task_without_fee()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.096")

        with self.assertRaises(NotFoundException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id + 1)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_a_wizard_of_another_company_is_not_a_source(self):

        mapping, task = self._task_without_fee()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096",
            company_id=self.company_id + 1,
        )
        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)

    # ---------------- 입력 변경 ----------------

    def test_input_edited_after_approval_is_not_used(self):

        mapping, task = self._task_without_fee()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096",
            approved_snapshot="edited",
        )
        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)

    def test_the_opt_in_only_in_the_current_input_is_ignored(self):

        mapping, task = self._task_without_fee()
        wizard = self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.096", opt_in=False,
        )
        current = json.loads(wizard.economics_input_json)
        current[0]["use_channel_fee_for_purchase_estimate"] = True
        wizard.economics_input_json = json.dumps(current)
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)


class ConfirmedUseOfApprovedRateTestCase(_FeeLinkBase):
    """위저드의 "발주 예상비용에도 사용" 선택이 없는 이미 등록된 상품 — 사용자의 명시적 확인이
    그 한 가지 조건만 대신하고, 나머지 검사는 그대로여야 한다."""

    def setUp(self):

        super().setUp()
        self.service = PurchaseTaskService(self.db)

    def _task_and_mapping(self):

        mapping = self._mapped_out_of_stock_sku()
        task = self._task_for()
        self.assertIsNone(task.coupang_fee_amount)
        return mapping, task

    def _confirm(self, task):
        return self.service.refresh_missing_channel_fee(
            task.id, self.company_id, triggered_by=7,
            confirm_use_of_approved_rate=True,
        )

    def _audit(self):
        return self.db.execute(text(
            "SELECT description, user_id FROM audit_logs "
            "WHERE action = 'PURCHASE_TASK_FEE_FROM_APPROVED_WIZARD'"
        )).fetchall()

    def test_without_the_confirmation_the_missing_opt_in_still_blocks(self):

        mapping, task = self._task_and_mapping()
        self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False,
        )
        with self.assertRaises(ConflictException):
            self.service.refresh_missing_channel_fee(task.id, self.company_id)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_with_the_confirmation_the_approved_rate_fills_the_empty_fee_and_who_confirmed_is_recorded(self):

        mapping, task = self._task_and_mapping()
        wizard = self._wizard(
            mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False,
        )
        refreshed = self._confirm(task)

        self.assertEqual(refreshed.coupang_fee_amount, 980.0)  # 10,000 × 9.8%
        rows = self._audit()
        self.assertEqual(len(rows), 1)
        self.assertIn(f"#{wizard.id}", rows[0][0])
        self.assertIn("사용자(#7)의 명시적 확인", rows[0][0])
        self.assertIn("예상값", rows[0][0])

    def test_the_confirmation_never_overwrites_an_existing_fee(self):

        mapping, task = self._task_and_mapping()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False)
        task.coupang_fee_amount = 321.0
        self.db.commit()
        with self.assertRaises(ConflictException):
            self._confirm(task)
        self.db.refresh(task)
        self.assertEqual(task.coupang_fee_amount, 321.0)

    def test_the_confirmation_does_not_relax_the_other_checks(self):
        """승인 전 위저드·승인 후 입력 변경·승인 패키지 없음·지문 없음·수수료율 미확정·
        다른 판매계정·다른 회사 위저드는 확인을 해도 근거가 되지 않는다."""

        mapping, task = self._task_and_mapping()
        variants = [
            dict(status=WizardStatus.NEEDS_CORRECTION, fee_rate="0.098"),
            dict(status=WizardStatus.APPROVED, fee_rate="0.098", approved_snapshot="edited"),
            dict(status=WizardStatus.APPROVED, fee_rate="0.098", approved_snapshot="none"),
            dict(status=WizardStatus.SUCCEEDED, fee_rate="0.098", with_fingerprint=False),
            dict(status=WizardStatus.APPROVED, fee_rate=None),
            dict(status=WizardStatus.APPROVED, fee_rate="0.098", account_id=99999),
            dict(status=WizardStatus.APPROVED, fee_rate="0.098", company_id=self.company_id + 1),
        ]
        for kwargs in variants:
            with self.subTest(**kwargs):
                wizard = self._wizard(mapping, opt_in=False, **kwargs)
                with self.assertRaises(ConflictException):
                    self._confirm(task)
                self.db.refresh(task)
                self.assertIsNone(task.coupang_fee_amount)
                self.db.delete(wizard)
                self.db.commit()
        self.assertEqual(self._audit(), [])

    def test_two_approved_wizards_are_still_ambiguous_even_with_the_confirmation(self):

        mapping, task = self._task_and_mapping()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False)
        self._wizard(mapping, status=WizardStatus.SUCCEEDED, fee_rate="0.098", opt_in=False)
        with self.assertRaises(ConflictException):
            self._confirm(task)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_the_progressed_task_rules_still_apply_with_the_confirmation(self):

        mapping, task = self._task_and_mapping()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False)
        task.status = PurchaseTaskStatus.CANDIDATES_READY
        self.db.commit()
        with self.assertRaises(ConflictException):
            self._confirm(task)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_another_company_cannot_use_the_confirmation(self):

        mapping, task = self._task_and_mapping()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False)
        with self.assertRaises(NotFoundException):
            self.service.refresh_missing_channel_fee(
                task.id, self.company_id + 1, triggered_by=7,
                confirm_use_of_approved_rate=True,
            )

    def test_resolver_default_is_unchanged_and_only_the_opt_in_condition_is_skipped(self):

        mapping = self._mapped_out_of_stock_sku()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False)
        args = dict(
            inventory_sku_id=mapping.inventory_sku_id, channel_code="COUPANG",
            channel_sku="CH-SKU-1", sale_amount=10000,
        )
        self.assertIsNone(resolve_channel_fee(self.db, self.company_id, **args))
        relaxed = resolve_channel_fee(
            self.db, self.company_id, require_opt_in=False, **args,
        )
        self.assertIsNotNone(relaxed)
        self.assertEqual(relaxed.fee_amount, 980.0)

    def test_the_route_defaults_to_no_confirmation_and_passes_the_flag_through(self):

        from types import SimpleNamespace

        from app.domains.purchase_task import router as module

        mapping, task = self._task_and_mapping()
        self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False)
        user = SimpleNamespace(company_id=self.company_id, id=7)

        self.assertFalse(module.RefreshChannelFeeRequest().confirm_use_of_approved_rate)
        for body in (None, module.RefreshChannelFeeRequest()):
            with self.assertRaises(ConflictException):
                module.refresh_missing_channel_fee(
                    task.id, body, current_user=user, db=self.db,
                )
        done = module.refresh_missing_channel_fee(
            task.id, module.RefreshChannelFeeRequest(confirm_use_of_approved_rate=True),
            current_user=user, db=self.db,
        )
        self.assertEqual(done.coupang_fee_amount, 980.0)


class PinnedWizardTestCase(_FeeLinkBase):
    """같은 상품에 승인된 위저드가 여럿이라 "정확히 하나" 규칙이 모호할 때 — 사용자가 근거 위저드를
    직접 지정해야만 쓸 수 있고, 지정된 위저드도 나머지 검사를 전부 통과해야 한다."""

    def setUp(self):

        super().setUp()
        self.service = PurchaseTaskService(self.db)

    def _setup(self):

        mapping = self._mapped_out_of_stock_sku()
        task = self._task_for()
        first = self._wizard(mapping, status=WizardStatus.SUCCEEDED, fee_rate="0.098", opt_in=False)
        second = self._wizard(mapping, status=WizardStatus.SUCCEEDED, fee_rate="0.098", opt_in=False)
        return mapping, task, first, second

    def _call(self, task, **kwargs):
        return self.service.refresh_missing_channel_fee(
            task.id, self.company_id, triggered_by=7,
            confirm_use_of_approved_rate=True, **kwargs,
        )

    def _audit(self):
        return self.db.execute(text(
            "SELECT description FROM audit_logs "
            "WHERE action = 'PURCHASE_TASK_FEE_FROM_APPROVED_WIZARD'"
        )).fetchall()

    def test_without_pinning_two_approved_wizards_stay_ambiguous(self):

        _mapping, task, _first, _second = self._setup()
        with self.assertRaises(ConflictException):
            self._call(task)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_the_pinned_wizard_is_used_and_recorded(self):

        _mapping, task, _first, second = self._setup()
        done = self._call(task, approved_wizard_id=second.id)

        self.assertEqual(done.coupang_fee_amount, 980.0)
        rows = self._audit()
        self.assertEqual(len(rows), 1)
        self.assertIn(f"#{second.id}의 채널수수료율", rows[0][0])
        self.assertIn(f"사용자가 근거 위저드 #{second.id} 지정", rows[0][0])

    def test_pinning_requires_the_confirmation(self):

        _mapping, task, first, _second = self._setup()
        with self.assertRaises(BadRequestException):
            self.service.refresh_missing_channel_fee(
                task.id, self.company_id, triggered_by=7, approved_wizard_id=first.id,
            )
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_a_pinned_wizard_that_is_not_a_valid_source_is_rejected(self):
        """미승인·승인 후 입력 변경·지문 없음·다른 회사·존재하지 않는 번호를 지정해도 근거가 되지 않는다."""

        mapping, task, _first, _second = self._setup()
        bad = [
            self._wizard(mapping, status=WizardStatus.NEEDS_CORRECTION, fee_rate="0.098", opt_in=False),
            self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False, approved_snapshot="edited"),
            self._wizard(mapping, status=WizardStatus.SUCCEEDED, fee_rate="0.098", opt_in=False, with_fingerprint=False),
            self._wizard(mapping, status=WizardStatus.APPROVED, fee_rate="0.098", opt_in=False, company_id=self.company_id + 1),
        ]
        for wizard in bad:
            with self.subTest(wizard=wizard.id):
                with self.assertRaises(ConflictException):
                    self._call(task, approved_wizard_id=wizard.id)
        with self.assertRaises(ConflictException):
            self._call(task, approved_wizard_id=99999)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)
        self.assertEqual(self._audit(), [])

    def test_a_wizard_for_another_product_cannot_be_pinned(self):
        """이 주문의 상품(listing)과 연결되지 않은 위저드는 지정해도 근거가 되지 않는다."""

        from types import SimpleNamespace

        from app.domains.marketplace_listing.model import MarketplaceListing

        mapping, task, _first, _second = self._setup()
        base = self.db.get(MarketplaceListing, mapping.marketplace_listing_id)
        other_listing = MarketplaceListing(
            company_id=self.company_id, product_candidate_id=base.product_candidate_id + 1000,
            marketplace_account_id=base.marketplace_account_id, status="DRAFT",
        )
        self.db.add(other_listing)
        self.db.commit()
        self.assertNotEqual(other_listing.id, base.id)
        other_wizard = self._wizard(
            SimpleNamespace(marketplace_listing_id=other_listing.id),
            status=WizardStatus.SUCCEEDED, fee_rate="0.5", opt_in=False,
        )
        with self.assertRaises(ConflictException):
            self._call(task, approved_wizard_id=other_wizard.id)
        self.db.refresh(task)
        self.assertIsNone(task.coupang_fee_amount)

    def test_an_existing_fee_is_not_overwritten_even_when_pinned(self):

        _mapping, task, first, _second = self._setup()
        task.coupang_fee_amount = 55.0
        self.db.commit()
        with self.assertRaises(ConflictException):
            self._call(task, approved_wizard_id=first.id)
        self.db.refresh(task)
        self.assertEqual(task.coupang_fee_amount, 55.0)

    def test_resolver_pin_filters_to_one_wizard_and_none_keeps_the_old_rule(self):

        mapping, _task, first, second = self._setup()
        args = dict(
            inventory_sku_id=mapping.inventory_sku_id, channel_code="COUPANG",
            channel_sku="CH-SKU-1", sale_amount=10000, require_opt_in=False,
        )
        self.assertIsNone(resolve_channel_fee(self.db, self.company_id, **args))
        pinned = resolve_channel_fee(
            self.db, self.company_id, only_wizard_id=first.id, **args,
        )
        self.assertEqual((pinned.wizard_id, pinned.fee_amount), (first.id, 980.0))
        self.assertIsNone(resolve_channel_fee(
            self.db, self.company_id, only_wizard_id=987654, **args,
        ))

    def test_the_route_passes_the_pinned_wizard_through(self):

        from types import SimpleNamespace

        from app.domains.purchase_task import router as module

        _mapping, task, _first, second = self._setup()
        user = SimpleNamespace(company_id=self.company_id, id=7)
        self.assertIsNone(module.RefreshChannelFeeRequest().approved_wizard_id)
        done = module.refresh_missing_channel_fee(
            task.id,
            module.RefreshChannelFeeRequest(
                confirm_use_of_approved_rate=True, approved_wizard_id=second.id,
            ),
            current_user=user, db=self.db,
        )
        self.assertEqual(done.coupang_fee_amount, 980.0)


class FeeRefreshRouteTestCase(unittest.TestCase):
    """라우터 연결 — 같은 서비스를 부르고 승인 가드(AdminGuard)를 쓰는지 정적으로 확인한다."""

    def test_route_is_registered_with_an_admin_guard_and_calls_the_service(self):

        import inspect

        from app.domains.purchase_task import router as module

        paths = {r.path for r in module.router.routes}
        self.assertIn("/purchase-tasks/{task_id}/refresh-channel-fee", paths)
        source = inspect.getsource(module.refresh_missing_channel_fee)
        self.assertIn("AdminGuard", source)
        self.assertIn("refresh_missing_channel_fee(", source)


if __name__ == "__main__":
    unittest.main()
