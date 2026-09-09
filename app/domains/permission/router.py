"""
=========================================================
Homez OS

File : app/domains/permission/router.py
Version : 1.0.0

Permission Router
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.permission.schema import PermissionCreate
from app.domains.permission.schema import PermissionResponse
from app.domains.permission.schema import PermissionUpdate
from app.domains.permission.service import PermissionService


router = APIRouter(
    prefix="/permissions",
    tags=["Permissions"],
)


def get_service(
    db: Session = Depends(get_db),
) -> PermissionService:

    return PermissionService(db)


@router.post(
    "",
    response_model=PermissionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_permission(
    data: PermissionCreate,
    service: PermissionService = Depends(get_service),
):

    return service.create_permission(data)


@router.get(
    "",
    response_model=list[PermissionResponse],
)
def list_permissions(
    service: PermissionService = Depends(get_service),
):

    return service.get_permissions()


@router.get(
    "/{permission_id}",
    response_model=PermissionResponse,
)
def get_permission(
    permission_id: int,
    service: PermissionService = Depends(get_service),
):

    permission = service.get_permission(permission_id)

    if permission is None:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found.",
        )

    return permission


@router.patch(
    "/{permission_id}",
    response_model=PermissionResponse,
)
def update_permission(
    permission_id: int,
    data: PermissionUpdate,
    service: PermissionService = Depends(get_service),
):

    permission = service.update_permission(
        permission_id,
        data,
    )

    if permission is None:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found.",
        )

    return permission


@router.delete(
    "/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_permission(
    permission_id: int,
    service: PermissionService = Depends(get_service),
):

    deleted = service.delete_permission(permission_id)

    if not deleted:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found.",
        )
