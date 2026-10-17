"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/registration_assistant_service.py

등록 도우미의 DB 접근부(2026-10-14). 계산은 `registration_assistant.py`(순수)가 하고, 이 서비스는
(1) 위저드·후보·온채널 근거·이전 성공 등록을 읽고 (2) 저장은 기존 `ListingWizardService`의 검증을 그대로 거치며
(3) 시도한 사실과 특이점·주의점을 후보 근거(append-only)에 남긴다.
저장 규칙 위반(`BadRequestException`)은 아무것도 쓰지 않고 오류 문구를 돌려준다.
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.listing_wizard_schema import FulfillmentSelectionInput
from app.domains.marketplace_listing.listing_wizard_schema import WizardDraftUpdateRequest
from app.domains.marketplace_listing.listing_wizard_schema import WizardFulfillmentUpdateRequest
from app.domains.marketplace_listing.listing_wizard_service import ListingWizardService
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.registration_assistant import NO_BRAND_KEYS
from app.domains.marketplace_listing.registration_assistant import NOTICE_EVIDENCE_PREFIX
from app.domains.marketplace_listing.registration_assistant import SELLER_DEFAULT_KEYS
from app.domains.marketplace_listing.registration_assistant import AssistantNote
from app.domains.marketplace_listing.registration_assistant import AssistantRequest
from app.domains.marketplace_listing.registration_assistant import Proposal
from app.domains.marketplace_listing.registration_assistant import build_proposal
from app.domains.marketplace_listing.registration_assistant import notes_payload
from app.domains.marketplace_listing.registration_assistant import pick_seller_defaults
from app.domains.product_candidate.constants import EvidenceType
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_import.notice_suggestions import decode_notice_rows
from app.domains.user_settings.repository import UserSettingRepository


# ---------------------------------------------------------------------------
# 이미 저장한 5단계 값 재사용(2026-10-17, 등록 도우미 Phase A)
#
# URL로 가져오기로 만든 위저드가 아니어도(수동 생성·복제), 사용자가 이미 5단계에 저장해 둔 값이 있으면 그것을 근거로 쓴다.
# 새 값을 만들어 내지 않는다 — 저장돼 있던 값을 같은 규칙(`build_proposal`)으로 다시 검증해 돌려주고, 도우미가 만든 제안을 저장할 때
# 이미 올려 둔 이미지·상세설명·사용권 선언이 지워지지 않도록 지켜 준다.
# ---------------------------------------------------------------------------


def saved_entry_for_account(channel_selections_json: str | None, account_id: int | None) -> dict:
    """위저드에 저장된 5단계 항목 중 이 판매계정의 것(없으면 빈 dict)."""

    try:
        entries = json.loads(channel_selections_json or "[]")
    except (TypeError, ValueError):
        return {}
    for entry in entries:
        if isinstance(entry, dict) and (account_id is None or entry.get("marketplace_account_id") == account_id):
            return entry
    return {}


def rows_from_saved_selection(entry: dict) -> list[tuple[str, str]]:
    """저장된 고시 값을 (라벨, 값) 행으로 바꾼다. 빈 값·내부 키(`__…`)는 뺀다."""

    info = ((entry or {}).get("channel_policy_attributes") or {}).get("notice_information") or {}
    rows: list[tuple[str, str]] = []
    group = str(info.get("__notice_category_name") or "").strip()
    if group:
        rows.append(("상품정보제공고시 구분", group))
    for key, value in info.items():
        if str(key).startswith("__") or not str(value).strip():
            continue
        rows.append((str(key).split("::", 1)[-1], str(value).strip()))
    return rows


def merge_saved_defaults(defaults: dict, entry: dict) -> tuple[dict, list[str]]:
    """판매자 공통 값이 비어 있으면 같은 위저드에 이미 저장된 값으로 채운다(고정값·이전 성공 등록이 우선). 무브랜드 확인 기록은
    저장된 상태가 NO_BRAND일 때만 가져온다. 반환: (병합 결과, 채운 키 목록)."""

    merged = dict(defaults)
    saved = (entry or {}).get("required_fields") or {}
    used: list[str] = []
    for key in SELLER_DEFAULT_KEYS:
        if key not in merged and saved.get(key) not in (None, ""):
            merged[key] = saved[key]
            used.append(key)
    if merged.get("brandState") != "NO_BRAND" and saved.get("brandState") == "NO_BRAND":
        for key in NO_BRAND_KEYS:
            if key in saved:
                merged[key] = saved[key]
        used.append("brandState")
    return merged, used


