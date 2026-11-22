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
from datetime import timezone
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.external_call.model import ExternalCallLog
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_after_sales_provider import KST
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

EVENT_ACTION = "COUPANG_AFTER_SALES_EVENT"
SETTLEMENT_EVERY = timedelta(hours=6)
RETURN_STATUS_DONE = "RETURNS_COMPLETED"
INQUIRY_ANSWERED = "ANSWERED"

# 2026-11-22(사용자 결정 "가 나 다") — 10분 읽기는 오늘부터 7일 전까지만 본다. 그보다 오래 열린 채 남은 반품·문의는
# 하루 한 번, 그 건이 들어온 주를 다시 읽어 끝났는지 본다(가). 쿠팡 조회로 찾지 못하는 건은 사람이 'WING에서 처리 완료'로 닫는다(나).
RECENT_DAYS = 7
RECHECK_EVERY = timedelta(hours=24)
MAX_RECHECK_WINDOWS = 3
WINDOW_BY_KIND = {"RETURN": "returns_window", "INQ_ONLINE": "online_window", "INQ_CALL": "call_window"}
CLOSE_ACTION = "COUPANG_AFTER_SALES_MANUAL_CLOSE"
_CLOSABLE_ID = re.compile(r"(RETURN|INQ_ONLINE|INQ_CALL):\d{1,20}")


def is_open_event(event: dict) -> bool:
    """끝나지 않은 반품(반품완료 전)·답하지 않은 문의인가."""

    kind = event.get("k")
    if kind == "RETURN":
        return event.get("s") != RETURN_STATUS_DONE
    if kind in ("INQ_ONLINE", "INQ_CALL"):
        return event.get("s") == "UNANSWERED"
    return False


