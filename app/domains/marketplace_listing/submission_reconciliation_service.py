"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/submission_reconciliation_service.py

2026-08-30 V7 후속 안정화 Phase 5 — 성공 제출 정합화.

배경: 운영 DB의 marketplace_submissions id=10(파싱 버그 이전 시점 —
coupang_live_provider.py::create_product()가 쿠팡의 실제 성공 응답
모양(중첩 없는 flat {"code":"SUCCESS","data":<숫자>})을 중첩 모양으로
잘못 가정해 INVALID_RESPONSE/UNKNOWN으로 오판정)이 실제로는 성공(실
WING 화면에서 사용자가 직접 확인 — 등록상품ID는 감사 증거 문서에만
보존, 이 파일에는 미기재)이었는데도
external_submission_ref가 비어 있는 채로 남아 있다.

`marketplace_submissions`는 append-only 감사 기록이다 — 이 서비스는
그 행을 절대 UPDATE하지 않는다. 대신 `MarketplaceSubmissionReconciliation`
에 별도 행을 append해 "이 submission_id는 이런 근거로 정합화됐다"는
사실만 추가한다. 운영자가 실제 화면에서 직접 확인한 sellerProductId를
입력받아, 상태 조회 Provider(Fake 또는 실제)로 그 ID가 실제로 존재·
승인 상태인지 재확인한 뒤에만 기록한다 — 추측으로 채우지 않는다.

2026-08-31 V7 필수 작업 2번(제출 장부 정합화 완성) — 이 지시가 요구한
사항을 반영해 아래를 새로 추가한다:
  1) 정합화 대상 원본 상태를 PENDING/SUBMITTING/UNKNOWN으로 명시적으로
     제한한다(그 외 상태는 이 기능의 대상이 아니다 — 특히 FAILED는
     쿠팡이 명시적으로 거부한 것이라 "장부 불일치"가 아니다).
  2) 같은 sellerProductId가 이미 다른 제출 건(같은 회사든 다른
     회사든)이나 다른 정합화 행에 연결돼 있으면 차단한다(중복 연결
     방지 — 회사·계정·위저드가 다른 제출에 같은 외부 ID가 잘못
     연결되는 사고를 막는다).
  3) 상품명·vendorUserId·카테고리 등 비교 가능한 식별값을 대조해
     모순되면 fail-closed로 막는다(submission_reconciliation_
     matching.py).
  4) 실제 기록(DB 쓰기)과 미리보기(dry-run, 아무것도 쓰지 않음)를
     분리한다 — preview_reconciliation()은 순수 조회+Provider 호출만
     하고, apply_reconciliation()만 실제로 append한다.
  5) 동시 적용 경쟁 상태를 감내한다 — 두 요청이 동시에 같은
     submission_id를 정합화하려 하면, DB의 UNIQUE 제약
     (uq_marketplace_submission_reconciliations_submission_id)이 뒤에
     커밋되는 쪽을 막고, 이 서비스는 그 경우를 500 에러가 아니라
     ALREADY_RECONCILED로 정상 반환한다.

실제 쿠팡 상태 조회 API 호출은 이 서비스 코드·Fake 테스트가 전부
끝난 뒤, 사용자가 명시적으로 승인할 때만 실행한다(이 세션 안전 원칙).
=========================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.marketplace_listing.constants import SubmissionStatus
from app.domains.marketplace_listing.coupang_live_provider import (
    CoupangProductProvider,
)
from app.domains.marketplace_listing.model import (
    MarketplaceSubmission,
    MarketplaceSubmissionReconciliation,
)
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.marketplace_listing.submission_reconciliation_matching import (
    MATCH_TIER_AUTO_ELIGIBLE,
    MATCH_TIER_BLOCKED,
    MATCH_TIER_MANUAL_REVIEW_REQUIRED,
    IdentifierComparison,
    MatchAssessment,
    evaluate_identifier_match,
)
from app.domains.product_candidate.model import ProductCandidate

# 이 상태들만 "실제로 성공/노출 확정"으로 취급한다 — 그 외(DENIED/
# DELETED 등)는 정합화 대상이 아니다(성공을 추측하지 않는다).
_RECONCILABLE_STATUS_NAMES = frozenset({
    "APPROVED", "PARTIAL_APPROVED", "IN_REVIEW", "APPROVING", "SAVED",
})

