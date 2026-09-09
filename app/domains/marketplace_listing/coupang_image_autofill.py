"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/coupang_image_autofill.py

쿠팡 Seller-Fulfilled 위저드 제출에서 required_fields["images"]가
비어 있을 때만, 위저드 2단계(selected_media_asset_ids)에서 선택한
media_asset들로부터 자동으로 채운다(R2 공개 업로드 포함,
app/domains/media_asset/public_hosting.py). 프론트엔드가 이미
images를 직접 채워 보냈으면(예: 수동 vendorPath 입력) 이 함수는
호출조차 되지 않는다 — 호출자(listing_wizard_service.py)가 그
조건을 먼저 확인한다.

첫 번째로 선택된 자산이 REPRESENTATION(대표이미지, 정확히 1개),
나머지는 DETAIL로 배정한다(쿠팡 공식 스펙 — REPRESENTATION 1개
필수 + DETAIL 최대 9개, app/domains/marketplace_listing/
required_fields_schemas.py의 CoupangProductImage 계약과 정확히
일치). 초과분은 조용히 잘라낼 뿐 오류를 내지 않는다 — 사용자가
10개 넘게 선택했다고 제출 자체를 막을 이유는 없다.

권리 미확인이거나 지원하지 않는 형식인 자산이 섞여 있으면(R2Media
HostingService.ensure_public_url()가 이미 검증) 전체 제출을 막는다
(fail-closed) — 일부만 성공한 채로 조용히 진행하지 않는다.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import CredentialStore
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.public_hosting import MediaHostingNotConfiguredError
from app.domains.media_asset.public_hosting import R2MediaHostingService

# 쿠팡 공식 상품등록 스펙: REPRESENTATION 1개 + DETAIL 최대 9개.
_MAX_TOTAL_IMAGES = 10


def autofill_coupang_images(
    db: Session,
    company_id: int,
    selected_media_asset_ids: list[int],
    credential_store: CredentialStore,
) -> list[dict]:

    if not selected_media_asset_ids:
        return []

    ids = selected_media_asset_ids[:_MAX_TOTAL_IMAGES]

    assets = {
        a.id: a for a in db.query(MediaAsset)
        .filter(MediaAsset.company_id == company_id)
        .filter(MediaAsset.id.in_(ids))
        .all()
    }
    missing = [i for i in ids if i not in assets]
    if missing:
        raise BadRequestException(
            f"선택된 이미지 중 존재하지 않는 항목이 있습니다: {missing}",
        )

    hosting = R2MediaHostingService(db, credential_store)

    images: list[dict] = []
    for order, asset_id in enumerate(ids):
        asset = assets[asset_id]
        try:
            public_url = hosting.ensure_public_url(asset)
        except MediaHostingNotConfiguredError as exc:
            raise BadRequestException(
                "이미지 공개 호스팅이 아직 설정되지 않아 자동으로 쿠팡 "
                "제출용 이미지를 준비할 수 없습니다.",
            ) from exc
        images.append({
            "imageOrder": order,
            "imageType": "REPRESENTATION" if order == 0 else "DETAIL",
            "vendorPath": public_url,
        })

    return images


__all__ = ["autofill_coupang_images"]