def carry_saved_content(request: AssistantRequest, entry: dict) -> tuple[AssistantRequest, list[str]]:
    """요청이 이미지·상세설명·사용권 선언을 비워 두었으면 저장돼 있던 값을 그대로 이어 받는다(사용자가 새로 넘긴 값이 우선)."""

    saved = (entry or {}).get("required_fields") or {}
    updates: dict = {}
    carried: list[str] = []
    if not request.images and saved.get("images"):
        updates["images"] = list(saved["images"])
        carried.append("대표 이미지")
    if not request.contents and saved.get("contents"):
        updates["contents"] = list(saved["contents"])
        updates["contents_source_asset_ids"] = list(saved.get("contentsSourceAssetIds") or request.contents_source_asset_ids)
        carried.append("상세설명")
    if not request.image_rights_confirmed and saved.get("liveImageRightsConfirmed") is True:
        updates["image_rights_confirmed"] = True
        carried.append("이미지 사용권 선언")
    return (request.model_copy(update=updates) if updates else request), carried


def proposal_to_dict(proposal: Proposal) -> dict:
    attrs = proposal.selection["channel_policy_attributes"]
    return {
        "item_count": proposal.item_count,
        "unresolved": proposal.unresolved,
        "notes": [n.to_dict() for n in proposal.notes],
        "draft_keywords": proposal.draft_keywords,
        "selection_preview": {
            "outbound_shipping_place_code": proposal.selection.get("outbound_shipping_place_code"),
            "return_center_code": proposal.selection.get("return_center_code"),
            "origin_country": attrs.get("origin_country"),
            "notice_information": attrs.get("notice_information"),
            "items": [
                {"itemName": i["itemName"], "sku": i["externalVendorSku"], "options": i["optionAttributes"]}
                for i in proposal.selection["required_fields"].get("items", [])
            ],
        },
    }


