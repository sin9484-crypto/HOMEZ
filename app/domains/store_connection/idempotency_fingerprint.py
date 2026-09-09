"""
=========================================================
Homez OS

File : app/domains/store_connection/idempotency_fingerprint.py

2026-08-01 CTO 재심사(2차) 반영 — idempotency 요청 본문 fingerprint.

기존 결함: create()가 동일 (company_id, creation_idempotency_key)의
기존 행을 발견하면 "지금 들어온 요청 내용"과 전혀 비교하지 않고
그대로 기존 행을 반환했다(service.py 196~206행). 이 상태에서는
클라이언트가 이미 사용된 idempotency key를 다른 marketplace_code/
seller_identifier/display_name/Credential로 재사용해도 오류 없이
"성공"으로 위장된 기존 행을 돌려받는다 — 실제로는 그 요청이 반영되지
않았는데도 클라이언트에게는 마치 반영된 것처럼 보이는 조용한 데이터
불일치다.

이 fingerprint는 "이번 idempotency key로 처음 성공했던 요청이
정확히 무엇이었는가"를 나타내는 64자 hex 다이제스트다. company_id/
marketplace_code/seller_identifier/display_name과, credential_fields
자체의 canonical fingerprint(verification_token.compute_credential_
fingerprint — Secret 원문이 아니라 그 SHA-256 다이제스트)를 canonical
JSON으로 묶은 뒤, app.core.config.settings.SECRET_KEY에서 파생한
전용 도메인 키로 HMAC-SHA256 서명한다. Secret 원문도, 그 canonical
JSON도 반환값에 포함되지 않는다 — 오직 64자 hex 다이제스트만 저장·
비교된다.

verification_token.py의 서명 키와는 다른 도메인 분리 문자열을 쓴다
(토큰 위조와 idempotency 우회는 서로 다른 위협이므로 키를 공유하지
않는다).
=========================================================
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.config import settings
from app.domains.store_connection.verification_token import (
    canonical_json,
    compute_credential_fingerprint,
)


def _fingerprint_key() -> bytes:

    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        b"store_connection_creation_idempotency_fingerprint_v1",
        hashlib.sha256,
    ).digest()


def compute_creation_request_fingerprint(
    company_id: int, marketplace_code: str, seller_identifier: str,
    display_name: str, credential_fields: dict,
) -> str:
    """
    같은 (company_id, idempotency_key) 재호출이 "정말 같은 요청"인지
    판별하는 데 쓰인다 — 하나라도 다르면 다른 fingerprint가 나온다.
    """

    payload = {
        "company_id": company_id,
        "marketplace_code": marketplace_code,
        "seller_identifier": seller_identifier,
        "display_name": display_name,
        "credential_fingerprint": compute_credential_fingerprint(
            credential_fields,
        ),
    }

    return hmac.new(
        _fingerprint_key(),
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


__all__ = [
    "compute_creation_request_fingerprint",
]
