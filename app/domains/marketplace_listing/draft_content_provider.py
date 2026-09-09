"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/draft_content_provider.py

V7 Gate 6 — 상품 설명 등 "쓰기 콘텐츠" AI Provider 경계.

app/domains/media_asset/providers.py(이미지 생성 Provider)와
app/domains/decision/evaluator_protocol.py(평가 Provider, 아직
미배선)가 이미 확립한 "추상 인터페이스 + Fake 구현체 등록" 패턴을
텍스트(상품 설명) 생성에도 동일하게 적용한다. 이번 Phase는 실제
LLM(예: Claude API)을 전혀 호출하지 않는다 — FakeDraftContentProvider만
실제로 동작하며, 네트워크 호출 없이 candidate 필드로부터 결정론적
설명 문구를 만든다(같은 입력 → 항상 같은 출력 — idempotency 테스트에
쓰인다).

실제 Provider(예: Claude API를 통한 상품 설명 생성)를 연결하려면 이
파일에 DraftContentProvider를 상속한 새 클래스를 추가하고
DRAFT_CONTENT_PROVIDERS_BY_CODE에 등록한다 — 이번 Phase는 그 골격
(추상 인터페이스 + 등록 지점)만 만든다(실제 승인·과금·네트워크 접근
없음, media_asset/providers.py와 동일한 설계 결정).

생성된 초안은 항상 content_source="AI_FAKE_DRAFT"로 표시된다 — 사람이
작성한 것처럼, 또는 실제 AI가 작성한 것처럼 위장하지 않는다
(app/domains/decision::evaluator_kind="deterministic" 명시 원칙과
동일한 "가짜 AI를 실제 AI로 보고하지 않는다" 철학).
=========================================================
"""

from __future__ import annotations

import hashlib
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class DraftContentRequest:

    candidate_id: int
    product_name: str
    category_hint: str | None
    brand_hint: str | None


@dataclass(frozen=True)
class DraftContentResult:

    description: str
    keywords: tuple[str, ...]
    content_source: str  # 예: "AI_FAKE_DRAFT"
    provider_code: str


class DraftContentProviderError(Exception):
    """Provider 자체가 요청을 처리할 수 없을 때(설정 없음 등)."""


class DraftContentProvider(ABC):

    code: str = "ABSTRACT"

    @abstractmethod
    def generate(self, request: DraftContentRequest) -> DraftContentResult:
        ...


class FakeDraftContentProvider(DraftContentProvider):
    """
    실제 LLM 호출 없이 candidate 필드만으로 결정론적인 설명 초안을
    만든다. 이 초안은 그 자체로는 아무것도 확정하지 않는다 — 위저드는
    여전히 EDITABLE 상태에서 이 값을 자유롭게 덮어쓸 수 있고, 승인
    (approve())은 이전과 동일하게 recent_auth_token+nonce로 보호된
    사람의 검토를 거쳐야 한다.
    """

    code = "FAKE"

    def generate(self, request: DraftContentRequest) -> DraftContentResult:

        digest = hashlib.sha256(
            f"{request.candidate_id}:{request.product_name}".encode("utf-8"),
        ).hexdigest()[:8]

        parts = [request.product_name]
        if request.brand_hint:
            parts.append(f"브랜드: {request.brand_hint}")
        if request.category_hint:
            parts.append(f"카테고리: {request.category_hint}")

        description = (
            f"[AI 초안 draft-{digest}] {' / '.join(parts)} — 이 설명은 "
            "실제 LLM이 아니라 결정론적 Fake Provider가 생성한 초안입니다. "
            "운영자가 검토·수정 후 승인해야만 실제 등록에 사용됩니다."
        )

        keywords = tuple(
            kw for kw in (request.brand_hint, request.category_hint) if kw
        )

        return DraftContentResult(
            description=description,
            keywords=keywords,
            content_source="AI_FAKE_DRAFT",
            provider_code=self.code,
        )


class DisabledDraftContentProvider(DraftContentProvider):
    """
    실제 Provider가 아직 연결되지 않은 상태의 명시적 fail-closed
    기본값 — 호출하면 항상 예외를 던진다(조용히 성공 처리하지 않음,
    media_asset::DisabledImageGenerationProvider와 동일한 설계).
    """

    code = "DISABLED"

    def generate(self, request: DraftContentRequest) -> DraftContentResult:

        raise DraftContentProviderError(
            "상품 설명 생성 Provider가 연결되지 않았습니다 — 실제 Provider "
            "연동은 별도 승인 후 진행합니다(현재는 FAKE Provider만 사용할 "
            "수 있습니다).",
        )


DRAFT_CONTENT_PROVIDERS_BY_CODE: dict[str, DraftContentProvider] = {
    "FAKE": FakeDraftContentProvider(),
    "DISABLED": DisabledDraftContentProvider(),
}


def get_draft_content_provider(code: str) -> DraftContentProvider:

    provider = DRAFT_CONTENT_PROVIDERS_BY_CODE.get(code)
    if provider is None:
        raise DraftContentProviderError(f"알 수 없는 Provider입니다: {code}")

    return provider


__all__ = [
    "DraftContentRequest",
    "DraftContentResult",
    "DraftContentProviderError",
    "DraftContentProvider",
    "FakeDraftContentProvider",
    "DisabledDraftContentProvider",
    "DRAFT_CONTENT_PROVIDERS_BY_CODE",
    "get_draft_content_provider",
]
