"""
=========================================================
Homez OS

Marketplace Schema
=========================================================
"""

from pydantic import BaseModel
from pydantic import ConfigDict


class MarketplaceBase(BaseModel):

    name: str

    code: str

    api_url: str | None = None

    api_key: str | None = None

    api_secret: str | None = None

    active: bool = True


class MarketplaceCreate(MarketplaceBase):
    pass


class MarketplaceUpdate(BaseModel):

    name: str | None = None

    code: str | None = None

    api_url: str | None = None

    api_key: str | None = None

    api_secret: str | None = None

    active: bool | None = None


class MarketplaceResponse(MarketplaceBase):

    id: int

    model_config = ConfigDict(
        from_attributes=True,
    )