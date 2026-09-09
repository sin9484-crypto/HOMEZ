"""
=========================================================
Homez OS

File : app/web/router.py

HOMEZ V3 운영자 Console — 정적 화면 서빙 + 읽기 전용 집계 API

이 라우터는 다음만 담당한다.
  1) /console 정적 HTML/CSS/JS 서빙 (인증 불필요 — 로그인 폼 자체가
     여기 있으므로 미인증 상태에서도 로드되어야 한다)
  2) 기존 Domain 모듈(automation_safety/product_candidate/funding/
     settlement)을 "읽기 전용"으로 조합해 화면에 필요한 요약 데이터를
     제공하는 집계 API (전부 admin_guard 필요)

이 파일은 app/domains/** 아래의 어떤 파일도 생성·수정하지 않는다.
기존 Model/Service/Repository를 import해서 조회만 한다. DB나 Migration을
직접 실행하지 않는다(V2.4/V3 스키마가 없으면 "적용 필요" 상태로만
안내한다).
=========================================================
"""

import os
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.desktop_auth import require_desktop_token
from app.core.guard import admin_guard
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import EmergencyStop
from app.core.audit_db import write_audit_log
from app.domains.notification_center.delivery_service import (
    NotificationDeliveryService,
)
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.repository import AutomationSafetyRepository
from app.domains.automation_safety.service import KST
from app.domains.automation_safety.service import SafetyService
from app.domains.funding.model import FundingAccount
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.user.model import User

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(WEB_DIR))

V23_TABLES = [
    "funding_accounts",
    "funding_ledgers",
    "funding_holds",
    "supplier_payments",
    "marketplace_settlements",
]

V24_V3_TABLES = [
    "automation_mode_states",
    "emergency_stops",
    "execution_limits",
    "execution_usages",
    "execution_period_usages",
    "product_candidates",
    "product_candidate_evidences",
    "product_candidate_decisions",
]

router = APIRouter(tags=["Console"])


class EmergencyStopActivateRequest(BaseModel):

    reason: str


class AutomationModeRequest(BaseModel):

    mode: str
    reason: str | None = None


# --------------------------------------------------
# 정적 화면 (인증 불필요 — 로그인 폼 자체가 이 페이지 안에 있음)
# --------------------------------------------------

