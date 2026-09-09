"""
=========================================================
Homez OS

File : app/core/setup_nonce.py

HOMEZ Desktop 최초 관리자 설정 — 일회용 setup nonce 보관소.

`app/core/desktop_token.py`와 동일한 설계 원칙(프로세스 전역 메모리
보관, 값 자체를 로그에 남기지 않음, URL/localStorage 미노출 — Desktop
Shell과 FastAPI 앱이 같은 프로세스의 스레드로 실행되므로 모듈 전역
변수로 안전하게 공유된다). 이 nonce는 최초 관리자 계정 생성이라는
"단 한 번만 허용되는" 쓰기 작업 전용 방어 계층이며, Desktop session
token과는 별개다(둘 다 필요 — Desktop token은 이 프로세스가 진짜
HOMEZ Desktop Shell임을, nonce는 그 창 안의 설정 화면이 실제로
로드되어 브리지를 호출했음을 각각 확인한다).
=========================================================
"""

import threading
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from enum import Enum

import secrets

from app.core.security import constant_time_compare

NONCE_TTL_SECONDS = 600  # 10분
MAX_FAILED_ATTEMPTS = 5

_lock = threading.Lock()
_state: dict | None = None


class NonceCheckStatus(str, Enum):

    VALID = "VALID"
    MISSING = "MISSING"
    MISMATCH = "MISMATCH"
    EXPIRED = "EXPIRED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    LOCKED = "LOCKED"


def _now() -> datetime:

    return datetime.now(timezone.utc)


def generate_setup_nonce() -> str:
    """
    새 nonce를 생성하고 이전 상태를 완전히 대체한다(프로세스 시작 시
    1회 호출 — app/desktop/server.py::start_server()).
    """

    global _state

    token = secrets.token_urlsafe(32)

    with _lock:
        _state = {
            "value": token,
            "expires_at": _now() + timedelta(seconds=NONCE_TTL_SECONDS),
            "consumed": False,
            "failed_attempts": 0,
        }

    return token


def get_current_nonce_for_bridge() -> str | None:
    """
    pywebview js_api 브리지 전용 — 이미 소비되었거나 만료된 nonce는
    반환하지 않는다(설정 화면이 새로고침돼도 죽은 nonce를 다시 주지
    않는다).
    """

    with _lock:
        if _state is None:
            return None
        if _state["consumed"] or _now() >= _state["expires_at"]:
            return None
        return _state["value"]


def verify_nonce(presented: str | None) -> NonceCheckStatus:
    """
    nonce를 소비하지 않고 상태만 확인한다 — 실제 소비는 계정 생성이
    완전히 성공한 뒤 `mark_nonce_consumed()`로 별도 수행한다(중간에
    실패하면 재시도할 수 있어야 하므로 잘못된 nonce 제시 자체만
    실패 횟수에 반영한다).
    """

    global _state

    with _lock:

        if _state is None:
            return NonceCheckStatus.MISSING

        if _state["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
            return NonceCheckStatus.LOCKED

        if _state["consumed"]:
            return NonceCheckStatus.ALREADY_CONSUMED

        if _now() >= _state["expires_at"]:
            return NonceCheckStatus.EXPIRED

        if not presented:
            _state["failed_attempts"] += 1
            return NonceCheckStatus.MISSING

        if not constant_time_compare(presented, _state["value"]):
            _state["failed_attempts"] += 1
            return NonceCheckStatus.MISMATCH

        return NonceCheckStatus.VALID


def mark_nonce_consumed() -> None:

    global _state

    with _lock:
        if _state is not None:
            _state["consumed"] = True


def clear_setup_nonce() -> None:
    """앱 종료 시 메모리에서 완전히 폐기한다."""

    global _state

    with _lock:
        _state = None


__all__ = [
    "NonceCheckStatus",
    "generate_setup_nonce",
    "get_current_nonce_for_bridge",
    "verify_nonce",
    "mark_nonce_consumed",
    "clear_setup_nonce",
    "NONCE_TTL_SECONDS",
    "MAX_FAILED_ATTEMPTS",
]
