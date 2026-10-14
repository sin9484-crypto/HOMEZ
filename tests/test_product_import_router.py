"""
=========================================================
Homez OS

File : tests/test_product_import_router.py

URL 상품 가져오기(2026-10-14) 라우터 배선 검증. 이 저장소는 httpx가 없어 TestClient를 쓰지 않는다 — 실제
FastAPI Dependant 체인을 읽어 권한 가드 배선을 확인하고, 라우터 함수를 직접 호출해 변환·오류 처리를 본다.
=========================================================
"""

import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from app.core.exceptions import BadRequestException
from app.domains.product_import import router as pr
from app.domains.product_import import schema
from tests.product_import_fixtures import FakeTransport
from tests.product_import_fixtures import GENERIC_HTML
from tests.product_import_fixtures import GENERIC_URL


class WiringTests(unittest.TestCase):

    def _route(self, path, method="POST"):
        return next(r for r in pr.router.routes if r.path == path and method in r.methods)

    def test_three_routes_exist_under_product_import(self):
        for path in ("/product-import/search", "/product-import/margin-proposal", "/product-import/create-draft"):
            with self.subTest(path=path):
                self._route(path)

    def test_every_route_is_behind_the_wizard_create_permission_guard(self):
        for path in ("/product-import/search", "/product-import/margin-proposal", "/product-import/create-draft"):
            with self.subTest(path=path):
                dependants = [d.call.__qualname__ for d in self._route(path).dependant.dependencies]
                self.assertTrue(any("ListingWizardPermissionGuard" in name for name in dependants), dependants)

    def test_router_is_mounted_in_the_app(self):
        from app.main import app

        paths = {route.path for route in app.routes}
        self.assertIn("/product-import/search", paths)
        self.assertIn("/product-import/create-draft", paths)

    def test_there_is_no_coupang_import_in_this_module(self):
        # 이 기능은 쿠팡 전송 코드를 부르지 않는다(후보·초안까지만).
        source = Path(pr.__file__).read_text(encoding="utf-8")
        self.assertNotIn("coupang_live", source)
        self.assertNotIn("listing_wizard_live_service", source)


class BehaviourTests(unittest.TestCase):

    def test_margin_proposal_route_returns_the_service_result(self):
        result = pr.propose_margin(
            schema.MarginProposalRequest(cost_of_goods=Decimal("5720"), shipping_cost=Decimal("3000")),
            current_user=mock.Mock(),
        )
        self.assertGreaterEqual(result.margin_rate, Decimal("0.18"))

    def test_search_route_turns_import_errors_into_400(self):
        transport = FakeTransport()  # 등록 안 된 URL → 404
        with mock.patch("app.domains.media_asset.url_import._resolve_and_validate_host", lambda h: None):
            with self.assertRaises(BadRequestException) as ctx:
                pr.search_product_url(
                    schema.UrlSearchRequest(url="https://shop.example.test/none"),
                    current_user=mock.Mock(company_id=1, id=1), db=mock.Mock(),
                    transport=transport, connection_service=None,
                )
        self.assertIn("HTTP 404", str(ctx.exception.detail if hasattr(ctx.exception, "detail") else ctx.exception))

    def test_search_route_returns_a_suggestion_for_a_readable_page(self):
        transport = FakeTransport()
        transport.add_html(GENERIC_URL, GENERIC_HTML)
        with mock.patch("app.domains.media_asset.url_import._resolve_and_validate_host", lambda h: None):
            result = pr.search_product_url(
                schema.UrlSearchRequest(url=GENERIC_URL),
                current_user=mock.Mock(company_id=1, id=1), db=mock.Mock(),
                transport=transport, connection_service=None,
            )
        self.assertEqual(result.source_kind, "GENERIC")
        self.assertEqual(transport.calls, [GENERIC_URL])


if __name__ == "__main__":
    unittest.main()
