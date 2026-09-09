from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.user.schema import (
    UserCreate,
    UserUpdate,
    UserResponse,
    UserListResponse,
)

from app.domains.user.service import (
    UserService,
)

from app.domains.user.model import (
    User,
)


router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


def get_service(
    db: Session = Depends(get_db),
) -> UserService:

    return UserService(
        db,
    )


@router.post(
    "",
    response_model=UserResponse,
)
def create_user(
    data: UserCreate,
    service: UserService = Depends(
        get_service
    ),
):

    user = User(
        **data.model_dump()
    )

    return service.create(
        user,
    )


@router.get(
    "/{user_id}",
    response_model=UserResponse,
)
def get_user(
    user_id: int,
    service: UserService = Depends(
        get_service
    ),
):

    return service.get(
        user_id,
    )
@router.get(
    "",
    response_model=UserListResponse,
)
def get_users(
    service: UserService = Depends(
        get_service
    ),
):

    users = service.list_active()

    return {
        "items": users,
        "total": len(users),
    }


@router.get(
    "/email/{email}",
    response_model=UserResponse,
)
def get_user_by_email(
    email: str,
    service: UserService = Depends(
        get_service
    ),
):

    return service.get_by_email(
        email,
    )


@router.get(
    "/username/{username}",
    response_model=UserResponse,
)
def get_user_by_username(
    username: str,
    service: UserService = Depends(
        get_service
    ),
):

    return service.get_by_username(
        username,
    )
@router.put(
    "/{user_id}",
    response_model=UserResponse,
)
def update_user(
    user_id: int,
    data: UserUpdate,
    service: UserService = Depends(
        get_service
    ),
):

    user = service.get(
        user_id,
    )

    if user is None:
        return None

    for key, value in data.model_dump(
        exclude_unset=True
    ).items():

        setattr(
            user,
            key,
            value,
        )

    return service.update(
        user,
    )


@router.delete(
    "/{user_id}",
)
def delete_user(
    user_id: int,
    service: UserService = Depends(
        get_service
    ),
):

    user = service.get(
        user_id,
    )

    if user is None:
        return {
            "success": False,
        }

    service.delete(
        user,
    )

    return {
        "success": True,
    }
@router.patch(
    "/{user_id}/deactivate",
    response_model=UserResponse,
)
def deactivate_user(
    user_id: int,
    service: UserService = Depends(
        get_service
    ),
):

    user = service.get(
        user_id,
    )

    if user is None:
        return None

    return service.deactivate(
        user,
    )


@router.get(
    "/search/{keyword}",
    response_model=list[UserResponse],
)
def search_users(
    keyword: str,
    service: UserService = Depends(
        get_service
    ),
):

    return service.search(
        keyword,
    )


__all__ = [
    "router",
]