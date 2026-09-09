"""
=========================================================
Homez OS

File : tests/test_packaging_foundation.py

Gate Y-6(2026-08-12) — 패키징 기반 검증. 이 세션은 실제
`pyinstaller homez.spec`을 실행하지 않는다(수백 MB 산출물 생성 +
검증되지 않은 실행 파일을 만드는 것은 이번 단계 범위 밖) — 대신
스펙 파일의 정적 유효성(구문, 참조 경로 실존 여부)과, 이미 구현된
`app/desktop/paths.py`의 frozen/dev 경로 분리 계약이 여전히
일관적인지, requirements.txt에 빌드 전용 도구가 섞여 들어가지
않았는지를 검증한다.
=========================================================
"""

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class SpecFileTestCase(unittest.TestCase):

    def setUp(self):

        self.spec_path = REPO_ROOT / "homez.spec"
        self.assertTrue(
            self.spec_path.exists(),
            "homez.spec가 저장소 루트에 없습니다.",
        )
        self.source = self.spec_path.read_text(encoding="utf-8")

    def test_spec_file_is_syntactically_valid_python(self):

        ast.parse(self.source)  # SyntaxError면 여기서 실패

    def test_spec_uses_onedir_not_onefile(self):
        """
        설계 근거(homez.spec 상단 주석)와 실제 코드가 일치하는지 —
        COLLECT()가 있어야 onedir이다(onefile은 COLLECT를 쓰지 않고
        EXE에 a.binaries/a.datas를 직접 넣는다).
        """

        self.assertIn("COLLECT(", self.source)

    def test_spec_disables_upx(self):

        self.assertIn("upx=False", self.source)

    def test_spec_references_existing_data_paths(self):
        """
        (repo-relative 소스 경로, dest) 쌍으로 datas에 나열된 실제
        파일/디렉터리가 전부 존재하는지 확인한다 — 존재하지 않는
        경로를 datas에 넣으면 pyinstaller 실행 자체가 즉시
        실패한다.
        """

        expected_paths = [
            "app/web/console.html",
            "app/web/console.css",
            "app/web/console.js",
            "app/web/i18n",
            "app/web/assets",
            "app/web/vendor",
            "migrations",
            "assets",
            "assets/homez-app.ico",
        ]

        for rel_path in expected_paths:
            with self.subTest(path=rel_path):
                self.assertTrue(
                    (REPO_ROOT / rel_path).exists(),
                    f"{rel_path}가 존재하지 않습니다 — homez.spec의 "
                    "datas 참조가 깨졌습니다.",
                )

    def test_spec_entry_point_exists(self):

        self.assertTrue(
            (REPO_ROOT / "app" / "desktop" / "main.py").exists(),
        )

    def test_spec_icon_path_matches_existing_verified_icon(self):
        """
        Gate F-10에서 이미 검증된 아이콘(assets/homez-app.ico)을
        그대로 재사용하는지 확인한다 — 새 아이콘을 만들지 않는다
        (재구현 금지 원칙).
        """

        self.assertIn('"assets" / "homez-app.ico"', self.source)


class RequirementsSeparationTestCase(unittest.TestCase):
    """
    Gate F-10A에서 Pillow를 런타임 requirements.txt에서 제외했던
    것과 동일한 원칙 — PyInstaller도 빌드 전용 도구이므로
    requirements.txt에는 없어야 하고, requirements-build.txt에만
    있어야 한다.
    """

    def test_pyinstaller_absent_from_runtime_requirements(self):

        runtime_reqs = (REPO_ROOT / "requirements.txt").read_text(
            encoding="utf-8",
        )
        self.assertNotIn("pyinstaller", runtime_reqs.lower())

    def test_pyinstaller_present_in_build_requirements(self):

        build_reqs_path = REPO_ROOT / "requirements-build.txt"
        self.assertTrue(build_reqs_path.exists())

        build_reqs = build_reqs_path.read_text(encoding="utf-8")
        self.assertIn("pyinstaller", build_reqs.lower())

    def test_fastapi_upload_runtime_dependency_is_declared(self):
        """UploadFile routes must import in a clean packaged environment."""

        runtime_reqs = (REPO_ROOT / "requirements.txt").read_text(
            encoding="utf-8",
        )
        self.assertRegex(runtime_reqs, r"(?m)^python-multipart==")


