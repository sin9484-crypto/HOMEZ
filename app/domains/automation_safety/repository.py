"""
=========================================================
Homez OS

File : app/domains/automation_safety/repository.py

V2.4 Commerce Safety Layer Repository

쓰기 메서드는 commit하지 않고 flush만 수행한다(*_no_commit). Transaction
경계(commit/rollback)는 SafetyService가 소유한다 — 하나의 evaluate() 호출
안에서 여러 쓰기를 하나의 Transaction으로 묶기 위함이다.
=========================================================
"""

from datetime import date

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.model import FunctionAutomationState


class AutomationSafetyRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # --------------------------------------------------
    # Automation Mode
    # --------------------------------------------------

    def get_current_mode_state(
        self,
    ) -> AutomationModeState | None:

        return (
            self.db.query(AutomationModeState)
            .order_by(AutomationModeState.id.desc())
            .first()
        )

    def add_mode_state_no_commit(
        self,
        state: AutomationModeState,
    ) -> AutomationModeState:

        self.db.add(state)
        self.db.flush()

        return state

    # --------------------------------------------------
    # Function Automation State (Phase 3, 회사×기능별)
    # --------------------------------------------------

    def get_current_function_mode_state(
        self,
        company_id: int,
        function_code: str,
    ) -> FunctionAutomationState | None:

        return (
            self.db.query(FunctionAutomationState)
            .filter(
                FunctionAutomationState.company_id == company_id,
                FunctionAutomationState.function_code == function_code,
            )
            .order_by(FunctionAutomationState.id.desc())
            .first()
        )

    def get_latest_function_mode_states_for_company(
        self,
        company_id: int,
    ) -> list[FunctionAutomationState]:
        """회사 안의 모든 기능코드 각각의 "가장 최근 행"만 반환한다
        (기능마다 이력이 여러 건 쌓이므로 단순 전체 조회로는 안 된다).
        기능 수가 10개 고정으로 작아 SQL 윈도우 함수 없이 Python에서
        간단히 골라낸다."""

        rows = (
            self.db.query(FunctionAutomationState)
            .filter(FunctionAutomationState.company_id == company_id)
            .order_by(FunctionAutomationState.id.desc())
            .all()
        )

        latest_by_function: dict[str, FunctionAutomationState] = {}
        for row in rows:
            if row.function_code not in latest_by_function:
                latest_by_function[row.function_code] = row

        return list(latest_by_function.values())

    def add_function_mode_state_no_commit(
        self,
        state: FunctionAutomationState,
    ) -> FunctionAutomationState:

        self.db.add(state)
        self.db.flush()

        return state

    # --------------------------------------------------
    # Emergency Stop
    # --------------------------------------------------

    def get_latest_emergency_stop(
        self,
    ) -> EmergencyStop | None:

        return (
            self.db.query(EmergencyStop)
            .order_by(EmergencyStop.id.desc())
            .first()
        )

    def add_emergency_stop_no_commit(
        self,
        stop: EmergencyStop,
    ) -> EmergencyStop:

        self.db.add(stop)
        self.db.flush()

        return stop

    # --------------------------------------------------
    # Execution Limit
    # --------------------------------------------------

    def get_active_limit(
        self,
        product_id: int | None,
    ) -> ExecutionLimit | None:

        query = self.db.query(ExecutionLimit).filter(
            ExecutionLimit.active.is_(True),
        )

        if product_id is not None:
            query = query.filter(ExecutionLimit.product_id == product_id)
        else:
            query = query.filter(ExecutionLimit.product_id.is_(None))

        return query.order_by(ExecutionLimit.id.desc()).first()

    def add_limit_no_commit(
        self,
        limit: ExecutionLimit,
    ) -> ExecutionLimit:

        self.db.add(limit)
        self.db.flush()

        return limit

    # --------------------------------------------------
    # Execution Usage (요청 단위 append-only 감사 로그)
    # --------------------------------------------------

    def get_usage_by_idempotency(
        self,
        idempotency_key: str,
    ) -> ExecutionUsage | None:

        return (
            self.db.query(ExecutionUsage)
            .filter(ExecutionUsage.idempotency_key == idempotency_key)
            .first()
        )

    def add_usage_no_commit(
        self,
        usage: ExecutionUsage,
    ) -> ExecutionUsage:

        self.db.add(usage)
        self.db.flush()

        return usage

    # --------------------------------------------------
    # Execution Period Usage (원자적 한도 카운터)
    # --------------------------------------------------

    def get_period_usage(
        self,
        scope_key: str,
        period_start: date,
    ) -> ExecutionPeriodUsage | None:

        return (
            self.db.query(ExecutionPeriodUsage)
            .filter(ExecutionPeriodUsage.scope_key == scope_key)
            .filter(ExecutionPeriodUsage.period_start == period_start)
            .first()
        )

    def ensure_period_usage_row_no_commit(
        self,
        scope_key: str,
        period_start: date,
    ) -> None:
        """
        (scope_key, period_start) 행이 없으면 0으로 생성한다.

        SQLite의 INSERT ... ON CONFLICT DO NOTHING을 사용해 동시에 여러
        요청이 같은 scope/period에 대해 최초 생성을 시도해도 정확히
        한 행만 남도록 한다(경쟁 시 예외 없이 조용히 무시됨).
        """

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(ExecutionPeriodUsage).values(
            scope_key=scope_key,
            period_start=period_start,
            consumed_funding=0,
            consumed_quantity=0,
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["scope_key", "period_start"],
        )

        self.db.execute(stmt)
        self.db.flush()

    def try_consume_period_usage(
        self,
        scope_key: str,
        period_start: date,
        funding_amount: float,
        quantity: int,
        funding_cap: float | None,
        quantity_cap: int | None,
    ) -> bool:
        """
        한도 내에서만 원자적으로 소비를 반영하는 단일 조건부 UPDATE.

        WHERE 절에 "반영 후 값이 한도 이하"라는 조건을 포함시켜, 조회와
        반영을 한 문장으로 묶는다. rowcount == 1이면 성공(반영됨),
        0이면 한도 초과(행은 변경되지 않음 — 원래 값 그대로 유지).
        """

        stmt = (
            update(ExecutionPeriodUsage)
            .where(ExecutionPeriodUsage.scope_key == scope_key)
            .where(ExecutionPeriodUsage.period_start == period_start)
        )

        if funding_cap is not None:
            stmt = stmt.where(
                ExecutionPeriodUsage.consumed_funding + funding_amount
                <= funding_cap,
            )

        if quantity_cap is not None:
            stmt = stmt.where(
                ExecutionPeriodUsage.consumed_quantity + quantity
                <= quantity_cap,
            )

        stmt = stmt.values(
            consumed_funding=ExecutionPeriodUsage.consumed_funding
            + funding_amount,
            consumed_quantity=ExecutionPeriodUsage.consumed_quantity
            + quantity,
        )

        result = self.db.execute(stmt)

        return result.rowcount == 1


__all__ = [
    "AutomationSafetyRepository",
]
