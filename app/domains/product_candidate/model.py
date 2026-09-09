"""
=========================================================
Homez OS

File : app/domains/product_candidate/model.py

V3 ProductCandidate — 상품 후보 계약

3계층 분리:
  1) 수집 원본/AI 판단 근거 → ProductCandidateEvidence (append-only)
  2) 운영자 결정 → ProductCandidateDecision (append-only)
  3) 현재 상태 투영 → ProductCandidate (최신 점수·status만 보관하는 현재 상태)

ProductCandidate.status는 운영자 승인 없이 APPROVED로 자동 전환되지
않는다(app/domains/product_candidate/service.py에서 강제). Funding/Order/
Purchase Model을 참조하지 않는다(전부 논리 참조, FK 없음).

2026-08-14 테넌트 격리 감사 — product_candidate 승인 상태 회사별 분리
(설계 Option B 채택):

기존 구조는 ProductCandidate.status 하나가 전역 단일 승인 상태였다
— 회사 A가 approve()하면 candidate_id 하나의 status가 전역으로
APPROVED가 되어, 그 즉시 다른 어떤 회사도 그 후보로 coupang 초안/
marketplace_listing/listing_package를 만들 수 있었다(회사 B가 회사
A의 승인에 무임승차). 이 결함은 marketplace_listing/service.py
docstring에 "잔존 위험(완료로 보고하지 않음)"으로 이미 명시적으로
기록돼 있었다.

ProductCandidate.status는 이제 "AI/시스템 추천 워크플로우 상태"로
의미를 좁힌다 — DISCOVERED → ANALYZED → RECOMMENDED(+EXPIRED)까지만
이 필드가 전이한다. 이 단계까지는 후보에 대한 객관적 사실(발견됨/
검토 완료/추천됨)이라 전역이어도 안전하다.

[정책 A, 2026-08-19 CTO 정책 결정 — 상태 의미 공식 계약]
ANALYZED는 "AI 분석 완료"가 아니라 "시스템 검토 단계를 완료하여
관리자의 판단이 가능한 상태"를 뜻한다. 실제 AI/시장 분석을 거쳤는지,
아니면 수동 등록 후보의 기본 정보 확인만 거쳤는지는 evidence_type과
trend_score/novelty_score의 존재 여부로 구분한다 — 상태 이름 자체로는
구분하지 않는다. RECOMMENDED는 실제 점수·검증된 추천 근거가 있을
때만 도달해야 한다. 전체 계약은 constants.py::CandidateStatus의
docstring이 단일 진실 공급원(single source of truth)이다 — 이
Model docstring은 요약만 제공한다.

승인/보류/거절(회사의 주관적 판단)은 신규 ProductCandidateSelection
(company_id, candidate_id) 당 정확히 1행의 "현재 상태 투영" 테이블로
옮긴다 — CoupangMarketplaceProduct.status가 "현재 상태", Coupang
IntegrationDecision이 "append-only 이력"인 것과 동일한 2계층 패턴을
그대로 재사용한다. UNIQUE(candidate_id, company_id)가 회사 경계와
동시성 가드(조건부 UPDATE의 WHERE 대상)를 동시에 제공한다.
ProductCandidateDecision(append-only 결정 이력)에도 company_id를
추가해 "회사별 승인 이력"으로 전환한다(요청 옵션 A의 요구사항도 함께
충족) — 두 테이블은 append log(Decision) + current projection
(Selection)의 역할 분담이다.

"회사 X 관점에서 candidate Y의 현재 상태"는:
  1) ProductCandidateSelection(candidate_id=Y, company_id=X)가 있으면
     그 행의 status(APPROVED/HELD/REJECTED).
  2) 없으면 ProductCandidate.status(그 회사가 아직 결정하지 않음 —
     DISCOVERED/ANALYZED/RECOMMENDED/EXPIRED 중 하나).

coupang/marketplace_listing/listing_package가 기존에
"ProductCandidate.status == APPROVED"로 게이트하던 지점은 전부
"company_id 관점의 파생 상태 == APPROVED"로 갱신해야 한다(각 도메인의
Whitelist 범위 안에서 함께 반영).

2026-08-15 V7 Gate 2 — product_candidate 재구조화(CTO 지시 원문
요구사항 2):

1) 비공개(PRIVATE) 후보 지원(완전히 새로운 기능, 기존 코드에 없었음):
   ProductCandidate에 visibility("GLOBAL"|"PRIVATE")와
   owner_company_id(논리 참조 companies.id, PRIVATE일 때만 채움)를
   추가한다. GLOBAL은 기존 그대로 전역 공유 발견 카탈로그다. PRIVATE는
   그 회사만 등록·조회·취급할 수 있는 후보 — 다른 회사에게는 존재
   자체가 404다(candidate_id를 알아도 볼 수 없다). candidate_key
   UNIQUE는 전역이라, PRIVATE 등록은 회사별로 네임스페이스를 분리한
   별도의 key 빌더(ProductCandidateService.build_private_candidate_key,
   "PRIVATE:{company_id}:..." 접두사)를 써서 서로 다른 회사가 같은
   source_reference로 각자 비공개 후보를 등록해도 충돌하지 않게 한다.

   가시성 검사는 ProductCandidateService.get_visible_for_company()
   하나에 모았다 — company_status 조회(get_effective_status_for_company),
   승인/보류/거절(_decide), 근거·결정 이력 조회(list_evidence/
   list_decisions), require_approved_for_company() 전부 이 메서드를
   거친다. 이렇게 하나로 모은 이유: PRIVATE 후보 도입 전에는
   app/domains/decision/service.py::evaluate_candidate()가 이
   가시성 검사를 거치지 않고 ProductCandidate를 id만으로 직접
   조회했다 — 그 경로를 이번에 함께 고치면서(app/domains/decision
   도 Gate 2 Whitelist 안) "가시성 검사를 빠뜨리기 쉬운 여러 곳에
   중복 구현" 대신 이 서비스가 유일한 진입점이 되도록 정리했다.

2) "관심 표시/점수/경제성 분석/승인/거절/메모/판매 선택"의 회사별
   스코프 재검토 결과(중복 구현 없이 기존 구조 재사용, homez-core
   원칙): 승인/거절/보류는 이미 ProductCandidateSelection(company_id
   스코프, Gate R13)이 담당한다. "판매 선택"은 APPROVED 전이 자체가
   그 의미다(coupang/marketplace_listing/listing_package가
   require_approved_for_company()로 이미 게이트). "경제성 분석"
   (예상매출·마진·가용자금 등)은 app/domains/decision의
   DecisionEvaluation이 이미 company_id로 스코프돼 있다(Gate R13) —
   이 파일에서 별도 필드로 중복 구현하지 않는다. "메모"는
   ProductCandidateDecision.memo(append-only 이력, 이미 company_id
   스코프)가 원본이며, 이번에 ProductCandidateSelection.memo(최신
   메모 1건의 현재 상태 투영)를 추가해 이력 전체를 훑지 않고도 최신
   메모를 바로 볼 수 있게 했다. "점수"는 ProductCandidate의 AI 산출
   객관적 점수(trend/demand/margin 등)를 가리키며, 이는 회사 주관적
   판단이 아니라 원본 카탈로그 데이터라 의도적으로 전역을 유지한다
   (요청 원문 "원본 발견 카탈로그 자체는 공유 가능한 전역으로 유지"와
   일치). "관심 표시"는 이 저장소에 별도 기능으로 존재한 적이 없어
   —이번 범위에서 새로 발명하지 않았다(비공개 후보처럼 "완전히
   새로운 기능일 수 있다"고 명시된 항목이 아니라 승인/보류로 이미
   충분히 표현 가능하다고 판단) — CTO 확인이 필요하면 별도 후속
   작업으로 남긴다.
=========================================================
"""

