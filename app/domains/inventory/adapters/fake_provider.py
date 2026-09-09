"""
=========================================================
Homez OS

File : app/domains/inventory/adapters/fake_provider.py

채널 재고 동기화 Fake Provider — 실제 네트워크 호출 없음. channel_sku에
`TRIGGER_*` 접두어를 넣으면 해당 시나리오가 결정론적으로 재현된다
(테스트 전용, app/domains/store_connection/adapters/coupang.py와 동일
기법) — 그 외 값은 전부 정상 동기화로 처리한다. 채널 코드 무관하게
동일한 Fake 동작을 쓴다(실제 Provider가 붙기 전까지는 채널별로 실제
차이가 없다 — 과설계 방지).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from app.domains.inventory.adapters.base import ChannelInventorySyncAdapter
from app.domains.inventory.adapters.base import ChannelSyncResult

TRIGGER_TIMEOUT = "TRIGGER_TIMEOUT"
TRIGGER_5XX = "TRIGGER_5XX"
TRIGGER_429 = "TRIGGER_429"
TRIGGER_INVALID_SKU = "TRIGGER_INVALID_SKU"

_ERROR_SUMMARIES: dict[str, str] = {
    TRIGGER_TIMEOUT: "채널 응답 시간이 초과되었습니다.",
    TRIGGER_5XX: "채널 플랫폼 오류입니다(5xx) — 잠시 후 다시 시도하세요.",
    TRIGGER_429: "채널 요청이 너무 많습니다(429) — 잠시 후 다시 시도하세요.",
    TRIGGER_INVALID_SKU: "채널에 등록되지 않은 SKU입니다.",
}


class FakeChannelInventorySyncAdapter(ChannelInventorySyncAdapter):

    channel_code = "FAKE"

    def sync_stock(
        self,
        channel_sku: str,
        available_qty: int,
        now: datetime,
    ) -> ChannelSyncResult:

        trigger = None

        for code in _ERROR_SUMMARIES:
            if channel_sku.startswith(code):
                trigger = code
                break

        if trigger is not None:
            return ChannelSyncResult(
                success=False,
                error_code=trigger,
                error_summary=_ERROR_SUMMARIES[trigger],
                synced_at=now,
            )

        return ChannelSyncResult(
            success=True,
            error_code=None,
            error_summary=None,
            synced_at=now,
        )


def get_channel_inventory_sync_adapter(
    channel_code: str,
) -> ChannelInventorySyncAdapter:
    """
    2026-08-15 V7 Gate 3 — 실제 Provider가 아직 없으므로 채널 코드와
    무관하게 항상 Fake Provider를 반환한다(계획 문서 9절 — "실제
    외부 재고 동기화 API 호출은 이번 Gate뿐 아니라 V7 착수 초기
    단계에서도 실행하지 않는다"). marketplace_listing.status_provider.
    get_status_provider()와 동일하게, 실제 Provider가 준비되면 이
    팩토리 함수 하나만 바꿔 끼우면 된다.
    """

    return FakeChannelInventorySyncAdapter()


__all__ = [
    "FakeChannelInventorySyncAdapter",
    "get_channel_inventory_sync_adapter",
    "TRIGGER_TIMEOUT",
    "TRIGGER_5XX",
    "TRIGGER_429",
    "TRIGGER_INVALID_SKU",
]
