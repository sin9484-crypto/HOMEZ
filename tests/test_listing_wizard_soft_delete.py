"""
=========================================================
Homez OS

File : tests/test_listing_wizard_soft_delete.py

2026-08-28 "대기 상품 정리" — ListingWizard soft delete(archive)/
restore/bulk-archive 서비스 계층 검증. 실제 DB·외부 API 없음 — 전부
임시 SQLite만 사용한다.

파생 이미지 ORPHANED 처리에 대한 참고: 실제 코드를 확인한 결과
ListingWizard.selected_media_asset_ids_json은 항상 ProductCandidate가
소유한(owner_type=PRODUCT_CANDIDATE) 기존 MediaAsset을 "선택"할 뿐,
ListingWizard 자신이 소유하는 파생 MediaAsset은 존재하지 않는다
(MediaAssetOwnerType.LISTING_PACKAGE는 app/domains/listing_package
라는 별개 Domain 전용이고 이 Domain에서는 어디서도 쓰이지 않음 —
grep으로 확인). 따라서 이 파일은 "archive()가 MediaAsset을 전혀
건드리지 않는다"를 직접 검증한다 — ORPHANED 전이 자체가 이 Domain에는
적용 대상이 없다는 사실을 코드로 증명한다.
=========================================================
"""

import json
import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing.constants import MAX_BULK_ARCHIVE_COUNT
from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardBulkArchiveRequest,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    ListingWizardService,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    deletion_eligibility,
)
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.constants import MediaAssetRole
from app.domains.media_asset.model import MediaAsset
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)

_COUNTER = 0


def _key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1
    return f"{prefix}-{_COUNTER}"


