"""
=========================================================
Homez OS

File : tools/start_homez_live.py

운영 앱 실행기(2026-11-02). 저장소 코드와 저장소 `homez.db`로 Desktop 앱을 켠다.

배경: PC가 다시 시작되면 운영 앱은 꺼지고, 바탕화면 아이콘은 옛 설치본을 가리켜서 사용자가 운영 앱을 켤 수단이 없었다.

규칙:
  - 앱은 켤 때 기준 데이터를 시딩하므로, 켜기 전에 그날의 복원점을 먼저 만든다(하루 1개, 있으면 건드리지 않음).
  - 복원점을 만들지 못하면 앱을 켜지 않고 알린다. 원본은 읽기 전용으로만 연다.
  - 오래된 복원점을 지우지 않는다.
  - 이미 켜져 있어도 앱의 단일 실행 잠금이 새 서버를 막으므로 여기서 따로 확인하지 않는다.
  - `--check`는 경로만 확인하고 복원점·실행 없이 끝난다.
=========================================================
"""

from __future__ import annotations

import ctypes
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = Path.home() / "Homez-Backups"
DB_NAME = "homez.db"
APP_MODULE = "app.desktop.main"
TITLE = "HOMEZ 운영 앱"
DROPPED_ENV_PREFIXES = ("HOMEZ_GUARD",)
DROPPED_ENV_NAMES = ("PYTHONPATH", "PYTHONHOME")

EXIT_OK = 0
EXIT_MISSING_FILE = 2
EXIT_BACKUP_FAILED = 3
EXIT_START_FAILED = 4


def show_message(text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)
    except (AttributeError, OSError):
        print(text, file=sys.stderr)


def app_environment(environ: dict[str, str]) -> dict[str, str]:
    """시험용 가드·경로 변수가 운영 앱으로 넘어가지 않게 뺀다."""

    return {
        k: v for k, v in environ.items()
        if k.upper() not in DROPPED_ENV_NAMES and not k.upper().startswith(DROPPED_ENV_PREFIXES)
    }


def make_daily_backup(db_path: Path, backup_root: Path, now: datetime) -> Path:
    """그날의 복원점을 돌려준다. 이미 있으면 그대로 두고, 없으면 만들어 무결성을 확인한 뒤 이름을 확정한다."""

    target_dir = backup_root / f"auto_start_{now:%Y%m%d}"
    target = target_dir / "homez_pre_start.db"
    if target.exists():
        return target
    target_dir.mkdir(parents=True, exist_ok=True)
    partial = target_dir / "homez_pre_start.db.partial"
    try:
        source = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        try:
            copy = sqlite3.connect(partial)
            try:
                source.backup(copy)
                result = copy.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                copy.close()
        finally:
            source.close()
        if result != "ok":
            raise sqlite3.DatabaseError("integrity_check failed")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def main(
    argv: list[str] | None = None, *, repo: Path = REPO_ROOT, backup_root: Path = BACKUP_ROOT,
    now: datetime | None = None, popen=subprocess.Popen, notify=show_message, environ: dict[str, str] | None = None,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    db_path = repo / DB_NAME
    pythonw = repo / "venv" / "Scripts" / "pythonw.exe"
    missing = [str(p) for p in (db_path, pythonw) if not p.is_file()]
    if missing:
        notify("운영 앱을 켜지 않았습니다. 다음 파일이 없습니다:\n" + "\n".join(missing))
        return EXIT_MISSING_FILE
    if "--check" in args:
        print(f"ok: {pythonw} -m {APP_MODULE} / {db_path}")
        return EXIT_OK
    try:
        make_daily_backup(db_path, backup_root, now or datetime.now())
    except (OSError, sqlite3.Error) as exc:
        notify(f"복원점을 만들지 못해 운영 앱을 켜지 않았습니다({type(exc).__name__}).\n앱은 그대로 꺼져 있고 주문 감지도 돌지 않습니다.")
        return EXIT_BACKUP_FAILED
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        popen(
            [str(pythonw), "-m", APP_MODULE], cwd=str(repo), env=app_environment(dict(os.environ if environ is None else environ)),
            creationflags=flags, close_fds=True,
        )
    except OSError as exc:
        notify(f"운영 앱을 켜지 못했습니다({type(exc).__name__}).")
        return EXIT_START_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
