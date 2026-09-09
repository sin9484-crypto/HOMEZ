"""
=========================================================
Homez OS

File : app/domains/product_candidate/router.py

V3 운영자 승인 API

후보 목록/상세/근거 조회 + 승인/보류/거절만 제공한다. 실제 상품 등록이나
구매를 실행하지 않으며, 외부 마켓 등록 Adapter를 호출하지 않는다.

2026-08-14 테넌트 격리 감사(Gate R13) — ProductCandidate 자체(원본
발견 카탈로그)는 전역으로 유지하지만, 승인/보류/거절은 이제
current_user.company_id로 스코프된다. 응답의 company_status 필드가
"이 회사 관점의 현재 상태"를 담는다(status는 전역 워크플로우 상태로
의미가 좁혀졌다).
=========================================================
"""

import uuid

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import CredentialStoreError
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.schema import (
    NaverDataLabCredentialSaveRequest,
)
from app.domains.product_candidate.schema import (
    NaverDataLabCredentialStatusResponse,
)
from app.domains.product_candidate.schema import (
    ProductCandidateDecisionRequest,
)
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.schema import ProductCandidateResponse
from app.domains.product_candidate.schema import TrendAnalysisRefreshResponse
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.product_candidate.trend_analysis_refresh_service import (
    TrendAnalysisRefreshService,
)
from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter
from app.domains.user.model import User


def get_trend_credential_store() -> CredentialStore:
    return WindowsCredentialStore()

router = APIRouter(
    prefix="/product-candidates",
    tags=["Product Candidate"],
)


