"""
=========================================================
Homez OS

File : tests/test_ai_learning_router_guard.py

2026-09-10 Phase 12 — ai_learning 라우터가 전부 SuperAdminGuard 없이
노출되지 않는지, 승인 경로가 재인증 헤더를 요구하는지 정적으로
검증한다. HTTP 서버를 띄우지 않는다.
"""

import inspect
import unittest

from app.core.guard import SuperAdminGuard
from app.domains.ai_learning import router as ai_learning_router_module


class AiLearningRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_routes_require_super_admin_guard(self):

        routes = ai_learning_router_module.router.routes
        self.assertGreater(len(routes), 0)

        for route in routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                SuperAdminGuard, calls,
                f"{route.path} 이(가) SuperAdminGuard 없이 노출되어 "
                "있습니다.",
            )

    def test_approve_route_requires_recent_auth_header(self):

        for route in ai_learning_router_module.router.routes:
            if route.name != "approve_model_candidate":
                continue

            sig = inspect.signature(route.endpoint)
            self.assertIn("recent_auth_token", sig.parameters)
            return

        self.fail("approve_model_candidate 라우트를 찾지 못했습니다.")


if __name__ == "__main__":
    unittest.main()
