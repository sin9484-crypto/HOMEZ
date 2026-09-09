"""
=========================================================
Homez OS

Company Repository
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.company.model import Company


class CompanyRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    def get_all(self):

        return self.db.query(Company).all()

    def get(
        self,
        company_id: int,
    ):

        return (
            self.db.query(Company)
            .filter(Company.id == company_id)
            .first()
        )

    def create(
        self,
        obj: Company,
    ):

        self.db.add(obj)

        self.db.commit()

        self.db.refresh(obj)

        return obj

    def update(
        self,
        obj: Company,
    ):

        self.db.commit()

        self.db.refresh(obj)

        return obj

    def delete(
        self,
        obj: Company,
    ):

        self.db.delete(obj)

        self.db.commit()