from datetime import date
from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class ProductCandidate(Base):
    """상품 후보 현재 상태(최신 점수 + 운영자 결정 상태)."""

    __tablename__ = "product_candidates"
    __table_args__ = (
        UniqueConstraint(
            "candidate_key",
            name="uq_product_candidates_candidate_key",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # source_type + market + source_reference로부터 결정론적으로 생성되는 키.
    # 동일 source + source_reference 중복 후보 방지의 실제 강제 지점.
    candidate_key: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    source_reference: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    market: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    product_name: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    category_hint: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    brand_hint: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    release_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    is_new_product: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    trend_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    novelty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    demand_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    competition_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    evidence_summary: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
    )

    # DISCOVERED / ANALYZED / RECOMMENDED / APPROVED / HELD / REJECTED / EXPIRED
    # 상태별 공식 의미(정책 A)는 constants.py::CandidateStatus
    # docstring 참고 — ANALYZED는 "AI 분석 완료"가 아니라 "시스템
    # 검토 완료, 관리자 판단 가능"이다.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="DISCOVERED",
        index=True,
    )

    # GLOBAL / PRIVATE (2026-08-15 V7 Gate 2, 요구사항 2 — 비공개 후보
    # 지원). GLOBAL은 기존 그대로 전역 공유 카탈로그, PRIVATE는
    # owner_company_id만 조회·취급할 수 있다.
    visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="GLOBAL",
        index=True,
    )

    # 논리 참조 (companies.id) — visibility="PRIVATE"일 때만 채워진다.
    owner_company_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ProductCandidateEvidence(Base):
    """수집 원본 / AI 판단 근거 (append-only, 덮어쓰지 않음)."""

    __tablename__ = "product_candidate_evidences"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (product_candidates.id)
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # RAW_SOURCE / TREND_ANALYSIS / NEW_PRODUCT_ANALYSIS
    evidence_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    payload_summary: Mapped[str] = mapped_column(
        String(2000),
        nullable=False,
    )

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class ProductCandidateDecision(Base):
    """운영자 결정 이력 (append-only)."""

    __tablename__ = "product_candidate_decisions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (companies.id) — 이 결정을 내린 회사(2026-08-14
    # 테넌트 격리 감사, "회사별 승인 이력" 전환).
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (product_candidates.id)
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # APPROVE / HOLD / REJECT
    action: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # 논리 참조 (users.id)
    operator_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    memo: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    decided_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class ProductCandidateSelection(Base):
    """
    회사별 현재 승인 상태 투영(current-state projection) — 신규 테이블
    (2026-08-14 테넌트 격리 감사, Option B).

    (candidate_id, company_id) 당 정확히 1행. ProductCandidate.status
    (전역 AI/추천 워크플로우 상태)와 별개로, "회사 X가 이 후보를 어떻게
    판단했는가"는 이 테이블에서만 파생한다. 행이 없으면 그 회사가 아직
    결정을 내리지 않은 것 — ProductCandidate.status를 그대로 보여준다.

    UNIQUE(candidate_id, company_id)가 회사 경계와 동시성 가드(조건부
    UPDATE의 WHERE 대상)를 동시에 제공한다 — CoupangMarketplaceProduct.
    status와 동일하게 "현재 상태"만 담당하고, 이력은
    ProductCandidateDecision(append-only)이 담당한다.
    """

    __tablename__ = "product_candidate_selections"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "company_id",
            name="uq_product_candidate_selections_candidate_company",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (product_candidates.id)
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (companies.id)
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # APPROVED / HELD / REJECTED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # 최신 메모의 현재 상태 투영(2026-08-15 V7 Gate 2) — 원본 이력은
    # ProductCandidateDecision.memo(append-only)에 그대로 남는다. 이
    # 컬럼은 이력 전체를 훑지 않고도 "이 회사가 이 후보에 남긴 가장
    # 최근 메모"를 바로 조회하기 위한 비정규화다.
    memo: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    decided_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "ProductCandidate",
    "ProductCandidateEvidence",
    "ProductCandidateDecision",
    "ProductCandidateSelection",
]
