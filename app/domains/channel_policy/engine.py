"""
=========================================================
Homez OS

File : app/domains/channel_policy/engine.py

채널 정책 평가 — 순수 함수(DB 접근 없음, margin_calculator.py와
동일한 설계 원칙). service.py가 DB에서 규칙·이력을 읽어와 이 함수에
넘기고, 결과를 ChannelPolicyEvaluation으로 저장하는 것을 전담한다.

정책 적합성(이 파일)과 수익성(margin_estimator.py)은 서로의 결과를
전혀 참조하지 않는다 — 함수 시그니처 자체에 마진 관련 입력이 없다.
=========================================================
"""

from __future__ import annotations

import re

from app.domains.channel_policy.constants import CATEGORY_SCOPE_ALL
from app.domains.channel_policy.constants import ChannelPolicyResult
from app.domains.channel_policy.constants import PolicySeverity
from app.domains.channel_policy.constants import PolicyValidationType
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.schema import RuleEvaluationDetail


def _category_matches(
    category_scope: list[str], category_hint: str | None, product_name: str,
) -> bool:

    if CATEGORY_SCOPE_ALL in category_scope:
        return True

    haystack = f"{category_hint or ''} {product_name}".lower()
    tokens = set(re.findall(r"[0-9a-zA-Z가-힣]+", haystack))

    # One/two-character policy terms such as "포" (artillery) must match a
    # complete token.  Plain substring matching classified "부직포" as a
    # prohibited artillery product.  Longer phrases keep substring matching
    # so natural product names with attached particles remain detectable.
    return any(
        keyword.lower() in tokens
        if len(keyword.strip()) <= 2
        else keyword.lower() in haystack
        for keyword in category_scope
    )


def _evaluate_rule(
    rule: ChannelPolicyRule,
    category_scope: list[str],
    required_fields: list[str] | None,
    required_evidence: list[str] | None,
    *,
    category_hint: str | None,
    product_name: str,
    product_attributes: dict,
    confirmed_evidence_rule_codes: set[str],
) -> RuleEvaluationDetail:

    applies = _category_matches(category_scope, category_hint, product_name)

    detail = RuleEvaluationDetail(
        rule_code=rule.rule_code,
        severity=rule.severity,
        validation_type=rule.validation_type,
        applies=applies,
        satisfied=True,
        official_source_url=rule.official_source_url,
        source_title=rule.source_title,
    )

    if not applies:
        return detail

    if rule.validation_type == PolicyValidationType.CATEGORY_PROHIBITED:
        # CA-3(2026-08-21 CTO 지시) — category_hint는 AI가 붙인 자유
        # 텍스트 힌트일 뿐 공식 카테고리 분류가 아니다. 키워드가
        # 매치됐다는 사실만으로 법적 판매금지를 확정하지 않는다 —
        # category_scope가 특정 키워드 목록(ALL이 아님)인 규칙은
        # 사용자가 실제로 official_category_code(쿠팡 Category
        # Metadata Query API 등으로 조회한 공식 카테고리 코드)를
        # 입력했을 때만 확정 판정(위반/통과)을 내린다. 코드가 없으면
        # "판단 불가 — 자료 필요"로만 표시한다(임의로 통과시키지도,
        # 임의로 차단하지도 않는다).
        if (
            CATEGORY_SCOPE_ALL not in category_scope
            and "official_category_code" not in product_attributes
        ):
            detail.satisfied = False
            detail.missing_fields = ["official_category_code"]
            return detail

        detail.satisfied = False
        return detail

    if rule.validation_type == PolicyValidationType.ORIGIN_COUNTRY_PROHIBITED:
        # required_fields_json을 "금지/제한 원산지 값 목록"으로 재해석.
        prohibited_values = set(required_fields or [])
        if "origin_country" not in product_attributes:
            detail.satisfied = False
            detail.missing_fields = ["origin_country"]
            return detail

        origin = product_attributes.get("origin_country")
        if origin in prohibited_values:
            detail.satisfied = False
        return detail

    if rule.validation_type == PolicyValidationType.STRUCTURAL_FIELD_REQUIRED:
        missing = [
            field for field in (required_fields or [])
            if not product_attributes.get(field)
        ]
        if missing:
            detail.satisfied = False
            detail.missing_fields = missing
        return detail

    if rule.validation_type == PolicyValidationType.CATEGORY_RESTRICTED_EVIDENCE:
        # CA-3 — CATEGORY_PROHIBITED와 동일한 원칙: 특정 카테고리
        # 키워드 매치는 공식 카테고리 코드 없이 확정 판단의 근거가
        # 되지 않는다.
        if (
            CATEGORY_SCOPE_ALL not in category_scope
            and "official_category_code" not in product_attributes
        ):
            detail.satisfied = False
            detail.missing_fields = ["official_category_code"]
            return detail

        if rule.rule_code not in confirmed_evidence_rule_codes:
            detail.satisfied = False
            detail.missing_evidence = list(required_evidence or [])
        return detail

    if rule.validation_type == PolicyValidationType.EVIDENCE_REQUIRED_GENERIC:
        if rule.rule_code == "CATEGORY_NOTICE_INFO_REQUIRED":
            from app.domains.marketplace_listing.category_metadata import (
                validate_saved_notice_contract,
            )
            missing = validate_saved_notice_contract(product_attributes)
            if missing:
                detail.satisfied = False
                detail.missing_fields = missing
                return detail
        if rule.rule_code not in confirmed_evidence_rule_codes:
            detail.satisfied = False
            detail.missing_evidence = list(required_evidence or [])
        return detail

    # 알 수 없는 validation_type — fail-closed(통과시키지 않는다).
    detail.satisfied = False

    return detail


