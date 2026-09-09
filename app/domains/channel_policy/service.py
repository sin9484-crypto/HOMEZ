"""
=========================================================
Homez OS

File : app/domains/channel_policy/service.py

채널 정책 엔진 — DB 조회/쓰기 오케스트레이션. 실제 판정 로직은
engine.py(순수 함수)에 전담시키고, 여기서는 규칙 로딩·이력 저장·
STALE 판정·회사 설정 CRUD·마진 추정 오케스트레이션만 담당한다.
=========================================================
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger("homez.channel_policy")

from app.core.audit_db import write_audit_log
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.ai_governance.constants import AIResultType
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import build_ai_result_envelope
from app.domains.ai_governance.service import require_active_capability
from app.domains.channel_policy.constants import (
    CHANNEL_POLICY_CATALOG_NOT_SEEDED_RULE_CODE,
)
from app.domains.channel_policy.constants import ChannelPolicyResult
from app.domains.channel_policy.constants import PolicySeverity
from app.domains.channel_policy.constants import PolicyValidationType
from app.domains.channel_policy.engine import evaluate_policy
from app.domains.channel_policy.fingerprint import (
    compute_channel_policy_input_fingerprint,
)
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.channel_policy.repository import ChannelPolicyRepository
from app.domains.channel_policy.rule_catalog import CHANNEL_POLICY_PROFILE_VERSION
from app.domains.channel_policy.rule_catalog import CHANNEL_POLICY_RULE_CATALOG
from app.domains.channel_policy.schema import ChannelPolicyEvaluationResponse
from app.domains.channel_policy.schema import CompanyChannelPolicySettingsResponse
from app.domains.channel_policy.schema import MarginEstimateInput
from app.domains.channel_policy.schema import MarginEstimateResult
from app.domains.channel_policy.schema import RuleEvaluationDetail
from app.domains.channel_policy.schema import UpdateCompanyChannelPolicySettingsRequest
from app.domains.marketplace_listing.margin_calculator import calculate_economics
from app.domains.marketplace_listing.listing_wizard_schema import EconomicsInputItem
from app.domains.marketplace_listing.model import MarketplaceFulfillmentSelection
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.model import MediaAsset
from app.domains.product_candidate.model import ProductCandidate


def _audit(
    db: Session, *, company_id: int | None, user_id: int | None,
    action: str, entity_id: str, description: str,
) -> None:

    write_audit_log(
        db, user_id=user_id, action=action, entity="channel_policy",
        entity_id=entity_id, description=description,
        company_id=company_id,
    )


class ChannelPolicyService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = ChannelPolicyRepository(db)

    # --------------------------------------------------
    # 규칙 카탈로그 시딩
    # --------------------------------------------------

    def seed_rule_catalog(
        self, actor_user_id: int | None = None,
    ) -> list[ChannelPolicyRule]:
        """CHANNEL_POLICY_RULE_CATALOG를 읽어 없으면 생성, 있으면
        내용을 갱신한다(카탈로그 자체가 Source of Truth — 관리자가
        DB를 직접 고치지 않는 한 이 함수가 항상 최신 상태로 맞춘다).
        `actor_user_id`는 관리자 엔드포인트(POST .../seed-catalog)를
        통한 수동 호출에서만 실제 사용자로 채워진다 — 부팅 시 자동
        시딩(`seed_channel_policy_catalog_at_boot`)은 시스템 주도라
        기본값 None을 그대로 쓴다.

        2026-08-24 — 이 함수는 매 부팅마다 무조건 호출된다(멱등
        보장을 위해). 이전에는 실제 INSERT/UPDATE 여부와 무관하게
        호출될 때마다 감사로그를 1건씩 남겨, 값이 전혀 바뀌지 않은
        정상 재시작에서도 audit_logs가 계속 쌓였다(실사용 중 재시작
        때마다 운영 DB에 무의미한 행이 쌓이는 결함으로 이어짐).
        이제는 실제로 새로 생성됐거나 기존 값과 하나라도 다른
        규칙이 있을 때만(`changed_count > 0`) 감사로그를 남긴다 —
        완전히 동일한 값으로 재적용되는 매 부팅에서는 감사로그가
        전혀 생기지 않는다."""

        touched: list[ChannelPolicyRule] = []
        changed_count = 0

        for entry in CHANNEL_POLICY_RULE_CATALOG:
            existing = self.repository.get_rule(
                entry["channel"], entry["rule_code"],
            )

            values = dict(
                channel=entry["channel"],
                rule_code=entry["rule_code"],
                category_scope_json=json.dumps(
                    entry["category_scope"], ensure_ascii=False,
                ),
                severity=entry["severity"],
                validation_type=entry["validation_type"],
                required_fields_json=(
                    json.dumps(entry["required_fields"], ensure_ascii=False)
                    if entry["required_fields"] is not None else None
                ),
                required_evidence_json=(
                    json.dumps(entry["required_evidence"], ensure_ascii=False)
                    if entry["required_evidence"] is not None else None
                ),
                official_source_url=entry["official_source_url"],
                source_title=entry["source_title"],
                verified_at=entry["verified_at"],
                profile_version=CHANNEL_POLICY_PROFILE_VERSION,
                active=entry["active"],
            )

            if existing is None:
                rule = self.repository.add_rule_no_commit(
                    ChannelPolicyRule(**values),
                )
                changed_count += 1
            else:
                is_changed = any(
                    getattr(existing, key) != value
                    for key, value in values.items()
                )
                if is_changed:
                    for key, value in values.items():
                        setattr(existing, key, value)
                    changed_count += 1
                rule = existing

            touched.append(rule)

        if changed_count > 0:
            _audit(
                self.db, company_id=None, user_id=actor_user_id,
                action="CHANNEL_POLICY_RULE_CATALOG_SEEDED",
                entity_id="ALL",
                description=(
                    f"채널 정책 규칙 카탈로그 시딩: {changed_count}건 "
                    f"신규/변경 (전체 확인 {len(touched)}건, "
                    f"profile_version={CHANNEL_POLICY_PROFILE_VERSION})"
                ),
            )

        self.db.commit()

        return touched

    # --------------------------------------------------
    # 평가
    # --------------------------------------------------

    def evaluate_and_record(
        self,
        *,
        company_id: int,
        product_candidate_id: int,
        channel: str,
        category_hint_override: str | None,
        product_attributes: dict,
        confirmed_evidence_rule_codes: list[str],
        evaluated_by: int | None,
        selection_id: int | None = None,
    ) -> ChannelPolicyEvaluationResponse:
        """
        CA-1(2026-08-21) — `selection_id`가 주어지면(실제
        MarketplaceFulfillmentSelection이 이미 만들어진 이후, 즉
        위저드 일괄 등록 이후) 그 selection의 required_fields(가격·
        옵션·배송 포함)와 현재 미디어 자산 목록까지 지문에 포함해
        저장한다 — 이 평가만이 제출 게이트를 통과할 수 있다.
        `selection_id`가 없으면(위저드 초안 단계 "미리보기") selection
        부분을 None으로 지문에 새긴다 — 그 지문은 구조적으로 제출
        시점 재계산 값과 절대 같을 수 없으므로, 미리보기 평가는
        화면 안내 용도로만 쓰이고 제출 게이트는 통과하지 못한다
        (의도된 fail-closed 설계 — current_valid_channel_policy 참고).
        """

        # CA-5(2026-08-21) — AI Capability Registry 강제. 등록되지
        # 않았거나 비활성인 capability는 여기서 즉시 차단된다(fail-
        # closed). 이 호출은 실행을 승인하지 않는다 — 아래 로직은
        # 여전히 정책 규칙 자체의 판정 결과로만 동작한다.
        require_active_capability(CapabilityCode.CHANNEL_POLICY_ASSIST)

        candidate = self.db.get(ProductCandidate, product_candidate_id)
        if candidate is None:
            raise NotFoundException("상품 후보를 찾을 수 없습니다.")

        selection = None
        if selection_id is not None:
            selection = self.db.get(
                MarketplaceFulfillmentSelection, selection_id,
            )
            if selection is None or selection.company_id != company_id:
                raise NotFoundException(
                    "MarketplaceFulfillmentSelection을 찾을 수 없습니다.",
                )

        media_asset_ids = self._media_asset_ids_for_candidate(
            product_candidate_id,
        )

        rules = self.repository.list_active_rules(channel)

        category_hint = category_hint_override or candidate.category_hint

        # Audit(2026-08-21, CTO 후속 지시) — `rules`가 비어 있는 두
        # 상태를 구분한다: (1) 관리자가 이 채널의 모든 규칙을 명시적
        # 으로 비활성화한 상태(정책 제약 없음이 사실) vs (2) 이 채널의
        # 카탈로그가 애초에 한 번도 시딩되지 않은 상태(신규 설치·
        # 업그레이드에서 seed 단계 누락/실패). 후자를 구분하지 않고
        # `evaluate_policy([], ...)`를 그대로 호출하면 규칙이 하나도
        # 없다는 이유만으로 항상 CHANNEL_ELIGIBLE이 나와 제출 게이트
        # 자체가 무력화된다 — 반드시 fail-closed로 CHANNEL_DATA_
        # REQUIRED 처리한다(임의로 BLOCKED로 올리지도 않는다 — 아직
        # "무엇이 위반인지"조차 판단할 근거가 없는 상태이기 때문).
        if not rules and not self.repository.has_any_rule_row_for_channel(
            channel,
        ):
            result = ChannelPolicyResult.CHANNEL_DATA_REQUIRED
            rule_results = [
                RuleEvaluationDetail(
                    rule_code=CHANNEL_POLICY_CATALOG_NOT_SEEDED_RULE_CODE,
                    severity=PolicySeverity.BLOCKING,
                    validation_type=PolicyValidationType.EVIDENCE_REQUIRED_GENERIC,
                    applies=True,
                    satisfied=False,
                    missing_evidence=[CHANNEL_POLICY_CATALOG_NOT_SEEDED_RULE_CODE],
                ),
            ]
        else:
            result, rule_results = evaluate_policy(
                rules,
                category_hint=category_hint,
                product_name=candidate.product_name,
                product_attributes=product_attributes,
                confirmed_evidence_rule_codes=confirmed_evidence_rule_codes,
            )

        current_profile_version = (
            rules[0].profile_version if rules else "NONE"
        )

        input_fingerprint = compute_channel_policy_input_fingerprint(
            candidate, selection, media_asset_ids,
        )

        evaluation = self.repository.add_evaluation_no_commit(
            ChannelPolicyEvaluation(
                company_id=company_id,
                product_candidate_id=product_candidate_id,
                channel=channel,
                result=result,
                policy_profile_version=current_profile_version,
                rule_results_json=json.dumps(
                    [detail.model_dump() for detail in rule_results],
                    ensure_ascii=False, default=str,
                ),
                input_fingerprint=input_fingerprint,
                evaluated_by=evaluated_by,
            ),
        )

        _audit(
            self.db, company_id=company_id, user_id=evaluated_by,
            action="CHANNEL_POLICY_EVALUATED",
            entity_id=str(evaluation.id),
            description=(
                f"채널 정책 평가: candidate={product_candidate_id}, "
                f"channel={channel}, result={result}"
            ),
        )

        self.db.commit()

        return self._to_response(evaluation, is_stale=False)

    def _media_asset_ids_for_candidate(
        self, product_candidate_id: int,
    ) -> list[int]:

        rows = (
            self.db.query(MediaAsset.id)
            .filter(
                MediaAsset.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE,
                MediaAsset.owner_id == product_candidate_id,
            )
            .all()
        )
        return [row[0] for row in rows]

    def current_valid_channel_policy(
        self,
        *,
        company_id: int,
        product_candidate_id: int,
        channel: str,
        selection: MarketplaceFulfillmentSelection,
    ) -> ChannelPolicyEvaluation | None:
        """
        CA-1 — 제출 직전 강제 게이트가 호출하는 fail-closed 재검증.
        `app/domains/marketplace_listing/approval_service.py::
        current_valid_approval()`와 정확히 같은 철학 — 최신 평가가
        없거나, 결과가 SUBMITTABLE이 아니거나, 지금 다시 계산한
        입력 지문이 다르거나, 정책 프로필 버전이 바뀌었으면 전부
        무효(None)로 취급한다. UNKNOWN/미평가를 절대 허용으로
        승격하지 않는다.
        """

        latest = self.repository.get_latest_evaluation(
            company_id, product_candidate_id, channel,
        )
        if latest is None or latest.result not in ChannelPolicyResult.SUBMITTABLE:
            return None

        candidate = self.db.get(ProductCandidate, product_candidate_id)
        if candidate is None:
            return None

        media_asset_ids = self._media_asset_ids_for_candidate(
            product_candidate_id,
        )
        current_fingerprint = compute_channel_policy_input_fingerprint(
            candidate, selection, media_asset_ids,
        )
        if current_fingerprint != latest.input_fingerprint:
            return None

        rules = self.repository.list_active_rules(channel)
        current_profile_version = rules[0].profile_version if rules else "NONE"
        if latest.policy_profile_version != current_profile_version:
            return None

        return latest

    def get_latest_evaluation_violation_fingerprint(
        self, company_id: int, product_candidate_id: int, channel: str,
    ) -> tuple[str, str] | None:
        """
        Section 3(2026-08-24) — CHANNEL_POLICY_VIOLATION 알림의
        anti-spam 지문. `current_valid_channel_policy()`가 게이트
        차단을 이미 확정한 뒤, "왜" 막혔는지(BLOCKED/DATA_REQUIRED
        인지, 최신 평가의 입력 지문·정책 버전이 무엇인지)를 새로
        재계산하지 않고 이미 저장된 최신 평가 행에서 읽기 전용으로
        요약만 반환한다 — 새 상태·테이블을 만들지 않는다. 최신 평가
        자체가 없으면(한 번도 평가하지 않음) None을 반환한다 — 그
        경우는 "위반"이 아니라 "미평가"이므로 호출자가 별도로
        판단한다.
        """

        latest = self.repository.get_latest_evaluation(
            company_id, product_candidate_id, channel,
        )
        if latest is None:
            return None

        return (
            latest.result,
            f"{latest.input_fingerprint}:{latest.policy_profile_version}",
        )

    def get_current_status(
        self, company_id: int, product_candidate_id: int, channel: str,
    ) -> ChannelPolicyEvaluationResponse | None:
        """재평가 없이 가장 최근 저장된 평가만 읽는다 — '자동 정책
        갱신' 토글이 꺼져 있을 때 이 경로를 쓴다. 활성 규칙의 현재
        profile_version과 저장된 평가의 profile_version이 다르면
        CHANNEL_POLICY_STALE로 표시한다(재평가하지 않는다 — 표시만
        바꾼다)."""

        evaluation = self.repository.get_latest_evaluation(
            company_id, product_candidate_id, channel,
        )
        if evaluation is None:
            return None

        rules = self.repository.list_active_rules(channel)
        current_profile_version = rules[0].profile_version if rules else "NONE"
        is_stale = evaluation.policy_profile_version != current_profile_version

        return self._to_response(evaluation, is_stale=is_stale)

    def _to_response(
        self, evaluation: ChannelPolicyEvaluation, *, is_stale: bool,
    ) -> ChannelPolicyEvaluationResponse:

        rule_results = [
            RuleEvaluationDetail(**item)
            for item in json.loads(evaluation.rule_results_json)
        ]

        display_result = (
            ChannelPolicyResult.CHANNEL_POLICY_STALE if is_stale
            else evaluation.result
        )

        return ChannelPolicyEvaluationResponse(
            id=evaluation.id,
            company_id=evaluation.company_id,
            product_candidate_id=evaluation.product_candidate_id,
            channel=evaluation.channel,
            result=display_result,
            policy_profile_version=evaluation.policy_profile_version,
            rule_results=rule_results,
            is_stale=is_stale,
            created_at=evaluation.created_at,
            ai_result=self._build_ai_result(
                display_result, rule_results, evaluation,
            ),
        )

    # Audit(2026-08-21, CTO 후속 지시) — 5종 판정을 공통 AI 결과
    # 계약(AIResultType)으로 매핑한다. execution_allowed는 여기서
    # 임의로 계산하지 않고, 이 판정이 실제로 SUBMITTABLE인지(=
    # current_valid_channel_policy()가 통과시킬 결과인지)만 그대로
    # 반영한다 — 실제 제출 가능 여부의 최종 결정은 여전히
    # submission_service.py::submit()의 재검증이 전담한다.
    _RESULT_TO_AI_RESULT_TYPE = {
        ChannelPolicyResult.CHANNEL_ELIGIBLE: AIResultType.CALCULATED_RESULT,
        ChannelPolicyResult.CHANNEL_ELIGIBLE_WITH_ACTIONS: (
            AIResultType.CALCULATED_RESULT
        ),
        ChannelPolicyResult.CHANNEL_DATA_REQUIRED: AIResultType.EVIDENCE_REQUIRED,
        ChannelPolicyResult.CHANNEL_POLICY_BLOCKED: AIResultType.POLICY_BLOCKED,
        ChannelPolicyResult.CHANNEL_POLICY_STALE: (
            AIResultType.HUMAN_REVIEW_REQUIRED
        ),
    }

    def _build_ai_result(
        self,
        display_result: str,
        rule_results: list[RuleEvaluationDetail],
        evaluation: ChannelPolicyEvaluation,
    ) -> AIResultEnvelope:

        blocking_rules = [
            r.rule_code for r in rule_results
            if r.applies and not r.satisfied
            and r.severity == PolicySeverity.BLOCKING
        ]
        missing_evidence = sorted({
            code for r in rule_results for code in r.missing_evidence
        })

        return build_ai_result_envelope(
            capability_code=CapabilityCode.CHANNEL_POLICY_ASSIST,
            result_type=self._RESULT_TO_AI_RESULT_TYPE[display_result],
            decision=display_result,
            calculated_values={
                "rule_count": len(rule_results),
                "applicable_rule_count": sum(
                    1 for r in rule_results if r.applies
                ),
            },
            missing_evidence=missing_evidence,
            blocking_rules=blocking_rules,
            execution_allowed=display_result in ChannelPolicyResult.SUBMITTABLE,
            policy_version=evaluation.policy_profile_version,
            evaluated_at=evaluation.created_at,
        )

    # --------------------------------------------------
    # 회사 설정
    # --------------------------------------------------

    def get_or_default_settings(
        self, company_id: int,
    ) -> CompanyChannelPolicySettingsResponse | None:

        settings = self.repository.get_settings(company_id)
        if settings is None:
            return None

        return CompanyChannelPolicySettingsResponse(
            id=settings.id,
            company_id=settings.company_id,
            min_target_margin_rate=settings.min_target_margin_rate,
            min_profit_per_order=settings.min_profit_per_order,
            max_initial_purchase_amount=settings.max_initial_purchase_amount,
            max_moq=settings.max_moq,
            max_lead_time_days=settings.max_lead_time_days,
            max_return_shipping_cost=settings.max_return_shipping_cost,
            allowed_categories=json.loads(settings.allowed_categories_json),
            forbidden_categories=json.loads(
                settings.forbidden_categories_json,
            ),
            safety_stock_buffer=settings.safety_stock_buffer,
            version=settings.version,
            updated_at=settings.updated_at,
        )

    def upsert_settings(
        self, company_id: int, updated_by: int,
        data: UpdateCompanyChannelPolicySettingsRequest,
    ) -> CompanyChannelPolicySettingsResponse:

        settings = self.repository.get_settings(company_id)

        if settings is None:
            if data.expected_version != 0:
                raise ConflictException(
                    "설정이 아직 존재하지 않습니다 — expected_version=0으로 생성하세요.",
                )
            settings = CompanyChannelPolicySettings(
                company_id=company_id, updated_by=updated_by, version=1,
                allowed_categories_json="[]", forbidden_categories_json="[]",
            )
            self.db.add(settings)
        else:
            if settings.version != data.expected_version:
                raise ConflictException(
                    "다른 사용자가 먼저 설정을 변경했습니다 — 최신 값을 다시 불러오세요.",
                )
            settings.version += 1
            settings.updated_by = updated_by

        for field in (
            "min_target_margin_rate", "min_profit_per_order",
            "max_initial_purchase_amount", "max_moq", "max_lead_time_days",
            "max_return_shipping_cost", "safety_stock_buffer",
        ):
            value = getattr(data, field)
            if value is not None:
                setattr(settings, field, value)

        if data.allowed_categories is not None:
            settings.allowed_categories_json = json.dumps(
                data.allowed_categories, ensure_ascii=False,
            )
        if data.forbidden_categories is not None:
            settings.forbidden_categories_json = json.dumps(
                data.forbidden_categories, ensure_ascii=False,
            )

        self.db.flush()
        _audit(
            self.db, company_id=company_id, user_id=updated_by,
            action="CHANNEL_POLICY_SETTINGS_UPDATED",
            entity_id=str(settings.id),
            description=f"채널 정책 회사 설정 변경 (version={settings.version})",
        )

        self.db.commit()
        self.db.refresh(settings)

        return self.get_or_default_settings(company_id)

    # --------------------------------------------------
    # 마진 추정(상품 선별 단계 — listing_wizard 6단계와 별개)
    # --------------------------------------------------

    def estimate_margin(
        self, company_id: int, data: MarginEstimateInput,
    ) -> MarginEstimateResult:

        # Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        # 강제(PROFITABILITY_CALCULATION). 순수 계산이라 EStop/승인과
        # 무관하지만(카탈로그의 stop_condition 그대로), 역할 계약 없는
        # 호출 자체는 여전히 차단한다.
        require_active_capability(CapabilityCode.PROFITABILITY_CALCULATION)

        missing_cost_fields = [
            field for field in (
                "cost_of_goods", "channel_fee_rate", "payment_fee_rate",
                "shipping_cost", "packaging_cost", "ad_cost",
                "return_reserve_rate", "tax_basis_rate",
            )
            if getattr(data, field) is None
        ]

        item = EconomicsInputItem(
            marketplace_account_id=0,
            sale_price=data.sale_price,
            cost_of_goods=data.cost_of_goods or 0,
            channel_fee_rate=data.channel_fee_rate or 0,
            payment_fee_rate=data.payment_fee_rate or 0,
            shipping_cost=data.shipping_cost or 0,
            packaging_cost=data.packaging_cost or 0,
            ad_cost=data.ad_cost or 0,
            return_reserve_rate=data.return_reserve_rate or 0,
            tax_basis_rate=data.tax_basis_rate or 0,
        )
        calc = calculate_economics(item)

        settings = self.repository.get_settings(company_id)
        target_rate = settings.min_target_margin_rate if settings else None
        meets_target = (
            None if target_rate is None
            else calc.margin_rate >= target_rate
        )

        return MarginEstimateResult(
            expected_revenue=calc.expected_revenue,
            total_cost=calc.total_cost,
            margin_amount=calc.margin_amount,
            margin_rate=calc.margin_rate,
            break_even_price=calc.break_even_price,
            is_provisional=bool(missing_cost_fields),
            missing_cost_fields=missing_cost_fields,
            meets_company_target=meets_target,
            company_target_margin_rate=target_rate,
        )


def seed_channel_policy_catalog_at_boot(db_path: Path) -> dict:
    """
    Audit(2026-08-21, CTO 후속 지시) — `app/database/seed.py::
    seed_environment()`(역할·권한 기준 데이터)와 정확히 동일한
    패턴: 공식 bootstrap 흐름(app/desktop/main.py)이 Migration 적용
    직후 호출하는 단발성 진입점. 주어진 db_path에 대해 자체
    SQLAlchemy 엔진으로 단일 Transaction을 열어 `seed_rule_catalog()`
    (멱등 — 값이 이미 동일하면 실제 UPDATE가 발생하지 않는다)를
    실행한다.

    이전에는 `POST /channel-policy/rules/seed-catalog`를 관리자가
    수동으로 호출해야만 카탈로그가 채워졌다 — 신규 설치·업그레이드
    환경에서 이 호출이 누락되면 모든 채널의 활성 규칙이 0건이 되고,
    `ChannelPolicyService.evaluate_and_record()`가 이를 fail-closed
    (CHANNEL_DATA_REQUIRED)로 처리하도록 이미 고쳤지만, 그 상태로는
    실제 상품을 영구히 제출할 수 없다 — 그래서 시딩 자체를 역할·권한과
    동일하게 부팅마다 자동·멱등 실행으로 바꾼다. 수동 엔드포인트는
    그대로 남겨둔다(운영 중 재시작 없이 카탈로그를 즉시 갱신하고 싶을
    때 유효).

    실패 시 전체 rollback 후 예외를 다시 던진다 — 호출자(desktop/
    main.py)가 fail-closed로 처리한다(seed_environment()와 동일한
    계약).
    """

    engine = create_engine(f"sqlite:///{db_path}")
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    try:
        rules = ChannelPolicyService(db).seed_rule_catalog()

        logger.info(
            "채널 정책 카탈로그 시딩 완료: rules=%d", len(rules),
        )

        return {"rules_seeded": len(rules)}

    except Exception as exc:

        db.rollback()
        logger.error("채널 정책 카탈로그 시딩 실패, 전체 rollback: %s", exc)
        raise

    finally:

        db.close()
        engine.dispose()


__all__ = ["ChannelPolicyService", "seed_channel_policy_catalog_at_boot"]
