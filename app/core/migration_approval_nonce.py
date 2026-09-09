"""
=========================================================
Homez OS

File : app/core/migration_approval_nonce.py

Gate G(2026-08-07) — Migration 승인 전용 단발성(single-use) nonce.

`app/core/setup_nonce.py`(최초 관리자 설정 전용)와 동일한 설계
원칙을 그대로 따르되, 별도 용도(Migration 승인)를 위한 독립 모듈이다
— 두 nonce를 같은 상태로 섞으면 한쪽 흐름의 소비/만료가 다른 쪽에
영향을 줄 수 있어 분리했다.

이 nonce는 "그 시점에 실제로 Migration 상태 화면(GET /desktop-setup/
migration-status)을 봤다"는 증거다 — SUPER_ADMIN 권한·recent-auth·
Desktop 토큰·loopback·Origin 일치와 별개로 요구된다(전부 함께 있어야
승인이 통과된다). `verify_nonce()`는 소비하지 않고 상태만 확인하고,
`mark_nonce_consumed()`는 Migration 적용이 실제로 성공한 뒤에만
호출한다 — 적용이 중간에 실패하면 같은 nonce로 재시도할 수 있게
한다(부분 실패를 성공으로 기록하지 않는다는 계약과 별개로, 승인
자체를 다시 받게 강제하지 않기 위함).
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
_state: dict | None = None


class MigrationApprovalNonceStatus(str, Enum):

    VALID = "VALID"
    MISSING = "MISSING"
    MISMATCH = "MISMATCH"
    EXPIRED = "EXPIRED"
    ALREADY_CONSUMED = "ALREADY_CONSUMED"
    LOCKED = "LOCKED"


def _now() -> datetime:

    return datetime.now(timezone.utc)


def generate_migration_approval_nonce() -> str:
    """
    새 nonce를 생성하고 이전 상태를 완전히 대체한다 — 매번 GET
    /desktop-setup/migration-status를 호출할 때마다 새로 발급한다
    (상태 화면을 다시 볼 때마다 이전 승인 시도는 자동으로 무효화).
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


def verify_nonce(presented: str | None) -> MigrationApprovalNonceStatus:
    """nonce를 소비하지 않고 상태만 확인한다."""

    global _state

    with _lock:

        if _state is None:
            return MigrationApprovalNonceStatus.MISSING

        if _state["failed_attempts"] >= MAX_FAILED_ATTEMPTS:
            return MigrationApprovalNonceStatus.LOCKED

        if _state["consumed"]:
            return MigrationApprovalNonceStatus.ALREADY_CONSUMED

        if _now() >= _state["expires_at"]:
            return MigrationApprovalNonceStatus.EXPIRED

        if not presented:
            _state["failed_attempts"] += 1
            return MigrationApprovalNonceStatus.MISSING

        if not constant_time_compare(presented, _state["value"]):
            _state["failed_attempts"] += 1
            return MigrationApprovalNonceStatus.MISMATCH

        return MigrationApprovalNonceStatus.VALID


def mark_nonce_consumed() -> None:

    global _state

    with _lock:
        if _state is not None:
            _state["consumed"] = True


def clear_migration_approval_nonce() -> None:
    """테스트/앱 종료 시 메모리에서 완전히 폐기한다."""

    global _state

    with _lock:
        _state = None


__all__ = [
    "MigrationApprovalNonceStatus",
    "generate_migration_approval_nonce",
    "verify_nonce",
    "mark_nonce_consumed",
    "clear_migration_approval_nonce",
    "NONCE_TTL_SECONDS",
    "MAX_FAILED_ATTEMPTS",
]
