"""
=========================================================
Homez OS

File : tests/test_media_asset_rights_evidence.py

2026-08-28 사용자 결정 — 이미지 권리 증빙 등록 + 사용자 진행-선택
감사기록(ImageRightsEvidenceService). 증빙 미제출이 기능을 차단하지
않는다는 정책과, "경고를 봤다"는 사실을 append-only로 기록하는
계약을 검증한다. 실제 운영 DB는 전혀 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset.model import ImageRightsAcknowledgement
from app.domains.media_asset.model import ImageRightsEvidence
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.rights_evidence_service import (
    ImageRightsEvidenceService,
)
from app.domains.media_asset.rights_evidence_service import UserAction
from app.domains.media_asset.rights_evidence_service import WorkflowStage
from app.domains.media_asset.rights_evidence_service import image_fingerprint
from app.domains.user.model import User  # noqa: F401


class ImageRightsEvidenceServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                MediaAsset.__table__,
                ImageRightsEvidence.__table__,
                ImageRightsAcknowledgement.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = ImageRightsEvidenceService(self.db)

        self.company = Company(name="테스트회사")
        self.db.add(self.company)
        self.db.commit()

        self.other_company = Company(name="다른회사")
        self.db.add(self.other_company)
        self.db.commit()

        self.asset = MediaAsset(
            company_id=self.company.id,
            owner_type="PRODUCT_CANDIDATE", owner_id=1,
            asset_role="ORIGINAL", purpose="MAIN", display_order=0,
            storage_path="media/1/ab/test.png",
            mime_type="image/png", file_size_bytes=1024,
            sha256_hex="a" * 64,
            rights_status="RIGHTS_UNVERIFIED",
        )
        self.db.add(self.asset)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # ------------------------------------------------
    # 증빙 등록
    # ------------------------------------------------

    def test_register_evidence_requires_supplier_or_domain(self):

        with self.assertRaises(BadRequestException):
            self.service.register_evidence(
                company_id=self.company.id, verified_by_user_id=1,
                evidence_type="LICENSE_FILE",
            )

    def test_register_evidence_by_source_domain_is_reusable(self):

        evidence = self.service.register_evidence(
            company_id=self.company.id, verified_by_user_id=1,
            evidence_type="SUPPLIER_PERMISSION_LETTER",
            source_domain="supplier.example.com",
            allowed_channels=["COUPANG"],
            commercial_use_status="ALLOWED",
            editing_allowed=True,
        )
        self.assertEqual(evidence.source_domain, "supplier.example.com")
        self.assertEqual(evidence.commercial_use_status, "ALLOWED")

        rows = self.service.list_evidence(
            self.company.id, source_domain="supplier.example.com",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, evidence.id)

    def test_evidence_registration_does_not_change_asset_rights_status(self):
        """증빙 등록 자체가 rights_status를 자동으로 VERIFIED로
        올리지 않는다 — 등록은 근거 자료를 남기는 것일 뿐, HOMEZ가
        진위를 보증한다는 뜻이 아니다."""

        self.service.register_evidence(
            company_id=self.company.id, verified_by_user_id=1,
            evidence_type="LICENSE_FILE", supplier_id=42,
            commercial_use_status="ALLOWED",
        )
        self.db.refresh(self.asset)
        self.assertEqual(self.asset.rights_status, "RIGHTS_UNVERIFIED")

    # ------------------------------------------------
    # 사용자 진행-선택 기록
    # ------------------------------------------------

    def test_acknowledge_records_continue_choice(self):

        record = self.service.acknowledge(
            company_id=self.company.id, user_id=1, asset_id=self.asset.id,
            workflow_stage=WorkflowStage.GENERATE_OR_EDIT_OR_EXPORT,
            warning_code="RIGHTS_EVIDENCE_MISSING",
            user_action=UserAction.CONTINUE,
        )
        self.assertEqual(record.user_action, "CONTINUE")
        self.assertEqual(
            record.image_fingerprint, image_fingerprint(self.asset),
        )

    def test_acknowledge_rejects_unknown_stage_or_action(self):

        with self.assertRaises(BadRequestException):
            self.service.acknowledge(
                company_id=self.company.id, user_id=1,
                asset_id=self.asset.id, workflow_stage="BOGUS_STAGE",
                warning_code="X", user_action=UserAction.CONTINUE,
            )
        with self.assertRaises(BadRequestException):
            self.service.acknowledge(
                company_id=self.company.id, user_id=1,
                asset_id=self.asset.id,
                workflow_stage=WorkflowStage.GENERATE_OR_EDIT_OR_EXPORT,
                warning_code="X", user_action="BOGUS_ACTION",
            )

    def test_acknowledge_unknown_asset_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.acknowledge(
                company_id=self.company.id, user_id=1, asset_id=999999,
                workflow_stage=WorkflowStage.GENERATE_OR_EDIT_OR_EXPORT,
                warning_code="X", user_action=UserAction.CONTINUE,
            )

    def test_company_isolation_on_acknowledge(self):
        """회사 A의 자산에 대해 회사 B로는 확인을 기록할 수 없다."""

        with self.assertRaises(NotFoundException):
            self.service.acknowledge(
                company_id=self.other_company.id, user_id=1,
                asset_id=self.asset.id,
                workflow_stage=WorkflowStage.GENERATE_OR_EDIT_OR_EXPORT,
                warning_code="X", user_action=UserAction.CONTINUE,
            )

    # ------------------------------------------------
    # 이미지 변경 시 기존 확인 무효화
    # ------------------------------------------------

    def test_has_current_acknowledgement_true_after_continue(self):

        self.assertFalse(
            self.service.has_current_acknowledgement(
                self.company.id, self.asset.id,
                WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            ),
        )
        self.service.acknowledge(
            company_id=self.company.id, user_id=1, asset_id=self.asset.id,
            workflow_stage=WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            warning_code="RIGHTS_EVIDENCE_MISSING",
            user_action=UserAction.CONTINUE,
        )
        self.assertTrue(
            self.service.has_current_acknowledgement(
                self.company.id, self.asset.id,
                WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            ),
        )

    def test_acknowledgement_invalidated_when_image_changes(self):
        """이미지가 바뀌면(재생성으로 sha256_hex가 달라지면) 기존
        확인은 더 이상 '현재' 확인으로 인정되지 않는다."""

        self.service.acknowledge(
            company_id=self.company.id, user_id=1, asset_id=self.asset.id,
            workflow_stage=WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            warning_code="RIGHTS_EVIDENCE_MISSING",
            user_action=UserAction.CONTINUE,
        )
        self.assertTrue(
            self.service.has_current_acknowledgement(
                self.company.id, self.asset.id,
                WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            ),
        )

        self.asset.sha256_hex = "b" * 64
        self.db.commit()

        self.assertFalse(
            self.service.has_current_acknowledgement(
                self.company.id, self.asset.id,
                WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            ),
        )

    def test_cancelled_action_does_not_count_as_current_acknowledgement(self):

        self.service.acknowledge(
            company_id=self.company.id, user_id=1, asset_id=self.asset.id,
            workflow_stage=WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            warning_code="RIGHTS_EVIDENCE_MISSING",
            user_action=UserAction.CANCELLED,
        )
        self.assertFalse(
            self.service.has_current_acknowledgement(
                self.company.id, self.asset.id,
                WorkflowStage.CHANNEL_SUBMISSION_FINAL,
            ),
        )


if __name__ == "__main__":
    unittest.main()
