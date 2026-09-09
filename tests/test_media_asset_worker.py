"""
=========================================================
Homez OS

File : tests/test_media_asset_worker.py

ImageGenerationJobWorker 검증(2026-08-02 CTO 보안 보완 Gate R5):
Provider 완료를 기다리지 않는 submit_job, Worker claim 동시성,
재시작 후 PENDING 복구, 정상 종료와 잔존 스레드 없음, 시작 시
정지된 RUNNING 일괄 복구, Fake Provider만 사용(외부 호출 0건).

실제 homez.db는 사용하지 않는다 — 임시 SQLite 파일 DB만 사용한다
(Worker는 스레드마다 새 Session을 열어야 하므로 :memory: DB가 아니라
실제 파일 DB가 필요하다).
=========================================================
"""

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.media_asset.constants import ImageJobStatus
from app.domains.media_asset.job_queue_service import (
    ImageGenerationJobQueueService,
)
from app.domains.media_asset.model import ImageGenerationDailyUsage
from app.domains.media_asset.model import ImageGenerationJob
from app.domains.media_asset.model import ImageGenerationResult
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.repository import MediaAssetRepository
from app.domains.media_asset.schema import ImageGenerationItemRequest
from app.domains.media_asset.schema import ImageGenerationJobSubmitRequest
from app.domains.media_asset.worker import ImageGenerationJobWorker
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

_COUNTER = 0


def _next_key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1

    return f"{prefix}-{_COUNTER}"


_TERMINAL_JOB_STATUSES = {
    ImageJobStatus.SUCCEEDED, ImageJobStatus.FAILED, ImageJobStatus.PARTIAL,
}


def _wait_until(predicate, timeout=5.0, interval=0.05):

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)

    return predicate()


class ImageGenerationJobWorkerTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(
            f"sqlite:///{path}", connect_args={"timeout": 15},
        )

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                MediaAsset.__table__,
                ImageGenerationJob.__table__,
                ImageGenerationResult.__table__,
                ImageGenerationDailyUsage.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )

        self.media_root = Path(tempfile.mkdtemp())

        setup_db = self.SessionLocal()
        try:
            self.company = Company(
                name="테스트 회사", business_number="111-11-11111",
                ceo="테스트", phone="02-000-0000",
                email="a@test.com", address="서울",
            )
            setup_db.add(self.company)
            setup_db.commit()
            self.company_id = self.company.id
        finally:
            setup_db.close()

        self.worker = None

    def tearDown(self):

        if self.worker is not None:
            self.worker.stop(timeout=5.0)

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _submit_pending_job(self, product_candidate_id=1, key=None):

        db = self.SessionLocal()
        try:
            service = ImageGenerationJobQueueService(db, media_root=self.media_root)
            req = ImageGenerationJobSubmitRequest(
                product_candidate_id=product_candidate_id,
                items=[ImageGenerationItemRequest(purpose="MAIN")],
                idempotency_key=key or _next_key("worker-job"),
            )
            job, _ = service.submit_job(req, 1, self.company_id)

            return job.id
        finally:
            db.close()

    def _get_job(self, job_id):

        db = self.SessionLocal()
        try:
            return (
                db.query(ImageGenerationJob)
                .filter(ImageGenerationJob.id == job_id)
                .first()
            )
        finally:
            db.close()

    # ----------------------------------------------------
    # 13. submit_job이 Provider 완료를 기다리지 않고 PENDING 반환
    #     (test_media_asset_job_queue.py에서 이미 검증 — 여기서는
    #     Worker가 그 PENDING을 실제로 처리하는지에 집중한다)
    # ----------------------------------------------------

    def test_worker_processes_pending_job_end_to_end(self):

        job_id = self._submit_pending_job()

        job_before = self._get_job(job_id)
        self.assertEqual(job_before.status, ImageJobStatus.PENDING)

        self.worker = ImageGenerationJobWorker(
            self.SessionLocal, media_root=self.media_root,
            poll_interval_seconds=0.05,
        )
        self.worker.start()

        completed = _wait_until(
            lambda: self._get_job(job_id).status in _TERMINAL_JOB_STATUSES,
            timeout=5.0,
        )
        self.assertTrue(completed, "Worker가 제한 시간 내에 Job을 처리하지 못함")

        final_job = self._get_job(job_id)
        self.assertEqual(final_job.status, ImageJobStatus.SUCCEEDED)

    # ----------------------------------------------------
    # 14. Worker claim 동시성 — 여러 Worker가 같은 Job을 동시에
    #     집으려 해도 정확히 한 번만 실행된다
    # ----------------------------------------------------

    def test_concurrent_workers_claim_each_job_exactly_once(self):

        job_ids = [self._submit_pending_job() for _ in range(6)]

        workers = [
            ImageGenerationJobWorker(
                self.SessionLocal, media_root=self.media_root,
                poll_interval_seconds=0.02,
            )
            for _ in range(3)
        ]

        try:
            for w in workers:
                w.start()

            all_done = _wait_until(
                lambda: all(
                    self._get_job(jid).status in _TERMINAL_JOB_STATUSES
                    for jid in job_ids
                ),
                timeout=10.0,
            )
            self.assertTrue(all_done, "일부 Job이 처리되지 않음")

        finally:
            for w in workers:
                w.stop(timeout=5.0)

        # 각 Job이 정확히 1건의 결과 세트만 가져야 한다(중복 실행되지
        # 않았음을 확인) — MediaAsset이 Job당 1개만 생성됐는지 확인.
        db = self.SessionLocal()
        try:
            repo = MediaAssetRepository(db)
            for jid in job_ids:
                job = self._get_job(jid)
                self.assertEqual(job.status, ImageJobStatus.SUCCEEDED)
                results = repo.list_results_for_job(jid, self.company_id)
                self.assertEqual(len(results), 1)
        finally:
            db.close()

    # ----------------------------------------------------
    # 15. 재시작 후 PENDING 복구 — Worker가 이미 존재하던 PENDING을
    #     시작하자마자 처리한다(별도 "복구" API 없이 폴링만으로 충분).
    # ----------------------------------------------------

    def test_worker_picks_up_pending_jobs_that_existed_before_start(self):

        job_id_1 = self._submit_pending_job(product_candidate_id=1)
        job_id_2 = self._submit_pending_job(product_candidate_id=2)

        # 이 시점에는 아직 Worker가 없다 — "앱 재시작 전" 상태를 재현.
        self.assertEqual(self._get_job(job_id_1).status, ImageJobStatus.PENDING)
        self.assertEqual(self._get_job(job_id_2).status, ImageJobStatus.PENDING)

        self.worker = ImageGenerationJobWorker(
            self.SessionLocal, media_root=self.media_root,
            poll_interval_seconds=0.05,
        )
        self.worker.start()

        both_done = _wait_until(
            lambda: (
                self._get_job(job_id_1).status in _TERMINAL_JOB_STATUSES
                and self._get_job(job_id_2).status in _TERMINAL_JOB_STATUSES
            ),
            timeout=5.0,
        )
        self.assertTrue(both_done)
        self.assertEqual(self._get_job(job_id_1).status, ImageJobStatus.SUCCEEDED)
        self.assertEqual(self._get_job(job_id_2).status, ImageJobStatus.SUCCEEDED)

    def test_worker_recovers_stalled_running_job_on_startup(self):
        """
        이전 Worker가 죽어 RUNNING인 채로 남은 Job은, 새 Worker가
        시작할 때 FAILED(TIMEOUT_RECOVERED)로 복구되어야 한다(안전하게
        재개할 수 없으므로 자동 재실행하지 않는다 — 사용자가 명시적
        retry_job()을 호출해야 한다).
        """

        job_id = self._submit_pending_job()

        db = self.SessionLocal()
        try:
            db.query(ImageGenerationJob).filter(
                ImageGenerationJob.id == job_id,
            ).update({
                "status": ImageJobStatus.RUNNING,
                "started_at": datetime.utcnow() - timedelta(hours=1),
            })
            db.commit()
        finally:
            db.close()

        self.worker = ImageGenerationJobWorker(
            self.SessionLocal, media_root=self.media_root,
            poll_interval_seconds=0.05, stall_timeout_seconds=300,
        )
        self.worker.start()

        recovered = _wait_until(
            lambda: self._get_job(job_id).status == ImageJobStatus.FAILED,
            timeout=5.0,
        )
        self.assertTrue(recovered)
        self.assertIn("TIMEOUT_RECOVERED", self._get_job(job_id).error_reason)

    # ----------------------------------------------------
    # 16. Worker 정상 종료와 잔존 스레드 없음
    # ----------------------------------------------------

    def test_worker_stop_leaves_no_running_thread(self):

        self.worker = ImageGenerationJobWorker(
            self.SessionLocal, media_root=self.media_root,
            poll_interval_seconds=0.05,
        )
        self.worker.start()
        self.assertTrue(self.worker.is_running())

        thread_ref = self.worker._thread  # noqa: SLF001

        stopped_cleanly = self.worker.stop(timeout=5.0)

        self.assertTrue(stopped_cleanly)
        self.assertFalse(self.worker.is_running())
        self.assertFalse(thread_ref.is_alive())

    def test_worker_start_stop_is_idempotent(self):

        self.worker = ImageGenerationJobWorker(
            self.SessionLocal, media_root=self.media_root,
            poll_interval_seconds=0.05,
        )
        self.worker.start()
        self.worker.start()  # 두 번째 start()는 아무 효과 없어야 함(멱등)
        self.assertTrue(self.worker.is_running())

        self.worker.stop()
        self.worker.stop()  # 두 번째 stop()도 안전해야 함
        self.assertFalse(self.worker.is_running())

    # ----------------------------------------------------
    # Worker 루프 자체는 예외에도 죽지 않는다
    # ----------------------------------------------------

    def test_worker_survives_exception_in_single_job_processing(self):

        from unittest import mock

        job_id = self._submit_pending_job()

        self.worker = ImageGenerationJobWorker(
            self.SessionLocal, media_root=self.media_root,
            poll_interval_seconds=0.05,
        )

        call_count = {"n": 0}
        original_process_one = self.worker._process_one  # noqa: SLF001

        def _flaky_process_one():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("시뮬레이션된 처리 중 예외")
            return original_process_one()

        with mock.patch.object(
            self.worker, "_process_one", side_effect=_flaky_process_one,
        ):
            self.worker.start()

            processed = _wait_until(
                lambda: self._get_job(job_id).status in _TERMINAL_JOB_STATUSES,
                timeout=5.0,
            )

        self.assertTrue(
            processed, "예외 발생 후에도 Worker가 계속 폴링하지 않음",
        )
        self.assertTrue(self.worker.is_running())

    # ----------------------------------------------------
    # 실제 외부 호출 0건 — FAKE Provider만 사용
    # ----------------------------------------------------

    def test_no_network_libraries_imported_in_worker_module(self):

        import app.domains.media_asset.worker as worker_mod

        with open(worker_mod.__file__, encoding="utf-8") as f:
            source = f.read()

        self.assertNotIn("import requests", source)
        self.assertNotIn("import httpx", source)
        self.assertNotIn("urllib.request", source)


if __name__ == "__main__":
    unittest.main()
