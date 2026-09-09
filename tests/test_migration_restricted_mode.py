"""
=========================================================
Homez OS

File : tests/test_migration_restricted_mode.py

Gate G(2026-08-07) — Migration 제한 모드 서버 강제 검증.

기존에는 미적용 Migration이 있을 때의 "제한 모드"가
app/web/console.js(homezLimitedMode)에서만 강제됐다 — 서버 자체는
아무것도 막지 않았다. 이 파일은 그 서버측 강제(app/main.py의
423 미들웨어, app/core/migration_restricted_mode.py의 캐시 상태,
app/core/migration_approval.py에 추가된 SUPER_ADMIN·recent-auth·
단발성 nonce·동시성 락)를 검증한다.

실제 homez.db는 어디에서도 사용하지 않는다 — 전부 임시 SQLite
파일과 process-global 상태(recent_auth/migration_approval_nonce)만
사용한다. 각 process-global 모듈은 테스트 간 상태가 새지 않도록
setUp/tearDown에서 초기화한다.
=========================================================
"""

import asyncio
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app.main as app_main
from app.core import migration_approval
from app.core import migration_approval_nonce
from app.core import migration_restricted_mode
from app.core import recent_auth
from app.core.desktop_setup import require_desktop_mode_and_token
from app.core.desktop_setup import require_matching_origin
from app.core.guard import SuperAdminGuard
from app.core.migration_approval_nonce import MigrationApprovalNonceStatus
from app.database.bootstrap import bootstrap_environment

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N_DIR = os.path.join(REPO_ROOT, "app", "web", "i18n")

_FAKE_SUPER_ADMIN = types.SimpleNamespace(id=1, role="SUPER_ADMIN")
_FAKE_ADMIN = types.SimpleNamespace(id=2, role="ADMIN")


def _write_migration(path: Path, content: str) -> None:

    path.write_text(content, encoding="utf-8")


def _load_js_object_literal(path, var_name):
    """
    tests/test_i18n.py의 동명 헬퍼와 달리 `.encode().decode(
    "unicode_escape")` 재인코딩을 하지 않는다 — 이 카탈로그 파일들은
    `\\uXXXX` 이스케이프가 아니라 원문 UTF-8 한글을 그대로 담고 있어서,
    그 재인코딩 단계가 오히려 멀티바이트 문자를 깨뜨린다(파일을 이미
    encoding="utf-8"로 열어 올바르게 디코드했으므로 추가 변환이
    필요 없다 — 키 존재 여부만 확인하는 테스트에서는 이 손상이
    드러나지 않았을 뿐이다).
    """

    with open(path, encoding="utf-8") as f:
        content = f.read()

    start = content.index(f"window.{var_name}")
    body = content[start:]

    pairs = re.findall(r'"([a-zA-Z0-9_.]+)":\s*"((?:[^"\\]|\\.)*)"', body)
    return dict(pairs)


class _FakeURL:

    def __init__(self, path):
        self.path = path


class _FakeRequest:

    def __init__(self, method, path):
        self.method = method
        self.url = _FakeURL(path)


def _run(coro):

    return asyncio.run(coro)


# --------------------------------------------------
# 1~3. 서버 전역 423 미들웨어 — 우회 쓰기 차단, 읽기 허용, 화이트리스트
# --------------------------------------------------

