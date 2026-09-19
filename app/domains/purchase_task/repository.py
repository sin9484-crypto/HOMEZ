"""
=========================================================
Homez OS

File : app/domains/purchase_task/repository.py

Gate PT-1(2026-08-22) — 모든 조회는 company_id로 스코프한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent
from app.domains.purchase_task.model import PurchaseRecord
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskBudgetReservation
from app.domains.purchase_task.model import PurchaseTaskCandidate
from app.domains.purchase_task.model import PurchaseTaskCsvImportLog
from app.domains.purchase_task.model import PurchaseTaskEmailLog
from app.domains.purchase_task.model import PurchaseTaskEmailPreference
from app.domains.purchase_task.model import PurchaseTaskPolicySetting
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo


class PurchaseTaskRepository:

    def __init__(self, db: Session):

        self.db = db

    # ---------------- PurchaseTask ----------------

    def add_task(self, task: PurchaseTask) -> PurchaseTask:

        self.db.add(task)
        self.db.flush()
        return task

    def get_task(self, task_id: int, company_id: int) -> PurchaseTask | None:

        return (
            self.db.query(PurchaseTask)
            .filter(
                PurchaseTask.id == task_id,
                PurchaseTask.company_id == company_id,
            )
            .first()
        )

    def get_task_by_idempotency(
        self, company_id: int, idempotency_key: str,
    ) -> PurchaseTask | None:

        return (
            self.db.query(PurchaseTask)
            .filter(
                PurchaseTask.company_id == company_id,
                PurchaseTask.idempotency_key == idempotency_key,
            )
            .first()
        )

    def list_tasks(
        self, company_id: int, *, status: str | None = None,
        skip: int = 0, limit: int = 100,
    ) -> list[PurchaseTask]:

        query = self.db.query(PurchaseTask).filter(
            PurchaseTask.company_id == company_id,
        )
        if status is not None:
            query = query.filter(PurchaseTask.status == status)

        return (
            query.order_by(PurchaseTask.created_at.desc())
            .offset(skip).limit(limit).all()
        )

    def list_by_source_order(
        self, company_id: int, source_order_id: int,
    ) -> list[PurchaseTask]:
        """Gate PT-2 — 주문 상세→관련 구매 작업 이동, 취소·수량 변경
        동기화에 쓰는 조회."""

        return (
            self.db.query(PurchaseTask)
            .filter(
                PurchaseTask.company_id == company_id,
                PurchaseTask.source_order_id == source_order_id,
            )
            .order_by(PurchaseTask.created_at.asc())
            .all()
        )

    def get_task_by_source_order_item(
        self, company_id: int, source_order_item_id: int,
    ) -> PurchaseTask | None:
        """품목 단위 멱등 조회 — idempotency_key 기반 생성과 별개로,
        취소·수량변경 동기화가 "이 품목에 대응하는 작업"을 직접
        찾을 때 쓴다."""

        return (
            self.db.query(PurchaseTask)
            .filter(
                PurchaseTask.company_id == company_id,
                PurchaseTask.source_order_item_id == source_order_item_id,
            )
            .order_by(PurchaseTask.created_at.desc())
            .first()
        )

    def list_tasks_with_deadline_approaching(
        self, company_id: int, *, now: datetime, within_hours: int,
    ) -> list[PurchaseTask]:
        """Gate PT-2E — 마감 임박(DEADLINE_APPROACHING) 이메일 재확인용.
        아직 예산이 점유된(=아직 끝나지 않은) 작업 중 기한이 지정
        시간 내로 다가온 것만 대상으로 한다."""

        from datetime import timedelta

        return (
            self.db.query(PurchaseTask)
            .filter(
                PurchaseTask.company_id == company_id,
                PurchaseTask.status.in_(PurchaseTaskStatus.BUDGET_HOLDING),
                PurchaseTask.purchase_deadline.isnot(None),
                PurchaseTask.purchase_deadline > now,
                PurchaseTask.purchase_deadline <= now + timedelta(hours=within_hours),
            )
            .all()
        )

    def count_open_tasks(self, company_id: int) -> int:

        return (
            self.db.query(PurchaseTask)
            .filter(
                PurchaseTask.company_id == company_id,
                PurchaseTask.status.in_(PurchaseTaskStatus.BUDGET_HOLDING),
            )
            .count()
        )

    def sum_recorded_amount_since(
        self, company_id: int, since: datetime,
    ) -> float:
        """일간/월간 지출한도(`PurchaseTaskPolicyService`) 집계 — 두
        발주 트랙을 모두 포함한다.

        - 수동(브라우저 구매) 트랙: `PurchaseRecord.actual_amount`
          (사람이 `record_purchase()`로 직접 입력한 확정 금액,
          created_at 기준 — 기존 동작 그대로 보존).
        - 온채널 API 발주 트랙: `record_purchase()`를 타지 않아
          `PurchaseRecord`가 생기지 않는다(그 모델 자체가 "사람이
          직접 입력"을 전제하므로 자동 생성하지 않기로 함, 2026-09-19
          항목 3). 대신 `PurchaseTaskBudgetReservation`의
          `CONFIRMED_LIKE`(=CONFIRMED 또는 CONFIRMED_PENDING_
          VERIFICATION) 상태를 `confirmed_at` 기준으로 더한다.
          PENDING_VERIFICATION은 발주 성공 직후(송장조회
          이전) 승인 스냅샷 금액으로 잠정 확정한 상태다
          (`confirm_onchannel_reservation_provisionally()`, Stage 1) —
          송장조회 실행 여부와 무관하게 발주 성공 즉시 한도를
          보호하기 위함이다. 실 API 조회가 성공하면 CONFIRMED(최종)
          로 승격된다(`_reconcile_onchannel_order_reservation()`,
          Stage 2). 둘 다 "돈이 이미 나갔다"는 사실은 같으므로 둘
          다 지출한도에 포함한다.

        이중계산 방지 — `record_purchase()`(수동 트랙)만 CONFIRMED를
        만들고, 온채널 트랙의 두 확정 메서드만 CONFIRMED_LIKE의 나머지
        값들을 만든다(코드 전수 확인, 그 외 없음). 전자는 항상 같은
        트랜잭션에서 `PurchaseRecord`도 함께 만든다. 따라서 "이미
        `PurchaseRecord`가 있는 purchase_task_id"의 CONFIRMED_LIKE
        예약은 수동 트랙 몫이므로 예약 합계에서 제외한다 — 같은
        지출을 두 번 세지 않는다."""

        from app.domains.purchase_task.constants import BudgetReservationStatus

        record_rows = (
            self.db.query(PurchaseRecord.actual_amount)
            .filter(
                PurchaseRecord.company_id == company_id,
                PurchaseRecord.created_at >= since,
            )
            .all()
        )
        record_total = sum(float(r[0]) for r in record_rows)

        recorded_task_ids = self.db.query(PurchaseRecord.purchase_task_id).filter(
            PurchaseRecord.company_id == company_id,
        )
        reservation_rows = (
            self.db.query(PurchaseTaskBudgetReservation.amount)
            .filter(
                PurchaseTaskBudgetReservation.company_id == company_id,
                PurchaseTaskBudgetReservation.status.in_(
                    BudgetReservationStatus.CONFIRMED_LIKE,
                ),
                PurchaseTaskBudgetReservation.confirmed_at >= since,
                PurchaseTaskBudgetReservation.purchase_task_id.notin_(recorded_task_ids),
            )
            .all()
        )
        reservation_total = sum(float(r[0]) for r in reservation_rows)

        return record_total + reservation_total

    def sum_quantity_for_product_since(
        self, company_id: int, gtin: str | None, model_name: str | None,
        since: datetime,
    ) -> int:
        """상품별 최대 구매수량 판정 — GTIN이 있으면 GTIN, 없으면
        모델명으로 동일 상품을 식별한다(product_matching과 동일하게
        GTIN을 최우선 식별자로 취급)."""

        query = self.db.query(PurchaseTask.quantity).filter(
            PurchaseTask.company_id == company_id,
            PurchaseTask.created_at >= since,
            PurchaseTask.status.in_(PurchaseTaskStatus.BUDGET_HOLDING),
        )
        if gtin:
            query = query.filter(PurchaseTask.gtin == gtin)
        elif model_name:
            query = query.filter(PurchaseTask.model_name == model_name)
        else:
            return 0

        return sum(int(r[0]) for r in query.all())

    # ---------------- PurchaseTaskCandidate ----------------

    def add_candidate(
        self, candidate: PurchaseTaskCandidate,
    ) -> PurchaseTaskCandidate:

        self.db.add(candidate)
        self.db.flush()
        return candidate

    def get_candidate(
        self, candidate_id: int, company_id: int,
    ) -> PurchaseTaskCandidate | None:

        return (
            self.db.query(PurchaseTaskCandidate)
            .filter(
                PurchaseTaskCandidate.id == candidate_id,
                PurchaseTaskCandidate.company_id == company_id,
            )
            .first()
        )

    def get_candidate_by_url(
        self, purchase_task_id: int, product_url: str,
    ) -> PurchaseTaskCandidate | None:

        return (
            self.db.query(PurchaseTaskCandidate)
            .filter(
                PurchaseTaskCandidate.purchase_task_id == purchase_task_id,
                PurchaseTaskCandidate.product_url == product_url,
            )
            .first()
        )

    def list_candidates(
        self, purchase_task_id: int, company_id: int,
    ) -> list[PurchaseTaskCandidate]:

        return (
            self.db.query(PurchaseTaskCandidate)
            .filter(
                PurchaseTaskCandidate.purchase_task_id == purchase_task_id,
                PurchaseTaskCandidate.company_id == company_id,
            )
            .order_by(PurchaseTaskCandidate.created_at.asc())
            .all()
        )

    # ---------------- Budget Reservation ----------------

    def add_reservation(
        self, reservation: PurchaseTaskBudgetReservation,
    ) -> PurchaseTaskBudgetReservation:

        self.db.add(reservation)
        self.db.flush()
        return reservation

    def get_reservation(
        self, reservation_id: int, company_id: int,
    ) -> PurchaseTaskBudgetReservation | None:

        return (
            self.db.query(PurchaseTaskBudgetReservation)
            .filter(
                PurchaseTaskBudgetReservation.id == reservation_id,
                PurchaseTaskBudgetReservation.company_id == company_id,
            )
            .first()
        )

    def list_expiring_active_reservations(
        self, before: datetime,
    ) -> list[PurchaseTaskBudgetReservation]:
        """만료 임박/만료된 활성 예약 전체(회사 무관 — 배치성 점검용).
        서비스가 각 건을 개별적으로 재검증한다."""

        from app.domains.purchase_task.constants import BudgetReservationStatus

        return (
            self.db.query(PurchaseTaskBudgetReservation)
            .filter(
                PurchaseTaskBudgetReservation.status.in_(
                    BudgetReservationStatus.ACTIVE,
                ),
                PurchaseTaskBudgetReservation.expires_at <= before,
            )
            .all()
        )

    # ---------------- PurchaseRecord ----------------

    def add_purchase_record(self, record: PurchaseRecord) -> PurchaseRecord:

        self.db.add(record)
        self.db.flush()
        return record

    def get_purchase_record_by_order_number(
        self, company_id: int, shopping_mall_code: str,
        external_order_number: str,
    ) -> PurchaseRecord | None:

        return (
            self.db.query(PurchaseRecord)
            .filter(
                PurchaseRecord.company_id == company_id,
                PurchaseRecord.shopping_mall_code == shopping_mall_code,
                PurchaseRecord.external_order_number == external_order_number,
            )
            .first()
        )

    def get_purchase_record_by_idempotency(
        self, company_id: int, idempotency_key: str,
    ) -> PurchaseRecord | None:

        return (
            self.db.query(PurchaseRecord)
            .filter(
                PurchaseRecord.company_id == company_id,
                PurchaseRecord.idempotency_key == idempotency_key,
            )
            .first()
        )

    def get_purchase_record_for_task(
        self, purchase_task_id: int, company_id: int,
    ) -> PurchaseRecord | None:

        return (
            self.db.query(PurchaseRecord)
            .filter(
                PurchaseRecord.purchase_task_id == purchase_task_id,
                PurchaseRecord.company_id == company_id,
            )
            .order_by(PurchaseRecord.created_at.desc())
            .first()
        )

    # ---------------- Tracking ----------------

    def get_tracking(
        self, purchase_task_id: int, company_id: int,
    ) -> PurchaseTaskTrackingInfo | None:

        return (
            self.db.query(PurchaseTaskTrackingInfo)
            .filter(
                PurchaseTaskTrackingInfo.purchase_task_id == purchase_task_id,
                PurchaseTaskTrackingInfo.company_id == company_id,
            )
            .first()
        )

    def add_tracking(
        self, tracking: PurchaseTaskTrackingInfo,
    ) -> PurchaseTaskTrackingInfo:

        self.db.add(tracking)
        self.db.flush()
        return tracking

    # ---------------- Email preference/log ----------------

    def get_email_preference(
        self, company_id: int, user_id: int,
    ) -> PurchaseTaskEmailPreference | None:

        return (
            self.db.query(PurchaseTaskEmailPreference)
            .filter(
                PurchaseTaskEmailPreference.company_id == company_id,
                PurchaseTaskEmailPreference.user_id == user_id,
            )
            .first()
        )

    def add_email_preference(
        self, pref: PurchaseTaskEmailPreference,
    ) -> PurchaseTaskEmailPreference:

        self.db.add(pref)
        self.db.flush()
        return pref

    def get_email_log_by_idempotency(
        self, company_id: int, idempotency_key: str,
    ) -> PurchaseTaskEmailLog | None:

        return (
            self.db.query(PurchaseTaskEmailLog)
            .filter(
                PurchaseTaskEmailLog.company_id == company_id,
                PurchaseTaskEmailLog.idempotency_key == idempotency_key,
            )
            .first()
        )

    def add_email_log(self, log: PurchaseTaskEmailLog) -> PurchaseTaskEmailLog:

        self.db.add(log)
        self.db.flush()
        return log

    def add_email_log_row(
        self, *, company_id: int, user_id: int, purchase_task_id: int | None,
        event_type: str, locale: str, status: str, idempotency_key: str,
        attempt_count: int, error_detail: str | None = None,
        sent_at=None,
    ) -> PurchaseTaskEmailLog:

        return self.add_email_log(PurchaseTaskEmailLog(
            company_id=company_id, user_id=user_id,
            purchase_task_id=purchase_task_id, event_type=event_type,
            locale=locale, status=status, idempotency_key=idempotency_key,
            attempt_count=attempt_count, error_detail=error_detail,
            sent_at=sent_at,
        ))

    # ---------------- 이메일 Provider 설정 계약(Gate PT-2E) ----------------

    def get_email_provider_setting(
        self, company_id: int,
    ) -> "PurchaseTaskEmailProviderSetting | None":

        from app.domains.purchase_task.model import (
            PurchaseTaskEmailProviderSetting,
        )
        return (
            self.db.query(PurchaseTaskEmailProviderSetting)
            .filter(PurchaseTaskEmailProviderSetting.company_id == company_id)
            .first()
        )

    def add_email_provider_setting(
        self, setting: "PurchaseTaskEmailProviderSetting",
    ) -> "PurchaseTaskEmailProviderSetting":

        self.db.add(setting)
        self.db.flush()
        return setting

    # ---------------- Policy ----------------

    def get_policy_setting(
        self, company_id: int,
    ) -> PurchaseTaskPolicySetting | None:

        return (
            self.db.query(PurchaseTaskPolicySetting)
            .filter(PurchaseTaskPolicySetting.company_id == company_id)
            .first()
        )

    def add_policy_setting(
        self, setting: PurchaseTaskPolicySetting,
    ) -> PurchaseTaskPolicySetting:

        self.db.add(setting)
        self.db.flush()
        return setting

    # ---------------- CSV import log ----------------

    def add_csv_import_log(
        self, log: PurchaseTaskCsvImportLog,
    ) -> PurchaseTaskCsvImportLog:

        self.db.add(log)
        self.db.flush()
        return log

    # ---------------- Channel connection (Gate PT-3, item 7) ----------------

    def add_connection(
        self, connection: PurchaseChannelConnection,
    ) -> PurchaseChannelConnection:

        self.db.add(connection)
        self.db.flush()
        return connection

    def get_connection(
        self, connection_id: int, company_id: int,
    ) -> PurchaseChannelConnection | None:
        """항상 company_id로 스코프한다 — 다른 회사의 연결 ID를 넘겨도
        절대 반환하지 않는다(회사 간 격리)."""

        return (
            self.db.query(PurchaseChannelConnection)
            .filter(
                PurchaseChannelConnection.id == connection_id,
                PurchaseChannelConnection.company_id == company_id,
            )
            .first()
        )

    def get_connection_by_idempotency(
        self, company_id: int, idempotency_key: str,
    ) -> PurchaseChannelConnection | None:

        return (
            self.db.query(PurchaseChannelConnection)
            .filter(
                PurchaseChannelConnection.company_id == company_id,
                PurchaseChannelConnection.idempotency_key == idempotency_key,
            )
            .first()
        )

    def list_connections(
        self, company_id: int, *, mall_code: str | None = None,
        include_inactive: bool = False,
    ) -> list[PurchaseChannelConnection]:

        query = self.db.query(PurchaseChannelConnection).filter(
            PurchaseChannelConnection.company_id == company_id,
        )
        if mall_code is not None:
            query = query.filter(PurchaseChannelConnection.mall_code == mall_code)
        if not include_inactive:
            query = query.filter(PurchaseChannelConnection.is_active.is_(True))
        return query.order_by(PurchaseChannelConnection.id.asc()).all()

    def add_connection_event(
        self, event: PurchaseChannelConnectionEvent,
    ) -> PurchaseChannelConnectionEvent:

        self.db.add(event)
        self.db.flush()
        return event


__all__ = ["PurchaseTaskRepository"]
