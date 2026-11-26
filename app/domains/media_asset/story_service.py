"""
=========================================================
Homez OS

File : app/domains/media_asset/story_service.py

2026-11-26 — **상황 장면 스토리 만들기**(규칙·그리기는 `story_scenes`). 사람이 고른 상황 장면(1~5개)을 이야기 순서로:

  1. 장면마다 기존 AI 배경 실행(`AiBackgroundService.run`)으로 추가 이미지를 만든다 — 그려 둔 배경이 있으면 다시 쓰고(호출 0회),
     없으면 그때 한 번 그린다(승인 센터 'AI 배경 그리기' 허용 1회씩). 상품은 실제 누끼, 제품 보존 검사 그대로.
  2. 모든 장면이 만들어졌을 때만, 그 사진들을 순서대로 이어 붙이고 문구를 넣은 **스토리 사진**(상세 이미지 용도)을 하나 더 만든다.
     하나라도 못 만들면 스토리 사진은 만들지 않고 이유를 알린다(빠진 장면이 있는 이야기를 만들지 않는다). 이미 만든 장면 사진은 남는다.
  3. 대표 사진은 정하지 않는다. '생성 배경' 표기는 이미지 밖(응답·기록)에 둔다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundException
from app.domains.media_asset import story_scenes as ss
from app.domains.media_asset.ai_background_service import AiBackgroundOutcome
from app.domains.media_asset.ai_background_service import AiBackgroundService
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.storage import read_media_file

OPERATION = "STORY_PAGE"
NOT_ALL_SCENES = "NOT_ALL_SCENES"


@dataclass(frozen=True)
class StoryOutcome:
    steps: list[ss.StoryStep]
    scenes: list[AiBackgroundOutcome]
    story_page: MediaAsset | None
    reason: str | None


class StoryService:

    def __init__(self, db: Session, background_service: AiBackgroundService):
        self.db = db
        self.backgrounds = background_service
        self.queue = background_service.queue

    def default_header(self, company_id: int, cutout: MediaAsset) -> str:
        if cutout.owner_type != MediaAssetOwnerType.PRODUCT_CANDIDATE or not inspect(self.db.get_bind()).has_table("product_candidates"):
            return "이럴 때 필요해요"
        row = self.db.execute(
            text("SELECT product_name FROM product_candidates WHERE id = :i AND (owner_company_id = :c OR owner_company_id IS NULL)"),
            {"i": cutout.owner_id, "c": company_id},
        ).fetchone()
        name = " ".join(str(row[0] or "").split())[: ss.HEADER_MAX] if row else ""
        return name or "이럴 때 필요해요"

    def run(self, company_id: int, requested_by: int, cutout_asset_id: int, codes: list[str], *,
            captions: dict[str, Any] | None = None, header: str | None = None) -> StoryOutcome:
        steps = ss.plan_story(codes, captions)           # 문구·장면 검사는 바깥 호출 전에
        cutout = self.queue.repository.get_media_asset_for_company(cutout_asset_id, company_id)
        if cutout is None:
            raise NotFoundException("누끼 이미지를 찾을 수 없습니다.")
        title = ss.clean_header(header if header is not None else self.default_header(company_id, cutout))

        scenes: list[AiBackgroundOutcome] = []
        for step in steps:
            outcome = self.backgrounds.run(company_id, requested_by, cutout_asset_id, [step.code], "ADDITIONAL")[0]
            scenes.append(outcome)
        made = [o for o in scenes if o.status in ("CREATED", "REUSED") and o.outcome is not None and o.outcome.asset is not None]   # 같은 사진이 이미 있으면 그것을 쓴다
        if len(made) != len(steps):
            return StoryOutcome(steps, scenes, None, NOT_ALL_SCENES)

        images = [read_media_file(self.queue.media_root, o.outcome.asset.storage_path) for o in made]
        first = made[0].outcome.asset
        job = self.queue._record_processing_job(  # noqa: SLF001 — 같은 패키지의 처리 작업 기록 규칙
            operation=OPERATION, provider_code="PILLOW_LOCAL", model_name="+".join(s.code for s in steps)[:100], source_asset_id=first.id,
            requested_by=requested_by, company_id=company_id,
            product_candidate_id=cutout.owner_id if cutout.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE else 0,
        )
        try:
            page = ss.render_story_page(title, steps, images)
            asset = self.queue._persist_generated_asset(  # noqa: SLF001
                company_id=company_id, owner_type=cutout.owner_type, owner_id=cutout.owner_id, purpose="DETAIL",
                display_order=cutout.display_order, image_bytes=page, self_reported_mime_type="image/png",
            )
        except Exception as exc:
            self.queue._finish_processing_job(job.id, company_id, success=False, error_reason=f"스토리 사진 실패: {exc}"[:300])  # noqa: SLF001
            raise
        asset.source_asset_id = first.id                  # 편집 기록이 AI 배경 장면까지 거슬러 올라가게(생성 표시)
        asset.rights_status = cutout.rights_status
        self.db.commit()
        self.queue._finish_processing_job(job.id, company_id, success=True, media_asset_id=asset.id)  # noqa: SLF001
        return StoryOutcome(steps, scenes, asset, None)


__all__ = ["StoryService", "StoryOutcome", "OPERATION", "NOT_ALL_SCENES"]
