"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/constants.py

채널별 판매 방식(Fulfillment Mode) 선택 — 고정 상수.

내부 표준 enum(FulfillmentMode)과 채널별 실제 API 필드는 분리한다 —
이 enum 값은 어떤 채널의 실제 API에도 그대로 전송되지 않는다
(app/domains/marketplace_listing/adapters가 변환을 전담).
=========================================================
"""


class FulfillmentMode:
    """
    HOMEZ 내부 표준 판매 방식 enum. 채널별 표시 이름은
    MarketplaceFulfillmentCapability.external_display_name에서 나온다
    (하드코딩 dict가 아니라 DB의 VERIFIED 상태를 신뢰한다).
    """

    SELLER_FULFILLED = "SELLER_FULFILLED"
    MARKETPLACE_FULFILLED = "MARKETPLACE_FULFILLED"
    DIRECT_PURCHASE = "DIRECT_PURCHASE"
    PARTNER_FAST_DELIVERY = "PARTNER_FAST_DELIVERY"
    PICKUP_OR_STORE_FULFILLED = "PICKUP_OR_STORE_FULFILLED"
    DIGITAL_DELIVERY = "DIGITAL_DELIVERY"
    OTHER_VERIFIED = "OTHER_VERIFIED"

    ALL = (
        SELLER_FULFILLED,
        MARKETPLACE_FULFILLED,
        DIRECT_PURCHASE,
        PARTNER_FAST_DELIVERY,
        PICKUP_OR_STORE_FULFILLED,
        DIGITAL_DELIVERY,
        OTHER_VERIFIED,
    )


class ChannelCode:
    """
    실제 코드·문서로 확인된 채널만 포함한다(임의 채널 추가 금지).
    G마켓/옥션/알리익스프레스/테무는 app/core/config.py에 활성화 flag만
    존재하고 실제 도메인·문서 확인이 없어 이번 phase에서 제외한다.
    """

    COUPANG = "COUPANG"
    NAVER_SMARTSTORE = "NAVER_SMARTSTORE"
    ELEVENST = "ELEVENST"

    ALL = (COUPANG, NAVER_SMARTSTORE, ELEVENST)


class ChannelDocVerificationStatus:

    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    UNVERIFIED = "UNVERIFIED"

    ALL = (VERIFIED, PARTIAL, UNVERIFIED)


class CapabilityStatus:
    """
    CoupangPolicySet과 동일한 거버넌스 패턴 — VERIFIED만 자동 판단(선택
    가능 여부 노출)에 사용된다. 정적 레지스트리(capability_registry.py)를
    시딩해도 status는 DRAFT로 시작하며, VERIFIED로 전환하는 것은 이
    코드가 자동으로 수행하지 않는다(별도 admin_guard 작업).
    """

    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"

    ALL = (DRAFT, VERIFIED, EXPIRED, REVOKED)


class EligibilityState:

    UNKNOWN = "UNKNOWN"
    CHECK_PENDING = "CHECK_PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

    ALL = (
        UNKNOWN,
        CHECK_PENDING,
        VERIFIED,
        REJECTED,
        EXPIRED,
        MANUAL_REVIEW_REQUIRED,
    )

    # 제출을 막지 않는(=사용 가능한) 유일한 상태. UNKNOWN은 절대 허용이
    # 아니다(fail-closed).
    USABLE = (VERIFIED,)


class DraftWorkflowState:

    DRAFT = "DRAFT"
    CHANNEL_SELECTED = "CHANNEL_SELECTED"
    FULFILLMENT_REQUIRED = "FULFILLMENT_REQUIRED"
    FULFILLMENT_SELECTED = "FULFILLMENT_SELECTED"
    ELIGIBILITY_UNVERIFIED = "ELIGIBILITY_UNVERIFIED"
    ELIGIBILITY_VERIFIED = "ELIGIBILITY_VERIFIED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

    ALL = (
        DRAFT,
        CHANNEL_SELECTED,
        FULFILLMENT_REQUIRED,
        FULFILLMENT_SELECTED,
        ELIGIBILITY_UNVERIFIED,
        ELIGIBILITY_VERIFIED,
        VALIDATION_FAILED,
        APPROVAL_REQUIRED,
        APPROVED,
        READY_TO_SUBMIT,
        SUBMITTED,
        FAILED,
        UNKNOWN,
    )


class WizardStep:
    """
    Gate I(2026-08-08) — 상품등록 통합 마법사 10단계. `ListingWizard.
    current_step`이 지금 사용자가 보고 있는 단계를 가리킨다. 이 순서는
    `docs/V6_EXECUTION_LEDGER.md`의 "Gate I 설계" 2번 항목(마법사
    단계별 소유 도메인) 표와 정확히 대응한다 — 새 단계를 추가하려면
    그 표도 함께 갱신해야 한다.
    """

    SOURCE = "SOURCE"
    DRAFT = "DRAFT"
    MEDIA = "MEDIA"
    CHANNELS = "CHANNELS"
    FULFILLMENT = "FULFILLMENT"
    ECONOMICS = "ECONOMICS"
    PRECHECK = "PRECHECK"
    APPROVAL = "APPROVAL"
    EXECUTION = "EXECUTION"
    RESULTS = "RESULTS"

    ORDERED = (
        SOURCE, DRAFT, MEDIA, CHANNELS, FULFILLMENT, ECONOMICS,
        PRECHECK, APPROVAL, EXECUTION, RESULTS,
    )
    ALL = ORDERED


class WizardStatus:
    """
    ListingWizard.status — 전체 마법사의 집계 상태. WizardStep(지금
    보고 있는 화면)과는 독립된 축이다 — 예를 들어 NEEDS_CORRECTION
    상태에서도 사용자는 어느 단계로든 자유롭게 이동해 수정할 수 있다.
    """

    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    PARTIALLY_SUCCEEDED = "PARTIALLY_SUCCEEDED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    # 2026-08-28 "대기 상품 정리" — soft delete 전이 상태. 기존
    # ALL/TERMINAL/EDITABLE 어디에도 이미 포함돼 있지 않던 값이므로,
    # 이 값을 새로 추가해도 기존 모든 조건부 UPDATE의 expected_statuses
    # whitelist는 자동으로 ARCHIVED를 배제한다(추가로 손댈 필요 없음).
    ARCHIVED = "ARCHIVED"

    ALL = (
        DRAFT, VALIDATING, NEEDS_CORRECTION, READY_FOR_APPROVAL, APPROVED,
        SUBMITTING, PARTIALLY_SUCCEEDED, SUCCEEDED, FAILED, CANCELLED,
        ARCHIVED,
    )

    # 승인 이후에만 실행(제출) 단계로 넘어갈 수 있다 — 승인 없이
    # SUBMITTING으로 전이하는 경로는 서비스 레벨에 존재하지 않는다.
    TERMINAL = (SUCCEEDED, FAILED, CANCELLED)

    # 이 상태들에서만 편집(PATCH)이 허용된다 — 제출이 시작된 뒤에는
    # 위저드 입력을 더 이상 바꿀 수 없다(이미 실행 중인 채널별 제출과
    # 어긋나는 것을 막기 위함).
    EDITABLE = (DRAFT, VALIDATING, NEEDS_CORRECTION, READY_FOR_APPROVAL)

    # 삭제(archive) 허용 출발 상태 — 사용자 결정 그대로: "작성 중"
    # (DRAFT/VALIDATING/NEEDS_CORRECTION), "승인 대기"
    # (READY_FOR_APPROVAL), "등록 실패"(FAILED), "명시적으로 취소한
    # 미제출 항목"(CANCELLED). APPROVED/SUBMITTING/PARTIALLY_SUCCEEDED/
    # SUCCEEDED는 의도적으로 제외한다(승인된 항목은 revoke-approval을
    # 먼저 거쳐야 하고, 제출 중·부분/전체 성공 항목은 실제 채널 상태와
    # 얽혀 있어 fail-closed로 삭제를 막는다 — 사용자 결정의 "삭제 금지
    # 상태" 목록과 일치).
    DELETABLE_FROM = (
        DRAFT, VALIDATING, NEEDS_CORRECTION, READY_FOR_APPROVAL, FAILED,
        CANCELLED,
    )


class SubmissionStatus:

    PENDING = "PENDING"
    # 실제 외부 호출권을 한 요청이 원자적으로 선점한 상태. 이 상태가
    # 남으면 결과 불확실로 취급하고 자동 재시도하지 않는다.
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    # 외부 API timeout·무응답 — 성공/실패를 추론하지 않는다.
    UNKNOWN = "UNKNOWN"

    ALL = (PENDING, SUBMITTING, SUBMITTED, FAILED, UNKNOWN)


class SelectionStatus:
    """방식 변경 시 append(SUPERSEDED)하며, 기존 행을 덮어쓰지 않는다."""

    SELECTED = "SELECTED"
    SUPERSEDED = "SUPERSEDED"

    ALL = (SELECTED, SUPERSEDED)


class ContractStatus:
    """MarketplaceAccount.direct_purchase_contract_status — 직매입 자격."""

    NONE = "NONE"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    ALL = (NONE, PENDING, VERIFIED, REJECTED, EXPIRED)


class ApprovalStatus:
    """
    MarketplaceSubmissionApproval.status — 요청과 결정은 항상 서로
    다른 행이다(append-only). PENDING만 approve()/reject() 대상이고,
    APPROVED만 revoke() 대상이다. EXPIRED는 저장된 상태 값이 아니라
    current_valid_approval()이 읽기 시점에 expires_at을 확인해 판단하는
    논리적 결과다(다른 append-only 상태들과 동일한 read-time 판단
    패턴).
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"

    ALL = (PENDING, APPROVED, REJECTED, EXPIRED, REVOKED)


