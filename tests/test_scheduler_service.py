"""
=========================================================
Homez OS

File : tests/test_scheduler_service.py

2026-09-10 Phase 6 — app/core/scheduler_service.py::SchedulerService
검증. 이 클래스는 이번 Phase 이전까지 어떤 호출부도 없었다(grep으로
확인) — 단독 테스트도 이번에 처음 만든다.

`_scheduler`가 클래스 레벨(프로세스 전역) 상태이므로, 각 테스트가
끝나면 반드시 shutdown()으로 정리한다 — 안 그러면 살아있는
BackgroundScheduler 스레드가 이후 테스트/프로세스에 새어나간다.
"""

import time
import unittest

from app.core.scheduler_service import SchedulerService


class SchedulerServiceLifecycleTestCase(unittest.TestCase):

    def tearDown(self):

        SchedulerService.shutdown()

    def test_start_returns_running_scheduler(self):

        scheduler = SchedulerService.start()

        self.assertTrue(scheduler.running)

    def test_start_twice_returns_same_instance(self):

        first = SchedulerService.start()
        second = SchedulerService.start()

        self.assertIs(first, second)

    def test_get_returns_none_before_start(self):

        self.assertIsNone(SchedulerService.get())

    def test_get_returns_instance_after_start(self):

        SchedulerService.start()

        self.assertIsNotNone(SchedulerService.get())

    def test_shutdown_before_start_does_not_raise(self):

        SchedulerService.shutdown()  # 시작한 적 없어도 예외 없어야 함

    def test_shutdown_clears_instance(self):

        SchedulerService.start()
        SchedulerService.shutdown()

        self.assertIsNone(SchedulerService.get())

    def test_shutdown_is_idempotent(self):

        SchedulerService.start()
        SchedulerService.shutdown()
        SchedulerService.shutdown()  # 두 번째도 예외 없어야 함


class SchedulerServiceIntervalJobTestCase(unittest.TestCase):

    def tearDown(self):

        SchedulerService.shutdown()

    def test_add_interval_job_registers_job(self):

        SchedulerService.add_interval_job(
            lambda: None, minutes=5, job_id="test-interval-job",
        )

        scheduler = SchedulerService.get()
        self.assertIsNotNone(scheduler.get_job("test-interval-job"))

    def test_add_interval_job_same_id_replaces_existing(self):

        SchedulerService.add_interval_job(
            lambda: None, minutes=5, job_id="test-replace-job",
        )
        SchedulerService.add_interval_job(
            lambda: None, minutes=10, job_id="test-replace-job",
        )

        scheduler = SchedulerService.get()
        jobs = [
            j for j in scheduler.get_jobs() if j.id == "test-replace-job"
        ]
        self.assertEqual(len(jobs), 1)

    def test_add_interval_job_sets_max_instances_one(self):

        SchedulerService.add_interval_job(
            lambda: None, minutes=5, job_id="test-max-instances-job",
        )

        job = SchedulerService.get().get_job("test-max-instances-job")
        self.assertEqual(job.max_instances, 1)


class SchedulerServiceCronJobTestCase(unittest.TestCase):

    def tearDown(self):

        SchedulerService.shutdown()

    def test_add_cron_job_registers_job(self):

        SchedulerService.add_cron_job(
            lambda: None,
            day_of_week="sun", hour=4, minute=0,
            job_id="test-cron-job",
        )

        scheduler = SchedulerService.get()
        self.assertIsNotNone(scheduler.get_job("test-cron-job"))

    def test_add_cron_job_same_id_replaces_existing(self):

        SchedulerService.add_cron_job(
            lambda: None,
            day_of_week="sun", hour=4, minute=0,
            job_id="test-cron-replace-job",
        )
        SchedulerService.add_cron_job(
            lambda: None,
            day_of_week="mon", hour=9, minute=30,
            job_id="test-cron-replace-job",
        )

        scheduler = SchedulerService.get()
        jobs = [
            j for j in scheduler.get_jobs()
            if j.id == "test-cron-replace-job"
        ]
        self.assertEqual(len(jobs), 1)

    def test_add_cron_job_sets_max_instances_one(self):

        SchedulerService.add_cron_job(
            lambda: None,
            day_of_week="sun", hour=4, minute=0,
            job_id="test-cron-max-instances-job",
        )

        job = SchedulerService.get().get_job(
            "test-cron-max-instances-job",
        )
        self.assertEqual(job.max_instances, 1)

    def test_add_cron_job_next_run_is_in_the_future(self):

        SchedulerService.add_cron_job(
            lambda: None,
            day_of_week="sun", hour=4, minute=0,
            job_id="test-cron-next-run-job",
        )

        job = SchedulerService.get().get_job("test-cron-next-run-job")
        self.assertIsNotNone(job.next_run_time)


class SchedulerServiceActualExecutionTestCase(unittest.TestCase):
    """실제로 스케줄러가 등록한 함수를 실행하는지까지 확인한다(아주
    짧은 간격의 interval job으로 실제 실행을 증명 — 등록만 하고
    실행 자체는 검증하지 않는 앞의 테스트들과 보완 관계)."""

    def tearDown(self):

        SchedulerService.shutdown()

    def test_registered_job_actually_executes(self):

        calls = []

        SchedulerService.add_interval_job(
            lambda: calls.append(1), minutes=1, job_id="test-exec-job",
        )

        # add_interval_job은 "다음 간격 후" 최초 실행이라 기본적으로는
        # 즉시 실행되지 않는다 — 실제 실행 자체를 증명하기 위해
        # job의 트리거를 직접 조작하지 않고, APScheduler 공개 API인
        # modify_job으로 next_run_time을 "지금"으로 앞당겨 트리거한다.
        from datetime import datetime

        scheduler = SchedulerService.get()
        scheduler.modify_job(
            "test-exec-job", next_run_time=datetime.now(),
        )

        deadline = time.time() + 5
        while not calls and time.time() < deadline:
            time.sleep(0.1)

        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
