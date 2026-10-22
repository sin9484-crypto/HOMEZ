"""
=========================================================
Homez OS

File : app/domains/ai_learning/suggestion_record_service.py

2026-10-23 — 마케팅 AI M4-full: **채택한 제안 기록**(`marketing_suggestion_records`)과 **전·후 성과 비교**.

  - 기록·채택 표시·옵션 연결·목록은 HOMEZ DB에만 쓴다. 쿠팡에 쓰는 일은 없다.
  - 성과 비교는 사용자가 누를 때만 기존 판매 성과 읽기(`SalesPerformanceService`, `AFTER_SALES_CHECK` 허용·읽기 전용)로 쿠팡 매출내역을 읽고,
    `suggestion_outcomes.evaluate`(순수 함수 — 판정하지 않음, 표본이 작으면 판단 불가)에 넘긴다. 읽은 매출내역은 저장하지 않는다.
  - 읽기가 끝까지 되지 않았거나(실패·허용 없음) 잘렸으면(`truncated`) **비교하지 않는다** — 일부만 보고 전·후를 말하지 않는다.
  - 회사 범위: 다른 회사의 기록은 "없음"과 같다.
=========================================================
"""

from __future__ import annotations

import re
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.exceptions import ServiceUnavailableException
from app.domains.ai_learning import suggestion_outcomes
from app.domains.ai_learning.model import MarketingSuggestionRecord

KINDS = suggestion_outcomes.KINDS
MAX_LIST = 200
KST_OFFSET = timedelta(hours=9)
NOT_READY_MESSAGE = "제안 기록 저장소가 아직 준비되지 않았습니다 — 앱을 다시 시작해 Migration 승인 화면에서 승인해 주세요(승인 전 자동 백업)."
_VENDOR_ITEM = re.compile(r"^\d{1,40}$")


def _clean_text(value: Any, limit: int, label: str, required: bool) -> str | None:
    text = re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""
    if not text:
        if required:
            raise BadRequestException(f"{label}을(를) 넣어 주세요.")
        return None
    if len(text) > limit:
        raise BadRequestException(f"{label}은(는) {limit}자까지 쓸 수 있습니다.")
    return text


