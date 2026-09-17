"""
=========================================================
Homez OS

File : app/domains/order/multi_channel_collection_service.py

전체 판매처 주문 수집 오케스트레이션 — 기존 채널별 개별 수집
서비스(CoupangOrderCollectionService 등)를 반복 호출하는 얇은
래퍼다. 커서/락(lock_token)·중복방지·정규화 로직은 여기서 다시
구현하지 않고 기존 서비스에 전부 위임한다.

마켓별 실제 수집 서비스는 _COLLECTION_RUNNERS 레지스트리에 등록한다
— 새 채널의 수집 서비스가 생기면 이 레지스트리에 한 줄만 추가하면
되고, 이 오케스트레이터 자체는 다시 손대지 않는다.

한 연결(계정)의 실패가 다른 연결의 수집을 막지 않는다 — 부분 실패는
그대로 결과에 담아 반환한다(조용히 건너뛰지 않는다).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.windows_credential_store import CredentialStore
from app.domains.order.adapters.coupang_collection import ALLOWED_STATUSES as COUPANG_ALLOWED_STATUSES
from app.domains.order.coupang_collection_service import CoupangCollectionRunResult
from app.domains.order.coupang_collection_service import CoupangOrderCollectionService
from app.domains.store_connection.model import StoreConnection


@dataclass(frozen=True)
class MultiChannelCollectionEntry:
    store_connection_id: int
    marketplace_code: str
    channel_status: str
    status: str
    received_order_count: int = 0
    new_fulfillment_count: int = 0
    updated_fulfillment_count: int = 0
    duplicate_fulfillment_count: int = 0
    failed_order_count: int = 0
    # 2026-09-16 개인 베타 잔여 작업(Phase 7, 2-8 운영 화면) — 신규
    # 미연결(SKU 매핑 필요) 항목 수. 기존 필드는 그대로 두고 끝에
    # 추가했다(기본값 있음 — 위치 인자로 생성하는 기존 호출부를
    # 깨지 않는다).
    new_unresolved_item_count: int = 0
    # 2026-09-17 Phase 7A 사후 감사 2차 — ACCEPT가 아닌 상태로 처음
    # 발견됐지만 HOMEZ에 대응 Order가 없는 주문(복구 검토 대상) 수.
    recovery_review_count: int = 0
    error_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MultiChannelCollectionRunResult:
    total_connections: int
    entries: tuple[MultiChannelCollectionEntry, ...]
    skipped_marketplace_codes: tuple[str, ...] = ()

    @property
    def succeeded_runs(self) -> int:
        return sum(1 for e in self.entries if e.status == "SUCCEEDED")

    @property
    def partial_runs(self) -> int:
        return sum(1 for e in self.entries if e.status == "PARTIAL")

    @property
    def failed_runs(self) -> int:
        return sum(1 for e in self.entries if e.status == "FAILED")


def _run_coupang_connection(
    db: Session, credential_store: CredentialStore, company_id: int,
    connection: StoreConnection, actor_user_id: int,
    provider_factory=None, statuses=None,
) -> list[MultiChannelCollectionEntry]:
    """쿠팡 연결 하나에 대해 `statuses`(없으면 지원되는 모든
    channel_status)를 순회한다 — 기존 CoupangOrderCollectionService.
    run()을 그대로 재사용.

    2026-09-17 개인 베타 실데이터 검증 Phase 7A 사후 감사 — 원래는
    항상 6개 상태 전부를 순회했다. `statuses`를 명시하면 그 부분
    집합만 조회한다(예: 신규 주문 자동 감지는 ACCEPT만 필요 — 아래
    `auto_collection_scheduler.py` 참고). 기존 "전체 통합 수집"
    수동 버튼(`POST /orders/collect/all`)은 `statuses`를 넘기지
    않아 기존 동작(6개 전부) 그대로 유지된다."""

    allowed = statuses if statuses is not None else COUPANG_ALLOWED_STATUSES
    unknown = set(allowed) - COUPANG_ALLOWED_STATUSES
    if unknown:
        raise ValueError(f"지원하지 않는 쿠팡 주문 상태입니다: {sorted(unknown)}")

    service = CoupangOrderCollectionService(
        db, credential_store,
        **({"provider_factory": provider_factory} if provider_factory else {}),
    )
    entries: list[MultiChannelCollectionEntry] = []
    for channel_status in sorted(allowed):
        result: CoupangCollectionRunResult = service.run(
            company_id, connection.id, channel_status,
            actor_user_id=actor_user_id,
        )
        entries.append(MultiChannelCollectionEntry(
            store_connection_id=connection.id,
            marketplace_code=connection.marketplace_code,
            channel_status=result.channel_status,
            status=result.status,
            received_order_count=result.received_order_count,
            new_fulfillment_count=result.new_fulfillment_count,
            updated_fulfillment_count=result.updated_fulfillment_count,
            duplicate_fulfillment_count=result.duplicate_fulfillment_count,
            failed_order_count=result.failed_order_count,
            new_unresolved_item_count=result.new_unresolved_item_count,
            recovery_review_count=result.recovery_review_count,
            error_codes=result.error_codes,
        ))
    return entries


# 마켓별 실제 실행기 레지스트리 — 새 채널이 생기면 여기 한 줄만 추가한다.
_COLLECTION_RUNNERS: dict[str, Callable] = {
    "COUPANG": _run_coupang_connection,
}


class OrderMultiChannelCollectionService:
    def __init__(self, db: Session, credential_store: CredentialStore, *, provider_factory=None):
        self.db = db
        self.credential_store = credential_store
        self.provider_factory = provider_factory

    def run_all(
        self, company_id: int, *, actor_user_id: int = 0,
        statuses: "frozenset[str] | set[str] | None" = None,
    ) -> MultiChannelCollectionRunResult:
        """`statuses`를 생략하면 기존과 동일하게(각 채널이 지원하는
        모든 상태) 수집한다 — 기존 "전체 통합 수집" 수동 버튼의
        동작을 바꾸지 않는다. 신규 주문 자동 감지처럼 특정 상태만
        필요한 호출부는 `statuses={"ACCEPT"}`처럼 명시적으로 좁힐
        수 있다(2026-09-17 Phase 7A 사후 감사 후속)."""

        connections = (
            self.db.query(StoreConnection)
            .filter(StoreConnection.company_id == company_id)
            .filter(StoreConnection.connection_status == "CONNECTED")
            .order_by(StoreConnection.id.asc())
            .all()
        )

        entries: list[MultiChannelCollectionEntry] = []
        skipped: list[str] = []
        for connection in connections:
            runner = _COLLECTION_RUNNERS.get(connection.marketplace_code)
            if runner is None:
                if connection.marketplace_code not in skipped:
                    skipped.append(connection.marketplace_code)
                continue
            entries.extend(runner(
                self.db, self.credential_store, company_id, connection,
                actor_user_id, self.provider_factory, statuses,
            ))

        return MultiChannelCollectionRunResult(
            total_connections=len(connections),
            entries=tuple(entries),
            skipped_marketplace_codes=tuple(skipped),
        )


__all__ = [
    "MultiChannelCollectionEntry",
    "MultiChannelCollectionRunResult",
    "OrderMultiChannelCollectionService",
]
