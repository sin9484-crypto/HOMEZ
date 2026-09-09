"""
=========================================================
Homez OS

File : app/domains/channel_policy/fingerprint.py

CP-2 CA-1(2026-08-21 CTO 지시) — 정책 평가 입력 지문. app/domains/
marketplace_listing/fingerprint.py(승인 지문)와 정확히 같은 원칙을
채널 정책 평가에 적용한다: "지금 이 순간 다시 계산한 지문"이 평가
당시 저장된 지문과 정확히 일치해야만 그 평가가 여전히 유효하다.

포함 필드 선택 근거 — "상품·가격·옵션·이미지·배송정보가 바뀌면 기존
평가 무효"(CA-1 요구):
  - product_name/category_hint: 상품 콘텐츠·카테고리 변경 감지.
  - selection.required_fields_json: 쿠팡 Schema(coupang_seller_
    fulfilled 등)는 가격(originalPrice/salePrice)·옵션(items)·배송
    (deliveryMethod/deliveryChargeType 등)을 전부 이 JSON 안에
    구조화해 담는다(app/domains/marketplace_listing/
    required_fields_schemas.py) — 그래서 이 한 필드가 가격·옵션·
    배송정보 변경 감지를 동시에 커버한다(중복 필드를 따로 두지 않는다).
  - media_asset_ids: 해당 candidate에 연결된 미디어 자산 id 집합
    (정렬) — 이미지 변경 감지.

selection이 없는 시점(위저드 초안 단계, 아직 MarketplaceFulfillment
Selection이 실제로 만들어지기 전)의 "미리보기" 평가는 그 사실 자체를
지문에 새긴다(selection_id=None) — 그 평가는 구조적으로 실제 제출
게이트를 통과할 수 없다(제출은 항상 실제 selection을 요구하므로,
selection=None으로 계산된 지문은 제출 시점 재계산 값과 절대 같을 수
없다 — 의도된 fail-closed 설계).
=========================================================
"""

from __future__ import annotations

from app.domains.marketplace_listing.fingerprint import canonical_json
from app.domains.marketplace_listing.fingerprint import sha256_hex
from app.domains.marketplace_listing.model import MarketplaceFulfillmentSelection
from app.domains.product_candidate.model import ProductCandidate


def compute_channel_policy_input_fingerprint(
    candidate: ProductCandidate,
    selection: MarketplaceFulfillmentSelection | None,
    media_asset_ids: list[int],
) -> str:

    return sha256_hex(canonical_json({
        "product_candidate_id": candidate.id,
        "product_name": candidate.product_name,
        "category_hint": candidate.category_hint,
        "selection_id": selection.id if selection else None,
        "required_fields_json": (
            selection.required_fields_json if selection else None
        ),
        "fulfillment_mode": selection.fulfillment_mode if selection else None,
        "media_asset_ids": sorted(media_asset_ids),
    }))


__all__ = ["compute_channel_policy_input_fingerprint"]
