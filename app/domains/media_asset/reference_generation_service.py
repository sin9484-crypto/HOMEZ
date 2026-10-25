"""
=========================================================
Homez OS

File : app/domains/media_asset/reference_generation_service.py

"참고 사진으로 만들기" R3a(2026-10-22, 사용자 자율 진행 지시) — `reference_generation.py`(순수 규칙)를 **저장된 사진·권리 기록·작업 기록**에 연결하는 서비스.

  - `preview`  : 계획만 만든다. DB·파일에 아무것도 쓰지 않고 외부 호출·비용이 없다. 화면이 "몇 장을 어떻게 만들지"와 경고·검수 항목을 미리 보여 주는 용도.
  - `generate` : 같은 계획을 **FAKE 제공자**로 실행해 결과 사진을 파생 자산으로 저장한다. 실제 이미지 생성 API는 연결하지 않았다(`ALLOWED_PROVIDER_CODES`가 FAKE뿐) —
                 FAKE 결과는 "시연용"이며 실제 제품 등록에 최종본으로 쓰지 않는다(`demo=True`로 표시).

기준:
  - 회사 범위: 참고 사진은 `current_user.company_id`의 자산만 읽는다(다른 회사 자산은 "없음"과 같은 응답).
  - 권리: 자산의 `rights_status`와 이 회사의 **현재 확인 기록**(`has_current_acknowledgement`, 이미지가 바뀌면 자동 무효)을 규칙에 넘긴다. RIGHTS_DENIED는 항상 막는다.
    결과 자산은 참고 사진의 권리 상태를 **그대로 물려받고 격상하지 않는다**.
  - 대표(MAIN)는 사용자 최종 확인(`confirmed_main=True`) 없이는 실행하지 않는다 — 계획의 `requires_user_confirmation`을 서비스가 강제한다(규칙 모듈은 표시만 한다).
  - 멱등: 같은 참고 사진·같은 선택이면 계획 지문이 같고, 가짜 제공자는 같은 바이트를 내며, 같은 바이트는 같은 자산으로 합쳐진다 → 반복 실행해도 자산이 늘지 않는다
    (작업 기록은 실행마다 남는다).
  - 기록: 작업(`ImageGenerationJob`, operation=REFERENCE_GENERATION)에 계획 요약(참고 사진 바이트 제외)을 남기고 결과마다 `ImageGenerationResult`를 남긴다.
    "AI 생성" 표시는 이미지 픽셀이 아니라 작업 기록·파일 이름(`aigen-…`)·응답의 `generated_label`에 둔다(워터마크 금지, 사용자 지시).
  - Migration 없음 — 기존 컬럼만 쓴다.
=========================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.media_asset import reference_generation as rg
from app.domains.media_asset.constants import ImageJobStatus
from app.domains.media_asset.constants import ImageResultStatus
from app.domains.media_asset.constants import ImageSafetyCheckStatus
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.derived_assets import adopt_derived
from app.domains.media_asset.fingerprint import canonical_json
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.providers import ImageGenerationProviderError
from app.domains.media_asset.providers import get_provider
from app.domains.media_asset.rights_evidence_service import ImageRightsEvidenceService
from app.domains.media_asset.rights_evidence_service import WorkflowStage
from app.domains.media_asset.storage import read_media_file

logger = logging.getLogger("homez")

OPERATION = "REFERENCE_GENERATION"
FILE_PREFIX = "aigen"


@dataclass(frozen=True)
class ReferenceGenerationOutcome:
    plan: rg.ReferenceGenerationPlan
    assets: tuple[MediaAsset, ...]
    job_id: int | None
    demo: bool
    provider_cost: float | None


class ReferenceGenerationService:

    def __init__(self, db: Session, queue: ImageGenerationJobQueueService | None = None, image_ai=None):
        self.db = db
        self.queue = queue or ImageGenerationJobQueueService(db)
        self.rights = ImageRightsEvidenceService(db)
        # 2026-10-25 실제 제공자(GEMINI)용 — `ImageAiService`(설정·키·승인 센터 허용·사용 기록·완료 알림). 없으면 GEMINI는 쓸 수 없다.
        self.image_ai = image_ai

    # ------------------------------------------------------------
    # 읽기: 참고 사진 → 검증된 입력
    # ------------------------------------------------------------

    def _load_reference(self, company_id: int, asset_id: int) -> tuple[MediaAsset, rg.ReferenceImage]:
        asset = self.queue.repository.get_media_asset_for_company(asset_id, company_id)
        if asset is None:
            raise NotFoundException("참고 사진을 찾을 수 없습니다.")
        try:
            image_bytes = read_media_file(self.queue.media_root, asset.storage_path)
        except Exception as exc:  # noqa: BLE001 — 파일이 없거나 읽을 수 없다
            raise BadRequestException("참고 사진 파일을 읽을 수 없습니다.") from exc
        acknowledged = self.rights.has_current_acknowledgement(
            company_id, asset.id, WorkflowStage.GENERATE_OR_EDIT_OR_EXPORT,
        )
        reference = rg.check_reference_image(image_bytes, asset.mime_type, asset.rights_status, acknowledged)
        return asset, reference

    # ------------------------------------------------------------
    # 미리보기(쓰기 없음)
    # ------------------------------------------------------------

    def preview(
        self, company_id: int, reference_asset_id: int, purpose: str,
        backgrounds: list[str], poses: list[str], count: int,
    ) -> dict:
        _asset, reference = self._load_reference(company_id, reference_asset_id)
        plan = rg.build_plan(reference, purpose, backgrounds, poses, count)
        return {
            **plan.to_summary(),
            "reference_asset_id": reference_asset_id,
            "rights_status": reference.rights_status,
            "rights_acknowledged": reference.rights_acknowledged,
            "demo": True,
            "estimated_cost": self._estimated_cost(len(plan.variants)),
        }

    @staticmethod
    def _estimated_cost(items: int) -> float:
        from app.domains.media_asset.providers import FakeImageGenerationProvider

        return round(FakeImageGenerationProvider.FAKE_COST_PER_ITEM * items, 6)

    # ------------------------------------------------------------
    # 실행(FAKE 제공자만)
    # ------------------------------------------------------------

    def generate(
        self, company_id: int, requested_by: int, reference_asset_id: int, purpose: str,
        backgrounds: list[str], poses: list[str], count: int,
        confirmed_main: bool = False, provider_code: str = "FAKE", instruction: str | None = None,
    ) -> ReferenceGenerationOutcome:
        asset, reference = self._load_reference(company_id, reference_asset_id)
        plan = rg.build_plan(reference, purpose, backgrounds, poses, count)
        if plan.requires_user_confirmation and confirmed_main is not True:
            raise BadRequestException(
                "대표 이미지에 AI 생성 사진을 쓰려면 사용자 최종 확인이 필요합니다. "
                "쿠팡 대표 이미지 규격·AI 생성 이미지 정책을 확인하지 못해 반려될 수 있습니다.",
            )
        # 실제 제공자는 요청 변환 단계에서 거절된다(FAKE만 허용).
        try:
            request = rg.to_provider_request(plan, reference, company_id, self._candidate_id(asset), provider_code)
        except rg.ReferenceGenerationError:
            raise
        if instruction:
            # 사용자가 대화로 준 요청 글(실제 제공자 프롬프트에 데이터로만 들어간다 — 규칙을 바꾸지 못한다).
            from dataclasses import replace as _replace

            from app.domains.media_asset.gemini_image_client import clean_instruction

            request = _replace(request, style_params={**request.style_params, "user_instruction": clean_instruction(instruction)})
        real = provider_code != "FAKE"
        if real:
            if self.image_ai is None:
                raise BadRequestException("실제 AI 이미지 생성 설정을 불러오지 못했습니다.")
            provider_obj = self.image_ai.provider(company_id, requested_by, target=f"REF{asset.id}")   # 설정·키가 없으면 여기서 막힌다(작업 행 없음)

        job = self._start_job(company_id, requested_by, asset, plan, provider_code)
        try:
            provider = provider_obj if real else get_provider(provider_code)
            provider_result = provider.generate(request)
        except ImageGenerationProviderError as exc:
            self._fail_job(job.id, company_id, str(exc))
            raise BadRequestException(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("참고 사진 생성 실패", exc_info=True)
            self._fail_job(job.id, company_id, f"참고 사진 생성 실패: {type(exc).__name__}")
            raise BadRequestException("참고 사진 생성에 실패했습니다(원본은 그대로 유지됩니다).") from exc

        assets, succeeded = self._store_results(company_id, asset, plan, provider_result, job.id)
        self._finish_job(job.id, company_id, succeeded=succeeded, total=len(plan.variants))
        if real:
            # 사용자 결정(10-24): 대표 사진을 임의로 정하지 않고, 만든 뒤 알린다.
            self.image_ai.notify_done(company_id, requested_by, created=len(assets), failed=len(plan.variants) - succeeded)
        if not assets:
            raise BadRequestException("생성된 사진이 없습니다(제공자가 모든 항목을 실패 처리했습니다).")
        return ReferenceGenerationOutcome(
            plan=plan, assets=tuple(assets), job_id=job.id, demo=not real, provider_cost=provider_result.provider_cost,
        )

    # ------------------------------------------------------------
    # 내부: 작업·결과 기록
    # ------------------------------------------------------------

    @staticmethod
    def _candidate_id(asset: MediaAsset) -> int:
        return asset.owner_id if asset.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE else 0

    def _start_job(
        self, company_id: int, requested_by: int, asset: MediaAsset, plan: rg.ReferenceGenerationPlan, provider_code: str,
    ) -> ImageGenerationJob:
        payload = {"operation": OPERATION, "source_asset_id": asset.id, **plan.to_summary()}
        job = ImageGenerationJob(
            company_id=company_id, listing_package_id=None, product_candidate_id=self._candidate_id(asset),
            provider_code=provider_code, model_name=plan.request_fingerprint[:60], prompt_fingerprint=plan.request_fingerprint,
            request_payload_json=canonical_json(payload), status=ImageJobStatus.RUNNING,
            idempotency_key=f"{OPERATION}-{asset.id}-{plan.request_fingerprint[:16]}-{datetime.utcnow().timestamp()}",
            requested_by=requested_by, started_at=datetime.utcnow(),
        )
        job = self.queue.repository.add_job_no_commit(job)
        self.db.commit()
        return job

    def _store_results(self, company_id, reference_asset, plan, provider_result, job_id):
        assets: list[MediaAsset] = []
        succeeded = 0
        by_index = {v.sequence_index: v for v in plan.variants}
        for item in provider_result.items:
            variant = by_index.get(item.sequence_index)
            ok = item.status == "SUCCEEDED" and item.image_bytes and variant is not None and item.safety_check_status != "BLOCKED"
            if not ok:
                self.queue.repository.add_result_no_commit(ImageGenerationResult(
                    company_id=company_id, job_id=job_id, media_asset_id=None, sequence_index=item.sequence_index, purpose=item.purpose,
                    status=ImageResultStatus.FAILED,
                    safety_check_status=ImageSafetyCheckStatus.BLOCKED if item.safety_check_status == "BLOCKED" else ImageSafetyCheckStatus.UNKNOWN,
                    error_reason=(item.error_reason or "생성 실패")[:300],
                ))
                continue
            generated = self.queue._persist_generated_asset(  # noqa: SLF001 — 같은 패키지의 파생 자산 저장 규칙을 그대로 쓴다
                company_id=company_id, owner_type=reference_asset.owner_type, owner_id=reference_asset.owner_id,
                purpose=reference_asset.purpose, display_order=reference_asset.display_order, image_bytes=item.image_bytes,
                self_reported_mime_type=item.mime_type or "image/png",
            )
            # 출처·권리 상태 승계·파일 이름은 새로 만든 자산에만 한 번 쓴다 — 같은 바이트의 다른 출처 자산이 이미 있으면 덮어쓰지 않는다(권리 격상 방지, derived_assets.py).
            adopt_derived(generated, reference_asset, f"{FILE_PREFIX}-{plan.purpose}-{variant.background}-{variant.pose}-{variant.sequence_index}.png".lower())
            self.queue.repository.add_result_no_commit(ImageGenerationResult(
                company_id=company_id, job_id=job_id, media_asset_id=generated.id, sequence_index=item.sequence_index, purpose=item.purpose,
                status=ImageResultStatus.SUCCEEDED, safety_check_status=ImageSafetyCheckStatus.UNKNOWN,  # 사람 검수 전이라 "통과"라고 하지 않는다
                error_reason=None,
            ))
            if all(existing.id != generated.id for existing in assets):   # 같은 사진이 두 번 오면(같은 바이트) 목록에는 한 번만
                assets.append(generated)
            succeeded += 1
        self.db.commit()
        return assets, succeeded

    def _finish_job(self, job_id: int, company_id: int, *, succeeded: int | bool, total: int) -> None:
        ok = bool(succeeded)
        status = ImageJobStatus.SUCCEEDED if (ok and succeeded == total) else (ImageJobStatus.PARTIAL if ok else ImageJobStatus.FAILED)
        self.queue.repository.update_job_fields_conditional(
            job_id, company_id, (ImageJobStatus.RUNNING,),
            {"status": status, "progress_percent": 100 if ok else 0, "completed_at": datetime.utcnow()},
        )
        self.db.commit()

    def _fail_job(self, job_id: int, company_id: int, reason: str) -> None:
        self.queue.repository.update_job_fields_conditional(
            job_id, company_id, (ImageJobStatus.RUNNING,),
            {"status": ImageJobStatus.FAILED, "progress_percent": 0, "error_reason": reason[:300], "completed_at": datetime.utcnow()},
        )
        self.db.commit()


__all__ = ["ReferenceGenerationService", "ReferenceGenerationOutcome", "OPERATION", "FILE_PREFIX"]
