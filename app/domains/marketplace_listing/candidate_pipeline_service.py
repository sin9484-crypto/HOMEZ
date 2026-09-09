"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/candidate_pipeline_service.py

V7 Gate 6 — 후보 선택→초안 생성→이미지 생성을 하나의 파이프라인으로
연결한다.

이 서비스는 새 비즈니스 규칙을 거의 만들지 않는다 — 전부 이미 검증된
기존 서비스에 위임한다(재구현하지 않는다):
  - ProductCandidateService.require_approved_for_company() — "이 회사
    관점에서 승인된 후보인가" 단일 게이트(재사용).
  - SafetyService — Emergency Stop / AutomationMode(재사용, 이 파일은
    새 안전장치를 만들지 않는다).
  - ListingWizardService — 위저드 생성/단계 갱신(재사용). 이 서비스는
    직접 commit하지 않는다 — 전부 ListingWizardService/
    ImageGenerationJobQueueService에 위임한다(Gate 4/5가 겪은 "하위
    서비스의 자체 commit/rollback과 충돌" 결함 클래스를 피하기 위해,
    이 서비스 자신은 독자적인 열린 Transaction을 유지한 채로 하위
    서비스를 호출하지 않는다 — 아래 각 메서드가 하위 서비스 호출
    사이에 자신만의 미확정 변경을 갖지 않는 것으로 보장한다).
  - ImageGenerationJobQueueService — 이미지 Job 제출(재사용).
  - draft_content_provider.FakeDraftContentProvider — 텍스트 초안
    제안(신규 Provider 경계, Fake만 실제로 동작).
  - media_asset.content_policy_check.run_content_policy_checklist —
    저작권/금지품목 간단 체크리스트(신규, 규칙 기반).

Recommend 모드(AutomationMode != LIMITED_AUTOMATION) vs Auto 모드
(LIMITED_AUTOMATION)의 차이(요청 원문 요구사항 4):
  - Recommend 모드: 이 서비스는 아무것도 만들거나 바꾸지 않는다 —
    제안 문구·근거(후보 원본 점수)·정책 체크리스트 결과만 응답으로
    돌려준다. 운영자가 그 제안을 참고해 기존 ListingWizardService/
    ImageGenerationJobQueueService API를 직접 호출해야 실제로
    반영된다.
  - Auto 모드(LIMITED_AUTOMATION) + Emergency Stop 비활성 + 정책
    체크리스트 통과: 자금 이동이 없는 준비 단계(위저드 생성, 초안
    문구 채우기, 이미지 Job 제출)까지 이 서비스가 직접 실행한다.
    승인(ListingWizardService.approve())은 recent_auth_token+nonce로
    보호된 기존 게이트이므로 이 서비스는 그 메서드를 절대 호출하지
    않는다 — Auto 모드에서도 최종 승인·제출은 항상 사람의 몫이다
    (이 파일이 임의로 내린 정책 결정 — CTO 확인 필요 사항으로 별도
    보고).

금지 품목 체크리스트(BLOCKING)는 두 모드 모두에서 파이프라인 시작
자체를 막는다(정책 위반은 점수로 상쇄하지 않는다는 이 저장소의 반복된
원칙 — app/domains/decision와 동일한 사상). 제3자 브랜드 IP 위험
(WARNING)은 막지 않고 결과에 표시만 한다.

