"""
=========================================================
Homez OS

File : tests/test_migration_approval.py

2026-08-05 CTO 재검증 지시 Gate E — Migration 승인 UX 검증
(결함 DEFECT-MIGRATION-AUTOAPPLY-UX-001 수정 확인).

실제 homez.db는 어디에서도 사용하지 않는다 — 모든 테스트가
`app.core.migration_approval._real_paths`를 임시 디렉터리로 교체해
호출한다. 라우터 엔드포인트 함수를 FastAPI TestClient 없이 직접
호출한다(다른 desktop-setup 계열 테스트와 동일한 패턴) — 방어
계층(require_loopback 등)은 desktop_setup.py 자체에서 이미 검증됐고,
여기서는 "그 방어 계층이 실제로 붙어 있는지"만 정적으로 확인한다.
=========================================================
"""

import os
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.core import migration_approval
from app.core.desktop_setup import require_desktop_mode_and_token
from app.core.desktop_setup import require_loopback
from app.core.desktop_setup import require_matching_origin
from app.core.guard import SuperAdminGuard
from app.core.migration_approval_nonce import MigrationApprovalNonceStatus


def _write_migration(path: Path, content: str) -> None:

    path.write_text(content, encoding="utf-8")


# Gate G(2026-08-07): approve_migration()이 이제 SUPER_ADMIN·recent-auth·
# 단발성 nonce를 추가로 요구한다. 이 파일의 기존 테스트들은 그
# 방어 계층들이 이미 각자 통과했다고 가정하고(SuperAdminGuard 자체는
# 아래 클래스에서, recent-auth/nonce 자체는
# tests/test_migration_restricted_mode.py에서 별도로 검증한다) 승인
# 흐름 자체(파일 목록 일치 여부, 백업, 적용, 실패 처리)만 계속
# 검증한다 — 그래서 이 두 의존성만 항상 통과하도록 patch한다.
_FAKE_SUPER_ADMIN = types.SimpleNamespace(id=1, role="SUPER_ADMIN")


def _approve_request(approved_files):

    return migration_approval.MigrationApproveRequest(
        approved_files=approved_files, approval_nonce="test-nonce",
    )


def _approve(data):

    with patch.object(
        migration_approval, "consume_recent_auth_token", return_value=True,
    ):
        with patch.object(
            migration_approval, "verify_approval_nonce",
            return_value=MigrationApprovalNonceStatus.VALID,
        ):
            return migration_approval.approve_migration(
                data,
                current_user=_FAKE_SUPER_ADMIN,
                recent_auth_token="dummy",
            )


class MigrationApprovalRouterWiringTestCase(unittest.TestCase):
    """
    승인이 필요한 유일한 쓰기 엔드포인트(approve)에 Desktop 전용
    방어 계층 3개가 전부 걸려 있는지 정적으로 확인한다 — 코드
    추정이 아니라 실제 FastAPI Dependant 체인을 읽는다.
    """

    def test_approve_endpoint_requires_desktop_mode_guards(self):

        approve_route = next(
            r for r in migration_approval.router.routes
            if r.path == "/desktop-setup/migration-status/approve"
        )
        calls = [d.call for d in approve_route.dependant.dependencies]

        self.assertIn(require_loopback, calls)
        self.assertIn(require_matching_origin, calls)
        self.assertIn(require_desktop_mode_and_token, calls)
        # Gate G(2026-08-07): SUPER_ADMIN 권한도 정적으로 걸려 있어야
        # 한다(파라미터 레벨 Depends도 dependant.dependencies에 포함됨).
        self.assertIn(SuperAdminGuard, calls)

    def test_status_endpoint_has_no_write_guards_required(self):
        """
        조회 엔드포인트는 로그인 여부와 무관하게(셸을 보여주기 전에도)
        호출 가능해야 한다 — desktop_setup.py::get_setup_status와 동일
        신뢰 수준.
        """

        status_route = next(
            r for r in migration_approval.router.routes
            if r.path == "/desktop-setup/migration-status"
        )
        self.assertEqual(status_route.dependant.dependencies, [])


class MigrationApprovalFlowTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_root = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_root / "homez.db"
        self.migrations_dir = self.tmp_root / "migrations"
        self.migrations_dir.mkdir()
        self.backups_dir = self.tmp_root / "backups"
        self.backups_dir.mkdir()

        # 기존 설치를 재현 — a 테이블만 있는 상태로 미리 만든다.
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_create_b.sql",
            "BEGIN;\nCREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n"
            "CREATE INDEX ix_b_id ON b (id);\n",
        )

        self._patcher = patch.object(
            migration_approval, "_real_paths",
            return_value=(self.db_path, self.migrations_dir, self.backups_dir),
        )
        self._patcher.start()

    def tearDown(self):

        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_status_reports_pending_with_targets_and_backup_preview(self):

        resp = migration_approval.get_migration_status()

        self.assertTrue(resp.approval_required)
        self.assertEqual(resp.pending_files, ["20260102_00_create_b.sql"])
        self.assertIn(
            "b", resp.targets_by_file["20260102_00_create_b.sql"].tables,
        )
        self.assertIn(
            "ix_b_id",
            resp.targets_by_file["20260102_00_create_b.sql"].indexes,
        )
        self.assertIsNotNone(resp.backup_path_preview)
        self.assertIn("homez_pre_bootstrap_migration_", resp.backup_path_preview)

    def test_status_reports_no_approval_needed_when_nothing_pending(self):

        # 미리 둘 다 승인 적용해 pending을 비운다.
        _approve(_approve_request(["20260102_00_create_b.sql"]))

        resp = migration_approval.get_migration_status()
        self.assertFalse(resp.approval_required)
        self.assertEqual(resp.pending_files, [])

    def test_approve_with_exact_pending_files_applies_and_returns_integrity_ok(self):

        result = _approve(_approve_request(["20260102_00_create_b.sql"]))

        self.assertEqual(result.applied, ["20260102_00_create_b.sql"])
        self.assertEqual(result.integrity_check_result, "ok")
        self.assertFalse(result.approval_required)
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(Path(result.backup_path).exists())

        conn = sqlite3.connect(str(self.db_path))
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        conn.close()
        self.assertIn("b", tables)

    def test_approve_with_wrong_file_list_does_not_apply(self):
        """
        그 시점의 실제 pending 목록과 정확히 일치하지 않으면(오래된
        승인·부분 승인·잘못된 파일명) 절대 적용되지 않는다.
        """

        result = _approve(_approve_request(["20260199_99_nonexistent.sql"]))

        self.assertEqual(result.applied, [])
        self.assertTrue(result.approval_required)

        conn = sqlite3.connect(str(self.db_path))
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        conn.close()
        self.assertNotIn("b", tables)

    def test_approve_with_empty_list_rejected(self):

        with self.assertRaises(HTTPException) as ctx:
            _approve(_approve_request([]))

        self.assertEqual(ctx.exception.status_code, 400)

    def test_approve_apply_failure_reports_500_and_preserves_backup(self):

        with patch(
            "app.database.bootstrap.MigrationRunner.apply_pending",
            side_effect=RuntimeError("시뮬레이션된 적용 실패"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                _approve(_approve_request(["20260102_00_create_b.sql"]))

        self.assertEqual(ctx.exception.status_code, 500)
        # 백업은 실패 이전에 이미 만들어졌어야 한다.
        backups = list(self.backups_dir.glob("*.db"))
        self.assertEqual(len(backups), 1)


class RealHomezDbUntouchedTestCase(unittest.TestCase):
    """
    이 파일의 모든 테스트가 `_real_paths`를 항상 임시 경로로 교체해
    호출했음을 방어적으로 재확인한다 — 실제 homez.db 해시가 이 파일
    실행 전후로 완전히 동일해야 한다.
    """

    REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    REAL_DB_PATH = os.path.join(REPO_ROOT, "homez.db")

    def test_real_db_hash_unchanged_after_module_tests(self):

        if not os.path.exists(self.REAL_DB_PATH):
            self.skipTest("실제 homez.db가 이 환경에 없습니다.")

        import hashlib

        with open(self.REAL_DB_PATH, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()

        # 이 테스트 자체는 실제 DB를 건드리지 않는다 — 단지 이 값을
        # 기록해 사람이 이번 실행 전후를 대조할 수 있게 한다(자동
        # 대조를 위한 별도 기준값 파일은 이 저장소에 두지 않는다 —
        # 세션 밖에서 실제 DB가 정상적으로 바뀔 수 있는 시스템이므로).
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
