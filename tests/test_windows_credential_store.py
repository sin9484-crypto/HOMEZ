"""
=========================================================
Homez OS

File : tests/test_windows_credential_store.py

Windows Credential Manager 래퍼 검증. 실제 이 개발 환경이 Windows이므로
WindowsCredentialStore()의 실제 save/read/delete/exists를 직접
검증한다(각 테스트는 자기 자신이 만든 target만 정리하고 끝낸다 — 실제
OS 저장소에 잔여물을 남기지 않는다). InMemoryCredentialStore/
AlwaysFailingCredentialStore(테스트 fixture)도 함께 검증한다.
=========================================================
"""

import sys
import unittest
import uuid

from app.core.windows_credential_store import (
    AlwaysFailingCredentialStore,
    CredentialNotFoundError,
    CredentialStoreError,
    CredentialStoreUnavailableError,
    InMemoryCredentialStore,
    WindowsCredentialStore,
)


def _fresh_target() -> str:

    return f"HOMEZ:test:{uuid.uuid4()}"


@unittest.skipUnless(sys.platform == "win32", "Windows 전용 Credential Manager 테스트")
class WindowsCredentialStoreTestCase(unittest.TestCase):

    def setUp(self):

        self.store = WindowsCredentialStore()
        self._targets_to_cleanup = []

    def tearDown(self):

        for target in self._targets_to_cleanup:
            try:
                self.store.delete(target)
            except CredentialStoreError:
                pass

    def _track(self, target: str) -> str:

        self._targets_to_cleanup.append(target)
        return target

    def test_save_then_read_round_trips_payload(self):

        target = self._track(_fresh_target())
        payload = {"vendor_id": "A00123456", "access_key": "ak", "secret_key": "sk"}

        self.store.save(target, payload)
        result = self.store.read(target)

        self.assertEqual(result, payload)

    def test_exists_reflects_actual_state(self):

        target = self._track(_fresh_target())

        self.assertFalse(self.store.exists(target))

        self.store.save(target, {"a": "b"})
        self.assertTrue(self.store.exists(target))

        self.store.delete(target)
        self.assertFalse(self.store.exists(target))

    def test_read_missing_target_raises_not_found(self):

        target = _fresh_target()

        with self.assertRaises(CredentialNotFoundError):
            self.store.read(target)

    def test_delete_missing_target_raises_error(self):

        target = _fresh_target()

        with self.assertRaises(CredentialStoreError):
            self.store.delete(target)

    def test_rotate_semantics_old_and_new_targets_independent(self):
        """
        저장소 자체는 "교체"라는 개념을 모른다 — 호출자(서비스)가
        새 target으로 save()하고 이전 target을 delete()하는 2단계로
        구성한다는 걸 이 테스트가 직접 재현해 확인한다.
        """

        old_target = self._track(_fresh_target())
        new_target = self._track(_fresh_target())

        self.store.save(old_target, {"secret_key": "old"})
        self.store.save(new_target, {"secret_key": "new"})

        self.assertEqual(self.store.read(old_target)["secret_key"], "old")
        self.assertEqual(self.store.read(new_target)["secret_key"], "new")

        self.store.delete(old_target)

        self.assertFalse(self.store.exists(old_target))
        self.assertTrue(self.store.exists(new_target))

    def test_error_messages_never_contain_secret_values(self):
        """
        Secret 조회 결과를 exception 메시지에 포함하지 않는다 — 존재
        하지 않는 target을 읽으려 할 때 예외 문자열에 target 식별자만
        있고 Secret 값 흔적이 없어야 한다(애초에 조회에 실패했으니
        Secret 값 자체가 없다는 점을 통해서도 구조적으로 보장된다).
        """

        target = _fresh_target()

        try:
            self.store.read(target)
            self.fail("should have raised")
        except CredentialNotFoundError as exc:
            message = str(exc)
            self.assertNotIn("secret", message.lower().replace("target", ""))


