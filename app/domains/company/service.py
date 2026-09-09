"""
=========================================================
Homez OS

Company Service
=========================================================
"""

from sqlalchemy.orm import Session

from app.core.base_service import BaseService

from app.domains.company.model import Company
from app.domains.company.repository import CompanyRepository
from app.domains.company.schema import (
    CompanyCreate,
    CompanyUpdate,
)


class CompanyService(BaseService[CompanyRepository]):

    def __init__(
        self,
        db: Session,
    ):

        # 2026-08-02: BaseService.__init__()은 repository만 받는다 — 이전
        # 코드는 존재하지 않는 db= 키워드를 전달해 CompanyService()가
        # 호출될 때마다 무조건 TypeError로 죽는 상태였다(인증 여부와
        # 무관하게 /companies/* 전체가 500이었다는 뜻). /companies/*
        # 보안 감사 중 함께 발견해 수정한다.
        super().__init__(
            repository=CompanyRepository(db),
        )

    def get_all(self):

        return self.repository.get_all()

    def get(
        self,
        company_id: int,
    ):

        return self.repository.get(company_id)

    def create(
        self,
        company: CompanyCreate,
    ):

        obj = Company(
            **company.model_dump()
        )

        return self.repository.create(obj)

    def update(
        self,
        company_id: int,
        company: CompanyUpdate,
    ):

        obj = self.repository.get(
            company_id
        )

        if obj is None:
            return None

        values = company.model_dump(
            exclude_unset=True
        )

        for key, value in values.items():

            setattr(
                obj,
                key,
                value,
            )

        return self.repository.update(obj)

    def delete(
        self,
        company_id: int,
    ):

        obj = self.repository.get(
            company_id
        )

        if obj is None:
            return False

        self.repository.delete(obj)

        return True