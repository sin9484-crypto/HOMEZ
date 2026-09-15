"""
=========================================================
Homez OS

File : tests/test_supplier_incident_service.py

2026-09-15 전면 감사 후속(Phase 9B, HOMEZ_USER_OPERATION_SETTINGS.md
7-11 — "품절, 오배송, 취소 또는 배송 지연이 반복되면 자동발주를
일시 중지하고 사용자에게 재사용 여부를 묻는다") — SupplierIncidentService
격리 테스트. 실제 homez.db·실제 Windows Credential Manager·실제
외부 API는 전혀 없다 — bootstrap_environment()가 만든 임시 SQLite
파일 DB만 사용한다(알림 발송 확인에 notification_email_logs 테이블이
필요하므로 경량 fixture가 아니라 전체 스키마를 쓴다).
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
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.purchase_task.constants import (
    DEFAULT_SUPPLIER_INCIDENT_MAX_COUNT,
    DEFAULT_SUPPLIER_INCIDENT_WINDOW_DAYS,
    ChannelConnectionEventType,
    ConnectionMethod,
    SupplierIncidentType,
)
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.supplier_incident_service import (
    SupplierIncidentService,
)
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class SupplierIncidentServiceTestCase(unittest.TestCase):

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
            name="공급처사건테스트 회사", business_number="777-77-77771",
            ceo="테스트", phone="02-000-0000",
            email="supplier-incident@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="incidentadmin",
            email="incidentadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.connection = PurchaseChannelConnection(
            company_id=self.company.id, mall_code="ONCHANNEL",
            connection_method=ConnectionMethod.CREDENTIAL,
            account_label="테스트 연결",
        )
        self.db.add(self.connection)
        self.db.commit()

        self.service = SupplierIncidentService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def _notification_rows(self):

        return self.db.execute(
            text(
                "SELECT user_id, event_code FROM notification_email_logs "
                "WHERE event_code = 'SUPPLIER_CONNECTION_ORDER_AUTO_PAUSED'",
            ),
        ).fetchall()

    # ---------------- 기본값 ----------------

    def test_default_auto_pause_setting_used_when_unset(self):

        window_days, max_count = self.service.get_auto_pause_setting(
            self.company.id,
        )
        self.assertEqual(window_days, DEFAULT_SUPPLIER_INCIDENT_WINDOW_DAYS)
        self.assertEqual(max_count, DEFAULT_SUPPLIER_INCIDENT_MAX_COUNT)

    def test_set_auto_pause_setting_requires_admin(self):

        with self.assertRaises(ForbiddenException):
            self.service.set_auto_pause_setting(
                self.company.id, window_days=10, max_incident_count=2,
                set_by=self.admin.id, is_admin=False,
            )

    def test_set_auto_pause_setting_rejects_non_positive_values(self):

        with self.assertRaises(BadRequestException):
            self.service.set_auto_pause_setting(
                self.company.id, window_days=0, max_incident_count=2,
                set_by=self.admin.id, is_admin=True,
            )
        with self.assertRaises(BadRequestException):
            self.service.set_auto_pause_setting(
                self.company.id, window_days=10, max_incident_count=0,
                set_by=self.admin.id, is_admin=True,
            )

    # ---------------- 사건 기록 ----------------

    def test_record_incident_rejects_unknown_type(self):

        with self.assertRaises(BadRequestException):
            self.service.record_incident(
                connection_id=self.connection.id, company_id=self.company.id,
                incident_type="NOT_A_REAL_TYPE", recorded_by=self.admin.id,
            )

    def test_record_incident_rejects_unknown_connection(self):

        with self.assertRaises(NotFoundException):
            self.service.record_incident(
                connection_id=999999, company_id=self.company.id,
                incident_type=SupplierIncidentType.STOCKOUT,
                recorded_by=self.admin.id,
            )

    def test_list_incidents_returns_most_recent_first(self):

        first = self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.STOCKOUT,
            recorded_by=self.admin.id,
        )
        second = self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.CANCELLATION,
            recorded_by=self.admin.id,
        )

        incidents = self.service.list_incidents(
            self.connection.id, self.company.id,
        )
        self.assertEqual([i.id for i in incidents], [second.id, first.id])

    # ---------------- 자동 일시중지 ----------------

    def test_auto_pause_triggers_exactly_at_threshold_and_notifies(self):

        self.service.set_auto_pause_setting(
            self.company.id, window_days=30, max_incident_count=3,
            set_by=self.admin.id, is_admin=True,
        )

        for i in range(1, 3):
            self.service.record_incident(
                connection_id=self.connection.id, company_id=self.company.id,
                incident_type=SupplierIncidentType.STOCKOUT,
                recorded_by=self.admin.id,
            )
            row = self.db.query(PurchaseChannelConnection).get(self.connection.id)
            self.assertIsNone(
                row.order_paused_at,
                f"{i}번째 사건에서는 아직 일시중지되면 안 된다.",
            )

        self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.STOCKOUT,
            recorded_by=self.admin.id,
        )

        row = self.db.query(PurchaseChannelConnection).get(self.connection.id)
        self.assertIsNotNone(row.order_paused_at)
        self.assertIsNotNone(row.order_paused_reason)

        rows = self._notification_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], self.admin.id)

    def test_incidents_outside_window_do_not_count(self):

        from datetime import datetime, timedelta

        self.service.set_auto_pause_setting(
            self.company.id, window_days=7, max_incident_count=2,
            set_by=self.admin.id, is_admin=True,
        )

        old = datetime.utcnow() - timedelta(days=30)
        self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.MISSHIP,
            recorded_by=self.admin.id, occurred_at=old,
        )
        self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.MISSHIP,
            recorded_by=self.admin.id,
        )

        row = self.db.query(PurchaseChannelConnection).get(self.connection.id)
        self.assertIsNone(
            row.order_paused_at,
            "기간 밖의 사건은 집계에 포함되면 안 된다.",
        )

    def test_further_incidents_after_pause_do_not_repeat_notification(self):

        self.service.set_auto_pause_setting(
            self.company.id, window_days=30, max_incident_count=2,
            set_by=self.admin.id, is_admin=True,
        )
        for _ in range(4):
            self.service.record_incident(
                connection_id=self.connection.id, company_id=self.company.id,
                incident_type=SupplierIncidentType.DELIVERY_DELAY,
                recorded_by=self.admin.id,
            )

        self.assertEqual(len(self._notification_rows()), 1)

    def test_different_connections_are_isolated(self):

        other_connection = PurchaseChannelConnection(
            company_id=self.company.id, mall_code="ONCHANNEL",
            connection_method=ConnectionMethod.CREDENTIAL,
            account_label="다른 연결",
        )
        self.db.add(other_connection)
        self.db.commit()

        self.service.set_auto_pause_setting(
            self.company.id, window_days=30, max_incident_count=2,
            set_by=self.admin.id, is_admin=True,
        )
        for _ in range(2):
            self.service.record_incident(
                connection_id=self.connection.id, company_id=self.company.id,
                incident_type=SupplierIncidentType.STOCKOUT,
                recorded_by=self.admin.id,
            )

        paused = self.db.query(PurchaseChannelConnection).get(self.connection.id)
        untouched = self.db.query(PurchaseChannelConnection).get(other_connection.id)
        self.assertIsNotNone(paused.order_paused_at)
        self.assertIsNone(untouched.order_paused_at)

    # ---------------- 재활성화 ----------------

    def test_reactivate_requires_admin(self):

        self.service.set_auto_pause_setting(
            self.company.id, window_days=30, max_incident_count=1,
            set_by=self.admin.id, is_admin=True,
        )
        self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.STOCKOUT,
            recorded_by=self.admin.id,
        )

        with self.assertRaises(ForbiddenException):
            self.service.reactivate_order_function(
                self.connection.id, self.company.id, is_admin=False,
                reactivated_by=self.admin.id, confirmation_note="확인함",
            )

    def test_reactivate_requires_non_blank_note(self):

        self.service.set_auto_pause_setting(
            self.company.id, window_days=30, max_incident_count=1,
            set_by=self.admin.id, is_admin=True,
        )
        self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.STOCKOUT,
            recorded_by=self.admin.id,
        )

        with self.assertRaises(BadRequestException):
            self.service.reactivate_order_function(
                self.connection.id, self.company.id, is_admin=True,
                reactivated_by=self.admin.id, confirmation_note="   ",
            )

    def test_reactivate_rejects_when_not_paused(self):

        with self.assertRaises(BadRequestException):
            self.service.reactivate_order_function(
                self.connection.id, self.company.id, is_admin=True,
                reactivated_by=self.admin.id, confirmation_note="확인함",
            )

    def test_reactivate_clears_pause_and_logs_event(self):

        self.service.set_auto_pause_setting(
            self.company.id, window_days=30, max_incident_count=1,
            set_by=self.admin.id, is_admin=True,
        )
        self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.STOCKOUT,
            recorded_by=self.admin.id,
        )
        row = self.db.query(PurchaseChannelConnection).get(self.connection.id)
        self.assertIsNotNone(row.order_paused_at)

        reactivated = self.service.reactivate_order_function(
            self.connection.id, self.company.id, is_admin=True,
            reactivated_by=self.admin.id,
            confirmation_note="자격증명·상품·재고 재확인 완료",
        )
        self.assertIsNone(reactivated.order_paused_at)
        self.assertIsNone(reactivated.order_paused_reason)

        from app.domains.purchase_task.model import PurchaseChannelConnectionEvent

        events = (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(
                PurchaseChannelConnectionEvent.connection_id == self.connection.id,
                PurchaseChannelConnectionEvent.event_type
                == ChannelConnectionEventType.ORDER_FUNCTION_REACTIVATED,
            )
            .all()
        )
        self.assertEqual(len(events), 1)
        self.assertIn("재확인", events[0].detail)

    def test_does_not_auto_reactivate_after_new_successful_lookup(self):
        """자동으로 다시 활성화하지 않는다 — 일시중지 상태 자체는
        사람이 명시적으로 reactivate_order_function()을 호출하기
        전까지 다른 어떤 이벤트로도 풀리지 않아야 한다."""

        self.service.set_auto_pause_setting(
            self.company.id, window_days=30, max_incident_count=1,
            set_by=self.admin.id, is_admin=True,
        )
        self.service.record_incident(
            connection_id=self.connection.id, company_id=self.company.id,
            incident_type=SupplierIncidentType.STOCKOUT,
            recorded_by=self.admin.id,
        )
        row = self.db.query(PurchaseChannelConnection).get(self.connection.id)
        self.assertIsNotNone(row.order_paused_at)

        # 연결의 다른 필드(예: verified_at)가 갱신되는 상황을 흉내내도
        # order_paused_at는 그대로여야 한다.
        row.verified_at = None
        self.db.commit()

        still_paused = self.db.query(PurchaseChannelConnection).get(
            self.connection.id,
        )
        self.assertIsNotNone(still_paused.order_paused_at)


if __name__ == "__main__":
    unittest.main()
