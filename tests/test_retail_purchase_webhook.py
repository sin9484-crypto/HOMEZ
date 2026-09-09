"""
=========================================================
Homez OS

File : tests/test_retail_purchase_webhook.py

Gate RP-1(2026-08-22 14차 지시 — 작업 5·9) 검증 — Provider Webhook
서명 검증·재사용(replay) 방지 계약. 실제 Provider secret은 어디에도
없다 — secret이 없으면 항상 실패해야 한다(fail-closed)는 것 자체가
검증 대상이다. 실제 homez.db는 전혀 열지 않는다(임시 SQLite 파일).
=========================================================
"""

import asyncio
import hashlib
import hmac
import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import UnauthorizedException
from app.database.base import Base
from app.domains.retail_purchase.model import RetailPurchaseWebhookEvent
from app.domains.retail_purchase.router import verify_webhook_request_signature
from app.domains.retail_purchase.webhook import WebhookReplayError
from app.domains.retail_purchase.webhook import WebhookSignatureError
from app.domains.retail_purchase.webhook import record_webhook_event_once
from app.domains.retail_purchase.webhook import verify_webhook_signature


class _FakeRequest:
    """FastAPI Request 대역 — httpx 미설치로 TestClient를 못 쓰는
    이 저장소의 기존 관례(router 함수를 직접 호출)를 그대로 따른다."""

    def __init__(self, body: bytes):
        self._body = body

    async def body(self) -> bytes:
        return self._body


class WebhookSignatureTestCase(unittest.TestCase):

    def test_rejects_when_no_secret_configured(self):
        """실 Provider가 아직 연결되지 않아 secret이 없으면 서명이
        아무리 그럴듯해도 통과시키지 않는다 — "Provider 키 없이
        성공 처리하지 않는다"는 요구사항의 핵심 검증."""

        payload = b'{"event":"order.paid"}'
        fake_signature = hashlib.sha256(payload).hexdigest()

        with self.assertRaises(WebhookSignatureError):
            verify_webhook_signature(
                secret=None, payload=payload, signature_header=fake_signature,
            )

    def test_rejects_missing_signature_header(self):

        with self.assertRaises(WebhookSignatureError):
            verify_webhook_signature(
                secret="real-secret", payload=b"{}", signature_header=None,
            )

    def test_rejects_wrong_signature(self):

        with self.assertRaises(WebhookSignatureError):
            verify_webhook_signature(
                secret="real-secret", payload=b'{"a":1}',
                signature_header="0" * 64,
            )

    def test_accepts_correct_hmac_signature(self):
        """secret이 실제로 연결된 경우의 계약 자체는 정상 동작해야
        한다 — 이 부분은 실 Provider 없이도 순수 함수로 검증 가능."""

        secret = "real-secret"
        payload = b'{"event":"order.paid"}'
        signature = hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256,
        ).hexdigest()

        verify_webhook_signature(
            secret=secret, payload=payload, signature_header=signature,
        )  # 예외가 나지 않으면 통과


class WebhookRequestSignatureDependencyTestCase(unittest.TestCase):
    """app/domains/retail_purchase/router.py::verify_webhook_request_
    signature — FastAPI Depends로 분리된 실제 인증 계약(2026-08-22
    14차 지시 작업 5 — tests/test_route_authentication_contract.py가
    이 route에 인식된 인증 Depends가 있는지 강제하면서 발견된 gap을
    수정한 결과)."""

    def test_rejects_without_provider_secret_connected(self):
        """실 Provider secret이 아직 연결되지 않은 현재 구조에서는,
        서명이 형식적으로 그럴듯해도 항상 거부된다(fail-closed)."""

        request = _FakeRequest(b'{"event":"order.paid"}')

        async def run():
            await verify_webhook_request_signature(
                provider_code="FAKE", request=request,
                signature="0" * 64,
            )

        with self.assertRaises(UnauthorizedException):
            asyncio.run(run())

    def test_rejects_missing_signature_header(self):

        request = _FakeRequest(b"{}")

        async def run():
            await verify_webhook_request_signature(
                provider_code="FAKE", request=request, signature=None,
            )

        with self.assertRaises(UnauthorizedException):
            asyncio.run(run())


class WebhookReplayTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(
            bind=self.engine, tables=[RetailPurchaseWebhookEvent.__table__],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_first_event_accepted(self):

        result = record_webhook_event_once(
            self.db, provider_code="FAKE", event_id="evt-1",
        )
        self.db.commit()
        self.assertEqual(result.event_id, "evt-1")

    def test_duplicate_event_rejected(self):

        record_webhook_event_once(self.db, provider_code="FAKE", event_id="evt-2")
        self.db.commit()

        with self.assertRaises(WebhookReplayError):
            record_webhook_event_once(
                self.db, provider_code="FAKE", event_id="evt-2",
            )

    def test_same_event_id_different_provider_is_not_a_replay(self):
        """(provider_code, event_id) 복합 UNIQUE — Provider가 다르면
        event_id가 우연히 같아도 별개 이벤트다."""

        record_webhook_event_once(self.db, provider_code="FAKE", event_id="evt-3")
        self.db.commit()

        result = record_webhook_event_once(
            self.db, provider_code="OFFICIAL_MARKETPLACE", event_id="evt-3",
        )
        self.db.commit()
        self.assertEqual(result.provider_code, "OFFICIAL_MARKETPLACE")


if __name__ == "__main__":
    unittest.main()
