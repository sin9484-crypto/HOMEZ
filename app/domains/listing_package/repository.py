"""
=========================================================
Homez OS

File : app/domains/listing_package/repository.py

no-commit Repository — 커밋/롤백은 Service 책임. 모든 단건 조회·
조건부 UPDATE는 company_id를 WHERE에 포함한다(예외 없음).
=========================================================
"""

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.listing_package.model import ListingPackage
from app.domains.listing_package.model import ListingPackageApproval


class ListingPackageRepository:

    def __init__(self, db: Session):

        self.db = db

    # ------------------------------------------------
    # ListingPackage
    # ------------------------------------------------

    def add_package_no_commit(self, package: ListingPackage) -> ListingPackage:

        self.db.add(package)
        self.db.flush()

        return package

    def get_package_for_company(
        self, package_id: int, company_id: int,
    ) -> ListingPackage | None:

        return (
            self.db.query(ListingPackage)
            .filter(ListingPackage.id == package_id)
            .filter(ListingPackage.company_id == company_id)
            .first()
        )

    def get_package_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> ListingPackage | None:

        return (
            self.db.query(ListingPackage)
            .filter(ListingPackage.company_id == company_id)
            .filter(ListingPackage.idempotency_key == idempotency_key)
            .first()
        )

    def list_packages_for_candidate(
        self, company_id: int, product_candidate_id: int,
    ) -> list[ListingPackage]:

        return (
            self.db.query(ListingPackage)
            .filter(ListingPackage.company_id == company_id)
            .filter(
                ListingPackage.product_candidate_id == product_candidate_id,
            )
            .order_by(ListingPackage.id.desc())
            .all()
        )

    def update_package_fields_conditional(
        self,
        package_id: int,
        company_id: int,
        expected_statuses: tuple[str, ...],
        values: dict,
    ) -> int:

        stmt = (
            update(ListingPackage)
            .where(ListingPackage.id == package_id)
            .where(ListingPackage.company_id == company_id)
            .where(ListingPackage.status.in_(expected_statuses))
            .values(**values)
        )

        return self.db.execute(stmt).rowcount

    # ------------------------------------------------
    # ListingPackageApproval (append-only)
    # ------------------------------------------------

    def add_approval_no_commit(
        self, approval: ListingPackageApproval,
    ) -> ListingPackageApproval:

        self.db.add(approval)
        self.db.flush()

        return approval

    def get_approval_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> ListingPackageApproval | None:

        return (
            self.db.query(ListingPackageApproval)
            .filter(ListingPackageApproval.company_id == company_id)
            .filter(ListingPackageApproval.idempotency_key == idempotency_key)
            .first()
        )

    def get_latest_approval(
        self, listing_package_id: int, company_id: int,
    ) -> ListingPackageApproval | None:

        return (
            self.db.query(ListingPackageApproval)
            .filter(ListingPackageApproval.company_id == company_id)
            .filter(
                ListingPackageApproval.listing_package_id
                == listing_package_id,
            )
            .order_by(ListingPackageApproval.id.desc())
            .first()
        )

    def list_approval_history(
        self, listing_package_id: int, company_id: int,
    ) -> list[ListingPackageApproval]:

        return (
            self.db.query(ListingPackageApproval)
            .filter(ListingPackageApproval.company_id == company_id)
            .filter(
                ListingPackageApproval.listing_package_id
                == listing_package_id,
            )
            .order_by(ListingPackageApproval.id)
            .all()
        )


__all__ = [
    "ListingPackageRepository",
]
