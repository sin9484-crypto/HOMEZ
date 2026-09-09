"""
=========================================================
Homez OS

File : app/domains/role/schema.py
Version : 1.0.0

Role Schema
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class RoleBase(BaseModel):

    name: str = Field(..., description="Role name")

    code: str = Field(..., description="Role code")

    description: str | None = Field(
        default=None,
        description="Role description",
    )


class RoleCreate(RoleBase):

    pass


class RoleUpdate(BaseModel):

    name: str | None = None

    code: str | None = None

    description: str | None = None


class RoleResponse(RoleBase):

    id: int

    created_at: datetime

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )
