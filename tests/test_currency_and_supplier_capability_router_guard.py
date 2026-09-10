"""
=========================================================
Homez OS

File : tests/test_currency_and_supplier_capability_router_guard.py

2026-09-10 Phase 9 — currency/supplier_capability 라우터가 전부
SuperAdminGuard 없이 노출되지 않는지 정적으로 검증한다
(app/domains/payment/router.py와 동일한 패턴). HTTP 서버를 띄우지
않는다.
=========================================================
"""

import unittest

from app.core.guard import SuperAdminGuard
from app.domains.currency import router as currency_router_module
from app.domains.supplier_capability import router as supplier_capability_router_module


class CurrencyRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_currency_routes_require_super_admin_guard(self):

        routes = currency_router_module.router.routes
        self.assertGreater(len(routes), 0)

        for route in routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                SuperAdminGuard, calls,
                f"{route.path} 이(가) SuperAdminGuard 없이 노출되어 "
                "있습니다.",
            )


class SupplierCapabilityRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_supplier_capability_routes_require_super_admin_guard(self):

        routes = supplier_capability_router_module.router.routes
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
