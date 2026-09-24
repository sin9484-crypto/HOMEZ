"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/model.py

채널별 판매 방식(Fulfillment Mode) 선택 — SQLAlchemy Model.

app/domains/coupang/model.py와 동일한 설계 원칙을 따른다: FK를
사용하지 않고 전부 논리 참조 컬럼(정수 id)만 사용한다. 이 Domain은
`app/domains/product_candidate`의 ProductCandidate를 읽기 전용으로만
참조한다(Product는 참조하지 않는다 — 승인된 ProductCandidate를 실제
Product 행으로 전환하는 코드 경로가 현재 없기 때문에, 유일한 기존
선례인 CoupangMarketplaceProduct와 동일하게 product_candidate_id를
기준 식별자로 쓴다).

비용(금액) 필드는 이 Domain에 두지 않는다 — 채널·비용 혼합을 원천적으로
막기 위해 비용은 Decision AI 경계(DecisionSupplementaryInputs)로만
전달하고, 이 Domain은 그 평가 결과(decision_evaluation_id)만 논리
참조로 보관한다.

MarketplaceFulfillmentEligibility와 MarketplaceSubmission은 둘 다
append-only다 — 각 행 자체가 감사 로그 역할을 하므로 별도의 9번째
감사 테이블을 두지 않는다.

2026-08-01 CTO 3차 지적 반영 — 회사(테넌트) 소유권 경계: 이 Domain
전체에 company_id가 전혀 없었다(신규 발견). Source of Truth는
MarketplaceAccount.company_id다 — 계정이 회사에 귀속되는 최초
지점이며, 그 이후 모든 테이블(Listing/Selection/Approval/
Eligibility/Submission)은 자신이 참조하는 account(또는 listing)의
company_id를 생성 시점에 그대로 복사해 저장한다(비정규화, FK 없음 —
이 코드베이스의 다른 모든 Domain과 동일한 원칙). 조회·조건부 UPDATE는
반드시 이 회사 자신의 company_id 컬럼을 WHERE에 포함해야 한다 —
account_id를 거쳐 매번 join으로 확인하지 않는다(StoreConnection의
IDOR 재감사와 동일한 이유 — 조인 기반 검사는 실수로 누락되기 쉽다).

MarketplaceChannel/MarketplaceFulfillmentCapability는 예외다 — 이
둘은 회사별 데이터가 아니라 "쿠팡이 어떤 방식을 지원하는가"류의
전역 플랫폼 설정이므로 company_id를 두지 않는다(모든 회사가 같은
값을 공유해서 봐야 한다).

