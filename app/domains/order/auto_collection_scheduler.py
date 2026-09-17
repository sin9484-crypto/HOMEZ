"""
=========================================================
Homez OS

File : app/domains/order/auto_collection_scheduler.py

2026-09-16 개인 베타 잔여 작업(Phase 6, HOMEZ_USER_OPERATION_SETTINGS.md
2-8) — "신규 주문을 5분마다 자동으로 감지한다"의 실제 판단 로직.

**이 파일은 새로운 주문 수집 방법을 만들지 않는다.** 기존
`OrderMultiChannelCollectionService.run_all()`(중복방지·커서 전진
규칙·정규화까지 전부 이미 구현·검증됨)을 그대로 호출하고, 이 파일은
그 호출을 "언제 해도 되는지"만 판단하는 얇은 오케스트레이션
계층이다:

  1) EmergencyStop 활성 — 전체 tick을 건너뛴다(회사 순회 자체를
     하지 않는다).
  2) Migration 제한 모드 — 전체 tick을 건너뛴다.
  3) 회사의 `FunctionCode.ORDER_COLLECTION` 함수모드가 AUTOMATIC이
     아니면(기본값 MANUAL 포함 PAUSED/ERROR/SEMI_AUTOMATIC 전부) 그
     회사만 건너뛴다.
  4) 이전 실행이 아직 진행 중이면(기존 `OrderCollectionCursorService.
     acquire()`가 `ConflictException`으로 이미 막아 준다 — 여기서
     다시 구현하지 않고 그 예외를 잡아 "건너뜀"으로만 기록한다) 그
     회사만 건너뛴다.
  5) 자격증명이 만료됐으면(기존 `CoupangOrderCollectionService.
     validate_connection()`이 이미 `BadRequestException`으로 막아
     준다) 그 회사만 건너뛴다 — 실패로 세지 않는다(사용자 재인증
     문제이지 시스템/Provider 장애가 아니므로).

한계(정직 공개): `run_all()`은 회사 단위로 그 회사의 모든 판매채널
연결을 한 번에 순회한다(연결별 독립 실행이 아니다). 그래서 이
스케줄러의 실행 단위도 회사 단위다 — 한 회사 안에서 연결 A가
`ConflictException`이나 자격증명 만료로 막히면, 그 뒤에 순회 예정
이던 같은 회사의 연결 B~Z는 이번 tick에서 시도되지 않고 다음 tick
(기본 5분 뒤)으로 넘어간다. 회사 자체는 서로 완전히 독립적이다(한
회사의 예외가 다른 회사의 tick을 막지 않는다).

2026-09-17 개인 베타 실데이터 검증 Phase 7A 사후 감사 — 원래
`run_all()`을 상태 인자 없이 호출해 쿠팡이 지원하는 6개 주문상태
(ACCEPT/INSTRUCT/DEPARTURE/DELIVERING/FINAL_DELIVERY/NONE_TRACKING)
를 전부 조회했다. 실제 Live 검증(승인 범위는 GET 1회였으나 실제로
6회 호출됨 — 절차 위반으로 별도 기록, `docs/audits/` 참고) 도중
드러난 구조적 문제: `createdAtFrom/To`는 **주문 생성 시각** 기준
필터라, ACCEPT가 아닌 나머지 5개 상태로 좁은 최근 구간을 조회해도
"그 시각에 생성되고 이미 그 상태까지 도달한" 주문만 잡힌다 —
정상적인 주문 처리는 며칠씩 걸리므로 5분 간격의 좁은 구간에서는
사실상 항상 빈 결과만 나온다(상태 동기화 목적을 이 필터로는 달성할
수 없다). 게다가 `CoupangOrderMaterializationService.ensure_orders()`
는 어느 상태로 수집됐든 처음 보는 channel_order_id면 무조건 신규
`Order`를 만든다 — 즉 "신규 주문 감지"라는 이 기능의 목적에는
ACCEPT 하나만 의미가 있고, 나머지 5개는 (a) 목적을 달성하지 못하며
(b) 불필요한 외부 호출 5회를 계정당 5분마다 반복해 호출 한도만
소모한다. 그래서 이 스케줄러는 `NEW_ORDER_DETECTION_STATUSES`
(ACCEPT만)로 명시적으로 좁힌다. 기존 수동 "전체 통합 수집" 버튼
(`POST /orders/collect/all`)은 여전히 6개 전부를 조회한다 — 그
버튼은 신규 감지가 아니라 사용자가 의도적으로 누르는 전체 점검
도구이므로 범위를 바꾸지 않았다. 배송 추적(FINAL_DELIVERY 등
"기존 주문의 상태 변화")은 이 스케줄러의 책임이 아니다 — 별도
저빈도 작업(예: 개별 주문 조회 기반)으로 다뤄야 하며, 이번
라운드에서는 그 별도 작업을 새로 만들지 않았다(범위 밖, 정직 공개).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.logger import logger
from app.core.migration_restricted_mode import is_restricted_mode
from app.core.windows_credential_store import CredentialStore
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.order.collection_model import OrderAutoCollectionState
from app.domains.order.multi_channel_collection_service import (
    OrderMultiChannelCollectionService,
)
from app.domains.store_connection.model import StoreConnection

CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD = 3  # purchase_task의 동일 취지 상수와 동일값
DEFAULT_INTERVAL_MINUTES = 5

# 2026-09-17 Phase 7A 사후 감사 — "신규 주문 감지"의 정의상 의미가
# 있는 상태는 ACCEPT뿐이다(모듈 docstring 참고). 자동 tick과 수동
# "지금 확인" 둘 다 이 상수만 쓴다 — 6개 전부를 조회하는 기존 수동
# "전체 통합 수집" 버튼과는 의도적으로 범위가 다르다.
NEW_ORDER_DETECTION_STATUSES = frozenset({"ACCEPT"})


class OrderCollectionTickOutcome:

    SKIPPED_EMERGENCY_STOP = "SKIPPED_EMERGENCY_STOP"
    SKIPPED_MIGRATION_RESTRICTED = "SKIPPED_MIGRATION_RESTRICTED"
    NOT_DUE = "NOT_DUE"
    SKIPPED_NOT_AUTOMATIC = "SKIPPED_NOT_AUTOMATIC"
    SKIPPED_ALREADY_RUNNING = "SKIPPED_ALREADY_RUNNING"
    SKIPPED_VALIDATION_BLOCKED = "SKIPPED_VALIDATION_BLOCKED"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"

    ALL = (
        SKIPPED_EMERGENCY_STOP, SKIPPED_MIGRATION_RESTRICTED, NOT_DUE,
        SKIPPED_NOT_AUTOMATIC, SKIPPED_ALREADY_RUNNING,
        SKIPPED_VALIDATION_BLOCKED, SUCCEEDED, PARTIAL, FAILED,
    )


@dataclass(frozen=True)
class OrderCollectionTickCompanyEntry:

    company_id: int
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class OrderCollectionTickResult:

    outcome: str  # tick 전체가 전역 게이트로 막혔으면 그 사유, 아니면"RAN"
    entries: tuple[OrderCollectionTickCompanyEntry, ...] = field(default_factory=tuple)


def get_or_create_auto_collection_state(
    db: Session, company_id: int,
) -> OrderAutoCollectionState:

    state = (
        db.query(OrderAutoCollectionState)
        .filter(OrderAutoCollectionState.company_id == company_id)
        .first()
    )
    if state is not None:
        return state

    state = OrderAutoCollectionState(
        company_id=company_id, interval_minutes=DEFAULT_INTERVAL_MINUTES,
        consecutive_failure_count=0,
    )
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


def set_interval_minutes(
    db: Session, company_id: int, minutes: int,
) -> OrderAutoCollectionState:

    if minutes < 1:
        raise BadRequestException("수집 주기는 1분 이상이어야 합니다.")

    state = get_or_create_auto_collection_state(db, company_id)
    state.interval_minutes = minutes
    db.commit()
    db.refresh(state)
    return state


def _is_due(state: OrderAutoCollectionState, now: datetime) -> bool:

    if state.last_attempted_at is None:
        return True
    return now - state.last_attempted_at >= timedelta(minutes=state.interval_minutes)


def _record_attempt_start(db: Session, state: OrderAutoCollectionState, now: datetime) -> None:

    state.last_attempted_at = now
    db.commit()


def _record_skip(
    db: Session, state: OrderAutoCollectionState, *, status: str,
    skip_reason: str,
) -> None:

    state.last_status = status
    state.last_skip_reason = skip_reason
    db.commit()


def _record_counts(state: OrderAutoCollectionState, result) -> None:
    """운영 화면의 "신규·중복·미연결·실패 주문수" 표시용 — 실제
    시도가 있었던 tick/수동 트리거에서만 호출한다(NOT_DUE/게이트
    차단 등 시도 자체가 없었던 경우는 이전 값을 그대로 둔다 — 0으로
    덮어써서 "이번에 0건이었다"고 오해하게 만들지 않는다)."""

    state.last_new_fulfillment_count = sum(e.new_fulfillment_count for e in result.entries)
    state.last_duplicate_fulfillment_count = sum(e.duplicate_fulfillment_count for e in result.entries)
    state.last_unresolved_item_count = sum(e.new_unresolved_item_count for e in result.entries)
    state.last_failed_order_count = sum(e.failed_order_count for e in result.entries)


def _notify_repeated_failure(
    db: Session, company_id: int, *, consecutive_failures: int, detail: str,
) -> None:
    """3회 연속 실패하면 주문 수집 기능만 ERROR로 낮춘다(다른
    기능에는 영향 없음) — 회사 관리자 알림 + 서버 관리자 알림 둘 다
    남긴다(10-18 두 채널 원칙)."""

    safety = SafetyService(db)

    if safety.get_function_mode(company_id, FunctionCode.ORDER_COLLECTION) == FunctionMode.ERROR:
        return  # 이미 ERROR — 중복 알림 방지(기존 가격인상 패턴과 동일)

    try:
        safety.demote_function_to_error(
            company_id, FunctionCode.ORDER_COLLECTION,
            reason=f"신규 주문 자동 감지가 {consecutive_failures}회 연속 실패해 자동 중지됨: {detail}",
        )
    except Exception:  # noqa: BLE001 — 강등 실패가 수집 결과 반환을 막지 않는다
        return

    try:
        from app.domains.notification_center.delivery_service import (
            NotificationDeliveryService,
        )
        from app.domains.user.model import User

        day_key = datetime.utcnow().strftime("%Y%m%d%H")
        for user in (
            db.query(User)
            .filter(User.company_id == company_id, User.is_active.is_(True))
            .all()
        ):
            if (user.role or "").strip().upper() != "SUPER_ADMIN":
                continue
            NotificationDeliveryService(db).dispatch(
                "FUNCTION_AUTOMATION_DEMOTED_TO_ERROR",
                company_id=company_id, user_id=user.id,
                idempotency_key=(
                    f"func-error-{FunctionCode.ORDER_COLLECTION}-{company_id}-{day_key}"
                ),
                title="신규 주문 자동 감지 기능이 오류 상태로 낮아졌습니다.",
                message=(
                    f"신규 주문 자동 감지가 {consecutive_failures}회 연속 실패해 "
                    "자동화가 중지됐습니다. 확인 후 필요하면 설정 화면에서 "
                    "다시 켜 주세요."
                ),
                link_path="order-collection",
                entity_ref=f"company:{company_id}",
                to_email=user.email,
                reason="ORDER_COLLECTION_REPEATED_FAILURE",
            )
    except Exception:  # noqa: BLE001
        pass

    try:
        from app.domains.platform_alert.hooks import notify_repeated_provider_error

        notify_repeated_provider_error(
            db, detail=f"company_id={company_id} order_collection: {detail}",
            entity_ref=f"company:{company_id}",
            idempotency_key=(
                f"order-collection-repeated-failure:{company_id}:{consecutive_failures}"
            ),
        )
    except Exception:  # noqa: BLE001
        pass


def _run_one_company(
    db: Session, credential_store: CredentialStore, company_id: int,
    *, now: datetime, provider_factory=None,
) -> OrderCollectionTickCompanyEntry:

    safety = SafetyService(db)
    state = get_or_create_auto_collection_state(db, company_id)

    if not _is_due(state, now):
        return OrderCollectionTickCompanyEntry(company_id, OrderCollectionTickOutcome.NOT_DUE)

    if not safety.is_function_automatic(company_id, FunctionCode.ORDER_COLLECTION):
        _record_skip(
            db, state, status=OrderCollectionTickOutcome.SKIPPED_NOT_AUTOMATIC,
            skip_reason="function_mode_not_automatic",
        )
        return OrderCollectionTickCompanyEntry(
            company_id, OrderCollectionTickOutcome.SKIPPED_NOT_AUTOMATIC,
        )

    _record_attempt_start(db, state, now)

    try:
        result = OrderMultiChannelCollectionService(
            db, credential_store, provider_factory=provider_factory,
        ).run_all(company_id, statuses=NEW_ORDER_DETECTION_STATUSES)
    except ConflictException:
        _record_skip(
            db, state, status=OrderCollectionTickOutcome.SKIPPED_ALREADY_RUNNING,
            skip_reason="collection_already_running",
        )
        return OrderCollectionTickCompanyEntry(
            company_id, OrderCollectionTickOutcome.SKIPPED_ALREADY_RUNNING,
        )
    except BadRequestException as exc:
        # 대부분 자격증명 만료/미연결 — 시스템 장애가 아니므로 연속
        # 실패 카운터를 올리지 않는다(사용자 재인증 문제일 뿐).
        _record_skip(
            db, state, status=OrderCollectionTickOutcome.SKIPPED_VALIDATION_BLOCKED,
            skip_reason=str(exc)[:200],
        )
        return OrderCollectionTickCompanyEntry(
            company_id, OrderCollectionTickOutcome.SKIPPED_VALIDATION_BLOCKED, str(exc)[:200],
        )
    except Exception as exc:  # noqa: BLE001 — 예상 밖 오류도 실패로 기록하고 다음 회사로 진행
        db.rollback()
        detail = f"{type(exc).__name__}: {exc}"[:200]
        state.consecutive_failure_count += 1
        state.last_error_summary = detail
        _record_skip(db, state, status=OrderCollectionTickOutcome.FAILED, skip_reason="unexpected_exception")
        if state.consecutive_failure_count >= CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD:
            _notify_repeated_failure(
                db, company_id,
                consecutive_failures=state.consecutive_failure_count, detail=detail,
            )
        logger.warning(
            "주문 자동 감지 실행 중 예상 밖 오류(다음 회사로 계속): company_id=%s error=%s",
            company_id, detail,
        )
        return OrderCollectionTickCompanyEntry(company_id, OrderCollectionTickOutcome.FAILED, detail)

    _record_counts(state, result)

    if result.total_connections == 0:
        state.last_status = OrderCollectionTickOutcome.SUCCEEDED
        state.last_succeeded_at = now
        state.consecutive_failure_count = 0
        db.commit()
        return OrderCollectionTickCompanyEntry(company_id, OrderCollectionTickOutcome.SUCCEEDED, "no_connections")

    if result.failed_runs > 0 and result.succeeded_runs == 0:
        detail = ", ".join(
            code for e in result.entries for code in e.error_codes
        )[:200] or "collection_failed"
        state.consecutive_failure_count += 1
        state.last_error_summary = detail
        state.last_status = OrderCollectionTickOutcome.FAILED
        db.commit()
        if state.consecutive_failure_count >= CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD:
            _notify_repeated_failure(
                db, company_id,
                consecutive_failures=state.consecutive_failure_count, detail=detail,
            )
        return OrderCollectionTickCompanyEntry(company_id, OrderCollectionTickOutcome.FAILED, detail)

    state.consecutive_failure_count = 0
    state.last_succeeded_at = now
    if result.failed_runs > 0:
        state.last_status = OrderCollectionTickOutcome.PARTIAL
        db.commit()
        return OrderCollectionTickCompanyEntry(company_id, OrderCollectionTickOutcome.PARTIAL)

    state.last_status = OrderCollectionTickOutcome.SUCCEEDED
    db.commit()
    return OrderCollectionTickCompanyEntry(company_id, OrderCollectionTickOutcome.SUCCEEDED)


def run_order_collection_tick(
    db: Session, credential_store: CredentialStore, *,
    now: datetime | None = None, provider_factory=None,
    is_restricted_mode_check=is_restricted_mode,
) -> OrderCollectionTickResult:
    """스케줄러가 5분(기본)마다 호출하는 진입점. 회사별 "지금 확인"
    수동 버튼(Phase 7)도 이 함수가 아니라 `_run_one_company`를 감싼
    별도의 `trigger_company_now()`를 써야 한다(그래야 수동 버튼이
    NOT_DUE로 조용히 무시되지 않는다) — 그 함수는 Phase 7에서
    추가한다.

    `is_restricted_mode_check`: 기본값은 실제 서버 프로세스 전역
    캐시(`app.core.migration_restricted_mode.is_restricted_mode`)를
    읽는다 — 이 캐시는 "지금 이 세션이 연결된 DB"가 아니라 "이
    프로세스가 실제로 서비스 중인 DATABASE_URL" 기준이라, 격리된
    테스트 DB로 이 함수를 호출해도 프로세스 전역 상태(다른 테스트가
    남긴 값, 또는 아직 한 번도 계산되지 않아 fail-closed로 True)가
    섞여 들어온다. 테스트는 이 인자에 결정적인 콜러블을 주입해야
    한다(임시 SQLite/Fake Provider 사용 규칙과 동일한 이유)."""

    now = now or datetime.utcnow()

    safety = SafetyService(db)
    if safety.is_emergency_stop_active():
        return OrderCollectionTickResult(OrderCollectionTickOutcome.SKIPPED_EMERGENCY_STOP)

    if is_restricted_mode_check():
        return OrderCollectionTickResult(OrderCollectionTickOutcome.SKIPPED_MIGRATION_RESTRICTED)

    company_ids = [
        row[0] for row in (
            db.query(StoreConnection.company_id)
            .filter(StoreConnection.marketplace_code == "COUPANG")
            .filter(StoreConnection.connection_status == "CONNECTED")
            .distinct()
            .order_by(StoreConnection.company_id.asc())
            .all()
        )
    ]

    entries = []
    for company_id in company_ids:
        entries.append(_run_one_company(
            db, credential_store, company_id, now=now, provider_factory=provider_factory,
        ))

    return OrderCollectionTickResult("RAN", tuple(entries))


def trigger_company_now(
    db: Session, credential_store: CredentialStore, company_id: int, *,
    now: datetime | None = None, provider_factory=None,
    is_restricted_mode_check=is_restricted_mode,
) -> OrderCollectionTickCompanyEntry:
    """Phase 7 "지금 확인" 수동 버튼 전용.

    자동 tick(`run_order_collection_tick`)과의 차이:
      - 주기(NOT_DUE) 판단을 건너뛴다 — 사용자가 방금 클릭했다는
        사실 자체가 "지금 해도 된다"는 의사표시다.
      - `FunctionCode.ORDER_COLLECTION` 함수모드가 AUTOMATIC이
        아니어도(MANUAL이 기본값이므로 개인 베타에서는 오히려 이
        경로가 기본 사용 경로가 된다) 막지 않는다 — 수동 확인은
        자동화 여부와 무관하게 항상 허용한다.
      - 반면 EmergencyStop과 Migration 제한 모드는 여전히 막는다 —
        전체 시스템 안전장치이므로 수동이라고 예외를 두지 않는다.
      - "이전 실행 진행 중"(스케줄 tick과의 충돌 포함)은 기존
        `OrderCollectionCursorService.acquire()`의 잠금이 그대로
        막아 준다 — 반복 클릭과 스케줄 실행이 겹치지 않는다는 요구를
        새 코드 없이 만족한다."""

    now = now or datetime.utcnow()

    safety = SafetyService(db)
    if safety.is_emergency_stop_active():
        return OrderCollectionTickCompanyEntry(
            company_id, OrderCollectionTickOutcome.SKIPPED_EMERGENCY_STOP,
        )
    if is_restricted_mode_check():
        return OrderCollectionTickCompanyEntry(
            company_id, OrderCollectionTickOutcome.SKIPPED_MIGRATION_RESTRICTED,
        )

    state = get_or_create_auto_collection_state(db, company_id)
    _record_attempt_start(db, state, now)

    try:
        result = OrderMultiChannelCollectionService(
            db, credential_store, provider_factory=provider_factory,
        ).run_all(company_id, statuses=NEW_ORDER_DETECTION_STATUSES)
    except ConflictException:
        _record_skip(
            db, state, status=OrderCollectionTickOutcome.SKIPPED_ALREADY_RUNNING,
            skip_reason="collection_already_running",
        )
        return OrderCollectionTickCompanyEntry(
            company_id, OrderCollectionTickOutcome.SKIPPED_ALREADY_RUNNING,
        )
    except BadRequestException as exc:
        _record_skip(
            db, state, status=OrderCollectionTickOutcome.SKIPPED_VALIDATION_BLOCKED,
            skip_reason=str(exc)[:200],
        )
        return OrderCollectionTickCompanyEntry(
            company_id, OrderCollectionTickOutcome.SKIPPED_VALIDATION_BLOCKED, str(exc)[:200],
        )

    _record_counts(state, result)

    if result.failed_runs > 0 and result.succeeded_runs == 0:
        detail = ", ".join(
            code for e in result.entries for code in e.error_codes
        )[:200] or "collection_failed"
        state.last_status = OrderCollectionTickOutcome.FAILED
        state.last_error_summary = detail
        db.commit()
        return OrderCollectionTickCompanyEntry(company_id, OrderCollectionTickOutcome.FAILED, detail)

    state.last_succeeded_at = now
    state.consecutive_failure_count = 0
    state.last_status = (
        OrderCollectionTickOutcome.PARTIAL if result.failed_runs > 0
        else OrderCollectionTickOutcome.SUCCEEDED
    )
    db.commit()
    return OrderCollectionTickCompanyEntry(company_id, state.last_status)


@dataclass(frozen=True)
class OrderCollectionConnectionPlan:

    store_connection_id: int
    marketplace_code: str
    status: str
    window_from: datetime
    window_to: datetime


@dataclass(frozen=True)
class OrderCollectionTriggerPlan:
    """2026-09-17 Phase 7A 사후 감사(요구사항 6, dry-run/plan) —
    "지금 확인"을 실제로 누르기 전에 정확히 무엇이 일어날지 미리
    계산한다. **이 함수는 외부 API를 전혀 호출하지 않고 DB에 아무
    것도 쓰지 않는다** — `OrderCollectionCursorService.preview_window()`
    (기존, 락도 안 잡고 쓰기도 안 하는 순수 조회)만 재사용한다.
    수동 확인창(Phase 7A 요구사항 5)과 실제 실행이 반드시 이 함수
    하나를 공유해야 화면 표시와 실행 계획이 어긋나지 않는다.

    2026-09-17 Phase 7A 사후 감사 2차(결함 1) — 이전 버전은
    `max_external_get_calls=len(plans)`로 "연결당 GET 1회"만 가정해
    페이지네이션을 반영하지 못했다(코드 주석으로 스스로 인정하고도
    계산하지 않았음). 쿠팡 주문조회는 연결×상태 1개당 최소 1회에서
    최대 `DEFAULT_MAX_PAGES`(100)회까지 GET을 호출할 수 있으므로
    (`adapters/coupang_collection.py`), 최소·최대를 모두 계산해
    숨기지 않는다."""

    company_id: int
    connections: tuple[OrderCollectionConnectionPlan, ...]
    statuses: tuple[str, ...]
    active_connection_count: int
    max_pages_per_connection: int
    min_external_get_calls: int
    max_external_get_calls: int
    retry_count: int
    will_write_order_or_purchase_task: bool
    will_submit_purchase_order_or_payment: bool
    page_limit_note: str = ""
    note: str = ""


def plan_manual_trigger(
    db: Session, company_id: int, *, now: datetime | None = None,
) -> OrderCollectionTriggerPlan:
    """`trigger_company_now()`가 실제로 하게 될 일을 미리 계산만
    한다 — 승인 지점(Phase 7B 이전의 확인창 등)에서 이 함수의 결과를
    그대로 사용자에게 보여줘야 한다."""

    from app.domains.order.adapters.coupang_collection import DEFAULT_MAX_PAGES
    from app.domains.order.collection_service import OrderCollectionCursorService

    now = now or datetime.utcnow()
    connections = (
        db.query(StoreConnection)
        .filter(StoreConnection.company_id == company_id)
        .filter(StoreConnection.marketplace_code == "COUPANG")
        .filter(StoreConnection.connection_status == "CONNECTED")
        .order_by(StoreConnection.id.asc())
        .all()
    )

    cursor_service = OrderCollectionCursorService(db)
    plans: list[OrderCollectionConnectionPlan] = []
    for connection in connections:
        for status in sorted(NEW_ORDER_DETECTION_STATUSES):
            window_from, window_to = cursor_service.preview_window(
                company_id, connection.id, status, now=now,
            )
            plans.append(OrderCollectionConnectionPlan(
                store_connection_id=connection.id,
                marketplace_code=connection.marketplace_code,
                status=status, window_from=window_from, window_to=window_to,
            ))

    return OrderCollectionTriggerPlan(
        company_id=company_id,
        connections=tuple(plans),
        statuses=tuple(sorted(NEW_ORDER_DETECTION_STATUSES)),
        active_connection_count=len(connections),
        max_pages_per_connection=DEFAULT_MAX_PAGES,
        min_external_get_calls=len(plans),  # 페이지네이션 없음(연결×상태 1개당 1회)
        max_external_get_calls=len(plans) * DEFAULT_MAX_PAGES,  # 연결×상태마다 페이지 상한까지 소진되는 최악의 경우
        retry_count=0,
        will_write_order_or_purchase_task=True,
        will_submit_purchase_order_or_payment=False,
        page_limit_note=(
            f"연결당 페이지 상한({DEFAULT_MAX_PAGES}페이지)에 도달하면 "
            "그 연결의 이번 실행은 실패로 처리되고 저장된 내용 없이 "
            "다음 실행에서 같은 구간을 재시도합니다 — 부분 성공으로 "
            "처리하지 않습니다."
        ),
        note=(
            "발주·결제·상품등록은 이 실행에서 절대 호출되지 않습니다 — "
            "신규 주문을 읽어 HOMEZ 주문/매입작업 대기열에만 반영합니다."
        ),
    )


__all__ = [
    "OrderCollectionTickOutcome",
    "OrderCollectionTickCompanyEntry",
    "OrderCollectionTickResult",
    "OrderCollectionConnectionPlan",
    "OrderCollectionTriggerPlan",
    "CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD",
    "DEFAULT_INTERVAL_MINUTES",
    "NEW_ORDER_DETECTION_STATUSES",
    "get_or_create_auto_collection_state",
    "set_interval_minutes",
    "run_order_collection_tick",
    "trigger_company_now",
    "plan_manual_trigger",
]
