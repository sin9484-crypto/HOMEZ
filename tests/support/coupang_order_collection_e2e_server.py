"""Isolated browser-E2E server. Never import from production startup."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DATA_ROOT = Path(os.environ["HOMEZ_ORDER_E2E_ROOT"]).resolve()
DB_PATH = DATA_ROOT / "data" / "homez.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["HOMEZ_DATA_ROOT"] = str(DATA_ROOT)
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"

from app.database.bootstrap import bootstrap_environment
from app.database.seed import seed_environment

bootstrap_environment(
    db_path=DB_PATH,
    migrations_dir=ROOT / "migrations",
    backups_dir=DATA_ROOT / "backups",
)
seed_environment(DB_PATH)

from app.core.security import hash_password
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.session import SessionLocal
from app.domains.company.model import Company
from app.domains.inventory.model import InventorySku
from app.domains.order.adapters.coupang_collection import CoupangOrderCollectionResult
from app.domains.order.adapters.coupang_collection import CoupangOrderPage
from app.domains.order.router import get_coupang_order_provider_factory
from app.domains.order.router import get_order_credential_store
from app.domains.role.model import Role
from app.domains.store_connection.model import StoreConnection
from app.domains.user.model import User
from app.main import app


PASSWORD = "Synthetic1!Pass"
USERNAME = "order_e2e_admin"

db = SessionLocal()
try:
    company = db.query(Company).filter(Company.name == "합성 주문 E2E 회사").first()
    if company is None:
        company = Company(name="합성 주문 E2E 회사", active=True)
        db.add(company)
        db.commit()
        db.refresh(company)
    role = db.query(Role).filter(Role.code == "SUPER_ADMIN").first()
    user = db.query(User).filter(User.username == USERNAME).first()
    if user is None:
        user = User(
            company_id=company.id, role_id=role.id, username=USERNAME,
            email="order-e2e@example.com",
            password_hash=hash_password(PASSWORD), name="합성 관리자",
            is_active=True,
        )
        db.add(user)
    connection = db.query(StoreConnection).filter(
        StoreConnection.company_id == company.id,
        StoreConnection.marketplace_code == "COUPANG",
    ).first()
    if connection is None:
        connection = StoreConnection(
            company_id=company.id, marketplace_code="COUPANG",
            display_name="합성 쿠팡 계정", seller_identifier="합성 판매자",
            credential_reference="order-e2e-credential",
            masked_credential_hint="***", connection_status="CONNECTED",
            credential_version=1, created_by=1,
            creation_idempotency_key="order-e2e-create",
            creation_request_fingerprint="e" * 64,
        )
        db.add(connection)
    sku = db.query(InventorySku).filter(
        InventorySku.company_id == company.id,
        InventorySku.sku_code == "HOMEZ-E2E-SKU",
    ).first()
    if sku is None:
        db.add(InventorySku(
            company_id=company.id, product_candidate_id=999001,
            sku_code="HOMEZ-E2E-SKU", option_label="40매 38x38cm",
            available_qty=0, reserved_qty=0, safety_stock=0, is_active=True,
        ))
    db.commit()
finally:
    db.close()


credential_store = InMemoryCredentialStore()
credential_store.save("order-e2e-credential", {
    "vendor_id": "A00000000", "access_key": "SYNTHETIC_ACCESS",
    "secret_key": "SYNTHETIC_SECRET",
})


class SyntheticOrderProvider:
    def __init__(self, _credential):
        pass

    def collect(self, **_kwargs):
        return CoupangOrderCollectionResult(
            True,
            pages=(CoupangOrderPage(({
                "shipmentBoxId": 880001,
                "orderId": 990001,
                "orderedAt": "2026-08-31T10:00:00+09:00",
                "status": "ACCEPT",
                "orderer": {"name": "합성주문자"},
                "receiver": {
                    "name": "합성수취인", "safeNumber": "+82-10-0000-0000",
                    "addr1": "합성시 합성구", "addr2": "1층",
                    "postCode": "00000",
                },
                "orderItems": [{
                    "sequenceNo": "001", "vendorItemId": 770001,
                    "externalVendorSkuCode": "COUPANG-E2E-SKU",
                    "vendorItemName": "합성 부직포 주방행주 40매 38x38cm",
                    "shippingCount": 1, "holdCountForCancel": 0,
                    "cancelCount": 0,
                    "salesPrice": {"currencyCode": "KRW", "units": 12900, "nanos": 0},
                    "orderPrice": {"currencyCode": "KRW", "units": 12900, "nanos": 0},
                    "discountPrice": None, "sellerProductId": 660001,
                }],
            },), None),),
            http_status=200,
        )


app.dependency_overrides[get_order_credential_store] = lambda: credential_store
app.dependency_overrides[get_coupang_order_provider_factory] = lambda: SyntheticOrderProvider


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["HOMEZ_ORDER_E2E_PORT"]))
