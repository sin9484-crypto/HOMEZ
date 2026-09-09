"""
=========================================================
Homez OS

File : app/desktop/bootstrap.py

HOMEZ Desktop Shell — 최상위 부트스트랩 진입점 (2026-07-30).

`scripts/start_homez_desktop.vbs`가 이 모듈을 `-m app.desktop.bootstrap`
으로 실행한다(이전에는 `-m app.desktop.main`을 직접 실행했다).

배경: `app/desktop/main.py`의 `run()`은 자기 자신의 예외를 이미
전부 처리하지만(포트 충돌/서버 준비 실패/기타 예외), 그 `run()`
함수 자체를 정의하는 `app.desktop.main` 모듈을 **import하는 단계**
(그리고 그 모듈이 다시 import하는 `webview`, `app.desktop.server`
등)에서 실패하면 `run()`은 호출되기도 전이라 어떤 보호도 받지
못한다 — pythonw.exe는 콘솔이 없어 이 경우 정말 아무 흔적도 없이
그냥 조용히 종료돼 버린다.

이 파일은 표준 라이브러리(`sys`, `os`, `ctypes`, `datetime`,
`pathlib`)만 사용해 그 import 단계까지 포함한 최후 방어선 역할을
한다 — `app.desktop.crash_log`의 import조차 실패할 수 있는 극단적
상황까지 가정해, 로그 기록 로직을 이 파일 안에 자체적으로
인라인한다(다른 앱 코드에 의존하지 않음).

비밀번호·해시·토큰·nonce·Cookie·Authorization 헤더·.env 내용·
DATABASE_URL 전체는 이 파일에서도 절대 기록하지 않는다 — 예외
"종류" 이름만 기록한다.
=========================================================
"""

from __future__ import annotations

import ctypes
import os
import sys
from datetime import datetime
from pathlib import Path

ERROR_CODE_BOOTSTRAP_FAILURE = "E1000"

_MB_ICONERROR = 0x10
_MB_OK = 0x0
_MB_SETFOREGROUND = 0x10000
_MB_TOPMOST = 0x40000

_LOG_FILE_NAME = "homez-desktop.log"
_MAX_LOG_BYTES = 1 * 1024 * 1024


def _bootstrap_log_path() -> Path:

    local_app_data = os.environ.get("LOCALAPPDATA")

    if local_app_data:
        return Path(local_app_data) / "HOMEZ" / "logs" / _LOG_FILE_NAME

    # LOCALAPPDATA조차 없는 극단적 환경 — 마지막 수단으로 이 파일이
    # 있는 위치(scripts/../app/desktop) 기준 storage\logs를 시도한다.
    # app.desktop.paths를 import하지 않는다(그 import 자체가 실패
    # 원인일 수 있으므로 여기서는 경로 계산을 직접 한다).
    repo_root = Path(__file__).resolve().parent.parent.parent

    return repo_root / "storage" / "logs" / _LOG_FILE_NAME


def _bootstrap_log(message: str) -> None:

    try:
        log_path = _bootstrap_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if log_path.exists() and log_path.stat().st_size > _MAX_LOG_BYTES:
            old_path = log_path.with_suffix(log_path.suffix + ".old")
            old_path.unlink(missing_ok=True)
            log_path.rename(old_path)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")

    except Exception:  # noqa: BLE001
        pass


def _bootstrap_message_box(error_code: str) -> None:

    try:
        log_path = str(_bootstrap_log_path())
    except Exception:  # noqa: BLE001
        log_path = "(로그 경로 확인 실패)"

    message = (
        f"HOMEZ를 시작하지 못했습니다. (오류 코드: {error_code})\n\n"
        f"로그 파일:\n{log_path}"
    )

    try:
        ctypes.windll.user32.MessageBoxW(
            0, message, "HOMEZ",
            _MB_ICONERROR | _MB_OK | _MB_SETFOREGROUND | _MB_TOPMOST,
        )
    except Exception:  # noqa: BLE001
        pass


def _import_desktop_main():
    """
    `app.desktop.main`을 import해 모듈 객체를 반환한다. 별도 함수로
    분리한 이유: 테스트가 `builtins.__import__` 전체를 몽키패치하지
    않고 이 함수 하나만 `patch.object(bootstrap, "_import_desktop_main",
    ...)`로 안전하게 대체할 수 있게 하기 위함이다(전역 import 훅을
    건드리면 같은 프로세스의 다른 테스트 파일과 타이밍이 겹쳐
    예측하기 어려운 상호작용이 생길 수 있음 — 2026-07-30 실제 발견).
    """

    import app.desktop.main as desktop_main

    return desktop_main


def main() -> int:
    """
    `app.desktop.main`을 import하고 `run()`을 호출하는 전체 과정을
    감싼다. import 실패와 run() 호출 자체의 예기치 못한 실패(run()이
    이미 자기 예외를 대부분 처리하므로 정상적으로는 여기까지 오지
    않아야 하지만, 방어적으로 다시 감싼다) 모두 이 함수에서 잡는다.
    """

    _bootstrap_log("HOMEZ Desktop bootstrap 시작")

    try:
        desktop_main = _import_desktop_main()

    except BaseException as exc:  # noqa: BLE001
        _bootstrap_log(
            f"[{ERROR_CODE_BOOTSTRAP_FAILURE}] app.desktop.main import 실패: "
            f"{type(exc).__name__}",
        )
        _bootstrap_message_box(ERROR_CODE_BOOTSTRAP_FAILURE)
        return 1

    try:
        return desktop_main.run()

    except BaseException as exc:  # noqa: BLE001
        # run() 자신의 try/except를 다시 감싸는 방어적 이중 안전장치 —
        # 정상적으로는 run()이 이미 모든 예외를 처리하고 정수를
        # 반환하므로 여기에 도달하지 않아야 한다.
        _bootstrap_log(
            f"[{ERROR_CODE_BOOTSTRAP_FAILURE}] run() 호출 자체가 실패: "
            f"{type(exc).__name__}",
        )
        _bootstrap_message_box(ERROR_CODE_BOOTSTRAP_FAILURE)
        return 1

    finally:
        _bootstrap_log("HOMEZ Desktop bootstrap 종료")


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "ERROR_CODE_BOOTSTRAP_FAILURE"]
