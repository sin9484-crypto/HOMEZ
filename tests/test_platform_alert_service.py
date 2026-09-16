"""
=========================================================
Homez OS

File : tests/test_platform_alert_service.py

2026-09-16 개인 베타 잔여 작업(Phase 5, HOMEZ_USER_OPERATION_SETTINGS.md
10-18) — 서버 관리자 알림(`platform_alert`) 격리 테스트. 임시
SQLite + FakePlatformAlertProvider만 사용한다 — 실제 이메일·SMS
발송은 전혀 없다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.bootstrap import bootstrap_environment
from app.domains.platform_alert.constants import PlatformAlertEventCode
from app.domains.platform_alert.model import PLATFORM_ALERT_CONTACT_TYPE_EMAIL
from app.domains.platform_alert.model import PLATFORM_ALERT_CONTACT_TYPE_SMS
from app.domains.platform_alert.provider import FakePlatformAlertProvider
from app.domains.platform_alert.repository import PlatformAlertRepository
from app.domains.platform_alert.service import MAX_TOTAL_ATTEMPTS
from app.domains.platform_alert.service import PlatformAlertDeliveryStatus
from app.domains.platform_alert.service import PlatformAlertService

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class PlatformAlertServiceTestCase(unittest.TestCase):

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

        self.repository = PlatformAlertRepository(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def _add_recipient(self, *, contact_type=PLATFORM_ALERT_CONTACT_TYPE_EMAIL, value="ops@example.com"):

        recipient = self.repository.add_recipient(
            contact_type=contact_type, contact_value=value,
        )
        self.db.commit()
        return recipient

    def _dispatch(self, service, event_code=PlatformAlertEventCode.MIGRATION_RESTRICTED_MODE_ENTERED, key="k1"):

        return service.dispatch_alert(
            event_code, title="제목", message="본문",
            entity_ref="entity:1", idempotency_key=key,
        )

    # ------------------------------ 수신자 없음 ------------------------------

    def test_no_recipients_records_no_recipient_configured_and_does_not_raise(self):

        service = PlatformAlertService(self.db, provider=FakePlatformAlertProvider())
        results = self._dispatch(service)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, PlatformAlertDeliveryStatus.NO_RECIPIENT_CONFIGURED)

    def test_no_recipients_case_is_idempotent(self):

        service = PlatformAlertService(self.db, provider=FakePlatformAlertProvider())
        self._dispatch(service, key="dup-key")
        results = self._dispatch(service, key="dup-key")

        self.assertEqual(results[0].status, PlatformAlertDeliveryStatus.SKIPPED_DUPLICATE)

    # ------------------------------ 정상 발송 ------------------------------

    def test_dispatch_creates_one_log_per_active_recipient_and_sends(self):

        self._add_recipient(value="a@example.com")
        self._add_recipient(value="b@example.com")
        provider = FakePlatformAlertProvider()
        service = PlatformAlertService(self.db, provider=provider)

        results = self._dispatch(service)

        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.status == PlatformAlertDeliveryStatus.SENT for r in results))
        self.assertEqual(len(provider.sent_messages), 2)
        self.assertEqual(
            {m.to for m in provider.sent_messages}, {"a@example.com", "b@example.com"},
        )

    def test_dispatch_skips_inactive_recipients(self):

        active = self._add_recipient(value="active@example.com")
        inactive = self._add_recipient(value="inactive@example.com")
        self.repository.set_recipient_active(inactive.id, active=False)
        self.db.commit()

        provider = FakePlatformAlertProvider()
        service = PlatformAlertService(self.db, provider=provider)
        results = self._dispatch(service)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].recipient_id, active.id)
        self.assertEqual(provider.sent_messages[0].to, "active@example.com")

    def test_dispatch_uses_recipient_channel_email_or_sms(self):

        self._add_recipient(
            contact_type=PLATFORM_ALERT_CONTACT_TYPE_SMS, value="010-0000-0000",
        )
        provider = FakePlatformAlertProvider()
        service = PlatformAlertService(self.db, provider=provider)
        self._dispatch(service)

        self.assertEqual(provider.sent_messages[0].channel, PLATFORM_ALERT_CONTACT_TYPE_SMS)

    # ------------------------------ 멱등성 ------------------------------

    def test_dispatch_is_idempotent_per_recipient(self):

        self._add_recipient()
        provider = FakePlatformAlertProvider()
        service = PlatformAlertService(self.db, provider=provider)

        self._dispatch(service, key="same-key")
        self._dispatch(service, key="same-key")

        self.assertEqual(len(provider.sent_messages), 1)

    def test_different_idempotency_keys_both_send(self):

        self._add_recipient()
        provider = FakePlatformAlertProvider()
        service = PlatformAlertService(self.db, provider=provider)

        self._dispatch(service, key="key-1")
        self._dispatch(service, key="key-2")

        self.assertEqual(len(provider.sent_messages), 2)

    # ------------------------------ Provider 미구성 ------------------------------

    def test_unconfigured_provider_records_no_provider_configured(self):

        self._add_recipient()
        provider = FakePlatformAlertProvider(configured=False)
        service = PlatformAlertService(self.db, provider=provider)

        results = self._dispatch(service)

        self.assertEqual(results[0].status, PlatformAlertDeliveryStatus.NO_PROVIDER_CONFIGURED)
        self.assertEqual(len(provider.sent_messages), 0)

    # ------------------------------ 실패·재시도 ------------------------------

    def test_send_failure_is_recorded_and_scheduled_for_retry(self):

        self._add_recipient()
        provider = FakePlatformAlertProvider(fail_next=True)
        service = PlatformAlertService(self.db, provider=provider)

        results = self._dispatch(service)

        self.assertEqual(results[0].status, PlatformAlertDeliveryStatus.FAILED)
        log = self.repository.get_by_id(results[0].log_id)
        self.assertIsNotNone(log.next_retry_at)
        self.assertEqual(log.attempt_count, 1)

    def test_retry_before_due_time_does_nothing(self):

        self._add_recipient()
        provider = FakePlatformAlertProvider(fail_next=True)
        service = PlatformAlertService(self.db, provider=provider)
        self._dispatch(service)

        results = service.retry_failed_alerts(now=datetime.utcnow())
        self.assertEqual(len(results), 0)

    def test_retry_after_due_time_resends_and_succeeds(self):

        self._add_recipient()
        provider = FakePlatformAlertProvider(fail_next=True)
        service = PlatformAlertService(self.db, provider=provider)
        self._dispatch(service)

        future = datetime.utcnow() + timedelta(minutes=31)
        results = service.retry_failed_alerts(now=future)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, PlatformAlertDeliveryStatus.SENT)
        self.assertEqual(len(provider.sent_messages), 1)

    def test_repeated_failures_eventually_exhaust_retries(self):

        recipient = self._add_recipient()

        class AlwaysFailProvider(FakePlatformAlertProvider):
            def send(self, message):
                raise RuntimeError("항상 실패(테스트)")

        provider = AlwaysFailProvider()
        service = PlatformAlertService(self.db, provider=provider)
        first = self._dispatch(service)
        log_id = first[0].log_id

        now = datetime.utcnow()
        statuses = []
        for _ in range(MAX_TOTAL_ATTEMPTS + 2):
            now += timedelta(minutes=31)
            results = service.retry_failed_alerts(now=now)
            if not results:
                break
            statuses.append(results[0].status)

        self.assertIn(PlatformAlertDeliveryStatus.RETRY_EXHAUSTED, statuses)
        log = self.repository.get_by_id(log_id)
        self.assertEqual(log.status, PlatformAlertDeliveryStatus.RETRY_EXHAUSTED)
        self.assertIsNone(log.next_retry_at)
        self.assertLessEqual(log.attempt_count, MAX_TOTAL_ATTEMPTS)
        self.assertEqual(len(provider.sent_messages), 0)
        self.assertEqual(recipient.contact_value, "ops@example.com")

    def test_deactivated_recipient_stops_retry_instead_of_looping_forever(self):

        recipient = self._add_recipient()
        provider = FakePlatformAlertProvider(fail_next=True)
        service = PlatformAlertService(self.db, provider=provider)
        self._dispatch(service)

        self.repository.set_recipient_active(recipient.id, active=False)
        self.db.commit()

        future = datetime.utcnow() + timedelta(minutes=31)
        results = service.retry_failed_alerts(now=future)

        self.assertEqual(results[0].status, PlatformAlertDeliveryStatus.RETRY_EXHAUSTED)
        self.assertEqual(len(provider.sent_messages), 0)

    # ------------------------------ 실패해도 업무는 유지 ------------------------------

    def test_dispatch_never_raises_even_for_unknown_event_code(self):

        self._add_recipient()
        service = PlatformAlertService(self.db, provider=FakePlatformAlertProvider())

        results = service.dispatch_alert(
            "NOT_A_REAL_EVENT_CODE", title="t", message="m",
            entity_ref=None, idempotency_key="k-unknown",
        )
        self.assertEqual(results, [])

    def test_delivery_log_is_not_a_substitute_for_audit_log(self):
        """알림 로그(platform_alert_delivery_logs)와 감사로그
        (audit_logs)는 별개 테이블이다 — 이 테스트는 그 분리가
        실제로 유지되는지(알림 로그 테이블에 감사로그 전용 컬럼이
        섞여 들어가지 않는지) 스키마 수준에서 확인한다."""

        from sqlalchemy import inspect as sa_inspect

        columns = {c["name"] for c in sa_inspect(self.engine).get_columns("platform_alert_delivery_logs")}
        self.assertNotIn("company_id", columns)
        self.assertIn("status", columns)
        self.assertIn("attempt_count", columns)


if __name__ == "__main__":
    unittest.main()
