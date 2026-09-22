"""
=========================================================
Homez OS

File : tests/test_homez_desktop.py

HOMEZ Desktop Shell (app/desktop/**) 검증.

실제 PyWebView 창(GUI)을 자동 생성하는 테스트는 이 파일에 포함하지
않는다 — 이 세션의 GUI 자동화 한계는 완료 보고서에 별도로 명시한다.
대신 서버 수명주기·단일 인스턴스·health 식별·로그 redaction 등 GUI
없이 검증 가능한 부분을 실제로 동작시켜 확인한다.

homez.db는 사용하지 않는다(실제 서버가 app.main:app을 그대로 띄우지만
/health는 DB를 조회하지 않으므로 안전하다 — DB 미변경은 테스트로
재확인한다). run()이 이제 시작 시 bootstrap_environment()를 호출하므로,
RunOrchestrationTestCase의 모든 테스트는 이 함수도 mock해 실제
homez.db를 절대 열지 않는다(2026-08-02).

2026-08-02 Gate R6-C: start_server()가 실제 SessionLocal 기반 Image
Generation Worker를 시작하므로, 실제 서버를 띄우는 모든 테스트
(StartServerTestCase)는 `image_worker_starter=`에 `_FakeImageWorker`를
주입해 실제 homez.db/media 저장소에 전혀 접근하지 않는다 — 이전에는
이 주입 지점이 없어 PENDING Job이 우연히 없어서 쓰기가 없었을 뿐,
구조적으로 격리돼 있지 않았다.
=========================================================
"""

import http.server
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.bootstrap import BootstrapResult
from app.database.migration_runner import ChecksumMismatchError
from app.desktop.paths import get_assets_dir
from app.desktop.paths import get_media_dir
from app.desktop.paths import get_repo_root
from app.desktop.server import DesktopServerHandle
from app.desktop.server import HEALTH_TIMEOUT_SECONDS
from app.desktop.server import HomezHealthCheckFailed
from app.desktop.server import PortIsOtherServiceError
from app.desktop.server import find_free_port
from app.desktop.server import is_homez_health_response
from app.desktop.server import start_server
from app.desktop.single_instance import SingleInstanceGuard

_NOOP_BOOTSTRAP_RESULT = BootstrapResult(is_new_install=False)

HOMEZ_DB_PATH = get_repo_root() / "homez.db"
HOMEZ_MEDIA_DIR = get_media_dir()


def _db_snapshot():

    if not HOMEZ_DB_PATH.exists():
        return None

    stat = HOMEZ_DB_PATH.stat()
    return stat.st_size, stat.st_mtime


def _media_snapshot():
    """실제 media 디렉터리 파일 목록(경로+크기) — Worker가 이미지를
    쓰지 않았음을 확인하는 데 쓴다."""

    if not HOMEZ_MEDIA_DIR.exists():
        return frozenset()

    return frozenset(
        (str(p.relative_to(HOMEZ_MEDIA_DIR)), p.stat().st_size)
        for p in HOMEZ_MEDIA_DIR.rglob("*") if p.is_file()
    )


class _FakeImageWorker:
    """
    2026-08-02 Gate R6-C — 실제 ImageGenerationJobWorker와 동일한
    공개 인터페이스(start/stop/is_running)만 흉내내는 테스트 더블.
    실제 DB/Session/스레드에 전혀 접근하지 않는다 — start()/stop()
    호출 여부와 stop() timeout 인자만 기록한다.
    """

    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0
        self.stop_timeouts = []

    def start(self):
        self.start_calls += 1

    def stop(self, timeout=10.0):
        self.stop_calls += 1
        self.stop_timeouts.append(timeout)

        return True

    def is_running(self):
        return self.start_calls > self.stop_calls


def _fake_image_worker_starter_returning(fake_worker):
    """`image_worker_starter=` 자리에 넘길 콜러블 — 실제
    `_start_image_generation_worker`와 동일하게 "만들고 start()까지
    호출한 뒤 반환"하는 계약을 그대로 지킨다(start()는 server.py가
    호출하는 게 아니라 starter 자신이 호출한다). 항상 같은
    `fake_worker` 인스턴스를 반환해 테스트가 start/stop 호출 여부를
    그 인스턴스에서 바로 확인할 수 있게 한다."""

    def _starter(logger):
        fake_worker.start()

        return fake_worker

    return _starter


class FindFreePortTestCase(unittest.TestCase):

    def test_find_free_port_returns_bindable_loopback_port(self):

        port = find_free_port("127.0.0.1")

        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)

        # 반환된 포트가 실제로 다시 바인드 가능해야 한다(즉시 재사용 가능).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))

    def test_find_free_port_returns_different_ports_across_calls(self):

        ports = {find_free_port("127.0.0.1") for _ in range(5)}

        # OS가 매번 동일 포트를 재사용할 수도 있으나(TIME_WAIT 회피 등)
        # 일반적으로는 서로 다른 임시 포트를 받는다 — 최소 2개 이상
        # 서로 다른 값이 나오는지만 느슨하게 확인한다.
        self.assertGreaterEqual(len(ports), 2)


class HealthResponseValidationTestCase(unittest.TestCase):

    def test_valid_homez_payload_passes(self):

        self.assertTrue(
            is_homez_health_response(
                {"success": True, "status": "healthy", "service": "HOMEZ"},
            ),
        )

    def test_missing_service_identifier_fails(self):

        self.assertFalse(
            is_homez_health_response({"success": True, "status": "healthy"}),
        )

    def test_wrong_service_identifier_fails(self):

        self.assertFalse(
            is_homez_health_response(
                {
                    "success": True, "status": "healthy",
                    "service": "SOME_OTHER_APP",
                },
            ),
        )

    def test_wrong_status_fails(self):

        self.assertFalse(
            is_homez_health_response(
                {"success": True, "status": "degraded", "service": "HOMEZ"},
            ),
        )

    def test_success_false_fails(self):

        self.assertFalse(
            is_homez_health_response(
                {"success": False, "status": "healthy", "service": "HOMEZ"},
            ),
        )

    def test_empty_payload_fails(self):

        self.assertFalse(is_homez_health_response({}))


