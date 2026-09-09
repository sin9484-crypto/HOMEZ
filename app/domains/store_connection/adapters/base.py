"""
=========================================================
Homez OS

File : app/domains/store_connection/adapters/base.py

판매채널 연결 검증 Adapter 공통 인터페이스 — 이번 Phase는 fixture
전용이다(실제 쿠팡/네이버 인증 서버를 호출하지 않는다). 어떤 구현체도
requests/httpx/socket 등 네트워크 라이브러리를 import하지 않는다
(app/domains/marketplace_listing/adapters/coupang_adapter.py와 동일
원칙 — tests/test_store_connection.py가 이를 정적으로 검증한다).
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class VerificationResult:
    """
    실제 플랫폼 응답 원문·서명값·Access Token은 어디에도 담지 않는다 —
    error_summary는 사람이 읽을 수 있는 안전한 요약 문장만 담는다.
    """

    success: bool
    error_code: str | None
    error_summary: str | None
    expiration_status: str
    expires_at: datetime | None


class StoreConnectionAdapter(ABC):

    marketplace_code: str

    @abstractmethod
    def validate_credential_shape(self, credential_fields: dict) -> None:
        """
        형식이 명백히 잘못된 경우 ValueError를 던진다(필수 필드 누락 등
        — 대부분은 schema.py의 Pydantic Schema가 이미 걸러내므로, 이
        메서드는 Schema로 표현하기 어려운 채널 고유의 형식 규칙만
        추가로 확인한다).
        """

    @abstractmethod
    def verify_connection(self, credential_fields: dict) -> VerificationResult:
        """
        fixture 기반 연결 검증 — 실제 네트워크 호출을 하지 않는다.
        """

    @abstractmethod
    def normalize_error(self, error_code: str) -> str:
        """error_code에 대응하는 사람이 읽을 수 있는 안전한 요약 문장."""

    @abstractmethod
    def get_expiration_status(
        self, credential_fields: dict, now: datetime | None = None,
    ) -> tuple[str, datetime | None]:
        """(expiration_status, expires_at) — fixture 데이터 기준."""


__all__ = [
    "VerificationResult",
    "StoreConnectionAdapter",
]