def _to_response(
    service: ProductCandidateService,
    candidate: ProductCandidate,
    company_id: int,
    company_status_override: str | None = None,
) -> ProductCandidateResponse:

    company_status = (
        company_status_override
        if company_status_override is not None
        else service.get_effective_status_for_company(
            candidate.id, company_id,
        )
    )

    return ProductCandidateResponse(
        id=candidate.id,
        candidate_key=candidate.candidate_key,
        source_type=candidate.source_type,
        source_reference=candidate.source_reference,
        market=candidate.market,
        product_name=candidate.product_name,
        category_hint=candidate.category_hint,
        brand_hint=candidate.brand_hint,
        discovered_at=candidate.discovered_at,
        release_date=candidate.release_date,
        is_new_product=candidate.is_new_product,
        trend_score=candidate.trend_score,
        novelty_score=candidate.novelty_score,
        demand_score=candidate.demand_score,
        competition_score=candidate.competition_score,
        margin_score=candidate.margin_score,
        risk_score=candidate.risk_score,
        confidence=candidate.confidence,
        evidence_summary=candidate.evidence_summary,
        status=candidate.status,
        company_status=company_status,
        visibility=candidate.visibility,
        owner_company_id=candidate.owner_company_id,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


@router.get(
    "",
    response_model=list[ProductCandidateResponse],
)
def list_candidates(
    status: str | None = Query(default=None),
    market: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    status 필터는 여전히 전역 워크플로우 상태(DISCOVERED/ANALYZED/
    RECOMMENDED/EXPIRED) 기준이다 — APPROVED/HELD/REJECTED로 필터링해
    "이 회사가 승인한 것만" 보려면 company_status 필드를 클라이언트에서
    확인해야 한다. 목록 자체는 2026-08-15 V7 Gate 2부터 이 회사가 볼 수
    있는 후보(GLOBAL 전체 + 자사 PRIVATE)로 서버 쿼리 레벨에서
    스코프된다 — 다른 회사의 PRIVATE 후보는 아예 나타나지 않는다.
    """

    service = ProductCandidateService(db)

    candidates = service.list_candidates_for_company(
        current_user.company_id,
        status=status, market=market, skip=skip, limit=limit,
    )

    return [
        _to_response(service, candidate, current_user.company_id)
        for candidate in candidates
    ]


@router.post(
    "/private",
    response_model=ProductCandidateResponse,
)
def register_private_candidate(
    data: ProductCandidatePrivateCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    회사가 직접 등록하는 비공개(PRIVATE) 후보(2026-08-15 V7 Gate 2,
    신규). 다른 회사에게는 존재 자체가 노출되지 않는다.
    """

    service = ProductCandidateService(db)
    candidate, _events = service.register_private_candidate(
        data,
        company_id=current_user.company_id,
        correlation_id=str(uuid.uuid4()),
    )

    return _to_response(service, candidate, current_user.company_id)


@router.get(
    "/system/naver-datalab-credential",
    response_model=NaverDataLabCredentialStatusResponse,
)
def get_naver_datalab_credential_status(
    current_user: User = Depends(admin_guard),
    credential_store: CredentialStore = Depends(get_trend_credential_store),
):
    """
    등록 여부만 알려준다 — Client ID/Secret 값 자체는 어떤 응답에도
    담지 않는다. 정적 경로이므로 `/{candidate_id}`보다 먼저 등록한다
    (`/exception-analysis`류와 동일한 기존 관례 — 그렇지 않으면
    "system"이 candidate_id로 오인돼 422가 난다).
    """

    try:
        credential_store.read(NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE)
        registered = True
    except (CredentialNotFoundError, CredentialStoreError):
        registered = False

    return NaverDataLabCredentialStatusResponse(registered=registered)


@router.post(
    "/system/naver-datalab-credential",
    response_model=NaverDataLabCredentialStatusResponse,
)
def save_naver_datalab_credential(
    data: NaverDataLabCredentialSaveRequest,
    current_user: User = Depends(admin_guard),
    credential_store: CredentialStore = Depends(get_trend_credential_store),
):
    """
    2026-09-06 신규 — 네이버 개발자센터에서 발급받은 Client ID/Secret을
    Windows Credential Manager에 저장한다(평문 DB 저장 금지 원칙,
    store_connection과 동일 패턴). 이후 NaverDataLabTrendAdapter가
    이 값을 읽어 실제 API를 호출한다.
    """

    credential_store.save(
        NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE,
        {"client_id": data.client_id, "client_secret": data.client_secret},
    )

    return NaverDataLabCredentialStatusResponse(registered=True)


@router.get(
    "/{candidate_id}",
    response_model=ProductCandidateResponse,
)
def get_candidate(
    candidate_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ProductCandidateService(db)
    candidate = service.get_visible_for_company(
        candidate_id, current_user.company_id,
    )

    return _to_response(service, candidate, current_user.company_id)


@router.get(
    "/{candidate_id}/evidence",
)
def get_candidate_evidence(
    candidate_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    수집 원본·AI 분석 근거 조회(append-only 이력 전체). GLOBAL 후보는
    전역이지만, PRIVATE 후보의 근거는 소유 회사만 볼 수 있다
    (2026-08-15 V7 Gate 2).
    """

    service = ProductCandidateService(db)
    rows = service.list_evidence(candidate_id, current_user.company_id)

    return [
        {
            "id": row.id,
            "evidence_type": row.evidence_type,
            "payload_summary": row.payload_summary,
            "score": row.score,
            "confidence": row.confidence,
            "recorded_at": row.recorded_at,
        }
        for row in rows
    ]


@router.get(
    "/{candidate_id}/decisions",
)
def get_candidate_decisions(
    candidate_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """이 회사가 이 후보에 대해 남긴 운영자 결정 이력만 조회한다."""

    service = ProductCandidateService(db)
    rows = service.list_decisions(candidate_id, current_user.company_id)

    return [
        {
            "id": row.id,
            "action": row.action,
            "operator_id": row.operator_id,
            "memo": row.memo,
            "decided_at": row.decided_at,
        }
        for row in rows
    ]


@router.post(
    "/{candidate_id}/analyze",
    response_model=ProductCandidateResponse,
)
def verify_candidate_info(
    candidate_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    직접 등록(PRIVATE_MANUAL)한 후보를 DISCOVERED에서 ANALYZED로
    전환하는 사용자용 엔드포인트다(2026-08-19 V7 워크플로우 완성,
    2026-08-19 CTO 보완 지시로 추측성 점수 제거, 정책 A 채택으로
    ANALYZED="시스템 검토 완료, 관리자 판단 가능"으로 공식 정의됨 —
    constants.py::CandidateStatus 참고). "AI 분석"이 아니라 기본
    정보가 등록되어 있음을 확인하는 절차이며, 점수를 계산하거나
    저장하지 않는다 — service.verify_private_candidate_info()의
    docstring에 그 사실을 명시한다. URL 경로는 기존 클라이언트/테스트
    호환을 위해 "/analyze"를 그대로 유지한다(내부 식별자, 사용자
    노출 문구는 console.js/i18n 쪽에서 "정보 확인"으로 별도 표기).
    get_visible_for_company()와 동일한 회사 격리를 거치므로 다른
    회사의 PRIVATE 후보는 404다.
    """

    service = ProductCandidateService(db)
    candidate, _events = service.verify_private_candidate_info(
        candidate_id,
        company_id=current_user.company_id,
        correlation_id=str(uuid.uuid4()),
        actor_user_id=current_user.id,
    )

    return _to_response(service, candidate, current_user.company_id)


@router.post(
    "/{candidate_id}/refresh-trend-analysis",
    response_model=TrendAnalysisRefreshResponse,
)
def refresh_trend_analysis(
    candidate_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_trend_credential_store),
):
    """
    2026-09-06 신규 — 네이버 데이터랩 공식 API로 이 후보의 실제
    트렌드 점수를 갱신한다(trend_discovery.adapter.
    NaverDataLabTrendAdapter, Fixture가 아닌 첫 실 데이터 연동).
    자격증명이 아직 등록되지 않았거나 API 호출이 실패하면 아무것도
    바꾸지 않고 status=NO_SIGNAL을 그대로 반환한다 — 실패를 성공으로
    위장하지 않는다. company_id 격리는
    TrendAnalysisRefreshService.refresh() 내부의
    get_visible_for_company()가 강제한다.
    """

    adapter = NaverDataLabTrendAdapter(
        credential_store, NaverDataLabTrendAdapter.DEFAULT_CREDENTIAL_REFERENCE,
    )
    service = TrendAnalysisRefreshService(db, adapter)
    result = service.refresh(candidate_id, current_user.company_id)

    return TrendAnalysisRefreshResponse(
        status=result.status, candidate_id=result.candidate_id,
        trend_score=result.trend_score, confidence=result.confidence,
        reason=result.reason,
    )


@router.post(
    "/{candidate_id}/recommend",
    response_model=ProductCandidateResponse,
)
def recommend_candidate(
    candidate_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    ANALYZED 상태의 후보를 RECOMMENDED로 전환한다(2026-08-19 V7
    워크플로우 완성). 2026-08-19 CTO 보완 지시 이후 service.recommend()
    자체가 company_id를 필수로 받아 get_visible_for_company()로 회사
    격리를 시행한다 — Service가 이제 유일한 강제 지점이다. 이 라우터의
    사전 get_visible_for_company() 호출은 그 위에 얹는 방어적 중복
    검증으로 유지한다(Service만 믿지 않는 이중 방어). 정책 A 채택
    이후 ANALYZED만으로는 실제 추천 근거를 보장하지 않으므로,
    service.recommend()는 trend_score/novelty_score가 실제로 존재할
    때만 통과시킨다(근거 없는 후보는 400) — UI 버튼 숨김과는 별개의
    API 레벨 강제.
    """

    service = ProductCandidateService(db)
    service.get_visible_for_company(candidate_id, current_user.company_id)

    candidate, _events = service.recommend(
        candidate_id,
        company_id=current_user.company_id,
        correlation_id=str(uuid.uuid4()),
    )

    return _to_response(service, candidate, current_user.company_id)


@router.post(
    "/{candidate_id}/approve",
    response_model=ProductCandidateResponse,
)
def approve_candidate(
    candidate_id: int,
    data: ProductCandidateDecisionRequest | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    이 회사 관점의 승인 상태만 APPROVED로 변경하고 Event를 기록한다.
    다른 회사의 이 후보에 대한 판단에는 영향을 주지 않는다(2026-08-14
    테넌트 격리 감사). 상품 등록이나 구매를 실행하지 않는다.
    """

    service = ProductCandidateService(db)
    new_status, _events = service.approve(
        candidate_id,
        company_id=current_user.company_id,
        operator_id=current_user.id,
        is_admin=True,
        memo=data.memo if data else None,
        correlation_id=str(uuid.uuid4()),
    )

    candidate = service.get_visible_for_company(
        candidate_id, current_user.company_id,
    )

    return _to_response(
        service, candidate, current_user.company_id, new_status,
    )


@router.post(
    "/{candidate_id}/hold",
    response_model=ProductCandidateResponse,
)
def hold_candidate(
    candidate_id: int,
    data: ProductCandidateDecisionRequest | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ProductCandidateService(db)
    new_status, _events = service.hold(
        candidate_id,
        company_id=current_user.company_id,
        operator_id=current_user.id,
        is_admin=True,
        memo=data.memo if data else None,
        correlation_id=str(uuid.uuid4()),
    )

    candidate = service.get_visible_for_company(
        candidate_id, current_user.company_id,
    )

    return _to_response(
        service, candidate, current_user.company_id, new_status,
    )


@router.post(
    "/{candidate_id}/reject",
    response_model=ProductCandidateResponse,
)
def reject_candidate(
    candidate_id: int,
    data: ProductCandidateDecisionRequest | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ProductCandidateService(db)
    new_status, _events = service.reject(
        candidate_id,
        company_id=current_user.company_id,
        operator_id=current_user.id,
        is_admin=True,
        memo=data.memo if data else None,
        correlation_id=str(uuid.uuid4()),
    )

    candidate = service.get_visible_for_company(
        candidate_id, current_user.company_id,
    )

    return _to_response(
        service, candidate, current_user.company_id, new_status,
    )


__all__ = [
    "router",
]