class _FakeOtherServiceHandler(http.server.BaseHTTPRequestHandler):
    """HOMEZ가 아닌 다른 서비스를 흉내낸다(success/status는 있지만 service 없음)."""

    def do_GET(self):  # noqa: N802

        body = b'{"success": true, "status": "healthy"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002

        pass  # 테스트 출력 오염 방지


def _bind_fake_other_service_server(
    host: str = "127.0.0.1", attempts: int = 5,
) -> tuple[http.server.HTTPServer, int]:
    """
    2026-08-30 V7 후속 안정화 — find_free_port()의 docstring이 이미
    명시한 알려진 경쟁(포트 확인과 실제 bind 사이의 짧은 창)이 전체
    회귀 실행 중 실제로 두 차례 관측됐다(WinError 10048). 호출자가
    재시도하라는 그 문서의 지침을 그대로 따른다 — 매번 새로 포트를
    확인해 바인드까지 성공한 소켓만 반환한다.
    """

    last_error: OSError | None = None
    for _ in range(attempts):
        port = find_free_port(host)
        try:
            return (
                http.server.HTTPServer((host, port), _FakeOtherServiceHandler),
                port,
            )
        except OSError as exc:
            last_error = exc
    raise last_error


class StartServerTestCase(unittest.TestCase):

    def test_default_startup_timeout_covers_measured_packaged_cold_start(self):
        """The clean packaged app measured 26.7s on the target Windows PC."""

        self.assertGreaterEqual(HEALTH_TIMEOUT_SECONDS, 45.0)

    def test_start_server_rejects_non_loopback_host(self):

        with self.assertRaises(ValueError):
            start_server(host="0.0.0.0")

    def test_start_server_rejects_other_service_on_port(self):
        """
        지정된 포트에 이미 HOMEZ가 아닌 서비스가 떠 있으면(응답은 오지만
        service=="HOMEZ" 식별자가 없음), 포트가 열려 있다는 이유만으로
        HOMEZ로 오인하지 않고 PortIsOtherServiceError를 던져야 한다.
        """

        fake_server, fake_port = _bind_fake_other_service_server("127.0.0.1")
        fake_thread = threading.Thread(
            target=fake_server.serve_forever, daemon=True,
        )
        fake_thread.start()

        try:
            with self.assertRaises(PortIsOtherServiceError):
                start_server(
                    host="127.0.0.1", port=fake_port, startup_timeout=5.0,
                )
        finally:
            fake_server.shutdown()
            fake_server.server_close()
            fake_thread.join(timeout=5)

    def test_start_server_times_out_when_nothing_responds(self):
        """
        아무 것도(진짜 HOMEZ도, 다른 서비스도) 응답하지 않는 상황을
        시뮬레이션한다 — 실제 uvicorn을 실행하지 않도록 존재하지 않을
        모듈 경로를 넘겨 서버 스레드가 즉시 죽게 만들고, 짧은
        startup_timeout 내에 HomezHealthCheckFailed로 안전하게
        실패하는지 확인한다.
        """

        with mock.patch(
            "app.desktop.server.uvicorn.Config",
            side_effect=lambda *a, **kw: mock.Mock(
                **{"setup_event_loop.side_effect": Exception("boom")},
            ),
        ):
            with self.assertRaises(HomezHealthCheckFailed):
                start_server(host="127.0.0.1", startup_timeout=1.5)

    def test_successful_start_and_shutdown_full_cycle(self):

        before_db = _db_snapshot()
        before_media = _media_snapshot()
        fake_worker = _FakeImageWorker()

        with mock.patch(
            "app.database.session.SessionLocal",
        ) as mock_session_local:
            handle = start_server(
                host="127.0.0.1", startup_timeout=15.0,
                image_worker_starter=_fake_image_worker_starter_returning(fake_worker),
            )

            try:
                self.assertIsInstance(handle, DesktopServerHandle)
                self.assertEqual(handle.host, "127.0.0.1")
                self.assertIs(handle.image_worker, fake_worker)
                self.assertEqual(fake_worker.start_calls, 1)

                request = urllib.request.Request(f"{handle.base_url}/health")
                with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
                    self.assertEqual(response.status, 200)

                # 스키마 미적용 상태에서도 Console 화면 자체는 로드되어야 한다
                # (/console은 정적 HTML — DB/Migration 상태와 무관).
                console_request = urllib.request.Request(
                    f"{handle.base_url}/console",
                )
                with urllib.request.urlopen(console_request, timeout=3) as resp:  # noqa: S310
                    self.assertEqual(resp.status, 200)

            finally:
                stopped = handle.shutdown(timeout=10)
                self.assertTrue(stopped, "서버가 제한 시간 내에 종료되지 않았습니다.")

            # Fake Worker를 썼으므로 실제 SessionLocal은 단 한 번도
            # 호출되지 않아야 한다(app.main 자체의 요청 처리 경로가
            # SessionLocal을 쓸 수는 있으나, 이 테스트는 /health와
            # /console만 호출했고 둘 다 DB에 접근하지 않는다).
            mock_session_local.assert_not_called()

        self.assertEqual(fake_worker.stop_calls, 1)
        self.assertFalse(fake_worker.is_running())

        # 종료 후에는 더 이상 응답하지 않아야 한다.
        time.sleep(0.3)
        with self.assertRaises((urllib.error.URLError, ConnectionError)):
            urllib.request.urlopen(  # noqa: S310
                f"{handle.base_url}/health", timeout=2,
            )

        after_db = _db_snapshot()
        self.assertEqual(
            before_db, after_db,
            "서버 시작/health 확인//console 로드/종료 과정에서 homez.db가 "
            "변경되었습니다.",
        )
        after_media = _media_snapshot()
        self.assertEqual(
            before_media, after_media,
            "서버 시작/종료 과정에서 media 저장소 파일이 변경되었습니다.",
        )

    def test_shutdown_only_stops_its_own_server(self):
        """
        서로 다른 두 DesktopServerHandle 중 하나를 종료해도 다른 하나는
        영향받지 않는다 — shutdown()이 오직 자신이 보관한 server/thread
        만 대상으로 한다는 것을 실제로 확인한다. 각자 독립된 Fake
        Worker를 주입해 두 서버의 start/stop이 서로 섞이지 않는지도
        함께 확인한다.
        """

        fake_worker_a = _FakeImageWorker()
        fake_worker_b = _FakeImageWorker()

        handle_a = start_server(
            host="127.0.0.1", startup_timeout=15.0,
            image_worker_starter=_fake_image_worker_starter_returning(fake_worker_a),
        )
        handle_b = start_server(
            host="127.0.0.1", startup_timeout=15.0,
            image_worker_starter=_fake_image_worker_starter_returning(fake_worker_b),
        )

        try:
            handle_a.shutdown(timeout=10)

            time.sleep(0.3)

            with self.assertRaises((urllib.error.URLError, ConnectionError)):
                urllib.request.urlopen(  # noqa: S310
                    f"{handle_a.base_url}/health", timeout=2,
                )

            self.assertEqual(fake_worker_a.stop_calls, 1)
            self.assertEqual(fake_worker_b.stop_calls, 0)

            # handle_b는 여전히 살아있어야 한다.
            request_b = urllib.request.Request(f"{handle_b.base_url}/health")
            with urllib.request.urlopen(request_b, timeout=3) as response:  # noqa: S310
                self.assertEqual(response.status, 200)

        finally:
            handle_b.shutdown(timeout=10)

        self.assertEqual(fake_worker_a.start_calls, 1)
        self.assertEqual(fake_worker_b.start_calls, 1)
        self.assertEqual(fake_worker_b.stop_calls, 1)

    def test_log_file_never_contains_session_token(self):

        fake_worker = _FakeImageWorker()

        handle = start_server(
            host="127.0.0.1", startup_timeout=15.0,
            image_worker_starter=_fake_image_worker_starter_returning(fake_worker),
        )

        try:
            token = handle.session_token
            self.assertTrue(len(token) > 10)

            log_path = None
            for h in handle.logger.handlers:
                if hasattr(h, "baseFilename"):
                    log_path = h.baseFilename
                    break

            self.assertIsNotNone(log_path, "로그 파일 핸들러를 찾을 수 없습니다.")

            with open(log_path, encoding="utf-8") as f:
                log_content = f.read()

            self.assertNotIn(
                token, log_content,
                "세션 토큰이 로그 파일에 그대로 기록되었습니다.",
            )

        finally:
            handle.shutdown(timeout=10)

    def test_worker_not_started_when_server_start_fails(self):
        """
        서버 기동 자체가 실패하면(포트 충돌) Worker는 아예 시작 시도조차
        하지 않아야 한다.
        """

        fake_worker = _FakeImageWorker()
        starter = _fake_image_worker_starter_returning(fake_worker)

        fake_server, fake_port = _bind_fake_other_service_server("127.0.0.1")
        fake_thread = threading.Thread(
            target=fake_server.serve_forever, daemon=True,
        )
        fake_thread.start()

        try:
            with self.assertRaises(PortIsOtherServiceError):
                start_server(
                    host="127.0.0.1", port=fake_port, startup_timeout=5.0,
                    image_worker_starter=starter,
                )
        finally:
            fake_server.shutdown()
            fake_server.server_close()
            fake_thread.join(timeout=5)

        self.assertEqual(fake_worker.start_calls, 0)
        self.assertEqual(fake_worker.stop_calls, 0)

    def test_default_image_worker_starter_failure_does_not_crash_server(self):
        """
        운영 기본 경로(`_start_image_generation_worker`) 자체가 내부에서
        예외를 흡수해 None을 반환하는지 — Worker 시작 실패가 서버 기동
        자체를 막지 않아야 한다는 정책의 유닛 테스트.
        """

        from app.desktop.server import _start_image_generation_worker

        fake_logger = mock.Mock()

        with mock.patch(
            "app.domains.media_asset.worker.ImageGenerationJobWorker",
            side_effect=RuntimeError("시뮬레이션된 Worker 생성 실패"),
        ):
            result = _start_image_generation_worker(fake_logger)

        self.assertIsNone(result)
        fake_logger.exception.assert_called_once()