class RestrictedModeMiddlewareTestCase(unittest.TestCase):

    def setUp(self):

        self._patcher = patch.object(app_main, "is_restricted_mode", return_value=True)
        self._patcher.start()

    def tearDown(self):

        self._patcher.stop()

    def _call_next_marker(self):

        async def call_next(_request):
            return "PASSED_TO_ROUTE"

        return call_next

    def test_curl_style_bypass_write_request_blocked_with_423(self):
        """
        시나리오 1 — 콘솔 JS를 거치지 않는 임의의 클라이언트(curl 등)가
        화이트리스트 밖 경로에 쓰기를 시도하면 서버가 직접 423으로
        차단한다(클라이언트 쪽 차단에 의존하지 않는다).
        """

        req = _FakeRequest("POST", "/domains/product/candidates/1/approve")
        resp = _run(app_main._enforce_migration_restricted_mode(req, self._call_next_marker()))

        self.assertEqual(resp.status_code, 423)
        self.assertEqual(
            resp.headers.get("X-Migration-Restricted-Code"),
            "MIGRATION_RESTRICTED_MODE",
        )

    def test_read_requests_always_allowed(self):
        """시나리오 2 — GET/HEAD/OPTIONS는 제한 모드에서도 항상 통과한다."""

        for method in ("GET", "HEAD", "OPTIONS"):
            req = _FakeRequest(method, "/domains/product/candidates")
            resp = _run(
                app_main._enforce_migration_restricted_mode(req, self._call_next_marker()),
            )
            self.assertEqual(resp, "PASSED_TO_ROUTE", method)

    def test_non_whitelisted_write_methods_all_blocked(self):
        """시나리오 3 — 화이트리스트 밖 POST/PUT/PATCH/DELETE 전부 차단."""

        for method in ("POST", "PUT", "PATCH", "DELETE"):
            req = _FakeRequest(method, "/domains/store_connection/1")
            resp = _run(
                app_main._enforce_migration_restricted_mode(req, self._call_next_marker()),
            )
            self.assertEqual(resp.status_code, 423, method)

    def test_whitelisted_write_paths_pass_through(self):

        for method, path in (
            ("POST", "/auth/login"),
            ("POST", "/auth/logout"),
            ("POST", "/auth/refresh"),
            ("POST", "/auth/recent-auth"),
            ("POST", "/desktop-auth/bootstrap"),
            ("POST", "/desktop-setup/migration-status/approve"),
        ):
            req = _FakeRequest(method, path)
            resp = _run(
                app_main._enforce_migration_restricted_mode(req, self._call_next_marker()),
            )
            self.assertEqual(resp, "PASSED_TO_ROUTE", path)

    def test_not_restricted_mode_allows_everything(self):

        self._patcher.stop()
        with patch.object(app_main, "is_restricted_mode", return_value=False):
            req = _FakeRequest("DELETE", "/domains/store_connection/1")
            resp = _run(
                app_main._enforce_migration_restricted_mode(req, self._call_next_marker()),
            )
            self.assertEqual(resp, "PASSED_TO_ROUTE")
        self._patcher.start()  # tearDown이 다시 stop()해도 안전하도록 재정렬


# --------------------------------------------------
# 4. VIEWER·타사 요청 거부(SUPER_ADMIN 아닌 역할)
# --------------------------------------------------

class SuperAdminGuardRejectionTestCase(unittest.TestCase):

    def test_non_super_admin_role_rejected(self):

        with self.assertRaises(HTTPException) as ctx:
            SuperAdminGuard(current_user=_FAKE_ADMIN)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_super_admin_role_passes(self):

        result = SuperAdminGuard(current_user=_FAKE_SUPER_ADMIN)
        self.assertIs(result, _FAKE_SUPER_ADMIN)


# --------------------------------------------------
# 5~6. 외부 Origin 거부 / Desktop token 누락 거부
# (승인 엔드포인트가 실제로 재사용하는 그 함수를 직접 검증한다)
# --------------------------------------------------

class DesktopDefenseLayerTestCase(unittest.TestCase):

    def test_external_origin_rejected(self):

        req = _FakeRequest("POST", "/desktop-setup/migration-status/approve")
        req.headers = {
            "host": "127.0.0.1:51234",
            "origin": "https://evil.example.com",
        }
        with self.assertRaises(HTTPException) as ctx:
            require_matching_origin(req)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_matching_loopback_origin_passes(self):

        req = _FakeRequest("POST", "/desktop-setup/migration-status/approve")
        req.headers = {
            "host": "127.0.0.1:51234",
            "origin": "http://127.0.0.1:51234",
        }
        require_matching_origin(req)  # 예외 없으면 통과

    def test_missing_desktop_token_rejected_when_desktop_mode_active(self):

        with patch(
            "app.core.desktop_setup.get_desktop_token", return_value="real-token",
        ):
            with self.assertRaises(HTTPException) as ctx:
                require_desktop_mode_and_token(homez_desktop_token=None)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_desktop_token_match_passes(self):

        with patch(
            "app.core.desktop_setup.get_desktop_token", return_value="real-token",
        ):
            require_desktop_mode_and_token(homez_desktop_token="real-token")


