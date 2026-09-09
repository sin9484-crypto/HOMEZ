"""
=========================================================
Homez OS

File : tests/test_product_candidate_role_contract.py

2026-08-19 CTO 정책 A 후속 지시 — "MANAGER가 403이므로 다른 역할도
논리적으로 동일하다고 갈음하지 않는다." admin_guard(AdminGuard)의
실제 역할별 통과/차단을 UserRole enum 전체(SUPER_ADMIN/ADMIN/MANAGER/
STAFF/SELLER/CUSTOMER/GUEST)에 대해 개별 고정한다.

product_candidate 라우터의 모든 엔드포인트(list/get/evidence/
decisions/private/analyze/recommend/approve/hold/reject)는 단일하게
admin_guard만 사용한다(app/domains/product_candidate/router.py 전수
확인, 세분화된 Permission 키 없음) — 따라서 AdminGuard() 자체의
역할별 계약을 고정하면 이 도메인 전체의 역할 계약을 고정하는 것과
동일하다. 실제 운영 계정을 만들지 않고, User 모델과 동일한 속성만
가진 격리 fixture(SimpleNamespace)를 사용한다 — 어떤 실제 DB도
건드리지 않는다.
"""

import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.core.authorization import UserRole
from app.core.guard import AdminGuard


def _fake_user(role: str | None):

    return SimpleNamespace(id=1, role=role, company_id=1)


class AdminGuardRoleContractTestCase(unittest.TestCase):
    """
    UserRole enum(app/core/authorization.py)의 실제 7개 값 전부를
    개별적으로 고정한다 — "VIEWER"는 이 enum에 존재하지 않는다는
    사실도 별도 테스트로 명시한다(직전 보고서가 잘못 가정한 역할
    이름을 실제 계약으로 정정).
    """

    def test_super_admin_passes(self):

        user = _fake_user(UserRole.SUPER_ADMIN.value)
        result = AdminGuard(current_user=user)
        self.assertIs(result, user)

    def test_admin_passes(self):

        user = _fake_user(UserRole.ADMIN.value)
        result = AdminGuard(current_user=user)
        self.assertIs(result, user)

    def test_manager_blocked(self):

        user = _fake_user(UserRole.MANAGER.value)
        with self.assertRaises(HTTPException) as ctx:
            AdminGuard(current_user=user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_staff_blocked(self):

        user = _fake_user(UserRole.STAFF.value)
        with self.assertRaises(HTTPException) as ctx:
            AdminGuard(current_user=user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_seller_blocked(self):

        user = _fake_user(UserRole.SELLER.value)
        with self.assertRaises(HTTPException) as ctx:
            AdminGuard(current_user=user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_customer_blocked(self):

        user = _fake_user(UserRole.CUSTOMER.value)
        with self.assertRaises(HTTPException) as ctx:
            AdminGuard(current_user=user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_guest_blocked(self):

        user = _fake_user(UserRole.GUEST.value)
        with self.assertRaises(HTTPException) as ctx:
            AdminGuard(current_user=user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_no_role_assigned_blocked(self):
        """role_id가 NULL이라 User.role이 None인 경우(계정은 있으나
        역할 미배정) — has_role()이 None을 명시적으로 거부한다."""

        user = _fake_user(None)
        with self.assertRaises(HTTPException) as ctx:
            AdminGuard(current_user=user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_anonymous_none_user_blocked(self):
        """user 자체가 None(인증 정보 없음)인 경우도 has_role()이
        방어한다 — 실제 401은 get_current_user(업스트림 의존성,
        이 도메인 범위 밖, 기존 auth 테스트 스위트가 이미 검증)가
        담당하지만, admin_guard 자신도 None에 대해 fail-closed임을
        고정한다."""

        with self.assertRaises(HTTPException) as ctx:
            AdminGuard(current_user=None)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_viewer_role_does_not_exist_in_enum(self):
        """
        직전 CTO 지시(정책 결정 이전 라운드)는 "VIEWER" 역할을
        가정했으나, 실제 UserRole enum에는 그런 값이 없다 — 이
        사실 자체를 회귀로 고정해, 향후 누군가 "VIEWER" 문자열을
        실제 값으로 오인해 코드에 넣지 않도록 한다.
        """

        role_values = {r.value for r in UserRole}
        self.assertNotIn("viewer", role_values)
        self.assertEqual(
            role_values,
            {
                "super_admin", "admin", "manager",
                "staff", "seller", "customer", "guest",
            },
        )

    def test_case_insensitive_role_comparison_still_enforced(self):
        """has_role()의 대소문자 무관 비교(2026-07-30 기존 계약)가
        MANAGER를 대문자로 저장해도 여전히 차단하는지 확인한다."""

        user = _fake_user("MANAGER")
        with self.assertRaises(HTTPException) as ctx:
            AdminGuard(current_user=user)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
