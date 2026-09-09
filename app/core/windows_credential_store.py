"""
=========================================================
Homez OS

File : app/core/windows_credential_store.py

Windows Credential Manager 기반 Secret 저장소.

원칙:
  - 표준 라이브러리 ctypes로 Win32 Credential API(advapi32.dll)만
    호출한다 — 신규 패키지(pywin32 등)를 설치하지 않는다.
  - 운영 코드(WindowsCredentialStore)는 Windows가 아니면 즉시
    CredentialStoreUnavailableError를 던진다 — in-memory로 자동
    fallback하지 않는다(fail-closed).
  - InMemoryCredentialStore는 테스트 전용 fixture다 — 운영 서비스
    코드가 스스로 이걸 선택하는 경로는 없다(항상 명시적 생성자 주입).
  - Secret 조회 결과·payload는 어떤 예외 메시지·__repr__·로그에도
    노출하지 않는다.
=========================================================
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from abc import ABC
from abc import abstractmethod


# 2026-09-08 후속 — 격리 검증 중 실제 Windows Credential Manager 오염
# 사고(합성 테스트 값이 homez_channel_connection_1에 실제로 저장된
# 사고) 재발 방지. 격리/E2E 실행 스크립트가 시작 시 이 환경변수를
# "1"로 설정하면, 그 프로세스 안에서 credential_store 주입이 누락돼
# 실수로 WindowsCredentialStore()가 새로 만들어지는 순간 조용히
# 실제 저장소로 전환되는 대신 즉시 예외로 실패한다(fail-closed를
# 프로세스 레벨로 한 단계 더 강제).
FORBID_REAL_CREDENTIAL_STORE_ENV_VAR = "HOMEZ_FORBID_REAL_CREDENTIAL_STORE"


class CredentialStoreError(Exception):
    """
    Credential 저장소 작업 실패 — 원인 문자열에 Secret 값을 포함하지
    않는다(오직 target_name과 Win32 오류 코드만 포함).
    """


class CredentialStoreUnavailableError(CredentialStoreError):
    """Windows Credential Manager를 사용할 수 없는 환경(fail-closed)."""


class CredentialNotFoundError(CredentialStoreError):
    pass


class CredentialStore(ABC):
    """
    저장·조회·교체·삭제를 분리한 추상 인터페이스. "교체"는 이 계층의
    책임이 아니다 — 호출자(서비스)가 새 target_name으로 save() 한
    뒤 이전 target_name을 delete()하는 2단계로 구성한다.
    """

    @abstractmethod
    def save(self, target_name: str, payload: dict) -> None:
        ...

    @abstractmethod
    def read(self, target_name: str) -> dict:
        ...

    @abstractmethod
    def delete(self, target_name: str) -> None:
        ...

    @abstractmethod
    def exists(self, target_name: str) -> bool:
        ...


# --------------------------------------------------
# Win32 CREDENTIALW 구조체 (wincred.h)
# --------------------------------------------------

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
# CRED_TYPE_GENERIC의 CredentialBlob 최대 크기(공식 문서 기준 5*512).
CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512


def _build_credentialw_type():
    """
    ctypes.wintypes는 Windows에서만 import 가능하므로, 이 함수 내부에서
    지연 import한다 — 이 모듈 자체는 어떤 플랫폼에서도 import는
    가능해야 한다(WindowsCredentialStore 인스턴스 생성 시점에만
    플랫폼을 검사한다).
    """

    from ctypes import wintypes

    class FILETIME(ctypes.Structure):

        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class CREDENTIALW(ctypes.Structure):

        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    return CREDENTIALW


class WindowsCredentialStore(CredentialStore):
    """
    실제 Windows Credential Manager(advapi32.dll CredWriteW/CredReadW/
    CredDeleteW)를 사용하는 운영용 구현.
    """

    def __init__(self) -> None:

        if os.environ.get(FORBID_REAL_CREDENTIAL_STORE_ENV_VAR) == "1":
            raise CredentialStoreUnavailableError(
                f"실제 Windows Credential Manager 접근이 이 실행 환경에서 "
                f"명시적으로 금지되어 있습니다({FORBID_REAL_CREDENTIAL_STORE_ENV_VAR}=1) "
                "— 격리/E2E 실행에서 credential_store 주입이 누락된 채로 "
                "WindowsCredentialStore()가 만들어지려 한 것으로 보입니다. "
                "실제 저장소로 조용히 전환하지 않습니다(fail-closed).",
            )

        if sys.platform != "win32":
            raise CredentialStoreUnavailableError(
                "Windows Credential Manager는 win32 플랫폼에서만 "
                "사용할 수 있습니다 — in-memory로 대체하지 않습니다"
                "(fail-closed).",
            )

        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._CREDENTIALW = _build_credentialw_type()

    def _last_error_text(self) -> str:
        """Secret을 포함하지 않는 Win32 오류 코드 문자열만 반환한다."""

        return f"WinError({ctypes.get_last_error()})"

    def save(self, target_name: str, payload: dict) -> None:

        blob_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        if len(blob_bytes) > CRED_MAX_CREDENTIAL_BLOB_SIZE:
            raise CredentialStoreError(
                f"target={target_name}: payload가 Credential Manager "
                "최대 크기를 초과합니다.",
            )

        blob_buffer = ctypes.create_string_buffer(blob_bytes, len(blob_bytes))

        credential = self._CREDENTIALW()
        credential.Flags = 0
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target_name
        credential.Comment = "HOMEZ store_connection credential"
        credential.CredentialBlobSize = len(blob_bytes)
        credential.CredentialBlob = ctypes.cast(
            blob_buffer, ctypes.POINTER(ctypes.c_char),
        )
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = None

        ok = self._advapi32.CredWriteW(ctypes.byref(credential), 0)

        if not ok:
            raise CredentialStoreError(
                f"target={target_name}: Credential 저장 실패 "
                f"({self._last_error_text()}).",
            )

    def read(self, target_name: str) -> dict:

        cred_ptr = ctypes.POINTER(self._CREDENTIALW)()

        ok = self._advapi32.CredReadW(
            target_name, CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr),
        )

        if not ok:
            raise CredentialNotFoundError(
                f"target={target_name}: Credential을 찾을 수 없습니다"
                f"({self._last_error_text()}).",
            )

        try:
            size = cred_ptr.contents.CredentialBlobSize
            blob_ptr = cred_ptr.contents.CredentialBlob
            raw = ctypes.string_at(blob_ptr, size)

            return json.loads(raw.decode("utf-8"))

        finally:
            self._advapi32.CredFree(cred_ptr)

    def delete(self, target_name: str) -> None:

        ok = self._advapi32.CredDeleteW(target_name, CRED_TYPE_GENERIC, 0)

        if not ok:
            raise CredentialStoreError(
                f"target={target_name}: Credential 삭제 실패 "
                f"({self._last_error_text()}).",
            )

    def exists(self, target_name: str) -> bool:

        try:
            self.read(target_name)
            return True

        except CredentialNotFoundError:
            return False


class InMemoryCredentialStore(CredentialStore):
    """
    테스트 전용 fixture — 운영 코드 경로에서는 절대 자동 선택되지
    않는다. 프로세스 메모리 안에서만 유지되고 실제 OS 저장소를
    건드리지 않는다.
    """

    def __init__(self) -> None:

        self._data: dict[str, dict] = {}

    def save(self, target_name: str, payload: dict) -> None:

        self._data[target_name] = dict(payload)

    def read(self, target_name: str) -> dict:

        if target_name not in self._data:
            raise CredentialNotFoundError(
                f"target={target_name}: Credential을 찾을 수 없습니다.",
            )

        return dict(self._data[target_name])

    def delete(self, target_name: str) -> None:

        if target_name not in self._data:
            raise CredentialStoreError(
                f"target={target_name}: 삭제할 Credential이 없습니다.",
            )

        del self._data[target_name]

    def exists(self, target_name: str) -> bool:

        return target_name in self._data


class AlwaysFailingCredentialStore(CredentialStore):
    """
    "Credential Manager 실패 시 fail-closed" 시나리오 전용 테스트
    fixture — 모든 작업이 CredentialStoreError를 던진다.
    """

    def save(self, target_name: str, payload: dict) -> None:

        raise CredentialStoreError("테스트: 저장소를 사용할 수 없습니다.")

    def read(self, target_name: str) -> dict:

        raise CredentialStoreError("테스트: 저장소를 사용할 수 없습니다.")

    def delete(self, target_name: str) -> None:

        raise CredentialStoreError("테스트: 저장소를 사용할 수 없습니다.")

    def exists(self, target_name: str) -> bool:

        raise CredentialStoreError("테스트: 저장소를 사용할 수 없습니다.")


class SelectiveFailureCredentialStore(CredentialStore):
    """
    2026-08-04 V6 Gate 2D: Credential 원자성(2단계 쓰기 보상 로직)을
    검증하기 위한 테스트 fixture — 메서드별·target별로 실패를 선택적으로
    주입하고, 각 메서드 호출 횟수를 셀 수 있다. InMemoryCredentialStore와
    달리 "이번 한 번만 실패" 또는 "이 target_name은 항상 실패"를 골라
    구성할 수 있어, 2단계 쓰기(Credential Store 먼저, DB 다음)의 각
    실패 지점을 정확히 재현한다.
    """

    def __init__(self) -> None:

        self._data: dict[str, dict] = {}
        self.call_counts: dict[str, int] = {
            "save": 0, "read": 0, "delete": 0, "exists": 0,
        }
        self._fail_next: dict[str, int] = {
            "save": 0, "read": 0, "delete": 0, "exists": 0,
        }
        self._fail_targets: dict[str, set] = {
            "save": set(), "read": set(), "delete": set(), "exists": set(),
        }

    def fail_next(self, method: str, times: int = 1) -> None:
        """다음 `times`번의 `method` 호출을(target_name 무관) 실패시킨다."""

        self._fail_next[method] = self._fail_next.get(method, 0) + times

    def fail_target(self, method: str, target_name: str) -> None:
        """특정 target_name에 대한 `method` 호출을 항상 실패시킨다."""

        self._fail_targets[method].add(target_name)

    def _maybe_fail(self, method: str, target_name: str) -> None:

        if self._fail_next.get(method, 0) > 0:
            self._fail_next[method] -= 1
            raise CredentialStoreError(
                f"테스트 주입 실패: {method}(target={target_name}).",
            )

        if target_name in self._fail_targets.get(method, set()):
            raise CredentialStoreError(
                f"테스트 주입 실패: {method}(target={target_name}).",
            )

    def save(self, target_name: str, payload: dict) -> None:

        self.call_counts["save"] += 1
        self._maybe_fail("save", target_name)
        self._data[target_name] = dict(payload)

    def read(self, target_name: str) -> dict:

        self.call_counts["read"] += 1
        self._maybe_fail("read", target_name)

        if target_name not in self._data:
            raise CredentialNotFoundError(
                f"target={target_name}: Credential을 찾을 수 없습니다.",
            )

        return dict(self._data[target_name])

    def delete(self, target_name: str) -> None:

        self.call_counts["delete"] += 1
        self._maybe_fail("delete", target_name)

        if target_name not in self._data:
            raise CredentialStoreError(
                f"target={target_name}: 삭제할 Credential이 없습니다.",
            )

        del self._data[target_name]

    def exists(self, target_name: str) -> bool:

        self.call_counts["exists"] += 1
        self._maybe_fail("exists", target_name)

        return target_name in self._data


__all__ = [
    "FORBID_REAL_CREDENTIAL_STORE_ENV_VAR",
    "CredentialStoreError",
    "CredentialStoreUnavailableError",
    "CredentialNotFoundError",
    "CredentialStore",
    "WindowsCredentialStore",
    "InMemoryCredentialStore",
    "AlwaysFailingCredentialStore",
    "SelectiveFailureCredentialStore",
]
