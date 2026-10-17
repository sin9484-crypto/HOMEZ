"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_reapproval_service.py

2026-10-17 — 연동 안정화 A3: 위저드 "최종 승인"(연쇄로 만들어지는 제출 승인, 유효 1시간) 만료 뒤 다시 받기.

배경: 8단계 승인 직후 만들어지는 최종 승인은 1시간만 유효한데, 화면에 남은 시간이 보이지 않았고 만료되면 다시 받는 방법이 없었다
(2026-10-17 11시간 뒤 전송을 시도해 `VALID_APPROVAL_REQUIRED`로 막혔다).

이 서비스는 **새 권한을 만들지 않는다.** 사용자가 8단계에서 승인한 내용이 그대로일 때(`approval_fingerprint` 일치)만, 만료된 최종 승인을
같은 내용으로 다시 1시간 받게 한다. 호출은 사용자가 비밀번호를 다시 확인한 요청에서만 이뤄진다(라우터).
- 제출이 아직 쿠팡에 가지 않았고(PENDING, 외부 번호·요청 지문·상관 ID 없음) 마지막 최종 승인이 APPROVED이지만 만료된 경우만 허용한다.
- 위저드 내용이 승인 이후 바뀌었거나, 승인이 취소·거절됐거나 아직 유효하면 거부한다.
- 외부 호출은 없다.
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.constants import ApprovalStatus
from app.domains.marketplace_listing.constants import SubmissionStatus
from app.domains.marketplace_listing.listing_wizard_live_service import ListingWizardLiveService
from app.domains.marketplace_listing.schema import MarketplaceSubmissionApprovalDecisionRequest
from app.domains.marketplace_listing.schema import MarketplaceSubmissionApprovalRequestRequest

# 위저드 일괄 제출의 최종 승인 유효 시간과 같다(`listing_wizard_submission._APPROVAL_TTL`).
REAPPROVAL_TTL = timedelta(hours=1)


def _decimal(value) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal("0")


class ListingWizardReapprovalService:

    def __init__(self, db: Session, *, now_factory=None):
        self.db = db
        self._now = now_factory or datetime.utcnow
        self.live = ListingWizardLiveService(db)
        self.approvals = ApprovalService(db)

    def status(self, wizard_id: int, submission_id: int, company_id: int) -> dict:
        wizard, submission, listing, selection = self.live.submission_context(wizard_id, submission_id, company_id)
        now = self._now()
        blockers: list[str] = []
        if (
            submission.status != SubmissionStatus.PENDING or submission.external_submission_ref
            or submission.request_fingerprint or submission.correlation_id
        ):
            blockers.append("SUBMISSION_ALREADY_SENT_OR_CLAIMED")
        if not self.live._approval_payload_unchanged(wizard, company_id):
            blockers.append("WIZARD_APPROVAL_CHANGED")
        latest = self.approvals.repository.get_latest_approval(listing.id, selection.id, company_id)
        valid = self.approvals.current_valid_approval(listing.id, selection.id, company_id, now=now)
        expires_at = latest.expires_at if latest is not None else None
        if valid is not None:
            state = "VALID"
        elif latest is None:
            state = "NONE"
        elif latest.status == ApprovalStatus.APPROVED and expires_at is not None and expires_at <= now:
            state = "EXPIRED"
        else:
            state = "OTHER"  # 취소·거절·대기·내용 변경으로 무효 — 되살리지 않는다
        minutes_left = max(int((expires_at - now).total_seconds() // 60), 0) if (valid is not None and expires_at) else 0
        return {
            "state": state,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "minutes_left": minutes_left,
            "can_reapprove": state == "EXPIRED" and not blockers,
            "blockers": blockers,
        }

    def reapprove(self, wizard_id: int, submission_id: int, company_id: int, user_id: int) -> dict:
        current = self.status(wizard_id, submission_id, company_id)
        if current["state"] == "VALID":
            raise ConflictException("최종 승인이 아직 유효합니다 — 다시 받을 필요가 없습니다.")
        if not current["can_reapprove"]:
            raise ConflictException(
                "최종 승인을 다시 받을 수 없습니다: " + (", ".join(current["blockers"]) or current["state"]),
            )
        wizard, submission, listing, selection = self.live.submission_context(wizard_id, submission_id, company_id)
        economics = next(
            (
                item for item in json.loads(wizard.economics_input_json or "[]")
                if item.get("marketplace_account_id") == submission.marketplace_account_id
            ),
            None,
        )
        if economics is None:
            raise BadRequestException("가격·마진 입력이 없어 최종 승인을 다시 받을 수 없습니다.")
        now = self._now()
        key = f"wizard-{wizard.id}-submission-{submission.id}-reapprove-{int(now.timestamp())}"
        approval, _dup = self.approvals.request_approval(
            MarketplaceSubmissionApprovalRequestRequest(
                listing_id=listing.id, selection_id=selection.id, planned_quantity=1,
                unit_price=_decimal(economics.get("sale_price")),
                unit_cost_of_goods=_decimal(economics.get("cost_of_goods")),
                expected_logistics_cost=_decimal(economics.get("shipping_cost")) + _decimal(economics.get("packaging_cost")),
                idempotency_key=f"{key}-request",
            ),
            requested_by=user_id, company_id=company_id,
        )
        approval, _dup = self.approvals.approve(
            approval.id,
            MarketplaceSubmissionApprovalDecisionRequest(
                expires_at=now + REAPPROVAL_TTL,
                reason=f"listing_wizard#{wizard.id} 최종 승인 만료 후 사용자가 비밀번호를 다시 확인해 재승인",
                idempotency_key=f"{key}-decide",
            ),
            approved_by=user_id, company_id=company_id,
        )
        return {
            "approval_id": approval.id, "status": approval.status,
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        }
