"""Normalize allowlisted Coupang order-sheet fields without retaining payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


class CoupangOrderNormalizationError(ValueError):
    """A stable error code only; never include source values or PII."""


@dataclass(frozen=True)
class NormalizedMoney:
    currency: str
    amount: Decimal


@dataclass(frozen=True)
class NormalizedCoupangOrderItem:
    channel_item_id: str
    vendor_item_id: str
    channel_sku: str
    product_name: str
    ordered_quantity: int
    hold_for_cancel_quantity: int
    cancelled_quantity: int
    fulfillable_quantity: int
    unit_price: NormalizedMoney
    order_price: NormalizedMoney
    discount_price: NormalizedMoney | None
    seller_product_id: str | None
    fulfillable: bool


@dataclass(frozen=True)
class NormalizedCoupangOrder:
    channel_order_id: str
    channel_fulfillment_id: str
    ordered_at: datetime
    raw_status: str
    buyer_name: str
    receiver_name: str
    receiver_phone: str
    receiver_address: str
    receiver_zipcode: str
    items: tuple[NormalizedCoupangOrderItem, ...]


def _required_dict(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoupangOrderNormalizationError(code)
    return value


def _required_text(value: Any, code: str) -> str:
    if value is None:
        raise CoupangOrderNormalizationError(code)
    text = str(value).strip()
    if not text:
        raise CoupangOrderNormalizationError(code)
    return text


def _non_negative_int(value: Any, code: str) -> int:
    if isinstance(value, bool):
        raise CoupangOrderNormalizationError(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise CoupangOrderNormalizationError(code) from None
    if parsed < 0 or str(value).strip() != str(parsed):
        raise CoupangOrderNormalizationError(code)
    return parsed


def normalize_money(value: Any, code: str) -> NormalizedMoney:
    data = _required_dict(value, code)
    currency = _required_text(data.get("currencyCode"), code)
    if currency != "KRW":
        raise CoupangOrderNormalizationError("UNSUPPORTED_CURRENCY")
    units = _non_negative_int(data.get("units"), code)
    nanos = _non_negative_int(data.get("nanos", 0), code)
    if nanos > 999_999_999:
        raise CoupangOrderNormalizationError(code)
    amount = Decimal(units) + (Decimal(nanos) / Decimal(1_000_000_000))
    if not amount.is_finite() or amount < 0:
        raise CoupangOrderNormalizationError(code)
    return NormalizedMoney(currency, amount)


def _parse_datetime(value: Any) -> datetime:
    text = _required_text(value, "ORDERED_AT_REQUIRED")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise CoupangOrderNormalizationError("ORDERED_AT_INVALID") from None
    if parsed.tzinfo is None:
        raise CoupangOrderNormalizationError("ORDERED_AT_TIMEZONE_REQUIRED")
    return parsed


def normalize_coupang_order_item(value: Any) -> NormalizedCoupangOrderItem:
    item = _required_dict(value, "ORDER_ITEM_INVALID")
    sequence = _required_text(item.get("sequenceNo"), "SEQUENCE_NO_REQUIRED")
    vendor_item_id = _required_text(
        item.get("vendorItemId"), "VENDOR_ITEM_ID_REQUIRED",
    )
    external_sku = str(item.get("externalVendorSkuCode") or "").strip()
    channel_sku = external_sku or vendor_item_id
    product_name = _required_text(
        item.get("vendorItemName"), "VENDOR_ITEM_NAME_REQUIRED",
    )
    ordered = _non_negative_int(
        item.get("shippingCount"), "SHIPPING_COUNT_INVALID",
    )
    held = _non_negative_int(
        item.get("holdCountForCancel", 0), "HOLD_COUNT_INVALID",
    )
    cancelled = _non_negative_int(
        item.get("cancelCount", 0), "CANCEL_COUNT_INVALID",
    )
    fulfillable_quantity = ordered - held - cancelled
    if fulfillable_quantity < 0:
        raise CoupangOrderNormalizationError("FULFILLABLE_QUANTITY_INVALID")

    seller_product_raw = item.get("sellerProductId")
    seller_product_id = (
        None if seller_product_raw in (None, "", 0, "0")
        else _required_text(seller_product_raw, "SELLER_PRODUCT_ID_INVALID")
    )
    discount = item.get("discountPrice")
    return NormalizedCoupangOrderItem(
        channel_item_id=sequence,
        vendor_item_id=vendor_item_id,
        channel_sku=channel_sku,
        product_name=product_name,
        ordered_quantity=ordered,
        hold_for_cancel_quantity=held,
        cancelled_quantity=cancelled,
        fulfillable_quantity=fulfillable_quantity,
        unit_price=normalize_money(item.get("salesPrice"), "SALES_PRICE_INVALID"),
        order_price=normalize_money(item.get("orderPrice"), "ORDER_PRICE_INVALID"),
        discount_price=(
            None if discount is None
            else normalize_money(discount, "DISCOUNT_PRICE_INVALID")
        ),
        seller_product_id=seller_product_id,
        fulfillable=fulfillable_quantity > 0,
    )


def normalize_coupang_order(value: Any) -> NormalizedCoupangOrder:
    order = _required_dict(value, "ORDER_INVALID")
    orderer = _required_dict(order.get("orderer"), "ORDERER_REQUIRED")
    receiver = _required_dict(order.get("receiver"), "RECEIVER_REQUIRED")
    items_raw = order.get("orderItems")
    if not isinstance(items_raw, list) or not items_raw:
        raise CoupangOrderNormalizationError("ORDER_ITEMS_REQUIRED")
    address_parts = [
        str(receiver.get("addr1") or "").strip(),
        str(receiver.get("addr2") or "").strip(),
    ]
    address = " ".join(part for part in address_parts if part)
    if not address:
        raise CoupangOrderNormalizationError("RECEIVER_ADDRESS_REQUIRED")
    return NormalizedCoupangOrder(
        channel_order_id=_required_text(order.get("orderId"), "ORDER_ID_REQUIRED"),
        channel_fulfillment_id=_required_text(
            order.get("shipmentBoxId"), "SHIPMENT_BOX_ID_REQUIRED",
        ),
        ordered_at=_parse_datetime(order.get("orderedAt")),
        raw_status=_required_text(order.get("status"), "ORDER_STATUS_REQUIRED"),
        buyer_name=_required_text(orderer.get("name"), "ORDERER_NAME_REQUIRED"),
        receiver_name=_required_text(receiver.get("name"), "RECEIVER_NAME_REQUIRED"),
        receiver_phone=_required_text(
            receiver.get("safeNumber"), "RECEIVER_PHONE_REQUIRED",
        ),
        receiver_address=address,
        receiver_zipcode=_required_text(
            receiver.get("postCode"), "RECEIVER_ZIPCODE_REQUIRED",
        ),
        items=tuple(normalize_coupang_order_item(item) for item in items_raw),
    )


__all__ = [
    "CoupangOrderNormalizationError",
    "NormalizedCoupangOrder",
    "NormalizedCoupangOrderItem",
    "NormalizedMoney",
    "normalize_coupang_order",
    "normalize_coupang_order_item",
    "normalize_money",
]