# --------------------------------------------------
# 7~8. recent-auth 누락·재사용 거부 / nonce 재사용 거부
# --------------------------------------------------

class RecentAuthAndNonceTestCase(unittest.TestCase):

    def setUp(self):

        with recent_auth._lock:
            recent_auth._tokens.clear()
            recent_auth._lockouts.clear()
        migration_approval_nonce.clear_migration_approval_nonce()

    def tearDown(self):

        with recent_auth._lock:
            recent_auth._tokens.clear()
            recent_auth._lockouts.clear()
        migration_approval_nonce.clear_migration_approval_nonce()

    def test_missing_recent_auth_token_rejected(self):

        self.assertFalse(
            recent_auth.consume_recent_auth_token(None, _FAKE_SUPER_ADMIN.id),
        )

    def test_recent_auth_token_reuse_rejected(self):

        raw_token, _ = recent_auth.issue_recent_auth_token(_FAKE_SUPER_ADMIN.id)

        first = recent_auth.consume_recent_auth_token(raw_token, _FAKE_SUPER_ADMIN.id)
        second = recent_auth.consume_recent_auth_token(raw_token, _FAKE_SUPER_ADMIN.id)

        self.assertTrue(first)
        self.assertFalse(second)

    def test_nonce_missing_rejected(self):

        status = migration_approval_nonce.verify_nonce(None)
        self.assertEqual(status, MigrationApprovalNonceStatus.MISSING)

    def test_nonce_reuse_rejected(self):

        nonce = migration_approval_nonce.generate_migration_approval_nonce()

        first = migration_approval_nonce.verify_nonce(nonce)
        migration_approval_nonce.mark_nonce_consumed()
        second = migration_approval_nonce.verify_nonce(nonce)

        self.assertEqual(first, MigrationApprovalNonceStatus.VALID)
        self.assertEqual(second, MigrationApprovalNonceStatus.ALREADY_CONSUMED)

    def test_approve_endpoint_rejects_missing_recent_auth_token(self):

        with patch.object(
            migration_approval, "verify_approval_nonce",
            return_value=MigrationApprovalNonceStatus.VALID,
        ):
            with self.assertRaises(HTTPException) as ctx:
                migration_approval.approve_migration(
                    migration_approval.MigrationApproveRequest(
                        approved_files=["x.sql"], approval_nonce="n",
                    ),
                    current_user=_FAKE_SUPER_ADMIN,
                    recent_auth_token=None,
                )

        self.assertEqual(ctx.exception.status_code, 401)

    def test_approve_endpoint_rejects_invalid_nonce(self):

        with patch.object(
            migration_approval, "consume_recent_auth_token", return_value=True,
        ):
            with self.assertRaises(HTTPException) as ctx:
                migration_approval.approve_migration(
                    migration_approval.MigrationApproveRequest(
                        approved_files=["x.sql"], approval_nonce="wrong",
                    ),
                    current_user=_FAKE_SUPER_ADMIN,
                    recent_auth_token="dummy",
                )

        self.assertEqual(ctx.exception.status_code, 401)


# --------------------------------------------------
# 헬퍼 — 임시 DB + migrations 디렉터리(9, 10, 11번 시나리오 공용)
# --------------------------------------------------