class SingleInstanceGuardTestCase(unittest.TestCase):

    def test_second_acquire_is_blocked_while_first_holds(self):

        name = f"Global\\HOMEZ_Test_Mutex_{os.getpid()}_1"

        guard_a = SingleInstanceGuard(name=name)
        guard_b = SingleInstanceGuard(name=name)

        try:
            self.assertTrue(guard_a.acquire())
            self.assertFalse(guard_a.is_already_running)

            self.assertFalse(guard_b.acquire())
            self.assertTrue(guard_b.is_already_running)

        finally:
            guard_a.release()
            guard_b.release()

    def test_acquire_succeeds_again_after_release(self):

        name = f"Global\\HOMEZ_Test_Mutex_{os.getpid()}_2"

        guard_a = SingleInstanceGuard(name=name)
        self.assertTrue(guard_a.acquire())
        guard_a.release()

        guard_b = SingleInstanceGuard(name=name)
        try:
            self.assertTrue(guard_b.acquire())
        finally:
            guard_b.release()

    def test_context_manager_releases_on_exit(self):

        name = f"Global\\HOMEZ_Test_Mutex_{os.getpid()}_3"

        with SingleInstanceGuard(name=name) as guard:
            self.assertFalse(guard.is_already_running)

        guard_again = SingleInstanceGuard(name=name)
        try:
            self.assertTrue(guard_again.acquire())
        finally:
            guard_again.release()


