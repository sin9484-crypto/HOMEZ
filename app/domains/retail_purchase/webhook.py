"""
=========================================================
Homez OS

File : app/domains/retail_purchase/webhook.py

Gate RP-1(2026-08-22 14차 지시 — 작업 5) — Provider Webhook 서명
검증·재사용(replay) 방지 계약. 실제 계약된 Provider가 아직 없으므로
이 모듈은 "검증 계약"까지만 구현한다 — 실제 Provider의 signing
secret 없이는 어떤 payload도 성공 처리하지 않는다(fail-closed).
주문 상태를 실제로 갱신하는 dispatch 로직은 실 Provider 연결 후
별도 구현 대상이다(이번 라운드는 NOT_IMPLEMENTED로 정직하게 남긴다).
=========================================================
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.retail_purchase.model import RetailPurchaseWebhookEvent


class WebhookSignatureError(Exception):
    """서명이 없거나·형식이 잘못됐거나·secret과 일치하지 않을 때 —
    메시지에 secret·payload 원문을 포함하지 않는다."""


class WebhookReplayError(Exception):
    """이미 처리된 event_id가 재전송됐을 때(재시도·공격 재생 모두
    같은 방식으로 차단한다)."""


def verify_webhook_signature(
    *, secret: str | None, payload: bytes, signature_header: str | None,
) -> None:
    """HMAC-SHA256(secret, payload) == signature_header(hex)일 때만
    통과한다. secret이 없으면(=실 Provider가 아직 연결되지 않음)
    무조건 실패한다 — "Provider 키 없이 성공 처리하지 않는다"는
    지시문 요구사항을 코드로 강제한다."""

    if not secret:
        raise WebhookSignatureError(
            "이 Provider에 연결된 서명 secret이 없어 Webhook을 처리할 "
            "수 없습니다(실 계약 전, PROVIDER_NOT_CONNECTED).",
        )
    if not signature_header:
        raise WebhookSignatureError("서명 헤더가 없습니다.")

    expected = hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header.strip().lower()):
        raise WebhookSignatureError("서명이 일치하지 않습니다.")


@dataclass(frozen=True)
class WebhookAcceptResult:

    provider_code: str
    event_id: str
    accepted_at: datetime


def record_webhook_event_once(
    db: Session, *, provider_code: str, event_id: str,
) -> WebhookAcceptResult:
    """(provider_code, event_id) UNIQUE 제약으로 재생 공격·중복
    재전송을 차단한다 — 서명 검증을 통과한 이후에만 호출한다."""

    now = datetime.utcnow()
    row = RetailPurchaseWebhookEvent(
        provider_code=provider_code, event_id=event_id, received_at=now,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise WebhookReplayError(
            f"이미 처리된 Webhook 이벤트입니다: {provider_code}/{event_id}",
        ) from e

    return WebhookAcceptResult(
        provider_code=provider_code, event_id=event_id, accepted_at=now,
    )


__all__ = [
    "WebhookSignatureError",
    "WebhookReplayError",
    "WebhookAcceptResult",
    "verify_webhook_signature",
    "record_webhook_event_once",
]
