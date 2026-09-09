"""
=========================================================
Homez OS

Marketplace Router
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.marketplace.service import MarketplaceService
from app.domains.marketplace.schema import (
    MarketplaceCreate,
    MarketplaceResponse,
    MarketplaceUpdate,
)


router = APIRouter()


@router.get(
    "/",
    response_model=list[MarketplaceResponse],
)
def get_marketplaces(
    db: Session = Depends(get_db),
):

    service = MarketplaceService(db)

    return service.get_all()


@router.get(
    "/{marketplace_id}",
    response_model=MarketplaceResponse,
)
def get_marketplace(
    marketplace_id: int,
    db: Session = Depends(get_db),
):

    service = MarketplaceService(db)

    marketplace = service.get(
        marketplace_id
    )

    if marketplace is None:

        raise HTTPException(
            status_code=404,
            detail="Marketplace not found",
        )

    return marketplace


@router.post(
    "/",
    response_model=MarketplaceResponse,
)
def create_marketplace(
    marketplace: MarketplaceCreate,
    db: Session = Depends(get_db),
):

    service = MarketplaceService(db)

    return service.create(
        marketplace
    )


@router.put(
    "/{marketplace_id}",
    response_model=MarketplaceResponse,
)
def update_marketplace(
    marketplace_id: int,
    marketplace: MarketplaceUpdate,
    db: Session = Depends(get_db),
):

    service = MarketplaceService(db)

    result = service.update(
        marketplace_id,
        marketplace,
    )

    if result is None:

        raise HTTPException(
            status_code=404,
            detail="Marketplace not found",
        )

    return result


@router.delete(
    "/{marketplace_id}",
)
def delete_marketplace(
    marketplace_id: int,
    db: Session = Depends(get_db),
):

    service = MarketplaceService(db)

    if not service.delete(
        marketplace_id
    ):

        raise HTTPException(
            status_code=404,
            detail="Marketplace not found",
        )

    return {
        "message": "Marketplace deleted"
    }