class RunOrchestrationTestCase(unittest.TestCase):
    """
    app.desktop.main.run()의 분기 로직을 실제 GUI 없이(mock으로) 검증한다.

    2026-07-30: 이 클래스의 테스트들은 (mock되지 않은) 실제 run()을
    호출하므로, run() 내부의 log_lifecycle_event()도 실제로 실행돼
    실제 사용자의 %LOCALAPPDATA%\\HOMEZ\\logs\\homez-desktop.log에 테스트
    잡음을 남기고 있었다(실제 재현·확인된 문제) — LOCALAPPDATA를
    테스트 전용 임시 디렉터리로 오버라이드해 실제 로그와 분리한다.
    """

    def setUp(self):

        self._tmp_local_appdata = tempfile.mkdtemp()
        self._env_patcher = mock.patch.dict(
            os.environ, {"LOCALAPPDATA": self._tmp_local_appdata},
        )
        self._env_patcher.start()

    def tearDown(self):

        self._env_patcher.stop()
        shutil.rmtree(self._tmp_local_appdata, ignore_errors=True)

    def _patch_bootstrap_success_isolation(self, desktop_main):
        """
        V7 Live Gate 4 재작업(2026-08-16) — run()이 이제 bootstrap 성공
        직후 DB 경로 일치 확인(get_homez_db_path/get_engine_db_path)과
        기준 데이터 시딩(seed_environment)을 추가로 수행한다.
        bootstrap_environment mock만으로는 더 이상 실제 homez.db 격리가
        보장되지 않으므로(개발 모드에서 get_homez_db_path()는 항상
        저장소 루트의 실제 homez.db를 계산한다 — LOCALAPPDATA 오버라이드는
        frozen 모드에만 영향), 이 세 지점도 함께 mock해 실제 DB를 절대
        열지 않게 한다. 두 경로 mock을 동일한 가짜 경로로 맞춰 두어
        run()의 fail-fast 불일치 검사를 통과시킨다(정상 진행 경로 검증이
        목적인 테스트이므로).
        """

        # main.py의 실제 코드는 get_homez_db_path(confirm=True).resolve()와
        # get_engine_db_path()(내부에서 이미 .resolve()된 값을 반환)를
        # 비교한다 — 두 mock 모두 이미 resolve()된 값을 줘야 한다.
        # 그렇지 않으면 tempfile.mkdtemp()가 반환하는 8.3 단축 경로
        # (예: DAUMPC~1)와 resolve() 후 긴 경로(Daum pc)가 서로 달라져
        # 이 테스트들이 "정상 진행" 대신 매번 DB 경로 불일치로 잘못
        # 조기 종료하는 실제 회귀를 직접 재현·확인했다(2026-08-16).
        fake_db_path = (
            Path(self._tmp_local_appdata) / "fake_official.db"
        ).resolve()

        patches = [
            mock.patch.object(
                desktop_main, "get_homez_db_path",
                return_value=fake_db_path,
            ),
            mock.patch.object(
                desktop_main, "get_engine_db_path",
                return_value=fake_db_path,
            ),
            mock.patch.object(
                desktop_main, "seed_environment",
                return_value={"roles_added": 0, "permissions_added": 0},
            ),
            # Audit(2026-08-21, CTO 후속 지시) — 채널 정책 카탈로그도
            # 이제 부팅마다 자동 시딩된다(seed_environment 바로 다음
            # 단계) — 이 fake_db_path에는 실제 channel_policy_rules
            # 테이블이 없으므로(Migration을 실행하지 않는 격리 테스트),
            # mock하지 않으면 이 단계에서 진짜 sqlite 오류로 실패한다.
            mock.patch.object(
                desktop_main, "seed_channel_policy_catalog_at_boot",
                return_value={"rules_seeded": 0},
            ),
            # 2026-09-22 격리 — run()은 시작할 때 이전 콘솔 로그인 세션을 지운다
            # (DesktopConsoleSessionStore.clear → 저장소 read·delete). 교체하지 않으면 이 테스트들이
            # 실제 Windows Credential Manager를 읽고 삭제한다. 인메모리 저장소로 교체한다.
            mock.patch.object(
                desktop_main, "WindowsCredentialStore", InMemoryCredentialStore,
            ),
        ]

        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_run_short_circuits_when_already_running(self):

        import app.desktop.main as desktop_main

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = False

            with mock.patch.object(
                desktop_main, "start_server",
            ) as start_server_mock:
                with mock.patch.object(
                    desktop_main, "_try_focus_existing_window",
                ):
                    exit_code = desktop_main.run()

            start_server_mock.assert_not_called()
            self.assertEqual(exit_code, 0)

    def test_run_does_not_open_window_when_server_fails(self):

        import app.desktop.main as desktop_main

        self._patch_bootstrap_success_isolation(desktop_main)

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = True

            with mock.patch.object(
                desktop_main, "bootstrap_environment",
                return_value=_NOOP_BOOTSTRAP_RESULT,
            ):
                with mock.patch.object(
                    desktop_main, "start_server",
                    side_effect=HomezHealthCheckFailed("테스트 강제 실패"),
                ):
                    with mock.patch("webview.create_window") as create_window_mock:
                        with mock.patch.object(
                            desktop_main, "_show_error_message_box",
                        ) as message_box_mock:
                            exit_code = desktop_main.run()

            create_window_mock.assert_not_called()
            self.assertEqual(exit_code, 1)
            guard_instance.release.assert_called_once()
            # pythonw.exe(콘솔 없음)에서도 오류가 조용히 사라지지 않도록
            # 메시지 박스가 호출돼야 한다(2026-07-30).
            message_box_mock.assert_called_once()

    def test_run_shows_message_box_on_port_conflict(self):

        import app.desktop.main as desktop_main

        self._patch_bootstrap_success_isolation(desktop_main)

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = True

            with mock.patch.object(
                desktop_main, "bootstrap_environment",
                return_value=_NOOP_BOOTSTRAP_RESULT,
            ):
                with mock.patch.object(
                    desktop_main, "start_server",
                    side_effect=PortIsOtherServiceError("테스트 포트 충돌"),
                ):
                    with mock.patch.object(
                        desktop_main, "_show_error_message_box",
                    ) as message_box_mock:
                        exit_code = desktop_main.run()

            self.assertEqual(exit_code, 1)
            message_box_mock.assert_called_once()
            # 원본 예외 메시지가 아니라 오류 코드만 전달돼야 한다.
            args, _ = message_box_mock.call_args
            self.assertEqual(args[0], desktop_main.ERROR_CODE_PORT_CONFLICT)
            self.assertNotIn("테스트 포트 충돌", args[0])

    def test_run_shuts_down_server_when_window_creation_fails(self):

        import app.desktop.main as desktop_main

        self._patch_bootstrap_success_isolation(desktop_main)

        fake_handle = mock.Mock()

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = True

            with mock.patch.object(
                desktop_main, "bootstrap_environment",
                return_value=_NOOP_BOOTSTRAP_RESULT,
            ):
                with mock.patch.object(
                    desktop_main, "start_server", return_value=fake_handle,
                ):
                    with mock.patch(
                        "webview.create_window",
                        side_effect=RuntimeError("창 생성 실패 시뮬레이션"),
                    ):
                        with mock.patch.object(
                            desktop_main, "_show_error_message_box",
                        ) as message_box_mock:
                            exit_code = desktop_main.run()

            fake_handle.shutdown.assert_called_once()
            guard_instance.release.assert_called_once()
            self.assertEqual(exit_code, 1)
            message_box_mock.assert_called_once()
            # 원본 예외(RuntimeError) 메시지나 traceback이 아니라 고정된
            # 안전 문구만 사용자에게 표시돼야 한다.
            args, _ = message_box_mock.call_args
            self.assertNotIn("창 생성 실패 시뮬레이션", args[0])

    def test_run_fails_closed_when_bootstrap_raises(self):
        """
        DB 부트스트랩(Migration Runner)이 checksum 불일치 등으로 실패하면
        서버를 아예 기동하지 않아야 한다(fail-closed) — Model과 불일치할
        수 있는 DB 위에서 서버가 뜨는 것을 막는다.
        """

        import app.desktop.main as desktop_main

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = True

            with mock.patch.object(
                desktop_main, "bootstrap_environment",
                side_effect=ChecksumMismatchError("테스트 checksum 불일치"),
            ):
                with mock.patch.object(
                    desktop_main, "start_server",
                ) as start_server_mock:
                    with mock.patch.object(
                        desktop_main, "_show_error_message_box",
                    ) as message_box_mock:
                        exit_code = desktop_main.run()

            start_server_mock.assert_not_called()
            self.assertEqual(exit_code, 1)
            guard_instance.release.assert_called_once()
            message_box_mock.assert_called_once()
            args, _ = message_box_mock.call_args
            self.assertEqual(args[0], desktop_main.ERROR_CODE_BOOTSTRAP_FAILED)
            self.assertNotIn("테스트 checksum 불일치", args[0])

    def test_message_box_helper_never_raises_even_if_user32_fails(self):

        import app.desktop.main as desktop_main

        with mock.patch(
            "ctypes.windll.user32.MessageBoxW", side_effect=OSError("simulated"),
        ):
            try:
                desktop_main._show_error_message_box("아무 메시지")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_show_error_message_box가 예외를 던졌습니다: {exc}")