class ListingStatus:
    """
    MarketplaceListing.status — 2026-08-01 Gate 5(CTO 2차 지적) 반영.

    수정 전 결함: repository.update_listing_status_conditional()가
    정의만 되어 있고 service.py 어디서도 호출되지 않았다 — 모든
    Listing이 생성 시점의 "DRAFT" 값에 영원히 고정된 채, 방식 선택
    완료·승인·제출이 실제로 진행돼도 "플랫폼별 등록 상태" 화면에는
    전혀 반영되지 않는 조용한 결함이었다.

    이 값들은 순수하게 표시/감사용 필드다 — 실제 제출 가능 여부를
    결정하는 보안 게이트는 여전히 ApprovalService.current_valid_
    approval()(fingerprint·만료·정책버전 재확인)뿐이다. 즉 이 상태값을
    잘못 전이시켜도 실제 제출 인가 로직은 전혀 우회되지 않는다
    (구조적으로 분리됨).

    전이:
      DRAFT --(finalize_fulfillment_selection)--> READY
      READY --(approve)--> APPROVED
      READY --(reject)--> REJECTED
      APPROVED --(revoke)--> READY
      READY/APPROVED --(pause)--> PAUSED
      PAUSED --(resume)--> READY
      APPROVED --(submit 시작, 승인 확인 직후)--> SUBMITTING
      SUBMITTING --(submit 구조적 검증 통과)--> SUBMITTED
      SUBMITTING --(submit 실패)--> FAILED
      SUBMITTING --(adapter 예외 등 UNKNOWN)--> 그대로 SUBMITTING(추측
        하지 않음 — SubmissionStatus.UNKNOWN과 동일한 철학)

    "SUBMITTED"는 실제 마켓 공개 제출을 의미하지 않는다 — 이
    코드베이스에는 네트워크 호출 코드 자체가 없다(app/domains/
    marketplace_listing 어디에도 requests/httpx/urllib import가
    없음). SUBMITTED는 "우리 시스템의 구조적 검증을 통과해 기록됐다"
    는 뜻이고, 실제 공개는 별도의, 이번 Phase에 존재하지 않는
    승인/버튼을 거쳐야 한다(Desktop UI에 LIVE_E2E_PENDING_USER_
    CREDENTIAL/AWAITING_FINAL_PRODUCT_SUBMISSION 배지로 항상 별도
    표시한다).
    """

    DRAFT = "DRAFT"
    READY = "READY"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"

    ALL = (
        DRAFT, READY, SUBMITTING, SUBMITTED, APPROVED, REJECTED, FAILED,
        PAUSED,
    )

    # pause()가 허용되는 출발 상태 — 아직 종결되지 않은, 사람이 개입할
    # 여지가 있는 상태만 일시정지할 수 있다.
    PAUSABLE_FROM = (READY, APPROVED)


