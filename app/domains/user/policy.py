from __future__ import annotations

from app.domains.user.model import User


class UserPolicy:
    """
    User Domain Policy

    사용자 도메인의
    권한 및 상태 검증 규칙 담당
    """

    @staticmethod
    def can_login(
        user: User,
    ) -> bool:

        return (
            user.is_active
            and user.deleted_at is None
        )


    @staticmethod
    def can_access(
        user: User,
    ) -> bool:

        return user.is_active


    @staticmethod
    def is_admin(
        user: User,
    ) -> bool:

        return user.role == "ADMIN"
    @staticmethod
    def can_modify(
        actor: User,
        target: User,
    ) -> bool:

        # 관리자 또는 본인만 수정 가능
        return (
            actor.role == "ADMIN"
            or actor.id == target.id
        )


    @staticmethod
    def can_delete(
        actor: User,
        target: User,
    ) -> bool:

        # 관리자만 삭제 가능
        # 본인 삭제는 별도 서비스 정책에서 처리

        return (
            actor.role == "ADMIN"
        )


    @staticmethod
    def can_change_role(
        actor: User,
    ) -> bool:

        return (
            actor.role == "ADMIN"
        )
    @staticmethod
    def can_manage_users(
        actor: User,
    ) -> bool:

        return (
            actor.role == "ADMIN"
            or actor.is_staff
        )


    @staticmethod
    def validate_role(
        role: str,
    ) -> bool:

        allowed_roles = {
            "USER",
            "STAFF",
            "ADMIN",
        }

        return (
            role in allowed_roles
        )


    @staticmethod
    def can_change_password(
        user: User,
    ) -> bool:

        return (
            user.is_active
            and user.deleted_at is None
        )


__all__ = [
    "UserPolicy",
]        