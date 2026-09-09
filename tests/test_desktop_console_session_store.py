"""
=========================================================
Homez OS

File : tests/test_desktop_console_session_store.py

2026-08-14 Gate F-2 — Desktop 콘솔 세션(access_token) Credential Manager
저장소 검증. 실제 Windows Credential Manager는 사용하지 않는다(기존
관례와 동일 — InMemoryCredentialStore fixture만 사용).

시나리오:
- 저장 후 load()로 동일 user_id/token을 그대로 복원
- 아무것도 저장하지 않았으면 load()가 None
- 만료 시각이 지난 세션은 load() 시점에 자동 삭제되고 None 반환
- 만료 시각이 없는(None) 세션은 만료 검사를 건너뛰고 정상 복원
- 다른 user_id로 다시 save()하면 이전 사용자의 Credential이 삭제됨
  (사용자 전환 격리 — 섞이지 않음)
- clear()가 활성 세션과 활성 사용자 포인터를 모두 지움(로그아웃)
- clear()를 아무것도 없는 상태에서 호출해도 예외 없이 안전
- 토큰 값이 어떤 예외에도 노출되지 않음(간접 확인 — 저장소 내부
  구현은 CredentialStoreError만 던지고 원문을 포함하지 않는다는
  기존 계약을 그대로 재사용하므로, 여기서는 저장된 raw payload가
  InMemoryCredentialStore 내부에만 있고 클래스 자신의 attribute/
  repr에는 없음을 확인)

2026-08-30 V7 후속 안정화 Phase 2 — Refresh Token도 함께 저장·복원
(load()가 이제 항상 "refresh_token" 키를 포함한다 — 저장한 적 없으면
None). 추가 시나리오:
- Refresh Token도 access_token과 함께 저장 후 그대로 복원
- Access Token만 만료되고 Refresh Token은 아직 유효하면, access_token
  은 여전히 반환하되 refresh_token은 그대로 살아있다(둘 다 지우지
  않는다 — 그게 auto-refresh가 쓸 값이다)
- Refresh Token까지 완전히 만료되면 세션 전체가 정리된다
=========================================================
"""

import time
import unittest

from app.core.desktop_console_session_store import DesktopConsoleSessionStore
from app.core.windows_credential_store import InMemoryCredentialStore


class DesktopConsoleSessionStoreTestCase(unittest.TestCase):

    def setUp(self):

        self.backing = InMemoryCredentialStore()
        self.store = DesktopConsoleSessionStore(self.backing)

    def test_load_without_any_save_returns_none(self):

        self.assertIsNone(self.store.load())

    def test_save_then_load_round_trips(self):

        self.store.save(42, "token-abc", int(time.time()) + 3600)

        loaded = self.store.load()
        self.assertEqual(
            loaded,
            {"user_id": 42, "access_token": "token-abc", "refresh_token": None},
        )

    def test_save_without_expiry_still_loads(self):

        self.store.save(7, "token-no-exp", None)

        loaded = self.store.load()
        self.assertEqual(
            loaded,
            {"user_id": 7, "access_token": "token-no-exp", "refresh_token": None},
        )

    def test_expired_session_is_purged_on_load(self):

        self.store.save(1, "expired-token", int(time.time()) - 10)

        self.assertIsNone(self.store.load())
        # 정리까지 됐는지 — 활성 사용자 포인터도 함께 지워졌어야 한다.
        self.assertFalse(self.backing.exists("HOMEZ:console_active_user"))
        self.assertFalse(self.backing.exists("HOMEZ:console_session:1"))

    def test_switching_user_deletes_previous_users_credential(self):

        self.store.save(1, "user1-token", None)
        self.assertTrue(self.backing.exists("HOMEZ:console_session:1"))

        self.store.save(2, "user2-token", None)

        self.assertFalse(self.backing.exists("HOMEZ:console_session:1"))
        self.assertTrue(self.backing.exists("HOMEZ:console_session:2"))

        loaded = self.store.load()
        self.assertEqual(loaded["user_id"], 2)
        self.assertEqual(loaded["access_token"], "user2-token")

    def test_same_user_relogin_does_not_delete_own_credential_prematurely(self):

        self.store.save(5, "first-token", None)
        self.store.save(5, "second-token", None)

        loaded = self.store.load()
        self.assertEqual(
            loaded,
            {"user_id": 5, "access_token": "second-token", "refresh_token": None},
        )

    def test_clear_removes_active_session_and_pointer(self):

        self.store.save(9, "token-9", None)
        self.store.clear()

        self.assertIsNone(self.store.load())
        self.assertFalse(self.backing.exists("HOMEZ:console_session:9"))
        self.assertFalse(self.backing.exists("HOMEZ:console_active_user"))

    def test_clear_on_empty_store_is_a_safe_noop(self):

        try:
            self.store.clear()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"clear() on empty store raised unexpectedly: {exc}")

    def test_refresh_token_round_trips_alongside_access_token(self):

        self.store.save(
            11, "access-11", int(time.time()) + 3600,
            refresh_token="refresh-11",
            refresh_expires_at_epoch=int(time.time()) + 86400,
        )

        loaded = self.store.load()
        self.assertEqual(loaded["access_token"], "access-11")
        self.assertEqual(loaded["refresh_token"], "refresh-11")

    def test_expired_access_token_alone_does_not_drop_still_valid_refresh_token(self):

        self.store.save(
            12, "access-12-expired", int(time.time()) - 10,
            refresh_token="refresh-12-still-valid",
            refresh_expires_at_epoch=int(time.time()) + 86400,
        )

        loaded = self.store.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["refresh_token"], "refresh-12-still-valid")

    def test_expired_refresh_token_purges_entire_session(self):
        # Refresh Token까지 완전히 만료됐고 Access Token도 만료됐으면
        # (둘 다 살아있지 않으면) 세션 전체를 정리한다.
        self.store.save(
            13, "access-13-expired", int(time.time()) - 10,
            refresh_token="refresh-13-expired",
            refresh_expires_at_epoch=int(time.time()) - 10,
        )

        self.assertIsNone(self.store.load())

    def test_token_value_never_appears_in_store_repr_or_attributes(self):

        self.store.save(3, "super-secret-token-value", None)

        # DesktopConsoleSessionStore 자신은 어떤 인스턴스 속성에도
        # 토큰 원문을 보관하지 않는다(전부 주입된 CredentialStore
        # 안에만 있다) — repr()에 노출되지 않음을 확인한다.
        self.assertNotIn("super-secret-token-value", repr(self.store))
        self.assertNotIn("super-secret-token-value", str(vars(self.store)))


if __name__ == "__main__":
    unittest.main()
