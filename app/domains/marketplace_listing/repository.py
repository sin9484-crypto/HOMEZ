"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/repository.py

쓰기 메서드는 commit하지 않고 flush만 수행한다(*_no_commit). Transaction
경계(commit/rollback)는 Service가 소유한다 — app/domains/coupang과
동일한 패턴.

2026-08-01 CTO 3차 지적 반영 — 회사(테넌트) 소유권 경계: Account/
Draft/Listing/Selection/Approval/Eligibility/Submission의 모든
단건 조회·목록 조회·조건부 UPDATE가 company_id를 필수 인자로 받고
WHERE 절에 포함한다. "조회만 회사 범위이고 UPDATE는 id만 쓰는"
절반짜리 수정을 하지 않는다 — 이 파일에 company_id 없이 id만으로
단건을 조회·변경하는 메서드는 하나도 남기지 않았다.

MarketplaceChannel/MarketplaceFulfillmentCapability는 예외다 — 전역
플랫폼 설정이므로 company_id로 범위를 좁히지 않는다(모든 회사가 같은
값을 본다).
=========================================================
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.marketplace_listing.constants import SelectionStatus
from app.domains.marketplace_listing.constants import SubmissionStatus
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentCapability,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentEligibility,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentSelection,
)
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.marketplace_listing.model import MarketplaceListingDraft
from app.domains.marketplace_listing.model import (
    MarketplaceListingStatusEvent,
)
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.marketplace_listing.model import (
    MarketplaceSubmissionApproval,
)
from app.domains.product_candidate.model import ProductCandidate


