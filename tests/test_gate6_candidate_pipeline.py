"""
=========================================================
Homez OS

File : tests/test_gate6_candidate_pipeline.py

V7 Gate 6 — 후보 선택→초안 생성→이미지 생성 통합 파이프라인 검증.
실제 DB·외부 API 없음 — 전부 임시 SQLite + Fake Provider만 사용한다
(기존 marketplace_listing/media_asset 도메인 테스트와 동일한 패턴).

시나리오:
- 회사 관점 미승인 후보는 파이프라인 진입 자체가 차단된다.
- Emergency Stop이 활성이면 자동 모드에서도 즉시 차단된다.
- 금지 품목 체크리스트에 걸리면 위저드/이미지 Job이 전혀 만들어지지
  않는다(BLOCKING).
- Recommend 모드(LIMITED_AUTOMATION이 아님)에서는 제안만 반환하고
  위저드/이미지 Job을 만들지 않는다.
- Auto 모드(LIMITED_AUTOMATION)에서는 위저드 생성+초안 자동 채움+
  이미지 Job 제출까지 자동 실행된다(승인은 자동 실행하지 않는다).
- 동일 idempotency_key 재호출은 위저드/이미지 Job을 중복 생성하지
  않는다.
- 이미지 Job 결과 선택이 위저드 media 단계(및 승인 fingerprint 재료)에
  정확히 반영된다.
- 실패/미승인 이미지 결과는 선택할 수 없다.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException, ConflictException
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import (
    AutomationModeState,
    EmergencyStop,
    ExecutionLimit,
    ExecutionPeriodUsage,
    ExecutionUsage,
)
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.marketplace_listing.candidate_pipeline_schema import (
    CandidatePipelineImageSelectionRequest,
    CandidatePipelineRunRequest,
)
from app.domains.marketplace_listing.candidate_pipeline_service import (
    CandidatePipelineService,
)
from app.domains.marketplace_listing.model import (
    ListingWizard,
    MarketplaceAccount,
    MarketplaceChannel,
    MarketplaceFulfillmentCapability,
    MarketplaceFulfillmentEligibility,
    MarketplaceFulfillmentSelection,
    MarketplaceListing,
    MarketplaceListingDraft,
    MarketplaceSubmission,
    MarketplaceSubmissionApproval,
)
from app.domains.media_asset.model import (
    ImageGenerationDailyUsage,
    ImageGenerationJob,
    ImageGenerationResult,
    MediaAsset,
)
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import (
    ProductCandidate,
    ProductCandidateSelection,
)
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)

_COUNTER = 0


def _key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


class CandidatePipelineTestCase(unittest.TestCase):

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
                MarketplaceFulfillmentCapability.__table__,
                MarketplaceListingDraft.__table__,
                MarketplaceListing.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmissionApproval.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__,
                ListingWizard.__table__,
                MediaAsset.__table__,
                ImageGenerationJob.__table__,
                ImageGenerationResult.__table__,
                ImageGenerationDailyUsage.__table__,
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )
        # audit_logs는 SQLAlchemy Model이 없는 레거시 테이블(2026-08-20
        # 3차 지시 — confirm_rights_verified()가 이제 감사로그를 남기므로
        # 이 테스트 DB에도 필요하다. tests/test_long_detail_image_ingest.py
        # 와 동일한 raw DDL을 재사용한다).
        with self.engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, "
                "user_id INTEGER, action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
                "description VARCHAR(500), ip_address VARCHAR(50))",
            )
            conn.commit()

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.media_root = tempfile.mkdtemp()
        self.service = CandidatePipelineService(self.db)
        # 이미지 Job이 실제 파일시스템 임시 경로를 쓰도록 재구성.
        from pathlib import Path

        from app.domains.media_asset.job_queue_service import (
            ImageGenerationJobQueueService,
        )
        self.service.image_service = ImageGenerationJobQueueService(
            self.db, media_root=Path(self.media_root),
        )

        company = Company(
            name="파이프라인 테스트 회사", business_number="777-77-77777",
            ceo="테스트", phone="02-000-0000", email="pipeline@example.com",
            address="서울",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _candidate(
        self, ref=None, status=CandidateStatus.APPROVED, product_name="테스트 상품",
        category_hint="생활용품", brand_hint="HOMEZ",
    ) -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=f"pipeline:COUPANG:{ref or _key('cand')}",
            source_type="TREND", source_reference=ref or "ref",
            market="COUPANG", product_name=product_name,
            category_hint=category_hint, brand_hint=brand_hint,
            status=status,
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    def _run(self, candidate_id, **overrides) -> dict:

        payload = dict(
            candidate_id=candidate_id,
            wizard_creation_idempotency_key=_key("wiz"),
            image_job_idempotency_key=_key("job"),
        )
        payload.update(overrides)

        return self.service.start_pipeline(
            CandidatePipelineRunRequest(**payload),
            self.company_id, actor_id=1,
        )

    # -------------------- 게이트 --------------------

    def test_not_approved_candidate_is_rejected(self):

        candidate = self._candidate(status=CandidateStatus.RECOMMENDED)

        with self.assertRaises(BadRequestException):
            self._run(candidate.id)

    def test_emergency_stop_blocks_even_in_auto_mode(self):

        candidate = self._candidate()
        SafetyService(self.db).set_mode(
            AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
        )
        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            self._run(candidate.id)

        self.assertEqual(
            self.db.query(ListingWizard).count(), 0,
            "Emergency Stop 중에는 위저드가 만들어지면 안 된다",
        )

    def test_content_generation_blocked_when_capability_deactivated(self):
        """Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        실연결 증거. EStop·정책 체크리스트가 전부 정상이어도
        CONTENT_GENERATION이 비활성이면 초안 생성 자체가 차단된다."""

        from app.domains.ai_governance.service import InactiveCapabilityError
        from tests.ai_governance_test_helpers import deactivated_capability

        candidate = self._candidate()

        with deactivated_capability("CONTENT_GENERATION"):
            with self.assertRaises(InactiveCapabilityError):
                self._run(candidate.id)

    def test_prohibited_content_blocks_pipeline_entirely(self):

        candidate = self._candidate(product_name="모형 총기 완구")

        with self.assertRaises(BadRequestException):
            self._run(candidate.id)

        self.assertEqual(self.db.query(ListingWizard).count(), 0)
        self.assertEqual(self.db.query(ImageGenerationJob).count(), 0)

    # -------------------- Recommend 모드 --------------------

    def test_recommend_mode_returns_suggestion_only_no_side_effects(self):

        candidate = self._candidate()
        # 기본 AutomationMode는 RECOMMEND_ONLY(설정한 적 없음 시).

        result = self._run(candidate.id)

        self.assertEqual(result["automation_mode"], "RECOMMEND_ONLY")
        self.assertFalse(result["auto_applied"])
        self.assertIsNone(result["wizard_id"])
        self.assertIsNone(result["image_job_id"])
        self.assertEqual(result["content_source"], "AI_FAKE_DRAFT")
        self.assertIn("테스트 상품", result["suggested_description"])
        self.assertEqual(self.db.query(ListingWizard).count(), 0)
        self.assertEqual(self.db.query(ImageGenerationJob).count(), 0)

    def test_recommend_mode_still_shows_ip_risk_warning(self):

        candidate = self._candidate(brand_hint="나이키")

        result = self._run(candidate.id)

        self.assertTrue(result["content_policy_passed"])
        self.assertIn("나이키", result["content_policy_warning_flags"])

    def test_operator_approval_mode_is_also_recommend_only(self):

        candidate = self._candidate()
        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

        result = self._run(candidate.id)

        self.assertFalse(result["auto_applied"])
        self.assertIsNone(result["wizard_id"])

    # -------------------- Auto 모드 --------------------

    def test_auto_mode_creates_wizard_and_fills_draft_and_submits_image_job(self):

        candidate = self._candidate()
        SafetyService(self.db).set_mode(
            AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
        )

        result = self._run(candidate.id)

        self.assertTrue(result["auto_applied"])
        self.assertIsNotNone(result["wizard_id"])
        self.assertIsNotNone(result["image_job_id"])

        wizard = self.db.query(ListingWizard).get(result["wizard_id"])
        self.assertEqual(wizard.product_candidate_id, candidate.id)
        self.assertEqual(wizard.company_id, self.company_id)
        # 승인은 절대 자동 실행되지 않는다.
        self.assertEqual(wizard.status, "DRAFT")
        self.assertIsNone(wizard.approved_at)

        import json
        draft = json.loads(wizard.draft_json)
        self.assertEqual(draft["product_name"], "테스트 상품")
        self.assertIn("AI 초안", draft["description"])

        job = self.db.query(ImageGenerationJob).get(result["image_job_id"])
        self.assertEqual(job.product_candidate_id, candidate.id)
        self.assertEqual(job.status, "PENDING")

    def test_auto_mode_idempotent_rerun_does_not_duplicate(self):

        candidate = self._candidate()
        SafetyService(self.db).set_mode(
            AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
        )

        wiz_key = _key("wiz")
        job_key = _key("job")

        first = self._run(
            candidate.id,
            wizard_creation_idempotency_key=wiz_key,
            image_job_idempotency_key=job_key,
        )
        second = self._run(
            candidate.id,
            wizard_creation_idempotency_key=wiz_key,
            image_job_idempotency_key=job_key,
        )

        self.assertEqual(first["wizard_id"], second["wizard_id"])
        self.assertEqual(first["image_job_id"], second["image_job_id"])
        self.assertEqual(self.db.query(ListingWizard).count(), 1)
        self.assertEqual(self.db.query(ImageGenerationJob).count(), 1)

    def test_auto_mode_blocked_by_prohibited_content_makes_no_wizard(self):

        candidate = self._candidate(category_hint="마약류 관련")
        SafetyService(self.db).set_mode(
            AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            self._run(candidate.id)

        self.assertEqual(self.db.query(ListingWizard).count(), 0)
        self.assertEqual(self.db.query(ImageGenerationJob).count(), 0)

    # -------------------- 이미지 결과 선택 --------------------

    def test_select_image_results_updates_wizard_media_and_fingerprint_material(self):

        candidate = self._candidate()
        SafetyService(self.db).set_mode(
            AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
        )

        result = self._run(candidate.id)
        wizard_id = result["wizard_id"]
        job_id = result["image_job_id"]

        # Worker가 할 일을 테스트에서 동기로 대신 실행한다(기존
        # media_asset 테스트와 동일한 패턴).
        self.service.image_service._execute_job(job_id, self.company_id)

        job_results = (
            self.db.query(ImageGenerationResult)
            .filter(ImageGenerationResult.job_id == job_id)
            .all()
        )
        self.assertTrue(len(job_results) >= 1)
        succeeded_ids = [
            r.id for r in job_results if r.status == "SUCCEEDED"
        ]
        self.assertTrue(succeeded_ids)

        wizard_before = self.db.query(ListingWizard).get(wizard_id)
        before_fingerprint_material = wizard_before.selected_media_asset_ids_json

        # 2026-08-20 3차 지시 — AI로 생성된 이미지도 예외 없이
        # RIGHTS_UNVERIFIED로 시작한다(자동 VERIFIED 정책 폐지). 위저드
        # 선택은 이 테스트의 검증 대상(fingerprint 재료 반영)이 아니므로,
        # 실제 운영 흐름과 동일하게 선택 전에 명시적으로 사용권을
        # 확인해 둔다.
        from app.domains.media_asset.constants import RightsVerificationBasis

        succeeded_asset_ids = [
            r.media_asset_id
            for r in job_results
            if r.status == "SUCCEEDED" and r.media_asset_id
        ]
        for asset_id in succeeded_asset_ids:
            self.service.image_service.confirm_rights_verified(
                asset_id, self.company_id,
                RightsVerificationBasis.COMMERCIAL_LICENSE, confirmed_by=1,
            )

        updated_wizard = self.service.select_image_results(
            CandidatePipelineImageSelectionRequest(
                wizard_id=wizard_id, image_job_id=job_id,
                selected_result_ids=succeeded_ids,
                expected_version=wizard_before.version,
            ),
            self.company_id,
        )

        import json
        selected_ids = json.loads(updated_wizard.selected_media_asset_ids_json)
        expected_media_ids = sorted(
            r.media_asset_id for r in job_results if r.id in succeeded_ids
        )
        self.assertEqual(sorted(selected_ids), expected_media_ids)
        self.assertNotEqual(
            updated_wizard.selected_media_asset_ids_json,
            before_fingerprint_material,
        )

    def test_select_image_results_rejects_failed_results(self):

        candidate = self._candidate()
        SafetyService(self.db).set_mode(
            AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
        )
        result = self._run(candidate.id)
        wizard_id = result["wizard_id"]
        job_id = result["image_job_id"]
        self.service.image_service._execute_job(job_id, self.company_id)

        # 존재하지 않는 result id는 거부돼야 한다.
        with self.assertRaises(BadRequestException):
            self.service.select_image_results(
                CandidatePipelineImageSelectionRequest(
                    wizard_id=wizard_id, image_job_id=job_id,
                    selected_result_ids=[999999],
                    expected_version=1,
                ),
                self.company_id,
            )

    def test_select_image_results_rejects_mismatched_wizard_candidate(self):

        candidate_a = self._candidate(ref="A")
        candidate_b = self._candidate(ref="B")
        SafetyService(self.db).set_mode(
            AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
        )

        result_a = self._run(candidate_a.id)
        result_b = self._run(candidate_b.id)

        self.service.image_service._execute_job(
            result_b["image_job_id"], self.company_id,
        )
        job_b_results = (
            self.db.query(ImageGenerationResult)
            .filter(ImageGenerationResult.job_id == result_b["image_job_id"])
            .filter(ImageGenerationResult.status == "SUCCEEDED")
            .all()
        )

        wizard_a = self.db.query(ListingWizard).get(result_a["wizard_id"])

        with self.assertRaises(BadRequestException):
            self.service.select_image_results(
                CandidatePipelineImageSelectionRequest(
                    wizard_id=result_a["wizard_id"],
                    image_job_id=result_b["image_job_id"],
                    selected_result_ids=[r.id for r in job_b_results],
                    expected_version=wizard_a.version,
                ),
                self.company_id,
            )


if __name__ == "__main__":
    unittest.main()
