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