class MarketplaceListingRepository:

    def __init__(self, db: Session):

        self.db = db

    # --------------------------------------------------
    # MarketplaceChannel (전역 — company_id 없음)
    # --------------------------------------------------

    def get_channel(self, channel_id: int) -> MarketplaceChannel | None:

        return (
            self.db.query(MarketplaceChannel)
            .filter(MarketplaceChannel.id == channel_id)
            .first()
        )

    def get_channel_by_code(self, code: str) -> MarketplaceChannel | None:

        return (
            self.db.query(MarketplaceChannel)
            .filter(MarketplaceChannel.code == code)
            .first()
        )

    def list_channels(self) -> list[MarketplaceChannel]:

        return (
            self.db.query(MarketplaceChannel)
            .order_by(MarketplaceChannel.id.asc())
            .all()
        )

    def add_channel_no_commit(
        self, channel: MarketplaceChannel,
    ) -> MarketplaceChannel:

        self.db.add(channel)
        self.db.flush()

        return channel

    # --------------------------------------------------
    # MarketplaceAccount (company_id 소유권의 Source of Truth)
    # --------------------------------------------------

    def get_account_for_company(
        self, account_id: int, company_id: int,
    ) -> MarketplaceAccount | None:
        """
        모든 단건 조회의 유일한 진입점이다 — company_id 없이 id만으로
        조회하는 메서드는 이 파일에 존재하지 않는다.
        """

        return (
            self.db.query(MarketplaceAccount)
            .filter(MarketplaceAccount.id == account_id)
            .filter(MarketplaceAccount.company_id == company_id)
            .first()
        )

    def get_account_by_company_channel_code(
        self, company_id: int, channel_id: int, account_code: str,
    ) -> MarketplaceAccount | None:

        return (
            self.db.query(MarketplaceAccount)
            .filter(MarketplaceAccount.company_id == company_id)
            .filter(MarketplaceAccount.channel_id == channel_id)
            .filter(MarketplaceAccount.account_code == account_code)
            .first()
        )

    def list_accounts_for_company_channel(
        self, company_id: int, channel_id: int,
    ) -> list[MarketplaceAccount]:

        return (
            self.db.query(MarketplaceAccount)
            .filter(MarketplaceAccount.company_id == company_id)
            .filter(MarketplaceAccount.channel_id == channel_id)
            .order_by(MarketplaceAccount.id.asc())
            .all()
        )

    def add_account_no_commit(
        self, account: MarketplaceAccount,
    ) -> MarketplaceAccount:

        self.db.add(account)
        self.db.flush()

        return account

    def count_active_listings_for_account(
        self, account_id: int, company_id: int,
    ) -> int:
        """
        고아 방지 — 이 계정을 참조하는 Listing이 몇 건인지 센다(현재
        이 Domain에는 Listing에 대한 "종결" 상태 개념이 없으므로,
        참조가 하나라도 있으면 비활성화를 차단하는 보수적 기준을
        쓴다).
        """

        return (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.marketplace_account_id == account_id)
            .filter(MarketplaceListing.company_id == company_id)
            .count()
        )

    def deactivate_account_conditional(
        self, account_id: int, company_id: int,
    ) -> int:

        stmt = (
            update(MarketplaceAccount)
            .where(MarketplaceAccount.id == account_id)
            .where(MarketplaceAccount.company_id == company_id)
            .where(MarketplaceAccount.is_active.is_(True))
            .values(is_active=False)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # MarketplaceFulfillmentCapability (전역 — company_id 없음)
    # --------------------------------------------------

    def get_capability(
        self, capability_id: int,
    ) -> MarketplaceFulfillmentCapability | None:

        return (
            self.db.query(MarketplaceFulfillmentCapability)
            .filter(MarketplaceFulfillmentCapability.id == capability_id)
            .first()
        )

    def get_capability_for_channel_mode(
        self, channel_id: int, fulfillment_mode: str,
    ) -> MarketplaceFulfillmentCapability | None:

        return (
            self.db.query(MarketplaceFulfillmentCapability)
            .filter(MarketplaceFulfillmentCapability.channel_id == channel_id)
            .filter(
                MarketplaceFulfillmentCapability.fulfillment_mode
                == fulfillment_mode,
            )
            .first()
        )

    def list_capabilities_for_channel(
        self, channel_id: int,
    ) -> list[MarketplaceFulfillmentCapability]:

        return (
            self.db.query(MarketplaceFulfillmentCapability)
            .filter(MarketplaceFulfillmentCapability.channel_id == channel_id)
            .order_by(MarketplaceFulfillmentCapability.id.asc())
            .all()
        )

    def add_capability_no_commit(
        self, capability: MarketplaceFulfillmentCapability,
    ) -> MarketplaceFulfillmentCapability:

        self.db.add(capability)
        self.db.flush()

        return capability

    def verify_capability_conditional(
        self,
        capability_id: int,
        expected_statuses: tuple[str, ...],
        verified_at: datetime,
    ) -> int:

        stmt = (
            update(MarketplaceFulfillmentCapability)
            .where(MarketplaceFulfillmentCapability.id == capability_id)
            .where(
                MarketplaceFulfillmentCapability.status.in_(
                    expected_statuses,
                ),
            )
            .values(status="VERIFIED", verified_at=verified_at)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # MarketplaceListingDraft
    # --------------------------------------------------

    def get_draft_for_company(
        self, draft_id: int, company_id: int,
    ) -> MarketplaceListingDraft | None:

        return (
            self.db.query(MarketplaceListingDraft)
            .filter(MarketplaceListingDraft.id == draft_id)
            .filter(MarketplaceListingDraft.company_id == company_id)
            .first()
        )

    def get_draft_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> MarketplaceListingDraft | None:

        return (
            self.db.query(MarketplaceListingDraft)
            .filter(MarketplaceListingDraft.company_id == company_id)
            .filter(
                MarketplaceListingDraft.idempotency_key == idempotency_key,
            )
            .first()
        )

    def add_draft_no_commit(
        self, draft: MarketplaceListingDraft,
    ) -> MarketplaceListingDraft:

        self.db.add(draft)
        self.db.flush()

        return draft

    def update_draft_state_conditional(
        self,
        draft_id: int,
        company_id: int,
        expected_states: tuple[str, ...],
        new_state: str,
    ) -> int:

        stmt = (
            update(MarketplaceListingDraft)
            .where(MarketplaceListingDraft.id == draft_id)
            .where(MarketplaceListingDraft.company_id == company_id)
            .where(MarketplaceListingDraft.workflow_state.in_(expected_states))
            .values(workflow_state=new_state)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def update_draft_channels_conditional(
        self,
        draft_id: int,
        company_id: int,
        expected_states: tuple[str, ...],
        new_state: str,
        selected_channel_ids_json: str,
    ) -> int:

        stmt = (
            update(MarketplaceListingDraft)
            .where(MarketplaceListingDraft.id == draft_id)
            .where(MarketplaceListingDraft.company_id == company_id)
            .where(MarketplaceListingDraft.workflow_state.in_(expected_states))
            .values(
                workflow_state=new_state,
                selected_channel_ids_json=selected_channel_ids_json,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # MarketplaceListing
    # --------------------------------------------------

    def get_listing_for_company(
        self, listing_id: int, company_id: int,
    ) -> MarketplaceListing | None:

        return (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == listing_id)
            .filter(MarketplaceListing.company_id == company_id)
            .first()
        )

    def get_listing_for_candidate_account(
        self, product_candidate_id: int, marketplace_account_id: int,
        company_id: int,
    ) -> MarketplaceListing | None:

        return (
            self.db.query(MarketplaceListing)
            .filter(
                MarketplaceListing.product_candidate_id
                == product_candidate_id,
            )
            .filter(
                MarketplaceListing.marketplace_account_id
                == marketplace_account_id,
            )
            .filter(MarketplaceListing.company_id == company_id)
            .first()
        )

    def list_listings_for_candidate(
        self, product_candidate_id: int, company_id: int,
    ) -> list[MarketplaceListing]:

        return (
            self.db.query(MarketplaceListing)
            .filter(
                MarketplaceListing.product_candidate_id
                == product_candidate_id,
            )
            .filter(MarketplaceListing.company_id == company_id)
            .order_by(MarketplaceListing.id.asc())
            .all()
        )

    def add_listing_no_commit(
        self, listing: MarketplaceListing,
    ) -> MarketplaceListing:

        self.db.add(listing)
        self.db.flush()

        return listing

    def update_listing_status_conditional(
        self,
        listing_id: int,
        company_id: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
    ) -> int:

        stmt = (
            update(MarketplaceListing)
            .where(MarketplaceListing.id == listing_id)
            .where(MarketplaceListing.company_id == company_id)
            .where(MarketplaceListing.status.in_(expected_statuses))
            .values(status=new_status)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # 플랫폼 상태 동기화 (2026-08-05 최종 제품화 Phase 4)
    # --------------------------------------------------

    def update_listing_platform_status_conditional(
        self,
        listing_id: int,
        company_id: int,
        *,
        expected_last_refreshed_at: datetime | None,
        platform_sync_status: str,
        platform_raw_status: str | None,
        status_last_refreshed_at: datetime,
        status_last_refresh_error_code: str | None,
        platform_status_observed_at: datetime | None,
        rate_limit_retry_after_seconds: int | None = None,
        rate_limit_retry_available_at: datetime | None = None,
        update_rate_limit_state: bool = False,
    ) -> int:
        """
        rate limit 체크(직전 status_last_refreshed_at과 지금 값이 서비스
        조회 시점과 여전히 같은지)를 조건부 UPDATE에 포함해, 두 새로고침
        요청이 거의 동시에 들어와도 하나만 반영되게 한다.

        2026-08-07 Gate H — `update_rate_limit_state=True`일 때만
        `rate_limit_*` 두 컬럼을 이 UPDATE에 포함한다(기본값 False —
        호출자가 명시적으로 재계산한 값을 넘길 때만 덮어쓴다. 이
        플래그가 없으면 매 호출마다 실수로 NULL을 덮어써 이미 알고
        있던 대기 상태를 잃어버릴 위험이 있다).
        """

        values = {
            "platform_sync_status": platform_sync_status,
            "platform_raw_status": platform_raw_status,
            "status_last_refreshed_at": status_last_refreshed_at,
            "status_last_refresh_error_code": status_last_refresh_error_code,
            "platform_status_observed_at": platform_status_observed_at,
        }
        if update_rate_limit_state:
            values["rate_limit_retry_after_seconds"] = (
                rate_limit_retry_after_seconds
            )
            values["rate_limit_retry_available_at"] = (
                rate_limit_retry_available_at
            )

        stmt = (
            update(MarketplaceListing)
            .where(MarketplaceListing.id == listing_id)
            .where(MarketplaceListing.company_id == company_id)
            .where(
                MarketplaceListing.status_last_refreshed_at.is_(None)
                if expected_last_refreshed_at is None
                else MarketplaceListing.status_last_refreshed_at
                == expected_last_refreshed_at,
            )
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def add_status_event_no_commit(
        self, event: MarketplaceListingStatusEvent,
    ) -> MarketplaceListingStatusEvent:

        self.db.add(event)
        self.db.flush()

        return event

    def list_status_events_for_listing(
        self, listing_id: int, company_id: int,
    ) -> list[MarketplaceListingStatusEvent]:

        return (
            self.db.query(MarketplaceListingStatusEvent)
            .filter(MarketplaceListingStatusEvent.listing_id == listing_id)
            .filter(MarketplaceListingStatusEvent.company_id == company_id)
            .order_by(MarketplaceListingStatusEvent.id.asc())
            .all()
        )

    def list_listings_filtered(
        self,
        company_id: int,
        *,
        channel_code: str | None = None,
        marketplace_account_id: int | None = None,
        fulfillment_mode: str | None = None,
        platform_sync_status: str | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[MarketplaceListing, str, str]]:
        """
        (Listing, channel_code, product_name) 튜플 목록을 반환한다 —
        검색·CSV 내보내기가 채널 코드·상품명을 함께 필요로 하므로
        호출부에서 다시 조인하지 않도록 여기서 한 번에 만든다.

        `limit`은 2026-08-05 CTO 반려 반영 — CSV 내보내기가 무제한
        행을 한 번에 만들지 않도록 호출부(export_csv)가 상한+1을
        넘겨 "초과 여부"까지 판단할 수 있게 한다. 일반 목록 조회는
        limit=None(제한 없음)을 그대로 사용한다.
        """

        query = (
            self.db.query(
                MarketplaceListing,
                MarketplaceChannel.code,
                ProductCandidate.product_name,
            )
            .join(
                MarketplaceAccount,
                MarketplaceAccount.id
                == MarketplaceListing.marketplace_account_id,
            )
            .join(
                MarketplaceChannel,
                MarketplaceChannel.id == MarketplaceAccount.channel_id,
            )
            .join(
                ProductCandidate,
                ProductCandidate.id
                == MarketplaceListing.product_candidate_id,
            )
            .filter(MarketplaceListing.company_id == company_id)
        )

        if channel_code is not None:
            query = query.filter(MarketplaceChannel.code == channel_code)
        if marketplace_account_id is not None:
            query = query.filter(
                MarketplaceListing.marketplace_account_id
                == marketplace_account_id,
            )
        if platform_sync_status is not None:
            query = query.filter(
                MarketplaceListing.platform_sync_status
                == platform_sync_status,
            )
        if updated_from is not None:
            query = query.filter(
                MarketplaceListing.updated_at >= updated_from,
            )
        if updated_to is not None:
            query = query.filter(MarketplaceListing.updated_at <= updated_to)
        if search:
            like = f"%{search}%"
            query = query.filter(
                (ProductCandidate.product_name.ilike(like))
                | (MarketplaceListing.external_listing_id.ilike(like)),
            )
        if fulfillment_mode is not None:
            subquery = (
                self.db.query(MarketplaceFulfillmentSelection.listing_id)
                .filter(
                    MarketplaceFulfillmentSelection.fulfillment_mode
                    == fulfillment_mode,
                )
                .filter(
                    MarketplaceFulfillmentSelection.status
                    == SelectionStatus.SELECTED,
                )
            )
            query = query.filter(MarketplaceListing.id.in_(subquery))

        query = query.order_by(MarketplaceListing.id.desc())

        if limit is not None:
            query = query.limit(limit)

        return query.all()

    # --------------------------------------------------
    # MarketplaceFulfillmentSelection
    # --------------------------------------------------

    def get_selection_for_company(
        self, selection_id: int, company_id: int,
    ) -> MarketplaceFulfillmentSelection | None:

        return (
            self.db.query(MarketplaceFulfillmentSelection)
            .filter(MarketplaceFulfillmentSelection.id == selection_id)
            .filter(MarketplaceFulfillmentSelection.company_id == company_id)
            .first()
        )

    def get_selection_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> MarketplaceFulfillmentSelection | None:

        return (
            self.db.query(MarketplaceFulfillmentSelection)
            .filter(MarketplaceFulfillmentSelection.company_id == company_id)
            .filter(
                MarketplaceFulfillmentSelection.idempotency_key
                == idempotency_key,
            )
            .first()
        )

    def get_current_selection_for_listing(
        self, listing_id: int, company_id: int,
    ) -> MarketplaceFulfillmentSelection | None:
        """현재 유효한(SELECTED) 선택 — 최신 행 기준."""

        return (
            self.db.query(MarketplaceFulfillmentSelection)
            .filter(MarketplaceFulfillmentSelection.listing_id == listing_id)
            .filter(MarketplaceFulfillmentSelection.company_id == company_id)
            .filter(MarketplaceFulfillmentSelection.status == "SELECTED")
            .order_by(MarketplaceFulfillmentSelection.id.desc())
            .first()
        )

    def list_selections_for_listing(
        self, listing_id: int, company_id: int,
    ) -> list[MarketplaceFulfillmentSelection]:

        return (
            self.db.query(MarketplaceFulfillmentSelection)
            .filter(MarketplaceFulfillmentSelection.listing_id == listing_id)
            .filter(MarketplaceFulfillmentSelection.company_id == company_id)
            .order_by(MarketplaceFulfillmentSelection.id.asc())
            .all()
        )

    def add_selection_no_commit(
        self, selection: MarketplaceFulfillmentSelection,
    ) -> MarketplaceFulfillmentSelection:

        self.db.add(selection)
        self.db.flush()

        return selection

    def supersede_selection_conditional(
        self, selection_id: int, company_id: int,
    ) -> int:

        stmt = (
            update(MarketplaceFulfillmentSelection)
            .where(MarketplaceFulfillmentSelection.id == selection_id)
            .where(MarketplaceFulfillmentSelection.company_id == company_id)
            .where(MarketplaceFulfillmentSelection.status == "SELECTED")
            .values(status="SUPERSEDED")
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # MarketplaceFulfillmentEligibility (append-only)
    # --------------------------------------------------

    def add_eligibility_no_commit(
        self, eligibility: MarketplaceFulfillmentEligibility,
    ) -> MarketplaceFulfillmentEligibility:

        self.db.add(eligibility)
        self.db.flush()

        return eligibility

    def get_eligibility_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> MarketplaceFulfillmentEligibility | None:

        return (
            self.db.query(MarketplaceFulfillmentEligibility)
            .filter(MarketplaceFulfillmentEligibility.company_id == company_id)
            .filter(
                MarketplaceFulfillmentEligibility.idempotency_key
                == idempotency_key,
            )
            .first()
        )

    def get_latest_eligibility(
        self, marketplace_account_id: int, fulfillment_mode: str,
        company_id: int,
    ) -> MarketplaceFulfillmentEligibility | None:
        """
        계정+방식의 "현재" 자격 — created_at(및 id) 기준 최신 행. 행이
        없으면 None(호출자가 UNKNOWN으로 취급).
        """

        return (
            self.db.query(MarketplaceFulfillmentEligibility)
            .filter(
                MarketplaceFulfillmentEligibility.marketplace_account_id
                == marketplace_account_id,
            )
            .filter(MarketplaceFulfillmentEligibility.company_id == company_id)
            .filter(
                MarketplaceFulfillmentEligibility.fulfillment_mode
                == fulfillment_mode,
            )
            .order_by(MarketplaceFulfillmentEligibility.id.desc())
            .first()
        )

    def list_eligibility_history(
        self, marketplace_account_id: int, fulfillment_mode: str,
        company_id: int,
    ) -> list[MarketplaceFulfillmentEligibility]:

        return (
            self.db.query(MarketplaceFulfillmentEligibility)
            .filter(
                MarketplaceFulfillmentEligibility.marketplace_account_id
                == marketplace_account_id,
            )
            .filter(MarketplaceFulfillmentEligibility.company_id == company_id)
            .filter(
                MarketplaceFulfillmentEligibility.fulfillment_mode
                == fulfillment_mode,
            )
            .order_by(MarketplaceFulfillmentEligibility.id.asc())
            .all()
        )

    # --------------------------------------------------
    # MarketplaceSubmissionApproval (append-only)
    # --------------------------------------------------

    def add_approval_no_commit(
        self, approval: MarketplaceSubmissionApproval,
    ) -> MarketplaceSubmissionApproval:

        self.db.add(approval)
        self.db.flush()

        return approval

    def get_approval_for_company(
        self, approval_id: int, company_id: int,
    ) -> MarketplaceSubmissionApproval | None:

        return (
            self.db.query(MarketplaceSubmissionApproval)
            .filter(MarketplaceSubmissionApproval.id == approval_id)
            .filter(MarketplaceSubmissionApproval.company_id == company_id)
            .first()
        )

    def get_approval_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> MarketplaceSubmissionApproval | None:

        return (
            self.db.query(MarketplaceSubmissionApproval)
            .filter(MarketplaceSubmissionApproval.company_id == company_id)
            .filter(
                MarketplaceSubmissionApproval.idempotency_key
                == idempotency_key,
            )
            .first()
        )

    def get_latest_approval(
        self, listing_id: int, selection_id: int, company_id: int,
    ) -> MarketplaceSubmissionApproval | None:
        """
        (listing_id, selection_id)의 "현재" 승인 상태 — id 기준 최신
        행. 행이 없으면 None(호출자가 승인 없음으로 취급, fail-closed).
        """

        return (
            self.db.query(MarketplaceSubmissionApproval)
            .filter(MarketplaceSubmissionApproval.listing_id == listing_id)
            .filter(
                MarketplaceSubmissionApproval.selection_id == selection_id,
            )
            .filter(MarketplaceSubmissionApproval.company_id == company_id)
            .order_by(MarketplaceSubmissionApproval.id.desc())
            .first()
        )

    def list_approval_history(
        self, listing_id: int, selection_id: int, company_id: int,
    ) -> list[MarketplaceSubmissionApproval]:

        return (
            self.db.query(MarketplaceSubmissionApproval)
            .filter(MarketplaceSubmissionApproval.listing_id == listing_id)
            .filter(
                MarketplaceSubmissionApproval.selection_id == selection_id,
            )
            .filter(MarketplaceSubmissionApproval.company_id == company_id)
            .order_by(MarketplaceSubmissionApproval.id.asc())
            .all()
        )

    # --------------------------------------------------
    # MarketplaceSubmission (append-only)
    # --------------------------------------------------

    def add_submission_no_commit(
        self, submission: MarketplaceSubmission,
    ) -> MarketplaceSubmission:

        self.db.add(submission)
        self.db.flush()

        return submission

    def get_submission_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> MarketplaceSubmission | None:

        return (
            self.db.query(MarketplaceSubmission)
            .filter(MarketplaceSubmission.company_id == company_id)
            .filter(
                MarketplaceSubmission.idempotency_key == idempotency_key,
            )
            .first()
        )

    def get_submission_for_company(
        self, submission_id: int, company_id: int,
    ) -> MarketplaceSubmission | None:

        return (
            self.db.query(MarketplaceSubmission)
            .filter(MarketplaceSubmission.id == submission_id)
            .filter(MarketplaceSubmission.company_id == company_id)
            .first()
        )

    def update_submission_status_conditional(
        self,
        submission_id: int,
        company_id: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
        external_submission_ref: str | None = None,
        error_reason: str | None = None,
        request_fingerprint: str | None = None,
        correlation_id: str | None = None,
        external_http_status: int | None = None,
        provider_warning_summary: str | None = None,
        provider_response_code: str | None = None,
    ) -> int:

        values: dict = {"status": new_status}
        if external_submission_ref is not None:
            values["external_submission_ref"] = external_submission_ref
        if error_reason is not None:
            values["error_reason"] = error_reason
        if request_fingerprint is not None:
            values["request_fingerprint"] = request_fingerprint
        if correlation_id is not None:
            values["correlation_id"] = correlation_id
        if external_http_status is not None:
            values["external_http_status"] = external_http_status
        if provider_warning_summary is not None:
            values["provider_warning_summary"] = provider_warning_summary
        if provider_response_code is not None:
            values["provider_response_code"] = provider_response_code

        stmt = (
            update(MarketplaceSubmission)
            .where(MarketplaceSubmission.id == submission_id)
            .where(MarketplaceSubmission.company_id == company_id)
            .where(MarketplaceSubmission.status.in_(expected_statuses))
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def list_unresolved_submissions_for_company(
        self, company_id: int,
    ) -> list[MarketplaceSubmission]:
        """
        2026-08-31 V7 필수 작업 2번(제출 장부 정합화 완성) — 정합화
        검토 화면이 필요한 전체 목록(PENDING/SUBMITTING/UNKNOWN, 이미
        external_submission_ref가 있는 행은 애초에 정합화 대상이 아니라
        제외)을 위저드 하나에 갇히지 않고 회사 전체 범위로 조회한다.
        가장 오래된 것부터 보여준다(먼저 발생한 불일치부터 확인하도록).
        """

        return (
            self.db.query(MarketplaceSubmission)
            .filter(MarketplaceSubmission.company_id == company_id)
            .filter(
                MarketplaceSubmission.status.in_((
                    SubmissionStatus.PENDING, SubmissionStatus.SUBMITTING,
                    SubmissionStatus.UNKNOWN,
                )),
            )
            .filter(MarketplaceSubmission.external_submission_ref.is_(None))
            .order_by(MarketplaceSubmission.attempted_at.asc())
            .all()
        )

    def list_submissions_for_listing(
        self, listing_id: int, company_id: int,
    ) -> list[MarketplaceSubmission]:

        return (
            self.db.query(MarketplaceSubmission)
            .filter(MarketplaceSubmission.listing_id == listing_id)
            .filter(MarketplaceSubmission.company_id == company_id)
            .order_by(MarketplaceSubmission.id.asc())
            .all()
        )


__all__ = [
    "MarketplaceListingRepository",
]
