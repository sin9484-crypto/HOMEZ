"""
=========================================================
Homez OS

Company Schema
=========================================================
"""

from pydantic import BaseModel, ConfigDict


class CompanyBase(BaseModel):

    name: str

    business_number: str | None = None

    ceo: str | None = None

    phone: str | None = None

    email: str | None = None

    address: str | None = None

    active: bool = True


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):

    name: str | None = None

    business_number: str | None = None

    ceo: str | None = None

    phone: str | None = None

    email: str | None = None

    address: str | None = None

    active: bool | None = None


class CompanyResponse(CompanyBase):

    id: int

    model_config = ConfigDict(
        from_attributes=True
    )