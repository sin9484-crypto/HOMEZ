"""
=========================================================
Homez OS

File : app/core/recent_auth.py

2026-08-04 V6 Gate 1A: "최근 인증(recent-auth)" 증명 — 이미 로그인된
사용자가 민감한 변경(회사명 변경 등)을 하기 직전에 현재 비밀번호를
다시 입력했음을 짧은 시간 동안 증명하는 1회용 토큰.

DB 테이블을 새로 만들지 않는다 — "5분 이내에만 유효하고 정확히 1회만
쓰이는" 값은 서버 재시작으로 사라져도 전혀 문제가 없고(오히려
바람직하다), account_registration/service.py의 rate limit 상태와
동일하게 프로세스 전역 dict + Lock으로 충분하다. 토큰 원문은 어디에도
로그로 남기지 않고, 저장 시에는 해시만 보관한다.

2026-08-04 CTO 재보완 — 명시적 제약(문서화 요구사항):

  이 모듈은 **HOMEZ Desktop의 단일 프로세스·단일 인스턴스 실행**만
  전제한다(app/desktop/single_instance.py가 이미 다중 인스턴스 자체를
  막는다). 프로세스 전역 dict + threading.Lock이므로:
    - 프로세스가 재시작되면(정상 종료/업데이트/크래시 불문) 발급된
      모든 recent-auth 토큰과 잠금 카운터가 전부 사라진다 — 이는
      버그가 아니라 "재시작 후에는 무엇이든 다시 확인받아야 한다"는
      더 안전한 기본값이다.
    - 여러 worker 프로세스나 여러 서버 인스턴스로 확장하는 순간
      이 방식은 더 이상 유효하지 않다 — 각 프로세스가 독립된
      `_tokens`/`_lockouts`를 가지므로 한 worker가 발급한 토큰을
      다른 worker가 소비 확인할 수 없다. 그런 구조로 전환한다면
      이 모듈을 Redis 등 공유 저장소 기반으로 반드시 다시 설계해야
      한다(지금은 그 전환을 하지 않는다 — HOMEZ Desktop은 정의상
      단일 프로세스다).
=========================================================
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import datetime
from datetime import timedelta
from datetime import timezone

RECENT_AUTH_TTL_SECONDS = 300  # 5분
RECENT_AUTH_MAX_FAILED_ATTEMPTS = 5
RECENT_AUTH_LOCKOUT_SECONDS = 900  # 15분

_lock = threading.Lock()
_tokens: dict[str, dict] = {}
_lockouts: dict[int, dict] = {}


def _now() -> datetime:

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_token(raw_token: str) -> str:

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_recent_auth_token(user_id: int) -> tuple[str, datetime]:
    """현재 비밀번호 확인에 성공한 직후에만 호출한다."""

    raw_token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=RECENT_AUTH_TTL_SECONDS)

    with _lock:
        _tokens[_hash_token(raw_token)] = {
            "user_id": user_id,
            "expires_at": expires_at,
            "consumed": False,
        }

    return raw_token, expires_at


def consume_recent_auth_token(raw_token: str | None, user_id: int) -> bool:
    """
    유효하면 즉시 소비(제거)하고 True를 반환한다 — 같은 토큰으로 두
    번째 호출하면 반드시 False다(1회성). 다른 사용자의 토큰이거나,
    만료됐거나, 이미 소비됐으면 False.
    """

    if not raw_token:
        return False

    key = _hash_token(raw_token)

    with _lock:
        entry = _tokens.get(key)

        if entry is None:
            return False

        if entry["consumed"] or entry["user_id"] != user_id or _now() >= entry["expires_at"]:
            del _tokens[key]
            return False

        del _tokens[key]
        return True


def is_recent_auth_locked_out(user_id: int) -> bool:
    """
    실패 횟수가 아직 상한 미만이면(잠금 자체가 걸린 적 없으면) 항상
    False다 — `locked_until`이 잠기지 않은 상태에서도 "생성 시각"으로
    채워져 있어, 그 값만으로 만료 여부를 판단하면 매 조회마다 실패
    카운터 자체가 지워지는 결함이 있었다(집계 도중 잠금 여부를
    확인하는 흔한 호출 패턴에서 실제로 재현됨).
    """

    with _lock:
        entry = _lockouts.get(user_id)

        if entry is None:
            return False

        if entry["failed"] < RECENT_AUTH_MAX_FAILED_ATTEMPTS:
            return False

        if _now() >= entry["locked_until"]:
            del _lockouts[user_id]
            return False

        return True


def record_recent_auth_failure(user_id: int) -> None:

    with _lock:
        entry = _lockouts.setdefault(user_id, {"failed": 0, "locked_until": _now()})
        entry["failed"] += 1

        if entry["failed"] >= RECENT_AUTH_MAX_FAILED_ATTEMPTS:
            entry["locked_until"] = _now() + timedelta(seconds=RECENT_AUTH_LOCKOUT_SECONDS)


def reset_recent_auth_state_for_tests() -> None:

    with _lock:
        _tokens.clear()
        _lockouts.clear()


__all__ = [
    "RECENT_AUTH_TTL_SECONDS",
    "issue_recent_auth_token",
    "consume_recent_auth_token",
    "is_recent_auth_locked_out",
    "record_recent_auth_failure",
    "reset_recent_auth_state_for_tests",
]
