"""
=========================================================
Homez OS

File : app/domains/role/router.py
Version : 1.0.0

Role Router
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.role.schema import RoleCreate
from app.domains.role.schema import RoleResponse
from app.domains.role.schema import RoleUpdate
from app.domains.role.service import RoleService


router = APIRouter(
    prefix="/roles",
    tags=["Roles"],
)


def get_service(
    db: Session = Depends(get_db),
) -> RoleService:

    return RoleService(db)


@router.post(
    "",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_role(
    data: RoleCreate,
    service: RoleService = Depends(get_service),
):

    return service.create_role(data)


@router.get(
    "",
    response_model=list[RoleResponse],
)
def list_roles(
    service: RoleService = Depends(get_service),
):

    return service.get_roles()


@router.get(
    "/{role_id}",
    response_model=RoleResponse,
)
def get_role(
    role_id: int,
    service: RoleService = Depends(get_service),
):

    role = service.get_role(role_id)

    if role is None:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found.",
        )

    return role


@router.patch(
    "/{role_id}",
    response_model=RoleResponse,
)
def update_role(
    role_id: int,
    data: RoleUpdate,
    service: RoleService = Depends(get_service),
):

    role = service.update_role(role_id, data)

    if role is None:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found.",
        )

    return role


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_role(
    role_id: int,
    service: RoleService = Depends(get_service),
):

    deleted = service.delete_role(role_id)

    if not deleted:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found.",
        )
