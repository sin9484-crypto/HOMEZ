"""
=========================================================
Homez OS

File : app/domains/inventory/adapters/base.py

채널 재고 동기화 Adapter 공통 인터페이스 — Fake Provider 전용(2026-08-15
V7 Gate 3). `docs/HOMEZ_V7_INVENTORY_PLAN.md` 9절: "채널 재고 동기화는
store_connection의 기존 어댑터 패턴(Fake Provider로 먼저 검증)을
재사용한다" — 실제 외부 API 호출은 이번 Gate뿐 아니라 V7 착수 초기
단계에서도 실행하지 않는다.
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ChannelSyncResult:
    """
    실제 플랫폼 응답 원문은 담지 않는다(store_connection.adapters.base.
    VerificationResult와 동일 원칙) — error_summary는 사람이 읽을 수
    있는 안전한 요약 문장만 담는다.
    """

    success: bool
    error_code: str | None
    error_summary: str | None
    synced_at: datetime


class ChannelInventorySyncAdapter(ABC):

    channel_code: str

    @abstractmethod
    def sync_stock(
        self,
        channel_sku: str,
        available_qty: int,
        now: datetime,
    ) -> ChannelSyncResult:
        """
        채널에 재고 수량을 반영 요청한다 — Fake Provider는 실제 네트워크
        호출을 하지 않고 channel_sku 값에 따라 결정론적 결과를
        반환한다(테스트 전용 TRIGGER_* 접두어).
        """


__all__ = [
    "ChannelSyncResult",
    "ChannelInventorySyncAdapter",
]
