"""
=========================================================
Homez OS

API Router
=========================================================
"""

from fastapi import APIRouter

from app.core.config import settings

from app.domains.auth.router import router as auth_router
from app.domains.user.router import router as user_router
from app.domains.company.router import router as company_router
from app.domains.role.router import router as role_router
from app.domains.category.router import router as category_router
from app.domains.brand.router import router as brand_router
from app.domains.supplier.router import router as supplier_router
from app.domains.product.router import router as product_router
from app.domains.marketplace.router import router as marketplace_router
from app.domains.audit.router import router as audit_router


api_router = APIRouter(
    prefix=f"{settings.API_PREFIX}{settings.API_V1_STR}"
)


api_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["Auth"],
)

api_router.include_router(
    user_router,
    prefix="/users",
    tags=["User"],
)

api_router.include_router(
    company_router,
    prefix="/companies",
    tags=["Company"],
)

api_router.include_router(
    role_router,
    prefix="/roles",
    tags=["Role"],
)

api_router.include_router(
    category_router,
    prefix="/categories",
    tags=["Category"],
)

api_router.include_router(
    brand_router,
    prefix="/brands",
    tags=["Brand"],
)

api_router.include_router(
    supplier_router,
    prefix="/suppliers",
    tags=["Supplier"],
)

api_router.include_router(
    product_router,
    prefix="/products",
    tags=["Product"],
)

api_router.include_router(
    marketplace_router,
    prefix="/marketplaces",
    tags=["Marketplace"],
)

api_router.include_router(
    audit_router,
    prefix="/audits",
    tags=["Audit"],
)