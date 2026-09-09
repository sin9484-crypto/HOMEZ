"""
=========================================================
Homez OS

File : app/domains/media_asset/url_import_service.py

Section 1(2026-08-28) — URL/상품페이지에서 가져온 이미지를 실제
MediaAsset으로 저장하는 서비스 계층. `url_import.py`(SSRF 안전
다운로드)와 `product_page_extraction.py`(후보 URL 추출)는 순수 로직만
갖고 있고, 이 파일이 그 결과를 `upload_original_asset()`
(job_queue_service.py)과 동일한 저장·중복제거·활성개수한도 파이프라인에
연결한다.

"상품 페이지의 모든 이미지를 무조건 저장하지 않는다"는 요구사항 그대로
— 후보 목록 조회(list_product_page_candidates)는 아무 것도 저장하지
않는다. 실제 저장은 사용자가 URL을 하나씩 명시적으로 선택해
import_image_from_url()/import_images_from_urls_batch()를 호출할 때만
일어난다.

배치 가져오기는 all-or-nothing이 아니다 — 한 URL이 실패해도(SSRF
차단, 위장 시도, 크기 초과 등) 나머지는 각자 독립적으로 계속
시도한다(사용자가 여러 후보를 한 번에 선택했을 때, 그중 하나가 이미
지워졌다고 전체를 막지 않는다는 요구사항).
=========================================================
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.media_asset.constants import (
    MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE,
)
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.constants import MediaAssetRole
from app.domains.media_asset.constants import MediaAssetSourceClassification
from app.domains.media_asset.image_validation import build_storage_path
from app.domains.media_asset.image_validation import validate_image_bytes
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.product_page_extraction import ImageCandidate
from app.domains.media_asset.product_page_extraction import (
    fetch_product_page_image_candidates,
)
from app.domains.media_asset.repository import MediaAssetRepository
from app.domains.media_asset.storage import write_media_file
from app.domains.media_asset.url_import import Transport
from app.domains.media_asset.url_import import fetch_image_safely


@dataclass(frozen=True)
class UrlImportResultItem:
    """배치 가져오기 한 건의 결과 — 성공/실패를 항목별로 구분한다."""

    source_url: str
    success: bool
    asset: MediaAsset | None = None
    error_message: str | None = None


class UrlImageImportService:

    def __init__(
        self, db: Session, transport: Transport,
        media_root: Path | None = None,
    ):

        self.db = db
        self.repository = MediaAssetRepository(db)
        self.transport = transport

        if media_root is None:
            from app.desktop.paths import get_media_dir

            media_root = get_media_dir()

        self.media_root = media_root

    # ------------------------------------------------
    # 상품 페이지 → 후보 URL 목록(저장 없음)
    # ------------------------------------------------

    def list_product_page_candidates(
        self, product_url: str,
    ) -> list[ImageCandidate]:
        """이미지를 하나도 저장하지 않는다 — 후보 URL만 반환한다.
        실제 다운로드는 사용자가 이 중 일부를 선택해
        import_image_from_url()을 호출할 때만 일어난다."""

        return fetch_product_page_image_candidates(product_url, self.transport)

    # ------------------------------------------------
    # 단건 가져오기(저장)
    # ------------------------------------------------

    def import_image_from_url(
        self,
        owner_type: str,
        owner_id: int,
        purpose: str,
        display_order: int,
        source_url: str,
        source_classification: str,
        company_id: int,
    ) -> MediaAsset:
        """upload_original_asset()(job_queue_service.py)과 동일한
        저장·중복제거·활성개수한도 파이프라인 — 차이는 이미지 바이트의
        출처가 로컬 업로드가 아니라 fetch_image_safely()로 안전하게
        받아온 URL이라는 점, 그리고 source_url/source_domain/
        source_classification을 함께 기록한다는 점뿐이다."""

        if owner_type not in MediaAssetOwnerType.ALL:
            raise BadRequestException(f"알 수 없는 owner_type입니다: {owner_type}")
        if source_classification not in MediaAssetSourceClassification.ALL:
            raise BadRequestException(
                f"알 수 없는 source_classification입니다: {source_classification}",
            )

        active_count = self.repository.count_active_media_assets_for_owner(
            company_id, owner_type, owner_id,
        )
        if active_count + 1 > MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE:
            raise BadRequestException(
                "이 상품에 대해 활성 이미지 수 한도"
                f"({MAX_ACTIVE_IMAGES_PER_PRODUCT_CANDIDATE}장)를 초과합니다.",
            )

        fetched = fetch_image_safely(source_url, self.transport)

        # url_import.py는 SSRF/위장/픽셀폭탄만 방어한다 — 로컬 업로드와
        # 동일한 해상도 정책(MIN/MAX_IMAGE_WIDTH/HEIGHT)까지는 보지
        # 않으므로, 저장 직전에 validate_image_bytes()를 다시 적용해
        # 두 경로(로컬 업로드/URL 가져오기)의 저장 정책을 통일한다.
        validated = validate_image_bytes(fetched.image_bytes)

        digest = hashlib.sha256(fetched.image_bytes).hexdigest()
        storage_path = build_storage_path(company_id, digest, validated.mime_type)

        existing = self.repository.get_media_asset_by_company_storage_path(
            company_id, storage_path,
        )
        if existing is not None:
            return existing

        write_media_file(self.media_root, storage_path, fetched.image_bytes)

        asset = MediaAsset(
            company_id=company_id,
            owner_type=owner_type,
            owner_id=owner_id,
            asset_role=MediaAssetRole.ORIGINAL,
            source_asset_id=None,
            channel_code=None,
            purpose=purpose,
            display_order=display_order,
            storage_path=storage_path,
            original_filename=None,
            mime_type=validated.mime_type,
            file_size_bytes=len(fetched.image_bytes),
            sha256_hex=digest,
            width=validated.width,
            height=validated.height,
            status="ACTIVE",
            source_url=source_url,
            source_domain=fetched.source_domain,
            source_classification=source_classification,
        )

        try:
            asset = self.repository.add_media_asset_no_commit(asset)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_media_asset_by_company_storage_path(
                company_id, storage_path,
            )
            if winner is None:
                raise

            return winner

        except Exception:
            self.db.rollback()
            raise

        return asset

    # ------------------------------------------------
    # 배치 가져오기(부분 실패 허용)
    # ------------------------------------------------

    def import_images_from_urls_batch(
        self,
        owner_type: str,
        owner_id: int,
        purpose: str,
        source_urls: list[str],
        source_classification: str,
        company_id: int,
        *,
        display_order_start: int = 0,
    ) -> list[UrlImportResultItem]:
        """여러 URL을 한 번에 가져온다 — 하나가 실패해도(UrlImportError/
        BadRequestException 등) 나머지는 계속 시도한다. 각 항목의
        display_order는 요청 목록 순서대로 display_order_start부터
        하나씩 증가한다."""

        results: list[UrlImportResultItem] = []

        for index, source_url in enumerate(source_urls):
            try:
                asset = self.import_image_from_url(
                    owner_type=owner_type,
                    owner_id=owner_id,
                    purpose=purpose,
                    display_order=display_order_start + index,
                    source_url=source_url,
                    source_classification=source_classification,
                    company_id=company_id,
                )
                results.append(
                    UrlImportResultItem(source_url=source_url, success=True, asset=asset),
                )
            except BadRequestException as exc:
                results.append(
                    UrlImportResultItem(
                        source_url=source_url, success=False, error_message=str(exc),
                    ),
                )

        return results


__all__ = [
    "UrlImageImportService",
    "UrlImportResultItem",
]
