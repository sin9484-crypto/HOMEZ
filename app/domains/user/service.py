from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.base_service import BaseService

from app.domains.user.model import (
    User,
)

from app.domains.user.repository import (
    UserRepository,
)


class UserService(
    BaseService[User]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        self.repository = UserRepository(
            db,
        )

    def get(
        self,
        user_id: int,
    ) -> User | None:

        return self.repository.get_by_id(
            user_id,
        )

    def get_by_email(
        self,
        email: str,
    ) -> User | None:

        return self.repository.get_by_email(
            email,
        )

    def get_by_username(
        self,
        username: str,
    ) -> User | None:

        return self.repository.get_by_username(
            username,
        )

    def list_active(
        self,
    ) -> list[User]:

        return self.repository.get_active_users()
    def get_by_phone(
        self,
        phone: str,
    ) -> User | None:

        return self.repository.get_by_phone(
            phone,
        )


    def get_by_nickname(
        self,
        nickname: str,
    ) -> User | None:

        return self.repository.get_by_nickname(
            nickname,
        )


    def search(
        self,
        keyword: str,
    ) -> list[User]:

        return self.repository.search(
            keyword,
        )


    def create(
        self,
        user: User,
    ) -> User:

        return self.repository.create(
            user,
        )
    def update(
        self,
        user: User,
    ) -> User:

        return self.repository.update(
            user,
        )


    def change_password(
        self,
        user: User,
        password_hash: str,
    ) -> User:

        user.password_hash = password_hash

        return self.repository.update(
            user,
        )


    def login_success(
        self,
        user: User,
    ) -> User:

        self.repository.increment_login_count(
            user,
        )

        # 2026-09-09 Phase 1 — 잠금 이력이 있었다면(잠금이 아직 유효한
        # 상태로 통과했을 리는 없다, AuthService.login이 그 전에 이미
        # 차단한다 — 여기 도달했다는 건 잠금이 자연 만료된 뒤 성공했거나,
        # 잠기기 전 단계에서 실패가 쌓여 있다가 이번에 성공한 경우다)
        # "해제" 감사 기록을 남긴다.
        had_lockout_history = (
            user.failed_login_count > 0 or user.locked_until is not None
        )

        self.repository.reset_login_lockout(
            user,
        )

        if had_lockout_history:
            try:
                write_audit_log(
                    self.repository.db,
                    user_id=None,
                    action="ACCOUNT_LOCKOUT_RESET",
                    entity="users",
                    entity_id=str(user.id),
                    description="로그인 성공으로 실패 횟수·잠금 상태가 초기화됐습니다.",
                    company_id=user.company_id,
                )
                self.repository.db.commit()
            except Exception:  # noqa: BLE001 — 감사 기록 실패가 로그인을 막지 않는다
                self.repository.db.rollback()

        return self.repository.update_last_login(
            user,
        )
    def login_failed(
        self,
        user: User,
    ) -> tuple[User, bool]:
        """반환값의 두 번째 항목(`newly_locked`)은 이번 실패로 계정이
        새로 잠금 상태에 들어갔는지를 나타낸다 — 이미 잠겨 있던
        계정이 계속 실패해도 매번 True가 되지 않는다(중복 알림
        방지, app/domains/user/repository.py::increment_failed_login
        참고)."""

        return self.repository.increment_failed_login(
            user,
        )


    def deactivate(
        self,
        user: User,
    ) -> User:

        return self.repository.deactivate(
            user,
        )


    def delete(
        self,
        user: User,
    ) -> None:

        self.repository.delete(
            user,
        )


__all__ = [
    "UserService",
]    