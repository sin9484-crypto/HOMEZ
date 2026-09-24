"""Live Coupang hand-off for an already prepared wizard submission."""

from __future__ import annotations

import json
import hashlib
import uuid
from dataclasses import dataclass
from dataclasses import replace

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException, ConflictException
from app.core.exceptions import ForbiddenException, NotFoundException
from app.core.audit_db import write_audit_log
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.marketplace_listing.approval_service import ApprovalService
from app.domains.marketplace_listing.constants import SubmissionStatus
from app.domains.marketplace_listing.coupang_image_autofill import (
    autofill_coupang_images,
)
from app.domains.marketplace_listing.coupang_live_payload import (
    build_coupang_live_payload,
)
from app.domains.marketplace_listing.coupang_live_provider import (
    CoupangProductProvider, LiveSubmissionResult,
)
from app.domains.marketplace_listing.listing_wizard_repository import (
    ListingWizardRepository,
)
from app.domains.marketplace_listing.listing_wizard_approval import (
    build_approval_package,
)
from app.domains.marketplace_listing.repository import MarketplaceListingRepository
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)


@dataclass(frozen=True)
class LivePreflight:
    ready: bool
    blockers: list[str]
    submission_id: int
    listing_id: int


class ListingWizardLiveService:
    def __init__(self, db: Session):
        self.db = db
        self.wizards = ListingWizardRepository(db)
        self.marketplace = MarketplaceListingRepository(db)
        self.safety = SafetyService(db)
        self.approvals = ApprovalService(db)

    def _context(self, wizard_id: int, submission_id: int, company_id: int):
        wizard = self.wizards.get_for_company(wizard_id, company_id)
        if wizard is None:
            raise NotFoundException("상품등록 작업을 찾을 수 없습니다.")
        submission = self.marketplace.get_submission_for_company(
            submission_id, company_id,
        )
        if submission is None:
            raise NotFoundException("제출 작업을 찾을 수 없습니다.")
        listing = self.marketplace.get_listing_for_company(
            submission.listing_id, company_id,
        )
        if listing is None or listing.id != submission.listing_id:
            raise NotFoundException("제출 대상 상품을 찾을 수 없습니다.")
        materialized = json.loads(wizard.materialized_listing_ids_json or "[]")
        if not any(
            row.get("listing_id") == listing.id
            and row.get("marketplace_account_id") == submission.marketplace_account_id
            for row in materialized
        ):
            raise BadRequestException("제출 작업이 이 상품등록 흐름에 속하지 않습니다.")
        selection = self.marketplace.get_selection_for_company(
            submission.selection_id, company_id,
        )
        if selection is None or selection.listing_id != listing.id:
            raise BadRequestException("제출 판매방식 정보가 일치하지 않습니다.")
        return wizard, submission, listing, selection

    def submission_context(self, wizard_id: int, submission_id: int, company_id: int):
        """옵션 연결처럼 다른 서비스가 같은 소유권 검증을 재사용하도록 여는 진입점."""

        return self._context(wizard_id, submission_id, company_id)

    def _build(self, wizard, submission, selection) -> tuple[dict | None, list[str]]:
        entries = json.loads(wizard.channel_selections_json or "[]")
        entry = next((x for x in entries if x.get("marketplace_account_id") == submission.marketplace_account_id), None)
        if entry is None:
            return None, ["CHANNEL_SELECTION_REQUIRED"]
        required_fields = dict(entry.get("required_fields") or {})
        # 2026-08-27 추가 — 프론트엔드가 images를 직접 채워 보냈으면
        # 절대 덮어쓰지 않는다(사용자의 명시적 선택 우선). 비어 있을
        # 때만, preflight/실제 전송 시점에만(위저드 중간 저장마다가
        # 아니라) 위저드 2단계(선택 media_asset_ids)에서 자동으로
        # 채운다 — R2 공개 업로드 포함(idempotent, 이미 업로드된
        # 자산은 media_assets.public_url을 그대로 재사용한다).
        if not required_fields.get("images"):
            try:
                selected_media_asset_ids = json.loads(
                    wizard.selected_media_asset_ids_json or "[]",
                )
                required_fields["images"] = autofill_coupang_images(
                    self.db, wizard.company_id, selected_media_asset_ids,
                    WindowsCredentialStore(),
                )
            except Exception:  # noqa: BLE001 — 자동 채움은 best-effort다.
                # 선택된 자산이 없거나, 파일이 없거나, R2가 아직 설정
                # 안 됐거나, 권리 미확인이거나 — 어떤 이유로 실패하든
                # images를 채우지 못했을 뿐이다. required_fields["images"]
                # 를 비운 채로 두면 아래 build_coupang_live_payload()의
                # 표준 검증(PUBLIC_IMAGE_URL_REQUIRED 등)이 원래
                # 계약대로 정확한 사유를 판정한다 — 여기서 별도
                # 코드로 크래시시키거나 다른 블로커를 새로 만들지
                # 않는다(이 자동 채움 기능이 아예 없던 때와 동일한
                # 실패 경로로 자연스럽게 되돌아간다).
                pass
        return build_coupang_live_payload(
            draft=json.loads(wizard.draft_json or "{}"),
            required_fields=required_fields,
            channel_policy_attributes=entry.get("channel_policy_attributes") or {},
        )

    def _approval_payload_unchanged(self, wizard, company_id: int) -> bool:
        if not wizard.approval_fingerprint or not wizard.approval_package_json:
            return False
        try:
            approved_package = json.loads(wizard.approval_package_json)
            approved_precheck = approved_package["precheck_status"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        _package, current_fingerprint = build_approval_package(
            wizard, self.db, company_id, approved_precheck,
        )
        return current_fingerprint == wizard.approval_fingerprint

    def preflight(
        self, wizard_id: int, submission_id: int, company_id: int,
    ) -> LivePreflight:
        wizard, submission, listing, selection = self._context(
            wizard_id, submission_id, company_id,
        )
        blockers: list[str] = []
        if submission.status != SubmissionStatus.PENDING:
            blockers.append("SUBMISSION_NOT_PENDING")
        if submission.external_submission_ref:
            blockers.append("ALREADY_SUBMITTED")
        if submission.request_fingerprint or submission.correlation_id:
            blockers.append("SUBMISSION_ALREADY_CLAIMED")
        # 2026-09-24 후속(자동 재신청 방지 라운드 3 — 중복 상품등록
        # 차단) — 위 세 검사는 전부 "이 submission 행 자신"만 본다.
        # 같은 (company_id, listing_id)에 대해 **다른** submission 행이
        # 이미 실제 전송을 시도했다면(correlation_id가 있다는 것 자체가
        # send()를 실제로 탔다는 뜻 — 구조적 검증만 하는 submission_
        # service.submit()은 correlation_id를 절대 설정하지 않는다)
        # 그 사실을 이 submission 행만 봐서는 알 수 없다 — 예를 들어
        # 같은 상품 후보·같은 판매계정으로 새 위저드를 다시 만들면
        # 새 idempotency_key로 새 submission 행이 생기고, 그 행은
        # 자기 자신의 필드만 보면 "아직 전송 안 함"처럼 보인다. 그래서
        # 같은 listing_id를 참조하는 다른 모든 행을 함께 조회해
        # 확인한다. 성공(SUBMITTED, external_submission_ref 있음)은
        # 재등록을 절대 허용하지 않는다. 결과불명(UNKNOWN)·아직 응답
        # 대기 중(SUBMITTING)도 "외부에 이미 생겼을 수 있다"는 사실이
        # 해소되지 않았으므로 막는다. 실제로 명시적 거절이 확인된
        # FAILED는 막지 않는다 — 정상적인 수정 후 재등록 경로까지
        # 막지 않기 위함이다(사용자 지시 원문). 이 SELECT 자체는
        # TOCTOU 경쟁을 완전히 막지 못한다 — 최종 방어선은 §claim
        # 지점의 부분 UNIQUE INDEX다(모델 참고).
        for other in self.marketplace.list_submissions_for_listing(
            listing.id, company_id,
        ):
            if other.id == submission.id:
                continue
            if other.correlation_id and (
                other.external_submission_ref
                or other.status in (
                    SubmissionStatus.SUBMITTING, SubmissionStatus.UNKNOWN,
                )
            ):
                blockers.append("DUPLICATE_LIVE_ATTEMPT_ON_SAME_LISTING")
                break
        if self.safety.is_emergency_stop_active():
            blockers.append("ESTOP_ACTIVE")
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            blockers.append("OPERATOR_APPROVAL_MODE_REQUIRED")
        if self.approvals.current_valid_approval(
            listing.id, selection.id, company_id,
        ) is None:
            blockers.append("VALID_APPROVAL_REQUIRED")
        if not self._approval_payload_unchanged(wizard, company_id):
            blockers.append("APPROVED_PAYLOAD_CHANGED")
        # 2026-09-15 전면 감사 후속(Phase 9G, HOMEZ_USER_OPERATION_
        # SETTINGS.md 10-4) — 이 후보(candidate)에 대한 가장 최근
        # 상품 속성 비교(이름/옵션/수량/사이즈/제조사/원산지)가
        # 불일치·확인불가로 차단(BLOCKED)된 채 아직 해소되지 않았으면
        # 실제 쿠팡 전송을 막는다. 비교를 실행한 적이 없으면(레코드
        # 없음) 통과한다 — 이 블로커는 "비교를 실행하라"는 요구가
        # 아니라 "이미 실행된 비교 결과를 무시하지 않는다"는 게이트다.
        if wizard.product_candidate_id is not None:
            from app.domains.product_attribute_match.service import (
                ProductAttributeMatchService,
            )

            if ProductAttributeMatchService(self.db).has_blocking_attribute_mismatch(
                company_id, f"candidate:{wizard.product_candidate_id}",
            ):
                blockers.append("PRODUCT_ATTRIBUTE_MISMATCH_BLOCKED")

            # 2026-09-15 전면 감사 후속(Phase 9J, HOMEZ_USER_OPERATION_
            # SETTINGS.md 10-18) — 이 후보에 대해 리콜/판매중지가
            # 확인돼 차단된 상태면 신규 등록(실제 쿠팡 전송)을 막는다.
            from app.domains.recall_notice.service import RecallNoticeService

            if RecallNoticeService(self.db).has_active_block(
                company_id, f"candidate:{wizard.product_candidate_id}",
            ):
                blockers.append("RECALL_OR_STOP_SALE_BLOCKED")
        _payload, payload_blockers = self._build(
            wizard, submission, selection,
        )
        blockers.extend(payload_blockers)
        blockers = sorted(set(blockers))
        return LivePreflight(
            ready=not blockers, blockers=blockers,
            submission_id=submission.id, listing_id=listing.id,
        )

    def send(
        self,
        wizard_id: int,
        submission_id: int,
        company_id: int,
        provider: CoupangProductProvider,
    ) -> LiveSubmissionResult:
        wizard, submission, listing, selection = self._context(
            wizard_id, submission_id, company_id,
        )
        preflight = self.preflight(wizard_id, submission_id, company_id)
        if not preflight.ready:
            raise ForbiddenException(
                "쿠팡 전송 조건이 충족되지 않았습니다: "
                + ", ".join(preflight.blockers),
            )
        payload, blockers = self._build(wizard, submission, selection)
        if payload is None or blockers:
            raise ForbiddenException("쿠팡 전송 Payload가 준비되지 않았습니다.")

        request_fingerprint = hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str,
            ).encode("utf-8"),
        ).hexdigest()
        correlation_id = str(uuid.uuid4())

        # 외부 호출 전에 단 한 요청만 PENDING을 선점한다. 여기서 commit
        # 한 뒤 호출하므로 프로세스가 중단돼도 SUBMITTING으로 남고,
        # 자동 재시도나 두 번째 외부 호출로 이어지지 않는다.
        #
        # 2026-09-24 후속 — 위 preflight()의 DUPLICATE_LIVE_ATTEMPT_
        # ON_SAME_LISTING 검사는 이 지점보다 먼저 실행되는 SELECT라
        # TOCTOU 경쟁을 완전히 막지 못한다(두 프로세스가 서로 다른
        # submission 행으로 거의 동시에 이 지점까지 왔다면 둘 다 그
        # SELECT를 통과했을 수 있다). 최종 방어선은 marketplace_
        # submissions의 부분 UNIQUE INDEX(uq_marketplace_submissions_
        # live_claim_per_listing, model.py 참고)다 — 같은 (company_id,
        # listing_id)에 대해 correlation_id가 있고 아직 해소되지 않은
        # (또는 이미 성공한) 행은 DB 자체가 동시에 하나만 존재하도록
        # 강제한다. 이 UPDATE 자체가 그 인덱스 대상이므로, 인덱스가
        # 실제 원본 DB에 적용된 뒤에는 두 번째 커밋이 IntegrityError로
        # 거부된다 — 그 경우도 똑같이 외부 호출(provider.create_
        # product) 이전에 막는다.
        try:
            claimed = self.marketplace.update_submission_status_conditional(
                submission.id, company_id, (SubmissionStatus.PENDING,),
                SubmissionStatus.SUBMITTING,
                request_fingerprint=request_fingerprint,
                correlation_id=correlation_id,
            )
        except IntegrityError:
            self.db.rollback()
            raise ConflictException(
                "같은 상품(listing)에 대해 다른 제출이 이미 쿠팡 전송을 "
                "시작했거나 성공했습니다 — 중복 등록을 방지하기 위해 "
                "이 전송을 진행하지 않습니다.",
            )
        if claimed != 1:
            self.db.rollback()
            raise ConflictException("다른 요청이 이미 쿠팡 전송을 시작했습니다.")
        self.db.commit()

        try:
            result = provider.create_product(payload)
        except Exception:
            result = LiveSubmissionResult(
                outcome="UNKNOWN", error_code="UNEXPECTED_PROVIDER_ERROR",
                error_summary="예기치 않은 오류로 등록 성공 여부를 확인할 수 없습니다.",
            )
        result = replace(result, correlation_id=correlation_id)
        status = {
            "SUBMITTED": SubmissionStatus.SUBMITTED,
            "FAILED": SubmissionStatus.FAILED,
            "UNKNOWN": SubmissionStatus.UNKNOWN,
        }[result.outcome]
        reason = None
        if result.error_code:
            reason = result.error_code
            if result.error_summary:
                reason += f": {result.error_summary}"
        try:
            changed = self.marketplace.update_submission_status_conditional(
                submission.id, company_id, (SubmissionStatus.SUBMITTING,), status,
                external_submission_ref=result.external_reference,
                error_reason=reason,
                external_http_status=result.http_status,
                provider_warning_summary=result.warning_summary,
                provider_response_code=result.provider_response_code,
            )
            if changed != 1:
                raise ConflictException(
                    "제출 상태가 동시에 변경되어 결과를 저장하지 못했습니다.",
                )
            self.db.commit()
        except Exception as persist_exc:
            self.db.rollback()
            # 2026-08-31 V7 필수 작업 2번(제출 장부 정합화) 감사에서 발견한
            # 결함 — 쿠팡 create_product()가 이미 성공 응답(sellerProductId
            # 포함)을 돌려준 뒤, 이 지점의 UPDATE 자체가 실패하면(디스크
            # I/O 오류·락 타임아웃 등) 그 sellerProductId는 이 로컬 변수
            # 안에만 있다가 예외와 함께 완전히 사라졌다 — 제출 행은
            # SUBMITTING에 영구히 멈추고, 실제로는 성공한 쿠팡 등록을
            # 나중에 정합화(submission_reconciliation_service.py)할 근거가
            # DB 어디에도 남지 않았다. 여기서 상태를 임의로 SUBMITTED로
            # 바꾸지는 않는다(추정 금지, fail-closed 유지) — 대신 별도
            # 트랜잭션으로 최소한의 증거만 감사 로그에 남겨, 사람이 나중에
            # 정합화 화면에서 이 sellerProductId를 입력할 근거를 잃지
            # 않게 한다. 이 best-effort 기록 자체가 실패해도 원래 예외는
            # 그대로 다시 던진다(이 저장 실패를 숨기지 않는다).
            try:
                write_audit_log(
                    self.db,
                    company_id=company_id,
                    user_id=submission.operator_approved_by,
                    action="COUPANG_PRODUCT_SUBMISSION_PERSIST_FAILED",
                    entity="marketplace_submission",
                    entity_id=str(submission.id),
                    description=(
                        f"correlation_id={correlation_id}; "
                        f"provider_outcome={result.outcome}; "
                        f"seller_product_id={result.external_reference or '-'}; "
                        f"persist_error={type(persist_exc).__name__}; "
                        "제출 상태를 DB에 반영하는 도중 오류가 발생해 "
                        "SUBMITTING 상태로 남아 있습니다 — 정합화 기능으로 "
                        "수동 확인이 필요합니다."
                    ),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
            raise

        try:
            write_audit_log(
                self.db,
                company_id=company_id,
                user_id=submission.operator_approved_by,
                action=f"COUPANG_PRODUCT_SUBMISSION_{result.outcome}",
                entity="marketplace_submission",
                entity_id=str(submission.id),
                # 2026-08-29 쿠팡 상품등록 핵심 차단 해결(Section 4 —
                # Payload 증거). Payload fingerprint(request_fingerprint,
                # 이미 위에서 계산)와 sellerProductId(external_reference),
                # 재시도 가능 여부(outcome 자체가 그 신호 — SUBMITTED는
                # 재시도 금지, FAILED는 payload 수정 후 새 제출, UNKNOWN은
                # 자동 재시도 절대 금지 후 수동 확인)를 명시적으로 함께
                # 남긴다. Credential·주소·전화번호 원문은 이 문자열에
                # 절대 포함하지 않는다(payload가 아니라 결과 메타데이터만).
                # 2026-08-30 후속 지시 — 성공 경고(warning_summary)도
                # 구조화된 필드로 남긴다(있음/없음 + 이미 마스킹된 값).
                # raw_response는 이제 _create_snippet()의 allowlist를
                # 거친 값이라 details/errorItems/개인정보 필드가 이미
                # 빠져 있다.
                description=(
                    f"correlation_id={correlation_id}; "
                    f"request_fingerprint={request_fingerprint}; "
                    f"http_status={result.http_status}; "
                    f"error_code={result.error_code or '-'}; "
                    f"seller_product_id={result.external_reference or '-'}; "
                    f"outcome={result.outcome}; "
                    f"provider_response_code={result.provider_response_code or '-'}; "
                    f"has_warning={'true' if result.warning_summary else 'false'}; "
                    f"warning_summary={result.warning_summary or '-'}; "
                    f"auto_retry_allowed=false; "
                    f"raw_response={result.raw_response_snippet or '-'}"
                ),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()

        if result.outcome != "SUBMITTED":
            dispatch_operational_event(
                self.db,
                "LISTING_SUBMISSION_FAILED",
                company_id=company_id,
                user_id=submission.operator_approved_by,
                idempotency_key=(
                    f"coupang-live-submission:{submission.id}:"
                    f"{correlation_id}:{result.outcome}"
                ),
                title="쿠팡 상품등록 결과를 확인하세요",
                message=(
                    "쿠팡 상품등록 요청이 실패했거나 결과가 불확실합니다. "
                    "자동 재시도하지 말고 상태를 확인하세요."
                ),
                link_path="listing-wizard",
                entity_ref=f"marketplace_submission:{submission.id}",
                reason=result.error_code or result.outcome,
                entity_summary=f"상품등록 제출 #{submission.id}",
            )
        return result

    def check_status(
        self,
        wizard_id: int,
        submission_id: int,
        company_id: int,
        provider: CoupangProductProvider,
    ):
        """
        2026-08-29 쿠팡 상품등록 핵심 차단 해결(V7-COUPANG-STATUS-001
        Service 연결) — Provider의 get_product_status()가 Phase 4에서
        추가됐으나 이 Service·Router·UI 어디에도 연결되지 않아 실제
        업무 흐름에서 쓸 수 없었다(감사에서 코드로 확인된 결함). 실제
        쿠팡에 제출되어 sellerProductId를 받은 제출 건에만 조회를
        허용한다 — 아직 제출되지 않은 건은 조회할 대상 자체가 없다.
        """

        _wizard, submission, _listing, _selection = self._context(
            wizard_id, submission_id, company_id,
        )
        if not submission.external_submission_ref:
            raise BadRequestException(
                "SELLER_PRODUCT_ID_NOT_AVAILABLE: 아직 쿠팡에 제출되지 "
                "않았거나 상품 ID를 확인할 수 없어 상태를 조회할 수 없습니다.",
            )
        return provider.get_product_status(submission.external_submission_ref)


__all__ = ["LivePreflight", "ListingWizardLiveService"]
