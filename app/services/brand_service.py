from sqlalchemy.orm import Session

from app import crud
from app.schemas import BrandCreate


def create_brand(
    db: Session,
    brand: BrandCreate,
):
    return crud.create_brand(db, brand)


def get_brands(
    db: Session,
):
    return crud.get_brands(db)


def update_brand(
    db: Session,
    brand_id: int,
    brand: BrandCreate,
):
    return crud.update_brand(
        db,
        brand_id,
        brand,
    )


def delete_brand(
    db: Session,
    brand_id: int,
):
    return crud.delete_brand(
        db,
        brand_id,
    )