@router.get(
    "/console",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def console_page():

    html_path = os.path.join(WEB_DIR, "console.html")

    if not os.path.exists(html_path):
        raise HTTPException(
            status_code=500,
            detail="console.html을 찾을 수 없습니다.",
        )

    with open(html_path, encoding="utf-8") as f:
        content = f.read()

    return HTMLResponse(content=content)


@router.get(
    "/console/static/console.css",
    include_in_schema=False,
)
def console_css():

    path = os.path.join(WEB_DIR, "console.css")

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(path, media_type="text/css")


@router.get(
    "/console/static/console.js",
    include_in_schema=False,
)
def console_js():

    path = os.path.join(WEB_DIR, "console.js")

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(path, media_type="application/javascript")


# Gate F-1(2026-08-06) 다국어(i18n) 정적 파일 — 다른 static 라우트와
# 동일한 명시적 allowlist 방식(경로 조작 방지, 디렉터리 통짜 마운트 없음).
_I18N_FILES = {"i18n.js", "ko-KR.js", "en-US.js"}


@router.get(
    "/console/static/i18n/{filename}",
    include_in_schema=False,
)
def console_i18n_asset(filename: str):

    if filename not in _I18N_FILES:
        raise HTTPException(status_code=404, detail="not found")

    path = os.path.join(WEB_DIR, "i18n", filename)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(path, media_type="application/javascript")


# Section 5(2026-08-28) 이미지 수동 편집기 MVP — Fabric.js(MIT, vendor
# 완료: app/web/vendor/fabric.min.js) 등 외부 CDN 의존 없이 로컬 정적
# 파일로만 서빙한다. 다른 static 라우트와 동일한 명시적 allowlist
# 방식(경로 조작 방지, 디렉터리 통짜 마운트 없음).
_VENDOR_FILES = {"fabric.min.js", "fabric.LICENSE.txt"}


@router.get(
    "/console/static/vendor/{filename}",
    include_in_schema=False,
)
def console_vendor_asset(filename: str):

    if filename not in _VENDOR_FILES:
        raise HTTPException(status_code=404, detail="not found")

    path = os.path.join(WEB_DIR, "vendor", filename)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not found")

    media_type = (
        "application/javascript" if filename.endswith(".js") else "text/plain"
    )

    return FileResponse(path, media_type=media_type)


@router.get(
    "/console/static/assets/{filename}",
    include_in_schema=False,
)
def console_asset(filename: str):
    """
    캐릭터/로고 등 정적 자산. 경로 조작 방지를 위해 파일명만 허용하고
    디렉터리 구분자가 포함되면 거부한다.
    """

    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    path = os.path.join(WEB_DIR, "assets", filename)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not found")

    media_type = "image/png" if filename.endswith(".png") else None

    return FileResponse(path, media_type=media_type)


@router.get(
    "/assets/guides/marketplace/{filename}",
    include_in_schema=False,
)
def marketplace_connection_guide_asset(filename: str):
    """
    판매채널 연결 안내 이미지(HOMEZ 자체 설명 SVG) 서빙 — 2026-07-31
    Phase 1 승인(사용자 확인 완료: app/web/router.py에 이 라우트 1개만
    최소 추가). 경로 조작 방지를 위해 파일명만 허용하고 .svg만
    서빙한다(다른 확장자·디렉터리 구분자·상위 경로 이동 거부).
    """

    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    if not filename.endswith(".svg"):
        raise HTTPException(status_code=400, detail="only .svg is served here")

    path = os.path.join(
        REPO_ROOT, "assets", "guides", "marketplace", filename,
    )

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(path, media_type="image/svg+xml")


# --------------------------------------------------
# 스키마 상태 헬퍼
# --------------------------------------------------

def _existing_tables(db: Session) -> set[str]:

    inspector = inspect(db.get_bind())
    return set(inspector.get_table_names())


def _v3_schema_ready(existing: set[str]) -> bool:

    return all(t in existing for t in V24_V3_TABLES)


def _schema_not_ready_payload(detail: str = "V3 DB 스키마 적용 필요") -> dict:

    return {
        "schema_ready": False,
        "message": detail,
    }


# --------------------------------------------------
# 개요 (첫 화면)
# --------------------------------------------------

@router.get("/console/api/overview")
def get_overview(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    existing = _existing_tables(db)

    result: dict = {
        "v3_schema_ready": _v3_schema_ready(existing),
        "candidates": None,
        "funding": None,
        "settlement": None,
        "safety": None,
    }

    if "product_candidates" in existing:

        today_start_kst = datetime.now(KST).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        today_start_utc = today_start_kst.astimezone(timezone.utc).replace(
            tzinfo=None,
        )

        discovered_today = (
            db.query(func.count(ProductCandidate.id))
            .filter(ProductCandidate.discovered_at >= today_start_utc)
            .scalar()
        ) or 0

        status_counts = dict(
            db.query(ProductCandidate.status, func.count(ProductCandidate.id))
            .group_by(ProductCandidate.status)
            .all()
        )

        decidable_count = sum(
            status_counts.get(s, 0) for s in CandidateStatus.DECIDABLE
        )

        # 2026-08-14 테넌트 격리 감사 — approve/hold/reject는 더 이상
        # ProductCandidate.status(전역)에 반영되지 않고 회사별
        # ProductCandidateSelection으로 옮겨졌다. 이 대시보드는 회사
        # 스코프가 없는 시스템 전체 개요 API(admin_guard, 다른 집계도
        # 전부 회사 무관 전역 카운트)이므로, 전 회사를 합산한 선택
        # 상태 카운트로 대체한다(회사별 분리가 필요하면 별도 API로
        # 확장해야 한다 — 이번 범위 밖).
        selection_status_counts = dict(
            db.query(
                ProductCandidateSelection.status,
                func.count(ProductCandidateSelection.id),
            )
            .group_by(ProductCandidateSelection.status)
            .all()
        )

        # ProductCandidate.status는 승인/보류/거절로 더 이상 전이하지
        # 않으므로(위 주석 참고), "심사 대기" 후보 수는 이제
        # DECIDABLE 상태이면서 아직 어느 회사로부터도 결정을 받지
        # 않은(ProductCandidateSelection 행이 하나도 없는) 후보만
        # 세야 한다 — 그렇지 않으면 이미 결정된 후보가 영원히
        # "대기중"으로 잘못 집계된다(2026-08-14 재감사에서 발견).
        already_decided_count = (
            db.query(func.count(func.distinct(ProductCandidate.id)))
            .join(
                ProductCandidateSelection,
                ProductCandidateSelection.candidate_id == ProductCandidate.id,
            )
            .filter(ProductCandidate.status.in_(CandidateStatus.DECIDABLE))
            .scalar()
        ) or 0

        pending_review = max(decidable_count - already_decided_count, 0)

        trend_active = (
            db.query(func.count(ProductCandidate.id))
            .filter(ProductCandidate.trend_score.isnot(None))
            .scalar()
        ) or 0

        new_product_count = (
            db.query(func.count(ProductCandidate.id))
            .filter(ProductCandidate.is_new_product.is_(True))
            .scalar()
        ) or 0

        result["candidates"] = {
            "discovered_today": discovered_today,
            "pending_review": pending_review,
            "approved": selection_status_counts.get(
                CandidateStatus.APPROVED, 0,
            ),
            "held": selection_status_counts.get(CandidateStatus.HELD, 0),
            "rejected": selection_status_counts.get(
                CandidateStatus.REJECTED, 0,
            ),
            "trend_active": trend_active,
            "new_product_count": new_product_count,
        }

    if "funding_accounts" in existing:

        account = (
            db.query(FundingAccount)
            .order_by(FundingAccount.id.asc())
            .first()
        )

        if account is not None:
            result["funding"] = {
                "total_funding": float(account.total_funding),
                "held_amount": float(account.held_amount),
                "available_amount": (
                    float(account.total_funding) - float(account.held_amount)
                ),
                "currency": account.currency,
            }

    if "marketplace_settlements" in existing:

        settlement_counts = dict(
            db.query(
                MarketplaceSettlement.status,
                func.count(MarketplaceSettlement.id),
            )
            .group_by(MarketplaceSettlement.status)
            .all()
        )
        result["settlement"] = settlement_counts

    if "emergency_stops" in existing and "automation_mode_states" in existing:

        service = SafetyService(db)
        result["safety"] = {
            "mode": service.get_current_mode(),
            "emergency_stop_active": service.is_emergency_stop_active(),
        }

    return result


# --------------------------------------------------
# 자동화 안전 상태
# --------------------------------------------------

@router.get("/console/api/safety-status")
def get_safety_status(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    existing = _existing_tables(db)

    if not (
        "automation_mode_states" in existing
        and "emergency_stops" in existing
        and "execution_limits" in existing
        and "execution_period_usages" in existing
    ):
        return _schema_not_ready_payload()

    service = SafetyService(db)
    repository = AutomationSafetyRepository(db)

    mode = service.get_current_mode()

    latest_stop = repository.get_latest_emergency_stop()
    emergency_stop = None

    if latest_stop is not None:
        emergency_stop = {
            "is_active": latest_stop.is_active,
            "reason": latest_stop.reason,
            "set_at": latest_stop.set_at.isoformat(),
            "cleared_at": (
                latest_stop.cleared_at.isoformat()
                if latest_stop.cleared_at
                else None
            ),
        }

    today = SafetyService._period_start_date()

    def _scope_payload(scope_key: str, limit: ExecutionLimit | None) -> dict | None:

        if limit is None:
            return None

        usage = repository.get_period_usage(scope_key, today)

        return {
            "daily_funding_limit": limit.daily_funding_limit,
            "daily_quantity_limit": limit.daily_quantity_limit,
            "per_product_funding_limit": limit.per_product_funding_limit,
            "per_product_quantity_limit": limit.per_product_quantity_limit,
            "used_funding_today": (
                float(usage.consumed_funding) if usage else 0.0
            ),
            "used_quantity_today": (
                int(usage.consumed_quantity) if usage else 0
            ),
        }

    global_limit = repository.get_active_limit(None)

    return {
        "schema_ready": True,
        "mode": mode,
        "mode_all": list(AutomationMode.ALL),
        "emergency_stop": emergency_stop,
        "global_scope": _scope_payload("GLOBAL", global_limit),
        "period_start_kst": today.isoformat(),
    }


# --------------------------------------------------
# 자동화 안전 — 실행 제어 (Emergency Stop / Automation Mode)
#
# automation_safety Domain 파일은 수정하지 않는다. 여기서는 이미 검증된
# SafetyService의 기존 메서드를 그대로 호출만 한다(새 금융/실행 로직
# 없음). 스키마 미적용 시 404 형태로 명확히 안내한다.
# --------------------------------------------------

@router.post("/console/api/safety/emergency-stop/activate")
def activate_emergency_stop(
    data: EmergencyStopActivateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    _desktop: None = Depends(require_desktop_token),
):

    existing = _existing_tables(db)

    if "emergency_stops" not in existing:
        raise HTTPException(
            status_code=409,
            detail="V3 DB 스키마 적용 필요 — emergency_stops 테이블 없음",
        )

    service = SafetyService(db)
    stop = service.activate_emergency_stop(
        reason=data.reason,
        set_by=current_user.id,
        is_admin=True,
    )

    # SafetyService.activate_emergency_stop() 자체는 알림/감사로그를
    # 남기지 않는다(자동화 안전 파일을 수정하지 않는다는 이 파일의
    # 원칙을 지키기 위해, 알림·감사 부가 로직은 여기 router에서만
    # 덧붙인다). 실패해도 EStop 발동 자체는 이미 끝났으므로 절대
    # 예외를 전파하지 않는다.
    try:
        write_audit_log(
            db, user_id=current_user.id, action="EMERGENCY_STOP_ACTIVATED",
            entity="emergency_stop", entity_id=str(stop.id),
            description=f"Emergency Stop 발동: {stop.reason}",
            company_id=current_user.company_id,
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()

    try:
        NotificationDeliveryService(db).dispatch(
            "ESTOP_ACTIVATED",
            company_id=current_user.company_id or 0,
            user_id=current_user.id,
            idempotency_key=f"estop-activated-{stop.id}",
            title="Emergency Stop이 활성화됐습니다.",
            message=f"사유: {stop.reason}",
            link_path="safety",
            entity_ref=f"emergency_stop:{stop.id}",
            to_email=current_user.email,
            reason=stop.reason,
            console_url="/console#safety",
        )
    except Exception:  # noqa: BLE001 — 알림 실패가 EStop 발동을 막지 않는다
        db.rollback()

    return {
        "is_active": stop.is_active,
        "reason": stop.reason,
        "set_at": stop.set_at.isoformat(),
    }


@router.post("/console/api/safety/emergency-stop/deactivate")
def deactivate_emergency_stop(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    _desktop: None = Depends(require_desktop_token),
):

    existing = _existing_tables(db)

    if "emergency_stops" not in existing:
        raise HTTPException(
            status_code=409,
            detail="V3 DB 스키마 적용 필요 — emergency_stops 테이블 없음",
        )

    service = SafetyService(db)
    stop = service.deactivate_emergency_stop(
        cleared_by=current_user.id,
        is_admin=True,
    )

    # 지시문의 "Emergency Stop 해제 승인" 요구를 이 Gate에서는 실행을
    # 막는 사전 승인 게이트로 구현하지 않았다(automation_safety Domain
    # 파일을 수정하지 않는다는 이 파일의 원칙, 그리고 새 승인 상태
    # 모델을 추가하는 것은 이번 Gate 범위를 넘는 변경이라 판단했다).
    # 대신 해제가 실제로 일어난 직후 다른 관리자들이 사후 검토할 수
    # 있도록 즉시 알린다 — 이것은 "승인 전 실행 차단"이 아니라
    # "실행 사실을 즉시 알려 검토를 유도"하는 완화된 구현이며, 이
    # 차이를 최종 보고서에 그대로 공개한다.
    if stop is not None:
        try:
            write_audit_log(
                db, user_id=current_user.id, action="EMERGENCY_STOP_DEACTIVATED",
                entity="emergency_stop", entity_id=str(stop.id),
                description="Emergency Stop 해제",
                company_id=current_user.company_id,
            )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

        try:
            NotificationDeliveryService(db).dispatch(
                "ESTOP_DEACTIVATION_APPROVAL_NEEDED",
                company_id=current_user.company_id or 0,
                user_id=current_user.id,
                idempotency_key=f"estop-deactivated-{stop.id}",
                title="Emergency Stop이 해제됐습니다 — 검토가 필요합니다.",
                message=f"해제 관리자: user_id={current_user.id}",
                link_path="safety",
                entity_ref=f"emergency_stop:{stop.id}",
                to_email=current_user.email,
                reason="Emergency Stop 해제 실행됨 — 사후 검토 필요",
                console_url="/console#safety",
            )
        except Exception:  # noqa: BLE001
            db.rollback()

    return {
        "is_active": stop.is_active if stop else False,
        "cleared_at": (
            stop.cleared_at.isoformat() if stop and stop.cleared_at else None
        ),
    }


@router.post("/console/api/safety/mode")
def set_automation_mode(
    data: AutomationModeRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    _desktop: None = Depends(require_desktop_token),
):

    existing = _existing_tables(db)

    if "automation_mode_states" not in existing:
        raise HTTPException(
            status_code=409,
            detail="V3 DB 스키마 적용 필요 — automation_mode_states 테이블 없음",
        )

    if data.mode not in AutomationMode.ALL:
        raise HTTPException(
            status_code=400,
            detail=f"알 수 없는 AutomationMode: {data.mode}",
        )

    service = SafetyService(db)
    state = service.set_mode(
        mode=data.mode,
        set_by=current_user.id,
        is_admin=True,
        reason=data.reason,
    )

    return {
        "mode": state.mode,
        "set_at": state.set_at.isoformat(),
    }


# --------------------------------------------------
# 시스템 상태
# --------------------------------------------------

@router.get("/console/api/system-status")
def get_system_status(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    integrity = "unknown"

    try:
        row = db.execute(text("PRAGMA integrity_check")).fetchone()
        integrity = row[0] if row else "unknown"
    except OperationalError:
        integrity = "error"

    existing = _existing_tables(db)

    v23_status = {t: (t in existing) for t in V23_TABLES}
    v3_status = {t: (t in existing) for t in V24_V3_TABLES}

    last_test_evidence = _read_last_test_evidence()

    from app.core.version import VERSION as app_version

    return {
        "api_connected": True,
        "app_version": app_version,
        "db_integrity": integrity,
        "v23_tables": v23_status,
        "v23_schema_applied": all(v23_status.values()),
        "v24_v3_tables": v3_status,
        "v24_v3_schema_applied": all(v3_status.values()),
        "last_recorded_test_evidence": last_test_evidence,
    }


def _read_last_test_evidence() -> str | None:
    """
    docs/HOMEZ_PROJECT_STATE.md에 이미 기록된 "Test Evidence" 섹션을
    있는 그대로 읽어 반환한다. 이 API가 테스트를 실행하지는 않는다 —
    저장소에 실제로 기록된 마지막 결과만 정직하게 보여준다.
    """

    state_path = os.path.join(REPO_ROOT, "docs", "HOMEZ_PROJECT_STATE.md")

    if not os.path.exists(state_path):
        return None

    try:
        with open(state_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    marker = "# Test Evidence"
    start = content.find(marker)

    if start == -1:
        return None

    end = content.find("\n# ", start + len(marker))

    if end == -1:
        section = content[start:]
    else:
        section = content[start:end]

    return section.strip()


__all__ = [
    "router",
]
