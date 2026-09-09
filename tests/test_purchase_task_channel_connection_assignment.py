"""
=========================================================
Homez OS

File : tests/test_purchase_task_channel_connection_assignment.py

Gate PT-3(2026-09-08, item 7) — PurchaseTaskService.
assign_channel_connection() 격리 테스트: 매입 작업의 계정 선택·전달·
재시도, 비활성/미검증 연결 차단, 회사 간 격리, 매입처 불일치 차단.
임시 SQLite 파일만 사용한다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationRead
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.model import (
    PurchaseChannelConnection,
    PurchaseChannelConnectionEvent,
    PurchaseRecord,
    PurchaseTask,
    PurchaseTaskBudgetReservation,
    PurchaseTaskCandidate,
    PurchaseTaskCsvImportLog,
    PurchaseTaskEmailLog,
    PurchaseTaskEmailPreference,
    PurchaseTaskPolicySetting,
    PurchaseTaskTrackingInfo,
)
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService
from app.domains.purchase_task.service import PurchaseTaskService
from app.domains.role.model import Role
from app.domains.user.model import User

NOW = datetime(2026, 9, 8, 12, 0, 0)

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)

_SOURCE_ATTRS = {
    "brand": "브랜드A", "manufacturer": "브랜드A", "model_name": "MODEL-1",
    "gtin": "1111111111111", "capacity": "100ml", "color_or_scent": "블랙",
    "options": ["기본"],
}


class ChannelConnectionAssignmentTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, FundingAccount.__table__,
                FundingLedger.__table__, PurchaseTask.__table__,
                PurchaseTaskCandidate.__table__,
                PurchaseTaskBudgetReservation.__table__,
                PurchaseRecord.__table__, PurchaseTaskTrackingInfo.__table__,
                PurchaseTaskEmailPreference.__table__,
                PurchaseTaskEmailLog.__table__,
                PurchaseTaskPolicySetting.__table__,
                PurchaseTaskCsvImportLog.__table__,
                PurchaseChannelConnection.__table__,
                PurchaseChannelConnectionEvent.__table__,
                AutomationModeState.__table__, EmergencyStop.__table__,
                ExecutionLimit.__table__, ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__, Role.__table__,
                User.__table__,
                Notification.__table__, NotificationRead.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.execute(text(AUDIT_LOGS_DDL))

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="부산",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.account_a = FundingAccount(
            company_id=self.company_a.id, total_funding=1000000.0,
        )
        self.db.add(self.account_a)
        self.db.commit()

        policy_svc = PurchaseTaskPolicyService(self.db)
        setting = policy_svc.get_or_create_default_settings(self.company_a.id)
        setting.min_net_profit = 0
        setting.min_margin_rate = 0
        setting.max_price_increase_rate = 0.5
        setting.require_return_allowed = True
        self.db.commit()

        self.service = PurchaseTaskService(self.db)
        self.connection_service = PurchaseChannelConnectionService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_task(self, key="pt:1"):

        return self.service.create_task(
            self.company_a.id, source_order_id=1, source_order_item_id=None,
            product_title="무선이어폰", brand="브랜드A", manufacturer="브랜드A",
            model_name="MODEL-1", gtin="1111111111111", capacity="100ml",
            quantity=1, color_or_scent="블랙", options=["기본"],
            coupang_sale_amount=30000.0, coupang_fee_amount=3000.0,
            purchase_deadline=NOW + timedelta(days=3),
            idempotency_key=key,
        )

    def _add_candidate_and_select(self, task, key_suffix, mall_code="NAVER_SHOPPING"):

        candidate = self.service.add_candidate(
            task.id, self.company_a.id, shopping_mall_code=mall_code,
            product_url=f"https://search.shopping.naver.com/product/{key_suffix}",
            candidate_title="무선이어폰 후보", brand="브랜드A",
            manufacturer="브랜드A", model_name="MODEL-1", gtin="1111111111111",
            capacity="100ml", color_or_scent="블랙", options=["기본"],
            estimated_price=10000.0, estimated_shipping_fee=3000.0,
            estimated_delivery_days=2, seller_trust_score=0.9,
            return_allowed=True,
        )
        self.service.run_match_check(
            task.id, candidate.id, self.company_a.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        task, _ = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company_a.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )
        return task, candidate

    def test_assign_verified_active_connection_succeeds(self):

        task = self._create_task(key="assign:1")
        task, _ = self._add_candidate_and_select(task, "assign1")

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A사 계정",
        )
        self.connection_service.mark_verified(connection.id, self.company_a.id)

        updated = self.service.assign_channel_connection(
            task.id, self.company_a.id, connection.id, triggered_by=1,
        )
        self.assertEqual(updated.channel_connection_id, connection.id)

    def test_assign_rejects_unverified_connection(self):

        task = self._create_task(key="assign:2")
        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A사 계정",
        )
        with self.assertRaises(ConflictException):
            self.service.assign_channel_connection(
                task.id, self.company_a.id, connection.id,
            )

    def test_assign_rejects_inactive_connection(self):

        task = self._create_task(key="assign:3")
        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A사 계정",
        )
        self.connection_service.mark_verified(connection.id, self.company_a.id)
        self.connection_service.deactivate_connection(connection.id, self.company_a.id)

        with self.assertRaises(ConflictException):
            self.service.assign_channel_connection(
                task.id, self.company_a.id, connection.id,
            )

    def test_assign_rejects_other_companys_connection(self):
        """회사 간 조회·수정·실행 격리."""

        task = self._create_task(key="assign:4")
        foreign_connection = self.connection_service.create_connection(
            self.company_b.id, mall_code="NAVER_SHOPPING", account_label="B사 계정",
        )
        self.connection_service.mark_verified(foreign_connection.id, self.company_b.id)

        with self.assertRaises(NotFoundException):
            self.service.assign_channel_connection(
                task.id, self.company_a.id, foreign_connection.id,
            )

    def test_assign_rejects_mall_code_mismatch_with_selected_candidate(self):

        task = self._create_task(key="assign:5")
        task, _ = self._add_candidate_and_select(
            task, "assign5", mall_code="NAVER_SHOPPING",
        )

        wrong_mall_connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="ELEVENST", account_label="11번가 계정",
        )
        self.connection_service.mark_verified(
            wrong_mall_connection.id, self.company_a.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.assign_channel_connection(
                task.id, self.company_a.id, wrong_mall_connection.id,
            )

    def test_reassign_after_failure_is_allowed_retry(self):
        """매입 작업의 계정 선택·전달·재시도 — 첫 연결로 실패했다고
        가정하고 다른 검증된 연결로 재배정할 수 있어야 한다."""

        task = self._create_task(key="assign:6")
        task, _ = self._add_candidate_and_select(task, "assign6")

        first_connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="첫 계정",
        )
        self.connection_service.mark_verified(first_connection.id, self.company_a.id)
        self.service.assign_channel_connection(
            task.id, self.company_a.id, first_connection.id,
        )

        second_connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="대체 계정",
        )
        self.connection_service.mark_verified(second_connection.id, self.company_a.id)

        retried = self.service.assign_channel_connection(
            task.id, self.company_a.id, second_connection.id,
        )
        self.assertEqual(retried.channel_connection_id, second_connection.id)

    def test_unknown_connection_id_raises_not_found(self):

        task = self._create_task(key="assign:7")
        with self.assertRaises(NotFoundException):
            self.service.assign_channel_connection(task.id, self.company_a.id, 999999)

    def test_purchase_record_keeps_connection_id_after_disconnect(self):
        """연결 해제 후에도 기존 매입 기록의 계정 추적은 유지한다 —
        지시문 4번/6번 핵심 요구사항."""

        task = self._create_task(key="assign:8")
        task, _ = self._add_candidate_and_select(task, "assign8")

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A사 계정",
        )
        self.connection_service.mark_verified(connection.id, self.company_a.id)
        self.service.assign_channel_connection(
            task.id, self.company_a.id, connection.id,
        )

        task = self.service.open_payment_page(task.id, self.company_a.id)
        task = self.service.record_purchase(
            task.id, self.company_a.id, shopping_mall_code="NAVER_SHOPPING",
            external_order_number="N-ORDER-8", actual_amount=13000.0,
            actual_shipping_fee=3000.0, purchased_at=NOW,
            selected_option_note="기본", memo=None, recorded_by=1,
            idempotency_key="rec:assign8",
        )

        record = self.db.query(PurchaseRecord).filter(
            PurchaseRecord.purchase_task_id == task.id,
        ).first()
        self.assertEqual(record.channel_connection_id, connection.id)

        # 연결을 해제해도 이미 만들어진 기록의 계정 추적은 그대로다.
        self.connection_service.deactivate_connection(connection.id, self.company_a.id)
        self.db.refresh(record)
        self.assertEqual(record.channel_connection_id, connection.id)

        # 그리고 그 연결은 여전히 조회 가능하다(하드 삭제하지 않으므로).
        still_resolvable = self.connection_service.get_connection_or_404(
            record.channel_connection_id, self.company_a.id,
        )
        self.assertFalse(still_resolvable.is_active)
        self.assertEqual(still_resolvable.account_label, "A사 계정")


if __name__ == "__main__":
    unittest.main()
