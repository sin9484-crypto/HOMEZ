"""
=========================================================
Homez OS

File : app/domains/account_recovery/router.py

계정 복구 API — 로그인 화면의 "아이디 찾기"/"비밀번호 재설정".

로그아웃 상태에서 호출되는 엔드포인트(아이디 찾기, 복구 코드 재설정,
이메일 재설정 요청/소비)는 인증 토큰이 없으므로 `get_current_user`를
쓸 수 없다 — 대신 app/core/desktop_setup.py가 이미 갖춘 Desktop 전용
방어 계층(loopback + Origin 일치 + Desktop 세션 토큰 쿠키)을 그대로
재사용한다. SUPER_ADMIN 발급 재설정만 이미 로그인된 사용자가 호출하는
일반 인증 엔드포인트다.

"reset_link"는 이 앱이 아직 실제 이메일 발송 Provider를 갖추지 않은
상태의 자리표시자(placeholder)다 — 공개 인터넷에 노출된 HTTPS 링크가
아니라, 향후 실제 Provider가 붙었을 때 그 Provider가 자신의 방식대로
완전한 URL을 구성할 수 있도록 원문 토큰만 전달하는 함수를 이 라우터가
쥐고 있다는 뜻이다(서비스 계층은 URL 스킴을 모른다).
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.desktop_setup import require_desktop_mode_and_token
from app.core.desktop_setup import require_loopback
from app.core.desktop_setup import require_matching_origin
from app.core.dependency import get_db
from app.core.guard import SuperAdminGuard
from app.core.security import sha256

from app.domains.account_recovery.model import PasswordResetToken
from app.domains.account_recovery.providers import NullPasswordResetDeliveryProvider
from app.domains.account_recovery.providers import PasswordResetDeliveryProvider
from app.domains.account_recovery.schema import (
    ForgotIdRequest,
    GenericMessageResponse,
    PasswordResetEmailStatusResponse,
    PasswordResetRequestRequest,
    PasswordResetTokenConsumeRequest,
    RecoveryCodeResetRequest,
    RecoveryCodesGenerateResponse,
    SuperAdminResetInitiateRequest,
    SuperAdminResetInitiateResponse,
)
from app.domains.account_recovery.service import (
    ForgotIdOutcome,
    SuperAdminResetError,
    consume_recovery_code,
    consume_reset_token,
    generate_recovery_codes,
    request_forgot_id,
    request_password_reset_email,
    super_admin_initiate_reset,
)
from app.domains.user.model import User


router = APIRouter(prefix="/account-recovery", tags=["Account Recovery"])

# 운영 Provider가 아직 없다 — 실제로 구성되면 이 모듈 전역을 그 구현체로
# 교체한다(app/desktop/server.py의 image_worker_starter와 동일한 주입
# 지점 철학: 기본값은 안전한 no-op, 테스트/운영은 override).
_provider: PasswordResetDeliveryProvider = NullPasswordResetDeliveryProvider()


def get_password_reset_provider() -> PasswordResetDeliveryProvider:

    return _provider


def _build_reset_link(token: str) -> str:
    """
    실제 공개 이메일 인프라가 없는 현재 단계의 자리표시자. 토큰 원문이
    이 문자열 안에 담기므로, 호출부 밖(로그·감사 로그)으로 절대
    내보내지 않는다 — Provider의 반환값만 사용하고 이 문자열 자체를
    별도로 저장하지 않는다.
    """

    return f"homez-desktop-reset://pending-email-integration?token={token}"


_FORGOT_ID_GENERIC_MESSAGE = (
    "입력한 정보와 일치하는 계정이 있으면 안내를 진행합니다."
)
_EMAIL_NOT_CONFIGURED_MESSAGE = (
    "현재 이메일 복구가 구성되지 않았습니다. 복구 코드를 사용하거나 관리자에게 문의하세요."
)
_RESET_GENERIC_FAILURE_MESSAGE = (
    "입력한 정보로 비밀번호를 재설정할 수 없습니다. 값을 다시 확인해 주세요."
)


# --------------------------------------------------
# 1. 아이디 찾기
# --------------------------------------------------

@router.post(
    "/forgot-id",
    response_model=GenericMessageResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def forgot_id(
    data: ForgotIdRequest,
    db: Session = Depends(get_db),
    provider: PasswordResetDeliveryProvider = Depends(get_password_reset_provider),
):

    outcome = request_forgot_id(db, email=data.email, provider=provider)

    if outcome == ForgotIdOutcome.EMAIL_NOT_CONFIGURED:
        return GenericMessageResponse(message=_EMAIL_NOT_CONFIGURED_MESSAGE)

    return GenericMessageResponse(message=_FORGOT_ID_GENERIC_MESSAGE)


# --------------------------------------------------
# 2. 로그인 상태 — 복구 코드 발급(설정 화면)
# --------------------------------------------------

@router.post(
    "/recovery-codes",
    response_model=RecoveryCodesGenerateResponse,
)
def generate_my_recovery_codes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    codes = generate_recovery_codes(db, user=current_user)

    return RecoveryCodesGenerateResponse(codes=codes)


# --------------------------------------------------
# 3. 로그아웃 상태 — 복구 코드로 재설정
# --------------------------------------------------

@router.post(
    "/reset-password/recovery-code",
    response_model=GenericMessageResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def reset_password_with_recovery_code(
    data: RecoveryCodeResetRequest,
    db: Session = Depends(get_db),
):

    try:
        result = consume_recovery_code(
            db,
            email=data.email,
            code=data.code,
            new_password=data.new_password,
            new_password_confirmation=data.new_password_confirmation,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 비밀번호가 정책을 만족하지 않거나 확인 값이 일치하지 않습니다.",
        )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_RESET_GENERIC_FAILURE_MESSAGE,
        )

    return GenericMessageResponse(message="비밀번호가 재설정되었습니다. 다시 로그인해 주세요.")


# --------------------------------------------------
# 4. 이메일 재설정 링크 — 상태/요청/소비
# --------------------------------------------------

@router.get(
    "/reset-password/email/status",
    response_model=PasswordResetEmailStatusResponse,
)
def password_reset_email_status(
    provider: PasswordResetDeliveryProvider = Depends(get_password_reset_provider),
):

    return PasswordResetEmailStatusResponse(configured=provider.is_configured)


@router.post(
    "/reset-password/email/request",
    response_model=GenericMessageResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def request_password_reset_email_endpoint(
    data: PasswordResetRequestRequest,
    db: Session = Depends(get_db),
    provider: PasswordResetDeliveryProvider = Depends(get_password_reset_provider),
):

    outcome = request_password_reset_email(
        db, email=data.email, provider=provider, build_reset_link=_build_reset_link,
    )

    if outcome == ForgotIdOutcome.EMAIL_NOT_CONFIGURED:
        return GenericMessageResponse(message=_EMAIL_NOT_CONFIGURED_MESSAGE)

    return GenericMessageResponse(message=_FORGOT_ID_GENERIC_MESSAGE)


@router.post(
    "/reset-password/email/confirm",
    response_model=GenericMessageResponse,
    dependencies=[
        Depends(require_loopback),
        Depends(require_matching_origin),
        Depends(require_desktop_mode_and_token),
    ],
)
def confirm_password_reset_token(
    data: PasswordResetTokenConsumeRequest,
    db: Session = Depends(get_db),
):

    try:
        result = consume_reset_token(
            db,
            token=data.token,
            new_password=data.new_password,
            new_password_confirmation=data.new_password_confirmation,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 비밀번호가 정책을 만족하지 않거나 확인 값이 일치하지 않습니다.",
        )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_RESET_GENERIC_FAILURE_MESSAGE,
        )

    return GenericMessageResponse(message="비밀번호가 재설정되었습니다. 다시 로그인해 주세요.")


# --------------------------------------------------
# 5. SUPER_ADMIN — 다른 사용자 재설정 시작
# --------------------------------------------------

@router.post(
    "/admin/initiate-reset",
    response_model=SuperAdminResetInitiateResponse,
)
def super_admin_initiate_reset_endpoint(
    data: SuperAdminResetInitiateRequest,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    try:
        raw_token = super_admin_initiate_reset(
            db, target_user_id=data.user_id, requesting_admin=current_user,
        )
    except LookupError as exc:
        error_code = exc.args[0] if exc.args else None

        if error_code == SuperAdminResetError.TARGET_INACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="비활성화된 사용자는 재설정을 시작할 수 없습니다.",
            )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="사용자를 찾을 수 없습니다.",
        )

    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == sha256(raw_token))
        .first()
    )

    return SuperAdminResetInitiateResponse(
        reset_token=raw_token,
        expires_at=row.expires_at,
    )


__all__ = ["router", "get_password_reset_provider"]
