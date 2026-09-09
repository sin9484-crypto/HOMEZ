"""
=========================================================
Homez OS

File : app/domains/diagnostics/service.py

Gate Y-5(2026-08-12) — 진단 내보내기. 전부 읽기 전용이다 — DB에
`PRAGMA integrity_check`(읽기 전용 연결)만 실행하고, Migration
상태는 `MigrationRunner.diagnose()`(문서화된 읽기 전용 진단 경로,
`app/database/migration_runner.py` 참고)만 쓴다. 로그 tail은
읽어서 redaction.py로 비밀정보를 지운 뒤에만 결과에 포함한다.
=========================================================
"""

from __future__ import annotations

import platform
import sqlite3
from datetime import datetime
from pathlib import Path

from app.core.version import VERSION as APP_VERSION
from app.database.migration_runner import MigrationRunner
from app.database.migration_runner import MigrationRunnerError
from app.domains.diagnostics.redaction import known_secret_values
from app.domains.diagnostics.redaction import redact_text

_DEFAULT_LOG_TAIL_LINES = 200


def _collect_db_diagnostics(db_path: Path) -> dict:

    db_path = Path(db_path)

    if not db_path.exists():
        return {
            "exists": False,
            "file_size_bytes": None,
            "integrity_check_result": None,
        }

    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
    )
    try:
        conn.execute("PRAGMA query_only=ON")
        integrity = conn.execute(
            "PRAGMA integrity_check",
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "exists": True,
        "file_size_bytes": db_path.stat().st_size,
        "integrity_check_result": integrity,
    }


def _collect_migration_diagnostics(
    db_path: Path,
    migrations_dir: Path,
) -> dict:

    db_path = Path(db_path)
    migrations_dir = Path(migrations_dir)

    if not db_path.exists():
        return {
            "status": "db_not_found",
            "applied_count": 0,
            "pending_count": 0,
            "pending_files": [],
        }

    runner = MigrationRunner(db_path, migrations_dir)

    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
    )
    try:
        conn.execute("PRAGMA query_only=ON")
        result = runner.diagnose(conn)
        return {
            "status": "ok",
            "applied_count": len(result.get("already_applied", [])),
            "pending_count": len(result.get("pending", [])),
            "pending_files": [
                Path(p).name for p in result.get("pending", [])
            ],
            "backfill_needed_count": len(
                result.get("backfill_needed", []),
            ),
        }
    except MigrationRunnerError as exc:
        return {
            "status": "diagnose_failed",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "applied_count": 0,
            "pending_count": 0,
            "pending_files": [],
        }
    finally:
        conn.close()


def _collect_log_tail(
    log_path: Path | None,
    *,
    lines: int,
    settings=None,
) -> str | None:

    if log_path is None:
        return None

    log_path = Path(log_path)

    if not log_path.exists():
        return None

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    tail = "".join(all_lines[-lines:])

    known_values = known_secret_values(settings) if settings else []

    return redact_text(tail, known_values=known_values)


def build_diagnostics_bundle(
    *,
    db_path: Path,
    migrations_dir: Path,
    route_count: int,
    log_path: Path | None = None,
    log_tail_lines: int = _DEFAULT_LOG_TAIL_LINES,
    settings=None,
) -> dict:

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "app_version": APP_VERSION,
        "os": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
        },
        "database": _collect_db_diagnostics(db_path),
        "migrations": _collect_migration_diagnostics(
            db_path,
            migrations_dir,
        ),
        "route_count": route_count,
        "log_tail": _collect_log_tail(
            log_path,
            lines=log_tail_lines,
            settings=settings,
        ),
    }


__all__ = ["build_diagnostics_bundle"]
