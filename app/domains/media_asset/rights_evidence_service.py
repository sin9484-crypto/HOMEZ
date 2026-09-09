"""
=========================================================
Homez OS

File : app/domains/media_asset/rights_evidence_service.py

이미지 권리 증빙 등록 + 사용자 진행-선택 감사기록(2026-08-28
사용자 결정). 증빙 미제출은 어떤 기능도 차단하지 않는다 — 이
서비스는 "경고를 봤고 사용자가 무엇을 선택했는지"만 기록한다.
증빙이 등록돼도 HOMEZ가 진위나 법적 효력을 보증하지 않는다(화면
문구·주석 전체에서 이 원칙을 유지한다).
=========================================================
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.media_asset.model import ImageRightsAcknowledgement
from app.domains.media_asset.model import ImageRightsEvidence
from app.domains.media_asset.model import MediaAsset


class WorkflowStage:

    GENERATE_OR_EDIT_OR_EXPORT = "GENERATE_OR_EDIT_OR_EXPORT"
    CHANNEL_SUBMISSION_FINAL = "CHANNEL_SUBMISSION_FINAL"

    ALL = (GENERATE_OR_EDIT_OR_EXPORT, CHANNEL_SUBMISSION_FINAL)


class UserAction:

    CONTINUE = "CONTINUE"
    REGISTER_EVIDENCE_LATER = "REGISTER_EVIDENCE_LATER"
    CANCELLED = "CANCELLED"

    ALL = (CONTINUE, REGISTER_EVIDENCE_LATER, CANCELLED)


def image_fingerprint(asset: MediaAsset) -> str:
    """이 자산의 현재 파생 상태를 나타내는 지문 — 이미지가 바뀌면
    (재생성으로 새 sha256_hex가 생기면) 값이 달라져, 예전 확인이
    지금도 유효하다고 착각하지 않게 한다."""

    payload = {
        "asset_id": asset.id,
        "sha256_hex": asset.sha256_hex,
        "rights_status": asset.rights_status,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ImageRightsEvidenceService:

    def __init__(self, db: Session):
        self.db = db

    def register_evidence(
        self,
        *,
        company_id: int,
        verified_by_user_id: int,
        evidence_type: str,
        supplier_id: int | None = None,
        source_domain: str | None = None,
        allowed_channels: list[str] | None = None,
        commercial_use_status: str = "UNKNOWN",
        editing_allowed: bool | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        evidence_reference: str | None = None,
        memo: str | None = None,
    ) -> ImageRightsEvidence:

        if not supplier_id and not source_domain:
            raise BadRequestException(
                "supplier_id 또는 source_domain 중 하나는 반드시 있어야 "
                "합니다 — 이 증빙을 재사용할 대상을 특정할 수 없습니다.",
            )

        evidence = ImageRightsEvidence(
            company_id=company_id,
            supplier_id=supplier_id,
            source_domain=source_domain,
            evidence_type=evidence_type,
            allowed_channels_json=json.dumps(
                allowed_channels or [], ensure_ascii=False,
            ),
            commercial_use_status=commercial_use_status,
            editing_allowed=editing_allowed,
            valid_from=valid_from,
            valid_until=valid_until,
            evidence_reference=evidence_reference,
            memo=memo,
            verified_by_user_id=verified_by_user_id,
            verified_at=datetime.utcnow(),
        )
        self.db.add(evidence)
        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    def list_evidence(
        self, company_id: int,
        supplier_id: int | None = None,
        source_domain: str | None = None,
    ) -> list[ImageRightsEvidence]:

        query = (
            self.db.query(ImageRightsEvidence)
            .filter(ImageRightsEvidence.company_id == company_id)
        )
        if supplier_id is not None:
            query = query.filter(ImageRightsEvidence.supplier_id == supplier_id)
        if source_domain is not None:
            query = query.filter(
                ImageRightsEvidence.source_domain == source_domain,
            )
        return query.order_by(ImageRightsEvidence.created_at.desc()).all()

    def acknowledge(
        self,
        *,
        company_id: int,
        user_id: int,
        asset_id: int,
        workflow_stage: str,
        warning_code: str,
        user_action: str,
    ) -> ImageRightsAcknowledgement:

        if workflow_stage not in WorkflowStage.ALL:
            raise BadRequestException(f"알 수 없는 workflow_stage: {workflow_stage}")
        if user_action not in UserAction.ALL:
            raise BadRequestException(f"알 수 없는 user_action: {user_action}")

        asset = (
            self.db.query(MediaAsset)
            .filter(MediaAsset.id == asset_id)
            .filter(MediaAsset.company_id == company_id)
            .first()
        )
        if asset is None:
            raise NotFoundException(f"media_asset={asset_id}를 찾을 수 없습니다.")

        record = ImageRightsAcknowledgement(
            company_id=company_id,
            user_id=user_id,
            asset_id=asset_id,
            workflow_stage=workflow_stage,
            warning_code=warning_code,
            user_action=user_action,
            image_fingerprint=image_fingerprint(asset),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def has_current_acknowledgement(
        self, company_id: int, asset_id: int, workflow_stage: str,
    ) -> bool:
        """이 자산의 '현재' 파생 상태에 대해, 이 시점(workflow_stage)의
        경고를 이미 CONTINUE로 확인한 기록이 있는지 — 화면이 반복
        경고로 사용자를 방해하지 않도록 돕는 조회 전용 헬퍼. 이미지가
        바뀌면(image_fingerprint 변경) 자동으로 False가 된다."""

        asset = (
            self.db.query(MediaAsset)
            .filter(MediaAsset.id == asset_id)
            .filter(MediaAsset.company_id == company_id)
            .first()
        )
        if asset is None:
            return False

        current_fingerprint = image_fingerprint(asset)
        existing = (
            self.db.query(ImageRightsAcknowledgement)
            .filter(ImageRightsAcknowledgement.company_id == company_id)
            .filter(ImageRightsAcknowledgement.asset_id == asset_id)
            .filter(ImageRightsAcknowledgement.workflow_stage == workflow_stage)
            .filter(ImageRightsAcknowledgement.user_action == UserAction.CONTINUE)
            .filter(
                ImageRightsAcknowledgement.image_fingerprint
                == current_fingerprint,
            )
            .first()
        )
        return existing is not None


__all__ = [
    "WorkflowStage",
    "UserAction",
    "image_fingerprint",
    "ImageRightsEvidenceService",
]
