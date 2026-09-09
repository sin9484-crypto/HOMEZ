"""
=========================================================
Homez OS

File : app/domains/auth/service.py
Version : 2.1.0

Auth Service
=========================================================
"""

from datetime import datetime
from datetime import timezone

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.audit_log import log_auth_event
from app.core.base_service import BaseService
from app.core.config import settings
from app.core.permission_check import get_permission_codes_for_role

from app.core.security import (
    create_access_token,
    decode_access_token,
    verify_password,
)
from app.core.token import create_refresh_token
from app.core.token import get_refresh_subject
from app.core.token import verify_refresh_token

from app.domains.auth.repository import (
    AuthRepository,
)

from app.domains.notification_center.delivery_service import (
    NotificationDeliveryService,
)
from app.domains.session.refresh_service import RefreshOutcome
from app.domains.session.refresh_service import issue_family
from app.domains.session.refresh_service import rotate
from app.domains.session.service import create_session

from app.domains.user.model import (
    User,
)

from app.domains.user.service import (
    UserService,
)


def _epoch_to_naive_utc(value: int) -> datetime:

    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)


class RefreshTokenError(ValueError):
    """
    2026-08-30 V7 후속 안정화 Phase 2 — `/auth/refresh`가 실패
    원인(만료/폐기/재사용탐지/무효)을 app/core/auth.py의 기존
    X-Auth-Error-Code 관례(SESSION_EXPIRED/SESSION_REVOKED)와 동일한
    코드로 라우터에 전달한다 — 새 헤더 계약을 만들지 않고 기존 것을
    그대로 재사용한다.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AuthService(
    BaseService[AuthRepository]
):

    def __init__(
        self,
        db: Session,
    ):
        self.db = db

        super().__init__(
            repository=AuthRepository(db),
        )

        self.user_service = UserService(db)

    # --------------------------------------------------
    # Authentication
    # --------------------------------------------------

    def authenticate(
        self,
        username: str,
        password: str,
    ) -> User | None:

        user = (
            self.repository.get_by_username(
                username
            )
        )

        if user is None:

            return None

        if not verify_password(
            password,
            user.password_hash,
        ):

            return None

        if (
            hasattr(user, "is_active")
            and not user.is_active
        ):

            return None

        return user

    # --------------------------------------------------
    # Login
    # --------------------------------------------------

    def login(
        self,
        username: str,
        password: str,
        *,
        is_desktop: bool = False,
        client_ip: str | None = None,
    ) -> dict:

        user = (
            self.repository.get_by_username(
                username
            )
        )

        if user is None:

            log_auth_event(
                "login_failed",
                username=username,
                reason="unknown_username",
                client_ip=client_ip,
            )

            raise ValueError(
                "Invalid username or password."
            )

        # 2026-09-09 Phase 1(로그인 무차별 대입 방어) — 잠긴 계정은
        # 비밀번호 검증 자체를 하지 않고 즉시 거부한다. 응답 메시지는
        # 아래 다른 실패 사유들과 동일한 일반 문구를 그대로 쓴다(계정
        # 존재 여부·잠금 여부를 외부에 다르게 노출하지 않기 위해 —
        # 이 파일의 기존 계정 열거 방지 원칙과 동일하게 적용). 실제
        # 잠금 사실은 로그인 시도자가 아니라 계정 소유자에게 이메일로
        # 별도 통지된다(잠금 전환 시점, 아래 참고).
        if self.user_service.repository.is_locked_out(user):

            log_auth_event(
                "login_failed",
                username=username,
                user_id=user.id,
                reason="account_locked",
                client_ip=client_ip,
            )

            raise ValueError(
                "Invalid username or password."
            )

        if not verify_password(
            password,
            user.password_hash,
        ):

            user, newly_locked = self.user_service.login_failed(
                user,
            )

            log_auth_event(
                "login_failed",
                username=username,
                user_id=user.id,
                reason="bad_password",
                client_ip=client_ip,
                failed_login_count=user.failed_login_count,
            )

            if newly_locked:
                self._notify_account_locked(user, client_ip=client_ip)

            raise ValueError(
                "Invalid username or password."
            )

        if (
            hasattr(user, "is_active")
            and not user.is_active
        ):

            # 2026-08-03: 이전에는 여기서만 PermissionError(403,
            # "Inactive user")를 던져, 존재하지 않는 계정/틀린 비밀번호
            # (401 "Invalid username or password.")와 응답이 달랐다 —
            # 그 차이 자체가 "이 계정은 존재한다"는 계정 열거 신호였다.
            # 승인 대기/거절/정지 계정도 전부 is_active=False로 표현
            # 되므로(app/domains/account_registration), 이 분기가 그
            # 상태들의 로그인 차단도 함께 담당한다 — 전부 동일한 일반
            # 오류로 통일한다.
            log_auth_event(
                "login_failed",
                username=username,
                user_id=user.id,
                reason="inactive_account",
                client_ip=client_ip,
            )

            raise ValueError(
                "Invalid username or password."
            )

        user = self.user_service.login_success(
            user,
        )

        token_data = {
            "sub": str(user.id),
            "username": user.username,
        }

        access_token = create_access_token(data=token_data)
        payload = decode_access_token(access_token)

        create_session(
            self.db,
            user_id=user.id,
            jti=payload["jti"],
            issued_at=_epoch_to_naive_utc(payload["iat"]),
            expires_at=_epoch_to_naive_utc(payload["exp"]),
            is_desktop=is_desktop,
        )

        # 2026-08-30 V7 후속 안정화 Phase 2 — 이 access 세션(jti)에
        # 묶인 Refresh Token family를 만든다. 스키마가 아직 없는
        # 환경(Migration 미적용)에서는 None이 돌아오고, 그때만
        # family_id 없는 레거시 방식으로 폴백한다(기존 동작 유지 —
        # 이 스키마가 없다고 로그인 자체가 막히지 않는다).
        refresh_token = issue_family(
            self.db, user_id=user.id, username=user.username,
            access_session_jti=payload["jti"],
        )
        if refresh_token is None:
            refresh_token = create_refresh_token(data=token_data)

        self.db.commit()

        log_auth_event(
            "login_success",
            username=username,
            user_id=user.id,
            client_ip=client_ip,
        )

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": user,
            "refresh_token": refresh_token,
            "permissions": sorted(get_permission_codes_for_role(self.db, user.role_id)),
        }

    # --------------------------------------------------
    # Lockout notification
    # --------------------------------------------------

    def _notify_account_locked(
        self,
        user: User,
        *,
        client_ip: str | None,
    ) -> None:
        """
        2026-09-09 Phase 1 — 계정이 이번 실패로 "새로" 잠겼을 때만
        호출된다(app/domains/user/repository.py::increment_failed_login
        의 동시성 방어 덕분에 여러 요청이 동시에 임계치를 넘겨도
        정확히 한 번만 호출됨). 실패한 로그인 시도자가 아니라 계정
        소유자 본인에게 통지한다 — 관리자가 아니라 잠긴 계정 그
        자신이 "사용자"에 해당한다(HOMEZ_USER_OPERATION_SETTINGS.md
        11번 "사용자에게 알린다").

        감사 기록의 user_id는 의도적으로 None이다 — 이 잠금은
        관리자의 조작이 아니라 반복된 로그인 실패 자체가 시스템적으로
        일으킨 것이라 행위자가 없다(관리자 수동 잠금·해제는
        app/core/account_admin.py에 별도로 있다면 그쪽에서
        current_user.id를 남긴다).
        """

        try:
            write_audit_log(
                self.db,
                user_id=None,
                action="ACCOUNT_LOCKED",
                entity="users",
                entity_id=str(user.id),
                description=(
                    f"연속 로그인 실패 {user.failed_login_count}회로 계정이 "
                    f"{settings.LOGIN_LOCK_MINUTES}분간 잠겼습니다"
                    + (f"(client_ip={client_ip})" if client_ip else "")
                    + "."
                ),
                company_id=user.company_id,
            )
            self.db.commit()
        except Exception:  # noqa: BLE001 — 감사 기록 실패가 로그인 응답을 막지 않는다
            self.db.rollback()

        lock_marker = user.locked_until.isoformat() if user.locked_until else ""
        # (recipient_user_id, recipient_email) — 각 수신자 본인의
        # user_id를 넘겨야 notification_center의 인앱 알림
        # (notify_user)이 실제로 그 수신자 앞으로 쌓인다. 전부 잠긴
        # 계정(user)의 id를 쓰면 관리자는 이메일만 받고 인앱 알림함에는
        # 아무것도 뜨지 않는 결함이 생긴다.
        recipients: list[tuple[int, str]] = [(user.id, user.email)]

        # "관리자 알림" 요구사항 — 잠긴 계정 본인 외에, 같은 회사의
        # 활성 SUPER_ADMIN에게도 함께 통지한다(app/core/account_admin.py
        # ::_count_active_super_admins와 동일한 방식으로 role을 조회 —
        # role은 계산 속성이라 쿼리 필터에는 못 쓴다).
        try:
            for admin in (
                self.db.query(User)
                .filter(
                    User.company_id == user.company_id,
                    User.is_active.is_(True),
                )
                .all()
            ):
                if admin.id == user.id:
                    continue
                if (admin.role or "").strip().upper() != "SUPER_ADMIN":
                    continue
                if admin.email == user.email:
                    continue
                recipients.append((admin.id, admin.email))
        except Exception:  # noqa: BLE001 — 관리자 조회 실패가 로그인 응답을 막지 않는다
            pass

        for recipient_user_id, to_email in recipients:
            try:
                NotificationDeliveryService(self.db).dispatch(
                    "LOGIN_ACCOUNT_LOCKED",
                    company_id=user.company_id or 0,
                    user_id=recipient_user_id,
                    idempotency_key=f"login-locked-user{recipient_user_id}-{lock_marker}",
                    title="로그인이 잠시 차단되었습니다.",
                    message=(
                        f"계정(id={user.id})이 비밀번호를 "
                        f"{settings.LOGIN_MAX_ATTEMPT}회 연속 틀려 "
                        f"{settings.LOGIN_LOCK_MINUTES}분간 로그인이 차단됩니다. "
                        "본인이 시도한 것이 아니라면 비밀번호를 변경해 주세요."
                    ),
                    link_path="settings/security",
                    entity_ref=f"user:{user.id}",
                    to_email=to_email,
                    reason="repeated_login_failure",
                    console_url="/console#settings",
                )
            except Exception:  # noqa: BLE001 — 알림 실패가 로그인 응답을 막지 않는다
                pass

    # --------------------------------------------------
    # Refresh
    # --------------------------------------------------

    def refresh(
        self,
        refresh_token: str,
    ) -> dict:
        """
        2026-08-30 V7 후속 안정화 Phase 2 — 감사에서 확인된 Critical
        결함(폐기된 세션의 Refresh Token으로도 계속 새 Access Token을
        받을 수 있었다) 수정. 실제 판정은 전부
        app.domains.session.refresh_service.rotate()가 한다(family 상태,
        연결된 access 세션 상태, rotation 소비, 재사용 탐지) — 이
        메서드는 그 결과를 기존 X-Auth-Error-Code 관례(app/core/
        auth.py의 SESSION_EXPIRED/SESSION_REVOKED)로 옮기기만 한다.

        `refresh_token_families`/`refresh_tokens` 스키마가 아직 없는
        환경(Migration 미적용)에서는 rotate()가 SCHEMA_NOT_READY를
        반환하고, 그때만 이 세션 도입 이전과 동일한 레거시 검증(서명·
        만료·활성 사용자만 확인, rotation 없음)으로 폴백한다 — 이
        스키마가 없다고 로그인 유지 자체가 막히지 않는다.
        """

        result = rotate(self.db, raw_refresh_token=refresh_token)

        if result.outcome == RefreshOutcome.SCHEMA_NOT_READY:
            return self._refresh_legacy_fallback(refresh_token)

        if result.outcome == RefreshOutcome.EXPIRED:
            raise RefreshTokenError(
                "SESSION_EXPIRED", "Refresh token expired.",
            )

        if result.outcome == RefreshOutcome.SESSION_REVOKED:
            raise RefreshTokenError(
                "SESSION_REVOKED", "Session has been revoked.",
            )

        if result.outcome == RefreshOutcome.REUSE_DETECTED:
            # rotate()가 이미 family/access 세션을 폐기했다 — 그
            # 쓰기가 실제로 반영되게 커밋한다(재사용 탐지를 감사·
            # 재확인 가능한 상태로 남긴다).
            self.db.commit()
            raise RefreshTokenError(
                "SESSION_REVOKED",
                "Refresh token reuse detected; session revoked.",
            )

        if result.outcome != RefreshOutcome.SUCCESS:
            raise ValueError("Invalid refresh token.")

        user = self.repository.get_by_id(result.user_id)

        if user is None:
            self.db.commit()
            raise ValueError("Invalid refresh token.")

        if hasattr(user, "is_active") and not user.is_active:
            # rotation 자체는 그대로 커밋한다 — 방금 소비된 예전
            # Refresh Token은 이미 죽었고, 새로 발급된 것은 이 응답
            # 밖으로 절대 나가지 않으므로(PermissionError로 대체)
            # 비활성 사용자의 Refresh 능력이 오히려 더 확실히
            # 무력화된다.
            self.db.commit()
            raise PermissionError("Inactive user")

        self.db.commit()

        return {
            "access_token": result.access_token,
            "token_type": "bearer",
            "user": user,
            "refresh_token": result.refresh_token,
        }

    def _refresh_legacy_fallback(self, refresh_token: str) -> dict:
        """
        `refresh_token_families`/`refresh_tokens` 스키마가 아직 없는
        환경 전용 — 이 Phase 도입 이전과 정확히 동일한 검증(서명·
        만료·구독자·활성 사용자만 확인, rotation·재사용탐지 없음).
        """

        if not verify_refresh_token(refresh_token):
            raise ValueError("Invalid refresh token.")

        subject = get_refresh_subject(refresh_token)

        if subject is None:
            raise ValueError("Invalid refresh token.")

        try:
            user_id = int(subject)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid refresh token.") from exc

        user = self.repository.get_by_id(user_id)

        if user is None:
            raise ValueError("Invalid refresh token.")

        if hasattr(user, "is_active") and not user.is_active:
            raise PermissionError("Inactive user")

        access_token = create_access_token(
            data={"sub": str(user.id), "username": user.username},
        )

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": user,
            "refresh_token": None,
        }

    # --------------------------------------------------
    # Current User
    # --------------------------------------------------

    def get_current_user(
        self,
        username: str,
    ) -> User | None:

        return self.repository.get_active_user(
            username
        )
