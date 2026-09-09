"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/fingerprint.py

Listing/Selection 상태 지문 계산 — app/domains/decision/service.py의
SHA-256 + json.dumps(sort_keys=True) 패턴을 그대로 재사용한다.

이 지문은 MarketplaceSubmissionApproval의 유효성 판단에 쓰인다:
승인 요청 시점에 스냅샷된 지문이 "지금 이 순간" 다시 계산한 지문과
정확히 일치해야만 그 승인이 유효하다(app/domains/marketplace_listing/
approval_service.py::current_valid_approval). Selection을 다시
선택하거나(새 id 발급) required_fields 내용이 바뀌면 지문이 달라져
기존 승인이 자동으로 무효화된다.
=========================================================
"""

import hashlib
import json

from app.domains.marketplace_listing.model import MarketplaceFulfillmentSelection
from app.domains.marketplace_listing.model import MarketplaceListing


def canonical_json(data: dict) -> str:

    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_listing_fingerprint(listing: MarketplaceListing) -> str:
    """
    2026-08-01 Gate 5(CTO 2차 지적) 반영 — status를 fingerprint에서
    제외했다. 이전에는 listing.status가 생성 이후 절대 바뀌지 않는
    결함이 있어(ListingStatus 전이 미구현) 이 필드를 포함해도 문제가
    없었다. 이제 select_fulfillment_mode/approve/submit 자체가
    listing.status를 정상적으로 전이시키므로, status를 포함하면
    "승인하는 행위 자체가 방금 승인한 그 승인을 무효화"하는 순환
    무효화 버그가 생긴다(READY에서 승인 요청 → approve()가 커밋 중에
    status를 APPROVED로 바꿈 → 재조회 시 fingerprint 불일치로 즉시
    무효 판정). 이 fingerprint가 실제로 보호해야 하는 것은 "승인
    대상의 상업적 정체성(어떤 candidate·어떤 계정·어떤 외부 리스팅)이
    바뀌었는가"이지, 이 승인 자체가 정상적으로 진행시키는 워크플로
    상태가 아니다.
    """

    return sha256_hex(canonical_json({
        "listing_id": listing.id,
        "product_candidate_id": listing.product_candidate_id,
        "marketplace_account_id": listing.marketplace_account_id,
        "external_listing_id": listing.external_listing_id,
    }))


def compute_selection_fingerprint(
    selection: MarketplaceFulfillmentSelection,
) -> str:

    return sha256_hex(canonical_json({
        "selection_id": selection.id,
        "capability_id": selection.capability_id,
        "fulfillment_mode": selection.fulfillment_mode,
        "status": selection.status,
        "required_fields_schema_name": (
            selection.required_fields_schema_name
        ),
        "required_fields_schema_version": (
            selection.required_fields_schema_version
        ),
        "required_fields_fingerprint": (
            selection.required_fields_fingerprint
        ),
    }))


__all__ = [
    "canonical_json",
    "sha256_hex",
    "compute_listing_fingerprint",
    "compute_selection_fingerprint",
]
