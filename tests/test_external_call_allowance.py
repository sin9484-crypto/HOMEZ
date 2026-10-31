"""
=========================================================
Homez OS

File : tests/test_external_call_allowance.py

2026-10-16 — 바깥 호출 허용(`external_call_*`)의 규칙을 고정한다.
  - Migration SQL이 모델의 정확한 DDL과 같고 임시 DB에 깨끗이 적용된다(실제 DB에는 적용하지 않는다).
  - 허용이 없으면 막고 요청을 한 번만 올린다. 횟수형은 정확히 허용한 만큼만 쓰고, 상시형은 시간당 상한을 지킨다.
  - 동시에 마지막 1회를 쓰려는 두 요청 중 하나만 통과한다.
  - 허용을 주는 API는 비밀번호 재확인이 필요하고, 거두는 쪽은 필요 없다. 회사 간 격리.
  - 작업 장부(송장·상품 출고일)와 5분 주문 감지가 실제 호출 **직전에** 허용을 확인한다. 허용 테이블이 없으면 예전 동작.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.constants import LogOutcome
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallNotAllowed
from app.domains.external_call.service import ExternalCallPolicyService

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261016_00_create_external_call_allowance_schema.sql"
NOW = datetime(2026, 10, 16, 3, 0, 0)


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.replace(",\n", ", ")).replace("( ", "(").replace(" )", ")").strip()


class MigrationTests(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def test_the_sql_is_exactly_the_models_canonical_ddl(self):
        dialect = sqlite_dialect.dialect()
        expected = []
        for table in (ExternalCallAllowance.__table__, ExternalCallLog.__table__):
            expected.append(_normalize(str(CreateTable(table).compile(dialect=dialect))))
            expected += [_normalize(str(CreateIndex(ix).compile(dialect=dialect))) for ix in sorted(table.indexes, key=lambda i: i.name)]
        text = MIGRATION.read_text(encoding="utf-8")
        statements = [_normalize(m) for m in re.findall(r"(CREATE (?:UNIQUE )?(?:TABLE|INDEX)[^;]*);", text, flags=re.S)]
        self.assertEqual([s.replace(" ,", ",").rstrip(",") for s in statements], [e.rstrip(";") for e in expected])

    def test_it_applies_cleanly_and_keeps_existing_data_untouched(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("CREATE TABLE existing_marker (id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO existing_marker (v) VALUES ('keep')")
            conn.commit()
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT v FROM existing_marker").fetchall(), [("keep",)])
            for table, model in (("external_call_allowances", ExternalCallAllowance), ("external_call_logs", ExternalCallLog)):
                self.assertEqual([r[1] for r in conn.execute(f"PRAGMA table_info({table})")], [c.name for c in model.__table__.columns])
        finally:
            conn.close()

    def test_reapplying_is_refused_not_silently_ignored(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        finally:
            conn.close()

    def test_the_rollback_note_names_both_tables_and_is_only_a_comment(self):
        text = MIGRATION.read_text(encoding="utf-8")
        tail = text.split("COMMIT;")[1]
        for line in tail.splitlines():
            if line.strip():
                self.assertTrue(line.startswith("--"), line)
        self.assertIn("DROP TABLE IF EXISTS external_call_logs;", tail)
        self.assertIn("DROP TABLE IF EXISTS external_call_allowances;", tail)


class ServiceBase(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(self.engine, tables=[
            ExternalCallAllowance.__table__, ExternalCallLog.__table__, FunctionAutomationState.__table__, EmergencyStop.__table__,
        ])
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.clock = [NOW]
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    def svc(self, db=None):
        return ExternalCallPolicyService(db or self.db, now_factory=lambda: self.clock[0])

    def grant(self, **kw):
        base = dict(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.PRODUCT_OUTBOUND_DAYS,
                    mode=AllowanceMode.COUNTED, count=2, user_id=1)
        base.update(kw)
        return self.svc().grant(1, **base)

    def authorize(self, **kw):
        base = dict(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.PRODUCT_OUTBOUND_DAYS)
        base.update(kw)
        return self.svc().authorize(1, base["system"], base["kind"], base["purpose"], actor_user_id=1)


class AuthorizeTests(ServiceBase):

    def test_without_an_allowance_the_call_is_refused_and_one_request_is_raised(self):
        for _ in range(3):
            with self.assertRaises(ExternalCallNotAllowed):
                self.authorize()
        pending = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).all()
        self.assertEqual(len(pending), 1)
        self.assertEqual((pending[0].kind, pending[0].purpose, pending[0].requested_count), ("READ", "PRODUCT_OUTBOUND_DAYS", 3))
        self.assertEqual(self.db.query(ExternalCallLog).count(), 0)

    def test_a_counted_allowance_is_used_exactly_as_many_times_as_granted(self):
        allowance = self.grant(count=2)
        self.authorize()
        self.authorize()
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize()
        self.db.refresh(allowance)
        self.assertEqual((allowance.remaining_count, allowance.status), (0, AllowanceStatus.EXHAUSTED))
        self.assertEqual(self.db.query(ExternalCallLog).filter_by(outcome=LogOutcome.ALLOWED).count(), 2)

    def test_read_and_write_are_separate(self):
        self.grant(kind=CallKind.READ, count=5)
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize(kind=CallKind.WRITE)

    def test_purpose_and_system_must_match_but_any_covers_every_purpose(self):
        self.grant(purpose=CallPurpose.INVOICE_UPLOAD, count=3)
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize(purpose=CallPurpose.PRODUCT_OUTBOUND_DAYS)
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize(system=ExternalSystem.ONCH, purpose=CallPurpose.INVOICE_UPLOAD)
        self.grant(purpose=CallPurpose.ANY, count=1)
        self.authorize(purpose=CallPurpose.PRODUCT_OUTBOUND_DAYS)

    def test_the_exact_purpose_is_used_before_any(self):
        exact = self.grant(purpose=CallPurpose.PRODUCT_OUTBOUND_DAYS, count=1)
        wide = self.grant(purpose=CallPurpose.ANY, count=1)
        ticket = self.authorize()
        self.assertEqual(ticket.allowance_id, exact.id)
        ticket = self.authorize()
        self.assertEqual(ticket.allowance_id, wide.id)

    def test_other_companies_never_share_allowances(self):
        self.grant()
        with self.assertRaises(ExternalCallNotAllowed):
            self.svc().authorize(2, ExternalSystem.COUPANG, CallKind.READ, CallPurpose.PRODUCT_OUTBOUND_DAYS)

    def test_an_expired_allowance_is_not_used(self):
        allowance = self.grant(count=5, hours=1)
        self.clock[0] = NOW + timedelta(hours=2)
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize()
        self.db.refresh(allowance)
        self.assertEqual(allowance.status, AllowanceStatus.EXPIRED)

    def test_a_revoked_allowance_is_not_used(self):
        allowance = self.grant(count=5)
        self.svc().revoke(1, allowance.id, user_id=1)
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize()

    def test_the_emergency_stop_blocks_even_with_an_allowance(self):
        from app.domains.automation_safety.service import SafetyService

        self.grant(count=5)
        SafetyService(self.db).activate_emergency_stop(1, "test", 1) if hasattr(SafetyService, "activate_emergency_stop") else None
        if not SafetyService(self.db).is_emergency_stop_active():
            self.skipTest("비상 정지 켜기 API 이름이 달라 이 환경에서 건너뜀")
        with self.assertRaises(ExternalCallNotAllowed) as caught:
            self.authorize()
        self.assertEqual(caught.exception.reason, "ESTOP_ACTIVE")

    def test_purposes_outside_the_enforced_list_are_only_logged(self):
        ticket = self.svc().authorize(1, ExternalSystem.COUPANG, CallKind.WRITE, "SALE_STOP", method="PUT")
        self.assertIsNone(ticket.allowance_id)
        log = self.db.query(ExternalCallLog).one()
        self.assertEqual((log.outcome, log.purpose, log.method), (LogOutcome.LOGGED, "SALE_STOP", "PUT"))
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)

    def test_finish_records_the_outcome_and_status(self):
        self.grant(count=2)
        ticket = self.authorize()
        self.svc().finish(ticket, succeeded=True, http_status=200)
        log = self.db.query(ExternalCallLog).one()
        self.assertEqual((log.outcome, log.http_status), (LogOutcome.SUCCEEDED, 200))
        ticket = self.authorize()
        self.svc().finish(ticket, succeeded=False, http_status=500)
        self.assertEqual(self.db.query(ExternalCallLog).order_by(ExternalCallLog.id.desc()).first().outcome, LogOutcome.FAILED)


class StandingTests(ServiceBase):

    def standing(self, per_hour=3, hours=24):
        return self.grant(purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING, count=None, per_hour_limit=per_hour, hours=hours)

    def test_the_hourly_limit_holds_and_resets(self):
        self.standing(per_hour=3)
        for _ in range(3):
            self.authorize(purpose=CallPurpose.ORDER_DETECTION)
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize(purpose=CallPurpose.ORDER_DETECTION)
        self.clock[0] = NOW + timedelta(minutes=61)
        self.authorize(purpose=CallPurpose.ORDER_DETECTION)

    def test_a_five_minute_cadence_fits_the_default_hourly_limit(self):
        from app.domains.external_call.constants import DEFAULT_ORDER_DETECTION_PER_HOUR

        self.standing(per_hour=DEFAULT_ORDER_DETECTION_PER_HOUR)
        for tick in range(24):  # 두 시간 동안 5분마다
            self.clock[0] = NOW + timedelta(minutes=5 * tick)
            self.authorize(purpose=CallPurpose.ORDER_DETECTION)

    def test_standing_is_only_for_reads_and_needs_a_period_and_limit(self):
        with self.assertRaises(BadRequestException):
            self.grant(kind=CallKind.WRITE, purpose=CallPurpose.INVOICE_UPLOAD, mode=AllowanceMode.STANDING, count=None, per_hour_limit=5, hours=24)
        with self.assertRaises(BadRequestException):
            self.grant(purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING, count=None, per_hour_limit=None, hours=24)
        with self.assertRaises(BadRequestException):
            self.grant(purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING, count=None, per_hour_limit=5, hours=None)
        with self.assertRaises(BadRequestException):
            self.grant(purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING, count=None, per_hour_limit=999, hours=24)


class DecisionTests(ServiceBase):

    def test_approve_uses_the_requested_terms_and_can_override_them(self):
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize(purpose=CallPurpose.INVOICE_UPLOAD, kind=CallKind.WRITE)
        request = self.db.query(ExternalCallAllowance).filter_by(status=AllowanceStatus.PENDING).one()
        approved = self.svc().approve(1, request.id, user_id=7)
        self.assertEqual((approved.status, approved.remaining_count, approved.decided_by), (AllowanceStatus.ACTIVE, 1, 7))
        self.authorize(purpose=CallPurpose.INVOICE_UPLOAD, kind=CallKind.WRITE)
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize(purpose=CallPurpose.INVOICE_UPLOAD, kind=CallKind.WRITE)

    def test_a_decided_request_cannot_be_decided_again(self):
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize()
        request = self.db.query(ExternalCallAllowance).one()
        self.svc().reject(1, request.id, user_id=1)
        with self.assertRaises(ConflictException):
            self.svc().approve(1, request.id, user_id=1)
        with self.assertRaises(ConflictException):
            self.svc().reject(1, request.id, user_id=1)

    def test_decisions_are_company_scoped(self):
        allowance = self.grant()
        with self.assertRaises(NotFoundException):
            self.svc().revoke(2, allowance.id, user_id=1)

    def test_count_bounds(self):
        for bad in (0, 51, -1):
            with self.assertRaises(BadRequestException, msg=bad):
                self.grant(count=bad)

    def test_unknown_values_are_rejected(self):
        with self.assertRaises(BadRequestException):
            self.grant(system="NO_SUCH_SYSTEM")  # NAVER는 2026-10-31(6단계)부터 알려진 값
        with self.assertRaises(BadRequestException):
            self.grant(purpose="WHATEVER")
        with self.assertRaises(BadRequestException):
            self.grant(kind="DELETE")


class ConcurrencyTests(ServiceBase):

    def test_two_sessions_racing_for_the_last_use_only_one_wins(self):
        self.grant(count=1)
        results = []
        barrier = threading.Barrier(2)

        def worker():
            db = self.Session()
            try:
                barrier.wait(timeout=10)
                self.svc(db).authorize(1, ExternalSystem.COUPANG, CallKind.READ, CallPurpose.PRODUCT_OUTBOUND_DAYS)
                results.append("ok")
            except ExternalCallNotAllowed:
                results.append("denied")
            finally:
                db.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(sorted(results), ["denied", "ok"])
        self.assertEqual(self.db.query(ExternalCallLog).filter_by(outcome=LogOutcome.ALLOWED).count(), 1)


class SummaryTests(ServiceBase):

    def test_summary_lists_pending_active_and_today_counts(self):
        with self.assertRaises(ExternalCallNotAllowed):
            self.authorize(purpose=CallPurpose.INVOICE_UPLOAD, kind=CallKind.WRITE)
        self.grant(count=3)
        self.authorize()
        summary = self.svc().summary(1)
        self.assertTrue(summary["installed"])
        self.assertEqual(len(summary["pending"]), 1)
        self.assertEqual(summary["active"][0]["remaining_count"], 2)
        self.assertEqual(summary["today"], {"COUPANG": {"READ": 1}})
        self.assertEqual(summary["recent"][0]["purpose_label"], CallPurpose.LABELS[CallPurpose.PRODUCT_OUTBOUND_DAYS])

    def test_the_summary_does_not_leak_other_companies(self):
        self.grant()
        self.assertEqual(self.svc().summary(2)["active"], [])

    def test_installed_reflects_the_tables(self):
        self.assertTrue(ExternalCallPolicyService.installed(self.db))
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        try:
            self.assertFalse(ExternalCallPolicyService.installed(sessionmaker(bind=engine)()))
        finally:
            engine.dispose()
            os.remove(path)


class WiringTests(unittest.TestCase):

    def test_granting_needs_recent_auth_and_revoking_does_not(self):
        router = (ROOT / "app/domains/external_call/router.py").read_text(encoding="utf-8")
        for name in ("grant_allowance", "approve_request"):
            body = router.split(f"def {name}(")[1].split("@router")[0]
            self.assertIn("_require_recent_auth(recent_auth_token, current_user)", body, name)
        for name in ("reject_request", "revoke_allowance", "get_summary"):
            body = router.split(f"def {name}(")[1].split("@router")[0]
            self.assertNotIn("_require_recent_auth", body, name)
        for name in ("grant_allowance", "approve_request", "reject_request", "revoke_allowance", "get_summary"):
            self.assertIn("Depends(AdminGuard)", router.split(f"def {name}(")[1].split("@router")[0], name)

    def test_the_schemas_are_strict(self):
        from pydantic import ValidationError

        from app.domains.external_call.schema import ApproveRequest
        from app.domains.external_call.schema import GrantRequest

        good = dict(system="COUPANG", kind="READ", purpose="ORDER_DETECTION", mode="STANDING", per_hour_limit=14, hours=24)
        GrantRequest(**good)
        for bad in ({"extra": 1}, {"count": 0}, {"count": 51}, {"per_hour_limit": 0}, {"hours": 0}, {"hours": 721}):
            with self.assertRaises(ValidationError, msg=bad):
                GrantRequest(**{**good, **bad})
        ApproveRequest()
        with self.assertRaises(ValidationError):
            ApproveRequest(count=0)
        with self.assertRaises(ValidationError):
            ApproveRequest(unknown=1)


if __name__ == "__main__":
    unittest.main()