idempotency_key UNIQUE는 전부 (company_id, idempotency_key) 복합으로
바꿨다 — 전역 UNIQUE였다면 StoreConnection에서 발견된 것과 동일한
근본 문제(서로 다른 회사가 우연히 같은 key를 쓰면 IntegrityError
복구 경로가 타사 행을 반환)가 그대로 재현된다.
=========================================================
"""

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import text

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

# 승인(Approval) 스냅샷 전용 금액 필드 — app/domains/coupang/model.py와
# 동일한 Numeric(Decimal) 컨벤션. 실제 Marketplace 공식 API payload에는
# 포함되지 않는다(HOMEZ 내부 Safety 판단 전용 입력).
MONEY = Numeric(14, 2, asdecimal=True)


class MarketplaceChannel(Base):
    """실제 코드·공식 문서로 확인된 판매채널만 등록한다(임의 추가 금지)."""

    __tablename__ = "marketplace_channels"
    __table_args__ = (
        UniqueConstraint(
            "code",
            name="uq_marketplace_channels_code",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # VERIFIED / PARTIAL / UNVERIFIED — UNVERIFIED 채널은 방식이
    # 0개여야 하고 제출 기능이 열리지 않는다(서비스 레벨에서 강제).
    doc_verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNVERIFIED",
    )
    doc_source_reference: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class MarketplaceAccount(Base):
    """
    채널 하나에 복수 계정이 있을 수 있다(예: 쿠팡 A 계정/B 계정). 계정별
    자격은 서로 재사용되지 않는다 — MarketplaceFulfillmentEligibility가
    항상 이 계정 id를 기준으로 독립 관리된다.
    """

    __tablename__ = "marketplace_accounts"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "channel_id", "account_code",
            name="uq_marketplace_accounts_company_channel_account",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 회사 소유권의 Source of Truth.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (marketplace_channels.id)
    channel_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    account_code: Mapped[str] = mapped_column(String(100), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 직매입(DIRECT_PURCHASE) 자격 전용 — 계약이 VERIFIED고 만료되지
    # 않은 계정만 활성화 대상이 된다. NONE/PENDING/VERIFIED/REJECTED/
    # EXPIRED.
    direct_purchase_contract_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NONE",
    )
    direct_purchase_contract_reference: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    direct_purchase_contract_verified_at: Mapped[datetime | None] = (
        mapped_column(DateTime, nullable=True)
    )
    direct_purchase_contract_expires_at: Mapped[datetime | None] = (
        mapped_column(DateTime, nullable=True)
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class MarketplaceFulfillmentCapability(Base):
    """
    채널별 판매 방식 거버넌스 헤더 — CoupangPolicySet과 동일한 원칙.
    status == VERIFIED인 행만 실제로 선택 가능한 방식으로 노출된다.
    정적 레지스트리(capability_registry.py)를 시딩해도 DRAFT로 시작하며,
    VERIFIED 전환은 이 코드가 자동으로 수행하지 않는다(별도 admin_guard
    작업 — AI가 임의로 판매 방식을 활성화할 수 없다).
    """

    __tablename__ = "marketplace_fulfillment_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "fulfillment_mode",
            name="uq_marketplace_capability_channel_mode",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (marketplace_channels.id)
    channel_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    fulfillment_mode: Mapped[str] = mapped_column(
        String(40), nullable=False,
    )

    is_supported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    requires_eligibility_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    requires_account_contract: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    external_display_name: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # 방식별 필수 입력 Pydantic Schema 식별자 — required_fields_schemas.py
    # 의 REQUIRED_FIELDS_SCHEMA_REGISTRY 키와 대응한다. nullable인 이유:
    # ELEVENST처럼 방식 자체가 0개인 채널, 또는 아직 Schema가 등록되지
    # 않은 신규 캡빌리티가 존재할 수 있다 — 그 경우 선택·제출 단계에서
    # None을 fail-closed로 취급한다(DB가 아니라 서비스 레벨에서 차단).
    schema_name: Mapped[str | None] = mapped_column(
        String(80), nullable=True,
    )
    schema_version: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
    )

    # 빈 값을 허용하지 않는다 — 근거 없는 캡빌리티 행을 만들지 않는다.
    doc_source_reference: Mapped[str] = mapped_column(
        String(500), nullable=False,
    )

    # DRAFT / VERIFIED / EXPIRED / REVOKED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class MarketplaceListingDraft(Base):
    """
    상품 등록 위저드 세션(현재 상태 투영). workflow_state는 요약/집계용
    "위저드 진행 상태"이며, 실제 채널별 결과는 MarketplaceListing/
    MarketplaceFulfillmentSelection/MarketplaceSubmission이 각각
    독립적으로 보관한다 — 한 채널의 실패가 다른 채널의 성공을 자동으로
    좌우하지 않는다.
    """

    __tablename__ = "marketplace_listing_drafts"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_marketplace_listing_drafts_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id)
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (product_candidates.id)
    product_candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    workflow_state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True,
    )

    # JSON 문자열 배열 — 선택된 marketplace_account_id 목록
    selected_channel_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )
    # 위저드 1단계(상품 기본정보) 스냅샷 — JSON 문자열
    product_basics_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    validation_errors_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    # 논리 참조 (users.id)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class MarketplaceListing(Base):
    """
    (ProductCandidate × MarketplaceAccount) 단위 Listing. UNIQUE 범위는
    product_candidate_id + marketplace_account_id다 — marketplace_id
    단독이 아니다: 같은 채널의 서로 다른 계정에 각각 별도 Listing이
    허용되어야 하기 때문이다(요청 원문 그대로).
    """

    __tablename__ = "marketplace_listings"
    __table_args__ = (
        UniqueConstraint(
            "product_candidate_id", "marketplace_account_id",
            name="uq_marketplace_listings_candidate_account",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 생성 시점에 marketplace_account_id가
    # 가리키는 계정의 company_id를 그대로 복사한다(비정규화).
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (marketplace_listing_drafts.id) — 위저드 없이도 직접
    # Listing이 생성될 수 있으므로 nullable.
    draft_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    # 논리 참조 (product_candidates.id)
    product_candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (marketplace_accounts.id)
    marketplace_account_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    external_listing_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True,
    )

    # 2026-08-05 최종 제품화 Phase 4 — 플랫폼 상태 동기화. `status`
    # (위 컬럼, HOMEZ 내부 제출 구조 검증 상태)와 완전히 별개다. 이
    # 컬럼들은 "가장 최근에 조회된 캐시"일 뿐이고, 실제 이력의
    # source of truth는 append-only인 MarketplaceListingStatusEvent다.
    platform_sync_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNKNOWN", index=True,
    )
    platform_raw_status: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    status_last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 마지막 새로고침이 실패했다면(조회 자체의 오류) 그 사유 —
    # ListingStatusCheckErrorCode. 성공 시 NULL로 지운다.
    status_last_refresh_error_code: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )
    # 2026-08-05 CTO 반려 반영 — "우리가 언제 물었는가"
    # (status_last_refreshed_at)와 "Provider가 그 상태를 언제 관측한
    # 것이라 주장하는가"를 분리한다. out-of-order(더 오래된 관측이
    # 나중에 도착) 방어의 기준값 — 새 관측의 이 값이 기존 값보다
    # 과거면 캐시를 덮지 않는다.
    platform_status_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # 2026-08-07 Gate H — Retry-After 정규화. Provider가 429와 함께
    # 알려준 원문(초/HTTP-date/구조화 필드)은 app/domains/
    # marketplace_listing/retry_after.py에서 정규화된 뒤 이 두 컬럼만
    # 남는다. `rate_limit_retry_after_seconds`는 가장 최근에 관측한
    # 원본 값(감사·CSV 표시용), `rate_limit_retry_available_at`은
    # 실제로 재시도를 막는 데 쓰이는 유효 시각(naive UTC)이다 — 늦게
    # 도착한 429가 이미 더 늦게 설정된 이 값을 앞당기지 못한다
    # (retry_after.combine_retry_available_at, 항상 더 늦은 쪽 유지).
    rate_limit_retry_after_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    rate_limit_retry_available_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class MarketplaceListingStatusEvent(Base):
    """
    2026-08-05 최종 제품화 Phase 4 — 플랫폼 상태 이력(append-only).
    새로고침(REFRESH) 또는 제출 결과 반영(SUBMISSION)마다 새 행이
    추가되며, 기존 행은 절대 수정·삭제하지 않는다(이 행 자체가 감사
    기록 — MarketplaceSubmission과 동일한 설계 원칙).

    조회 자체가 실패한 경우에도(예: 429) 행을 남긴다 — normalized_
    status는 UNKNOWN, error_code에 실패 사유를 담아 "새로고침을
    시도했지만 알 수 없었다"는 사실 자체를 이력에서 지우지 않는다.
    """

    __tablename__ = "marketplace_listing_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — listing.company_id를 그대로 복사.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (marketplace_listings.id)
    listing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (marketplace_submissions.id) — SUBMISSION 발생 이벤트일
    # 때만 채워진다.
    submission_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # "REFRESH" | "SUBMISSION"
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    previous_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )
    normalized_status: Mapped[str] = mapped_column(
        String(30), nullable=False,
    )
    platform_raw_status: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    # Provider가 이 관측이 유효하다고 주장하는 시각(out-of-order 판단
    # 근거) — created_at(우리가 이 행을 기록한 시각)과 다른 축이다.
    provider_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 새로고침 자체가 실패했거나(조회 오류), 성공했지만 캐시에
    # 반영하지 않았을 때(STALE_OBSERVATION_IGNORED/TERMINAL_STATE_
    # LOCKED) 채워진다(ListingStatusCheckErrorCode).
    error_code: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )
    # 이 관측이 실제로 listing 캐시에 반영됐는지 — False면 event에는
    # 남지만 platform_sync_status 등은 바뀌지 않았다는 뜻이다.
    applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    # 2026-08-05 CTO 반려 반영(item 4) — 이 listing에 대해 연속으로
    # 몇 번째 시도인지("REFRESH"·"RETRY" 공통 카운트, 성공하거나
    # source가 바뀌지 않는 한 계속 누적). 재시도 감사 추적용 — 재시도
    # 엔드포인트가 "몇 번째 재시도인지"를 이전 실패 이유를 지우지
    # 않고 새 행에 기록하기 위해 필요하다.
    attempt_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    # 논리 참조 (users.id) — 수동 새로고침을 요청한 운영자. 자동/시스템
    # 트리거는 NULL.
    triggered_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )


class MarketplaceFulfillmentSelection(Base):
    """
    채널별 판매 방식 선택 — capability_id로 정확한 채널+방식 캡빌리티를
    고정한다(이후 캡빌리티가 바뀌어도 이 선택이 어떤 근거로 이뤄졌는지
    추적 가능, 비용/규칙 혼합 방지). 방식 변경은 새 행을 append하고
    이전 행을 SUPERSEDED로 표시한다 — 덮어쓰지 않는다.
    """

    __tablename__ = "marketplace_fulfillment_selections"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_marketplace_fulfillment_selections_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — listing.company_id를 그대로 복사.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (marketplace_listings.id)
    listing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (marketplace_fulfillment_capabilities.id)
    capability_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    fulfillment_mode: Mapped[str] = mapped_column(
        String(40), nullable=False,
    )

    # 방식별 필수 입력 — 자유 JSON이 아니라 검증된(Pydantic model_dump)
    # canonical JSON 문자열이다. 아래 schema_name/version/fingerprint와
    # 항상 함께 저장된다 — 이 세 값이 없으면 어떤 Schema로 검증됐는지
    # 알 수 없어 제출 단계에서 재검증이 불가능해진다(fail-closed 근거).
    required_fields_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}",
    )
    required_fields_schema_name: Mapped[str] = mapped_column(
        String(80), nullable=False,
    )
    required_fields_schema_version: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    # 검증된 required_fields의 SHA-256 — 승인(Approval)의
    # selection_fingerprint 계산에 포함되어, 방식 변경뿐 아니라 같은
    # 방식이라도 필수 입력 내용이 바뀌면 기존 승인이 무효화되게 한다.
    required_fields_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    # SELECTED / SUPERSEDED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SELECTED", index=True,
    )

    # 논리 참조 (decision_evaluations.id) — Decision AI 추천 연결(선택적)
    decision_evaluation_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    # 논리 참조 (users.id)
    selected_by: Mapped[int] = mapped_column(Integer, nullable=False)

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class MarketplaceSubmissionApproval(Base):
    """
    제출 승인 — append-only(이 행 자체가 감사 기록). request/approve/
    reject/revoke 각각 새 행을 추가한다. (listing_id, selection_id)의
    "현재 유효한 승인"은 가장 최근 행이 APPROVED이고, 만료되지 않았고,
    지금 이 순간의 Listing/Selection/Capability 상태와 fingerprint·
    정책버전이 정확히 일치할 때만 성립한다(읽기 시점 판단 —
    approval_service.py::current_valid_approval 참고,
    eligibility_service.py와 동일한 철학).

    클라이언트는 approved_by를 절대 지정할 수 없다 — approve() 호출
    자체의 admin_guard current_user에서만 채워진다(router.py에는 그
    값을 담을 요청 필드 자체가 없다). 승인 요청과 제출은 항상 서로
    다른 API 호출이다 — 같은 요청에서 승인+제출을 함께 하지 않는다.

    unit_price/unit_cost_of_goods/expected_logistics_cost/
    planned_quantity는 실제 Marketplace 공식 API payload 필드가 아니다
    — HOMEZ가 SafetyService에 넘길 실제 자금 노출을 계산하기 위한
    내부 전용 스냅샷이다(0 하드코딩 금지 근거).
    """

    __tablename__ = "marketplace_submission_approvals"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_marketplace_submission_approvals_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — listing.company_id를 그대로 복사.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (marketplace_listings.id / marketplace_fulfillment_selections.id)
    listing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    selection_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # PENDING / APPROVED / REJECTED / EXPIRED / REVOKED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
    )

    # 요청 시점(그리고 승인 시점 재확인)의 Listing/Selection 상태 지문 —
    # 하나라도 달라지면 이 승인은 더 이상 유효하지 않다.
    listing_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    selection_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    capability_policy_version: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )

    # Safety 입력 계산 전용 스냅샷(공식 API 필드 아님) — 전부 Decimal.
    planned_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[object] = mapped_column(MONEY, nullable=False)
    unit_cost_of_goods: Mapped[object] = mapped_column(MONEY, nullable=False)
    expected_logistics_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )

    # 논리 참조 (users.id)
    requested_by: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    # 논리 참조 (users.id) — approve()가 호출된 admin_guard 사용자에서만
    # 채워진다. 클라이언트 요청 바디에는 이 값을 담을 필드가 없다.
    approved_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 무기한 승인을 금지한다 — APPROVED로 전이하려면 반드시 필요.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    reason: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class MarketplaceFulfillmentEligibility(Base):
    """
    계정별 자격 검사 이력(append-only — 이 행 자체가 감사 기록). "현재"
    자격은 항상 created_at 기준 최신 행으로 판단하며, 행이 하나도
    없으면 UNKNOWN으로 취급한다(자동 VERIFIED 없음). 계정 간 재사용
    금지 — marketplace_account_id로 항상 독립 관리된다.
    """

    __tablename__ = "marketplace_fulfillment_eligibilities"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_marketplace_fulfillment_eligibility_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — account.company_id를 그대로 복사.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (marketplace_accounts.id) — 계정별 독립, 재사용 안 함
    marketplace_account_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    fulfillment_mode: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True,
    )
    # 논리 참조 (marketplace_fulfillment_capabilities.id)
    capability_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # UNKNOWN / CHECK_PENDING / VERIFIED / REJECTED / EXPIRED /
    # MANUAL_REVIEW_REQUIRED
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNKNOWN", index=True,
    )
    # 예: "COUPANG_WING_OPT_IN", "DIRECT_PURCHASE_CONTRACT",
    # "MANUAL_REVIEW"
    check_source: Mapped[str] = mapped_column(String(50), nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 만료 후에는 읽기 시점에 재확인을 요구한다(서비스 레벨에서 강제).
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    # 논리 참조 (users.id) — MANUAL_REVIEW_REQUIRED 경로에서 필수
    reviewer_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    review_reason: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class MarketplaceSubmission(Base):
    """
    채널별 제출 시도(append-only — 이 행 자체가 감사 기록). 채널마다
    독립된 행이며, 한 채널의 실패·timeout이 다른 채널의 행에 영향을
    주지 않는다. operator_approved_by가 비어있으면 SUBMITTED로 전이할
    수 없다(서비스 레벨에서 강제).
    """

    __tablename__ = "marketplace_submissions"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_marketplace_submissions_company_idempotency",
        ),
        # 2026-09-24 후속(자동 재신청 방지 라운드 3 — 중복 상품등록
        # 차단, 설계·격리 리허설만, 원본 DB 미적용) — 위 UNIQUE는
        # (company_id, idempotency_key) 조합만 막는다. idempotency_key는
        # `wizard-{id}-account-{id}-submission`처럼 **위저드 단위**로
        # 계산되므로, 같은 상품 후보·같은 판매계정으로 위저드를 새로
        # 하나 더 만들면 새 idempotency_key로 완전히 새 행이 생겨
        # 이 UNIQUE를 우회한다 — 그 새 행은 listing_id는 같지만(같은
        # 후보+계정 조합은 marketplace_listings.UNIQUE로 하나뿐이다)
        # idempotency_key가 다르다. preflight()의 애플리케이션 레벨
        # 검사(listing_wizard_live_service.py, DUPLICATE_LIVE_ATTEMPT_
        # ON_SAME_LISTING)가 이 우회를 대부분 막지만, 그 검사는 SELECT
        # 라 두 프로세스가 동시에 검사를 통과한 뒤 이 아래 부분
        # UNIQUE INDEX가 최종 방어선이 된다(claim 지점의 UPDATE가
        # 대상). correlation_id는 실제 send()가 provider를 호출하기
        # 직전에만 설정되므로(구조 검증만 하는 submission_service.
        # submit()은 절대 설정하지 않는다), "같은 listing에 대해
        # 실제 전송을 시도했고 아직 확정되지 않았거나(SUBMITTING/
        # UNKNOWN) 이미 성공한(external_submission_ref 있음) 행은
        # 동시에 하나만 존재한다"를 DB가 강제한다. 실제로 명시적
        # 거절(FAILED)로 끝난 행은 이 조건에서 빠진다 — 정상적인
        # 수정 후 재등록 경로까지 막지 않기 위해서다(append-only
        # 이력은 그대로 남고, 이 인덱스는 오직 "동시에 몇 개까지
        # 허용하는가"만 강제한다 — 과거 행을 지우거나 덮어쓰지
        # 않는다).
        Index(
            "uq_marketplace_submissions_live_claim_per_listing",
            "company_id", "listing_id",
            unique=True,
            sqlite_where=text(
                "correlation_id IS NOT NULL AND ("
                "external_submission_ref IS NOT NULL OR "
                "status IN ('SUBMITTING', 'UNKNOWN')"
                ")",
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — listing.company_id를 그대로 복사.
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (marketplace_listings.id)
    listing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (marketplace_fulfillment_selections.id) — 제출된 정확한
    # 방식을 고정
    selection_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 감사 조회 편의를 위한 비정규화(marketplace_accounts.id) — 채널별
    # 감사 로그 조회에 사용(decision_audit_logs의 candidate_id 비정규화
    # 패턴과 동일)
    marketplace_account_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    external_submission_ref: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )

    request_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
    )
    external_http_status: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # PENDING / SUBMITTED / FAILED / UNKNOWN
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING", index=True,
    )

    # SafetyService.evaluate() 결과 스냅샷(ALLOW/REQUIRE_APPROVAL/DENY)
    safety_decision: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )

    # 논리 참조 (users.id) — 비어있으면 SUBMITTED로 전이 불가
    operator_approved_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    operator_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    error_reason: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )

    # 2026-08-30 후속 지시(성공 경고 보존) — 성공(SUBMITTED)이어도
    # 쿠팡이 함께 돌려준 확인 필요 안내(예: Brand Enrollment 경고)를
    # 담는다. 실패 사유(error_reason)와 의미가 다르므로 절대 섞지
    # 않는다 — 이 값이 있어도 status는 그대로 SUBMITTED다. 화면을
    # 다시 열어도 사라지지 않도록 이 행 자체에 영구 보존한다(마스킹된
    # 값만 저장 — coupang_live_provider.py::LiveSubmissionResult.
    # warning_summary가 이미 마스킹을 거친 값이다).
    provider_warning_summary: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )
    # 성공 판정 근거가 된 쿠팡 응답의 최상위 code(예: "SUCCESS") —
    # 감사 목적으로 무엇을 근거로 성공 판정했는지 구조적으로 남긴다.
    provider_response_code: Mapped[str | None] = mapped_column(
        String(80), nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    attempted_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class MarketplaceSubmissionReconciliation(Base):
    """
    2026-08-30 V7 후속 안정화 Phase 5 — `MarketplaceSubmission`은
    append-only 감사 기록이다(클래스 docstring 참고) — 실제로 성공한
    제출이 파싱 버그 등으로 UNKNOWN/FAILED로 잘못 기록됐더라도, 그
    행을 절대 직접 UPDATE하지 않는다("임의 DB 수정 없이 정합화" 요구
    사항). 대신 이 테이블에 별도 행을 append해 "이 submission_id는
    나중에 이런 근거로 정합화됐다"는 사실만 추가로 기록한다 — 원본
    행의 error_reason/status는 그대로 감사 이력에 남는다.

    submission_id마다 최대 1건만 허용한다(UNIQUE) — 같은 제출을 두 번
    정합화하지 않는다(멱등). FK 없음(다른 Domain과 동일 설계 원칙).
    """

    __tablename__ = "marketplace_submission_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "submission_id",
            name="uq_marketplace_submission_reconciliations_submission_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # 논리 참조 (marketplace_submissions.id) — 정합화 대상 원본 행.
    submission_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 운영자가 실제 판매채널 화면(WING 등)에서 직접 확인해 입력한 값 —
    # 이 서비스가 추측해서 채우지 않는다.
    external_submission_ref: Mapped[str] = mapped_column(
        String(200), nullable=False,
    )

    # 정합화 시점에 상태 조회 Provider(Fake 또는 실제)가 반환한 원본
    # 상태명(예: APPROVED) — 근거를 그대로 남긴다.
    observed_status_name: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )

    reconciled_by_user_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
    )
    reason: Mapped[str] = mapped_column(
        String(500), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )


class ListingWizard(Base):
    """
    Gate I(2026-08-08) — 상품등록 통합 마법사. `MarketplaceListingDraft`
    (기존 `ml*` Console 마법사 전용, 좁은 후보→채널→방식 흐름)와는
    의도적으로 별개 테이블이다 — docs/V6_EXECUTION_LEDGER.md "Gate I
    설계" 3-1항 참고. 이 테이블은 오직 "진행 중 단계별 편집 상태를
    담는 그릇"이며, 실제 커밋 가능한 데이터(Listing/Selection/
    Submission)는 일괄 등록 시점에만 기존 MarketplaceListingService.
    create_listing()/select_fulfillment_mode()를 호출해 정식으로
    생성한다 — 이 테이블 자신은 그 결과 id만 materialized_listing_
    ids_json에 기록한다.

    단계별 세부값을 전부 이 한 행 위의 JSON 컬럼으로 두는 이유는
    "여러 새 테이블로 흩어진 신규 대형 도메인"을 피하기 위함이다 —
    각 JSON의 구조 검증은 서비스 계층의 Pydantic 모델이 전담한다(이
    테이블 자체는 원문 문자열만 저장).
    """

    __tablename__ = "listing_wizards"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "creation_idempotency_key",
            name="uq_listing_wizards_company_creation_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 논리 참조 (companies.id) — 유일한 격리 경계(Gate I 설계 6번 항목).
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (users.id)
    created_by_user_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )

    # WizardStep — 지금 사용자가 보고 있는 단계.
    current_step: Mapped[str] = mapped_column(
        String(30), nullable=False, default="SOURCE",
    )
    # WizardStatus — 전체 마법사 집계 상태(current_step과 독립된 축).
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DRAFT", index=True,
    )

    # MANUAL / AI_RECOMMENDED / CLONE — 1단계(상품 소스) 진입 경로.
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="MANUAL",
    )
    # 논리 참조 (product_candidates.id) — 1단계 완료 후에만 채워짐.
    product_candidate_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    # 논리 참조 (listing_wizards.id) — 복제 계보(자기 참조, FK 없음).
    cloned_from_wizard_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # 2단계(상품 초안) 스냅샷 — JSON 문자열. AI 원본값과 사용자 수정값을
    # 서비스 계층이 구분해 저장한다(이 컬럼은 최종 병합 결과만 담음).
    draft_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 3단계(이미지) — 선택된 media_asset_id JSON 배열.
    selected_media_asset_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )
    # 4-5단계(판매채널/판매방식) — 채널별 선택값 JSON 배열. 실제
    # MarketplaceFulfillmentSelection 행은 아직 생성하지 않는다.
    channel_selections_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )
    # 6단계(가격·마진) — 입력값/계산결과 JSON. 계산은 항상 서버(Decimal)
    # 전담이며, 이 컬럼들은 그 결과의 감사 스냅샷이다.
    economics_input_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    economics_result_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    # 7단계(사전검사) — 구조화 검사 결과 JSON(완성 문장 저장 안 함).
    validation_result_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )

    # 8단계(승인) — 불변 승인 패키지 스냅샷 + fingerprint.
    approval_package_json: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    approval_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    # 논리 참조 (users.id) — 오직 SuperAdminGuard 호출자에서만 채워짐.
    approved_by_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 승인/거절/취소 이력 JSON 배열(append-only 로그의 경량 사본).
    approval_history_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )

    # 10단계(결과) — 일괄 등록으로 실제 생성된 MarketplaceListing.id
    # JSON 배열(채널별 부분 성공/실패 추적용).
    materialized_listing_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )

    # Gate J(자동 저장)가 이어받을 낙관적 동시성/자동저장 경계.
    autosave_client_token: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    autosave_saved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    # 생성 요청 자체의 idempotency — 이 코드베이스 전역 컨벤션과 동일
    # (company_id, key) 복합 UNIQUE.
    creation_idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    # 2026-08-28 "대기 상품 정리" — soft delete(=ARCHIVED 상태 전이).
    # status == "ARCHIVED"가 "삭제됨"의 Source of Truth이고(별도
    # is_deleted 불리언을 두지 않는다 — 기존 status 축을 그대로
    # 재사용), 아래 필드들은 그 전이의 감사 스냅샷이다. 물리 삭제는
    # 이 코드가 절대 수행하지 않는다(보존기간 경과 후 물리 삭제는
    # 별도 배치 작업 — 이번 범위 밖).
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 논리 참조 (users.id)
    deleted_by_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    delete_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    # 삭제 요청 자체의 idempotency(creation_idempotency_key와 동일
    # 원칙, 별도 축) — 같은 요청을 재전송해도 중복 삭제 시도로 취급하지
    # 않는다.
    deletion_request_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True,
    )
    # restore()가 정확히 어느 상태로 되돌려야 하는지 알기 위한 필수
    # 스냅샷 — archive() 호출 직전의 status를 그대로 담아 두고,
    # restore() 성공 시 이 값을 status로 복사한 뒤 다시 비운다.
    status_before_archive: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )

    restored_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    # 논리 참조 (users.id)
    restored_by_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "MarketplaceChannel",
    "MarketplaceAccount",
    "MarketplaceFulfillmentCapability",
    "MarketplaceListingDraft",
    "MarketplaceListing",
    "MarketplaceListingStatusEvent",
    "MarketplaceFulfillmentSelection",
    "MarketplaceSubmissionApproval",
    "MarketplaceFulfillmentEligibility",
    "MarketplaceSubmission",
    "MarketplaceSubmissionReconciliation",
    "ListingWizard",
]
