"""
=========================================================
Homez OS

File : app/domains/external_call/router.py

2026-10-16 — 바깥 호출 허용 API(승인 센터가 쓴다). 모든 경로는 관리자 전용이고 회사 범위로만 동작한다.
허용을 **주는** 일(직접 허용·요청 승인)은 비밀번호 재확인이 필요하다 — 허용은 곧 바깥으로 나갈 수 있는 권한이기 때문이다.
허용을 **거두는** 일(거절·취소)은 더 안전한 방향이라 재확인 없이 된다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.dependency import get_db
from app.core.exceptions import UnauthorizedException
from app.core.guard import AdminGuard
from app.core.recent_auth import consume_recent_auth_token
from app.domains.external_call.schema import ApproveRequest
from app.domains.external_call.schema import GrantRequest
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.user.model import User

router = APIRouter(prefix="/external-calls", tags=["external-calls"])


def _require_recent_auth(token: str | None, user: User) -> None:
    if not consume_recent_auth_token(token, user.id):
        raise UnauthorizedException(
            "EXTERNAL_CALL_RECENT_AUTH_REQUIRED: 바깥 호출을 허용하려면 현재 비밀번호를 다시 확인해야 합니다.",
        )


def _describe(allowance) -> str:
    return (
        f"{allowance.system} {allowance.kind} {allowance.purpose} {allowance.mode}"
        f" count={allowance.remaining_count} per_hour={allowance.per_hour_limit}"
    )


@router.get("/summary")
def get_summary(current_user: User = Depends(AdminGuard), db: Session = Depends(get_db)):
    """승인 대기·허용 중·오늘 나간 횟수·최근 기록. 테이블이 아직 없으면 `installed=false`로 알린다(오류가 아니다)."""

    if not ExternalCallPolicyService.installed(db):
        return {"installed": False, "pending": [], "active": [], "today": {}, "recent": []}
    return ExternalCallPolicyService(db).summary(current_user.company_id)


@router.post("/allowances")
def grant_allowance(
    data: GrantRequest, current_user: User = Depends(AdminGuard), db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(default=None, alias="X-Recent-Auth-Token"),
):
    _require_recent_auth(recent_auth_token, current_user)
    allowance = ExternalCallPolicyService(db).grant(
        current_user.company_id, system=data.system, kind=data.kind, purpose=data.purpose, mode=data.mode,
        count=data.count, per_hour_limit=data.per_hour_limit, hours=data.hours, reason=data.reason, user_id=current_user.id,
    )
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id, action="EXTERNAL_CALL_GRANTED",
        entity="external_call_allowance", entity_id=str(allowance.id), description=f"바깥 호출 허용: {_describe(allowance)}",
    )
    db.commit()
    return {"id": allowance.id, "status": allowance.status}


class RenewRequest(BaseModel):
    """만료 임박·만료 상시 조회 허용을 같은 한도로 일괄 연장한다."""

    within_hours: int = Field(default=12, ge=1, le=168)
    hours: int = Field(default=24, ge=1, le=72)

    model_config = ConfigDict(extra="forbid")


@router.post("/renew-expiring")
def renew_expiring(
    data: RenewRequest, current_user: User = Depends(AdminGuard), db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(default=None, alias="X-Recent-Auth-Token"),
):
    _require_recent_auth(recent_auth_token, current_user)
    created = ExternalCallPolicyService(db).renew_expiring(
        current_user.company_id, user_id=current_user.id, within_hours=data.within_hours, hours=data.hours,
    )
    for allowance in created:
        write_audit_log(
            db, user_id=current_user.id, company_id=current_user.company_id, action="EXTERNAL_CALL_RENEWED",
            entity="external_call_allowance", entity_id=str(allowance.id),
            description=f"바깥 호출 허용 일괄 연장: {_describe(allowance)}",
        )
    db.commit()
    return {"renewed": [{"id": a.id, "purpose": a.purpose, "per_hour_limit": a.per_hour_limit} for a in created]}


@router.post("/requests/{allowance_id}/approve")
def approve_request(
    allowance_id: int, data: ApproveRequest, current_user: User = Depends(AdminGuard), db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(default=None, alias="X-Recent-Auth-Token"),
):
    _require_recent_auth(recent_auth_token, current_user)
    allowance = ExternalCallPolicyService(db).approve(
        current_user.company_id, allowance_id, count=data.count, per_hour_limit=data.per_hour_limit,
        hours=data.hours, user_id=current_user.id,
    )
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id, action="EXTERNAL_CALL_APPROVED",
        entity="external_call_allowance", entity_id=str(allowance.id), description=f"바깥 호출 요청 허용: {_describe(allowance)}",
    )
    db.commit()
    return {"id": allowance.id, "status": allowance.status}


@router.post("/requests/{allowance_id}/reject")
def reject_request(allowance_id: int, current_user: User = Depends(AdminGuard), db: Session = Depends(get_db)):
    allowance = ExternalCallPolicyService(db).reject(current_user.company_id, allowance_id, user_id=current_user.id)
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id, action="EXTERNAL_CALL_REJECTED",
        entity="external_call_allowance", entity_id=str(allowance.id), description=f"바깥 호출 요청 거절: {_describe(allowance)}",
    )
    db.commit()
    return {"id": allowance.id, "status": allowance.status}


@router.post("/allowances/{allowance_id}/revoke")
def revoke_allowance(allowance_id: int, current_user: User = Depends(AdminGuard), db: Session = Depends(get_db)):
    allowance = ExternalCallPolicyService(db).revoke(current_user.company_id, allowance_id, user_id=current_user.id)
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id, action="EXTERNAL_CALL_REVOKED",
        entity="external_call_allowance", entity_id=str(allowance.id), description=f"바깥 호출 허용 취소: {_describe(allowance)}",
    )
    db.commit()
    return {"id": allowance.id, "status": allowance.status}
