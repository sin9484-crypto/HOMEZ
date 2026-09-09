"""
=========================================================
Homez OS

File : app/domains/source/repository.py

no-commit Repository — 커밋/롤백은 항상 호출하는 Service의 책임이다
(이 코드베이스 전체 컨벤션). 단건 조회는 전부 company_id를 WHERE에
포함한다.
=========================================================
"""

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.source.model import CompanySupplierRelation
from app.domains.source.model import SupplierProductLink


class SourceRepository:

    def __init__(self, db: Session):

        self.db = db

    def add_link_no_commit(
        self, link: SupplierProductLink,
    ) -> SupplierProductLink:

        self.db.add(link)
        self.db.flush()

        return link

    def get_link_for_company(
        self, link_id: int, company_id: int,
    ) -> SupplierProductLink | None:

        return (
            self.db.query(SupplierProductLink)
            .filter(SupplierProductLink.id == link_id)
            .filter(SupplierProductLink.company_id == company_id)
            .first()
        )

    def get_link_by_natural_key(
        self, company_id: int, product_candidate_id: int,
        supplier_id: int, supplier_sku: str,
    ) -> SupplierProductLink | None:

        return (
            self.db.query(SupplierProductLink)
            .filter(SupplierProductLink.company_id == company_id)
            .filter(
                SupplierProductLink.product_candidate_id
                == product_candidate_id,
            )
            .filter(SupplierProductLink.supplier_id == supplier_id)
            .filter(SupplierProductLink.supplier_sku == supplier_sku)
            .first()
        )

    def list_links_for_candidate(
        self, company_id: int, product_candidate_id: int,
    ) -> list[SupplierProductLink]:

        return (
            self.db.query(SupplierProductLink)
            .filter(SupplierProductLink.company_id == company_id)
            .filter(
                SupplierProductLink.product_candidate_id
                == product_candidate_id,
            )
            .filter(SupplierProductLink.status == "ACTIVE")
            .order_by(SupplierProductLink.unit_cost.asc())
            .all()
        )

    def update_status_conditional(
        self, link_id: int, company_id: int,
        expected_statuses: tuple[str, ...], new_status: str,
    ) -> int:

        stmt = (
            update(SupplierProductLink)
            .where(SupplierProductLink.id == link_id)
            .where(SupplierProductLink.company_id == company_id)
            .where(SupplierProductLink.status.in_(expected_statuses))
            .values(status=new_status)
        )

        return self.db.execute(stmt).rowcount


    # ------------------------------------------------
    # CompanySupplierRelation
    # ------------------------------------------------

    def add_relation_no_commit(
        self, relation: CompanySupplierRelation,
    ) -> CompanySupplierRelation:

        self.db.add(relation)
        self.db.flush()

        return relation

    def get_relation_for_company(
        self, relation_id: int, company_id: int,
    ) -> CompanySupplierRelation | None:

        return (
            self.db.query(CompanySupplierRelation)
            .filter(CompanySupplierRelation.id == relation_id)
            .filter(CompanySupplierRelation.company_id == company_id)
            .first()
        )

    def get_relation_by_company_supplier(
        self, company_id: int, supplier_id: int,
    ) -> CompanySupplierRelation | None:

        return (
            self.db.query(CompanySupplierRelation)
            .filter(CompanySupplierRelation.company_id == company_id)
            .filter(CompanySupplierRelation.supplier_id == supplier_id)
            .first()
        )

    def list_relations_for_company(
        self, company_id: int,
    ) -> list[CompanySupplierRelation]:

        return (
            self.db.query(CompanySupplierRelation)
            .filter(CompanySupplierRelation.company_id == company_id)
            .order_by(CompanySupplierRelation.id)
            .all()
        )

    def update_approval_status_conditional(
        self, relation_id: int, company_id: int,
        expected_statuses: tuple[str, ...], new_status: str,
    ) -> int:

        stmt = (
            update(CompanySupplierRelation)
            .where(CompanySupplierRelation.id == relation_id)
            .where(CompanySupplierRelation.company_id == company_id)
            .where(CompanySupplierRelation.approval_status.in_(expected_statuses))
            .values(approval_status=new_status)
        )

        return self.db.execute(stmt).rowcount


__all__ = [
    "SourceRepository",
]
