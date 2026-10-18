"""
=========================================================
Homez OS

File : app/domains/diagnostics/health_service.py

2026-10-18 — 서버 관리 AI T1: 운영 상태 스냅샷을 **읽기 전용**으로 모아 `health_summary.evaluate`에 넘긴다. DB 쓰기·외부 호출·AI·재시작·복구 실행 없음.

읽는 곳(각각 실패하면 그 항목만 "읽지 못함"으로 남고 나머지는 계속한다):
  - 자동 작업 엔진: `SchedulerService.get()`의 실행 여부와 등록된 작업(다음 실행 시각). 기대하는 작업 목록은 `scheduler.jobs`의 작업 번호 상수.
  - 백업: `backup_records` 가장 최근 시각 / 복구 시험: `restore_attempts` 가장 최근 시도(성공·실패).
  - 디스크: 데이터베이스 파일이 있는 디스크의 여유 용량(`shutil.disk_usage`).
  - 긴급 중지: `SafetyService.is_emergency_stop_active()`.
무거운 점검(DB 무결성 검사·마이그레이션 진단)은 요청마다 돌리지 않는다 — 진단 내보내기(`/diagnostics/export`)와 주간 복구 시험이 맡는다.
=========================================================
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable

from sqlalchemy.orm import Session

from app.domains.backup.model import BackupRecord
from app.domains.diagnostics import health_summary
from app.domains.restore.model import RESTORE_STATUS_SUCCEEDED
from app.domains.restore.model import RestoreAttempt

logger = logging.getLogger("homez")


def expected_job_ids() -> list[str]:
    from app.domains.scheduler import jobs

    return [
        jobs.BACKUP_REHEARSAL_JOB_ID, jobs.RECALL_NOTICE_CHECK_JOB_ID, jobs.ORDER_COLLECTION_TICK_JOB_ID,
        jobs.DELIVERY_TRACKING_JOB_ID, jobs.AFTER_SALES_JOB_ID, jobs.PRODUCT_STATUS_JOB_ID,
    ]


def _to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value  # 이미 UTC naive로 저장된 시각


class HealthService:

    def __init__(
        self, db: Session, *, now_factory: Callable[[], datetime] | None = None, scheduler_getter: Callable[[], Any] | None = None,
        scheduler_enabled: bool | None = None, disk_path: Path | None = None, expected_jobs: list[str] | None = None,
    ):
        self.db = db
        self._now = now_factory or datetime.utcnow
        self._scheduler_getter = scheduler_getter
        self._scheduler_enabled = scheduler_enabled
        self._disk_path = disk_path
        self._expected_jobs = expected_jobs

    def _scheduler(self) -> tuple[bool | None, dict | None]:
        enabled = self._scheduler_enabled
        if enabled is None:
            try:
                from app.core.config import settings

                enabled = bool(settings.SCHEDULER_ENABLED)
            except Exception:  # noqa: BLE001
                return None, None
        try:
            getter = self._scheduler_getter
            if getter is None:
                from app.core.scheduler_service import SchedulerService

                getter = SchedulerService.get
            scheduler = getter()
            if scheduler is None:
                return enabled, None
            jobs = [{"id": job.id, "next_run": _to_utc_naive(getattr(job, "next_run_time", None))} for job in scheduler.get_jobs()]
            return enabled, {"running": bool(getattr(scheduler, "running", False)), "jobs": jobs}
        except Exception:  # noqa: BLE001 — 읽지 못하면 "알 수 없음"으로 둔다
            logger.warning("운영 상태 점검: 자동 작업 엔진을 읽지 못했다", exc_info=True)
            return None, None

    def _latest_backup(self) -> tuple[bool, datetime | None]:
        try:
            row = self.db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).first()
            return True, _to_utc_naive(row.created_at) if row else None
        except Exception:  # noqa: BLE001
            self.db.rollback()
            return False, None

    def _latest_rehearsal(self) -> tuple[bool, dict | None]:
        try:
            row = self.db.query(RestoreAttempt).order_by(RestoreAttempt.created_at.desc()).first()
            if row is None:
                return True, None
            return True, {"at": _to_utc_naive(row.created_at), "ok": row.status == RESTORE_STATUS_SUCCEEDED}
        except Exception:  # noqa: BLE001
            self.db.rollback()
            return False, None

    def _disk_free(self) -> int | None:
        try:
            path = self._disk_path
            if path is None:
                from app.database.session import get_engine_db_path

                path = get_engine_db_path()
            if path is None:
                return None
            target = Path(path)
            return int(shutil.disk_usage(target.parent if target.suffix else target).free)
        except Exception:  # noqa: BLE001
            return None

    def _estop(self) -> bool | None:
        try:
            from app.domains.automation_safety.service import SafetyService

            return bool(SafetyService(self.db).is_emergency_stop_active())
        except Exception:  # noqa: BLE001
            self.db.rollback()
            return None

    def snapshot(self) -> dict[str, Any]:
        enabled, scheduler = self._scheduler()
        backup_ok, latest_backup = self._latest_backup()
        rehearsal_ok, latest_rehearsal = self._latest_rehearsal()
        snap: dict[str, Any] = {
            "now": self._now(), "scheduler_enabled": enabled, "scheduler": scheduler,
            "expected_jobs": self._expected_jobs if self._expected_jobs is not None else expected_job_ids(),
            "rehearsal_known": rehearsal_ok, "latest_rehearsal": latest_rehearsal,
            "disk_free_bytes": self._disk_free(), "estop_active": self._estop(),
        }
        if backup_ok:
            snap["latest_backup_at"] = latest_backup
        return snap

    def summary(self) -> dict[str, Any]:
        snap = self.snapshot()
        result = health_summary.evaluate(snap)
        return {**result, "checked_at": snap["now"].isoformat(timespec="seconds")}