class RegistrationAssistantService:

    def __init__(self, db: Session):
        self.db = db

    def _onch_rows(self, candidate_id: int) -> list[tuple[str, str]]:
        rows = (
            self.db.query(ProductCandidateEvidence)
            .filter(ProductCandidateEvidence.candidate_id == candidate_id)
            .order_by(ProductCandidateEvidence.id.desc())
            .all()
        )
        for row in rows:
            if str(row.payload_summary or "").startswith(NOTICE_EVIDENCE_PREFIX):
                return decode_notice_rows(row.payload_summary)
        return []

    def _prior_success(self, wizard_id: int, company_id: int, account_id: int):
        """같은 판매계정으로 성공 등록한 가장 최근 위저드의 (required_fields, notice_information, 출고지, 반품지)."""

        prior_rows = (
            self.db.query(ListingWizard)
            .filter(ListingWizard.company_id == company_id, ListingWizard.id != wizard_id)
            .filter(ListingWizard.status == WizardStatus.SUCCEEDED)
            .order_by(ListingWizard.id.desc())
            .all()
        )
        for prior in prior_rows:
            for entry in json.loads(prior.channel_selections_json or "[]"):
                if entry.get("marketplace_account_id") == account_id and entry.get("required_fields"):
                    attrs = entry.get("channel_policy_attributes") or {}
                    return (
                        entry["required_fields"], attrs.get("notice_information") or {},
                        entry.get("outbound_shipping_place_code"), entry.get("return_center_code"),
                    )
        return {}, {}, None, None

    def _fixed_defaults(self, user_id: int | None, company_id: int) -> dict:
        """사용자가 저장한 판매자 고정값(listing_defaults). 읽지 못하거나 형식이 틀리면 빈 값 — 이전 등록 값으로 되돌아간다."""

        if user_id is None:
            return {}
        try:
            row = UserSettingRepository(self.db).get_for_user(user_id, company_id, "listing_defaults")
            value = json.loads(row.setting_value) if row is not None else {}
        except Exception:  # noqa: BLE001 — 고정값은 편의 기능이라 실패해도 등록을 막지 않는다.
            return {}
        return value if isinstance(value, dict) else {}

    def propose(
        self, wizard_id: int, company_id: int, request: AssistantRequest, metadata: dict, user_id: int | None = None,
    ) -> Proposal:
        wizard = ListingWizardService(self.db).get(wizard_id, company_id)
        entries = json.loads(wizard.channel_selections_json or "[]")
        account_id = request.marketplace_account_id or (entries[0].get("marketplace_account_id") if entries else None)
        if not account_id:
            raise BadRequestException("판매계정을 먼저 4단계에서 선택해야 합니다.")
        candidate = self.db.query(ProductCandidate).filter(ProductCandidate.id == wizard.product_candidate_id).first()
        if candidate is None:
            raise BadRequestException("위저드에 상품 후보가 연결되어 있지 않습니다.")
        saved = saved_entry_for_account(wizard.channel_selections_json, account_id)
        rows = self._onch_rows(candidate.id)
        rows_from_saved = False
        if not rows:
            rows = rows_from_saved_selection(saved)
            rows_from_saved = bool(rows)
        if not rows:
            raise BadRequestException(
                "고시 근거가 없습니다 — URL로 상품 가져오기로 만든 위저드이거나, 5단계에 고시를 한 번 저장해 둔 위저드에서 쓸 수 있습니다.",
            )
        prior_fields, prior_notice, prior_outbound, prior_return = self._prior_success(wizard_id, company_id, account_id)
        defaults = pick_seller_defaults(prior_fields, prior_notice, self._fixed_defaults(user_id, company_id))
        defaults, defaults_from_saved = merge_saved_defaults(defaults, saved)
        request, carried = carry_saved_content(request, saved)
        proposal = build_proposal(
            wizard_draft=json.loads(wizard.draft_json or "{}"), candidate_id=candidate.id,
            source_reference=candidate.source_reference or f"CANDIDATE:{candidate.id}", account_id=account_id,
            onch_rows=rows, metadata=metadata, seller_defaults=defaults,
            outbound_code=prior_outbound or saved.get("outbound_shipping_place_code"),
            return_center_code=prior_return or saved.get("return_center_code"), request=request,
        )
        if rows_from_saved:
            proposal.notes.append(AssistantNote(
                "INFO", "NOTICE_FROM_SAVED_VALUES",
                "온채널 고시 근거가 없어, 이 위저드 5단계에 이미 저장된 고시 값을 근거로 다시 검증했다 — 새 값을 만들지 않았다.",
            ))
        if defaults_from_saved:
            proposal.notes.append(AssistantNote(
                "INFO", "SELLER_VALUES_FROM_SAVED",
                "고정값·이전 등록에 없는 판매자 공통 값은 이 위저드에 이미 저장된 값을 썼다: " + ", ".join(defaults_from_saved),
            ))
        if carried:
            proposal.notes.append(AssistantNote(
                "INFO", "SAVED_CONTENT_KEPT", "이미 올려 둔 값을 그대로 유지한다: " + ", ".join(carried),
            ))
        return proposal

    def apply(
        self, wizard_id: int, company_id: int, actor_user_id: int, request: AssistantRequest, metadata: dict,
    ) -> dict:
        proposal = self.propose(wizard_id, company_id, request, metadata, user_id=actor_user_id)
        service = ListingWizardService(self.db)
        wizard = service.get(wizard_id, company_id)
        applied = False
        error: str | None = None
        policy_result = None
        if proposal.ready:
            draft = json.loads(wizard.draft_json or "{}")
            version = request.expected_version or wizard.version
            try:
                if proposal.draft_keywords and list(draft.get("keywords") or []) != proposal.draft_keywords:
                    updated = service.update_draft(wizard_id, company_id, WizardDraftUpdateRequest(
                        expected_version=version, product_name=draft["product_name"], brand=draft.get("brand"),
                        category=draft.get("category"), description=draft.get("description"),
                        options=draft.get("options") or [], certifications=draft.get("certifications") or [],
                        keywords=proposal.draft_keywords,
                    ))
                    version = updated.version
                service.update_fulfillment(
                    wizard_id, company_id,
                    WizardFulfillmentUpdateRequest(
                        expected_version=version, selections=[FulfillmentSelectionInput(**proposal.selection)],
                    ),
                    actor_user_id=actor_user_id,
                )
                applied = True
                # 저장한 값 그대로 채널 정책 평가를 남긴다 — 평가가 없으면 제출이 CHANNEL_POLICY_GATE_FAILED로 막혀
                # 위저드가 FAILED(수정 불가)가 된다(2026-10-15 #14 사례).
                saved = service.get(wizard_id, company_id)
                entry = next(
                    (e for e in json.loads(saved.channel_selections_json or "[]")
                     if e.get("marketplace_account_id") == proposal.selection["marketplace_account_id"]), {},
                )
                evaluation = ChannelPolicyService(self.db).evaluate_and_record(
                    company_id=company_id, product_candidate_id=wizard.product_candidate_id, channel="COUPANG",
                    category_hint_override=None, product_attributes=entry.get("channel_policy_attributes") or {},
                    confirmed_evidence_rule_codes=entry.get("channel_policy_confirmed_evidence_rule_codes") or [],
                    evaluated_by=actor_user_id, selection_id=None,
                )
                policy_result = getattr(evaluation, "result", None) or (evaluation.get("result") if isinstance(evaluation, dict) else None)
            except BadRequestException as exc:
                self.db.rollback()
                error = str(exc)
        # 시도한 사실과 특이점·주의점은 저장 성공 여부와 무관하게 후보 근거(append-only)에 남긴다.
        stamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        self.db.add(ProductCandidateEvidence(
            candidate_id=wizard.product_candidate_id, evidence_type=EvidenceType.RAW_SOURCE,
            payload_summary=notes_payload(proposal, wizard_id=wizard_id, applied=applied, when=stamp)[:4000],
        ))
        self.db.commit()
        return {
            "applied": applied, "error": error, "ready": proposal.ready, "policy_result": policy_result,
            **proposal_to_dict(proposal),
        }
