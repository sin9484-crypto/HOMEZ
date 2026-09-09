"""
=========================================================
Homez OS

File : app/desktop/server.py

HOMEZ Desktop Shell — 백그라운드 FastAPI/Uvicorn 서버 관리

- 127.0.0.1(loopback)에만 바인드한다. 0.0.0.0 등 외부 인터페이스는
  거부한다.
- 고정 포트(8000 등)에 의존하지 않는다 — 매번 사용 가능한 임의 포트를
  확보한다.
- /health 응답이 success==True, status=="healthy", service=="HOMEZ"를
  전부 만족해야 준비 완료로 판단한다. 포트가 열려 있지만 이 조건을
  만족하지 않으면(다른 프로그램) 즉시 중단하고 PortIsOtherServiceError
  를 던진다 — 절대로 "포트가 열렸으니 HOMEZ"로 간주하지 않는다.
- 이 모듈이 시작한 uvicorn.Server/Thread 객체만 종료 대상으로 삼는다
  — 다른 프로세스를 taskkill하거나 포트 번호만으로 프로세스를 찾아
  종료하는 로직은 존재하지 않는다.
- 로그에는 세션 토큰·비밀번호·`.env` 내용을 남기지 않는다.
=========================================================
"""

import json
import logging
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

import uvicorn

from app.core.desktop_token import clear_desktop_token
from app.core.desktop_token import set_desktop_token
from app.core.setup_nonce import clear_setup_nonce
from app.core.setup_nonce import generate_setup_nonce
from app.desktop.paths import get_logs_dir

if TYPE_CHECKING:
    from app.domains.media_asset.worker import ImageGenerationJobWorker

LOG_FILE_NAME = "desktop-launcher.log"
MAX_LOG_BYTES = 2 * 1024 * 1024  # 2MB — 넘으면 .old로 교체(간단 rotation)

# 2026-07-30: 실제 숨김 런처(pythonw.exe) 콜드 스타트에서 10초 타임아웃을
# 초과해 HomezHealthCheckFailed로 실패하는 사례를 실제로 재현·확인했다
# (%LOCALAPPDATA%\HOMEZ\logs\homez-desktop.log에 기록된 실제 타임스탬프
# 기준: bootstrap 시작→run() 진입까지 약 10초가 순전히 app.desktop.main과
# 그 하위 전체 FastAPI/SQLAlchemy 앱 import에 소요됨 — 2026-07-29 Desktop
# Shell 스모크 테스트에서도 "콜드 스타트 자원 경합"으로 이미 관찰된 적
# 있는 변동성이다). 2026-08-25 clean-venv 패키지의 실제 콜드 스타트가
# 26.7초 걸려 기존 25초 제한에서 E1002가 재현됐다. 초기화 실패는 계속
# 제한하되, 정상적인 첫 실행과 실시간 백신 검사 편차를 수용한다.
HEALTH_TIMEOUT_SECONDS = 45.0
HEALTH_POLL_INTERVAL_SECONDS = 0.2
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def get_desktop_logger() -> logging.Logger:

    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / LOG_FILE_NAME

    logger = logging.getLogger("homez.desktop")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
        old_path = logs_dir / f"{LOG_FILE_NAME}.old"
        old_path.unlink(missing_ok=True)
        log_path.rename(old_path)

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"),
    )
    logger.addHandler(handler)

    return logger


