"""
=========================================================
Homez OS

File : app/domains/media_asset/image_search_providers.py

인터넷 이미지 검색 Provider 경계 — 2026-08-20 CTO 지시 Section 3.

FakeImageSearchProvider는 네트워크 호출이 전혀 없는 결정론적 더미
결과만 반환한다 — 이 결과를 실제 검색 완료로 보고하지 않는다.
NaverImageSearchProvider(2026-09-07 추가)는 NAVER API HUB 공식
이미지 검색 API(IMAGE_SEARCH_PROVIDER_DECISION_REQUIRED 해소 —
공식 API·이용약관이 확인된 Provider로 결정됨)로 실제 검색 결과를
가져오는 첫 실 Provider다.

PermissionStatus.VERIFIED_ALLOWED만 선택·다운로드가 기본 허용된다
(요구사항) — 나머지는 미리보기·출처 확인까지만 허용한다는 정책을
이 모듈의 데이터 모델 자체로 표현한다(서비스/라우터가 이 값을 보고
차단해야 한다 — 이 파일은 값만 정의). NaverImageSearchProvider도
검색 결과가 실제로 재사용 허가된 이미지인지 개별 확인하지 않았으므로
언제나 UNKNOWN을 반환한다 — "네이버가 찾아줬다"가 "상업적으로 써도
된다"를 의미하지 않는다(과장 금지 원칙, FakeImageSearchProvider와
동일).
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.core.api_usage_tracker import ApiUsageTracker
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError


class ImagePermissionStatus:

    VERIFIED_ALLOWED = "VERIFIED_ALLOWED"
    SUPPLIER_PERMISSION_REQUIRED = "SUPPLIER_PERMISSION_REQUIRED"
    MANUFACTURER_PERMISSION_REQUIRED = "MANUFACTURER_PERMISSION_REQUIRED"
    UNKNOWN = "UNKNOWN"
    PROHIBITED = "PROHIBITED"

    ALL = (
        VERIFIED_ALLOWED, SUPPLIER_PERMISSION_REQUIRED,
        MANUFACTURER_PERMISSION_REQUIRED, UNKNOWN, PROHIBITED,
    )

    # 선택·다운로드(등록 이미지로 저장) 가능한 상태 — 그 외에는
    # 미리보기·출처 확인까지만 허용한다.
    SELECTABLE = (VERIFIED_ALLOWED,)


@dataclass(frozen=True)
class ImageSearchQuery:

    product_name: str
    brand: str | None = None
    scent: str | None = None
    volume: str | None = None
    composition: str | None = None
    barcode: str | None = None
    supplier_sku: str | None = None


@dataclass(frozen=True)
class ImageSearchResultItem:

    title: str
    source_page_url: str
    preview_image_url: str
    provider_code: str
    source_site: str
    width: int | None
    height: int | None
    file_format: str | None
    searched_at: datetime
    match_status: str  # LIKELY_MATCH / UNCERTAIN / NO_MATCH
    permission_status: str  # ImagePermissionStatus


class ImageSearchProviderError(Exception):
    """Provider 자체가 요청을 처리할 수 없을 때."""


class ImageSearchProvider(ABC):

    code: str = "ABSTRACT"

    @abstractmethod
    def search(
        self, query: ImageSearchQuery,
    ) -> list[ImageSearchResultItem]:
        ...


class FakeImageSearchProvider(ImageSearchProvider):
    """
    네트워크 호출 없이 결정론적인 더미 검색 결과를 반환한다. 실제
    검색이 아니므로 permission_status는 항상 UNKNOWN으로 표시해
    선택·다운로드가 기본 차단되게 한다(허위로 VERIFIED_ALLOWED를
    자칭하지 않는다).
    """

    code = "FAKE"

    def search(
        self, query: ImageSearchQuery,
    ) -> list[ImageSearchResultItem]:

        now = datetime.utcnow()

        return [
            ImageSearchResultItem(
                title=f"[시연용 결과] {query.product_name}",
                source_page_url="https://example.invalid/fake-search-result",
                preview_image_url="https://example.invalid/fake-preview.png",
                provider_code=self.code,
                source_site="FAKE_PROVIDER",
                width=None,
                height=None,
                file_format=None,
                searched_at=now,
                match_status="UNCERTAIN",
                permission_status=ImagePermissionStatus.UNKNOWN,
            ),
        ]


class NaverImageSearchProvider(ImageSearchProvider):
    """
    NAVER API HUB 공식 이미지 검색 API — GET /search/v1/image
    (api.ncloud-docs.com/docs/naver-api-hub-search-image, 2026-09-07
    확인). trend_discovery의 NaverDataLabTrendAdapter와 자격증명
    (Client ID/Secret)을 공유한다 — "하나의 API 키로 검색어 트렌드·
    이미지 검색을 모두 호출할 수 있다"는 공식 안내에 따른 것이다.

    반환하는 모든 결과의 permission_status는 언제나 UNKNOWN이다 —
    검색 결과가 실제로 상업적 재사용이 허가된 이미지인지는 이
    Provider가 판단하지 않는다(운영자가 출처 페이지를 직접 확인해야
    한다). match_status도 언제나 UNCERTAIN이다(네이버가 "정확히 이
    상품"이라고 보장하지 않음, 키워드 매칭 결과일 뿐).
    """

    code = "NAVER"
    ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/image"
    REQUEST_TIMEOUT_SECONDS = 10
    DISPLAY_COUNT = 20
    USAGE_BUCKET = "naver_search_image"
    # 2026-09-07 사용자 확정 — 검색 계열 API(이미지 포함) 합산 무료
    # 할당량.
    DAILY_CALL_LIMIT = 25000
    MONTHLY_CALL_LIMIT = 775000

    def __init__(
        self,
        credential_store: CredentialStore,
        credential_reference: str,
        *,
        http_get: Callable | None = None,
        usage_tracker: ApiUsageTracker | None = None,
    ):
        self.credential_store = credential_store
        self.credential_reference = credential_reference
        if http_get is not None:
            self._http_get = http_get
        else:
            import requests

            self._http_get = requests.get
        if usage_tracker is not None:
            self.usage_tracker = usage_tracker
        else:
            from app.desktop.paths import get_config_dir

            self.usage_tracker = ApiUsageTracker(
                get_config_dir() / "naver_api_hub_usage.json",
            )

    def _build_query(self, query: ImageSearchQuery) -> str:

        parts = [query.product_name]
        if query.brand:
            parts.append(query.brand)
        return " ".join(p for p in parts if p).strip()

    def search(
        self, query: ImageSearchQuery,
    ) -> list[ImageSearchResultItem]:
        """
        자격증명 없음/한도 초과/네트워크 실패/응답 이상 등 어떤
        이유로든 실제 검색이 불가능하면 빈 목록을 반환한다(예외를
        던지지 않는다 — FakeImageSearchProvider와 달리 이 Provider는
        실패했다고 더미 카드를 보여주지 않는다, 빈 목록이 "결과 없음"
        과 "실패"를 구분하지 않는 이유는 어느 쪽이든 운영자가 할 일은
        "재시도하거나 검색어를 바꾸는 것"으로 동일하기 때문이다).
        """

        search_text = self._build_query(query)
        if not search_text:
            return []

        limit_check = self.usage_tracker.is_within_limit(
            self.USAGE_BUCKET,
            daily_limit=self.DAILY_CALL_LIMIT,
            monthly_limit=self.MONTHLY_CALL_LIMIT,
        )
        if not limit_check.within_limit:
            return []

        try:
            credential = self.credential_store.read(self.credential_reference)
        except (CredentialNotFoundError, CredentialStoreError):
            return []

        client_id = str(credential.get("client_id", "")).strip()
        client_secret = str(credential.get("client_secret", "")).strip()
        if not client_id or not client_secret:
            return []

        self.usage_tracker.record_call(self.USAGE_BUCKET)

        try:
            response = self._http_get(
                self.ENDPOINT,
                headers={
                    "X-NCP-APIGW-API-KEY-ID": client_id,
                    "X-NCP-APIGW-API-KEY": client_secret,
                },
                params={
                    "query": search_text,
                    "display": self.DISPLAY_COUNT,
                    "sort": "sim",
                },
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — 네트워크 실패는 fail-closed로 빈 목록
            return []

        if response.status_code != 200:
            return []

        try:
            payload = response.json()
            raw_items = payload.get("items") or []
        except (ValueError, AttributeError):
            return []

        now = datetime.utcnow()
        results = []
        for raw in raw_items:
            try:
                title = str(raw["title"])
                link = str(raw["link"])
                thumbnail = str(raw["thumbnail"])
            except (KeyError, TypeError):
                continue  # 필수 필드 없는 항목은 조용히 건너뛴다(전체를 실패시키지 않음)

            width = raw.get("sizewidth")
            height = raw.get("sizeheight")
            results.append(ImageSearchResultItem(
                title=title,
                source_page_url=link,
                preview_image_url=thumbnail,
                provider_code=self.code,
                source_site="NAVER",
                width=int(width) if width else None,
                height=int(height) if height else None,
                file_format=None,
                searched_at=now,
                match_status="UNCERTAIN",
                permission_status=ImagePermissionStatus.UNKNOWN,
            ))

        return results


class DisabledImageSearchProvider(ImageSearchProvider):

    code = "DISABLED"

    def search(
        self, query: ImageSearchQuery,
    ) -> list[ImageSearchResultItem]:

        raise ImageSearchProviderError(
            "실제 이미지 검색 Provider가 연결되지 않았습니다 "
            "(IMAGE_SEARCH_PROVIDER_DECISION_REQUIRED) — 대신 사용 허가된 "
            "이미지 URL을 직접 추가하거나 실제 제품 사진을 업로드해 주세요.",
        )


def _build_naver_image_search_provider() -> NaverImageSearchProvider:

    from app.core.windows_credential_store import WindowsCredentialStore

    # "homez_naver_api_hub"는 app/domains/trend_discovery/adapter.py::
    # NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE와 동일한
    # 문자열이어야 한다(같은 API 키를 공유) — 도메인 간 import를
    # 피하려고 문자열을 여기 다시 적었으므로, 한쪽을 바꾸면 반드시
    # 다른 쪽도 같이 바꿔야 한다(tests/test_naver_image_search_
    # provider.py가 이 일치를 검증한다).
    return NaverImageSearchProvider(
        WindowsCredentialStore(), "homez_naver_api_hub",
    )


PROVIDERS_BY_CODE: dict[str, ImageSearchProvider] = {
    "FAKE": FakeImageSearchProvider(),
    "DISABLED": DisabledImageSearchProvider(),
    "NAVER": _build_naver_image_search_provider(),
}


def get_image_search_provider(code: str) -> ImageSearchProvider:

    provider = PROVIDERS_BY_CODE.get(code)
    if provider is None:
        raise ImageSearchProviderError(f"알 수 없는 Provider입니다: {code}")

    return provider


__all__ = [
    "ImagePermissionStatus",
    "ImageSearchQuery",
    "ImageSearchResultItem",
    "ImageSearchProviderError",
    "ImageSearchProvider",
    "FakeImageSearchProvider",
    "NaverImageSearchProvider",
    "DisabledImageSearchProvider",
    "PROVIDERS_BY_CODE",
    "get_image_search_provider",
]
