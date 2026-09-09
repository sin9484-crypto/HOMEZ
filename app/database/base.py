"""
=========================================================
Homez OS

File : app/database/base.py
Version : 2.1.0

SQLAlchemy Base
=========================================================
"""

from sqlalchemy.orm import DeclarativeBase


class Base(
    DeclarativeBase
):
    """
    Base class for all SQLAlchemy ORM models.
    """

    pass