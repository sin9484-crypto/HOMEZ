"""
=========================================================
Homez OS

File : app/core/database.py
Version : 5.0.0

Database Utilities
=========================================================
"""

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.session import SessionLocal


def create_session() -> Session:

    return SessionLocal()


@contextmanager
def session_scope():

    session = SessionLocal()

    try:

        yield session

        session.commit()

    except Exception:

        session.rollback()

        raise

    finally:

        session.close()


def health_check() -> bool:

    try:

        with session_scope() as db:

            db.execute(
                text("SELECT 1")
            )

        return True

    except Exception:

        return False
def execute(
    query,
    params: dict | None = None,
):

    with session_scope() as db:

        return db.execute(
            query,
            params or {},
        )


def scalar(
    query,
    params: dict | None = None,
):

    with session_scope() as db:

        return db.execute(
            query,
            params or {},
        ).scalar()


def fetch_one(
    query,
    params: dict | None = None,
):

    with session_scope() as db:

        return db.execute(
            query,
            params or {},
        ).first()


def fetch_all(
    query,
    params: dict | None = None,
):

    with session_scope() as db:

        return db.execute(
            query,
            params or {},
        ).all()
def execute_commit(
    query,
    params: dict | None = None,
) -> None:

    with session_scope() as db:

        db.execute(
            query,
            params or {},
        )


def exists(
    query,
    params: dict | None = None,
) -> bool:

    with session_scope() as db:

        result = db.execute(
            query,
            params or {},
        ).first()

        return result is not None


def count(
    query,
    params: dict | None = None,
) -> int:

    with session_scope() as db:

        result = db.execute(
            query,
            params or {},
        ).scalar()

        return int(result or 0)
__all__ = [
    "create_session",
    "session_scope",
    "health_check",
    "execute",
    "scalar",
    "fetch_one",
    "fetch_all",
    "execute_commit",
    "exists",
    "count",
]