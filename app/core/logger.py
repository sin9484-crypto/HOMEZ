"""
=========================================================
Homez OS

File : logger.py
Version : 5.0.0

Logging System
=========================================================
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import settings


def _resolve_log_dir() -> Path:
    """
    2026-08-24 패키징 격리 재작업 — 실측 격리 설치 검증 중 발견:
    이전에는 `Path("logs")`(프로세스 CWD 기준 상대경로)를 무조건
    썼다 — 패키징 모드에서는 실행 위치(설치 폴더)에 `logs/homez.log`
    가 그대로 생겨, `app/desktop/paths.py`의 data/logs/backups/
    config/media 격리 계약(HOMEZ_DATA_ROOT)을 완전히 우회했다(이
    파일도 그 계약 밖에 있었다).

    개발 모드(is_frozen()=False)는 기존 동작을 그대로 유지한다 —
    CWD 기준 상대경로(`logs/`)는 이 저장소를 그대로 쓰는 기존
    개발·테스트 워크플로와 이미 맞물려 있어, 여기서 바꾸지 않는다.
    패키징 모드에서만 `get_logs_dir()`(HOMEZ_DATA_ROOT를 따르는
    동일한 격리 계약)로 옮긴다.
    """

    from app.desktop.paths import get_logs_dir, is_frozen

    if is_frozen():
        return get_logs_dir()

    return Path("logs")


LOG_DIR = _resolve_log_dir()
LOG_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


LOG_FORMAT = (
    "%(asctime)s | "
    "%(levelname)s | "
    "%(name)s | "
    "%(message)s"
)


def create_logger(
    name: str,
) -> logging.Logger:

    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(settings.LOG_LEVEL)

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()

    # pythonw.exe로 실행되는 Desktop Shell에서는 콘솔 스트림의 인코딩이
    # 시스템 로케일(예: cp949)로 고정돼, em dash(—)처럼 그 코드페이지에
    # 없는 문자가 로그 메시지에 포함되면 UnicodeEncodeError가 발생해
    # 호출부(FormClosing 등)까지 예외가 전파된다(2026-07-30 실제 재현 —
    # 창 닫기 시 처리되지 않은 예외 대화상자로 확인됨). 로깅 자체가
    # 앱을 죽이지 않도록 콘솔 스트림을 UTF-8로 강제하고, 그래도 표현 못하는
    # 문자는 예외 대신 이스케이프 표기로 대체한다.
    try:
        console.stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_DIR / "homez.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )

    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)

    return logger
def set_log_level(
    level: int,
) -> None:

    logger.setLevel(level)

    for handler in logger.handlers:
        handler.setLevel(level)


def add_file_handler(
    filename: str,
    level: int = logging.INFO,
) -> None:

    handler = RotatingFileHandler(
        LOG_DIR / filename,
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )

    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(LOG_FORMAT)
    )

    logger.addHandler(handler)


def add_stream_handler(
    level: int = logging.INFO,
) -> None:

    handler = logging.StreamHandler(sys.stdout)

    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(LOG_FORMAT)
    )

    logger.addHandler(handler)
def get_logger(
    name: str,
) -> logging.Logger:

    return create_logger(name)


def remove_handlers() -> None:

    for handler in logger.handlers[:]:

        handler.close()

        logger.removeHandler(handler)


def enable_propagation(
    enabled: bool = True,
) -> None:

    logger.propagate = enabled


def disable_propagation() -> None:

    logger.propagate = False
logger = create_logger("homez")


__all__ = [
    "logger",
    "create_logger",
    "get_logger",
    "set_log_level",
    "add_file_handler",
    "add_stream_handler",
    "remove_handlers",
    "enable_propagation",
    "disable_propagation",
]
