"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_precheck.py

Gate I(2026-08-08) — 7단계(사전검사) 구조화 검사 엔진. 다른 도메인의
상태를 읽기만 한다(EligibilityService.is_usable()/SafetyService.
is_emergency_stop_active()/is_restricted_mode() — 전부 기존 코드
그대로 재사용, 새 조회 로직을 만들지 않는다). 완성된 한국어 문장은
절대 만들지 않는다 — code/severity/step/field/channel/blocking/
localized_message_key + 구조화 params만 반환한다(화면 i18n이 문구를
조립).

승인 이후 재검증(승인 fingerprint 불일치)은 이 모듈이 아니라
submission_service 쪽 제출 직전 게이트가 전담한다 — "사전검사"는
승인 이전 단계 전용이다.
=========================================================
"""

import json
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.migration_restricted_mode import is_restricted_mode
from app.domains.automation_safety.service import SafetyService
from app.domains.channel_policy.constants import ChannelPolicyResult
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.marketplace_listing.constants import CapabilityStatus
from app.domains.marketplace_listing.constants import ChannelCode
from app.domains.marketplace_listing.constants import ContractStatus
from app.domains.marketplace_listing.constants import FulfillmentMode
from app.domains.marketplace_listing.constants import WizardStep
from app.domains.marketplace_listing.coupang_submission_contract import (
    validate_coupang_submission_contract,
)
from app.domains.marketplace_listing.eligibility_service import (
    EligibilityService,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    ValidationIssue,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardValidateResponse,
)
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.repository import (
    MarketplaceListingRepository,
)
from app.domains.marketplace_listing.required_fields_schemas import (
    get_required_fields_schema,
)
from app.domains.media_asset.model import MediaAsset


def _issue(
    code: str, step: str, blocking: bool, message_key: str,
    field: str | None = None, channel: str | None = None,
    params: dict | None = None,
) -> ValidationIssue:

    return ValidationIssue(
        code=code, severity="BLOCKING" if blocking else "WARNING",
        step=step, field=field, channel=channel, blocking=blocking,
        localized_message_key=message_key, params=params or {},
    )


def run_precheck(
    wizard: ListingWizard, db: Session, company_id: int,
) -> WizardValidateResponse:

    issues: list[ValidationIssue] = []
    repo = MarketplaceListingRepository(db)
    eligibility = EligibilityService(db)
    channel_policy_service = ChannelPolicyService(db)

    # --- 2단계: 상품 초안 ---
    draft = json.loads(wizard.draft_json) if wizard.draft_json else None
    if draft is None or not draft.get("product_name"):
        issues.append(_issue(
            "DRAFT_MISSING", WizardStep.DRAFT, True,
            "listing_wizard.precheck.draft_missing",
        ))
    else:
        option_names = {opt.get("name") for opt in draft.get("options", [])}
        for sku in draft.get("sku_list", []):
            for opt_name in sku.get("option_values", {}):
                if opt_name not in option_names:
                    issues.append(_issue(
                        "DRAFT_SKU_OPTION_UNDECLARED", WizardStep.DRAFT,
                        True, "listing_wizard.precheck.sku_option_undeclared",
                        field="sku_list",
                        params={
                            "sku_code": sku.get("sku_code"),
                            "option_name": opt_name,
                        },
                    ))

    # --- 3단계: 이미지 ---
    media_ids = json.loads(wizard.selected_media_asset_ids_json or "[]")
    if not media_ids:
        issues.append(_issue(
            "MEDIA_NONE_SELECTED", WizardStep.MEDIA, True,
            "listing_wizard.precheck.media_none_selected",
        ))
    else:
        existing_count = (
            db.query(MediaAsset)
            .filter(MediaAsset.company_id == company_id)
            .filter(MediaAsset.id.in_(media_ids))
            .count()
        )
        if existing_count != len(set(media_ids)):
            issues.append(_issue(
                "MEDIA_ASSET_NOT_FOUND", WizardStep.MEDIA, True,
                "listing_wizard.precheck.media_asset_not_found",
            ))

    # --- 4/5단계: 판매채널·판매 방식 ---
    channel_selections = json.loads(wizard.channel_selections_json or "[]")
    if not channel_selections:
        issues.append(_issue(
            "CHANNELS_NONE_SELECTED", WizardStep.CHANNELS, True,
            "listing_wizard.precheck.channels_none_selected",
        ))

    for entry in channel_selections:
        account_id = entry.get("marketplace_account_id")
        account = repo.get_account_for_company(account_id, company_id)

        if account is None or not account.is_active:
            issues.append(_issue(
                "CHANNEL_ACCOUNT_INVALID", WizardStep.CHANNELS, True,
                "listing_wizard.precheck.channel_account_invalid",
                channel=str(account_id),
            ))
            continue

        channel = repo.get_channel(account.channel_id)
        fulfillment_mode = entry.get("fulfillment_mode")

        if not fulfillment_mode:
            issues.append(_issue(
                "FULFILLMENT_MODE_MISSING", WizardStep.FULFILLMENT, True,
                "listing_wizard.precheck.fulfillment_mode_missing",
                channel=channel.code if channel else str(account_id),
            ))
            continue

        capabilities = repo.list_capabilities_for_channel(channel.id)
        matching = next(
            (
                c for c in capabilities
                if c.fulfillment_mode == fulfillment_mode
            ),
            None,
        )
        if (
            matching is None or not matching.is_supported
            or matching.status != CapabilityStatus.VERIFIED
        ):
            issues.append(_issue(
                "FULFILLMENT_MODE_NOT_SUPPORTED", WizardStep.FULFILLMENT,
                True, "listing_wizard.precheck.fulfillment_mode_not_supported",
                channel=channel.code, params={"mode": fulfillment_mode},
            ))
            continue

        schema_entry = get_required_fields_schema(
            channel.code, fulfillment_mode,
        )
        if schema_entry is None:
            issues.append(_issue(
                "FULFILLMENT_REQUIRED_FIELDS_SCHEMA_MISSING",
                WizardStep.FULFILLMENT, True,
                "listing_wizard.precheck.required_fields_schema_missing",
                channel=channel.code,
            ))
        else:
            schema_cls, _name, _version = schema_entry
            try:
                schema_cls.model_validate(entry.get("required_fields", {}))
            except ValidationError:
                issues.append(_issue(
                    "FULFILLMENT_REQUIRED_FIELDS_INVALID",
                    WizardStep.FULFILLMENT, True,
                    "listing_wizard.precheck.required_fields_invalid",
                    channel=channel.code,
                ))

            # V7 안정화(2026-08-30, 감사 F-01) — 7단계 사전검사와
            # 9~10단계 실제 전송 직전 Payload 생성이 서로 다른 검사를
            # 하던 것이 근본 원인이었다. 이제 이 자리에서도 같은
            # coupang_submission_contract.validate_coupang_submission_
            # contract()를 호출해, 여기서 PASS면 실제 Payload 생성도
            # 반드시 PASS하도록 만든다(반대도 마찬가지 — 한쪽에만
            # 검사를 추가하는 것이 재발 원인이라 이 파일에 새 검사를
            # 직접 추가하지 않는다). 이 모듈의 기존 규칙("완성된
            # 한국어 문장을 만들지 않는다")을 지키기 위해 계약의
            # message_ko는 여기서 쓰지 않고 code만 구조화 params로
            # 넘긴다 — 문구 조립은 화면 i18n이 code 기준으로 한다.
            if (
                channel.code == ChannelCode.COUPANG
                and fulfillment_mode == FulfillmentMode.SELLER_FULFILLED
                and draft is not None
            ):
                contract_result = validate_coupang_submission_contract(
                    draft=draft,
                    required_fields=entry.get("required_fields") or {},
                    channel_policy_attributes=(
                        entry.get("channel_policy_attributes") or {}
                    ),
                )
                for contract_issue in contract_result.issues:
                    issues.append(_issue(
                        contract_issue.code, WizardStep.FULFILLMENT,
                        contract_issue.blocking,
                        "listing_wizard.precheck.contract."
                        + contract_issue.code.lower(),
                        field=contract_issue.field_path,
                        channel=channel.code,
                        params={"ui_field": contract_issue.ui_field},
                    ))

        if matching.requires_eligibility_check and not eligibility.is_usable(
            account_id, fulfillment_mode, company_id,
        ):
            issues.append(_issue(
                "ELIGIBILITY_NOT_VERIFIED", WizardStep.FULFILLMENT, True,
                "listing_wizard.precheck.eligibility_not_verified",
                channel=channel.code,
            ))

        if (
            matching.requires_account_contract
            and account.direct_purchase_contract_status
            != ContractStatus.VERIFIED
        ):
            issues.append(_issue(
                "CONTRACT_NOT_VERIFIED", WizardStep.FULFILLMENT, True,
                "listing_wizard.precheck.contract_not_verified",
                channel=channel.code,
            ))

        # CP-2(2026-08-21) — 채널 정책(카테고리 금지/데이터 필요 등)은
        # 이미 계산돼 저장된 최신 판정만 읽는다(재평가하지 않는다 —
        # 재평가는 프런트엔드가 채널 선택 시 별도로
        # POST /channel-policy/evaluate를 호출해 트리거한다). 정책과
        # 수익성(ECONOMICS 단계)은 여기서도 절대 섞지 않는다 — 이
        # 블록은 오직 channel_policy_service의 판정 결과 코드만 읽는다.
        #
        # 여기서는 의도적으로 전부 non-blocking(WARNING)이다 — 이
        # 사전검사는 "작성 중 정책 알림 표시"에 해당하는 화면 안내일
        # 뿐이고, 실제 우회 불가능한 강제 게이트는 submission_service.
        # py::submit()의 제출 직전 재검사가 전담한다(마법사 승인
        # 자체를 이 신호로 막으면 CP-2 이전부터 있던 기존 위저드 테스트
        # 91개가 "채널 정책을 한 번도 평가한 적 없다"는 이유만으로
        # 전부 깨진다 — 그 회귀 대신 제출 경계 하나에만 강제를 건다).
        if wizard.product_candidate_id is not None:
            policy_status = channel_policy_service.get_current_status(
                company_id, wizard.product_candidate_id, channel.code,
            )
            if policy_status is None:
                issues.append(_issue(
                    "CHANNEL_POLICY_NOT_EVALUATED", WizardStep.CHANNELS,
                    False, "listing_wizard.precheck.channel_policy_not_evaluated",
                    channel=channel.code,
                ))
            elif policy_status.result == ChannelPolicyResult.CHANNEL_POLICY_BLOCKED:
                issues.append(_issue(
                    "CHANNEL_POLICY_BLOCKED", WizardStep.CHANNELS, False,
                    "listing_wizard.precheck.channel_policy_blocked",
                    channel=channel.code,
                ))
            elif policy_status.result == ChannelPolicyResult.CHANNEL_DATA_REQUIRED:
                issues.append(_issue(
                    "CHANNEL_POLICY_DATA_REQUIRED", WizardStep.CHANNELS, False,
                    "listing_wizard.precheck.channel_policy_data_required",
                    channel=channel.code,
                ))
            elif policy_status.result == ChannelPolicyResult.CHANNEL_POLICY_STALE:
                issues.append(_issue(
                    "CHANNEL_POLICY_STALE", WizardStep.CHANNELS, False,
                    "listing_wizard.precheck.channel_policy_stale",
                    channel=channel.code,
                ))
            elif policy_status.result == ChannelPolicyResult.CHANNEL_ELIGIBLE_WITH_ACTIONS:
                issues.append(_issue(
                    "CHANNEL_POLICY_ACTIONS_REQUIRED", WizardStep.CHANNELS,
                    False, "listing_wizard.precheck.channel_policy_actions_required",
                    channel=channel.code,
                ))

    # --- 6단계: 가격·마진 ---
    economics_by_account = {
        item.get("marketplace_account_id"): item
        for item in json.loads(wizard.economics_result_json or "[]")
    }
    for entry in channel_selections:
        account_id = entry.get("marketplace_account_id")
        result = economics_by_account.get(account_id)

        if result is None:
            issues.append(_issue(
                "ECONOMICS_MISSING", WizardStep.ECONOMICS, True,
                "listing_wizard.precheck.economics_missing",
                channel=str(account_id),
            ))
            continue

        if Decimal(str(result.get("margin_amount", "0"))) < 0:
            issues.append(_issue(
                "ECONOMICS_NEGATIVE_MARGIN", WizardStep.ECONOMICS, True,
                "listing_wizard.precheck.economics_negative_margin",
                channel=str(account_id),
                params={"margin_amount": result.get("margin_amount")},
            ))

        if result.get("break_even_price") is None:
            issues.append(_issue(
                "ECONOMICS_BREAK_EVEN_IMPOSSIBLE", WizardStep.ECONOMICS,
                True, "listing_wizard.precheck.economics_break_even_impossible",
                channel=str(account_id),
            ))

    # --- 시스템 전역 게이트 ---
    if SafetyService(db).is_emergency_stop_active():
        issues.append(_issue(
            "SAFETY_EMERGENCY_STOP_ACTIVE", WizardStep.PRECHECK, True,
            "listing_wizard.precheck.emergency_stop_active",
        ))

    if is_restricted_mode():
        issues.append(_issue(
            "SYSTEM_MIGRATION_RESTRICTED_MODE", WizardStep.PRECHECK, True,
            "listing_wizard.precheck.migration_restricted_mode",
        ))

    has_blocking = any(issue.blocking for issue in issues)
    status = "NEEDS_CORRECTION" if has_blocking else "READY_FOR_APPROVAL"

    return WizardValidateResponse(status=status, issues=issues)


__all__ = ["run_precheck"]
