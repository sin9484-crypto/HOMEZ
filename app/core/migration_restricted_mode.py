"""
=========================================================
Homez OS

File : app/core/migration_restricted_mode.py

Gate G(2026-08-07) — Migration 제한 모드 서버 강제.

기존에는 미적용 Migration이 있을 때 "제한 모드"가 콘솔 클라이언트
(app/web/console.js의 homezLimitedMode/LIMITED_MODE_ALLOWED_PATHS)
에서만 강제됐다 — 서버 자체는 어떤 요청도 막지 않았으므로, 콘솔 JS를
거치지 않는 임의의 HTTP 요청(curl, 다른 클라이언트, 변조된 JS)은
미적용 Migration이 있는 상태에서도 그대로 쓰기에 성공할 수 있었다.
이 모듈은 그 서버측 강제의 상태 계층이다 — 실제 423 차단은
app/main.py의 미들웨어가 이 모듈의 `is_restricted_mode()`를 참조해
수행한다.

설계:
  - `MigrationRunner.diagnose()`는 호출마다 migrations 디렉터리를
    다시 스캔하고 모든 파일의 SHA-256을 다시 계산한다(app/database/
    migration_runner.py) — 매 요청마다 호출하기엔 비싸다. 그래서
    진단 결과를 프로세스 전역에 캐시하고, (1) 서버 시작 시 1회,
    (2) Migration 적용 성공 직후에만 재계산한다.
  - 진단 자체가 실패하면(ChecksumMismatchError 등 — 파일 변조·손상
    의심) 조용히 무시하지 않고 **fail-closed로 제한 모드를 켠 채로
    둔다** — 정상 판정이 안 되는 상태에서 쓰기를 허용하는 쪽이 훨씬
    위험하기 때문이다. 이 경우 서버 자체는 계속 뜬 채로 유지된다
    (health/최소 인증/진단은 계속 응답 가능해야 운영자가 원인을
    조사할 수 있다).
  - 캐시가 아직 한 번도 계산되지 않은 상태(이론상 lifespan 훅이
    아직 실행되지 않은 극초기 요청)도 fail-closed로 제한 모드로
    취급한다.
=========================================================
"""

import sqlite3
import threading
from dataclasses import dataclass
from dataclasses import field

from app.database.migration_runner import MigrationRunner

_lock = threading.Lock()
_state: dict | None = None


@dataclass
class RestrictedModeState:

    restricted: bool
    pending_files: list[str] = field(default_factory=list)
    error: str | None = None


def _real_migration_paths():
    """
    2026-08-20 4차 지시(DB 경로 단일 계약) — 이 함수가 검사해야 하는
    DB는 "실제 homez.db"가 아니라 "지금 이 요청을 실제로 처리하는
    SQLAlchemy 세션이 붙어 있는 DB"다. 그래서 `app/database/session.py`
    가 엔진을 만들 때 쓰는 것과 동일한 `settings.DATABASE_URL`을
    `resolve_sqlite_path()`(app/core/first_admin_setup.py — 이미
    company_recovery_setup.py/desktop_setup.py가 같은 목적으로 쓰는
    공용 헬퍼)로 그대로 해석한다. 이전에는 여기서 `get_homez_db_path
    (confirm=True)`를 직접 호출해 DATABASE_URL을 완전히 무시하고
    "실제" homez.db 고정 경로만 봤다 — 격리 테스트 DB로 서버를 띄워도
    이 검사기만은 항상 실제 운영 DB의 미적용 Migration 여부로 전역
    423을 걸었다는 결함(2026-08-20 3차 라운드에서 실제로 재현·확인됨).
    DATABASE_URL을 아예 설정하지 않은 기본(운영/Desktop) 실행에서는
    `_default_database_url()`(app/core/config.py)이 애초에 이
    `get_homez_db_path(confirm=True)`와 동일한 절대경로로 계산되므로,
    이 변경은 기본 동작을 조금도 바꾸지 않는다 — DATABASE_URL을
    명시적으로 override한 경우에만 그 override를 실제로 존중하게
    고친 것이다. `_diagnose_pending()`은 항상 읽기 전용(mode=ro +
    PRAGMA query_only=ON)이므로 이 함수 자체가 어떤 DB에도 쓰지
    않는다.
    """

    from pathlib import Path

    from app.core.first_admin_setup import resolve_sqlite_path
    from app.desktop import paths

    db_path = Path(resolve_sqlite_path())
    migrations_dir = paths.get_repo_root() / "migrations"

    return db_path, migrations_dir