class DesktopLauncherScriptTestCase(unittest.TestCase):
    """scripts/start_homez_desktop.cmd + 바로가기 대상 변경 검증."""

    def test_start_homez_desktop_cmd_is_pure_ascii(self):

        path = get_repo_root() / "scripts" / "start_homez_desktop.cmd"
        data = path.read_bytes()

        try:
            data.decode("ascii")
        except UnicodeDecodeError as exc:
            self.fail(f"start_homez_desktop.cmd에 비ASCII 바이트가 있습니다: {exc}")

    def test_start_homez_desktop_cmd_invokes_desktop_main(self):

        path = get_repo_root() / "scripts" / "start_homez_desktop.cmd"
        content = path.read_text(encoding="ascii")

        self.assertIn("app.desktop.main", content)
        self.assertIn("venv\\Scripts\\python.exe", content)

    def test_shortcut_script_targets_hidden_vbs_via_wscript(self):
        """
        2026-07-30: 바로가기가 .cmd를 직접 가리키면 cmd.exe가 항상 검은
        콘솔 창을 띄운다 — TargetPath가 wscript.exe이고 Arguments가
        start_homez_desktop.vbs를 가리키는지 확인한다(콘솔 없는 숨김
        실행). 브라우저 기반 런처(start_homez.cmd)로 되돌아가지
        않았는지도 함께 확인한다.
        """

        path = get_repo_root() / "scripts" / "create_homez_shortcut.ps1"
        content = path.read_text(encoding="utf-8-sig")

        self.assertIn("start_homez_desktop.vbs", content)
        self.assertIn("wscript.exe", content)
        self.assertIn('$Shortcut.TargetPath = $WscriptPath', content)
        self.assertNotIn('$Shortcut.TargetPath = $TargetCmd', content)
        self.assertNotIn('"scripts\\start_homez.cmd"', content)

    def test_desktop_cmd_still_exists_for_dev_debugging(self):
        """개발/디버그용 콘솔 실행 경로는 삭제하지 않고 그대로 유지한다."""

        path = get_repo_root() / "scripts" / "start_homez_desktop.cmd"
        self.assertTrue(path.exists())


