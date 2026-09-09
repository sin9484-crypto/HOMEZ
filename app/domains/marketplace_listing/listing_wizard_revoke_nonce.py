"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_revoke_nonce.py

Gate Q-1(2026-08-09) — 승인 취소(Revoke Approval) 전용 단발성
(single-use) nonce.

`listing_wizard_approval_nonce.py`(승인 전용)와 동일한 설계 원칙을
그대로 따르되, 완전히 별도의 저장소를 쓴다 — 승인 nonce와 취소
nonce를 같은 상태로 섞으면 승인 미리보기를 본 것이 취소를 위한
"봤다는 증거"로 잘못 재사용되거나, 그 반대의 혼선이 생길 수 있어
분리했다(Migration 승인 nonce와 최초 설정 nonce를 분리한 것과 동일한
이유).

이 nonce는 "그 시점에 실제로 이 위저드의 승인 취소 미리보기(GET
.../revoke-approval-preview)를 봤다"는 증거다 — SuperAdminGuard·
recent-auth와 별개로 요구된다.
=========================================================
"""

import secrets
import threading
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from enum import Enum

from app.core.security import constant_time_compare

NONCE_TTL_SECONDS = 300  # 5분 — 다른 위저드 nonce들과 같은 창
MAX_FAILED_ATTEMPTS = 5

_lock = threading.Lock()
_state_by_wizard_id: dict[int, dict] = {}


class WizardRevokeNonceStatus(str, Enum):

    VALID = "VALID"
    MISSING = "MISSING"
    MISMATCH = "MISMATCH"
    EXPIRED = "EXPIRED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    LOCKED = "LOCKED"


def _now() -> datetime:

    return datetime.now(timezone.utc)


def generate_wizard_revoke_nonce(wizard_id: int) -> tuple[str, datetime]:
    """
    새 nonce를 생성하고 이 wizard_id의 이전 취소 nonce 상태를 완전히
    대체한다 — 취소 미리보기를 다시 볼 때마다 이전 취소 시도는
    자동으로 무효화된다. (nonce, 만료시각) 튜플을 반환한다.
    """

    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=NONCE_TTL_SECONDS)

    with _lock:
        _state_by_wizard_id[wizard_id] = {
            "value": token,
            "expires_at": expires_at,
            "consumed": False,
            "failed_attempts": 0,
        }

    return token, expires_at


def verify_nonce(
    wizard_id: int, presented: str | None,
) -> WizardRevokeNonceStatus:
    """nonce를 소비하지 않고 상태만 확인한다."""

    with _lock:
        state = _state_by_wizard_id.get(wizard_id)

        if state is None:
            return WizardRevokeNonceStatus.MISSING

        if state["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
            return WizardRevokeNonceStatus.LOCKED

        if state["consumed"]:
            return WizardRevokeNonceStatus.ALREADY_CONSUMED

        if _now() >= state["expires_at"]:
            return WizardRevokeNonceStatus.EXPIRED

        if not presented:
            state["failed_attempts"] += 1
            return WizardRevokeNonceStatus.MISSING

        if not constant_time_compare(presented, state["value"]):
            state["failed_attempts"] += 1
            return WizardRevokeNonceStatus.MISMATCH

        return WizardRevokeNonceStatus.VALID


def mark_nonce_consumed(wizard_id: int) -> None:

    with _lock:
        state = _state_by_wizard_id.get(wizard_id)
        if state is not None:
            state["consumed"] = True


def clear_wizard_revoke_nonce(wizard_id: int) -> None:

    with _lock:
        _state_by_wizard_id.pop(wizard_id, None)


def clear_all_wizard_revoke_nonces() -> None:
    """테스트 전용 — 전체 상태를 폐기한다."""

    with _lock:
        _state_by_wizard_id.clear()


__all__ = [
    "WizardRevokeNonceStatus",
    "generate_wizard_revoke_nonce",
    "verify_nonce",
    "mark_nonce_consumed",
    "clear_wizard_revoke_nonce",
    "clear_all_wizard_revoke_nonces",
    "NONCE_TTL_SECONDS",
    "MAX_FAILED_ATTEMPTS",
]
