from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository
from app.core.config import settings

from app.domains.user.model import (
    User,
)


class UserRepository(
    BaseRepository[User]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        super().__init__(
            db,
            User,
        )

    def get_by_id(
        self,
        user_id: int,
    ) -> User | None:

        return self.db.scalar(
            select(User).where(
                User.id == user_id
            )
        )

    def get_by_email(
        self,
        email: str,
    ) -> User | None:

        return self.db.scalar(
            select(User).where(
                User.email == email
            )
        )

    def get_by_username(
        self,
        username: str,
    ) -> User | None:

        return self.db.scalar(
            select(User).where(
                User.username == username
            )
        )
    def get_by_phone(
        self,
        phone: str,
    ) -> User | None:

        return self.db.scalar(
            select(User).where(
                User.phone == phone
            )
        )


    def get_by_nickname(
        self,
        nickname: str,
    ) -> User | None:

        return self.db.scalar(
            select(User).where(
                User.nickname == nickname
            )
        )


    def get_active_users(
        self,
    ) -> list[User]:

        return list(
            self.db.scalars(
                select(User).where(
                    User.is_active.is_(True)
                )
            )
        )
    def search(
        self,
        keyword: str,
    ) -> list[User]:

        query = (
            select(User)
            .where(
                User.email.contains(keyword)
                |
                User.username.contains(keyword)
                |
                User.nickname.contains(keyword)
            )
        )

        return list(
            self.db.scalars(
                query
            )
        )


    def get_by_role(
        self,
        role: str,
    ) -> list[User]:

        return list(
            self.db.scalars(
                select(User).where(
                    User.role == role
                )
            )
        )


    def update_last_login(
        self,
        user: User,
    ) -> User:
        """
        실제 homez.db의 users 테이블에는 last_login_at 컬럼이 없다
        (2026-07-30 확인 — User 모델을 실제 스키마에 맞게 정리하며
        같이 발견함). 이 메서드는 로그인 성공 경로(AuthService.login →
        UserService.login_success)에서 호출되므로 예외 없이 안전하게
        끝나야 한다 — 존재하지 않는 컬럼에 값을 쓰지 않고 그대로
        반환한다. 마지막 로그인 시각을 실제로 기록하려면 별도 승인
        하에 Migration으로 컬럼을 추가해야 한다.
        """

        return user

    def increment_login_count(
        self,
        user: User,
    ) -> User:
        """login_count 컬럼이 실제 DB에 없어 update_last_login과 동일하게
        안전한 no-op으로 둔다(2026-07-30)."""

        return user


    def is_locked_out(
        self,
        user: User,
        *,
        now: datetime | None = None,
    ) -> bool:
        """`locked_until`이 미래 시각이면 잠긴 상태다. NULL이거나
        과거 시각이면 잠금 아님(별도 "해제" 배치 없이 시간 경과만으로
        자연 해제된다)."""

        if user.locked_until is None:
            return False

        return user.locked_until > (now or datetime.utcnow())

    def increment_failed_login(
        self,
        user: User,
        *,
        now: datetime | None = None,
    ) -> tuple[User, bool]:
        """
        2026-09-09 Phase 1(로그인 무차별 대입 방어) — 로그인 실패
        1건을 기록하고, 이번 실패로 새로 잠금 상태에 들어갔는지
        `(user, newly_locked)`로 반환한다.

        동시 요청 경쟁 조건 방어: Python에서 값을 읽어 +1 해서 다시
        쓰는 대신, `failed_login_count = failed_login_count + 1`을
        SQL 표현식으로 그대로 UPDATE에 실어 보낸다 — 두 요청이 거의
        동시에 들어와도 각 UPDATE 문 자체가 원자적이라 증가분이
        유실되지 않는다. 잠금 전환(`locked_until` 설정)도 별도의
        조건부 UPDATE(`WHERE locked_until IS NULL`)로 한 번 더 감싸서,
        여러 요청이 동시에 임계치를 넘겨도 그중 정확히 하나만
        `rowcount > 0`을 받는다 — 그 하나만 "새로 잠김" 알림·감사
        기록을 트리거하게 해서 중복 알림을 막는다.
        """

        current_time = now or datetime.utcnow()

        self.db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                failed_login_count=User.failed_login_count + 1,
                last_failed_login_at=current_time,
            )
        )
        self.db.commit()
        self.db.refresh(user)

        newly_locked = False

        if user.failed_login_count >= settings.LOGIN_MAX_ATTEMPT:

            lock_result = self.db.execute(
                update(User)
                .where(User.id == user.id, User.locked_until.is_(None))
                .values(
                    locked_until=current_time
                    + timedelta(minutes=settings.LOGIN_LOCK_MINUTES),
                )
            )
            self.db.commit()
            self.db.refresh(user)

            newly_locked = lock_result.rowcount > 0

        return user, newly_locked

    def reset_login_lockout(
        self,
        user: User,
    ) -> User:
        """로그인 성공 시 실패 카운트·잠금을 안전하게 초기화한다."""

        if user.failed_login_count == 0 and user.locked_until is None:
            return user

        self.db.execute(
            update(User)
            .where(User.id == user.id)
            .values(failed_login_count=0, locked_until=None)
        )
        self.db.commit()
        self.db.refresh(user)

        return user


    def deactivate(
        self,
        user: User,
    ) -> User:

        user.is_active = False

        self.db.commit()
        self.db.refresh(
            user
        )

        return user


__all__ = [
    "UserRepository",
]