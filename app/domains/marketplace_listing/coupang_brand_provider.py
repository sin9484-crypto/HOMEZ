"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/coupang_brand_provider.py

2026-08-30 V7 안정화 Phase 3 — 브랜드 3상태 계약(감사 F-02)의
OFFICIAL_BRAND 조회 부분. 판매자 커뮤니티의 "[브랜드 없음]" 표기
관행은 공식 계약이 아니므로 그대로 구현하지 않는다(이 지시의 명시적
정정) — 이 파일은 NO_BRAND를 다루지 않는다(그건 사용자가 직접 확인해
저장한 값을 그대로 쓴다, required_fields_schemas.py/
coupang_submission_contract.py 참고). 여기서는 OFFICIAL_BRAND 확정에
필요한 브랜드 검색(brandId/공식 브랜드명)만 다룬다.

2026-09-27 후속 — 실제 쿠팡 브랜드 검색 API가 공식 문서화돼 있음을
확인했다(확인 출처: https://developers.coupang.com/hc/ko/articles/
58230017410841-브랜드-검색, 2026-09-27 fetch):
  POST /v2/providers/seller_api/apis/api/v1/marketplace/brands/search
  요청 body: {"brandName": str, "countPerPage": int, "page": int}
  응답: {"data": {"items": [{"brandId","brandName","brandLogoUrl",
         "isUIDRequired","allowedUIDTypes"}], "totalCount": int}}
기존 출고지/카테고리 조회와 동일한 HMAC 인증(coupang_signing.py)을
그대로 재사용한다 — 별도 인증 방식이 아니다. `CoupangLiveBrandProvider`
가 이 Protocol의 실제 구현이다(listing_wizard_router.py가 기존
_coupang_metadata_provider()와 동일한 패턴으로 생성한다).

이 공식 응답에는 "Enrollment(입점) 상태" 필드가 없다 — 이전 버전
주석이 언급한 "Brand Enrollment 경고"는 이 API가 아니라 다른 화면에서
관찰된 사실이었다. 그래서 실제 조회 결과는 항상 enrollment_status=
"UNKNOWN"으로 채운다(모르는 값을 ENROLLED로 임의 가정하지 않는다는
기존 원칙 유지) — Fake Provider의 테스트 전용 ENROLLED/NOT_ENROLLED
값은 그대로 둔다(실제 API 계약과 무관, 격리 테스트용).
=========================================================
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from app.domains.store_connection.adapters.coupang_signing import (
    build_authorization_header,
)

BASE_URL = "https://api-gateway.coupang.com"
SEARCH_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/brands/search"


@dataclass(frozen=True)
class BrandSearchResult:
    """브랜드 검색 결과 1건 — 화면이 사용자에게 선택지로 보여주는 모양."""

    brand_id: str
    official_brand_name: str
    # 확인된 값(ENROLLED/NOT_ENROLLED)만 넣는다 — 모르면 "UNKNOWN"이지
    # 임의로 ENROLLED를 가정하지 않는다(실제 Live 검증에서 Brand
    # Enrollment 경고가 나온 사례가 있었다 — 감사 배경).
    enrollment_status: str
    # 2026-09-27 — 공식 응답의 isUIDRequired/allowedUIDTypes를 그대로
    # 보존한다(이 상품이 GTIN/MPN 같은 고유식별자를 추가로 요구하는지
    # 화면에서 바로 알 수 있게). 이번 라운드에서 새 차단 로직을 만들지
    # 않는다 — 표시만 한다.
    is_uid_required: bool = False
    allowed_uid_types: tuple[str, ...] = ()


class CoupangBrandProvider(Protocol):
    def search_brand(self, query: str) -> list[BrandSearchResult]: ...


class CoupangBrandProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CoupangLiveBrandProvider:
    """실제 쿠팡 Open API 브랜드 검색. 기존 CoupangCategoryMetadataProvider
    와 동일한 서명·요청 패턴(coupang_signing.py)을 그대로 재사용한다."""

    def __init__(self, credential: dict, timeout: int = 10):
        self.access_key = credential.get("access_key", "")
        self.secret_key = credential.get("secret_key", "")
        if not self.access_key or not self.secret_key:
            raise CoupangBrandProviderError("쿠팡 API 자격증명이 준비되지 않았습니다.")
        self.timeout = timeout

    def _request(self, body: dict) -> dict:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        auth = build_authorization_header(
            self.access_key, self.secret_key, "POST", SEARCH_PATH, "",
            now=datetime.now(timezone.utc),
        )
        request = urllib.request.Request(
            BASE_URL + SEARCH_PATH, data=encoded, method="POST",
            headers={
                "Authorization": auth,
                "Content-Type": "application/json;charset=UTF-8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 상태 코드만 노출한다 — 응답 본문·헤더·인증정보는 절대
            # 포함하지 않는다(기존 카테고리 조회 Provider와 동일 원칙).
            raise CoupangBrandProviderError(
                f"쿠팡 브랜드 검색에 실패했습니다 (HTTP {exc.code}).", exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise CoupangBrandProviderError(
                "쿠팡 브랜드 검색 서비스에 연결하지 못했습니다.",
            ) from exc

    def search_brand(self, query: str) -> list[BrandSearchResult]:
        payload = self._request({"brandName": query, "countPerPage": 10, "page": 1})

        data = payload.get("data") or {}
        items = data.get("items") or []
        results: list[BrandSearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            brand_id = str(item.get("brandId") or "").strip()
            brand_name = str(item.get("brandName") or "").strip()
            if not brand_id or not brand_name:
                continue
            uid_types = item.get("allowedUIDTypes") or []
            results.append(BrandSearchResult(
                brand_id=brand_id,
                official_brand_name=brand_name,
                enrollment_status="UNKNOWN",
                is_uid_required=bool(item.get("isUIDRequired")),
                allowed_uid_types=tuple(
                    str(t) for t in uid_types if isinstance(t, str)
                ),
            ))
        return results


class FakeCoupangBrandProvider:
    """
    테스트 전용 — 실제 쿠팡 브랜드 검색 API를 호출하지 않는다. 호출
    기록(calls)을 남겨 "실제 Provider가 호출된 적이 없다"를 테스트가
    직접 확인할 수 있게 한다(필수 테스트 15번: 실제 Provider가 테스트
    에서 절대 호출되지 않는다).
    """

    def __init__(self, results: dict[str, list[BrandSearchResult]] | None = None):
        self._results = dict(results or {})
        self.calls: list[str] = []

    def search_brand(self, query: str) -> list[BrandSearchResult]:
        self.calls.append(query)
        return list(self._results.get(query, []))


def brand_lookup_fingerprint(query: str, result: BrandSearchResult) -> str:
    """
    사용자가 실제로 선택한 검색 결과를 고정하는 fingerprint. 저장된
    brandId/officialBrandName이 이 조회 결과에서 나온 것인지(다른
    경로로 값만 위조해 넣지 않았는지) coupang_submission_contract.py가
    아니라 승인 재검증 단계에서 대조할 수 있게 한다 — 이번 세션
    범위(Phase 3)는 fingerprint 계산 자체만 제공하고, 승인 재검증에
    실제로 연결하는 것은 이후 작업이다(완료 보고에 명시).
    """

    encoded = json.dumps(
        {
            "query": query, "brand_id": result.brand_id,
            "official_brand_name": result.official_brand_name,
            "enrollment_status": result.enrollment_status,
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BrandSearchResult",
    "CoupangBrandProvider",
    "CoupangBrandProviderError",
    "CoupangLiveBrandProvider",
    "FakeCoupangBrandProvider",
    "brand_lookup_fingerprint",
]
