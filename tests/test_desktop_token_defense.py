"""
=========================================================
Homez OS

File : tests/test_desktop_token_defense.py

HOMEZ Desktop Shell — Desktop session token 방어 계층 검증
(2026-07-30). httpx가 없어 실제 HTTP/쿠키 왕복은 검증하지 못하고,
`app/core/desktop_auth.py`의 의존성/엔드포인트 함수를 직접 호출해
로직을 검증한다(아래 각 테스트 docstring에 실행 방식 명시).
=========================================================
"""

import inspect
import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException

from app.core.desktop_auth import DesktopBootstrapRequest
from app.core.desktop_auth import bootstrap_desktop_session
from app.core.desktop_auth import require_desktop_token
from app.core.desktop_token import clear_desktop_token
from app.core.desktop_token import is_desktop_mode
from app.core.desktop_token import set_desktop_token
from app.domains.decision import router as decision_router_module
from app.web import router as web_router_module


class DesktopTokenDefenseTestCase(unittest.TestCase):

    def tearDown(self):

        clear_desktop_token()

    # --------------------------------------------------
    # 일반 브라우저(비 Desktop) 모드 — 추가 방어가 조용히 비활성화된다
    # --------------------------------------------------

    def test_require_desktop_token_is_noop_when_not_desktop_mode(self):

        self.assertFalse(is_desktop_mode())

        # 예외를 던지지 않아야 한다(기존 브라우저 launcher/테스트 호출을
        # 깨지 않기 위한 "추가 방어"라는 설계 그대로).
        require_desktop_token(homez_desktop_token=None)

    # --------------------------------------------------
    # 12. Desktop token 누락
    # --------------------------------------------------

    def test_missing_cookie_is_rejected_in_desktop_mode(self):

        set_desktop_token("real-token-abc")

        with self.assertRaises(HTTPException) as ctx:
            require_desktop_token(homez_desktop_token=None)

        self.assertEqual(ctx.exception.status_code, 403)

    # --------------------------------------------------
    # 13. Desktop token 불일치
    # --------------------------------------------------

    def test_mismatched_cookie_is_rejected_in_desktop_mode(self):

        set_desktop_token("real-token-abc")

        with self.assertRaises(HTTPException) as ctx:
            require_desktop_token(homez_desktop_token="wrong-token")

        self.assertEqual(ctx.exception.status_code, 403)

    def test_matching_cookie_passes(self):

        set_desktop_token("real-token-abc")

        # 예외가 없으면 통과.
        require_desktop_token(homez_desktop_token="real-token-abc")

    # --------------------------------------------------
    # Bootstrap 엔드포인트
    # --------------------------------------------------

    def test_bootstrap_rejects_when_not_desktop_mode(self):

        response = MagicMock()

        with self.assertRaises(HTTPException) as ctx:
            bootstrap_desktop_session(
                data=DesktopBootstrapRequest(token="anything"),
                response=response,
            )

        self.assertEqual(ctx.exception.status_code, 409)

    def test_bootstrap_rejects_wrong_token(self):

        set_desktop_token("real-token-abc")
        response = MagicMock()

        with self.assertRaises(HTTPException) as ctx:
            bootstrap_desktop_session(
                data=DesktopBootstrapRequest(token="wrong"),
                response=response,
            )

        self.assertEqual(ctx.exception.status_code, 403)
        response.set_cookie.assert_not_called()

    def test_bootstrap_sets_httponly_samesite_strict_cookie_on_success(self):

        set_desktop_token("real-token-abc")
        response = MagicMock()

        result = bootstrap_desktop_session(
            data=DesktopBootstrapRequest(token="real-token-abc"),
            response=response,
        )

        self.assertEqual(result, {"ok": True})
        response.set_cookie.assert_called_once()
        _, kwargs = response.set_cookie.call_args
        self.assertTrue(kwargs["httponly"])
        self.assertEqual(kwargs["samesite"], "strict")
        self.assertEqual(kwargs["value"], "real-token-abc")

    # --------------------------------------------------
    # 14. Desktop token만으로는 사용자 인증(로그인)을 대체할 수 없다
    # --------------------------------------------------

    def test_desktop_token_dependency_never_returns_a_user(self):
        """
        require_desktop_token은 통과 시 None을 반환할 뿐 User를 반환하지
        않는다 — 이 값만으로는 current_user를 구성할 수 없으므로 어떤
        엔드포인트도 이것만으로 "로그인된 사용자"를 흉내낼 수 없다.
        """

        set_desktop_token("real-token-abc")
        result = require_desktop_token(homez_desktop_token="real-token-abc")

        self.assertIsNone(result)

    # --------------------------------------------------
    # 위험 작업 엔드포인트가 admin_guard와 require_desktop_token을
    # "함께" 요구하는지 — 라우터 함수 시그니처를 직접 검사한다.
    # --------------------------------------------------

    def _dependency_names(self, func) -> set[str]:

        names = set()
        for param in inspect.signature(func).parameters.values():
            default = param.default
            dependency_callable = getattr(default, "dependency", None)
            if dependency_callable is not None:
                names.add(dependency_callable.__name__)
        return names

    def test_safety_mutation_endpoints_require_both_guards(self):

        for func in (
            web_router_module.activate_emergency_stop,
            web_router_module.deactivate_emergency_stop,
            web_router_module.set_automation_mode,
        ):
            deps = self._dependency_names(func)
            self.assertIn("AdminGuard", deps, func.__name__)
            self.assertIn("require_desktop_token", deps, func.__name__)

    def test_decision_mutation_endpoints_require_both_guards(self):

        for func in (
            decision_router_module.evaluate_candidate,
            decision_router_module.approve_evaluation,
            decision_router_module.hold_evaluation,
            decision_router_module.reject_evaluation,
            decision_router_module.override_evaluation,
        ):
            deps = self._dependency_names(func)
            self.assertIn("AdminGuard", deps, func.__name__)
            self.assertIn("require_desktop_token", deps, func.__name__)


if __name__ == "__main__":
    unittest.main()
