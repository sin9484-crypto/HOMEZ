"""
=========================================================
Homez OS

File : app/domains/media_asset/concept_run_service.py

2026-10-23 — 이미지 AI I2: **컨셉별 독립 작업**. 누끼 한 장으로 고른 컨셉 여러 개를 각각 따로 만들고, 컨셉마다 **검사 결과**(`concept_inspection`)를 붙여 돌려준다.
로컬 Pillow 합성이라 외부 접촉·비용이 없다(L0·L1 컨셉만 — 생성형 L2 컨셉은 아직 없다).

컨셉마다 결과 상태(`status`):
  - CREATED       만들어 저장했다(검사에서 FAIL이 없다)          - REUSED       같은 내용의 사진이 이미 있어 그것을 썼다
  - CHECK_FAILED  만들었지만 검사에서 FAIL이 있어 **저장하지 않았다**(제품 보존 미달·대표 규격 미달 등 — 사진은 남기지 않는다)
  - SKIPPED       알 수 없는 컨셉이거나 이 용도에 쓸 수 없는 컨셉(대표는 흰 배경만)       - FAILED  합성 중 오류
한 컨셉의 실패·건너뜀·검사 실패는 **다른 컨셉에 영향을 주지 않는다**.

기준:
  - **제품은 다시 그리지 않는다** — 합성(`composition.compose_with_background`)만 하고, 제품 픽셀이 그대로인지를 검사가 숫자로 확인한다.
  - 원본(누끼)은 그대로 둔다. 결과는 새 파생 자산이고, 출처·권리 상태 승계는 만들 때 한 번만(`derived_assets.adopt_derived`) — 같은 바이트의 다른 출처 자산은 덮어쓰지 않는다.
  - 같은 누끼·같은 컨셉·같은 용도면 같은 결과(결정적)라 반복해도 자산이 늘지 않는다(멱등). 한 번에 최대 6개 컨셉, 같은 코드는 한 번만.
  - 회사 범위(다른 회사 누끼는 "없음"), 사용 금지(RIGHTS_DENIED) 사진은 시작 전에 거절한다.
  - 검사하지 못한 항목(정책 적합)은 NOT_CHECKED로 그대로 알린다 — 통과로 쓰지 않는다. 이미지에 글자·워터마크를 더하지 않는다.
=========================================================
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.media_asset import concept_inspection
from app.domains.media_asset.composition import compose_with_background
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.derived_assets import adopt_derived
from app.domains.media_asset.image_concepts import PURPOSES
from app.domains.media_asset.image_concepts import get_concept
from app.domains.media_asset.image_validation import build_storage_path
from app.domains.media_asset.image_validation import validate_image_bytes
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.storage import read_media_file

logger = logging.getLogger("homez")

OPERATION = "CONCEPT_RUN"
MAX_CONCEPTS = 6
CREATED, REUSED, CHECK_FAILED, SKIPPED, FAILED = "CREATED", "REUSED", "CHECK_FAILED", "SKIPPED", "FAILED"


@dataclass(frozen=True)
class ConceptRunOutcome:
    concept_code: str
    status: str
    asset: MediaAsset | None
    checks: tuple[dict[str, Any], ...] = ()
    usable: bool | None = None
    reason: str | None = None


class ConceptRunService:

    def __init__(self, db: Session, queue: ImageGenerationJobQueueService | None = None):
        self.db = db
        self.queue = queue or ImageGenerationJobQueueService(db)

    def run(self, company_id: int, requested_by: int, cutout_asset_id: int, concept_codes: list[str], purpose: str) -> list[ConceptRunOutcome]:
        if purpose not in PURPOSES:
            raise BadRequestException("알 수 없는 이미지 용도입니다.")
        codes: list[str] = []
        for code in concept_codes or []:
            if not isinstance(code, str) or not code.strip():
                raise BadRequestException("컨셉 코드가 올바르지 않습니다.")
            if code not in codes:
                codes.append(code)
        if not codes:
            raise BadRequestException("만들 컨셉을 하나 이상 고르세요.")
        if len(codes) > MAX_CONCEPTS:
            raise BadRequestException(f"한 번에 최대 {MAX_CONCEPTS}개 컨셉까지 만들 수 있습니다.")

        cutout = self.queue.repository.get_media_asset_for_company(cutout_asset_id, company_id)
        if cutout is None:
            raise NotFoundException("누끼 이미지를 찾을 수 없습니다.")
        if cutout.rights_status == "RIGHTS_DENIED":
            raise BadRequestException("사용이 금지된 사진은 컨셉으로 만들 수 없습니다.")
        try:
            cutout_bytes = read_media_file(self.queue.media_root, cutout.storage_path)
            validate_image_bytes(cutout_bytes)       # 깨진 파일은 컨셉마다 실패시키지 않고 시작 전에 거절한다
        except Exception as exc:  # noqa: BLE001
            raise BadRequestException("누끼 이미지 파일을 읽을 수 없습니다.") from exc
        return [self._one(company_id, requested_by, cutout, cutout_bytes, code, purpose) for code in codes]

    def _one(self, company_id: int, requested_by: int, cutout: MediaAsset, cutout_bytes: bytes, code: str, purpose: str) -> ConceptRunOutcome:
        try:
            concept = get_concept(code)
        except BadRequestException:
            return ConceptRunOutcome(code, SKIPPED, None, reason="UNKNOWN_CONCEPT")
        if purpose not in concept.usable_for:
            return ConceptRunOutcome(code, SKIPPED, None, reason="NOT_USABLE_FOR_PURPOSE")

        job = self.queue._record_processing_job(  # noqa: SLF001 — 같은 패키지의 처리 작업 기록 규칙을 그대로 쓴다
            operation=OPERATION, provider_code="PILLOW_LOCAL", model_name=code, source_asset_id=cutout.id, requested_by=requested_by, company_id=company_id,
            product_candidate_id=cutout.owner_id if cutout.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE else 0,
        )
        try:
            composed = compose_with_background(cutout_bytes, concept.background_spec())
            results = concept_inspection.inspect(cutout_bytes, composed, concept.checks, purpose)
        except Exception as exc:  # noqa: BLE001
            logger.warning("컨셉 합성 실패", exc_info=True)
            self.queue._finish_processing_job(job.id, company_id, success=False, error_reason=f"컨셉 합성 실패: {type(exc).__name__}")  # noqa: SLF001
            return ConceptRunOutcome(code, FAILED, None, reason="COMPOSE_FAILED")

        checks = tuple(r.to_dict() for r in results)
        if not concept_inspection.usable(results):
            self.queue._finish_processing_job(job.id, company_id, success=False, error_reason="컨셉 검사 실패(저장하지 않음)")  # noqa: SLF001
            return ConceptRunOutcome(code, CHECK_FAILED, None, checks, False, "INSPECTION_FAILED")

        try:
            validated = validate_image_bytes(composed)
            existing = self.queue.repository.get_media_asset_by_company_storage_path(
                company_id, build_storage_path(company_id, hashlib.sha256(composed).hexdigest(), validated.mime_type),
            )
            asset = self.queue._persist_generated_asset(  # noqa: SLF001
                company_id=company_id, owner_type=cutout.owner_type, owner_id=cutout.owner_id, purpose=cutout.purpose, display_order=cutout.display_order,
                image_bytes=composed, self_reported_mime_type="image/png",
            )
            linked = adopt_derived(asset, cutout, f"concept-{code}-{purpose}.png".lower())
            self.db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("컨셉 저장 실패", exc_info=True)
            self.db.rollback()
            self.queue._finish_processing_job(job.id, company_id, success=False, error_reason=f"컨셉 저장 실패: {type(exc).__name__}")  # noqa: SLF001
            return ConceptRunOutcome(code, FAILED, None, checks, True, "SAVE_FAILED")

        self.queue._finish_processing_job(job.id, company_id, success=True, model_version=code, media_asset_id=asset.id)  # noqa: SLF001
        if existing is None:
            return ConceptRunOutcome(code, CREATED, asset, checks, True, None)
        return ConceptRunOutcome(code, REUSED, asset, checks, True, None if linked else "EXISTING_ASSET_WITH_SAME_CONTENT")


__all__ = ["ConceptRunService", "ConceptRunOutcome", "MAX_CONCEPTS", "OPERATION", "CREATED", "REUSED", "CHECK_FAILED", "SKIPPED", "FAILED"]
