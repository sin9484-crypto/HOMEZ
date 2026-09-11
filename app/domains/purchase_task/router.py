"""
=========================================================
Homez OS

File : app/domains/purchase_task/router.py

Gate PT-1(2026-08-22 15차 지시) — A+E+F 매입·발주 Workflow API. 전
엔드포인트가 current_user.company_id만 사용한다. 조회는 StaffGuard,
쓰기는 AdminGuard(정책 변경은 +recent-auth+optimistic concurrency,
취소/반품/환불도 관리자 승인 필요 원칙 그대로 AdminGuard). 정적
경로(policy/email-preference/csv)를 동적 경로({task_id})보다 먼저
등록한다(Gate AI-F3에서 발견된 라우팅 순서 결함 재발 방지).
=========================================================
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Header
from fastapi import Query
from fastapi import UploadFile
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.core.exceptions import ServiceUnavailableException
from app.core.exceptions import TooManyRequestsException
from app.core.exceptions import UnauthorizedException
from app.core.permission_check import require_permission
from app.core.guard import AdminGuard
from app.core.guard import StaffGuard
from app.core.recent_auth import consume_recent_auth_token
from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError
from app.domains.purchase_task.channel_adapter import get_purchase_channel_adapter
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.constants import EmailNotificationEventType
from app.domains.purchase_task.csv_import import parse_csv_rows
from app.domains.purchase_task.csv_import import preview_csv
from app.domains.purchase_task.email_service import PurchaseTaskEmailService
from app.domains.purchase_task.model import PurchaseTaskCsvImportLog
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService
from app.domains.purchase_task.repository import PurchaseTaskRepository
from app.domains.purchase_task.schema import AssignChannelConnectionRequest
from app.domains.purchase_task.schema import ChannelConnectionCreate
from app.domains.purchase_task.schema import ChannelConnectionCredentialSave
from app.domains.purchase_task.schema import ChannelConnectionRename
from app.domains.purchase_task.schema import ChannelConnectionResponse
from app.domains.purchase_task.schema import MemberPointCheckResponse
from app.domains.purchase_task.schema import OrderLookupResponse
from app.domains.purchase_task.schema import TrackingLookupResponse
from app.domains.purchase_task.schema import OrderSubmissionReviewRequest
from app.domains.purchase_task.schema import OrderSubmissionReviewResponse
from app.domains.purchase_task.schema import ShippingCostConfirmationRequest
from app.domains.purchase_task.schema import FinalizeOrderApprovalRequest
from app.domains.purchase_task.schema import PurchaseOrderApprovalResponse
from app.domains.purchase_task.schema import SubmitRealOrderRequest
from app.domains.purchase_task.schema import PurchaseOrderSubmissionAttemptResponse
from app.domains.purchase_task.schema import ProductLookupResponse
from app.domains.purchase_task.schema import CandidateCreate
from app.domains.purchase_task.schema import CandidateResponse
from app.domains.purchase_task.schema import CancelRequest
from app.domains.purchase_task.schema import CsvImportResultResponse
from app.domains.purchase_task.schema import CsvPreviewResponse
from app.domains.purchase_task.schema import CsvRowResultResponse
from app.domains.purchase_task.schema import EmailPreferenceResponse
from app.domains.purchase_task.schema import EmailPreferenceUpdate
from app.domains.purchase_task.schema import EmailProviderSettingResponse
from app.domains.purchase_task.schema import EmailProviderSettingUpdate
from app.domains.purchase_task.schema import EvaluateRequest
from app.domains.purchase_task.schema import EvaluateResponse
from app.domains.purchase_task.schema import ExtendReservationRequest
from app.domains.purchase_task.schema import MatchCheckRequest
from app.domains.purchase_task.schema import PolicySettingResponse
from app.domains.purchase_task.schema import PolicySettingUpdate
from app.domains.purchase_task.schema import PurchaseTaskCreate
from app.domains.purchase_task.schema import PurchaseTaskResponse
from app.domains.purchase_task.schema import RecordPurchaseRequest
from app.domains.purchase_task.schema import RecordTrackingRequest
from app.domains.purchase_task.schema import RefundRequest
from app.domains.purchase_task.schema import SearchLinksResponse
from app.domains.purchase_task.schema import TestEmailRequest
from app.domains.purchase_task.schema import TrackingResponse
from app.domains.purchase_task.service import PurchaseTaskService
from app.domains.user.model import User

router = APIRouter(prefix="/purchase-tasks", tags=["Purchase Task"])


def _get_email_provider():
    """실 이메일 Provider 연결 지점 — 이번 라운드는 구성돼 있지
    않다(NullPurchaseTaskEmailProvider가 기본값, email_service.py
    참고). 실 Provider 연결은 별도 승인 대상이다."""

    from app.domains.purchase_task.email_provider import (
        NullPurchaseTaskEmailProvider,
    )
    return NullPurchaseTaskEmailProvider()


# --------------------------------------------------
# 정책(정적 경로 — 동적 {task_id} 경로보다 먼저 등록)
# --------------------------------------------------

@router.get("/policy", response_model=PolicySettingResponse)
def get_policy(
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskPolicyService(db)
    setting = service.get_or_create_default_settings(current_user.company_id)
    db.commit()
    return _policy_to_response(setting)


@router.put("/policy", response_model=PolicySettingResponse)
def update_policy(
    data: PolicySettingUpdate,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
    if_unmodified_since: str | None = Header(
        default=None, alias="X-If-Unmodified-Since",
    ),
):
    """정책 변경은 recent-auth + optimistic concurrency를 요구한다
    (지시문 15). X-If-Unmodified-Since에 마지막으로 조회한
    updated_at(ISO 문자열)을 그대로 보내야 한다 — 그 사이 다른
    관리자가 먼저 바꿨으면 409로 거부한다."""

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise UnauthorizedException(
            "PURCHASE_TASK_POLICY_RECENT_AUTH_REQUIRED: 매입 정책을 "
            "변경하려면 현재 비밀번호를 다시 확인해야 합니다.",
        )

    service = PurchaseTaskPolicyService(db)
    setting = service.get_or_create_default_settings(current_user.company_id)

    if if_unmodified_since:
        from datetime import datetime as _dt
        try:
            expected = _dt.fromisoformat(if_unmodified_since)
        except ValueError:
            raise BadRequestException(
                "X-If-Unmodified-Since 형식이 올바르지 않습니다.",
            )
        if setting.updated_at != expected:
            from app.core.exceptions import ConflictException
            raise ConflictException(
                "다른 곳에서 이미 정책이 변경되었습니다 — 새로고침 후 "
                "다시 시도하세요.",
            )

    update_data = data.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(setting, field_name, value)

    db.commit()
    return _policy_to_response(setting)


def _policy_to_response(setting) -> PolicySettingResponse:

    return PolicySettingResponse(
        id=setting.id, company_id=setting.company_id,
        per_order_max_amount=setting.per_order_max_amount,
        daily_purchase_limit_amount=setting.daily_purchase_limit_amount,
        monthly_purchase_budget_amount=setting.monthly_purchase_budget_amount,
        max_quantity_per_product=setting.max_quantity_per_product,
        min_net_profit=setting.min_net_profit,
        min_margin_rate=setting.min_margin_rate,
        max_price_increase_rate=setting.max_price_increase_rate,
        max_delivery_days=setting.max_delivery_days,
        require_return_allowed=setting.require_return_allowed,
        min_match_confidence=setting.min_match_confidence,
        max_concurrent_tasks=setting.max_concurrent_tasks,
        default_additional_shipping_fee=setting.default_additional_shipping_fee,
        default_return_risk_reserve=setting.default_return_risk_reserve,
        budget_reservation_hours=setting.budget_reservation_hours,
        min_residual_points=setting.min_residual_points,
        order_approval_validity_minutes=setting.order_approval_validity_minutes,
        updated_at=setting.updated_at,
    )


# --------------------------------------------------
# 이메일 알림 설정(정적 경로)
# --------------------------------------------------

@router.get("/email-preference", response_model=EmailPreferenceResponse)
def get_email_preference(
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskEmailService(db, provider=_get_email_provider())
    pref = service.get_preference(current_user.company_id, current_user.id)
    try:
        toggles = json.loads(pref.event_toggles_json or "{}")
    except (TypeError, ValueError):
        toggles = {}
    return EmailPreferenceResponse(enabled=pref.enabled, event_toggles=toggles)


@router.put("/email-preference", response_model=EmailPreferenceResponse)
def update_email_preference(
    data: EmailPreferenceUpdate,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskEmailService(db, provider=_get_email_provider())
    pref = service.set_preference(
        current_user.company_id, current_user.id, enabled=data.enabled,
        event_toggles=data.event_toggles,
    )
    try:
        toggles = json.loads(pref.event_toggles_json or "{}")
    except (TypeError, ValueError):
        toggles = {}
    return EmailPreferenceResponse(enabled=pref.enabled, event_toggles=toggles)


@router.post("/email-preference/test")
def send_test_email(
    data: TestEmailRequest,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskEmailService(db, provider=_get_email_provider())
    status_value = service.send_notification(
        company_id=current_user.company_id, user_id=current_user.id,
        to_email=data.to_email, event_type=EmailNotificationEventType.TASK_CREATED,
        purchase_task_id=None, product_title="테스트 알림",
        detail="이메일 알림 설정 테스트입니다.",
        idempotency_key=f"test:{current_user.id}:{data.to_email}:{__import__('time').time()}",
    )
    return {"status": status_value}


# --------------------------------------------------
# 이메일 Provider 설정 계약(Gate PT-2E, 정적 경로) — 이 값을 저장해도
# 실제 발송 경로(_get_email_provider())는 여전히 Null Provider다.
# 실제 SMTP/Transactional Adapter 연결은 별도 승인 대상이다.
# --------------------------------------------------

@router.get(
    "/email-provider-setting", response_model=EmailProviderSettingResponse,
)
def get_email_provider_setting(
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    repository = PurchaseTaskRepository(db)
    setting = repository.get_email_provider_setting(current_user.company_id)
    if setting is None:
        from datetime import datetime as _dt
        return EmailProviderSettingResponse(
            provider_type="NONE", smtp_host=None, smtp_port=None,
            smtp_use_tls=True, from_address=None, from_name=None,
            has_credential_reference=False, is_active=False,
            updated_at=_dt.utcnow(),
        )
    return EmailProviderSettingResponse(
        provider_type=setting.provider_type, smtp_host=setting.smtp_host,
        smtp_port=setting.smtp_port, smtp_use_tls=setting.smtp_use_tls,
        from_address=setting.from_address, from_name=setting.from_name,
        has_credential_reference=setting.credential_reference is not None,
        is_active=setting.is_active, updated_at=setting.updated_at,
    )


@router.put(
    "/email-provider-setting", response_model=EmailProviderSettingResponse,
)
def update_email_provider_setting(
    data: EmailProviderSettingUpdate,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """credential_reference를 다루므로 정책 변경과 동일하게
    recent-auth를 요구한다."""

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise UnauthorizedException(
            "PURCHASE_TASK_EMAIL_PROVIDER_RECENT_AUTH_REQUIRED: 이메일 "
            "Provider 설정을 변경하려면 현재 비밀번호를 다시 확인해야 "
            "합니다.",
        )

    repository = PurchaseTaskRepository(db)
    setting = repository.get_email_provider_setting(current_user.company_id)
    if setting is None:
        from app.domains.purchase_task.model import (
            PurchaseTaskEmailProviderSetting,
        )
        setting = PurchaseTaskEmailProviderSetting(
            company_id=current_user.company_id,
        )
        repository.add_email_provider_setting(setting)

    for field_name, value in data.model_dump().items():
        setattr(setting, field_name, value)

    db.commit()
    return EmailProviderSettingResponse(
        provider_type=setting.provider_type, smtp_host=setting.smtp_host,
        smtp_port=setting.smtp_port, smtp_use_tls=setting.smtp_use_tls,
        from_address=setting.from_address, from_name=setting.from_name,
        has_credential_reference=setting.credential_reference is not None,
        is_active=setting.is_active, updated_at=setting.updated_at,
    )


# --------------------------------------------------
# CSV 가져오기(정적 경로)
# --------------------------------------------------

@router.post("/csv/preview", response_model=CsvPreviewResponse)
async def preview_csv_upload(
    file: UploadFile = File(...),
    current_user: User = Depends(AdminGuard),
):

    raw = await file.read()
    try:
        preview = preview_csv(raw)
    except ValueError as e:
        raise BadRequestException(str(e)) from e

    return CsvPreviewResponse(
        header=preview.header, row_count=preview.row_count,
        sample_rows=preview.sample_rows,
        column_mapping_ok=preview.column_mapping_ok,
        missing_columns=preview.missing_columns,
    )


@router.post("/csv/import", response_model=CsvImportResultResponse)
async def import_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """행별 성공·실패를 그대로 보고한다 — 전체 성공으로 가장하지
    않는다."""

    raw = await file.read()
    try:
        rows = parse_csv_rows(raw)
    except ValueError as e:
        raise BadRequestException(str(e)) from e

    service = PurchaseTaskService(db)
    results: list[CsvRowResultResponse] = []
    success_count = 0

    for row in rows:
        if not row.success:
            results.append(CsvRowResultResponse(
                row_number=row.row_number, success=False, error=row.error,
            ))
            continue

        try:
            task = service.record_purchase(
                row.data["purchase_task_id"], current_user.company_id,
                shopping_mall_code=row.data["shopping_mall_code"],
                external_order_number=row.data["external_order_number"],
                actual_amount=row.data["actual_amount"],
                actual_shipping_fee=row.data["actual_shipping_fee"],
                purchased_at=row.data["purchased_at"],
                selected_option_note=row.data["selected_option_note"],
                memo=row.data["memo"], recorded_by=current_user.id,
                idempotency_key=(
                    f"csv:{current_user.company_id}:"
                    f"{row.data['shopping_mall_code']}:"
                    f"{row.data['external_order_number']}"
                ),
            )
            results.append(CsvRowResultResponse(
                row_number=row.row_number, success=True, error=None,
                purchase_task_id=task.id,
            ))
            success_count += 1
        except Exception as e:  # noqa: BLE001 — 행별 실패를 계속 진행시킨다
            results.append(CsvRowResultResponse(
                row_number=row.row_number, success=False, error=str(e),
            ))

    repository = PurchaseTaskRepository(db)
    repository.add_csv_import_log(PurchaseTaskCsvImportLog(
        company_id=current_user.company_id, uploaded_by=current_user.id,
        total_rows=len(rows), success_rows=success_count,
        failure_rows=len(rows) - success_count,
    ))
    db.commit()

    return CsvImportResultResponse(
        total_rows=len(rows), success_rows=success_count,
        failure_rows=len(rows) - success_count, results=results,
    )


# --------------------------------------------------
# 구매 작업(목록/생성)
# --------------------------------------------------

@router.get("", response_model=list[PurchaseTaskResponse])
def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    source_order_id: int | None = None,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.list_tasks(
        current_user.company_id, status=status_filter,
        source_order_id=source_order_id,
    )


@router.post("/reconcile-orders")
def reconcile_orders(
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """Gate PT-2B — 자동 생성 훅을 놓친 기존 주문을 수동으로 다시
    훑는다(백필). 자동 생성 경로와 동일한 Service 메서드를 그대로
    호출한다."""

    service = PurchaseTaskService(db)
    return service.reconcile_orders(
        current_user.company_id, triggered_by=current_user.id,
    )


@router.post("/check-deadlines")
def check_deadlines(
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """Gate PT-2E — DEADLINE_APPROACHING 재확인. 배경 스케줄러가
    없으므로 관리자가 명시적으로 호출한다."""

    service = PurchaseTaskService(db)
    return {"notified_task_ids": service.check_deadlines_approaching(
        current_user.company_id,
    )}


@router.post(
    "", response_model=PurchaseTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    data: PurchaseTaskCreate,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.create_task(
        current_user.company_id, source_order_id=data.source_order_id,
        source_order_item_id=data.source_order_item_id,
        product_title=data.product_title, brand=data.brand,
        manufacturer=data.manufacturer, model_name=data.model_name,
        gtin=data.gtin, capacity=data.capacity, quantity=data.quantity,
        color_or_scent=data.color_or_scent, options=data.options,
        components=data.components,
        shippable_region_note=data.shippable_region_note,
        coupang_sale_amount=data.coupang_sale_amount,
        coupang_fee_amount=data.coupang_fee_amount,
        purchase_deadline=data.purchase_deadline,
        idempotency_key=data.idempotency_key,
        correlation_id=data.correlation_id, created_by=current_user.id,
    )


# --------------------------------------------------
# 매입처 연결(Gate PT-3, 2026-09-08, item 7) — 정적 경로, 동적
# {task_id} 경로보다 먼저 등록한다(이 파일의 기존 원칙 그대로).
# --------------------------------------------------

def get_purchase_channel_connection_service(
    db: Session = Depends(get_db),
) -> PurchaseChannelConnectionService:
    """2026-09-08 후속(격리 검증 중 실제 Windows Credential Manager
    오염 사고 재발 방지) — 이 아래 모든 매입처 연결 엔드포인트가
    PurchaseChannelConnectionService를 직접 생성하지 않고 이 의존성을
    거치게 한다. 격리/E2E 검증 스크립트는 FastAPI의 표준 오버라이드
    (`app.dependency_overrides[get_purchase_channel_connection_service]
    = lambda: PurchaseChannelConnectionService(isolated_db,
    credential_store=InMemoryCredentialStore())`)로 자격증명 저장소까지
    함께 격리해야 한다 — DATABASE_URL만 바꾸는 것으로는 부족하다(직접
    생성하면 credential_store 인자를 안 넘기는 한 항상 실제
    WindowsCredentialStore()를 새로 만든다)."""

    return PurchaseChannelConnectionService(db)


@router.post(
    "/channel-connections", response_model=ChannelConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_channel_connection(
    data: ChannelConnectionCreate,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):

    return service.create_connection(
        current_user.company_id, mall_code=data.mall_code,
        account_label=data.account_label, memo=data.memo,
        idempotency_key=data.idempotency_key, triggered_by=current_user.id,
    )


@router.get("/channel-connections", response_model=list[ChannelConnectionResponse])
def list_channel_connections(
    mall_code: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    current_user: User = Depends(StaffGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):

    return service.list_connections(
        current_user.company_id, mall_code=mall_code,
        include_inactive=include_inactive,
    )


@router.put(
    "/channel-connections/{connection_id}",
    response_model=ChannelConnectionResponse,
)
def rename_channel_connection(
    connection_id: int, data: ChannelConnectionRename,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):

    return service.rename_connection(
        connection_id, current_user.company_id, data.account_label,
        triggered_by=current_user.id,
    )


@router.post(
    "/channel-connections/{connection_id}/check",
    response_model=ChannelConnectionResponse,
)
def check_channel_connection(
    connection_id: int,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):

    return service.check_connection_status(connection_id, current_user.company_id)


@router.post(
    "/channel-connections/{connection_id}/verify",
    response_model=ChannelConnectionResponse,
)
def verify_channel_connection(
    connection_id: int,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """사람이 직접 "연결 확인 완료"를 눌렀을 때만 호출된다 — 이
    엔드포인트가 verified_at을 채우는 유일한 경로다."""

    return service.mark_verified(
        connection_id, current_user.company_id, triggered_by=current_user.id,
    )


@router.post(
    "/channel-connections/{connection_id}/additional-auth-required",
    response_model=ChannelConnectionResponse,
)
def mark_channel_connection_additional_auth_required(
    connection_id: int,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """HOMEZ가 2단계 인증·본인확인 요구를 기술적으로 감지할 방법이
    없어, 사람이 실제로 로그인하다가 그 화면을 만났을 때 직접
    표시하는 자기보고 엔드포인트다."""

    return service.mark_additional_auth_required(
        connection_id, current_user.company_id, triggered_by=current_user.id,
    )


@router.post(
    "/channel-connections/{connection_id}/deactivate",
    response_model=ChannelConnectionResponse,
)
def deactivate_channel_connection(
    connection_id: int,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):

    return service.deactivate_connection(
        connection_id, current_user.company_id, triggered_by=current_user.id,
    )


@router.post(
    "/channel-connections/{connection_id}/reactivate",
    response_model=ChannelConnectionResponse,
)
def reactivate_channel_connection(
    connection_id: int,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):

    return service.reactivate_connection(
        connection_id, current_user.company_id, triggered_by=current_user.id,
    )


@router.delete(
    "/channel-connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_channel_connection(
    connection_id: int,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """"연결 해제"(비활성화, 되돌릴 수 있음)와 달리 행 자체를 완전히
    지운다 — 사용해본 적 없는 테스트/오등록 연결 정리 전용. 과거
    구매 작업·매입 기록이 실제로 참조하는 연결은 서비스 계층에서
    거부한다."""

    service.delete_connection(
        connection_id, current_user.company_id, triggered_by=current_user.id,
    )


@router.get("/channel-connections/{connection_id}/capabilities")
def get_channel_connection_capabilities(
    connection_id: int,
    current_user: User = Depends(StaffGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """이 매입처 Adapter가 8개 기능 중 실제로 무엇을 확인했는지(지원/
    미지원/미확인)를 화면에 보여주기 위한 정적 조회 — 실제 네트워크
    호출 없음."""

    connection = service.get_connection_or_404(connection_id, current_user.company_id)
    try:
        adapter = get_purchase_channel_adapter(connection.mall_code)
    except PurchaseChannelAdapterError:
        return {"mall_code": connection.mall_code, "capabilities": {}}
    return {
        "mall_code": connection.mall_code,
        "capabilities": adapter.capability_matrix(),
    }


@router.post(
    "/channel-connections/{connection_id}/credential",
    response_model=ChannelConnectionResponse,
)
def save_channel_connection_credential(
    connection_id: int, data: ChannelConnectionCredentialSave,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """CREDENTIAL 방식 연결(현재 온채널) 전용 — 값은 이 함수를 거쳐
    가기만 할 뿐 로그·응답 어디에도 남기지 않는다."""

    return service.save_credential(
        connection_id, current_user.company_id, auth_key=data.auth_key,
        allowed_ip=data.allowed_ip, triggered_by=current_user.id,
    )


def _translate_onchannel_error(exc: Exception):
    """2026-09-08 후속 — 온채널 실 API 호출 실패를 구분해서 HTTP로
    옮긴다(지시문 1번: 인증실패·권한부족·호출제한·응답형식오류를
    구분한다). 예외 메시지는 onchannel_client.py가 이미 JWT·개인정보
    없이 만들어 두므로 그대로 전달해도 안전하다.

    2026-09-08 후속(7번 결함 감사, 3번 — "실패 상태의 의미 구분") —
    OnchannelAuthenticationError를 그냥 401로 던지면, console.js의
    apiFetch()가 이를 "HOMEZ 로그인 세션 만료"와 절대 구분하지
    못한다(app/core/auth.py가 실제 세션 만료 때 붙이는 X-Auth-
    Error-Code 헤더가 없는 401은 클라이언트가 무조건 로그인 화면
    전환으로 처리한다 — console.js:216-219). 격리 브라우저로 실제
    재현: 온채널 쪽 401(자격증명 무효)이 발생하자 대시보드 전체가
    로그아웃되고 "인증이 필요합니다" 로그인 화면으로 튕겼다 — 매입처
    자격증명 문제가 운영자의 HOMEZ 로그인 자체를 끊어버린 것이다.
    이 헤더에 ALLOWED_LOGOUT_CODES(SESSION_EXPIRED/SESSION_REVOKED/
    ACCOUNT_DISABLED)에 없는 코드를 실어, 클라이언트가 "인증 실패는
    맞지만 로그아웃 대상은 아니다"로 정확히 구분하게 한다(console.js
    handleAuthFailure의 기존 설계된 탈출구를 그대로 재사용)."""

    from app.domains.purchase_task.onchannel_client import (
        OnchannelAuthenticationError, OnchannelNetworkError,
        OnchannelNotFoundError, OnchannelPermissionError,
        OnchannelRateLimitedError, OnchannelResponseFormatError,
        OnchannelValidationError,
    )

    if isinstance(exc, OnchannelAuthenticationError):
        raise UnauthorizedException(
            str(exc),
            headers={"X-Auth-Error-Code": "EXTERNAL_CHANNEL_AUTH_FAILED"},
        ) from exc
    if isinstance(exc, OnchannelPermissionError):
        raise ForbiddenException(str(exc)) from exc
    if isinstance(exc, OnchannelNotFoundError):
        raise NotFoundException(str(exc)) from exc
    if isinstance(exc, OnchannelRateLimitedError):
        raise TooManyRequestsException(str(exc)) from exc
    if isinstance(exc, OnchannelValidationError):
        raise BadRequestException(str(exc)) from exc
    if isinstance(exc, (OnchannelResponseFormatError, OnchannelNetworkError)):
        raise ServiceUnavailableException(str(exc)) from exc
    raise ServiceUnavailableException(str(exc)) from exc


@router.get(
    "/channel-connections/{connection_id}/products/{external_product_id}",
    response_model=ProductLookupResponse,
)
def lookup_channel_connection_product(
    connection_id: int, external_product_id: str,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """실제 매입처 API로 상품을 조회한다 — 호출 시점에 실제 네트워크
    요청이 나간다. 승인된 범위에서만 사용한다."""

    from app.domains.purchase_task.onchannel_client import OnchannelApiError

    try:
        result = service.lookup_product(
            connection_id, current_user.company_id, external_product_id,
            triggered_by=current_user.id,
        )
    except OnchannelApiError as exc:
        _translate_onchannel_error(exc)
    except PurchaseChannelAdapterError as exc:
        raise BadRequestException(str(exc)) from exc
    return result


@router.get(
    "/channel-connections/{connection_id}/orders/{external_order_number}",
    response_model=OrderLookupResponse,
)
def lookup_channel_connection_order(
    connection_id: int, external_order_number: str,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """실제 매입처 API로 주문을 조회한다 — 호출 시점에 실제 네트워크
    요청이 나간다. 승인된 범위에서만 사용한다."""

    from app.domains.purchase_task.onchannel_client import OnchannelApiError

    try:
        result = service.lookup_order(
            connection_id, current_user.company_id, external_order_number,
            triggered_by=current_user.id,
        )
    except OnchannelApiError as exc:
        _translate_onchannel_error(exc)
    except PurchaseChannelAdapterError as exc:
        raise BadRequestException(str(exc)) from exc
    return result


@router.get(
    "/channel-connections/{connection_id}/orders/{external_order_number}/tracking",
    response_model=TrackingLookupResponse,
)
def lookup_channel_connection_tracking(
    connection_id: int, external_order_number: str,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """실제 매입처 API로 배송·송장 정보를 조회한다(2026-09-10 후속,
    Phase 7) — 호출 시점에 실제 네트워크 요청이 나간다. 복수 송장이
    감지되면 자동으로 하나를 고르지 않는다(단일 송장 정책 —
    응답의 multiple_deliveries_detected 참고)."""

    from app.domains.purchase_task.onchannel_client import OnchannelApiError

    try:
        result = service.lookup_tracking(
            connection_id, current_user.company_id, external_order_number,
            triggered_by=current_user.id,
        )
    except OnchannelApiError as exc:
        _translate_onchannel_error(exc)
    except PurchaseChannelAdapterError as exc:
        raise BadRequestException(str(exc)) from exc
    return result


@router.get(
    "/channel-connections/{connection_id}/point",
    response_model=MemberPointCheckResponse,
)
def check_channel_connection_member_point(
    connection_id: int,
    current_user: User = Depends(AdminGuard),
    service: PurchaseChannelConnectionService = Depends(get_purchase_channel_connection_service),
):
    """실제 매입처 API로 회원 포인트를 조회한다 — 발주·결제 계약
    조사 전용 진단 호출, 호출 시점에 실제 네트워크 요청이 나간다.
    이 호출은 연결 확인 상태(status/verified_at)를 절대 바꾸지
    않는다(서비스 계층 계약) — 포인트 조회 성공을 발주 실행 권한
    검증으로 재사용하지 않기 위함이다. 응답의 member_id는 서버가
    이미 마스킹한 값만 담는다."""

    from app.domains.purchase_task.onchannel_client import OnchannelApiError

    try:
        result = service.check_member_point(
            connection_id, current_user.company_id, triggered_by=current_user.id,
        )
    except OnchannelApiError as exc:
        _translate_onchannel_error(exc)
    except PurchaseChannelAdapterError as exc:
        raise BadRequestException(str(exc)) from exc
    return result


# --------------------------------------------------
# 구매 작업(단건, 동적 경로)
# --------------------------------------------------

@router.get("/{task_id}", response_model=PurchaseTaskResponse)
def get_task(
    task_id: int,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.get_task(task_id, current_user.company_id)


@router.get("/{task_id}/search-links", response_model=SearchLinksResponse)
def get_search_links(
    task_id: int,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    task = service.get_task(task_id, current_user.company_id)
    return service.get_search_links(task)


@router.post(
    "/{task_id}/channel-connection", response_model=PurchaseTaskResponse,
)
def assign_channel_connection(
    task_id: int, data: AssignChannelConnectionRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """이 작업을 어느 연결된 매입처 계정으로 처리할지 사람이 명시적
    으로 선택한다 — 여러 연결 중 하나를 서버가 대신 고르지 않는다."""

    service = PurchaseTaskService(db)
    return service.assign_channel_connection(
        task_id, current_user.company_id, data.connection_id,
        triggered_by=current_user.id,
    )


@router.post(
    "/{task_id}/order-submission-review",
    response_model=OrderSubmissionReviewResponse,
)
def review_order_submission(
    task_id: int, data: OrderSubmissionReviewRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """2026-09-09 후속("발주 전 최종 검토 화면") — 쿠팡 주문·매입처
    연결·온채널 상품/옵션·수취정보를 한 응답으로 모아 보여준다.
    **읽기 전용이다** — 이 엔드포인트는 실제 발주를 절대 실행하지
    않는다(PurchaseOrderSubmissionService.submit_order()를 호출하는
    코드가 없다). 수취정보 원문은 X-Recent-Auth-Token(현재 비밀번호
    재확인, /orders/{id}/sensitive-detail과 동일한 계약)이 없으면
    마스킹된 값만 내려간다.

    2026-09-10 Phase 11(HOMEZ_USER_OPERATION_SETTINGS.md 11번 —
    "개인정보 조회 권한을 별도로 설정한다") — 원문(unmasked)을
    시도하는 요청(X-Recent-Auth-Token이 실려 온 경우)만
    VIEW_SENSITIVE_DATA 권한을 요구한다. 마스킹된 값만 보는 일반
    발주 검토 흐름은 이 권한이 없어도 그대로 동작한다 — 원문 조회
    시도에만 추가 게이트를 건다."""

    if recent_auth_token:
        require_permission(db, current_user, "VIEW_SENSITIVE_DATA")

    service = PurchaseTaskService(db)
    return service.build_order_submission_review(
        task_id, current_user.company_id,
        external_product_id=data.external_product_id,
        options=[opt.model_dump() for opt in data.options],
        recent_auth_token=recent_auth_token,
        triggered_by=current_user.id,
    )


def _order_approval_to_response(approval) -> PurchaseOrderApprovalResponse:

    return PurchaseOrderApprovalResponse(
        id=approval.id, purchase_task_id=approval.purchase_task_id,
        product_code=approval.product_code, status=approval.status,
        shipping_cost_amount=approval.shipping_cost_amount,
        shipping_cost_is_free_confirmed=approval.shipping_cost_is_free_confirmed,
        shipping_cost_source=approval.shipping_cost_source,
        shipping_cost_basis_memo=approval.shipping_cost_basis_memo,
        shipping_cost_confirmed_by=approval.shipping_cost_confirmed_by,
        shipping_cost_confirmed_at=approval.shipping_cost_confirmed_at,
        item_amount_snapshot=approval.item_amount_snapshot,
        required_points_snapshot=approval.required_points_snapshot,
        current_points_snapshot=approval.current_points_snapshot,
        margin_amount_snapshot=approval.margin_amount_snapshot,
        margin_rate_snapshot=approval.margin_rate_snapshot,
        approved_by=approval.approved_by, approved_at=approval.approved_at,
        expires_at=approval.expires_at,
        invalidated_reason=approval.invalidated_reason,
    )


@router.get(
    "/{task_id}/order-approval",
    response_model=PurchaseOrderApprovalResponse | None,
)
def get_order_approval(
    task_id: int, connection_id: int | None = None,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """2026-09-11 후속(반자동 완료 라운드 Phase 5·7) — 이 작업의
    현재 발주 승인 상태를 조회한다(부작용 없음). 아직 승인 절차를
    시작하지 않았으면 null을 반환한다. `connection_id`를 생략하면
    이 작업에 이미 배정된 연결(task.channel_connection_id)을 쓴다 —
    /shipping-cost·/finalize는 항상 connection_id를 명시적으로
    받으므로, 호출부가 같은 값을 여기도 넘기면 배정 여부와 무관하게
    정확히 그 승인을 조회할 수 있다."""

    from app.domains.purchase_task.order_approval_service import (
        PurchaseOrderApprovalService,
    )

    task_service = PurchaseTaskService(db)
    task = task_service.get_task(task_id, current_user.company_id)
    effective_connection_id = connection_id or task.channel_connection_id
    if effective_connection_id is None:
        return None

    approval_service = PurchaseOrderApprovalService(db)
    approval = approval_service.get_approval(
        effective_connection_id, current_user.company_id, task_id,
    )
    if approval is None:
        return None
    return _order_approval_to_response(approval)


@router.post(
    "/{task_id}/order-approval/shipping-cost",
    response_model=PurchaseOrderApprovalResponse,
)
def confirm_order_shipping_cost(
    task_id: int, data: ShippingCostConfirmationRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """2026-09-11 후속(Phase 5) — 배송비를 추정하지 않는다. 사용자가
    외부 화면에서 직접 확인한 값을 증거(출처·메모)와 함께 기록한다.
    이 호출만으로는 발주가 열리지 않는다 — 반드시 /finalize를 거쳐야
    ACTIVE로 전환된다."""

    from app.domains.purchase_task.order_approval_service import (
        PurchaseOrderApprovalService,
    )

    task_service = PurchaseTaskService(db)
    # get_task()가 회사 소유가 아니면 NotFoundException을 던진다 —
    # 다른 회사의 작업에 승인 행을 만드는 것을 이 시점에서 차단한다.
    task_service.get_task(task_id, current_user.company_id)

    approval_service = PurchaseOrderApprovalService(db)
    approval = approval_service.confirm_shipping_cost(
        data.connection_id, current_user.company_id, task_id,
        data.external_product_id,
        shipping_cost_amount=data.shipping_cost_amount,
        is_free_shipping_confirmed=data.is_free_shipping_confirmed,
        source=data.source, basis_memo=data.basis_memo,
        confirmed_by=current_user.id,
    )
    return _order_approval_to_response(approval)


@router.post(
    "/{task_id}/order-approval/finalize",
    response_model=PurchaseOrderApprovalResponse,
)
def finalize_order_approval(
    task_id: int, data: FinalizeOrderApprovalRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """2026-09-11 후속(Phase 7) — 배송비 확인이 끝난 승인에 대해
    한도·잔여포인트·마진을 최종 재확인하고, 전부 통과하면 ACTIVE로
    전환한다(유효시간 기본 10분). 이 엔드포인트 자체는 온채널에
    네트워크 호출을 하지 않는다 — item_amount/current_points는
    호출부가 직전 /order-submission-review에서 이미 실측한 값을
    그대로 넘긴다."""

    from app.domains.purchase_task.order_approval_service import (
        PurchaseOrderApprovalService,
    )

    task_service = PurchaseTaskService(db)
    task_service.get_task(task_id, current_user.company_id)

    approval_service = PurchaseOrderApprovalService(db)
    approval = approval_service.finalize_approval(
        data.connection_id, current_user.company_id, task_id,
        item_amount=data.item_amount, current_points=data.current_points,
        triggered_by=current_user.id,
    )
    return _order_approval_to_response(approval)


@router.post(
    "/{task_id}/order-approval/submit",
    response_model=PurchaseOrderSubmissionAttemptResponse,
)
def submit_real_order(
    task_id: int, data: SubmitRealOrderRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """2026-09-11 후속(반자동 완료 라운드 목표 1) — 실제 온채널
    발주를 실행한다. **이 엔드포인트가 존재한다는 사실 자체는
    승인이 아니다.** `data.confirm_real_submission`이 True가
    아니면 PurchaseOrderSubmissionService.submit_order()가 그
    시점에서 즉시 거부한다(연결 조회조차 하지 않는다 — 기존
    fail-closed 설계 그대로). 이 요청 바디는 수취인 개인정보를
    담으므로(발주 실행에 필요) 재인증(X-Recent-Auth-Token)과
    VIEW_SENSITIVE_DATA 권한을 둘 다 요구한다 — 단순 조회보다
    엄격한 게이트다(실제 금전·개인정보 전송이 걸려 있기 때문).

    이 메서드를 호출해도 Gate A(연결 인증)·Gate B(계약 확인)·
    Gate C(판매신청)·Gate D(포인트·배송비 승인)가 전부 그대로
    적용된다 — 이 엔드포인트는 그 게이트들을 우회하는 새 경로가
    아니라, 이미 있던 submit_order()를 최초로 라우터에 연결하는
    것뿐이다."""

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise UnauthorizedException(
            "PURCHASE_ORDER_SUBMIT_RECENT_AUTH_REQUIRED: 실제 발주를 "
            "실행하려면 현재 비밀번호를 다시 확인해야 합니다.",
        )
    require_permission(db, current_user, "VIEW_SENSITIVE_DATA")

    from app.domains.purchase_task.onchannel_client import OnchannelApiError
    from app.domains.purchase_task.order_submission_service import (
        PurchaseOrderSubmissionService,
    )

    task_service = PurchaseTaskService(db)
    task_service.get_task(task_id, current_user.company_id)

    service = PurchaseOrderSubmissionService(db)
    try:
        attempt = service.submit_order(
            data.connection_id, current_user.company_id,
            idempotency_key=data.idempotency_key, product_code=data.product_code,
            options=[opt.model_dump() for opt in data.options],
            recv_name=data.recv_name, recv_tell=data.recv_tell,
            recv_mobile=data.recv_mobile, zipcode=data.zipcode,
            address=data.address, address_detail=data.address_detail,
            comment=data.comment, site_name=data.site_name,
            purchase_task_id=task_id, triggered_by=current_user.id,
            confirm_real_submission=data.confirm_real_submission,
        )
    except OnchannelApiError as exc:
        _translate_onchannel_error(exc)
    except PurchaseChannelAdapterError as exc:
        raise BadRequestException(str(exc)) from exc

    return PurchaseOrderSubmissionAttemptResponse(
        id=attempt.id, status=attempt.status,
        external_order_code=attempt.external_order_code,
        failure_detail=attempt.failure_detail,
        idempotency_key=attempt.idempotency_key,
        started_at=attempt.started_at, finished_at=attempt.finished_at,
    )


@router.get("/{task_id}/candidates", response_model=list[CandidateResponse])
def list_candidates(
    task_id: int,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    service.get_task(task_id, current_user.company_id)  # 회사 소유 확인
    return service.repository.list_candidates(task_id, current_user.company_id)


@router.post(
    "/{task_id}/candidates", response_model=CandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_candidate(
    task_id: int,
    data: CandidateCreate,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.add_candidate(
        task_id, current_user.company_id,
        shopping_mall_code=data.shopping_mall_code,
        product_url=data.product_url, candidate_title=data.candidate_title,
        brand=data.brand, manufacturer=data.manufacturer,
        model_name=data.model_name, gtin=data.gtin, capacity=data.capacity,
        color_or_scent=data.color_or_scent, options=data.options,
        estimated_price=data.estimated_price,
        estimated_shipping_fee=data.estimated_shipping_fee,
        estimated_delivery_days=data.estimated_delivery_days,
        seller_trust_score=data.seller_trust_score,
        return_allowed=data.return_allowed, added_by=current_user.id,
    )


@router.post(
    "/{task_id}/candidates/{candidate_id}/match-check",
    response_model=CandidateResponse,
)
def run_match_check(
    task_id: int, candidate_id: int, data: MatchCheckRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.run_match_check(
        task_id, candidate_id, current_user.company_id,
        source_attrs=data.source.model_dump(), confirmed_by=current_user.id,
    )


@router.post(
    "/{task_id}/candidates/{candidate_id}/evaluate",
    response_model=EvaluateResponse,
)
def evaluate_and_prepare(
    task_id: int, candidate_id: int, data: EvaluateRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    from decimal import Decimal

    service = PurchaseTaskService(db)
    task, result = service.evaluate_and_prepare(
        task_id, candidate_id, current_user.company_id,
        source_attrs=data.source.model_dump(), evaluated_by=current_user.id,
        additional_shipping_fee=(
            Decimal(str(data.additional_shipping_fee))
            if data.additional_shipping_fee is not None else None
        ),
        return_risk_reserve=(
            Decimal(str(data.return_risk_reserve))
            if data.return_risk_reserve is not None else None
        ),
    )
    return EvaluateResponse(
        task=task, decision=result.decision, reasons=list(result.reasons),
    )


@router.post("/{task_id}/open-payment-page", response_model=PurchaseTaskResponse)
def open_payment_page(
    task_id: int,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.open_payment_page(
        task_id, current_user.company_id, opened_by=current_user.id,
    )


@router.post(
    "/{task_id}/extend-reservation", response_model=PurchaseTaskResponse,
)
def extend_reservation(
    task_id: int, data: ExtendReservationRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.extend_budget_reservation(
        task_id, current_user.company_id, extra_hours=data.extra_hours,
        extended_by=current_user.id,
    )


@router.post("/{task_id}/record-purchase", response_model=PurchaseTaskResponse)
def record_purchase(
    task_id: int, data: RecordPurchaseRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.record_purchase(
        task_id, current_user.company_id,
        shopping_mall_code=data.shopping_mall_code,
        external_order_number=data.external_order_number,
        actual_amount=data.actual_amount,
        actual_shipping_fee=data.actual_shipping_fee,
        purchased_at=data.purchased_at,
        selected_option_note=data.selected_option_note, memo=data.memo,
        recorded_by=current_user.id, idempotency_key=data.idempotency_key,
    )


@router.get("/{task_id}/tracking", response_model=TrackingResponse)
def get_tracking(
    task_id: int,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    service.get_task(task_id, current_user.company_id)
    tracking = service.repository.get_tracking(task_id, current_user.company_id)
    if tracking is None:
        from app.core.exceptions import NotFoundException
        raise NotFoundException("아직 송장 정보가 없습니다.")
    return tracking


@router.post("/{task_id}/tracking", response_model=PurchaseTaskResponse)
def record_tracking(
    task_id: int, data: RecordTrackingRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.record_tracking(
        task_id, current_user.company_id, courier=data.courier,
        courier_confirmed=data.courier_confirmed,
        tracking_number=data.tracking_number, shipped_at=data.shipped_at,
        expected_arrival_at=data.expected_arrival_at,
        is_partial_shipment=data.is_partial_shipment,
        recorded_by=current_user.id,
    )


@router.post("/{task_id}/delivered", response_model=PurchaseTaskResponse)
def mark_delivered(
    task_id: int,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.mark_delivered(
        task_id, current_user.company_id, marked_by=current_user.id,
    )


@router.post("/{task_id}/cancel", response_model=PurchaseTaskResponse)
def request_cancel(
    task_id: int, data: CancelRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.request_cancel(
        task_id, current_user.company_id, data.reason,
        requested_by=current_user.id,
    )


@router.post("/{task_id}/return", response_model=PurchaseTaskResponse)
def request_return(
    task_id: int, data: CancelRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.request_return(
        task_id, current_user.company_id, data.reason,
        requested_by=current_user.id,
    )


@router.post("/{task_id}/refund", response_model=PurchaseTaskResponse)
def record_refund(
    task_id: int, data: RefundRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.record_refund(
        task_id, current_user.company_id, refund_amount=data.refund_amount,
        memo=data.memo, recorded_by=current_user.id,
    )


@router.post("/{task_id}/complete", response_model=PurchaseTaskResponse)
def complete_task(
    task_id: int,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = PurchaseTaskService(db)
    return service.complete_task(
        task_id, current_user.company_id, completed_by=current_user.id,
    )


__all__ = ["router"]
