"""
=========================================================
Homez OS

File : app/domains/backup/service.py

Gate Y-1(2026-08-12) — 백업 엔진. `app/database/migration_runner.py::
MigrationRunner.create_backup()`이 이미 쓰는 SQLite 온라인 백업 API
패턴(`sqlite3.Connection.backup()` — 쓰기 중인 DB에도 안전, WAL/
저널 상태와 무관하게 일관된 스냅샷을 만든다)과 백업 직후
`PRAGMA integrity_check` 검증을 그대로 재사용한다. 차이점은
Migration 전용이 아니라 범용(수동 백업 등)으로 쓸 수 있도록
독립 도메인으로 분리하고, 결과를 `BackupRecord` 이력으로 남긴다는
점이다.

무결성 검증에 실패하면(예: 디스크 오류, 백업 도중 중단) 그 백업
파일은 이력 테이블에 절대 기록하지 않는다 — 손상된 백업을 "성공"
으로 보이게 하는 것은 복구 시점에 더 위험하다(있는 줄 알았던
백업이 실제로는 못 쓰는 상태).
=========================================================
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.windows_credential_store import CredentialStore
from app.domains.backup.encryption import encrypt_file
from app.domains.backup.encryption import get_or_create_backup_encryption_key
from app.domains.backup.model import BackupRecord
from app.domains.backup.repository import BackupRepository

TRIGGER_SOURCE_MANUAL = "manual"
TRIGGER_SOURCE_PRE_MIGRATION = "pre_migration"
TRIGGER_SOURCE_PRE_RESTORE = "pre_restore"
# 2026-09-09 Phase 5(HOMEZ_USER_OPERATION_SETTINGS.md 1·11·14번 —
# "DB 복구 가능 여부를 매주 자동 또는 안내 기반으로 시험하고 결과를
# 기록한다") — app/domains/restore/service.py::RestoreService.
# run_weekly_rehearsal()이 이 값으로 만든 백업을 곧바로 검증용으로
# 쓰고 리허설이 끝나면 삭제한다(장기 보관 대상 아님 — 그래서 별도
# retention 정책이 필요 없다). 실제 주기 실행(Phase 6, 스케줄러)이
# 붙기 전까지는 관리자가 수동으로 트리거한다.
TRIGGER_SOURCE_SCHEDULED_REHEARSAL = "scheduled_rehearsal"

# 2026-08-15 V7 Gate 8 — 보존 정책 기본값. 이 저장소는 스케줄러/cron
# 도메인이 없어(HOMEZ_EMPTY_SCAFFOLD_INVENTORY.md의 "V8 이후 후보 —
# 자동화·백그라운드 처리 인프라" 참고) 자동 주기 실행은 범위 밖이다
# — 이 상수는 "몇 개까지는 보존 대상으로 본다"는 조회 기준선일
# 뿐이고, 실제 삭제는 어떤 코드 경로에서도 자동 수행하지 않는다.
DEFAULT_RETENTION_KEEP_COUNT = 30

_VALID_TRIGGER_SOURCES = frozenset(
    {
        TRIGGER_SOURCE_MANUAL,
        TRIGGER_SOURCE_PRE_MIGRATION,
        TRIGGER_SOURCE_PRE_RESTORE,
        TRIGGER_SOURCE_SCHEDULED_REHEARSAL,
    },
)

_SHA256_CHUNK_SIZE = 1024 * 1024


class BackupError(Exception):
    pass


class BackupService:

    def __init__(
        self,
        db: Session,
        credential_store: CredentialStore,
    ):
        """2026-09-15 전면 감사 후속(Phase 5) — credential_store가
        필수 인자가 됐다. 실제 백업 파일은 반드시 암호화한다는
        요구사항(HOMEZ_USER_OPERATION_SETTINGS.md 11번)을 "깜빡하고
        인자를 안 넘기면 조용히 평문으로 저장되는" 방식으로 두지
        않기 위해서다 — 테스트에서도 InMemoryCredentialStore를
        명시적으로 넘겨야 한다."""

        self.repository = BackupRepository(db)
        self.credential_store = credential_store

    def create_backup(
        self,
        *,
        source_db_path: Path,
        backups_dir: Path,
        trigger_source: str,
        triggered_by_user_id: int | None = None,
        label: str | None = None,
    ) -> BackupRecord:

        if trigger_source not in _VALID_TRIGGER_SOURCES:
            raise BackupError(
                f"알 수 없는 trigger_source입니다: {trigger_source!r}",
            )

        source_db_path = Path(source_db_path)

        if not source_db_path.exists():
            raise BackupError(
                f"원본 DB 파일이 존재하지 않습니다: {source_db_path}",
            )

        backups_dir = Path(backups_dir)
        backups_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backups_dir / f"homez_backup_{timestamp}.db"

        if backup_path.resolve() == source_db_path.resolve():
            raise BackupError(
                f"백업 경로가 원본 DB 경로와 동일합니다 — 차단합니다: "
                f"{backup_path}",
            )

        src = sqlite3.connect(
            f"file:{source_db_path}?mode=ro",
            uri=True,
        )
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        verify = sqlite3.connect(
            f"file:{backup_path}?mode=ro",
            uri=True,
        )
        try:
            verify.execute("PRAGMA query_only=ON")
            integrity = verify.execute(
                "PRAGMA integrity_check",
            ).fetchone()[0]
        finally:
            verify.close()

        if integrity != "ok":
            raise BackupError(
                f"백업 무결성 검증 실패: {backup_path} "
                f"(integrity_check={integrity}) — 이 백업은 이력에 "
                f"기록되지 않았습니다.",
            )

        # 2026-09-15 전면 감사 후속(Phase 5) — sha256은 항상 "평문
        # 내용"의 해시로 남긴다(BackupRecord.sha256 docstring 참고).
        # 암호화는 integrity_check·sha256 계산이 끝난 뒤 마지막
        # 단계에서만 수행한다 — SQLite는 암호화된 파일을 열어
        # integrity_check할 수 없으므로 순서를 반드시 이렇게 지킨다.
        sha256 = sha256_of_file(backup_path)

        encryption_key = get_or_create_backup_encryption_key(
            self.credential_store,
        )
        encrypt_file(backup_path, backup_path, encryption_key)

        file_size_bytes = backup_path.stat().st_size

        record = BackupRecord(
            file_path=str(backup_path),
            file_size_bytes=file_size_bytes,
            sha256=sha256,
            integrity_check_result=integrity,
            trigger_source=trigger_source,
            triggered_by_user_id=triggered_by_user_id,
            label=label,
            is_encrypted=True,
        )

        return self.repository.create(record)

    def list_backups(
        self,
        limit: int = 50,
    ) -> list[BackupRecord]:

        return self.repository.list_recent(limit)

    def list_backups_beyond_retention(
        self,
        keep_count: int = DEFAULT_RETENTION_KEEP_COUNT,
    ) -> list[BackupRecord]:
        """
        읽기 전용 보존 정책 조회 — 아무것도 삭제하지 않는다(설계
        의도는 service.py 상단 `DEFAULT_RETENTION_KEEP_COUNT` 주석
        참고).
        """

        return self.repository.list_beyond_retention(keep_count)


def sha256_of_file(path: Path) -> str:

    digest = hashlib.sha256()

    with open(path, "rb") as f:
        while True:
            chunk = f.read(_SHA256_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)

    return digest.hexdigest()


__all__ = [
    "BackupService",
    "BackupError",
    "TRIGGER_SOURCE_MANUAL",
    "TRIGGER_SOURCE_PRE_MIGRATION",
    "TRIGGER_SOURCE_PRE_RESTORE",
    "TRIGGER_SOURCE_SCHEDULED_REHEARSAL",
    "sha256_of_file",
]
