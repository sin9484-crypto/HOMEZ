"""
=========================================================
Homez OS

File : app/domains/product_candidate/release_date_fill_service.py

2026-11-01 — 사용자 결정 D4: **온채널 상품 등록일을 그대로 출시일로 쓴다**(사람 확인 없이). 출시일이 비어 있는 온채널 후보
(`source_reference = ONCHANNEL:상품코드`, 이 회사 소유)에 온채널 판매사 상품 목록(`GET /openapi/seller/product`)의 `created_at` 날짜를 넣는다.
상세 조회에는 이 칸이 없어(공식 스펙 확인) 목록을 쪽 단위로 읽어 코드를 맞춘다.

  - 한계(🔴 실제 응답 미확인): 온채널 '등록일'은 실제 제품 출시일이 아니고, 판매사 목록의 날짜가 공급사 등록일인지 판매 신청일인지도 스펙에 없다.
    오래된 상품이 신제품으로 판정될 수 있음을 사용자가 알고 고른 결정이다. 화면에는 '온채널 등록일 기준'으로 표시한다.
  - 목록에 없는 상품(판매 신청 전 등)은 '모름' 그대로 둔다. 추측하지 않는다.
  - 날짜를 읽을 수 없거나 미래 날짜면 저장하지 않는다(자동 보정 금지).
  - 이미 적힌 출시일은 덮어쓰지 않는다(비어 있을 때만 쓰는 조건부 갱신이라 동시에 돌아도 한 번만 들어간다).
  - 바깥 호출: 쪽마다 승인 센터 허용(`ONCH`/`READ`/`SUPPLIER_RELEASE_DATE`) 1회. 허용이 없거나 비상 정지면 호출하지 않고 이유를 돌려준다.
    한 번에 최대 10쪽(쪽당 100개).
  - 출처는 후보 근거(append-only)에 남긴다. 금액·주문·재고는 건드리지 않는다.
=========================================================
"""

from __future__ import annotations

import logging
import re
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Callable

from sqlalchemy.orm import Session

logger = logging.getLogger("homez")

SOURCE_MARK = "RELEASE_DATE_SOURCE:ONCHANNEL_CREATED_AT"
BASIS_ONCHANNEL = "ONCHANNEL_CREATED_AT"
REFERENCE_PREFIX = "ONCHANNEL:"
PAGE_SIZE = 100          # 공식 스펙의 쪽당 최대
MAX_PAGES_PER_RUN = 10   # 허용 요청 기본 10회와 같다
BLOCKED_ALLOWANCE = "ALLOWANCE_REQUIRED"
BLOCKED_ESTOP = "ESTOP"
BLOCKED_NO_CONNECTION = "NO_CONNECTION"
BLOCKED_ERROR = "LOOKUP_ERROR"

_KST = timedelta(hours=9)
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$")


def parse_created_at(value) -> date | None:
    """'YYYY-MM-DD HH:MM:SS' 또는 'YYYY-MM-DD'의 날짜 부분. 그 밖의 모양·없는 날짜는 None(고쳐 읽지 않는다)."""

    match = _DATE.match(str(value or "").strip())
    if not match:
        return None
    try:
        return date(*(int(x) for x in match.groups()))
    except ValueError:
        return None


def release_gate(db: Session, company_id: int, *, actor_user_id: int | None = None):
    """온채널 상품 목록 한 쪽을 승인 센터에 묻는 문."""

    from app.domains.external_call.constants import CallKind
    from app.domains.external_call.constants import CallPurpose
    from app.domains.external_call.constants import ExternalSystem
    from app.domains.external_call.gate import ExternalCallGate

    return ExternalCallGate(
        db, company_id, ExternalSystem.ONCH, CallKind.READ, CallPurpose.SUPPLIER_RELEASE_DATE,
        actor_user_id=actor_user_id, method="GET",
    )