# 2026-08-31 — 정합화는 "장부 불일치"(추적을 잃어버린 상태) 대상이다.
# FAILED는 쿠팡이 명시적으로 거부한 결과라 이 기능의 대상이 아니다.
# SUBMITTING은 원자적 선점 이후 결과가 영구히 반영되지 못하고 멈춘
# 상태로(app/domains/marketplace_listing/listing_wizard_live_service.py
# ::send() 감사에서 확인 — 성공 응답을 받고도 최종 업데이트가 실패하면
# 이 상태로 영구히 남을 수 있다), UNKNOWN과 동일한 "결과 불확실" 계열
# 이라 함께 포함한다.
_RECONCILABLE_SOURCE_STATUSES = frozenset({
    SubmissionStatus.PENDING, SubmissionStatus.SUBMITTING, SubmissionStatus.UNKNOWN,
})


@dataclass(frozen=True)
class ReconciliationAssessment:
    """preview_reconciliation()의 순수 조회 결과 — 아무것도 쓰지 않는다."""

    outcome: str
    submission_id: int
    external_submission_ref: str | None = None
    observed_status_name: str | None = None
    match_tier: str | None = None
    matched_fields: tuple[str, ...] = ()
    mismatched_fields: tuple[str, ...] = ()
    unavailable_fields: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True)
class ReconciliationResult:

    outcome: str
    submission_id: int
    external_submission_ref: str | None = None
    observed_status_name: str | None = None
    detail: str | None = None


class MarketplaceSubmissionReconciliationRepository:

    def __init__(self, db: Session):
        self.db = db

    def get_by_submission_id(
        self, submission_id: int,
    ) -> MarketplaceSubmissionReconciliation | None:

        return (
            self.db.query(MarketplaceSubmissionReconciliation)
            .filter(MarketplaceSubmissionReconciliation.submission_id == submission_id)
            .first()
        )

    def get_by_external_submission_ref(
        self, external_submission_ref: str,
    ) -> MarketplaceSubmissionReconciliation | None:

        return (
            self.db.query(MarketplaceSubmissionReconciliation)
            .filter(
                MarketplaceSubmissionReconciliation.external_submission_ref
                == external_submission_ref,
            )
            .first()
        )

    def create_no_commit(
        self,
        *,
        company_id: int,
        submission_id: int,
        external_submission_ref: str,
        observed_status_name: str | None,
        reconciled_by_user_id: int,
        reconciled_at: datetime,
        reason: str,
    ) -> MarketplaceSubmissionReconciliation:

        row = MarketplaceSubmissionReconciliation(
            company_id=company_id, submission_id=submission_id,
            external_submission_ref=external_submission_ref,
            observed_status_name=observed_status_name,
            reconciled_by_user_id=reconciled_by_user_id,
            reconciled_at=reconciled_at, reason=reason,
            created_at=reconciled_at,
        )
        self.db.add(row)
        self.db.flush()

        return row


def _find_submission_using_external_ref(
    db: Session, external_submission_ref: str, exclude_submission_id: int,
) -> MarketplaceSubmission | None:
    """이미 이 sellerProductId를 원본 행에 갖고 있는 다른 제출이
    있는지(회사 무관 — 실제 쿠팡 상품번호는 전역적으로 유일하다)
    확인한다. 자기 자신은 제외한다."""

    return (
        db.query(MarketplaceSubmission)
        .filter(MarketplaceSubmission.external_submission_ref == external_submission_ref)
        .filter(MarketplaceSubmission.id != exclude_submission_id)
        .first()
    )


