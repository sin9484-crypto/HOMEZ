"""
=========================================================
Homez OS

File : app/domains/account_registration/router.py

회원가입 + 승인 워크플로 API.

로그아웃 상태에서 호출되는 가입/상태확인 엔드포인트는
app/core/desktop_setup.py의 기존 Desktop 전용 방어 계층(loopback +
Origin 일치 + Desktop 세션 토큰 쿠키)을 그대로 재사용한다 — 인증
토큰이 없는 상태이므로 get_current_user를 쓸 수 없다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app.core.authorization import is_super_admin
from app.core.desktop_setup import require_desktop_mode_and_token
from app.core.desktop_setup import require_loopback
from app.core.desktop_setup import require_matching_origin
from app.core.dependency import get_db
from app.core.auth import get_current_user
from app.core.permission_check import has_permission

from app.domains.account_registration.constants import (
    PERM_USER_ACCESS_APPROVE,
    PERM_USER_ACCESS_REJECT,
    PERM_USER_ACCESS_SUSPEND,
    PERM_USER_REGISTRATION_VIEW,
    PERM_USER_ROLE_ASSIGN,
)
from app.domains.account_registration.schema import (
    ApproveRequest,
    AssignRoleRequest,
    GenericMessageResponse,
    InvitationCreateRequest,
    InvitationCreateResponse,
    InvitationSummary,
    RegisterRequest,
    RegisterResponse,
    RegistrationRequestSummary,
    RegistrationStatusResponse,
    RejectRequest,
)
from app.domains.account_registration.service import (
    RegisterOutcome,
    RegistrationActionError,
    _mask_email,
    approve_request,
    create_invitation,
    list_invitations,
    list_pending_requests,
    reactivate_user,
    register,
    reject_request,
    resolve_sqlite_path,
    revoke_invitation,
    suspend_user,
    verify_status_check_token,
    assign_role as service_assign_role,
)
from app.domains.account_registration.model import UserRegistrationRequest
from app.domains.user.model import User


router = APIRouter(prefix="/account-registration", tags=["Account Registration"])

_GENERIC_REGISTER_FAILURE = "가입 신청을 처리할 수 없습니다. 초대 코드와 입력값을 다시 확인해 주세요."
_ACTION_ERROR_MESSAGES = {
    RegistrationActionError.INVALID_TRANSITION.value: "현재 상태에서는 이 작업을 수행할 수 없습니다.",
    RegistrationActionError.ROLE_TOO_HIGH.value: "부여할 수 있는 권한 범위를 초과했습니다.",
    RegistrationActionError.UNKNOWN_ROLE.value: "존재하지 않는 역할입니다.",
    RegistrationActionError.SELF_ACTION_FORBIDDEN.value: "본인 계정에는 이 작업을 수행할 수 없습니다.",
    RegistrationActionError.LAST_SUPER_ADMIN.value: "마지막 활성 SUPER_ADMIN은 이 작업의 대상이 될 수 없습니다.",
}


def _require_approve_permission(db: Session, current_user: User) -> None:

    if not (is_super_admin(current_user) or has_permission(db, current_user, PERM_USER_ACCESS_APPROVE)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")


def _raise_action_error(exc: ValueError) -> None:

    code = exc.args[0] if exc.args else None
    detail = _ACTION_ERROR_MESSAGES.get(code, "요청을 처리할 수 없습니다.")

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _to_summary(row: UserRegistrationRequest, db: Session) -> RegistrationRequestSummary:

    user = db.query(User).filter(User.id == row.user_id).first()

    return RegistrationRequestSummary(
        id=row.id,
        display_name=(user.name if user and user.name else (user.username if user else "-")),
        masked_email=_mask_email(user.email) if user else "-",
        status=row.status,
        requested_at=row.requested_at,
    )


# --------------------------------------------------
# 1. 공개 가입
# --------------------------------------------------

@router.post(
    "/register",
    response_model=RegisterResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def register_endpoint(data: RegisterRequest):

    from app.domains.account_registration.service import issue_status_check_token

    try:
        result = register(
            resolve_sqlite_path(),
            email=data.email,
            display_name=data.display_name,
            password=data.password,
            password_confirmation=data.password_confirmation,
            invitation_code=data.invitation_code,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 비밀번호가 정책을 만족하지 않거나 확인 값이 일치하지 않습니다.",
        )

    if result.outcome in (RegisterOutcome.INVALID_INVITATION, RegisterOutcome.COMPANY_UNAVAILABLE):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_GENERIC_REGISTER_FAILURE)

    if result.outcome == RegisterOutcome.DUPLICATE_ACCOUNT:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 이메일 또는 사용자명입니다.")

    token = issue_status_check_token(result.request_id)

    return RegisterResponse(
        message="가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다.",
        status_check_token=token,
    )


@router.get(
    "/status",
    response_model=RegistrationStatusResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def registration_status_endpoint(token: str, db: Session = Depends(get_db)):

    request_id = verify_status_check_token(token)

    if request_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="유효하지 않은 확인 토큰입니다.")

    row = db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == request_id).first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="유효하지 않은 확인 토큰입니다.")

    return RegistrationStatusResponse(status=row.status)


# --------------------------------------------------
# 2. 관리자 — 승인 대기 목록/승인/거절
# --------------------------------------------------

@router.get(
    "/requests",
    response_model=list[RegistrationRequestSummary],
)
def list_requests_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    if not (is_super_admin(current_user) or has_permission(db, current_user, PERM_USER_REGISTRATION_VIEW)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    rows = list_pending_requests(db, current_user=current_user)

    return [_to_summary(row, db) for row in rows]


@router.post(
    "/{request_id}/approve",
    response_model=GenericMessageResponse,
)
def approve_endpoint(
    request_id: int,
    data: ApproveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    _require_approve_permission(db, current_user)

    try:
        approve_request(db, request_id=request_id, current_user=current_user, role_code=data.role_code)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="가입 신청을 찾을 수 없습니다.")
    except ValueError as exc:
        _raise_action_error(exc)

    return GenericMessageResponse(message="승인되었습니다.")


@router.post(
    "/{request_id}/reject",
    response_model=GenericMessageResponse,
)
def reject_endpoint(
    request_id: int,
    data: RejectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    if not (is_super_admin(current_user) or has_permission(db, current_user, PERM_USER_ACCESS_REJECT)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        reject_request(db, request_id=request_id, current_user=current_user, reason_code=data.reason_code)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="가입 신청을 찾을 수 없습니다.")
    except ValueError as exc:
        _raise_action_error(exc)

    return GenericMessageResponse(message="거절되었습니다.")


@router.post(
    "/{request_id}/suspend",
    response_model=GenericMessageResponse,
)
def suspend_endpoint(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    if not (is_super_admin(current_user) or has_permission(db, current_user, PERM_USER_ACCESS_SUSPEND)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        suspend_user(db, request_id=request_id, current_user=current_user)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.")
    except ValueError as exc:
        _raise_action_error(exc)

    return GenericMessageResponse(message="정지되었습니다.")


@router.post(
    "/{request_id}/reactivate",
    response_model=GenericMessageResponse,
)
def reactivate_endpoint(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    if not (is_super_admin(current_user) or has_permission(db, current_user, PERM_USER_ACCESS_SUSPEND)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        reactivate_user(db, request_id=request_id, current_user=current_user)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.")
    except ValueError as exc:
        _raise_action_error(exc)

    return GenericMessageResponse(message="재활성화되었습니다. 다시 로그인해야 합니다.")


@router.post(
    "/{user_id}/assign-role",
    response_model=GenericMessageResponse,
)
def assign_role_endpoint(
    user_id: int,
    data: AssignRoleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    if not (is_super_admin(current_user) or has_permission(db, current_user, PERM_USER_ROLE_ASSIGN)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        service_assign_role(db, target_user_id=user_id, current_user=current_user, role_code=data.role_code)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.")
    except ValueError as exc:
        _raise_action_error(exc)

    return GenericMessageResponse(message="역할이 변경되었습니다.")


# --------------------------------------------------
# 3. 초대 코드
# --------------------------------------------------

@router.post(
    "/invitations",
    response_model=InvitationCreateResponse,
)
def create_invitation_endpoint(
    data: InvitationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    _require_approve_permission(db, current_user)

    try:
        invitation, raw_code = create_invitation(
            db, current_user=current_user, max_role_code=data.max_role_code,
            ttl_seconds=data.ttl_seconds, max_uses=data.max_uses,
        )
    except ValueError as exc:
        _raise_action_error(exc)

    return InvitationCreateResponse(
        invitation_code=raw_code, expires_at=invitation.expires_at, max_uses=invitation.max_uses,
    )


@router.get(
    "/invitations",
    response_model=list[InvitationSummary],
)
def list_invitations_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    _require_approve_permission(db, current_user)

    rows = list_invitations(db, current_user=current_user)

    return [
        InvitationSummary(
            id=row.id, max_role_code=row.max_role_code, expires_at=row.expires_at,
            max_uses=row.max_uses, used_count=row.used_count, revoked=row.revoked_at is not None,
        )
        for row in rows
    ]


@router.delete(
    "/invitations/{invitation_id}",
    response_model=GenericMessageResponse,
)
def revoke_invitation_endpoint(
    invitation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    _require_approve_permission(db, current_user)

    try:
        revoke_invitation(db, current_user=current_user, invitation_id=invitation_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="초대 코드를 찾을 수 없습니다.")

    return GenericMessageResponse(message="폐기되었습니다.")


__all__ = ["router"]
