"""
=========================================================
Homez OS

File : app/domains/media_asset/image_processing_providers.py

배경 제거(누끼) Provider 경계 — app/domains/media_asset/providers.py
(이미지 "생성" Provider)와 동일한 철학을 그대로 따른다: Fake는
네트워크 없이 결정론적으로 동작하고, Disabled는 fail-closed
기본값이다.

RembgBackgroundRemovalProvider만 실제 처리를 수행한다 — 완전 로컬
ML 추론이며 유료 API가 아니다. 다만 최초 호출 시 사전학습 모델
(u2netp, 가장 작은 모델을 명시적으로 선택)을 내려받는 네트워크
호출이 한 번 발생할 수 있다(2026-08-20 CTO 승인 항목, requirements.txt
주석 참고) — 이 파일 자체는 그 네트워크 호출을 스스로 트리거하지
않고, 실제로 generate()가 호출될 때만 발생한다. 이번 라운드의 격리
테스트는 FakeBackgroundRemovalProvider만 사용해 이 다운로드를
실행하지 않았다(최종 보고에 명시).
=========================================================
"""

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class BackgroundRemovalRequest:

    company_id: int
    source_image_bytes: bytes
    source_mime_type: str


@dataclass(frozen=True)
class BackgroundRemovalResult:

    status: str  # SUCCEEDED / FAILED
    image_bytes: bytes | None  # 성공 시 투명 PNG
    provider_code: str
    model_version: str | None
    error_reason: str | None = None


class BackgroundRemovalProviderError(Exception):
    """Provider 자체가 요청을 처리할 수 없을 때."""


class BackgroundRemovalProvider(ABC):

    code: str = "ABSTRACT"

    @abstractmethod
    def remove_background(
        self, request: BackgroundRemovalRequest,
    ) -> BackgroundRemovalResult:
        ...


class FakeBackgroundRemovalProvider(BackgroundRemovalProvider):
    """
    실제 배경 제거를 수행하지 않는다 — 원본 이미지를 그대로 투명 PNG
    컨테이너에 담아 반환한다(픽셀 단위 분리 없음, 시연/테스트 전용).
    실제 제품 등록에는 이 Provider의 결과를 최종본으로 쓰지 않는다
    (UI가 "시연용 이미지"로 표시해야 한다 — 요구사항).
    """

    code = "FAKE"

    def remove_background(
        self, request: BackgroundRemovalRequest,
    ) -> BackgroundRemovalResult:

        import io

        from PIL import Image

        try:
            img = Image.open(io.BytesIO(request.source_image_bytes)).convert("RGBA")
        except Exception as exc:  # noqa: BLE001
            return BackgroundRemovalResult(
                status="FAILED",
                image_bytes=None,
                provider_code=self.code,
                model_version=None,
                error_reason=f"이미지를 열 수 없습니다: {type(exc).__name__}",
            )

        out = io.BytesIO()
        img.save(out, format="PNG")

        return BackgroundRemovalResult(
            status="SUCCEEDED",
            image_bytes=out.getvalue(),
            provider_code=self.code,
            model_version="fake-passthrough-v1",
        )


class RembgBackgroundRemovalProvider(BackgroundRemovalProvider):
    """
    실제 로컬 ML 기반 배경 제거(rembg + onnxruntime, u2netp 모델).

    2026-09-06 명시적 실행 확인 완료 — 실제로 호출해 모델을 내려받고
    (u2netp, 약 4.57MB, rembg 공식 GitHub Release에서 받음 — 유료 API
    아님) 배경 제거가 성공(SUCCEEDED)함을 확인했다. 1회 호출에 CPU
    추론 기준 약 9~14초 소요된다(모델 캐시 후에도 비슷함 — 다운로드
    시간이 아니라 추론 자체의 소요 시간). 이 Provider는 요청을 동기
    적으로 처리하므로(`job_queue_service.py::remove_background`가
    호출 즉시 결과를 기다림 — 실제 백그라운드 워커로 넘기지 않음),
    호출 중 HTTP 워커 스레드가 그 시간만큼 점유된다 — 콘솔의 수동
    "누끼 따기" 버튼 클릭처럼 저빈도 사용에는 허용 가능하지만, 대량
    일괄 처리에는 부적합하다(향후 진짜 비동기 큐가 필요해지면 별도
    검토).
    """

    code = "REMBG"
    MODEL_VERSION = "u2netp"

    def remove_background(
        self, request: BackgroundRemovalRequest,
    ) -> BackgroundRemovalResult:

        try:
            from rembg import new_session
            from rembg import remove
        except ImportError as exc:
            raise BackgroundRemovalProviderError(
                "rembg가 설치되어 있지 않습니다(requirements.txt 확인 필요).",
            ) from exc

        try:
            session = new_session(self.MODEL_VERSION)
            result_bytes = remove(request.source_image_bytes, session=session)

        except Exception as exc:  # noqa: BLE001
            return BackgroundRemovalResult(
                status="FAILED",
                image_bytes=None,
                provider_code=self.code,
                model_version=self.MODEL_VERSION,
                error_reason=f"배경 제거 실패: {type(exc).__name__}",
            )

        return BackgroundRemovalResult(
            status="SUCCEEDED",
            image_bytes=result_bytes,
            provider_code=self.code,
            model_version=self.MODEL_VERSION,
        )


class DisabledBackgroundRemovalProvider(BackgroundRemovalProvider):

    code = "DISABLED"

    def remove_background(
        self, request: BackgroundRemovalRequest,
    ) -> BackgroundRemovalResult:

        raise BackgroundRemovalProviderError(
            "배경 제거 Provider가 연결되지 않았습니다.",
        )


PROVIDERS_BY_CODE: dict[str, BackgroundRemovalProvider] = {
    "FAKE": FakeBackgroundRemovalProvider(),
    "REMBG": RembgBackgroundRemovalProvider(),
    "DISABLED": DisabledBackgroundRemovalProvider(),
}


def get_background_removal_provider(code: str) -> BackgroundRemovalProvider:

    provider = PROVIDERS_BY_CODE.get(code)
    if provider is None:
        raise BackgroundRemovalProviderError(f"알 수 없는 Provider입니다: {code}")

    return provider


__all__ = [
    "BackgroundRemovalRequest",
    "BackgroundRemovalResult",
    "BackgroundRemovalProviderError",
    "BackgroundRemovalProvider",
    "FakeBackgroundRemovalProvider",
    "RembgBackgroundRemovalProvider",
    "DisabledBackgroundRemovalProvider",
    "PROVIDERS_BY_CODE",
    "get_background_removal_provider",
]
