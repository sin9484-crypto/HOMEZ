"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_submission.py

Gate I(2026-08-08) — 9/10단계(실행/결과) 제출 오케스트레이션. 이
Gate에서 새로 만든 로직은 "채널별로 아래 체인을 순서대로 호출한다"는
조율뿐이다 — 실제 Listing/Selection/Approval/Submission 생성·검증
로직은 전부 기존, 수정하지 않은 서비스를 그대로 호출한다:
`MarketplaceListingService.create_listing()`/`select_fulfillment_
mode()`, `ApprovalService.request_approval()`/`approve()`,
`SubmissionService.submit()`.

승인 게이트 이중 구조: 이 함수는 Gate I 자신의 위저드 승인
(SuperAdminGuard + recent-auth + 단발성 nonce, listing_wizard_
approval.py)이 **이미 통과한 뒤에만** 호출된다 — 그 시점에 이미
검증된 사람(`approved_by`)의 신원을 그대로 근거로 삼아, 채널별
ApprovalService.request_approval()→approve()를 내부적으로 순서대로
호출한다. 클라이언트가 boolean으로 승인을 자칭하는 경로가 아니다 —
위저드 승인 자체가 이미 서버 DB에 기록된 SUPER_ADMIN 결정이고, 이
오케스트레이션은 그 결정을 하위 도메인의 기존 계약(승인 요청/승인은
별도 API라는 원칙)에 맞게 그대로 반영할 뿐이다.

