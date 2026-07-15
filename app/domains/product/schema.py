"""
HOMEZ AI Commerce OS
Build 06

Product Schema
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class ProductBase(BaseModel):
    """
    Product 공통 모델
    """

    name: str

    brand: Optional[str] = None

    category: Optional[str] = None

    supplier: Optional[str] = None

    purchase_price: float = 0.0

    selling_price: float = 0.0

    margin_rate: float = 0.0

    is_active: bool = True


class ProductCreate(ProductBase):
    """
    상품 생성
    """
    pass


class ProductUpdate(BaseModel):
    """
    상품 수정
    """

    name: Optional[str] = None

    brand: Optional[str] = None

    category: Optional[str] = None

    supplier: Optional[str] = None

    purchase_price: Optional[float] = None

    selling_price: Optional[float] = None

    margin_rate: Optional[float] = None

    is_active: Optional[bool] = None


class ProductResponse(ProductBase):
    """
    상품 조회
    """

    id: int

    model_config = ConfigDict(
        from_attributes=True
    )