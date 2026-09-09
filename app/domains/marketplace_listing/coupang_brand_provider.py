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
필요한 브랜드 검색(brandId/공식 브랜드명/Enrollment 상태)만 다룬다.

실제 쿠팡 브랜드 검색 API 연동은 이번 세션 범위 밖이다(이번 세션
안전 조건 — 추가 실제 상품 생성 POST 금지와 별개로, 브랜드 검색
자체도 아직 실사·연동 검증을 거치지 않았다). 이 파일은 Fake Provider
계약을 먼저 정의하고 그 계약대로 테스트한다 — 실제 Provider는 이
Protocol을 구현하는 별도 클래스로 나중에 추가한다(예:
coupang_live_provider.py의 CoupangLiveProductProvider와 동일한 패턴).
=========================================================
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class BrandSearchResult:
    """브랜드 검색 결과 1건 — 화면이 사용자에게 선택지로 보여주는 모양."""

    brand_id: str
    official_brand_name: str
    # 확인된 값(ENROLLED/NOT_ENROLLED)만 넣는다 — 모르면 "UNKNOWN"이지
    # 임의로 ENROLLED를 가정하지 않는다(실제 Live 검증에서 Brand
    # Enrollment 경고가 나온 사례가 있었다 — 감사 배경).
    enrollment_status: str


class CoupangBrandProvider(Protocol):
    def search_brand(self, query: str) -> list[BrandSearchResult]: ...


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
    "FakeCoupangBrandProvider",
    "brand_lookup_fingerprint",
]
