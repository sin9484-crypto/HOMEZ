"""
=========================================================
Homez OS

File : tests/test_listing_wizard_bulk_margin_and_submit.py

2026-09-06 "일괄 마진 설정 + 멀티마켓 동시 등록" 검증.

margin_calculator.derive_sale_price_for_margin_rate()는 순수 함수라
직접 검증한다. bulk_apply_margin_rate()/bulk_submit()은 오케스트레이션
계층(선택 모드/최대 건수/건별 독립 처리)만 검증하고, 실제 계산·저장·
채널 제출 로직 자체는 update_economics()/submit()의 기존 테스트가
이미 커버하므로 여기서는 두 메서드를 mock으로 대체해 반복 로직만
분리해서 검증한다(동일 원칙: test_order_multi_channel_collection_
service.py에서 CoupangOrderCollectionService.run()을 재사용 검증한
것과 같은 접근).
=========================================================
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing.constants import MAX_BULK_MARGIN_APPLY_COUNT
from app.domains.marketplace_listing.constants import MAX_BULK_SUBMIT_COUNT
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkMarginApplyRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkSubmitRequest,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    ListingWizardService,
)
from app.domains.marketplace_listing.margin_calculator import (
    derive_sale_price_for_margin_rate,
)
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.user.model import User  # noqa: F401 (relationship 해석용)

_COUNTER = 0


def _key(prefix: str) -> str:
    global _COUNTER
    _COUNTER += 1
    return f"{prefix}-{_COUNTER}"


class DeriveSalePriceForMarginRateTest(unittest.TestCase):
    """순수 함수 — DB 없이 직접 검증."""

    def _item(self, **overrides) -> EconomicsInputItem:
        base = dict(
            marketplace_account_id=1,
            cost_of_goods=Decimal("5000"),
            sale_price=Decimal("0"),  # 이 함수는 sale_price를 쓰지 않는다
            channel_fee_rate=Decimal("0.1"),
            payment_fee_rate=Decimal("0.03"),
            shipping_cost=Decimal("2000"),
            packaging_cost=Decimal("0"),
            ad_cost=Decimal("0"),
            return_reserve_rate=Decimal("0"),
            tax_basis_rate=Decimal("0"),
        )
        base.update(overrides)
        return EconomicsInputItem(**base)

    def test_zero_target_margin_matches_break_even_price(self):
        item = self._item()
        derived = derive_sale_price_for_margin_rate(item, Decimal("0"))
        # fixed_costs = 5000 + 2000 = 7000, rate_sum = 0.13
        # break_even_price = 7000 / (1 - 0.13) = 8045.98(반올림)
        self.assertEqual(derived, Decimal("8045.98"))

    def test_positive_target_margin_yields_higher_price_than_break_even(self):
        item = self._item()
        break_even = derive_sale_price_for_margin_rate(item, Decimal("0"))
        with_margin = derive_sale_price_for_margin_rate(item, Decimal("0.2"))
        self.assertGreater(with_margin, break_even)

    def test_infeasible_target_margin_returns_none(self):
        item = self._item(
            channel_fee_rate=Decimal("0.5"), payment_fee_rate=Decimal("0.5"),
        )
        # rate_sum = 1.0 already, any positive target margin is infeasible
        derived = derive_sale_price_for_margin_rate(item, Decimal("0.01"))
        self.assertIsNone(derived)

    def test_sale_price_field_is_ignored(self):
        cheap = self._item(sale_price=Decimal("1"))
        expensive = self._item(sale_price=Decimal("999999"))
        self.assertEqual(
            derive_sale_price_for_margin_rate(cheap, Decimal("0.1")),
            derive_sale_price_for_margin_rate(expensive, Decimal("0.1")),
        )


class BulkMarginApplyAndSubmitOrchestrationTest(unittest.TestCase):

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, ListingWizard.__table__],
        )
        with self.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            ))

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = ListingWizardService(self.db)

        self.company = self._seed_company("회사 A")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_company(self, name: str) -> Company:
        company = Company(
            name=name, business_number=_key("000-00"), ceo="테스트",
            phone="02-000-0000", email=f"{_key('wiz')}@test.com",
            address="서울",
        )
        self.db.add(company)
        self.db.commit()
        return company

    def _seed_wizard(
        self, company_id: int, *, status: str = WizardStatus.DRAFT,
        economics_items: list[dict] | None = None,
    ) -> ListingWizard:
        wizard = ListingWizard(
            company_id=company_id, created_by_user_id=1,
            current_step="SOURCE", status=status, source_type="MANUAL",
            selected_media_asset_ids_json="[]",
            materialized_listing_ids_json="[]",
            economics_input_json=(
                json.dumps(economics_items) if economics_items is not None
                else None
            ),
            creation_idempotency_key=_key("create"),
        )
        self.db.add(wizard)
        self.db.commit()
        return wizard

    _ECON_ITEM = {
        "marketplace_account_id": 1, "cost_of_goods": "5000",
        "sale_price": "1", "channel_fee_rate": "0.1",
        "payment_fee_rate": "0", "shipping_cost": "0",
        "packaging_cost": "0", "ad_cost": "0",
        "return_reserve_rate": "0", "tax_basis_rate": "0",
    }

    # ---------------- bulk_apply_margin_rate ----------------

    def test_requires_exactly_one_mode(self):
        with self.assertRaises(BadRequestException):
            self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=None, select_all_matching_filter=False,
                    target_margin_rate=Decimal("0.1"),
                ),
                self.company.id,
            )

    def test_select_all_requires_confirm_text(self):
        with self.assertRaises(BadRequestException):
            self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    select_all_matching_filter=True, confirm_text="틀림",
                    target_margin_rate=Decimal("0.1"),
                ),
                self.company.id,
            )

    def test_explicit_ids_over_max_count_rejected(self):
        with self.assertRaises(BadRequestException):
            self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=list(range(MAX_BULK_MARGIN_APPLY_COUNT + 1)),
                    target_margin_rate=Decimal("0.1"),
                ),
                self.company.id,
            )

    def test_missing_wizard_is_skipped_others_still_applied(self):
        good = self._seed_wizard(
            self.company.id, economics_items=[self._ECON_ITEM],
        )
        missing_id = 999999

        with mock.patch.object(
            self.service, "update_economics",
        ) as mocked_update:
            result = self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=[good.id, missing_id],
                    target_margin_rate=Decimal("0.1"),
                ),
                self.company.id,
            )

        self.assertEqual(result.requested_count, 2)
        self.assertEqual(result.succeeded, [good.id])
        skipped_ids = {item.wizard_id for item in result.skipped}
        self.assertEqual(skipped_ids, {missing_id})
        mocked_update.assert_called_once()
        called_wizard_id = mocked_update.call_args[0][0]
        self.assertEqual(called_wizard_id, good.id)

    def test_wizard_without_economics_input_is_skipped(self):
        empty = self._seed_wizard(self.company.id, economics_items=None)

        with mock.patch.object(self.service, "update_economics") as mocked:
            result = self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=[empty.id],
                    target_margin_rate=Decimal("0.1"),
                ),
                self.company.id,
            )

        self.assertEqual(result.succeeded, [])
        self.assertEqual(len(result.skipped), 1)
        mocked.assert_not_called()

    def test_infeasible_margin_is_skipped_not_failed(self):
        high_fee_item = dict(self._ECON_ITEM, channel_fee_rate="0.99")
        wizard = self._seed_wizard(
            self.company.id, economics_items=[high_fee_item],
        )

        with mock.patch.object(self.service, "update_economics") as mocked:
            result = self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=[wizard.id],
                    target_margin_rate=Decimal("0.5"),
                ),
                self.company.id,
            )

        self.assertEqual(result.succeeded, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.failed, [])
        mocked.assert_not_called()

    def test_one_conflict_does_not_block_other_wizards(self):
        first = self._seed_wizard(
            self.company.id, economics_items=[self._ECON_ITEM],
        )
        second = self._seed_wizard(
            self.company.id, economics_items=[self._ECON_ITEM],
        )

        def side_effect(wizard_id, company_id, data):
            if wizard_id == first.id:
                raise ConflictException("동시 편집 충돌")
            return None

        with mock.patch.object(
            self.service, "update_economics", side_effect=side_effect,
        ):
            result = self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=[first.id, second.id],
                    target_margin_rate=Decimal("0.1"),
                ),
                self.company.id,
            )

        self.assertEqual(result.succeeded, [second.id])
        self.assertEqual(
            {item.wizard_id for item in result.skipped}, {first.id},
        )
        self.assertEqual(result.failed, [])

    def test_unexpected_exception_is_reported_as_failed(self):
        wizard = self._seed_wizard(
            self.company.id, economics_items=[self._ECON_ITEM],
        )

        with mock.patch.object(
            self.service, "update_economics",
            side_effect=RuntimeError("예상치 못한 오류"),
        ):
            result = self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=[wizard.id],
                    target_margin_rate=Decimal("0.1"),
                ),
                self.company.id,
            )

        self.assertEqual(result.succeeded, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0].wizard_id, wizard.id)

    def test_derived_sale_price_is_passed_through_to_update_economics(self):
        wizard = self._seed_wizard(
            self.company.id, economics_items=[self._ECON_ITEM],
        )

        with mock.patch.object(self.service, "update_economics") as mocked:
            self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=[wizard.id],
                    target_margin_rate=Decimal("0.2"),
                ),
                self.company.id,
            )

        passed_request = mocked.call_args[0][2]
        self.assertEqual(len(passed_request.items), 1)
        expected = derive_sale_price_for_margin_rate(
            EconomicsInputItem(**self._ECON_ITEM), Decimal("0.2"),
        )
        self.assertEqual(passed_request.items[0].sale_price, expected)

    def test_company_isolation(self):
        other_company = self._seed_company("회사 B")
        wizard = self._seed_wizard(
            other_company.id, economics_items=[self._ECON_ITEM],
        )

        with mock.patch.object(self.service, "update_economics") as mocked:
            result = self.service.bulk_apply_margin_rate(
                WizardBulkMarginApplyRequest(
                    wizard_ids=[wizard.id],
                    target_margin_rate=Decimal("0.1"),
                ),
                self.company.id,
            )

        self.assertEqual(result.succeeded, [])
        self.assertEqual(len(result.skipped), 1)
        mocked.assert_not_called()

    # ---------------- bulk_submit ----------------

    def test_bulk_submit_over_max_count_rejected(self):
        with self.assertRaises(BadRequestException):
            self.service.bulk_submit(
                WizardBulkSubmitRequest(
                    wizard_ids=list(range(MAX_BULK_SUBMIT_COUNT + 1)),
                ),
                self.company.id, 1,
            )

    def test_bulk_submit_missing_wizard_is_skipped(self):
        good = self._seed_wizard(self.company.id, status=WizardStatus.APPROVED)
        missing_id = 999999

        with mock.patch.object(self.service, "submit") as mocked_submit:
            result = self.service.bulk_submit(
                WizardBulkSubmitRequest(wizard_ids=[good.id, missing_id]),
                self.company.id, 1,
            )

        self.assertEqual(result.succeeded, [good.id])
        self.assertEqual(
            {item.wizard_id for item in result.skipped}, {missing_id},
        )
        mocked_submit.assert_called_once()

    def test_bulk_submit_not_approved_is_skipped_not_failed(self):
        draft = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)

        with mock.patch.object(
            self.service, "submit",
            side_effect=ConflictException("승인(APPROVED)된 위저드만 제출할 수 있습니다."),
        ):
            result = self.service.bulk_submit(
                WizardBulkSubmitRequest(wizard_ids=[draft.id]),
                self.company.id, 1,
            )

        self.assertEqual(result.succeeded, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.failed, [])

    def test_bulk_submit_one_failure_does_not_block_others(self):
        first = self._seed_wizard(self.company.id, status=WizardStatus.APPROVED)
        second = self._seed_wizard(self.company.id, status=WizardStatus.APPROVED)

        def side_effect(wizard_id, company_id, approved_by, data):
            if wizard_id == first.id:
                raise RuntimeError("채널 호출 실패")
            return None

        with mock.patch.object(
            self.service, "submit", side_effect=side_effect,
        ):
            result = self.service.bulk_submit(
                WizardBulkSubmitRequest(wizard_ids=[first.id, second.id]),
                self.company.id, 1,
            )

        self.assertEqual(result.succeeded, [second.id])
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0].wizard_id, first.id)

    def test_bulk_submit_company_isolation(self):
        other_company = self._seed_company("회사 C")
        wizard = self._seed_wizard(
            other_company.id, status=WizardStatus.APPROVED,
        )

        with mock.patch.object(self.service, "submit") as mocked_submit:
            result = self.service.bulk_submit(
                WizardBulkSubmitRequest(wizard_ids=[wizard.id]),
                self.company.id, 1,
            )

        self.assertEqual(result.succeeded, [])
        self.assertEqual(len(result.skipped), 1)
        mocked_submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