class PlatformSyncStatus:
    """
    2026-08-05 최종 제품화 Phase 4 — 플랫폼(쿠팡·네이버) 원본 상태를
    정규화한 HOMEZ 공통 상태. `ListingStatus`(HOMEZ 내부 위저드/제출
    구조 검증 상태)와는 완전히 별개 개념이다 — 이 값은 오직 "외부
    채널이 지금 이 Listing을 어떤 상태로 보고 있는가"만 나타내며,
    `MarketplaceListing.status`가 결정하는 제출 가능 여부에는 전혀
    관여하지 않는다(구조적으로 분리).

    실제 쿠팡·네이버 상태 코드 매핑 문서를 아직 확인하지 못했으므로
    (이 Phase는 Fake Provider만 사용), 원본 코드 → 이 정규화 상태로
    바꾸는 실제 매핑 테이블은 만들지 않는다 — Fake Provider가 이미
    정규화된 값을 직접 반환하고, `platform_raw_status`에는 그 근거가
    된 가상 원본 코드를 함께 저장해 "두 값을 분리 저장한다"는 요구를
    충족한다. 실제 매핑은 공식 문서 확인 후 별도로 구현해야 한다.
    """

    DRAFT = "DRAFT"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PROCESSING = "PROCESSING"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    ENDED = "ENDED"
    # 조회 실패·미확인 — 절대 성공으로 추론하지 않는다(fail-closed).
    UNKNOWN = "UNKNOWN"

    ALL = (
        DRAFT, VALIDATION_FAILED, PENDING_APPROVAL, APPROVED, QUEUED,
        SUBMITTING, SUBMITTED, PROCESSING, ACTIVE, REJECTED, FAILED,
        PAUSED, ENDED, UNKNOWN,
    )

    # 이 상태들만 "채널에 실제로 노출 중"으로 UI가 표시할 수 있다 —
    # 그 외(특히 UNKNOWN)는 절대 성공/노출로 표시하지 않는다.
    LIVE = (PROCESSING, ACTIVE)

    # 2026-08-05 CTO 반려 반영 — terminal 상태의 잘못된 역행 차단.
    # 실제 쿠팡·네이버 상태 코드 문서를 아직 확인하지 못했으므로(추측
    # 금지 원칙), 전이표 전체를 확정하지 않는다 — 유일하게 명확한
    # 경계만 코드로 강제한다: ENDED는 채널이 최종적으로 종료했다고
    # 보고한 상태이므로, 자동 새로고침으로 여기서 다른 상태로 되돌아갈
    # 수 없다(실제로 재개됐다면 운영자가 별도로 확인·재승인해야 한다 —
    # 이 서비스가 조용히 되돌리지 않는다). UNKNOWN은 "조회 실패"일
    # 뿐이라 이 규칙에서 제외한다(9번 항목 — 조회 실패는 항상 이전
    # 상태를 보존하며 ENDED 여부와 무관).
    TERMINAL = (ENDED,)


