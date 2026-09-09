from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.category.model import (
    Category,
)

from app.domains.category.schema import (
    CategoryCreate,
    CategoryUpdate,
    CategoryResponse,
    CategoryListResponse,
)

from app.domains.category.service import (
    CategoryService,
)


router = APIRouter(
    prefix="/categories",
    tags=["Categories"],
)


def get_service(
    db: Session = Depends(get_db),
) -> CategoryService:

    return CategoryService(
        db,
    )


@router.post(
    "",
    response_model=CategoryResponse,
)
def create_category(
    data: CategoryCreate,
    service: CategoryService = Depends(
        get_service
    ),
):

    category = Category(
        **data.model_dump()
    )

    return service.create(
        category,
    )


@router.get(
    "/{category_id}",
    response_model=CategoryResponse,
)
def get_category(
    category_id: int,
    service: CategoryService = Depends(
        get_service
    ),
):

    return service.get(
        category_id,
    )
@router.get(
    "",
    response_model=CategoryListResponse,
)
def get_categories(
    service: CategoryService = Depends(
        get_service
    ),
):

    categories = service.list_active()

    return {
        "items": categories,
        "total": len(categories),
    }


@router.get(
    "/root/list",
    response_model=list[CategoryResponse],
)
def get_root_categories(
    service: CategoryService = Depends(
        get_service
    ),
):

    return service.get_root_categories()


@router.get(
    "/{category_id}/children",
    response_model=list[CategoryResponse],
)
def get_child_categories(
    category_id: int,
    service: CategoryService = Depends(
        get_service
    ),
):

    return service.get_children(
        category_id,
    )


@router.get(
    "/search/{keyword}",
    response_model=list[CategoryResponse],
)
def search_categories(
    keyword: str,
    service: CategoryService = Depends(
        get_service
    ),
):

    return service.search(
        keyword,
    )
@router.get(
    "/slug/{slug}",
    response_model=CategoryResponse,
)
def get_category_by_slug(
    slug: str,
    service: CategoryService = Depends(
        get_service
    ),
):

    return service.get_by_slug(
        slug,
    )


@router.get(
    "/name/{name}",
    response_model=CategoryResponse,
)
def get_category_by_name(
    name: str,
    service: CategoryService = Depends(
        get_service
    ),
):

    return service.get_by_name(
        name,
    )


@router.get(
    "/visible/list",
    response_model=list[CategoryResponse],
)
def get_visible_categories(
    service: CategoryService = Depends(
        get_service
    ),
):

    return service.get_visible_categories()


@router.get(
    "/popular/list",
    response_model=list[CategoryResponse],
)
def get_popular_categories(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    service: CategoryService = Depends(
        get_service
    ),
):

    return service.get_popular_categories(
        limit,
    )
@router.put(
    "/{category_id}",
    response_model=CategoryResponse,
)
def update_category(
    category_id: int,
    data: CategoryUpdate,
    service: CategoryService = Depends(
        get_service
    ),
):

    category = service.get(
        category_id,
    )

    if category is None:
        return None

    for key, value in data.model_dump(
        exclude_unset=True
    ).items():

        setattr(
            category,
            key,
            value,
        )

    return service.update(
        category,
    )


@router.patch(
    "/{category_id}/deactivate",
    response_model=CategoryResponse,
)
def deactivate_category(
    category_id: int,
    service: CategoryService = Depends(
        get_service
    ),
):

    category = service.get(
        category_id,
    )

    if category is None:
        return None

    return service.deactivate(
        category,
    )


@router.delete(
    "/{category_id}",
)
def delete_category(
    category_id: int,
    service: CategoryService = Depends(
        get_service
    ),
):

    category = service.get(
        category_id,
    )

    if category is None:
        return {
            "success": False,
        }

    service.delete(
        category,
    )

    return {
        "success": True,
    }


@router.get(
    "/tree/all",
)
def get_category_tree(
    service: CategoryService = Depends(
        get_service
    ),
):

    categories = service.list_active()

    return service.build_tree(
        categories,
    )


__all__ = [
    "router",
]