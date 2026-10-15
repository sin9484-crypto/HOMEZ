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
from app.domains.marketplace_listing.registration_assistant import NOTICE_EVIDENCE_PREFIX
from app.domains.marketplace_listing.registration_assistant import AssistantRequest
from app.domains.marketplace_listing.registration_assistant import Proposal
from app.domains.marketplace_listing.registration_assistant import build_proposal
from app.domains.marketplace_listing.registration_assistant import notes_payload
from app.domains.marketplace_listing.registration_assistant import pick_seller_defaults
from app.domains.product_candidate.constants import EvidenceType
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_import.notice_suggestions import decode_notice_rows


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

    def propose(self, wizard_id: int, company_id: int, request: AssistantRequest, metadata: dict) -> Proposal:
        wizard = ListingWizardService(self.db).get(wizard_id, company_id)
        entries = json.loads(wizard.channel_selections_json or "[]")
        account_id = request.marketplace_account_id or (entries[0].get("marketplace_account_id") if entries else None)
        if not account_id:
            raise BadRequestException("판매계정을 먼저 4단계에서 선택해야 합니다.")
        candidate = self.db.query(ProductCandidate).filter(ProductCandidate.id == wizard.product_candidate_id).first()
        if candidate is None:
            raise BadRequestException("위저드에 상품 후보가 연결되어 있지 않습니다.")
        rows = self._onch_rows(candidate.id)
        if not rows:
            raise BadRequestException("온채널 정보고시 근거가 없습니다 — URL로 상품 가져오기로 만든 위저드에서만 쓸 수 있습니다.")
        prior_fields, prior_notice, prior_outbound, prior_return = self._prior_success(wizard_id, company_id, account_id)
        return build_proposal(
            wizard_draft=json.loads(wizard.draft_json or "{}"), candidate_id=candidate.id,
            source_reference=candidate.source_reference or f"CANDIDATE:{candidate.id}", account_id=account_id,
            onch_rows=rows, metadata=metadata, seller_defaults=pick_seller_defaults(prior_fields, prior_notice),
            outbound_code=prior_outbound, return_center_code=prior_return, request=request,
        )

    def apply(
        self, wizard_id: int, company_id: int, actor_user_id: int, request: AssistantRequest, metadata: dict,
    ) -> dict:
        proposal = self.propose(wizard_id, company_id, request, metadata)
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
