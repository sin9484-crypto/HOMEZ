"""
=========================================================
Homez OS

File : tests/test_db_identity.py

2026-08-30 후속 지시 — DB Identity 진단(app/domains/diagnostics/
db_identity.py) 격리 검증. 전부 임시 파일/디렉터리만 사용한다 —
실제 dev/운영 homez.db는 절대 열지 않는다.
=========================================================
"""

import os
import platform
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from app.core.guard import admin_guard
from app.domains.diagnostics import db_identity as db_identity_module
from app.domains.diagnostics.db_identity import (
    _get_windows_file_identity,
    _path_suggests_package_redirection,
    collect_db_identity,
)


_IS_WINDOWS = platform.system() == "Windows"


class DbIdentityBasicsTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_dbid_test_"))

    def tearDown(self):

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_temp_sqlite_path_hash_size(self):

        db_path = self.tmp_dir / "isolated.db"
        content = b"HOMEZ isolated test content, not a real database"
        db_path.write_bytes(content)

        import hashlib

        expected_hash = hashlib.sha256(content).hexdigest()

        result = collect_db_identity(
            engine_db_path=db_path, bootstrap_db_path=db_path,
        )

        self.assertTrue(result["exists"])
        self.assertEqual(result["resolved_db_path"], str(db_path.resolve()))
        self.assertEqual(result["sha256"], expected_hash)
        self.assertEqual(result["file_size_bytes"], len(content))
        self.assertTrue(result["identity_stable"])
        self.assertFalse(result["changed_during_measurement"])
        self.assertFalse(result["path_mismatch_warning"])

    def test_two_different_dbs_have_different_identity(self):

        db_a = self.tmp_dir / "a.db"
        db_b = self.tmp_dir / "b.db"
        db_a.write_bytes(b"content A - synthetic only")
        db_b.write_bytes(b"content B - synthetic only, different")

        result_a = collect_db_identity(engine_db_path=db_a, bootstrap_db_path=None)
        result_b = collect_db_identity(engine_db_path=db_b, bootstrap_db_path=None)

        self.assertNotEqual(result_a["sha256"], result_b["sha256"])
        if _IS_WINDOWS:
            self.assertNotEqual(
                result_a["windows_file_identity"]["file_index"],
                result_b["windows_file_identity"]["file_index"],
            )

    @unittest.skipUnless(_IS_WINDOWS, "하드링크 file_index 판별은 Windows 전용")
    def test_hardlink_shares_file_index(self):

        original = self.tmp_dir / "original.db"
        original.write_bytes(b"shared synthetic content via hardlink")
        linked = self.tmp_dir / "linked.db"
        os.link(str(original), str(linked))

        result_original = collect_db_identity(
            engine_db_path=original, bootstrap_db_path=None,
        )
        result_linked = collect_db_identity(
            engine_db_path=linked, bootstrap_db_path=None,
        )

        self.assertEqual(
            result_original["windows_file_identity"]["file_index"],
            result_linked["windows_file_identity"]["file_index"],
        )
        self.assertEqual(
            result_original["windows_file_identity"]["hardlink_count"], 2,
        )
        self.assertEqual(
            result_linked["windows_file_identity"]["hardlink_count"], 2,
        )

    @unittest.skipUnless(_IS_WINDOWS, "Windows 전용 file identity")
    def test_unrelated_file_has_different_file_index_from_hardlink_pair(self):

        original = self.tmp_dir / "original2.db"
        original.write_bytes(b"content")
        linked = self.tmp_dir / "linked2.db"
        os.link(str(original), str(linked))
        unrelated = self.tmp_dir / "unrelated.db"
        unrelated.write_bytes(b"totally separate file")

        r_original = collect_db_identity(engine_db_path=original, bootstrap_db_path=None)
        r_unrelated = collect_db_identity(engine_db_path=unrelated, bootstrap_db_path=None)

        self.assertNotEqual(
            r_original["windows_file_identity"]["file_index"],
            r_unrelated["windows_file_identity"]["file_index"],
        )

    def test_change_during_measurement_is_detected(self):
        # 해시 계산 도중(그 함수가 실행되는 사이) 파일이 실제로
        # 바뀌면 identity_stable=False로 보고돼야 한다. _sha256_of_file
        # 을 "정상 계산 + 파일을 실제로 변경"하는 걸로 잠깐 바꿔치기해
        # 그 경합 상황을 실제로 재현한다.
        db_path = self.tmp_dir / "changing.db"
        db_path.write_bytes(b"before")

        real_sha256 = db_identity_module._sha256_of_file

        def _sha256_then_mutate(path):
            digest = real_sha256(path)
            with open(path, "ab") as f:
                f.write(b"MUTATED_DURING_MEASUREMENT")
            return digest

        with mock.patch.object(
            db_identity_module, "_sha256_of_file", side_effect=_sha256_then_mutate,
        ):
            result = collect_db_identity(
                engine_db_path=db_path, bootstrap_db_path=None,
            )

        self.assertTrue(result["changed_during_measurement"])
        self.assertFalse(result["identity_stable"])

    def test_no_change_during_measurement_reports_stable(self):

        db_path = self.tmp_dir / "stable.db"
        db_path.write_bytes(b"stable content, never touched during measurement")

        result = collect_db_identity(engine_db_path=db_path, bootstrap_db_path=None)

        self.assertFalse(result["changed_during_measurement"])
        self.assertTrue(result["identity_stable"])

    def test_file_over_size_limit_skips_hash_without_crashing(self):
        # 2026-08-30 후속 지시(운영 안전 감사) — DB가 커지면 이 진단이
        # 요청을 오래 막지 않도록, 상한을 넘는 파일은 전체 해시 계산을
        # 건너뛰고 이유를 알린다(다른 필드는 그대로 즉시 돌려준다).
        # 실제로 500MB 파일을 만들지 않고, 상한값만 낮춰 합성한다.
        db_path = self.tmp_dir / "huge.db"
        db_path.write_bytes(b"x" * 1000)

        with mock.patch.object(db_identity_module, "_MAX_HASH_BYTES", 100):
            result = collect_db_identity(engine_db_path=db_path, bootstrap_db_path=None)

        self.assertIsNone(result["sha256"])
        self.assertEqual(result["hash_skipped_reason"], "FILE_TOO_LARGE")
        self.assertFalse(result["identity_stable"])
        self.assertEqual(result["file_size_bytes"], 1000)
        self.assertTrue(result["exists"])

    def test_file_under_size_limit_computes_hash_normally(self):

        db_path = self.tmp_dir / "small.db"
        db_path.write_bytes(b"small enough content")

        with mock.patch.object(db_identity_module, "_MAX_HASH_BYTES", 10_000_000):
            result = collect_db_identity(engine_db_path=db_path, bootstrap_db_path=None)

        self.assertIsNone(result["hash_skipped_reason"])
        self.assertIsNotNone(result["sha256"])

    def test_db_content_and_mtime_unchanged_after_collect(self):
        # "읽기 전용"이라는 주장을 실제로 증명한다 — 진단 실행 전후로
        # 파일 바이트와 mtime이 정확히 동일해야 한다.
        db_path = self.tmp_dir / "untouched.db"
        original_bytes = b"content that must survive the diagnostic untouched"
        db_path.write_bytes(original_bytes)
        mtime_before = db_path.stat().st_mtime

        collect_db_identity(engine_db_path=db_path, bootstrap_db_path=db_path)

        self.assertEqual(db_path.read_bytes(), original_bytes)
        self.assertEqual(db_path.stat().st_mtime, mtime_before)

    def test_path_mismatch_warning_when_engine_and_bootstrap_paths_differ(self):

        engine_path = self.tmp_dir / "engine.db"
        bootstrap_path = self.tmp_dir / "bootstrap.db"
        engine_path.write_bytes(b"engine content")
        bootstrap_path.write_bytes(b"bootstrap content")

        result = collect_db_identity(
            engine_db_path=engine_path, bootstrap_db_path=bootstrap_path,
        )

        self.assertTrue(result["path_mismatch_warning"])

    def test_no_path_mismatch_warning_when_paths_match(self):

        same_path = self.tmp_dir / "same.db"
        same_path.write_bytes(b"same content")

        result = collect_db_identity(
            engine_db_path=same_path, bootstrap_db_path=same_path,
        )

        self.assertFalse(result["path_mismatch_warning"])

    def test_missing_engine_path_reports_unavailable_without_crash(self):

        result = collect_db_identity(engine_db_path=None, bootstrap_db_path=None)

        self.assertIsNone(result["resolved_db_path"])
        self.assertFalse(result["exists"])
        self.assertFalse(result["identity_stable"])

    def test_missing_file_reports_without_crash(self):

        missing = self.tmp_dir / "does_not_exist.db"

        result = collect_db_identity(engine_db_path=missing, bootstrap_db_path=missing)

        self.assertFalse(result["exists"])
        self.assertFalse(result["identity_stable"])

    def test_packages_localcache_path_triggers_redirection_warning(self):
        # 이 세션에서 실제로 관측된 패턴을 합성 경로로 재현한다 — 진짜
        # 사용자 이름·실제 앱 패키지 이름은 쓰지 않는다.
        fake_root = self.tmp_dir / "Packages" / "SyntheticApp_test1234" / "LocalCache" / "Local" / "HOMEZ" / "data"
        fake_root.mkdir(parents=True)
        db_path = fake_root / "homez.db"
        db_path.write_bytes(b"synthetic redirected copy")

        result = collect_db_identity(engine_db_path=db_path, bootstrap_db_path=None)

        self.assertTrue(result["redirection_suspected"])

    def test_normal_path_does_not_trigger_redirection_warning(self):

        db_path = self.tmp_dir / "HOMEZ" / "data" / "homez.db"
        db_path.parent.mkdir(parents=True)
        db_path.write_bytes(b"normal path content")

        result = collect_db_identity(engine_db_path=db_path, bootstrap_db_path=None)

        self.assertFalse(result["redirection_suspected"])

    def test_homez_data_root_env_is_reported_masked_not_raw(self):

        db_path = self.tmp_dir / "masked_env.db"
        db_path.write_bytes(b"content")

        fake_root_value = str(self.tmp_dir / "some_isolated_test_root")
        with mock.patch.dict(os.environ, {"HOMEZ_DATA_ROOT": fake_root_value}):
            result = collect_db_identity(engine_db_path=db_path, bootstrap_db_path=None)

        env_info = result["homez_data_root_env"]
        self.assertTrue(env_info["is_set"])
        self.assertIsNotNone(env_info["masked_value"])
        self.assertNotEqual(env_info["masked_value"], fake_root_value)

    def test_homez_data_root_env_unset_is_reported_as_such(self):

        db_path = self.tmp_dir / "no_env.db"
        db_path.write_bytes(b"content")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HOMEZ_DATA_ROOT", None)
            result = collect_db_identity(engine_db_path=db_path, bootstrap_db_path=None)

        env_info = result["homez_data_root_env"]
        self.assertFalse(env_info["is_set"])
        self.assertIsNone(env_info["masked_value"])

    def test_non_windows_platform_reports_available_false_without_crash(self):
        # "지원하지 않음"이 아니라 available=False로 정상 응답해야
        # 한다는 요구를 직접 확인한다 — platform.system()을
        # 비-Windows로 흉내낸다.
        db_path = self.tmp_dir / "posix_sim.db"
        db_path.write_bytes(b"content")

        with mock.patch.object(
            db_identity_module.platform, "system", return_value="Linux",
        ):
            identity = _get_windows_file_identity(db_path)

        self.assertFalse(identity.available)
        self.assertEqual(identity.error, "NOT_WINDOWS")


