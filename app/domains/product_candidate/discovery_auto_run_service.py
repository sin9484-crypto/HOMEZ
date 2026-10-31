"""
=========================================================
Homez OS

File : app/domains/product_candidate/discovery_auto_run_service.py

2026-10-31 — AI 통합 계획 6단계: 상품 발굴 자동 실행(하루 한 번). 3단계 탐색 흐름에서 사람이 누르던 두 가지를 정해진 시간에 대신 누른다.
  - 신제품 판정: 출시일이 적힌 후보만(바깥 호출 없음). 출시일이 없으면 손대지 않는다('모름').
  - 트렌드 조회: 규칙 검색어가 있고 짧은(3단어 이하) 후보만, 한 번에 최대 10건. 네이버 호출은 승인 센터의 '검색어 트렌드 조회' 허용이
    있어야 나가고(없으면 그 자리에서 멈추고 허용 요청이 올라간다), 긴 검색어는 사람이 고르도록 건너뛴다.
하지 않는 것: 판매 판단 평가·승인·거절·상품 등록·쿠팡 쓰기. 사람 검토를 기다리는 후보는 건드리지 않는다.

켜고 끄기는 새 표를 만들지 않고 기존 기능별 자동화 모드(`FunctionCode.PRODUCT_DISCOVERY` '상품 발굴', 기본 수동)를 쓴다 — '자동'일 때만 돈다.
비상 정지가 켜져 있으면 돌지 않는다. 실행 결과는 감사 기록(`audit_logs`, action=DISCOVERY_AUTO_RUN)에 한 줄로 남기고, 화면은 그 마지막 줄을 읽는다.
=========================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from datetime import timedelta
from typing import Callable

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("homez")

RUN_ACTION = "DISCOVERY_AUTO_RUN"
RUN_FROM_HOUR_KST = 6           # 아침 6시(한국 시각) 이후 첫 확인에서 하루 한 번
MAX_TREND_LOOKUPS_PER_RUN = 10  # 한 번에 네이버 조회 최대 건수(허용 요청 기본 10회와 같다)
_KST = timedelta(hours=9)

BLOCKED_ESTOP = "ESTOP"
BLOCKED_ALLOWANCE = "ALLOWANCE_REQUIRED"
BLOCKED_NO_KEY = "NO_KEY"


def naver_trend_gate(db: Session, company_id: int, *, actor_user_id: int | None = None):
    """네이버 검색어 트렌드 한 번을 승인 센터에 묻는 문(사람이 누를 때·자동 실행 때 같은 문)."""

    from app.domains.external_call.constants import CallKind
    from app.domains.external_call.constants import CallPurpose
    from app.domains.external_call.constants import ExternalSystem
    from app.domains.external_call.gate import ExternalCallGate

    return ExternalCallGate(
        db, company_id, ExternalSystem.NAVER, CallKind.READ, CallPurpose.TREND_LOOKUP,
        actor_user_id=actor_user_id, method="POST",
    )


def _default_adapter_factory(db: Session, company_id: int):
    from app.core.windows_credential_store import WindowsCredentialStore
    from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter

    return NaverDataLabTrendAdapter(
        WindowsCredentialStore(), NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE,
        call_gate=naver_trend_gate(db, company_id),
    )


def _default_key_check() -> bool | None:
    """네이버 키가 저장돼 있는지(값은 읽기만 하고 쓰지 않는다). 확인할 수 없으면 None."""

    from app.core.windows_credential_store import CredentialNotFoundError
    from app.core.windows_credential_store import WindowsCredentialStore
    from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter

    try:
        WindowsCredentialStore().read(NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE)
        return True
    except CredentialNotFoundError:
        return False
    except Exception:  # noqa: BLE001 — 확인할 수 없음은 '모름'
        return None


class DiscoveryAutoRunService:

    def __init__(
        self, db: Session, *, adapter_factory: Callable | None = None, key_check: Callable[[], bool | None] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ):
        self.db = db
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._key_check = key_check or _default_key_check
        self._now = now_factory or datetime.utcnow

    # ------------------------------------------------------------
    # 켜짐 여부·마지막 실행
    # ------------------------------------------------------------

    @staticmethod
    def candidate_companies(db: Session) -> list[int]:
        """'상품 발굴' 자동화 모드를 한 번이라도 정한 회사(실제로 돌지는 due()가 정한다)."""

        if not sa_inspect(db.get_bind()).has_table("function_automation_states"):
            return []
        from app.domains.automation_safety.constants import FunctionCode

        rows = db.execute(
            text("SELECT DISTINCT company_id FROM function_automation_states WHERE function_code = :f"),
            {"f": FunctionCode.PRODUCT_DISCOVERY},
        ).fetchall()
        return sorted(r[0] for r in rows if r[0] is not None)

    def mode(self, company_id: int) -> str | None:
        """'상품 발굴' 자동화 모드. 확인할 수 없으면 None(자동으로 보지 않는다)."""

        from app.domains.automation_safety.constants import FunctionCode
        from app.domains.automation_safety.service import SafetyService

        try:
            return SafetyService(self.db).get_function_mode(company_id, FunctionCode.PRODUCT_DISCOVERY)
        except Exception:  # noqa: BLE001 — 표가 없거나 읽을 수 없으면 '모름' → 돌지 않는다
            self.db.rollback()
            return None

    def last_run(self, company_id: int) -> dict | None:
        try:
            row = self.db.execute(
                text("SELECT description FROM audit_logs WHERE action = :a AND entity_id = :e ORDER BY id DESC LIMIT 1"),
                {"a": RUN_ACTION, "e": str(company_id)},
            ).fetchone()
        except Exception:  # noqa: BLE001 — 감사 기록 표가 없으면 기록 없음
            self.db.rollback()
            return None
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def due(self, company_id: int, *, now: datetime | None = None) -> bool:
        from app.domains.automation_safety.constants import FunctionMode

        if self.mode(company_id) != FunctionMode.AUTOMATIC:
            return False
        kst = (now or self._now()) + _KST
        if kst.hour < RUN_FROM_HOUR_KST:
            return False
        last = self.last_run(company_id)
        return not (last and str(last.get("at") or "")[:10] == kst.date().isoformat())

    def status(self, company_id: int) -> dict:
        from app.domains.automation_safety.constants import FunctionMode

        mode = self.mode(company_id)
        return {
            "mode": mode, "automatic": mode == FunctionMode.AUTOMATIC, "last_run": self.last_run(company_id),
            "run_from_hour": RUN_FROM_HOUR_KST, "max_lookups": MAX_TREND_LOOKUPS_PER_RUN,
        }

    # ------------------------------------------------------------
    # 실행
    # ------------------------------------------------------------

    def run(self, company_id: int, *, now: datetime | None = None) -> dict:
        from app.domains.automation_safety.service import SafetyService
        from app.domains.external_call.service import ExternalCallNotAllowed
        from app.domains.product_candidate import discovery_flow_service as flow
        from app.domains.product_candidate.new_product_analysis_refresh_service import NewProductAnalysisRefreshService
        from app.domains.product_candidate.trend_analysis_refresh_service import TrendAnalysisRefreshService

        now = now or self._now()
        summary = {
            "at": (now + _KST).strftime("%Y-%m-%dT%H:%M"), "trend_saved": 0, "trend_no_data": 0, "new_saved": 0,
            "skipped_long": 0, "skipped_empty": 0, "deferred": 0, "failed": 0, "blocked": None,
        }

        if SafetyService(self.db).is_emergency_stop_active():
            summary["blocked"] = BLOCKED_ESTOP
            return self._record(company_id, summary)

        rows = [r for r in flow.DiscoveryFlowService(self.db).list_flow(company_id)["rows"] if r["next"] in (flow.NEXT_TREND, flow.NEXT_NEW_PRODUCT)]

        # 1) 신제품 판정 — 출시일이 적힌 것만(바깥 호출 없음)
        new_service = NewProductAnalysisRefreshService(self.db)
        for r in rows:
            if r["release_date"] is None or r["is_new_product"] is not None:
                continue
            try:
                if new_service.refresh(r["candidate_id"], company_id, now=now).status == "APPLIED":
                    summary["new_saved"] += 1
            except Exception:  # noqa: BLE001 — 한 후보의 실패가 나머지를 막지 않는다
                self.db.rollback()
                summary["failed"] += 1
                logger.exception("상품 발굴 자동 실행: 후보 %s 신제품 판정 실패", r["candidate_id"])

        # 2) 트렌드 조회 — 규칙 검색어가 있고 짧은 것만, 최대 10건, 허용이 있어야 나간다
        targets = []
        for r in rows:
            if r["next"] != flow.NEXT_TREND:
                continue
            kw = r["keyword"] or {}
            if not kw.get("keyword"):
                summary["skipped_empty"] += 1
            elif kw.get("needs_shortening"):
                summary["skipped_long"] += 1
            else:
                targets.append(r)

        if targets and self._key_check() is False:
            summary["blocked"] = BLOCKED_NO_KEY
            summary["deferred"] = len(targets)
            return self._record(company_id, summary)

        trend_service = TrendAnalysisRefreshService(self.db, self._adapter_factory(self.db, company_id)) if targets else None
        for index, r in enumerate(targets):
            if index >= MAX_TREND_LOOKUPS_PER_RUN:
                summary["deferred"] = len(targets) - index
                break
            try:
                result = trend_service.refresh(r["candidate_id"], company_id)
            except ExternalCallNotAllowed as exc:
                summary["blocked"] = BLOCKED_ESTOP if exc.reason == "ESTOP_ACTIVE" else BLOCKED_ALLOWANCE
                summary["deferred"] = len(targets) - index
                break
            except Exception:  # noqa: BLE001
                self.db.rollback()
                summary["failed"] += 1
                logger.exception("상품 발굴 자동 실행: 후보 %s 트렌드 조회 실패", r["candidate_id"])
                continue
            if result.status == "APPLIED":
                summary["trend_saved"] += 1
            else:
                summary["trend_no_data"] += 1

        return self._record(company_id, summary)

    def _record(self, company_id: int, summary: dict) -> dict:
        from app.core.audit_db import write_audit_log

        write_audit_log(
            self.db, user_id=None, action=RUN_ACTION, entity="discovery_auto_run", entity_id=str(company_id),
            description=json.dumps(summary, ensure_ascii=False, separators=(",", ":")), company_id=company_id,
        )
        self.db.commit()
        return summary


__all__ = [
    "BLOCKED_ALLOWANCE",
    "BLOCKED_ESTOP",
    "BLOCKED_NO_KEY",
    "DiscoveryAutoRunService",
    "MAX_TREND_LOOKUPS_PER_RUN",
    "RUN_ACTION",
    "RUN_FROM_HOUR_KST",
    "naver_trend_gate",
]