def find_free_port(host: str = "127.0.0.1") -> int:
    """
    지정된 loopback 호스트에서 사용 가능한 임의 포트를 확보한다.

    포트를 바인드해 OS로부터 빈 포트 번호를 받은 뒤 즉시 소켓을
    닫는다 — 이 확인과 실제 uvicorn 바인드 사이에 이론적으로 아주 짧은
    경쟁 창이 있을 수 있으나(다른 프로세스가 그 사이 같은 포트를
    가져가는 경우), uvicorn 자체가 실제 서버 소켓을 새로 여는 구조라
    완전히 없앨 수는 없다. 이 경쟁이 실제로 발생하면 서버 바인드가
    실패해 예외가 발생하며, 호출자는 재시도할 수 있다.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


class HomezHealthCheckFailed(RuntimeError):
    """서버가 제한 시간 내에 정상 응답하지 않았다."""


class PortIsOtherServiceError(RuntimeError):
    """포트가 열려 있지만 응답이 HOMEZ 식별자를 만족하지 않는다."""


def _check_health(base_url: str, timeout: float = 2.0) -> dict:

    request = urllib.request.Request(f"{base_url}/health", method="GET")

    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = response.read().decode("utf-8")
        return json.loads(body)


def is_homez_health_response(payload: dict) -> bool:
    """
    HOMEZ 서버로 확정하기 위한 세 조건을 전부 검사한다. 단순히 HTTP
    200이 왔다는 것만으로는 부족하다.
    """

    return (
        payload.get("success") is True
        and payload.get("status") == "healthy"
        and payload.get("service") == "HOMEZ"
    )


@dataclass
class DesktopServerHandle:
    """
    이 Desktop 프로세스가 시작한 서버 하나에 대한 핸들.

    server/thread를 직접 보관하므로, shutdown()은 오직 이 핸들이
    시작한 서버만 종료할 수 있다 — 포트 번호나 프로세스 이름으로 다른
    프로세스를 찾아 종료하는 코드 경로 자체가 없다.
    """

    host: str
    port: int
    session_token: str
    setup_nonce: str
    server: "uvicorn.Server"
    thread: threading.Thread
    logger: logging.Logger = field(repr=False)
    # 2026-08-02 Gate R5: Image Generation Job을 폴링해 처리하는
    # 백그라운드 Worker — 이 서버와 함께 시작·종료된다.
    image_worker: "ImageGenerationJobWorker | None" = None

    @property
    def base_url(self) -> str:

        return f"http://{self.host}:{self.port}"

    def shutdown(self, timeout: float = 10.0) -> bool:

        self.logger.info("서버 종료 시작 (port=%s)", self.port)

        if self.image_worker is not None:
            self.image_worker.stop(timeout=timeout)

        self.server.should_exit = True
        self.thread.join(timeout=timeout)

        # 이 프로세스의 Desktop 토큰과 최초 설정 nonce를 항상 즉시
        # 폐기한다(스레드 join 성공 여부와 무관) — 앱 종료 시 둘 다
        # 남아있지 않아야 한다는 요구사항. 새 start_server() 호출마다
        # 새로 생성되므로, 여기서 지워도 다음 실행에는 영향이 없다.
        clear_desktop_token()
        clear_setup_nonce()

        if self.thread.is_alive():
            self.logger.warning(
                "서버 스레드가 제한 시간(%.1f초) 내에 종료되지 않음 "
                "(port=%s)", timeout, self.port,
            )
            return False

        self.logger.info("서버 종료 완료 (port=%s)", self.port)
        return True


def _build_image_generation_service(db):
    """
    2026-08-02 Gate R5 — Worker가 각 Job을 처리할 때 쓸
    ImageGenerationJobQueueService를 만든다. `ListingPackageService`를
    통해 만들어야 on_job_completed 콜백(listing_package_id를 가진
    Job이 끝나면 그 패키지의 fingerprint를 다시 계산)이 연결된다 —
    media_asset 도메인 자체는 listing_package를 몰라야 하므로, 이
    조립은 도메인 경계를 이미 알고 있는 이 조립부(app/desktop/
    server.py)에서만 한다. 함수 안에서 지연 import하는 이유: 이
    모듈은 app.main:app을 uvicorn으로 띄우기 전에도 import될 수
    있으므로, 전체 도메인 그래프 import를 여기 최상단으로 끌어올리지
    않는다.
    """

    from app.domains.listing_package.service import ListingPackageService

    return ListingPackageService(db).image_queue_service


def _start_image_generation_worker(
    logger: logging.Logger,
) -> "ImageGenerationJobWorker | None":
    """
    Image Generation Job 백그라운드 Worker를 시작한다. 이 함수 자체가
    실패해도(예: DB 연결 문제) Desktop 서버 기동 자체를 막지 않는다
    — 이미지 생성은 핵심 로그인/조회 기능과 독립적이므로, Worker
    시작 실패는 로그만 남기고 서버는 정상적으로 계속 뜬다.
    """

    try:
        from app.database.session import SessionLocal
        from app.domains.media_asset.worker import ImageGenerationJobWorker

        worker = ImageGenerationJobWorker(
            SessionLocal, service_factory=_build_image_generation_service,
        )
        worker.start()

        return worker

    except Exception:  # noqa: BLE001
        logger.exception("Image Generation Worker 시작 실패(서버는 계속 진행)")

        return None


def start_server(
    host: str = "127.0.0.1",
    port: int | None = None,
    startup_timeout: float = HEALTH_TIMEOUT_SECONDS,
    image_worker_starter: (
        "Callable[[logging.Logger], ImageGenerationJobWorker | None] | None"
    ) = None,
) -> DesktopServerHandle:
    """
    FastAPI(app.main:app)를 백그라운드 스레드에서 uvicorn으로 시작하고,
    /health가 HOMEZ 식별자를 만족할 때까지 대기한 뒤 핸들을 반환한다.

    실패 시(다른 서비스가 포트를 쓰고 있음, 타임아웃) 이 함수가 시작한
    스레드/서버는 반환 전에 반드시 정리한 뒤 예외를 던진다 — 실패한
    채로 방치된 백그라운드 서버가 남지 않는다.

    `image_worker_starter`: 2026-08-02 Gate R6-C — 운영 기본값은
    `_start_image_generation_worker`(실제 SessionLocal/homez.db를
    쓰는 진짜 Worker)다. 테스트는 이 자리에 Fake/No-op 콜러블을 주입해
    실제 DB에 전혀 접근하지 않고 서버 수명주기(성공 시 1회 시작,
    종료 시 1회 정지)만 검증할 수 있다 — 이전에는 이 지점이 항상
    `_start_image_generation_worker`에 하드코딩돼 있어, 실제 서버를
    띄우는 모든 테스트가(우연히 PENDING Job이 없어서 쓰기가 없었을
    뿐) 실제 homez.db에 매번 접근하고 있었다.
    """

    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            "HOMEZ Desktop 서버는 loopback(127.0.0.1/localhost)에만 "
            f"바인드할 수 있습니다. (요청됨: {host})",
        )

    starter = image_worker_starter or _start_image_generation_worker

    logger = get_desktop_logger()

    if port is None:
        port = find_free_port(host)

    session_token = secrets.token_urlsafe(32)

    # 같은 프로세스 안에서 uvicorn 스레드로 실행될 app.main:app이
    # 이 토큰을 검증할 수 있도록 프로세스 전역에 설정한다(2026-07-30,
    # app/core/desktop_auth.py — 이전에는 이 토큰이 생성만 되고 어떤
    # API도 보호하지 않았다).
    set_desktop_token(session_token)

    # 최초 관리자 설정 화면 전용 일회용 nonce도 프로세스 시작마다 새로
    # 만든다(2026-07-30, app/core/desktop_setup.py). users가 이미 1명
    # 이상이면 이 nonce는 아무 API도 실제로 사용하지 않는다 — 그래도
    # 매번 새로 생성하는 이유는, 설정 화면 자체를 아예 렌더링하지 않을
    # 조건(서버의 setup_required=false)과는 별개로, nonce 발급 로직은
    # "요청 시점의 실제 DB 상태"에만 의존하도록 단순하게 유지하기 위함.
    setup_nonce = generate_setup_nonce()

    config = uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(
        target=server.run, name="homez-desktop-uvicorn", daemon=True,
    )

    logger.info("서버 시작 시도: host=%s port=%s", host, port)
    thread.start()

    base_url = f"http://{host}:{port}"
    deadline = time.monotonic() + startup_timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:

        try:
            payload = _check_health(base_url)

        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
            continue

        if is_homez_health_response(payload):
            logger.info("HOMEZ 서버 준비 완료: %s", base_url)

            image_worker = starter(logger)

            return DesktopServerHandle(
                host=host,
                port=port,
                session_token=session_token,
                setup_nonce=setup_nonce,
                server=server,
                thread=thread,
                logger=logger,
                image_worker=image_worker,
            )

        logger.error(
            "포트 %s의 서비스가 HOMEZ 식별자를 만족하지 않음 — 다른 "
            "프로그램으로 판단하고 중단합니다.", port,
        )
        server.should_exit = True
        thread.join(timeout=5)
        clear_desktop_token()
        clear_setup_nonce()

        raise PortIsOtherServiceError(
            f"포트 {port}의 서비스가 HOMEZ가 아닙니다(식별자 불일치).",
        )

    logger.error(
        "서버 준비 타임아웃(%.1f초, port=%s): %s",
        startup_timeout, port, last_error,
    )
    server.should_exit = True
    thread.join(timeout=5)
    clear_desktop_token()
    clear_setup_nonce()

    raise HomezHealthCheckFailed(
        f"HOMEZ 서버가 {startup_timeout:.1f}초 내에 준비되지 않았습니다 "
        f"(port={port}).",
    )


__all__ = [
    "DesktopServerHandle",
    "HomezHealthCheckFailed",
    "PortIsOtherServiceError",
    "find_free_port",
    "is_homez_health_response",
    "start_server",
    "get_desktop_logger",
]
