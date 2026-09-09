from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base


class Product(Base):

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sku: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True,
        index=True,
    )

    brand_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "brands.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    category_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "categories.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    supplier_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "suppliers.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    price: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    sale_price: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    cost_price: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    stock_quantity: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="ACTIVE",
        nullable=False,
        index=True,
    )
    ai_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    view_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    sales_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    return_rate: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    search_keywords: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    image_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    supplier_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    metadata_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        onupdate=datetime.utcnow,
    )

    brand = relationship(
        "Brand",
        back_populates="products",
    )

    category = relationship(
        "Category",
        back_populates="products",
    )

    supplier = relationship(
        "Supplier",
        back_populates="products",
    )

    inventories = relationship(
        "Inventory",
        back_populates="product",
    )


    def __repr__(
        self,
    ) -> str:

        return (
            f"Product("
            f"id={self.id}, "
            f"name='{self.name}', "
            f"status='{self.status}'"
            f")"
        )


__all__ = [
    "Product",
]
