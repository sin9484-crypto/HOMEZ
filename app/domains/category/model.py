from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base


class Category(Base):

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    slug: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "categories.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    sort_order: Mapped[int] = mapped_column(
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

    is_visible: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    product_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    level: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    ai_keyword: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    metadata_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    image_url: Mapped[str | None] = mapped_column(
        String(500),
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

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    parent = relationship(
        "Category",
        remote_side="Category.id",
        back_populates="children",
    )

    children = relationship(
        "Category",
        back_populates="parent",
    )

    products = relationship(
        "Product",
        back_populates="category",
    )

    def __repr__(
        self,
    ) -> str:

        return (
            f"Category("
            f"id={self.id}, "
            f"name='{self.name}', "
            f"level={self.level}"
            f")"
        )


__all__ = [
    "Category",
]

# 2026-10-30 시험 격리 결함 수정 — inventory/model.py 끝의 설명과 같은 이유다. 위 클래스의 relationship("Product")는
# 공유 Base 레지스트리에서 이름으로 늦게(첫 mapper configure 때) 해석되므로, 이 모듈만 import된 프로세스(예: 같은 패키지의
# 다른 모듈을 불러온 시험)에서 첫 조회가 일어나면 매퍼 설정이 실패해 그 프로세스의 뒤 시험이 연쇄로 깨졌다(2026-10-29 회귀:
# 같은 묶음에서 오류 72~196건, 모듈 단독은 통과). 이 모듈이 참조 대상을 항상 함께 등록하도록 여기서 import한다.
# 순환 import를 피하려고 반드시 클래스 정의 '뒤'에 둔다.
from app.domains.product.model import Product  # noqa: F401,E402
