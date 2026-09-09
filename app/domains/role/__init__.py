"""
=========================================================
Homez OS

Role Domain
=========================================================
"""

from app.domains.role.model import Role
from app.domains.role.router import router
from app.domains.role.schema import RoleCreate
from app.domains.role.schema import RoleResponse
from app.domains.role.schema import RoleUpdate
from app.domains.role.service import RoleService

__all__ = [
    "Role",
    "RoleCreate",
    "RoleUpdate",
    "RoleResponse",
    "RoleService",
    "router",
]
