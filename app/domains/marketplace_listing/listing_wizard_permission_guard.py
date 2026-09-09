"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_permission_guard.py

Gate Q-2(2026-08-09) — VIEWER 등 비-관리자 역할을 위한 세부 Permission
기반 Guard. ADMIN/SUPER_ADMIN은 기존 admin_guard와 동일하게 무조건
통과한다 — has_permission() 조회 경로는 ADMIN/SUPER_ADMIN이 아닌
역할에만 적용된다. 이렇게 설계한 이유: 이 Domain의 8개 Permission
코드는 아직 실제 homez.db `permissions` 테이블에 시딩되지 않았다
(listing_wizard_permissions.py 상단 주석 참고) — ADMIN/SUPER_ADMIN
접근을 has_permission() 조회 결과에만 의존시키면, 시딩 전 실제
배포에서 기존 ADMIN 사용자까지 이 Domain 전체를 못 쓰게 되는
회귀가 생긴다. 역할 우선 통과 + Permission은 "추가로 허용"하는
계층으로 설계해 이 위험을 원천 차단했다.
=========================================================
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.authorization import UserRole
from app.core.authorization import authorization_exception
from app.core.authorization import has_role
from app.core.dependency import get_db
from app.core.permission_check import has_permission
from app.domains.user.model import User


def ListingWizardPermissionGuard(code: str):

    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:

        if has_role(current_user, UserRole.ADMIN, UserRole.SUPER_ADMIN):
            return current_user

        if has_permission(db, current_user, code):
            return current_user

        raise authorization_exception()

    return dependency


def user_can(db: Session, current_user: User, code: str) -> bool:
    """
    Guard가 아니라, 응답 페이로드 필드 단위 가시성(예: 금액 정보 노출
    여부)을 판단할 때 쓰는 순수 조회 헬퍼 — 실패해도 예외를 던지지
    않고 bool만 돌려준다.
    """

    if has_role(current_user, UserRole.ADMIN, UserRole.SUPER_ADMIN):
        return True

    return has_permission(db, current_user, code)


__all__ = ["ListingWizardPermissionGuard", "user_can"]
