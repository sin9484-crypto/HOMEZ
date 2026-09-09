"""
=========================================================
Homez OS

File : tests/test_app_version_single_source.py

작업 5(2026-08-21) — app/core/version.py의 VERSION을 배포 SemVer의
유일한 출처로 단일화했는지 검증한다. "V7"은 제품 개발 단계 이름일
뿐 SemVer와 섞이면 안 된다(모든 곳이 "V7"이 아니라 실제 "2.1.0"을
가리켜야 한다).
=========================================================
"""

import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


class AppVersionSingleSourceTestCase(unittest.TestCase):

    def test_config_app_version_matches_version_module(self):

        from app.core.config import settings
        from app.core.version import VERSION

        self.assertEqual(settings.APP_VERSION, VERSION)

    def test_diagnostics_service_imports_same_version_module(self):

        import app.core.version as version_module
        import app.domains.diagnostics.service as diagnostics_service

        # 값만 같은 게 아니라 실제로 같은 모듈에서 가져온 것인지
        # 확인한다 — 우연히 값이 겹치는 별도 하드코딩을 걸러낸다.
        self.assertIs(
            diagnostics_service.APP_VERSION, version_module.VERSION,
        )

    def test_web_router_system_status_reads_version_module(self):

        source = (_REPO_ROOT / "app" / "web" / "router.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("from app.core.version import VERSION", source)
        self.assertIn('"app_version": app_version', source)

    def test_console_js_renders_app_version_from_api_response(self):

        source = (_REPO_ROOT / "app" / "web" / "console.js").read_text(
            encoding="utf-8",
        )
        self.assertIn("data.app_version", source)
        self.assertIn("system.app_version_label", source)

    def test_installer_iss_version_matches_version_module(self):
        """
        Inno Setup은 Python 모듈을 컴파일 시점에 읽을 수 없어
        AppVersion을 문자열로 하드코딩한다 — 이 테스트는 그 하드코딩
        값이 app/core/version.py::VERSION과 실제로 같은지 드리프트를
        감시한다(값이 벌어지면 이 테스트가 실패해 알려준다). 이번
        라운드는 인스톨러를 실행·재빌드하지 않는다 — 이 테스트도
        .iss 파일을 읽기만 한다.
        """

        from app.core.version import VERSION

        iss_path = _REPO_ROOT / "installer" / "homez.iss"
        source = iss_path.read_text(encoding="utf-8")

        match = re.search(r'#define AppVersion "([^"]+)"', source)
        self.assertIsNotNone(
            match, "installer/homez.iss에서 AppVersion 정의를 찾지 못함",
        )
        self.assertEqual(match.group(1), VERSION)

    def test_version_module_is_semver_and_not_the_v7_stage_name(self):

        from app.core.version import VERSION

        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")
        self.assertNotEqual(VERSION, "V7")
        self.assertNotIn("V7", VERSION)


if __name__ == "__main__":
    unittest.main()
