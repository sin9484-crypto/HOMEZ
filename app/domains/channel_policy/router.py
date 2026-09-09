"""
=========================================================
Homez OS

File : app/domains/channel_policy/router.py

채널 정책 엔진 — API 경계. 신규 Permission 코드를 시딩하지 않는다
(사용자 지시 — "신규 Permission 시딩" 금지) — 기존
listing_wizard_permissions.py의 이미 정의된(아직 시딩되지 않은)
코드를 그대로 재사용한다(ADMIN/SUPER_ADMIN은 오늘과 동일하게 항상
통과, 다른 역할은 향후 실제 시딩 후에만 열린다 — 기존 접근성 유지).
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import AdminGuard
from app.domains.channel_policy.schema import (
    ChannelPolicyEvaluationResponse,
)
from app.domains.channel_policy.schema import (
    CompanyChannelPolicySettingsResponse,
)
from app.domains.channel_policy.schema import ChannelPolicyRuleSummary
from app.domains.channel_policy.schema import EvaluateChannelPolicyRequest
from app.domains.channel_policy.schema import MarginEstimateInput
from app.domains.channel_policy.schema import MarginEstimateResult
from app.domains.channel_policy.schema import (
    UpdateCompanyChannelPolicySettingsRequest,
)
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_ECONOMICS_VIEW,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_EDIT,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_VIEW,
)
from app.domains.user.model import User

router = APIRouter(prefix="/channel-policy", tags=["Channel Policy"])

ChannelPolicyViewGuard = ListingWizardPermissionGuard(LISTING_WIZARD_VIEW)
ChannelPolicyEconomicsGuard = ListingWizardPermissionGuard(
    LISTING_ECONOMICS_VIEW,
)
ChannelPolicyEditGuard = ListingWizardPermissionGuard(LISTING_WIZARD_EDIT)


def get_service(db: Session = Depends(get_db)) -> ChannelPolicyService:

    return ChannelPolicyService(db)


@router.post(
    "/rules/seed-catalog", response_model=list[ChannelPolicyRuleSummary],
)
def seed_channel_policy_rule_catalog(
    current_user: User = Depends(AdminGuard),
    service: ChannelPolicyService = Depends(get_service),
):
    """
    `CHANNEL_POLICY_RULE_CATALOG`(코드에 고정된 실제 공식 근거 기반
    규칙 목록)을 DB에 반영한다.

    Audit(2026-08-21, CTO 후속 지시) — 이 카탈로그는 이제
    `app/desktop/main.py`가 부팅마다 `seed_channel_policy_catalog_
    at_boot()`를 통해 자동·멱등으로도 시딩한다(app/database/seed.py
    ::seed_environment()의 역할·권한 시딩과 동일한 패턴). 이 엔드
    포인트는 그 자동 시딩을 대체하지 않는다 — 서버 재시작 없이
    카탈로그를 즉시 갱신하고 싶을 때(예: 새 공식 근거를 확인해
    `rule_catalog.py`를 배포 사이에 갱신한 경우) SUPER_ADMIN이
    수동으로 즉시 반영하는 보조 경로다. 또한 `evaluate_and_record()`
    는 이 채널에 규칙 행이 하나도 없으면(카탈로그 미시딩) CHANNEL_
    ELIGIBLE이 아니라 CHANNEL_DATA_REQUIRED로 fail-closed 처리한다
    — 시딩이 어떤 이유로든 누락돼도 정책 검사가 조용히 무력화되지
    않는다.
    """

    return service.seed_rule_catalog(actor_user_id=current_user.id)


@router.post("/evaluate", response_model=ChannelPolicyEvaluationResponse)
def evaluate_channel_policy(
    data: EvaluateChannelPolicyRequest,
    current_user: User = Depends(ChannelPolicyViewGuard),
    service: ChannelPolicyService = Depends(get_service),
):
    """채널 선택 시 정책 프로필을 자동 활성화하는 지점 — 프런트엔드가
    채널을 선택할 때마다 이 엔드포인트를 호출해 최신 판정을 받는다."""

    return service.evaluate_and_record(
        company_id=current_user.company_id,
        product_candidate_id=data.product_candidate_id,
        channel=data.channel,
        category_hint_override=data.category_hint_override,
        product_attributes=data.product_attributes,
        confirmed_evidence_rule_codes=data.confirmed_evidence_rule_codes,
        evaluated_by=current_user.id,
        selection_id=data.selection_id,
    )


@router.get(
    "/status/{product_candidate_id}/{channel}",
    response_model=ChannelPolicyEvaluationResponse | None,
)
def get_channel_policy_status(
    product_candidate_id: int,
    channel: str,
    current_user: User = Depends(ChannelPolicyViewGuard),
    service: ChannelPolicyService = Depends(get_service),
):
    """재평가 없이 마지막 저장된 판정만 조회한다(자동 갱신 토글이
    꺼져 있을 때). 정책 버전이 바뀌었으면 재평가 대신
    CHANNEL_POLICY_STALE로 표시된다."""

    return service.get_current_status(
        current_user.company_id, product_candidate_id, channel,
    )


@router.get("/settings", response_model=CompanyChannelPolicySettingsResponse | None)
def get_company_channel_policy_settings(
    current_user: User = Depends(ChannelPolicyViewGuard),
    service: ChannelPolicyService = Depends(get_service),
):

    return service.get_or_default_settings(current_user.company_id)


@router.put("/settings", response_model=CompanyChannelPolicySettingsResponse)
def update_company_channel_policy_settings(
    data: UpdateCompanyChannelPolicySettingsRequest,
    current_user: User = Depends(AdminGuard),
    service: ChannelPolicyService = Depends(get_service),
):
    """회사 수익성·구매한도 기준 — 정책 적합성과 별개 축이므로
    ADMIN 권한으로 관리한다(다른 회사 전역 설정 화면과 동일한
    보호 수준)."""

    return service.upsert_settings(
        current_user.company_id, current_user.id, data,
    )


@router.post("/margin-estimate", response_model=MarginEstimateResult)
def estimate_margin(
    data: MarginEstimateInput,
    current_user: User = Depends(ChannelPolicyEconomicsGuard),
    service: ChannelPolicyService = Depends(get_service),
):
    """상품 선별 단계의 잠정 마진 추정 — listing_wizard 6단계
    (확정 채널·방식 선택 이후)와는 별개다."""

    return service.estimate_margin(current_user.company_id, data)


__all__ = ["router"]
