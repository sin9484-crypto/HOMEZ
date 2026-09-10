"""
=========================================================
Homez OS

File : tests/test_payment_router_guard.py

2026-09-10 Phase 7 — 결제 라우터의 모든 경로가 SuperAdminGuard 없이
노출되지 않는지 정적으로 검증한다(app/domains/backup/router.py의
동일 패턴을 따름). HTTP 서버를 띄우지 않는다 — route.dependant를
직접 조회한다.
"""

import unittest

from app.core.guard import SuperAdminGuard
from app.domains.payment import router as payment_router_module


class PaymentRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_payment_routes_require_super_admin_guard(self):

        routes = payment_router_module.router.routes
        self.assertGreater(len(routes), 0)

        for route in routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                SuperAdminGuard,
                calls,
                f"{route.path} 이(가) SuperAdminGuard 없이 노출되어 "
                "있습니다(인증 우회 가능성).",
            )

    def test_sensitive_mutations_require_recent_auth_header(self):
        """등록/비활성화/한도변경 3개 경로는 X-Recent-Auth-Token 헤더
        파라미터를 선언하고 있어야 한다 — 정적으로 시그니처를
        확인한다(실제 재인증 로직은 test_payment_domain.py가 서비스
        계층에서 검증하지 않으므로, 라우터가 그 검증을 실제로
        호출하는지는 코드 리뷰 대상 — 여기서는 최소한 헤더가 선언돼
        누락되지 않았는지만 정적으로 보증한다)."""

        import inspect

        recent_auth_required = {
            "register_payment_method",
            "deactivate_payment_method",
            "set_auto_payment_limit",
        }

        for route in payment_router_module.router.routes:
            if route.name not in recent_auth_required:
                continue

            sig = inspect.signature(route.endpoint)
            self.assertIn(
                "recent_auth_token", sig.parameters,
                f"{route.name}은 recent_auth_token 파라미터가 있어야 "
                "합니다.",
            )


if __name__ == "__main__":
    unittest.main()