class ListingWizardSoftDeleteTestCase(unittest.TestCase):

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
                ListingWizard.__table__,
                MediaAsset.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            ))

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = ListingWizardService(self.db)

        self.company = self._seed_company("회사 A")
        self.other_company = self._seed_company("회사 B")

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # ---------------- 시딩 헬퍼 ----------------

    def _seed_company(self, name: str) -> Company:

        company = Company(
            name=name, business_number=_key("000-00"), ceo="테스트",
            phone="02-000-0000", email=f"{_key('wiz')}@test.com",
            address="서울",
        )
        self.db.add(company)
        self.db.commit()
        return company

    def _seed_wizard(
        self, company_id: int, *, status: str = WizardStatus.DRAFT,
        materialized_listing_ids: list | None = None,
        selected_media_asset_ids: list[int] | None = None,
    ) -> ListingWizard:

        wizard = ListingWizard(
            company_id=company_id, created_by_user_id=1,
            current_step="SOURCE", status=status, source_type="MANUAL",
            selected_media_asset_ids_json=json.dumps(
                selected_media_asset_ids or [],
            ),
            materialized_listing_ids_json=json.dumps(
                materialized_listing_ids or [],
            ),
            creation_idempotency_key=_key("create"),
        )
        self.db.add(wizard)
        self.db.commit()
        return wizard

    def _seed_media_asset(self, company_id: int, candidate_id: int) -> MediaAsset:

        asset = MediaAsset(
            company_id=company_id, owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE,
            owner_id=candidate_id, asset_role=MediaAssetRole.ORIGINAL,
            purpose="MAIN", display_order=0,
            storage_path=f"media/{company_id}/{_key('a')}.jpg",
            mime_type="image/jpeg", file_size_bytes=100,
            sha256_hex=_key("sha").ljust(64, "0")[:64],
            width=800, height=800, status="ACTIVE",
        )
        self.db.add(asset)
        self.db.commit()
        return asset

    # ---------------- deletion_eligibility ----------------

    def test_draft_is_deletable(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        eligible, reason = deletion_eligibility(wizard)
        self.assertTrue(eligible)
        self.assertIsNone(reason)

    def test_ready_for_approval_is_deletable(self):

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.READY_FOR_APPROVAL,
        )
        eligible, _ = deletion_eligibility(wizard)
        self.assertTrue(eligible)

    def test_failed_is_deletable(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.FAILED)
        eligible, _ = deletion_eligibility(wizard)
        self.assertTrue(eligible)

    def test_cancelled_is_deletable(self):

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.CANCELLED,
        )
        eligible, _ = deletion_eligibility(wizard)
        self.assertTrue(eligible)

    def test_succeeded_is_not_deletable(self):

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.SUCCEEDED,
        )
        eligible, reason = deletion_eligibility(wizard)
        self.assertFalse(eligible)
        self.assertIsNotNone(reason)

    def test_approved_is_not_deletable(self):

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.APPROVED,
        )
        eligible, reason = deletion_eligibility(wizard)
        self.assertFalse(eligible)
        self.assertIn("승인", reason)

    def test_submitting_is_not_deletable(self):

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.SUBMITTING,
        )
        eligible, _ = deletion_eligibility(wizard)
        self.assertFalse(eligible)

    def test_materialized_listing_ids_block_even_if_status_deletable(self):
        """상태만으로는 잡히지 않는 경계 사례 — 외부 Listing ID가 하나라도
        있으면 status가 무엇이든 fail-closed로 차단한다."""

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.FAILED,
            materialized_listing_ids=[
                {"marketplace_account_id": 1, "listing_id": 99, "status": "PENDING"},
            ],
        )
        eligible, reason = deletion_eligibility(wizard)
        self.assertFalse(eligible)
        self.assertIn("쿠팡", reason)

    # ---------------- archive() / restore() ----------------

    def test_archive_draft_wizard_succeeds(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)

        archived = self.service.archive(
            wizard.id, self.company.id, wizard.version, 42, "테스트 삭제",
            _key("del"),
        )

        self.assertEqual(archived.status, WizardStatus.ARCHIVED)
        self.assertIsNotNone(archived.deleted_at)
        self.assertEqual(archived.deleted_by_user_id, 42)
        self.assertEqual(archived.status_before_archive, WizardStatus.DRAFT)

    def test_archive_writes_audit_log(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        self.service.archive(
            wizard.id, self.company.id, wizard.version, 42, "감사로그 확인",
            _key("del"),
        )

        rows = self.db.execute(
            text("SELECT action, entity_id FROM audit_logs"),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "LISTING_WIZARD_ARCHIVED")
        self.assertEqual(rows[0][1], str(wizard.id))

    def test_archive_blocked_for_succeeded_wizard(self):

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.SUCCEEDED,
        )
        with self.assertRaises(BadRequestException):
            self.service.archive(
                wizard.id, self.company.id, wizard.version, 42, "차단 확인",
                _key("del"),
            )
        self.db.rollback()
        current = self.db.get(ListingWizard, wizard.id)
        self.assertEqual(current.status, WizardStatus.SUCCEEDED)

    def test_archive_blocked_when_materialized_listing_id_present(self):

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.DRAFT,
            materialized_listing_ids=[
                {"marketplace_account_id": 1, "listing_id": 5, "status": "PENDING"},
            ],
        )
        with self.assertRaises(BadRequestException):
            self.service.archive(
                wizard.id, self.company.id, wizard.version, 42, "차단 확인",
                _key("del"),
            )

    def test_archive_then_restore_round_trip(self):

        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.READY_FOR_APPROVAL,
        )
        archived = self.service.archive(
            wizard.id, self.company.id, wizard.version, 42, "복원 테스트",
            _key("del"),
        )
        restored = self.service.restore(
            wizard.id, self.company.id, archived.version, 99,
        )

        self.assertEqual(restored.status, WizardStatus.READY_FOR_APPROVAL)
        self.assertIsNone(restored.deleted_at)
        self.assertIsNone(restored.deleted_by_user_id)
        self.assertIsNone(restored.delete_reason)
        self.assertIsNone(restored.status_before_archive)
        self.assertIsNotNone(restored.restored_at)
        self.assertEqual(restored.restored_by_user_id, 99)

    def test_restore_rejects_non_archived_wizard(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        with self.assertRaises(BadRequestException):
            self.service.restore(wizard.id, self.company.id, wizard.version, 99)

    # ---------------- idempotency / concurrency ----------------

    def test_repeated_archive_with_same_deletion_request_id_is_idempotent(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        request_id = _key("del")

        first = self.service.archive(
            wizard.id, self.company.id, wizard.version, 42, "재시도 테스트",
            request_id,
        )
        second = self.service.archive(
            wizard.id, self.company.id, first.version, 42, "재시도 테스트",
            request_id,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(second.status, WizardStatus.ARCHIVED)
        rows = self.db.execute(
            text("SELECT COUNT(*) FROM audit_logs WHERE action='LISTING_WIZARD_ARCHIVED'"),
        ).fetchone()
        self.assertEqual(rows[0], 1, "재요청은 감사로그를 중복 기록하지 않아야 한다.")

    def test_archive_with_different_deletion_request_id_on_already_archived_conflicts(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        self.service.archive(
            wizard.id, self.company.id, wizard.version, 42, "최초 삭제",
            _key("del"),
        )
        with self.assertRaises(ConflictException):
            self.service.archive(
                wizard.id, self.company.id, wizard.version, 42, "다른 요청",
                _key("del"),
            )

    def test_duplicate_click_same_deletion_request_id_converges_without_error(self):
        """같은 삭제 버튼을 두 번 누르는 것처럼, 같은 deletion_request_id를
        가진 두 요청이 순차로 도착하는 상황 — 첫 번째가 실제 전이를
        수행하고, 두 번째는 예외 없이 같은 결과로 수렴해야 한다."""

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        stale_version = wizard.version
        request_id = _key("del-dup")

        winner = self.service.archive(
            wizard.id, self.company.id, stale_version, 1, "첫 번째 요청",
            request_id,
        )
        self.assertEqual(winner.status, WizardStatus.ARCHIVED)

        loser = self.service.archive(
            wizard.id, self.company.id, stale_version, 1, "두 번째 요청(중복)",
            request_id,
        )
        self.assertEqual(loser.status, WizardStatus.ARCHIVED)
        self.assertEqual(loser.id, winner.id)

    def test_repository_level_race_only_one_conditional_update_wins(self):
        """서비스 계층의 사전 재조회를 우회해 DB 원자성 자체를 검증한다
        — 진짜 동시 요청(두 커넥션이 거의 동시에 UPDATE)을 흉내내,
        같은 version으로 두 번 조건부 UPDATE를 시도하면 정확히 하나만
        rowcount=1이어야 한다."""

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)

        first_rowcount = self.service.repository.archive_conditional(
            wizard.id, self.company.id, wizard.version,
            WizardStatus.DELETABLE_FROM, 1, "첫 시도", _key("del-race-a"),
        )
        second_rowcount = self.service.repository.archive_conditional(
            wizard.id, self.company.id, wizard.version,
            WizardStatus.DELETABLE_FROM, 2, "두번째 시도", _key("del-race-b"),
        )
        self.db.commit()

        self.assertEqual({first_rowcount, second_rowcount}, {0, 1})
        self.assertEqual(first_rowcount + second_rowcount, 1)

    def test_archive_stale_version_on_still_editable_wizard_conflicts(self):
        """archive()가 아니라 다른 편집이 먼저 버전을 올린 경우는 진짜
        버전 충돌이어야 한다(삭제됨으로 조용히 수렴시키지 않는다)."""

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        stale_version = wizard.version

        rowcount = self.service.repository.update_status_conditional(
            wizard.id, self.company.id, wizard.version,
            (WizardStatus.DRAFT,), WizardStatus.VALIDATING,
        )
        self.assertEqual(rowcount, 1)
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.archive(
                wizard.id, self.company.id, stale_version, 1, "충돌 확인",
                _key("del"),
            )

    # ---------------- company isolation ----------------

    def test_archive_is_company_isolated(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        with self.assertRaises(Exception):
            self.service.archive(
                wizard.id, self.other_company.id, wizard.version, 1,
                "타사 삭제 시도", _key("del"),
            )
        self.db.rollback()
        current = self.db.get(ListingWizard, wizard.id)
        self.assertEqual(current.status, WizardStatus.DRAFT)

    def test_list_wizards_is_company_isolated_for_archived_items(self):

        wizard = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        self.service.archive(
            wizard.id, self.company.id, wizard.version, 1, "격리 테스트",
            _key("del"),
        )

        other_company_archived = self.service.list_wizards(
            self.other_company.id, include_archived=True,
        )
        self.assertEqual(other_company_archived, [])

    # ---------------- media_assets / 증빙 보존 ----------------

    def test_archive_never_touches_media_assets(self):
        """실제 코드 확인 결과 ListingWizard는 파생 MediaAsset을 소유하지
        않는다(선택만 한다) — archive()가 MediaAsset 테이블에 아무
        영향도 주지 않음을 직접 증명한다."""

        candidate = ProductCandidate(
            candidate_key=_key("cand"), source_type="MANUAL",
            source_reference="ref", market="COUPANG",
            product_name="테스트 상품", status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        asset = self._seed_media_asset(self.company.id, candidate.id)
        wizard = self._seed_wizard(
            self.company.id, status=WizardStatus.DRAFT,
            selected_media_asset_ids=[asset.id],
        )

        self.service.archive(
            wizard.id, self.company.id, wizard.version, 1, "이미지 보존 확인",
            _key("del"),
        )

        preserved = self.db.get(MediaAsset, asset.id)
        self.assertIsNotNone(preserved)
        self.assertEqual(preserved.status, "ACTIVE")

    # ---------------- bulk_archive ----------------

    def test_bulk_archive_explicit_ids_mixed_statuses(self):

        deletable = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        blocked = self._seed_wizard(
            self.company.id, status=WizardStatus.SUCCEEDED,
        )
        missing_id = 999999

        response = self.service.bulk_archive(
            WizardBulkArchiveRequest(
                wizard_ids=[deletable.id, blocked.id, missing_id],
                reason="혼합 상태 테스트",
                deletion_request_id=_key("bulk"),
            ),
            self.company.id, 1,
        )

        self.assertEqual(response.requested_count, 3)
        self.assertEqual(response.succeeded, [deletable.id])
        skipped_ids = {item.wizard_id for item in response.skipped}
        self.assertEqual(skipped_ids, {blocked.id, missing_id})
        self.assertEqual(response.failed, [])

        self.db.expire_all()
        self.assertEqual(
            self.db.get(ListingWizard, deletable.id).status, WizardStatus.ARCHIVED,
        )
        self.assertEqual(
            self.db.get(ListingWizard, blocked.id).status, WizardStatus.SUCCEEDED,
        )

    def test_bulk_archive_partial_failure_does_not_roll_back_prior_success(self):
        """건별 독립 Transaction 계약 — 목록 중간의 실패가 이미 성공한
        앞선 항목의 삭제를 되돌리지 않아야 한다."""

        first = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        second = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)

        response = self.service.bulk_archive(
            WizardBulkArchiveRequest(
                wizard_ids=[first.id, 999999, second.id],
                reason="부분 실패 테스트",
                deletion_request_id=_key("bulk"),
            ),
            self.company.id, 1,
        )

        self.assertIn(first.id, response.succeeded)
        self.assertIn(second.id, response.succeeded)

        self.db.expire_all()
        self.assertEqual(
            self.db.get(ListingWizard, first.id).status, WizardStatus.ARCHIVED,
        )
        self.assertEqual(
            self.db.get(ListingWizard, second.id).status, WizardStatus.ARCHIVED,
        )

    def test_bulk_archive_requires_exactly_one_mode(self):

        with self.assertRaises(BadRequestException):
            self.service.bulk_archive(
                WizardBulkArchiveRequest(
                    wizard_ids=None, select_all_matching_filter=False,
                    reason="모드 없음", deletion_request_id=_key("bulk"),
                ),
                self.company.id, 1,
            )

    def test_bulk_archive_select_all_requires_confirm_text(self):

        self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)

        with self.assertRaises(BadRequestException):
            self.service.bulk_archive(
                WizardBulkArchiveRequest(
                    select_all_matching_filter=True, confirm_text="지워줘",
                    reason="확인문구 오류", deletion_request_id=_key("bulk"),
                ),
                self.company.id, 1,
            )

    def test_bulk_archive_select_all_matching_filter_respects_status_filter(self):

        draft = self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        failed = self._seed_wizard(self.company.id, status=WizardStatus.FAILED)

        response = self.service.bulk_archive(
            WizardBulkArchiveRequest(
                select_all_matching_filter=True, confirm_text="삭제",
                status_filter=WizardStatus.DRAFT, reason="필터 확인",
                deletion_request_id=_key("bulk"),
            ),
            self.company.id, 1,
        )

        self.assertEqual(response.succeeded, [draft.id])
        self.db.expire_all()
        self.assertEqual(
            self.db.get(ListingWizard, failed.id).status, WizardStatus.FAILED,
        )

    def test_bulk_archive_select_all_over_cap_is_rejected(self):

        for _ in range(MAX_BULK_ARCHIVE_COUNT + 1):
            self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)

        with self.assertRaises(BadRequestException):
            self.service.bulk_archive(
                WizardBulkArchiveRequest(
                    select_all_matching_filter=True, confirm_text="삭제",
                    reason="상한 확인", deletion_request_id=_key("bulk"),
                ),
                self.company.id, 1,
            )

    def test_bulk_archive_is_company_isolated(self):

        other_wizard = self._seed_wizard(
            self.other_company.id, status=WizardStatus.DRAFT,
        )

        response = self.service.bulk_archive(
            WizardBulkArchiveRequest(
                wizard_ids=[other_wizard.id], reason="타사 항목 시도",
                deletion_request_id=_key("bulk"),
            ),
            self.company.id, 1,
        )

        self.assertEqual(response.succeeded, [])
        self.assertEqual(len(response.skipped), 1)
        self.db.expire_all()
        self.assertEqual(
            self.db.get(ListingWizard, other_wizard.id).status, WizardStatus.DRAFT,
        )

    # ---------------- preview ----------------

    def test_preview_bulk_archive_count_matches_actual_deletable_set(self):

        self._seed_wizard(self.company.id, status=WizardStatus.DRAFT)
        self._seed_wizard(self.company.id, status=WizardStatus.FAILED)
        self._seed_wizard(self.company.id, status=WizardStatus.SUCCEEDED)

        preview = self.service.preview_bulk_archive_count(self.company.id, None)

        self.assertEqual(preview.deletable_count, 2)
        self.assertFalse(preview.capped)


if __name__ == "__main__":
    unittest.main()
