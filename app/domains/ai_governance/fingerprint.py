"""
=========================================================
Homez OS

File : app/domains/ai_governance/fingerprint.py

AG-4(2026-08-21) — ProposedAction의 input_fingerprint 계산. 이
코드베이스 전역 fingerprint 패턴(SHA-256 + canonical JSON)을 그대로
재사용한다(app/domains/marketplace_listing/fingerprint.py 등과 동일
원칙 — 각 도메인이 자체 사본을 둔다).
=========================================================
"""

import hashlib
import json


def canonical_json(data: dict) -> str:

    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_proposed_action_fingerprint(
    *,
    capability_code: str,
    action_type: str,
    target_entity: str,
    proposed_payload: dict,
) -> str:
    """
    제안 생성 시점의 "무엇을 하자는 제안이었는가"를 그대로 담는다.
    승인 시점에 이 값을 다시 계산해 저장된 값과 비교한다 — 대상
    데이터가 바뀌면(예: 가격 변경 대상 상품의 가격이 그 사이 다시
    바뀜) 자동으로 무효화된다(이 코드베이스의 다른 fingerprint
    게이트와 동일한 fail-closed 철학).
    """

    payload = {
        "capability_code": capability_code,
        "action_type": action_type,
        "target_entity": target_entity,
        "proposed_payload": proposed_payload,
    }
    return sha256_hex(canonical_json(payload))


__all__ = [
    "canonical_json", "sha256_hex", "compute_proposed_action_fingerprint",
]
