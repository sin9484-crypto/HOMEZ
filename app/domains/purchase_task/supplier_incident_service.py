"""
=========================================================
Homez OS

File : app/domains/purchase_task/supplier_incident_service.py

2026-09-15 전면 감사 후속(Phase 9B, HOMEZ_USER_OPERATION_SETTINGS.md
7-11 — "품절, 오배송, 취소 또는 배송 지연이 반복되면 자동발주를
일시 중지하고 사용자에게 재사용 여부를 묻는다").

이 서비스는 PurchaseChannelConnectionService와 의도적으로 분리한다
— Credential Store 의존성이 전혀 없다(사건 기록·집계·일시중지
판정은 자격증명을 전혀 건드리지 않는다).

핵심 규칙:
1. 품절/오배송/취소/배송지연/인증실패를 각각 다른 사건으로 기록한다
   (하나의 카운터로 합치지 않는다 — 사용자에게 "무엇이" 반복됐는지
   정확히 알리기 위함).
2. 설정 가능한 기간(window_days)·횟수(max_incident_count)를 넘으면
   그 연결의 "발주" 기능만 PurchaseChannelConnection.order_paused_at
   으로 일시중지한다 — 다른 연결, 다른 기능(등록/가격/재고 등)은
   전혀 건드리지 않는다.
3. 자동으로 다시 활성화하지 않는다 — reactivate_order_function()을
   통해 사람이 명시적으로 확인 근거를 남겨야만 풀린다.
4. 일시중지가 발동하는 "그 순간"에만 활성 SUPER_ADMIN 전원에게
   알림을 보낸다(SUPPLIER_CONNECTION_ORDER_AUTO_PAUSED) — 이미
   일시중지된 상태에서 사건이 더 쌓여도 반복 알림을 보내지 않는다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.notification_center.operational_events import dispatch_operational_event
from app.domains.purchase_task.constants import ChannelConnectionEventType
from app.domains.purchase_task.constants import DEFAULT_SUPPLIER_INCIDENT_MAX_COUNT
from app.domains.purchase_task.constants import DEFAULT_SUPPLIER_INCIDENT_WINDOW_DAYS
from app.domains.purchase_task.constants import SupplierIncidentType
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent
from app.domains.purchase_task.model import PurchaseChannelConnectionIncident
from app.domains.purchase_task.model import SupplierIncidentAutoPauseSetting


class SupplierIncidentService:

    def __init__(self, db: Session):

        self.db = db

    # ---------------- 설정 ----------------

    def get_auto_pause_setting(self, company_id: int) -> tuple[int, int]:
        """(window_days, max_incident_count) — 설정된 적 없으면
        안전한 기본값을 반환한다."""

        latest = (
            self.db.query(SupplierIncidentAutoPauseSetting)
            .filter(SupplierIncidentAutoPauseSetting.company_id == company_id)
            .order_by(SupplierIncidentAutoPauseSetting.id.desc())
            .first()
        )
        if latest is None:
            return (
                DEFAULT_SUPPLIER_INCIDENT_WINDOW_DAYS,
                DEFAULT_SUPPLIER_INCIDENT_MAX_COUNT,
            )
        return latest.window_days, latest.max_incident_count

    def set_auto_pause_setting(
        self, company_id: int, *, window_days: int, max_incident_count: int,
        set_by: int, is_admin: bool,
    ) -> SupplierIncidentAutoPauseSetting:

        if not is_admin:
            raise ForbiddenException(
                "매입처 사건 자동일시중지 기준 변경은 관리자만 가능합니다.",
            )
        if window_days <= 0 or max_incident_count <= 0:
            raise BadRequestException(
                "기간(일)과 횟수는 모두 1 이상이어야 합니다.",
            )

        setting = SupplierIncidentAutoPauseSetting(
            company_id=company_id, window_days=window_days,
            max_incident_count=max_incident_count, set_by=set_by,
        )
        self.db.add(setting)
        self.db.commit()
        self.db.refresh(setting)
        return setting

    # ---------------- 사건 기록 ----------------

    def record_incident(
        self, *, connection_id: int, company_id: int, incident_type: str,
        detail: str | None = None, recorded_by: int | None,
        occurred_at: datetime | None = None,
    ) -> PurchaseChannelConnectionIncident:

        if incident_type not in SupplierIncidentType.ALL:
            raise BadRequestException(f"알 수 없는 사건 종류: {incident_type}")

        connection = (
            self.db.query(PurchaseChannelConnection)
            .filter(
                PurchaseChannelConnection.id == connection_id,
                PurchaseChannelConnection.company_id == company_id,
            )
            .first()
        )
        if connection is None:
            raise NotFoundException(
                "매입처 연결을 찾을 수 없습니다 — 같은 회사 소유의 연결인지 확인하세요.",
            )

        incident = PurchaseChannelConnectionIncident(
            company_id=company_id, connection_id=connection.id,
            incident_type=incident_type, detail=detail,
            recorded_by=recorded_by,
            occurred_at=occurred_at or datetime.utcnow(),
        )
        self.db.add(incident)
        self.db.commit()
        self.db.refresh(incident)

        self._maybe_pause_for_incidents(connection)

        return incident

    def list_incidents(
        self, connection_id: int, company_id: int,
    ) -> list[PurchaseChannelConnectionIncident]:

        return (
            self.db.query(PurchaseChannelConnectionIncident)
            .filter(
                PurchaseChannelConnectionIncident.connection_id == connection_id,
                PurchaseChannelConnectionIncident.company_id == company_id,
            )
            .order_by(PurchaseChannelConnectionIncident.occurred_at.desc())
            .all()
        )

    # ---------------- 자동 일시중지 ----------------

    def _maybe_pause_for_incidents(
        self, connection: PurchaseChannelConnection,
    ) -> None:

        if connection.order_paused_at is not None:
            # 이미 일시중지됐다 — 사건이 더 쌓여도 재판정·재알림하지
            # 않는다(사람이 재활성화하기 전까지는 이 상태를 그대로
            # 유지하는 것 자체가 이 기능의 목적이다).
            return

        window_days, max_count = self.get_auto_pause_setting(
            connection.company_id,
        )
        window_start = datetime.utcnow() - timedelta(days=window_days)
        count = (
            self.db.query(PurchaseChannelConnectionIncident)
            .filter(
                PurchaseChannelConnectionIncident.connection_id == connection.id,
                PurchaseChannelConnectionIncident.company_id == connection.company_id,
                PurchaseChannelConnectionIncident.occurred_at >= window_start,
            )
            .count()
        )
        if count < max_count:
            return

        connection.order_paused_at = datetime.utcnow()
        connection.order_paused_reason = (
            f"최근 {window_days}일 안에 매입처 사건 {count}건(임계값 "
            f"{max_count}건) — 발주 기능 자동 일시중지"
        )
        event = PurchaseChannelConnectionEvent(
            company_id=connection.company_id, connection_id=connection.id,
            event_type=ChannelConnectionEventType.ORDER_FUNCTION_PAUSED,
            detail=connection.order_paused_reason, triggered_by=None,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(connection)

        self._notify_auto_pause(connection, count, window_days, max_count)

    def _notify_auto_pause(
        self, connection: PurchaseChannelConnection, count: int,
        window_days: int, max_count: int,
    ) -> None:
        """best-effort — 알림 발송 실패가 사건 기록·일시중지 자체를
        되돌리면 안 된다(app/domains/purchase_task/
        channel_connection_service.py::_notify_repeated_lookup_failure
        와 동일한 패턴)."""

        try:
            from app.domains.user.model import User

            for user in (
                self.db.query(User)
                .filter(
                    User.company_id == connection.company_id,
                    User.is_active.is_(True),
                )
                .all()
            ):
                if (user.role or "").strip().upper() != "SUPER_ADMIN":
                    continue

                dispatch_operational_event(
                    self.db, "SUPPLIER_CONNECTION_ORDER_AUTO_PAUSED",
                    company_id=connection.company_id, user_id=user.id,
                    idempotency_key=(
                        f"supplier-order-auto-pause:{connection.id}:"
                        f"{connection.order_paused_at.isoformat()}"
                    ),
                    title="매입처 연결의 발주 기능이 자동 일시중지됐습니다",
                    message=(
                        f"매입처 연결 #{connection.id}({connection.account_label})"
                        f"에서 최근 {window_days}일 안에 사건이 {count}건 "
                        f"발생해(임계값 {max_count}건) 발주 기능이 자동으로 "
                        "일시중지됐습니다 — 원인을 확인한 뒤 재사용 여부를 "
                        "결정해 주세요."
                    ),
                    link_path="purchase-channel-connections",
                    entity_ref=f"purchase_channel_connection:{connection.id}",
                    reason="REPEATED_SUPPLIER_INCIDENTS",
                    entity_summary=(
                        f"매입처 연결 #{connection.id}({connection.account_label})"
                    ),
                )
        except Exception:  # noqa: BLE001 — 알림 실패가 원 업무를 되돌리면 안 된다
            pass

    def reactivate_order_function(
        self, connection_id: int, company_id: int, *, is_admin: bool,
        reactivated_by: int, confirmation_note: str,
    ) -> PurchaseChannelConnection:
        """사람이 원인을 확인하고 재사용하기로 결정했다는 근거
        (confirmation_note)를 남겨야만 풀 수 있다 — 자동 복구
        경로는 존재하지 않는다."""

        if not is_admin:
            raise ForbiddenException(
                "발주 기능 재활성화는 관리자만 가능합니다.",
            )

        connection = (
            self.db.query(PurchaseChannelConnection)
            .filter(
                PurchaseChannelConnection.id == connection_id,
                PurchaseChannelConnection.company_id == company_id,
            )
            .first()
        )
        if connection is None:
            raise NotFoundException(
                "매입처 연결을 찾을 수 없습니다 — 같은 회사 소유의 연결인지 확인하세요.",
            )
        if connection.order_paused_at is None:
            raise BadRequestException(
                "이 연결은 발주 기능이 일시중지된 상태가 아닙니다.",
            )
        if not confirmation_note or not confirmation_note.strip():
            raise BadRequestException(
                "재활성화 사유(무엇을 어떻게 확인했는지)를 입력해야 합니다.",
            )

        connection.order_paused_at = None
        connection.order_paused_reason = None
        event = PurchaseChannelConnectionEvent(
            company_id=company_id, connection_id=connection.id,
            event_type=ChannelConnectionEventType.ORDER_FUNCTION_REACTIVATED,
            detail=confirmation_note.strip(), triggered_by=reactivated_by,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(connection)
        return connection


__all__ = ["SupplierIncidentService"]
