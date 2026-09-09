"""
=========================================================
Homez OS

File : app/domains/source/discovery_providers.py

공급처 검색(SupplierDiscoveryProvider) 경계 — 2026-08-20 CTO Section
2. 실제 인터넷/B2B 공급처 검색 API는 아직 연결하지 않는다
(SUPPLIER_DISCOVERY_PROVIDER_PENDING). Fake/Manual/Csv 세 Provider는
전부 네트워크 호출이 없다 — 검색 결과는 이 결과를 바탕으로 사용자가
`SourceService.create_link()`로 직접 확정해야 실제 등록된다(이
모듈 자체는 아무것도 저장하지 않는다).
=========================================================
"""

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime


@dataclass(frozen=True)
class SupplierDiscoveryQuery:

    product_name: str
    brand: str | None = None
    barcode: str | None = None
    manufacturer_sku: str | None = None
    category: str | None = None
    min_order_qty: int | None = None
    ship_regions: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SupplierDiscoveryResultItem:

    supplier_id: int
    supplier_product_id: str
    product_name: str
    options: str | None
    unit_cost: float
    stock: int | None
    moq: int
    shipping_cost: float | None
    lead_time_days: int | None
    last_checked_at: datetime
    source: str
    verification_status: str  # VERIFIED / UNVERIFIED


class SupplierDiscoveryProviderError(Exception):
    """Provider 자체가 요청을 처리할 수 없을 때."""


class SupplierDiscoveryProvider(ABC):

    code: str = "ABSTRACT"

    @abstractmethod
    def search_suppliers(
        self, query: SupplierDiscoveryQuery,
    ) -> list[SupplierDiscoveryResultItem]:
        ...


class FakeSupplierDiscoveryProvider(SupplierDiscoveryProvider):
    """네트워크 호출 없이 결정론적인 더미 결과 1건을 반환한다 — 실제
    검색이 아니므로 verification_status는 항상 UNVERIFIED다."""

    code = "FAKE"

    def search_suppliers(
        self, query: SupplierDiscoveryQuery,
    ) -> list[SupplierDiscoveryResultItem]:

        return [
            SupplierDiscoveryResultItem(
                supplier_id=0,
                supplier_product_id=f"FAKE-{query.product_name}",
                product_name=f"[시연용] {query.product_name}",
                options=query.brand,
                unit_cost=0.0,
                stock=None,
                moq=1,
                shipping_cost=None,
                lead_time_days=None,
                last_checked_at=datetime.utcnow(),
                source="FAKE_PROVIDER",
                verification_status="UNVERIFIED",
            ),
        ]


class ManualSupplierDiscoveryProvider(SupplierDiscoveryProvider):
    """자동 검색을 아예 시도하지 않는다 — 항상 빈 목록을 반환해 UI가
    수동 입력(SourceService.create_link 직접 호출) 경로로 넘어가게
    한다."""

    code = "MANUAL"

    def search_suppliers(
        self, query: SupplierDiscoveryQuery,
    ) -> list[SupplierDiscoveryResultItem]:

        return []


class CsvSupplierDiscoveryProvider(SupplierDiscoveryProvider):
    """사용자가 미리 준비한 CSV/표(메모리 내 dict 목록)에서만
    검색한다 — 외부 호출 없음. 각 row는 최소
    supplier_id/supplier_product_id/product_name/unit_cost/moq를
    가져야 한다."""

    code = "CSV"

    def __init__(self, rows: list[dict]):

        self._rows = rows

    def search_suppliers(
        self, query: SupplierDiscoveryQuery,
    ) -> list[SupplierDiscoveryResultItem]:

        keyword = query.product_name.strip().lower()
        results = []

        for row in self._rows:
            name = str(row.get("product_name", ""))
            if keyword and keyword not in name.lower():
                continue

            results.append(SupplierDiscoveryResultItem(
                supplier_id=int(row["supplier_id"]),
                supplier_product_id=str(row["supplier_product_id"]),
                product_name=name,
                options=row.get("options"),
                unit_cost=float(row["unit_cost"]),
                stock=row.get("stock"),
                moq=int(row.get("moq", 1)),
                shipping_cost=row.get("shipping_cost"),
                lead_time_days=row.get("lead_time_days"),
                last_checked_at=datetime.utcnow(),
                source="CSV_IMPORT",
                verification_status="UNVERIFIED",
            ))

        return results


PROVIDERS_BY_CODE: dict[str, type] = {
    "FAKE": FakeSupplierDiscoveryProvider,
    "MANUAL": ManualSupplierDiscoveryProvider,
}


def get_discovery_provider(code: str) -> SupplierDiscoveryProvider:

    provider_cls = PROVIDERS_BY_CODE.get(code)
    if provider_cls is None:
        raise SupplierDiscoveryProviderError(
            f"알 수 없거나 아직 연결되지 않은 Provider입니다: {code} "
            "(SUPPLIER_DISCOVERY_PROVIDER_PENDING)",
        )

    return provider_cls()


__all__ = [
    "SupplierDiscoveryQuery",
    "SupplierDiscoveryResultItem",
    "SupplierDiscoveryProviderError",
    "SupplierDiscoveryProvider",
    "FakeSupplierDiscoveryProvider",
    "ManualSupplierDiscoveryProvider",
    "CsvSupplierDiscoveryProvider",
    "PROVIDERS_BY_CODE",
    "get_discovery_provider",
]
