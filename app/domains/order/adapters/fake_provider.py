"""
=========================================================
Homez OS

File : app/domains/order/adapters/fake_provider.py

채널 주문 상태 동기화 Fake Provider — 실제 네트워크 호출 없음.
channel_order_id에 `TRIGGER_*` 접두어를 넣으면 해당 실패 시나리오가
결정론적으로 재현된다(app/domains/inventory/adapters/fake_provider.py
와 동일 기법). `_CANCELLED_SUFFIX`로 끝나면 채널 측에서 주문이
취소됐다고 관측된 시나리오를 재현한다(요구사항 3 — 채널에서 관측된
취소가 내부 취소 파이프라인으로 이어지는 경로 검증용). 그 외 값은
"채널 상태 변화 없음(PAID)"으로 처리한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from app.domains.order.adapters.base import ChannelOrderStatusResult
from app.domains.order.adapters.base import OrderChannelStatusAdapter

TRIGGER_TIMEOUT = "TRIGGER_TIMEOUT"
TRIGGER_5XX = "TRIGGER_5XX"
TRIGGER_429 = "TRIGGER_429"

_ERROR_SUMMARIES: dict[str, str] = {
    TRIGGER_TIMEOUT: "채널 응답 시간이 초과되었습니다.",
    TRIGGER_5XX: "채널 플랫폼 오류입니다(5xx) — 잠시 후 다시 시도하세요.",
    TRIGGER_429: "채널 요청이 너무 많습니다(429) — 잠시 후 다시 시도하세요.",
}

_RETRY_AFTER_SECONDS_429 = 30

# 이 접미어로 끝나는 channel_order_id는 "채널에서 주문이 취소됨"을
# 재현한다(성공적인 조회지만 관측 상태가 CANCELLED).
CHANNEL_CANCELLED_SUFFIX = "_CHANNEL_CANCELLED"


class FakeOrderChannelStatusAdapter(OrderChannelStatusAdapter):

    channel_code = "FAKE"

    def check_order_status(
        self,
        channel_order_id: str,
        now: datetime,
    ) -> ChannelOrderStatusResult:

        for trigger, summary in _ERROR_SUMMARIES.items():
            if channel_order_id.startswith(trigger):
                retry_after = (
                    _RETRY_AFTER_SECONDS_429
                    if trigger == TRIGGER_429
                    else None
                )
                return ChannelOrderStatusResult(
                    success=False,
                    raw_status=None,
                    observed_at=None,
                    error_code=trigger,
                    error_summary=summary,
                    retry_after_seconds=retry_after,
                )

        if channel_order_id.endswith(CHANNEL_CANCELLED_SUFFIX):
            return ChannelOrderStatusResult(
                success=True,
                raw_status="CANCELLED",
                observed_at=now,
                error_code=None,
                error_summary=None,
                retry_after_seconds=None,
            )

        return ChannelOrderStatusResult(
            success=True,
            raw_status="PAID",
            observed_at=now,
            error_code=None,
            error_summary=None,
            retry_after_seconds=None,
        )


def get_order_channel_status_adapter(
    channel_code: str,
) -> OrderChannelStatusAdapter:
    """
    2026-08-15 V7 Gate 4 — 실제 Provider가 아직 없으므로 채널 코드와
    무관하게 항상 Fake Provider를 반환한다(Inventory Gate 3의
    get_channel_inventory_sync_adapter()와 동일 컨벤션).
    """

    return FakeOrderChannelStatusAdapter()


__all__ = [
    "FakeOrderChannelStatusAdapter",
    "get_order_channel_status_adapter",
    "TRIGGER_TIMEOUT",
    "TRIGGER_5XX",
    "TRIGGER_429",
    "CHANNEL_CANCELLED_SUFFIX",
]