class DbIdentityRedirectionHelperTestCase(unittest.TestCase):

    def test_path_helper_detects_packages_and_localcache_segments(self):

        suspicious = Path(
            r"C:\Users\test\AppData\Local\Packages\Some.App_abc123\LocalCache\Local\HOMEZ\data\homez.db",
        )
        self.assertTrue(_path_suggests_package_redirection(suspicious))

    def test_path_helper_ignores_normal_path(self):

        normal = Path(r"C:\Users\test\AppData\Local\HOMEZ\data\homez.db")
        self.assertFalse(_path_suggests_package_redirection(normal))


class DbIdentityAccessControlTestCase(unittest.TestCase):
    """
    회사 일반 사용자 접근 차단 / 관리자만 조회 가능. admin_guard는
    순수 함수(User -> User 또는 예외)라 FastAPI 서버를 띄우지 않고
    직접 호출해 검증한다 — /db-identity가 다른 진단 라우트와 동일하게
    admin_guard 의존성을 쓰는 것은 tests/test_diagnostics_export.py의
    라우트 계약 테스트가 이미 확인한다.
    """

    def test_regular_company_user_is_blocked(self):

        regular_user = SimpleNamespace(role="SELLER")

        with self.assertRaises(HTTPException) as ctx:
            admin_guard(regular_user)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_customer_user_is_blocked(self):

        customer_user = SimpleNamespace(role="CUSTOMER")

        with self.assertRaises(HTTPException):
            admin_guard(customer_user)

    def test_admin_user_is_allowed(self):

        admin_user = SimpleNamespace(role="ADMIN")

        result = admin_guard(admin_user)

        self.assertIs(result, admin_user)

    def test_super_admin_user_is_allowed(self):

        super_admin_user = SimpleNamespace(role="SUPER_ADMIN")

        result = admin_guard(super_admin_user)

        self.assertIs(result, super_admin_user)


if __name__ == "__main__":
    unittest.main()
