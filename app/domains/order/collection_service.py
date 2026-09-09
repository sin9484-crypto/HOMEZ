"""Cursor and locking service for read-only channel order collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictException
from app.domains.order.adapters.coupang_collection import ALLOWED_STATUSES
from app.domains.order.collection_model import OrderCollectionCursor


INITIAL_LOOKBACK = timedelta(hours=1)
OVERLAP = timedelta(minutes=5)
STALE_LOCK_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class CollectionLease:
    cursor_id: int
    lock_token: str
    created_at_from: datetime
    created_at_to: datetime


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class OrderCollectionCursorService:
    def __init__(self, db: Session):
        self.db = db

    def _get(
        self, company_id: int, store_connection_id: int, channel_status: str,
    ) -> OrderCollectionCursor | None:
        return (
            self.db.query(OrderCollectionCursor)
            .filter(OrderCollectionCursor.company_id == company_id)
            .filter(OrderCollectionCursor.store_connection_id == store_connection_id)
            .filter(OrderCollectionCursor.channel_status == channel_status)
            .first()
        )

    def _get_or_create(
        self, company_id: int, store_connection_id: int, channel_status: str,
    ) -> OrderCollectionCursor:
        cursor = self._get(company_id, store_connection_id, channel_status)
        if cursor is not None:
            return cursor
        cursor = OrderCollectionCursor(
            company_id=company_id,
            store_connection_id=store_connection_id,
            channel_status=channel_status,
            run_status="IDLE",
        )
        self.db.add(cursor)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            cursor = self._get(company_id, store_connection_id, channel_status)
            if cursor is None:
                raise
        self.db.refresh(cursor)
        return cursor

    def acquire(
        self,
        company_id: int,
        store_connection_id: int,
        channel_status: str,
        *,
        now: datetime | None = None,
    ) -> CollectionLease:
        if channel_status not in ALLOWED_STATUSES:
            raise ValueError("INVALID_ORDER_COLLECTION_STATUS")
        now_aware = _utc_aware(now or datetime.now(timezone.utc))
        now_db = _utc_naive(now_aware)
        stale_before = now_db - STALE_LOCK_AFTER
        cursor = self._get_or_create(
            company_id, store_connection_id, channel_status,
        )
        token = uuid4().hex
        result = self.db.execute(
            update(OrderCollectionCursor)
            .where(OrderCollectionCursor.id == cursor.id)
            .where(
                or_(
                    OrderCollectionCursor.run_status != "RUNNING",
                    OrderCollectionCursor.locked_at.is_(None),
                    OrderCollectionCursor.locked_at <= stale_before,
                ),
            )
            .values(
                run_status="RUNNING", lock_token=token, locked_at=now_db,
                last_error_code=None, updated_at=now_db,
            ),
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictException("이미 이 판매계정의 주문을 가져오고 있습니다.")
        self.db.commit()
        self.db.refresh(cursor)

        if cursor.last_successful_to is None:
            start = now_aware - INITIAL_LOOKBACK
        else:
            last = _utc_aware(cursor.last_successful_to)
            if last > now_aware:
                self.fail(cursor.id, token, "FUTURE_CURSOR")
                raise ValueError("FUTURE_ORDER_COLLECTION_CURSOR")
            start = last - OVERLAP
        return CollectionLease(cursor.id, token, start, now_aware)

    def preview_window(
        self,
        company_id: int,
        store_connection_id: int,
        channel_status: str,
        *,
        now: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        """잠금이나 DB 쓰기 없이 다음 조회 구간만 계산한다."""
        if channel_status not in ALLOWED_STATUSES:
            raise ValueError("INVALID_ORDER_COLLECTION_STATUS")
        end = _utc_aware(now or datetime.now(timezone.utc))
        position = self._get(company_id, store_connection_id, channel_status)
        if position is None or position.last_successful_to is None:
            return end - INITIAL_LOOKBACK, end
        last = _utc_aware(position.last_successful_to)
        if last > end:
            raise ValueError("FUTURE_ORDER_COLLECTION_CURSOR")
        return last - OVERLAP, end

    def succeed(
        self, cursor_id: int, lock_token: str, successful_to: datetime,
    ) -> None:
        value = _utc_naive(successful_to)
        result = self.db.execute(
            update(OrderCollectionCursor)
            .where(OrderCollectionCursor.id == cursor_id)
            .where(OrderCollectionCursor.run_status == "RUNNING")
            .where(OrderCollectionCursor.lock_token == lock_token)
            .values(
                last_successful_to=value, run_status="IDLE",
                lock_token=None, locked_at=None, last_error_code=None,
                updated_at=_utc_naive(datetime.now(timezone.utc)),
            ),
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictException("주문 수집 실행권이 만료되었거나 변경되었습니다.")
        self.db.commit()

    def fail(self, cursor_id: int, lock_token: str, error_code: str) -> None:
        result = self.db.execute(
            update(OrderCollectionCursor)
            .where(OrderCollectionCursor.id == cursor_id)
            .where(OrderCollectionCursor.run_status == "RUNNING")
            .where(OrderCollectionCursor.lock_token == lock_token)
            .values(
                run_status="FAILED", lock_token=None, locked_at=None,
                last_error_code=error_code[:50],
                updated_at=_utc_naive(datetime.now(timezone.utc)),
            ),
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictException("주문 수집 실행권이 만료되었거나 변경되었습니다.")
        self.db.commit()


__all__ = [
    "CollectionLease", "INITIAL_LOOKBACK", "OVERLAP", "STALE_LOCK_AFTER",
    "OrderCollectionCursorService",
]
