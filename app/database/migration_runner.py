"""
=========================================================
Homez OS

File : app/database/migration_runner.py

Migration Runner — 파일명 순서대로 정확히 1회 적용, checksum
불일치·순서 역전·부분 적용·중복 적용을 차단한다.

핵심 설계:
  - schema_migrations 테이블(이 러너 자신의 부기 테이블 — 도메인
    Migration 파일이 아니므로 CREATE TABLE IF NOT EXISTS를 여기서만
    예외적으로 사용한다)에 filename/checksum/applied_at/status를
    기록한다.
  - 이 러너가 처음 붙는 기존 DB(추적 이전에 이미 일부 Migration이
    적용돼 있던 상태)를 위해 reconcile_backfill()을 제공한다 — 각
    Migration 파일이 만드는 테이블/인덱스가 전부 이미 존재하면
    "BACKFILLED"로 이력만 채우고 다시 실행하지 않는다. 일부만
    존재하면(부분 적용) 절대 자동으로 넘어가지 않고 즉시 예외를
    던진다 — 사람이 직접 진단해야 하는 상태다.
  - 이미 이력에 있는 파일은 현재 파일 내용의 checksum을 다시 계산해
    저장된 값과 대조한다 — 다르면 즉시 중단(변조/손상 감지).
  - 순서 역전: 이미 적용된 파일들 중 가장 늦은 filename보다 사전순
    으로 앞서는 미적용 파일이 있으면 중단한다(그 파일은 원래
    "먼저" 실행됐어야 했다는 뜻).
  - apply_pending()은 대상 테이블/인덱스가 하나라도 이미 존재하면
    실행 자체를 시도하지 않고 중단한다(중복 적용 차단, 사전 확인).
    단, `CREATE TABLE/INDEX IF NOT EXISTS`로 선언된 대상은 예외다 —
    이미 존재해도 SQLite가 조용히 no-op으로 처리하도록 파일 작성자가
    의도한 것이므로 충돌로 취급하지 않는다(신규 설치에서 여러
    Migration 파일이 순차 적용되며 뒤 파일이 앞 파일과 같은 객체를
    의도적으로 다시 선언하는 문서화된 안전장치 패턴 — 예:
    20260727_add_settlement_ledger_unique_index.sql 참고). 파일
    실행이 실패하면 이력에 기록하지 않는다(그 파일은 계속 "미적용"
    상태로 남아 재시도 가능 — 최초 CREATE TABLE 실패라면 SQLite 자체
    Transaction이 원자적으로 rollback한다).
  - diagnose()는 진짜 읽기 전용이다 — schema_migrations 테이블이
    없으면 존재 여부만 sqlite_master로 확인하고 빈 이력으로 취급할
    뿐, 만들지 않는다(get_history_readonly()). 이 테이블을 실제로
    만드는 것(ensure_history_table())은 오직 쓰기가 필요하다고 이미
    판단된 mutation 경로(reconcile_backfill()/apply_pending())가,
    쓸 내용이 실제로 있을 때만 호출한다 — 진단만 하고 아무것도 쓰지
    않는 호출은 DB 파일을 조금도 건드리지 않는다는 계약이다.
=========================================================
"""

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path


class MigrationRunnerError(Exception):
    """Migration Runner 자체의 진단 가능한 오류(기반 클래스)."""


class ChecksumMismatchError(MigrationRunnerError):
    """이미 적용/백필된 Migration 파일의 내용이 기록된 checksum과 다르다."""


class OrderInversionError(MigrationRunnerError):
    """이미 적용된 최신 파일보다 사전순으로 앞서는 미적용 파일이 있다."""


class PartialApplicationError(MigrationRunnerError):
    """한 Migration 파일의 대상 객체 중 일부만 DB에 존재한다(부분 적용)."""


class DuplicateApplicationError(MigrationRunnerError):
    """대상 객체가 이미 존재하는데 다시 적용을 시도했다."""


class MigrationExecutionError(MigrationRunnerError):
    """Migration 파일 실행 자체가 실패했다(원본 예외를 감싼다)."""


@dataclass(frozen=True)
class MigrationRecord:

    filename: str
    checksum: str
    applied_at: str
    status: str  # APPLIED / BACKFILLED
    execution_ms: int | None
    notes: str | None


