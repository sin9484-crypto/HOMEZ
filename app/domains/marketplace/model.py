"""
=========================================================
Homez OS

Marketplace Model
Version : 2.0.0
=========================================================
"""

from sqlalchemy import Boolean
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base


class Marketplace(Base):

    __tablename__ = "marketplaces"

    # --------------------------------------------------
    # Primary Key
    # --------------------------------------------------

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # --------------------------------------------------
    # Foreign Key
    # --------------------------------------------------

    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"),
        nullable=True,
    )

    # --------------------------------------------------
    # Marketplace Information
    # --------------------------------------------------

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
    )

    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
    )

    api_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    api_key: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    api_secret: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # --------------------------------------------------
    # Relationship
    # --------------------------------------------------

    company = relationship(
        "Company",
        back_populates="marketplaces",
        lazy="joined",
    )

    # --------------------------------------------------
    # Representation
    # --------------------------------------------------

    def __repr__(self):

        return (
            f"<Marketplace("
            f"id={self.id}, "
            f"name='{self.name}'"
            f")>"
        )