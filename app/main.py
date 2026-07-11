from fastapi import FastAPI

from app.database.init_db import initialize_database

# Routers
from app.routers.products import router as product_router
from app.routers.suppliers import router as supplier_router
from app.routers.brands import router as brand_router
from app.routers.categories import router as category_router


app = FastAPI(
    title="Homez ERP 개발자 센터 (Homez ERP Developer Center)",
    description="""
Homez AI 기반 쇼핑몰 ERP 개발자 API

Developer API Documentation for Homez ERP
""",
    version="0.1.0",
)


@app.on_event("startup")
def startup():

    initialize_database()


# ==================================================
# Router
# ==================================================

app.include_router(product_router)
app.include_router(supplier_router)
app.include_router(brand_router)
app.include_router(category_router)


@app.get(
    "/",
    tags=["시스템"],
    summary="시스템 상태",
)
def home():

    return {
        "project": "Homez",
        "version": "0.1.0",
        "status": "running",
    }