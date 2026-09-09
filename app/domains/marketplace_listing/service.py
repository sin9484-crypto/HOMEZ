"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/service.py

채널별 판매 방식 선택 — 위저드 Draft 상태 머신 + Listing/Selection
생성 + Capability Registry 시딩.

Transaction/멱등성 원칙은 app/domains/coupang/service.py와 동일하다:
  - Repository는 commit하지 않는다(no_commit/flush).
  - 상태 전이는 조건부 UPDATE + rowcount 검증으로 반영한다.
  - idempotency_key UNIQUE 위반(IntegrityError)은 rollback 후 승자를
    재조회해 그대로 반환한다.

2026-08-01 CTO 3차 지적 반영 — 회사 소유권 격리(신규 발견): 이 파일의
모든 단건 조회·변경 메서드는 company_id를 필수 인자로 받는다. 내부적
으로 항상 repository의 company 스코프 조회/조건부 UPDATE만 사용하며,
company_id 없이 id만으로 접근하는 경로는 없다. 다른 회사의 객체에
접근하면 "존재하지 않는 것"과 완전히 동일한 NotFoundException(404)을
던진다.

MarketplaceChannel/MarketplaceFulfillmentCapability(seed_capability_
registry/verify_capability/list_channels/list_capabilities)는
예외다 — 전역 플랫폼 설정이라 company_id로 범위를 좁히지 않는다.

