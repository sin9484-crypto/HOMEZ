"""
=========================================================
Homez OS

File : app/domains/media_asset/providers.py

이미지 생성 Provider 경계 — 이번 Phase는 실제 외부 이미지 생성 API를
전혀 호출하지 않는다. FakeImageGenerationProvider만 실제로 동작하며,
네트워크 호출 없이 결정론적인 로컬 테스트 PNG만 만든다(같은 요청
지문이면 항상 같은 바이트를 반환 — idempotency/재시도 테스트에
쓰인다). DisabledImageGenerationProvider는 "아직 실제 Provider가
연결되지 않았다"를 명시적으로 실패시키는 fail-closed 기본값이다 —
설정되지 않은 상태에서 조용히 성공한 것처럼 응답하지 않는다.

실제 Provider(예: 유료 이미지 생성 API)를 연결하려면 이 파일에
ImageGenerationProvider를 상속한 새 클래스를 추가하고
PROVIDERS_BY_CODE에 등록한다 — 이번 Phase는 그 골격(추상 인터페이스
+ 등록 지점)만 만든다.
=========================================================
"""

import hashlib
import struct
import zlib
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field


@dataclass(frozen=True)
class ImageGenerationRequestItem:

    purpose: str
    sequence_index: int


@dataclass(frozen=True)
class ImageGenerationRequest:

    company_id: int
    product_candidate_id: int
    prompt_fingerprint: str
    style_params: dict
    items: tuple[ImageGenerationRequestItem, ...]


@dataclass(frozen=True)
class ImageGenerationResultItem:

    sequence_index: int
    purpose: str
    status: str  # SUCCEEDED / FAILED
    image_bytes: bytes | None
    mime_type: str | None
    safety_check_status: str  # PASSED / BLOCKED / UNKNOWN
    error_reason: str | None = None


@dataclass(frozen=True)
class ImageGenerationProviderResult:

    items: tuple[ImageGenerationResultItem, ...]
    provider_cost: float | None = field(default=None)


class ImageGenerationProviderError(Exception):
    """Provider 자체가 요청을 처리할 수 없을 때(설정 없음 등)."""


class ImageGenerationProvider(ABC):

    code: str = "ABSTRACT"

    @abstractmethod
    def generate(
        self, request: ImageGenerationRequest,
    ) -> ImageGenerationProviderResult:
        ...


def _deterministic_png(seed_text: str, width: int = 32, height: int = 32) -> bytes:
    """
    네트워크 호출 없이, 입력 문자열로부터 결정론적인 최소 유효 PNG
    바이트를 만든다(같은 seed_text → 항상 같은 바이트). 실제 이미지
    콘텐츠가 아니라 "형식상 유효한 PNG"를 만드는 것이 목적 —
    signature/MIME/해상도 검증 경로를 실제로 통과시키기 위함이다.
    """

    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    r, g, b = digest[0], digest[1], digest[2]

    def chunk(chunk_type: bytes, data: bytes) -> bytes:

        return (
            struct.pack(">I", len(data))
            + chunk_type
            + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"

    ihdr = struct.pack(
        ">IIBBBBB", width, height, 8, 2, 0, 0, 0,
    )

    raw_row = bytes([r, g, b]) * width
    raw = b"".join(b"\x00" + raw_row for _ in range(height))
    idat = zlib.compress(raw)

    return (
        signature
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


class FakeImageGenerationProvider(ImageGenerationProvider):
    """
    실제 네트워크 호출 없이 결정론적인 로컬 테스트 이미지를 반환한다.
    비용은 항목당 고정된 낮은 값으로 시뮬레이션한다.
    """

    code = "FAKE"

    FAKE_COST_PER_ITEM = 0.01

    def generate(
        self, request: ImageGenerationRequest,
    ) -> ImageGenerationProviderResult:

        results = []
        for item in request.items:
            seed = f"{request.prompt_fingerprint}:{item.sequence_index}:{item.purpose}"
            image_bytes = _deterministic_png(seed)

            results.append(ImageGenerationResultItem(
                sequence_index=item.sequence_index,
                purpose=item.purpose,
                status="SUCCEEDED",
                image_bytes=image_bytes,
                mime_type="image/png",
                safety_check_status="PASSED",
            ))

        return ImageGenerationProviderResult(
            items=tuple(results),
            provider_cost=self.FAKE_COST_PER_ITEM * len(request.items),
        )


class DisabledImageGenerationProvider(ImageGenerationProvider):
    """
    실제 Provider가 아직 연결되지 않은 상태의 명시적 fail-closed
    기본값 — 호출하면 항상 예외를 던진다(조용히 성공 처리하지 않음).
    """

    code = "DISABLED"

    def generate(
        self, request: ImageGenerationRequest,
    ) -> ImageGenerationProviderResult:

        raise ImageGenerationProviderError(
            "이미지 생성 Provider가 연결되지 않았습니다 — 실제 Provider "
            "연동은 별도 승인 후 진행합니다(현재는 FAKE Provider만 "
            "사용할 수 있습니다).",
        )


PROVIDERS_BY_CODE: dict[str, ImageGenerationProvider] = {
    "FAKE": FakeImageGenerationProvider(),
    "DISABLED": DisabledImageGenerationProvider(),
}


def get_provider(code: str) -> ImageGenerationProvider:

    provider = PROVIDERS_BY_CODE.get(code)
    if provider is None:
        raise ImageGenerationProviderError(f"알 수 없는 Provider입니다: {code}")

    return provider


__all__ = [
    "ImageGenerationRequestItem",
    "ImageGenerationRequest",
    "ImageGenerationResultItem",
    "ImageGenerationProviderResult",
    "ImageGenerationProviderError",
    "ImageGenerationProvider",
    "FakeImageGenerationProvider",
    "DisabledImageGenerationProvider",
    "PROVIDERS_BY_CODE",
    "get_provider",
]