class ListingStatusCheckErrorCode:
    """
    상태 조회(refresh) 자체의 실패 사유 — Provider 호출이 실패했을 때만
    쓰인다(정상 조회 결과의 PlatformSyncStatus와는 다른 축). 이 코드가
    있으면 `PlatformSyncStatus.UNKNOWN`과 함께 저장되고, 이전에 알려진
    정규화 상태를 덮어쓰지 않는다.
    """

    UNAUTHORIZED_401 = "UNAUTHORIZED_401"
    FORBIDDEN_403 = "FORBIDDEN_403"
    NOT_FOUND_404 = "NOT_FOUND_404"
    RATE_LIMITED_429 = "RATE_LIMITED_429"
    PLATFORM_ERROR_5XX = "PLATFORM_ERROR_5XX"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"

    # 2026-08-05 CTO 반려 반영 — 조회 자체는 성공했지만(Provider가
    # 응답함) 그 값을 그대로 반영하지 않은 두 가지 경우. 둘 다
    # PlatformSyncStatus.UNKNOWN이 아니라 실제 관측값을 event에 남기되,
    # listing 캐시는 갱신하지 않는다(아래 두 값 전용 — 위 6개 코드와는
    # 발생 지점이 다르다).
    STALE_OBSERVATION_IGNORED = "STALE_OBSERVATION_IGNORED"
    TERMINAL_STATE_LOCKED = "TERMINAL_STATE_LOCKED"

    ALL = (
        UNAUTHORIZED_401, FORBIDDEN_403, NOT_FOUND_404, RATE_LIMITED_429,
        PLATFORM_ERROR_5XX, TIMEOUT, UNKNOWN,
        STALE_OBSERVATION_IGNORED, TERMINAL_STATE_LOCKED,
    )


