"""
=========================================================
Homez OS

File : app/domains/inventory/adapters/__init__.py

Inventory 채널 재고 동기화 Adapter — Fake Provider 전용(2026-08-15
V7 Gate 3). app/domains/store_connection/adapters와 동일 원칙: 어떤
구현체도 requests/httpx/socket 등 네트워크 라이브러리를 import하지
않는다. 실제 쿠팡/네이버 재고 동기화 API 연동은 별도 Live Gate.
=========================================================
"""

from app.domains.inventory.adapters.base import ChannelInventorySyncAdapter
from app.domains.inventory.adapters.base import ChannelSyncResult
from app.domains.inventory.adapters.fake_provider import (
    FakeChannelInventorySyncAdapter,
)
from app.domains.inventory.adapters.fake_provider import (
    get_channel_inventory_sync_adapter,
)

__all__ = [
    "ChannelInventorySyncAdapter",
    "ChannelSyncResult",
    "FakeChannelInventorySyncAdapter",
    "get_channel_inventory_sync_adapter",
]
