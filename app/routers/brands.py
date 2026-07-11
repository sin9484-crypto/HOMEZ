from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.schemas import BrandCreate, Brand
from app.services.brand_service import (
    create_brand,
    get_brands,
    update_brand,
    delete_brand,
)

router = APIRouter(
    prefix="/brands",
    tags=["브랜드관리"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/",
    response_model=Brand,
    summary="브랜드 등록",
    description="새로운 브랜드를 등록합니다.",
)
def create(
    brand: BrandCreate,
    db: Session = Depends(get_db),
):
    return create_brand(db, brand)


@router.get(
    "/",
    response_model=list[Brand],
    summary="브랜드 조회",
    description="등록된 브랜드 목록을 조회합니다.",
)
def read(
    db: Session = Depends(get_db),
):
    return get_brands(db)


@router.put(
    "/{brand_id}",
    response_model=Brand,
    summary="브랜드 수정",
    description="등록된 브랜드를 수정합니다.",
)
def update(
    brand_id: int,
    brand: BrandCreate,
    db: Session = Depends(get_db),
):

    result = update_brand(
        db,
        brand_id,
        brand,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="브랜드를 찾을 수 없습니다.",
        )

    return result


@router.delete(
    "/{brand_id}",
    summary="브랜드 삭제",
    description="등록된 브랜드를 삭제합니다.",
)
def delete(
    brand_id: int,
    db: Session = Depends(get_db),
):

    result = delete_brand(
        db,
        brand_id,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="브랜드를 찾을 수 없습니다.",
        )

    return {
        "message": "삭제되었습니다."
    }