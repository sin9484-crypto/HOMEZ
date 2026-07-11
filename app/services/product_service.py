from sqlalchemy.orm import Session

from app import crud
from app.schemas import ProductCreate


def create_product(db: Session, product: ProductCreate):
    return crud.create_product(db, product)


def get_products(db: Session):
    return crud.get_products(db)


def update_product(db: Session, product_id: int, product: ProductCreate):
    return crud.update_product(db, product_id, product)


def delete_product(db: Session, product_id: int):
    return crud.delete_product(db, product_id)