def _kst_time(value) -> datetime | None:
    """쿠팡 시각 문자열(`2026-10-16T15:10:44+09:00` 또는 시간대 없는 한국 시각)을 KST 시각으로."""

    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(KST) if parsed.tzinfo else parsed.replace(tzinfo=KST)


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
        return (payload.get("g") or payload.get("s")) if payload else None

    def _record(self, company_id: int, entity_id: str, payload: dict, new_events: list[dict]) -> None:
        if self._latest_state(entity_id) == (payload.get("g") or payload.get("s")):
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

    def _record_returns(self, company_id: int, rows, new_events: list[dict]) -> None:
        for r in rows:
            self._record(company_id, f"RETURN:{r['receipt_id']}", {
                "k": "RETURN", "o": pack_order_id(r["order_id"]), "s": r["status"] or "UNKNOWN", "ty": r["receipt_type"],
                "t": r["created_at"], "m": r["modified_at"], "rc": r["reason_category"], "f": r["fault_by"], "n": r["count"],
                # 회수 택배: 송장번호는 긴 숫자라 중앙 가림 규칙을 피해 4자리씩 밑줄로 저장한다. 상태가 같아도 송장이 생기면 새 이벤트(g).
                "dc": r.get("return_courier"), "di": pack_order_id(r.get("return_invoice")), "dt": r.get("return_delivery_type"),
                "g": f"{r['status'] or 'UNKNOWN'}|{r.get('return_courier') or ''}|{pack_order_id(r.get('return_invoice')) or ''}",
            }, new_events)

    def _record_inquiries(self, company_id: int, rows, tag: str, new_events: list[dict]) -> None:
        for r in rows:
            self._record(company_id, f"{tag}:{r['inquiry_id']}", {
                "k": tag, "o": pack_order_id((r["order_ids"] or [None])[0]), "s": INQUIRY_ANSWERED if r["answered"] else "UNANSWERED",
                "t": r["inquiry_at"], "p": r.get("seller_product_id"),
            }, new_events)

    def _recheck_due(self, company_id: int) -> bool:
        last = (
            self.db.query(ExternalCallLog.created_at)
            .filter(ExternalCallLog.company_id == company_id, ExternalCallLog.purpose == "AFTER_SALES_CHECK",
                    ExternalCallLog.target.in_(tuple(WINDOW_BY_KIND.values())), ExternalCallLog.outcome == "SUCCEEDED")
            .order_by(ExternalCallLog.id.desc())
            .first()
        )
        return last is None or self._now() - last[0] >= RECHECK_EVERY

    def _old_open_windows(self) -> list[tuple[str, datetime]]:
        """7일보다 오래 열린 채 남은 항목마다 (읽기 종류, 기간 끝)을 만든다. 기간 끝 = 들어온 때 + 6일이라 그 건이 기간 안에 든다.
        같은 종류·같은 날로 끝나는 기간은 한 번만, 한 번에 최대 `MAX_RECHECK_WINDOWS`개(오래된 것부터)."""

        now_kst = self._now().replace(tzinfo=timezone.utc).astimezone(KST)
        cutoff = now_kst - timedelta(days=RECENT_DAYS)
        windows: dict[tuple[str, object], datetime] = {}
        for event in self.latest_events(self.db):
            what = WINDOW_BY_KIND.get(event.get("k"))
            created = _kst_time(event.get("t"))
            if what is None or created is None or created >= cutoff or not is_open_event(event):
                continue
            end = created + timedelta(days=RECENT_DAYS - 1)
            windows.setdefault((what, end.date()), end)
        ordered = sorted(windows.items(), key=lambda item: item[1])
        return [(key[0], end) for key, end in ordered[:MAX_RECHECK_WINDOWS]]

    # ------------------------------------------------------------
    # 나) 'WING에서 처리 완료' — 사람이 닫는다(쿠팡에는 아무것도 보내지 않는다)
    # ------------------------------------------------------------

    def close_manually(self, company_id: int, entity_id: str, user_id: int | None) -> dict:
        """지금 열려 있는 반품·문의 하나를 사람이 닫았다고 기록한다. 기록은 그때의 마지막 쿠팡 이벤트를 가리키므로,
        그 뒤 쿠팡에서 새 상태가 오면(다시 열림 등) 그 상태를 따른다. 쿠팡 상태 기록 자체는 바꾸지 않는다."""

        entity_id = str(entity_id or "")
        if not _CLOSABLE_ID.fullmatch(entity_id):
            raise BadRequestException("닫을 수 있는 반품·문의 번호가 아닙니다.")
        row = self.db.execute(
            text("SELECT id FROM audit_logs WHERE action = :a AND entity_id = :e AND company_id = :c ORDER BY id DESC LIMIT 1"),
            {"a": EVENT_ACTION, "e": entity_id, "c": company_id},
        ).first()
        current = next((e for e in self.latest_events(self.db, limit=5000) if e.get("entity_id") == entity_id), None)
        if row is None or current is None or not is_open_event(current):
            raise NotFoundException("지금 열려 있는 반품·문의가 아닙니다.")
        write_audit_log(
            self.db, user_id=user_id, action=CLOSE_ACTION, entity="after_sales", entity_id=entity_id,
            description=json.dumps({"ref": int(row[0]), "u": user_id}, separators=(",", ":")), company_id=company_id,
        )
        self.db.commit()
        return {"entity_id": entity_id, "closed": True}

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

        returns = read("returns", days=RECENT_DAYS)
        if returns is not None:
            self._record_returns(company_id, returns.rows, summary["new_events"])
        if summary["blocked"]:
            self.db.commit()
            return summary

        for what, tag in (("online", "INQ_ONLINE"), ("call", "INQ_CALL")):
            result = read(what, days=RECENT_DAYS)
            if summary["blocked"]:
                break
            if result is None:
                continue
            self._record_inquiries(company_id, result.rows, tag, summary["new_events"])

        # 가) 7일이 지나 열린 채 남은 반품·문의 — 하루 한 번, 그 건이 들어온 주를 다시 읽는다(같은 허용·같은 기록 규칙).
        summary["rechecked_windows"] = 0
        if not summary["blocked"] and self._recheck_due(company_id):
            for what, end in self._old_open_windows():
                result = read(what, days=RECENT_DAYS, end_at=end)
                if summary["blocked"]:
                    break
                if result is None:
                    continue
                summary["rechecked_windows"] += 1
                if what == "returns_window":
                    self._record_returns(company_id, result.rows, summary["new_events"])
                else:
                    self._record_inquiries(company_id, result.rows, "INQ_ONLINE" if what == "online_window" else "INQ_CALL", summary["new_events"])

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
            text("SELECT id, entity_id, description FROM audit_logs WHERE action = :a ORDER BY id DESC LIMIT :n"),
            {"a": EVENT_ACTION, "n": limit},
        ).fetchall()
        closes: dict[str, int] = {}
        for entity_id, description in db.execute(
            text("SELECT entity_id, description FROM audit_logs WHERE action = :a ORDER BY id DESC"), {"a": CLOSE_ACTION},
        ).fetchall():
            try:
                ref = int(json.loads(description).get("ref"))
            except (TypeError, ValueError, AttributeError):
                continue
            closes.setdefault(entity_id, ref)
        seen: dict[str, dict] = {}
        event_ids: dict[str, int] = {}
        for aid, entity_id, description in rows:
            if entity_id in seen:
                continue
            payload = parse_event(description)
            if payload is None:
                continue
            if payload.get("o"):
                payload["o"] = unpack_order_id(payload["o"])
            if payload.get("di"):
                payload["di"] = unpack_order_id(payload["di"])
            # 긴 접수번호·문의번호는 중앙 가림 규칙이 가릴 수 있어(`******3062`), 항상 따로 저장된 entity_id의 번호를 쓴다.
            if payload.get("k") in ("RETURN", "INQ_ONLINE", "INQ_CALL") and ":" in entity_id:
                payload["id"] = entity_id.split(":", 1)[1]
            seen[entity_id] = {"entity_id": entity_id, **payload}
            event_ids[entity_id] = int(aid)
        # 나) 사람이 닫은 항목: 닫을 때의 마지막 쿠팡 이벤트가 지금도 마지막이면 끝난 것으로 보여 준다(mc=1). 그 뒤 새 이벤트가 오면 쿠팡 상태를 따른다.
        for entity_id, event in seen.items():
            if entity_id in closes and closes[entity_id] >= event_ids[entity_id] and is_open_event(event):
                event["s"] = RETURN_STATUS_DONE if event.get("k") == "RETURN" else INQUIRY_ANSWERED
                event["mc"] = 1
        return list(seen.values())


__all__ = [
    "AfterSalesMonitorService", "CLOSE_ACTION", "EVENT_ACTION", "RETURN_STATUS_DONE", "is_open_event", "pack_order_id", "parse_event",
    "unpack_order_id",
]
