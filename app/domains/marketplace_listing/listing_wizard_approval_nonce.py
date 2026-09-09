"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_approval_nonce.py

Gate I(2026-08-08) — 위저드 승인 전용 단발성(single-use) nonce.

`app/core/migration_approval_nonce.py`와 동일한 설계 원칙을 따르되,
Migration 승인(시스템 전역, 동시에 하나만 진행)과 달리 위저드 승인은
회사마다 동시에 여러 건이 진행될 수 있으므로 전역 단일 상태가 아니라
`wizard_id`로 키를 나눈 dict 저장소를 쓴다.

이 nonce는 "그 시점에 실제로 이 위저드의 승인 미리보기(GET .../
approval-preview)를 봤다"는 증거다 — SuperAdminGuard·recent-auth와
별개로 요구된다(전부 함께 있어야 승인이 통과된다). `verify_nonce()`는
소비하지 않고 상태만 확인하고, `mark_nonce_consumed()`는 승인이 실제로
성공한 뒤에만 호출한다.
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
_state_by_wizard_id: dict[int, dict] = {}


class WizardApprovalNonceStatus(str, Enum):

    VALID = "VALID"
    MISSING = "MISSING"
    MISMATCH = "MISMATCH"
    EXPIRED = "EXPIRED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    LOCKED = "LOCKED"


def _now() -> datetime:

    return datetime.now(timezone.utc)


def generate_wizard_approval_nonce(wizard_id: int) -> tuple[str, datetime]:
    """
    새 nonce를 생성하고 이 wizard_id의 이전 상태를 완전히 대체한다 —
    승인 미리보기를 다시 볼 때마다 이전 승인 시도는 자동으로
    무효화된다. (nonce, 만료시각) 튜플을 반환한다.
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
) -> WizardApprovalNonceStatus:
    """nonce를 소비하지 않고 상태만 확인한다."""

    with _lock:
        state = _state_by_wizard_id.get(wizard_id)

        if state is None:
            return WizardApprovalNonceStatus.MISSING

        if state["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
            return WizardApprovalNonceStatus.LOCKED

        if state["consumed"]:
            return WizardApprovalNonceStatus.ALREADY_CONSUMED

        if _now() >= state["expires_at"]:
            return WizardApprovalNonceStatus.EXPIRED

        if not presented:
            state["failed_attempts"] += 1
            return WizardApprovalNonceStatus.MISSING

        if not constant_time_compare(presented, state["value"]):
            state["failed_attempts"] += 1
            return WizardApprovalNonceStatus.MISMATCH

        return WizardApprovalNonceStatus.VALID


def mark_nonce_consumed(wizard_id: int) -> None:

    with _lock:
        state = _state_by_wizard_id.get(wizard_id)
        if state is not None:
            state["consumed"] = True


def clear_wizard_approval_nonce(wizard_id: int) -> None:
    """
    승인 패키지가 재계산(=fingerprint 변경)돼 무효화될 때 함께
    호출한다 — 이전 nonce로 옛 패키지를 승인할 수 없게 한다.
    """

    with _lock:
        _state_by_wizard_id.pop(wizard_id, None)


def clear_all_wizard_approval_nonces() -> None:
    """테스트 전용 — 전체 상태를 폐기한다."""

    with _lock:
        _state_by_wizard_id.clear()


__all__ = [
    "WizardApprovalNonceStatus",
    "generate_wizard_approval_nonce",
    "verify_nonce",
    "mark_nonce_consumed",
    "clear_wizard_approval_nonce",
    "clear_all_wizard_approval_nonces",
    "NONCE_TTL_SECONDS",
    "MAX_FAILED_ATTEMPTS",
]