# 상태 새로고침 사이 최소 간격(초) — 이보다 짧은 간격의 재요청은
# TooManyRequestsException(429)으로 거부한다(Provider 대신 서버 자체
# 요청 빈도를 제한 — Phase 4 "9. 상태 새로고침에 rate limit과 backoff를
# 적용한다" 요구).
STATUS_REFRESH_MIN_INTERVAL_SECONDS = 30

# 2026-08-28 "대기 상품 정리" — "전체 필터 삭제" 1회 요청이 처리할 수
# 있는 최대 건수. MAX_CSV_EXPORT_ROWS(listing_wizard_csv_export.py)와
# 동일한 원칙 — 초과분을 조용히 자르지 않고 명시적으로 차단한다.
MAX_BULK_ARCHIVE_COUNT = 200

# 2026-09-06 "일괄 마진 설정 + 멀티마켓 동시 등록" — 대량 마진 재적용
# 1회 요청의 최대 건수. MAX_BULK_ARCHIVE_COUNT와 동일한 원칙.
MAX_BULK_MARGIN_APPLY_COUNT = 200

# 제출은 실제 채널 등록(구조적 성공)까지 이어지는 되돌리기 어려운
# 작업이라 한 번에 처리하는 건수를 의도적으로 더 작게 둔다.
MAX_BULK_SUBMIT_COUNT = 50


__all__ = [
    "FulfillmentMode",
    "ChannelCode",
    "ChannelDocVerificationStatus",
    "CapabilityStatus",
    "EligibilityState",
    "DraftWorkflowState",
    "WizardStep",
    "WizardStatus",
    "SubmissionStatus",
    "SelectionStatus",
    "ContractStatus",
    "ApprovalStatus",
    "ListingStatus",
    "PlatformSyncStatus",
    "ListingStatusCheckErrorCode",
    "STATUS_REFRESH_MIN_INTERVAL_SECONDS",
    "MAX_BULK_ARCHIVE_COUNT",
    "MAX_BULK_MARGIN_APPLY_COUNT",
    "MAX_BULK_SUBMIT_COUNT",
]
