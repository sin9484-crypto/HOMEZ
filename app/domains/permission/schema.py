"""
=========================================================
Homez OS

File : app/domains/permission/schema.py
Version : 1.0.0

Permission Schema
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class PermissionBase(BaseModel):

    name: str = Field(..., description="Permission name")

    code: str = Field(..., description="Permission code")

    description: str | None = Field(
        default=None,
        description="Permission description",
    )

    active: bool = Field(
        default=True,
        description="Active flag",
    )


class PermissionCreate(PermissionBase):

    pass


class PermissionUpdate(BaseModel):

    name: str | None = None

    code: str | None = None

    description: str | None = None

    active: bool | None = None


class PermissionResponse(PermissionBase):

    id: int

    created_at: datetime

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )
