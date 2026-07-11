from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.schemas import SupplierCreate, Supplier
from app.services.supplier_service import (
    create_supplier,
    get_suppliers,
    update_supplier,
    delete_supplier,
)

router = APIRouter(
    prefix="/suppliers",
    tags=["Suppliers"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/", response_model=Supplier)
def create(
    supplier: SupplierCreate,
    db: Session = Depends(get_db),
):
    return create_supplier(db, supplier)


@router.get("/", response_model=list[Supplier])
def read(
    db: Session = Depends(get_db),
):
    return get_suppliers(db)


@router.put("/{supplier_id}", response_model=Supplier)
def update(
    supplier_id: int,
    supplier: SupplierCreate,
    db: Session = Depends(get_db),
):
    result = update_supplier(
        db,
        supplier_id,
        supplier,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Supplier not found",
        )

    return result


@router.delete("/{supplier_id}")
def delete(
    supplier_id: int,
    db: Session = Depends(get_db),
):
    result = delete_supplier(
        db,
        supplier_id,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Supplier not found",
        )

    return {
        "message": "Deleted successfully"
    }