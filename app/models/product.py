from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    String,
)

from app.database.base import Base


class Product(Base):
    __tablename__ = "products"

    # ===========================
    # 기본 정보
    # ===========================

    id = Column(Integer, primary_key=True, index=True)

    product_code = Column(
        String(100),
        unique=True,
        nullable=False,
    )

    name = Column(
        String(255),
        nullable=False,
    )

    # ===========================
    # ERP 기본 정보
    # ===========================

    brand = Column(
        String(255),
        default="",
    )

    category = Column(
        String(255),
        default="",
    )

    supplier = Column(
        String(255),
        default="",
    )

    # ===========================
    # 가격
    # ===========================

    cost_price = Column(
        Integer,
        default=0,
    )

    selling_price = Column(
        Integer,
        default=0,
    )

    expected_profit = Column(
        Integer,
        default=0,
    )

    # ===========================
    # 상품 상태
    # ===========================

    status = Column(
        String(30),
        default="판매중",
    )

    is_active = Column(
        Boolean,
        default=True,
    )

    # ===========================
    # AI
    # ===========================

    ai_score = Column(
        Integer,
        default=0,
    )

    ai_reason = Column(
        String(1000),
        default="",
    )