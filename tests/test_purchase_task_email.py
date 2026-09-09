"""
=========================================================
Homez OS

File : tests/test_purchase_task_email.py

Gate PT-1(2026-08-22) 검증 — PurchaseTaskEmailService. 실 Provider
Credential 없음, FakePurchaseTaskEmailProvider만 사용한다.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationRead
from app.domains.purchase_task.constants import EmailNotificationEventType
from app.domains.purchase_task.constants import EmailSendStatus
from app.domains.purchase_task.email_provider import FakePurchaseTaskEmailProvider
from app.domains.purchase_task.email_provider import NullPurchaseTaskEmailProvider
from app.domains.purchase_task.email_service import PurchaseTaskEmailService
from app.domains.purchase_task.model import PurchaseTaskEmailLog
from app.domains.purchase_task.model import PurchaseTaskEmailPreference

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class PurchaseTaskEmailServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                PurchaseTaskEmailLog.__table__,
                PurchaseTaskEmailPreference.__table__,
                Notification.__table__, NotificationRead.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.execute(text(AUDIT_LOGS_DDL))

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_provider_not_configured_is_reported_honestly(self):

        service = PurchaseTaskEmailService(
            self.db, provider=NullPurchaseTaskEmailProvider(),
        )
        status = service.send_notification(
            company_id=1, user_id=1, to_email="a@example.com",
            event_type=EmailNotificationEventType.TASK_CREATED,
            purchase_task_id=1, product_title="상품", detail="테스트",
            idempotency_key="k1",
        )
        self.assertEqual(status, EmailSendStatus.PROVIDER_NOT_CONFIGURED)

    def test_fake_provider_sends_and_logs(self):

        provider = FakePurchaseTaskEmailProvider()
        service = PurchaseTaskEmailService(self.db, provider=provider)

        status = service.send_notification(
            company_id=1, user_id=1, to_email="a@example.com",
            event_type=EmailNotificationEventType.TASK_CREATED,
            purchase_task_id=1, product_title="상품", detail="테스트",
            idempotency_key="k2",
        )
        self.assertEqual(status, EmailSendStatus.SENT)
        self.assertEqual(len(provider.sent_messages), 1)

    def test_duplicate_idempotency_key_does_not_resend(self):

        provider = FakePurchaseTaskEmailProvider()
        service = PurchaseTaskEmailService(self.db, provider=provider)

        service.send_notification(
            company_id=1, user_id=1, to_email="a@example.com",
            event_type=EmailNotificationEventType.TASK_CREATED,
            purchase_task_id=1, product_title="상품", detail="테스트",
            idempotency_key="dup",
        )
        service.send_notification(
            company_id=1, user_id=1, to_email="a@example.com",
            event_type=EmailNotificationEventType.TASK_CREATED,
            purchase_task_id=1, product_title="상품", detail="테스트",
            idempotency_key="dup",
        )
        self.assertEqual(len(provider.sent_messages), 1)

    def test_opted_out_user_is_skipped(self):

        provider = FakePurchaseTaskEmailProvider()
        service = PurchaseTaskEmailService(self.db, provider=provider)
        service.set_preference(1, 1, enabled=False)

        status = service.send_notification(
            company_id=1, user_id=1, to_email="a@example.com",
            event_type=EmailNotificationEventType.TASK_CREATED,
            purchase_task_id=1, product_title="상품", detail="테스트",
            idempotency_key="opt-out",
        )
        self.assertEqual(status, EmailSendStatus.SKIPPED_OPTED_OUT)
        self.assertEqual(len(provider.sent_messages), 0)

    def test_per_event_toggle_off_is_skipped(self):

        provider = FakePurchaseTaskEmailProvider()
        service = PurchaseTaskEmailService(self.db, provider=provider)
        service.set_preference(
            1, 1, enabled=True,
            event_toggles={EmailNotificationEventType.PRICE_CHANGED: False},
        )

        status = service.send_notification(
            company_id=1, user_id=1, to_email="a@example.com",
            event_type=EmailNotificationEventType.PRICE_CHANGED,
            purchase_task_id=1, product_title="상품", detail="테스트",
            idempotency_key="toggle-off",
        )
        self.assertEqual(status, EmailSendStatus.SKIPPED_OPTED_OUT)

        # 끄지 않은 다른 이벤트는 그대로 발송된다.
        status2 = service.send_notification(
            company_id=1, user_id=1, to_email="a@example.com",
            event_type=EmailNotificationEventType.TASK_CREATED,
            purchase_task_id=1, product_title="상품", detail="테스트",
            idempotency_key="toggle-other",
        )
        self.assertEqual(status2, EmailSendStatus.SENT)

    def test_provider_failure_is_logged_as_failed_not_success(self):

        provider = FakePurchaseTaskEmailProvider(fail_next=True)
        service = PurchaseTaskEmailService(self.db, provider=provider)

        # fail_next는 1회만 실패하도록 설계돼 있어 재시도가 곧바로
        # 성공할 수 있다 — 매 시도마다 실패하도록 provider를 감싼다.
        class _AlwaysFailProvider(FakePurchaseTaskEmailProvider):
            def send(self, message):
                raise RuntimeError("항상 실패(테스트)")

        service2 = PurchaseTaskEmailService(self.db, provider=_AlwaysFailProvider())
        status = service2.send_notification(
            company_id=1, user_id=1, to_email="a@example.com",
            event_type=EmailNotificationEventType.TASK_CREATED,
            purchase_task_id=1, product_title="상품", detail="테스트",
            idempotency_key="always-fail",
        )
        self.assertEqual(status, EmailSendStatus.FAILED)

        log = self.db.query(PurchaseTaskEmailLog).filter(
            PurchaseTaskEmailLog.idempotency_key == "always-fail",
        ).first()
        self.assertEqual(log.attempt_count, 3)  # MAX_SEND_ATTEMPTS

        # EMAIL_DELIVERY_FAILED — 이메일로 다시 알리지 않고 Desktop
        # 알림(notification_center)에만 남긴다.
        notif = self.db.query(Notification).filter(
            Notification.company_id == 1, Notification.user_id == 1,
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("발송 실패", notif.title)


if __name__ == "__main__":
    unittest.main()
