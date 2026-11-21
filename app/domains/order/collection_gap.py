"""
=========================================================
Homez OS

File : app/domains/order/collection_gap.py

2026-11-21 — 연결을 바꿨을 때 주문 감지가 읽지 않은 구간 알리기(사용자 결정 "2-B").

새 판매계정 연결의 수집 커서는 기록이 없어서 '지금부터 1시간 전'(`INITIAL_LOOKBACK`)부터 읽기 시작한다.
그래서 옛 연결이 멈춘 뒤 새 연결이 처음 확인하기까지 들어온 주문은 자동으로 읽히지 않는다
(실제 사례: 2026-11-03 11:38 ~ 11-08 20:11 KST 결제완료 구간). 같은 판매자인지 자동으로 확인할 방법이 없어
위치를 자동으로 이어받지 않고, 구간을 화면에 알리기만 한다. 운영자가 WING에서 확인한 뒤 '확인함'을 누르면
감사 기록 한 건을 남기고 더는 알리지 않는다.

읽기는 DB만 본다 — 외부 호출 없음. 커서·연결은 바꾸지 않는다.
=========================================================
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.store_connection.model import StoreConnection

GAP_ACK_ACTION = "ORDER_COLLECTION_GAP_ACKNOWLEDGED"
GAP_ACK_ENTITY = "order_collection_cursor"
ACTIVE_CONNECTION_STATUS = "CONNECTED"


@dataclass(frozen=True)
class CollectionGap:
    cursor_id: int                       # 멈춘 옛 커서
    channel_status: str
    old_store_connection_id: int
    old_display_name: str
    gap_from: datetime                   # 옛 커서가 마지막으로 읽은 끝(UTC)
    new_store_connection_id: int
    new_display_name: str
    new_connection_created_at: datetime  # 새 연결을 만든 때(UTC) — 새 커서는 그 뒤 첫 확인 1시간 전부터 읽었다


def _acknowledged_cursor_ids(db: Session, company_id: int) -> set[int]:
    rows = db.execute(
        text("SELECT entity_id FROM audit_logs WHERE company_id = :company_id AND action = :action AND entity = :entity"),
        {"company_id": company_id, "action": GAP_ACK_ACTION, "entity": GAP_ACK_ENTITY},
    ).fetchall()
    ids: set[int] = set()
    for (entity_id,) in rows:
        try:
            ids.add(int(entity_id))
        except (TypeError, ValueError):
            continue
    return ids


def find_collection_gaps(db: Session, company_id: int, statuses) -> list[CollectionGap]:
    """옛 연결(지금 `CONNECTED`가 아님)의 커서가 마지막으로 읽은 뒤에 같은 마켓의 새 연결이 만들어졌으면 그 구간을 돌려준다.
    자동 감지가 실제로 도는 상태(`statuses`)만 본다 — 예전에 한 번만 수동으로 읽은 다른 상태 커서는 알리지 않는다."""

    wanted = {str(s) for s in statuses}
    if not wanted:
        return []
    connections = db.query(StoreConnection).filter(StoreConnection.company_id == company_id).all()
    by_id = {c.id: c for c in connections}
    active = [c for c in connections if c.connection_status == ACTIVE_CONNECTION_STATUS]
    if not active:
        return []
    cursors = (
        db.query(OrderCollectionCursor)
        .filter(OrderCollectionCursor.company_id == company_id)
        .filter(OrderCollectionCursor.channel_status.in_(sorted(wanted)))
        .filter(OrderCollectionCursor.last_successful_to.isnot(None))
        .order_by(OrderCollectionCursor.id)
        .all()
    )
    acknowledged = _acknowledged_cursor_ids(db, company_id)
    gaps: list[CollectionGap] = []
    for cursor in cursors:
        old = by_id.get(cursor.store_connection_id)
        if old is None or old.connection_status == ACTIVE_CONNECTION_STATUS or cursor.id in acknowledged:
            continue
        replacements = [
            c for c in active
            if c.id != old.id and c.marketplace_code == old.marketplace_code
            and c.created_at is not None and c.created_at >= cursor.last_successful_to
        ]
        if not replacements:
            continue
        new = min(replacements, key=lambda c: (c.created_at, c.id))
        gaps.append(CollectionGap(
            cursor_id=cursor.id, channel_status=cursor.channel_status,
            old_store_connection_id=old.id, old_display_name=old.display_name,
            gap_from=cursor.last_successful_to,
            new_store_connection_id=new.id, new_display_name=new.display_name,
            new_connection_created_at=new.created_at,
        ))
    return gaps