class HiddenVbsLauncherTestCase(unittest.TestCase):
    """scripts/start_homez_desktop.vbs 정적 내용 검증."""

    @classmethod
    def setUpClass(cls):

        path = get_repo_root() / "scripts" / "start_homez_desktop.vbs"
        cls.path = path
        cls.content = path.read_text(encoding="utf-16")

    def test_computes_project_root_from_own_location(self):

        self.assertIn("WScript.ScriptFullName", self.content)
        self.assertIn("GetParentFolderName", self.content)
        # 절대 설치 경로를 소스에 하드코딩하지 않는다.
        self.assertNotIn("C:\\Users", self.content)

    def test_prefers_pythonw_and_checks_existence(self):

        self.assertIn("pythonw.exe", self.content)
        self.assertIn("FileExists(pythonwPath)", self.content)
        self.assertNotIn("Scripts\\python.exe", self.content)

    def test_shows_user_friendly_message_when_pythonw_missing(self):

        self.assertIn("MsgBox", self.content)
        self.assertIn("vbCritical", self.content)
        self.assertIn("WScript.Quit", self.content)

    def test_invokes_desktop_bootstrap_module(self):
        """
        2026-07-30: app.desktop.main을 직접 실행하면 그 모듈 자체의
        import 실패(예: webview 관련 문제)가 run()의 자체 예외 처리
        범위 밖이라 조용히 사라질 수 있다 — app.desktop.bootstrap이
        import 단계까지 포함해 감싸므로 이를 통해 실행돼야 한다.
        """

        self.assertIn("-m app.desktop.bootstrap", self.content)
        self.assertNotIn("-m app.desktop.main", self.content)

    def test_working_directory_set_to_project_root(self):

        self.assertIn("shell.CurrentDirectory = projectRoot", self.content)

    def test_runs_hidden_and_does_not_block(self):
        """WindowStyle 0 = SW_HIDE, waitOnReturn False."""

        self.assertIn("shell.Run commandLine, 0, False", self.content)

    def test_never_passes_secrets_as_arguments(self):
        """
        커맨드라인을 구성하는 실제 코드 줄(commandLine = ...)에는
        고정 문자열(따옴표로 감싼 pythonwPath + "-m app.desktop.bootstrap")
        만 있어야 한다 — 주석에서 "이런 값을 전달하지 않는다"고
        설명하는 것은 허용되므로 전체 텍스트가 아니라 그 실행 줄만
        검사한다.
        """

        command_line_stmt = next(
            line for line in self.content.splitlines()
            if line.strip().startswith("commandLine =")
        )

        for forbidden in ("password", "token", "nonce", "database_url", "authorization", "cookie"):
            self.assertNotIn(forbidden, command_line_stmt.lower())

    def test_command_line_uses_fixed_argument_only(self):
        """
        커맨드라인이 고정 문자열(quote된 pythonwPath + 고정 인자)로만
        구성되고, 외부 입력이나 사용자 지정 문자열을 이어붙이지 않는다
        (shell injection 위험 없음).
        """

        self.assertIn(
            'commandLine = """" & pythonwPath & """ -m app.desktop.bootstrap"',
            self.content,
        )

    def test_file_is_utf16_with_bom_for_korean_text(self):
        """VBScript가 한글을 깨지지 않게 표시하려면 UTF-16 BOM이 필요하다."""

        raw = self.path.read_bytes()
        self.assertEqual(raw[:2], b"\xff\xfe", "UTF-16LE BOM이 없습니다.")
        self.assertIn("실행 파일을 찾을 수 없습니다", self.content)

    def test_does_not_open_external_browser(self):

        for forbidden in ("http://", "https://", "iexplore", "chrome"):
            self.assertNotIn(forbidden, self.content.lower())


