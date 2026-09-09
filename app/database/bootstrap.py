"""
=========================================================
Homez OS

File : app/database/bootstrap.py

최초 실행 부트스트랩 — %LOCALAPPDATA%\\HOMEZ\\{data,logs,backups,
config,media} 디렉터리를 만들고, 신규 설치면 빈 DB를 만든 뒤 모든
Migration을 순차 적용하고, 기존 DB면 미적용 Migration만 적용한다.

개발용 homez.db는 실행 파일에 포함하지 않는다 — 이 함수는 항상
paths.get_homez_db_path()가 가리키는 위치(개발 모드: 저장소 루트,
패키징 모드: %LOCALAPPDATA%\\HOMEZ\\data)에서만 동작한다.

기존 DB에 대해 실제로 Migration을 적용하기 직전에는 항상 새 백업을
먼저 만든다(신규 설치는 빈 DB이므로 백업 대상 데이터가 없어
생략한다).

순서 계약(2026-08-02 CTO 보안 보완 Gate R1 — 이전 버전은 reconcile_
backfill()이 schema_migrations에 쓰기를 한 "뒤"에야 create_backup()을
호출해, 백업이 실패하면 이미 쓰기가 발생한 상태로 남는 결함이
있었다):
  1. 읽기 전용 연결(mode=ro)로 diagnose()만 수행 — 이 단계는 DB에
     어떤 쓰기도 하지 않는다(checksum 불일치 등은 여기서 예외로
     드러나며, 이 경우도 쓰기 0건이 보장된다).
  2. 진단 결과로 쓰기가 필요한지(backfill_needed 또는 pending이
     하나라도 있는지) 판단한다.
  3. 쓰기가 필요하면 그때만 원본 DB의 백업을 만들고 무결성을
     검증한다(create_backup() 실패 시 이 함수 전체가 예외를 던지며,
     그 시점까지 실제 DB에는 어떤 쓰기도 없었다).
  4. 백업이 성공(또는 애초에 쓰기가 필요 없음)한 뒤에만 schema_
     migrations를 만들고 backfill/apply를 수행한다.
=========================================================
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.database.migration_runner import MigrationRunner

_AUDIT_LOGS_TABLE_EXISTS_SQL = (
    "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_logs'"
)


@dataclass
class PendingMigrationPlan:
    """
    2026-08-05 CTO 재검증 지시 Gate E — Migration 승인 UX. 실제 DDL을
    실행하는 `pending` Migration만 담는다(`backfill_needed`는 이미
    존재하는 객체의 이력만 채우는 것이라 항상 자동 처리하며 이 계획에
    포함하지 않는다).
    """

    files: list[str]
    targets_by_file: dict[str, dict[str, list[str]]]
    backup_path_preview: Path


@dataclass
class BootstrapResult:

    is_new_install: bool
    backfilled: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    backup_path: Path | None = None
    already_applied: list[str] = field(default_factory=list)
    migration_approval_required: bool = False
    pending_migration_plan: PendingMigrationPlan | None = None
    integrity_check_result: str | None = None


def _write_migration_audit_event(
    conn: sqlite3.Connection, action: str, description: str,
) -> None:
    """
    2026-08-05 CTO 재검증 지시 Gate E — Migration 승인·적용·실패를
    감사 로그에 남긴다. ORM 세션이 아니라 이 모듈이 이미 들고 있는
    raw sqlite3 커넥션을 그대로 써서, bootstrap 경로에 SQLAlchemy
    Session 의존성을 새로 추가하지 않는다. `audit_logs` 테이블이 아직
    없는 아주 이른 부트스트랩 단계(신규 설치 등)에서는 조용히
    건너뛴다(이 함수 자체가 실패해도 부트스트랩 전체를 막으면 안
    된다 — 감사 기록은 최선 노력이지 핵심 경로가 아니다).
    """

    try:
        exists = conn.execute(_AUDIT_LOGS_TABLE_EXISTS_SQL).fetchone()
        if exists is None:
            return

        conn.execute(
            "INSERT INTO audit_logs (company_id, user_id, action, entity, "
            "entity_id, description, ip_address) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                None, None, action, "MigrationRunner", "bootstrap",
                f"{description} ({datetime.now(timezone.utc).isoformat()})",
                None,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        # 감사 로그 기록 실패가 부트스트랩 자체를 막지 않는다 — 다만
        # 조용히 삼키지는 않고 최소한 흔적을 남긴다.
        try:
            conn.rollback()
        except sqlite3.Error:
            pass


def _record_backup_history_event(
    conn: sqlite3.Connection, backup_path: Path, label: str,
) -> None:
    """
    2026-08-15 V7 Gate 8 — 이 모듈의 pre-migration 백업
    (`MigrationRunner.create_backup()`)은 파일만 만들 뿐
    `app/domains/backup`의 `BackupRecord` 이력에는 전혀 남지 않아,
    운영자가 백업 이력 화면(`GET /backups`)에서 이 백업의 존재를 알
    수 없었다(Gate 8 감사 중 발견 — 데이터 안전성 문제는 아니고
    가시성 결함). `_write_migration_audit_event()`와 동일한 방어
    원칙을 따른다: `backup_records` 테이블이 아직 없는 이른
    부트스트랩 단계(신규 설치 등)에서는 조용히 건너뛰고, 이 기록이
    실패해도 부트스트랩 자체를 절대 막지 않는다 — 파일 자체는
    `create_backup()`이 이미 무결성 검증까지 마친 뒤이므로, 여기서
    실패해도 "이력에 안 보인다"는 열화일 뿐이다.
    """

    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='backup_records'",
        ).fetchone()
        if exists is None:
            return

        from app.domains.backup.service import sha256_of_file

        conn.execute(
            "INSERT INTO backup_records (file_path, file_size_bytes, "
            "sha256, integrity_check_result, trigger_source, "
            "triggered_by_user_id, label, created_at) VALUES "
            "(?, ?, ?, 'ok', 'pre_migration', NULL, ?, ?)",
            (
                str(backup_path),
                backup_path.stat().st_size,
                sha256_of_file(backup_path),
                label,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except (sqlite3.Error, OSError):
        try:
            conn.rollback()
        except sqlite3.Error:
            pass


def ensure_directories() -> None:

    from app.desktop import paths

    for getter in (
        paths.get_data_dir, paths.get_logs_dir, paths.get_backups_dir,
        paths.get_config_dir, paths.get_media_dir,
    ):
        getter().mkdir(parents=True, exist_ok=True)


def bootstrap_environment(
    db_path: Path | None = None, migrations_dir: Path | None = None,
    backups_dir: Path | None = None,
    approved_migration_files: list[str] | None = None,
    *, confirm_production_path: bool = False,
) -> BootstrapResult:
    """
    2026-08-05 CTO 재검증 지시 Gate E — `approved_migration_files`가
    없으면(기본값) 실제 DDL을 실행하는 `pending` Migration은 절대
    자동 적용하지 않는다 — `backfill_needed`(이미 존재하는 객체의
    이력만 채우는 것, 실제 스키마 변경 없음)만 계속 자동 처리한다.
    `pending`이 있는데 승인이 없으면 `BootstrapResult.migration_
    approval_required=True`와 `pending_migration_plan`을 채워
    반환하고, 실제 DB에는 그 파일들을 적용하지 않는다 — 호출자
    (Desktop UI)가 사용자에게 명시적으로 보여주고 승인받은 뒤에만
    `approved_migration_files=diagnosis["pending"]`를 넘겨 다시
    호출해야 실제로 적용된다. 이 값이 diagnose() 시점의 실제 pending
    목록과 정확히 일치하지 않으면(다른 파일이 그 사이 추가된 경우
    등) 역시 적용하지 않는다 — "그때 보여준 것과 정확히 같은 것만
    적용"을 강제한다.

    2026-08-11 Gate V-1 — `db_path`를 명시하지 않으면(기본값 None)
    반드시 `confirm_production_path=True`도 함께 넘겨야 한다. 그렇지
    않으면 즉시 `ProductionDbAccessNotConfirmedError`를 던지고 아무
    것도 하지 않는다(2026-08-10 사고 재발 방지 — 경로 격리 없이
    실행된 이 함수가 실제 homez.db에 승인 없는 backfill 이력을 남긴
    적이 있다, docs/V6_EXECUTION_LEDGER.md "Gate U 사고 정정" 절
    참고). 공식 Desktop 부팅 흐름(app/desktop/main.py)만 이 값을
    True로 넘긴다 — 테스트·임시 스크립트는 항상 `db_path`에 자체
    임시 경로를 명시해야 한다.
    """

    from app.desktop import paths

    if db_path is None and not confirm_production_path:
        raise paths.ProductionDbAccessNotConfirmedError(
            "bootstrap_environment()는 db_path 없이 호출될 때 "
            "confirm_production_path=True도 함께 요구합니다 — 실제 "
            "운영 DB를 암묵적으로 대상으로 삼는 사고를 막기 위한 "
            "안전장치입니다. 테스트·임시 스크립트라면 db_path에 자체 "
            "임시 경로를 넘기세요.",
        )

    if db_path is None or migrations_dir is None or backups_dir is None:
        # 실제 경로 계약(%LOCALAPPDATA%\HOMEZ\* 등)이 필요한 인자만
        # 최소한으로 채운다 — 테스트에서 db_path/migrations_dir/
        # backups_dir를 전부 override하면 ensure_directories() 외에는
        # 실제 저장소 경로를 전혀 건드리지 않는다.
        ensure_directories()

    if db_path is None:
        db_path = paths.get_homez_db_path(confirm=True)
    if migrations_dir is None:
        migrations_dir = paths.get_repo_root() / "migrations"
    if backups_dir is None:
        backups_dir = paths.get_backups_dir()

    is_new_install = not db_path.exists()
    runner = MigrationRunner(db_path, migrations_dir)

    if is_new_install:
        # 빈 DB이므로 백업 대상 데이터가 없다 — 곧바로 전체 순차
        # 적용으로 진행한다.
        db_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(str(db_path)).close()

        conn = sqlite3.connect(str(db_path))
        try:
            applied = runner.apply_pending(conn)

            return BootstrapResult(
                is_new_install=True, backfilled=[], applied=applied,
                backup_path=None, already_applied=[],
            )
        finally:
            conn.close()

    # 기존 설치 — 1) 읽기 전용 진단(쓰기 0건 보장).
    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ro_conn.execute("PRAGMA query_only = ON")
    try:
        diagnosis = runner.diagnose(ro_conn)
    finally:
        ro_conn.close()

    pending_files = diagnosis["pending"]

    # pending 적용은 "이번 호출에서 diagnose()가 실제로 본 파일 목록과
    # 정확히 같은 것"을 승인받았을 때만 허용한다 — 부분 승인이나
    # 다른 시점의 승인을 재사용하지 못하게 막는다.
    pending_approved = (
        bool(pending_files)
        and approved_migration_files is not None
        and set(approved_migration_files) == set(pending_files)
    )

    if pending_files and not pending_approved:
        # 승인 없이는 실제 DDL을 실행하지 않는다 — backfill_needed는
        # 이미 존재하는 객체의 이력만 채우는 것이라(스키마 변경 없음)
        # 계속 자동 처리한다.
        backfilled: list[str] = []
        backup_path = None
        if diagnosis["backfill_needed"]:
            backup_path = runner.create_backup(
                backups_dir, "bootstrap_migration",
            )
            conn = sqlite3.connect(str(db_path))
            try:
                _record_backup_history_event(
                    conn, backup_path, "bootstrap_migration",
                )
                backfilled = runner.reconcile_backfill(conn)
                _write_migration_audit_event(
                    conn, "MIGRATION_PENDING_DETECTED",
                    f"승인 대기 중인 Migration {len(pending_files)}건 발견"
                    f"(백필 {len(backfilled)}건은 자동 처리됨): "
                    f"{', '.join(pending_files)}",
                )
            finally:
                conn.close()
        else:
            # 감사 로그만 남기려고 새 쓰기 연결을 열지는 않는다 —
            # audit_logs 테이블이 있는 기존 DB에서만, 별도 커넥션으로
            # 최선 노력 기록만 남긴다.
            log_conn = sqlite3.connect(str(db_path))
            try:
                _write_migration_audit_event(
                    log_conn, "MIGRATION_PENDING_DETECTED",
                    f"승인 대기 중인 Migration {len(pending_files)}건 발견: "
                    f"{', '.join(pending_files)}",
                )
            finally:
                log_conn.close()

        targets = runner.describe_pending_targets(pending_files)
        backup_preview = (
            backups_dir
            / f"homez_pre_bootstrap_migration_"
              f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )

        return BootstrapResult(
            is_new_install=False,
            backfilled=backfilled,
            applied=[],
            backup_path=backup_path,
            already_applied=diagnosis["already_applied"],
            migration_approval_required=True,
            pending_migration_plan=PendingMigrationPlan(
                files=list(pending_files),
                targets_by_file=targets,
                backup_path_preview=backup_preview,
            ),
        )

    needs_write = bool(diagnosis["backfill_needed"]) or pending_approved

    # 2) 쓰기가 필요할 때만, mutation 이전에 백업부터 만든다.
    backup_path = None
    if needs_write:
        backup_path = runner.create_backup(backups_dir, "bootstrap_migration")

    # 3) 백업이 끝난 뒤에만 실제 쓰기(backfill/apply)를 수행한다.
    conn = sqlite3.connect(str(db_path))
    try:
        if backup_path is not None:
            _record_backup_history_event(
                conn, backup_path, "bootstrap_migration",
            )

        if pending_approved:
            _write_migration_audit_event(
                conn, "MIGRATION_APPLY_APPROVED",
                f"사용자 승인으로 Migration {len(pending_files)}건 적용 "
                f"시작: {', '.join(pending_files)}",
            )

        backfilled = (
            runner.reconcile_backfill(conn)
            if diagnosis["backfill_needed"] else []
        )

        try:
            applied = (
                runner.apply_pending(conn) if pending_approved else []
            )
        except Exception as exc:
            _write_migration_audit_event(
                conn, "MIGRATION_APPLY_FAILED",
                f"Migration 적용 실패: {type(exc).__name__}: {exc} — "
                f"백업 위치: {backup_path}",
            )
            raise

        integrity_check_result = None
        if applied:
            integrity_check_result = conn.execute(
                "PRAGMA integrity_check",
            ).fetchone()[0]

            # Gate G(2026-08-07): 이 저장소의 모든 도메인은 FK를 쓰지
            # 않는다(model.py 헤더에 명시된 설계 원칙) — SQLite
            # 자체에서도 FK를 강제하지 않으므로 위반이 나올 수는
            # 없지만, 적용 직후 명시적으로 검사해 그 사실을 감사
            # 로그에 남긴다(향후 어떤 Migration이 FK를 도입하더라도
            # 이 검사가 그대로 유효하도록).
            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_violations:
                _write_migration_audit_event(
                    conn, "MIGRATION_APPLY_FOREIGN_KEY_VIOLATION",
                    f"Migration {len(applied)}건 적용 후 foreign_key_check "
                    f"위반 {len(fk_violations)}건 발견: "
                    f"{', '.join(applied)}",
                )
                raise RuntimeError(
                    f"Migration 적용 후 foreign_key_check에서 "
                    f"{len(fk_violations)}건의 위반이 발견됐습니다 — "
                    "적용 전 자동 백업에서 복구해야 합니다.",
                )

            _write_migration_audit_event(
                conn, "MIGRATION_APPLIED",
                f"Migration {len(applied)}건 적용 완료: "
                f"{', '.join(applied)} — integrity_check="
                f"{integrity_check_result}, foreign_key_check="
                f"{len(fk_violations)}건 위반",
            )

        return BootstrapResult(
            is_new_install=False,
            backfilled=backfilled,
            applied=applied,
            backup_path=backup_path,
            already_applied=diagnosis["already_applied"],
            migration_approval_required=False,
            integrity_check_result=integrity_check_result,
        )

    finally:
        conn.close()


__all__ = [
    "BootstrapResult",
    "PendingMigrationPlan",
    "ensure_directories",
    "bootstrap_environment",
]