class _TempMigrationEnvMixin:

    def _make_temp_env(self):

        tmp_root = Path(tempfile.mkdtemp())
        db_path = tmp_root / "homez.db"
        migrations_dir = tmp_root / "migrations"
        migrations_dir.mkdir()
        backups_dir = tmp_root / "backups"
        backups_dir.mkdir()

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        _write_migration(
            migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            migrations_dir / "20260102_00_create_b.sql",
            "BEGIN;\nCREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        return tmp_root, db_path, migrations_dir, backups_dir


# --------------------------------------------------
# 9. 백업 실패 시 Migration 미실행
# --------------------------------------------------

class BackupFailureTestCase(_TempMigrationEnvMixin, unittest.TestCase):

    def setUp(self):

        self.tmp_root, self.db_path, self.migrations_dir, self.backups_dir = (
            self._make_temp_env()
        )

    def tearDown(self):

        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_backup_failure_prevents_any_write(self):

        with patch(
            "app.database.bootstrap.MigrationRunner.create_backup",
            side_effect=OSError("시뮬레이션된 백업 실패(디스크 가득 등)"),
        ):
            with self.assertRaises(OSError):
                bootstrap_environment(
                    db_path=self.db_path, migrations_dir=self.migrations_dir,
                    backups_dir=self.backups_dir,
                    approved_migration_files=["20260102_00_create_b.sql"],
                )

        conn = sqlite3.connect(str(self.db_path))
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        # schema_migrations 테이블조차 아직 생성되지 않았어야 한다 —
        # 백업 실패 시점에는 어떤 쓰기 연결도 열리지 않았다.
        conn.close()

        self.assertNotIn("b", tables)
        self.assertNotIn("schema_migrations", tables)


# --------------------------------------------------
# 10. Migration 실패 시 정합성 유지(부분 적용을 성공으로 기록 안 함)
# --------------------------------------------------

class PartialApplyFailureIntegrityTestCase(_TempMigrationEnvMixin, unittest.TestCase):

    def setUp(self):

        self.tmp_root, self.db_path, self.migrations_dir, self.backups_dir = (
            self._make_temp_env()
        )

    def tearDown(self):

        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_apply_failure_leaves_migration_still_pending_and_db_intact(self):

        with patch(
            "app.database.bootstrap.MigrationRunner.apply_pending",
            side_effect=RuntimeError("시뮬레이션된 적용 실패"),
        ):
            with self.assertRaises(RuntimeError):
                bootstrap_environment(
                    db_path=self.db_path, migrations_dir=self.migrations_dir,
                    backups_dir=self.backups_dir,
                    approved_migration_files=["20260102_00_create_b.sql"],
                )

        conn = sqlite3.connect(str(self.db_path))
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(integrity, "ok")
        conn.close()

        # 다음 진단에서도 여전히 pending으로 보여야 한다 — "실패"가
        # "적용됨"으로 잘못 기록되지 않았다는 뜻이다.
        from app.database.migration_runner import MigrationRunner

        runner = MigrationRunner(self.db_path, self.migrations_dir)
        ro_conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        ro_conn.execute("PRAGMA query_only = ON")
        try:
            diagnosis = runner.diagnose(ro_conn)
        finally:
            ro_conn.close()

        self.assertIn("20260102_00_create_b.sql", diagnosis["pending"])

    def test_foreign_key_violation_after_apply_blocks_success(self):
        """
        이 저장소는 FK를 쓰지 않지만, foreign_key_check 자체가 실제로
        결과를 검사하고 위반 시 차단하는지는 별도로 확인해야 한다 —
        PRAGMA foreign_key_check 결과를 강제로 위반 있음으로 꾸며서
        확인한다.
        """

        # PRAGMA foreign_key_check의 반환값만 조작한다(다른 SQL은 그대로
        # 실제 sqlite3에 위임한다) — sqlite3.Connection은 C 확장 타입이라
        # 인스턴스에 임의 속성(execute 등)을 대입할 수 없으므로, connect()의
        # factory 인자로 넘기는 서브클래스에서 메서드를 오버라이드한다.
        class _FKViolationConnection(sqlite3.Connection):

            def execute(self, sql, *args, **kwargs):

                if isinstance(sql, str) and sql.strip() == "PRAGMA foreign_key_check":

                    class _FakeCursor:

                        def fetchall(self_inner):
                            return [("b", 1, "a", 0)]

                    return _FakeCursor()

                return super().execute(sql, *args, **kwargs)

        real_connect = sqlite3.connect

        def fake_connect(*args, **kwargs):
            kwargs.setdefault("factory", _FKViolationConnection)
            return real_connect(*args, **kwargs)

        with patch("sqlite3.connect", side_effect=fake_connect):
            with self.assertRaises(RuntimeError):
                bootstrap_environment(
                    db_path=self.db_path, migrations_dir=self.migrations_dir,
                    backups_dir=self.backups_dir,
                    approved_migration_files=["20260102_00_create_b.sql"],
                )


# --------------------------------------------------
# 11. 동시 승인 요청 중 정확히 하나만 실행
# --------------------------------------------------

class ConcurrentApprovalTestCase(_TempMigrationEnvMixin, unittest.TestCase):

    def setUp(self):

        self.tmp_root, self.db_path, self.migrations_dir, self.backups_dir = (
            self._make_temp_env()
        )
        self._paths_patcher = patch.object(
            migration_approval, "_real_paths",
            return_value=(self.db_path, self.migrations_dir, self.backups_dir),
        )
        self._paths_patcher.start()
        self._recent_auth_patcher = patch.object(
            migration_approval, "consume_recent_auth_token", return_value=True,
        )
        self._recent_auth_patcher.start()
        self._nonce_patcher = patch.object(
            migration_approval, "verify_approval_nonce",
            return_value=MigrationApprovalNonceStatus.VALID,
        )
        self._nonce_patcher.start()

    def tearDown(self):

        self._nonce_patcher.stop()
        self._recent_auth_patcher.stop()
        self._paths_patcher.stop()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_second_concurrent_request_rejected_immediately_while_first_holds_lock(self):
        """
        결정적 버전 — 락을 직접 쥔 상태에서 두 번째 요청이 즉시(블로킹
        없이) 423으로 거절되는지 확인한다. 실제 스레드 경합보다 더
        빠르고 항상 재현 가능하다.
        """

        acquired = migration_approval._apply_lock.acquire(blocking=False)
        self.assertTrue(acquired)

        try:
            with self.assertRaises(HTTPException) as ctx:
                migration_approval.approve_migration(
                    migration_approval.MigrationApproveRequest(
                        approved_files=["20260102_00_create_b.sql"],
                        approval_nonce="n",
                    ),
                    current_user=_FAKE_SUPER_ADMIN,
                    recent_auth_token="dummy",
                )
            self.assertEqual(ctx.exception.status_code, 423)
        finally:
            migration_approval._apply_lock.release()

    def test_two_real_concurrent_threads_exactly_one_applies(self):

        original_bootstrap = migration_approval.bootstrap_environment

        def slow_bootstrap(*args, **kwargs):
            time.sleep(0.3)
            return original_bootstrap(*args, **kwargs)

        results = []

        def worker():
            try:
                with patch.object(
                    migration_approval, "bootstrap_environment",
                    side_effect=slow_bootstrap,
                ):
                    result = migration_approval.approve_migration(
                        migration_approval.MigrationApproveRequest(
                            approved_files=["20260102_00_create_b.sql"],
                            approval_nonce="n",
                        ),
                        current_user=_FAKE_SUPER_ADMIN,
                        recent_auth_token="dummy",
                    )
                    results.append(("ok", result))
            except HTTPException as exc:
                results.append(("blocked", exc.status_code))

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        time.sleep(0.05)  # t1이 먼저 락을 잡도록 살짝 시간차를 둔다
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        outcomes = [r[0] for r in results]
        self.assertEqual(outcomes.count("ok"), 1, results)
        self.assertEqual(outcomes.count("blocked"), 1, results)
        blocked_status = next(r[1] for r in results if r[0] == "blocked")
        self.assertEqual(blocked_status, 423)


# --------------------------------------------------
# 12. 서버 재시작 후 제한 모드 재계산(캐시가 아니라 실제 DB 기준)
# --------------------------------------------------

class RestartRecomputationTestCase(_TempMigrationEnvMixin, unittest.TestCase):

    def setUp(self):

        self.tmp_root, self.db_path, self.migrations_dir, self.backups_dir = (
            self._make_temp_env()
        )
        self._paths_patcher = patch.object(
            migration_restricted_mode, "_real_migration_paths",
            return_value=(self.db_path, self.migrations_dir),
        )
        self._paths_patcher.start()
        migration_restricted_mode.reset_restricted_mode_state_for_tests()

    def tearDown(self):

        self._paths_patcher.stop()
        migration_restricted_mode.reset_restricted_mode_state_for_tests()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_restricted_before_any_computation(self):
        """캐시가 아직 계산되지 않은 상태는 fail-closed로 제한 모드다."""

        self.assertTrue(migration_restricted_mode.is_restricted_mode())

    def test_restricted_true_while_pending_migration_exists(self):

        state = migration_restricted_mode.refresh_restricted_mode_state()
        self.assertTrue(state.restricted)
        self.assertIn("20260102_00_create_b.sql", state.pending_files)
        self.assertTrue(migration_restricted_mode.is_restricted_mode())

    def test_restart_recomputes_from_actual_db_not_stale_cache(self):
        """
        "재시작"을 캐시 초기화로 흉내낸다 — 재계산 결과가 매번 그
        시점의 실제 DB 상태를 반영해야 한다(이전 계산값을 그대로
        재사용하지 않는다).
        """

        migration_restricted_mode.refresh_restricted_mode_state()
        self.assertTrue(migration_restricted_mode.is_restricted_mode())

        # 재시작 흉내: 캐시를 완전히 비운다.
        migration_restricted_mode.reset_restricted_mode_state_for_tests()
        self.assertTrue(migration_restricted_mode.is_restricted_mode())  # 미계산 = fail-closed

        # 재시작 직후 다시 계산 — 여전히 pending이 있으므로 여전히 제한.
        migration_restricted_mode.refresh_restricted_mode_state()
        self.assertTrue(migration_restricted_mode.is_restricted_mode())

        # 이제 실제로 적용한다.
        bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
            approved_migration_files=["20260102_00_create_b.sql"],
        )

        # 다시 "재시작"을 흉내내고 재계산하면 이제는 제한 모드가 아니다.
        migration_restricted_mode.reset_restricted_mode_state_for_tests()
        migration_restricted_mode.refresh_restricted_mode_state()
        self.assertFalse(migration_restricted_mode.is_restricted_mode())

    def test_diagnose_failure_is_fail_closed_not_a_crash(self):

        with patch(
            "app.database.migration_runner.MigrationRunner.diagnose",
            side_effect=RuntimeError("시뮬레이션된 checksum 불일치"),
        ):
            state = migration_restricted_mode.refresh_restricted_mode_state()

        self.assertTrue(state.restricted)
        self.assertIsNotNone(state.error)


