"""
=========================================================
Homez OS

File : app/domains/store_connection/verification_token.py

"연결 테스트"와 "저장"을 분리하되, Secret은 어떤 DB row에도 절대
남기지 않기 위한 stateless 서명 토큰.

verify_new()가 fixture 연결 검증에 성공하면, 그 순간 제출된
credential_fields의 canonical fingerprint를 짧은 만료(3분)와 함께
HMAC으로 서명해 클라이언트에 돌려준다. create()/rotate_credential()는
이 토큰의 서명·만료·company_id·fingerprint가 지금 다시 제출된 요청과
정확히 일치하는지 재확인한다 — 입력값이 하나라도 바뀌면 fingerprint가
달라져 토큰이 무효화된다("6. 입력값이 변경되면 fingerprint 무효화").

서명 키는 app.core.config.settings.SECRET_KEY에서 파생한다(다른
프로토콜과 키를 그대로 공유하지 않도록 도메인 분리 파생).

2026-08-01 CTO 재심사 반영 — 회사 간 token 재사용 차단(Critical):
토큰 payload에 company_id를 반드시 포함하고, 회사 A가 발급받은
토큰을 회사 B의 create()/rotate_credential() 호출에 사용하면
company_id 불일치로 즉시 거부한다.

2026-08-04 V6 Gate 2 재보완 — jti 완전 단발성(single-use) 소비 구현:
이전에는 jti(무작위 nonce)를 payload에 담기만 하고 서버가 "이미
소비됨"으로 기록하지 않아, 3분 TTL 안에서라면 같은 토큰을 여러 번
제출할 수 있었다(잔존 위험으로 명시돼 있었음). 이제 `verify_token()`
이 모든 다른 검증(서명·만료·company_id·fingerprint 등)을 통과한
직후, 마지막 단계로 jti를 프로세스 전역 저장소에 원자적으로
등록한다 — 이미 등록된 jti면 거부한다. `app/core/recent_auth.py`와
동일한 설계 원칙(HOMEZ Desktop 단일 프로세스 전제, DB 테이블 신설
없음, TTL이 지나면 자동 정리, 재시작하면 전부 사라져도 안전)을
따른다 — 새 영속 저장소가 필요 없어 최소 변경으로 완전한 단발성을
달성한다. create()의 idempotency-key 재호출 경로는 verify_token()
자체를 다시 호출하지 않으므로(기존 row를 그대로 반환), 정상적인
재시도/재조회는 이 소비 로직의 영향을 받지 않는다.

2026-08-04 V6 Gate 2A 재보완 — 프로세스 재시작 후 재사용 차단:
위 jti 저장소는 프로세스 메모리이므로, "완전한 단발성"은 같은 프로세스
안에서만 성립하고 "재시작 후에도 예전 토큰이 거부된다"는 별개의
성질이다(재시작하면 `_consumed_jtis`가 통째로 비므로, 죽기 직전에
발급됐지만 아직 소비되지 않은 3분 이내 토큰을 재시작 후 제출하면
서명·만료·company_id·fingerprint가 전부 여전히 유효해 통과해버릴 수
있었다). 이를 막기 위해 프로세스가 시작될 때(모듈이 처음 import될
때) 고엔트로피 `boot_id`를 한 번 생성해 모든 발급 토큰의 payload에
싣고, 검증 시 **현재 프로세스의 boot_id**와 constant-time으로 비교한다
— 재시작하면 boot_id 자체가 달라지므로 이전 프로세스가 발급한 토큰은
사용 여부와 무관하게 전부 거부된다. jti 단발성 소비는 "같은 프로세스
안에서 정확히 1회"를 담당하고, boot_id는 "다른(이전) 프로세스에서
발급된 토큰 자체를 거부"를 담당한다 — 두 방어는 독립적이고 상호
보완적이다.
=========================================================
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime
from datetime import timedelta

from app.core.config import settings

# 2026-08-01: 재사용 가능 시간을 최소화하기 위해 10분 → 3분으로 단축.
TOKEN_TTL = timedelta(minutes=3)

# --------------------------------------------------
# boot_id — 이 프로세스가 시작될 때 정확히 한 번 생성된다. 재시작하면
# 반드시 새 값이 되므로, 이전 프로세스가 발급한 토큰은 boot_id
# 불일치로 전부 거부된다(사용/미사용과 무관).
# --------------------------------------------------

_BOOT_ID = secrets.token_urlsafe(24)


def current_boot_id() -> str:

    return _BOOT_ID


def reset_boot_id_for_tests(new_boot_id: str | None = None) -> str:
    """
    테스트에서 "프로세스 재시작"을 흉내내기 위한 헬퍼 — 실제로는
    boot_id가 모듈 import 시 한 번만 정해지고 이후 절대 바뀌지 않는다.
    """

    global _BOOT_ID
    _BOOT_ID = new_boot_id or secrets.token_urlsafe(24)
    return _BOOT_ID


# --------------------------------------------------
# jti 단발성 소비 — 같은 프로세스 안에서 정확히 1회만 허용한다(app/core/
# recent_auth.py와 동일 설계). jti -> 만료 시각. 만료된 항목은 다음
# 소비 시도 때 함께 청소된다.
# --------------------------------------------------

_jti_lock = threading.Lock()
_consumed_jtis: dict[str, datetime] = {}


def _consume_jti(jti: str, expires_at: datetime, now: datetime) -> bool:
    """이미 소비됐으면 False. 처음이면 등록하고 True."""

    with _jti_lock:
        expired = [k for k, exp in _consumed_jtis.items() if exp <= now]
        for k in expired:
            del _consumed_jtis[k]

        if jti in _consumed_jtis:
            return False

        _consumed_jtis[jti] = expires_at
        return True


def reset_verification_token_jti_state_for_tests() -> None:

    with _jti_lock:
        _consumed_jtis.clear()


def _signing_key() -> bytes:

    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b"store_connection_verification_token_v2",
        hashlib.sha256,
    ).digest()


def canonical_json(data: dict) -> str:

    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def compute_credential_fingerprint(credential_fields: dict) -> str:

    return hashlib.sha256(
        canonical_json(credential_fields).encode("utf-8"),
    ).hexdigest()


class InvalidVerificationTokenError(Exception):
    pass


def issue_token(
    company_id: int, marketplace_code: str, seller_identifier: str,
    credential_fields: dict, now: datetime | None = None,
) -> tuple[str, datetime]:

    now = now or datetime.utcnow()
    expires_at = now + TOKEN_TTL

    payload = {
        "company_id": company_id,
        "marketplace_code": marketplace_code,
        "seller_identifier": seller_identifier,
        "credential_fingerprint": compute_credential_fingerprint(
            credential_fields,
        ),
        "expires_at": expires_at.isoformat(),
        "jti": secrets.token_urlsafe(16),
        "boot_id": _BOOT_ID,
    }

    payload_b64 = base64.urlsafe_b64encode(
        canonical_json(payload).encode("utf-8"),
    ).decode("ascii")

    signature = hmac.new(
        _signing_key(), payload_b64.encode("ascii"), hashlib.sha256,
    ).hexdigest()

    return f"{payload_b64}.{signature}", expires_at


def verify_token(
    token: str, company_id: int, marketplace_code: str,
    seller_identifier: str, credential_fields: dict,
    now: datetime | None = None,
) -> None:
    """
    유효하지 않으면(서명 불일치/만료/회사 불일치/fingerprint 불일치/
    형식 오류) InvalidVerificationTokenError를 던진다 — fail-closed
    (값이 애매하면 항상 거부).

    company_id는 토큰을 발급받은 회사와 지금 이 토큰을 사용하려는
    호출자의 company_id가 정확히 같아야 한다 — 다른 회사가 가로챈
    (또는 우연히 얻은) 토큰을 자신의 연결 생성/교체에 쓸 수 없다.
    """

    now = now or datetime.utcnow()

    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError as exc:
        raise InvalidVerificationTokenError("토큰 형식이 올바르지 않습니다.") from exc

    expected_signature = hmac.new(
        _signing_key(), payload_b64.encode("ascii"), hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidVerificationTokenError("토큰 서명이 유효하지 않습니다.")

    try:
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64.encode("ascii")),
        )
    except Exception as exc:  # noqa: BLE001
        raise InvalidVerificationTokenError("토큰 payload를 해석할 수 없습니다.") from exc

    if not hmac.compare_digest(
        str(payload.get("boot_id", "")), _BOOT_ID,
    ):
        raise InvalidVerificationTokenError(
            "이 토큰은 이전 실행에서 발급되어 더 이상 사용할 수 없습니다 — "
            "다시 검증하세요.",
        )

    expires_at = datetime.fromisoformat(payload["expires_at"])
    if expires_at <= now:
        raise InvalidVerificationTokenError("토큰이 만료되었습니다 — 다시 검증하세요.")

    if payload.get("company_id") != company_id:
        raise InvalidVerificationTokenError(
            "이 토큰은 다른 회사에서 발급되어 사용할 수 없습니다.",
        )

    if payload["marketplace_code"] != marketplace_code:
        raise InvalidVerificationTokenError(
            "토큰의 채널이 지금 요청과 일치하지 않습니다.",
        )
    if payload["seller_identifier"] != seller_identifier:
        raise InvalidVerificationTokenError(
            "토큰의 판매자 식별자가 지금 요청과 일치하지 않습니다.",
        )

    current_fingerprint = compute_credential_fingerprint(credential_fields)
    if payload["credential_fingerprint"] != current_fingerprint:
        raise InvalidVerificationTokenError(
            "검증 이후 입력값이 변경되어 토큰이 무효화되었습니다 — "
            "다시 검증하세요.",
        )

    # 다른 모든 검증을 통과한 토큰만 여기 도달한다 — 이 시점에 정확히
    # 1회만 소비를 허용한다(재사용 시도는 여기서 거부).
    jti = payload.get("jti")
    if not jti or not _consume_jti(jti, expires_at, now):
        raise InvalidVerificationTokenError(
            "이미 사용된 토큰입니다 — 다시 검증하세요.",
        )


__all__ = [
    "TOKEN_TTL",
    "InvalidVerificationTokenError",
    "compute_credential_fingerprint",
    "issue_token",
    "verify_token",
    "reset_verification_token_jti_state_for_tests",
    "current_boot_id",
    "reset_boot_id_for_tests",
]
