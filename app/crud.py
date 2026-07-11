from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.supplier import Supplier
from app.models.brand import Brand
from app.models.category import Category

from app.schemas import (
    ProductCreate,
    SupplierCreate,
    BrandCreate,
    CategoryCreate,
)


# ==================================================
# Product CRUD
# ==================================================

def create_product(
    db: Session,
    product: ProductCreate,
):

    db_product = Product(
        name=product.name,
        brand=product.brand,
        price=product.price,
    )

    db.add(db_product)
    db.commit()
    db.refresh(db_product)

    return db_product


def get_products(
    db: Session,
):

    return db.query(Product).all()


def update_product(
    db: Session,
    product_id: int,
    product: ProductCreate,
):

    db_product = (
        db.query(Product)
        .filter(Product.id == product_id)
        .first()
    )

    if db_product is None:
        return None

    db_product.name = product.name
    db_product.brand = product.brand
    db_product.price = product.price

    db.commit()
    db.refresh(db_product)

    return db_product


def delete_product(
    db: Session,
    product_id: int,
):

    db_product = (
        db.query(Product)
        .filter(Product.id == product_id)
        .first()
    )

    if db_product is None:
        return None

    db.delete(db_product)
    db.commit()

    return db_product
# ==================================================
# Supplier CRUD
# ==================================================

def create_supplier(
    db: Session,
    supplier: SupplierCreate,
):

    db_supplier = Supplier(
        name=supplier.name,
        site=supplier.site,
        supplier_url=supplier.supplier_url,
        memo=supplier.memo,
        is_active=supplier.is_active,
    )

    db.add(db_supplier)
    db.commit()
    db.refresh(db_supplier)

    return db_supplier


def get_suppliers(
    db: Session,
):

    return db.query(Supplier).all()


def update_supplier(
    db: Session,
    supplier_id: int,
    supplier: SupplierCreate,
):

    db_supplier = (
        db.query(Supplier)
        .filter(Supplier.id == supplier_id)
        .first()
    )

    if db_supplier is None:
        return None

    db_supplier.name = supplier.name
    db_supplier.site = supplier.site
    db_supplier.supplier_url = supplier.supplier_url
    db_supplier.memo = supplier.memo
    db_supplier.is_active = supplier.is_active

    db.commit()
    db.refresh(db_supplier)

    return db_supplier


def delete_supplier(
    db: Session,
    supplier_id: int,
):

    db_supplier = (
        db.query(Supplier)
        .filter(Supplier.id == supplier_id)
        .first()
    )

    if db_supplier is None:
        return None

    db.delete(db_supplier)
    db.commit()

    return db_supplier
# ==================================================
# Brand CRUD
# ==================================================

def create_brand(
    db: Session,
    brand: BrandCreate,
):

    db_brand = Brand(
        name=brand.name,
    )

    db.add(db_brand)
    db.commit()
    db.refresh(db_brand)

    return db_brand


def get_brands(
    db: Session,
):

    return db.query(Brand).all()


def update_brand(
    db: Session,
    brand_id: int,
    brand: BrandCreate,
):

    db_brand = (
        db.query(Brand)
        .filter(Brand.id == brand_id)
        .first()
    )

    if db_brand is None:
        return None

    db_brand.name = brand.name

    db.commit()
    db.refresh(db_brand)

    return db_brand


def delete_brand(
    db: Session,
    brand_id: int,
):

    db_brand = (
        db.query(Brand)
        .filter(Brand.id == brand_id)
        .first()
    )

    if db_brand is None:
        return None

    db.delete(db_brand)
    db.commit()

    return db_brand
# ==================================================
# Category CRUD
# ==================================================

def create_category(
    db: Session,
    category: CategoryCreate,
):

    db_category = Category(
        name=category.name,
    )

    db.add(db_category)
    db.commit()
    db.refresh(db_category)

    return db_category


def get_categories(
    db: Session,
):

    return db.query(Category).all()


def update_category(
    db: Session,
    category_id: int,
    category: CategoryCreate,
):

    db_category = (
        db.query(Category)
        .filter(Category.id == category_id)
        .first()
    )

    if db_category is None:
        return None

    db_category.name = category.name

    db.commit()
    db.refresh(db_category)

    return db_category


def delete_category(
    db: Session,
    category_id: int,
):

    db_category = (
        db.query(Category)
        .filter(Category.id == category_id)
        .first()
    )

    if db_category is None:
        return None

    db.delete(db_category)
    db.commit()

    return db_category