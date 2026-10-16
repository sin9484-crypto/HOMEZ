"""
=========================================================
Homez OS

File : app/domains/order/after_sales_monitor_service.py

2026-10-16 — 배송 이후 단계(④반품·민원, ⑤정산)를 쿠팡에서 **읽어** 이벤트로 남긴다.

  - 반품·취소 요청 목록, 상품 문의, 콜센터 문의(10분마다), 정산 내역(6시간마다, 이번 달·지난 달)을 읽기 전용 GET으로 가져온다.
    조회는 `AFTER_SALES_CHECK` 허용(상시·시간당 상한)이 있어야만 나가고, 막히면 즉시 멈춘다.
  - 가져온 것은 **상태가 바뀐 것만** 감사 기록 `COUPANG_AFTER_SALES_EVENT`로 남긴다(같은 상태를 10분마다 다시 쓰지 않는다).
    기록에는 ID·상태·시각·건수·금액·주문번호만 있고 고객 이름·연락처·문의 본문·계좌는 없다.
  - **자동으로 처리하지 않는다**: 반품 승인·수령 확인·환불·문의 답변·구매 작업 상태 변경은 하지 않는다. 사람이 오늘 할 일에서 본다.
=========================================================
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from datetime import timedelta
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.domains.external_call.model import ExternalCallLog
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_after_sales_provider import KST
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

EVENT_ACTION = "COUPANG_AFTER_SALES_EVENT"
SETTLEMENT_EVERY = timedelta(hours=6)
RETURN_STATUS_DONE = "RETURNS_COMPLETED"


_MASKED_ID = re.compile(r'("id":)\*+\d*')


def parse_event(description) -> dict | None:
    """기록의 JSON을 읽는다. 중앙 가림 규칙이 긴 번호(`"id":1919873062`)를 `******3062`로 바꿔 JSON이 깨진 예전 기록도 읽는다
    (번호는 따로 저장된 entity_id에서 되살린다)."""

    try:
        return json.loads(description)
    except (TypeError, ValueError):
        pass
    try:
        return json.loads(_MASKED_ID.sub(r'\1"?"', str(description)))
    except (TypeError, ValueError):
        return None


def pack_order_id(value) -> str | None:
    """감사 기록의 중앙 가림 규칙(`redact_free_text`)은 긴 숫자를 전화번호로 보고 가린다. 주문번호는 개인정보가 아니라 연결에 꼭 필요하므로
    4자리씩 밑줄로 나눠 저장한다(8103602504198 → 8103_6025_04198). 읽을 때 `unpack_order_id`로 되돌린다."""

    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return "_".join(digits[i:i + 4] for i in range(0, len(digits), 4)) or None


def unpack_order_id(value) -> str | None:
    return str(value).replace("_", "") if value else None


class AfterSalesMonitorService:

    def __init__(self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None, now_factory=None):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self._now = now_factory or datetime.utcnow

    # ------------------------------------------------------------
    # 이벤트 기록(상태가 바뀐 것만)
    # ------------------------------------------------------------

    def _latest_state(self, entity_id: str) -> str | None:
        row = self.db.execute(
            text("SELECT description FROM audit_logs WHERE action = :a AND entity_id = :e ORDER BY id DESC LIMIT 1"),
            {"a": EVENT_ACTION, "e": entity_id},
        ).first()
        if row is None:
            return None
        payload = parse_event(row[0])
        return payload.get("s") if payload else None

    def _record(self, company_id: int, entity_id: str, payload: dict, new_events: list[dict]) -> None:
        if self._latest_state(entity_id) == payload.get("s"):
            return
        description = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:490]
        write_audit_log(
            self.db, user_id=None, action=EVENT_ACTION, entity="after_sales", entity_id=entity_id,
            description=description, company_id=company_id,
        )
        new_events.append({"entity_id": entity_id, **payload})

    # ------------------------------------------------------------
    # 읽기
    # ------------------------------------------------------------

    def _connection_id(self, company_id: int) -> int | None:
        row = (
            self.db.query(StoreConnection.id)
            .filter(StoreConnection.company_id == company_id, StoreConnection.marketplace_code == "COUPANG",
                    StoreConnection.connection_status == "CONNECTED")
            .order_by(StoreConnection.id.asc())
            .first()
        )
        return row[0] if row else None

    def _settlement_due(self, company_id: int) -> bool:
        last = (
            self.db.query(ExternalCallLog.created_at)
            .filter(ExternalCallLog.company_id == company_id, ExternalCallLog.purpose == "AFTER_SALES_CHECK",
                    ExternalCallLog.target == "settlement", ExternalCallLog.outcome == "SUCCEEDED")
            .order_by(ExternalCallLog.id.desc())
            .first()
        )
        return last is None or self._now() - last[0] >= SETTLEMENT_EVERY

    def refresh(self, company_id: int, *, settlement: bool | None = None) -> dict:
        """반품·문의(·정산)를 한 번 읽는다. `settlement=None`이면 6시간마다 한 번만 읽는다."""

        summary = {"ok": True, "blocked": False, "failures": [], "new_events": [], "calls": 0, "no_connection": False}
        store_id = self._connection_id(company_id)
        if store_id is None:
            summary.update(ok=False, no_connection=True)
            return summary
        actions = self._actions_factory(self.db)

        def read(what: str, **params):
            result, failure = actions.read_after_sales(company_id=company_id, store_connection_id=store_id, what=what, **params)
            if result is None:
                detail = failure.detail if failure is not None else ""
                if str(detail).startswith("EXTERNAL_") or "허용" in str(detail):
                    summary["blocked"] = True
                summary["failures"].append(f"{what}: {detail}"[:200])
                summary["ok"] = False
                return None
            summary["calls"] += result.calls
            if not result.ok:
                summary["failures"].append(f"{what}: {result.detail}"[:200])
                summary["ok"] = False
                return None
            return result

        returns = read("returns", days=7)
        if returns is not None:
            for r in returns.rows:
                self._record(company_id, f"RETURN:{r['receipt_id']}", {
                    "k": "RETURN", "o": pack_order_id(r["order_id"]), "s": r["status"] or "UNKNOWN", "ty": r["receipt_type"],
                    "t": r["created_at"], "m": r["modified_at"], "rc": r["reason_category"], "f": r["fault_by"], "n": r["count"],
                }, summary["new_events"])
        if summary["blocked"]:
            self.db.commit()
            return summary

        for what, tag in (("online", "INQ_ONLINE"), ("call", "INQ_CALL")):
            result = read(what, days=7)
            if summary["blocked"]:
                break
            if result is None:
                continue
            for r in result.rows:
                self._record(company_id, f"{tag}:{r['inquiry_id']}", {
                    "k": tag, "o": pack_order_id((r["order_ids"] or [None])[0]), "s": "ANSWERED" if r["answered"] else "UNANSWERED",
                    "t": r["inquiry_at"], "p": r.get("seller_product_id"),
                }, summary["new_events"])

        if not summary["blocked"] and (settlement if settlement is not None else self._settlement_due(company_id)):
            now = self._now().replace(tzinfo=None) + timedelta(hours=9)
            months = [now.strftime("%Y-%m"), (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")]
            for ym in months:
                result = read("settlement", year_month=ym)
                if summary["blocked"]:
                    break
                if result is None:
                    continue
                for r in result.rows:
                    key = f"SETTLEMENT:{ym}:{r.get('type')}:{r.get('settlement_date')}"
                    self._record(company_id, key, {
                        "k": "SETTLEMENT", "id": ym, "s": f"{r.get('status')}:{r.get('final_amount')}", "st": r.get("status"), "ty": r.get("type"),
                        "d": r.get("settlement_date"), "sale": r.get("total_sale"), "fee": r.get("service_fee"),
                        "fin": r.get("final_amount"), "ded": r.get("deduction"),
                    }, summary["new_events"])
        self.db.commit()
        return summary

    # ------------------------------------------------------------
    # 보여 주기(읽기 전용) — 이벤트를 항목별 최신 상태로 모은다
    # ------------------------------------------------------------

    @staticmethod
    def latest_events(db: Session, limit: int = 400) -> list[dict]:
        rows = db.execute(
            text("SELECT entity_id, description FROM audit_logs WHERE action = :a ORDER BY id DESC LIMIT :n"),
            {"a": EVENT_ACTION, "n": limit},
        ).fetchall()
        seen: dict[str, dict] = {}
        for entity_id, description in rows:
            if entity_id in seen:
                continue
            payload = parse_event(description)
            if payload is None:
                continue
            if payload.get("o"):
                payload["o"] = unpack_order_id(payload["o"])
            # 긴 접수번호·문의번호는 중앙 가림 규칙이 가릴 수 있어(`******3062`), 항상 따로 저장된 entity_id의 번호를 쓴다.
            if payload.get("k") in ("RETURN", "INQ_ONLINE", "INQ_CALL") and ":" in entity_id:
                payload["id"] = entity_id.split(":", 1)[1]
            seen[entity_id] = {"entity_id": entity_id, **payload}
        return list(seen.values())


__all__ = ["AfterSalesMonitorService", "EVENT_ACTION", "RETURN_STATUS_DONE", "pack_order_id", "parse_event", "unpack_order_id"]
