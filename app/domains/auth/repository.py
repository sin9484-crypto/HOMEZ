"""
=========================================================
Homez OS

Auth Repository
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.user.model import User


class AuthRepository:

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: int):

        return (
            self.db.query(User)
            .filter(User.id == user_id)
            .first()
        )

    def get_by_email(self, email: str):

        return (
            self.db.query(User)
            .filter(User.email == email)
            .first()
        )

    def get_by_username(self, username: str):

        return (
            self.db.query(User)
            .filter(User.username == username)
            .first()
        )

    def get_active_user(self, username: str):

        return (
            self.db.query(User)
            .filter(
                User.username == username,
                User.is_active.is_(True),
            )
            .first()
        )

    def exists_email(self, email: str) -> bool:

        return (
            self.db.query(User)
            .filter(User.email == email)
            .first()
            is not None
        )

    def exists_username(self, username: str) -> bool:

        return (
            self.db.query(User)
            .filter(User.username == username)
            .first()
            is not None
        )

    def create(self, user: User):

        self.db.add(user)
        self.db.flush()
        self.db.refresh(user)

        return user

    def update(self, user: User):

        self.db.add(user)
        self.db.flush()
        self.db.refresh(user)

        return user

    def delete(self, user: User):

        self.db.delete(user)

    def list(self):

        return (
            self.db.query(User)
            .order_by(User.id.desc())
            .all()
        )