"""
=========================================================
Homez OS

File : app/domains/order/product_status_monitor_service.py

2026-10-17 — 등록한 상품의 쿠팡 심사·판매 상태를 **읽어** 바뀐 것만 이벤트로 남긴다(연동 C단계 C1·C2).

배경: 오늘 레이펄스 등록이 쿠팡에서 승인반려가 됐는데, HOMEZ는 사용자가 WING에서 직접 보고서야 알았다(앱은 "제출 성공"만 알았다).

  - 대상: 쿠팡에 전송 성공(SUBMITTED, 상품 번호 있음)한 상품 중 최근 30일 안의 것. 한 번 읽을 때 최대 5개.
  - 아직 승인되지 않은 상품은 30분마다 읽고, 승인완료(APPROVED) 상품은 상품마다 정해진 하루 한 번 자리에서만 다시 읽는다. 삭제(DELETED)는 읽지 않는다.
  - 상태가 **바뀐 것만** 감사 기록 `COUPANG_PRODUCT_STATUS_EVENT`(`PROD:{상품번호}`)로 남긴다. 번호는 긴 숫자라 중앙 가림 규칙이 가리므로
    JSON에는 넣지 않고 entity_id에서 읽는다.
  - 승인반려(DENIED)가 되면 상태 변경 이력을 한 번 더 읽어 반려 사유(`comment`)를 `PRODWHY:{상품번호}`로 남긴다. 사유 문구는 공식 문서에 반려 사유로
    명시돼 있지 않으므로(진행 문구일 수 있음) 있는 그대로만 보여 주고, 반려 이력에 사유가 없으면 "사유 없음"으로 기록해 WING 확인을 안내한다.
  - 호출은 `PRODUCT_STATUS_CHECK` 허용(상시·시간당 상한)이 있어야만 나가고, 막히면 즉시 멈춘다. **쓰지 않는다** — 수정·재제출·판매중지는 하지 않는다.
=========================================================
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from datetime import timedelta
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.domains.external_call.constants import CallKind as ExtKind
from app.domains.external_call.constants import CallPurpose as ExtPurpose
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import ActionResult
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_product_history_provider import CoupangProductStatusProvider
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

EVENT_ACTION = "COUPANG_PRODUCT_STATUS_EVENT"
MAX_PRODUCTS_PER_RUN = 5
LOOKBACK_DAYS = 30
APPROVED_RECHECK_SLOTS = 48  # 30분 자리 48개 = 하루. 승인완료 상품은 상품마다 한 자리에서만 다시 읽는다.
_DENIED_WORDS = re.compile(r"반려|denied|deny|reject", re.IGNORECASE)


class ProductStatusActions(CoupangChannelActions):
    """상품 상태·이력 읽기 전용 실행기 — 연결 확인·자격증명·허용 확인은 기존 실행기를 그대로 쓴다."""

    def __init__(self, db: Session, **kwargs):
        kwargs.setdefault("provider_factory", CoupangProductStatusProvider)
        super().__init__(db, **kwargs)

    def read_product(self, *, company_id: int, store_connection_id: int, what: str, seller_product_id: str):
        """(결과, None) 또는 (None, 막힌/실패 ActionResult). `what`은 status|history. 허용이 없으면 호출하지 않는다."""

        method_name = {"status": "read_product_status", "history": "read_product_history"}.get(what)
        if method_name is None:
            return None, ActionResult(ActionOutcome.ACTION_REQUIRED, "알 수 없는 조회 종류")
        provider, failure = self._provider(company_id, store_connection_id)
        if failure is not None:
            return None, failure
        ticket, denied = self._external_authorize(company_id, ExtKind.READ, ExtPurpose.PRODUCT_STATUS_CHECK, target=what)
        if denied is not None:
            return None, denied
        try:
            result = getattr(provider, method_name)(seller_product_id)
        except Exception as exc:  # noqa: BLE001 — 읽기 실패는 값이 아니라 실패로 다룬다
            logger.warning("쿠팡 상품 상태 조회 예외(%s): %s", what, type(exc).__name__)
            self._external_finish(ticket, succeeded=False)
            return None, ActionResult(ActionOutcome.ACTION_REQUIRED, "조회에 실패했다")
        self._external_finish(ticket, succeeded=bool(result.ok), http_status=result.http_status)
        return result, None


def parse_event(description) -> dict | None:
    try:
        data = json.loads(description)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class ProductStatusMonitorService:

    def __init__(self, db: Session, *, actions_factory: Callable[[Session], ProductStatusActions] | None = None, now_factory=None):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: ProductStatusActions(session))
        self._now = now_factory or datetime.utcnow

    # ---- 이벤트 ----

    def _latest(self, entity_id: str) -> dict | None:
        row = self.db.execute(
            text("SELECT description FROM audit_logs WHERE action = :a AND entity_id = :e ORDER BY id DESC LIMIT 1"),
            {"a": EVENT_ACTION, "e": entity_id},
        ).first()
        return parse_event(row[0]) if row else None

    def _record(self, company_id: int, entity_id: str, payload: dict, new_events: list[dict]) -> None:
        latest = self._latest(entity_id)
        if latest is not None and latest.get("g") == payload.get("g"):
            return
        description = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:490]
        write_audit_log(
            self.db, user_id=None, action=EVENT_ACTION, entity="product_status", entity_id=entity_id,
            description=description, company_id=company_id,
        )
        new_events.append({"entity_id": entity_id, **payload})

    # ---- 대상 ----

    def _connection_id(self, company_id: int) -> int | None:
        row = (
            self.db.query(StoreConnection.id)
            .filter(StoreConnection.company_id == company_id, StoreConnection.marketplace_code == "COUPANG",
                    StoreConnection.connection_status == "CONNECTED")
            .order_by(StoreConnection.id.asc())
            .first()
        )
        return row[0] if row else None

    def targets(self, company_id: int) -> list[str]:
        since = self._now() - timedelta(days=LOOKBACK_DAYS)
        rows = (
            self.db.query(MarketplaceSubmission.external_submission_ref)
            .filter(
                MarketplaceSubmission.company_id == company_id, MarketplaceSubmission.status == "SUBMITTED",
                MarketplaceSubmission.external_submission_ref.isnot(None), MarketplaceSubmission.created_at >= since,
            )
            .order_by(MarketplaceSubmission.id.desc())
            .all()
        )
        refs: list[str] = []
        for (ref,) in rows:
            ref = str(ref).strip()
            if ref.isdigit() and ref not in refs:
                refs.append(ref)
        return refs

    def _slot(self) -> int:
        return int(self._now().timestamp() // 1800) % APPROVED_RECHECK_SLOTS

    def due(self, ref: str) -> bool:
        latest = self._latest(f"PROD:{ref}")
        if latest is None:
            return True
        status = latest.get("s")
        if status == "DELETED":
            return False
        if status == "APPROVED":
            return int(ref) % APPROVED_RECHECK_SLOTS == self._slot()
        return True

    # ---- 읽기 ----

    def refresh(self, company_id: int) -> dict:
        summary = {"ok": True, "blocked": False, "failures": [], "new_events": [], "calls": 0, "checked": 0, "no_connection": False}
        store_id = self._connection_id(company_id)
        if store_id is None:
            summary.update(ok=False, no_connection=True)
            return summary
        actions = self._actions_factory(self.db)

        def read(what: str, ref: str):
            result, failure = actions.read_product(company_id=company_id, store_connection_id=store_id, what=what, seller_product_id=ref)
            if result is None:
                detail = failure.detail if failure is not None else ""
                if str(detail).startswith("EXTERNAL_") or "허용" in str(detail):
                    summary["blocked"] = True
                summary["failures"].append(f"{what}: {detail}"[:200])
                summary["ok"] = False
                return None
            summary["calls"] += 1
            if not result.ok:
                summary["failures"].append(f"{what}: {result.detail}"[:200])
                summary["ok"] = False
                return None
            return result

        for ref in [r for r in self.targets(company_id) if self.due(r)][:MAX_PRODUCTS_PER_RUN]:
            status = read("status", ref)
            if summary["blocked"]:
                break
            if status is None:
                continue
            summary["checked"] += 1
            row = status.rows[0]
            now_text = (self._now() + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M")
            self._record(company_id, f"PROD:{ref}", {
                "k": "PROD", "s": row["status"], "r": row["raw"], "n": row["name"], "t": now_text, "g": f"{row['status']}|{row['raw']}",
            }, summary["new_events"])
            if row["status"] == "DENIED":
                self._read_reason(company_id, ref, read, summary)
                if summary["blocked"]:
                    break
        self.db.commit()
        return summary

    def _read_reason(self, company_id: int, ref: str, read, summary: dict) -> None:
        denied_event = self._latest(f"PROD:{ref}") or {}
        marker = str(denied_event.get("t") or "")
        why = self._latest(f"PRODWHY:{ref}")
        if why is not None and why.get("w") == marker:
            return  # 이 반려에 대한 사유는 이미 읽었다
        history = read("history", ref)
        if history is None:
            return
        denied = next((r for r in history.rows if _DENIED_WORDS.search(r["status"] or "")), None)
        reason = (denied or {}).get("comment") or ""
        state = hashlib.sha1(f"{marker}|{reason}".encode("utf-8")).hexdigest()[:10]
        self._record(company_id, f"PRODWHY:{ref}", {
            "k": "PRODWHY", "s": "REASON" if reason else "NO_REASON", "c": reason, "at": (denied or {}).get("at") or "",
            "w": marker, "g": state,
        }, summary["new_events"])

    # ---- 보여 주기(읽기 전용) ----

    @staticmethod
    def latest_by_product(db: Session, limit: int = 400) -> dict[str, dict]:
        """상품 번호 → {status, raw, name, checked_at, reason, reason_at, reason_state}."""

        rows = db.execute(
            text("SELECT entity_id, description FROM audit_logs WHERE action = :a ORDER BY id DESC LIMIT :n"),
            {"a": EVENT_ACTION, "n": limit},
        ).fetchall()
        seen: set[str] = set()
        products: dict[str, dict] = {}
        for entity_id, description in rows:
            if entity_id in seen:
                continue
            seen.add(entity_id)
            payload = parse_event(description)
            if payload is None or ":" not in str(entity_id):
                continue
            kind, ref = str(entity_id).split(":", 1)
            item = products.setdefault(ref, {})
            if kind == "PROD":
                item.update(status=payload.get("s"), raw=payload.get("r"), name=payload.get("n"), checked_at=payload.get("t"))
            elif kind == "PRODWHY":
                item.update(reason=payload.get("c") or "", reason_state=payload.get("s"), reason_at=payload.get("at"), reason_for=payload.get("w"))
        return products
