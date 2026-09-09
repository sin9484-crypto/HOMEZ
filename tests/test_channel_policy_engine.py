"""
=========================================================
Homez OS

File : tests/test_channel_policy_engine.py

채널 정책 엔진(CP-2, 2026-08-21 CTO 지시) 검증. 반드시
MigrationRunner로 실제 migrations/*.sql을 순서대로 적용해 DB를
만든다(test_supplier_legacy_schema_upgrade.py와 동일한 원칙) —
create_all()은 쓰지 않는다.
=========================================================
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ConflictException
from app.database.migration_runner import MigrationRunner
from app.domains.channel_policy.constants import ChannelPolicyResult
from app.domains.channel_policy.engine import evaluate_policy
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.marketplace_listing.category_metadata import notice_input_fingerprint
from app.domains.channel_policy.schema import MarginEstimateInput
from app.domains.channel_policy.schema import (
    UpdateCompanyChannelPolicySettingsRequest,
)
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.product_candidate.model import ProductCandidate

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = Path(REPO_ROOT) / "migrations"


class ChannelPolicyEngineTestCase(unittest.TestCase):
    """실제 migrations/를 전부 적용한 임시 DB 위에서 서비스 계층
    전체(엔진 + DB 저장 + 재조회)를 검증한다."""

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

        self.candidate_liquor = self._seed_candidate(
            "k-liquor", "프리미엄 위스키 선물세트", "주류",
        )
        self.candidate_earbuds = self._seed_candidate(
            "k-earbuds", "무선 이어폰", "전자제품",
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            os.remove(self.db_path)

    def _seed_candidate(
        self, key: str, name: str, category_hint: str,
    ) -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=key, source_type="TREND", source_reference=key,
            market="FAKE", product_name=name, category_hint=category_hint,
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    _COMPLETE_ATTRS = {
        "origin_country": "KOREA",
        "brand": "홈즈",
        "purchase_options": {"color": "black"},
        "product_identifier": "BARCODE123",
        # CA-3 — 카테고리 확정 판정(위반/통과)에는 공식 카테고리 코드가
        # 있어야 한다(자유 텍스트 category_hint만으로 확정하지 않는다).
        "official_category_code": "99999",
        "category_metadata_version": "test-meta-v1",
        "category_metadata_fingerprint": "test-meta-fingerprint",
        "notice_information": {"기타 재화::품명 및 모델명": "테스트 상품"},
        "notice_required_field_keys": ["기타 재화::품명 및 모델명"],
        "notice_confirmed_at": "2026-08-24T00:00:00",
        "notice_confirmed_by_user_id": 1,
        "notice_input_fingerprint": notice_input_fingerprint(
            "99999", "test-meta-v1", "test-meta-fingerprint",
            {"기타 재화::품명 및 모델명": "테스트 상품"},
        ),
    }

    # --------------------------------------------------
    # 1. 규칙 카탈로그 시딩·근거
    # --------------------------------------------------

    def test_seed_creates_only_evidence_backed_rules_as_active(self):

        rules = (
            self.db.query(ChannelPolicyRule)
            .filter(ChannelPolicyRule.active.is_(True))
            .all()
        )
        for rule in rules:
            self.assertIsNotNone(
                rule.official_source_url,
                f"{rule.rule_code}가 active인데 공식 출처가 없음",
            )
            self.assertIsNotNone(rule.verified_at)

    def test_naver_placeholder_rules_stay_inactive_policy_evidence_required(self):

        placeholder = (
            self.db.query(ChannelPolicyRule)
            .filter(
                ChannelPolicyRule.channel == "NAVER_SMARTSTORE",
                ChannelPolicyRule.rule_code
                == "NAVER_PROHIBITED_CATEGORY_PLACEHOLDER",
            )
            .one()
        )
        self.assertFalse(placeholder.active)
        self.assertIn("POLICY_EVIDENCE_REQUIRED", placeholder.source_title)
        self.assertIsNone(placeholder.official_source_url)

    def test_reseeding_is_idempotent_no_duplicate_rows(self):

        before = self.db.query(ChannelPolicyRule).count()
        self.service.seed_rule_catalog()
        after = self.db.query(ChannelPolicyRule).count()
        self.assertEqual(before, after)

    # --------------------------------------------------
    # 2. 채널 선택 자동 활성화 / 복수 채널 독립 평가
    # --------------------------------------------------

    def test_evaluate_is_channel_specific_not_shared(self):

        coupang_result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )
        naver_result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="NAVER_SMARTSTORE", category_hint_override=None,
            product_attributes={
                "deliveryType": "DELIVERY",
                "deliveryAttributeType": "NORMAL",
            },
            confirmed_evidence_rule_codes=[],
            evaluated_by=1,
        )

        self.assertEqual(coupang_result.result, ChannelPolicyResult.CHANNEL_ELIGIBLE)
        self.assertEqual(naver_result.result, ChannelPolicyResult.CHANNEL_ELIGIBLE)
        # 서로 다른 규칙 집합을 평가했다는 증거 — rule_code 교집합이 없다.
        coupang_codes = {r.rule_code for r in coupang_result.rule_results}
        naver_codes = {r.rule_code for r in naver_result.rule_results}
        self.assertEqual(coupang_codes & naver_codes, set())

    # --------------------------------------------------
    # 3. BLOCK 우회 불가
    # --------------------------------------------------

    def test_absolute_prohibited_category_blocks_regardless_of_evidence(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_liquor.id,
            channel="COUPANG", category_hint_override=None,
            # 모든 구조화 필드와 증빙을 다 채워도 절대 금지 카테고리는
            # 통과할 수 없다.
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=[
                "CATEGORY_NOTICE_INFO_REQUIRED",
                "RESTRICTED_CATEGORY_CERTIFICATION_REQUIRED",
            ],
            evaluated_by=1,
        )
        self.assertEqual(result.result, ChannelPolicyResult.CHANNEL_POLICY_BLOCKED)

        blocked_rule = next(
            r for r in result.rule_results
            if r.rule_code == "PROHIBITED_CATEGORY_ABSOLUTE"
        )
        self.assertTrue(blocked_rule.applies)
        self.assertFalse(blocked_rule.satisfied)

    def test_category_scope_all_evidence_toggle_cannot_bypass_evaluation(self):
        """'작성 중 정책 알림 표시' 토글은 화면 표시만 제어할 뿐,
        엔진 자체를 우회하는 파라미터가 존재하지 않는다는 것을
        구조적으로 증명한다 — evaluate_and_record는 confirmed_evidence
        _rule_codes를 아무리 많이 넘겨도 CATEGORY_PROHIBITED류 규칙은
        satisfied로 만들 수 없다(evidence 확인 대상이 아니기 때문)."""

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_liquor.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["PROHIBITED_CATEGORY_ABSOLUTE"],
            evaluated_by=1,
        )
        self.assertEqual(result.result, ChannelPolicyResult.CHANNEL_POLICY_BLOCKED)

    def test_category_keyword_match_without_official_code_is_data_required_not_blocked(self):
        """CA-3(2026-08-21 CTO 지시) — category_hint는 AI 자유 텍스트
        힌트일 뿐이다. 공식 카테고리 코드 없이 키워드 매치만으로는
        절대 판매금지를 확정하지 않는다 — '자료 필요'로만 표시한다."""

        attrs_without_code = dict(self._COMPLETE_ATTRS)
        del attrs_without_code["official_category_code"]

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_liquor.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=attrs_without_code,
            confirmed_evidence_rule_codes=[
                "CATEGORY_NOTICE_INFO_REQUIRED",
                "RESTRICTED_CATEGORY_CERTIFICATION_REQUIRED",
            ],
            evaluated_by=1,
        )
        self.assertEqual(result.result, ChannelPolicyResult.CHANNEL_DATA_REQUIRED)

        rule = next(
            r for r in result.rule_results
            if r.rule_code == "PROHIBITED_CATEGORY_ABSOLUTE"
        )
        self.assertTrue(rule.applies)
        self.assertFalse(rule.satisfied)
        self.assertIn("official_category_code", rule.missing_fields)

    def test_category_scope_all_rules_never_require_official_code(self):
        """category_scope=["ALL"]인 규칙(예: 상품식별정보 필수)은
        카테고리 판단이 아니므로 공식 카테고리 코드와 무관하게 정상
        평가된다."""

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes={
                "brand": "홈즈", "purchase_options": {"color": "black"},
                "product_identifier": "BARCODE123",
            },
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )
        rule = next(
            r for r in result.rule_results
            if r.rule_code == "PRODUCT_IDENTIFICATION_REQUIRED"
        )
        self.assertTrue(rule.satisfied)

    # --------------------------------------------------
    # 4. 데이터 부족 vs 정책 위반 구분
    # --------------------------------------------------

    def test_missing_structural_data_is_data_required_not_blocked(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes={}, confirmed_evidence_rule_codes=[],
            evaluated_by=1,
        )
        self.assertEqual(result.result, ChannelPolicyResult.CHANNEL_DATA_REQUIRED)

    def test_missing_only_evidence_is_eligible_with_actions(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=[],  # 상품정보제공고시 미확인
            evaluated_by=1,
        )
        self.assertEqual(
            result.result, ChannelPolicyResult.CHANNEL_ELIGIBLE_WITH_ACTIONS,
        )

    # --------------------------------------------------
    # 5. STALE 처리 / 재평가
    # --------------------------------------------------

    def test_status_without_reevaluation_reflects_stale_when_profile_changes(self):

        self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )

        fresh_status = self.service.get_current_status(
            1, self.candidate_earbuds.id, "COUPANG",
        )
        self.assertFalse(fresh_status.is_stale)
        self.assertEqual(fresh_status.result, ChannelPolicyResult.CHANNEL_ELIGIBLE)

        # 정책 프로필 버전을 올린다(실제 규칙 변경을 흉내).
        rule = (
            self.db.query(ChannelPolicyRule)
            .filter(ChannelPolicyRule.channel == "COUPANG")
            .first()
        )
        for r in self.db.query(ChannelPolicyRule).filter(
            ChannelPolicyRule.channel == "COUPANG",
        ):
            r.profile_version = "2099.01.01.1"
        self.db.commit()

        stale_status = self.service.get_current_status(
            1, self.candidate_earbuds.id, "COUPANG",
        )
        self.assertTrue(stale_status.is_stale)
        self.assertEqual(
            stale_status.result, ChannelPolicyResult.CHANNEL_POLICY_STALE,
        )

    def test_reevaluation_after_policy_change_produces_new_snapshot_row(self):

        first = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )
        second = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes={}, confirmed_evidence_rule_codes=[],
            evaluated_by=1,
        )
        self.assertNotEqual(first.id, second.id)
        history = self.service.repository.list_evaluation_history(
            1, self.candidate_earbuds.id, "COUPANG",
        )
        self.assertEqual(len(history), 2)

    # --------------------------------------------------
    # 6. 정책 스냅샷 재현
    # --------------------------------------------------

    def test_evaluation_snapshot_is_reproducible_from_stored_json(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_liquor.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=[], evaluated_by=1,
        )

        stored = self.service.repository.get_latest_evaluation(
            1, self.candidate_liquor.id, "COUPANG",
        )
        raw = json.loads(stored.rule_results_json)
        self.assertEqual(len(raw), len(result.rule_results))
        codes_in_snapshot = {item["rule_code"] for item in raw}
        codes_in_response = {r.rule_code for r in result.rule_results}
        self.assertEqual(codes_in_snapshot, codes_in_response)

    # --------------------------------------------------
    # 7. 회사(테넌트) 격리
    # --------------------------------------------------

    def test_evaluations_are_isolated_per_company(self):

        self.service.evaluate_and_record(
            company_id=100, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )

        company_b_status = self.service.get_current_status(
            200, self.candidate_earbuds.id, "COUPANG",
        )
        self.assertIsNone(company_b_status)

    def test_settings_are_isolated_per_company(self):

        self.service.upsert_settings(
            100, updated_by=1,
            data=UpdateCompanyChannelPolicySettingsRequest(
                expected_version=0, min_target_margin_rate="0.15",
            ),
        )
        settings_b = self.service.get_or_default_settings(200)
        self.assertIsNone(settings_b)

    # --------------------------------------------------
    # 8. 회사 설정 — 낙관적 동시성
    # --------------------------------------------------

    def test_settings_update_rejects_stale_version(self):

        self.service.upsert_settings(
            1, updated_by=1,
            data=UpdateCompanyChannelPolicySettingsRequest(
                expected_version=0, min_target_margin_rate="0.10",
            ),
        )
        with self.assertRaises(ConflictException):
            self.service.upsert_settings(
                1, updated_by=1,
                data=UpdateCompanyChannelPolicySettingsRequest(
                    expected_version=0, min_target_margin_rate="0.20",
                ),
            )

    # --------------------------------------------------
    # 9. 정책 통과 전 수익성 추천 차단(정책·수익성 분리 증명)
    # --------------------------------------------------

    def test_margin_estimate_never_considers_policy_result(self):
        """엔진 서명 자체에 정책 관련 인자가 없다는 것을 직접 증명 —
        절대 금지 카테고리 상품이라도 margin_estimate는 정상적으로
        높은 마진을 계산해 돌려준다(수익성 계산 자체는 정책과 무관).
        정책 BLOCK 여부로 "추천"할지 말지 결정하는 것은 이 결과를
        소비하는 상위 화면/서비스의 책임이지, 이 두 함수가 서로의
        존재를 알아서는 안 된다는 설계를 그대로 검증한다."""

        margin = self.service.estimate_margin(
            1, MarginEstimateInput(sale_price=100000, cost_of_goods=10000),
        )
        self.assertGreater(margin.margin_amount, 0)
        self.assertTrue(margin.is_provisional)

    def test_missing_costs_produce_provisional_result_never_zero_filled_silently(self):

        margin = self.service.estimate_margin(
            1, MarginEstimateInput(sale_price=50000),
        )
        self.assertTrue(margin.is_provisional)
        self.assertIn("cost_of_goods", margin.missing_cost_fields)

    def test_fully_specified_costs_are_not_provisional(self):

        margin = self.service.estimate_margin(
            1,
            MarginEstimateInput(
                sale_price=50000, cost_of_goods=20000,
                channel_fee_rate="0.1", payment_fee_rate="0.03",
                shipping_cost=3000, packaging_cost=500, ad_cost=1000,
                return_reserve_rate="0.02", tax_basis_rate="0.1",
            ),
        )
        self.assertFalse(margin.is_provisional)
        self.assertEqual(margin.missing_cost_fields, [])

    def test_margin_meets_company_target_uses_settings(self):

        self.service.upsert_settings(
            1, updated_by=1,
            data=UpdateCompanyChannelPolicySettingsRequest(
                expected_version=0, min_target_margin_rate="0.90",
            ),
        )
        margin = self.service.estimate_margin(
            1,
            MarginEstimateInput(
                sale_price=10000, cost_of_goods=9000,
                channel_fee_rate="0", payment_fee_rate="0",
                shipping_cost=0, packaging_cost=0, ad_cost=0,
                return_reserve_rate="0", tax_basis_rate="0",
            ),
        )
        self.assertFalse(margin.is_provisional)
        self.assertFalse(margin.meets_company_target)
        self.assertEqual(str(margin.company_target_margin_rate), "0.9000")

    def test_margin_target_none_when_settings_not_configured(self):

        margin = self.service.estimate_margin(
            999, MarginEstimateInput(sale_price=10000, cost_of_goods=1000),
        )
        self.assertIsNone(margin.meets_company_target)
        self.assertIsNone(margin.company_target_margin_rate)

    def test_estimate_margin_blocks_when_profitability_capability_deactivated(self):

        from app.domains.ai_governance.service import InactiveCapabilityError
        from tests.ai_governance_test_helpers import deactivated_capability

        with deactivated_capability("PROFITABILITY_CALCULATION"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.estimate_margin(
                    999,
                    MarginEstimateInput(sale_price=10000, cost_of_goods=1000),
                )

    # --------------------------------------------------
    # Audit(2026-08-21, CTO 후속 지시) — 카탈로그 미시딩 fail-closed
    # --------------------------------------------------

    def test_unseeded_channel_never_returns_eligible(self):
        """setUp()이 COUPANG/NAVER_SMARTSTORE는 시딩했지만, 카탈로그에
        없는 채널 코드는 규칙 행이 0건이다 — 이 상태에서 완전한
        product_attributes를 넘겨도 절대 CHANNEL_ELIGIBLE이 나오면
        안 된다(신규 설치·업그레이드에서 시딩이 누락된 상황과 동일)."""

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="FUTURE_CHANNEL_NEVER_SEEDED",
            category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["ANY_CODE"],
            evaluated_by=1,
        )

        self.assertEqual(
            result.result, ChannelPolicyResult.CHANNEL_DATA_REQUIRED,
        )
        self.assertNotIn(
            result.result, ChannelPolicyResult.SUBMITTABLE,
        )
        self.assertTrue(any(
            r.rule_code == "CHANNEL_POLICY_CATALOG_NOT_SEEDED"
            for r in result.rule_results
        ))

    def test_unseeded_channel_blocks_submission_gate(self):

        from app.domains.marketplace_listing.model import (
            MarketplaceFulfillmentSelection,
        )

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="FUTURE_CHANNEL_NEVER_SEEDED",
            category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=[],
            evaluated_by=1,
            selection_id=None,
        )
        self.assertEqual(
            result.result, ChannelPolicyResult.CHANNEL_DATA_REQUIRED,
        )

        valid = self.service.current_valid_channel_policy(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="FUTURE_CHANNEL_NEVER_SEEDED",
            selection=None,
        )
        self.assertIsNone(valid)

    def test_channel_with_all_rules_explicitly_inactive_is_still_eligible(self):
        """관리자가 이 채널의 모든 규칙을 명시적으로 비활성화한 상태
        (행은 존재, active=False 전부)는 "미시딩"과 다르다 — 정책
        제약이 의도적으로 없는 상태이므로 여전히 CHANNEL_ELIGIBLE이어야
        한다(과도하게 막지 않는다)."""

        self.db.query(ChannelPolicyRule).filter(
            ChannelPolicyRule.channel == "COUPANG",
        ).update({"active": False})
        self.db.commit()

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes={}, confirmed_evidence_rule_codes=[],
            evaluated_by=1,
        )

        self.assertEqual(result.result, ChannelPolicyResult.CHANNEL_ELIGIBLE)

    def test_latest_evaluation_ordering_is_deterministic_by_id_not_created_at(self):
        """created_at만으로 정렬하면 동일 밀리초 커밋 시 비결정적이다
        — id.desc()로 통일했는지 실제 DB 행을 조작해 확인한다."""

        first = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )
        second = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )
        # 두 행의 created_at을 동일하게 맞춰(동시 커밋을 흉내) id만이
        # 유일한 결정 기준이 되게 한다.
        from app.domains.channel_policy.model import ChannelPolicyEvaluation
        rows = self.db.query(ChannelPolicyEvaluation).filter(
            ChannelPolicyEvaluation.id.in_([first.id, second.id]),
        ).all()
        same_ts = rows[0].created_at
        for row in rows:
            row.created_at = same_ts
        self.db.commit()

        latest = self.service.repository.get_latest_evaluation(
            1, self.candidate_earbuds.id, "COUPANG",
        )
        self.assertEqual(latest.id, second.id)

    # --------------------------------------------------
    # Audit(2026-08-21, CTO 후속 지시) — 공통 AI 결과 계약(ai_result)
    # --------------------------------------------------

    def test_ai_result_envelope_reflects_eligible_as_executable(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )

        self.assertIsNotNone(result.ai_result)
        self.assertEqual(result.ai_result.decision, "CHANNEL_ELIGIBLE")
        self.assertEqual(result.ai_result.result_type, "CALCULATED_RESULT")
        self.assertTrue(result.ai_result.execution_allowed)
        self.assertEqual(
            result.ai_result.capability_code, "CHANNEL_POLICY_ASSIST",
        )
        self.assertEqual(
            result.ai_result.policy_version, result.policy_profile_version,
        )
        self.assertIsNotNone(result.ai_result.evaluated_at)

    def test_ai_result_envelope_reflects_blocked_as_not_executable(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_liquor.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=[],
            evaluated_by=1,
        )

        self.assertEqual(result.result, ChannelPolicyResult.CHANNEL_POLICY_BLOCKED)
        self.assertEqual(result.ai_result.result_type, "POLICY_BLOCKED")
        self.assertFalse(result.ai_result.execution_allowed)
        self.assertIn(
            "PROHIBITED_CATEGORY_ABSOLUTE", result.ai_result.blocking_rules,
        )

    def test_ai_result_envelope_reflects_data_required_as_evidence_required(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes={}, confirmed_evidence_rule_codes=[],
            evaluated_by=1,
        )

        self.assertEqual(result.result, ChannelPolicyResult.CHANNEL_DATA_REQUIRED)
        self.assertEqual(result.ai_result.result_type, "EVIDENCE_REQUIRED")
        self.assertFalse(result.ai_result.execution_allowed)


class ChannelPolicyPureEngineTestCase(unittest.TestCase):
    """DB 없이 engine.evaluate_policy() 자체(순수 함수)만 검증 —
    margin_calculator.py와 동일한 스타일."""

    def _make_rule(self, **overrides) -> ChannelPolicyRule:

        defaults = dict(
            id=1, rule_code="TEST_RULE", channel="COUPANG",
            category_scope_json='["ALL"]',
            severity="BLOCKING",
            validation_type="STRUCTURAL_FIELD_REQUIRED",
            required_fields_json='["brand"]',
            required_evidence_json=None,
            official_source_url="https://example.test",
            source_title="test", profile_version="v1", active=True,
        )
        defaults.update(overrides)
        return ChannelPolicyRule(**defaults)

    def test_no_applicable_rules_yields_eligible(self):

        result, details = evaluate_policy(
            [], category_hint="전자제품", product_name="이어폰",
            product_attributes={}, confirmed_evidence_rule_codes=[],
        )
        self.assertEqual(result, ChannelPolicyResult.CHANNEL_ELIGIBLE)
        self.assertEqual(details, [])

    def test_category_scope_keyword_matching_is_case_insensitive_substring(self):

        rule = self._make_rule(
            category_scope_json='["Liquor"]',
            validation_type="CATEGORY_PROHIBITED",
        )
        # CA-3 — 공식 카테고리 코드 없이는 키워드 매치만으로 확정
        # 판단을 내리지 않는다(DATA_REQUIRED로만 표시).
        result, details = evaluate_policy(
            [rule], category_hint=None, product_name="Premium LIQUOR Gift Set",
            product_attributes={}, confirmed_evidence_rule_codes=[],
        )
        self.assertEqual(result, ChannelPolicyResult.CHANNEL_DATA_REQUIRED)
        self.assertTrue(details[0].applies)
        self.assertIn("official_category_code", details[0].missing_fields)

        # 공식 카테고리 코드가 있으면 확정 판단(BLOCKED)으로 넘어간다.
        result2, details2 = evaluate_policy(
            [rule], category_hint=None, product_name="Premium LIQUOR Gift Set",
            product_attributes={"official_category_code": "CAT-1"},
            confirmed_evidence_rule_codes=[],
        )
        self.assertEqual(result2, ChannelPolicyResult.CHANNEL_POLICY_BLOCKED)
        self.assertTrue(details2[0].applies)


class AuditLoggingTestCase(ChannelPolicyEngineTestCase):
    """2026-08-24 Section 2 — ChannelPolicyService의 카탈로그 시딩·
    평가·회사 설정 변경이 실제로 audit_logs에 남는지, 회사 간 격리가
    유지되는지 검증한다. setUp()이 이미 seed_rule_catalog()를
    한 번 호출하므로(actor_user_id=None, 부팅과 동일한 시스템 시딩)
    그 1건은 baseline으로 취급한다."""

    def _audit_rows(self, action: str | None = None):

        from sqlalchemy import text

        sql = (
            "SELECT company_id, user_id, action, entity, entity_id, "
            "description FROM audit_logs"
        )
        params = {}
        if action is not None:
            sql += " WHERE action = :action"
            params["action"] = action
        return self.db.execute(text(sql), params).fetchall()

    def test_setup_seeding_already_wrote_one_system_audit_row(self):

        rows = self._audit_rows("CHANNEL_POLICY_RULE_CATALOG_SEEDED")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].user_id)
        self.assertIsNone(rows[0].company_id)

    def test_reseed_with_no_actual_change_does_not_duplicate_audit_log(self):
        """2026-08-24 — 값이 전혀 바뀌지 않은 재시딩(정상적인 매 부팅
        재시도와 동일한 상황)은 감사로그를 새로 남기지 않는다 —
        실사용 중 재시작마다 운영 DB에 무의미한 행이 쌓이던 결함의
        재발 방지 계약."""

        for _ in range(10):
            self.service.seed_rule_catalog(actor_user_id=7)

        rows = self._audit_rows("CHANNEL_POLICY_RULE_CATALOG_SEEDED")
        self.assertEqual(
            len(rows), 1,
            "동일한 값으로 10회 재시딩했는데 감사로그가 추가로 생김",
        )

    def test_reseed_after_real_change_writes_exactly_one_new_audit_row(self):
        """카탈로그와 실제로 다른 값(예: 수동 DB 드리프트)이 있으면
        재시딩이 그 값을 되돌리면서 정확히 1건의 새 감사로그를
        남긴다 — 조건부 기록이 "항상 0건"으로 퇴화하지 않았는지
        확인."""

        from app.domains.channel_policy.model import ChannelPolicyRule

        one_rule = self.db.query(ChannelPolicyRule).first()
        one_rule.active = not one_rule.active
        self.db.commit()

        self.service.seed_rule_catalog(actor_user_id=7)

        rows = self._audit_rows("CHANNEL_POLICY_RULE_CATALOG_SEEDED")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].user_id, 7)
        self.assertIn("1건", rows[1].description)

    def test_evaluate_and_record_writes_audit_log(self):

        result = self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=5,
        )

        rows = self._audit_rows("CHANNEL_POLICY_EVALUATED")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].company_id, 1)
        self.assertEqual(rows[0].user_id, 5)
        self.assertEqual(rows[0].entity_id, str(result.id))

    def test_upsert_settings_writes_audit_log(self):

        self.service.upsert_settings(
            1, updated_by=9,
            data=UpdateCompanyChannelPolicySettingsRequest(
                expected_version=0, min_target_margin_rate="0.15",
            ),
        )

        rows = self._audit_rows("CHANNEL_POLICY_SETTINGS_UPDATED")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].company_id, 1)
        self.assertEqual(rows[0].user_id, 9)

    def test_audit_log_company_isolation(self):

        self.service.upsert_settings(
            100, updated_by=1,
            data=UpdateCompanyChannelPolicySettingsRequest(
                expected_version=0, min_target_margin_rate="0.15",
            ),
        )
        self.service.upsert_settings(
            200, updated_by=1,
            data=UpdateCompanyChannelPolicySettingsRequest(
                expected_version=0, min_target_margin_rate="0.25",
            ),
        )

        rows = self._audit_rows("CHANNEL_POLICY_SETTINGS_UPDATED")
        company_ids = sorted(r.company_id for r in rows)
        self.assertEqual(company_ids, [100, 200])

    def test_audit_log_description_has_no_sensitive_markers(self):

        self.service.evaluate_and_record(
            company_id=1, product_candidate_id=self.candidate_earbuds.id,
            channel="COUPANG", category_hint_override=None,
            product_attributes=self._COMPLETE_ATTRS,
            confirmed_evidence_rule_codes=["CATEGORY_NOTICE_INFO_REQUIRED"],
            evaluated_by=1,
        )

        rows = self._audit_rows("CHANNEL_POLICY_EVALUATED")
        description = rows[0].description.lower()
        for forbidden in ("password", "card", "secret", "token", "api_key"):
            self.assertNotIn(forbidden, description)


if __name__ == "__main__":
    unittest.main()
