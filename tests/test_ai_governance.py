"""
=========================================================
Homez OS

File : tests/test_ai_governance.py

AI Capability Registry(CA-5, 2026-08-21 CTO 지시) 검증.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.migration_runner import MigrationRunner
from app.domains.ai_governance.capability_catalog import AI_CAPABILITY_CATALOG
from app.domains.ai_governance.capability_catalog import CapabilityContract
from app.domains.ai_governance.constants import AutomationLevel
from app.domains.ai_governance.constants import CapabilityType
from app.domains.ai_governance.service import InactiveCapabilityError
from app.domains.ai_governance.service import UnknownCapabilityError
from app.domains.ai_governance.service import get_capability
from app.domains.ai_governance.service import list_capabilities
from app.domains.ai_governance.service import require_active_capability
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.product_candidate.model import ProductCandidate

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = Path(REPO_ROOT) / "migrations"

_REQUIRED_TEXT_FIELDS = (
    "capability_code", "display_name", "purpose", "allowed_basis",
    "decision_criteria", "missing_data_handling", "user_approval_condition",
    "stop_condition", "model_prompt_policy_version",
    "implementation_reference",
)


class AICapabilityCatalogTestCase(unittest.TestCase):
    """DB 없이 카탈로그 자체의 완전성만 검증."""

    def test_catalog_covers_all_13_required_areas(self):

        expected = {
            "PRODUCT_DISCOVERY", "PRODUCT_ANALYSIS", "PRODUCT_SELECTION",
            "PROFITABILITY_CALCULATION", "CONTENT_GENERATION",
            "IMAGE_PROCESSING", "CHANNEL_POLICY_ASSIST",
            "SUPPLIER_RECOMMENDATION", "PRICING_INVENTORY",
            "ORDER_SHIPMENT_RETURN", "SETTLEMENT",
            "OPERATIONS_COORDINATION", "USER_GUIDANCE",
        }
        actual = {c.capability_code for c in AI_CAPABILITY_CATALOG}
        self.assertEqual(actual, expected)

    def test_no_duplicate_capability_codes(self):

        codes = [c.capability_code for c in AI_CAPABILITY_CATALOG]
        self.assertEqual(len(codes), len(set(codes)))

    def test_every_capability_has_all_required_text_fields_filled(self):

        for c in AI_CAPABILITY_CATALOG:
            for field_name in _REQUIRED_TEXT_FIELDS:
                value = getattr(c, field_name)
                self.assertTrue(
                    value and value.strip(),
                    f"{c.capability_code}.{field_name}가 비어 있습니다.",
                )

    def test_every_capability_type_is_one_of_the_allowed_types(self):
        """2026-09-07: EXTERNAL_DATA_PROVIDER 추가로 허용 유형이
        5종에서 6종으로 늘었다 — 이 테스트는 개수를 못박지 않고
        `CapabilityType.ALL`(단일 진실 공급원) 소속 여부만 검증하므로
        이름만 "다섯 가지"에서 일반화한다(동작 변경 없음)."""

        for c in AI_CAPABILITY_CATALOG:
            self.assertIn(c.capability_type, CapabilityType.ALL)

    def test_no_capability_exceeds_l3_automation(self):
        """실제 상품 제출·가격 변경·발주·환불·정산 확정은 AI가 직접
        수행하지 않는다 — 카탈로그 어떤 항목도 L3(승인 요청)을 넘어
        서는 자동화 수준을 자칭하지 않는다."""

        for c in AI_CAPABILITY_CATALOG:
            self.assertIn(c.max_automation_level, AutomationLevel.ALL)

    def test_fake_providers_never_declare_external_provider(self):
        """FAKE_PROVIDER 유형은 정의상 실제 외부 Provider를 호출하지
        않는다 — external_provider 필드가 채워져 있으면 자기모순."""

        for c in AI_CAPABILITY_CATALOG:
            if c.capability_type == CapabilityType.FAKE_PROVIDER:
                self.assertIsNone(
                    c.external_provider,
                    f"{c.capability_code}는 FAKE_PROVIDER인데 "
                    f"external_provider={c.external_provider!r}이 채워져 있음.",
                )

    def test_no_capability_currently_claims_generative_ai_or_predictive_model(self):
        """2026-08-21 기준 이 코드베이스에는 실제 LLM/외부 AI 호출이
        전혀 없다(Explore 조사로 확인) — 카탈로그가 그 사실과 다르게
        GENERATIVE_AI/PREDICTIVE_MODEL을 자칭하면 과장 보고다."""

        for c in AI_CAPABILITY_CATALOG:
            self.assertNotIn(
                c.capability_type,
                (CapabilityType.GENERATIVE_AI, CapabilityType.PREDICTIVE_MODEL),
                f"{c.capability_code}가 아직 존재하지 않는 실제 AI 연동을 "
                "자칭하고 있습니다.",
            )

    # --------------------------------------------------
    # AG-2(2026-08-21) — 확장된 상세 역할 계약 필드
    # --------------------------------------------------

    # Gate AI-F1(2026-08-22)에서 PRICING_INVENTORY, Gate AI-F2
    # (2026-08-22)에서 ORDER_SHIPMENT_RETURN·SETTLEMENT·OPERATIONS_
    # COORDINATION·USER_GUIDANCE의 실제 로직이 구현됐다 — 13개
    # capability 전부 실제 코드 경로가 연결된 상태다. 이 집합은 비워
    # 두되, 앞으로 새 capability가 "역할 계약만 등록, 실제 로직 없음"
    # 상태로 추가되면 그 코드를 여기 넣는다.
    _NOT_YET_CONNECTED_CODES = set()

    def test_every_capability_has_display_name_en_and_provider_status(self):

        for c in AI_CAPABILITY_CATALOG:
            self.assertTrue(
                c.display_name_en and c.display_name_en.strip(),
                f"{c.capability_code}.display_name_en이 비어 있습니다.",
            )
            self.assertTrue(
                c.provider_status and c.provider_status.strip(),
                f"{c.capability_code}.provider_status가 비어 있습니다.",
            )

    def test_every_capability_has_responsibilities_and_output_contract_or_is_honestly_unimplemented(
        self,
    ):
        """구현된 13개 전부 output_contract가 비어 있으면 안 된다(실제로
        무슨 결과 유형을 내는지 표시해야 한다). _NOT_YET_CONNECTED_CODES가
        비어 있는 지금은 전부 이 분기를 통과해야 하며, 앞으로 미구현
        capability가 추가되면 그 경우에만 output_contract가 비어 있는
        것 자체가 정직한 표시가 된다."""

        for c in AI_CAPABILITY_CATALOG:
            if c.capability_code in self._NOT_YET_CONNECTED_CODES:
                self.assertEqual(
                    c.output_contract, (),
                    f"{c.capability_code}는 미구현 상태인데 "
                    "output_contract가 채워져 있습니다 — 과장 보고입니다.",
                )
            else:
                self.assertTrue(
                    c.output_contract,
                    f"{c.capability_code}.output_contract가 비어 있습니다.",
                )
                self.assertTrue(
                    c.responsibilities,
                    f"{c.capability_code}.responsibilities가 비어 있습니다.",
                )

    def test_not_yet_connected_capabilities_have_no_actual_entry_points(self):
        """Gate AI-F1~F2(2026-08-22)로 13개 capability 전부 실제
        actual_entry_points가 채워졌다 — _NOT_YET_CONNECTED_CODES가
        비어 있으므로 이 테스트는 지금 else 분기(모두 채워져 있어야
        함)만 검증한다. 앞으로 새 capability가 "역할 계약만 등록,
        실제 로직 없음" 상태로 추가되면 그 코드를
        _NOT_YET_CONNECTED_CODES에 넣어 actual_entry_points=() 를
        정직하게 요구해야 한다."""

        for c in AI_CAPABILITY_CATALOG:
            if c.capability_code in self._NOT_YET_CONNECTED_CODES:
                self.assertEqual(
                    c.actual_entry_points, (),
                    f"{c.capability_code}는 미구현으로 표시돼 있는데 "
                    "actual_entry_points가 채워져 있습니다 — 카탈로그와 "
                    "실제 연결 상태가 불일치합니다.",
                )
                self.assertTrue(
                    c.unimplemented_dependencies,
                    f"{c.capability_code}.unimplemented_dependencies가 "
                    "비어 있습니다(미구현 사유를 밝혀야 합니다).",
                )
            else:
                self.assertTrue(
                    c.actual_entry_points,
                    f"{c.capability_code}.actual_entry_points가 비어 "
                    "있습니다 — 실제로 연결된 곳이 있어야 합니다.",
                )


class AICapabilityRegistryServiceTestCase(unittest.TestCase):

    def test_require_active_capability_returns_contract_for_known_active_code(self):

        contract = require_active_capability("CHANNEL_POLICY_ASSIST")
        self.assertIsInstance(contract, CapabilityContract)
        self.assertEqual(contract.capability_code, "CHANNEL_POLICY_ASSIST")

    def test_require_active_capability_blocks_unknown_code(self):

        with self.assertRaises(UnknownCapabilityError):
            require_active_capability("NOT_A_REAL_CAPABILITY")

    def test_require_active_capability_blocks_inactive_code(self):

        # 카탈로그에 비활성 항목이 실제로 있는지 확인 후, 없으면
        # 임시로 하나 비활성 처리해 차단 동작만 검증(카탈로그 원본은
        # 건드리지 않는다 — 로컬 사본으로 검증).
        from dataclasses import replace

        contract = get_capability("USER_GUIDANCE")
        inactive = replace(contract, active=False)

        import app.domains.ai_governance.service as svc_module
        original = svc_module._CATALOG_BY_CODE["USER_GUIDANCE"]
        svc_module._CATALOG_BY_CODE["USER_GUIDANCE"] = inactive
        try:
            with self.assertRaises(InactiveCapabilityError):
                require_active_capability("USER_GUIDANCE")
        finally:
            svc_module._CATALOG_BY_CODE["USER_GUIDANCE"] = original

    def test_list_capabilities_returns_all_catalog_entries(self):

        self.assertEqual(len(list_capabilities()), len(AI_CAPABILITY_CATALOG))


class AIGovernanceChannelPolicyIntegrationTestCase(unittest.TestCase):
    """CA-5가 실제로 강제되는 통합 지점 — ChannelPolicyService가
    호출 시마다 require_active_capability("CHANNEL_POLICY_ASSIST")를
    거친다는 것을 실제 DB로 증명한다."""

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
        self.service = ChannelPolicyService(self.db)
        self.service.seed_rule_catalog()

        candidate = ProductCandidate(
            candidate_key="ai-gov-1", source_type="TREND",
            source_reference="r1", market="FAKE",
            product_name="무선 이어폰", category_hint="전자제품",
        )
        self.db.add(candidate)
        self.db.commit()
        self.candidate_id = candidate.id

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            os.remove(self.db_path)

    def test_evaluate_and_record_blocks_when_capability_deactivated(self):

        import app.domains.ai_governance.service as svc_module
        from dataclasses import replace

        original = svc_module._CATALOG_BY_CODE["CHANNEL_POLICY_ASSIST"]
        svc_module._CATALOG_BY_CODE["CHANNEL_POLICY_ASSIST"] = replace(
            original, active=False,
        )
        try:
            with self.assertRaises(InactiveCapabilityError):
                self.service.evaluate_and_record(
                    company_id=1, product_candidate_id=self.candidate_id,
                    channel="COUPANG", category_hint_override=None,
                    product_attributes={}, confirmed_evidence_rule_codes=[],
                    evaluated_by=1,
                )
        finally:
            svc_module._CATALOG_BY_CODE["CHANNEL_POLICY_ASSIST"] = original

    def test_evaluate_and_record_succeeds_when_capability_active(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_id,
            channel="COUPANG", category_hint_override=None,
            product_attributes={}, confirmed_evidence_rule_codes=[],
            evaluated_by=1,
        )
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