class AppUserModelIdTestCase(unittest.TestCase):
    """
    Gate F-10(2026-08-07) — app.desktop.main._set_app_user_model_id()가
    Windows 작업표시줄·Alt+Tab 그룹 식별자를 실제로 설정하는지, 그리고
    실패해도 HOMEZ 실행 자체를 막지 않는지 검증한다. 실제 Windows API를
    호출하지 않고 ctypes.windll을 mock으로 대체한다.
    """

    def test_calls_windows_api_with_stable_app_user_model_id(self):

        import app.desktop.main as desktop_main

        fake_logger = mock.Mock()
        fake_set_id = mock.Mock(return_value=0)
        fake_shell32 = mock.Mock()
        fake_shell32.SetCurrentProcessExplicitAppUserModelID = fake_set_id
        fake_windll = mock.Mock()
        fake_windll.shell32 = fake_shell32

        with mock.patch.object(desktop_main.sys, "platform", "win32"):
            with mock.patch.object(desktop_main.ctypes, "windll", fake_windll, create=True):
                desktop_main._set_app_user_model_id(fake_logger)

        fake_set_id.assert_called_once_with(desktop_main.APP_USER_MODEL_ID)
        self.assertEqual(desktop_main.APP_USER_MODEL_ID, "HOMEZ.CommerceOS.Desktop")
        fake_logger.info.assert_called_once()
        fake_logger.warning.assert_not_called()

    def test_is_noop_outside_windows(self):

        import app.desktop.main as desktop_main

        fake_logger = mock.Mock()
        fake_windll = mock.Mock()

        with mock.patch.object(desktop_main.sys, "platform", "linux"):
            with mock.patch.object(desktop_main.ctypes, "windll", fake_windll, create=True):
                desktop_main._set_app_user_model_id(fake_logger)

        fake_windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_not_called()
        fake_logger.info.assert_not_called()
        fake_logger.warning.assert_not_called()

    def test_swallows_exception_and_only_logs_error_type(self):

        import app.desktop.main as desktop_main

        fake_logger = mock.Mock()
        fake_shell32 = mock.Mock()
        fake_shell32.SetCurrentProcessExplicitAppUserModelID = mock.Mock(
            side_effect=OSError("가짜 실패 — 실제 경로/Secret 아님"),
        )
        fake_windll = mock.Mock()
        fake_windll.shell32 = fake_shell32

        with mock.patch.object(desktop_main.sys, "platform", "win32"):
            with mock.patch.object(desktop_main.ctypes, "windll", fake_windll, create=True):
                # 예외가 밖으로 전파되지 않아야 한다 — HOMEZ 실행을 막지 않는다.
                desktop_main._set_app_user_model_id(fake_logger)

        fake_logger.warning.assert_called_once()
        # 예외 메시지 원문이 아니라 예외 타입 이름만 로그에 남아야 한다.
        warning_args = fake_logger.warning.call_args[0]
        self.assertIn("OSError", warning_args)
        self.assertNotIn("가짜 실패", " ".join(str(a) for a in warning_args))

    def test_nonzero_hresult_logs_warning_but_does_not_raise(self):

        import app.desktop.main as desktop_main

        fake_logger = mock.Mock()
        fake_shell32 = mock.Mock()
        fake_shell32.SetCurrentProcessExplicitAppUserModelID = mock.Mock(return_value=1)
        fake_windll = mock.Mock()
        fake_windll.shell32 = fake_shell32

        with mock.patch.object(desktop_main.sys, "platform", "win32"):
            with mock.patch.object(desktop_main.ctypes, "windll", fake_windll, create=True):
                desktop_main._set_app_user_model_id(fake_logger)

        fake_logger.warning.assert_called_once()
        fake_logger.info.assert_not_called()


