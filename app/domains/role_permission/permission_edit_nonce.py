"""
=========================================================
Homez OS

File : app/domains/role_permission/permission_edit_nonce.py

Gate T(2026-08-10) — 역할의 개별 Permission 집합을 수정하기 전
발급하는 단발성(single-use) nonce. `app/domains/marketplace_listing/
listing_wizard_approval_nonce.py`와 동일한 설계 원칙(role_id로 키를
나눈 프로세스 전역 dict, 재발급 시 이전 상태 완전 대체, 소비는 실제
수정이 성공한 뒤에만)을 그대로 재사용한다.

이 nonce는 "그 시점에 실제로 이 역할의 현재 Permission 목록을
조회했다"는 증거다 — SuperAdminGuard·recent-auth와 별개로 요구된다
(전부 함께 있어야 수정이 통과된다).
=========================================================
"""

import secrets
import threading
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from enum import Enum

from app.core.security import constant_time_compare

NONCE_TTL_SECONDS = 300  # 5분 — X-Recent-Auth-Token과 같은 창
MAX_FAILED_ATTEMPTS = 5

_lock = threading.Lock()
_state_by_role_id: dict[int, dict] = {}


class PermissionEditNonceStatus(str, Enum):

    VALID = "VALID"
    MISSING = "MISSING"
    MISMATCH = "MISMATCH"
    EXPIRED = "EXPIRED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    LOCKED = "LOCKED"


def _now() -> datetime:

    return datetime.now(timezone.utc)


def generate_permission_edit_nonce(role_id: int) -> tuple[str, datetime]:
    """
    새 nonce를 생성하고 이 role_id의 이전 상태를 완전히 대체한다 —
    현재 Permission 목록을 다시 조회할 때마다 이전 수정 시도는 자동
    무효화된다. (nonce, 만료시각) 튜플을 반환한다.
    """

    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=NONCE_TTL_SECONDS)

    with _lock:
        _state_by_role_id[role_id] = {
            "value": token,
            "expires_at": expires_at,
            "consumed": False,
            "failed_attempts": 0,
        }

    return token, expires_at


def verify_nonce(
    role_id: int, presented: str | None,
) -> PermissionEditNonceStatus:
    """nonce를 소비하지 않고 상태만 확인한다."""

    with _lock:
        state = _state_by_role_id.get(role_id)

        if state is None:
            return PermissionEditNonceStatus.MISSING

        if state["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
            return PermissionEditNonceStatus.LOCKED

        if state["consumed"]:
            return PermissionEditNonceStatus.ALREADY_CONSUMED

        if _now() >= state["expires_at"]:
            return PermissionEditNonceStatus.EXPIRED

        if not presented:
            state["failed_attempts"] += 1
            return PermissionEditNonceStatus.MISSING

        if not constant_time_compare(presented, state["value"]):
            state["failed_attempts"] += 1
            return PermissionEditNonceStatus.MISMATCH

        return PermissionEditNonceStatus.VALID


def mark_nonce_consumed(role_id: int) -> None:

    with _lock:
        state = _state_by_role_id.get(role_id)
        if state is not None:
            state["consumed"] = True


def clear_permission_edit_nonce(role_id: int) -> None:

    with _lock:
        _state_by_role_id.pop(role_id, None)


def clear_all_permission_edit_nonces() -> None:
    """테스트 전용 — 전체 상태를 폐기한다."""

    with _lock:
        _state_by_role_id.clear()


__all__ = [
    "PermissionEditNonceStatus",
    "generate_permission_edit_nonce",
    "verify_nonce",
    "mark_nonce_consumed",
    "clear_permission_edit_nonce",
    "clear_all_permission_edit_nonces",
    "NONCE_TTL_SECONDS",
    "MAX_FAILED_ATTEMPTS",
]
