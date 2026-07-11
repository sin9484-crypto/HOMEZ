from pydantic import BaseModel


# ==================================================
# Product
# ==================================================

class ProductCreate(BaseModel):
    # 기본 정보
    product_code: str
    name: str

    # ERP 기본 정보
    brand: str = ""
    category: str = ""
    supplier: str = ""

    # 가격 정보
    cost_price: int = 0
    selling_price: int = 0
    expected_profit: int = 0

    # 상품 상태
    status: str = "판매중"
    is_active: bool = True

    # AI 정보
    ai_score: int = 0
    ai_reason: str = ""


class Product(ProductCreate):
    id: int

    class Config:
        from_attributes = True


# ==================================================
# Brand
# ==================================================

class BrandCreate(BaseModel):
    name: str


class Brand(BrandCreate):
    id: int

    class Config:
        from_attributes = True


# ==================================================
# Category
# ==================================================

class CategoryCreate(BaseModel):
    name: str


class Category(CategoryCreate):
    id: int

    class Config:
        from_attributes = True


# ==================================================
# Supplier
# ==================================================

class SupplierCreate(BaseModel):
    name: str
    site: str
    supplier_url: str | None = None
    memo: str | None = None
    is_active: bool = True


class Supplier(SupplierCreate):
    id: int

    class Config:
        from_attributes = True