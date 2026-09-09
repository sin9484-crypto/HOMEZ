"""
=========================================================
Homez OS

File : tests/test_unauthenticated_router_removal.py

Gate X-1(2026-08-12) — Critical 보안 결함 재발 방지.

`app/domains/role_permission/router.py`(`/role-permissions`)와
`app/domains/user/router.py`(`/users`)는 어떤 라우트에도 인증/
Permission 검사(`Depends`)가 없어, 무인증 상태로 임의 역할의
Permission을 일괄 교체하거나 사용자 생성·조회·수정(role_id/
company_id/active 포함)·삭제·검색이 가능했다 — SUPER_ADMIN+
recent-auth+nonce로 삼중 방어된 `/admin/roles/...`,
`/admin/users/...`를 완전히 우회하는 경로였다.

이 파일은 그 두 라우터가 실제 `app.main.app`에 다시 마운트되지
않았는지를 회귀로 고정한다. 실제 homez.db는 사용하지 않는다
(`app.main`을 import만 하고 라우트 목록만 읽는다 — DB 연결·쓰기
없음).
=========================================================
"""

import unittest


class UnauthenticatedRouterRemovalTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        import app.main

        cls.routes = [
            r.path for r in app.main.app.routes if hasattr(r, "path")
        ]

    def test_unauthenticated_role_permissions_router_not_mounted(self):

        offending = [p for p in self.routes if p.startswith("/role-permissions")]
        self.assertEqual(
            offending, [],
            "무인증 /role-permissions 라우터가 다시 마운트됐다 — "
            "임의 역할의 Permission을 인증 없이 일괄 교체할 수 있는 "
            "Critical 결함이 재발한다. 실제 관리 경로는 "
            "role_permission_admin_router(/admin/roles/...)뿐이어야 한다.",
        )

    def test_unauthenticated_user_router_not_mounted(self):

        offending = [
            p for p in self.routes
            if p == "/users" or p.startswith("/users/")
        ]
        self.assertEqual(
            offending, [],
            "무인증 /users 라우터가 다시 마운트됐다 — 인증·회사 격리 "
            "없이 사용자를 생성·조회·수정(role_id/company_id/active "
            "포함)·삭제·검색할 수 있는 Critical 결함이 재발한다. 실제 "
            "관리 경로는 app/core/account_admin.py(/admin/users/...)뿐이어야 한다.",
        )

    def test_authenticated_replacement_routes_still_mounted(self):
        """대체 경로 자체가 실수로 함께 삭제되지 않았는지 확인한다."""

        self.assertTrue(
            any("/admin/roles" in p for p in self.routes),
            "/admin/roles 계열 라우트가 사라졌다 — 인증된 역할 관리 "
            "경로 자체가 없어졌다.",
        )
        self.assertTrue(
            any("/admin/users" in p for p in self.routes),
            "/admin/users 계열 라우트가 사라졌다 — 인증된 사용자 관리 "
            "경로 자체가 없어졌다.",
        )


if __name__ == "__main__":
    unittest.main()