class InMemoryCredentialStoreTestCase(unittest.TestCase):
    """테스트 전용 fixture — 운영 코드가 자동으로 선택하지 않는다."""

    def setUp(self):

        self.store = InMemoryCredentialStore()

    def test_save_read_delete_exists(self):

        target = "t1"
        self.assertFalse(self.store.exists(target))

        self.store.save(target, {"k": "v"})
        self.assertTrue(self.store.exists(target))
        self.assertEqual(self.store.read(target), {"k": "v"})

        self.store.delete(target)
        self.assertFalse(self.store.exists(target))

    def test_read_missing_raises(self):

        with self.assertRaises(CredentialNotFoundError):
            self.store.read("missing")

    def test_delete_missing_raises(self):

        with self.assertRaises(CredentialStoreError):
            self.store.delete("missing")

    def test_never_persists_to_real_os_store(self):
        """
        in-memory 저장소는 프로세스 메모리 dict일 뿐이다 — 별개의
        인스턴스는 서로의 데이터를 볼 수 없다(실제 OS 전역 상태를
        전혀 건드리지 않는다는 것의 방증).
        """

        other = InMemoryCredentialStore()
        self.store.save("shared-name", {"a": 1})

        self.assertFalse(other.exists("shared-name"))


class AlwaysFailingCredentialStoreTestCase(unittest.TestCase):
    """"Credential Manager 실패 시 fail-closed" 시나리오 전용 fixture."""

    def setUp(self):

        self.store = AlwaysFailingCredentialStore()

    def test_all_operations_fail_closed(self):

        with self.assertRaises(CredentialStoreError):
            self.store.save("x", {})
        with self.assertRaises(CredentialStoreError):
            self.store.read("x")
        with self.assertRaises(CredentialStoreError):
            self.store.delete("x")
        with self.assertRaises(CredentialStoreError):
            self.store.exists("x")


class CredentialStoreUnavailablePlatformGuardTestCase(unittest.TestCase):
    """
    WindowsCredentialStore는 win32가 아니면 즉시 fail-closed로
    CredentialStoreUnavailableError를 던진다 — in-memory로 자동
    fallback하지 않는다. 실제 플랫폼을 임시로 흉내내 검증한다.
    """

    def test_raises_on_non_windows_platform(self):

        import app.core.windows_credential_store as mod

        original_platform = sys.platform
        sys.platform = "linux"
        try:
            with self.assertRaises(CredentialStoreUnavailableError):
                mod.WindowsCredentialStore()
        finally:
            sys.platform = original_platform


class ForbidRealCredentialStoreGuardTestCase(unittest.TestCase):
    """2026-09-08 후속 — 격리 검증 스크립트가 자격증명 저장소를
    injection 없이 실수로 실제 WindowsCredentialStore()를 만들면
    (사고 재현: 격리 DB만 갈아끼우고 credential_store는 그대로 둔
    채 실행 — 실제로 homez_channel_connection_1에 합성 값이 저장된
    사고), 조용히 실제 저장소로 넘어가지 않고 그 자리에서 즉시
    실패해야 한다. 이 테스트는 그 가드 자체를 검증한다 — 실제
    Credential Manager에는 어떤 값도 쓰지 않는다(생성자에서 바로
    예외가 나므로 save/read를 시도할 기회조차 없다)."""

    def setUp(self):

        import os

        import app.core.windows_credential_store as mod

        self._mod = mod
        self._env_var = mod.FORBID_REAL_CREDENTIAL_STORE_ENV_VAR
        self._original_value = os.environ.get(self._env_var)
        self.addCleanup(self._restore_env)

    def _restore_env(self):

        import os

        if self._original_value is None:
            os.environ.pop(self._env_var, None)
        else:
            os.environ[self._env_var] = self._original_value

    def test_raises_immediately_when_forbidden(self):

        import os

        os.environ[self._env_var] = "1"

        with self.assertRaises(CredentialStoreUnavailableError):
            self._mod.WindowsCredentialStore()

    def test_does_not_raise_when_not_set(self):
        """가드가 없을 때(기본값)는 기존처럼 정상 생성된다 — 이
        가드가 평소 운영 코드 경로까지 막아버리지 않는지 확인."""

        import os

        os.environ.pop(self._env_var, None)

        try:
            self._mod.WindowsCredentialStore()
        except CredentialStoreUnavailableError:
            self.fail("HOMEZ_FORBID_REAL_CREDENTIAL_STORE 미설정 시에도 차단됨")


if __name__ == "__main__":
    unittest.main()
