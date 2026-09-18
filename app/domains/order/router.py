"""
=========================================================
Homez OS

File : app/domains/order/router.py

Order Router — V7 Gate 4(2026-08-15) 처음부터 재설계.

전 엔드포인트가 admin_guard + current_user.company_id만 사용한다
(요청 바디로 company_id를 받지 않는다 — Gate 1/2/3에서 반복 발견된
크로스테넌트 결함 클래스를 처음부터 차단, Inventory Gate 3 router와
동일 컨벤션).
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.exceptions import UnauthorizedException
from app.core.permission_check import require_permission
from app.core.guard import admin_guard
from app.core.recent_auth import consume_recent_auth_token
from app.core.audit_db import write_audit_log
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.automation_safety.service import SafetyService
from app.domains.order.exception_analysis_service import (
    OrderExceptionAnalysisService,
)
from app.domains.order.schema import OrderCancelRequest
from app.domains.order.schema import CoupangOrderCollectionRequest
from app.domains.order.schema import CoupangOrderCollectionRunResponse
from app.domains.order.schema import CoupangOrderCollectionPreviewResponse
from app.domains.order.schema import OrderChannelCollectRequest
from app.domains.order.schema import OrderChannelSyncResult
from app.domains.order.schema import OrderCollectResult
from app.domains.order.schema import OrderExceptionAnalysisResult
from app.domains.order.schema import OrderExceptionReviewActionCreate
from app.domains.order.schema import OrderExceptionReviewActionResponse
from app.domains.order.schema import OrderExceptionResponse
from app.domains.order.schema import OrderIngestionEventResponse
from app.domains.order.schema import OrderCollectionPositionResponse
from app.domains.order.schema import OrderCollectionOpsStatusResponse
from app.domains.order.schema import OrderCollectionOpsTriggerResponse
from app.domains.order.schema import OrderCollectionOpsIntervalUpdateRequest
from app.domains.order.schema import OrderCollectionOpsResumeResponse
from app.domains.order.schema import OrderCollectionOpsPlanResponse
from app.domains.order.schema import OrderCollectionPlanConnectionResponse
from app.domains.order.schema import OrderCollectionTestBudgetPlanResponse
from app.domains.order.schema import OrderCollectionTestBudgetTriggerRequest
from app.domains.order.schema import OrderCollectionTestBudgetTriggerResponse
from app.domains.order.schema import OrderItemResponse
from app.domains.order.schema import OrderResponse
from app.domains.order.schema import OrderSensitiveDetailResponse
from app.domains.order.schema import UnresolvedOrderItemResponse
from app.domains.order.schema import UnresolvedOrderItemResolveRequest
from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.order.collection_service import OrderCollectionCursorService
from app.domains.order.collection_persistence import CoupangOrderCollectionPersistence
from app.domains.order.coupang_collection_service import CoupangOrderCollectionService
from app.domains.order.multi_channel_collection_service import OrderMultiChannelCollectionService
from app.domains.order.schema import MultiChannelCollectionEntryResponse
from app.domains.order.schema import MultiChannelCollectionRunResponse
from app.domains.order.adapters.coupang_collection import CoupangOrderCollectionProvider
from app.domains.order.order_materialization import CoupangOrderMaterializationService
from app.domains.order.schema import OrderStatusEventResponse
from app.domains.order.service import OrderService
from app.domains.user.model import User

router = APIRouter(
    prefix="/orders",
    tags=["Order"],
)


def get_order_credential_store() -> CredentialStore:
    return WindowsCredentialStore()


def get_coupang_order_provider_factory():
    """FastAPI dependency override seam for isolated tests; production is real."""
    return CoupangOrderCollectionProvider


# --------------------------------------------------
# 2026-09-16 개인 베타 잔여 작업(Phase 7, HOMEZ_USER_OPERATION_SETTINGS.md
# 2-8 운영 화면) — 회사 단위 자동 주문 감지 운영 상태·수동 확인·
# 주기 변경·일시중지/재개. 개인정보(구매자/수취인)를 전혀 다루지
# 않는다(집계값만).
#
# 이미 존재하는 `POST /orders/collect/all`(위쪽)은 이 Phase 이전에
# 만들어진 별개의 수동 전체수집 버튼이다 — `auto_collection_
# scheduler`의 5개 게이트(EmergencyStop/Migration제한모드/자동화
# 여부/중복실행방지/자격증명만료)를 거치지 않는다. 이 Phase는 그
# 엔드포인트를 건드리지 않는다(기존 호출부 보호) — 대신 게이트를
# 전부 거치는 새 엔드포인트(`/collection-ops/trigger`)를 추가해
# 운영 화면 전용으로 쓴다.
# --------------------------------------------------

@router.get(
    "/collection-ops/status",
    response_model=OrderCollectionOpsStatusResponse,
)
def get_collection_ops_status(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    from datetime import timedelta

    from app.domains.automation_safety.constants import FunctionCode
    from app.domains.automation_safety.service import SafetyService
    from app.domains.order.auto_collection_scheduler import (
        get_or_create_auto_collection_state,
    )

    company_id = current_user.company_id
    state = get_or_create_auto_collection_state(db, company_id)
    db.commit()
    function_mode = SafetyService(db).get_function_mode(
        company_id, FunctionCode.ORDER_COLLECTION,
    )

    next_due_at = None
    if state.last_attempted_at is not None:
        next_due_at = state.last_attempted_at + timedelta(minutes=state.interval_minutes)

    return OrderCollectionOpsStatusResponse(
        company_id=company_id, function_mode=function_mode,
        interval_minutes=state.interval_minutes,
        last_attempted_at=state.last_attempted_at,
        last_succeeded_at=state.last_succeeded_at,
        next_due_at=next_due_at,
        last_status=state.last_status, last_skip_reason=state.last_skip_reason,
        last_error_summary=state.last_error_summary,
        consecutive_failure_count=state.consecutive_failure_count,
        last_new_fulfillment_count=state.last_new_fulfillment_count,
        last_duplicate_fulfillment_count=state.last_duplicate_fulfillment_count,
        last_unresolved_item_count=state.last_unresolved_item_count,
        last_failed_order_count=state.last_failed_order_count,
    )


@router.get(
    "/collection-ops/plan",
    response_model=OrderCollectionOpsPlanResponse,
)
def get_collection_ops_plan(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """2026-09-17 Phase 7A 사후 감사(요구사항 5/6) — "지금 확인"을
    누르기 전 확인창이 이 계획을 그대로 보여준다. 외부 호출·DB
    쓰기가 전혀 없다(`plan_manual_trigger()` 자체가 순수 조회)."""

    from app.domains.order.auto_collection_scheduler import plan_manual_trigger

    plan = plan_manual_trigger(db, current_user.company_id)

    return OrderCollectionOpsPlanResponse(
        company_id=plan.company_id,
        connections=[
            OrderCollectionPlanConnectionResponse(
                store_connection_id=c.store_connection_id,
                marketplace_code=c.marketplace_code,
                status=c.status, window_from=c.window_from, window_to=c.window_to,
            )
            for c in plan.connections
        ],
        statuses=list(plan.statuses),
        active_connection_count=plan.active_connection_count,
        max_pages_per_connection=plan.max_pages_per_connection,
        min_external_get_calls=plan.min_external_get_calls,
        max_external_get_calls=plan.max_external_get_calls,
        retry_count=plan.retry_count,
        will_write_order_or_purchase_task=plan.will_write_order_or_purchase_task,
        will_submit_purchase_order_or_payment=plan.will_submit_purchase_order_or_payment,
        page_limit_note=plan.page_limit_note,
        note=plan.note,
    )


@router.post(
    "/collection-ops/trigger",
    response_model=OrderCollectionOpsTriggerResponse,
)
def trigger_collection_ops_now(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_order_credential_store),
    provider_factory=Depends(get_coupang_order_provider_factory),
):
    """"지금 확인" 수동 버튼. 반복 클릭이나 마침 진행 중인 자동
    tick과 겹쳐도 기존 `OrderCollectionCursorService.acquire()` 잠금이
    막아 준다(새 락 로직을 추가하지 않았다) — 겹치면 예외 없이
    SKIPPED_ALREADY_RUNNING으로 돌아온다."""

    from app.domains.order.auto_collection_scheduler import trigger_company_now

    entry = trigger_company_now(
        db, credential_store, current_user.company_id,
        provider_factory=provider_factory,
    )

    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id,
        action="ORDER_COLLECTION_OPS_MANUAL_TRIGGER", entity="company",
        entity_id=str(current_user.company_id),
        description=f"주문 자동 감지 수동 확인: outcome={entry.outcome}",
    )
    db.commit()

    return OrderCollectionOpsTriggerResponse(
        company_id=entry.company_id, outcome=entry.outcome, detail=entry.detail,
    )


@router.get(
    "/collection-ops/test-budget-plan",
    response_model=OrderCollectionTestBudgetPlanResponse,
)
def get_collection_ops_test_budget_plan(
    store_connection_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """2026-09-18 Phase 7B 실제 테스트 주문 검증 — 연결 1개·ACCEPT
    고정·페이지 1장 상한(외부 GET 최대 1회) 시험 전용 실행계획.
    외부 호출·DB 쓰기 없음."""

    from app.domains.order.auto_collection_scheduler import plan_test_budget_run

    plan = plan_test_budget_run(db, current_user.company_id, store_connection_id)
    return OrderCollectionTestBudgetPlanResponse(
        company_id=plan.company_id,
        store_connection_id=plan.store_connection_id,
        channel_status=plan.channel_status,
        window_from=plan.window_from, window_to=plan.window_to,
        max_pages=plan.max_pages,
        max_external_get_calls=plan.max_external_get_calls,
        retry_count=plan.retry_count,
        will_write_order_or_purchase_task=plan.will_write_order_or_purchase_task,
        will_submit_purchase_order_or_payment=plan.will_submit_purchase_order_or_payment,
        note=plan.note,
    )


@router.post(
    "/collection-ops/test-budget-trigger",
    response_model=OrderCollectionTestBudgetTriggerResponse,
)
def trigger_collection_ops_test_budget(
    data: OrderCollectionTestBudgetTriggerRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_order_credential_store),
    provider_factory=Depends(get_coupang_order_provider_factory),
):
    """2026-09-18 Phase 7B 실제 테스트 주문 검증 — 연결 1개·ACCEPT
    고정·페이지 1장 상한을 서버에서 강제한다(요청 바디에 상태나
    페이지 수를 넓힐 수 있는 필드 자체가 없다 — 스키마가 이미
    거부한다). `window_from`/`window_to`를 둘 다 주면(같은 값을
    두 번째 호출에 그대로 되돌려주는 방식) 동일 주문 중복 재조회
    시험이 되고, 둘 다 생략하면 최초 수집이 된다."""

    from app.domains.order.auto_collection_scheduler import run_test_budget_collection

    if (data.window_from is None) != (data.window_to is None):
        raise BadRequestException("window_from/window_to는 둘 다 지정하거나 둘 다 생략해야 합니다.")
    window_override = (
        (data.window_from, data.window_to)
        if data.window_from is not None and data.window_to is not None
        else None
    )

    result = run_test_budget_collection(
        db, credential_store, current_user.company_id, data.store_connection_id,
        actor_user_id=current_user.id, provider_factory=provider_factory,
        window_override=window_override,
    )

    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id,
        action="ORDER_COLLECTION_TEST_BUDGET_RUN", entity="store_connection",
        entity_id=str(data.store_connection_id),
        description=(
            f"주문수집 시험 실행(최대 GET 1회): outcome={result.outcome}, "
            f"received={result.received_order_count}, "
            f"new={result.new_fulfillment_count}, "
            f"duplicate={result.duplicate_fulfillment_count}, "
            f"recovery_review={result.recovery_review_count}, "
            f"failed={result.failed_order_count}"
        ),
    )
    db.commit()

    return OrderCollectionTestBudgetTriggerResponse(
        outcome=result.outcome,
        store_connection_id=result.store_connection_id,
        channel_status=result.channel_status,
        max_pages=result.max_pages,
        window_from=result.window_from, window_to=result.window_to,
        run_status=result.run_status,
        received_order_count=result.received_order_count,
        new_fulfillment_count=result.new_fulfillment_count,
        duplicate_fulfillment_count=result.duplicate_fulfillment_count,
        new_unresolved_item_count=result.new_unresolved_item_count,
        recovery_review_count=result.recovery_review_count,
        failed_order_count=result.failed_order_count,
        error_codes=list(result.error_codes),
        skip_reason=result.skip_reason,
    )


@router.post(
    "/collection-ops/interval",
    response_model=OrderCollectionOpsStatusResponse,
)
def update_collection_ops_interval(
    data: OrderCollectionOpsIntervalUpdateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    from app.domains.automation_safety.constants import FunctionCode
    from app.domains.automation_safety.service import SafetyService
    from app.domains.order.auto_collection_scheduler import set_interval_minutes

    company_id = current_user.company_id
    state = set_interval_minutes(db, company_id, data.interval_minutes)

    write_audit_log(
        db, user_id=current_user.id, company_id=company_id,
        action="ORDER_COLLECTION_OPS_INTERVAL_CHANGED", entity="company",
        entity_id=str(company_id),
        description=f"주문 자동 감지 주기 변경: {data.interval_minutes}분",
    )
    db.commit()

    return get_collection_ops_status(current_user=current_user, db=db)


@router.post(
    "/collection-ops/pause",
    response_model=OrderCollectionOpsResumeResponse,
)
def pause_collection_ops(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """일시중지(자동화를 낮추는 방향)는 재인증이 필요하지 않다 —
    더 안전한 방향으로의 전환이기 때문이다(재개만 재인증 대상)."""

    from app.domains.automation_safety.constants import FunctionCode
    from app.domains.automation_safety.constants import FunctionMode
    from app.domains.automation_safety.service import SafetyService

    company_id = current_user.company_id
    SafetyService(db).set_function_mode(
        company_id, FunctionCode.ORDER_COLLECTION, FunctionMode.PAUSED,
        set_by=current_user.id, is_admin=True, reason="운영 화면에서 수동 일시중지",
    )
    db.commit()

    return OrderCollectionOpsResumeResponse(
        company_id=company_id, function_mode=FunctionMode.PAUSED,
    )


@router.post(
    "/collection-ops/resume",
    response_model=OrderCollectionOpsResumeResponse,
)
def resume_collection_ops(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """자동 감지를 켜는(재개하는) 방향은 재인증이 필요하다
    (HOMEZ_USER_OPERATION_SETTINGS.md 2-8 운영 화면 요구사항 —
    "중지·재개는 재인증 필요"). 기존 배송정보 열람(`GET /orders/
    {order_id}/sensitive-detail`)과 동일한 `consume_recent_auth_token`
    메커니즘을 재사용한다 — 새 재인증 방식을 만들지 않았다.

    정직한 공개: 범용 기능별 자동화 모드 화면(`POST /console/api/
    function-modes/{function_code}`)으로도 같은 회사의 ORDER_COLLECTION
    을 AUTOMATIC으로 바꿀 수 있고, 그 경로는 재인증을 요구하지
    않는다(이 Phase 이전부터 있던 범용 화면이며 10개 기능 전체가
    공유한다 — 이번 범위에서 바꾸지 않았다). 이 전용 엔드포인트는
    "운영 화면에서 재개할 때"의 안전장치이지, 유일한 경로를 막는
    것은 아니다."""

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise UnauthorizedException(
            "ORDER_COLLECTION_RESUME_RECENT_AUTH_REQUIRED: 주문 자동 감지를 "
            "다시 켜려면 현재 비밀번호를 다시 확인해야 합니다.",
        )

    from app.domains.automation_safety.constants import FunctionCode
    from app.domains.automation_safety.constants import FunctionMode
    from app.domains.automation_safety.service import SafetyService

    company_id = current_user.company_id
    SafetyService(db).set_function_mode(
        company_id, FunctionCode.ORDER_COLLECTION, FunctionMode.AUTOMATIC,
        set_by=current_user.id, is_admin=True, reason="운영 화면에서 재인증 후 재개",
    )
    db.commit()

    write_audit_log(
        db, user_id=current_user.id, company_id=company_id,
        action="ORDER_COLLECTION_OPS_RESUMED", entity="company",
        entity_id=str(company_id),
        description="주문 자동 감지 재개(재인증 완료)",
    )
    db.commit()

    return OrderCollectionOpsResumeResponse(
        company_id=company_id, function_mode=FunctionMode.AUTOMATIC,
    )


# --------------------------------------------------
# 채널 주문 수집(요구사항 1/2/3/6/7)
# --------------------------------------------------

@router.post(
    "/collect",
    response_model=OrderCollectResult,
    status_code=status.HTTP_201_CREATED,
)
def collect_channel_order(
    data: OrderChannelCollectRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrderService(db)

    return service.collect_channel_order(
        current_user.company_id, data, current_user.id,
    )


@router.post(
    "/collect/coupang",
    response_model=CoupangOrderCollectionRunResponse,
)
def collect_coupang_orders(
    data: CoupangOrderCollectionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_order_credential_store),
    provider_factory=Depends(get_coupang_order_provider_factory),
):
    result = CoupangOrderCollectionService(
        db, credential_store, provider_factory=provider_factory,
    ).run(
        current_user.company_id,
        data.store_connection_id,
        data.channel_status,
        actor_user_id=current_user.id,
    )
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id,
        action="COUPANG_ORDER_COLLECTION_RUN", entity="store_connection",
        entity_id=str(data.store_connection_id),
        description=(
            f"쿠팡 주문 조회 status={result.status}, "
            f"channel_status={result.channel_status}, "
            f"received={result.received_order_count}, "
            f"failed={result.failed_order_count}"
        ),
    )
    db.commit()
    return CoupangOrderCollectionRunResponse(
        **{**result.__dict__, "error_codes": list(result.error_codes)},
    )


@router.post(
    "/collect/coupang/preview",
    response_model=CoupangOrderCollectionPreviewResponse,
)
def preview_coupang_order_collection(
    data: CoupangOrderCollectionRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    # 연결의 회사/채널/상태 검증은 실행 서비스와 동일 계약을 재사용하되,
    # Credential은 읽지 않고 외부 API도 호출하지 않는다.
    CoupangOrderCollectionService.validate_connection(
        db, current_user.company_id, data.store_connection_id,
    )
    start, end = OrderCollectionCursorService(db).preview_window(
        current_user.company_id, data.store_connection_id, data.channel_status,
    )
    return CoupangOrderCollectionPreviewResponse(
        store_connection_id=data.store_connection_id,
        channel_status=data.channel_status,
        created_at_from=start,
        created_at_to=end,
    )


@router.post(
    "/collect/all",
    response_model=MultiChannelCollectionRunResponse,
)
def collect_all_channel_orders(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_order_credential_store),
    provider_factory=Depends(get_coupang_order_provider_factory),
):
    """CONNECTED 상태인 이 회사의 모든 판매처 연결에 대해, 등록된
    채널만 순회하며 기존 개별 수집 로직을 그대로 반복 호출한다(요구
    사항: 판매계정마다 따로 누르지 않아도 되는 통합 수집)."""

    result = OrderMultiChannelCollectionService(
        db, credential_store, provider_factory=provider_factory,
    ).run_all(current_user.company_id, actor_user_id=current_user.id)

    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id,
        action="ORDER_MULTI_CHANNEL_COLLECTION_RUN", entity="company",
        entity_id=str(current_user.company_id),
        description=(
            f"전체 판매처 주문 수집 실행 connections={result.total_connections}, "
            f"succeeded={result.succeeded_runs}, partial={result.partial_runs}, "
            f"failed={result.failed_runs}, "
            f"skipped_marketplaces={list(result.skipped_marketplace_codes)}"
        ),
    )
    db.commit()

    return MultiChannelCollectionRunResponse(
        total_connections=result.total_connections,
        succeeded_runs=result.succeeded_runs,
        partial_runs=result.partial_runs,
        failed_runs=result.failed_runs,
        skipped_marketplace_codes=list(result.skipped_marketplace_codes),
        entries=[
            MultiChannelCollectionEntryResponse(
                store_connection_id=e.store_connection_id,
                marketplace_code=e.marketplace_code,
                channel_status=e.channel_status,
                status=e.status,
                received_order_count=e.received_order_count,
                new_fulfillment_count=e.new_fulfillment_count,
                updated_fulfillment_count=e.updated_fulfillment_count,
                duplicate_fulfillment_count=e.duplicate_fulfillment_count,
                failed_order_count=e.failed_order_count,
                error_codes=list(e.error_codes),
            )
            for e in result.entries
        ],
    )


@router.get(
    "/collection-status",
    response_model=list[OrderCollectionPositionResponse],
)
def list_order_collection_status(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    return (
        db.query(OrderCollectionCursor)
        .filter(OrderCollectionCursor.company_id == current_user.company_id)
        .order_by(OrderCollectionCursor.store_connection_id.asc())
        .order_by(OrderCollectionCursor.channel_status.asc())
        .all()
    )


@router.get(
    "/unresolved-items",
    response_model=list[UnresolvedOrderItemResponse],
)
def list_unresolved_order_items(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    return CoupangOrderCollectionPersistence(db).list_unresolved(
        current_user.company_id,
    )


@router.post(
    "/unresolved-items/{unresolved_item_id}/resolve",
    response_model=OrderItemResponse,
)
def resolve_unresolved_order_item(
    unresolved_item_id: int,
    data: UnresolvedOrderItemResolveRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    item = CoupangOrderMaterializationService(db).resolve_item(
        current_user.company_id, unresolved_item_id,
        data.inventory_sku_id, current_user.id,
    )
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id,
        action="ORDER_ITEM_SKU_RESOLVED", entity="unresolved_order_item",
        entity_id=str(unresolved_item_id),
        description="상품 연결 대기 품목을 HOMEZ 재고 SKU에 연결",
    )
    db.commit()
    return item


@router.get(
    "",
    response_model=list[OrderResponse],
)
def list_orders(
    order_status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrderService(db)

    return service.list_orders(
        current_user.company_id, order_status, skip, limit,
    )



# --------------------------------------------------
# Gate AI-F2(2026-08-22) — 주문·배송·반품 예외 분석(읽기 전용). 반드시
# 아래 "/{order_id}"보다 먼저 등록한다 — FastAPI/Starlette는 경로를
# 등록 순서대로 매칭하므로, 동적 경로를 앞에 두면 "/orders/exception-
# analysis"의 "exception-analysis"가 order_id로 오인되어 422(정수
# 파싱 실패)가 난다(app/domains/guides/router.py의 동일 원칙 —
# 실제 Browser E2E로 재현·확인 후 이 위치로 옮겼다).
# --------------------------------------------------

@router.get(
    "/exception-analysis",
    response_model=OrderExceptionAnalysisResult,
)
def analyze_order_exceptions(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrderExceptionAnalysisService(db)
    exceptions, envelope = service.analyze(current_user.company_id)

    return OrderExceptionAnalysisResult(
        exceptions=[
            OrderExceptionResponse(
                exception_type=e.exception_type, urgency=e.urgency,
                target_entity=e.target_entity, evidence=e.evidence,
                elapsed_hours=e.elapsed_hours,
                recommended_action=e.recommended_action,
                missing_evidence=e.missing_evidence,
                approval_required=e.approval_required,
                execution_allowed=e.execution_allowed,
            )
            for e in exceptions
        ],
        ai_result=envelope.model_dump(mode="json"),
    )


@router.post(
    "/exception-analysis/review-actions",
    response_model=OrderExceptionReviewActionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order_exception_review_action(
    data: OrderExceptionReviewActionCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """요청 바디의 exception_type/target_entity로 지금 이 순간의
    analyze() 결과에서 다시 찾은 예외만 사용한다 — 클라이언트가 보낸
    urgency/evidence/execution_allowed는 존재하지 않으므로 신뢰할
    필요조차 없다."""

    if SafetyService(db).is_emergency_stop_active():
        raise BadRequestException(
            "Emergency Stop이 활성화되어 있어 새 검토 제안을 만들 수 "
            "없습니다.",
        )

    service = OrderExceptionAnalysisService(db)
    exceptions, _envelope = service.analyze(current_user.company_id)

    match = next(
        (
            e for e in exceptions
            if e.exception_type == data.exception_type
            and e.target_entity == data.target_entity
        ),
        None,
    )
    if match is None:
        raise NotFoundException(
            "해당 예외를 다시 찾을 수 없습니다 — 이미 해소되었거나 "
            "상태가 바뀌었을 수 있습니다.",
        )

    action = service.create_review_action(
        current_user.company_id, match,
        idempotency_key=data.idempotency_key, created_by=current_user.id,
    )

    return OrderExceptionReviewActionResponse(proposed_action=action)


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
)
def get_order(
    order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrderService(db)

    return service.get_order(order_id, current_user.company_id)


@router.get(
    "/{order_id}/sensitive-detail",
    response_model=OrderSensitiveDetailResponse,
)
def get_order_sensitive_detail(
    order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """
    배송 원문은 (1) VIEW_SENSITIVE_DATA 권한을 가진 사용자만, (2)
    관리자 재인증 후 정확히 한 번만 조회할 수 있다.

    2026-09-10 Phase 11(HOMEZ_USER_OPERATION_SETTINGS.md 11번 —
    "개인정보 조회 권한을 별도로 설정한다") — 이전에는 admin_guard
    (일반 관리자 여부)만으로 이 엔드포인트에 접근할 수 있었다.
    SUPER_ADMIN은 `has_permission()`이 무조건 True를 반환하므로
    (app/core/permission_check.py::is_super_admin 단락 평가) 이
    권한 체크가 추가돼도 기존 SUPER_ADMIN 흐름은 그대로 동작한다 —
    이 체크는 "SUPER_ADMIN이 아닌 관리자급 사용자에게 개인정보
    조회 권한을 별도로 주지 않는 한 막는다"는 새 게이트다.
    """
    require_permission(db, current_user, "VIEW_SENSITIVE_DATA")
    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise UnauthorizedException(
            "ORDER_SENSITIVE_DETAIL_RECENT_AUTH_REQUIRED: 배송 정보를 "
            "보려면 현재 비밀번호를 다시 확인해야 합니다.",
        )
    return OrderService(db).get_order(order_id, current_user.company_id)


@router.get(
    "/{order_id}/items",
    response_model=list[OrderItemResponse],
)
def list_order_items(
    order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrderService(db)

    return service.list_items(order_id, current_user.company_id)


@router.get(
    "/{order_id}/status-events",
    response_model=list[OrderStatusEventResponse],
)
def list_order_status_events(
    order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrderService(db)

    return service.list_status_events(order_id, current_user.company_id)


@router.get(
    "/{order_id}/ingestion-events",
    response_model=list[OrderIngestionEventResponse],
)
def list_order_ingestion_events(
    order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrderService(db)

    events = service.list_ingestion_events(order_id, current_user.company_id)
    return [
        OrderIngestionEventResponse(
            id=event.id,
            company_id=event.company_id,
            channel_code=event.channel_code,
            channel_order_id=event.channel_order_id,
            status=event.status,
            order_id=event.order_id,
            has_raw_payload=bool(event.raw_payload),
            has_normalized_snapshot=bool(event.normalized_snapshot),
            error_code=event.error_code,
            error_summary=event.error_summary,
            created_at=event.created_at,
        )
        for event in events
    ]


# --------------------------------------------------
# 취소(요구사항 3)
# --------------------------------------------------

@router.post(
    "/{order_id}/cancel",
    response_model=OrderResponse,
)
def cancel_order(
    order_id: int,
    data: OrderCancelRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrderService(db)

    return service.cancel_order(
        order_id, current_user.company_id, data.reason, current_user.id,
    )


# --------------------------------------------------
# 채널 상태 동기화(요구사항 1/6, Fake Provider 전용)
# --------------------------------------------------

@router.post(
    "/{order_id}/sync-channel-status",
    response_model=OrderChannelSyncResult,
)
def sync_channel_status(
    order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """실제 쿠팡/네이버 API를 호출하지 않는다 — Fake Provider 전용."""

    service = OrderService(db)

    return service.sync_channel_status(
        order_id, current_user.company_id, current_user.id,
    )


__all__ = [
    "router",
]
