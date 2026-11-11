"""
=========================================================
Homez OS

File : app/domains/media_asset/concept_preview_service.py

2026-11-11 — **샘플 미리보기**. 사용자 요청 "이미지 첨부하면 이미지 샘플 나오고 어떤 형식으로 생성할 것인지 샘플 선택할수있게 만들어줘".
사진 한 장으로 고를 수 있는 컨셉마다 작은 샘플(실제 상품 누끼를 그 배경에 붙인 축소본)을 만들어 돌려준다. 사용자는 샘플을 보고 고른 뒤
기존 '컨셉 만들기'(로컬)·'AI 배경 만들기'(Cloudflare) 실행으로 실제 사진을 만든다.

지키는 것:
  - **읽기 전용** — 샘플은 메모리에서만 만들어 돌려준다. 자산·파일·처리 작업 기록을 남기지 않는다(DB 쓰기 0, 파일 쓰기 0).
  - **바깥 호출 0** — 로컬 컨셉은 PC 안 합성. AI 배경 컨셉은 이미 그려 저장한 배경이 있을 때만 그것으로 샘플을 만들고,
    없으면 샘플 없이 '아직 그린 배경 없음'으로 알린다(샘플 때문에 Cloudflare를 부르지 않는다).
  - 상품은 다시 그리지 않는다 — 기존 합성(`compose_with_background`)을 작은 캔버스로 쓴다. 실제 결과의 검사는 실행 때 그대로 한다.
  - 누끼(투명 배경)가 필요하다. 고른 사진이 누끼가 아니면 그 사진에서 만든 누끼를 찾고, 없으면 `needs_cutout`으로 알린다(자동으로 누끼를 따지 않는다).
  - 회사 범위(다른 회사 사진은 "없음"), 사용 금지(RIGHTS_DENIED) 사진은 거절한다.
=========================================================
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.media_asset import ai_background_concepts as abc
from app.domains.media_asset.ai_background_service import _background_path
from app.domains.media_asset.ai_background_service import MAX_VARIATIONS
from app.domains.media_asset.composition import BackgroundKind
from app.domains.media_asset.composition import BackgroundSpec
from app.domains.media_asset.composition import compose_with_background
from app.domains.media_asset.image_concepts import PURPOSES
from app.domains.media_asset.image_concepts import list_concepts
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.storage import media_file_exists
from app.domains.media_asset.storage import read_media_file

logger = logging.getLogger("homez")

PREVIEW_SIDE = 320
JPEG_QUALITY = 82
LOCAL, AI = "LOCAL", "AI"
_MAX_DERIVED_SCAN = 30


def _has_transparency(image: Image.Image) -> bool:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        alpha = image.convert("RGBA").getchannel("A")
        low, _high = alpha.getextrema()
        return low < 255
    return False


def _small_cutout(data: bytes) -> bytes:
    """샘플용으로 누끼를 미리 줄인다(합성이 빠르다). 비율은 그대로, 키우지 않는다."""

    with Image.open(io.BytesIO(data)) as img:
        rgba = img.convert("RGBA")
    rgba.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    rgba.save(out, format="PNG")
    return out.getvalue()


def _to_data_uri(png_bytes: bytes) -> str:
    with Image.open(io.BytesIO(png_bytes)) as img:
        rgb = img.convert("RGB")
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


class ConceptPreviewService:

    def __init__(self, db: Session, queue: ImageGenerationJobQueueService | None = None):
        self.db = db
        self.queue = queue or ImageGenerationJobQueueService(db)

    # ---- 누끼 찾기 ----

    def _read_rgba(self, asset: MediaAsset) -> tuple[bytes, Image.Image] | None:
        try:
            data = read_media_file(self.queue.media_root, asset.storage_path)
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                return data, img.copy()
        except Exception:  # noqa: BLE001 — 읽을 수 없는 파일은 누끼 후보에서 뺀다
            return None

    def _find_cutout(self, company_id: int, asset: MediaAsset) -> tuple[MediaAsset, bytes] | None:
        read = self._read_rgba(asset)
        if read is not None and _has_transparency(read[1]):
            return asset, read[0]
        derived = (
            self.db.query(MediaAsset)
            .filter(MediaAsset.company_id == company_id)
            .filter(MediaAsset.source_asset_id == asset.id)
            .filter(MediaAsset.asset_role == "GENERATED")
            .filter(MediaAsset.status == "ACTIVE")
            .filter(MediaAsset.rights_status != "RIGHTS_DENIED")
            .order_by(MediaAsset.id.desc())
            .limit(_MAX_DERIVED_SCAN)
            .all()
        )
        for candidate in derived:
            got = self._read_rgba(candidate)
            if got is not None and _has_transparency(got[1]):
                return candidate, got[0]
        return None

    # ---- 샘플 ----

    def _latest_ai_version(self, company_id: int, code: str) -> int:
        latest = 0
        for version in range(1, MAX_VARIATIONS + 1):
            if not media_file_exists(self.queue.media_root, _background_path(company_id, code, version)):
                break
            latest = version
        return latest

    def _sample(self, cutout_small: bytes, spec: BackgroundSpec) -> str | None:
        try:
            composed = compose_with_background(cutout_small, spec, canvas_size=(PREVIEW_SIDE, PREVIEW_SIDE))
            return _to_data_uri(composed)
        except Exception:  # noqa: BLE001 — 한 샘플의 실패가 나머지를 막지 않는다
            logger.warning("샘플 합성 실패", exc_info=True)
            return None

    def previews(self, company_id: int, asset_id: int, purpose: str) -> dict[str, Any]:
        if purpose not in PURPOSES:
            raise BadRequestException("알 수 없는 이미지 용도입니다.")
        asset = self.queue.repository.get_media_asset_for_company(asset_id, company_id)
        if asset is None:
            raise NotFoundException("사진을 찾을 수 없습니다.")
        if asset.rights_status == "RIGHTS_DENIED":
            raise BadRequestException("사용이 금지된 사진은 샘플을 만들 수 없습니다.")

        found = self._find_cutout(company_id, asset)
        base = {"source_asset_id": asset.id, "purpose": purpose, "preview_side": PREVIEW_SIDE}
        if found is None:
            return {**base, "needs_cutout": True, "cutout_asset_id": None, "samples": []}
        cutout, cutout_bytes = found
        try:
            small = _small_cutout(cutout_bytes)
        except Exception as exc:  # noqa: BLE001
            raise BadRequestException("누끼 이미지 파일을 읽을 수 없습니다.") from exc

        samples: list[dict[str, Any]] = []
        for concept in list_concepts(purpose):
            samples.append({
                "code": concept.code, "kind": LOCAL, "label_ko": concept.label_ko, "description": concept.description,
                "processing_level": concept.processing_level, "preview": self._sample(small, concept.background_spec()),
                "needs_draw": False, "background_version": None,
            })
        if purpose in abc.PURPOSES:
            for concept in abc.list_ai_concepts():
                version = self._latest_ai_version(company_id, concept.code)
                preview = None
                if version:
                    try:
                        background = read_media_file(self.queue.media_root, _background_path(company_id, concept.code, version))
                        preview = self._sample(small, BackgroundSpec(kind=BackgroundKind.EXISTING_ASSET, existing_asset_bytes=background))
                    except Exception:  # noqa: BLE001
                        logger.warning("저장된 AI 배경을 읽지 못함", exc_info=True)
                samples.append({
                    "code": concept.code, "kind": AI, "label_ko": concept.label_ko, "description": concept.description,
                    "processing_level": concept.processing_level, "preview": preview, "needs_draw": version == 0,
                    "background_version": version or None, "prompt": concept.generation_prompt,
                })
        return {**base, "needs_cutout": False, "cutout_asset_id": cutout.id, "samples": samples}


__all__ = ["ConceptPreviewService", "PREVIEW_SIDE", "LOCAL", "AI"]