_CREATE_TABLE_RE = re.compile(r"^CREATE TABLE(?: IF NOT EXISTS)? (\w+)", re.M)
_CREATE_INDEX_RE = re.compile(
    r"^CREATE(?: UNIQUE)? INDEX(?: IF NOT EXISTS)? (\w+)", re.M,
)
# 대상이 이미 존재해도 안전한(SQLite 자체가 no-op으로 처리하는) 구문만
# 골라낸다 — apply_pending()의 충돌 사전 검사가 "의도적으로 IF NOT
# EXISTS를 쓴 문장"까지 막지 않도록 구분하는 데 쓴다.
_CREATE_TABLE_GUARDED_RE = re.compile(
    r"^CREATE TABLE IF NOT EXISTS (\w+)", re.M,
)
_CREATE_INDEX_GUARDED_RE = re.compile(
    r"^CREATE(?: UNIQUE)? INDEX IF NOT EXISTS (\w+)", re.M,
)
_DROP_TABLE_RE = re.compile(r"^DROP TABLE(?: IF EXISTS)? (\w+)", re.M)
_CREATE_INDEX_ON_RE = re.compile(
    r"^CREATE(?: UNIQUE)? INDEX(?: IF NOT EXISTS)? (\w+) ON (\w+)", re.M,
)


def _non_comment_sql(content: str) -> str:

    lines = [
        line for line in content.splitlines()
        if not line.strip().startswith("--")
    ]
    return "\n".join(lines)


def _extract_target_objects(content: str) -> tuple[list[str], list[str]]:

    executed = _non_comment_sql(content)
    tables = _CREATE_TABLE_RE.findall(executed)
    indexes = _CREATE_INDEX_RE.findall(executed)

    return tables, indexes


def _extract_guarded_targets(content: str) -> set[str]:
    """
    `CREATE TABLE/INDEX IF NOT EXISTS ...`로 선언된 대상 이름만 모은다.
    이런 대상은 이미 존재해도 SQLite가 조용히 건너뛰므로, apply_pending()
    의 사전 충돌 검사에서 예외로 취급한다(예: 20260727_add_settlement_
    ledger_unique_index.sql — 20260727_00_create_funding_settlement_
    schema.sql이 같은 인덱스를 이미 만든 뒤에 실행돼도 의도된 no-op
    안전장치로 남는다).
    """

    executed = _non_comment_sql(content)

    return (
        set(_CREATE_TABLE_GUARDED_RE.findall(executed))
        | set(_CREATE_INDEX_GUARDED_RE.findall(executed))
    )


def _extract_superseded_targets(content: str) -> set[str]:
    """
    "<이름>_new로 CREATE → 원본 DROP TABLE → RENAME" 기법을 쓰는
    테이블 재생성형 Migration을 위한 국소 보정. 같은 파일 안에서
    CREATE TABLE/INDEX보다 앞선 줄에 그 대상(테이블 자신, 또는
    인덱스가 속한 테이블)을 DROP TABLE한 적이 있다면, 이 스크립트를
    실제로 실행하는 시점에는 그 이름이 이미 사라진 뒤이므로 "현재 DB에
    이미 존재하는 충돌"로 취급하지 않는다.

    이 함수는 파일 안의 줄 순서만 본다(실제 sqlite3.executescript()가
    실행하는 순서와 동일 — 이 저장소의 Migration 파일은 항상 한 문장이
    한 줄에서 시작하는 컨벤션을 따른다). DROP TABLE보다 뒤에 나오는
    CREATE만 보정 대상이다 — 순서가 반대(CREATE가 먼저, DROP이 나중)면
    이 함수는 아무것도 보정하지 않는다(그 경우는 애초에 재생성 기법이
    아니다).
    """

    executed = _non_comment_sql(content)
    lines = executed.splitlines()

    dropped_tables: set[str] = set()
    superseded: set[str] = set()

    for line in lines:
        drop_match = _DROP_TABLE_RE.match(line)
        if drop_match:
            dropped_tables.add(drop_match.group(1))
            continue

        create_table_match = _CREATE_TABLE_RE.match(line)
        if create_table_match:
            name = create_table_match.group(1)
            if name in dropped_tables:
                superseded.add(name)
            continue

        create_index_match = _CREATE_INDEX_ON_RE.match(line)
        if create_index_match:
            index_name, owning_table = create_index_match.groups()
            if owning_table in dropped_tables:
                superseded.add(index_name)

    return superseded


