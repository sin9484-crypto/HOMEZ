"""
2026-11-01 — 사용자 결정 D5: 아침 브리핑을 이메일로도 받기(가짜 메일 서버, 바깥 호출 0).

고정하는 약속
  - 암호화(STARTTLS/SSL) 없이는 보내지 않고, 한 통마다 승인 센터 허용 1회. 허용이 없거나 비상 정지면 접속하지 않는다.
  - 한 통에 접속 한 번: 실패해도 같은 메시지로 다시 로그인하지 않는다(메일 계정 잠김 방지). 오류 기록에 서버 응답 원문·계정·비밀번호가 없다.
  - 비밀번호는 자격증명 저장소에만. DB·응답·감사 기록에 없다.
  - 켠 사람에게만, 아침 브리핑만 나간다. 메일이 실패해도 앱 안 알림은 남고 같은 날 다시 보내지 않는다. 다른 알림의 이메일 경로는 그대로 Null이다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import smtplib
import subprocess
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.main  # noqa: F401 — 모든 모델을 메타데이터에 올린다
from app.core.exceptions import BadRequestException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.service import SafetyService
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.notification_center import router as notif_router
from app.domains.notification_center.brief_email_service import BriefEmailService
from app.domains.notification_center.brief_email_service import credential_target
from app.domains.notification_center.email_provider import NotificationEmailMessage
from app.domains.notification_center.email_provider import NullNotificationEmailProvider
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationEmailLog
from app.domains.notification_center.smtp_provider import SmtpConfig
from app.domains.notification_center.smtp_provider import SmtpNotificationEmailProvider
from app.domains.orchestration.morning_brief_service import MorningBriefService
from app.domains.user.model import User
from tests.test_easy_briefing_ui import HARNESS
from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import NODE
from tests.test_easy_briefing_ui import NOW
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

A = 1
MORNING = datetime(2026, 10, 31, 22, 0)  # 한국 시각 11-01 07:00
SECRET = "my mail pass 123"

_TEMPLATE = {}


def _template_db() -> str:
    if "path" not in _TEMPLATE:
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "template.db")
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50), created_at DATETIME)"))
        engine.dispose()
        _TEMPLATE["path"], _TEMPLATE["folder"] = path, folder
    return _TEMPLATE["path"]


def tearDownModule():
    if "folder" in _TEMPLATE:
        shutil.rmtree(_TEMPLATE["folder"], ignore_errors=True)
    _TEMPLATE.clear()


class FakeSmtp:
    """smtplib.SMTP / SMTP_SSL 흉내. 호출 순서를 기록한다."""

    instances: list = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.steps, self.sent = [], []
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.steps.append("quit")

    def starttls(self, context=None):
        self.steps.append("starttls")
        self.tls_context = context

    def login(self, user, password):
        self.steps.append("login")
        self.credentials = (user, password)
        if FakeSmtp.fail is not None:
            raise FakeSmtp.fail

    def send_message(self, mail):
        self.steps.append("send")
        self.sent.append(mail)


class DbCase(unittest.TestCase):

    def setUp(self):
        FakeSmtp.instances, FakeSmtp.fail = [], None
        self.tmp = tempfile.mkdtemp()
        shutil.copyfile(_template_db(), f"{self.tmp}/t.db")
        self.engine = create_engine(f"sqlite:///{self.tmp}/t.db", connect_args={"check_same_thread": False})
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.store = InMemoryCredentialStore()
        self.mail = BriefEmailService(self.db, self.store, smtp_factory=FakeSmtp, ssl_factory=FakeSmtp)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def user(self, name="u1", company_id=A):
        u = User(company_id=company_id, email=f"{name}@example.com", username=name, password_hash="x", is_active=True)
        self.db.add(u)
        self.db.commit()
        return u.id

    def allow(self, count=5):
        return ExternalCallPolicyService(self.db).grant(
            A, system=ExternalSystem.EMAIL, kind=CallKind.WRITE, purpose=CallPurpose.BRIEF_EMAIL,
            mode=AllowanceMode.COUNTED, count=count, user_id=1)

    def save(self, user_id, **over):
        values = {"host": "smtp.example.com", "port": 587, "security": "STARTTLS", "login": "", "sender": "from@example.com",
                  "to": "me@example.com", "enabled": True, "password": SECRET}
        values.update(over)
        return self.mail.save(user_id, A, **values)

    def brief(self, *users, now=MORNING):
        svc = MorningBriefService(self.db, now_factory=lambda: now, email_service=self.mail)
        svc._system_counts = lambda: {"system": 0}
        svc.build = lambda company_id: {"title": "오늘 볼 일 3건", "message": "승인 센터 대기 1건 · 쿠팡 승인반려 2건", "unknown": [], "counts": {}}
        return svc.run(A)


class ProviderTests(DbCase):

    def provider(self, security="STARTTLS", password=SECRET, login=""):
        from app.domains.notification_center.brief_email_service import email_gate

        return SmtpNotificationEmailProvider(SmtpConfig("smtp.example.com", 587, security, login, "from@example.com"), password, email_gate(self.db, A, 7),
                                             smtp_factory=FakeSmtp, ssl_factory=FakeSmtp)

    def message(self):
        return NotificationEmailMessage(to_email="me@example.com", subject="제목", body_text="본문", event_code="DAILY_BRIEFING", locale="ko-KR")

    def test_starttls_comes_before_login_and_the_certificate_is_checked(self):
        allowance = self.allow()
        self.provider(login="id").send(self.message())
        client = FakeSmtp.instances[0]
        self.assertEqual(client.steps, ["starttls", "login", "send", "quit"])
        self.assertEqual(client.credentials, ("id", SECRET))
        self.assertTrue(client.tls_context.check_hostname)
        self.assertEqual(client.timeout, 20)
        mail = client.sent[0]
        self.assertEqual((mail["To"], mail["From"], mail["Subject"]), ("me@example.com", "from@example.com", "제목"))
        self.db.expire_all()
        self.assertEqual(self.db.get(ExternalCallAllowance, allowance.id).remaining_count, 4)
        log = self.db.query(ExternalCallLog).filter_by(outcome="SUCCEEDED").one()
        self.assertEqual((log.system, log.kind, log.purpose, log.target), ("EMAIL", "WRITE", "BRIEF_EMAIL", "m***@example.com"))

    def test_ssl_connects_with_a_verifying_context_and_login_falls_back_to_the_sender(self):
        self.allow()
        self.provider(security="SSL").send(self.message())
        client = FakeSmtp.instances[0]
        self.assertEqual(client.steps, ["login", "send", "quit"])
        self.assertTrue(client.context.check_hostname)
        self.assertEqual(client.credentials[0], "from@example.com")

    def test_an_unknown_security_never_connects_in_plain_text(self):
        self.allow()
        with self.assertRaises(RuntimeError):
            self.provider(security="NONE").send(self.message())
        self.assertEqual(FakeSmtp.instances, [])

    def test_without_an_allowance_it_does_not_connect_and_raises_a_request(self):
        with self.assertRaises(RuntimeError) as caught:
            self.provider().send(self.message())
        self.assertEqual(str(caught.exception), "EXTERNAL_ALLOWANCE_REQUIRED")
        self.assertEqual(FakeSmtp.instances, [])
        pending = self.db.query(ExternalCallAllowance).filter_by(status="PENDING").one()
        self.assertEqual((pending.system, pending.purpose, pending.kind, pending.mode, pending.requested_count), ("EMAIL", "BRIEF_EMAIL", "WRITE", "COUNTED", 30))

    def test_emergency_stop_blocks_even_with_an_allowance(self):
        self.allow()
        SafetyService(self.db).activate_emergency_stop("시험", set_by=1, is_admin=True)
        with self.assertRaises(RuntimeError) as caught:
            self.provider().send(self.message())
        self.assertEqual(str(caught.exception), "EXTERNAL_CALL_BLOCKED_ESTOP")
        self.assertEqual(FakeSmtp.instances, [])

    def test_one_message_gets_one_connection_and_one_allowance_even_when_retried(self):
        allowance = self.allow()
        FakeSmtp.fail = smtplib.SMTPAuthenticationError(535, b"bad user someone@example.com")
        provider, message = self.provider(), self.message()
        for _ in range(3):
            with self.assertRaises(RuntimeError) as caught:
                provider.send(message)
            self.assertEqual(str(caught.exception), "SMTP_AUTH_FAILED:535")
        self.assertEqual(len(FakeSmtp.instances), 1)
        self.db.expire_all()
        self.assertEqual(self.db.get(ExternalCallAllowance, allowance.id).remaining_count, 4)
        self.assertEqual(self.db.query(ExternalCallLog).filter_by(outcome="FAILED").count(), 1)

    def test_a_denial_is_not_asked_again_for_the_same_message(self):
        provider, message = self.provider(), self.message()
        with mock.patch.object(ExternalCallPolicyService, "authorize", wraps=ExternalCallPolicyService(self.db).authorize) as authorize:
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    provider.send(message)
        self.assertEqual(authorize.call_count, 1)

    def test_errors_carry_only_fixed_codes(self):
        self.allow(10)
        cases = [
            (smtplib.SMTPAuthenticationError(535, b"secret reply"), "SMTP_AUTH_FAILED:535"),
            (smtplib.SMTPDataError(554, b"secret reply"), "SMTP_REJECTED:554"),
            (OSError("secret host detail"), "SMTP_ERROR:OSError"),
        ]
        for error, code in cases:
            FakeSmtp.fail = error
            with self.assertRaises(RuntimeError) as caught:
                self.provider().send(self.message())
            self.assertEqual(str(caught.exception), code)

    def test_it_is_not_configured_without_a_password(self):
        self.assertFalse(self.provider(password=None).is_configured)
        self.assertTrue(self.provider().is_configured)


class SettingsTests(DbCase):

    def test_settings_are_saved_and_the_password_only_in_the_store(self):
        uid = self.user()
        out = self.save(uid)
        self.assertTrue(out["enabled"] and out["complete"] and out["password_saved"])
        self.assertEqual((out["host"], out["port"], out["security"], out["sender"], out["to"]), ("smtp.example.com", 587, "STARTTLS", "from@example.com", "me@example.com"))
        self.assertEqual(self.store.read(credential_target(uid)), {"password": SECRET})
        self.assertNotIn(SECRET, json.dumps(out))
        dump = json.dumps([tuple(r) for r in self.db.execute(text("SELECT setting_value FROM user_settings")).fetchall()], ensure_ascii=False)
        self.assertIn("smtp.example.com", dump)
        self.assertNotIn(SECRET, dump)

    def test_turning_on_and_off_sets_the_event_email_switch(self):
        from app.domains.notification_center.repository import NotificationDeliveryRepository

        uid = self.user()
        repo = NotificationDeliveryRepository(self.db)
        self.save(uid)
        self.assertIs(repo.get_event_preference(A, uid, "DAILY_BRIEFING").email_immediate_override, True)
        self.assertFalse(self.save(uid, enabled=False, password=None)["enabled"])
        self.assertIs(repo.get_event_preference(A, uid, "DAILY_BRIEFING").email_immediate_override, False)

    def test_a_blank_password_keeps_the_saved_one(self):
        uid = self.user()
        self.save(uid)
        self.save(uid, password=None, host="smtp2.example.com")
        self.assertEqual(self.store.read(credential_target(uid)), {"password": SECRET})

    def test_it_cannot_be_turned_on_without_a_password(self):
        uid = self.user()
        with self.assertRaises(BadRequestException):
            self.save(uid, password=None)
        self.assertFalse(self.mail.status(uid, A)["enabled"])

    def test_bad_values_are_refused_before_anything_is_stored(self):
        uid = self.user()
        for over in ({"host": "a b"}, {"host": ""}, {"port": 0}, {"port": 70000}, {"port": True}, {"port": "587"}, {"security": "NONE"}, {"security": ""},
                     {"sender": "x"}, {"to": "x@"}, {"to": "a@b.co\nBcc: c@d.co"}, {"sender": ""}, {"login": "a b"}, {"password": "한글비번"}):
            with self.assertRaises(BadRequestException, msg=str(over)):
                self.save(uid, **over)
        self.assertFalse(self.store.exists(credential_target(uid)))
        self.assertEqual(self.mail.settings(uid, A), {})

    def test_other_keys_in_the_same_setting_survive(self):
        from app.domains.user_settings.repository import UserSettingRepository
        from app.domains.user_settings.schema import SETTING_KEY_NOTIFICATION_PREFERENCES
        from app.domains.user_settings.service import UserSettingService

        uid = self.user()
        UserSettingService(UserSettingRepository(self.db)).put(uid, A, SETTING_KEY_NOTIFICATION_PREFERENCES, {"other": 1}, 0, 1)
        self.save(uid)
        self.assertEqual(self.mail._blob(uid, A)[0]["other"], 1)

    def test_each_user_has_their_own_settings_and_password(self):
        one, two = self.user("u1"), self.user("u2")
        self.save(one)
        self.assertEqual(self.mail.settings(two, A), {})
        self.assertFalse(self.store.exists(credential_target(two)))

    def test_when_off_the_store_is_not_even_opened(self):
        uid = self.user()

        class Explodes:
            def read(self, target):
                raise AssertionError("열면 안 된다")

            def exists(self, target):
                raise AssertionError("열면 안 된다")

        self.assertIsNone(BriefEmailService(self.db, Explodes()).delivery_for(uid, A))

    def test_saved_but_turned_off_does_not_open_the_store_either(self):
        uid = self.user()
        self.save(uid, enabled=False)

        class Explodes:
            def read(self, target):
                raise AssertionError("열면 안 된다")

        self.assertIsNone(BriefEmailService(self.db, Explodes()).delivery_for(uid, A))

    def test_a_lost_password_is_not_shown_as_on(self):
        uid = self.user()
        self.save(uid)
        self.store.delete(credential_target(uid))
        status = self.mail.status(uid, A)
        self.assertEqual((status["enabled"], status["complete"], status["password_saved"]), (False, False, False))

    def test_a_test_email_goes_to_the_saved_address_only_with_an_allowance(self):
        uid = self.user()
        self.save(uid)
        self.assertEqual(self.mail.send_test(uid, A), {"sent": False, "reason": "EXTERNAL_ALLOWANCE_REQUIRED"})
        self.assertEqual(FakeSmtp.instances, [])
        self.allow()
        self.assertEqual(self.mail.send_test(uid, A), {"sent": True, "reason": None})
        mail = FakeSmtp.instances[0].sent[0]
        self.assertEqual(mail["To"], "me@example.com")
        self.assertIn("승인·결제할 수 없습니다", mail.get_content())

    def test_a_test_email_needs_settings_and_a_password(self):
        uid = self.user()
        with self.assertRaises(BadRequestException):
            self.mail.send_test(uid, A)
        self.save(uid)
        self.store.delete(credential_target(uid))
        with self.assertRaises(BadRequestException):
            self.mail.send_test(uid, A)


class MorningBriefTests(DbCase):

    def logs(self):
        self.db.expire_all()
        return self.db.query(NotificationEmailLog).order_by(NotificationEmailLog.id).all()

    def test_only_who_turned_it_on_gets_the_mail_with_the_count_summary(self):
        on, off = self.user("on"), self.user("off")
        self.allow()
        self.save(on)
        result = self.brief()
        self.assertEqual(result["sent"], 2)
        self.assertEqual(len(FakeSmtp.instances), 1)
        mail = FakeSmtp.instances[0].sent[0]
        self.assertEqual(mail["To"], "me@example.com")
        self.assertIn("승인 센터 대기 1건 · 쿠팡 승인반려 2건", mail.get_content())
        self.assertIn("아침 브리핑", mail["Subject"])
        by_user = {row.user_id: row.status for row in self.logs()}
        self.assertEqual(by_user, {on: "SENT", off: "SKIPPED_IN_APP_ONLY"})
        self.assertEqual(self.db.query(Notification).count(), 2)  # 앱 안 알림은 둘 다

    def test_the_same_day_is_not_sent_twice_and_the_next_day_is(self):
        self.user()
        uid = self.db.query(User).one().id
        self.allow()
        self.save(uid)
        self.brief()
        self.assertEqual(self.brief()["sent"], 0)
        self.assertEqual(len(FakeSmtp.instances), 1)
        self.brief(now=datetime(2026, 11, 1, 22, 0))
        self.assertEqual(len(FakeSmtp.instances), 2)

    def test_no_allowance_keeps_the_app_notice_and_reports_why(self):
        uid = self.user()
        self.save(uid)
        self.brief()
        self.assertEqual(FakeSmtp.instances, [])
        row = self.logs()[0]
        self.assertEqual((row.status, row.error_detail), ("FAILED", "EXTERNAL_ALLOWANCE_REQUIRED"))
        titles = [n.title for n in self.db.query(Notification).all()]
        self.assertIn("오늘 볼 일 3건", titles)
        last = self.mail.status(uid, A)["last"]
        self.assertEqual((last["status"], last["detail"]), ("FAILED", "EXTERNAL_ALLOWANCE_REQUIRED"))
        self.assertEqual(self.db.query(ExternalCallAllowance).filter_by(status="PENDING", system="EMAIL").count(), 1)

    def test_a_mail_failure_logs_in_once_and_stores_no_secret(self):
        uid = self.user()
        self.allow()
        self.save(uid)
        FakeSmtp.fail = smtplib.SMTPAuthenticationError(535, b"reply with secret")
        self.brief()
        self.assertEqual(len(FakeSmtp.instances), 1)
        row = self.logs()[0]
        self.assertEqual(row.error_detail, "SMTP_AUTH_FAILED:535")
        dump = json.dumps([tuple(r) for r in self.db.execute(text("SELECT action, description FROM audit_logs")).fetchall()], ensure_ascii=False)
        for word in (SECRET, "reply with secret", "me@example.com"):
            self.assertNotIn(word, dump)

    def test_unreadable_email_settings_do_not_stop_the_app_notice(self):
        uid = self.user()

        class Broken:
            def delivery_for(self, user_id, company_id):
                raise RuntimeError("x")

        svc = MorningBriefService(self.db, now_factory=lambda: MORNING, email_service=Broken())
        svc.build = lambda company_id: {"title": "오늘 볼 일 3건", "message": "요약", "unknown": [], "counts": {}}
        with self.assertLogs("homez", level="WARNING"):
            self.assertEqual(svc.run(A)["sent"], 1)
        self.assertEqual(self.logs()[0].user_id, uid)
        self.assertEqual(self.logs()[0].status, "SKIPPED_IN_APP_ONLY")

    def test_turned_off_means_no_mail_even_with_everything_saved(self):
        uid = self.user()
        self.allow()
        self.save(uid)
        self.save(uid, enabled=False, password=None)
        self.brief()
        self.assertEqual(FakeSmtp.instances, [])
        self.assertEqual(self.logs()[0].status, "SKIPPED_IN_APP_ONLY")


class WiringTests(unittest.TestCase):

    def test_other_notifications_still_have_no_mail_sender(self):
        self.assertIsInstance(notif_router._get_notification_email_provider(), NullNotificationEmailProvider)

    def test_routes_are_guarded_and_saving_needs_a_password_recheck(self):
        from app.core.exceptions import ForbiddenException
        from app.core.guard import AdminGuard
        from app.core.guard import SuperAdminGuard

        def route(method):
            return next(r for r in app.main.app.routes if getattr(r, "path", "") == "/notifications/brief-email" and method in r.methods)

        self.assertIn(AdminGuard, [d.call for d in route("GET").dependant.dependencies])
        self.assertIn(SuperAdminGuard, [d.call for d in route("PUT").dependant.dependencies])
        test_route = next(r for r in app.main.app.routes if getattr(r, "path", "") == "/notifications/brief-email/test")
        self.assertEqual(test_route.methods, {"POST"})
        self.assertIn(AdminGuard, [d.call for d in test_route.dependant.dependencies])
        data = notif_router.BriefEmailSettingsRequest(host="smtp.example.com", port=587, security="SSL", sender="a@b.co", to="c@d.co", enabled=True)
        store = mock.Mock()
        with self.assertRaises(ForbiddenException):
            notif_router.save_brief_email_settings(data, current_user=mock.Mock(id=1, company_id=A), recent_auth_token=None, db=mock.Mock(), credential_store=store)
        store.save.assert_not_called()

    def test_the_new_system_and_purpose_are_enforced_and_named(self):
        self.assertIn(CallPurpose.BRIEF_EMAIL, CallPurpose.ENFORCED)
        self.assertIn(ExternalSystem.EMAIL, ExternalSystem.ALL)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.BRIEF_EMAIL], "아침 브리핑 이메일 발송")
        self.assertEqual(ExternalSystem.LABELS[ExternalSystem.EMAIL], "이메일 발송")


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------

def mail_texts():
    block = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:mail\.[a-z_]+|ai\.key_[a-z]+|error))": ("(?:[^"\\]|\\.)*")', block)}


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenFunctionTests(unittest.TestCase):

    def run_js(self, expression):
        constants = re.search(r"  const MAIL_REASON_KEY = \{.*?\};", JS, re.S).group(0)
        sources = "\n".join([constants, 'const chip = (tone, text) => `<i class="${tone}">${text}</i>`;'] + [function_source(n) for n in ("parseUtcMs", "mailReasonText", "mailLastHtml", "mailSettingsHtml", "mailSettingsForm")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__NOW__", str(NOW)).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(mail_texts())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_reason_codes_become_plain_words(self):
        self.assertIn("앱 비밀번호", self.run_js("mailReasonText('SMTP_AUTH_FAILED:535')"))
        self.assertIn("허용", self.run_js("mailReasonText('EXTERNAL_ALLOWANCE_REQUIRED')"))
        self.assertIn("비상 정지", self.run_js("mailReasonText('EXTERNAL_CALL_BLOCKED_ESTOP')"))
        self.assertIn("암호화", self.run_js("mailReasonText('SMTP_TLS_FAILED')"))
        self.assertIn("SMTP_ERROR:OSError", self.run_js("mailReasonText('SMTP_ERROR:OSError')"))

    def test_the_last_result_is_honest(self):
        sent = self.run_js("mailLastHtml({status: 'SENT', at: '2026-11-01T00:00:00'})")
        self.assertIn("보냄", sent)
        failed = self.run_js("mailLastHtml({status: 'FAILED', at: '2026-11-01T00:00:00', detail: 'EXTERNAL_ALLOWANCE_REQUIRED'})")
        self.assertIn("보내지 못했어요", failed)
        self.assertIn("앱 안 알림은 남았어요", failed)
        self.assertNotIn("보냄", failed)
        self.assertIn("앱 안 알림만", self.run_js("mailLastHtml({status: 'SKIPPED_IN_APP_ONLY', at: '2026-11-01T00:00:00'})"))
        self.assertIn("아직 보낸 기록이 없어요", self.run_js("mailLastHtml(null)"))
        self.assertIn("보내지 못했어요", self.run_js("mailLastHtml({status: 'PROVIDER_NOT_CONFIGURED', at: '2026-11-01T00:00:00', detail: 'EMAIL_PROVIDER_NOT_CONFIGURED'})"))

    def test_the_card_shows_saved_state_escaped_and_never_a_password_value(self):
        html = self.run_js("mailSettingsHtml({enabled: true, complete: true, password_saved: true, to: '<b>me@x.co</b>', host: 'smtp.x.co', port: 587, security: 'STARTTLS', sender: 'a@x.co', last: null, password: 'should-not-show'})")
        self.assertIn("&lt;b&gt;me@x.co&lt;/b&gt;", html)
        self.assertIn("smtp.x.co:587 (STARTTLS)", html)
        self.assertIn('id="ez-mail-test"', html)
        self.assertNotIn("should-not-show", html)
        off = self.run_js("mailSettingsHtml({enabled: false, complete: false, password_saved: false, to: '', host: '', port: 587, security: 'STARTTLS', sender: '', last: null})")
        self.assertNotIn('id="ez-mail-test"', off)
        self.assertIn('<i class="gray">꺼짐</i>', off)

    def test_the_form_checks_shape_and_keeps_a_blank_password_out(self):
        values = {"host": "smtp.x.co", "port": "587", "security": "SSL", "sender": "a@x.co", "to": "b@x.co", "login": " id ", "enabled": "ON", "password": ""}
        out = self.run_js(f"mailSettingsForm((n) => ({json.dumps(values)})[n])")
        self.assertEqual(out["body"], {"host": "smtp.x.co", "port": 587, "security": "SSL", "login": "id", "sender": "a@x.co", "to": "b@x.co", "enabled": True})
        withpw = self.run_js(f"mailSettingsForm((n) => ({json.dumps({**values, 'password': ' pw '})})[n])")
        self.assertEqual(withpw["body"]["password"], " pw ")  # 앱 비밀번호의 공백을 고치지 않는다
        for over, key in (({"host": "a b"}, "easy.mail.err_host"), ({"port": "0"}, "easy.mail.err_port"), ({"security": "NONE"}, "easy.mail.err_port"), ({"to": "x"}, "easy.mail.err_email"), ({"sender": ""}, "easy.mail.err_email")):
            self.assertEqual(self.run_js(f"mailSettingsForm((n) => ({json.dumps({**values, **over})})[n])"), {"error": key}, str(over))


class ScreenWiringTests(unittest.TestCase):

    def test_saving_rechecks_the_password_and_testing_confirms_first(self):
        edit = function_source("mailSettingsEdit")
        self.assertLess(edit.index("B.promptRecentAuthToken()"), edit.index('api("/notifications/brief-email"'))
        self.assertIn('headers: { "X-Recent-Auth-Token": token }', edit)
        self.assertIn('type="password" autocomplete="new-password"', edit)
        test = function_source("mailSettingsTest")
        self.assertLess(test.index("await ezDialog("), test.index("api("))
        self.assertIn("if (!ok) return;", test)

    def test_the_more_screen_loads_the_card(self):
        self.assertIn('id="ez-mail-box"', JS)
        self.assertIn("mailSettingsLoad();", JS)

    def test_the_texts_exist_in_both_languages(self):
        ko = set(re.findall(r'"(easy\.mail\.[a-z_]+)":', re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)))
        en = set(re.findall(r'"(easy\.mail\.[a-z_]+)":', re.search(r'"en-US": \{(.*?)\n  \}', TEXTS, re.S).group(1)))
        self.assertEqual(ko, en)
        self.assertGreater(len(ko), 30)


if __name__ == "__main__":
    unittest.main()
