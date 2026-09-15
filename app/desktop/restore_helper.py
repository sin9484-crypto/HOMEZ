"""
=========================================================
Homez OS

File : app/desktop/restore_helper.py

V7 Live Gate 4 복원 결함 수정(2026-08-17) — Gate R-1 Option B.

Gate R-0에서 계측으로 증명한 사실 두 가지(재현 스크립트:
scratchpad의 live_gate4_restore_fix_gate_r0_repro.py, 결과는
docs/V6_EXECUTION_LEDGER.md 이번 세션 절 참고):

1. SQLAlchemy `QueuePool`은 `Session.close()` 이후에도 물리
   `sqlite3.Connection`을 풀에 살려두고, Windows에서 SQLite가 여는
   파일 핸들은 `FILE_SHARE_DELETE`를 포함하지 않는다 — 그 커넥션이
   살아있는 동안 `os.replace()`가 대상 파일을 교체하지 못한다
   (`PermissionError: [WinError 5]`).
2. HOMEZ Desktop은 살아있는 멀티스레드 FastAPI 서버다.
   `app/database/session.py`의 전역 `engine`은 이 서버의 모든 다른
   요청·도메인 핸들러·Image Generation Worker 스레드가 공유한다 —
   복원을 실행하는 그 요청 하나가 자기 자신의 커넥션을 dispose해도,
   그 순간과 `os.replace()` 사이에 **다른 스레드가 같은 전역 engine에서
   새 커넥션을 체크아웃**하면 다시 파일이 잠긴다(TOCTOU 경쟁). 이걸
   막으려면 사실상 서버 전체를 멈춰야 한다.

그래서 실제 파일 교체(`RestoreService.restore()` 실행)는 절대 살아있는
Desktop 서버 프로세스 "안에서" 하지 않는다. 이 모듈은:
  - 복원 요청을 받으면 계획(RestorePlan)을 파일로 저장하고,
  - 완전히 별도의 Helper 프로세스를 기동한 뒤,
  - Desktop 창을 닫아 메인 앱이 (기존 main.py의 정상 종료 경로 그대로)
    완전히 종료되게 하고,
  - Helper 프로세스는 메인 프로세스가 "완전히" 종료된 뒤에만(OS가
    보장하는 프로세스 종료 시 전체 핸들 회수를 이용) 실제 교체를
    수행하고, 끝나면 HOMEZ를 다시 실행한다.

패키징된 단일 exe가 이 모듈의 CLI 플래그로 스스로를 Helper 모드로
재실행하므로 별도 실행 파일을 새로 빌드할 필요가 없다.
=========================================================
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from app.core.windows_credential_store import CredentialStore

RESTORE_HELPER_CLI_FLAG = "--homez-restore-helper"
PLAN_ARG = "--plan"
PARENT_PID_ARG = "--parent-pid"

PENDING_PLAN_FILENAME = "restore_pending_plan.json"
RESULT_FILENAME = "restore_helper_result.json"

DEFAULT_PARENT_EXIT_TIMEOUT_SECONDS = 30.0


class RestoreHelperError(Exception):
    pass


# ============================================================
# RestorePlan — 비밀정보 없음(경로/ID/시각뿐), 메인 프로세스가
# 완전히 종료된 뒤 Helper가 다시 읽을 수 있도록 JSON으로 남긴다.
# ============================================================


@dataclass
class RestorePlan:

    plan_id: str
    source_backup_path: str
    target_db_path: str
    expected_sha256: str
    pre_restore_backups_dir: str
    triggered_by_user_id: int | None
    requested_at: str
    relaunch_argv: list[str]

    def to_json(self) -> str:

        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "RestorePlan":

        data = json.loads(raw)
        return cls(**data)


def save_plan(path: Path, plan: RestorePlan) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.to_json(), encoding="utf-8")


def load_plan(path: Path) -> RestorePlan:

    if not path.exists():
        raise RestoreHelperError(f"복원 계획 파일을 찾을 수 없습니다: {path}")

    return RestorePlan.from_json(path.read_text(encoding="utf-8"))


def default_plan_path() -> Path:

    from app.desktop.paths import get_data_dir

    return get_data_dir() / PENDING_PLAN_FILENAME


def default_result_path() -> Path:

    from app.desktop.paths import get_data_dir

    return get_data_dir() / RESULT_FILENAME


def build_relaunch_argv() -> list[str]:
    """
    Helper가 복원(성공/실패 무관) 완료 후 HOMEZ를 다시 띄우기 위한
    argv. 패키징된 단일 exe는 자기 자신을 인자 없이 재실행하면
    정상적으로 GUI 모드로 뜬다(`RESTORE_HELPER_CLI_FLAG`가 없으므로).
    """

    if getattr(sys, "frozen", False):
        return [sys.executable]

    return [sys.executable, "-m", "app.desktop.main"]


# ============================================================
# Desktop 컨텍스트 등록 — main.py가 창 생성 직후 1회 호출한다.
# 라우터(요청 스레드)가 window.destroy()를 호출할 수 있게 하기
# 위함(pywebview는 다른 스레드에서의 호출을 지원하도록 설계됨).
# ============================================================

_desktop_window: Any = None
_registry_lock = threading.Lock()


def register_desktop_context(window: Any) -> None:

    global _desktop_window

    with _registry_lock:
        _desktop_window = window


def _get_desktop_window() -> Any:

    with _registry_lock:
        return _desktop_window


def clear_desktop_context() -> None:
    """테스트/재시작 시 등록을 초기화한다."""

    global _desktop_window

    with _registry_lock:
        _desktop_window = None


# ============================================================
# 부모 프로세스 종료 대기 (Windows) — PID 재사용 경쟁을 피하려고
# 부모가 아직 살아있는 시점(Helper 기동 직후)에 즉시 핸들을 연다.
# ============================================================

_PROCESS_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x0


def wait_for_process_exit(
    pid: int,
    timeout: float = DEFAULT_PARENT_EXIT_TIMEOUT_SECONDS,
) -> bool:
    """
    지정 PID 프로세스가 완전히 종료될 때까지 대기한다.

    OS가 프로세스 종료 시 그 프로세스의 모든 파일 핸들을 원자적으로
    회수한다는 보장을 그대로 이용한다 — "완전히 종료됨"이 확인된
    이후에만 실제 파일 교체를 진행해야 Gate R-0가 증명한 커넥션 잔존
    문제(그리고 그로 인한 WinError 5)를 구조적으로 피할 수 있다.
    """

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_PROCESS_SYNCHRONIZE, False, pid)

    if not handle:
        # 열 대상이 없다 — 이미 종료된 것으로 간주(경쟁의 극단값).
        return True

    try:
        wait_ms = int(timeout * 1000)
        result = kernel32.WaitForSingleObject(handle, wait_ms)
        return result == _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


# ============================================================
# 메인 프로세스 측 — 복원 요청 접수 → Helper 기동 → 창 종료 유도
# ============================================================


def spawn_helper_process(
    plan_path: Path,
    parent_pid: int,
) -> subprocess.Popen:

    if getattr(sys, "frozen", False):
        argv = [
            sys.executable,
            RESTORE_HELPER_CLI_FLAG,
            PLAN_ARG, str(plan_path),
            PARENT_PID_ARG, str(parent_pid),
        ]
    else:
        argv = [
            sys.executable, "-m", "app.desktop.restore_helper",
            RESTORE_HELPER_CLI_FLAG,
            PLAN_ARG, str(plan_path),
            PARENT_PID_ARG, str(parent_pid),
        ]

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )

    return subprocess.Popen(  # noqa: S603
        argv,
        creationflags=creationflags,
        close_fds=True,
    )


def request_restore_shutdown(
    plan: RestorePlan,
    plan_path: Path | None = None,
) -> subprocess.Popen:
    """
    복원 실행 요청을 접수한다: 계획 저장 → Helper 프로세스 기동(부모
    PID 전달) → 등록된 Desktop 창을 닫아 메인 앱을 정상 종료 경로로
    보낸다.

    창을 닫은 뒤의 종료 절차(`handle.shutdown()`, `guard.release()`)는
    `app/desktop/main.py::run()`의 기존 `finally` 블록이 그대로
    수행한다 — 이 함수는 그 경로를 변경하지 않는다.
    """

    plan_path = plan_path or default_plan_path()
    save_plan(plan_path, plan)

    parent_pid = os.getpid()
    proc = spawn_helper_process(plan_path, parent_pid)

    window = _get_desktop_window()
    if window is None:
        raise RestoreHelperError(
            "Desktop 창 컨텍스트가 등록되지 않았습니다 — 복원 실행은 "
            "Desktop 앱 프로세스 안에서만 가능합니다(테스트/CLI 단독 "
            "실행에서는 이 함수를 호출할 수 없습니다).",
        )

    try:
        window.destroy()
    except Exception as exc:  # noqa: BLE001
        raise RestoreHelperError(
            f"창 종료 요청 실패: {type(exc).__name__}",
        ) from exc

    return proc


# ============================================================
# Helper 프로세스 측 — 부모 종료 대기 → 재검증 → 실제 교체 → 재기동
# ============================================================


def _write_result(path: Path, outcome: dict) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def run_restore_in_helper_process(
    plan: RestorePlan,
    result_path: Path | None = None,
    *,
    credential_store: CredentialStore | None = None,
) -> dict:
    """
    Helper 프로세스 안에서 실제 복원을 수행한다. 이 함수는 부모
    프로세스가 이미 완전히 종료된 뒤에만 호출돼야 한다(호출자인
    `main_cli()`가 보장).

    이 함수 자신도 새 SQLAlchemy engine/session을 만들어
    `RestoreService.restore()`를 호출하지만 — 완전히 새 프로세스이므로
    다른 요청 스레드가 같은 engine을 공유할 일이 없어(Option B의
    핵심 이점) Gate R-0 실험 4b가 증명한 "restore() 자신의 중간
    쓰기가 dispose 이후에도 커넥션을 되살리는" 문제는
    `RestoreService.restore()` 자체에 적용한 수정(os.replace() 직전
    명시적 dispose, app/domains/restore/service.py)만으로 충분히
    처리된다.

    2026-09-15 전면 감사 후속(Phase 5) — `credential_store`는
    테스트가 `InMemoryCredentialStore`를 주입할 수 있도록 선택
    인자로 뒀다(None이면 실제 Helper 프로세스답게
    `WindowsCredentialStore()`를 만든다). 이 값을 생성자 인자로
    노출하지 않으면 테스트가 이 함수를 부를 때마다 실제 Windows
    Credential Manager를 건드리게 된다.
    """

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.base import Base
    from app.domains.restore.service import RestoreError
    from app.domains.restore.service import RestoreService

    if credential_store is None:
        from app.core.windows_credential_store import WindowsCredentialStore
        credential_store = WindowsCredentialStore()

    result_path = result_path or default_result_path()

    outcome: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    source_backup_path = Path(plan.source_backup_path)
    target_db_path = Path(plan.target_db_path)

    # 계획 저장 이후 실행까지 시간차가 있으므로(부모 종료 대기 포함),
    # 실행 직전 다시 한번 존재/SHA-256을 재검증한다 — 그 사이 백업
    # 파일이 지워지거나 바뀌었을 가능성을 신뢰하지 않는다.
    if not source_backup_path.exists():
        outcome["status"] = "failed"
        outcome["reason"] = "백업 파일이 더 이상 존재하지 않습니다(재검증 실패)."
        _write_result(result_path, outcome)
        return outcome

    # 2026-09-15 전면 감사 후속(Phase 5) — 예전에는 여기서 원본
    # 바이트를 직접 sha256_of_file()로 해시해 plan.expected_sha256과
    # 비교했다. 이제 백업이 암호화되어 있으면(app/domains/backup/
    # service.py가 이제 항상 그렇게 만든다) 원본 바이트는 암호문이라
    # 이 비교가 항상 실패한다(expected_sha256은 항상 평문 기준 —
    # BackupRecord.sha256 docstring 참고). 이 "계획 저장 이후 파일이
    # 바뀌었는가" 재검증은 아래 RestoreService.restore()가 내부적으로
    # 호출하는 validate_backup_file()이 암호화를 인식해 이미 똑같이
    # (그리고 올바르게) 수행하므로, 여기서 중복 검사를 별도로 유지하지
    # 않는다 — 파일이 바뀌었다면 restore()가 RestoreError로 실패하고
    # 아래 except 블록이 잡는다(결과는 동일, 실행 경로만 한 단계
    # 늦춰진다).

    pre_restore_backups_dir = Path(plan.pre_restore_backups_dir)

    engine = create_engine(f"sqlite:///{target_db_path}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    try:
        service = RestoreService(db, credential_store)
        attempt = service.restore(
            source_backup_path=source_backup_path,
            target_db_path=target_db_path,
            expected_sha256=plan.expected_sha256,
            pre_restore_backups_dir=pre_restore_backups_dir,
            triggered_by_user_id=plan.triggered_by_user_id,
        )
        outcome["status"] = "succeeded"
        outcome["restore_attempt_id"] = attempt.id
        outcome["pre_restore_backup_path"] = attempt.pre_restore_backup_path
        outcome["integrity_check_result"] = attempt.integrity_check_result

    except RestoreError as exc:
        outcome["status"] = "failed"
        outcome["reason"] = str(exc)

    except Exception as exc:  # noqa: BLE001
        outcome["status"] = "failed"
        outcome["reason"] = f"{type(exc).__name__}: {exc}"

    finally:
        db.close()
        engine.dispose()

    outcome["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_result(result_path, outcome)

    return outcome


def relaunch_homez(relaunch_argv: list[str]) -> subprocess.Popen | None:
    """
    HOMEZ를 다시 기동한다. 복원 성공/실패와 무관하게 항상 호출된다
    — `os.replace()`의 원자성 덕분에 실패한 복원은 target_db_path를
    항상 직전 상태 그대로 남기므로, 재기동 자체는 항상 안전하다
    (사용자가 조용히 "앱이 사라짐"을 겪지 않게 하기 위함).
    """

    if not relaunch_argv:
        return None

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )

    return subprocess.Popen(  # noqa: S603
        relaunch_argv,
        creationflags=creationflags,
        close_fds=True,
    )


def main_cli(argv: list[str] | None = None) -> int:
    """
    `RESTORE_HELPER_CLI_FLAG`로 재실행됐을 때의 진입점.

    흐름: 부모 프로세스 완전 종료 대기 → 계획 로드 → 복원 실행 →
    결과 기록 → 계획 파일 정리 → HOMEZ 재기동. 부모가 시간 내
    종료되지 않으면(비정상 상황) **복원을 아예 실행하지 않고** 실패로
    기록한다 — fail-closed.
    """

    import argparse

    argv = list(argv) if argv is not None else sys.argv[1:]

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(RESTORE_HELPER_CLI_FLAG, action="store_true")
    parser.add_argument(PLAN_ARG, dest="plan_path", required=True)
    parser.add_argument(
        PARENT_PID_ARG, dest="parent_pid", type=int, required=True,
    )
    args, _unknown = parser.parse_known_args(argv)

    plan_path = Path(args.plan_path)
    result_path = plan_path.parent / RESULT_FILENAME

    exited = wait_for_process_exit(args.parent_pid)
    if not exited:
        _write_result(result_path, {
            "status": "failed",
            "reason": (
                f"부모 프로세스(pid={args.parent_pid})가 제한 시간 "
                "내에 종료되지 않아 안전을 위해 복원을 실행하지 "
                "않았습니다."
            ),
        })
        return 1

    try:
        plan = load_plan(plan_path)
    except RestoreHelperError as exc:
        _write_result(result_path, {"status": "failed", "reason": str(exc)})
        return 1

    outcome = run_restore_in_helper_process(plan, result_path)

    try:
        plan_path.unlink(missing_ok=True)
    except OSError:
        pass

    relaunch_homez(plan.relaunch_argv)

    return 0 if outcome.get("status") == "succeeded" else 1


if __name__ == "__main__":

    sys.exit(main_cli())


__all__ = [
    "RESTORE_HELPER_CLI_FLAG",
    "PLAN_ARG",
    "PARENT_PID_ARG",
    "RestoreHelperError",
    "RestorePlan",
    "save_plan",
    "load_plan",
    "default_plan_path",
    "default_result_path",
    "build_relaunch_argv",
    "register_desktop_context",
    "clear_desktop_context",
    "wait_for_process_exit",
    "spawn_helper_process",
    "request_restore_shutdown",
    "run_restore_in_helper_process",
    "relaunch_homez",
    "main_cli",
]