채널 하나의 실패가 다른 채널 처리를 막지 않는다(부분 성공 지원) —
각 채널을 개별 try/except로 격리한다. 실패한 채널만 다시 시도하려면
`account_ids` 필터로 대상만 좁혀 재호출한다(이미 성공한 단계는 각
계층의 idempotency_key 덕분에 재실행해도 안전하게 기존 행을
반환한다).
=========================================================
"""

import json
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentSelectionCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalDecisionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalRequestRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionRequest,
)
from app.domains.marketplace_listing.service import (
    MarketplaceListingService,
)
from app.domains.marketplace_listing.submission_service import (
    SubmissionService,
)

# 승인 만료 — 위저드 일괄 제출은 승인 직후 즉시 이어지므로 짧게 둔다
# (사람이 오래 붙잡아 둘 이유가 없는 자동 연쇄 승인이기 때문).
_APPROVAL_TTL = timedelta(hours=1)


@dataclass
class WizardChannelSubmissionResult:

    marketplace_account_id: int
    listing_id: int | None
    status: str
    error_reason: str | None


def submit_wizard_channels(
    wizard: ListingWizard,
    db: Session,
    company_id: int,
    approved_by: int,
    account_ids: list[int] | None = None,
    retry_key_suffix: str | None = None,
) -> list[WizardChannelSubmissionResult]:

    listing_service = MarketplaceListingService(db)
    approval_service = ApprovalService(db)
    submission_service = SubmissionService(db)
    channel_policy_service = ChannelPolicyService(db)

    channel_selections = json.loads(wizard.channel_selections_json or "[]")
    economics_by_account = {
        item["marketplace_account_id"]: item
        for item in json.loads(wizard.economics_input_json or "[]")
    }

    if account_ids is not None:
        target_ids = set(account_ids)
        channel_selections = [
            entry for entry in channel_selections
            if entry.get("marketplace_account_id") in target_ids
        ]

    results: list[WizardChannelSubmissionResult] = []

    for entry in channel_selections:
        account_id = entry.get("marketplace_account_id")
        idempotency_base = f"wizard-{wizard.id}-account-{account_id}"

        try:
            result = _submit_one_channel(
                wizard, entry, economics_by_account.get(account_id),
                listing_service, approval_service, submission_service,
                channel_policy_service,
                company_id, approved_by, idempotency_base,
                retry_key_suffix,
            )
        except AppException as exc:
            result = WizardChannelSubmissionResult(
                marketplace_account_id=account_id, listing_id=None,
                status="FAILED", error_reason=str(exc.detail),
            )

        results.append(result)

    return results


def _submit_one_channel(
    wizard: ListingWizard,
    entry: dict,
    economics: dict | None,
    listing_service: MarketplaceListingService,
    approval_service: ApprovalService,
    submission_service: SubmissionService,
    channel_policy_service: ChannelPolicyService,
    company_id: int,
    approved_by: int,
    idempotency_base: str,
    retry_key_suffix: str | None = None,
) -> WizardChannelSubmissionResult:

    account_id = entry.get("marketplace_account_id")
    fulfillment_mode = entry.get("fulfillment_mode")
    required_fields = entry.get("required_fields", {})

    if economics is None:
        return WizardChannelSubmissionResult(
            marketplace_account_id=account_id, listing_id=None,
            status="FAILED",
            error_reason="가격·마진 입력이 없어 제출할 수 없습니다.",
        )

    listing, _dup = listing_service.create_listing(
        MarketplaceListingCreateRequest(
            product_candidate_id=wizard.product_candidate_id,
            marketplace_account_id=account_id,
        ),
        company_id,
    )

    selection, _dup, _events = listing_service.select_fulfillment_mode(
        MarketplaceFulfillmentSelectionCreateRequest(
            listing_id=listing.id,
            fulfillment_mode=fulfillment_mode,
            required_fields=required_fields,
            idempotency_key=f"{idempotency_base}-selection",
        ),
        selected_by=approved_by,
        company_id=company_id,
    )

    # CA-1(2026-08-21) — 위저드 일괄 제출도 submission_service.submit()
    # 을 그대로 호출하므로(아래) 동일한 채널 정책 게이트를 통과해야
    # 한다. 위저드는 별도 "지금 검사" 버튼이 없는 일괄 흐름이라, 여기서
    # 그 selection이 실제로 만들어진 직후 자동으로 평가를 기록한다 —
    # 이 평가의 지문은 방금 만든 이 selection을 그대로 반영하므로,
    # 곧바로 이어지는 submit() 호출에서 지문 불일치가 나지 않는다.
    #
    # Audit(2026-08-21, CTO 후속 지시) — 이전에는 이 호출이 무조건
    # product_attributes={}로 평가를 기록했다. 실제 규칙 카탈로그가
    # 시딩된 뒤(이제 부팅마다 자동 시딩된다 — app/desktop/main.py)
    # COUPANG처럼 category_scope=["ALL"] 구조화 필드 규칙이 활성인
    # 채널에서는 이 빈 평가가 항상 CHANNEL_DATA_REQUIRED로 나와
    # 제출을 막는다 — 이는 "정직한 fail-closed"이지 결함이 아니다
    # (없는 데이터를 추정해 통과시키지 않는다). 다만 예전 코드는 이
    # 빈 평가를 무조건 새로 기록해, 운영자가 위저드 진행 중 이미
    # 실제 값으로 검사를 마쳤더라도 그 결과를 덮어쓸 수 있는 여지가
    # 있었다 — 이제 entry(위저드 채널별 선택 payload)에 실제 값이
    # 있으면 그 값을 그대로 쓰고, 없으면(현재 UI 미구현) 정직하게
    # 빈 값으로 평가한다. 이 함수는 이 데이터를 추정하지 않는다 —
    # 호출자(향후 위저드 UI 또는 이 함수를 호출하는 테스트)가 실제로
    # 넘긴 값만 사용한다.
    account = listing_service.repository.get_account_for_company(
        account_id, company_id,
    )
    channel = listing_service.repository.get_channel(account.channel_id)
    channel_policy_service.evaluate_and_record(
        company_id=company_id,
        product_candidate_id=wizard.product_candidate_id,
        channel=channel.code,
        category_hint_override=None,
        product_attributes=entry.get("channel_policy_attributes", {}),
        confirmed_evidence_rule_codes=entry.get(
            "channel_policy_confirmed_evidence_rule_codes", [],
        ),
        evaluated_by=approved_by,
        selection_id=selection.id,
    )

    approval, _dup = approval_service.request_approval(
        MarketplaceSubmissionApprovalRequestRequest(
            listing_id=listing.id,
            selection_id=selection.id,
            planned_quantity=1,
            unit_price=Decimal(str(economics["sale_price"])),
            unit_cost_of_goods=Decimal(str(economics["cost_of_goods"])),
            expected_logistics_cost=(
                Decimal(str(economics.get("shipping_cost", "0")))
                + Decimal(str(economics.get("packaging_cost", "0")))
            ),
            idempotency_key=(
                f"{idempotency_base}-approval-request"
                + (f"-{retry_key_suffix}" if retry_key_suffix else "")
            ),
        ),
        requested_by=approved_by,
        company_id=company_id,
    )

    approval, _dup = approval_service.approve(
        approval.id,
        MarketplaceSubmissionApprovalDecisionRequest(
            expires_at=datetime.utcnow() + _APPROVAL_TTL,
            reason=f"listing_wizard#{wizard.id} 승인에 따른 연쇄 승인",
            idempotency_key=(
                f"{idempotency_base}-approval-decide"
                + (f"-{retry_key_suffix}" if retry_key_suffix else "")
            ),
        ),
        approved_by=approved_by,
        company_id=company_id,
    )

    submission = submission_service.submit(
        MarketplaceSubmissionRequest(
            listing_id=listing.id,
            selection_id=selection.id,
            idempotency_key=(
                f"{idempotency_base}-submission"
                + (f"-{retry_key_suffix}" if retry_key_suffix else "")
            ),
        ),
        company_id=company_id,
    )

    return WizardChannelSubmissionResult(
        marketplace_account_id=account_id,
        listing_id=listing.id,
        status=submission.status,
        error_reason=submission.error_reason,
    )


__all__ = ["submit_wizard_channels", "WizardChannelSubmissionResult"]
