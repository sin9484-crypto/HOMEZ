"""
=========================================================
Homez OS

File : app/domains/order/adapters/base.py

채널 주문 상태 동기화 Adapter 공통 인터페이스 — Fake Provider 전용
(2026-08-15 V7 Gate 4). app/domains/inventory/adapters/base.py(Gate 3)
와 동일한 패턴 — 실제 외부 API 호출은 하지 않는다.

채널로부터 "새 주문이 들어왔다"는 요구사항 1의 수집 자체는 웹훅/푸시
수신 형태(OrderService.collect_channel_order — raw payload를 그대로
받아 저장)로 처리하고, 이 Adapter는 오직 "이미 수집된 주문의 채널 측
상태가 바뀌었는지" 폴링 확인(요구사항 6 — 부분 실패/안전한 재시도의
실제 무대)만 담당한다.
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ChannelOrderStatusResult:
    """
    실제 플랫폼 응답 원문은 담지 않는다(store_connection.adapters.base.
    VerificationResult와 동일 원칙).
    """

    success: bool
    raw_status: str | None
    observed_at: datetime | None
    error_code: str | None
    error_summary: str | None
    retry_after_seconds: int | None


class OrderChannelStatusAdapter(ABC):

    channel_code: str

    @abstractmethod
    def check_order_status(
        self,
        channel_order_id: str,
        now: datetime,
    ) -> ChannelOrderStatusResult:
        """
        채널에 이 주문의 현재 상태를 조회한다 — Fake Provider는 실제
        네트워크 호출을 하지 않고 channel_order_id 값에 따라 결정론적
        결과를 반환한다(테스트 전용 TRIGGER_* 접두어/접미어).
        """


__all__ = [
    "ChannelOrderStatusResult",
    "OrderChannelStatusAdapter",
]
