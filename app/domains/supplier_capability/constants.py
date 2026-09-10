"""
=========================================================
Homez OS

File : app/domains/supplier_capability/constants.py

2026-09-10 Phase 9(HOMEZ_USER_OPERATION_SETTINGS.md 7번 — "국내·해외
매입처 Adapter 계약을 통일하고, 상품/옵션/가격/재고/배송비/배송기간/
발주가능/취소가능 여부를 능력 플래그로 노출한다. 미확인 기능은
'미확인'으로 표시하고 추측하지 않는다") — 이 도메인이 존재하는 이유.

**왜 기존 4개의 서로 다른 Adapter 계약을 "통일"(병합)하지 않았는가**
(app/domains/purchase_task/channel_adapter.py::PurchaseChannelAdapter/
ChannelCapability, app/domains/source/discovery_providers.py::
SupplierDiscoveryProvider, app/domains/purchase/
supplier_order_providers.py::SupplierOrderProvider,
app/domains/retail_purchase/provider.py::RetailPurchaseProvider/
ProviderCapability): 네 계약 모두 이미 실제로 쓰이고 테스트되는
살아있는 코드다. 이 넷을 하나의 ABC 계층으로 강제 병합하는 것은
호출부 전체에 영향을 주는 대규모 리팩터링이라(CLAUDE.md Whitelist
— 대규모 리팩터링은 별도 승인 대상) 이번 세션의 "위험하면 서두르지
않는다" 원칙에 따라 하지 않았다.

대신 이 도메인은 **공급처 하나당 하나의 "능력 요약표"를 별도로,
추가적으로** 기록한다 — 어느 내부 Adapter 시스템을 실제로 쓰든
무관하게, "이 공급처가 실제로 무엇을 지원하는지"를 한 곳에서 사람이
읽을 수 있는 8개 플래그로 요약한다. 기존 `ChannelCapability`
(app/domains/purchase_task/constants.py)의 tri-state 관례
(SUPPORTED/NOT_SUPPORTED/UNKNOWN)를 그대로 재사용한다(새 어휘를
또 만들지 않는다) — 다만 이 도메인 안에서 독립적으로 정의해 기존
purchase_task 내부 구현에 결합하지 않는다.
=========================================================
"""

from __future__ import annotations


class CapabilitySupport:
    """"미확인은 절대 추측하지 않는다"를 UNKNOWN 기본값으로 강제한다
    — app/domains/purchase_task/constants.py::CapabilitySupport와
    동일한 관례."""

    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    UNKNOWN = "UNKNOWN"

    ALL = (SUPPORTED, NOT_SUPPORTED, UNKNOWN)


class SupplierCapabilityFlag:
    """문서 7번이 나열한 8개 항목 그대로 — 순서도 원문과 동일하게
    유지한다."""

    PRODUCT_INFO = "PRODUCT_INFO"
    OPTION_INFO = "OPTION_INFO"
    PRICE_INFO = "PRICE_INFO"
    STOCK_INFO = "STOCK_INFO"
    SHIPPING_FEE_INFO = "SHIPPING_FEE_INFO"
    SHIPPING_DAYS_INFO = "SHIPPING_DAYS_INFO"
    ORDER_PLACEMENT = "ORDER_PLACEMENT"
    CANCELABILITY = "CANCELABILITY"

    ALL = (
        PRODUCT_INFO, OPTION_INFO, PRICE_INFO, STOCK_INFO,
        SHIPPING_FEE_INFO, SHIPPING_DAYS_INFO, ORDER_PLACEMENT,
        CANCELABILITY,
    )

    LABELS_KO = {
        PRODUCT_INFO: "상품정보",
        OPTION_INFO: "옵션정보",
        PRICE_INFO: "가격정보",
        STOCK_INFO: "재고정보",
        SHIPPING_FEE_INFO: "배송비정보",
        SHIPPING_DAYS_INFO: "배송기간정보",
        ORDER_PLACEMENT: "발주가능여부",
        CANCELABILITY: "취소가능여부",
    }


__all__ = ["CapabilitySupport", "SupplierCapabilityFlag"]