def compute_checksum(path: Path) -> str:

    return hashlib.sha256(path.read_bytes()).hexdigest()


class MigrationRunner:

    def __init__(self, db_path: Path, migrations_dir: Path):

        self.db_path = Path(db_path)
        self.migrations_dir = Path(migrations_dir)

    # ------------------------------------------------
    # 파일 목록
    # ------------------------------------------------

    def list_migration_files(self) -> list[Path]:

        return sorted(self.migrations_dir.glob("*.sql"))

    # ------------------------------------------------
    # 이력 테이블
    # ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:

        return sqlite3.connect(str(self.db_path))

    def ensure_history_table(self, conn: sqlite3.Connection) -> None:
        """
        schema_migrations 부기 테이블을 실제로 만든다(쓰기). 오직
        mutation 경로(reconcile_backfill()/apply_pending())가, 쓸
        내용이 있다고 이미 판단한 뒤에만 호출해야 한다 — diagnose()/
        get_history_readonly()는 이 메서드를 절대 호출하지 않는다.
        """

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                status TEXT NOT NULL,
                execution_ms INTEGER,
                notes TEXT
            )
            """,
        )
        conn.commit()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:

        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()

        return row is not None

    def get_history_readonly(
        self, conn: sqlite3.Connection,
    ) -> dict[str, MigrationRecord]:
        """
        schema_migrations를 읽기만 한다 — 테이블이 없으면 만들지 않고
        빈 이력({})을 반환한다. DB 파일에 어떤 쓰기도 발생시키지 않는다
        (diagnose()가 사용하는 유일한 이력 조회 경로).
        """

        if not self._table_exists(conn, "schema_migrations"):
            return {}

        rows = conn.execute(
            "SELECT filename, checksum, applied_at, status, execution_ms, "
            "notes FROM schema_migrations",
        ).fetchall()

        return {
            row[0]: MigrationRecord(
                filename=row[0], checksum=row[1], applied_at=row[2],
                status=row[3], execution_ms=row[4], notes=row[5],
            )
            for row in rows
        }

    def get_history(self, conn: sqlite3.Connection) -> dict[str, MigrationRecord]:
        """
        schema_migrations를 만들고(없으면) 읽는다 — mutation 경로
        전용. 진단에는 절대 쓰지 않는다(get_history_readonly() 참고).
        """

        self.ensure_history_table(conn)

        return self.get_history_readonly(conn)

    # ------------------------------------------------
    # 진단(읽기 전용) — 실행 전 항상 먼저 호출한다. 이 메서드와 그것이
    # 호출하는 모든 것은 DB에 어떤 쓰기도 하지 않는다.
    # ------------------------------------------------

    def diagnose(self, conn: sqlite3.Connection) -> dict:
        """
        실제 변경 없이 현재 상태를 진단한다 — checksum 불일치, 순서
        역전, 부분 적용을 전부 검사해 문제가 있으면 즉시 예외를
        던진다. 문제가 없으면 {"backfill_needed": [...], "pending":
        [...], "already_applied": [...]}를 반환한다. schema_migrations
        테이블이 아직 없어도 만들지 않는다(get_history_readonly()).
        """

        history = self.get_history_readonly(conn)
        existing_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        existing_indexes = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'",
            ).fetchall()
        }

        files = self.list_migration_files()

        backfill_needed = []
        pending = []
        already_applied = []

        # 순서 역전 검사는 "지금까지 순회하며 본 것" 기준이 아니라
        # 이력 전체에서 사전순으로 가장 늦은 파일명 기준이어야 한다.
        # glob 정렬 순서상 역전을 일으키는 미적용 파일이 이미 적용된
        # 파일보다 먼저 순회될 수 있기 때문이다(예: b가 이미 적용된
        # 상태에서 a가 나중에 추가되면, 정렬 순서상 a가 b보다 먼저
        # 순회되므로 순회 중 누적 방식으로는 감지할 수 없다).
        #
        # 단, 이 검사는 "이 러너가 실제로 순서를 통제하며 적용한"
        # APPLIED 이력에만 적용한다 — BACKFILLED 이력(러너 도입 이전에
        # 사람이 Gate 단위로 선택적·비순차적으로 적용해온 실제 운영
        # 이력을 그대로 인정하는 것)까지 기준으로 삼으면, 과거의 정상적
        # 부분 적용(예: 뒤 날짜 Migration을 먼저 승인해 적용하고 앞 날짜
        # Migration은 의도적으로 보류한 이력)이 전부 "역전"으로 오탐된다.
        # BACKFILLED는 과거 사실의 기록일 뿐 향후 순서를 구속하지 않는다.
        applied_only_filenames = [
            fn for fn, record in history.items() if record.status == "APPLIED"
        ]
        max_applied_filename = (
            max(applied_only_filenames) if applied_only_filenames else None
        )

        for path in files:
            fn = path.name
            checksum = compute_checksum(path)

            if fn in history:
                record = history[fn]
                if record.checksum != checksum:
                    raise ChecksumMismatchError(
                        f"{fn}: 저장된 checksum({record.checksum})과 현재 "
                        f"파일 checksum({checksum})이 다릅니다 — 파일이 "
                        "변조되었거나 손상되었을 수 있습니다. 진행을 "
                        "중단합니다.",
                    )
                already_applied.append(fn)

                continue

            # 이력에 없다 — 순서 역전 검사
            if (
                max_applied_filename is not None
                and fn < max_applied_filename
            ):
                raise OrderInversionError(
                    f"{fn}은 이미 적용된 {max_applied_filename}보다 "
                    "사전순으로 앞섭니다 — 이 파일은 원래 먼저 적용됐어야 "
                    "합니다. 진행을 중단합니다.",
                )

            content = path.read_text(encoding="utf-8")
            tables, indexes = _extract_target_objects(content)
            targets = tables + indexes
            existing_names = existing_tables | existing_indexes
            # 같은 파일 안에서 DROP TABLE로 먼저 제거된 뒤 재생성되는
            # 대상은 "이미 존재하는 충돌"이 아니다(_extract_superseded_
            # targets 참고 — 테이블 재생성형 Migration 지원).
            superseded = _extract_superseded_targets(content)

            present = [
                t for t in targets
                if t in existing_names and t not in superseded
            ]
            missing = [
                t for t in targets
                if t not in existing_names or t in superseded
            ]

            if targets and not missing:
                backfill_needed.append((fn, checksum))
            elif targets and present and missing:
                raise PartialApplicationError(
                    f"{fn}: 대상 객체 중 일부만 DB에 존재합니다(부분 "
                    f"적용) — 존재: {present}, 없음: {missing}. 자동으로 "
                    "넘어가지 않습니다 — 직접 진단이 필요합니다.",
                )
            else:
                pending.append(fn)

        return {
            "backfill_needed": backfill_needed,
            "pending": pending,
            "already_applied": already_applied,
        }

    def describe_pending_targets(
        self, pending_filenames: list[str],
    ) -> dict[str, dict[str, list[str]]]:
        """
        2026-08-05 CTO 재검증 지시 Gate E — Migration 승인 UX용. 각
        pending 파일이 실제로 무엇을 만드는지(테이블/인덱스 이름)를
        읽기 전용으로 파일 내용만 파싱해 돌려준다 — DB에는 전혀
        접근하지 않는다(diagnose()가 이미 확인한 pending 목록을
        그대로 받아 파일만 다시 읽는다).
        """

        by_file = self.list_migration_files()
        lookup = {p.name: p for p in by_file}

        result: dict[str, dict[str, list[str]]] = {}
        for filename in pending_filenames:
            path = lookup.get(filename)
            if path is None:
                continue
            tables, indexes = _extract_target_objects(
                path.read_text(encoding="utf-8"),
            )
            result[filename] = {"tables": tables, "indexes": indexes}

        return result

    # ------------------------------------------------
    # Backfill — 기존에 이미 적용됐지만 추적되지 않던 Migration의
    # 이력만 채운다(SQL을 다시 실행하지 않는다)
    # ------------------------------------------------

    def reconcile_backfill(self, conn: sqlite3.Connection) -> list[str]:

        diagnosis = self.diagnose(conn)
        backfilled = []

        if not diagnosis["backfill_needed"]:
            return backfilled

        # 쓸 내용이 실제로 있다고 확인된 뒤에만 부기 테이블을 만든다.
        self.ensure_history_table(conn)

        for fn, checksum in diagnosis["backfill_needed"]:
            conn.execute(
                "INSERT INTO schema_migrations "
                "(filename, checksum, applied_at, status, execution_ms, "
                "notes) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    fn, checksum, datetime.now(timezone.utc).isoformat(),
                    "BACKFILLED", None,
                    "Migration Runner 도입 이전에 이미 적용된 것으로 "
                    "감지되어 SQL을 재실행하지 않고 이력만 기록함.",
                ),
            )
            backfilled.append(fn)

        conn.commit()

        return backfilled

    # ------------------------------------------------
    # 적용
    # ------------------------------------------------

    def plan_pending(self, conn: sqlite3.Connection) -> list[Path]:

        diagnosis = self.diagnose(conn)
        pending_names = set(diagnosis["pending"])

        return [
            p for p in self.list_migration_files()
            if p.name in pending_names
        ]

    def apply_pending(
        self, conn: sqlite3.Connection, dry_run: bool = False,
    ) -> list[str]:

        pending = self.plan_pending(conn)
        applied = []

        if not pending:
            return applied

        if not dry_run:
            # 쓸 내용이 실제로 있다고 확인된 뒤에만 부기 테이블을 만든다.
            self.ensure_history_table(conn)

        for path in pending:
            fn = path.name
            content = path.read_text(encoding="utf-8")
            checksum = compute_checksum(path)

            tables, indexes = _extract_target_objects(content)
            existing_names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type IN ('table', 'index')",
                ).fetchall()
            }
            # IF NOT EXISTS로 선언된 대상은 이미 존재해도 충돌이 아니다
            # — SQLite 자체가 no-op으로 처리하도록 파일 작성자가 의도한
            # 것이다(_extract_guarded_targets 참고). 그 외 대상(신규
            # 설치에서 CREATE TABLE처럼 의도적으로 IF NOT EXISTS를 쓰지
            # 않은 것)이 이미 존재하면 여전히 중복 적용으로 차단한다.
            guarded = _extract_guarded_targets(content)
            superseded = _extract_superseded_targets(content)
            collisions = [
                t for t in tables + indexes
                if t in existing_names
                and t not in guarded
                and t not in superseded
            ]
            if collisions:
                raise DuplicateApplicationError(
                    f"{fn}: 이미 존재하는 객체와 충돌합니다(중복 적용 "
                    f"차단): {collisions}",
                )

            if dry_run:
                applied.append(fn)

                continue

            start = time.monotonic()
            try:
                conn.executescript(content)
            except Exception as exc:
                raise MigrationExecutionError(
                    f"{fn} 적용 실패: {type(exc).__name__}: {exc}",
                ) from exc

            execution_ms = int((time.monotonic() - start) * 1000)

            conn.execute(
                "INSERT INTO schema_migrations "
                "(filename, checksum, applied_at, status, execution_ms, "
                "notes) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    fn, checksum, datetime.now(timezone.utc).isoformat(),
                    "APPLIED", execution_ms, None,
                ),
            )
            conn.commit()
            applied.append(fn)

        return applied

    # ------------------------------------------------
    # 백업
    # ------------------------------------------------

    def create_backup(self, backups_dir: Path, label: str) -> Path:

        backups_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backups_dir / f"homez_pre_{label}_{timestamp}.db"

        if backup_path.resolve() == self.db_path.resolve():
            raise MigrationRunnerError(
                f"백업 경로가 원본 DB 경로와 동일합니다 — 차단합니다: "
                f"{backup_path}",
            )

        src = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        verify = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        verify.execute("PRAGMA query_only=ON")
        integrity = verify.execute("PRAGMA integrity_check").fetchone()[0]
        verify.close()

        if integrity != "ok":
            raise MigrationRunnerError(
                f"백업 무결성 검증 실패: {backup_path} (integrity_check="
                f"{integrity})",
            )

        return backup_path


__all__ = [
    "MigrationRunner",
    "MigrationRunnerError",
    "ChecksumMismatchError",
    "OrderInversionError",
    "PartialApplicationError",
    "DuplicateApplicationError",
    "MigrationExecutionError",
    "MigrationRecord",
    "compute_checksum",
]
