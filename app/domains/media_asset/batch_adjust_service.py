"""
=========================================================
Homez OS

File : app/domains/media_asset/batch_adjust_service.py

2026-10-23 — 이미지 AI R2: 선택한 사진 여러 장에 **같은 밝기·색온도 보정**을 한 번에 적용해 파생 자산으로 저장한다(`batch_adjust.py`의 순수 계산을 저장된 사진·작업 기록에 연결).
로컬 Pillow 보정이라 외부 접촉·비용이 없다.

기준:
  - **원본은 그대로 둔다** — 보정 결과는 새 파생 자산(`GENERATED`, 출처 = 원본)이다. 같은 값으로 다시 하면 같은 바이트라 같은 자산이 재사용된다(멱등).
  - **한 장의 실패가 나머지를 막지 않는다** — 사진마다 결과(`CREATED` 만들었다 / `REUSED` 이미 있던 자산 / `SKIPPED` 건너뜀 / `FAILED` 실패)를 따로 돌려준다.
  - 회사 범위(다른 회사 자산은 "없음"과 같은 건너뜀), 사용 금지(RIGHTS_DENIED) 사진은 건너뜀. 권리 상태는 **만들어질 때 한 번 물려받기만** 하고 격상하지 않는다(`derived_assets.adopt_derived`).
  - 같은 바이트가 **다른 출처의 자산**으로 이미 있으면 그 자산의 출처·권리·이름을 덮어쓰지 않고 `REUSED`로 보고한다.
  - 한 번에 최대 20장(`MAX_BATCH`), 같은 번호는 한 번만 처리. 값 검증은 `validate_adjustment`가 한다(범위 밖·0/0은 거절, 조용히 깎지 않는다).
  - 이미지에 글자·워터마크를 더하지 않는다.
=========================================================
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.media_asset.batch_adjust import MAX_BATCH
from app.domains.media_asset.batch_adjust import adjust_image
from app.domains.media_asset.batch_adjust import validate_adjustment
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.derived_assets import adopt_derived
from app.domains.media_asset.image_validation import build_storage_path
from app.domains.media_asset.image_validation import validate_image_bytes
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.storage import read_media_file

logger = logging.getLogger("homez")

OPERATION = "BATCH_ADJUST"
FILE_PREFIX = "adj"

CREATED, REUSED, SKIPPED, FAILED = "CREATED", "REUSED", "SKIPPED", "FAILED"


@dataclass(frozen=True)
class AdjustOutcome:
    asset_id: int
    status: str
    asset: MediaAsset | None
    reason: str | None = None


class BatchAdjustService:

    def __init__(self, db: Session, queue: ImageGenerationJobQueueService | None = None):
        self.db = db
        self.queue = queue or ImageGenerationJobQueueService(db)

    def adjust(self, company_id: int, requested_by: int, asset_ids: list[int], brightness, warmth) -> list[AdjustOutcome]:
        adjustment = validate_adjustment(brightness, warmth)
        ids: list[int] = []
        for value in asset_ids or []:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise BadRequestException("사진 번호는 양의 정수여야 합니다.")
            if value not in ids:
                ids.append(value)
        if not ids:
            raise BadRequestException("보정할 사진을 하나 이상 고르세요.")
        if len(ids) > MAX_BATCH:
            raise BadRequestException(f"한 번에 최대 {MAX_BATCH}장까지 보정할 수 있습니다.")
        return [self._one(company_id, requested_by, asset_id, adjustment) for asset_id in ids]

    def _one(self, company_id: int, requested_by: int, asset_id: int, adjustment) -> AdjustOutcome:
        asset = self.queue.repository.get_media_asset_for_company(asset_id, company_id)
        if asset is None:
            return AdjustOutcome(asset_id, SKIPPED, None, "NOT_FOUND")
        if asset.rights_status == "RIGHTS_DENIED":
            return AdjustOutcome(asset_id, SKIPPED, None, "RIGHTS_DENIED")

        job = self.queue._record_processing_job(  # noqa: SLF001 — 같은 패키지의 처리 작업 기록 규칙을 그대로 쓴다
            operation=OPERATION, provider_code="PILLOW_LOCAL", model_name=adjustment.label(), source_asset_id=asset.id, requested_by=requested_by,
            company_id=company_id,
            product_candidate_id=asset.owner_id if asset.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE else 0,
        )
        try:
            source_bytes = read_media_file(self.queue.media_root, asset.storage_path)
            adjusted, mime = adjust_image(source_bytes, adjustment)
            validated = validate_image_bytes(adjusted)
            existing = self.queue.repository.get_media_asset_by_company_storage_path(
                company_id, build_storage_path(company_id, hashlib.sha256(adjusted).hexdigest(), validated.mime_type),
            )
            derived = self.queue._persist_generated_asset(  # noqa: SLF001
                company_id=company_id, owner_type=asset.owner_type, owner_id=asset.owner_id, purpose=asset.purpose,
                display_order=asset.display_order, image_bytes=adjusted, self_reported_mime_type=mime,
            )
            linked = adopt_derived(derived, asset, f"{FILE_PREFIX}-{adjustment.label()}-{asset.id}.{'jpg' if mime == 'image/jpeg' else 'png'}")
            self.db.commit()
        except BadRequestException as exc:
            self.queue._finish_processing_job(job.id, company_id, success=False, error_reason=f"일괄 보정 실패: {exc.detail}"[:300])  # noqa: SLF001
            return AdjustOutcome(asset_id, FAILED, None, str(exc.detail))
        except Exception as exc:  # noqa: BLE001
            logger.warning("일괄 보정 실패", exc_info=True)
            self.db.rollback()
            self.queue._finish_processing_job(job.id, company_id, success=False, error_reason=f"일괄 보정 실패: {type(exc).__name__}")  # noqa: SLF001
            return AdjustOutcome(asset_id, FAILED, None, "보정에 실패했습니다(원본은 그대로 유지됩니다).")

        self.queue._finish_processing_job(job.id, company_id, success=True, model_version=adjustment.label(), media_asset_id=derived.id)  # noqa: SLF001
        if existing is None:
            return AdjustOutcome(asset_id, CREATED, derived, None)
        # 같은 바이트가 이미 있었다 — 같은 사진에서 같은 값으로 만든 자산이면 그대로 재사용, 다른 출처의 자산이면 손대지 않고 그렇다고 알린다.
        return AdjustOutcome(asset_id, REUSED, derived, None if linked else "EXISTING_ASSET_WITH_SAME_CONTENT")


__all__ = ["BatchAdjustService", "AdjustOutcome", "CREATED", "REUSED", "SKIPPED", "FAILED", "OPERATION", "FILE_PREFIX"]