def evaluate_policy(
    rules: list[ChannelPolicyRule],
    *,
    category_hint: str | None,
    product_name: str,
    product_attributes: dict,
    confirmed_evidence_rule_codes: list[str],
) -> tuple[str, list[RuleEvaluationDetail]]:
    """규칙 목록 + 상품 정보를 받아 (result, rule_results)를 반환한다.
    DB에 아무것도 쓰지 않는다 — 저장은 service.py의 책임."""

    import json

    confirmed = set(confirmed_evidence_rule_codes)
    results: list[RuleEvaluationDetail] = []

    has_blocking_violation = False
    has_data_required = False
    has_action_required = False

    for rule in rules:
        category_scope = json.loads(rule.category_scope_json)
        required_fields = (
            json.loads(rule.required_fields_json)
            if rule.required_fields_json else None
        )
        required_evidence = (
            json.loads(rule.required_evidence_json)
            if rule.required_evidence_json else None
        )

        detail = _evaluate_rule(
            rule, category_scope, required_fields, required_evidence,
            category_hint=category_hint, product_name=product_name,
            product_attributes=product_attributes,
            confirmed_evidence_rule_codes=confirmed,
        )
        results.append(detail)

        if not detail.applies or detail.satisfied:
            continue

        if detail.missing_fields:
            # 원산지·구조화 필드 자체가 없으면 카테고리 금지 판정보다
            # 먼저 "데이터 부족"으로 분류한다 — 아직 무엇을 위반했는지도
            # 확정할 수 없는 상태이기 때문이다.
            has_data_required = True
        elif rule.validation_type in (
            PolicyValidationType.CATEGORY_PROHIBITED,
            PolicyValidationType.ORIGIN_COUNTRY_PROHIBITED,
        ) and rule.severity == PolicySeverity.BLOCKING:
            has_blocking_violation = True
        elif detail.missing_evidence:
            has_action_required = True
        elif rule.severity == PolicySeverity.BLOCKING:
            has_blocking_violation = True
        else:
            has_action_required = True

    if has_blocking_violation:
        result = ChannelPolicyResult.CHANNEL_POLICY_BLOCKED
    elif has_data_required:
        result = ChannelPolicyResult.CHANNEL_DATA_REQUIRED
    elif has_action_required:
        result = ChannelPolicyResult.CHANNEL_ELIGIBLE_WITH_ACTIONS
    else:
        result = ChannelPolicyResult.CHANNEL_ELIGIBLE

    return result, results


__all__ = ["evaluate_policy"]
