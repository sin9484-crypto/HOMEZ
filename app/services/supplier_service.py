from sqlalchemy.orm import Session

from app import crud
from app.schemas import SupplierCreate


def create_supplier(
    db: Session,
    supplier: SupplierCreate,
):
    return crud.create_supplier(db, supplier)


def get_suppliers(
    db: Session,
):
    return crud.get_suppliers(db)


def update_supplier(
    db: Session,
    supplier_id: int,
    supplier: SupplierCreate,
):
    return crud.update_supplier(
        db,
        supplier_id,
        supplier,
    )


def delete_supplier(
    db: Session,
    supplier_id: int,
):
    return crud.delete_supplier(
        db,
        supplier_id,
    )