"""
=========================================================
Homez OS

File : app/desktop/single_instance.py

HOMEZ Desktop Shell — 단일 인스턴스 가드

Windows named mutex를 사용한다(ctypes만 사용, pywin32 등 추가 패키지
불필요 — 이번 단계에서 승인된 패키지는 pywebview뿐이다).

named mutex는 파일 락과 달리 프로세스가 비정상 종료되면 OS가 자동으로
소유권을 해제한다 — 별도의 "stale lock 복구" 로직이 필요 없다(이 자체가
stale lock 문제를 구조적으로 피하는 방식이다).
=========================================================
"""

import ctypes
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Global\\HOMEZ_Desktop_App_SingleInstance_Mutex"


class SingleInstanceGuard:
    """
    사용법:

        guard = SingleInstanceGuard()
        if not guard.acquire():
            print("HOMEZ가 이미 실행 중입니다.")
            return
        try:
            ...
        finally:
            guard.release()
    """

    def __init__(self, name: str = MUTEX_NAME):

        self._name = name
        self._handle: int | None = None
        self.is_already_running = False

    def acquire(self) -> bool:
        """
        뮤텍스 획득을 시도한다. 반환값이 True면 이 프로세스가 유일한
        인스턴스다. False면 이미 다른 HOMEZ Desktop 인스턴스가 실행
        중이라는 뜻이며(self.is_already_running도 True로 설정된다),
        이 프로세스는 새 서버를 만들지 않고 종료해야 한다.
        """

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR,
        ]

        handle = kernel32.CreateMutexW(None, False, self._name)
        last_error = ctypes.get_last_error()

        if not handle:
            # CreateMutexW 자체가 실패(핸들 0) — 뮤텍스를 신뢰할 수
            # 없으므로 안전한 쪽(이미 실행 중인 것으로 간주)을 택한다.
            self.is_already_running = True
            return False

        self._handle = handle
        self.is_already_running = last_error == ERROR_ALREADY_EXISTS

        return not self.is_already_running

    def release(self) -> None:

        if self._handle:
            ctypes.WinDLL(
                "kernel32", use_last_error=True,
            ).CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SingleInstanceGuard":

        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:

        self.release()


__all__ = [
    "SingleInstanceGuard",
    "MUTEX_NAME",
]
