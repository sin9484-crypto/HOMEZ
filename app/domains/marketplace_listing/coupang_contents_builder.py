"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/coupang_contents_builder.py

2026-08-31 V7 후속 결함 수정 — required_fields["contents"](쿠팡
상세설명)를 사용자가 원본 JSON 편집 없이 구성할 수 있게 하는 안전한
빌더. app/domains/marketplace_listing/coupang_image_autofill.py와
동일한 원칙을 그대로 따른다:

- 새 공개 URL을 "만들어내지" 않는다 — 이미 업로드된 media_asset을
  R2MediaHostingService.ensure_public_url()로 조회/발급할 뿐이다
  (같은 자산을 다시 요청하면 캐시된 값을 그대로 반환 — 멱등).
- 임의 HTML을 생성하지 않는다 — coupang_submission_contract.py가
  요구하는 정확히 하나의 구조
  (`<img src="...">`, 정규식 `<img[^>]+src=["']([^"']+)["']`로
  검증됨)만 만든다.
- 회사 소유가 아니거나 존재하지 않는 media_asset이 섞여 있으면
  일부만 반영하지 않고 전체를 막는다(fail-closed) — 자동 업로드
  이미지와 동일한 정책.

2026-08-31 Phase 7.6 감사 수정 — 최초 버전은 company_id만 확인하고
(1) 그 media_asset이 실제로 이 wizard의 상품 후보 소유인지,
(2) purpose가 DETAIL인지, (3) status가 ACTIVE인지, (4) 같은 id가
중복 제출됐는지를 확인하지 않았다. 같은 회사 안의 "다른 상품"
이미지나, 대표(MAIN)/썸네일 이미지, 이미 보관 처리된(ORPHANED/
DELETED) 이미지가 상세설명에 섞여 들어갈 수 있었다 — 전부 이번
수정으로 막는다.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import CredentialStore
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.constants import MediaAssetPurpose
from app.domains.media_asset.constants import MediaAssetStatus
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.public_hosting import MediaHostingNotConfiguredError
from app.domains.media_asset.public_hosting import R2MediaHostingService


def build_contents_from_media_assets(
    db: Session,
    company_id: int,
    product_candidate_id: int,
    media_asset_ids: list[int],
    credential_store: CredentialStore,
) -> list[dict]:
    """
    선택된 media_asset들로 required_fields["contents"] 배열을
    만든다. 각 자산 하나가 contents 배열의 한 항목(IMAGE 타입)이
    된다 — 여러 이미지를 한 항목에 몰아넣지 않는다(각 항목이 실패
    지점을 명확히 가리키게 하기 위함).

    다음 중 하나라도 위반하면 전체 요청을 막는다(fail-closed,
    일부만 반영하지 않는다):
    - 이 회사 소유가 아니거나 존재하지 않는 id
    - 이 wizard의 상품 후보(product_candidate_id) 소유가 아닌 자산
      (같은 회사의 다른 상품 이미지 차단)
    - purpose가 DETAIL이 아닌 자산(대표/썸네일 이미지 차단)
    - status가 ACTIVE가 아닌 자산(ORPHANED/DELETED 차단)
    - 중복 제출된 id
    """

    if not media_asset_ids:
        return []

    duplicates = sorted({
        asset_id for asset_id in media_asset_ids
        if media_asset_ids.count(asset_id) > 1
    })
    if duplicates:
        raise BadRequestException(
            f"같은 이미지가 중복 선택되었습니다: {duplicates}",
        )

    assets = {
        a.id: a for a in db.query(MediaAsset)
        .filter(MediaAsset.company_id == company_id)
        .filter(MediaAsset.id.in_(media_asset_ids))
        .all()
    }
    missing = [i for i in media_asset_ids if i not in assets]
    if missing:
        raise BadRequestException(
            f"선택된 상세설명 이미지 중 존재하지 않는 항목이 있습니다: {missing}",
        )

    wrong_owner = [
        a.id for a in assets.values()
        if not (
            a.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE
            and a.owner_id == product_candidate_id
        )
    ]
    if wrong_owner:
        raise BadRequestException(
            f"이 상품 후보의 이미지가 아닙니다: {sorted(wrong_owner)}",
        )

    wrong_purpose = [
        a.id for a in assets.values() if a.purpose != MediaAssetPurpose.DETAIL
    ]
    if wrong_purpose:
        raise BadRequestException(
            f"상세설명에는 DETAIL(상세) 이미지만 사용할 수 있습니다: {sorted(wrong_purpose)}",
        )

    inactive = [
        a.id for a in assets.values() if a.status != MediaAssetStatus.ACTIVE
    ]
    if inactive:
        raise BadRequestException(
            f"더 이상 사용할 수 없는 이미지가 포함되어 있습니다: {sorted(inactive)}",
        )

    hosting = R2MediaHostingService(db, credential_store)

    contents: list[dict] = []
    for asset_id in media_asset_ids:
        asset = assets[asset_id]
        try:
            public_url = hosting.ensure_public_url(asset)
        except MediaHostingNotConfiguredError as exc:
            raise BadRequestException(
                "이미지 공개 호스팅이 아직 설정되지 않아 상세설명을 "
                "자동으로 구성할 수 없습니다.",
            ) from exc
        contents.append({
            "contentsType": "IMAGE",
            "contentDetails": [
                {"detailType": "IMAGE", "content": f'<img src="{public_url}">'},
            ],
        })

    return contents


__all__ = ["build_contents_from_media_assets"]
