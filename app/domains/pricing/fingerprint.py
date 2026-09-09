"""
=========================================================
Homez OS

File : app/domains/pricing/fingerprint.py

가격 변경 승인 지문 — app/domains/marketplace_listing/fingerprint.py의
SHA-256 + json.dumps(sort_keys=True) 패턴을 그대로 재사용한다(도메인
슬라이스 간 교차 import를 피하기 위해 동일한 소형 헬퍼를 이 도메인
안에도 복제한다 — listing_wizard_csv_export.py가 status_sync_
service.py의 CSV 정책을 복제한 것과 동일한 기존 관례).

이 지문은 PriceChangeRequest의 유효성 판단에 쓰인다: 요청 시점에
스냅샷된 ProductPricing 상태 지문이 승인 시점에 다시 계산한 지문과
정확히 일치해야만 승인이 유효하다 — 요청과 승인 사이에 다른 경로로
원가·판매가가 바뀌었다면(경쟁 요청 등) 승인은 거부되고 재요청을
요구한다(자동 보정 금지, 낡은 전제로 가격을 승인하지 않는다).
=========================================================
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal


def canonical_json(data: dict) -> str:

    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_pricing_fingerprint(
    *,
    listing_id: int,
    sale_price: Decimal,
    cost_of_goods: Decimal,
    shipping_cost: Decimal,
    packaging_cost: Decimal,
    ad_cost: Decimal,
    channel_fee_rate: Decimal,
    payment_fee_rate: Decimal,
    return_reserve_rate: Decimal,
    tax_basis_rate: Decimal,
) -> str:
    """
    ProductPricing의 "상업적 정체성"(가격 변경 승인이 실제로 보호해야
    하는 것) 지문. version/updated_at 등 흐름 메타데이터는 포함하지
    않는다 — marketplace_listing.fingerprint.compute_listing_
    fingerprint가 status를 제외한 것과 동일 이유(정상 워크플로 진행
    자체가 순환적으로 자기 지문을 무효화하지 않게 한다).
    """

    return sha256_hex(canonical_json({
        "listing_id": listing_id,
        "sale_price": sale_price,
        "cost_of_goods": cost_of_goods,
        "shipping_cost": shipping_cost,
        "packaging_cost": packaging_cost,
        "ad_cost": ad_cost,
        "channel_fee_rate": channel_fee_rate,
        "payment_fee_rate": payment_fee_rate,
        "return_reserve_rate": return_reserve_rate,
        "tax_basis_rate": tax_basis_rate,
    }))


__all__ = [
    "canonical_json",
    "sha256_hex",
    "compute_pricing_fingerprint",
]
