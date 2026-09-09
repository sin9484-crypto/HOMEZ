"""
=========================================================
Homez OS

File : app/domains/role/service.py
Version : 1.0.0

Role Service
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.role.model import Role
from app.domains.role.repository import RoleRepository
from app.domains.role.schema import RoleCreate
from app.domains.role.schema import RoleUpdate


class RoleService:

    def __init__(self, db: Session) -> None:

        self.db = db
        self.repository = RoleRepository(db)

    def create_role(self, data: RoleCreate) -> Role:

        role = Role(
            name=data.name,
            code=data.code,
            description=data.description,
        )

        return self.repository.create(role)

    def get_role(self, role_id: int) -> Role | None:

        return self.repository.get(role_id)

    def get_roles(self) -> list[Role]:

        return self.repository.get_all()

    def update_role(
        self,
        role_id: int,
        data: RoleUpdate,
    ) -> Role | None:

        role = self.repository.get(role_id)

        if role is None:

            return None

        values = data.model_dump(exclude_unset=True)

        for key, value in values.items():

            setattr(role, key, value)

        return self.repository.update(role)

    def delete_role(self, role_id: int) -> bool:

        role = self.repository.get(role_id)

        if role is None:

            return False

        self.repository.delete(role)

        return True


def find_role_by_code_ci(db: Session, code: str) -> Role | None:
    """
    대소문자 무시로 역할 코드를 조회한다. 실제 시딩된 역할 코드는
    대문자("SUPER_ADMIN")이지만 다른 표기의 환경도 안전하게 인식하기
    위함이다(app/core/authorization.py::has_role()와 동일한 이유,
    2026-07-30 발견 사례 재발 방지).
    """

    normalized = code.strip().upper()

    for role in db.query(Role).all():
        if role.code.strip().upper() == normalized:
            return role

    return None