class DesktopIconAssetTestCase(unittest.TestCase):
    """
    Gate F-10(2026-08-07) — assets/homez-app.ico가 승인된 로고
    (app/web/assets/homez-logo.png)에서 재생성된, 요구된 9개 해상도를
    모두 포함하는 유효한 Windows ICO인지 순수 struct 파싱으로 검증한다
    (Pillow는 requirements.txt에 "런타임 앱 코드에서는 import하지
    않는다"고 명시돼 있으므로 테스트에서도 의존하지 않는다).
    """

    REQUIRED_SIZES = frozenset({16, 20, 24, 32, 40, 48, 64, 128, 256})

    @classmethod
    def setUpClass(cls):

        import struct

        cls.ico_path = get_repo_root() / "assets" / "homez-app.ico"
        cls.data = cls.ico_path.read_bytes()

        reserved, image_type, count = struct.unpack_from("<HHH", cls.data, 0)
        cls.header = (reserved, image_type, count)

        cls.entries = []
        for i in range(count):
            offset = 6 + i * 16
            width, height, colors, reserved2, planes, bpp, size, imgoffset = (
                struct.unpack_from("<BBBBHHII", cls.data, offset)
            )
            cls.entries.append(
                {
                    "width": width or 256,
                    "height": height or 256,
                    "size_bytes": size,
                    "image_offset": imgoffset,
                },
            )

    def test_ico_file_exists(self):

        self.assertTrue(self.ico_path.exists())

    def test_header_declares_icon_type(self):

        reserved, image_type, _count = self.header
        self.assertEqual(reserved, 0)
        self.assertEqual(image_type, 1)

    def test_contains_exactly_required_nine_resolutions(self):

        found_sizes = {entry["width"] for entry in self.entries}
        self.assertEqual(found_sizes, self.REQUIRED_SIZES)
        self.assertEqual(len(self.entries), len(self.REQUIRED_SIZES))

    def test_every_entry_is_square_and_has_nonzero_payload(self):

        for entry in self.entries:
            self.assertEqual(entry["width"], entry["height"])
            self.assertGreater(entry["size_bytes"], 0)

    def test_every_entry_payload_fits_within_file(self):

        file_size = len(self.data)
        for entry in self.entries:
            end = entry["image_offset"] + entry["size_bytes"]
            self.assertLessEqual(end, file_size)

    def test_original_approved_logo_source_untouched(self):
        """
        원본 승인 로고(app/web/assets/homez-logo.png)는 ico 재생성
        과정에서 절대 수정되지 않아야 한다.
        """

        source = get_repo_root() / "app" / "web" / "assets" / "homez-logo.png"
        self.assertTrue(source.exists())
        # PNG 시그니처만 확인 — 내용 자체를 픽셀 단위로 비교하지는
        # 않지만, 파일이 여전히 유효한 PNG로 남아있는지는 보증한다.
        self.assertEqual(source.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


class DesktopIconAssetContractTestCase(unittest.TestCase):
    """
    Gate F-10A(2026-08-07) — assets/homez-app.ico가 "런타임 필수
    자산"이라는 계약과, 그 경로 해석이 현재 작업 디렉터리(CWD)에
    의존하지 않는다는 계약을 검증한다. Pillow는 여기서도 사용하지
    않는다(운영 requirements에서 제거됨 — 아이콘 생성 스크립트만의
    선택적 개발 의존성이다).
    """

    def test_get_assets_dir_does_not_depend_on_current_working_directory(self):

        original_cwd = os.getcwd()
        tmp_dir = tempfile.mkdtemp()
        try:
            os.chdir(tmp_dir)
            resolved = get_assets_dir()
        finally:
            os.chdir(original_cwd)
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.assertEqual(resolved, get_repo_root() / "assets")
        self.assertTrue(resolved.is_absolute())

    def test_homez_app_ico_is_referenced_as_required_runtime_asset(self):

        main_py = (
            get_repo_root() / "app" / "desktop" / "main.py"
        ).read_text(encoding="utf-8")

        self.assertIn("homez-app.ico", main_py)

    def test_homez_app_ico_exists_at_resolved_assets_path(self):

        ico_path = get_assets_dir() / "homez-app.ico"
        self.assertTrue(ico_path.exists())

    def test_no_stray_backup_ico_files_left_in_deploy_asset_path(self):
        """
        Gate F-10 작업 중 임시로 만들었던 `.bak-pre-f10` 백업이 배포
        자산 경로에 남아있지 않아야 한다 — 재현 가능한 생성 스크립트
        (scripts/generate_homez_ico.py)가 있으므로 백업을 자산
        폴더에 보관할 필요가 없다.
        """

        assets_dir = get_assets_dir()
        leftover = list(assets_dir.glob("*.ico.bak*"))
        self.assertEqual(leftover, [])

    def test_generation_script_exists_and_is_syntactically_valid(self):

        import ast

        script_path = get_repo_root() / "scripts" / "generate_homez_ico.py"
        self.assertTrue(script_path.exists())

        source = script_path.read_text(encoding="utf-8")
        ast.parse(source)  # SyntaxError면 테스트가 실패한다.

        # 앱 시작 경로(app/desktop/main.py, app/desktop/server.py)
        # 어디에서도 이 생성 스크립트를 import/실행하지 않아야 한다 —
        # 자동 실행 금지 요구사항.
        main_py = (
            get_repo_root() / "app" / "desktop" / "main.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("generate_homez_ico", main_py)

    def test_generation_script_not_declared_in_production_requirements(self):
        """
        2026-08-20 갱신 — Pillow는 더 이상 scripts/generate_homez_ico.py
        전용 선택적 개발 의존성이 아니다. 이미지 Workflow(배경 합성
        app/domains/media_asset/composition.py, 긴 이미지 분할
        app/domains/media_asset/image_split.py)가 실제 운영 코드에서
        Pillow를 사용하도록 CTO가 명시적으로 승인했다(requirements.txt
        주석 참고) — 따라서 "Pillow가 requirements.txt/app/ 어디에도
        없어야 한다"는 이전 계약은 더 이상 사실이 아니다. 이 테스트는
        이제 "아이콘 생성 스크립트 자체가 별도로 Pillow를 재선언하지
        않는다"만 확인한다(중복 선언 방지가 실질적으로 남은 의도).
        """

        requirements = (get_repo_root() / "requirements.txt").read_text(
            encoding="utf-8",
        )
        pillow_declarations = [
            line for line in requirements.splitlines()
            if line.strip().lower().startswith("pillow==")
        ]
        self.assertEqual(
            len(pillow_declarations), 1,
            "Pillow는 정확히 한 번만 선언돼야 합니다(중복 선언 방지).",
        )


@unittest.skipUnless(
    __import__("importlib").util.find_spec("PIL") is not None,
    "Pillow가 설치돼 있지 않습니다 — 아이콘 생성 스크립트는 선택적 "
    "개발 의존성이므로 미설치 환경에서는 이 테스트를 건너뜁니다.",
)
class IconGenerationScriptDeterminismTestCase(unittest.TestCase):
    """
    Gate F-10A — scripts/generate_homez_ico.py가 같은 승인 원본에서
    항상 동일한 바이트 결과를 만드는지(결정적 생성) 확인한다. 실제
    배포 자산(assets/homez-app.ico)을 직접 덮어써 재확인한 뒤 원상
    복구한다 — 별도 임시 경로로 옮기지 않는 이유는 스크립트 자체가
    출력 경로를 하드코딩하고 있고, 그 계약 자체를 검증해야 하기
    때문이다.
    """

    def test_repeated_generation_is_byte_identical(self):

        import hashlib
        import importlib.util

        ico_path = get_assets_dir() / "homez-app.ico"
        original_bytes = ico_path.read_bytes()
        original_hash = hashlib.sha256(original_bytes).hexdigest()

        script_path = get_repo_root() / "scripts" / "generate_homez_ico.py"
        spec = importlib.util.spec_from_file_location(
            "generate_homez_ico", script_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        try:
            module.generate()
            regenerated_hash = hashlib.sha256(
                ico_path.read_bytes(),
            ).hexdigest()
            self.assertEqual(regenerated_hash, original_hash)
        finally:
            # 실행 중 어떤 이유로든 내용이 달라졌다면 원본으로 복구한다
            # — 이 테스트가 배포 자산을 훼손한 채 끝나지 않게 한다.
            if ico_path.read_bytes() != original_bytes:
                ico_path.write_bytes(original_bytes)


if __name__ == "__main__":
    unittest.main()
