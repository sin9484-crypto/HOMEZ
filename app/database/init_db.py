"""
=========================================================
Homez OS

Database Initialize
=========================================================
"""

from app.database.base import Base
from app.database.session import engine

# ----------------------------
# Models
# ----------------------------

from app.domains.company.model import Company
from app.domains.user.model import User
from app.domains.role.model import Role
from app.domains.category.model import Category
from app.domains.brand.model import Brand
from app.domains.supplier.model import Supplier
from app.domains.product.model import Product
from app.domains.marketplace.model import Marketplace


def initialize_database():

    Base.metadata.create_all(
        bind=engine
    )