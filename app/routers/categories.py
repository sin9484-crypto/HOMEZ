from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.schemas import CategoryCreate, Category
from app.services.category_service import (
    create_category,
    get_categories,
    update_category,
    delete_category,
)

router = APIRouter(
    prefix="/categories",
    tags=["카테고리관리"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/",
    response_model=Category,
    summary="카테고리 등록",
    description="새로운 카테고리를 등록합니다.",
)
def create(
    category: CategoryCreate,
    db: Session = Depends(get_db),
):
    return create_category(db, category)


@router.get(
    "/",
    response_model=list[Category],
    summary="카테고리 조회",
    description="등록된 카테고리 목록을 조회합니다.",
)
def read(
    db: Session = Depends(get_db),
):
    return get_categories(db)


@router.put(
    "/{category_id}",
    response_model=Category,
    summary="카테고리 수정",
    description="등록된 카테고리를 수정합니다.",
)
def update(
    category_id: int,
    category: CategoryCreate,
    db: Session = Depends(get_db),
):

    result = update_category(
        db,
        category_id,
        category,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="카테고리를 찾을 수 없습니다.",
        )

    return result


@router.delete(
    "/{category_id}",
    summary="카테고리 삭제",
    description="등록된 카테고리를 삭제합니다.",
)
def delete(
    category_id: int,
    db: Session = Depends(get_db),
):

    result = delete_category(
        db,
        category_id,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="카테고리를 찾을 수 없습니다.",
        )

    return {
        "message": "삭제되었습니다."
    }