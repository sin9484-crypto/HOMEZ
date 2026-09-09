"""
=========================================================
Homez OS

File : app/domains/automation_safety/model.py

V2.4 Commerce Safety Layer
Emergency Stop / Automation Mode / Execution Limit / Execution Usage

이 Domain은 Funding/Order/Purchase Model을 직접 참조하지 않는다.
product_id 등은 전부 논리 참조(FK 없음)이며, 실제 실행 여부를 결정하지
않고 ALLOW/REQUIRE_APPROVAL/DENY 판단만 내린다.
=========================================================
"""

from datetime import date
from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class AutomationModeState(Base):
    """
    현재 자동화 모드.

    append-only: 모드를 바꿀 때마다 새 행을 추가하고, 가장 최근 행
    (id 최대)이 현재 상태를 나타낸다. 이렇게 하면 모드 변경 이력 자체가
    감사 기록이 된다.
    """

    __tablename__ = "automation_mode_states"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # RECOMMEND_ONLY / OPERATOR_APPROVAL / LIMITED_AUTOMATION / DISABLED
    mode: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # 논리 참조 (users.id)
    set_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    set_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class EmergencyStop(Base):
    """
    비상 정지 이력 (append-only).

    활성화/해제 각각 새 행으로 남기며, 가장 최근 행(id 최대)의
    is_active가 현재 상태를 나타낸다.
    """

    __tablename__ = "emergency_stops"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        index=True,
    )

    reason: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # 논리 참조 (users.id)
    set_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    set_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    # 논리 참조 (users.id) — 해제한 사람
    cleared_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    audit_ref: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )


class ExecutionLimit(Base):
    """
    자금·수량 실행 한도.

    product_id가 None이면 전역(GLOBAL) 한도, 값이 있으면 상품별 한도.
    같은 scope(전역 또는 특정 product_id)에서 활성(active=True) 상태인
    가장 최근 행이 현재 적용되는 한도다.
    """

    __tablename__ = "execution_limits"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (products.id); None이면 전역 한도
    product_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    daily_funding_limit: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    per_product_funding_limit: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    daily_quantity_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    per_product_quantity_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="KRW",
    )

    # 한도 재설정 기준 기간. MVP: "DAILY" 고정(자연일, Asia/Seoul KST 자정 기준).
    period: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="DAILY",
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ExecutionUsage(Base):
    """
    허용(ALLOW)된 실행 요청의 자금·수량 소비 기록 (append-only 감사 로그).

    한도 판단 자체는 ExecutionPeriodUsage의 원자적 조건부 UPDATE로
    수행하며, 이 테이블은 "어떤 요청이 언제 무엇을 소비했는지"의 요청
    단위 감사 추적과 idempotency_key 기반 중복 요청 감지 전용이다.
    idempotency_key UNIQUE 제약으로 동일 요청의 중복 반영을 DB 레벨에서
    차단한다.
    """

    __tablename__ = "execution_usages"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_execution_usages_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (products.id)
    product_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    funding_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class ExecutionPeriodUsage(Base):
    """
    scope(전역 "GLOBAL" 또는 "PRODUCT:{product_id}") × period_start(Asia/Seoul
    자정 기준 KST 날짜) 단위의 누적 소비 카운터.

    ExecutionUsage(요청 단위 append-only 감사 로그)와 별도로, 한도 판단을
    "하나의 원자적 조건부 UPDATE"로 처리하기 위한 집계 테이블이다.
    SQLite는 전체 DB에 대해 한 번에 하나의 writer만 허용하므로, 단일
    `UPDATE ... WHERE consumed + :amount <= :limit` 문은 조회(check)와
    반영(consume)이 분리되지 않는 원자적 연산이 되어 동시 요청이 한도를
    같이 통과하는 경쟁을 막는다(별도의 SELECT 후 INSERT 방식은 경쟁에
    취약해 사용하지 않는다).
    """

    __tablename__ = "execution_period_usages"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "period_start",
            name="uq_execution_period_usage_scope_period",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    scope_key: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        index=True,
    )

    # Asia/Seoul(KST, UTC+9 고정) 자정 기준의 날짜. datetime이 아닌 순수
    # 날짜값이므로 별도 UTC 변환 없이 그대로 저장·비교한다 — "오늘"을
    # KST 기준으로 계산하는 시점(SafetyService._period_start_date)에서만
    # KST 변환이 필요하고, 이후 DB 비교는 날짜값 동일성 비교로 충분하다.
    period_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    consumed_funding: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    consumed_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "AutomationModeState",
    "EmergencyStop",
    "ExecutionLimit",
    "ExecutionUsage",
    "ExecutionPeriodUsage",
]
