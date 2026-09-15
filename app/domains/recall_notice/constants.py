"""
=========================================================
Homez OS

File : app/domains/recall_notice/constants.py

2026-09-15 전면 감사 후속(Phase 9I/9J, 10-17/10-18).
=========================================================
"""

from __future__ import annotations


class RecallCheckRunStatus:

    SUCCESS = "SUCCESS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILURE = "FAILURE"

    ALL = (SUCCESS, PARTIAL_FAILURE, FAILURE)


class RecallCheckJobMode:
    """10-17 — "기본 함수 모드는 PAUSED로 한다." 사용자가 명시적으로
    ACTIVE로 바꾸기 전까지는 실제 Provider가 있어도(현재는 없다)
    자동으로 실행되지 않는다."""

    PAUSED = "PAUSED"
    ACTIVE = "ACTIVE"

    ALL = (PAUSED, ACTIVE)
    DEFAULT = PAUSED


class RecallProductBlockStatus:

    BLOCKED = "BLOCKED"
    UNBLOCKED = "UNBLOCKED"

    ALL = (BLOCKED, UNBLOCKED)


__all__ = [
    "RecallCheckRunStatus",
    "RecallCheckJobMode",
    "RecallProductBlockStatus",
]
