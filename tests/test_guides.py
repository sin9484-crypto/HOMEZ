"""
=========================================================
Homez OS

File : tests/test_guides.py

2026-08-13 — Desktop Console 사이드바 "가이드" 메뉴 백엔드 검증.

이 저장소는 httpx가 설치되어 있지 않아(기존 관례,
tests/test_route_authentication_contract.py 주석 참고) FastAPI
TestClient로 실제 HTTP 왕복을 재현하지 않는다 — 대신
  1) service.resolve_safe_path()/list_guides()를 순수 함수로 직접
     호출해 경로 탈출·확장자 allowlist·pending 판정을 검증하고,
  2) router.py의 엔드포인트 함수를 일반 파이썬 함수로 직접 호출해
     (FastAPI route handler는 평범한 callable이다) 경로 조작 요청이
     실제로 404가 되는지 확인하고,
  3) route.dependant.dependencies를 정적으로 읽어 admin_guard가 아닌
     get_current_user가 걸려 있는지(=일반 로그인 사용자도 접근
     가능) 고정한다.

실제 homez.db는 전혀 사용하지 않는다 — docs/guides/의 실제 파일만
읽기 전용으로 확인한다.
=========================================================
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.core.auth import get_current_user
from app.core.guard import admin_guard
from app.domains.guides import router as guides_router_module
from app.domains.guides import service as guides_service
from app.domains.guides.constants import GUIDE_REGISTRY


class ResolveSafePathTestCase(unittest.TestCase):
    """docs/guides/ 실제 파일 대상 — 임시 디렉터리를 만들지 않고
    저장소에 실제로 존재하는 파일/경로로 직접 검증한다(가짜 fixture가
    아니라 실 산출물 자체가 검증 대상)."""

    def test_real_ko_document_resolves(self):

        resolved = guides_service.resolve_safe_path("01_QUICK_START_KO.md")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_file())

    def test_real_ko_pdf_resolves(self):

        resolved = guides_service.resolve_safe_path("pdf/01_QUICK_START_KO.pdf")
        self.assertIsNotNone(resolved)

    def test_real_screenshot_resolves(self):

        resolved = guides_service.resolve_safe_path(
            "screenshots/01_quick_start/02_login_ko.png",
        )
        self.assertIsNotNone(resolved)

    def test_nonexistent_video_returns_none(self):
        """영상 파일은 아직 하나도 없다 — 존재하지 않는 파일을 있는
        것처럼 반환하면 안 된다(요구사항 핵심)."""

        resolved = guides_service.resolve_safe_path(
            "videos/01_quick_start_ko.mp4",
        )
        self.assertIsNone(resolved)

    def test_parent_traversal_rejected(self):

        # docs/guides/ 밖(저장소 루트의 homez.db)을 가리키려는 시도.
        resolved = guides_service.resolve_safe_path("../../homez.db")
        self.assertIsNone(resolved)

    def test_parent_traversal_with_extension_allowlisted_still_rejected(self):
        """확장자만 맞추면 통과할 거라는 우회 시도 — 그래도 경로
        탈출 자체가 먼저 막혀야 한다."""

        resolved = guides_service.resolve_safe_path(
            "../../app/web/console.md",
        )
        self.assertIsNone(resolved)

    def test_absolute_path_injection_rejected(self):

        resolved = guides_service.resolve_safe_path("C:\\Windows\\win.ini")
        self.assertIsNone(resolved)

    def test_disallowed_extension_rejected_even_if_file_exists(self):
        """allowlist 밖 확장자는 실제로 존재하는 파일이라도 서빙하지
        않는다 — 임시 디렉터리에 .py 파일을 만들어 확인한다."""

        tmp_dir = Path(tempfile.mkdtemp(prefix="homez_guides_test_"))
        try:
            (tmp_dir / "not_allowed.py").write_text("print('x')", encoding="utf-8")
            resolved = guides_service.resolve_safe_path(
                "not_allowed.py", root=tmp_dir,
            )
            self.assertIsNone(resolved)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_null_byte_rejected(self):

        resolved = guides_service.resolve_safe_path("01_QUICK_START_KO.md\x00.png")
        self.assertIsNone(resolved)

    def test_empty_path_rejected(self):

        self.assertIsNone(guides_service.resolve_safe_path(""))
        self.assertIsNone(guides_service.resolve_safe_path(None))


class ListGuidesTestCase(unittest.TestCase):

    def test_returns_exactly_five_guides_in_order(self):

        summaries = guides_service.list_guides()
        self.assertEqual(len(summaries), 5)
        self.assertEqual([s.order for s in summaries], [1, 2, 3, 4, 5])
        self.assertEqual(
            [s.id for s in summaries],
            [
                "quick-start", "admin-setup", "product-registration",
                "mobile-usage", "troubleshooting",
            ],
        )

    def test_list_guides_still_works_when_user_guidance_capability_deactivated(self):
        """Audit(2026-08-21, AG-0) — 정적 가이드 열람은 AI 판단이
        아니다. USER_GUIDANCE AI Capability가 비활성이어도 정상
        동작해야 한다(이전 라운드의 잘못된 게이트를 되돌린 회귀 방지
        테스트)."""

        from tests.ai_governance_test_helpers import deactivated_capability

        with deactivated_capability("USER_GUIDANCE"):
            summaries = guides_service.list_guides()

        self.assertEqual(len(summaries), 5)

    def test_documents_are_available_for_both_locales(self):
        """실제 KO/EN 문서 5편 × 2언어 = 10개가 전부 이번 세션에
        만들어졌다 — 전부 available=True여야 한다."""

        for summary in guides_service.list_guides():
            for locale in ("ko-KR", "en-US"):
                with self.subTest(guide=summary.id, locale=locale):
                    self.assertTrue(summary.document[locale].available)

    def test_no_video_is_available_yet(self):
        """이 시점 기준 실제 영상 파일이 하나도 없다 — pending으로
        정직하게 보고해야 한다(가짜로 재생 가능 표시 금지)."""

        for summary in guides_service.list_guides():
            for locale in ("ko-KR", "en-US"):
                with self.subTest(guide=summary.id, locale=locale):
                    self.assertFalse(summary.video[locale].available)
                    self.assertIsNone(summary.video[locale].path)

    def test_en_pdf_not_fabricated(self):
        """실제로 KO PDF만 렌더링되었다(EN PDF는 없음) — en-US 키
        자체가 없거나 available=False여야 하며, 존재하지 않는 자산을
        있는 것처럼 만들지 않는다."""

        for summary in guides_service.list_guides():
            en_pdf = summary.pdf.get("en-US")
            if en_pdf is not None:
                self.assertFalse(en_pdf.available)

    def test_get_guide_returns_none_for_unknown_id(self):

        self.assertIsNone(guides_service.get_guide("does-not-exist"))

    def test_get_guide_returns_matching_summary(self):

        summary = guides_service.get_guide("quick-start")
        self.assertIsNotNone(summary)
        self.assertEqual(summary.id, "quick-start")

    def test_registry_ids_are_unique(self):

        ids = [g["id"] for g in GUIDE_REGISTRY]
        self.assertEqual(len(ids), len(set(ids)))


class GuideFileEndpointTestCase(unittest.TestCase):
    """router.py의 엔드포인트 함수를 직접 호출한다(TestClient 없이도
    FastAPI route handler는 평범한 동기 함수다)."""

    def test_valid_real_file_returns_file_response(self):

        resp = guides_router_module.get_guide_file(
            file_path="01_QUICK_START_KO.md", _=None,
        )
        self.assertTrue(Path(resp.path).is_file())

    def test_path_traversal_returns_404(self):

        with self.assertRaises(HTTPException) as ctx:
            guides_router_module.get_guide_file(
                file_path="../../homez.db", _=None,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_nonexistent_video_returns_404(self):

        with self.assertRaises(HTTPException) as ctx:
            guides_router_module.get_guide_file(
                file_path="videos/01_quick_start_ko.mp4", _=None,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_unknown_guide_id_returns_404(self):

        with self.assertRaises(HTTPException) as ctx:
            guides_router_module.get_guide(guide_id="not-a-real-guide", _=None)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_known_guide_id_returns_summary(self):

        result = guides_router_module.get_guide(guide_id="mobile-usage", _=None)
        self.assertEqual(result.id, "mobile-usage")

    def test_list_guides_endpoint_returns_five(self):

        result = guides_router_module.list_guides(_=None)
        self.assertEqual(len(result.guides), 5)
        self.assertEqual(result.version, "6.5")


class GuidesRouterAuthContractTestCase(unittest.TestCase):
    """네 엔드포인트(Gate AI-F2에서 /ask 추가) 모두 admin_guard가
    아니라 get_current_user다 — Manager/Staff/Viewer를 포함한 모든
    로그인 사용자가 읽을 수 있어야 한다는 요구사항 그대로
    (notification_center와 동일한 패턴)."""

    def _dependency_names(self, route):

        return {d.call for d in route.dependant.dependencies}

    def test_all_four_routes_use_get_current_user_not_admin_guard(self):

        routes = guides_router_module.router.routes
        self.assertEqual(len(routes), 4)

        for route in routes:
            with self.subTest(path=route.path):
                deps = self._dependency_names(route)
                self.assertIn(get_current_user, deps)
                self.assertNotIn(admin_guard, deps)

    def test_no_route_accepts_a_request_body(self):
        """가이드 화면은 데이터를 변경하지 않는다 — 요청 바디를 받는
        엔드포인트가 하나도 없어야 한다."""

        routes = guides_router_module.router.routes
        for route in routes:
            with self.subTest(path=route.path):
                self.assertEqual(route.methods, {"GET"})


if __name__ == "__main__":
    unittest.main()