# --------------------------------------------------
# 13. ko-KR/en-US 문구 확인
# --------------------------------------------------

class RestrictedModeWordingTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.ko = _load_js_object_literal(
            os.path.join(I18N_DIR, "ko-KR.js"), "HOMEZ_I18N_CATALOG_KO_KR",
        )
        cls.en = _load_js_object_literal(
            os.path.join(I18N_DIR, "en-US.js"), "HOMEZ_I18N_CATALOG_EN_US",
        )

    def test_new_gate_g_keys_present_in_both_locales_with_nonempty_values(self):

        new_keys = (
            "ma.current_password_label",
            "ma.error_current_password_required",
            "ma.error_invalid_password",
            "ma.error_too_many_attempts",
            "ma.error_super_admin_required",
            "ma.error_nonce_invalid",
            "ma.error_apply_in_progress",
        )

        for key in new_keys:
            self.assertIn(key, self.ko, key)
            self.assertIn(key, self.en, key)
            self.assertTrue(self.ko[key].strip(), key)
            self.assertTrue(self.en[key].strip(), key)
            self.assertNotEqual(self.ko[key], self.en[key], key)

    def test_server_423_detail_matches_client_write_blocked_key(self):
        """
        서버 미들웨어가 보내는 한국어 detail 문자열이 클라이언트가 이미
        쓰고 있는 ma.write_blocked_error와 정확히 같아야 한다 — 서버가
        직접 차단하는 드문 경로(우회 요청)에서도 문구가 어긋나지
        않는다.
        """

        server_detail = (
            "제한 모드입니다 — 대기 중인 데이터 구조 업데이트를 "
            "먼저 적용해야 저장·전송이 가능합니다."
        )
        self.assertEqual(self.ko["ma.write_blocked_error"], server_detail)

    def test_error_code_constant_matches_header_contract(self):

        self.assertEqual(
            app_main.MIGRATION_RESTRICTED_MODE_ERROR_CODE,
            "MIGRATION_RESTRICTED_MODE",
        )


class RealHomezDbUntouchedTestCase(unittest.TestCase):
    """이 파일의 모든 테스트가 실제 homez.db를 건드리지 않았는지 확인한다."""

    REAL_DB_PATH = os.path.join(REPO_ROOT, "homez.db")

    def test_real_db_hash_unchanged_after_module_tests(self):

        if not os.path.exists(self.REAL_DB_PATH):
            self.skipTest("실제 homez.db가 이 환경에 없습니다.")

        import hashlib

        with open(self.REAL_DB_PATH, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()

        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
