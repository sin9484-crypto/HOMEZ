"""
=========================================================
Homez OS

File : app/core/desktop_token.py

HOMEZ Desktop Shell — 현재 프로세스의 Desktop 세션 토큰 보관소.

`app/desktop/server.py::start_server()`가 프로세스마다 새로
생성하는 `session_token`(2026-07-29부터 생성만 되고 어떤 API도
보호하지 않던 값)을, 같은 프로세스 안에서 실행되는 FastAPI 앱이
검증할 수 있도록 모듈 전역에 보관한다.

Desktop Shell(`app/desktop/main.py`)과 FastAPI 앱(`app.main:app`)은
uvicorn을 별도 프로세스가 아니라 같은 프로세스의 스레드로 실행하므로
(app/desktop/server.py 참고), 이 모듈 전역 변수는 두 쪽에서 안전하게
공유된다 — 별도 IPC나 파일 기록이 필요 없다.

토큰 값 자체는 로그에 남기지 않는다.
=========================================================
"""

import threading

_lock = threading.Lock()
_current_token: str | None = None


def set_desktop_token(token: str) -> None:

    global _current_token

    with _lock:
        _current_token = token


def get_desktop_token() -> str | None:

    with _lock:
        return _current_token


def clear_desktop_token() -> None:

    global _current_token

    with _lock:
        _current_token = None


def is_desktop_mode() -> bool:
    """이 프로세스가 현재 Desktop Shell로 실행 중인지(토큰이 설정돼 있는지)."""

    return get_desktop_token() is not None


__all__ = [
    "set_desktop_token",
    "get_desktop_token",
    "clear_desktop_token",
    "is_desktop_mode",
]
