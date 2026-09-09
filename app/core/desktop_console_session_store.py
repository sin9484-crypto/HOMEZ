"""
=========================================================
Homez OS

File : app/core/desktop_console_session_store.py

2026-08-14 Gate F-2 — Desktop 콘솔 로그인 세션(access_token)을 Windows
Credential Manager에 저장하는 헬퍼.

배경: Desktop 앱은 실행마다 완전히 무작위 포트로 뜬다(app/desktop/
server.py::find_free_port(), 정책상 고정하지 않는다) — 그 결과
브라우저 origin이 매번 바뀌어 origin에 종속된 localStorage의
로그인 토큰(`homez_console_token`)이 재시작마다 전부 사라진다.

이 모듈은 app/core/windows_credential_store.py의 기존 CredentialStore
추상화를 그대로 재사용한다(신규 저장 메커니즘을 만들지 않는다) —
StoreConnection Credential과 동일한 계열이지만 target 네임스페이스는
분리한다("HOMEZ:console_session:*"). 토큰 원문은 어떤 예외 메시지·
로그에도 남기지 않는다(windows_credential_store.py 자신의 원칙을
그대로 물려받는다 — 이 모듈도 절대 토큰 값을 로깅하지 않는다).

사용자 전환 격리: "현재 활성 사용자" 포인터(ACTIVE_USER_TARGET)를
별도 target에 두고, save()가 다른 user_id로 호출되면 이전 사용자의
Credential을 먼저 삭제한다 — 서로 다른 사용자로 로그인할 때 이전
사용자의 저장된 토큰이 남아 섞이는 일이 없다.

만료 자동 정리: 저장 시점에 JWT의 `exp`(epoch seconds)를 함께
기록하고, load() 시점에 이미 지났으면 그 자리에서 삭제하고 None을
반환한다.
=========================================================
"""

from __future__ import annotations

import time

from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError

def _credential_namespace() -> str:
    """2026-08-24 패키징 격리 재작업 — `app/domains/store_connection/
    service.py::_credential_namespace()`와 동일 원칙. `HOMEZ_DATA_ROOT`
    가 설정된 동안에만 접두사가 바뀐다 — 운영 경로는 변하지 않는다."""

    import os

    if os.environ.get("HOMEZ_DATA_ROOT", "").strip():
        return "HOMEZ_TEST"

    return "HOMEZ"


def _target_prefix() -> str:

    return f"{_credential_namespace()}:console_session:"


def _active_user_target() -> str:

    return f"{_credential_namespace()}:console_active_user"


class DesktopConsoleSessionStore:
    """
    `CredentialStore` 구현체(운영: WindowsCredentialStore, 테스트:
    InMemoryCredentialStore)를 주입받아 Desktop 콘솔 세션을 관리한다.
    """

    def __init__(self, credential_store: CredentialStore):

        self._store = credential_store

    def _target_for_user(self, user_id: int) -> str:

        return f"{_target_prefix()}{user_id}"

    def _read_active_user_id(self) -> int | None:

        try:
            payload = self._store.read(_active_user_target())

        except CredentialNotFoundError:
            return None

        raw = payload.get("user_id")

        try:
            return int(raw) if raw is not None else None

        except (TypeError, ValueError):
            return None

    def save(
        self,
        user_id: int,
        access_token: str,
        expires_at_epoch: int | None,
        *,
        refresh_token: str | None = None,
        refresh_expires_at_epoch: int | None = None,
    ) -> None:
        """
        새 로그인 세션을 저장한다. 직전 활성 사용자가 이번 user_id와
        다르면(사용자 전환) 그 사용자의 Credential을 먼저 삭제한다 —
        best-effort(이미 없거나 삭제 실패해도 새 세션 저장 자체는
        계속 진행한다, 새 세션 저장 실패로 이어지지 않게 하기 위함).

        2026-08-30 V7 후속 안정화 Phase 2 — Refresh Token도 같은
        Credential 항목에 함께 저장한다("Refresh Token을 JavaScript
        localStorage/sessionStorage에 저장하지 않는다" 요구사항 —
        이 저장소 자체가 Windows Credential Manager이므로 자동으로
        충족된다). Access Token보다 만료가 훨씬 길므로 자체 만료
        시각을 따로 기록한다(access_token만 만료돼도 refresh_token은
        지우지 않는다 — 그게 정확히 auto-refresh가 쓰는 값이다).
        """

        previous_user_id = self._read_active_user_id()

        if previous_user_id is not None and previous_user_id != user_id:
            try:
                self._store.delete(self._target_for_user(previous_user_id))

            except CredentialStoreError:
                pass

        self._store.save(
            self._target_for_user(user_id),
            {
                "access_token": access_token, "expires_at_epoch": expires_at_epoch,
                "refresh_token": refresh_token,
                "refresh_expires_at_epoch": refresh_expires_at_epoch,
            },
        )
        self._store.save(_active_user_target(), {"user_id": user_id})

    def load(self) -> dict | None:
        """
        현재 활성 사용자의 저장된 세션을 반환한다(`{"user_id": int,
        "access_token": str, "refresh_token": str | None}`). 없거나
        Refresh Token까지 완전히 만료됐으면 None을 반환한다(만료된
        경우 자동으로 정리한다).

        Access Token만 만료되고 Refresh Token은 아직 유효하면 그대로
        반환한다 — 호출자(Desktop bridge)가 즉시 `/auth/refresh`로
        갱신하는 것이 정상 흐름이다(access_token 단독 만료를 이유로
        여기서 전체 세션을 지우면 매번 재로그인을 강제하게 된다).
        """

        user_id = self._read_active_user_id()

        if user_id is None:
            return None

        try:
            payload = self._store.read(self._target_for_user(user_id))

        except CredentialNotFoundError:
            return None

        refresh_token = payload.get("refresh_token")
        refresh_expires_at = payload.get("refresh_expires_at_epoch")
        access_token = payload.get("access_token")

        refresh_alive = (
            refresh_token
            and (refresh_expires_at is None or time.time() < float(refresh_expires_at))
        )

        if not refresh_alive:
            expires_at = payload.get("expires_at_epoch")
            if expires_at is not None and time.time() >= float(expires_at):
                self.clear()
                return None

        if not access_token and not refresh_alive:
            return None

        return {
            "user_id": user_id, "access_token": access_token,
            "refresh_token": refresh_token if refresh_alive else None,
        }

    def clear(self) -> None:
        """현재 활성 사용자의 세션과 활성 사용자 포인터를 모두 지운다."""

        user_id = self._read_active_user_id()

        if user_id is not None:
            try:
                self._store.delete(self._target_for_user(user_id))

            except CredentialStoreError:
                pass

        try:
            self._store.delete(_active_user_target())

        except CredentialStoreError:
            pass


__all__ = [
    "DesktopConsoleSessionStore",
]
