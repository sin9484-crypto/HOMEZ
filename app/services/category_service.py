from sqlalchemy.orm import Session

from app import crud
from app.schemas import CategoryCreate


def create_category(
    db: Session,
    category: CategoryCreate,
):
    return crud.create_category(db, category)


def get_categories(
    db: Session,
):
    return crud.get_categories(db)


def update_category(
    db: Session,
    category_id: int,
    category: CategoryCreate,
):
    return crud.update_category(
        db,
        category_id,
        category,
    )


def delete_category(
    db: Session,
    category_id: int,
):
    return crud.delete_category(
        db,
        category_id,
    )