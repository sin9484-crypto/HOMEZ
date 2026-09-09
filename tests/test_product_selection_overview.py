"""
=========================================================
Homez OS

File : tests/test_product_selection_overview.py

상품 선별 통합 화면(CA-2, 2026-08-21 CTO 지시) 검증.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.migration_runner import MigrationRunner
from app.domains.channel_policy.schema import UpdateCompanyChannelPolicySettingsRequest
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_selection.constants import SelectionItemStatus
from app.domains.product_selection.constants import SelectionStepCode
from app.domains.product_selection.schema import ProfitabilityQuery
from app.domains.product_selection.service import ProductSelectionService
from app.domains.source.model import SupplierProductLink

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = Path(REPO_ROOT) / "migrations"


class ProductSelectionOverviewTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)

        conn = sqlite3.connect(str(self.db_path))
        try:
            runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)
            runner.ensure_history_table(conn)
            runner.apply_pending(conn)
        finally:
            conn.close()

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()
        self.service = ProductSelectionService(self.db)
        ChannelPolicyService(self.db).seed_rule_catalog()

        self.candidate = ProductCandidate(
            candidate_key="ps-1", source_type="TREND", source_reference="r1",
            market="FAKE", product_name="무선 이어폰", category_hint="전자제품",
        )
        self.db.add(self.candidate)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            os.remove(self.db_path)

    def test_all_dimensions_data_required_on_bare_candidate(self):

        result = self.service.get_overview(
            company_id=1, product_candidate_id=self.candidate.id,
            channel=None, profitability_query=None,
        )
        by_code = {s.step_code: s for s in result.steps}

        self.assertEqual(
            by_code[SelectionStepCode.CHANNEL_POLICY].status,
            SelectionItemStatus.DATA_REQUIRED,
        )
        self.assertEqual(
            by_code[SelectionStepCode.RESALE_RIGHTS].status,
            SelectionItemStatus.DATA_REQUIRED,
        )
        self.assertEqual(
            by_code[SelectionStepCode.IMAGE_RIGHTS].status,
            SelectionItemStatus.DATA_REQUIRED,
        )
        self.assertEqual(
            by_code[SelectionStepCode.SUPPLIER_EVIDENCE].status,
            SelectionItemStatus.DATA_REQUIRED,
        )
        self.assertEqual(
            by_code[SelectionStepCode.PROFITABILITY_RISK].status,
            SelectionItemStatus.DATA_REQUIRED,
        )
        self.assertFalse(result.policy_blocked)
        self.assertIsNone(result.profitability_meets_target)

    def test_policy_blocked_flag_is_independent_of_profitability(self):
        """정책 차단과 경제성 미달을 절대 같은 축으로 섞지 않는다 —
        정책이 BLOCKED여도 수익성은 별도로 PASS일 수 있다."""

        liquor = ProductCandidate(
            candidate_key="ps-liquor", source_type="TREND",
            source_reference="r2", market="FAKE",
            product_name="위스키 선물세트", category_hint="주류",
        )
        self.db.add(liquor)
        self.db.commit()

        self.service.channel_policy_service.evaluate_and_record(
            company_id=1, product_candidate_id=liquor.id, channel="COUPANG",
            category_hint_override=None,
            # CA-3 — 공식 카테고리 코드가 있어야만 확정 BLOCKED 판정이
            # 나온다(자유 텍스트 힌트만으로는 DATA_REQUIRED에 그친다).
            product_attributes={"official_category_code": "COUPANG-LIQUOR-1"},
            confirmed_evidence_rule_codes=[], evaluated_by=1,
        )

        result = self.service.get_overview(
            company_id=1, product_candidate_id=liquor.id, channel="COUPANG",
            profitability_query=ProfitabilityQuery(
                sale_price=Decimal("100000"), cost_of_goods=Decimal("10000"),
            ),
        )
        self.assertTrue(result.policy_blocked)
        by_code = {s.step_code: s for s in result.steps}
        self.assertEqual(
            by_code[SelectionStepCode.CHANNEL_POLICY].status,
            SelectionItemStatus.BLOCKED,
        )
        # 수익성 자체는 정책과 무관하게 독립적으로 계산된다(잠정 판정
        # 이더라도 정책 BLOCKED에 의해 강제로 BLOCKED가 되지 않는다).
        self.assertNotEqual(
            by_code[SelectionStepCode.PROFITABILITY_RISK].status,
            SelectionItemStatus.BLOCKED,
        )

    def test_supplier_link_present_marks_evidence_and_moq_steps(self):

        link = SupplierProductLink(
            company_id=1, product_candidate_id=self.candidate.id,
            supplier_id=1, supplier_sku="SKU-1", unit_cost=1000, moq=10,
            lead_time_days=5, shipping_cost=2000, stock_available=100,
            return_policy="7일 이내 반품 가능", created_by=1,
        )
        self.db.add(link)
        self.db.commit()

        result = self.service.get_overview(
            company_id=1, product_candidate_id=self.candidate.id,
            channel=None, profitability_query=None,
        )
        by_code = {s.step_code: s for s in result.steps}
        self.assertEqual(
            by_code[SelectionStepCode.SUPPLIER_EVIDENCE].status,
            SelectionItemStatus.PASS,
        )
        self.assertEqual(
            by_code[SelectionStepCode.INVENTORY_MOQ_LEAD_TIME].status,
            SelectionItemStatus.PASS,
        )
        self.assertEqual(
            by_code[SelectionStepCode.SHIPPING_RETURN_FEASIBILITY].status,
            SelectionItemStatus.PASS,
        )

    def test_provisional_margin_is_data_required_not_zero_filled(self):

        result = self.service.get_overview(
            company_id=1, product_candidate_id=self.candidate.id,
            channel=None,
            profitability_query=ProfitabilityQuery(sale_price=Decimal("10000")),
        )
        by_code = {s.step_code: s for s in result.steps}
        step = by_code[SelectionStepCode.PROFITABILITY_RISK]
        self.assertEqual(step.status, SelectionItemStatus.DATA_REQUIRED)
        self.assertIn("cost_of_goods", step.detail_codes)

    def test_margin_below_company_target_is_needs_improvement(self):
        """ProfitabilityQuery는 sale_price/cost_of_goods만 받는다 —
        나머지 비용 필드(수수료 등)를 명시하지 않았으므로 결과는
        여전히 잠정(DATA_REQUIRED)이다. 그래도 meets_company_target
        자체는 실제 계산값 기준으로 정확히 채워져야 한다(0으로
        추정하지 않되, 계산 가능한 값은 숨기지 않는다)."""

        ChannelPolicyService(self.db).upsert_settings(
            1, updated_by=1,
            data=UpdateCompanyChannelPolicySettingsRequest(
                expected_version=0, min_target_margin_rate="0.9",
            ),
        )
        result = self.service.get_overview(
            company_id=1, product_candidate_id=self.candidate.id,
            channel=None,
            profitability_query=ProfitabilityQuery(
                sale_price=Decimal("10000"), cost_of_goods=Decimal("9500"),
            ),
        )
        by_code = {s.step_code: s for s in result.steps}
        self.assertEqual(
            by_code[SelectionStepCode.PROFITABILITY_RISK].status,
            SelectionItemStatus.DATA_REQUIRED,
        )
        self.assertFalse(result.profitability_meets_target)

    def test_user_final_approval_reflects_selection_status(self):

        selection = ProductCandidateSelection(
            candidate_id=self.candidate.id, company_id=1, status="APPROVED",
        )
        self.db.add(selection)
        self.db.commit()

        result = self.service.get_overview(
            company_id=1, product_candidate_id=self.candidate.id,
            channel=None, profitability_query=None,
        )
        by_code = {s.step_code: s for s in result.steps}
        self.assertEqual(
            by_code[SelectionStepCode.USER_FINAL_APPROVAL].status,
            SelectionItemStatus.PASS,
        )

    def test_all_nine_steps_always_present_in_fixed_order(self):

        result = self.service.get_overview(
            company_id=1, product_candidate_id=self.candidate.id,
            channel=None, profitability_query=None,
        )
        codes = [s.step_code for s in result.steps]
        self.assertEqual(codes, list(SelectionStepCode.ORDERED))


if __name__ == "__main__":
    unittest.main()
