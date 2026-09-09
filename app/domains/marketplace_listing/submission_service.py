"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/submission_service.py

채널별 제출 — Emergency Stop 최우선 확인 → 서버에 저장된 유효한
APPROVED 승인 확인(클라이언트 Boolean 없음) → 저장된 required_fields를
Schema로 재검증 → 검증된 typed data만 Adapter에 전달 → Adapter 출력을
outbound Schema로 재검증 → 채널별 독립 처리(한 채널의 실패가 다른
채널에 영향을 주지 않음) → idempotency_key로 중복 제출 방지.

실제 Marketplace API를 호출하지 않는다 — Adapter는 구조 변환만
수행하고(app/domains/marketplace_listing/adapters), 이 서비스는 그
결과를 append-only MarketplaceSubmission 행으로 기록할 뿐이다.

이번 재감사 반영:
  - operator_approved_by 파라미터를 완전히 제거했다. 승인 여부는
    ApprovalService.current_valid_approval()의 서버측 조회 결과로만
    판단한다 — 요청자가 스스로 "승인됨"을 자칭할 수 없다.
  - adapter.translate(mode, {})처럼 빈 dict를 넘기던 자리를, 저장된
    required_fields_json을 파싱→Schema 재검증→typed data 전달로
    교체했다.
  - SafetyService.evaluate()에 하드코딩됐던 funding_amount=0,
    quantity=0을 승인 스냅샷(planned_quantity, unit_cost_of_goods,
    expected_logistics_cost)에서 계산한 실제 Decimal 값으로 교체했다.

