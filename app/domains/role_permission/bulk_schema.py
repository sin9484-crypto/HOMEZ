"""
=========================================================
Homez OS

File : app/domains/role_permission/bulk_schema.py
Version : 2.1.0

Role Permission Bulk Schema
=========================================================
"""

from pydantic import BaseModel
from pydantic import Field


class RolePermissionBulkUpdate(
    BaseModel
):

    permission_ids: list[int] = Field(
        ...,
        description="Permission ID List",
        min_length=0,
    )