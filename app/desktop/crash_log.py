"""
=========================================================
Homez OS

File : app/desktop/crash_log.py

HOMEZ Desktop Shell — 숨김 실행(pythonw.exe) 전용 보조 크래시 로그.

`app/desktop/server.py::get_desktop_logger()`가 이미 `storage\\logs\\
desktop-launcher.log`에 시작/종료/포트/단일 인스턴스/pywebview/예외를
전부 기록한다(2026-07-29부터 존재, 변경하지 않음). 이 모듈은 그것을
대체하지 않는다 — pythonw.exe로 실행하면 콘솔이 아예 없어 사용자가
문제 진단 시 참고할 "확실히 쓰기 가능한" 위치가 하나 더 있으면
좋겠다는 요구에 따라, `%LOCALAPPDATA%\\HOMEZ\\logs\\homez-desktop.log`
에 최소한의 수명주기 이벤트만 추가로 append한다(중복 로깅, 실패해도
앱 동작에 영향 없음).

기록 금지: 비밀번호, 비밀번호 해시, access token, Desktop token, setup
nonce, Cookie, Authorization 헤더, .env 내용, DATABASE_URL 전체.
호출자는 이 사실을 지키고 안전한 요약 문자열만 넘겨야 한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

MAX_LOG_BYTES = 1 * 1024 * 1024  # 1MB — 넘으면 .old로 교체
LOG_FILE_NAME = "homez-desktop.log"


def get_crash_log_path() -> Path:
    """
    2026-08-24 패키징 격리 재작업 — 이전에는 이 함수가 LOCALAPPDATA를
    직접 읽어 `app/desktop/paths.py::get_logs_dir()`의 `HOMEZ_DATA_ROOT`
    격리 계약을 완전히 우회했다(격리 테스트 중에도 이 수명주기
    로그만은 항상 실제 운영 %LOCALAPPDATA%\\HOMEZ\\logs\\로 새어
    나갔다 — 실측으로 발견). 이제 `get_logs_dir()`를 그대로 위임해
    다른 사용자 데이터 경로(logs/backups/config/media)와 동일하게
    `HOMEZ_DATA_ROOT`를 따른다. 실패(LOCALAPPDATA도 HOMEZ_DATA_ROOT
    도 없는 등)는 `log_lifecycle_event()`의 기존 바깥쪽 try/except가
    조용히 흡수한다 — 로그 기록 실패가 앱 동작을 막지 않는다는 계약은
    그대로 유지된다.
    """

    from app.desktop.paths import get_logs_dir

    return get_logs_dir() / LOG_FILE_NAME


def log_lifecycle_event(message: str) -> None:
    """
    안전한 요약 메시지 1건을 타임스탬프와 함께 append한다. 이 함수
    자체가 실패해도(디스크 권한 등) 절대 예외를 던지지 않는다 — 로그
    기록 실패가 앱 동작을 막으면 안 된다.
    """

    try:
        log_path = get_crash_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
            old_path = log_path.with_suffix(log_path.suffix + ".old")
            old_path.unlink(missing_ok=True)
            log_path.rename(old_path)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")

    except Exception:  # noqa: BLE001
        pass


__all__ = ["get_crash_log_path", "log_lifecycle_event", "LOG_FILE_NAME"]