새 테이블을 만들지 않는다 — 파이프라인 자체의 상태는 별도로
영속화하지 않고, 실행 결과는 전부 기존 ListingWizard/
ImageGenerationJob 행에 남는다(이미지 Job이 어느 위저드를 위해
제출됐는지는 요청 시점의 product_candidate_id 일치로만 판단한다 —
두 도메인 사이에 새 FK/논리참조 컬럼을 추가하지 않는다).
=========================================================
"""

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.marketplace_listing.candidate_pipeline_schema import (
    CandidatePipelineImageSelectionRequest,
    CandidatePipelineRunRequest,
)
from app.domains.marketplace_listing.draft_content_provider import (
    DraftContentRequest,
    get_draft_content_provider,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCreateRequest,
    WizardDraftUpdateRequest,
    WizardMediaUpdateRequest,
    WizardSourceUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    ListingWizardService,
)
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.media_asset.constants import (
    ImageResultStatus,
    ImageSafetyCheckStatus,
)
from app.domains.media_asset.content_policy_check import (
    run_content_policy_checklist,
)
from app.domains.media_asset.job_queue_service import (
    ImageGenerationJobQueueService,
)
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.schema import (
    ImageGenerationItemRequest,
    ImageGenerationJobSubmitRequest,
)
from app.domains.product_candidate.service import ProductCandidateService


class CandidatePipelineService:

    def __init__(self, db: Session):

        self.db = db
        self.safety_service = SafetyService(db)
        self.wizard_service = ListingWizardService(db)
        self.image_service = ImageGenerationJobQueueService(db)
        self.candidate_service = ProductCandidateService(db)

    def _is_auto_mode(self) -> bool:

        return (
            self.safety_service.get_current_mode()
            == AutomationMode.LIMITED_AUTOMATION
        )

    # --------------------------------------------------
    # 파이프라인 시작(후보 선택 → 초안 제안/생성 → 이미지 Job)
    # --------------------------------------------------

    def start_pipeline(
        self,
        data: CandidatePipelineRunRequest,
        company_id: int,
        actor_id: int,
    ) -> dict:

        # 1) 후보 선택 — 이 회사 관점에서 승인된 후보만 파이프라인에
        #    태울 수 있다(재사용, 재구현 안 함).
        candidate = self.candidate_service.require_approved_for_company(
            data.candidate_id, company_id,
        )

        # 2) Emergency Stop — 다른 무엇보다 우선 차단(app/domains/
        #    decision::_check_safety_gate와 동일한 원칙).
        if self.safety_service.is_emergency_stop_active():
            raise BadRequestException(
                "Emergency Stop이 활성화되어 있어 파이프라인을 시작할 수 "
                "없습니다.",
            )

        # 3) 저작권/금지품목 체크리스트 — BLOCKING이면 위저드/이미지 Job
        #    자체를 만들지 않는다(양쪽 모드 공통, 점수로 상쇄하지 않음).
        policy_result = run_content_policy_checklist(
            candidate.product_name, candidate.category_hint,
            candidate.brand_hint,
        )
        if not policy_result.passed:
            raise BadRequestException(
                "이 후보는 금지 품목 체크리스트를 통과하지 못해 파이프라인을 "
                f"시작할 수 없습니다(플래그: {list(policy_result.blocking_flags)}).",
            )

        # 4) 제안 문구 — 실제 LLM 호출 없음(Fake Provider만 동작).
        # Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        # 강제(CONTENT_GENERATION). EStop·정책 체크리스트 통과 이후,
        # 실제 초안 생성 이전에 연결한다.
        require_active_capability(CapabilityCode.CONTENT_GENERATION)

        content_provider = get_draft_content_provider(
            data.content_provider_code,
        )
        draft_result = content_provider.generate(DraftContentRequest(
            candidate_id=candidate.id,
            product_name=candidate.product_name,
            category_hint=candidate.category_hint,
            brand_hint=candidate.brand_hint,
        ))

        candidate_scores = {
            "trend_score": candidate.trend_score,
            "novelty_score": candidate.novelty_score,
            "demand_score": candidate.demand_score,
            "competition_score": candidate.competition_score,
            "margin_score": candidate.margin_score,
            "risk_score": candidate.risk_score,
            "confidence": candidate.confidence,
        }

        result = {
            "automation_mode": self.safety_service.get_current_mode(),
            "auto_applied": False,
            "suggested_description": draft_result.description,
            "suggested_keywords": list(draft_result.keywords),
            "content_source": draft_result.content_source,
            "content_provider_code": draft_result.provider_code,
            "content_policy_passed": policy_result.passed,
            "content_policy_blocking_flags": list(policy_result.blocking_flags),
            "content_policy_warning_flags": list(policy_result.warning_flags),
            "content_policy_checklist_version": policy_result.checklist_version,
            "candidate_scores": candidate_scores,
            "wizard_id": None,
            "wizard_status": None,
            "wizard_version": None,
            "image_job_id": None,
            "image_job_status": None,
        }

        # 5) Recommend 모드(LIMITED_AUTOMATION이 아님) — 제안만 반환하고
        #    아무것도 만들지 않는다.
        if not self._is_auto_mode():
            return result

        # 6) Auto 모드 — 준비 단계(위저드 생성, 초안 채우기, 이미지 Job
        #    제출)까지 자동 실행한다. 승인(approve())은 절대 호출하지
        #    않는다.
        wizard, _wizard_dup = self.wizard_service.create(
            WizardCreateRequest(
                source_type="PIPELINE_AUTO",
                product_candidate_id=candidate.id,
                creation_idempotency_key=data.wizard_creation_idempotency_key,
            ),
            created_by=actor_id, company_id=company_id,
        )

        if wizard.current_step == "SOURCE" and wizard.status == "DRAFT":
            wizard = self.wizard_service.update_source(
                wizard.id, company_id,
                WizardSourceUpdateRequest(
                    expected_version=wizard.version,
                    product_candidate_id=candidate.id,
                ),
            )

        if wizard.current_step == "DRAFT" and wizard.status == "DRAFT":
            wizard = self.wizard_service.update_draft(
                wizard.id, company_id,
                WizardDraftUpdateRequest(
                    expected_version=wizard.version,
                    product_name=candidate.product_name,
                    brand=candidate.brand_hint,
                    category=candidate.category_hint,
                    description=draft_result.description,
                    keywords=list(draft_result.keywords),
                ),
            )

        image_job, _job_dup = self.image_service.submit_job(
            ImageGenerationJobSubmitRequest(
                product_candidate_id=candidate.id,
                provider_code=data.image_provider_code,
                items=[
                    ImageGenerationItemRequest(purpose=purpose)
                    for purpose in data.image_purposes
                ],
                idempotency_key=data.image_job_idempotency_key,
            ),
            actor_id, company_id,
        )

        result.update({
            "auto_applied": True,
            "wizard_id": wizard.id,
            "wizard_status": wizard.status,
            "wizard_version": wizard.version,
            "image_job_id": image_job.id,
            "image_job_status": image_job.status,
        })

        return result

    # --------------------------------------------------
    # 이미지 Job 결과 선택 → 위저드 media 단계 반영(승인 fingerprint에
    # 자동으로 편입 — 새 fingerprint 로직을 만들지 않는다).
    # --------------------------------------------------

    def select_image_results(
        self, data: CandidatePipelineImageSelectionRequest, company_id: int,
    ) -> ListingWizard:

        wizard = self.wizard_service.get(data.wizard_id, company_id)

        job = (
            self.db.query(ImageGenerationJob)
            .filter(ImageGenerationJob.id == data.image_job_id)
            .filter(ImageGenerationJob.company_id == company_id)
            .first()
        )
        if job is None:
            raise NotFoundException("ImageGenerationJob을 찾을 수 없습니다.")

        if job.product_candidate_id != wizard.product_candidate_id:
            raise BadRequestException(
                "이 이미지 Job은 이 위저드의 후보(product_candidate_id)와 "
                "일치하지 않습니다.",
            )

        requested_ids = list(dict.fromkeys(data.selected_result_ids))

        results = (
            self.db.query(ImageGenerationResult)
            .filter(ImageGenerationResult.company_id == company_id)
            .filter(ImageGenerationResult.job_id == data.image_job_id)
            .filter(ImageGenerationResult.id.in_(requested_ids))
            .all()
        )

        found_ids = {r.id for r in results}
        missing = [rid for rid in requested_ids if rid not in found_ids]
        if missing:
            raise BadRequestException(
                f"선택한 결과 중 존재하지 않는 항목이 있습니다: {missing}",
            )

        unusable = [
            r for r in results
            if r.status != ImageResultStatus.SUCCEEDED
            or r.safety_check_status not in ImageSafetyCheckStatus.USABLE
            or r.media_asset_id is None
        ]
        if unusable:
            raise BadRequestException(
                "성공(SUCCEEDED)하고 안전성 검사를 통과(PASSED)한 결과만 "
                "선택할 수 있습니다.",
            )

        by_id = {r.id: r for r in results}
        media_ids = [by_id[rid].media_asset_id for rid in requested_ids]

        return self.wizard_service.update_media(
            data.wizard_id, company_id,
            WizardMediaUpdateRequest(
                expected_version=data.expected_version,
                selected_media_asset_ids=media_ids,
            ),
        )


__all__ = [
    "CandidatePipelineService",
]
