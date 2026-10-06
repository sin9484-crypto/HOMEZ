"""
=========================================================
Homez OS

File : app/domains/purchase_task/channel_fee_resolver.py

자동 생성 매입 작업(ORDER_AUTO)의 `coupang_fee_amount`가 항상 비어 있어
후보 평가·최종 승인이 영원히 막히던 문제를 위한 최소 연결.

새 저장소·새 계산기를 만들지 않고 이미 있는 연결만 따라간다:
  주문 상품(inventory_sku_id, channel_code, channel_sku)
    → InventoryChannelMapping(marketplace_listing_id)
    → 위저드가 만든 Listing(materialized_listing_ids_json)
    → 그 위저드의 **승인 당시** 경제성 입력(approval_package_json)의
      channel_fee_rate

**기본은 미승인이다.** 위저드 8단계 승인은 등록 패키지에 대한 승인이라 그
자체로는 이 요율을 발주 예상비용에 쓰는 승인이 아니다. 전역 스위치를 두지
않고, 운영자가 위저드 6단계에서 **그 판매계정 항목에만**
`use_channel_fee_for_purchase_estimate`를 선택하고 8단계 승인(재인증·지문·승인
이력)을 받은 경우에만 쓴다 — 승인 당시 패키지에 저장된 항목의 값만 본다.
회사·판매계정·listing 범위 안에서만 적용되고, 승인을 취소하거나 승인 후
입력이 바뀌면 효력이 사라진다. 이미 만들어진 작업의 수수료 금액과 과거
승인 스냅샷은 소급 변경하지 않는다.

선택이 켜져 있어도 다음이면 `None`이다 — 0으로 가정하지 않는다:
매핑이 정확히 하나로 특정되지 않음 / 승인된 위저드가 정확히 하나가 아님
(수수료율이 같아도 임의 선택하지 않는다) / 승인 당시 입력이 없거나 현재
입력과 다름(승인 후 수정) / 수수료율 미확정·범위 밖.

이 금액은 사용자가 채택한 요율에 의한 **예상값**이며 정산으로 검증된 실제
수수료가 아니다(감사로그에도 그렇게 남긴다).
=========================================================
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.domains.marketplace_listing.constants import WizardStatus

logger = logging.getLogger(__name__)

# 사용자가 8단계에서 승인한 뒤의 상태만 인정한다(수정 필요/승인 대기 등은 제외).
FEE_SOURCE_WIZARD_STATUSES = (
    WizardStatus.APPROVED,
    WizardStatus.SUBMITTING,
    WizardStatus.PARTIALLY_SUCCEEDED,
    WizardStatus.SUCCEEDED,
)


@dataclass(frozen=True)
class ResolvedChannelFee:
    fee_amount: float
    fee_rate: Decimal
    wizard_id: int
    listing_id: int


def resolve_channel_fee(
    db: Session, company_id: int, *, inventory_sku_id: int | None,
    channel_code: str | None, channel_sku: str | None,
    sale_amount: Decimal | float | int | None,
) -> ResolvedChannelFee | None:
    """`sale_amount`는 이미 수량이 곱해진 주문 줄 합계다 — 여기서 수량을
    다시 곱하지 않는다."""

    if (
        inventory_sku_id is None or not channel_code or not channel_sku
        or sale_amount is None
    ):
        return None

    from app.domains.inventory.model import InventoryChannelMapping
    from app.domains.marketplace_listing.model import ListingWizard

    # 누락 테이블만 구분한다(그 외 DB 오류는 삼키지 않고 그대로 올라간다).
    # 운영에서 필수 스키마가 빠졌다면 원인을 알 수 있게 로그를 남기고,
    # 수수료는 비운 채(=최종 승인 차단 유지) 돌아간다.
    inspector = inspect(db.get_bind())
    missing = [
        name for name in (
            InventoryChannelMapping.__tablename__, ListingWizard.__tablename__,
        )
        if not inspector.has_table(name)
    ]
    if missing:
        logger.warning(
            "channel_fee_resolver: required table(s) missing %s — "
            "purchase task fee left empty", missing,
        )
        return None

    mappings = (
        db.query(InventoryChannelMapping)
        .filter(
            InventoryChannelMapping.company_id == company_id,
            InventoryChannelMapping.inventory_sku_id == inventory_sku_id,
            InventoryChannelMapping.channel_code == channel_code,
            InventoryChannelMapping.channel_sku == channel_sku,
            InventoryChannelMapping.is_active.is_(True),
        )
        .all()
    )
    listing_ids = {m.marketplace_listing_id for m in mappings}
    if len(listing_ids) != 1:
        return None
    listing_id = next(iter(listing_ids))

    wizards = (
        db.query(ListingWizard)
        .filter(
            ListingWizard.company_id == company_id,
            ListingWizard.status.in_(FEE_SOURCE_WIZARD_STATUSES),
            ListingWizard.deleted_at.is_(None),
            ListingWizard.approval_fingerprint.isnot(None),
        )
        .all()
    )

    candidates: list[tuple[ListingWizard, Decimal]] = []
    for wizard in wizards:
        try:
            materialized = json.loads(wizard.materialized_listing_ids_json or "[]")
            current_input = json.loads(wizard.economics_input_json or "[]")
            package = json.loads(wizard.approval_package_json or "{}")
        except json.JSONDecodeError:
            continue
        approved_input = package.get("economics_input")
        # 승인 당시 입력이 없거나, 승인 후 현재 입력이 달라졌으면 쓰지 않는다.
        if not approved_input or approved_input != current_input:
            continue
        for row in materialized:
            if row.get("listing_id") != listing_id:
                continue
            account_id = row.get("marketplace_account_id")
            entry = next(
                (
                    e for e in approved_input
                    if e.get("marketplace_account_id") == account_id
                ),
                None,
            )
            if entry is None or entry.get("channel_fee_rate") is None:
                continue
            # 운영자가 이 판매계정 항목에 대해 명시적으로 선택하고 승인한
            # 경우만(승인 당시 패키지의 값, 정확히 True).
            if entry.get("use_channel_fee_for_purchase_estimate") is not True:
                continue
            try:
                rate = Decimal(str(entry["channel_fee_rate"]))
            except InvalidOperation:
                continue
            if not (Decimal("0") <= rate < Decimal("1")):
                continue
            candidates.append((wizard, rate))

    # 후보가 정확히 하나일 때만 쓴다. 수수료율이 같아도 둘 이상이면 어느
    # 위저드를 따를지 임의로 고르지 않는다.
    if len(candidates) != 1:
        return None
    wizard, rate = candidates[0]
    fee = (Decimal(str(sale_amount)) * rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )
    return ResolvedChannelFee(
        fee_amount=float(fee), fee_rate=rate, wizard_id=wizard.id,
        listing_id=listing_id,
    )


__all__ = ["ResolvedChannelFee", "resolve_channel_fee"]
