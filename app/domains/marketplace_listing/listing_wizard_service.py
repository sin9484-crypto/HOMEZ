"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_service.py

Gate I(2026-08-08) — 상품등록 통합 마법사 Service. Transaction 경계
(commit/rollback)는 이 파일이 소유한다(repository.py는 *_no_commit/
조건부 UPDATE만 제공) — 이 도메인 전체와 동일한 원칙.

이 파일 자신은 새 비즈니스 규칙을 거의 만들지 않는다 — 단계 저장은
그대로 JSON 컬럼에 검증된 값을 옮겨 담고, 실제 판단(사전검사/마진
계산/승인 패키지/제출)은 각각 전담 모듈(listing_wizard_precheck.py/
margin_calculator.py/listing_wizard_approval.py/listing_wizard_
submission.py)에 위임한다.
=========================================================
"""

import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability
from app.core.exceptions import UnauthorizedException
from app.core.recent_auth import consume_recent_auth_token
from app.domains.marketplace_listing.constants import MAX_BULK_ARCHIVE_COUNT
from app.domains.marketplace_listing.constants import MAX_BULK_MARGIN_APPLY_COUNT
from app.domains.marketplace_listing.constants import MAX_BULK_SUBMIT_COUNT
from app.domains.marketplace_listing.constants import SubmissionStatus
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.constants import WizardStep
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)
from app.domains.marketplace_listing.listing_wizard_approval import (
    build_approval_package,
)
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    WizardApprovalNonceStatus,
)
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    clear_wizard_approval_nonce,
)
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    generate_wizard_approval_nonce,
)
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    mark_nonce_consumed,
)
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    verify_nonce,
)
from app.domains.marketplace_listing.listing_wizard_csv_export import (
    MAX_CSV_EXPORT_ROWS,
)
from app.domains.marketplace_listing.listing_wizard_csv_export import (
    build_wizards_csv,
)
from app.domains.marketplace_listing.listing_wizard_revoke_nonce import (
    WizardRevokeNonceStatus,
)
from app.domains.marketplace_listing.listing_wizard_revoke_nonce import (
    generate_wizard_revoke_nonce,
)
from app.domains.marketplace_listing.listing_wizard_revoke_nonce import (
    mark_nonce_consumed as revoke_mark_nonce_consumed,
)
from app.domains.marketplace_listing.listing_wizard_revoke_nonce import (
    verify_nonce as revoke_verify_nonce,
)
from app.domains.marketplace_listing.listing_wizard_precheck import (
    run_precheck,
)
from app.domains.marketplace_listing.listing_wizard_repository import (
    ListingWizardRepository,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    ApprovalPreviewResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardApproveRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchivePreviewResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchiveRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchiveResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchiveResultItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkMarginApplyPreviewResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkMarginApplyRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkMarginApplyResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkMarginApplyResultItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkSubmitRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkSubmitResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkSubmitResultItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardChannelsUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCloneRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCreateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardDraftUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardEconomicsUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardFulfillmentUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardMediaUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardResultChannelItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardResultsResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardRevokeApprovalRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardRevokePreviewResponse,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardSourceUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardSubmitRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardValidateResponse,
)
from app.domains.marketplace_listing.listing_wizard_submission import (
    submit_wizard_channels,
)
from app.domains.marketplace_listing.margin_calculator import (
    calculate_economics_batch,
)
from app.domains.marketplace_listing.margin_calculator import (
    derive_sale_price_for_margin_rate,
)
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.repository import MarketplaceListingRepository

_WIZARD_NOT_FOUND = "상품등록 위저드를 찾을 수 없습니다."
_VERSION_CONFLICT = (
    "이 위저드는 그 사이에 다른 곳에서 변경됐습니다 — 다시 불러온 뒤 "
    "시도하세요."
)

# 2026-08-28 "대기 상품 정리" — 사용자 결정의 "삭제 금지 상태" 목록을
# 그대로 사람이 읽을 수 있는 사유로 옮긴다. 여기 없는 상태는 일반
# 문구로 대체한다(_deletion_block_reason 참고).
_DELETE_BLOCK_REASON_BY_STATUS = {
    WizardStatus.APPROVED: "승인된 항목입니다 — 먼저 승인을 취소해야 삭제할 수 있습니다.",
    WizardStatus.SUBMITTING: "현재 쿠팡에 상품을 등록하고 있습니다.",
    WizardStatus.PARTIALLY_SUCCEEDED: "쿠팡에 이미 등록된 상품입니다.",
    WizardStatus.SUCCEEDED: "쿠팡에 이미 등록된 상품입니다.",
    WizardStatus.ARCHIVED: "이미 삭제(보관)된 항목입니다.",
}


def deletion_eligibility(wizard: ListingWizard) -> tuple[bool, str | None]:
    """
    (삭제 가능 여부, 불가능하면 사람이 읽을 수 있는 사유). 이 함수는
    읽기 전용 판정만 하고 아무것도 바꾸지 않는다 — 목록/상세 조회와
    실제 archive() 양쪽에서 재사용해 "화면에 보이는 판정"과 "실제로
    서버가 강제하는 판정"이 항상 같은 코드 경로에서 나오게 한다.

    상품 제출 여부가 불확실하면 삭제하지 않는다(fail-closed) —
    materialized_listing_ids_json이 비어있지 않으면(쿠팡 Listing ID
    등 외부 참조가 하나라도 남아있으면) status가 무엇이든 무조건
    차단한다(상태 목록만으로는 잡히지 않는 경계 사례까지 이중으로
    방어).
    """

    if wizard.status not in WizardStatus.DELETABLE_FROM:
        reason = _DELETE_BLOCK_REASON_BY_STATUS.get(
            wizard.status,
            "상품 상태를 확인할 수 없어 안전을 위해 삭제하지 않았습니다.",
        )
        return False, reason

    materialized = json.loads(wizard.materialized_listing_ids_json or "[]")
    if materialized:
        return False, "쿠팡에 이미 등록된 상품입니다."

    return True, None


class ListingWizardService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = ListingWizardRepository(db)

    # --------------------------------------------------
    # 조회 / 생성
    # --------------------------------------------------

    def get(self, wizard_id: int, company_id: int) -> ListingWizard:

        wizard = self.repository.get_for_company(wizard_id, company_id)
        if wizard is None:
            raise NotFoundException(_WIZARD_NOT_FOUND)

        return wizard

    def list_wizards(
        self, company_id: int, status: str | None = None,
        limit: int = 100, offset: int = 0, include_archived: bool = False,
    ) -> list[ListingWizard]:

        return self.repository.list_for_company(
            company_id, status, limit, offset,
            include_archived=include_archived,
        )

    def export_csv(
        self, company_id: int, *, include_economics: bool,
        locale: str = "ko-KR", status: str | None = None,
    ) -> str:
        """
        Gate U-3(2026-08-10) — 회사 범위로 위저드를 CSV로 내보낸다.
        `include_economics=False`면 금액 관련 컬럼 자체가 CSV에서
        빠진다(Gate U-1과 동일한 원칙 — 값이 아니라 컬럼을 없앤다).
        상한(MAX_CSV_EXPORT_ROWS)+1건을 조회해 초과분을 조용히
        자르지 않고 명시적으로 차단한다(status_sync_service.py::
        export_csv()와 동일한 정책).
        """

        wizards = self.repository.list_for_company(
            company_id, status, limit=MAX_CSV_EXPORT_ROWS + 1, offset=0,
        )
        if len(wizards) > MAX_CSV_EXPORT_ROWS:
            raise BadRequestException(
                f"내보낼 수 있는 최대 행수({MAX_CSV_EXPORT_ROWS}개)를 "
                "초과했습니다 — 필터를 좁혀 다시 시도하세요.",
            )

        return build_wizards_csv(
            wizards, include_economics=include_economics, locale=locale,
        )

    def create(
        self, data: WizardCreateRequest, created_by: int, company_id: int,
    ) -> tuple[ListingWizard, bool]:

        existing = self.repository.get_by_company_creation_idempotency_key(
            company_id, data.creation_idempotency_key,
        )
        if existing is not None:
            return existing, True

        wizard = ListingWizard(
            company_id=company_id,
            created_by_user_id=created_by,
            current_step=WizardStep.SOURCE,
            status=WizardStatus.DRAFT,
            source_type=data.source_type,
            product_candidate_id=data.product_candidate_id,
            cloned_from_wizard_id=data.cloned_from_wizard_id,
            creation_idempotency_key=data.creation_idempotency_key,
        )

        try:
            wizard = self.repository.add_no_commit(wizard)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            winner = self.repository.get_by_company_creation_idempotency_key(
                company_id, data.creation_idempotency_key,
            )
            if winner is None:
                raise
            return winner, True

        return wizard, False

    def clone(
        self, wizard_id: int, company_id: int, created_by: int,
        data: WizardCloneRequest,
    ) -> tuple[ListingWizard, bool]:

        source = self.get(wizard_id, company_id)

        existing = self.repository.get_by_company_creation_idempotency_key(
            company_id, data.creation_idempotency_key,
        )
        if existing is not None:
            return existing, True

        # 이전 위저드의 검증·승인·제출 이력은 물려받지 않는다 — 승인은
        # 그 시점의 스냅샷일 뿐 재사용 가능한 자격이 아니다(내용을
        # 그대로 복제해도 새 위저드는 처음부터 다시 사전검사·승인을
        # 거쳐야 한다).
        clone = ListingWizard(
            company_id=company_id,
            created_by_user_id=created_by,
            current_step=WizardStep.SOURCE,
            status=WizardStatus.DRAFT,
            source_type="CLONE",
            product_candidate_id=source.product_candidate_id,
            cloned_from_wizard_id=source.id,
            draft_json=source.draft_json,
            selected_media_asset_ids_json=(
                source.selected_media_asset_ids_json
            ),
            channel_selections_json=source.channel_selections_json,
            economics_input_json=source.economics_input_json,
            creation_idempotency_key=data.creation_idempotency_key,
        )

        try:
            clone = self.repository.add_no_commit(clone)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            winner = self.repository.get_by_company_creation_idempotency_key(
                company_id, data.creation_idempotency_key,
            )
            if winner is None:
                raise
            return winner, True

        return clone, False

    # --------------------------------------------------
    # 단계별 PATCH
    # --------------------------------------------------

    def _assert_editable(self, wizard: ListingWizard) -> None:

        if wizard.status not in WizardStatus.EDITABLE:
            raise ConflictException(
                f"현재 상태({wizard.status})에서는 위저드를 수정할 수 "
                "없습니다.",
            )

    def _conflict_detail(self, wizard_id: int, company_id: int) -> dict:
        """
        Gate J(2026-08-08) — 버전 충돌(409) 시 구조화된 detail을
        반환한다. 클라이언트가 별도 GET 없이 "누가 마지막으로 저장
        했는지·지금 서버의 진짜 버전이 무엇인지"를 바로 알 수 있게 해,
        재조회→내 변경사항 재적용→재저장 흐름을 한 번의 오류 응답만
        보고 시작할 수 있게 한다. 이 저장소의 다른 구조화 오류(Gate
        G/H)와 동일하게 detail을 dict로 전달한다(FastAPI가 그대로
        JSON 직렬화).
        """

        current = self.get(wizard_id, company_id)

        return {
            "error_code": "WIZARD_VERSION_CONFLICT",
            "current_version": current.version,
            "current_step": current.current_step,
            "status": current.status,
            "autosave_client_token": current.autosave_client_token,
            "updated_at": current.updated_at.isoformat(),
        }

    def update_source(
        self, wizard_id: int, company_id: int,
        data: WizardSourceUpdateRequest,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)
        self._assert_editable(wizard)

        rowcount = self.repository.update_source_conditional(
            wizard_id, company_id, data.expected_version,
            WizardStatus.EDITABLE, data.product_candidate_id,
            WizardStep.DRAFT,
            autosave_client_token=data.autosave_client_token,
        )
        if rowcount == 0:
            raise ConflictException(self._conflict_detail(wizard_id, company_id))
        self.db.commit()

        return self.get(wizard_id, company_id)

    def update_draft(
        self, wizard_id: int, company_id: int,
        data: WizardDraftUpdateRequest,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)
        self._assert_editable(wizard)

        payload = data.model_dump(
            exclude={"expected_version", "autosave_client_token"},
        )
        rowcount = self.repository.update_draft_conditional(
            wizard_id, company_id, data.expected_version,
            WizardStatus.EDITABLE,
            json.dumps(payload, ensure_ascii=False), WizardStep.MEDIA,
            autosave_client_token=data.autosave_client_token,
        )
        if rowcount == 0:
            raise ConflictException(self._conflict_detail(wizard_id, company_id))
        self.db.commit()

        return self.get(wizard_id, company_id)

    def update_media(
        self, wizard_id: int, company_id: int,
        data: WizardMediaUpdateRequest,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)
        self._assert_editable(wizard)

        # 2026-08-28 사용자 결정으로 2026-08-20 CTO 지시(RIGHTS_UNVERIFIED
        # 선택 자체를 하드 차단)를 대체한다 — 증빙 미제출만으로는 어떤
        # 기능도 차단하지 않는다. 대신 화면이 경고를 표시하고
        # ImageRightsAcknowledgementService가 사용자의 진행 선택을
        # append-only로 기록한다(app/domains/media_asset/
        # rights_evidence_service.py). 실제 판매채널 제출 직전에는
        # 별도로 다시 경고·기록한다(listing_wizard_live_service.py).
        #
        # RIGHTS_DENIED(명시적 사용금지·권리철회·삭제요청·신고/분쟁)는
        # 이 정책 반전과 무관하게 계속 하드 차단된다 — "증빙 미제출"과
        # "명시적으로 금지됨"은 반대 방향의 서로 다른 상태다.
        if data.selected_media_asset_ids:
            from app.domains.media_asset.model import MediaAsset

            denied = (
                self.db.query(MediaAsset.id)
                .filter(MediaAsset.company_id == company_id)
                .filter(MediaAsset.id.in_(data.selected_media_asset_ids))
                .filter(MediaAsset.rights_status == "RIGHTS_DENIED")
                .all()
            )
            if denied:
                ids = ", ".join(str(row[0]) for row in denied)
                raise BadRequestException(
                    "RIGHTS_DENIED: 사용이 금지된 이미지는 등록에 선택할 "
                    f"수 없습니다(이미지 #{ids}).",
                )

        rowcount = self.repository.update_media_conditional(
            wizard_id, company_id, data.expected_version,
            WizardStatus.EDITABLE,
            json.dumps(data.selected_media_asset_ids),
            WizardStep.CHANNELS,
            autosave_client_token=data.autosave_client_token,
        )
        if rowcount == 0:
            raise ConflictException(self._conflict_detail(wizard_id, company_id))
        self.db.commit()

        return self.get(wizard_id, company_id)

    def update_channels(
        self, wizard_id: int, company_id: int,
        data: WizardChannelsUpdateRequest,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)
        self._assert_editable(wizard)

        # 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 격리 브라우저 E2E
        # 중 실제로 재현된 데이터 손실 결함(4단계로 되돌아가 "다음"을
        # 다시 누르면 5단계에서 이미 저장한 fulfillment_mode/
        # required_fields/items/channel_policy_attributes가 통째로
        # 사라짐)을 고친다. 계속 선택된 계정은 기존 선택 dict를 그대로
        # 보존하고, 새로 추가된 계정만 빈 selection으로 시작한다 —
        # 선택 해제된 계정의 데이터가 사라지는 것은 기존 의도된 동작
        # 그대로 유지한다(그 계정은 더 이상 authoritative 목록에 없음).
        existing_by_account = {
            sel.get("marketplace_account_id"): sel
            for sel in json.loads(wizard.channel_selections_json or "[]")
            if isinstance(sel, dict)
        }
        selections = [
            existing_by_account.get(account_id) or {"marketplace_account_id": account_id}
            for account_id in data.marketplace_account_ids
        ]
        rowcount = self.repository.update_channels_conditional(
            wizard_id, company_id, data.expected_version,
            WizardStatus.EDITABLE,
            json.dumps(selections), WizardStep.FULFILLMENT,
            autosave_client_token=data.autosave_client_token,
        )
        if rowcount == 0:
            raise ConflictException(self._conflict_detail(wizard_id, company_id))
        self.db.commit()

        return self.get(wizard_id, company_id)

    def update_fulfillment(
        self, wizard_id: int, company_id: int,
        data: WizardFulfillmentUpdateRequest,
        actor_user_id: int | None = None,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)
        self._assert_editable(wizard)

        # 4단계에서 고른 계정 집합을 그대로 대체한다 — 5단계는 "그
        # 계정들에 대해 각각 방식을 정한다"는 다음 단계이므로, 여기서
        # 넘어온 목록이 새 authoritative 목록이 된다(사전검사가 누락·
        # 불일치를 별도로 잡는다).
        from datetime import datetime
        from app.domains.marketplace_listing.category_metadata import (
            notice_input_fingerprint, validate_saved_notice_contract,
            validate_purchase_options,
        )
        selections = []
        for item in data.selections:
            selection = item.model_dump()
            from app.domains.marketplace_listing.model import (
                MarketplaceAccount, MarketplaceChannel,
            )
            account = (
                self.db.query(MarketplaceAccount)
                .filter(MarketplaceAccount.id == item.marketplace_account_id)
                .filter(MarketplaceAccount.company_id == company_id)
                .first()
            )
            channel = (
                self.db.query(MarketplaceChannel)
                .filter(MarketplaceChannel.id == account.channel_id)
                .first()
            ) if account else None
            if channel and channel.code == "COUPANG" and item.fulfillment_mode == "SELLER_FULFILLED":
                from app.domains.marketplace_listing.coupang_logistics_provider import (
                    get_cached_location,
                )
                outbound = get_cached_location(
                    company_id, wizard_id, "outbound",
                    item.outbound_shipping_place_code or "",
                )
                returns = get_cached_location(
                    company_id, wizard_id, "return",
                    item.return_center_code or "",
                )
                if outbound is None or returns is None:
                    raise BadRequestException(
                        "COUPANG_LOGISTICS_SELECTION_REQUIRED: 쿠팡 출고지와 "
                        "반품지를 조회한 뒤 선택해야 합니다."
                    )
                if not outbound.usable or not returns.usable:
                    raise BadRequestException(
                        "COUPANG_LOGISTICS_LOCATION_DISABLED: 사용할 수 없는 "
                        "출고지 또는 반품지입니다."
                    )
                if not all((
                    returns.name, returns.contact_number, returns.zip_code,
                    returns.address,
                )):
                    raise BadRequestException(
                        "COUPANG_LOGISTICS_LOCATION_INCOMPLETE: 반품지의 주소·"
                        "연락처 정보가 불완전합니다."
                    )
                required_fields = selection["required_fields"]
                required_fields.update({
                    "outboundShippingPlaceCode": outbound.code,
                    "returnCenterCode": returns.code,
                    "returnChargeName": returns.name,
                    "companyContactNumber": returns.contact_number,
                    "returnZipCode": returns.zip_code,
                    "returnAddress": returns.address,
                    "returnAddressDetail": returns.address_detail or None,
                })
            attrs = selection["channel_policy_attributes"]
            confirmed = selection["channel_policy_confirmed_evidence_rule_codes"]
            # 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 실제 백업 DB
            # 증거(Wizard #6)에서 구매옵션이 Category Metadata와
            # 무관한 자유 텍스트로 저장된 것을 확인했다(V7-COUPANG-
            # ATTR-001). 카테고리 조회 시점에 프론트가 함께 저장한
            # `purchase_option_field_definitions` 스냅샷을 기준으로,
            # exposed+required 속성이 비어 있거나 SELECT 허용값을
            # 벗어나면 저장 자체를 막는다(fail-closed) — 제출 시점까지
            # 미루지 않는다.
            field_definitions = attrs.get("purchase_option_field_definitions")
            # 2026-08-29 — 옵션 조합 UI(Section 2A). items[]에 자기
            # 자신의 optionAttributes(구매옵션 조합)를 가진 항목이
            # 있으면 다중 옵션 상품으로 보고 item마다 검증한다. 없으면
            # 기존처럼 상품 전체 공유 purchase_options만 검증한다
            # (단일 옵션 상품, 기존 동작 그대로).
            raw_items = selection["required_fields"].get("items") or []
            per_item_option_sets = [
                item.get("optionAttributes") for item in raw_items
                if isinstance(item, dict) and item.get("optionAttributes")
            ]
            if field_definitions and per_item_option_sets:
                for option_attrs in per_item_option_sets:
                    item_missing = validate_purchase_options(field_definitions, option_attrs)
                    if item_missing:
                        raise BadRequestException(
                            "COUPANG_PURCHASE_OPTION_REQUIRED: 옵션 조합 "
                            f"{option_attrs}에 다음 필수 구매옵션이 비어 있거나 "
                            "허용된 값이 아닙니다: " + ", ".join(item_missing)
                        )
                seen_combos = set()
                for option_attrs in per_item_option_sets:
                    combo_key = tuple(sorted(option_attrs.items()))
                    if combo_key in seen_combos:
                        raise BadRequestException(
                            "COUPANG_DUPLICATE_OPTION_COMBINATION: 동일한 구매옵션 "
                            f"조합이 중복 등록되었습니다: {dict(combo_key)}"
                        )
                    seen_combos.add(combo_key)
            elif field_definitions:
                missing_options = validate_purchase_options(
                    field_definitions, attrs.get("purchase_options") or {},
                )
                if missing_options:
                    raise BadRequestException(
                        "COUPANG_PURCHASE_OPTION_REQUIRED: 다음 필수 구매옵션이 "
                        "비어 있거나 허용된 값이 아닙니다: "
                        + ", ".join(missing_options)
                    )
            if raw_items:
                seen_skus_save: set[str] = set()
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    sku = item.get("externalVendorSku")
                    if sku and sku in seen_skus_save:
                        raise BadRequestException(
                            f"COUPANG_DUPLICATE_SKU: 옵션 간 SKU가 중복되었습니다: {sku}"
                        )
                    if sku:
                        seen_skus_save.add(sku)
            if "CATEGORY_NOTICE_INFO_REQUIRED" in confirmed:
                attrs["notice_confirmed_at"] = datetime.utcnow().isoformat()
                attrs["notice_confirmed_by_user_id"] = actor_user_id
                if all(attrs.get(key) for key in (
                    "official_category_code", "category_metadata_version",
                    "category_metadata_fingerprint", "notice_information",
                    "notice_required_field_keys",
                )):
                    attrs["notice_input_fingerprint"] = notice_input_fingerprint(
                        str(attrs["official_category_code"]),
                        str(attrs["category_metadata_version"]),
                        str(attrs["category_metadata_fingerprint"]),
                        attrs["notice_information"],
                    )
                missing = validate_saved_notice_contract(attrs)
                if missing:
                    raise BadRequestException(
                        "CATEGORY_NOTICE_INFO_REQUIRED 확인 근거가 불완전합니다: "
                        + ", ".join(missing)
                    )
                required_fields = selection["required_fields"]
                category_code = str(attrs["official_category_code"])
                if not category_code.isdigit():
                    raise BadRequestException(
                        "CATEGORY_METADATA_INVALID: 쿠팡 공식 카테고리 "
                        "코드는 숫자여야 합니다."
                    )
                existing_code = required_fields.get("displayCategoryCode")
                if existing_code not in (None, "", category_code, int(category_code)):
                    raise BadRequestException(
                        "CATEGORY_METADATA_PAYLOAD_MISMATCH: 저장 카테고리와 "
                        "제출 Payload 카테고리가 다릅니다."
                    )
                required_fields["displayCategoryCode"] = int(category_code)
                notices_by_category: dict[str, list[dict]] = {}
                for key, value in attrs["notice_information"].items():
                    if "::" not in key:
                        continue
                    category_name, detail_name = key.split("::", 1)
                    notices_by_category.setdefault(category_name, []).append({
                        "noticeCategoryDetailName": detail_name,
                        "content": value,
                    })
                required_fields["notices"] = [
                    {
                        "noticeCategoryName": category_name,
                        "noticeCategoryDetailNames": details,
                    }
                    for category_name, details in notices_by_category.items()
                ]
            selections.append(selection)
        rowcount = self.repository.update_channels_conditional(
            wizard_id, company_id, data.expected_version,
            WizardStatus.EDITABLE,
            json.dumps(selections, ensure_ascii=False), WizardStep.ECONOMICS,
            autosave_client_token=data.autosave_client_token,
        )
        if rowcount == 0:
            raise ConflictException(self._conflict_detail(wizard_id, company_id))
        self.db.commit()

        return self.get(wizard_id, company_id)

    def update_economics(
        self, wizard_id: int, company_id: int,
        data: WizardEconomicsUpdateRequest,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)
        self._assert_editable(wizard)

        # Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        # 강제(PROFITABILITY_CALCULATION). 위저드 6단계 마진 미리보기
        # 계산의 실제 진입점.
        require_active_capability(CapabilityCode.PROFITABILITY_CALCULATION)

        results = calculate_economics_batch(data.items)

        input_json = json.dumps(
            [item.model_dump(mode="json") for item in data.items],
            ensure_ascii=False,
        )
        result_json = json.dumps(
            [item.model_dump(mode="json") for item in results],
            ensure_ascii=False,
        )

        rowcount = self.repository.update_economics_conditional(
            wizard_id, company_id, data.expected_version,
            WizardStatus.EDITABLE,
            input_json, result_json, WizardStep.PRECHECK,
            autosave_client_token=data.autosave_client_token,
        )
        if rowcount == 0:
            raise ConflictException(self._conflict_detail(wizard_id, company_id))
        self.db.commit()

        return self.get(wizard_id, company_id)

    # --------------------------------------------------
    # 사전검사 / 승인 / 제출 / 결과
    # --------------------------------------------------

    def validate(
        self, wizard_id: int, company_id: int, expected_version: int,
        actor_user_id: int | None = None,
    ) -> WizardValidateResponse:

        wizard = self.get(wizard_id, company_id)
        self._assert_editable(wizard)

        result = run_precheck(wizard, self.db, company_id)
        new_status = (
            WizardStatus.READY_FOR_APPROVAL if result.status
            == "READY_FOR_APPROVAL" else WizardStatus.NEEDS_CORRECTION
        )

        rowcount = self.repository.update_validation_result_conditional(
            wizard_id, company_id, expected_version, WizardStatus.EDITABLE,
            result.model_dump_json(), new_status, WizardStep.PRECHECK,
        )
        if rowcount == 0:
            raise ConflictException(_VERSION_CONFLICT)
        self.db.commit()

        if new_status == WizardStatus.READY_FOR_APPROVAL:
            dispatch_operational_event(
                self.db, "LISTING_FINAL_APPROVAL_NEEDED",
                company_id=company_id, user_id=actor_user_id,
                idempotency_key=f"listing-final-approval:{wizard_id}:{expected_version + 1}",
                title="상품 등록 최종 승인이 필요합니다",
                message=f"상품 등록 작업 #{wizard_id}가 사전검사를 통과했습니다.",
                link_path="listing-wizard", entity_ref=f"listing_wizard:{wizard_id}",
                reason="최종 승인 후에만 제출할 수 있습니다.",
                entity_summary=f"상품 등록 작업 #{wizard_id}",
            )

        return result

    def approval_preview(
        self, wizard_id: int, company_id: int,
    ) -> ApprovalPreviewResponse:

        wizard = self.get(wizard_id, company_id)
        if wizard.status != WizardStatus.READY_FOR_APPROVAL:
            raise ConflictException(
                "사전검사를 통과(READY_FOR_APPROVAL)한 위저드만 승인 "
                "미리보기를 생성할 수 있습니다.",
            )

        package, fingerprint = build_approval_package(
            wizard, self.db, company_id, wizard.status,
        )

        rowcount = self.repository.update_approval_package_conditional(
            wizard_id, company_id, wizard.version,
            (WizardStatus.READY_FOR_APPROVAL,),
            json.dumps(package, ensure_ascii=False), fingerprint,
        )
        if rowcount == 0:
            raise ConflictException(_VERSION_CONFLICT)
        self.db.commit()

        wizard = self.get(wizard_id, company_id)
        nonce, expires_at = generate_wizard_approval_nonce(wizard_id)

        return ApprovalPreviewResponse(
            approval_package=package, fingerprint=fingerprint,
            approval_nonce=nonce, nonce_expires_at=expires_at,
            version=wizard.version,
        )

    def approve(
        self, wizard_id: int, company_id: int, approved_by: int,
        recent_auth_token: str | None, data: WizardApproveRequest,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)

        if not consume_recent_auth_token(recent_auth_token, approved_by):
            raise UnauthorizedException(
                "승인 전 현재 비밀번호로 다시 확인해야 합니다.",
            )

        nonce_status = verify_nonce(wizard_id, data.approval_nonce)
        if nonce_status != WizardApprovalNonceStatus.VALID:
            raise UnauthorizedException(
                "승인 요청이 유효하지 않습니다 — 승인 미리보기를 다시 "
                f"불러온 뒤 시도하세요({nonce_status.value}).",
            )

        if wizard.approval_fingerprint != data.expected_fingerprint:
            raise ConflictException(
                "승인 대상 내용이 미리보기를 본 뒤 바뀌었습니다 — 승인 "
                "미리보기를 다시 불러온 뒤 시도하세요.",
            )

        if not data.product_image_match_confirmed:
            raise BadRequestException(
                "PRODUCT_IMAGE_MATCH_NOT_CONFIRMED: 선택한 이미지가 등록 "
                "상품(상품명·용량·향·구성)과 일치함을 최종 검토 화면에서 "
                "확인해야 승인할 수 있습니다.",
            )

        # 2026-08-28 사용자 결정 — 권리 미확인 이미지도 8단계 승인을
        # 막지 않는다(2026-08-20 3차 지시 대체). 경고·기록은 화면과
        # ImageRightsAcknowledgementService가 담당한다. RIGHTS_DENIED는
        # 예외 — 계속 하드 차단(update_media()와 동일 원칙).
        selected_ids = json.loads(wizard.selected_media_asset_ids_json or "[]")
        if selected_ids:
            from app.domains.media_asset.model import MediaAsset

            denied = (
                self.db.query(MediaAsset.id)
                .filter(MediaAsset.company_id == company_id)
                .filter(MediaAsset.id.in_(selected_ids))
                .filter(MediaAsset.rights_status == "RIGHTS_DENIED")
                .all()
            )
            if denied:
                ids = ", ".join(str(row[0]) for row in denied)
                raise BadRequestException(
                    "RIGHTS_DENIED: 사용이 금지된 이미지가 선택돼 있어 "
                    f"승인할 수 없습니다(이미지 #{ids}).",
                )

        history = json.loads(wizard.approval_history_json or "[]")
        history.append({
            "action": "APPROVED",
            "by_user_id": approved_by,
            "at": datetime.utcnow().isoformat(),
        })

        rowcount = self.repository.mark_approved_conditional(
            wizard_id, company_id, data.expected_version,
            data.expected_fingerprint, approved_by, datetime.utcnow(),
            json.dumps(history, ensure_ascii=False),
        )
        if rowcount == 0:
            raise ConflictException(
                "승인 조건(버전/상태/지문)이 일치하지 않습니다 — 다시 "
                "확인 후 시도하세요.",
            )
        self.db.commit()
        mark_nonce_consumed(wizard_id)

        return self.get(wizard_id, company_id)

    def revoke_approval_preview(
        self, wizard_id: int, company_id: int,
    ) -> WizardRevokePreviewResponse:
        """
        Gate Q-1(2026-08-09) — 지금 취소하려는 승인의 내용을 그대로
        보여주고, 그것을 실제로 봤다는 증거인 취소 nonce를 발급한다.
        승인 미리보기(`approval_preview`)와 완전히 대칭인 설계 —
        재계산하지 않고 이미 저장된 Package/fingerprint를 그대로
        반환한다(재계산하면 취소 대상 자체가 바뀌어버린다).
        """

        wizard = self.get(wizard_id, company_id)
        if wizard.status != WizardStatus.APPROVED:
            raise ConflictException(
                "승인(APPROVED)된 위저드만 승인을 취소할 수 있습니다.",
            )

        nonce, expires_at = generate_wizard_revoke_nonce(wizard_id)

        return WizardRevokePreviewResponse(
            approval_package=json.loads(wizard.approval_package_json or "{}"),
            fingerprint=wizard.approval_fingerprint,
            approved_by_user_id=wizard.approved_by_user_id,
            approved_at=wizard.approved_at,
            revoke_nonce=nonce,
            nonce_expires_at=expires_at,
            version=wizard.version,
        )

    def revoke_approval(
        self, wizard_id: int, company_id: int, revoked_by: int,
        recent_auth_token: str | None,
        data: WizardRevokeApprovalRequest,
    ) -> ListingWizard:
        """
        Gate Q-1(2026-08-09) — APPROVED 상태를 벗어나 다시 편집할 수
        있게 한다. SUBMITTING 이후(제출이 시작된 뒤)로는 절대 도달할
        수 없다 — `revoke_approval_conditional()`이 `WHERE status ==
        'APPROVED'`로 걸어 두므로, 이미 제출이 시작된 위저드는 이
        메서드가 무엇을 하든 rowcount=0으로 실패한다. 기존 승인
        Package·fingerprint·승인자·승인시각은 `approval_history`에
        스냅샷으로 보존하고(제출 스냅샷 자체는 절대 덮어쓰지 않는다),
        "현재 유효한 승인" 슬롯(`approval_fingerprint`/`approved_by_
        user_id`/`approved_at`)만 비운다.
        """

        wizard = self.get(wizard_id, company_id)

        if not consume_recent_auth_token(recent_auth_token, revoked_by):
            raise UnauthorizedException(
                "승인 취소 전 현재 비밀번호로 다시 확인해야 합니다.",
            )

        nonce_status = revoke_verify_nonce(wizard_id, data.revoke_nonce)
        if nonce_status != WizardRevokeNonceStatus.VALID:
            raise UnauthorizedException(
                "승인 취소 요청이 유효하지 않습니다 — 승인 취소 미리보기를 "
                f"다시 불러온 뒤 시도하세요({nonce_status.value}).",
            )

        history = json.loads(wizard.approval_history_json or "[]")
        history.append({
            "action": "APPROVAL_REVOKED",
            "by_user_id": revoked_by,
            "at": datetime.utcnow().isoformat(),
            "reason": data.reason,
            "preserved_fingerprint": wizard.approval_fingerprint,
            "preserved_approval_package": json.loads(
                wizard.approval_package_json or "{}",
            ),
            "preserved_approved_by_user_id": wizard.approved_by_user_id,
            "preserved_approved_at": (
                wizard.approved_at.isoformat()
                if wizard.approved_at else None
            ),
        })

        rowcount = self.repository.revoke_approval_conditional(
            wizard_id, company_id, data.expected_version,
            json.dumps(history, ensure_ascii=False),
        )
        if rowcount == 0:
            raise ConflictException(
                "승인 취소 조건(버전/상태)이 일치하지 않습니다 — 이미 "
                "제출이 시작됐거나 다른 곳에서 먼저 처리됐을 수 있습니다.",
            )
        self.db.commit()
        revoke_mark_nonce_consumed(wizard_id)
        clear_wizard_approval_nonce(wizard_id)

        return self.get(wizard_id, company_id)

    def submit(
        self, wizard_id: int, company_id: int, approved_by: int,
        data: WizardSubmitRequest,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)

        if data.execution_mode == "DRAFT_SAVE":
            # 각 단계 PATCH가 이미 저장했으므로 별도 반영할 것이 없다 —
            # 상태 전이 없이 그대로 반환한다.
            return wizard

        if wizard.status != WizardStatus.APPROVED:
            raise ConflictException(
                "승인(APPROVED)된 위저드만 제출할 수 있습니다.",
            )

        rowcount = self.repository.update_status_conditional(
            wizard_id, company_id, data.expected_version,
            (WizardStatus.APPROVED,), WizardStatus.SUBMITTING,
            current_step=WizardStep.EXECUTION,
        )
        if rowcount == 0:
            raise ConflictException(_VERSION_CONFLICT)
        self.db.commit()

        wizard = self.get(wizard_id, company_id)
        results = submit_wizard_channels(
            wizard, self.db, company_id, approved_by,
        )

        return self._persist_submission_results(
            wizard, company_id, results,
        )

    def retry_failed_channels(
        self, wizard_id: int, company_id: int, approved_by: int,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)
        if wizard.status not in (
            WizardStatus.PARTIALLY_SUCCEEDED, WizardStatus.FAILED,
        ):
            raise ConflictException(
                "부분성공(PARTIALLY_SUCCEEDED) 또는 실패(FAILED) 상태의 "
                "위저드만 재시도할 수 있습니다.",
            )

        # Gate Q-1(2026-08-09) — "실패한 채널 재시도는 승인된 동일
        # fingerprint일 때만 허용"이라는 요구는 이 상태 전이 구조 자체가
        # 이미 만족한다: PARTIALLY_SUCCEEDED/FAILED는 SUBMITTING을 거친
        # 뒤에만 도달하고, 승인 취소(`revoke_approval`)는 정확히
        # APPROVED 상태에서만 걸리는 조건부 UPDATE라(`WHERE status ==
        # 'APPROVED'`) SUBMITTING 이후로는 절대 도달할 수 없다 — 즉 이
        # 시점의 wizard는 최초 제출에 실제로 쓰인 fingerprint 이후
        # 한 번도 편집되지 않았음이 상태 기계로 보장된다. 다만 그 보장이
        # 우연이 아니라 명시적 계약임을 여기서 fail-closed로 재확인한다.
        if not wizard.approval_fingerprint:
            raise ConflictException(
                "이 위저드에 유효한 승인 지문이 없습니다 — 재시도할 수 "
                "없습니다.",
            )

        materialized = json.loads(wizard.materialized_listing_ids_json or "[]")
        failed_account_ids = [
            entry["marketplace_account_id"] for entry in materialized
            if entry.get("status") != SubmissionStatus.PENDING
        ]
        if not failed_account_ids:
            return wizard

        retry_results = submit_wizard_channels(
            wizard, self.db, company_id, approved_by,
            account_ids=failed_account_ids,
            # 최초 제출과 같은 idempotency key를 재사용하면 과거 실패
            # 결과만 반환되어 안전 모드 변경 뒤에도 실제 재평가가 되지
            # 않는다. 현재 version은 한 번의 의도적 재시도 동안에는
            # 안정적이고, 결과 저장 후 증가하므로 중복 클릭은 막으면서
            # 다음 의도적 재시도는 새 요청으로 구분할 수 있다.
            retry_key_suffix=f"retry-v{wizard.version}",
        )

        by_account = {
            entry["marketplace_account_id"]: entry for entry in materialized
        }
        for result in retry_results:
            by_account[result.marketplace_account_id] = {
                "marketplace_account_id": result.marketplace_account_id,
                "listing_id": result.listing_id,
                "status": result.status,
                "error_reason": result.error_reason,
            }

        return self._persist_materialized(
            wizard, company_id, list(by_account.values()),
        )

    def _persist_submission_results(
        self, wizard: ListingWizard, company_id: int, results: list,
    ) -> ListingWizard:

        materialized = [
            {
                "marketplace_account_id": r.marketplace_account_id,
                "listing_id": r.listing_id,
                "status": r.status,
                "error_reason": r.error_reason,
            }
            for r in results
        ]
        return self._persist_materialized(wizard, company_id, materialized)

    def _persist_materialized(
        self, wizard: ListingWizard, company_id: int, materialized: list[dict],
    ) -> ListingWizard:

        # 2026-08-05 최종 제품화 Phase 4/ListingStatus 문서에 이미 기록된
        # 사실과 동일한 이유로, MarketplaceSubmission의 "구조적으로
        # 성공" 상태값은 SUBMITTED가 아니라 PENDING이다(실제 마켓
        # 공개 제출을 뜻하는 SUBMITTED 개념 자체가 이 코드베이스엔
        # 없다 — 외부 네트워크 호출 코드가 아예 없기 때문). 여기서도
        # 그 기존 계약을 그대로 따른다.
        succeeded = [
            m for m in materialized
            if m.get("status") == SubmissionStatus.PENDING
        ]
        failed = [
            m for m in materialized
            if m.get("status") != SubmissionStatus.PENDING
        ]

        if failed and succeeded:
            final_status = WizardStatus.PARTIALLY_SUCCEEDED
        elif failed:
            final_status = WizardStatus.FAILED
        else:
            final_status = WizardStatus.SUCCEEDED

        # 채널별 실제 생성(Listing/Selection/Approval/Submission)은 이미
        # 각자의 서비스가 독립적으로 commit했다 — 여기서 rowcount==0이
        # 나오더라도(동시 요청 경합) 그 결과 자체를 잃지 않는다. 다만
        # 이 위저드 행에 결과를 반영하지 못했을 뿐이므로, 그 경우
        # 재조회만으로 최신 wizard 행을 반환한다(무음 실패가 아니라
        # "이번 갱신은 반영되지 않았다"는 사실만 상위 호출자가 재조회로
        # 자연스럽게 확인하게 된다).
        rowcount = self.repository.append_materialized_listing_ids_conditional(
            wizard.id, company_id, wizard.version,
            json.dumps(materialized, ensure_ascii=False), final_status,
        )
        if rowcount:
            self.db.commit()

        if final_status in (WizardStatus.FAILED, WizardStatus.PARTIALLY_SUCCEEDED):
            dispatch_operational_event(
                self.db, "LISTING_SUBMISSION_FAILED",
                company_id=company_id, user_id=None,
                idempotency_key=f"listing-submission:{wizard.id}:{wizard.version}:{final_status}",
                title="상품 등록 제출 결과를 확인하세요",
                message=f"상품 등록 작업 #{wizard.id}에 실패한 채널이 있습니다.",
                link_path="listing-wizard", entity_ref=f"listing_wizard:{wizard.id}",
                reason="실패 원인을 확인한 뒤 재시도 승인이 필요합니다.",
                entity_summary=f"상품 등록 작업 #{wizard.id}",
            )

        return self.get(wizard.id, company_id)

    def results(
        self, wizard_id: int, company_id: int,
    ) -> WizardResultsResponse:

        wizard = self.get(wizard_id, company_id)
        materialized = json.loads(wizard.materialized_listing_ids_json or "[]")
        marketplace_repository = MarketplaceListingRepository(self.db)

        channels = []
        for row in materialized:
            enriched = dict(row)
            listing_id = row.get("listing_id")
            if listing_id is not None:
                submissions = marketplace_repository.list_submissions_for_listing(
                    listing_id, company_id,
                )
                matching = [
                    submission for submission in submissions
                    if submission.marketplace_account_id
                    == row.get("marketplace_account_id")
                ]
                if matching:
                    enriched["submission_id"] = matching[-1].id
                    enriched["status"] = matching[-1].status
                    enriched["error_reason"] = matching[-1].error_reason
                    # 2026-08-30 후속 지시(성공 경고 보존) — 화면을 다시
                    # 열어도 사라지지 않아야 하므로, 매번 이 DB 값을
                    # 그대로 다시 읽어서 채운다(별도 캐시/세션 상태 없음).
                    enriched["provider_warning_summary"] = (
                        matching[-1].provider_warning_summary
                    )
            channels.append(WizardResultChannelItem(**enriched))

        return WizardResultsResponse(
            wizard_id=wizard.id,
            status=wizard.status,
            channels=channels,
        )

    # --------------------------------------------------
    # 2026-08-28 "대기 상품 정리" — soft delete(archive) / restore
    # --------------------------------------------------

    def archive(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        deleted_by_user_id: int,
        reason: str,
        deletion_request_id: str,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)

        if wizard.status == WizardStatus.ARCHIVED:
            # 같은 요청의 재전송(네트워크 재시도 등)은 성공으로
            # 수렴시킨다 — 다른 요청이 이미 삭제한 것이면 명확히
            # 알린다(무조건 멱등 성공으로 조용히 덮지 않는다).
            if wizard.deletion_request_id == deletion_request_id:
                return wizard
            raise ConflictException(
                _DELETE_BLOCK_REASON_BY_STATUS[WizardStatus.ARCHIVED],
            )

        eligible, block_reason = deletion_eligibility(wizard)
        if not eligible:
            raise BadRequestException(block_reason)

        rowcount = self.repository.archive_conditional(
            wizard_id, company_id, expected_version,
            WizardStatus.DELETABLE_FROM, deleted_by_user_id, reason,
            deletion_request_id,
        )
        if rowcount == 0:
            # 그 사이 다른 요청이 먼저 삭제를 완료했다면 결과적으로
            # 원하는 상태(삭제됨)에 이미 도달했으므로 성공으로
            # 수렴시킨다 — 그 외(내용이 바뀌었거나 상태가 달라짐)는
            # 진짜 버전 충돌이다.
            current = self.repository.get_for_company(wizard_id, company_id)
            if current is not None and current.status == WizardStatus.ARCHIVED:
                return current
            raise ConflictException(_VERSION_CONFLICT)

        write_audit_log(
            self.db, user_id=deleted_by_user_id,
            action="LISTING_WIZARD_ARCHIVED", entity="listing_wizard",
            entity_id=str(wizard_id),
            description=(
                f"대기 상품 삭제(request={deletion_request_id}): {reason}"
            ),
            company_id=company_id,
        )
        self.db.commit()

        return self.get(wizard_id, company_id)

    def restore(
        self,
        wizard_id: int,
        company_id: int,
        expected_version: int,
        restored_by_user_id: int,
    ) -> ListingWizard:

        wizard = self.get(wizard_id, company_id)
        if wizard.status != WizardStatus.ARCHIVED:
            raise BadRequestException("삭제(보관)된 항목만 복원할 수 있습니다.")

        rowcount = self.repository.restore_conditional(
            wizard_id, company_id, expected_version, restored_by_user_id,
        )
        if rowcount == 0:
            raise ConflictException(_VERSION_CONFLICT)

        write_audit_log(
            self.db, user_id=restored_by_user_id,
            action="LISTING_WIZARD_RESTORED", entity="listing_wizard",
            entity_id=str(wizard_id), description="대기 상품 복원",
            company_id=company_id,
        )
        self.db.commit()

        return self.get(wizard_id, company_id)

    def preview_bulk_archive_count(
        self, company_id: int, status_filter: str | None,
    ) -> WizardBulkArchivePreviewResponse:

        ids = self.repository.list_ids_for_bulk_archive(
            company_id, status_filter, WizardStatus.DELETABLE_FROM,
            MAX_BULK_ARCHIVE_COUNT + 1,
        )
        capped = len(ids) > MAX_BULK_ARCHIVE_COUNT

        return WizardBulkArchivePreviewResponse(
            deletable_count=(MAX_BULK_ARCHIVE_COUNT if capped else len(ids)),
            capped=capped,
        )

    def bulk_archive(
        self,
        data: WizardBulkArchiveRequest,
        company_id: int,
        actor_user_id: int,
    ) -> WizardBulkArchiveResponse:
        """
        건별로 독립 처리한다(원자적 전체 성공/실패가 아니다) — 성공한
        건은 archive()가 자체적으로 즉시 commit하므로, 이후 건에서 예외가
        나도 이미 성공한 항목은 그대로 유지된다("실패 시 삭제되지 않은
        항목을 그대로 유지" 요구를 원자성이 아니라 건별 독립 Transaction
        으로 충족한다).
        """

        has_explicit_ids = bool(data.wizard_ids)
        if has_explicit_ids == data.select_all_matching_filter:
            raise BadRequestException(
                "wizard_ids 또는 select_all_matching_filter 중 정확히 "
                "하나만 지정해야 합니다.",
            )

        if data.select_all_matching_filter:
            if data.confirm_text != "삭제":
                raise BadRequestException(
                    "확인 문구가 정확히 '삭제'와 일치해야 합니다.",
                )
            target_ids = self.repository.list_ids_for_bulk_archive(
                company_id, data.status_filter, WizardStatus.DELETABLE_FROM,
                MAX_BULK_ARCHIVE_COUNT + 1,
            )
            if len(target_ids) > MAX_BULK_ARCHIVE_COUNT:
                raise BadRequestException(
                    "한 번에 삭제할 수 있는 최대 건수"
                    f"({MAX_BULK_ARCHIVE_COUNT}건)를 초과했습니다 — 필터를 "
                    "좁혀 다시 시도하세요.",
                )
        else:
            if len(data.wizard_ids) > MAX_BULK_ARCHIVE_COUNT:
                raise BadRequestException(
                    "한 번에 삭제할 수 있는 최대 건수"
                    f"({MAX_BULK_ARCHIVE_COUNT}건)를 초과했습니다.",
                )
            target_ids = data.wizard_ids

        succeeded: list[int] = []
        skipped: list[WizardBulkArchiveResultItem] = []
        failed: list[WizardBulkArchiveResultItem] = []

        for wizard_id in target_ids:
            try:
                wizard = self.repository.get_for_company(wizard_id, company_id)
                if wizard is None:
                    skipped.append(WizardBulkArchiveResultItem(
                        wizard_id=wizard_id, outcome="SKIPPED",
                        reason="존재하지 않거나 다른 회사의 항목입니다.",
                    ))
                    continue

                self.archive(
                    wizard_id, company_id, wizard.version, actor_user_id,
                    data.reason, data.deletion_request_id,
                )
                succeeded.append(wizard_id)

            except (BadRequestException, ConflictException) as exc:
                self.db.rollback()
                skipped.append(WizardBulkArchiveResultItem(
                    wizard_id=wizard_id, outcome="SKIPPED", reason=str(exc),
                ))
            except Exception as exc:  # noqa: BLE001 — 한 건의 예외가 나머지를 막지 않는다
                self.db.rollback()
                failed.append(WizardBulkArchiveResultItem(
                    wizard_id=wizard_id, outcome="FAILED", reason=str(exc),
                ))

        return WizardBulkArchiveResponse(
            requested_count=len(target_ids), succeeded=succeeded,
            skipped=skipped, failed=failed,
        )

    # --------------------------------------------------
    # 2026-09-06 "일괄 마진 설정 + 멀티마켓 동시 등록" — bulk_archive와
    # 동일한 안전장치(두 모드 중 하나, 확인 문구, 최대 건수, 건별 독립
    # 처리)를 그대로 재사용한다. 실제 계산·저장은 기존
    # update_economics()/submit()에 위임하고 여기서는 반복만 한다.
    # --------------------------------------------------

    def preview_bulk_margin_apply_count(
        self, company_id: int, status_filter: str | None,
    ) -> WizardBulkMarginApplyPreviewResponse:

        # WizardStatus.EDITABLE은 WizardStatus.DELETABLE_FROM과 값이
        # 동일하고(둘 다 "아직 채널에 제출되지 않은 상태" 집합), 이
        # 조회는 materialized_listing_ids_json == "[]"까지 함께
        # 확인하므로 그대로 재사용한다(새 Repository 메서드 불필요).
        ids = self.repository.list_ids_for_bulk_archive(
            company_id, status_filter, WizardStatus.EDITABLE,
            MAX_BULK_MARGIN_APPLY_COUNT + 1,
        )
        capped = len(ids) > MAX_BULK_MARGIN_APPLY_COUNT

        return WizardBulkMarginApplyPreviewResponse(
            applicable_count=(
                MAX_BULK_MARGIN_APPLY_COUNT if capped else len(ids)
            ),
            capped=capped,
        )

    def bulk_apply_margin_rate(
        self, data: WizardBulkMarginApplyRequest, company_id: int,
    ) -> WizardBulkMarginApplyResponse:
        """
        각 위저드에 이미 저장된 원가·수수료율(economics_input_json)은
        그대로 두고, target_margin_rate 하나로 판매가만 새로 역산해
        기존 update_economics()에 그대로 위임한다 — 낙관적 동시성·
        Capability 검사·감사 로그는 전부 기존 경로를 그대로 탄다.
        """

        has_explicit_ids = bool(data.wizard_ids)
        if has_explicit_ids == data.select_all_matching_filter:
            raise BadRequestException(
                "wizard_ids 또는 select_all_matching_filter 중 정확히 "
                "하나만 지정해야 합니다.",
            )

        if data.select_all_matching_filter:
            if data.confirm_text != "적용":
                raise BadRequestException(
                    "확인 문구가 정확히 '적용'과 일치해야 합니다.",
                )
            target_ids = self.repository.list_ids_for_bulk_archive(
                company_id, data.status_filter, WizardStatus.EDITABLE,
                MAX_BULK_MARGIN_APPLY_COUNT + 1,
            )
            if len(target_ids) > MAX_BULK_MARGIN_APPLY_COUNT:
                raise BadRequestException(
                    "한 번에 적용할 수 있는 최대 건수"
                    f"({MAX_BULK_MARGIN_APPLY_COUNT}건)를 초과했습니다 — "
                    "필터를 좁혀 다시 시도하세요.",
                )
        else:
            if len(data.wizard_ids) > MAX_BULK_MARGIN_APPLY_COUNT:
                raise BadRequestException(
                    "한 번에 적용할 수 있는 최대 건수"
                    f"({MAX_BULK_MARGIN_APPLY_COUNT}건)를 초과했습니다.",
                )
            target_ids = data.wizard_ids

        succeeded: list[int] = []
        skipped: list[WizardBulkMarginApplyResultItem] = []
        failed: list[WizardBulkMarginApplyResultItem] = []

        for wizard_id in target_ids:
            try:
                wizard = self.repository.get_for_company(wizard_id, company_id)
                if wizard is None:
                    skipped.append(WizardBulkMarginApplyResultItem(
                        wizard_id=wizard_id, outcome="SKIPPED",
                        reason="존재하지 않거나 다른 회사의 항목입니다.",
                    ))
                    continue

                stored_items = json.loads(wizard.economics_input_json or "[]")
                if not stored_items:
                    skipped.append(WizardBulkMarginApplyResultItem(
                        wizard_id=wizard_id, outcome="SKIPPED",
                        reason="경제성 정보가 아직 입력되지 않았습니다.",
                    ))
                    continue

                new_items = []
                infeasible = False
                for raw in stored_items:
                    item = EconomicsInputItem.model_validate(raw)
                    derived = derive_sale_price_for_margin_rate(
                        item, data.target_margin_rate,
                    )
                    if derived is None:
                        infeasible = True
                        break
                    new_items.append(
                        item.model_copy(update={"sale_price": derived}),
                    )

                if infeasible:
                    skipped.append(WizardBulkMarginApplyResultItem(
                        wizard_id=wizard_id, outcome="SKIPPED",
                        reason="목표 마진율이 수수료 합계상 구조적으로 "
                        "달성 불가능합니다.",
                    ))
                    continue

                self.update_economics(
                    wizard_id, company_id,
                    WizardEconomicsUpdateRequest(
                        expected_version=wizard.version, items=new_items,
                    ),
                )
                succeeded.append(wizard_id)

            except (BadRequestException, ConflictException) as exc:
                self.db.rollback()
                skipped.append(WizardBulkMarginApplyResultItem(
                    wizard_id=wizard_id, outcome="SKIPPED", reason=str(exc),
                ))
            except Exception as exc:  # noqa: BLE001 — 한 건의 예외가 나머지를 막지 않는다
                self.db.rollback()
                failed.append(WizardBulkMarginApplyResultItem(
                    wizard_id=wizard_id, outcome="FAILED", reason=str(exc),
                ))

        return WizardBulkMarginApplyResponse(
            requested_count=len(target_ids), succeeded=succeeded,
            skipped=skipped, failed=failed,
        )

    def bulk_submit(
        self, data: WizardBulkSubmitRequest, company_id: int,
        approved_by: int,
    ) -> WizardBulkSubmitResponse:
        """
        명시적으로 고른 위저드만 대상으로 한다(전체 필터 제출 없음 —
        되돌리기 어려운 작업이라 위험 반경을 의도적으로 좁힘). APPROVED
        상태가 아닌 위저드는 기존 submit()이 그대로 차단하며, 그
        결과를 SKIPPED로 분류한다(다른 위저드 제출은 막지 않는다).
        """

        if len(data.wizard_ids) > MAX_BULK_SUBMIT_COUNT:
            raise BadRequestException(
                "한 번에 제출할 수 있는 최대 건수"
                f"({MAX_BULK_SUBMIT_COUNT}건)를 초과했습니다.",
            )

        succeeded: list[int] = []
        skipped: list[WizardBulkSubmitResultItem] = []
        failed: list[WizardBulkSubmitResultItem] = []

        for wizard_id in data.wizard_ids:
            try:
                wizard = self.repository.get_for_company(wizard_id, company_id)
                if wizard is None:
                    skipped.append(WizardBulkSubmitResultItem(
                        wizard_id=wizard_id, outcome="SKIPPED",
                        reason="존재하지 않거나 다른 회사의 항목입니다.",
                    ))
                    continue

                self.submit(
                    wizard_id, company_id, approved_by,
                    WizardSubmitRequest(
                        expected_version=wizard.version,
                        execution_mode="SUBMIT",
                    ),
                )
                succeeded.append(wizard_id)

            except (BadRequestException, ConflictException) as exc:
                self.db.rollback()
                skipped.append(WizardBulkSubmitResultItem(
                    wizard_id=wizard_id, outcome="SKIPPED", reason=str(exc),
                ))
            except Exception as exc:  # noqa: BLE001 — 한 건의 예외가 나머지를 막지 않는다
                self.db.rollback()
                failed.append(WizardBulkSubmitResultItem(
                    wizard_id=wizard_id, outcome="FAILED", reason=str(exc),
                ))

        return WizardBulkSubmitResponse(
            requested_count=len(data.wizard_ids), succeeded=succeeded,
            skipped=skipped, failed=failed,
        )


__all__ = ["ListingWizardService", "deletion_eligibility"]