def _expected_identifiers(
    db: Session, submission: MarketplaceSubmission, company_id: int,
) -> tuple[str | None, str | None, str | None]:
    """HOMEZ가 이 제출 건에 대해 이미 알고 있는 기대값(상품명·
    vendorUserId·카테고리)을 조회한다. 어느 하나라도 찾을 수 없으면
    None으로 둔다(추정하지 않는다 — evaluate_identifier_match()가
    UNAVAILABLE로 처리한다)."""

    marketplace_repo = MarketplaceListingRepository(db)

    listing = marketplace_repo.get_listing_for_company(
        submission.listing_id, company_id,
    )
    expected_product_name = None
    if listing is not None:
        expected_product_name = (
            db.query(ProductCandidate.product_name)
            .filter(ProductCandidate.id == listing.product_candidate_id)
            .scalar()
        )

    selection = marketplace_repo.get_selection_for_company(
        submission.selection_id, company_id,
    )
    expected_vendor_user_id = None
    expected_display_category_code = None
    if selection is not None and selection.required_fields_json:
        try:
            required_fields = json.loads(selection.required_fields_json)
        except (TypeError, ValueError):
            required_fields = {}
        if isinstance(required_fields, dict):
            vendor_user_id = required_fields.get("vendorUserId")
            if isinstance(vendor_user_id, str) and vendor_user_id:
                expected_vendor_user_id = vendor_user_id
            category_code = required_fields.get("displayCategoryCode")
            if isinstance(category_code, (str, int)) and not isinstance(category_code, bool):
                expected_display_category_code = str(category_code)

    return expected_product_name, expected_vendor_user_id, expected_display_category_code


def _load_submission_for_reconciliation(
    db: Session, submission_id: int, company_id: int,
) -> MarketplaceSubmission:

    marketplace_repo = MarketplaceListingRepository(db)
    submission = marketplace_repo.get_submission_for_company(
        submission_id, company_id,
    )
    if submission is None:
        raise NotFoundException("제출 기록을 찾을 수 없습니다.")

    return submission


def preview_reconciliation(
    db: Session,
    *,
    submission_id: int,
    company_id: int,
    operator_confirmed_seller_product_id: str,
    provider: CoupangProductProvider,
) -> ReconciliationAssessment:
    """
    읽기 전용 미리보기 — DB에 아무 행도 쓰지 않고, 커밋도 하지 않는다.
    운영자가 sellerProductId를 입력하면 "실제로 적용하면 어떻게 될지"
    를 먼저 보여주기 위한 dry-run이다. 실제 쓰기는 apply_reconciliation()
    만 수행한다.
    """

    submission = _load_submission_for_reconciliation(db, submission_id, company_id)

    if submission.external_submission_ref:
        return ReconciliationAssessment(
            outcome="ALREADY_RESOLVED_ON_ORIGINAL_RECORD",
            submission_id=submission_id,
            external_submission_ref=submission.external_submission_ref,
        )

    recon_repo = MarketplaceSubmissionReconciliationRepository(db)
    existing = recon_repo.get_by_submission_id(submission_id)
    if existing is not None:
        return ReconciliationAssessment(
            outcome="ALREADY_RECONCILED", submission_id=submission_id,
            external_submission_ref=existing.external_submission_ref,
            observed_status_name=existing.observed_status_name,
        )

    if submission.status not in _RECONCILABLE_SOURCE_STATUSES:
        return ReconciliationAssessment(
            outcome="INELIGIBLE_SUBMISSION_STATUS", submission_id=submission_id,
            detail=(
                f"status={submission.status}인 제출은 정합화 대상이 "
                "아닙니다(PENDING/SUBMITTING/UNKNOWN만 대상)."
            ),
        )

    duplicate = _find_submission_using_external_ref(
        db, operator_confirmed_seller_product_id, exclude_submission_id=submission_id,
    )
    if duplicate is not None:
        return ReconciliationAssessment(
            outcome="DUPLICATE_EXTERNAL_REF", submission_id=submission_id,
            detail=(
                f"이 sellerProductId는 이미 다른 제출(submission_id="
                f"{duplicate.id})에 연결되어 있습니다 — 같은 상품을 두 "
                "제출에 연결할 수 없습니다."
            ),
        )
    duplicate_recon = recon_repo.get_by_external_submission_ref(
        operator_confirmed_seller_product_id,
    )
    if duplicate_recon is not None:
        return ReconciliationAssessment(
            outcome="DUPLICATE_EXTERNAL_REF", submission_id=submission_id,
            detail=(
                "이 sellerProductId는 이미 다른 정합화 기록(submission_id="
                f"{duplicate_recon.submission_id})에 연결되어 있습니다."
            ),
        )

    status_result = provider.get_product_status(
        operator_confirmed_seller_product_id,
    )

    if status_result.outcome != "FOUND":
        return ReconciliationAssessment(
            outcome="STATUS_NOT_CONFIRMED", submission_id=submission_id,
            detail=status_result.error_summary or status_result.outcome,
        )

    if status_result.status_name not in _RECONCILABLE_STATUS_NAMES:
        return ReconciliationAssessment(
            outcome="STATUS_NOT_SUCCESS", submission_id=submission_id,
            observed_status_name=status_result.status_name,
            detail=(
                f"상태가 {status_result.status_name}이라 정합화 대상이 "
                "아닙니다(성공으로 추정하지 않습니다)."
            ),
        )

    expected_name, expected_vendor_user_id, expected_category = _expected_identifiers(
        db, submission, company_id,
    )
    assessment = evaluate_identifier_match(
        expected_product_name=expected_name,
        expected_vendor_user_id=expected_vendor_user_id,
        expected_display_category_code=expected_category,
        observed_seller_product_name=status_result.seller_product_name,
        observed_vendor_user_id=status_result.vendor_user_id,
        observed_display_category_code=status_result.display_category_code,
    )

    if assessment.tier == MATCH_TIER_BLOCKED:
        return ReconciliationAssessment(
            outcome="IDENTIFIER_MISMATCH", submission_id=submission_id,
            observed_status_name=status_result.status_name,
            match_tier=assessment.tier,
            matched_fields=assessment.matched_fields,
            mismatched_fields=assessment.mismatched_fields,
            unavailable_fields=assessment.unavailable_fields,
            detail=(
                "입력한 sellerProductId의 정보가 이 제출 건의 기록과 "
                f"모순됩니다(불일치: {', '.join(assessment.mismatched_fields)})."
            ),
        )

    outcome = (
        "READY_AUTO_ELIGIBLE" if assessment.tier == MATCH_TIER_AUTO_ELIGIBLE
        else "READY_MANUAL_REVIEW"
    )
    return ReconciliationAssessment(
        outcome=outcome, submission_id=submission_id,
        external_submission_ref=operator_confirmed_seller_product_id,
        observed_status_name=status_result.status_name,
        match_tier=assessment.tier,
        matched_fields=assessment.matched_fields,
        mismatched_fields=assessment.mismatched_fields,
        unavailable_fields=assessment.unavailable_fields,
    )


