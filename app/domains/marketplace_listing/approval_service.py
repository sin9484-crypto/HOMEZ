"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/approval_service.py

제출 승인 — 요청과 결정(승인/거절/취소)은 항상 서로 다른 API 호출이다.
같은 요청에서 승인+제출을 함께 하지 않는다.

클라이언트는 approved_by/rejected_by/revoked_by를 절대 지정할 수
없다 — 이 값들은 오직 각 액션 메서드의 파라미터(admin_guard로 인증된
호출자 id)에서만 채워진다. app/domains/marketplace_listing/schema.py의
어떤 승인 관련 요청 스키마에도 그런 필드가 없다.

app/domains/marketplace_listing/eligibility_service.py와 동일한
철학 — 읽기 시점 판단, append-only, 자동으로 관대해지지 않는다
(만료·fingerprint 불일치는 fail-closed).

2026-08-01 CTO 3차 지적 반영 — 이 파일의 모든 메서드가 company_id를
필수 인자로 받는다. listing_id/selection_id/approval_id는 항상
company 스코프로 조회하며, 다른 회사의 객체를 지정하면 존재하지 않는
것과 동일한 404로 거부된다.
=========================================================
"""

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.marketplace_listing.constants import ApprovalStatus
from app.domains.marketplace_listing.constants import ListingStatus
from app.domains.marketplace_listing.fingerprint import (
    compute_listing_fingerprint,
)
from app.domains.marketplace_listing.fingerprint import (
    compute_selection_fingerprint,
)
from app.domains.marketplace_listing.model import MarketplaceSubmissionApproval
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalDecisionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalRejectionRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionApprovalRequestRequest,
)

_LISTING_NOT_FOUND = "MarketplaceListing을 찾을 수 없습니다."
_SELECTION_NOT_FOUND = "MarketplaceFulfillmentSelection을 찾을 수 없습니다."
_APPROVAL_NOT_FOUND = "MarketplaceSubmissionApproval을 찾을 수 없습니다."


class ApprovalService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = MarketplaceListingRepository(db)

    def request_approval(
        self, data: MarketplaceSubmissionApprovalRequestRequest,
        requested_by: int, company_id: int,
    ) -> tuple[MarketplaceSubmissionApproval, bool]:

        existing = self.repository.get_approval_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing, True

        listing = self.repository.get_listing_for_company(
            data.listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException(_LISTING_NOT_FOUND)

        selection = self.repository.get_selection_for_company(
            data.selection_id, company_id,
        )
        if selection is None:
            raise NotFoundException(_SELECTION_NOT_FOUND)
        if selection.listing_id != listing.id:
            raise BadRequestException(
                "selection이 이 listing에 속하지 않습니다.",
            )
        if selection.status != "SELECTED":
            raise BadRequestException(
                "SUPERSEDED된 선택에는 승인을 요청할 수 없습니다 — "
                "현재 선택된 방식으로 다시 요청하세요.",
            )

        capability = self.repository.get_capability(selection.capability_id)
        if capability is None:
            raise NotFoundException(
                "MarketplaceFulfillmentCapability를 찾을 수 없습니다.",
            )

        approval = MarketplaceSubmissionApproval(
            company_id=company_id,
            listing_id=listing.id,
            selection_id=selection.id,
            status=ApprovalStatus.PENDING,
            listing_fingerprint=compute_listing_fingerprint(listing),
            selection_fingerprint=compute_selection_fingerprint(selection),
            capability_policy_version=capability.policy_version,
            planned_quantity=data.planned_quantity,
            unit_price=data.unit_price,
            unit_cost_of_goods=data.unit_cost_of_goods,
            expected_logistics_cost=data.expected_logistics_cost,
            requested_by=requested_by,
            idempotency_key=data.idempotency_key,
        )

        try:
            approval = self.repository.add_approval_no_commit(approval)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_approval_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        return approval, False

    def _fresh_check(
        self, pending: MarketplaceSubmissionApproval, company_id: int,
    ) -> None:
        """
        approve()/reject() 시점에 요청 당시 스냅샷된 fingerprint/
        정책버전이 "지금"과 여전히 같은지 재확인한다 — 요청과 결정
        사이에 가격·수량·필수입력·정책이 바뀌었으면 이 요청 자체가
        이미 낡은 것이므로 거부한다(재요청 필요).
        """

        listing = self.repository.get_listing_for_company(
            pending.listing_id, company_id,
        )
        selection = self.repository.get_selection_for_company(
            pending.selection_id, company_id,
        )

        if listing is None or selection is None:
            raise ConflictException(
                "승인 대상 Listing/Selection을 더 이상 찾을 수 없습니다.",
            )

        capability = self.repository.get_capability(selection.capability_id)

        if (
            compute_listing_fingerprint(listing) != pending.listing_fingerprint
            or compute_selection_fingerprint(selection)
            != pending.selection_fingerprint
            or capability is None
            or capability.policy_version != pending.capability_policy_version
        ):
            raise ConflictException(
                "요청 이후 Listing/Selection/정책 상태가 변경되었습니다 "
                "— 승인 요청을 다시 생성하세요.",
            )

    def approve(
        self, approval_id: int,
        data: MarketplaceSubmissionApprovalDecisionRequest,
        approved_by: int, company_id: int,
    ) -> tuple[MarketplaceSubmissionApproval, bool]:

        existing = self.repository.get_approval_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing, True

        pending = self.repository.get_approval_for_company(
            approval_id, company_id,
        )
        if pending is None:
            raise NotFoundException(_APPROVAL_NOT_FOUND)

        latest = self.repository.get_latest_approval(
            pending.listing_id, pending.selection_id, company_id,
        )
        if latest is None or latest.id != pending.id:
            raise ConflictException(
                "이 승인 요청은 더 이상 최신 상태가 아닙니다 — 새 요청을 "
                "다시 생성하세요.",
            )
        if pending.status != ApprovalStatus.PENDING:
            raise BadRequestException(
                "PENDING 상태의 승인 요청만 승인할 수 있습니다. "
                f"(현재: {pending.status})",
            )

        self._fresh_check(pending, company_id)

        approval = MarketplaceSubmissionApproval(
            company_id=company_id,
            listing_id=pending.listing_id,
            selection_id=pending.selection_id,
            status=ApprovalStatus.APPROVED,
            listing_fingerprint=pending.listing_fingerprint,
            selection_fingerprint=pending.selection_fingerprint,
            capability_policy_version=pending.capability_policy_version,
            planned_quantity=pending.planned_quantity,
            unit_price=pending.unit_price,
            unit_cost_of_goods=pending.unit_cost_of_goods,
            expected_logistics_cost=pending.expected_logistics_cost,
            requested_by=pending.requested_by,
            requested_at=pending.requested_at,
            # approved_by는 오직 이 파라미터(admin_guard current_user)
            # 에서만 온다 — data(요청 바디)에는 그런 필드가 없다.
            approved_by=approved_by,
            approved_at=datetime.utcnow(),
            expires_at=data.expires_at,
            reason=data.reason,
            idempotency_key=data.idempotency_key,
        )

        try:
            approval = self.repository.add_approval_no_commit(approval)

            # 2026-08-01 Gate 5(CTO 2차 지적) — 순수 표시용 전이(rowcount
            # 무시). 실제 제출 인가는 여전히 current_valid_approval()의
            # fingerprint/만료 재확인뿐이다.
            self.repository.update_listing_status_conditional(
                pending.listing_id, company_id,
                (ListingStatus.READY,), ListingStatus.APPROVED,
            )
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_approval_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        return approval, False

    def _decide_terminal(
        self, approval_id: int,
        data: MarketplaceSubmissionApprovalRejectionRequest,
        actor_id: int, new_status: str, company_id: int,
    ) -> tuple[MarketplaceSubmissionApproval, bool]:

        existing = self.repository.get_approval_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing, True

        if not data.reason.strip():
            raise BadRequestException("reason은 비어있을 수 없습니다.")

        current = self.repository.get_approval_for_company(
            approval_id, company_id,
        )
        if current is None:
            raise NotFoundException(_APPROVAL_NOT_FOUND)

        latest = self.repository.get_latest_approval(
            current.listing_id, current.selection_id, company_id,
        )
        if latest is None or latest.id != current.id:
            raise ConflictException(
                "이 승인 건은 더 이상 최신 상태가 아닙니다.",
            )

        allowed_from = (
            (ApprovalStatus.PENDING,)
            if new_status == ApprovalStatus.REJECTED
            else (ApprovalStatus.APPROVED,)
        )
        if current.status not in allowed_from:
            raise BadRequestException(
                f"{allowed_from} 상태에서만 {new_status}로 전이할 수 "
                f"있습니다. (현재: {current.status})",
            )

        approval = MarketplaceSubmissionApproval(
            company_id=company_id,
            listing_id=current.listing_id,
            selection_id=current.selection_id,
            status=new_status,
            listing_fingerprint=current.listing_fingerprint,
            selection_fingerprint=current.selection_fingerprint,
            capability_policy_version=current.capability_policy_version,
            planned_quantity=current.planned_quantity,
            unit_price=current.unit_price,
            unit_cost_of_goods=current.unit_cost_of_goods,
            expected_logistics_cost=current.expected_logistics_cost,
            requested_by=current.requested_by,
            requested_at=current.requested_at,
            approved_by=(
                current.approved_by
                if new_status == ApprovalStatus.REVOKED
                else None
            ),
            approved_at=(
                current.approved_at
                if new_status == ApprovalStatus.REVOKED
                else None
            ),
            expires_at=None,
            reason=data.reason,
            idempotency_key=data.idempotency_key,
        )

        try:
            approval = self.repository.add_approval_no_commit(approval)

            # 2026-08-01 Gate 5(CTO 2차 지적) — 순수 표시용 전이(rowcount
            # 무시). REJECTED는 승인 대기(READY) 상태에서만 나오고,
            # REVOKED는 이미 APPROVED였던 것을 다시 READY로 되돌린다
            # (재승인 사이클을 다시 밟게 한다).
            if new_status == ApprovalStatus.REJECTED:
                self.repository.update_listing_status_conditional(
                    current.listing_id, company_id,
                    (ListingStatus.READY,), ListingStatus.REJECTED,
                )
            elif new_status == ApprovalStatus.REVOKED:
                self.repository.update_listing_status_conditional(
                    current.listing_id, company_id,
                    (ListingStatus.APPROVED,), ListingStatus.READY,
                )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_approval_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        return approval, False

    def reject(
        self, approval_id: int,
        data: MarketplaceSubmissionApprovalRejectionRequest,
        rejected_by: int, company_id: int,
    ) -> tuple[MarketplaceSubmissionApproval, bool]:

        return self._decide_terminal(
            approval_id, data, rejected_by, ApprovalStatus.REJECTED,
            company_id,
        )

    def revoke(
        self, approval_id: int,
        data: MarketplaceSubmissionApprovalRejectionRequest,
        revoked_by: int, company_id: int,
    ) -> tuple[MarketplaceSubmissionApproval, bool]:

        return self._decide_terminal(
            approval_id, data, revoked_by, ApprovalStatus.REVOKED,
            company_id,
        )

    def current_valid_approval(
        self, listing_id: int, selection_id: int, company_id: int,
        now: datetime | None = None,
    ) -> MarketplaceSubmissionApproval | None:
        """
        지금 이 순간 제출에 쓸 수 있는 유효한 승인이 있는지 판단한다.
        fail-closed: 최신 행이 APPROVED가 아니거나, 만료됐거나,
        Listing/Selection/정책이 승인 당시와 달라졌으면 전부 무효
        (None)로 취급한다 — UNKNOWN을 절대 허용으로 승격하지 않는다.
        """

        now = now or datetime.utcnow()

        latest = self.repository.get_latest_approval(
            listing_id, selection_id, company_id,
        )
        if latest is None or latest.status != ApprovalStatus.APPROVED:
            return None

        if latest.expires_at is not None and latest.expires_at <= now:
            return None

        listing = self.repository.get_listing_for_company(
            listing_id, company_id,
        )
        selection = self.repository.get_selection_for_company(
            selection_id, company_id,
        )
        if listing is None or selection is None:
            return None

        capability = self.repository.get_capability(selection.capability_id)
        if capability is None:
            return None

        if compute_listing_fingerprint(listing) != latest.listing_fingerprint:
            return None
        if (
            compute_selection_fingerprint(selection)
            != latest.selection_fingerprint
        ):
            return None
        if capability.policy_version != latest.capability_policy_version:
            return None

        return latest

    def history(
        self, listing_id: int, selection_id: int, company_id: int,
    ) -> list[MarketplaceSubmissionApproval]:

        return self.repository.list_approval_history(
            listing_id, selection_id, company_id,
        )


__all__ = [
    "ApprovalService",
]
