"""
=========================================================
Homez OS

File : tests/test_refund_router_guard.py

2026-09-10 Phase 8 — 환불 라우터의 모든 경로가 SuperAdminGuard 없이
노출되지 않는지, 승인 경로가 재인증 헤더를 요구하는지 정적으로
검증한다(app/domains/payment/router.py와 동일한 패턴). HTTP 서버를
띄우지 않는다.
"""

import inspect
import unittest

from app.core.guard import SuperAdminGuard
from app.domains.refund import router as refund_router_module


class RefundRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_refund_routes_require_super_admin_guard(self):

        routes = refund_router_module.router.routes
        self.assertGreater(len(routes), 0)

        for route in routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                SuperAdminGuard,
                calls,
                f"{route.path} 이(가) SuperAdminGuard 없이 노출되어 "
                "있습니다(인증 우회 가능성).",
            )

    def test_approve_route_requires_recent_auth_header(self):
        """"returns proceed only after user notice + approval"의
        API 표면 — 승인 경로만 재인증 헤더 파라미터를 요구한다."""

        for route in refund_router_module.router.routes:
            if route.name != "approve_refund":
                continue

            sig = inspect.signature(route.endpoint)
            self.assertIn("recent_auth_token", sig.parameters)
            return

        self.fail("approve_refund 라우트를 찾지 못했습니다.")

    def test_reject_and_execute_routes_exist(self):

        names = {route.name for route in refund_router_module.router.routes}
        self.assertIn("reject_refund", names)
        self.assertIn("mark_refund_executed", names)


if __name__ == "__main__":
    unittest.main()
