"""
=========================================================
Homez OS

File : app/domains/restore/service.py

Gate Y-2(2026-08-12) — 복원 엔진. 두 단계로 나눈다:

1) validate_backup_file(): 순수 읽기 전용 검증(SHA-256 일치 +
   PRAGMA integrity_check) — 아무것도 쓰지 않는다.
2) restore(): 검증을 통과한 백업만 target_db_path에 실제로 덮어쓴다.
   - target이 이미 존재하면 덮어쓰기 직전에 반드시 안전 백업을
     한 번 더 만든다(`app.domains.backup.service.BackupService`
     재사용 — 복원이 잘못된 백업으로 실행됐을 때 되돌릴 유일한
     수단).
   - 실제 교체는 같은 디렉터리에 임시 파일로 먼저 쓰고
     `os.replace()`로 원자적으로 바꿔치기한다(교체 도중 프로세스가
     죽어도 target_db_path는 항상 "이전 상태 그대로" 또는
     "완전히 새 상태" 둘 중 하나이지, 중간에 잘린 파일이 되지
     않는다).
   - 각 실패 지점마다 RestoreAttempt를 FAILED로 기록한다 —
     복원 시도 자체가 감사 대상이다(성공만 남기는 backup_records와
     다른 점).

실제 운영 DB(homez.db)로의 복원은 이 세션의 표준 안전 경계에 따라
별도 명시적 승인 없이는 router에서 노출하지 않는다 — service.py는
target_db_path를 그대로 받는 범용 엔진이며, 어떤 경로로 호출할지는
호출자(router 또는 향후 관리 스크립트)의 책임이다.
=========================================================
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.domains.backup.service import TRIGGER_SOURCE_PRE_RESTORE
from app.domains.backup.service import BackupService
from app.domains.backup.service import sha256_of_file
from app.domains.restore.model import RESTORE_STATUS_FAILED
from app.domains.restore.model import RESTORE_STATUS_SUCCEEDED
from app.domains.restore.model import RestoreAttempt
from app.domains.restore.repository import RestoreRepository


class RestoreError(Exception):
    pass


# V7 Live Gate 4 복원 결함 수정(2026-08-17) — 코디네이터 명시적 판단으로
# 재활성화(아래 validate_backup_file() 주석 참고). "핵심 HOMEZ 테이블들"
# 로 users/companies/roles(신원·테넌트 골격 — 이게 없으면 로그인 자체가
# 불가능해 앱이 즉시 깨진다)와 schema_migrations(이 앱의 Migration
# 부기 테이블, app/database/migration_runner.py 참고)를 검사한다. 향후
# 추가되는 도메인 테이블은 포함하지 않는다 — 오래된(그러나 여전히
# 유효한) 과거 백업까지 이 판정 때문에 소급 거부되지 않도록, 앱
# 초창기부터 항상 존재해 온 근본 테이블만 필수로 본다.
REQUIRED_CORE_TABLES = frozenset({
    "schema_migrations",
    "users",
    "companies",
    "roles",
})


def require_app_closed_confirmation(confirmed: bool) -> None:
    """
    2026-08-15 V7 Gate 8 — 실제 운영 DB 복원 실행 엔드포인트가 노출될
    때(현재는 여전히 노출하지 않는다 — Y-2 설계와 동일하게 V7 Live
    Gate로 남김), 그 라우터는 `RestoreService.restore()`를 호출하기
    직전에 반드시 이 함수를 통과시켜야 한다.

    이유: Desktop 앱은 프로세스 내내 실제 DB에 대한 SQLAlchemy 연결
    풀을 열어 둔다. `restore()`가 쓰는 `os.replace()`는 Windows에서
    대상 파일을 다른 프로세스/핸들이 배타적으로 잠그고 있으면
    실패하거나(파일 잠금), 설령 성공하더라도 이미 열려 있던 연결이
    교체 이전 상태를 계속 바라보는 조용한 불일치를 만들 수 있다 —
    "복원 직전 재백업"만으로는 이 클래스의 위험을 막지 못한다(재백업은
    데이터 손실을 막지, 진행 중인 연결의 불일치를 막지 않는다).

    fail-closed: `confirmed`가 정확히 True가 아니면(None, False,
    문자열 "true" 등 어떤 것도) 무조건 차단한다.
    """

    if confirmed is not True:
        raise RestoreError(
            "복원을 실행하려면 앱의 다른 모든 인스턴스/연결이 "
            "종료되었음을 명시적으로 확인해야 합니다 "
            "(app_closed_confirmed=True 필요).",
        )


class RestoreService:

    def __init__(
        self,
        db: Session,
    ):
        self.db = db
        self.repository = RestoreRepository(db)

    def validate_backup_file(
        self,
        backup_path: Path,
        expected_sha256: str | None = None,
    ) -> dict:

        backup_path = Path(backup_path)

        if not backup_path.exists():
            return {
                "file_exists": False,
                "sha256_matches": False,
                "integrity_check_result": None,
                "restorable": False,
                "reason": "백업 파일이 존재하지 않습니다.",
            }

        actual_sha256 = sha256_of_file(backup_path)
        sha256_matches = (
            expected_sha256 is None
            or actual_sha256 == expected_sha256
        )

        # V7 Live Gate 4 재작업 — 결함 3: 파일이 SQLite로 열리지도
        # 못할 만큼 손상됐거나 SQLite 파일이 아니면 sqlite3.connect()/
        # execute()가 sqlite3.DatabaseError(예: "file is not a
        # database") 또는 sqlite3.OperationalError를 던진다. 이전에는
        # 이 예외를 감싸지 않아 그대로 전파돼 router가 처리되지 않은
        # HTTP 500을 반환했다 — 여기서 구조화된 결과로 변환해, 손상·
        # SQLite 아님을 "정상적으로 검증 실패한 백업"으로 다룬다(원본
        # DB·백업 파일에는 이 read-only 검증 경로가 어떤 쓰기도 하지
        # 않으므로 영향 없음). 내부 경로/SQL은 reason에 포함하지 않는다.
        # 2026-08-16 재검토에서는 무결성 검사 통과 후 schema_migrations
        # 테이블 존재 여부로 "필수 테이블 누락(스키마 불완전)"을 판정하는
        # 로직을 되돌렸었다 — 당시 이미 승인·통과해 있던
        # tests/test_restore_engine.py(HOMEZ 스키마와 무관한 단일
        # 테이블짜리 유효한 백업도 정상 복원 가능으로 기대)가 깨지는
        # 것을 전체 회귀에서 확인했기 때문이었다.
        #
        # 2026-08-17 V7 Live Gate 4 복원 결함 수정 — 코디네이터가 그
        # 판단을 명시적으로 뒤집었다: validate_backup_file()은 범용
        # SQLite 검증기가 아니라 "이 HOMEZ 백업을 복원해도 안전한가"를
        # 판정하는 함수이므로, HOMEZ 스키마와 무관한 파일을 restorable=
        # True로 판정하면 사용자가 그걸 실제로 복원했을 때 앱이 즉시
        # 깨진다(로그인조차 안 됨) — 이건 이번 Gate가 사용자에게 명시적
        # 요구받은 안전 요구사항이다. 그래서 REQUIRED_CORE_TABLES 검사를
        # 다시 넣고, 그 결과 깨지는 tests/test_restore_engine.py의 해당
        # 케이스는 삭제가 아니라 **기대치 자체를 이 안전 요구사항에 맞게
        # 수정**했다(그 파일 안의
        # test_restore_into_nonexistent_target_succeeds_with_full_schema_backup
        # 참고 — 이름과 setUp의 백업 생성 방식을 "HOMEZ 스키마와 무관한
        # 단일 테이블"에서 "HOMEZ 핵심 테이블을 갖춘 최소 백업"으로
        # 바꾸고, 별도로 스키마 불완전 백업이 실제로 거부되는지 확인하는
        # 테스트를 추가했다 — CLAUDE.md의 "실패 테스트 삭제/assertion
        # 약화 금지" 원칙은 "실패를 피하려고 몰래 기대치를 낮추는 것"을
        # 금지하는 것이지, "낡은 전제를 실제 안전 요구사항에 맞게 명시적
        # 승인 하에 갱신하는 것"까지 금지하지 않는다).
        try:
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
        except sqlite3.DatabaseError:
            return {
                "file_exists": True,
                "sha256_matches": sha256_matches,
                "integrity_check_result": None,
                "restorable": False,
                "reason": (
                    "백업 파일이 손상되었거나 올바른 SQLite 데이터베이스 "
                    "파일이 아닙니다."
                ),
            }
        except sqlite3.OperationalError:
            return {
                "file_exists": True,
                "sha256_matches": sha256_matches,
                "integrity_check_result": None,
                "restorable": False,
                "reason": "백업 파일을 열어 검증할 수 없습니다.",
            }

        missing_required_tables: list[str] = []
        if sha256_matches and integrity == "ok":
            missing_required_tables = self._find_missing_required_tables(
                backup_path,
            )

        reason = None
        if not sha256_matches:
            reason = (
                "SHA-256 불일치 — 백업 파일이 변조되었거나 "
                "손상되었을 수 있습니다."
            )
        elif integrity != "ok":
            reason = f"무결성 검사 실패: {integrity}"
        elif missing_required_tables:
            reason = (
                "HOMEZ 백업이 아니거나 스키마가 불완전합니다 — 필수 "
                "테이블이 없습니다: "
                f"{', '.join(sorted(missing_required_tables))}"
            )

        restorable = (
            sha256_matches
            and integrity == "ok"
            and not missing_required_tables
        )

        return {
            "file_exists": True,
            "sha256_matches": sha256_matches,
            "integrity_check_result": integrity,
            "restorable": restorable,
            "reason": reason,
        }

    def _find_missing_required_tables(
        self,
        backup_path: Path,
    ) -> list[str]:
        """
        REQUIRED_CORE_TABLES 중 이 백업 파일에 실제로 없는 테이블
        이름만 반환한다(전부 있으면 빈 리스트). 읽기 전용이다 — 이
        함수는 integrity_check가 이미 "ok"로 통과한 뒤에만 호출되므로
        열기 자체가 실패할 일은 드물지만, 혹시 몰라 같은 방식으로
        구조화된 결과로 처리한다(예외를 그대로 전파하지 않음).
        """

        try:
            conn = sqlite3.connect(
                f"file:{backup_path}?mode=ro",
                uri=True,
            )
        except sqlite3.DatabaseError:
            return list(REQUIRED_CORE_TABLES)

        try:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            ).fetchall()
        finally:
            conn.close()

        existing_tables = {row[0] for row in rows}

        return sorted(REQUIRED_CORE_TABLES - existing_tables)

    def restore(
        self,
        *,
        source_backup_path: Path,
        target_db_path: Path,
        expected_sha256: str | None = None,
        pre_restore_backups_dir: Path | None = None,
        triggered_by_user_id: int | None = None,
    ) -> RestoreAttempt:

        source_backup_path = Path(source_backup_path)
        target_db_path = Path(target_db_path)

        validation = self.validate_backup_file(
            source_backup_path,
            expected_sha256,
        )

        if not validation["restorable"]:
            attempt = RestoreAttempt(
                source_backup_path=str(source_backup_path),
                target_db_path=str(target_db_path),
                pre_restore_backup_path=None,
                status=RESTORE_STATUS_FAILED,
                integrity_check_result=validation[
                    "integrity_check_result"
                ],
                error_message=validation["reason"],
                file_size_bytes=None,
                triggered_by_user_id=triggered_by_user_id,
            )
            self.repository.create(attempt)

            raise RestoreError(
                validation["reason"] or "백업 검증에 실패했습니다.",
            )

        pre_restore_backup_path: str | None = None

        try:
            if target_db_path.exists():
                if pre_restore_backups_dir is None:
                    raise RestoreError(
                        "target_db_path가 이미 존재하지만 "
                        "pre_restore_backups_dir가 지정되지 않았습니다 "
                        "— 안전 백업 없이 기존 DB를 덮어쓸 수 없습니다.",
                    )

                backup_service = BackupService(self.db)
                safety_record = backup_service.create_backup(
                    source_db_path=target_db_path,
                    backups_dir=pre_restore_backups_dir,
                    trigger_source=TRIGGER_SOURCE_PRE_RESTORE,
                    triggered_by_user_id=triggered_by_user_id,
                    label="복원 직전 자동 안전 백업",
                )
                pre_restore_backup_path = safety_record.file_path

            # V7 Live Gate 4 복원 결함 수정(2026-08-17) — Gate R-0에서
            # 계측으로 증명된 원인: 위 안전 백업 단계가 self.db로
            # BackupRecord 행을 쓰면(BaseRepository.create() ->
            # db.add/commit/refresh) SQLAlchemy 커넥션 풀이 target_db_path
            # 에 대한 물리 sqlite3 커넥션을 새로 열어 풀에 살려둔다.
            # Windows에서 SQLite가 여는 파일 핸들은 FILE_SHARE_DELETE를
            # 포함하지 않으므로, 이 커넥션이 살아있는 동안 아래
            # os.replace()가 PermissionError([WinError 5])로 실패한다
            # (재현 스크립트: live_gate4_restore_fix_gate_r0_repro.py,
            # 실험 1/4). engine.dispose()를 restore() 진입 "이전"에만
            # 해도 소용없다는 것도 실험 4b로 증명됨(바로 위 안전 백업
            # 쓰기가 dispose 이후에도 커넥션을 되살리기 때문) — 그래서
            # dispose는 반드시 이 지점, 즉 self.db를 쓰는 모든 쓰기가
            # 끝난 "직후"이자 os.replace() "직전"이어야 한다.
            #
            # self.db.close()는 이 Session이 들고 있던 커넥션을 풀에
            # 반납할 뿐 물리 핸들은 닫지 않으므로, bind(Engine)까지
            # 명시적으로 dispose()해 풀에 남은 모든 물리 커넥션을 실제로
            # 닫는다. Session은 close() 이후에도 재사용 가능하므로(다음
            # 사용 시 새 커넥션을 lazy하게 연다), 아래에서 실패/성공
            # 기록을 남길 때(self.repository.create(attempt))는 별도
            # 처리 없이 그대로 동작한다 — 단, 그 시점에 다시 열리는
            # 새 커넥션은 os.replace() 완료 "이후"이므로 안전하다.
            bind = self.db.get_bind()
            self.db.close()
            if hasattr(bind, "dispose"):
                bind.dispose()

            target_db_path.parent.mkdir(parents=True, exist_ok=True)

            fd, tmp_name = tempfile.mkstemp(
                dir=str(target_db_path.parent),
                prefix=".restore_tmp_",
                suffix=".db",
            )
            os.close(fd)
            tmp_path = Path(tmp_name)

            try:
                with open(source_backup_path, "rb") as src_f:
                    with open(tmp_path, "wb") as dst_f:
                        while True:
                            chunk = src_f.read(1024 * 1024)
                            if not chunk:
                                break
                            dst_f.write(chunk)

                os.replace(str(tmp_path), str(target_db_path))
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()

            final_verify = sqlite3.connect(
                f"file:{target_db_path}?mode=ro",
                uri=True,
            )
            try:
                final_verify.execute("PRAGMA query_only=ON")
                final_integrity = final_verify.execute(
                    "PRAGMA integrity_check",
                ).fetchone()[0]
            finally:
                final_verify.close()

            if final_integrity != "ok":
                raise RestoreError(
                    f"복원 후 무결성 검사 실패: {final_integrity} "
                    f"(pre_restore_backup_path="
                    f"{pre_restore_backup_path}에서 수동 복구 가능)",
                )

            attempt = RestoreAttempt(
                source_backup_path=str(source_backup_path),
                target_db_path=str(target_db_path),
                pre_restore_backup_path=pre_restore_backup_path,
                status=RESTORE_STATUS_SUCCEEDED,
                integrity_check_result=final_integrity,
                error_message=None,
                file_size_bytes=target_db_path.stat().st_size,
                triggered_by_user_id=triggered_by_user_id,
            )

            return self.repository.create(attempt)

        except Exception as exc:
            attempt = RestoreAttempt(
                source_backup_path=str(source_backup_path),
                target_db_path=str(target_db_path),
                pre_restore_backup_path=pre_restore_backup_path,
                status=RESTORE_STATUS_FAILED,
                integrity_check_result=None,
                error_message=str(exc),
                file_size_bytes=None,
                triggered_by_user_id=triggered_by_user_id,
            )
            self.repository.create(attempt)

            if isinstance(exc, RestoreError):
                raise

            raise RestoreError(str(exc)) from exc

    def list_attempts(
        self,
        limit: int = 50,
    ) -> list[RestoreAttempt]:

        return self.repository.list_recent(limit)


__all__ = [
    "RestoreService",
    "RestoreError",
    "require_app_closed_confirmation",
    "REQUIRED_CORE_TABLES",
]