**해소됨(2026-08-14 테넌트 격리 감사, Gate R13)**: 이전에는
ProductCandidate 자체에 company_id가 없어, 회사 A가 승인한 후보를
회사 B도 그대로 Listing 생성에 쓸 수 있었다(candidate.status라는
전역 필드만 비교했기 때문). 이제 create_listing()이
ProductCandidateService.require_approved_for_company()를 거쳐 "이
회사 관점의 승인 상태"(ProductCandidateSelection)를 확인한다 —
ProductCandidate 자체(원본 발견 카탈로그)는 여전히 전역이지만, 승인
판단은 회사별로 완전히 분리됐다.
=========================================================
"""

import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.marketplace_listing.capability_registry import (
    CAPABILITY_SEED,
)
from app.domains.marketplace_listing.capability_registry import CHANNEL_SEED
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import DraftWorkflowState
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.constants import ListingStatus
from app.domains.marketplace_listing.constants import SelectionStatus
from app.domains.marketplace_listing.events import (
    MarketplaceListingEventType,
)
from app.domains.marketplace_listing.events import (
    build_marketplace_listing_event,
)
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentCapability,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentSelection,
)
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.marketplace_listing.model import MarketplaceListingDraft
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceFulfillmentSelectionCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingDraftCreateRequest,
)
from app.domains.marketplace_listing.fingerprint import canonical_json
from app.domains.marketplace_listing.fingerprint import sha256_hex
from app.domains.marketplace_listing.required_fields_schemas import (
    get_required_fields_schema,
)
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.service import ProductCandidateService
from pydantic import ValidationError

_ACCOUNT_NOT_FOUND = "MarketplaceAccount를 찾을 수 없습니다."
_DRAFT_NOT_FOUND = "MarketplaceListingDraft를 찾을 수 없습니다."
_LISTING_NOT_FOUND = "MarketplaceListing을 찾을 수 없습니다."


class MarketplaceListingService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = MarketplaceListingRepository(db)

    # --------------------------------------------------
    # Capability Registry 시딩 (전역 — company_id 없음, admin_guard 필요)
    # --------------------------------------------------
    #
    # 앱 시작 시나 Migration에서 자동으로 호출되지 않는다. 정적
    # 레지스트리(capability_registry.py)를 실제로 활성화(status를
    # VERIFIED로)하는 것은 admin_guard로 인증된 실제 운영자가 별도로
    # 수행해야 하는 작업이다(verify_capability 참고) — 이 메서드는
    # DRAFT 상태로만 시딩한다.

    def seed_capability_registry(self) -> list[MarketplaceFulfillmentCapability]:

        created: list[MarketplaceFulfillmentCapability] = []

        try:
            for channel_code, channel_info in CHANNEL_SEED.items():
                channel = self.repository.get_channel_by_code(channel_code)

                if channel is None:
                    channel = self.repository.add_channel_no_commit(
                        MarketplaceChannel(
                            code=channel_code,
                            name=channel_info["name"],
                            doc_verification_status=channel_info[
                                "doc_verification_status"
                            ],
                            doc_source_reference=channel_info[
                                "doc_source_reference"
                            ],
                        ),
                    )

                for mode, cap_info in CAPABILITY_SEED.get(
                    channel_code, {},
                ).items():
                    existing = self.repository.get_capability_for_channel_mode(
                        channel.id, mode,
                    )
                    if existing is not None:
                        continue

                    capability = self.repository.add_capability_no_commit(
                        MarketplaceFulfillmentCapability(
                            channel_id=channel.id,
                            fulfillment_mode=mode,
                            is_supported=cap_info["is_supported"],
                            requires_eligibility_check=cap_info[
                                "requires_eligibility_check"
                            ],
                            requires_account_contract=cap_info[
                                "requires_account_contract"
                            ],
                            external_display_name=cap_info[
                                "external_display_name"
                            ],
                            schema_name=cap_info.get("schema_name"),
                            schema_version=cap_info.get("schema_version"),
                            policy_version="1.0.0",
                            doc_source_reference=cap_info[
                                "doc_source_reference"
                            ],
                            status=CapabilityStatus.DRAFT,
                        ),
                    )
                    created.append(capability)

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return created

    def verify_capability(
        self, capability_id: int, idempotency_key: str,
    ) -> MarketplaceFulfillmentCapability:
        """
        캡빌리티를 VERIFIED로 전환한다(전역 — company_id 없음). 실제
        정책/문서를 대조 확인한 관리자의 명시적 판단이어야 한다 —
        router.py의 admin_guard가 그 통제 지점이다(AI가 임의로 방식을
        활성화할 수 없다).
        """

        capability = self.repository.get_capability(capability_id)
        if capability is None:
            raise NotFoundException(
                "MarketplaceFulfillmentCapability를 찾을 수 없습니다.",
            )

        try:
            rowcount = self.repository.verify_capability_conditional(
                capability_id,
                (CapabilityStatus.DRAFT, CapabilityStatus.EXPIRED),
                datetime.utcnow(),
            )
            if rowcount != 1:
                raise ConflictException(
                    "캡빌리티 상태가 동시에 변경되어 VERIFIED 전환을 "
                    "반영할 수 없습니다.",
                )

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return self.repository.get_capability(capability_id)

    def list_channels(self) -> list[MarketplaceChannel]:

        return self.repository.list_channels()

    # --------------------------------------------------
    # MarketplaceAccount
    # --------------------------------------------------

    def create_account(
        self, channel_id: int, account_code: str, account_name: str,
        company_id: int,
    ) -> MarketplaceAccount:

        existing = self.repository.get_account_by_company_channel_code(
            company_id, channel_id, account_code,
        )
        if existing is not None:
            return existing

        channel = self.repository.get_channel(channel_id)
        if channel is None:
            raise NotFoundException("MarketplaceChannel을 찾을 수 없습니다.")

        # 데이터 무결성 — 비활성 채널 아래 새 계정을 만들지 않는다
        # (FK가 없으므로 서비스 레벨에서 고아 방지를 강제한다).
        if not channel.is_active:
            raise BadRequestException(
                "비활성화된 채널 아래에는 새 계정을 생성할 수 없습니다.",
            )

        account = MarketplaceAccount(
            company_id=company_id,
            channel_id=channel_id,
            account_code=account_code,
            account_name=account_name,
        )

        try:
            account = self.repository.add_account_no_commit(account)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_account_by_company_channel_code(
                company_id, channel_id, account_code,
            )
            if winner is None:
                raise

            return winner

        except Exception:
            self.db.rollback()
            raise

        return account

    def deactivate_account(
        self, account_id: int, idempotency_key: str, company_id: int,
    ) -> MarketplaceAccount:
        """
        계정을 soft-deactivate한다 — 이 Domain 어디에도 실제 DELETE는
        없다. 이 계정을 참조하는 활성(비종결) Listing이 하나라도 있으면
        고아 방지를 위해 차단한다.
        """

        account = self.repository.get_account_for_company(
            account_id, company_id,
        )
        if account is None:
            raise NotFoundException(_ACCOUNT_NOT_FOUND)

        if not account.is_active:
            return account

        active_count = self.repository.count_active_listings_for_account(
            account_id, company_id,
        )
        if active_count > 0:
            raise ConflictException(
                f"이 계정을 참조하는 활성 Listing이 {active_count}건 "
                "있어 비활성화할 수 없습니다.",
            )

        try:
            rowcount = self.repository.deactivate_account_conditional(
                account_id, company_id,
            )
            if rowcount != 1:
                raise ConflictException(
                    "계정 상태가 동시에 변경되어 비활성화를 반영할 수 "
                    "없습니다.",
                )
            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return self.repository.get_account_for_company(account_id, company_id)

    def list_accounts_for_channel(
        self, channel_id: int, company_id: int,
    ) -> list[MarketplaceAccount]:

        return self.repository.list_accounts_for_company_channel(
            company_id, channel_id,
        )

    def list_capabilities(
        self, channel_id: int,
    ) -> list[MarketplaceFulfillmentCapability]:

        return self.repository.list_capabilities_for_channel(channel_id)

    # --------------------------------------------------
    # Listing Draft (위저드)
    # --------------------------------------------------

    def create_draft(
        self, data: MarketplaceListingDraftCreateRequest, created_by: int,
        company_id: int,
    ) -> tuple[MarketplaceListingDraft, bool]:

        existing = self.repository.get_draft_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing, True

        candidate = (
            self.db.query(ProductCandidate)
            .filter(ProductCandidate.id == data.product_candidate_id)
            .first()
        )
        if candidate is None:
            raise NotFoundException("ProductCandidate를 찾을 수 없습니다.")

        draft = MarketplaceListingDraft(
            company_id=company_id,
            product_candidate_id=data.product_candidate_id,
            workflow_state=DraftWorkflowState.DRAFT,
            selected_channel_ids_json="[]",
            product_basics_json=json.dumps(
                data.product_basics, ensure_ascii=False,
            ),
            created_by=created_by,
            idempotency_key=data.idempotency_key,
        )

        try:
            draft = self.repository.add_draft_no_commit(draft)
            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_draft_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner, True

        except Exception:
            self.db.rollback()
            raise

        return draft, False

    def select_channels(
        self, draft_id: int, marketplace_account_ids: list[int],
        company_id: int,
    ) -> MarketplaceListingDraft:
        """
        위저드 2단계 — 채널(계정) 선택. 선택되지 않은 채널은 등록
        대상이 아니다(요청 원문 그대로) — 이후 단계는 여기서 선택된
        계정만 대상으로 한다.
        """

        draft = self.repository.get_draft_for_company(draft_id, company_id)
        if draft is None:
            raise NotFoundException(_DRAFT_NOT_FOUND)

        if not marketplace_account_ids:
            raise BadRequestException("최소 1개 이상의 채널(계정)을 선택해야 합니다.")

        for account_id in marketplace_account_ids:
            account = self.repository.get_account_for_company(
                account_id, company_id,
            )
            if account is None or not account.is_active:
                raise BadRequestException(
                    f"유효하지 않은 marketplace_account_id: {account_id}",
                )

        try:
            rowcount = self.repository.update_draft_channels_conditional(
                draft_id,
                company_id,
                (
                    DraftWorkflowState.DRAFT,
                    DraftWorkflowState.CHANNEL_SELECTED,
                ),
                DraftWorkflowState.FULFILLMENT_REQUIRED,
                json.dumps(marketplace_account_ids),
            )
            if rowcount != 1:
                raise ConflictException(
                    "Draft 상태가 동시에 변경되어 채널 선택을 반영할 수 "
                    "없습니다.",
                )

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return self.repository.get_draft_for_company(draft_id, company_id)

    def finalize_fulfillment_selection(
        self, draft_id: int, company_id: int,
    ) -> MarketplaceListingDraft:
        """
        위저드 3단계 완료 확인 — 선택된 모든 채널(계정)에 활성
        (SELECTED) Fulfillment Selection이 존재해야만
        FULFILLMENT_REQUIRED → FULFILLMENT_SELECTED 전이를 허용한다.
        일부 채널만 방식을 선택했으면 여기서 막힌다(요청 원문: "선택된
        채널은 판매 방식이 반드시 필요함").
        """

        draft = self.repository.get_draft_for_company(draft_id, company_id)
        if draft is None:
            raise NotFoundException(_DRAFT_NOT_FOUND)

        selected_account_ids = json.loads(draft.selected_channel_ids_json)

        missing: list[int] = []
        for account_id in selected_account_ids:
            listing = self.repository.get_listing_for_candidate_account(
                draft.product_candidate_id, account_id, company_id,
            )
            selection = (
                self.repository.get_current_selection_for_listing(
                    listing.id, company_id,
                )
                if listing is not None
                else None
            )
            if selection is None:
                missing.append(account_id)

        if missing:
            raise BadRequestException(
                "다음 채널(계정)에 아직 판매 방식이 선택되지 않았습니다 "
                f"— 선택된 모든 채널은 판매 방식이 필수입니다: {missing}",
            )

        try:
            rowcount = self.repository.update_draft_state_conditional(
                draft_id,
                company_id,
                (DraftWorkflowState.FULFILLMENT_REQUIRED,),
                DraftWorkflowState.FULFILLMENT_SELECTED,
            )
            if rowcount != 1:
                raise ConflictException(
                    "Draft 상태가 동시에 변경되어 완료 확인을 반영할 수 "
                    "없습니다.",
                )

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return self.repository.get_draft_for_company(draft_id, company_id)

    def _ensure_listing(
        self, product_candidate_id: int, marketplace_account_id: int,
        draft_id: int | None, company_id: int,
    ) -> MarketplaceListing:

        existing = self.repository.get_listing_for_candidate_account(
            product_candidate_id, marketplace_account_id, company_id,
        )
        if existing is not None:
            return existing

        listing = MarketplaceListing(
            company_id=company_id,
            draft_id=draft_id,
            product_candidate_id=product_candidate_id,
            marketplace_account_id=marketplace_account_id,
            status="DRAFT",
        )

        try:
            listing = self.repository.add_listing_no_commit(listing)
            self.db.flush()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_listing_for_candidate_account(
                product_candidate_id, marketplace_account_id, company_id,
            )
            if winner is None:
                raise

            return winner

        return listing

    def create_listing(
        self, data: MarketplaceListingCreateRequest, company_id: int,
    ) -> tuple[MarketplaceListing, bool]:
        """
        (ProductCandidate × MarketplaceAccount) Listing을 생성한다.
        UNIQUE 범위는 product_candidate_id + marketplace_account_id다 —
        marketplace_id 단독이 아니다(같은 채널의 다른 계정에 각각 별도
        Listing 허용). marketplace_account_id는 반드시 호출자의
        company_id 소유여야 한다(다른 회사 계정을 지정하면 존재하지
        않는 것과 동일하게 거부된다).
        """

        candidate = (
            self.db.query(ProductCandidate)
            .filter(ProductCandidate.id == data.product_candidate_id)
            .first()
        )
        if candidate is None:
            raise NotFoundException("ProductCandidate를 찾을 수 없습니다.")

        # ProductCandidate→Listing 명시적 계약 — ProductCandidate를
        # 영구 판매상품처럼 쓰지 않는다. app/domains/coupang/service.py
        # ::create_draft()와 동일한 게이트를 그대로 재사용한다.
        #
        # 2026-08-14 테넌트 격리 감사(Gate R13) — 예전에는
        # candidate.status(전역)를 직접 비교해, 회사 A의 승인에 회사 B가
        # 무임승차할 수 있었다(marketplace_listing/service.py의 기존
        # "잔존 위험" docstring에 이미 기록돼 있던 결함). 이제
        # ProductCandidateService.require_approved_for_company()로
        # "이 회사 관점의 승인 상태"를 판단한다.
        ProductCandidateService(self.db).require_approved_for_company(
            data.product_candidate_id, company_id,
        )

        account = self.repository.get_account_for_company(
            data.marketplace_account_id, company_id,
        )
        if account is None or not account.is_active:
            raise BadRequestException("유효하지 않은 marketplace_account_id입니다.")

        existing = self.repository.get_listing_for_candidate_account(
            data.product_candidate_id, data.marketplace_account_id,
            company_id,
        )
        if existing is not None:
            return existing, True

        try:
            listing = self._ensure_listing(
                data.product_candidate_id, data.marketplace_account_id,
                data.draft_id, company_id,
            )
            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return listing, False

    def get_listing(self, listing_id: int, company_id: int) -> MarketplaceListing:

        listing = self.repository.get_listing_for_company(
            listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException(_LISTING_NOT_FOUND)

        return listing

    def list_listings_for_candidate(
        self, product_candidate_id: int, company_id: int,
    ) -> list[MarketplaceListing]:

        return self.repository.list_listings_for_candidate(
            product_candidate_id, company_id,
        )

    # --------------------------------------------------
    # Fulfillment Selection — "선택된 채널은 판매 방식이 반드시 필요함"
    # --------------------------------------------------

    def select_fulfillment_mode(
        self, data: MarketplaceFulfillmentSelectionCreateRequest,
        selected_by: int, company_id: int,
    ) -> tuple[MarketplaceFulfillmentSelection, bool, list]:

        existing = self.repository.get_selection_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing, True, []

        if data.fulfillment_mode not in FulfillmentMode.ALL:
            raise BadRequestException(
                f"알 수 없는 fulfillment_mode: {data.fulfillment_mode}",
            )

        listing = self.repository.get_listing_for_company(
            data.listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException(_LISTING_NOT_FOUND)

        account = self.repository.get_account_for_company(
            listing.marketplace_account_id, company_id,
        )
        if account is None:
            raise NotFoundException(_ACCOUNT_NOT_FOUND)

        capability = self.repository.get_capability_for_channel_mode(
            account.channel_id, data.fulfillment_mode,
        )
        if capability is None or capability.status != CapabilityStatus.VERIFIED:
            raise BadRequestException(
                "이 채널에서 확인되지 않았거나 아직 VERIFIED되지 않은 "
                f"판매 방식입니다: {data.fulfillment_mode}",
            )

        if not capability.is_supported:
            raise BadRequestException(
                f"이 채널은 {data.fulfillment_mode} 방식을 지원하지 "
                "않습니다.",
            )

        # 자유 JSON 대신 채널·방식별 등록된 Pydantic Schema로 실제
        # 검증한다 — 미지원 필드는 기본 거부, 금액/수량/범위를
        # 강제한다(required_fields_schemas.py).
        channel = self.repository.get_channel(account.channel_id)
        schema_entry = get_required_fields_schema(
            channel.code, data.fulfillment_mode,
        )
        if schema_entry is None or capability.schema_name is None:
            raise BadRequestException(
                f"{data.fulfillment_mode} 방식은 아직 필수 입력 "
                "Schema가 등록되지 않아 선택할 수 없습니다.",
            )

        schema_cls, schema_name, schema_version = schema_entry
        if (
            capability.schema_name != schema_name
            or capability.schema_version != schema_version
        ):
            raise BadRequestException(
                "캡빌리티에 저장된 Schema 버전이 현재 레지스트리와 "
                "일치하지 않습니다 — 관리자에게 문의하세요.",
            )

        try:
            validated_fields = schema_cls.model_validate(
                data.required_fields,
            )
        except ValidationError as exc:
            raise BadRequestException(
                f"필수 입력 검증에 실패했습니다: {exc}",
            ) from exc

        required_fields_canonical = canonical_json(
            json.loads(validated_fields.model_dump_json()),
        )
        required_fields_fingerprint = sha256_hex(required_fields_canonical)

        try:
            current = self.repository.get_current_selection_for_listing(
                listing.id, company_id,
            )
            if current is not None:
                rowcount = self.repository.supersede_selection_conditional(
                    current.id, company_id,
                )
                if rowcount != 1:
                    raise ConflictException(
                        "기존 선택이 동시에 변경되어 새 선택을 반영할 "
                        "수 없습니다.",
                    )

            selection = MarketplaceFulfillmentSelection(
                company_id=company_id,
                listing_id=listing.id,
                capability_id=capability.id,
                fulfillment_mode=data.fulfillment_mode,
                required_fields_json=required_fields_canonical,
                required_fields_schema_name=schema_name,
                required_fields_schema_version=schema_version,
                required_fields_fingerprint=required_fields_fingerprint,
                status=SelectionStatus.SELECTED,
                selected_by=selected_by,
                idempotency_key=data.idempotency_key,
            )
            selection = self.repository.add_selection_no_commit(selection)

            # 2026-08-01 Gate 5(CTO 2차 지적) 반영 — 수정 전 결함:
            # repository.update_listing_status_conditional()이 정의만
            # 되어 있고 어디서도 호출되지 않아, 모든 Listing이 생성 시점
            # "DRAFT"에 영원히 고정돼 있었다("플랫폼별 등록 상태" 화면이
            # 실제 진행 상황을 전혀 반영하지 못하는 조용한 결함). 검증된
            # 판매 방식이 확정되는 이 시점(Draft 위저드 경유 여부와
            # 무관 — 위저드 없이 직접 생성된 Listing도 포함)에 DRAFT→
            # READY로 전이한다. rowcount는 확인하지 않는다(순수 표시용
            # 필드, 실제 제출 인가는 ApprovalService.current_valid_
            # approval()뿐).
            self.repository.update_listing_status_conditional(
                listing.id, company_id,
                (ListingStatus.DRAFT,), ListingStatus.READY,
            )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_selection_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner, True, []

        except Exception:
            self.db.rollback()
            raise

        events = [
            build_marketplace_listing_event(
                MarketplaceListingEventType.FULFILLMENT_MODE_SELECTED,
                listing.id,
                source="marketplace_listing",
                correlation_id=data.idempotency_key,
                payload={"fulfillment_mode": data.fulfillment_mode},
            ),
        ]

        return selection, False, events

    def get_current_selection(
        self, listing_id: int, company_id: int,
    ) -> MarketplaceFulfillmentSelection | None:

        return self.repository.get_current_selection_for_listing(
            listing_id, company_id,
        )

    def channel_selections_summary(
        self, product_candidate_id: int, company_id: int,
    ) -> list[dict]:
        """
        최종 요약(채널/계정/방식/자격/상태)에 쓰이는 행별 독립 데이터.
        한 채널의 실패가 다른 채널 행에 영향을 주지 않는다 — 각 항목은
        그 채널의 최신 Listing/Selection 상태만 반영한다. 다른 회사의
        Listing은 애초에 목록에 포함되지 않는다(company_id 스코프).
        """

        listings = self.repository.list_listings_for_candidate(
            product_candidate_id, company_id,
        )

        summary = []
        for listing in listings:
            account = self.repository.get_account_for_company(
                listing.marketplace_account_id, company_id,
            )
            selection = self.repository.get_current_selection_for_listing(
                listing.id, company_id,
            )
            channel = (
                self.repository.get_channel(account.channel_id)
                if account is not None
                else None
            )

            summary.append({
                "listing_id": listing.id,
                "channel_name": channel.name if channel else None,
                "account_name": account.account_name if account else None,
                "fulfillment_mode": (
                    selection.fulfillment_mode if selection else None
                ),
                "listing_status": listing.status,
            })

        return summary

    # --------------------------------------------------
    # 일시정지 / 재개 — 2026-08-01 Gate 5(CTO 2차 지적) 신규
    # --------------------------------------------------

    def pause_listing(self, listing_id: int, company_id: int) -> MarketplaceListing:
        """
        READY 또는 APPROVED 상태의 Listing만 일시정지할 수 있다 —
        이미 SUBMITTING/SUBMITTED인 것을 일시정지해도 이미 진행된
        제출 시도 자체를 되돌리지 않는다(그런 취소는 승인
        revoke()의 역할이다).
        """

        listing = self.repository.get_listing_for_company(
            listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException(_LISTING_NOT_FOUND)

        if listing.status == ListingStatus.PAUSED:
            return listing  # 이미 일시정지 — 재호출은 idempotent 성공.

        if listing.status not in ListingStatus.PAUSABLE_FROM:
            raise BadRequestException(
                f"{ListingStatus.PAUSABLE_FROM} 상태에서만 일시정지할 수 "
                f"있습니다. (현재: {listing.status})",
            )

        rowcount = self.repository.update_listing_status_conditional(
            listing_id, company_id,
            ListingStatus.PAUSABLE_FROM, ListingStatus.PAUSED,
        )
        if rowcount != 1:
            raise ConflictException(
                "Listing 상태가 동시에 변경되어 일시정지를 반영할 수 "
                "없습니다 — 새로고침 후 다시 시도하세요.",
            )

        self.db.commit()

        return self.repository.get_listing_for_company(listing_id, company_id)

    def resume_listing(self, listing_id: int, company_id: int) -> MarketplaceListing:
        """PAUSED만 재개할 수 있다 — 항상 READY로 돌아간다(재승인 필요)."""

        listing = self.repository.get_listing_for_company(
            listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException(_LISTING_NOT_FOUND)

        if listing.status == ListingStatus.READY:
            return listing  # 이미 재개됨 — idempotent 성공.

        if listing.status != ListingStatus.PAUSED:
            raise BadRequestException(
                f"PAUSED 상태에서만 재개할 수 있습니다. (현재: {listing.status})",
            )

        rowcount = self.repository.update_listing_status_conditional(
            listing_id, company_id,
            (ListingStatus.PAUSED,), ListingStatus.READY,
        )
        if rowcount != 1:
            raise ConflictException(
                "Listing 상태가 동시에 변경되어 재개를 반영할 수 "
                "없습니다 — 새로고침 후 다시 시도하세요.",
            )

        self.db.commit()

        return self.repository.get_listing_for_company(listing_id, company_id)


__all__ = [
    "MarketplaceListingService",
]
