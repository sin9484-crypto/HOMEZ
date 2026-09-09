"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/eligibility_service.py

채널·계정별 판매 방식 자격 검증 — fail-closed.

고정 규칙(요청 원문 그대로):
  - UNKNOWN은 허용이 아니다.
  - 자격이 필요한 방식은 VERIFIED만 제출 가능하다.
  - 자격 확인 시각과 근거를 기록한다.
  - 자격 만료 후 재검증이 필요하다.
  - 계정별로 독립 관리한다 — 다른 계정의 자격을 재사용하지 않는다.
  - 수동 확인은 확인자·시각·근거·만료일을 감사 기록한다.
  - 외부 자격 API가 없으면 자동 VERIFIED 처리하지 않는다 —
    MANUAL_REVIEW_REQUIRED 후 별도 review() 호출로만 VERIFIED 도달한다.

2026-08-01 CTO 3차 지적 반영 — 이 파일의 모든 메서드가 company_id를
필수 인자로 받는다. marketplace_account_id는 항상 company 스코프로
조회하며, 다른 회사의 계정을 지정하면 존재하지 않는 것과 동일하게
거부된다.
=========================================================
"""

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.marketplace_listing.constants import ContractStatus
from app.domains.marketplace_listing.constants import EligibilityState
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentEligibility,
)
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceEligibilityCheckRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceEligibilityManualReviewRequest,
)

_ACCOUNT_NOT_FOUND = "MarketplaceAccount를 찾을 수 없습니다."


class EligibilityService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = MarketplaceListingRepository(db)

    def current_state(
        self, marketplace_account_id: int, fulfillment_mode: str,
        company_id: int, now: datetime | None = None,
    ) -> str:
        """
        "현재" 자격 상태를 판단한다 — 최신 행 기준, 없으면 UNKNOWN.
        읽기 시점에 만료를 강제한다: 저장된 state가 VERIFIED라도
        expires_at이 지났으면 EXPIRED로 취급한다(재검증 필요).
        """

        now = now or datetime.utcnow()

        latest = self.repository.get_latest_eligibility(
            marketplace_account_id, fulfillment_mode, company_id,
        )
        if latest is None:
            return EligibilityState.UNKNOWN

        if latest.expires_at is not None and latest.expires_at <= now:
            return EligibilityState.EXPIRED

        return latest.state

    def is_usable(
        self, marketplace_account_id: int, fulfillment_mode: str,
        company_id: int, now: datetime | None = None,
    ) -> bool:

        return self.current_state(
            marketplace_account_id, fulfillment_mode, company_id, now,
        ) in EligibilityState.USABLE

    def check(
        self, data: MarketplaceEligibilityCheckRequest, company_id: int,
    ) -> MarketplaceFulfillmentEligibility:
        """
        자격을 확인한다. fail-closed 5단계:
        1) capability가 VERIFIED가 아니면 즉시 UNKNOWN.
        2) requires_account_contract(직매입)면 계정 계약 상태까지 확인.
        3) 자동 확인 경로가 없으면 MANUAL_REVIEW_REQUIRED로 남긴다
           (자동으로 VERIFIED를 생성하지 않는다).
        4) 각 확인은 append-only 행으로 기록(이 행 자체가 감사 기록).
        5) idempotency_key로 중복 확인 방지(company 범위).
        """

        existing = self.repository.get_eligibility_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing

        account = self.repository.get_account_for_company(
            data.marketplace_account_id, company_id,
        )
        if account is None:
            raise NotFoundException(_ACCOUNT_NOT_FOUND)

        capability = self.repository.get_capability_for_channel_mode(
            account.channel_id, data.fulfillment_mode,
        )

        if capability is None or capability.status != CapabilityStatus.VERIFIED:
            return self._record(
                data, company_id,
                capability.id if capability else None,
                EligibilityState.UNKNOWN, "CAPABILITY_NOT_VERIFIED",
                None, None, None,
            )

        if not capability.requires_eligibility_check:
            # 자격 확인이 필요 없는 방식(예: 판매자배송)은 즉시
            # VERIFIED로 기록한다 — 근거는 캡빌리티 자체.
            return self._record(
                data, company_id, capability.id, EligibilityState.VERIFIED,
                "NO_ELIGIBILITY_CHECK_REQUIRED", None,
                datetime.utcnow(), None,
            )

        if capability.requires_account_contract:
            now = datetime.utcnow()
            contract_ok = (
                account.direct_purchase_contract_status
                == ContractStatus.VERIFIED
                and (
                    account.direct_purchase_contract_expires_at is None
                    or account.direct_purchase_contract_expires_at > now
                )
            )

            if not contract_ok:
                return self._record(
                    data, company_id, capability.id,
                    EligibilityState.REJECTED,
                    "DIRECT_PURCHASE_CONTRACT", None, None, None,
                )

            return self._record(
                data, company_id, capability.id, EligibilityState.VERIFIED,
                "DIRECT_PURCHASE_CONTRACT",
                account.direct_purchase_contract_reference, now,
                account.direct_purchase_contract_expires_at,
            )

        # 자동 확인 경로가 없다 — 절대 자동 VERIFIED를 만들지 않는다.
        return self._record(
            data, company_id, capability.id,
            EligibilityState.MANUAL_REVIEW_REQUIRED,
            "MANUAL_REVIEW", None, None, None,
        )

    def _record(
        self,
        data: MarketplaceEligibilityCheckRequest,
        company_id: int,
        capability_id: int | None,
        state: str,
        check_source: str,
        evidence_reference: str | None,
        verified_at: datetime | None,
        expires_at: datetime | None,
    ) -> MarketplaceFulfillmentEligibility:

        eligibility = MarketplaceFulfillmentEligibility(
            company_id=company_id,
            marketplace_account_id=data.marketplace_account_id,
            fulfillment_mode=data.fulfillment_mode,
            capability_id=capability_id or 0,
            state=state,
            check_source=check_source,
            evidence_reference=evidence_reference,
            checked_at=datetime.utcnow(),
            verified_at=verified_at,
            expires_at=expires_at,
            idempotency_key=data.idempotency_key,
        )

        try:
            eligibility = self.repository.add_eligibility_no_commit(
                eligibility,
            )
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_eligibility_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner

        except Exception:
            self.db.rollback()
            raise

        return eligibility

    def manual_review(
        self, data: MarketplaceEligibilityManualReviewRequest,
        reviewer_id: int, company_id: int,
    ) -> MarketplaceFulfillmentEligibility:
        """
        MANUAL_REVIEW_REQUIRED 경로를 관리자가 직접 확정한다.
        reviewer_id·review_reason이 항상 필요하며, VERIFIED로
        확정하려면 expires_at도 필요하다(무기한 자동 유효 금지).
        """

        existing = self.repository.get_eligibility_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing

        if data.new_state not in (
            EligibilityState.VERIFIED, EligibilityState.REJECTED,
        ):
            raise BadRequestException(
                "수동 확인은 VERIFIED 또는 REJECTED로만 확정할 수 "
                "있습니다.",
            )

        if not data.review_reason.strip():
            raise BadRequestException("review_reason은 비어있을 수 없습니다.")

        if data.new_state == EligibilityState.VERIFIED and data.expires_at is None:
            raise BadRequestException(
                "VERIFIED로 확정하려면 expires_at(만료일)이 필요합니다 — "
                "무기한 자동 유효는 허용하지 않습니다.",
            )

        account = self.repository.get_account_for_company(
            data.marketplace_account_id, company_id,
        )
        if account is None:
            raise NotFoundException(_ACCOUNT_NOT_FOUND)

        capability = self.repository.get_capability_for_channel_mode(
            account.channel_id, data.fulfillment_mode,
        )
        if capability is None:
            raise NotFoundException(
                "MarketplaceFulfillmentCapability를 찾을 수 없습니다.",
            )

        eligibility = MarketplaceFulfillmentEligibility(
            company_id=company_id,
            marketplace_account_id=data.marketplace_account_id,
            fulfillment_mode=data.fulfillment_mode,
            capability_id=capability.id,
            state=data.new_state,
            check_source="MANUAL_REVIEW",
            evidence_reference=None,
            checked_at=datetime.utcnow(),
            verified_at=(
                datetime.utcnow()
                if data.new_state == EligibilityState.VERIFIED
                else None
            ),
            expires_at=data.expires_at,
            reviewer_id=reviewer_id,
            review_reason=data.review_reason,
            idempotency_key=data.idempotency_key,
        )

        try:
            eligibility = self.repository.add_eligibility_no_commit(
                eligibility,
            )
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_eligibility_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner

        except Exception:
            self.db.rollback()
            raise

        return eligibility

    def history(
        self, marketplace_account_id: int, fulfillment_mode: str,
        company_id: int,
    ) -> list[MarketplaceFulfillmentEligibility]:

        return self.repository.list_eligibility_history(
            marketplace_account_id, fulfillment_mode, company_id,
        )


__all__ = [
    "EligibilityService",
]
