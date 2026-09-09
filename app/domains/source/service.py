"""
=========================================================
Homez OS

File : app/domains/source/service.py

Sourcing Service — 공급처(Supplier, 전역 카탈로그) ↔ 상품 후보
(ProductCandidate, company 격리) 연결. 실제 발주 실행은 이 도메인의
책임이 아니다(app.domains.purchase.service.PurchaseService가 이미
전담) — 여기서는 "발주 전, 어떤 공급처의 어떤 SKU를 얼마에 살지
찾아 확정"하는 상류 단계만 담당한다.
=========================================================
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.product_candidate.repository import ProductCandidateRepository
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.source.model import CompanySupplierRelation
from app.domains.source.model import SupplierProductLink
from app.domains.source.repository import SourceRepository
from app.domains.source.schema import CompanySupplierRelationCreate
from app.domains.source.schema import SupplierProductLinkCreate
from app.domains.supplier.model import Supplier


class SourceService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = SourceRepository(db)
        self._candidate_repository = ProductCandidateRepository(db)

    def create_link(
        self, data: SupplierProductLinkCreate, company_id: int,
        created_by: int,
    ) -> SupplierProductLink:
        """2026-08-21 작업 1(4차 지시) — "승인된 후보만 공급처 상품
        연결 대상으로 선택할 수 있게 한다" 요구사항. ProductCandidate
        Domain이 이미 제공하는 단일 승인 게이트(require_approved_
        for_company — coupang/marketplace_listing 등이 공유하는
        것과 동일한 관문)를 그대로 재사용한다 — 이 회사가 아직 결정
        하지 않았거나(다른 회사 결정에 무임승차 불가) HELD/REJECTED인
        후보는 여기서 400으로 차단되고, 다른 회사의 PRIVATE 후보는
        404로 존재 자체가 숨는다(get_visible_for_company가 이미
        하던 동작을 그대로 포함)."""

        ProductCandidateService(self.db).require_approved_for_company(
            data.product_candidate_id, company_id,
        )

        supplier = self.db.get(Supplier, data.supplier_id)
        if supplier is None or not supplier.is_active:
            raise BadRequestException(
                "존재하지 않거나 비활성화된 공급처입니다.",
            )

        existing = self.repository.get_link_by_natural_key(
            company_id, data.product_candidate_id, data.supplier_id,
            data.supplier_sku,
        )
        if existing is not None:
            return existing

        link = SupplierProductLink(
            company_id=company_id,
            product_candidate_id=data.product_candidate_id,
            supplier_id=data.supplier_id,
            supplier_sku=data.supplier_sku,
            unit_cost=data.unit_cost,
            moq=data.moq,
            lead_time_days=data.lead_time_days,
            shipping_cost=data.shipping_cost,
            price_valid_until=data.price_valid_until,
            stock_available=data.stock_available,
            return_policy=data.return_policy,
            status="ACTIVE",
            created_by=created_by,
        )

        try:
            link = self.repository.add_link_no_commit(link)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_link_by_natural_key(
                company_id, data.product_candidate_id, data.supplier_id,
                data.supplier_sku,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "공급처 연결 생성 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        return link

    def list_links_for_candidate(
        self, product_candidate_id: int, company_id: int,
    ) -> list[SupplierProductLink]:

        candidate = self._candidate_repository.get_visible_for_company(
            product_candidate_id, company_id,
        )
        if candidate is None:
            raise NotFoundException("상품 후보를 찾을 수 없습니다.")

        return self.repository.list_links_for_candidate(
            company_id, product_candidate_id,
        )

    def get_best_link(
        self, product_candidate_id: int, company_id: int,
    ) -> SupplierProductLink | None:
        """가장 저렴한 활성 연결 1건 — repository가 이미 unit_cost
        오름차순으로 정렬해 반환하므로 첫 번째만 취한다."""

        links = self.list_links_for_candidate(product_candidate_id, company_id)

        return links[0] if links else None

    def deactivate_link(
        self, link_id: int, company_id: int,
    ) -> SupplierProductLink:

        link = self.repository.get_link_for_company(link_id, company_id)
        if link is None:
            raise NotFoundException("공급처 연결을 찾을 수 없습니다.")

        rowcount = self.repository.update_status_conditional(
            link_id, company_id, ("ACTIVE",), "INACTIVE",
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "이미 비활성화된 연결입니다 — 다시 조회 후 시도하세요.",
            )

        self.db.commit()

        return self.repository.get_link_for_company(link_id, company_id)


    # ------------------------------------------------
    # 회사별 공급처 거래정보(CompanySupplierRelation)
    # ------------------------------------------------

    def create_relation(
        self, data: CompanySupplierRelationCreate, company_id: int,
        created_by: int,
    ) -> CompanySupplierRelation:

        supplier = self.db.get(Supplier, data.supplier_id)
        if supplier is None:
            raise NotFoundException("공급처를 찾을 수 없습니다.")

        existing = self.repository.get_relation_by_company_supplier(
            company_id, data.supplier_id,
        )
        if existing is not None:
            return existing

        relation = CompanySupplierRelation(
            company_id=company_id,
            supplier_id=data.supplier_id,
            approval_status="PENDING",
            contact_name=data.contact_name,
            contact_phone=data.contact_phone,
            contact_email=data.contact_email,
            payment_terms=data.payment_terms,
            credential_reference=data.credential_reference,
            notes=data.notes,
            created_by=created_by,
        )

        try:
            relation = self.repository.add_relation_no_commit(relation)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_relation_by_company_supplier(
                company_id, data.supplier_id,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "공급처 거래정보 생성 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        return relation

    def list_relations(self, company_id: int) -> list[CompanySupplierRelation]:

        return self.repository.list_relations_for_company(company_id)

    def set_relation_approval(
        self, relation_id: int, company_id: int, approve: bool,
    ) -> CompanySupplierRelation:

        relation = self.repository.get_relation_for_company(
            relation_id, company_id,
        )
        if relation is None:
            raise NotFoundException("공급처 거래정보를 찾을 수 없습니다.")

        new_status = "APPROVED" if approve else "REJECTED"
        rowcount = self.repository.update_approval_status_conditional(
            relation_id, company_id, ("PENDING", "APPROVED", "REJECTED"),
            new_status,
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "승인 상태 변경 중 충돌이 발생했습니다 — 다시 조회 후 시도하세요.",
            )

        self.db.commit()

        return self.repository.get_relation_for_company(relation_id, company_id)


__all__ = [
    "SourceService",
]
