"""
=========================================================
Homez OS

File : app/domains/media_asset/studio_service.py

2026-11-26 — 촬영장 화보(`studio_shots`)와 상세 정보 카드(`info_cards`)를 다른 사진과 같은 처리 기록·저장 규칙으로 만든다.
  - 화보: 누끼 한 장 → 고른 스타일마다 추가 이미지(ADDITIONAL), 처리 기록 STUDIO_SHOT(편집 기록은 '배경 바꾸기' — 로컬, AI 아님).
  - 카드: 핵심 카드는 누끼를 쓰고, 사용법 카드는 그림만 — 상세 이미지(DETAIL), 처리 기록 INFO_CARD(편집 기록은 '상세 이미지').
  - 바깥 호출 없음. 원본·누끼는 그대로. 회사 범위·사용 금지 사진은 거절.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.media_asset import info_cards as ic
from app.domains.media_asset import studio_shots as st
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.storage import read_media_file

STUDIO_OPERATION = "STUDIO_SHOT"
CARD_OPERATION = "INFO_CARD"


class StudioService:

    def __init__(self, db: Session, queue: ImageGenerationJobQueueService | None = None):
        self.db = db
        self.queue = queue or ImageGenerationJobQueueService(db)

    def _cutout(self, company_id: int, cutout_asset_id: int) -> tuple[MediaAsset, bytes]:
        cutout = self.queue.repository.get_media_asset_for_company(cutout_asset_id, company_id)
        if cutout is None:
            raise NotFoundException("누끼 이미지를 찾을 수 없습니다.")
        if cutout.rights_status == "RIGHTS_DENIED":
            raise BadRequestException("사용이 금지된 사진은 쓸 수 없습니다.")
        return cutout, read_media_file(self.queue.media_root, cutout.storage_path)

    def _save(self, company_id: int, user_id: int, source: MediaAsset, operation: str, model: str, purpose: str, png: bytes) -> MediaAsset:
        job = self.queue._record_processing_job(  # noqa: SLF001 — 같은 패키지의 처리 작업 기록 규칙
            operation=operation, provider_code="PILLOW_LOCAL", model_name=model, source_asset_id=source.id, requested_by=user_id,
            company_id=company_id, product_candidate_id=source.owner_id if source.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE else 0,
        )
        try:
            asset = self.queue._persist_generated_asset(  # noqa: SLF001
                company_id=company_id, owner_type=source.owner_type, owner_id=source.owner_id, purpose=purpose,
                display_order=source.display_order, image_bytes=png, self_reported_mime_type="image/png",
            )
        except Exception as exc:
            self.queue._finish_processing_job(job.id, company_id, success=False, error_reason=f"{operation} 실패: {type(exc).__name__}")  # noqa: SLF001
            raise
        asset.source_asset_id = source.id
        asset.rights_status = source.rights_status
        self.db.commit()
        self.queue._finish_processing_job(job.id, company_id, success=True, media_asset_id=asset.id)  # noqa: SLF001
        return asset

    def shots(self, company_id: int, user_id: int, cutout_asset_id: int, styles: list[str]) -> list[MediaAsset]:
        if not styles or len(set(styles)) != len(styles) or any(s not in st.STYLES for s in styles):
            raise BadRequestException(f"화보 스타일은 {', '.join(st.STYLES)} 중에서 겹치지 않게 고르세요.")
        cutout, data = self._cutout(company_id, cutout_asset_id)
        made = []
        for style in styles:
            try:
                shot = st.compose(data, style)
            except st.StudioError as exc:
                raise BadRequestException(str(exc)) from exc
            made.append(self._save(company_id, user_id, cutout, STUDIO_OPERATION, style, "ADDITIONAL", shot.png_bytes))
        return made

    def cards(self, company_id: int, user_id: int, cutout_asset_id: int) -> list[MediaAsset]:
        cutout, data = self._cutout(company_id, cutout_asset_id)
        try:
            points, steps = ic.points_card(data), ic.steps_card()
        except ic.CardError as exc:
            raise BadRequestException(str(exc)) from exc
        return [self._save(company_id, user_id, cutout, CARD_OPERATION, "POINTS", "DETAIL", points),
                self._save(company_id, user_id, cutout, CARD_OPERATION, "STEPS", "DETAIL", steps)]


    def brand_pages(self, company_id: int, user_id: int, hero_asset_id: int, scenes: list[tuple[int, str, str]]) -> list[MediaAsset]:
        """2026-11-26 상표 상세 페이지 두 장(상세 이미지). 고시는 이 상품 후보의 가장 최근 등록 마법사에서 원문 그대로 읽는다."""

        import json

        from sqlalchemy import text

        from app.domains.media_asset import brand_detail as bd

        hero, hero_bytes = self._cutout(company_id, hero_asset_id)
        tiles = []
        for asset_id, title, line in scenes:
            asset, data = self._cutout(company_id, asset_id)
            if asset.owner_id != hero.owner_id or asset.owner_type != hero.owner_type:
                raise BadRequestException(f"사진 #{asset_id}은(는) 같은 상품의 사진이 아닙니다.")
            tiles.append(bd.Scene(data, title, line))
        row = self.db.execute(
            text("SELECT channel_selections_json FROM listing_wizards WHERE company_id = :c AND product_candidate_id = :p ORDER BY id DESC LIMIT 1"),
            {"c": company_id, "p": hero.owner_id},
        ).fetchone()
        notices = []
        if row and row[0]:
            for selection in json.loads(row[0]) or []:
                notices = ((selection or {}).get("required_fields") or {}).get("notices") or []
                if notices:
                    break
        rows, ingredients, cautions = bd.notice_facts(notices)
        if not rows:
            raise BadRequestException("이 상품의 상품정보제공고시를 찾지 못해 제품 정보 쪽을 만들 수 없습니다.")
        try:
            one = bd.page_one(hero_bytes)
            two = bd.page_two(tiles, rows, ingredients, cautions)
        except bd.BrandPageError as exc:
            raise BadRequestException(str(exc)) from exc
        return [self._save(company_id, user_id, hero, CARD_OPERATION, "BRAND_PAGE_1", "DETAIL", one),
                self._save(company_id, user_id, hero, CARD_OPERATION, "BRAND_PAGE_2", "DETAIL", two)]


__all__ = ["StudioService", "STUDIO_OPERATION", "CARD_OPERATION"]