def _vendor_item(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip()
    if isinstance(value, bool) or not _VENDOR_ITEM.match(text):
        raise BadRequestException("쿠팡 옵션 ID는 숫자만 쓸 수 있습니다.")
    return text


def to_dict(row: MarketingSuggestionRecord) -> dict[str, Any]:
    return {
        "id": row.id, "kind": row.kind, "vendor_item_id": row.vendor_item_id, "product_ref": row.product_ref, "suggested_value": row.suggested_value,
        "suggested_at": row.suggested_at.date().isoformat() if row.suggested_at else None, "adopted": bool(row.adopted),
        "adopted_at": row.adopted_at.isoformat(timespec="seconds") if row.adopted_at else None,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
    }


class SuggestionRecordService:

    def __init__(self, db: Session, *, now_factory=None, sales_factory=None):
        self.db = db
        self._now = now_factory or datetime.utcnow
        self._sales_factory = sales_factory
        self._table_checked = False

    def _require_table(self) -> None:
        """Migration 20261023_00을 앱에서 승인하기 전에는 표가 없다 — 서버 오류 대신 할 일을 알려 준다(표를 만들지 않는다)."""

        if self._table_checked:
            return
        if not inspect(self.db.get_bind()).has_table(MarketingSuggestionRecord.__tablename__):
            raise ServiceUnavailableException(NOT_READY_MESSAGE)
        self._table_checked = True

    def _get(self, company_id: int, record_id: int) -> MarketingSuggestionRecord:
        self._require_table()
        row = self.db.query(MarketingSuggestionRecord).filter(MarketingSuggestionRecord.id == record_id, MarketingSuggestionRecord.company_id == company_id).first()
        if row is None:
            raise NotFoundException("제안 기록을 찾을 수 없습니다.")
        return row

    def create(self, company_id: int, user_id: int, kind: str, suggested_value: Any, suggested_on: Any = None, product_ref: Any = None,
               vendor_item_id: Any = None, adopted: bool = False) -> dict[str, Any]:
        self._require_table()
        if kind not in KINDS:
            raise BadRequestException("제안 종류가 올바르지 않습니다.")
        value = _clean_text(suggested_value, 300, "제안 내용", True)
        ref = _clean_text(product_ref, 80, "상품 메모", False)
        vendor = _vendor_item(vendor_item_id)
        today = (self._now() + KST_OFFSET).date()   # 판매자가 보는 '오늘'은 한국 날짜다(저장 시각은 UTC)
        if suggested_on in (None, ""):
            day = today
        elif isinstance(suggested_on, date):
            day = suggested_on
        else:
            raise BadRequestException("반영한 날은 날짜여야 합니다.")
        if day > today:
            raise BadRequestException("반영한 날은 오늘(한국 날짜)이나 그 이전이어야 합니다.")
        if adopted is not True and adopted is not False:
            raise BadRequestException("채택 여부가 올바르지 않습니다.")
        now = self._now()
        row = MarketingSuggestionRecord(
            company_id=company_id, kind=kind, vendor_item_id=vendor, product_ref=ref, suggested_value=value,
            suggested_at=datetime(day.year, day.month, day.day), adopted=adopted, adopted_at=now if adopted else None, created_by=user_id, created_at=now,
        )
        self.db.add(row)
        self.db.commit()
        return to_dict(row)

    def update(self, company_id: int, record_id: int, *, adopted: Any = None, vendor_item_id: Any = ...) -> dict[str, Any]:
        """채택 표시를 바꾸거나 옵션 ID를 연결·해제한다. 바꿀 값만 넘긴다(넘기지 않은 값은 그대로)."""

        row = self._get(company_id, record_id)
        if adopted is not None:
            if adopted is not True and adopted is not False:
                raise BadRequestException("채택 여부가 올바르지 않습니다.")
            if adopted and not row.adopted:
                row.adopted_at = self._now()
            if not adopted:
                row.adopted_at = None
            row.adopted = adopted
        if vendor_item_id is not ...:
            row.vendor_item_id = _vendor_item(vendor_item_id)
        self.db.commit()
        return to_dict(row)

    def list(self, company_id: int, limit: int = 100) -> list[dict[str, Any]]:
        self._require_table()
        limit = max(1, min(int(limit), MAX_LIST))
        rows = (self.db.query(MarketingSuggestionRecord).filter(MarketingSuggestionRecord.company_id == company_id)
                .order_by(MarketingSuggestionRecord.id.desc()).limit(limit).all())
        return [to_dict(r) for r in rows]

    def evaluate(self, company_id: int, days: int = 30) -> dict[str, Any]:
        """쿠팡 매출내역을 읽어(허용이 있을 때만) 기록마다 전·후를 나란히 보여 준다. 읽기가 완전하지 않으면 비교하지 않는다."""

        self._require_table()
        records = [to_dict(r) for r in (self.db.query(MarketingSuggestionRecord).filter(MarketingSuggestionRecord.company_id == company_id)
                                        .order_by(MarketingSuggestionRecord.id.asc()).limit(suggestion_outcomes.MAX_RECORDS).all())]
        if not records:
            return {"read": None, "evaluation": None, "reason": "NO_RECORDS"}
        if self._sales_factory is None:
            from app.domains.order.sales_performance_service import SalesPerformanceService

            sales = SalesPerformanceService(self.db)
        else:
            sales = self._sales_factory(self.db)
        read, rows = sales.read_rows(company_id, days)
        status = {k: read.get(k) for k in ("ok", "blocked", "no_connection", "detail", "days", "calls", "truncated", "checked_at")}
        if not read.get("ok"):
            return {"read": status, "evaluation": None, "reason": "READ_FAILED"}
        if read.get("truncated"):
            return {"read": status, "evaluation": None, "reason": "READ_TRUNCATED"}
        return {"read": status, "evaluation": suggestion_outcomes.evaluate(records, rows), "reason": None}


__all__ = ["SuggestionRecordService", "to_dict", "KINDS", "MAX_LIST", "NOT_READY_MESSAGE"]
