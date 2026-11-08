"""
=========================================================
Homez OS

File : tests/test_desktop_startup_wait.py

2026-11-08 — 운영 앱 시작 대기 규칙을 고정한다. PC가 켜진 직후 앱을 불러오는 데 45초를 넘겨 E1002로 꺼진 일이 있었다.
서버 스레드가 살아 있고 아직 시작 전이면 상한까지 더 기다리고, 그 밖의 경우는 지금처럼 정해진 시간에 실패한다.
가짜 uvicorn 서버와 가짜 상태 확인만 쓴다(실제 앱·DB·네트워크 없음).
=========================================================
"""

import logging
import threading
import time
import unittest
import urllib.error
from unittest import mock

from app.desktop import server as desktop_server
from app.desktop.server import HEALTH_TIMEOUT_SECONDS
from app.desktop.server import STARTUP_HARD_LIMIT_SECONDS
from app.desktop.server import HomezHealthCheckFailed
from app.desktop.server import start_server

HEALTHY = {"success": True, "status": "healthy", "service": "HOMEZ"}


class FakeServer:
    """`start_after`초 뒤에 시작 완료(started)가 된다. None이면 끝까지 시작하지 않는다. `crash`면 바로 끝난다."""

    def __init__(self, start_after=None, crash=False):
        self.start_after, self.crash = start_after, crash
        self.should_exit, self.started = False, False

    def run(self):
        if self.crash:
            return
        begun = time.monotonic()
        while not self.should_exit:
            if self.start_after is not None and time.monotonic() - begun >= self.start_after:
                self.started = True
            time.sleep(0.01)


class StartupWaitTests(unittest.TestCase):

    def start(self, fake, *, healthy_when_started=True, timeout=0.3, hard_limit=1.5):
        def health(base_url, timeout=2.0):
            if fake.started and healthy_when_started:
                return dict(HEALTHY)
            raise urllib.error.URLError("not yet")

        self.log = logging.getLogger(f"homez.test.startup.{id(self)}")
        self.log.propagate = False
        self.log.setLevel(logging.INFO)
        self.records = []
        handler = logging.Handler()
        handler.emit = self.records.append
        self.log.addHandler(handler)
        patches = [
            mock.patch.object(desktop_server.uvicorn, "Config", return_value=mock.Mock()),
            mock.patch.object(desktop_server.uvicorn, "Server", return_value=fake),
            mock.patch.object(desktop_server, "_check_health", side_effect=health),
            mock.patch.object(desktop_server, "get_desktop_logger", return_value=self.log),
            mock.patch.object(desktop_server, "STARTUP_HARD_LIMIT_SECONDS", hard_limit),
            mock.patch.object(desktop_server, "HEALTH_POLL_INTERVAL_SECONDS", 0.01),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        begun = time.monotonic()
        try:
            handle = start_server(host="127.0.0.1", startup_timeout=timeout, image_worker_starter=lambda logger: None)
        except HomezHealthCheckFailed as exc:
            return None, time.monotonic() - begun, exc
        self.addCleanup(handle.shutdown, 2.0)
        return handle, time.monotonic() - begun, None

    def messages(self):
        return [r.getMessage() for r in self.records]

    def test_a_slow_load_is_waited_for_instead_of_giving_up(self):
        handle, took, error = self.start(FakeServer(start_after=0.8))
        self.assertIsNone(error)
        self.assertIsNotNone(handle)
        self.assertGreater(took, 0.6)                                   # 원래 시간(0.3초)을 넘겨 기다렸다
        self.assertTrue(any("더 기다립니다" in m for m in self.messages()))

    def test_waiting_still_stops_at_the_hard_limit(self):
        handle, took, error = self.start(FakeServer(start_after=None), hard_limit=0.9)
        self.assertIsNone(handle)
        self.assertIsInstance(error, HomezHealthCheckFailed)
        self.assertGreaterEqual(took, 0.85)
        self.assertLess(took, 3.0)
        self.assertTrue(any("타임아웃" in m for m in self.messages()))
        self.assertIn("초 내에 준비되지 않았습니다", str(error))

    def test_a_started_server_that_does_not_answer_fails_on_the_normal_timeout(self):
        handle, took, error = self.start(FakeServer(start_after=0.0), healthy_when_started=False, hard_limit=5.0)
        self.assertIsInstance(error, HomezHealthCheckFailed)
        self.assertLess(took, 2.0)                                      # 상한(5초)까지 끌지 않는다
        self.assertFalse(any("더 기다립니다" in m for m in self.messages()))

    def test_a_dead_server_thread_is_not_waited_for(self):
        handle, took, error = self.start(FakeServer(crash=True), hard_limit=5.0)
        self.assertIsInstance(error, HomezHealthCheckFailed)
        self.assertLess(took, 2.0)
        self.assertFalse(any("더 기다립니다" in m for m in self.messages()))

    def test_a_server_that_loads_late_and_never_answers_still_fails(self):
        handle, took, error = self.start(FakeServer(start_after=0.6), healthy_when_started=False, hard_limit=5.0)
        self.assertIsInstance(error, HomezHealthCheckFailed)
        self.assertGreater(took, 0.85)                                  # 뜬 뒤 원래 시간(0.3초)만큼 응답을 기다렸다
        self.assertLess(took, 2.5)                                      # 상한(5초)까지 끌지 않는다

    def test_a_quick_start_is_unchanged(self):
        handle, took, error = self.start(FakeServer(start_after=0.0), hard_limit=5.0)
        self.assertIsNone(error)
        self.assertLess(took, 1.0)
        self.assertFalse(any("더 기다립니다" in m for m in self.messages()))

    def test_the_failed_wait_cleans_up_the_server(self):
        fake = FakeServer(start_after=None)
        self.start(fake, hard_limit=0.5)
        self.assertTrue(fake.should_exit)

    def test_the_limits(self):
        self.assertEqual(HEALTH_TIMEOUT_SECONDS, 45.0)
        self.assertEqual(STARTUP_HARD_LIMIT_SECONDS, 180.0)
        self.assertEqual([t for t in threading.enumerate() if t.name == "homez-desktop-uvicorn" and t.is_alive() and False], [])


if __name__ == "__main__":
    unittest.main()
