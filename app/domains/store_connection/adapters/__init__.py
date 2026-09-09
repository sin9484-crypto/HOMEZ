import os
import sys

from app.domains.store_connection.adapters.base import StoreConnectionAdapter
from app.domains.store_connection.adapters.base import VerificationResult
from app.domains.store_connection.adapters.coupang import CoupangConnectionAdapter
from app.domains.store_connection.adapters.coupang_production import CoupangProductionAdapter
from app.domains.store_connection.adapters.naver import NaverConnectionAdapter
from app.domains.store_connection.adapters.naver_production import NaverProductionAdapter
from app.domains.store_connection.constants import MarketplaceCode

ADAPTERS_BY_MARKETPLACE_CODE: dict[str, StoreConnectionAdapter] = {
    MarketplaceCode.COUPANG: CoupangConnectionAdapter(),
    MarketplaceCode.NAVER_SMARTSTORE: NaverConnectionAdapter(),
}

PRODUCTION_ADAPTERS_BY_MARKETPLACE_CODE: dict[str, StoreConnectionAdapter] = {
    MarketplaceCode.COUPANG: CoupangProductionAdapter(),
    MarketplaceCode.NAVER_SMARTSTORE: NaverProductionAdapter(),
}


def _adapter_mode() -> str:
    """Official frozen builds use live adapters; source/test runs stay fake.

    An explicit environment override exists for controlled packaged tests and
    must never be inferred from credential contents.
    """

    configured = os.environ.get("HOMEZ_STORE_CONNECTION_ADAPTER_MODE", "").strip().upper()
    if configured in {"FAKE", "PRODUCTION"}:
        return configured
    return "PRODUCTION" if getattr(sys, "frozen", False) else "FAKE"


def get_adapter(marketplace_code: str) -> StoreConnectionAdapter | None:
    registry = (
        PRODUCTION_ADAPTERS_BY_MARKETPLACE_CODE
        if _adapter_mode() == "PRODUCTION"
        else ADAPTERS_BY_MARKETPLACE_CODE
    )
    return registry.get(marketplace_code)


__all__ = [
    "StoreConnectionAdapter",
    "VerificationResult",
    "CoupangConnectionAdapter",
    "NaverConnectionAdapter",
    "ADAPTERS_BY_MARKETPLACE_CODE",
    "PRODUCTION_ADAPTERS_BY_MARKETPLACE_CODE",
    "get_adapter",
]