def _diagnose_pending(db_path, migrations_dir) -> tuple[list[str], str | None]:
    """
    읽기 전용으로만 진단한다(mode=ro + PRAGMA query_only=ON) — 어떤
    경우에도 이 함수 자체가 DB에 쓰기를 하지 않는다. DB 파일이 아직
    없으면(신규 설치 직전) pending 없음으로 취급한다 — 신규 설치는
    bootstrap_environment()가 즉시 전체를 순차 적용하므로 승인 대기
    상태 자체가 존재하지 않는다.
    """

    if not db_path.exists():
        return [], None

    runner = MigrationRunner(db_path, migrations_dir)

    try:
        ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        ro_conn.execute("PRAGMA query_only = ON")
        try:
            diagnosis = runner.diagnose(ro_conn)
        finally:
            ro_conn.close()
    except Exception as exc:  # noqa: BLE001 — 진단 실패는 fail-closed로 처리
        return [], f"{type(exc).__name__}: {exc}"

    return list(diagnosis["pending"]), None


def refresh_restricted_mode_state() -> RestrictedModeState:
    """
    실제로 진단을 다시 수행하고 캐시를 갱신한다. 호출 시점:
      - FastAPI lifespan 시작 시(app/main.py) — 서버 재시작마다 재계산.
      - Migration 적용(app/core/migration_approval.py::approve_migration)
        성공 직후 — 방금 적용한 파일이 더 이상 pending에 남지 않도록
        즉시 재계산.

    2026-09-16 개인 베타 잔여 작업(Phase 5, 10-18) — 서버 관리자
    알림(Migration 제한 모드 진입)은 **의도적으로 이 함수 안에서
    보내지 않는다.** 처음에는 여기서 바로 `SessionLocal()`을 열어
    보냈으나, 그러면 이 함수가 FastAPI lifespan 시작 시(스키마가
    아직 적용되지 않았을 수도 있는 가장 이른 시점)마다 즉시 ORM
    세션을 여는 부작용이 생겨, "이 진단 경로는 세션을 전혀 열지
    않는다"는 기존 설계 불변식(`_diagnose_pending()`이 굳이 원시
    `sqlite3`(mode=ro)만 쓰는 이유와 같다)을 깨고
    `tests/test_homez_desktop.py::test_successful_start_and_shutdown_full_cycle`
    (스키마 미적용 상태에서도 `/health`·`/console`만 호출하면
    SessionLocal이 단 한 번도 불려서는 안 된다는 기존 회귀)를
    실패시켰다. 대신 실제로 쓰기 요청이 제한 모드에 막히는 순간
    (`app/main.py::_enforce_migration_restricted_mode()`의 423
    분기 — 이미 그 시점에 실제 영향이 발생했다는 뜻이므로 세션을
    여는 비용이 정당화된다)에만 알린다.
    """

    global _state

    db_path, migrations_dir = _real_migration_paths()
    pending, error = _diagnose_pending(db_path, migrations_dir)

    new_state = RestrictedModeState(
        restricted=bool(pending) or error is not None,
        pending_files=pending,
        error=error,
    )

    with _lock:
        _state = new_state

    return new_state


def is_restricted_mode() -> bool:
    """
    캐시된 상태만 읽는다(디스크 I/O 없음) — 요청마다 호출해도 저렴
    하다. 아직 한 번도 계산되지 않았다면(이론상 lifespan 훅이 아직
    실행되지 않은 상태) fail-closed로 제한 모드로 취급한다.
    """

    with _lock:
        if _state is None:
            return True
        return _state.restricted


def get_restricted_mode_state() -> RestrictedModeState:
    """진단·상태 표시용 — 캐시된 스냅샷을 그대로 반환한다."""

    with _lock:
        if _state is None:
            return RestrictedModeState(restricted=True, pending_files=[], error=None)
        return _state


def reset_restricted_mode_state_for_tests() -> None:
    """테스트 전용 — 캐시를 완전히 초기화한다(다음 조회는 미계산 상태로 취급)."""

    global _state

    with _lock:
        _state = None


__all__ = [
    "RestrictedModeState",
    "refresh_restricted_mode_state",
    "is_restricted_mode",
    "get_restricted_mode_state",
    "reset_restricted_mode_state_for_tests",
]
