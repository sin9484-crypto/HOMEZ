"""
=========================================================
Homez OS

File : tests/test_health_summary.py

2026-10-18 — 서버 관리 AI T1: 운영 상태 진단 규칙(`health_summary.py`, 순수·AI 없음)과 수집 서비스(`health_service.py`, 읽기 전용)를 고정한다. 외부 호출 0.

  - 자동 작업 엔진이 꺼졌거나(설정으로 끈 것이 아니면) 작업이 빠지면 FAIL, 멈췄거나 15분 넘게 밀리면 WARN. 설정으로 꺼 둔 것은 INFO.
  - 백업: 없음·7일 초과 WARN, 30일 초과 FAIL. 복구 시험: 최근 실패 FAIL, 14일 초과 WARN, 기록 없음 INFO. 디스크: 1GB 미만 WARN, 300MB 미만 FAIL. 긴급 중지 WARN.
  - 읽지 못한 항목은 정상이라 하지 않고 `unknown`에 남긴다(그 항목만 건너뛰고 나머지는 점검한다).
  - 수집은 읽기뿐이다 — DB 변경·작업 실행·재시작·복구·외부 호출이 없다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.backup.model import BackupRecord
from app.domains.diagnostics import health_summary as hs
from app.domains.diagnostics.health_service import HealthService
from app.domains.restore.model import RESTORE_STATUS_FAILED
from app.domains.restore.model import RESTORE_STATUS_SUCCEEDED
from app.domains.restore.model import RestoreAttempt

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 18, 12, 0, 0)
JOBS = ["job_a", "job_b"]
GB = 1024 ** 3


def good(**overrides):
    snap = {
        "now": NOW, "scheduler_enabled": True,
        "scheduler": {"running": True, "jobs": [{"id": j, "next_run": NOW + timedelta(minutes=3)} for j in JOBS]},
        "expected_jobs": JOBS, "latest_backup_at": NOW - timedelta(days=1), "rehearsal_known": True,
        "latest_rehearsal": {"at": NOW - timedelta(days=2), "ok": True}, "disk_free_bytes": 20 * GB, "estop_active": False,
        # 2026-10-21 반영 A: 자동 감지·읽기 허용도 정상 스냅샷의 일부(없으면 "모름"으로 남는다 — 아래 시험은 tests/test_health_detection_checks.py)
        "detection": {"health_code": "OK", "minutes_since_attempt": 3}, "reads": {"lapsed": [], "never": []},
    }
    snap.update(overrides)
    return snap


def codes(result, level=None):
    return [c["code"] for c in result["checks"] if level is None or c["level"] == level]


class RuleTests(unittest.TestCase):

    def test_a_healthy_snapshot_is_ok_with_every_item_passed(self):
        r = hs.evaluate(good())
        self.assertEqual((r["overall"], r["checks"], r["unknown"]), ("OK", [], []))
        self.assertEqual(set(r["passed"]), {"SCHEDULER", "JOBS", "BACKUP", "REHEARSAL", "DISK", "ESTOP", "DETECTION", "READS"})

    def test_scheduler_states(self):
        self.assertEqual(codes(hs.evaluate(good(scheduler=None)), "FAIL"), ["SCHEDULER_NOT_RUNNING"])
        self.assertEqual(codes(hs.evaluate(good(scheduler={"running": False, "jobs": []})), "FAIL"), ["SCHEDULER_NOT_RUNNING"])
        off = hs.evaluate(good(scheduler_enabled=False, scheduler=None))
        self.assertEqual((off["overall"], codes(off)), ("INFO", ["SCHEDULER_DISABLED"]))  # 설정으로 꺼 둔 것은 이상이 아니다
        self.assertIn("SCHEDULER", hs.evaluate(good(scheduler_enabled=None))["unknown"])

    def test_jobs_missing_paused_and_overdue(self):
        jobs = [{"id": "job_a", "next_run": None}]
        r = hs.evaluate(good(scheduler={"running": True, "jobs": jobs}))
        self.assertEqual(codes(r, "FAIL"), ["JOBS_MISSING"])
        self.assertEqual(r["checks"][0]["params"]["jobs"], ["job_b"])
        self.assertEqual(codes(r, "WARN"), ["JOBS_PAUSED"])
        late = [{"id": "job_a", "next_run": NOW - timedelta(minutes=16)}, {"id": "job_b", "next_run": NOW - timedelta(minutes=15)}]
        r = hs.evaluate(good(scheduler={"running": True, "jobs": late}))
        self.assertEqual(codes(r), ["JOBS_OVERDUE"])
        self.assertEqual(r["checks"][0]["params"]["jobs"], ["job_a"])  # 정확히 15분은 아직 밀린 것이 아니다

    def test_unexpected_extra_jobs_are_ignored(self):
        jobs = [{"id": j, "next_run": NOW + timedelta(minutes=1)} for j in JOBS] + [{"id": "other", "next_run": None}]
        self.assertEqual(hs.evaluate(good(scheduler={"running": True, "jobs": jobs}))["checks"], [])

    def test_backup_age_thresholds(self):
        self.assertEqual(hs.evaluate(good(latest_backup_at=NOW - timedelta(days=7, hours=23)))["checks"], [])
        r = hs.evaluate(good(latest_backup_at=NOW - timedelta(days=8)))
        self.assertEqual((codes(r, "WARN"), r["checks"][0]["params"]), (["BACKUP_STALE"], {"days": 8}))
        r = hs.evaluate(good(latest_backup_at=NOW - timedelta(days=31)))
        self.assertEqual(codes(r, "FAIL"), ["BACKUP_OLD"])
        self.assertEqual(codes(hs.evaluate(good(latest_backup_at=None)), "WARN"), ["BACKUP_NONE"])
        snap = good()
        del snap["latest_backup_at"]
        self.assertIn("BACKUP", hs.evaluate(snap)["unknown"])

    def test_restore_rehearsal_states(self):
        self.assertEqual(codes(hs.evaluate(good(latest_rehearsal={"at": NOW - timedelta(days=3), "ok": False})), "FAIL"), ["REHEARSAL_FAILED"])
        self.assertEqual(codes(hs.evaluate(good(latest_rehearsal={"at": NOW - timedelta(days=15), "ok": True})), "WARN"), ["REHEARSAL_STALE"])
        self.assertEqual(hs.evaluate(good(latest_rehearsal={"at": NOW - timedelta(days=14), "ok": True}))["checks"], [])
        none = hs.evaluate(good(latest_rehearsal=None))
        self.assertEqual((none["overall"], codes(none)), ("INFO", ["REHEARSAL_NONE"]))
        self.assertIn("REHEARSAL", hs.evaluate(good(rehearsal_known=False))["unknown"])

    def test_disk_thresholds(self):
        self.assertEqual(hs.evaluate(good(disk_free_bytes=GB))["checks"], [])
        r = hs.evaluate(good(disk_free_bytes=GB - 1))
        self.assertEqual((codes(r, "WARN"), r["checks"][0]["params"]["free_mb"]), (["DISK_LOW"], 1023))
        self.assertEqual(codes(hs.evaluate(good(disk_free_bytes=299 * 1024 ** 2)), "FAIL"), ["DISK_CRITICAL"])
        self.assertIn("DISK", hs.evaluate(good(disk_free_bytes=None))["unknown"])

    def test_emergency_stop_warns_and_unknown_is_not_called_healthy(self):
        self.assertEqual(codes(hs.evaluate(good(estop_active=True)), "WARN"), ["ESTOP_ACTIVE"])
        r = hs.evaluate(good(estop_active=None, disk_free_bytes=None))
        self.assertEqual(set(r["unknown"]), {"ESTOP", "DISK"})
        self.assertNotIn("ESTOP", r["passed"])
        self.assertEqual(r["overall"], "OK")  # 모르는 것은 문제로도 정상으로도 세지 않고 따로 알린다

    def test_overall_is_the_worst_level_and_checks_are_sorted_severe_first(self):
        r = hs.evaluate(good(estop_active=True, latest_backup_at=NOW - timedelta(days=40), latest_rehearsal=None))
        self.assertEqual(r["overall"], "FAIL")
        self.assertEqual([c["level"] for c in r["checks"]], ["FAIL", "WARN", "INFO"])

    def test_the_rules_module_is_pure(self):
        source = (ROOT / "app/domains/diagnostics/health_summary.py").read_text(encoding="utf-8")
        for forbidden in ("sqlalchemy", "requests", "urllib", "socket", "subprocess", "open(", "shutil", "os.", "call_llm"):
            self.assertNotIn(forbidden, source, forbidden)


class FakeScheduler:
    def __init__(self, running=True, jobs=()):
        self.running, self._jobs = running, list(jobs)
        self.calls = []

    def get_jobs(self):
        self.calls.append("get_jobs")
        return self._jobs

    def __getattr__(self, name):  # 읽기 말고 다른 호출(실행·일시중지·삭제 등)은 시험이 바로 알아챈다
        if name in ("pause_job", "resume_job", "remove_job", "shutdown", "start", "add_job", "modify_job", "pause", "resume"):
            raise AssertionError(f"읽기 전용 점검이 {name}을 호출했다")
        raise AttributeError(name)


def job(job_id, next_run):
    return SimpleNamespace(id=job_id, next_run_time=next_run)


class ServiceTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[BackupRecord.__table__, RestoreAttempt.__table__])
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def backup(self, at):
        self.db.add(BackupRecord(file_path="x.db", file_size_bytes=1, sha256="a" * 64, integrity_check_result="ok", trigger_source="manual", created_at=at))
        self.db.commit()

    def attempt(self, at, status):
        self.db.add(RestoreAttempt(source_backup_path="a", target_db_path="b", status=status, created_at=at))
        self.db.commit()

    def service(self, scheduler, **kw):
        kw.setdefault("scheduler_enabled", True)
        kw.setdefault("disk_path", Path(self.db_path))
        kw.setdefault("expected_jobs", JOBS)
        kw.setdefault("now_factory", lambda: NOW)
        return HealthService(self.db, scheduler_getter=lambda: scheduler, **kw)

    def test_a_healthy_system_reads_as_ok(self):
        aware = (NOW + timedelta(minutes=2)).replace(tzinfo=timezone.utc)
        scheduler = FakeScheduler(jobs=[job(j, aware) for j in JOBS])
        self.backup(NOW - timedelta(days=2))
        self.attempt(NOW - timedelta(days=3), RESTORE_STATUS_SUCCEEDED)
        summary = self.service(scheduler).summary()
        self.assertEqual(summary["overall"], "OK")
        self.assertEqual(summary["checked_at"], "2026-10-18T12:00:00")
        self.assertIn("BACKUP", summary["passed"])

    def test_timezone_aware_job_times_are_compared_in_utc(self):
        kst = timezone(timedelta(hours=9))
        on_time = (NOW + timedelta(minutes=2)).replace(tzinfo=timezone.utc).astimezone(kst)   # 같은 시각의 한국 표기
        late = (NOW - timedelta(minutes=30)).replace(tzinfo=timezone.utc).astimezone(kst)
        summary = self.service(FakeScheduler(jobs=[job("job_a", on_time), job("job_b", late)])).summary()
        self.assertEqual([(c["code"], c["params"]["jobs"]) for c in summary["checks"] if c["code"] == "JOBS_OVERDUE"], [("JOBS_OVERDUE", ["job_b"])])

    def test_the_latest_backup_and_attempt_win_and_a_failed_latest_attempt_fails(self):
        self.backup(NOW - timedelta(days=40))
        self.backup(NOW - timedelta(days=1))
        self.attempt(NOW - timedelta(days=10), RESTORE_STATUS_SUCCEEDED)
        self.attempt(NOW - timedelta(days=1), RESTORE_STATUS_FAILED)
        summary = self.service(FakeScheduler(jobs=[job(j, NOW + timedelta(minutes=1)) for j in JOBS])).summary()
        self.assertEqual([c["code"] for c in summary["checks"]], ["REHEARSAL_FAILED"])  # 백업은 최신(1일 전) 기준이라 정상

    def test_no_backup_rows_means_none_and_missing_tables_mean_unknown_not_healthy(self):
        summary = self.service(FakeScheduler(jobs=[job(j, NOW + timedelta(minutes=1)) for j in JOBS])).summary()
        self.assertIn("BACKUP_NONE", [c["code"] for c in summary["checks"]])
        self.db.execute(text("DROP TABLE backup_records"))
        self.db.execute(text("DROP TABLE restore_attempts"))
        self.db.commit()
        broken = self.service(FakeScheduler(jobs=[job(j, NOW + timedelta(minutes=1)) for j in JOBS])).summary()
        self.assertIn("BACKUP", broken["unknown"])
        self.assertIn("REHEARSAL", broken["unknown"])
        self.assertNotIn("BACKUP", broken["passed"])

    def test_a_scheduler_that_cannot_be_read_is_unknown_and_a_stopped_one_fails(self):
        class Broken:
            def get_jobs(self):
                raise RuntimeError("boom")

            running = True

        summary = self.service(Broken()).summary()
        self.assertIn("SCHEDULER", summary["unknown"])
        self.assertNotIn("SCHEDULER_NOT_RUNNING", [c["code"] for c in summary["checks"]])
        none = self.service(None).summary()
        self.assertIn("SCHEDULER_NOT_RUNNING", [c["code"] for c in none["checks"]])

    def test_the_collection_only_reads(self):
        scheduler = FakeScheduler(jobs=[job(j, NOW + timedelta(minutes=1)) for j in JOBS])
        self.backup(NOW - timedelta(days=1))
        tables = ("backup_records", "restore_attempts")
        before = [self.db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables]
        self.service(scheduler).summary()
        self.assertEqual(before, [self.db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables])
        self.assertEqual(scheduler.calls, ["get_jobs"])

    def test_the_service_source_has_no_writes_or_side_effects(self):
        source = (ROOT / "app/domains/diagnostics/health_service.py").read_text(encoding="utf-8")
        for forbidden in (".commit(", ".add(", ".delete(", ".execute(", "requests", "subprocess", "call_llm", "pause_job", "resume_job", "remove_job", "shutdown(", "create_backup", "restore"):
            self.assertNotIn(forbidden, source.replace("RESTORE_STATUS", "").replace("RestoreAttempt", "").replace("restore_attempts", "").replace("restore.model", ""), forbidden)

    def test_the_expected_jobs_are_the_registered_job_ids(self):
        from app.domains.diagnostics.health_service import expected_job_ids
        from app.domains.scheduler import jobs

        self.assertEqual(
            set(expected_job_ids()),
            {jobs.BACKUP_REHEARSAL_JOB_ID, jobs.RECALL_NOTICE_CHECK_JOB_ID, jobs.ORDER_COLLECTION_TICK_JOB_ID, jobs.DELIVERY_TRACKING_JOB_ID, jobs.AFTER_SALES_JOB_ID, jobs.PRODUCT_STATUS_JOB_ID},
        )
        # 실제로 등록되는 작업과도 같다(작업을 추가하고 이 목록을 빼먹으면 여기서 알린다)
        source = (ROOT / "app/domains/scheduler/jobs.py").read_text(encoding="utf-8")
        registered = set(__import__("re").findall(r"job_id=([A-Z_]+JOB_ID)", source.split("def register_all_jobs")[1]))
        self.assertEqual(registered, {"BACKUP_REHEARSAL_JOB_ID", "RECALL_NOTICE_CHECK_JOB_ID", "ORDER_COLLECTION_TICK_JOB_ID", "DELIVERY_TRACKING_JOB_ID", "AFTER_SALES_JOB_ID", "PRODUCT_STATUS_JOB_ID"})


class RouteTests(unittest.TestCase):

    def test_the_route_is_admin_only_read_only_and_json(self):
        import sys

        import app.domains.diagnostics.router  # noqa: F401

        module = sys.modules["app.domains.diagnostics.router"]
        route = next(r for r in module.router.routes if r.path == "/diagnostics/health-summary")
        self.assertEqual(route.methods, {"GET"})
        names = [d.call.__qualname__.lower() for d in route.dependant.dependencies]
        self.assertTrue(any("admin" in n for n in names), names)


if __name__ == "__main__":
    unittest.main()
