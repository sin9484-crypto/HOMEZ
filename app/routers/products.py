from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.schemas import ProductCreate, Product
from app.services.product_service import (
    create_product,
    get_products,
    update_product,
    delete_product,
)

router = APIRouter(
    prefix="/products",
    tags=["Products"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/", response_model=Product)
def create(product: ProductCreate, db: Session = Depends(get_db)):
    return create_product(db, product)


@router.get("/", response_model=list[Product])
def read(db: Session = Depends(get_db)):
    return get_products(db)


@router.put("/{product_id}", response_model=Product)
def update(
    product_id: int,
    product: ProductCreate,
    db: Session = Depends(get_db),
):
    result = update_product(db, product_id, product)

    if result is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return result


@router.delete("/{product_id}")
def delete(
    product_id: int,
    db: Session = Depends(get_db),
):
    result = delete_product(db, product_id)

    if result is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "message": "Deleted successfully"
    }