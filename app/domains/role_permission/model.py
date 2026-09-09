"""
=========================================================
Homez OS

File : app/domains/role_permission/model.py
Version : 1.1.0

Role Permission Model
=========================================================
"""

from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base


class RolePermission(Base):

    __tablename__ = "role_permissions"

    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_role_permission",
        ),
    )

    # --------------------------------------------------
    # Primary Key
    # --------------------------------------------------

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # --------------------------------------------------
    # Foreign Key
    # --------------------------------------------------

    role_id: Mapped[int] = mapped_column(
        ForeignKey(
            "roles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    permission_id: Mapped[int] = mapped_column(
        ForeignKey(
            "permissions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    # --------------------------------------------------
    # Relationship
    # --------------------------------------------------

    role = relationship(
        "Role",
        back_populates="role_permissions",
        lazy="joined",
    )

    permission = relationship(
        "Permission",
        back_populates="role_permissions",
        lazy="joined",
    )

    # --------------------------------------------------

    def __repr__(self):

        return (
            f"<RolePermission("
            f"role_id={self.role_id}, "
            f"permission_id={self.permission_id}"
            f")>"
        )


# permission = relationship("Permission", ...) 위는 문자열로 클래스를
# 참조한다 — RolePermission이 어디서 import되든 Permission이 항상
# 함께 로드되도록 여기서 명시적으로 import한다. 클래스 정의 "이후"
# (파일 맨 아래)에 두는 이유: permission.model → permission/__init__.py
# → permission/policy.py → role.repository → role/__init__.py →
# role.model → role_permission.model로 이어지는 순환 import 경로가
# 있어(2026-07-30 발견), 이 import를 클래스 정의보다 먼저 두면
# "partially initialized module" 오류가 난다. RolePermission 클래스가
# 이미 이 모듈 네임스페이스에 바인딩된 뒤라면 그 순환 경로가 다시
# role_permission.model을 참조해도 정상적으로 찾을 수 있다.
from app.domains.permission.model import Permission  # noqa: E402,F401