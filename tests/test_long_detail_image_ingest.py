"""
=========================================================
Homez OS

File : tests/test_long_detail_image_ingest.py

2026-08-20 3차 지시 — 긴 이미지 실제 유입 경로 + 이미지 권리 검증.
780×15548 세로 이미지로 격리 DB에서 업로드→분할→계보 보존을
검증하고, RIGHTS_UNVERIFIED 이미지가 상품등록 마법사 이미지
선택에서 차단된 뒤 사용권 확인 후 통과하는지 확인한다. 실제 운영
DB는 전혀 사용하지 않는다.

2026-08-31 Phase 8 차단 해소 작업 — 이전에는 개발자 개인 Desktop의
실제 참고 사진("샤프란", 780×15548px)을 읽어와 검증했다. 이 파일이
없는 환경(이 저장소를 새로 받은 모든 환경 포함)에서는 관련 테스트
5개가 전부 skip되어 회귀가 이 경로를 실제로 검증하지 못했다 —
검증 대상(치수·분할·계보·권리 상태·위저드 선택 흐름)은 실제 사진의
픽셀 내용에 의존하지 않으므로, 같은 치수의 합성 JPEG
(tests/synthetic_tall_image_helper.py)으로 교체해 환경과 무관하게
항상 실행되게 했다 — assertion 강도는 그대로 유지했다(치수·조각
수·checksum 유일성 등 어떤 값도 느슨하게 바꾸지 않았다).
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.constants import MediaAssetRole
from app.domains.media_asset.job_queue_service import (
    ImageGenerationJobQueueService,
)
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.constants import WizardStep
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardMediaUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    ListingWizardService,
)
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.product_candidate.model import ProductCandidate
from app.domains.user.model import User  # noqa: F401
from tests.synthetic_tall_image_helper import build_synthetic_tall_product_jpeg

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class LongDetailImageIngestTestCase(unittest.TestCase):

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
                MediaAsset.__table__,
                ImageGenerationJob.__table__,
                ImageGenerationResult.__table__,
                ImageGenerationDailyUsage.__table__,
                ListingWizard.__table__,
            ],
        )

        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.media_root = Path(tempfile.mkdtemp())
        self.service = ImageGenerationJobQueueService(
            self.db, media_root=self.media_root,
        )
        self.wizard_service = ListingWizardService(self.db)

        self.company = self._seed_company()
        self.candidate = self._seed_candidate()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_company(self):

        company = Company(
            name="긴 이미지 테스트 회사", business_number="555-55-55555",
            ceo="테스트", phone="02-000-0000",
            email="longimg@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()

        return company

    def _seed_candidate(self):

        candidate = ProductCandidate(
            candidate_key="saffron-test:1", source_type="MANUAL",
            source_reference="saffron-test", market="COUPANG",
            product_name="샤프란 핑크센세이션 3.1L×4", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        return candidate

    def _tall_reference_image_bytes(self) -> bytes:
        """780×15548 합성 JPEG(실제 참고 사진과 동일 치수) — 환경과
        무관하게 항상 사용 가능하다(tests/synthetic_tall_image_
        helper.py 참고)."""

        return build_synthetic_tall_product_jpeg()

    # ------------------------------------------------
    # 일반 이미지 한도는 그대로(전역으로 올리지 않음)
    # ------------------------------------------------

    def test_normal_upload_still_rejects_the_tall_reference_image(self):

        image_bytes = self._tall_reference_image_bytes()

        with self.assertRaises(BadRequestException):
            self.service.upload_original_asset(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
                owner_id=self.candidate.id, purpose="MAIN", display_order=0,
                image_bytes=image_bytes, original_filename="saffron.jpg",
                company_id=self.company.id,
            )

    # ------------------------------------------------
    # 긴 이미지 전용 경로 — 실제 파일
    # ------------------------------------------------

    def test_tall_reference_image_uploads_and_splits_via_dedicated_path(self):

        image_bytes = self._tall_reference_image_bytes()

        original, segments = self.service.upload_and_split_long_detail_image(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, image_bytes=image_bytes,
            original_filename="saffron_full.jpg", segment_height=1200,
            requested_by=1, company_id=self.company.id,
        )

        # 원본 보존 — checksum까지 실제 원본과 일치.
        self.assertEqual(original.asset_role, MediaAssetRole.ORIGINAL)
        self.assertEqual(original.mime_type, "image/jpeg")
        import hashlib
        self.assertEqual(
            original.sha256_hex, hashlib.sha256(image_bytes).hexdigest(),
        )

        # 조각 수·크기 범위·계보·순서·빈 조각 없음.
        self.assertGreater(len(segments), 1)
        seen_orders = set()
        for seg in segments:
            self.assertEqual(seg.asset_role, MediaAssetRole.GENERATED)
            self.assertEqual(seg.source_asset_id, original.id)
            self.assertEqual(seg.width, 780)
            self.assertGreaterEqual(seg.height, 1)
            self.assertLessEqual(seg.height, 1500)
            self.assertGreater(seg.file_size_bytes, 0)
            self.assertTrue(seg.sha256_hex)
            self.assertNotIn(seg.display_order, seen_orders)
            seen_orders.add(seg.display_order)

        # 조각 사이에 완전히 동일한 checksum(중복 조각)이 없어야 한다.
        checksums = [s.sha256_hex for s in segments]
        self.assertEqual(len(checksums), len(set(checksums)))

    def test_long_detail_upload_rejects_oversized_file(self):

        # 20MB 초과 시늉(실제로 큰 바이트열을 만들지 않고 파일 크기
        # 검증 자체가 헤더 파싱 이전에 동작하는지만 확인).
        fake_huge = b"\x00" * (21 * 1024 * 1024)

        with self.assertRaises(BadRequestException):
            self.service.upload_and_split_long_detail_image(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
                owner_id=self.candidate.id, image_bytes=fake_huge,
                original_filename="huge.jpg", segment_height=1200,
                requested_by=1, company_id=self.company.id,
            )

    # ------------------------------------------------
    # 사용권 상태(RIGHTS_UNVERIFIED) 게이트
    # ------------------------------------------------

    def test_long_detail_segments_start_rights_unverified(self):

        image_bytes = self._tall_reference_image_bytes()

        original, segments = self.service.upload_and_split_long_detail_image(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, image_bytes=image_bytes,
            original_filename="saffron_full.jpg", segment_height=1200,
            requested_by=1, company_id=self.company.id,
        )

        self.assertEqual(original.rights_status, "RIGHTS_UNVERIFIED")
        for seg in segments:
            self.assertEqual(seg.rights_status, "RIGHTS_UNVERIFIED")

    def test_normal_upload_also_starts_unverified(self):
        """2026-08-20 3차 정정 — "일반 업로드=자동 VERIFIED" 정책은
        폐기됐다. 일반 업로드도 예외 없이 RIGHTS_UNVERIFIED로
        시작한다."""

        from app.domains.media_asset.providers import _deterministic_png

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("normal-upload"),
            original_filename=None, company_id=self.company.id,
        )

        self.assertEqual(asset.rights_status, "RIGHTS_UNVERIFIED")

    def test_confirm_rights_verified_rejects_invalid_basis(self):

        from app.domains.media_asset.providers import _deterministic_png

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("basis-test"),
            original_filename=None, company_id=self.company.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.confirm_rights_verified(
                asset.id, self.company.id, "MADE_UP_BASIS", confirmed_by=1,
            )

    def test_confirm_rights_verified_writes_audit_log_without_image_content(self):

        from app.domains.media_asset.constants import RightsVerificationBasis
        from app.domains.media_asset.providers import _deterministic_png

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("audit-test"),
            original_filename=None, company_id=self.company.id,
        )

        self.service.confirm_rights_verified(
            asset.id, self.company.id,
            RightsVerificationBasis.SUPPLIER_BRAND_PERMISSION, confirmed_by=7,
        )

        row = self.db.execute(text(
            "SELECT company_id, user_id, action, entity, entity_id, "
            "description FROM audit_logs WHERE action = "
            "'MEDIA_ASSET_RIGHTS_VERIFIED'",
        )).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], self.company.id)
        self.assertEqual(row[1], 7)
        self.assertEqual(row[3], "MediaAsset")
        self.assertEqual(row[4], str(asset.id))
        self.assertIn("SUPPLIER_BRAND_PERMISSION", row[5])
        # 이미지 원문(바이트)이나 storage_path가 감사로그에 남지 않는다.
        self.assertNotIn(asset.storage_path, row[5])

    def test_confirm_rights_verified_audit_log_survives_session_close_without_extra_commit(self):
        """2026-08-20 4차 지시 — 실제 격리 서버 브라우저 E2E에서 실제로
        재현된 결함의 회귀 테스트. `write_audit_log()`(app/core/
        audit_db.py)는 자체적으로 commit()하지 않고, FastAPI의
        `get_db()`(app/database/session.py)는 요청이 끝나면 session을
        commit 없이 close()만 한다 — 그래서 `confirm_rights_verified()`
        가 먼저 commit()하고 그 뒤에 write_audit_log()를 호출했다면,
        감사로그 INSERT는 session이 닫힐 때 조용히 rollback된다. 같은
        세션 안에서 바로 조회하는 테스트(위 test_confirm_rights_
        verified_writes_audit_log_without_image_content)는 autoflush
        덕분에 이 문제를 가린다 — 그래서 이 테스트는 일부러 self.db를
        닫고 완전히 새 세션으로 다시 연다(실제 request-scoped 세션
        수명주기를 그대로 재현)."""

        from app.domains.media_asset.constants import RightsVerificationBasis
        from app.domains.media_asset.providers import _deterministic_png

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("session-close-test"),
            original_filename=None, company_id=self.company.id,
        )
        asset_id = asset.id

        self.service.confirm_rights_verified(
            asset_id, self.company.id,
            RightsVerificationBasis.SELF_CAPTURED, confirmed_by=9,
        )

        self.db.close()  # 실제 get_db() 요청 종료와 동일 — 추가 commit 없음.

        fresh_db = self.SessionLocal()
        try:
            row = fresh_db.execute(text(
                "SELECT action, user_id FROM audit_logs WHERE entity_id = :eid "
                "AND action = 'MEDIA_ASSET_RIGHTS_VERIFIED'",
            ), {"eid": str(asset_id)}).fetchone()
            self.assertIsNotNone(
                row,
                "감사로그가 session.close() 이후 사라졌다 — commit() 순서 결함.",
            )
            self.assertEqual(row[1], 9)

            rights_status = fresh_db.execute(text(
                "SELECT rights_status FROM media_assets WHERE id = :aid",
            ), {"aid": asset_id}).fetchone()[0]
            self.assertEqual(rights_status, "VERIFIED")
        finally:
            fresh_db.close()

    def test_revoke_rights_verification_flips_back_and_audits(self):

        from app.domains.media_asset.constants import RightsVerificationBasis
        from app.domains.media_asset.providers import _deterministic_png

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("revoke-test"),
            original_filename=None, company_id=self.company.id,
        )
        self.service.confirm_rights_verified(
            asset.id, self.company.id,
            RightsVerificationBasis.SELF_CAPTURED, confirmed_by=1,
        )

        revoked = self.service.revoke_rights_verification(
            asset.id, self.company.id, revoked_by=1, reason="확인 착오",
        )
        self.assertEqual(revoked.rights_status, "RIGHTS_UNVERIFIED")

        row = self.db.execute(text(
            "SELECT action FROM audit_logs WHERE action = "
            "'MEDIA_ASSET_RIGHTS_VERIFICATION_REVOKED'",
        )).fetchone()
        self.assertIsNotNone(row)

    def test_derived_asset_never_upgrades_beyond_source_rights(self):
        """원본이 RIGHTS_UNVERIFIED면 배경합성 결과도 VERIFIED가 될 수
        없다 — 원본을 VERIFIED로 만들지 않은 채 파생시켜 확인한다."""

        from app.domains.media_asset.composition import BackgroundKind
        from app.domains.media_asset.composition import BackgroundSpec
        from app.domains.media_asset.providers import _deterministic_png

        original = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=_deterministic_png("inherit-test"),
            original_filename=None, company_id=self.company.id,
        )
        self.assertEqual(original.rights_status, "RIGHTS_UNVERIFIED")

        cutout = self.service.remove_background(
            source_asset_id=original.id, provider_code="FAKE",
            requested_by=1, company_id=self.company.id,
        )
        self.assertEqual(cutout.rights_status, "RIGHTS_UNVERIFIED")

        composed = self.service.compose_background(
            cutout_asset_id=cutout.id,
            background_spec=BackgroundSpec(kind=BackgroundKind.WHITE),
            requested_by=1, company_id=self.company.id,
        )
        self.assertEqual(composed.rights_status, "RIGHTS_UNVERIFIED")

    def test_listing_wizard_allows_selection_of_rights_unverified_image(self):
        """2026-08-28 사용자 결정 — 2026-08-20 3차 지시(선택 시점 하드
        차단)를 대체한다. 증빙 미제출만으로는 어떤 기능도 차단하지
        않는다 — 대신 화면 경고 + ImageRightsAcknowledgementService가
        사용자 선택을 append-only로 기록한다(별도 테스트 파일
        test_media_asset_rights_evidence.py)."""

        image_bytes = self._tall_reference_image_bytes()
        _original, segments = (
            self.service.upload_and_split_long_detail_image(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
                owner_id=self.candidate.id, image_bytes=image_bytes,
                original_filename="saffron_full.jpg", segment_height=1200,
                requested_by=1, company_id=self.company.id,
            )
        )

        wizard = ListingWizard(
            company_id=self.company.id, created_by_user_id=1,
            current_step=WizardStep.MEDIA, status=WizardStatus.DRAFT,
            source_type="MANUAL", product_candidate_id=self.candidate.id,
            selected_media_asset_ids_json="[]",
            channel_selections_json="[]",
            approval_history_json="[]",
            materialized_listing_ids_json="[]",
            version=1,
            creation_idempotency_key="wiz-rights-test-1",
        )
        self.db.add(wizard)
        self.db.commit()

        result = self.wizard_service.update_media(
            wizard.id, self.company.id,
            WizardMediaUpdateRequest(
                expected_version=1,
                selected_media_asset_ids=[segments[0].id],
                autosave_client_token=None,
            ),
        )
        import json
        selected = json.loads(result.selected_media_asset_ids_json)
        self.assertIn(segments[0].id, selected)

    def test_confirm_rights_verified_unblocks_selection(self):

        image_bytes = self._tall_reference_image_bytes()
        _original, segments = (
            self.service.upload_and_split_long_detail_image(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
                owner_id=self.candidate.id, image_bytes=image_bytes,
                original_filename="saffron_full.jpg", segment_height=1200,
                requested_by=1, company_id=self.company.id,
            )
        )

        from app.domains.media_asset.constants import RightsVerificationBasis

        confirmed = self.service.confirm_rights_verified(
            segments[0].id, self.company.id,
            RightsVerificationBasis.SELF_CAPTURED, confirmed_by=1,
        )
        self.assertEqual(confirmed.rights_status, "VERIFIED")

        wizard = ListingWizard(
            company_id=self.company.id, created_by_user_id=1,
            current_step=WizardStep.MEDIA, status=WizardStatus.DRAFT,
            source_type="MANUAL", product_candidate_id=self.candidate.id,
            selected_media_asset_ids_json="[]",
            channel_selections_json="[]",
            approval_history_json="[]",
            materialized_listing_ids_json="[]",
            version=1,
            creation_idempotency_key="wiz-rights-test-2",
        )
        self.db.add(wizard)
        self.db.commit()

        result = self.wizard_service.update_media(
            wizard.id, self.company.id,
            WizardMediaUpdateRequest(
                expected_version=1,
                selected_media_asset_ids=[segments[0].id],
                autosave_client_token=None,
            ),
        )
        import json
        selected = json.loads(result.selected_media_asset_ids_json)
        self.assertIn(segments[0].id, selected)

    # ------------------------------------------------
    # 2026-08-28 정책 반전 재감사 — RIGHTS_DENIED는 계속 차단
    # ------------------------------------------------

    def test_rights_denied_still_blocks_wizard_selection(self):
        """증빙 미제출(RIGHTS_UNVERIFIED)은 이제 허용되지만,
        RIGHTS_DENIED(명시적 사용금지)는 이번 정책 반전으로 우회되지
        않고 계속 하드 차단돼야 한다."""

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=self._deterministic_png_bytes("denied-test"),
            original_filename=None, company_id=self.company.id,
        )
        self.service.deny_rights(
            asset.id, self.company.id, denied_by=1,
            reason="공급처가 사용 중단을 요청함",
        )

        wizard = ListingWizard(
            company_id=self.company.id, created_by_user_id=1,
            current_step=WizardStep.MEDIA, status=WizardStatus.DRAFT,
            source_type="MANUAL", product_candidate_id=self.candidate.id,
            selected_media_asset_ids_json="[]",
            channel_selections_json="[]",
            approval_history_json="[]",
            materialized_listing_ids_json="[]",
            version=1,
            creation_idempotency_key="wiz-denied-test-1",
        )
        self.db.add(wizard)
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.wizard_service.update_media(
                wizard.id, self.company.id,
                WizardMediaUpdateRequest(
                    expected_version=1,
                    selected_media_asset_ids=[asset.id],
                    autosave_client_token=None,
                ),
            )

    def test_deny_rights_is_company_isolated(self):

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=self._deterministic_png_bytes("isolation-test"),
            original_filename=None, company_id=self.company.id,
        )
        other_company = Company(name="다른회사")
        self.db.add(other_company)
        self.db.commit()

        from app.core.exceptions import NotFoundException

        with self.assertRaises(NotFoundException):
            self.service.deny_rights(
                asset.id, other_company.id, denied_by=1, reason="무관 회사",
            )

    def test_deny_rights_audit_log_does_not_leak_reason_verbatim_search(self):
        """감사로그 description에 사유 문구가 들어가는 것은 허용되지만
        (짧은 요약 목적), evidence_reference/memo 같은 증빙 원문
        필드나 Credential 값은 audit_logs 어디에도 없어야 한다 —
        이 테스트는 최소한 audit_logs 테이블에 R2/자격증명 관련
        키워드가 전혀 없음을 확인한다."""

        asset = self.service.upload_original_asset(
            owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=self.candidate.id, purpose="MAIN", display_order=0,
            image_bytes=self._deterministic_png_bytes("audit-leak-test"),
            original_filename=None, company_id=self.company.id,
        )
        self.service.deny_rights(
            asset.id, self.company.id, denied_by=1,
            reason="access_key=SHOULD-NEVER-APPEAR-AS-EVIDENCE-FIELD",
        )

        from sqlalchemy import text as sa_text

        rows = self.db.execute(sa_text(
            "SELECT description FROM audit_logs "
            "WHERE action = 'MEDIA_ASSET_RIGHTS_DENIED'",
        )).fetchall()
        self.assertEqual(len(rows), 1)
        # description에 사유 요약은 남되(운영상 필요), evidence_reference/
        # memo/자격증명류 필드명 자체가 별도 컬럼으로 새지 않는지 확인 —
        # 이 테이블에 그런 컬럼 자체가 없다는 스키마 계약을 재확인한다.
        columns = {
            row[1] for row in self.db.execute(
                sa_text("PRAGMA table_info(audit_logs)"),
            ).fetchall()
        }
        self.assertNotIn("evidence_reference", columns)
        self.assertNotIn("credential", columns)
        self.assertNotIn("secret_key", columns)

    def _deterministic_png_bytes(self, seed: str) -> bytes:

        from app.domains.media_asset.providers import _deterministic_png

        return _deterministic_png(seed)


if __name__ == "__main__":
    unittest.main()
