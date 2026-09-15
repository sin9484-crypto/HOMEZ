"""
=========================================================
Homez OS

File : tests/test_notification_delivery_pt3.py

Gate PT-3(2026-08-23 17차 지시) — 중앙 알림 전달 엔진
(NotificationDeliveryService) 및 EStop 실제 연결 지점 검증.

실제 homez.db는 사용하지 않는다 — 임시 SQLite 파일에 전체 Model
스키마 + audit_logs(raw SQL 테이블, Model이 없어 별도 Migration으로
생성한다)를 적용한 뒤 그 안에서만 검증한다.
=========================================================
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from datetime import datetime
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.main  # noqa: F401 — Base.metadata에 전체 Model을 등록시키기 위한 import(FK 해석에 필요)
from app.database.base import Base
from app.domains.notification_center.delivery_service import DeliveryStatus
from app.domains.notification_center.delivery_service import (
    NotificationDeliveryService,
)
from app.domains.notification_center.email_provider import (
    FakeNotificationEmailProvider,
)
from app.domains.notification_center.email_provider import (
    NullNotificationEmailProvider,
)
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationEmailLog

_AUDIT_LOG_MIGRATIONS = [
    "migrations/20260810_00_create_audit_logs_schema.sql",
    "migrations/20260821_00_add_audit_logs_created_at.sql",
]


def _build_temp_db() -> tuple[str, "sessionmaker"]:

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    raw = sqlite3.connect(path)
    for fn in _AUDIT_LOG_MIGRATIONS:
        with open(fn, encoding="utf-8") as f:
            raw.executescript(f.read())
    raw.commit()
    raw.close()

    engine = create_engine(f"sqlite:///{path}")
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return path, session_local, engine


class _AlwaysFailProvider:

    is_configured = True

    def send(self, message):
        raise RuntimeError("forced failure for test")


class NotificationDeliveryServiceTestCase(unittest.TestCase):

    def setUp(self):

        self.db_path, self.SessionLocal, self.engine = _build_temp_db()
        self.db = self.SessionLocal()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass  # Windows 파일 잠금 — 테스트 결과에는 영향 없음

    # ---------------- 1. 이벤트별 인앱 알림 생성 ----------------

    def test_dispatch_creates_in_app_notification_for_every_event(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        svc.dispatch(
            "CANDIDATE_REVIEW_NEEDED", company_id=1, user_id=10,
            idempotency_key="k1", title="검토 필요", message="상세",
        )

        rows = self.db.query(Notification).filter(
            Notification.company_id == 1, Notification.user_id == 10,
        ).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "검토 필요")

    # ---------------- 2. 심각도별 전달정책 ----------------

    def test_critical_sends_email_immediately(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        result = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="c1",
            title="x", message="x", to_email="a@example.com",
        )
        self.assertEqual(result.status, DeliveryStatus.SENT)
        self.assertEqual(len(fake.sent_messages), 1)

    def test_high_with_email_immediate_flag_sends_now(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        result = svc.dispatch(
            "MARGIN_BELOW_MINIMUM", company_id=1, user_id=10, idempotency_key="h1",
            title="x", message="x", to_email="a@example.com",
        )
        self.assertEqual(result.status, DeliveryStatus.SENT)

    def test_medium_defers_to_pending_unconfirmed_not_sent_immediately(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        result = svc.dispatch(
            "CANDIDATE_REVIEW_NEEDED", company_id=1, user_id=10, idempotency_key="m1",
            title="x", message="x", to_email="a@example.com",
        )
        self.assertEqual(result.status, DeliveryStatus.PENDING_UNCONFIRMED)
        self.assertEqual(len(fake.sent_messages), 0)

    def test_low_never_sends_email_in_app_only(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        result = svc.dispatch(
            "DELIVERY_CONFIRMATION_FAILED", company_id=1, user_id=10,
            idempotency_key="l1", title="x", message="x", to_email="a@example.com",
        )
        self.assertEqual(result.status, DeliveryStatus.SKIPPED_IN_APP_ONLY)
        self.assertEqual(len(fake.sent_messages), 0)

    # ---------------- 3. 미확인시간 경과 후 이메일 전환 ----------------

    def test_medium_escalates_to_email_only_after_wait_elapsed(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        dispatched = svc.dispatch(
            "CANDIDATE_REVIEW_NEEDED", company_id=1, user_id=10,
            idempotency_key="esc1", title="x", message="x", to_email="a@example.com",
        )

        too_early = svc.escalate_unconfirmed_to_email(
            dispatched.log_id, to_email="a@example.com",
        )
        self.assertEqual(too_early.status, DeliveryStatus.PENDING_UNCONFIRMED)
        self.assertEqual(len(fake.sent_messages), 0)

        later = datetime.utcnow() + timedelta(hours=5)
        escalated = svc.escalate_unconfirmed_to_email(
            dispatched.log_id, to_email="a@example.com", now=later,
            is_still_needed=lambda: True,
        )
        self.assertEqual(escalated.status, DeliveryStatus.SENT)
        self.assertEqual(len(fake.sent_messages), 1)

    # ---------------- 4. 이미 처리된 업무는 발송 취소 ----------------

    def test_pre_send_reverification_cancels_already_resolved(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        dispatched = svc.dispatch(
            "CANDIDATE_REVIEW_NEEDED", company_id=1, user_id=10,
            idempotency_key="cancel1", title="x", message="x",
            to_email="a@example.com",
        )
        later = datetime.utcnow() + timedelta(hours=5)
        result = svc.escalate_unconfirmed_to_email(
            dispatched.log_id, to_email="a@example.com", now=later,
            is_still_needed=lambda: False,
        )
        self.assertEqual(result.status, DeliveryStatus.CANCELLED_ALREADY_RESOLVED)
        self.assertEqual(len(fake.sent_messages), 0)

    def test_critical_immediate_send_also_honors_pre_send_reverification(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        result = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="cancel2",
            title="x", message="x", to_email="a@example.com",
            is_still_needed=lambda: False,
        )
        self.assertEqual(result.status, DeliveryStatus.CANCELLED_ALREADY_RESOLVED)
        self.assertEqual(len(fake.sent_messages), 0)

    # ---------------- 5. 중복 발송 방지(멱등성) ----------------

    def test_duplicate_idempotency_key_skips_second_dispatch_entirely(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="dup1",
            title="x", message="x", to_email="a@example.com",
        )
        second = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="dup1",
            title="y", message="y", to_email="a@example.com",
        )
        self.assertEqual(second.status, DeliveryStatus.SKIPPED_DUPLICATE)
        self.assertEqual(len(fake.sent_messages), 1)

        in_app_count = self.db.query(Notification).filter(
            Notification.company_id == 1, Notification.user_id == 10,
        ).count()
        self.assertEqual(in_app_count, 1)

    def test_same_idempotency_key_different_company_is_not_deduplicated(self):
        """멱등키는 company_id와 함께 게이팅된다 — 다른 회사의 같은
        키는 서로 다른 이벤트로 취급돼야 한다(회사 격리)."""

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        r_a = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=100, user_id=1, idempotency_key="shared",
            title="x", message="x", to_email="a@example.com",
        )
        r_b = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=200, user_id=1, idempotency_key="shared",
            title="x", message="x", to_email="a@example.com",
        )
        self.assertEqual(r_a.status, DeliveryStatus.SENT)
        self.assertEqual(r_b.status, DeliveryStatus.SENT)
        self.assertEqual(len(fake.sent_messages), 2)

    # ---------------- 6. 이메일 실패해도 트랜잭션(=인앱 알림)은 성공 ----------------

    def test_email_failure_does_not_prevent_in_app_notification(self):

        svc = NotificationDeliveryService(self.db, provider=_AlwaysFailProvider())

        result = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="failtx",
            title="EStop 발동", message="x", to_email="a@example.com",
        )
        self.assertEqual(result.status, DeliveryStatus.FAILED)

        # dispatch()가 만드는 인앱 알림(원래 이벤트) + 이메일 발송
        # 실패 자체를 알리는 보조 인앱 알림(_notify_email_failure) —
        # 둘 다 이메일과 무관하게 이미 커밋된 상태여야 한다.
        rows = self.db.query(Notification).filter(
            Notification.company_id == 1, Notification.user_id == 10,
        ).all()
        self.assertEqual(len(rows), 2)
        titles = {row.title for row in rows}
        self.assertIn("EStop 발동", titles)
        self.assertIn("알림 이메일 발송 실패", titles)

    def test_dispatch_never_raises_even_when_provider_raises(self):
        """이 메서드 자체가 예외를 전파하지 않는다는 계약을 확인한다
        (호출자의 실제 업무 트랜잭션을 절대 실패시키지 않기 위해)."""

        svc = NotificationDeliveryService(self.db, provider=_AlwaysFailProvider())
        try:
            svc.dispatch(
                "ESTOP_ACTIVATED", company_id=1, user_id=10,
                idempotency_key="noraise1", title="x", message="x",
                to_email="a@example.com",
            )
        except Exception as exc:  # noqa: BLE001
            self.fail(f"dispatch()가 예외를 전파했다: {exc}")

    # ---------------- 7. 재시도 횟수 제한 ----------------

    def test_retry_exhausted_after_max_total_attempts(self):

        svc = NotificationDeliveryService(self.db, provider=_AlwaysFailProvider())

        first = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="retry1",
            title="x", message="x", to_email="a@example.com",
        )
        self.assertEqual(first.status, DeliveryStatus.FAILED)

        t = datetime.utcnow()
        second = svc.retry_failed_notification(
            first.log_id, to_email="a@example.com", now=t + timedelta(hours=1),
        )
        self.assertEqual(second.status, DeliveryStatus.FAILED)

        third = svc.retry_failed_notification(
            first.log_id, to_email="a@example.com", now=t + timedelta(hours=2),
        )
        self.assertEqual(third.status, DeliveryStatus.RETRY_EXHAUSTED)

        # 이미 소진된 뒤에는 다시 재시도를 시도해도 그대로 소진 상태.
        fourth = svc.retry_failed_notification(
            first.log_id, to_email="a@example.com", now=t + timedelta(hours=3),
        )
        self.assertEqual(fourth.status, DeliveryStatus.RETRY_EXHAUSTED)

    def test_retry_before_backoff_elapsed_is_rejected(self):

        svc = NotificationDeliveryService(self.db, provider=_AlwaysFailProvider())
        first = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="retry2",
            title="x", message="x", to_email="a@example.com",
        )
        too_soon = svc.retry_failed_notification(
            first.log_id, to_email="a@example.com", now=datetime.utcnow(),
        )
        self.assertEqual(too_soon.detail, "RETRY_AFTER_NOT_ELAPSED")
        self.assertEqual(too_soon.status, DeliveryStatus.FAILED)

    # ---------------- 8. Provider 미구성 정직 표시 ----------------

    def test_null_provider_never_claims_success(self):

        svc = NotificationDeliveryService(self.db, provider=NullNotificationEmailProvider())
        result = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="null1",
            title="x", message="x", to_email="a@example.com",
        )
        self.assertEqual(result.status, DeliveryStatus.PROVIDER_NOT_CONFIGURED)

        log = self.db.query(NotificationEmailLog).filter(
            NotificationEmailLog.id == result.log_id,
        ).first()
        self.assertEqual(log.status, DeliveryStatus.PROVIDER_NOT_CONFIGURED)
        self.assertIsNone(log.sent_at)

    def test_company_notification_audit_does_not_use_fake_user_zero(self):
        svc = NotificationDeliveryService(
            self.db, provider=NullNotificationEmailProvider(),
        )
        svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=None,
            idempotency_key="null-company-user", title="x", message="x",
        )
        row = self.db.execute(text(
            "SELECT user_id FROM audit_logs "
            "WHERE action = 'NOTIFICATION_EMAIL_SKIPPED' "
            "ORDER BY id DESC LIMIT 1",
        )).first()
        self.assertIsNotNone(row)
        self.assertIsNone(row[0])

    # ---------------- 9. Fake Provider 성공/실패/부분실패 ----------------

    def test_fake_provider_fail_next_then_recovers(self):

        fake = FakeNotificationEmailProvider(configured=True, fail_next=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        # fail_next=True는 send() 1회만 실패시키지만, dispatch() 내부
        # 재시도 루프(최대 3회)가 같은 호출 안에서 그 실패를 흡수하고
        # 성공할 수 있다 — 그래서 이 케이스는 SENT로 끝나는 것이 정상
        # 동작이다(무한 실패가 아니라 "다음 1회만 실패"이므로).
        result = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="fakefail1",
            title="x", message="x", to_email="a@example.com",
        )
        self.assertEqual(result.status, DeliveryStatus.SENT)
        self.assertEqual(len(fake.sent_messages), 1)

    # ---------------- 10. 민감정보 미노출 ----------------

    def test_email_log_never_stores_recipient_email(self):

        fake = FakeNotificationEmailProvider(configured=True)
        svc = NotificationDeliveryService(self.db, provider=fake)

        result = svc.dispatch(
            "ESTOP_ACTIVATED", company_id=1, user_id=10, idempotency_key="pii1",
            title="x", message="x", to_email="secret-admin@example.com",
        )
        log = self.db.query(NotificationEmailLog).filter(
            NotificationEmailLog.id == result.log_id,
        ).first()

        for column in log.__table__.columns:
            value = getattr(log, column.name)
            if isinstance(value, str):
                self.assertNotIn("secret-admin@example.com", value)

    # ---------------- 11. 알 수 없는 이벤트 코드는 거부 ----------------

    def test_unknown_event_code_raises_key_error(self):

        svc = NotificationDeliveryService(self.db, provider=FakeNotificationEmailProvider())
        with self.assertRaises(KeyError):
            svc.dispatch(
                "NOT_A_REAL_EVENT", company_id=1, user_id=10,
                idempotency_key="unknown1", title="x", message="x",
            )


class EmergencyStopNotificationWiringTestCase(unittest.TestCase):
    """app/web/router.py의 EStop 활성화/해제 엔드포인트가 실제로
    NotificationDeliveryService.dispatch()를 호출하는지 확인한다.
    다른 desktop-setup 라우터 테스트와 동일하게 라우터 함수를
    TestClient 없이 직접 호출한다(방어 계층은 admin_guard/
    require_desktop_token 자체 테스트에서 이미 검증됨)."""

    def setUp(self):

        self.db_path, self.SessionLocal, self.engine = _build_temp_db()
        self.db = self.SessionLocal()
        self.admin = types.SimpleNamespace(
            id=1, company_id=7, email="admin@example.com", role="ADMIN",
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass

    def test_activate_emergency_stop_creates_notification_and_email_log(self):

        from app.web import router as web_router

        data = web_router.EmergencyStopActivateRequest(reason="manual test trigger")
        result = web_router.activate_emergency_stop(
            data, current_user=self.admin, db=self.db, _desktop=None,
        )

        self.assertTrue(result["is_active"])

        notif = self.db.query(Notification).filter(
            Notification.company_id == 7, Notification.user_id == 1,
        ).first()
        self.assertIsNotNone(notif)

        log = self.db.query(NotificationEmailLog).filter(
            NotificationEmailLog.company_id == 7,
            NotificationEmailLog.event_code == "ESTOP_ACTIVATED",
        ).first()
        self.assertIsNotNone(log)

    def test_activate_emergency_stop_still_succeeds_if_notification_layer_breaks(self):
        """알림 실패가 EStop 발동 자체를 절대 막지 않는다는 계약을
        확인한다 — company_id가 None이라도(신규/미배정 사용자) EStop
        자체는 그대로 성공해야 한다."""

        from app.web import router as web_router

        broken_admin = types.SimpleNamespace(
            id=2, company_id=None, email="broken@example.com", role="ADMIN",
        )
        data = web_router.EmergencyStopActivateRequest(reason="edge case")
        result = web_router.activate_emergency_stop(
            data, current_user=broken_admin, db=self.db, _desktop=None,
        )
        self.assertTrue(result["is_active"])

    def test_deactivate_emergency_stop_creates_review_notification(self):

        from app.web import router as web_router

        activate_data = web_router.EmergencyStopActivateRequest(reason="setup")
        web_router.activate_emergency_stop(
            activate_data, current_user=self.admin, db=self.db, _desktop=None,
        )

        result = web_router.deactivate_emergency_stop(
            current_user=self.admin, db=self.db, _desktop=None,
        )
        self.assertFalse(result["is_active"])

        log = self.db.query(NotificationEmailLog).filter(
            NotificationEmailLog.company_id == 7,
            NotificationEmailLog.event_code == "ESTOP_DEACTIVATION_APPROVAL_NEEDED",
        ).first()
        self.assertIsNotNone(log)

    def test_unified_notifications_serializes_when_estop_active(self):
        """실사용 브라우저 검증 중 발견한 회귀: EStop이 활성화된 동안
        GET /notifications/unified이 500을 반환했다
        (UnifiedNotificationService가 만드는 합성 항목의 created_at이
        None이라 응답 스키마 검증에 실패함, app/domains/notification_
        center/unified_service.py:73-83, Gate PT-2F 당시부터 있던
        결함 — 이번 Gate의 EStop 실제 연결로 처음 실사용 경로를
        탔다). Pydantic 응답 스키마로 직접 검증해 재발을 막는다."""

        from app.web import router as web_router
        from app.domains.notification_center.unified_service import (
            UnifiedNotificationService,
        )
        from app.domains.notification_center.schema import (
            UnifiedNotificationListResponse,
        )

        data = web_router.EmergencyStopActivateRequest(reason="regression test")
        web_router.activate_emergency_stop(
            data, current_user=self.admin, db=self.db, _desktop=None,
        )

        result = UnifiedNotificationService(self.db).list_unified(
            self.admin.company_id, self.admin.id,
        )
        self.assertTrue(result["emergency_stop_active"])

        try:
            validated = UnifiedNotificationListResponse.model_validate(result)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"응답 스키마 검증 실패(회귀): {exc}")

        self.assertIsNotNone(validated.items[0].created_at)

    def test_deactivate_when_never_activated_does_not_crash_or_notify(self):

        from app.web import router as web_router

        result = web_router.deactivate_emergency_stop(
            current_user=self.admin, db=self.db, _desktop=None,
        )
        self.assertFalse(result["is_active"])

        log_count = self.db.query(NotificationEmailLog).filter(
            NotificationEmailLog.company_id == 7,
        ).count()
        self.assertEqual(log_count, 0)


class NotificationPreferenceRouterTestCase(unittest.TestCase):
    """설정 화면이 쓰는 새 엔드포인트(GET/PUT preference, catalog,
    event-preferences, test-email)를 라우터 함수 직접 호출로
    검증한다."""

    def setUp(self):

        self.db_path, self.SessionLocal, self.engine = _build_temp_db()
        self.db = self.SessionLocal()
        self.user_a = types.SimpleNamespace(id=1, company_id=7)
        self.user_b = types.SimpleNamespace(id=2, company_id=7)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass

    def test_get_preference_defaults_before_any_write(self):

        from app.domains.notification_center import router as notif_router

        result = notif_router.get_notification_preference(
            current_user=self.user_a, db=self.db,
        )
        self.assertTrue(result.email_enabled)
        self.assertIsNone(result.quiet_hours_start)

    def test_put_then_get_preference_roundtrips(self):

        from app.domains.notification_center import router as notif_router
        from app.domains.notification_center.schema import (
            NotificationPreferenceUpdate,
        )

        notif_router.update_notification_preference(
            NotificationPreferenceUpdate(
                email_enabled=False, quiet_hours_start=22, quiet_hours_end=6,
            ),
            current_user=self.user_a, db=self.db,
        )
        result = notif_router.get_notification_preference(
            current_user=self.user_a, db=self.db,
        )
        self.assertFalse(result.email_enabled)
        self.assertEqual(result.quiet_hours_start, 22)
        self.assertEqual(result.quiet_hours_end, 6)

    def test_preference_does_not_leak_across_users(self):

        from app.domains.notification_center import router as notif_router
        from app.domains.notification_center.schema import (
            NotificationPreferenceUpdate,
        )

        notif_router.update_notification_preference(
            NotificationPreferenceUpdate(email_enabled=False),
            current_user=self.user_a, db=self.db,
        )
        result_b = notif_router.get_notification_preference(
            current_user=self.user_b, db=self.db,
        )
        self.assertTrue(result_b.email_enabled)  # user_a의 변경이 안 보임

    def test_catalog_listing_returns_all_29_events_with_honest_wired_flag(self):

        from app.domains.notification_center import router as notif_router
        from app.domains.notification_center.event_catalog import (
            EVENT_CATALOG,
        )

        result = notif_router.list_notification_catalog(current_user=self.user_a)
        self.assertEqual(len(result), len(EVENT_CATALOG))

        by_code = {r.event_code: r for r in result}
        expected_wired = {
            "CANDIDATE_REVIEW_NEEDED",
            "LISTING_FINAL_APPROVAL_NEEDED",
            "LISTING_SUBMISSION_FAILED",
            "PRICE_CHANGE_APPROVAL_NEEDED",
            "RECONCILIATION_REVIEW_NEEDED",
            "RETURN_EXCHANGE_APPROVAL_NEEDED",
            "CHANNEL_CREDENTIAL_EXPIRED",
            "ESTOP_ACTIVATED",
            "ESTOP_DEACTIVATION_APPROVAL_NEEDED",
            "NOTIFICATION_EMAIL_DELIVERY_FAILED",
            "CHANNEL_POLICY_VIOLATION",
            "MARGIN_BELOW_MINIMUM",
            # 2026-09-10 최종 회귀에서 발견 — HOMEZ V7 개인 베타 Phase
            # 1/4/5(로그인 잠금 알림, 자동화 오류강등 알림, 백업
            # 리허설 실패 알림)가 event_catalog.py에 wired=True로
            # 추가했는데 이 하드코딩 목록을 갱신하지 않아 실패했다.
            # 이 파일에서 이미 여러 번 겪은 "새 항목 추가 시 이 집합도
            # 매번 갱신해야 한다"는 유지보수 규칙과 동일한 패턴이다.
            "LOGIN_ACCOUNT_LOCKED",
            "FUNCTION_AUTOMATION_DEMOTED_TO_ERROR",
            "BACKUP_RESTORE_REHEARSAL_FAILED",
            # 2026-09-15 전면 감사 후속(Phase 9, HOMEZ_USER_OPERATION_
            # SETTINGS.md 8-16) — 매입처 조회 반복 실패 알림.
            "SUPPLIER_LOOKUP_REPEATED_FAILURE",
        }
        actual_wired = {code for code, row in by_code.items() if row.wired}
        self.assertEqual(actual_wired, expected_wired)

    def test_every_wired_event_has_a_real_non_catalog_call_site(self):

        from app.domains.notification_center.event_catalog import EVENT_CATALOG

        source_files = [
            Path("app/web/router.py"),
            Path("app/domains/notification_center/delivery_service.py"),
            Path("app/domains/product_candidate/service.py"),
            Path("app/domains/marketplace_listing/listing_wizard_service.py"),
            Path("app/domains/pricing/service.py"),
            Path("app/domains/return_order/service.py"),
            Path("app/domains/store_connection/service.py"),
            Path("app/domains/retail_purchase/service.py"),
            Path("app/domains/marketplace_listing/submission_service.py"),
            # 2026-09-10 최종 회귀에서 발견 — 위 expected_wired 수정과
            # 같은 원인. Phase 1(LOGIN_ACCOUNT_LOCKED)·Phase 4
            # (FUNCTION_AUTOMATION_DEMOTED_TO_ERROR)·Phase 5
            # (BACKUP_RESTORE_REHEARSAL_FAILED)가 실제로 발생시키는
            # 코드 위치를 이 목록에 추가한다.
            Path("app/domains/auth/service.py"),
            Path("app/domains/purchase_task/service.py"),
            Path("app/domains/restore/service.py"),
            # 2026-09-15 전면 감사 후속(Phase 9) —
            # SUPPLIER_LOOKUP_REPEATED_FAILURE가 실제로 발생하는 위치.
            Path("app/domains/purchase_task/channel_connection_service.py"),
        ]
        source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
        for event_code, definition in EVENT_CATALOG.items():
            if definition.wired:
                self.assertIn(f'"{event_code}"', source, event_code)

    def test_event_preference_default_reflects_catalog_when_no_override(self):

        from app.domains.notification_center import router as notif_router

        results = notif_router.list_notification_event_preferences(
            current_user=self.user_a, db=self.db,
        )
        by_code = {r.event_code: r for r in results}
        self.assertTrue(by_code["ESTOP_ACTIVATED"].enabled)
        self.assertIsNone(by_code["ESTOP_ACTIVATED"].email_immediate_override)

    def test_event_preference_override_roundtrips(self):

        from app.domains.notification_center import router as notif_router
        from app.domains.notification_center.schema import (
            NotificationEventPreferenceUpdate,
        )

        notif_router.update_notification_event_preference(
            "CANDIDATE_REVIEW_NEEDED",
            NotificationEventPreferenceUpdate(
                email_immediate_override=True,
                unconfirmed_wait_minutes_override=15,
            ),
            current_user=self.user_a, db=self.db,
        )
        results = notif_router.list_notification_event_preferences(
            current_user=self.user_a, db=self.db,
        )
        by_code = {r.event_code: r for r in results}
        self.assertTrue(by_code["CANDIDATE_REVIEW_NEEDED"].email_immediate_override)
        self.assertEqual(
            by_code["CANDIDATE_REVIEW_NEEDED"].unconfirmed_wait_minutes_override, 15,
        )

    def test_critical_cannot_disable_event_rejects_disable_attempt(self):

        from app.core.exceptions import BadRequestException
        from app.domains.notification_center import router as notif_router
        from app.domains.notification_center.schema import (
            NotificationEventPreferenceUpdate,
        )

        with self.assertRaises(BadRequestException):
            notif_router.update_notification_event_preference(
                "ESTOP_ACTIVATED",
                NotificationEventPreferenceUpdate(enabled=False),
                current_user=self.user_a, db=self.db,
            )

    def test_non_critical_event_can_be_disabled(self):

        from app.domains.notification_center import router as notif_router
        from app.domains.notification_center.schema import (
            NotificationEventPreferenceUpdate,
        )

        result = notif_router.update_notification_event_preference(
            "CANDIDATE_REVIEW_NEEDED",
            NotificationEventPreferenceUpdate(enabled=False),
            current_user=self.user_a, db=self.db,
        )
        self.assertFalse(result.enabled)

    def test_unknown_event_code_returns_not_found(self):

        from app.core.exceptions import NotFoundException
        from app.domains.notification_center import router as notif_router
        from app.domains.notification_center.schema import (
            NotificationEventPreferenceUpdate,
        )

        with self.assertRaises(NotFoundException):
            notif_router.update_notification_event_preference(
                "NOT_A_REAL_EVENT",
                NotificationEventPreferenceUpdate(enabled=False),
                current_user=self.user_a, db=self.db,
            )

    def test_test_email_endpoint_is_honest_about_null_provider(self):
        """실제 Provider가 없으므로 테스트 발송은 항상
        PROVIDER_NOT_CONFIGURED다 — 성공한 것처럼 보이면 안 된다."""

        from app.domains.notification_center import router as notif_router
        from app.domains.notification_center.delivery_service import (
            DeliveryStatus,
        )
        from app.domains.notification_center.schema import (
            TestNotificationEmailRequest,
        )

        result = notif_router.send_test_notification_email(
            TestNotificationEmailRequest(to_email="a@example.com"),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(result.status, DeliveryStatus.PROVIDER_NOT_CONFIGURED)


if __name__ == "__main__":
    unittest.main()
