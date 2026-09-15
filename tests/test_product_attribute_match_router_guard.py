"""
=========================================================
Homez OS

File : tests/test_product_attribute_match_router_guard.py

2026-09-15 전면 감사 후속(Phase 9G) — product_attribute_match 라우터가
전부 SuperAdminGuard 없이 노출되지 않는지 정적으로 검증한다. HTTP
서버를 띄우지 않는다.
"""

import unittest

from app.core.guard import SuperAdminGuard
from app.domains.product_attribute_match import router as pam_router_module


class ProductAttributeMatchRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_routes_require_super_admin_guard(self):

        routes = pam_router_module.router.routes
        self.assertGreater(len(routes), 0)

        for route in routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                SuperAdminGuard, calls,
                f"{route.path} 이(가) SuperAdminGuard 없이 노출되어 "
                "있습니다.",
            )


if __name__ == "__main__":
    unittest.main()
