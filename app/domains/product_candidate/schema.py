"""
=========================================================
Homez OS

File : app/domains/product_candidate/schema.py

V3 ProductCandidate Schema
=========================================================
"""

from datetime import date
from datetime import datetime

from pydantic import BaseModel
from pydantic import Field


class ProductCandidateDiscover(BaseModel):

    source_type: str
    source_reference: str
    market: str
    product_name: str
    category_hint: str | None = None
    brand_hint: str | None = None
    release_date: date | None = None


class ProductCandidatePrivateCreate(BaseModel):
    """
    비공개(PRIVATE) 후보 등록 요청(2026-08-15 V7 Gate 2, 신규 기능).
    company_id는 요청 바디로 받지 않는다 — Router가 항상
    current_user.company_id를 owner_company_id로 사용한다.
    """

    source_type: str | None = None
    source_reference: str
    market: str
    product_name: str
    category_hint: str | None = None
    brand_hint: str | None = None
    release_date: date | None = None


class ProductCandidateDecisionRequest(BaseModel):

    memo: str | None = None


class ProductCandidateResponse(BaseModel):

    id: int
    candidate_key: str
    source_type: str
    source_reference: str
    market: str
    product_name: str
    category_hint: str | None
    brand_hint: str | None
    discovered_at: datetime
    release_date: date | None
    is_new_product: bool | None
    trend_score: float | None
    novelty_score: float | None
    demand_score: float | None
    competition_score: float | None
    margin_score: float | None
    risk_score: float | None
    confidence: float | None
    evidence_summary: str | None
    # 전역 AI/추천 워크플로우 상태(DISCOVERED/ANALYZED/RECOMMENDED/
    # EXPIRED) — 2026-08-14 테넌트 격리 감사 이후 APPROVED/HELD/
    # REJECTED는 이 필드에 더 이상 나타나지 않는다.
    status: str
    # 이 API를 호출한 회사 관점의 현재 상태(status와 동일하거나,
    # 이 회사가 이미 승인/보류/거절했다면 APPROVED/HELD/REJECTED).
    company_status: str
    # GLOBAL(전역 공유 카탈로그) | PRIVATE(회사 전용, 2026-08-15
    # V7 Gate 2 신규).
    visibility: str
    owner_company_id: int | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class NaverDataLabCredentialSaveRequest(BaseModel):
    """
    2026-09-06 — 네이버 데이터랩 Client ID/Secret을 Windows Credential
    Manager에 등록한다. 이 회사만의 것이 아니라 홈즈 설치 전체가
    공유하는 시스템 레벨 자격증명이다(네이버 개발자센터 애플리케이션은
    계정 1개당 발급되므로 회사별로 나눌 대상이 아님).
    """

    client_id: str = Field(min_length=1, max_length=200)
    client_secret: str = Field(min_length=1, max_length=200)


class NaverDataLabCredentialStatusResponse(BaseModel):

    registered: bool


class TrendAnalysisRefreshResponse(BaseModel):
    """
    2026-09-06 — 실 네이버 데이터랩 API 기반 트렌드 재분석 응답.
    status=NO_SIGNAL이면 아무것도 바뀌지 않았다는 뜻이다(reason 참고).
    """

    status: str  # APPLIED / NO_SIGNAL
    candidate_id: int
    trend_score: float | None = None
    confidence: float | None = None
    reason: str | None = None


__all__ = [
    "ProductCandidateDiscover",
    "ProductCandidatePrivateCreate",
    "ProductCandidateDecisionRequest",
    "ProductCandidateResponse",
    "TrendAnalysisRefreshResponse",
    "NaverDataLabCredentialSaveRequest",
    "NaverDataLabCredentialStatusResponse",
]
