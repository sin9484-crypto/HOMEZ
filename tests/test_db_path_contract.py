"""
=========================================================
Homez OS

File : tests/test_db_path_contract.py

2026-08-31 Preflight 8-A 코드 감사에서 발견한 결함의 회귀 테스트 —
app/domains/backup/router.py(create_backup), app/domains/restore/
router.py(execute_restore), app/domains/diagnostics/router.py
(export_diagnostics) 세 곳이 실제 SQLAlchemy engine이 쓰는 DB
경로(app.database.session.get_engine_db_path())와 비교하지 않은 채
app.desktop.paths.get_homez_db_path(confirm=True)만으로 파일 경로를
계산했다 — DATABASE_URL이 override된 채 프로세스가 떠 있으면
백업/복원/진단이 조용히 엉뚱한 파일을 대상으로 동작할 수 있었다.

app/core/db_path_contract.py::assert_bootstrap_path_matches_engine()
이 그 공유 fail-closed 검사다. 이 파일은:
1) 그 함수 자체의 일치/불일치 판정이 정확한지,
2) 세 라우터가 실제로 그 함수를 거쳐가며, 불일치 시 파괴적 동작
   (백업 파일 생성, 복원 계획 저장·종료 트리거)을 전혀 수행하지
   않는지를 확인한다.

실제 운영 DB는 어디에서도 열지 않는다 — get_engine_db_path()/
get_homez_db_path() 둘 다 이 테스트에서는 patch로 대체한다.
=========================================================
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.db_path_contract import ERROR_CODE_DB_PATH_MISMATCH
from app.core.db_path_contract import assert_bootstrap_path_matches_engine
from app.core.exceptions import InternalServerException

_PATCH_ENGINE = "app.core.db_path_contract.get_engine_db_path"
_PATCH_BOOTSTRAP = "app.core.db_path_contract.get_homez_db_path"


class AssertBootstrapPathMatchesEngineTestCase(unittest.TestCase):

    def test_matching_paths_return_the_path_without_raising(self):

        same_path = Path(r"C:\fake\homez.db")

        with patch(_PATCH_BOOTSTRAP, return_value=same_path), \
                patch(_PATCH_ENGINE, return_value=same_path):
            result = assert_bootstrap_path_matches_engine()

        self.assertEqual(result, same_path)

    def test_mismatched_paths_raise_internal_server_error(self):

        bootstrap_path = Path(r"C:\fake\bootstrap\homez.db")
        engine_path = Path(r"C:\fake\engine\homez.db")

        with patch(_PATCH_BOOTSTRAP, return_value=bootstrap_path), \
                patch(_PATCH_ENGINE, return_value=engine_path):
            with self.assertRaises(InternalServerException) as ctx:
                assert_bootstrap_path_matches_engine()

        self.assertIn(ERROR_CODE_DB_PATH_MISMATCH, str(ctx.exception))
        self.assertIn(str(bootstrap_path), str(ctx.exception))
        self.assertIn(str(engine_path), str(ctx.exception))

    def test_engine_path_none_raises_rather_than_silently_using_bootstrap(self):
        """get_engine_db_path()가 None을 반환하는 경우(예: 비-SQLite
        엔진, 또는 :memory:)에도 자동으로 bootstrap 경로를 신뢰해
        진행하지 않는다 — 확인할 수 없으면 막는다."""

        bootstrap_path = Path(r"C:\fake\homez.db")

        with patch(_PATCH_BOOTSTRAP, return_value=bootstrap_path), \
                patch(_PATCH_ENGINE, return_value=None):
            with self.assertRaises(InternalServerException):
                assert_bootstrap_path_matches_engine()

    def test_does_not_open_or_read_any_file(self):
        """이 함수는 경로 문자열만 비교한다 — 파일을 열거나 읽지
        않는다는 계약을 보장하려면, 실제로 존재하지 않는 경로를
        줘도 FileNotFoundError 없이 정상적으로(일치 여부만) 판단할
        수 있어야 한다."""

        nonexistent = Path(r"C:\this\path\does\not\exist\homez.db")

        with patch(_PATCH_BOOTSTRAP, return_value=nonexistent), \
                patch(_PATCH_ENGINE, return_value=nonexistent):
            result = assert_bootstrap_path_matches_engine()

        self.assertEqual(result, nonexistent)


class BackupRouterFailClosedTestCase(unittest.TestCase):
    """backup/router.py::create_backup()가 경로 불일치 시 실제로
    막히고, BackupService.create_backup()(실제 파일 쓰기)이 전혀
    호출되지 않는지 확인한다."""

    def test_create_backup_blocked_on_path_mismatch_and_never_writes(self):

        import types

        from app.domains.backup.router import create_backup
        from app.domains.backup.schema import BackupCreateRequest

        bootstrap_path = Path(r"C:\fake\bootstrap\homez.db")
        engine_path = Path(r"C:\fake\engine\homez.db")

        fake_user = types.SimpleNamespace(id=1, company_id=1, role="ADMIN")

        with patch(_PATCH_BOOTSTRAP, return_value=bootstrap_path), \
                patch(_PATCH_ENGINE, return_value=engine_path), \
                patch(
                    "app.domains.backup.router.BackupService.create_backup",
                ) as fake_create:
            with self.assertRaises(InternalServerException):
                create_backup(
                    BackupCreateRequest(label="test"),
                    current_user=fake_user, db=None,
                )

            fake_create.assert_not_called()


class DiagnosticsExportFailClosedTestCase(unittest.TestCase):
    """diagnostics/router.py::export_diagnostics()가 경로 불일치 시
    실제로 막히고, build_diagnostics_bundle()이 전혀 호출되지
    않는지 확인한다."""

    def test_export_diagnostics_blocked_on_path_mismatch(self):

        import types

        from app.domains.diagnostics.router import export_diagnostics

        bootstrap_path = Path(r"C:\fake\bootstrap\homez.db")
        engine_path = Path(r"C:\fake\engine\homez.db")
        fake_user = types.SimpleNamespace(id=1, company_id=1, role="ADMIN")

        with patch(_PATCH_BOOTSTRAP, return_value=bootstrap_path), \
                patch(_PATCH_ENGINE, return_value=engine_path), \
                patch(
                    "app.domains.diagnostics.router.build_diagnostics_bundle",
                ) as fake_bundle:
            with self.assertRaises(InternalServerException):
                export_diagnostics(_=fake_user)

            fake_bundle.assert_not_called()


class RestoreRouterFailClosedTestCase(unittest.TestCase):
    """restore/router.py::execute_restore()가 경로 불일치 시 실제로
    막히고, HOMEZ 종료·복원 Helper 기동(request_restore_shutdown)이
    전혀 트리거되지 않는지 확인한다 — 이 경로는 파일을 통째로
    덮어쓰는 가장 위험한 지점이라 별도로 검증한다."""

    def test_execute_restore_blocked_on_path_mismatch_before_shutdown_trigger(self):

        import types
        from unittest.mock import MagicMock

        from app.domains.restore.router import execute_restore
        from app.domains.restore.schema import RestoreExecuteRequest

        bootstrap_path = Path(r"C:\fake\bootstrap\homez.db")
        engine_path = Path(r"C:\fake\engine\homez.db")
        fake_user = types.SimpleNamespace(id=1, company_id=1, role="ADMIN")

        fake_record = types.SimpleNamespace(
            id=1, file_path=r"C:\fake\backup.db", sha256="a" * 64,
        )

        with patch(
            "app.domains.restore.router.BackupRepository",
        ) as fake_repo_cls, patch(
            "app.domains.restore.router.RestoreService",
        ) as fake_service_cls, patch(
            "app.desktop.restore_helper.request_restore_shutdown",
        ) as fake_shutdown, patch(
            _PATCH_BOOTSTRAP, return_value=bootstrap_path,
        ), patch(
            _PATCH_ENGINE, return_value=engine_path,
        ):
            fake_repo_cls.return_value.get.return_value = fake_record
            fake_service_cls.return_value.validate_backup_file.return_value = {
                "restorable": True, "reason": None,
            }

            background_tasks = MagicMock()

            with self.assertRaises(InternalServerException):
                execute_restore(
                    RestoreExecuteRequest(
                        backup_record_id=1, app_closed_confirmed=True,
                    ),
                    background_tasks=background_tasks,
                    current_user=fake_user, db=None,
                )

            # BackgroundTasks에 종료 트리거 자체를 등록하지 않아야
            # 한다 — 등록됐다면 응답 전송 후 실제로 앱을 종료시켰을
            # 것이다.
            background_tasks.add_task.assert_not_called()
            fake_shutdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
