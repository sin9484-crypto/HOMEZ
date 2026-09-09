"""
=========================================================
Homez OS

File : app/domains/role_permission/router.py
Version : 2.1.0

Role Permission Router
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.role_permission.bulk_schema import (
    RolePermissionBulkUpdate,
)
from app.domains.role_permission.repository import (
    RolePermissionRepository,
)
from app.domains.role_permission.schema import (
    RolePermissionCreate,
    RolePermissionResponse,
    RolePermissionUpdate,
)
from app.domains.role_permission.service import (
    RolePermissionService,
)

router = APIRouter()


# --------------------------------------------------
# Create
# --------------------------------------------------

@router.post(
    "/",
    response_model=RolePermissionResponse,
)
def create_role_permission(
    data: RolePermissionCreate,
    db: Session = Depends(get_db),
):

    service = RolePermissionService(db)

    try:
        return service.create(data)

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e),
        )


# --------------------------------------------------
# Read
# --------------------------------------------------

@router.get(
    "/",
    response_model=list[RolePermissionResponse],
)
def get_all(
    db: Session = Depends(get_db),
):

    service = RolePermissionService(db)

    return service.get_all()


@router.get(
    "/{role_permission_id}",
    response_model=RolePermissionResponse,
)
def get(
    role_permission_id: int,
    db: Session = Depends(get_db),
):

    service = RolePermissionService(db)

    entity = service.get(
        role_permission_id
    )

    if entity is None:

        raise HTTPException(
            status_code=404,
            detail="RolePermission not found.",
        )

    return entity


@router.get(
    "/role/{role_id}",
    response_model=list[RolePermissionResponse],
)
def get_by_role(
    role_id: int,
    db: Session = Depends(get_db),
):

    service = RolePermissionService(db)

    return service.get_by_role(role_id)


@router.get(
    "/permission/{permission_id}",
    response_model=list[RolePermissionResponse],
)
def get_by_permission(
    permission_id: int,
    db: Session = Depends(get_db),
):

    service = RolePermissionService(db)

    return service.get_by_permission(
        permission_id
    )


# --------------------------------------------------
# Update
# --------------------------------------------------

@router.put(
    "/{role_permission_id}",
    response_model=RolePermissionResponse,
)
def update(
    role_permission_id: int,
    data: RolePermissionUpdate,
    db: Session = Depends(get_db),
):

    service = RolePermissionService(db)

    try:

        entity = service.update(
            role_permission_id,
            data,
        )

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    if entity is None:

        raise HTTPException(
            status_code=404,
            detail="RolePermission not found.",
        )

    return entity


# --------------------------------------------------
# Delete
# --------------------------------------------------

@router.delete(
    "/{role_permission_id}",
)
def delete(
    role_permission_id: int,
    db: Session = Depends(get_db),
):

    service = RolePermissionService(db)

    if not service.delete(
        role_permission_id
    ):

        raise HTTPException(
            status_code=404,
            detail="RolePermission not found.",
        )

    return {
        "success": True,
    }


# --------------------------------------------------
# Bulk Replace
# --------------------------------------------------

@router.put(
    "/role/{role_id}/permissions",
)
def replace_permissions(
    role_id: int,
    data: RolePermissionBulkUpdate,
    db: Session = Depends(get_db),
):

    service = RolePermissionService(db)

    return service.replace_permissions(
        role_id,
        data.permission_ids,
    )