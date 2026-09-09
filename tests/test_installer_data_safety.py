"""Inno Setup 운영/격리 설치 데이터 안전 계약."""

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER_PATH = REPO_ROOT / "installer" / "homez.iss"


class InstallerDataSafetyContractTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = INSTALLER_PATH.read_text(encoding="utf-8")

    def test_production_app_id_is_unchanged(self):
        self.assertIn(
            "AppId={{6F5B2E6E-6C9C-4C7B-9C2B-3A6D4E9F1A02}",
            self.source,
        )

    def test_test_app_id_is_distinct(self):
        self.assertIn("#ifdef HOMEZ_TEST_INSTALLER", self.source)
        self.assertIn(
            "AppId={{000D9771-99FC-4AC7-BACC-1A115E256113}",
            self.source,
        )

    def test_test_build_omits_run_section_at_preprocess_time(self):
        guarded_run = re.search(
            r"#ifndef HOMEZ_TEST_INSTALLER\s*\[Run\].*?#endif",
            self.source,
            re.DOTALL,
        )
        self.assertIsNotNone(guarded_run)
        self.assertIn("Homez.exe", guarded_run.group(0).replace("{#AppExeName}", "Homez.exe"))

    def test_test_build_does_not_create_official_shortcuts(self):
        icons = re.search(r"\[Icons\](.*?)\[Run\]", self.source, re.DOTALL)
        self.assertIsNotNone(icons)
        self.assertIn("#ifdef HOMEZ_TEST_INSTALLER", icons.group(1))
        self.assertIn("#else", icons.group(1))

    def test_test_uninstall_uses_only_explicit_test_root(self):
        self.assertIn(
            "DataDir := CanonicalPath(GetEnv('HOMEZ_DATA_ROOT'))",
            self.source,
        )
        self.assertIn("if not IsSafeTestDataDir(DataDir) then", self.source)

    def test_test_delete_requires_sentinel(self):
        self.assertIn(".homez-isolated-test-root", self.source)
        self.assertIn("FileExists(SentinelPath)", self.source)

    def test_test_delete_blocks_operating_path_overlap(self):
        self.assertIn("function PathsOverlap", self.source)
        self.assertIn(
            "PathsOverlap(CanonicalDataDir, OfficialDataDir)",
            self.source,
        )

    def test_empty_drive_and_localappdata_roots_are_blocked(self):
        self.assertIn("function IsDriveOrLocalAppDataRoot", self.source)
        self.assertIn("PathCanonical = ''", self.source)
        self.assertIn("ExtractFileDrive(PathCanonical)", self.source)
        self.assertIn("LocalAppDataCanonical", self.source)

    def test_test_delete_is_explicit_opt_in(self):
        self.assertIn("if TestOverride <> 'DELETE' then", self.source)
        self.assertIn(
            "HOMEZ isolated test data preserved: DELETE not explicitly requested",
            self.source,
        )

    def test_test_install_without_root_cannot_create_sentinel(self):
        sentinel_function = re.search(
            r"procedure CreateTestDataSentinel\(\);\s*\nvar\b.*?^end;",
            self.source,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(sentinel_function)
        body = sentinel_function.group(0)
        self.assertIn("GetEnv('HOMEZ_DATA_ROOT')", body)
        self.assertIn("TestDataDir = ''", body)


if __name__ == "__main__":
    unittest.main()
