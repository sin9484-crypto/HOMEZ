"""
=========================================================
Homez OS

File : tests/test_listing_package.py

Listing Package 재감사 시나리오: idempotency 충돌(같은 key 다른
payload → 409), 승인 fingerprint 일치 강제, 승인 후 payload 변경 시
자동 무효화, 오토 모드에서도 무승인 제출 차단, 회사 격리, 채널
준비상태 계산, 부분 성공(캡빌리티 미검증 채널이 있어도 준비된
채널은 READY로 표시), 실제 외부 호출 0건.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.listing_package.constants import ListingPackageMode
from app.domains.listing_package.constants import ListingPackageStatus
from app.domains.listing_package.model import ListingPackage
from app.domains.listing_package.model import ListingPackageApproval
from app.domains.listing_package.schema import ChannelSelectionItem
from app.domains.listing_package.schema import CreateListingPackageRequest
from app.domains.listing_package.schema import ImageOptions
from app.domains.listing_package.schema import ListingPackageApprovalRequest
from app.domains.listing_package.schema import ListingPackageRejectionRequest
from app.domains.listing_package.service import ListingPackageService
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentCapability,
)
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.schema import ImageGenerationItemRequest
from app.domains.media_asset.schema import ImageGenerationJobSubmitRequest
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

_COUNTER = 0


def _next_key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


class ListingPackageTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                MarketplaceChannel.__table__,
                MarketplaceFulfillmentCapability.__table__,
                MediaAsset.__table__,
                ImageGenerationJob.__table__,
                ImageGenerationResult.__table__,
                ImageGenerationDailyUsage.__table__,
                ListingPackage.__table__,
                ListingPackageApproval.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.service = ListingPackageService(self.db)
        self.service.image_queue_service.media_root = Path(tempfile.mkdtemp())

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")

        self.channel = MarketplaceChannel(
            code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.db.add(self.channel)
        self.db.commit()

        self.capability = MarketplaceFulfillmentCapability(
            channel_id=self.channel.id,
            fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
            is_supported=True, requires_eligibility_check=False,
            requires_account_contract=False,
            external_display_name="판매자 배송", policy_version="1.0.0",
            doc_source_reference="test", status=CapabilityStatus.VERIFIED,
            verified_at=datetime.utcnow(),
        )
        self.db.add(self.capability)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_and_finalize(self, req, requested_by, company_id):
        """
        2026-08-02 Gate R5: create_listing_package()의 이미지 생성은
        더 이상 동기로 끝나지 않는다(별도 Worker가 처리) — 이 헬퍼는
        패키지를 만든 뒤, 그 패키지에 딸린 PENDING 이미지 Job이 있으면
        실제 ImageGenerationJobWorker가 할 일(_execute_job 호출)을
        대신 한 번 해준다. 그 호출이 끝나면 ListingPackageService에
        연결된 on_job_completed 콜백이 자동으로 fingerprint 재계산과
        READY 전이 재확인까지 처리한다(_handle_image_job_completed).
        """

        pkg, dup = self.service.create_listing_package(
            req, requested_by, company_id,
        )

        if not dup:
            pending_job = (
                self.db.query(ImageGenerationJob)
                .filter(ImageGenerationJob.listing_package_id == pkg.id)
                .filter(ImageGenerationJob.status == "PENDING")
                .first()
            )
            if pending_job is not None:
                self.service.image_queue_service._execute_job(  # noqa: SLF001
                    pending_job.id, company_id,
                )

        final_pkg = self.service.repository.get_package_for_company(
            pkg.id, company_id,
        )

        return final_pkg, dup

    def _seed_company(self, label, business_number):

        company = Company(
            name=f"회사 {label}", business_number=business_number,
            ceo="테스트", phone="02-000-0000",
            email=f"{label.lower()}@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()

        return company

    def _candidate(self, company_id, ref="C1"):

        candidate = ProductCandidate(
            candidate_key=f"test:{ref}", source_type="TREND",
            source_reference=ref, market="COUPANG",
            product_name=f"상품 {ref}", status=CandidateStatus.APPROVED,
            margin_score=0.4, risk_score=0.1,
            evidence_summary="테스트 근거",
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    def _create_request(self, candidate_id, mode="STANDARD", key=None):

        return CreateListingPackageRequest(
            product_candidate_id=candidate_id,
            channel_selections=[
                ChannelSelectionItem(
                    channel_code="COUPANG",
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                ),
            ],
            image_options=ImageOptions(main_count=1, detail_count=1),
            mode=mode,
            idempotency_key=key or _next_key("pkg"),
        )

    # ----------------------------------------------------
    # idempotency
    # ----------------------------------------------------

    def test_same_key_same_payload_returns_existing_package(self):

        candidate = self._candidate(self.company_a.id)
        key = _next_key("pkg")
        req = self._create_request(candidate.id, key=key)

        pkg1, dup1 = self._create_and_finalize(
            req, 1, self.company_a.id,
        )
        pkg2, dup2 = self._create_and_finalize(
            req, 1, self.company_a.id,
        )

        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(pkg1.id, pkg2.id)

    def test_same_key_different_payload_returns_409(self):

        candidate = self._candidate(self.company_a.id)
        key = _next_key("pkg")
        req1 = self._create_request(candidate.id, key=key)
        self._create_and_finalize(req1, 1, self.company_a.id)

        req2 = CreateListingPackageRequest(
            product_candidate_id=candidate.id,
            channel_selections=[
                ChannelSelectionItem(
                    channel_code="COUPANG",
                    fulfillment_mode=FulfillmentMode.SELLER_FULFILLED,
                ),
            ],
            image_options=ImageOptions(main_count=3, detail_count=3),
            mode="STANDARD",
            idempotency_key=key,
        )

        with self.assertRaises(ConflictException):
            self._create_and_finalize(req2, 1, self.company_a.id)

    def test_same_idempotency_key_usable_independently_by_both_companies(self):

        candidate_a = self._candidate(self.company_a.id, "CA")
        candidate_b = self._candidate(self.company_b.id, "CB")
        key = _next_key("pkg")

        req_a = self._create_request(candidate_a.id, key=key)
        req_b = self._create_request(candidate_b.id, key=key)

        pkg_a, _ = self._create_and_finalize(req_a, 1, self.company_a.id)
        pkg_b, _ = self._create_and_finalize(req_b, 1, self.company_b.id)

        self.assertNotEqual(pkg_a.id, pkg_b.id)

    # ----------------------------------------------------
    # 회사 격리
    # ----------------------------------------------------

    def test_cross_company_package_access_returns_404(self):

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        with self.assertRaises(NotFoundException):
            self.service.get_package(pkg.id, self.company_b.id)

    # ----------------------------------------------------
    # 승인 fingerprint / 무효화
    # ----------------------------------------------------

    def test_approve_requires_matching_fingerprint(self):

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        with self.assertRaises(ConflictException):
            self.service.approve_package(
                pkg.id,
                ListingPackageApprovalRequest(
                    expected_package_fingerprint="stale-fingerprint",
                    idempotency_key=_next_key("appr"),
                ),
                approved_by=99, company_id=self.company_a.id,
            )

    def test_approval_invalidated_after_payload_changes(self):

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        approval, _ = self.service.approve_package(
            pkg.id,
            ListingPackageApprovalRequest(
                expected_package_fingerprint=pkg.package_fingerprint,
                idempotency_key=_next_key("appr"),
            ),
            approved_by=99, company_id=self.company_a.id,
        )
        self.assertIsNotNone(
            self.service.current_valid_approval(pkg.id, self.company_a.id),
        )

        # 이미지 재생성으로 패키지 상태 변경 → fingerprint 재계산.
        job_req = ImageGenerationJobSubmitRequest(
            product_candidate_id=candidate.id, listing_package_id=pkg.id,
            items=[ImageGenerationItemRequest(purpose="MAIN")],
            idempotency_key=_next_key("extra-image"),
        )
        extra_job, _ = self.service.image_queue_service.submit_job(
            job_req, 1, self.company_a.id,
        )
        # 2026-08-02 Gate R5: submit_job()은 PENDING만 저장하고 즉시
        # 반환한다 — 실제 실행(및 그에 따른 fingerprint 재계산 콜백)은
        # Worker가 할 일이므로 여기서 직접 한 번 대신 해준다.
        self.service.image_queue_service._execute_job(  # noqa: SLF001
            extra_job.id, self.company_a.id,
        )

        self.assertIsNone(
            self.service.current_valid_approval(pkg.id, self.company_a.id),
        )

    def test_duplicate_approve_call_returns_same_approval(self):

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        key = _next_key("appr")
        approval1, dup1 = self.service.approve_package(
            pkg.id,
            ListingPackageApprovalRequest(
                expected_package_fingerprint=pkg.package_fingerprint,
                idempotency_key=key,
            ),
            approved_by=99, company_id=self.company_a.id,
        )
        approval2, dup2 = self.service.approve_package(
            pkg.id,
            ListingPackageApprovalRequest(
                expected_package_fingerprint=pkg.package_fingerprint,
                idempotency_key=key,
            ),
            approved_by=99, company_id=self.company_a.id,
        )

        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(approval1.id, approval2.id)

    def test_reject_cancels_package(self):

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        self.service.reject_package(
            pkg.id,
            ListingPackageRejectionRequest(
                reason="테스트 거절", idempotency_key=_next_key("rej"),
            ),
            rejected_by=1, company_id=self.company_a.id,
        )

        updated = self.service.get_package(pkg.id, self.company_a.id)
        self.assertEqual(updated.status, ListingPackageStatus.CANCELLED)

    # ----------------------------------------------------
    # 제출 차단 — 일반/오토 모드 둘 다
    # ----------------------------------------------------

    def test_submit_without_approval_is_forbidden_standard_mode(self):

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id, mode=ListingPackageMode.STANDARD)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        with self.assertRaises(ForbiddenException):
            self.service.submit_to_channels(pkg.id, self.company_a.id)

    def test_submit_without_approval_is_forbidden_auto_mode(self):
        """오토 모드에서도 승인 없이는 절대 제출되지 않는다."""

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(
            candidate.id, mode=ListingPackageMode.AI_AUTO_PROPOSAL,
        )
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        self.assertEqual(pkg.mode, ListingPackageMode.AI_AUTO_PROPOSAL)

        with self.assertRaises(ForbiddenException):
            self.service.submit_to_channels(pkg.id, self.company_a.id)

    def test_submit_with_approval_is_blocked_as_out_of_scope_not_forbidden(self):
        """
        승인은 있지만 실제 채널 제출 자체는 이번 Phase 범위 밖 —
        403(권한 없음)이 아니라 400(범위 밖)으로 구분되어야 한다.
        """

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        self.service.approve_package(
            pkg.id,
            ListingPackageApprovalRequest(
                expected_package_fingerprint=pkg.package_fingerprint,
                idempotency_key=_next_key("appr"),
            ),
            approved_by=99, company_id=self.company_a.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.submit_to_channels(pkg.id, self.company_a.id)

    # ----------------------------------------------------
    # 2026-08-02 Gate R4: 이미지 내용 기반 승인 fingerprint
    # ----------------------------------------------------

    def test_approval_snapshot_contains_full_image_descriptors(self):

        import json

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        approval, _ = self.service.approve_package(
            pkg.id,
            ListingPackageApprovalRequest(
                expected_package_fingerprint=pkg.package_fingerprint,
                idempotency_key=_next_key("appr"),
            ),
            approved_by=99, company_id=self.company_a.id,
        )

        snapshot = json.loads(approval.payload_snapshot_json)
        images = snapshot["images"]

        self.assertEqual(len(images), 2)  # main_count=1 + detail_count=1

        for descriptor in images:
            for field in (
                "asset_id", "sha256", "purpose", "sequence", "mime_type",
                "width", "height", "file_size", "source_asset_id",
                "storage_path",
            ):
                self.assertIn(field, descriptor)
            self.assertEqual(len(descriptor["sha256"]), 64)

    def test_image_order_change_invalidates_fingerprint(self):
        """
        같은 asset_id 집합이라도 순서가 바뀌면 package_fingerprint가
        달라져야 한다 — descriptor 목록을 정렬하지 않고 실제 순서
        그대로 지문에 반영하는지 확인한다.
        """

        from app.domains.listing_package.fingerprint import (
            compute_package_fingerprint,
        )

        descriptors = [
            {"asset_id": 1, "sha256": "a" * 64, "purpose": "MAIN", "sequence": 0},
            {"asset_id": 2, "sha256": "b" * 64, "purpose": "DETAIL", "sequence": 1},
        ]

        fp_original = compute_package_fingerprint(
            '{"x": 1}', '{"y": 2}', descriptors, "STANDARD",
        )
        fp_reordered = compute_package_fingerprint(
            '{"x": 1}', '{"y": 2}', list(reversed(descriptors)), "STANDARD",
        )

        self.assertNotEqual(fp_original, fp_reordered)

    def test_image_hash_change_invalidates_fingerprint(self):

        from app.domains.listing_package.fingerprint import (
            compute_package_fingerprint,
        )

        descriptors = [
            {"asset_id": 1, "sha256": "a" * 64, "purpose": "MAIN", "sequence": 0},
        ]
        tampered = [
            {"asset_id": 1, "sha256": "f" * 64, "purpose": "MAIN", "sequence": 0},
        ]

        fp_original = compute_package_fingerprint(
            '{"x": 1}', '{"y": 2}', descriptors, "STANDARD",
        )
        fp_tampered = compute_package_fingerprint(
            '{"x": 1}', '{"y": 2}', tampered, "STANDARD",
        )

        self.assertNotEqual(fp_original, fp_tampered)

    def test_submit_blocks_when_approved_image_file_tampered_externally(self):
        """
        MediaAsset 파일 내용이 앱 "외부"에서 직접 바뀌는 상황 — DB
        컬럼(sha256_hex 등)은 그대로이므로 package_fingerprint 비교
        만으로는 잡히지 않는다. 제출 직전 실제 파일을 다시 읽어
        재검증해야만 차단할 수 있다.
        """

        import json

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        approval, _ = self.service.approve_package(
            pkg.id,
            ListingPackageApprovalRequest(
                expected_package_fingerprint=pkg.package_fingerprint,
                idempotency_key=_next_key("appr"),
            ),
            approved_by=99, company_id=self.company_a.id,
        )

        snapshot = json.loads(approval.payload_snapshot_json)
        storage_path = snapshot["images"][0]["storage_path"]

        media_root = self.service.image_queue_service.media_root
        target = media_root / storage_path
        target.write_bytes(b"\x00\x01tampered-bytes-not-a-real-image")

        # fingerprint 자체는 여전히 유효(DB 컬럼은 안 바뀌었으므로) —
        # ForbiddenException은 오직 실제 파일 재검증에서만 나와야 한다.
        self.assertIsNotNone(
            self.service.current_valid_approval(pkg.id, self.company_a.id),
        )

        with self.assertRaises(ForbiddenException):
            self.service.submit_to_channels(pkg.id, self.company_a.id)

    def test_submit_blocks_when_approved_image_file_missing(self):

        import json

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        approval, _ = self.service.approve_package(
            pkg.id,
            ListingPackageApprovalRequest(
                expected_package_fingerprint=pkg.package_fingerprint,
                idempotency_key=_next_key("appr"),
            ),
            approved_by=99, company_id=self.company_a.id,
        )

        snapshot = json.loads(approval.payload_snapshot_json)
        storage_path = snapshot["images"][0]["storage_path"]

        media_root = self.service.image_queue_service.media_root
        (media_root / storage_path).unlink()

        with self.assertRaises(ForbiddenException):
            self.service.submit_to_channels(pkg.id, self.company_a.id)

    def test_submit_succeeds_past_file_verification_when_untampered(self):
        """
        정상 경로(파일 변조 없음)는 파일 재검증까지 전부 통과해
        "Phase 범위 밖"(BadRequestException)에 도달해야 한다 —
        ForbiddenException이 잘못 발생하지 않는지 확인한다.
        """

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        self.service.approve_package(
            pkg.id,
            ListingPackageApprovalRequest(
                expected_package_fingerprint=pkg.package_fingerprint,
                idempotency_key=_next_key("appr"),
            ),
            approved_by=99, company_id=self.company_a.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.submit_to_channels(pkg.id, self.company_a.id)

    # ----------------------------------------------------
    # 초안·이미지·채널 통합 생성 결과
    # ----------------------------------------------------

    def test_create_listing_package_produces_draft_images_and_readiness(self):

        candidate = self._candidate(self.company_a.id)
        req = self._create_request(candidate.id)
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        import json
        draft = json.loads(pkg.draft_payload_json)
        self.assertEqual(draft["product_name"], candidate.product_name)

        readiness = self.service.compute_channel_readiness_for_package(pkg)
        self.assertEqual(len(readiness), 1)
        self.assertTrue(readiness[0].ready)

        self.assertEqual(pkg.status, ListingPackageStatus.READY_FOR_REVIEW)
        self.assertIsNotNone(pkg.estimated_revenue)
        self.assertIsNotNone(pkg.risk_summary)

    def test_create_listing_package_rejects_non_approved_candidate(self):

        candidate = self._candidate(self.company_a.id)
        candidate.status = CandidateStatus.DISCOVERED
        self.db.commit()

        req = self._create_request(candidate.id)

        with self.assertRaises(BadRequestException):
            self.service.create_listing_package(
                req, created_by=1, company_id=self.company_a.id,
            )

    def test_unready_channel_capability_marks_package_not_ready(self):

        candidate = self._candidate(self.company_a.id)
        req = CreateListingPackageRequest(
            product_candidate_id=candidate.id,
            channel_selections=[
                ChannelSelectionItem(
                    channel_code="COUPANG",
                    fulfillment_mode=FulfillmentMode.MARKETPLACE_FULFILLED,
                ),
            ],
            image_options=ImageOptions(main_count=1, detail_count=0),
            mode="STANDARD",
            idempotency_key=_next_key("unready"),
        )
        pkg, _ = self._create_and_finalize(req, 1, self.company_a.id)

        readiness = self.service.compute_channel_readiness_for_package(pkg)
        self.assertFalse(readiness[0].ready)
        self.assertEqual(pkg.status, ListingPackageStatus.DRAFT)

    # ----------------------------------------------------
    # 실제 외부 호출 0건
    # ----------------------------------------------------

    def test_no_network_libraries_imported_in_listing_package_domain(self):

        import app.domains.listing_package.service as mod

        with open(mod.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("import requests", source)
        self.assertNotIn("import httpx", source)
        self.assertNotIn("urllib.request", source)


if __name__ == "__main__":
    unittest.main()
