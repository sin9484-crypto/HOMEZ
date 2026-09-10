"""
=========================================================
Homez OS

File : tests/test_price_stock_safety_router_guard.py

2026-09-10 Phase 10 — price_stock_safety 라우터가 전부 SuperAdminGuard
없이 노출되지 않는지 정적으로 검증한다. HTTP 서버를 띄우지 않는다.
"""

import unittest

from app.core.guard import SuperAdminGuard
from app.domains.price_stock_safety import router as price_stock_safety_router_module


class PriceStockSafetyRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_routes_require_super_admin_guard(self):

        routes = price_stock_safety_router_module.router.routes
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