def apply_reconciliation(
    db: Session,
    *,
    submission_id: int,
    company_id: int,
    operator_confirmed_seller_product_id: str,
    provider: CoupangProductProvider,
    actor_user_id: int,
    reason: str,
    now: datetime | None = None,
) -> ReconciliationResult:
    """
    실제로 MarketplaceSubmissionReconciliation 행을 append한다 — 이
    함수를 호출하는 것 자체가 "운영 승인"이다(라우터에서 SuperAdminGuard
    로 제한, 이 함수 자체는 재현 가능한 재검증을 다시 전부 수행한다 —
    preview 이후 상태가 바뀌었을 가능성을 신뢰하지 않는다).

    멱등: 이미 정합화된 submission_id를 다시 호출하면 새 행을 만들지
    않고 기존 결과를 그대로 반환한다(ALREADY_RECONCILED). 두 요청이
    동시에 도착해도(TOCTOU) DB UNIQUE 제약이 두 번째 커밋을 막고,
    이 함수는 그 경우도 예외 대신 ALREADY_RECONCILED로 반환한다.
    """

    if not reason or not reason.strip():
        raise BadRequestException("정합화 사유(reason)를 반드시 입력해야 합니다.")

    submission = _load_submission_for_reconciliation(db, submission_id, company_id)

    recon_repo = MarketplaceSubmissionReconciliationRepository(db)
    existing = recon_repo.get_by_submission_id(submission_id)
    if existing is not None:
        return ReconciliationResult(
            outcome="ALREADY_RECONCILED", submission_id=submission_id,
            external_submission_ref=existing.external_submission_ref,
            observed_status_name=existing.observed_status_name,
        )

    if submission.external_submission_ref:
        return ReconciliationResult(
            outcome="ALREADY_RESOLVED_ON_ORIGINAL_RECORD",
            submission_id=submission_id,
            external_submission_ref=submission.external_submission_ref,
        )

    if submission.status not in _RECONCILABLE_SOURCE_STATUSES:
        raise BadRequestException(
            f"status={submission.status}인 제출은 정합화 대상이 아닙니다"
            "(PENDING/SUBMITTING/UNKNOWN만 대상).",
        )

    duplicate = _find_submission_using_external_ref(
        db, operator_confirmed_seller_product_id, exclude_submission_id=submission_id,
    )
    if duplicate is not None:
        raise BadRequestException(
            "이 sellerProductId는 이미 다른 제출에 연결되어 있어 "
            "정합화를 적용할 수 없습니다(중복 연결 차단).",
        )
    if recon_repo.get_by_external_submission_ref(
        operator_confirmed_seller_product_id,
    ) is not None:
        raise BadRequestException(
            "이 sellerProductId는 이미 다른 정합화 기록에 연결되어 있어 "
            "적용할 수 없습니다(중복 연결 차단).",
        )

    status_result = provider.get_product_status(
        operator_confirmed_seller_product_id,
    )

    if status_result.outcome != "FOUND":
        return ReconciliationResult(
            outcome="STATUS_NOT_CONFIRMED", submission_id=submission_id,
            detail=status_result.error_summary or status_result.outcome,
        )

    if status_result.status_name not in _RECONCILABLE_STATUS_NAMES:
        return ReconciliationResult(
            outcome="STATUS_NOT_SUCCESS", submission_id=submission_id,
            observed_status_name=status_result.status_name,
            detail=(
                f"상태가 {status_result.status_name}이라 정합화 대상이 "
                "아닙니다(성공으로 추정하지 않습니다)."
            ),
        )

    expected_name, expected_vendor_user_id, expected_category = _expected_identifiers(
        db, submission, company_id,
    )
    assessment = evaluate_identifier_match(
        expected_product_name=expected_name,
        expected_vendor_user_id=expected_vendor_user_id,
        expected_display_category_code=expected_category,
        observed_seller_product_name=status_result.seller_product_name,
        observed_vendor_user_id=status_result.vendor_user_id,
        observed_display_category_code=status_result.display_category_code,
    )
    if assessment.tier == MATCH_TIER_BLOCKED:
        raise BadRequestException(
            "입력한 sellerProductId의 정보가 이 제출 건의 기록과 "
            f"모순되어 적용할 수 없습니다(불일치: "
            f"{', '.join(assessment.mismatched_fields)}).",
        )

    reconciled_at = now or datetime.utcnow()
    snapshot_before = (
        f"before: status={submission.status}, "
        f"external_submission_ref={submission.external_submission_ref or '-'}"
    )
    snapshot_after = (
        f"after: reconciliation.external_submission_ref="
        f"{operator_confirmed_seller_product_id}, observed_status="
        f"{status_result.status_name}, match_tier={assessment.tier}, "
        f"matched_fields={','.join(assessment.matched_fields) or '-'}, "
        f"unavailable_fields={','.join(assessment.unavailable_fields) or '-'}"
    )

    try:
        recon_repo.create_no_commit(
            company_id=company_id, submission_id=submission_id,
            external_submission_ref=operator_confirmed_seller_product_id,
            observed_status_name=status_result.status_name,
            reconciled_by_user_id=actor_user_id, reconciled_at=reconciled_at,
            reason=reason,
        )
        write_audit_log(
            db, user_id=actor_user_id, action="MARKETPLACE_SUBMISSION_RECONCILED",
            entity="marketplace_submission", entity_id=str(submission_id),
            description=(
                f"제출 정합화: submission_id={submission_id}; {snapshot_before}; "
                f"{snapshot_after}; 사유={reason}"
            ),
            company_id=company_id,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = recon_repo.get_by_submission_id(submission_id)
        if winner is None:
            raise
        return ReconciliationResult(
            outcome="ALREADY_RECONCILED", submission_id=submission_id,
            external_submission_ref=winner.external_submission_ref,
            observed_status_name=winner.observed_status_name,
        )
    except Exception:
        db.rollback()
        raise

    return ReconciliationResult(
        outcome="RECONCILED", submission_id=submission_id,
        external_submission_ref=operator_confirmed_seller_product_id,
        observed_status_name=status_result.status_name,
    )


__all__ = [
    "ReconciliationAssessment",
    "ReconciliationResult",
    "MarketplaceSubmissionReconciliationRepository",
    "preview_reconciliation",
    "apply_reconciliation",
]
