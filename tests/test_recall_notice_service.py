"""
=========================================================
Homez OS

File : tests/test_recall_notice_service.py

2026-09-15 전면 감사 후속(Phase 9I/9J, HOMEZ_USER_OPERATION_SETTINGS.md
10-17/10-18) — RecallNoticeService 격리 테스트. 실제 homez.db·실제
외부 네트워크는 전혀 없다(FakeRecallNoticeProvider만 사용).
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.recall_notice.constants import RecallCheckJobMode
from app.domains.recall_notice.constants import RecallCheckRunStatus
from app.domains.recall_notice.constants import RecallProductBlockStatus
from app.domains.recall_notice.provider import FakeRecallNoticeProvider
from app.domains.recall_notice.provider import RecallNoticeRecord
from app.domains.recall_notice.service import RecallNoticeService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class RecallNoticeServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="리콜테스트 회사", business_number="999-99-99991",
            ceo="테스트", phone="02-000-0000",
            email="recall@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="recalladmin",
            email="recalladmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.service = RecallNoticeService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def _notification_rows(self, event_code):

        return self.db.execute(
            text(
                "SELECT user_id FROM notification_email_logs "
                "WHERE event_code = :code",
            ),
            {"code": event_code},
        ).fetchall()

    # ------------------------------
    # Phase 9I — Job 모드
    # ------------------------------

    def test_job_mode_defaults_to_paused(self):

        self.assertEqual(self.service.get_job_mode(), RecallCheckJobMode.PAUSED)

    def test_set_job_mode_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_job_mode(
                RecallCheckJobMode.ACTIVE, set_by=self.admin.id, is_admin=False,
            )

    def test_set_job_mode_rejects_unknown_value(self):

        with self.assertRaises(BadRequestException):
            self.service.set_job_mode(
                "SOMETHING_ELSE", set_by=self.admin.id, is_admin=True,
            )

    def test_set_job_mode_updates_latest_wins(self):

        self.service.set_job_mode(
            RecallCheckJobMode.ACTIVE, set_by=self.admin.id, is_admin=True,
        )
        self.assertEqual(self.service.get_job_mode(), RecallCheckJobMode.ACTIVE)

        self.service.set_job_mode(
            RecallCheckJobMode.PAUSED, set_by=self.admin.id, is_admin=True,
        )
        self.assertEqual(self.service.get_job_mode(), RecallCheckJobMode.PAUSED)

    # ------------------------------
    # Phase 9I — 매일 확인 실행(Fake Provider)
    # ------------------------------

    def test_daily_check_ingests_new_notices(self):

        provider = FakeRecallNoticeProvider([
            RecallNoticeRecord(
                product_identifier="P1", reason="위해성 확인",
                source="FAKE", manufacturer="제조사A", model="모델A",
                announcement_date=datetime(2026, 9, 1),
            ),
            RecallNoticeRecord(
                product_identifier="P2", reason="자발적 회수",
                source="FAKE",
            ),
        ])

        run = self.service.run_daily_check(provider)

        self.assertEqual(run.status, RecallCheckRunStatus.SUCCESS)
        self.assertEqual(run.notices_found_count, 2)
        self.assertEqual(run.new_notices_count, 2)

    def test_duplicate_notice_across_runs_is_not_reingested(self):

        record = RecallNoticeRecord(
            product_identifier="P1", reason="위해성 확인", source="FAKE",
            manufacturer="제조사A", model="모델A",
            announcement_date=datetime(2026, 9, 1),
        )

        first = self.service.run_daily_check(FakeRecallNoticeProvider([record]))
        second = self.service.run_daily_check(FakeRecallNoticeProvider([record]))

        self.assertEqual(first.new_notices_count, 1)
        self.assertEqual(second.notices_found_count, 1)
        self.assertEqual(
            second.new_notices_count, 0,
            "같은 공고를 다시 수집해도 새 행이 추가되면 안 된다.",
        )

    def test_partial_failure_preserves_already_ingested_notices(self):
        """이미 처리한 항목까지는 저장되고, 그 이후만 실패로
        기록되는지 검증한다."""

        first_two = [
            RecallNoticeRecord(
                product_identifier="P1", reason="사유1", source="FAKE",
            ),
            RecallNoticeRecord(
                product_identifier="P2", reason="사유2", source="FAKE",
            ),
        ]
        provider = FakeRecallNoticeProvider(
            first_two + [
                RecallNoticeRecord(
                    product_identifier="P3", reason="사유3", source="FAKE",
                ),
            ],
            fail_after=2,
        )

        run = self.service.run_daily_check(provider)

        self.assertEqual(run.status, RecallCheckRunStatus.PARTIAL_FAILURE)
        self.assertEqual(run.notices_found_count, 2)
        self.assertEqual(run.new_notices_count, 2)
        self.assertIsNotNone(run.error_detail)

        # 재실행(재시작을 흉내)해도 이미 저장된 2건은 다시 세지 않는다.
        second = self.service.run_daily_check(
            FakeRecallNoticeProvider(first_two),
        )
        self.assertEqual(second.new_notices_count, 0)

    def test_total_failure_before_any_item_is_recorded_as_failure(self):

        provider = FakeRecallNoticeProvider(
            [
                RecallNoticeRecord(
                    product_identifier="P1", reason="사유1", source="FAKE",
                ),
            ],
            fail_after=0,
        )

        run = self.service.run_daily_check(provider)

        self.assertEqual(run.status, RecallCheckRunStatus.FAILURE)
        self.assertEqual(run.notices_found_count, 0)
        self.assertEqual(run.new_notices_count, 0)

    def test_restart_across_new_service_instance_still_dedupes(self):
        """"재시작" 검증 — 새 세션·새 서비스 인스턴스로도 이미 저장된
        공고를 그대로 인식한다(메모리 상태가 아니라 DB에 근거)."""

        record = RecallNoticeRecord(
            product_identifier="P1", reason="위해성 확인", source="FAKE",
        )
        self.service.run_daily_check(FakeRecallNoticeProvider([record]))

        fresh_service = RecallNoticeService(self.db)
        second = fresh_service.run_daily_check(FakeRecallNoticeProvider([record]))

        self.assertEqual(second.new_notices_count, 0)

    # ------------------------------
    # Phase 9J — 차단/해제
    # ------------------------------

    def test_block_product_creates_blocked_row_and_notifies(self):

        block = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )

        self.assertEqual(block.status, RecallProductBlockStatus.BLOCKED)
        self.assertEqual(len(self._notification_rows("RECALL_PRODUCT_BLOCKED")), 1)

    def test_block_product_rejects_blank_reason(self):

        with self.assertRaises(BadRequestException):
            self.service.block_product(
                company_id=self.company.id, product_identifier="P1",
                reason="   ",
            )

    def test_repeated_block_for_same_product_does_not_duplicate_or_renotify(self):

        first = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="1차",
        )
        second = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="2차",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self._notification_rows("RECALL_PRODUCT_BLOCKED")), 1)

    def test_has_active_block_true_after_block(self):

        self.assertFalse(
            self.service.has_active_block(self.company.id, "P1"),
        )
        self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )
        self.assertTrue(
            self.service.has_active_block(self.company.id, "P1"),
        )

    def test_block_is_isolated_per_company(self):

        other_company = Company(
            name="다른 회사", business_number="999-99-99992",
            ceo="테스트", phone="02-000-0001",
            email="recall-other@example.com", address="테스트",
        )
        self.db.add(other_company)
        self.db.commit()

        self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )

        self.assertFalse(
            self.service.has_active_block(other_company.id, "P1"),
        )

    def test_assert_not_blocked_raises_when_blocked(self):

        self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )

        with self.assertRaises(ConflictException):
            self.service.assert_not_blocked(self.company.id, "P1")

    def test_assert_not_blocked_passes_when_never_blocked(self):

        self.service.assert_not_blocked(self.company.id, "NEVER-BLOCKED")

    def test_unblock_requires_admin(self):

        block = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )

        with self.assertRaises(ForbiddenException):
            self.service.unblock_product(
                block.id, self.company.id, is_admin=False,
                approved_by=self.admin.id, justification="확인함",
            )

    def test_unblock_requires_non_blank_justification(self):

        block = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )

        with self.assertRaises(BadRequestException):
            self.service.unblock_product(
                block.id, self.company.id, is_admin=True,
                approved_by=self.admin.id, justification="   ",
            )

    def test_unblock_succeeds_with_justification(self):

        block = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )

        resolved = self.service.unblock_product(
            block.id, self.company.id, is_admin=True,
            approved_by=self.admin.id,
            justification="정부 발표 철회 확인, 공문 첨부",
        )

        self.assertEqual(resolved.status, RecallProductBlockStatus.UNBLOCKED)
        self.assertFalse(
            self.service.has_active_block(self.company.id, "P1"),
        )

    def test_unblock_already_unblocked_rejected(self):

        block = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )
        self.service.unblock_product(
            block.id, self.company.id, is_admin=True,
            approved_by=self.admin.id, justification="1차 해제",
        )

        with self.assertRaises(BadRequestException):
            self.service.unblock_product(
                block.id, self.company.id, is_admin=True,
                approved_by=self.admin.id, justification="2차 해제",
            )

    def test_reblock_after_unblock_creates_new_row(self):
        """과거 해제가 영구 면제를 주지 않는다 — 같은 상품이 다시
        차단되면 새 BLOCKED 행이 생긴다."""

        first = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="1차 리콜",
        )
        self.service.unblock_product(
            first.id, self.company.id, is_admin=True,
            approved_by=self.admin.id, justification="1차 해제",
        )
        self.assertFalse(self.service.has_active_block(self.company.id, "P1"))

        second = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="2차 리콜(재발)",
        )

        self.assertNotEqual(first.id, second.id)
        self.assertTrue(self.service.has_active_block(self.company.id, "P1"))

    def test_get_block_other_company_not_found(self):

        block = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="리콜 확인됨",
        )

        with self.assertRaises(NotFoundException):
            self.service.get_block(block.id, self.company.id + 999)

    def test_list_blocks_filters_by_status(self):

        b1 = self.service.block_product(
            company_id=self.company.id, product_identifier="P1",
            reason="1차",
        )
        self.service.block_product(
            company_id=self.company.id, product_identifier="P2",
            reason="1차",
        )
        self.service.unblock_product(
            b1.id, self.company.id, is_admin=True,
            approved_by=self.admin.id, justification="해제",
        )

        blocked = self.service.list_blocks(
            self.company.id, status=RecallProductBlockStatus.BLOCKED,
        )
        self.assertEqual(
            [b.product_identifier for b in blocked], ["P2"],
        )


if __name__ == "__main__":
    unittest.main()