class ReleaseDateFillService:

    def __init__(self, db: Session, connection_service=None, *, now_factory: Callable[[], datetime] | None = None):
        self.db = db
        self._connections = connection_service
        self._now = now_factory or datetime.utcnow

    def _connection_service(self):
        if self._connections is None:
            from app.domains.purchase_task.channel_connection_service import PurchaseChannelConnectionService

            self._connections = PurchaseChannelConnectionService(self.db)
        return self._connections

    def missing(self, company_id: int) -> dict[str, list[int]]:
        """상품코드 → 출시일이 비어 있는 이 회사 소유 후보 번호들."""

        from app.domains.product_candidate.model import ProductCandidate

        rows = (
            self.db.query(ProductCandidate.id, ProductCandidate.source_reference)
            .filter(
                ProductCandidate.owner_company_id == company_id,
                ProductCandidate.release_date.is_(None),
                ProductCandidate.source_reference.like(f"{REFERENCE_PREFIX}%"),
            )
            .order_by(ProductCandidate.id)
            .all()
        )
        found: dict[str, list[int]] = {}
        for candidate_id, reference in rows:
            code = str(reference)[len(REFERENCE_PREFIX):].strip()
            if code:
                found.setdefault(code, []).append(candidate_id)
        return found

    def _save(self, candidate_ids: list[int], code: str, raw: str, value: date) -> list[int]:
        """비어 있을 때만 넣고 근거를 함께 남긴다(후보마다 한 트랜잭션)."""

        from app.domains.product_candidate.model import ProductCandidate
        from app.domains.product_candidate.model import ProductCandidateEvidence

        saved = []
        for candidate_id in candidate_ids:
            changed = (
                self.db.query(ProductCandidate)
                .filter(ProductCandidate.id == candidate_id, ProductCandidate.release_date.is_(None))
                .update({ProductCandidate.release_date: value}, synchronize_session=False)
            )
            if changed != 1:
                self.db.rollback()
                continue
            self.db.add(ProductCandidateEvidence(
                candidate_id=candidate_id, evidence_type="RAW_SOURCE",
                payload_summary=f"{SOURCE_MARK} code={code} created_at={raw}"[:2000],
            ))
            self.db.commit()
            saved.append(candidate_id)
        return saved

    def run(self, company_id: int, *, actor_user_id: int | None = None) -> dict:
        """비어 있는 온채널 후보의 출시일을 채운다. 후보·연결이 없으면 호출하지 않는다."""

        from app.domains.external_call.service import ExternalCallNotAllowed

        result = {
            "candidates": 0, "filled": 0, "filled_ids": [], "not_found": 0, "future": 0, "unreadable": 0,
            "pages_read": 0, "truncated": False, "blocked": None,
        }
        wanted = self.missing(company_id)
        result["candidates"] = sum(len(ids) for ids in wanted.values())
        if not wanted:
            return result

        service = self._connection_service()
        active = [c for c in service.list_connections(company_id, mall_code="ONCHANNEL") if getattr(c, "is_active", True)]
        if not active:
            result["blocked"] = BLOCKED_NO_CONNECTION
            return result

        gate = release_gate(self.db, company_id, actor_user_id=actor_user_id)
        today = (self._now() + _KST).date()
        remaining = dict(wanted)
        for page in range(1, MAX_PAGES_PER_RUN + 1):
            try:
                ticket = gate.open(target=f"seller/product?page={page}")
            except ExternalCallNotAllowed as exc:
                result["blocked"] = BLOCKED_ESTOP if exc.reason == "ESTOP_ACTIVE" else BLOCKED_ALLOWANCE
                break
            try:
                listing = service.list_products(active[0].id, company_id, page=page, page_size=PAGE_SIZE, triggered_by=actor_user_id)
            except Exception as exc:  # noqa: BLE001 — 원문은 숨기고 유형만 남긴다. 이미 채운 것은 그대로 둔다.
                gate.close(ticket, succeeded=False)
                logger.warning("온채널 등록일 조회 실패: %s", type(exc).__name__)
                result["blocked"] = BLOCKED_ERROR
                break
            gate.close(ticket, succeeded=True, http_status=200)
            result["pages_read"] += 1
            items = listing.items or ()
            for item in items:
                ids = remaining.pop(item.external_product_id, None)
                if not ids:
                    continue
                value = parse_created_at(item.created_at)
                if value is None:
                    result["unreadable"] += len(ids)
                elif value > today:
                    result["future"] += len(ids)
                else:
                    result["filled_ids"].extend(self._save(ids, item.external_product_id, str(item.created_at), value))
            if not remaining or len(items) < PAGE_SIZE:
                break
        else:
            result["truncated"] = bool(remaining)

        result["filled"] = len(result["filled_ids"])
        finished_listing = result["blocked"] is None and not result["truncated"]
        if finished_listing:
            result["not_found"] = sum(len(ids) for ids in remaining.values())
        if result["filled"]:
            from app.core.audit_db import write_audit_log

            write_audit_log(
                self.db, user_id=actor_user_id, company_id=company_id, action="RELEASE_DATE_FILLED",
                entity="product_candidate", entity_id="ONCHANNEL_CREATED_AT",
                description=f"온채널 등록일로 출시일 채움 {result['filled']}건(목록 {result['pages_read']}쪽)",
            )
            self.db.commit()
        return result

    def run_and_judge(self, company_id: int, *, actor_user_id: int | None = None, now: datetime | None = None) -> dict:
        """사람이 누를 때: 채운 뒤 15일 규칙까지 바로 돌린다(후보마다 누르지 않도록). 판정 실패는 다음 판정으로 넘긴다."""

        from app.domains.product_candidate.new_product_analysis_refresh_service import NewProductAnalysisRefreshService

        result = self.run(company_id, actor_user_id=actor_user_id)
        judged_new, judged_old, judge_failed = 0, 0, 0
        judge = NewProductAnalysisRefreshService(self.db)
        for candidate_id in result["filled_ids"]:
            try:
                res = judge.refresh(candidate_id, company_id, now=now)
            except Exception:  # noqa: BLE001 — 출시일은 이미 저장됐다. 판정은 '신제품 판정' 버튼·자동 실행이 이어 한다.
                self.db.rollback()
                judge_failed += 1
                logger.exception("온채널 등록일 채운 뒤 신제품 판정 실패: 후보 %s", candidate_id)
                continue
            if res.status != "APPLIED":
                judge_failed += 1
            elif res.is_new_product:
                judged_new += 1
            else:
                judged_old += 1
        return {**result, "judged_new": judged_new, "judged_old": judged_old, "judge_failed": judge_failed}


__all__ = ["BASIS_ONCHANNEL", "ReleaseDateFillService", "SOURCE_MARK", "parse_created_at", "release_gate"]