2026-08-01 CTO 3차 지적 반영 — submit()이 company_id를 필수 인자로
받는다. listing_id/selection_id는 항상 company 스코프로 조회하며,
다른 회사의 객체를 지정하면 존재하지 않는 것과 동일한 404로 거부된다
(idempotency 재호출도 company 범위로만 조회한다).
=========================================================
"""

import json
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.automation_safety.constants import SafetyDecision
from app.domains.automation_safety.service import SafetyService
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.marketplace_listing.adapters.coupang_adapter import (
    CoupangFulfillmentAdapter,
)
from app.domains.marketplace_listing.adapters.naver_adapter import (
    NaverFulfillmentAdapter,
)
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.constants import ChannelCode
from app.domains.marketplace_listing.constants import EligibilityState
from app.domains.marketplace_listing.constants import ListingStatus
from app.domains.marketplace_listing.constants import SubmissionStatus
from app.domains.marketplace_listing.eligibility_service import (
    EligibilityService,
)
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.marketplace_listing.outbound_schemas import (
    get_outbound_schema,
)
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.marketplace_listing.required_fields_schemas import (
    get_required_fields_schema,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionRequest,
)
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)

ADAPTERS_BY_CHANNEL_CODE = {
    ChannelCode.COUPANG: CoupangFulfillmentAdapter(),
    ChannelCode.NAVER_SMARTSTORE: NaverFulfillmentAdapter(),
}

_LISTING_NOT_FOUND = "MarketplaceListing을 찾을 수 없습니다."
_SELECTION_NOT_FOUND = "MarketplaceFulfillmentSelection을 찾을 수 없습니다."
_ACCOUNT_NOT_FOUND = "MarketplaceAccount를 찾을 수 없습니다."


class SubmissionService:

    def __init__(self, db: Session, safety_service: SafetyService | None = None):

        self.db = db
        self.repository = MarketplaceListingRepository(db)
        self.eligibility_service = EligibilityService(db)
        self.approval_service = ApprovalService(db)
        self.safety_service = safety_service or SafetyService(db)
        self.channel_policy_service = ChannelPolicyService(db)

    def submit(
        self, data: MarketplaceSubmissionRequest, company_id: int,
    ) -> MarketplaceSubmission:
        """
        채널 하나에 대한 제출을 시도한다. 호출자(router.py)는 여러
        채널에 대해 이 메서드를 독립적으로 반복 호출한다 — 한 채널의
        예외/timeout이 다른 채널 호출에 영향을 주지 않는다.

        승인은 이 메서드가 받는 파라미터가 아니라, 이 메서드 내부에서
        ApprovalService를 통해 서버 DB를 조회해서만 판단한다 — 승인
        요청/승인/거절/취소는 전부 별도의 API 호출이다.
        """

        existing = self.repository.get_submission_by_company_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing

        # 감사 기록에 실제 listing/account id를 남기기 위해 조회는
        # 먼저 하되(읽기 전용, 부작용 없음), 그 이후의 모든 비즈니스
        # 로직(자격·안전성 판단)보다 Emergency Stop 확인이 우선한다.
        listing = self.repository.get_listing_for_company(
            data.listing_id, company_id,
        )
        if listing is None:
            raise NotFoundException(_LISTING_NOT_FOUND)

        if self.safety_service.is_emergency_stop_active():
            return self._record(
                data, company_id, listing.id, listing.marketplace_account_id,
                status=SubmissionStatus.FAILED,
                safety_decision=SafetyDecision.DENY,
                operator_approved_by=None,
                error_reason="Emergency Stop이 활성화되어 있어 제출할 수 없습니다.",
            )

        selection = self.repository.get_selection_for_company(
            data.selection_id, company_id,
        )
        if selection is None:
            raise NotFoundException(_SELECTION_NOT_FOUND)
        if selection.status != "SELECTED":
            raise BadRequestException(
                "SUPERSEDED된 선택으로는 제출할 수 없습니다 — 현재 "
                "선택된 방식으로 다시 시도하세요.",
            )
        if selection.listing_id != listing.id:
            raise BadRequestException("selection이 이 listing에 속하지 않습니다.")

        account = self.repository.get_account_for_company(
            listing.marketplace_account_id, company_id,
        )
        if account is None:
            raise NotFoundException(_ACCOUNT_NOT_FOUND)

        capability = self.repository.get_capability(selection.capability_id)
        if capability is None:
            raise NotFoundException(
                "MarketplaceFulfillmentCapability를 찾을 수 없습니다.",
            )

        # 자격이 필요한 방식은 VERIFIED(그리고 만료되지 않음)만 제출
        # 가능하다 — UNKNOWN은 절대 허용이 아니다(fail-closed).
        if capability.requires_eligibility_check:
            state = self.eligibility_service.current_state(
                account.id, selection.fulfillment_mode, company_id,
            )
            if state not in EligibilityState.USABLE:
                return self._record(
                    data, company_id, listing.id, account.id,
                    status=SubmissionStatus.FAILED,
                    safety_decision=SafetyDecision.DENY,
                    operator_approved_by=None,
                    error_reason=(
                        f"자격 상태가 사용 가능하지 않습니다(state={state}) "
                        "— 제출을 차단합니다."
                    ),
                )

        # CA-1(2026-08-21) — 제출 직전 채널 정책 강제 재검사. 이
        # 지점은 listing_wizard_precheck.py의 draft-time 안내(non-
        # blocking, "작성 중 정책 알림 표시" 토글로 화면 노출만 제어)
        # 와 달리 프런트엔드가 절대 우회할 수 없다. current_valid_
        # channel_policy()는 (a) 최신 평가가 SUBMITTABLE 결과인지,
        # (b) 지금 다시 계산한 입력 지문(상품·가격·옵션·배송·이미지)이
        # 평가 당시와 정확히 같은지, (c) 정책 프로필 버전이 그대로인지
        # 전부 재확인한다(app/domains/marketplace_listing/
        # approval_service.py::current_valid_approval()와 동일한
        # fail-closed 철학). 평가가 아예 없거나(POLICY_NOT_EVALUATED)
        # BLOCKED/DATA_REQUIRED거나(POLICY_BLOCKING_RESULT) 지문·
        # 버전이 달라졌으면(POLICY_STALE) 전부 제출을 막는다.
        channel_code_for_policy = self._channel_code(account.channel_id)
        valid_policy = self.channel_policy_service.current_valid_channel_policy(
            company_id=company_id,
            product_candidate_id=listing.product_candidate_id,
            channel=channel_code_for_policy,
            selection=selection,
        )
        if valid_policy is None:
            failed_submission = self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=SafetyDecision.DENY,
                operator_approved_by=None,
                error_reason=(
                    "CHANNEL_POLICY_GATE_FAILED: 채널 정책 검사를 통과한 "
                    "유효한 평가가 없습니다 — 정책 검사를 실행(또는 "
                    "재실행)한 뒤 다시 시도하세요. 평가 없음/BLOCKED/"
                    "DATA_REQUIRED/입력 변경(가격·옵션·이미지·배송)으로 "
                    "인한 지문 불일치/정책 버전 변경 중 하나입니다."
                ),
            )

            # Section 3(2026-08-24) — CHANNEL_POLICY_VIOLATION. 이
            # 시점은 실제 제출 시도(프런트엔드가 우회할 수 없는 서버측
            # 게이트)이지 화면 미리보기가 아니다. 최신 저장 평가가
            # 실제로 BLOCKED/DATA_REQUIRED일 때만 발송한다 — 평가
            # 자체가 없거나(미평가) STALE(과거엔 SUBMITTABLE이었으나
            # 그 사이 입력이 바뀐 것)뿐이면 "정책 위반"이 아니므로
            # 발송하지 않는다.
            summary = (
                self.channel_policy_service
                .get_latest_evaluation_violation_fingerprint(
                    company_id, listing.product_candidate_id,
                    channel_code_for_policy,
                )
            )
            if summary is not None and summary[0] in (
                "CHANNEL_POLICY_BLOCKED", "CHANNEL_DATA_REQUIRED",
            ):
                result_code, fingerprint = summary
                dispatch_operational_event(
                    self.db, "CHANNEL_POLICY_VIOLATION",
                    company_id=company_id, user_id=None,
                    idempotency_key=(
                        f"channel-policy-violation:{listing.id}:"
                        f"{selection.id}:{result_code}:{fingerprint}"
                    ),
                    title="판매채널 정책 위반 또는 필수정보가 부족합니다",
                    message=(
                        f"상품 등록 #{listing.id}이 채널({channel_code_for_policy}) "
                        "정책 검사를 통과하지 못해 제출이 차단됐습니다."
                    ),
                    link_path="marketplace-listing",
                    entity_ref=f"marketplace_listing:{listing.id}",
                    reason=result_code,
                    entity_summary=f"상품 등록 #{listing.id}",
                )

            return failed_submission

        # Critical 재감사 수정 — 클라이언트가 자칭하는 Boolean이 아니라
        # 서버 DB에 저장된, 지금 이 순간에도 여전히 유효한(만료되지
        # 않고 fingerprint·정책버전이 그대로인) APPROVED 승인만 인정한다.
        approval = self.approval_service.current_valid_approval(
            listing.id, selection.id, company_id,
        )
        if approval is None:
            raise ForbiddenException(
                "유효한 운영자 승인이 없습니다 — 승인 요청 후 별도로 "
                "승인을 받아야 제출할 수 있습니다(승인과 제출은 서로 "
                "다른 API 호출입니다). 이미 승인이 있었더라도 그 사이 "
                "선택·가격·수량·필수 입력이 바뀌었으면 승인이 "
                "무효화되어 다시 요청해야 합니다.",
            )

        # 2026-08-01 Gate 5(CTO 2차 지적) — 실제 제출 시도에 들어가는
        # 순간부터 listing.status를 SUBMITTING으로 표시한다(순수
        # 표시용, rowcount 무시 — 실제 인가는 위 approval 확인뿐이다).
        # 이 지점 이후의 모든 _record() 호출은 update_listing_status=
        # True로 SUBMITTING→SUBMITTED/FAILED 전이를 시도한다.
        self.repository.update_listing_status_conditional(
            listing.id, company_id,
            (ListingStatus.APPROVED,), ListingStatus.SUBMITTING,
        )

        # High 재감사 수정 — funding_amount=0, quantity=0 하드코딩을
        # 제거하고 승인 스냅샷에서 실제 Decimal 값을 계산한다. 유효하지
        # 않은 값(음수 등)은 0으로 보정하지 않고 명시적으로 차단한다.
        quantity = approval.planned_quantity
        funding_amount = (
            Decimal(approval.unit_cost_of_goods) * Decimal(quantity)
            + Decimal(approval.expected_logistics_cost)
        )
        if quantity <= 0 or funding_amount < 0:
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=SafetyDecision.DENY,
                operator_approved_by=None,
                error_reason=(
                    "INSUFFICIENT_SAFETY_INPUT: 승인에 기록된 수량/"
                    "자금 입력이 유효하지 않습니다."
                ),
                update_listing_status=True,
            )

        # SafetyService(app/domains/automation_safety)는 이 도메인과
        # 별개로 이미 Float를 쓰는 기존 도메인이다(443개 회귀의 일부,
        # 이번 재감사 범위 밖). 이 Domain 내부는 끝까지 Decimal로
        # 계산하고, 그 경계를 넘는 이 한 호출 지점에서만 float()로
        # 캐스팅한다.
        safety_result = self.safety_service.evaluate(
            idempotency_key=data.idempotency_key,
            product_id=None,
            funding_amount=float(funding_amount),
            quantity=quantity,
            operator_approved=True,
        )

        if safety_result["decision"] != SafetyDecision.ALLOW:
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=safety_result["decision"],
                operator_approved_by=None,
                error_reason=",".join(safety_result.get("reasons", [])),
                update_listing_status=True,
            )

        channel_code = self._channel_code(account.channel_id)

        # High 재감사 수정 — 저장된 required_fields_json을 파싱하고,
        # 선택 시점에 저장된 schema_name/version이 지금의 레지스트리와
        # 여전히 일치하는지 확인한 뒤 재검증한다. 손상·불일치·검증
        # 실패는 전부 fail-closed(제출 차단)다.
        schema_entry = get_required_fields_schema(
            channel_code, selection.fulfillment_mode,
        )
        if schema_entry is None:
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason="이 채널·방식에 대한 필수 입력 Schema가 없습니다.",
                update_listing_status=True,
            )

        schema_cls, schema_name, schema_version = schema_entry
        if (
            selection.required_fields_schema_name,
            selection.required_fields_schema_version,
        ) != (schema_name, schema_version):
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason=(
                    "저장된 필수 입력 Schema 버전이 현재 레지스트리와 "
                    "다릅니다 — 방식을 다시 선택하세요."
                ),
                update_listing_status=True,
            )

        try:
            raw_fields = json.loads(selection.required_fields_json)
        except json.JSONDecodeError:
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason=(
                    "저장된 필수 입력 JSON이 손상되었습니다(fail-closed)."
                ),
                update_listing_status=True,
            )

        try:
            validated_fields = schema_cls.model_validate(raw_fields)
        except ValidationError as exc:
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason=f"필수 입력 재검증 실패: {exc}",
                update_listing_status=True,
            )

        adapter = ADAPTERS_BY_CHANNEL_CODE.get(channel_code)

        if adapter is None:
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason="이 채널에 대한 Adapter가 없습니다.",
                update_listing_status=True,
            )

        validated_payload = json.loads(validated_fields.model_dump_json())

        try:
            translated = adapter.translate(
                selection.fulfillment_mode, validated_payload,
            )
        except Exception as exc:  # noqa: BLE001
            # 한 채널의 예외가 다른 채널 호출에 영향을 주지 않도록 이
            # 자리에서 잡고 UNKNOWN으로 기록한다(성공/실패를 추론하지
            # 않는다).
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.UNKNOWN,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason=f"어댑터 예외: {type(exc).__name__}",
                update_listing_status=True,
            )

        if translated == "NOT_SUPPORTED":
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason=(
                    f"{selection.fulfillment_mode} 방식은 이 채널에서 "
                    "지원되지 않습니다(NOT_SUPPORTED)."
                ),
                update_listing_status=True,
            )

        # High 재감사 수정 — Adapter 출력도 채널별 outbound Schema로
        # 다시 검증한다(Adapter 내부 버그가 그대로 "성공"으로 기록되는
        # 것을 막는다).
        outbound_schema = get_outbound_schema(
            channel_code, selection.fulfillment_mode,
        )
        if outbound_schema is None:
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.UNKNOWN,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason="outbound Schema가 등록되지 않았습니다.",
                update_listing_status=True,
            )

        try:
            outbound_schema.model_validate(translated)
        except ValidationError as exc:
            return self._record(
                data, company_id, listing.id, account.id,
                status=SubmissionStatus.FAILED,
                safety_decision=safety_result["decision"],
                operator_approved_by=approval.approved_by,
                error_reason=f"outbound 페이로드 검증 실패: {exc}",
                update_listing_status=True,
            )

        # 이번 phase는 실제 네트워크 호출을 하지 않는다 — 구조 변환·
        # 검증이 성공하면 PENDING으로 기록한다(실제 제출 성공을
        # 의미하지 않음). listing.status는 SUBMITTED로 전이하되, 이는
        # "우리 시스템 검증 통과"를 뜻할 뿐 실제 마켓 공개가 아니다
        # (Desktop UI가 항상 별도로 LIVE_E2E_PENDING_USER_CREDENTIAL/
        # AWAITING_FINAL_PRODUCT_SUBMISSION을 표시한다).
        return self._record(
            data, company_id, listing.id, account.id,
            status=SubmissionStatus.PENDING,
            safety_decision=safety_result["decision"],
            operator_approved_by=approval.approved_by,
            error_reason=None,
            update_listing_status=True,
        )

    def _channel_code(self, channel_id: int) -> str | None:

        channel = self.repository.get_channel(channel_id)

        return channel.code if channel is not None else None

    def _record(
        self,
        data: MarketplaceSubmissionRequest,
        company_id: int,
        listing_id: int | None,
        marketplace_account_id: int | None,
        status: str,
        safety_decision: str,
        operator_approved_by: int | None,
        error_reason: str | None,
        update_listing_status: bool = False,
    ) -> MarketplaceSubmission:
        """
        update_listing_status=True는 이 호출이 SUBMITTING 진입 이후의
        경로에서 왔다는 뜻이다(2026-08-01 Gate 5) — 이 경우 listing의
        표시용 상태를 SUBMITTING→SUBMITTED(PENDING)/FAILED로 정리한다.
        UNKNOWN(어댑터 예외, outbound Schema 없음 등 "성공/실패를
        추론할 수 없는" 경우)은 SUBMITTING에 그대로 둔다 — 사람이
        확인해야 한다는 신호를 유지한다(추측하지 않는다).
        """

        submission = MarketplaceSubmission(
            company_id=company_id,
            listing_id=listing_id or 0,
            selection_id=data.selection_id,
            marketplace_account_id=marketplace_account_id or 0,
            status=status,
            safety_decision=safety_decision,
            operator_approved_by=operator_approved_by,
            operator_approved_at=(
                datetime.utcnow() if operator_approved_by is not None else None
            ),
            error_reason=error_reason,
            idempotency_key=data.idempotency_key,
            attempted_at=datetime.utcnow(),
        )

        try:
            submission = self.repository.add_submission_no_commit(submission)

            if update_listing_status and listing_id:
                if status == SubmissionStatus.PENDING:
                    self.repository.update_listing_status_conditional(
                        listing_id, company_id,
                        (ListingStatus.SUBMITTING,), ListingStatus.SUBMITTED,
                    )
                elif status == SubmissionStatus.FAILED:
                    self.repository.update_listing_status_conditional(
                        listing_id, company_id,
                        (ListingStatus.SUBMITTING,), ListingStatus.FAILED,
                    )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_submission_by_company_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner

        except Exception:
            self.db.rollback()
            raise

        return submission

    def list_submissions(
        self, listing_id: int, company_id: int,
    ) -> list[MarketplaceSubmission]:

        return self.repository.list_submissions_for_listing(
            listing_id, company_id,
        )


__all__ = [
    "SubmissionService",
]
