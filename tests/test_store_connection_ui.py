"""
=========================================================
Homez OS

File : tests/test_store_connection_ui.py

판매채널 연동 — Desktop UI/이미지 정적 검증:
1) 안내 이미지 12개 파일이 실제로 존재하고 올바른 형식(SVG, 16:9,
   1440px 이상)인지 확인
2) 각 안내 단계에 alt text가 있는지 확인
3) 공식 문서 URL이 올바른지 확인
4) console.html에 쿠팡·네이버 로그인 ID/비밀번호 입력 필드가 없는지 확인
5) console.html/console.js에 필요한 UI 요소(뷰, 다이얼로그, 버튼)가
   존재하는지 확인
6) console.js 문법 오류가 없는지 확인(node가 있는 환경에서만)
=========================================================
"""

import os
import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDES_DIR = os.path.join(REPO_ROOT, "assets", "guides", "marketplace")

EXPECTED_IMAGE_FILES = [
    "marketplace-connection-overview.svg",
    "coupang-step-01-open-wing.svg",
    "coupang-step-02-open-api-menu.svg",
    "coupang-step-03-issue-api-key.svg",
    "coupang-step-04-copy-credentials.svg",
    "coupang-step-05-homez-connect.svg",
    "naver-step-01-open-api-center.svg",
    "naver-step-02-register-application.svg",
    "naver-step-03-permissions.svg",
    "naver-step-04-copy-credentials.svg",
    "naver-step-05-homez-connect.svg",
    "credential-security-warning.svg",
]


class GuideImageAssetTestCase(unittest.TestCase):

    def test_all_twelve_image_files_exist(self):

        for filename in EXPECTED_IMAGE_FILES:
            path = os.path.join(GUIDES_DIR, filename)
            self.assertTrue(os.path.exists(path), f"{filename}가 존재하지 않습니다.")
            self.assertGreater(os.path.getsize(path), 0, f"{filename}가 비어 있습니다.")

    def test_all_images_are_well_formed_svg_with_16_9_and_min_width(self):

        for filename in EXPECTED_IMAGE_FILES:
            path = os.path.join(GUIDES_DIR, filename)
            tree = ET.parse(path)
            root = tree.getroot()
            view_box = root.get("viewBox")
            self.assertIsNotNone(view_box, f"{filename}에 viewBox가 없습니다.")

            _, _, w_str, h_str = view_box.split()
            width, height = float(w_str), float(h_str)

            self.assertGreaterEqual(
                width, 1440, f"{filename} 너비가 1440px 미만입니다.",
            )
            ratio = width / height
            self.assertAlmostEqual(
                ratio, 16 / 9, delta=0.05,
                msg=f"{filename}이 16:9 비율이 아닙니다(ratio={ratio}).",
            )

    def test_each_image_has_a_title_element_as_alt_text_source(self):

        for filename in EXPECTED_IMAGE_FILES:
            path = os.path.join(GUIDES_DIR, filename)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("<title>", content, f"{filename}에 <title>이 없습니다.")
            self.assertIn(
                'aria-label="', content, f"{filename}에 aria-label이 없습니다.",
            )

    def test_no_real_secret_or_pii_patterns_in_images(self):
        """모든 예시 값은 EXAMPLE_ONLY 또는 마스킹만 사용해야 한다."""

        suspicious_patterns = [
            re.compile(r"\b[\w.+-]+@(?!example\.com)[\w-]+\.[\w.-]+\b"),
        ]

        for filename in EXPECTED_IMAGE_FILES:
            path = os.path.join(GUIDES_DIR, filename)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            for pattern in suspicious_patterns:
                self.assertEqual(
                    pattern.findall(content), [],
                    f"{filename}에 실제처럼 보이는 이메일이 있습니다.",
                )


class GuideStepDataTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        from app.domains.store_connection.router import _GUIDES

        cls.guides = _GUIDES

    def test_coupang_and_naver_guides_registered(self):

        self.assertIn("COUPANG", self.guides)
        self.assertIn("NAVER_SMARTSTORE", self.guides)

    def test_official_doc_urls_are_correct(self):

        self.assertEqual(
            self.guides["COUPANG"]["official_doc_url"],
            "https://developers.coupang.com/",
        )
        self.assertEqual(
            self.guides["NAVER_SMARTSTORE"]["official_doc_url"],
            "https://apicenter.commerce.naver.com/",
        )

    def test_every_guide_step_has_non_empty_alt_text(self):

        for marketplace_code, guide in self.guides.items():
            for step in guide["steps"]:
                self.assertTrue(
                    step["image_alt_text"].strip(),
                    f"{marketplace_code} step {step['step_number']}에 "
                    "alt text가 없습니다.",
                )

    def test_every_guide_step_image_path_exists_on_disk(self):

        for marketplace_code, guide in self.guides.items():
            for step in guide["steps"]:
                relative_path = step["image_path"]
                full_path = os.path.join(REPO_ROOT, relative_path)
                self.assertTrue(
                    os.path.exists(full_path),
                    f"{marketplace_code} step {step['step_number']}의 이미지 "
                    f"파일이 없습니다: {relative_path}",
                )

    def test_coupang_has_five_steps_and_naver_has_five_steps(self):

        self.assertEqual(len(self.guides["COUPANG"]["steps"]), 5)
        self.assertEqual(len(self.guides["NAVER_SMARTSTORE"]["steps"]), 5)

    def test_no_step_claims_to_be_official_capture_without_a_real_source(self):
        """
        공식 화면 캡처를 확보하지 못했으므로(2026-07-31 기준) 모든 단계가
        is_official_capture=False(HOMEZ 자체 설명 다이어그램)여야 한다 —
        실제 화면처럼 위조하지 않는다는 요구사항의 구조적 확인.
        """

        for marketplace_code, guide in self.guides.items():
            for step in guide["steps"]:
                self.assertFalse(
                    step["is_official_capture"],
                    f"{marketplace_code} step {step['step_number']}이 공식 "
                    "캡처라고 주장하지만 실제 공식 스크린샷을 확보한 적이 "
                    "없습니다.",
                )


class ConsoleHtmlNoLoginFieldsTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()

    def test_store_connection_view_exists(self):

        self.assertIn('id="view-store-connection"', self.html)
        self.assertIn('data-view="store-connection"', self.html)

    def test_wizard_and_zoom_dialogs_exist(self):

        self.assertIn('id="sc-wizard-dialog"', self.html)
        self.assertIn('id="sc-image-zoom-dialog"', self.html)

    def test_security_banner_present(self):

        self.assertIn("로그인 비밀번호를 요구하지 않습니다", self.html)

    def test_store_connection_section_has_no_password_type_inputs(self):
        """
        판매채널(쿠팡·네이버) 연동 카드 자체(HOMEZ 앱 로그인 폼 제외)에는
        쿠팡/네이버 로그인 비밀번호에 해당하는 password 타입 입력
        필드가 정적으로 존재하지 않는다 — Secret 입력 필드는 wizard가
        동적으로 JS에서 생성하므로(console.js 쪽에서 검증), 정적
        HTML에는 아예 없다.

        2026-08-30 V7 후속 안정화 — 검사 범위를 "sc-compare-grid"(쿠팡·
        네이버 연결 카드)로 좁혔다. 이전에는 이 뒤에 추가된 R2
        (Cloudflare 공개 이미지 호스팅) Secret Access Key 정적 입력
        필드까지 같은 슬라이스에 포함해 오탐(false positive)이
        발생했다 — R2 Secret은 "쿠팡/네이버 로그인 비밀번호"가 아니라
        이 화면의 보안 배너가 명시적으로 허용하는 "공식 API
        자격증명" 범주이고, type="password"로 마스킹하는 것 자체가
        올바른 처리다(속성 축소가 아니라 대상 축소).
        """

        start = self.html.index('class="sc-compare-grid"')
        end = self.html.index("</div>", self.html.index('id="sc-start-naver-btn"'))
        section = self.html[start:end]

        self.assertNotIn('type="password"', section)


class ConsoleJsUiLogicTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_required_functions_exist(self):

        for name in (
            "loadStoreConnectionView", "scOpenWizard", "scRenderWizardStep",
            "scRunVerify", "scRunSave", "scCloseWizard", "scOpenImageZoom",
            "initStoreConnectionWizard",
        ):
            self.assertIn(f"function {name}", self.js, f"{name} 함수가 없습니다.")

    def test_credential_fields_have_no_login_id_password_labels(self):

        forbidden = ("로그인 아이디", "로그인 비밀번호를 입력하세요")
        for term in forbidden:
            self.assertNotIn(term, self.js)

    def test_syntax_is_valid_when_node_available(self):

        node_path = shutil.which("node")
        if node_path is None:
            self.skipTest("Node.js를 찾을 수 없어 문법 검사를 건너뜁니다.")

        js_path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        result = subprocess.run(
            [node_path, "--check", js_path],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_verify_failure_ui_distinguishes_error_categories(self):
        """
        Gate 4(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5) 요구사항 —
        "연결 실패, 권한 부족, 만료, rate limit 상태를 UI에 구분
        표시한다". scErrorCategory()가 각 카테고리를 서로 다른
        CSS class로 매핑하는지 정적으로 확인한다(색상 구분은
        console.css에서 별도 검증).
        """

        self.assertIn("function scErrorCategory", self.js)

        for error_code, css_class in (
            ("RATE_LIMITED_429", "rate-limited"),
            ("CREDENTIAL_EXPIRED", "expired"),
            ("UNAUTHORIZED_401", "unauthorized"),
            ("FORBIDDEN_403", "forbidden"),
            ("PERMISSION_PENDING", "pending"),
        ):
            self.assertIn(error_code, self.js)
            self.assertIn(css_class, self.js)


class ConsoleCssErrorCategoryStylingTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.css")
        with open(path, encoding="utf-8") as f:
            cls.css = f.read()

    def test_error_category_classes_have_distinct_styling(self):

        for css_class in ("rate-limited", "expired", "pending", "platform"):
            self.assertIn(
                f".sc-verify-result.failure.{css_class}", self.css,
                f"{css_class} 카테고리의 구분된 스타일이 없습니다.",
            )


class RouterHtmlWiringTestCase(unittest.TestCase):

    def test_guide_asset_route_registered(self):

        from app.web.router import router as web_router

        paths = [
            getattr(r, "path", None) for r in web_router.routes
        ]
        self.assertIn("/assets/guides/marketplace/{filename}", paths)

    def test_guide_asset_route_rejects_path_traversal(self):

        from fastapi import HTTPException

        from app.web.router import marketplace_connection_guide_asset

        with self.assertRaises(HTTPException):
            marketplace_connection_guide_asset("../../../etc/passwd")

        with self.assertRaises(HTTPException):
            marketplace_connection_guide_asset("subdir/file.svg")

    def test_guide_asset_route_only_serves_svg(self):

        from fastapi import HTTPException

        from app.web.router import marketplace_connection_guide_asset

        with self.assertRaises(HTTPException):
            marketplace_connection_guide_asset("something.png")


if __name__ == "__main__":
    unittest.main()
