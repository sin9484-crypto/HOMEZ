"""
=========================================================
Homez OS

File : app/domains/role_permission/schema.py
Version : 1.0.0

Role Permission Schema
=========================================================
"""

from pydantic import BaseModel
from pydantic import ConfigDict


class RolePermissionBase(BaseModel):

    role_id: int
    permission_id: int


class RolePermissionCreate(RolePermissionBase):
    pass


class RolePermissionUpdate(BaseModel):

    role_id: int | None = None
    permission_id: int | None = None


class RolePermissionResponse(RolePermissionBase):

    id: int

    model_config = ConfigDict(
        from_attributes=True,
    )