class IconAssetTestCase(unittest.TestCase):

    def test_icon_file_has_valid_ico_magic_bytes(self):
        """
        ICO 파일 포맷 시그니처(00 00 01 00)를 직접 확인한다 —
        Gate F-10에서 이미 이 파일을 만들었지만, 이번 Gate Y-6은
        패키징 스펙이 참조하는 시점 기준으로 다시 한번 파일이
        실제 유효한 ICO인지 증거를 남긴다(재구현이 아니라 재확인).
        """

        icon_path = REPO_ROOT / "assets" / "homez-app.ico"
        self.assertTrue(icon_path.exists())

        header = icon_path.read_bytes()[:4]
        self.assertEqual(header, b"\x00\x00\x01\x00")


class DesktopPathsFrozenModeContractTestCase(unittest.TestCase):
    """
    app/desktop/paths.py는 이미 Gate F 시리즈에서 구현되어 있다 —
    여기서는 재구현하지 않고, Gate Y-6이 의존하는 계약(모든 사용자
    데이터 경로가 is_frozen() 분기와 LOCALAPPDATA를 갖고 있는지)만
    소스 코드 정적 검사로 재확인한다.
    """

    def setUp(self):

        self.source = (
            REPO_ROOT / "app" / "desktop" / "paths.py"
        ).read_text(encoding="utf-8")

    def test_all_user_data_dirs_have_frozen_branch(self):
        """2026-08-24 패키징 격리 재작업 — 이제 5개 함수 모두
        `_frozen_data_root()`(그 내부에서 LOCALAPPDATA를 기본값으로
        쓰고, HOMEZ_DATA_ROOT가 설정된 동안에만 격리 테스트용으로
        바꿔치기하는 공용 헬퍼)를 통해서만 LOCALAPPDATA에 도달한다 —
        각 함수 자신의 본문에 그 리터럴 문자열이 직접 있을 필요는
        없다. 대신 (a) 각 함수가 여전히 is_frozen() 분기를 갖고,
        (b) 그 분기가 `_frozen_data_root()`를 호출하며, (c) 공용
        헬퍼 자체가 실제로 LOCALAPPDATA를 기본값으로 쓰는지를
        따로 확인한다 — 계약의 실질은 그대로 유지된다."""

        helper_match = re.search(
            r"def _frozen_data_root\(.*?\n(?=def |\Z)",
            self.source, re.DOTALL,
        )
        self.assertIsNotNone(helper_match, "_frozen_data_root 정의를 못 찾음")
        self.assertIn(
            "LOCALAPPDATA", helper_match.group(0),
            "_frozen_data_root()에 LOCALAPPDATA 기본값이 없습니다.",
        )

        for func_name in (
            "get_data_dir",
            "get_logs_dir",
            "get_backups_dir",
            "get_config_dir",
            "get_media_dir",
        ):
            match = re.search(
                rf"def {func_name}\(.*?\n(?=def |\Z)",
                self.source,
                re.DOTALL,
            )
            self.assertIsNotNone(match, f"{func_name} 정의를 못 찾음")

            body = match.group(0)
            self.assertIn(
                "is_frozen()",
                body,
                f"{func_name}에 is_frozen() 분기가 없습니다.",
            )
            self.assertIn(
                "_frozen_data_root()",
                body,
                f"{func_name}이 _frozen_data_root()를 거치지 않습니다.",
            )

    def test_homez_db_path_requires_explicit_confirmation(self):
        """
        운영 DB 오접근 사고(2026-08-10, Gate V-1) 재발 방지 계약이
        패키징 이후에도 여전히 살아있는지 확인 — confirm=True 없이는
        예외를 던져야 한다.
        """

        self.assertIn(
            "ProductionDbAccessNotConfirmedError",
            self.source,
        )
        self.assertIn("confirm: bool = False", self.source)


if __name__ == "__main__":
    unittest.main()
