"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_seller_wing.py

2026-10-27 — 쿠팡 답변·확인 요청의 판매자 WING 아이디(replyBy·confirmBy)를 **판매자 고정값**에서 찾는다.

실제 첫 사용 준비 점검에서 쿠팡 연결 자격증명에는 WING 아이디가 없고(업체코드·키만 저장 — 연결 화면에 그 칸이 없다),
사용자가 상품 등록 5단계에서 "고정값으로 저장"한 값(`user_settings.listing_defaults.wing_user_id`, 상품 등록의
vendorUserId로 쓰는 값)에만 있다는 것을 확인했다. 그래서 문의 답변·고객센터 답변·고객센터 문의확인은 다음 순서로 정한다.

  1) 자격증명에 `wing_user_id`가 있으면 그 값(기존 규칙 그대로 — `CoupangChannelActions._provider`가 우선한다).
  2) 없으면 그 판매 연결의 회사에서 저장된 판매자 고정값 WING 아이디가 **하나로 정해질 때만** 그 값.
     형식이 틀리거나, 사용자마다 값이 달라 하나로 정해지지 않으면 쓰지 않는다(추측하지 않는다 → 보내지 않음).

값은 메모리에서만 합치며 저장·복사·기록하지 않는다.
=========================================================
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.domains.marketplace_listing.registration_assistant import WING_ID_PATTERN
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.store_connection.model import StoreConnection
from app.domains.user_settings.model import UserSetting
from app.domains.user_settings.schema import SETTING_KEY_LISTING_DEFAULTS


def seller_wing_user_id(db: Session, store_connection_id: int) -> str | None:
    """판매 연결 → 그 회사의 판매자 고정값 WING 아이디(하나로 정해질 때만). 읽지 못하면 None."""

    try:
        store = (
            db.query(StoreConnection)
            .filter(StoreConnection.id == store_connection_id, StoreConnection.marketplace_code == "COUPANG")
            .first()
        )
        if store is None:
            return None
        rows = (
            db.query(UserSetting)
            .filter(UserSetting.company_id == store.company_id, UserSetting.setting_key == SETTING_KEY_LISTING_DEFAULTS)
            .all()
        )
    except Exception:  # noqa: BLE001 — 못 읽으면 값 없음(보내지 않음)으로 둔다.
        return None
    values: set[str] = set()
    for row in rows:
        try:
            value = json.loads(row.setting_value or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        wing = value.get("wing_user_id")
        wing = wing.strip() if isinstance(wing, str) else ""
        if wing and WING_ID_PATTERN.match(wing):
            values.add(wing)
    return next(iter(values)) if len(values) == 1 else None


def actions_with_seller_wing(session: Session) -> CoupangChannelActions:
    """답변·확인 서비스의 기본 실행기 — 자격증명에 WING 아이디가 없을 때만 판매자 고정값을 쓴다."""

    return CoupangChannelActions(session, wing_user_id=lambda store_id: seller_wing_user_id(session, store_id))


__all__ = ["actions_with_seller_wing", "seller_wing_user_id"]
