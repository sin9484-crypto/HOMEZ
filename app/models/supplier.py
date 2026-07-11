from sqlalchemy import Boolean, Column, Integer, String

from app.database.base import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False)

    site = Column(String, nullable=False)

    supplier_url = Column(String)

    memo = Column(String)

    is_active = Column(Boolean, default=True)