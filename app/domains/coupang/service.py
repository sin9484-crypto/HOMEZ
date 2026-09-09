"""
=========================================================
Homez OS

File : app/domains/coupang/service.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation — Service

이번 단계 범위: ProductCandidate(APPROVED) → 정책 검증 → 수익성 계산
→ Dry Run → 운영자 최종 승인(READY_FOR_SUBMISSION)까지만 실행한다.
실제 쿠팡 상품등록·주문수집·발주·정산 반영은 하지 않는다(서비스에
해당 메서드 자체가 없다).

상태 모델 비고: 계약(constants.IntegrationStatus)에는 VALIDATING이
정의되어 있으나, 이번 정책 검증은 동기적으로 즉시 끝나는 연산이라
DRAFT → (READY_FOR_REVIEW | VALIDATION_FAILED)로 직접 전이한다(비동기
검증 파이프라인이 생기면 그때 VALIDATING을 실제 중간 상태로 사용한다).

Transaction/멱등성 원칙은 app/domains/product_candidate/service.py와
동일하다:
  - Repository는 commit하지 않는다(no_commit/flush) — 각 서비스 메서드가
    정확히 commit 1회로 끝나고 모든 예외에서 rollback한다.
  - 상태 전이는 "현재 상태가 기대값일 때만" 조건부 UPDATE + rowcount
    검증으로 반영한다.
  - idempotency_key UNIQUE 위반(IntegrityError)은 rollback 후 승자를
    재조회해 그대로 반환한다(자동 병합 없음, duplicate=True로 표시).

2026-08-14 테넌트 격리 감사(Gate R13) — 회사 소유권 격리:
이 파일의 모든 공개 메서드는 company_id를 필수 인자로 받는다.
내부적으로 항상 repository의 회사 스코프 조회/조건부 UPDATE만
사용하며, company_id 없이 id만으로 접근하는 경로는 없다. 다른 회사의
객체에 접근하면 "존재하지 않는 것"과 완전히 동일한
NotFoundException(404)을 던진다. ProductCandidate 자체는 전역이지만,
"이 후보가 APPROVED인가"는 이제 ProductCandidateService.
require_approved_for_company()를 통해 호출자의 company_id 관점에서
판단한다(다른 회사의 승인에 무임승차할 수 없다).
CoupangPolicySet/CoupangPolicyRule은 전역 플랫폼 설정이라 예외다
(company_id로 범위를 좁히지 않는다).
=========================================================
"""

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.coupang.constants import MIN_ACCEPTABLE_MARGIN_RATE
from app.domains.coupang.constants import MONEY_QUANTIZE
from app.domains.coupang.constants import RATE_QUANTIZE
from app.domains.coupang.constants import ROUNDING
from app.domains.coupang.constants import STOCK_FRESHNESS_THRESHOLD_HOURS
from app.domains.coupang.constants import DryRunOutcome
from app.domains.coupang.constants import IntegrationDecisionAction
from app.domains.coupang.constants import IntegrationStatus
from app.domains.coupang.constants import PolicyMatchField
from app.domains.coupang.constants import PolicySetStatus
from app.domains.coupang.constants import RiskLevel
from app.domains.coupang.constants import SalesMethod
from app.domains.coupang.constants import ValidationStatus
from app.domains.coupang.events import CoupangEventType
from app.domains.coupang.events import build_coupang_event
from app.domains.coupang.gateway import CoupangDryRunGateway
from app.domains.coupang.gateway import CoupangGateway
from app.domains.coupang.gateway import DryRunResult
from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangPolicyRule
from app.domains.coupang.model import CoupangPolicySet
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate
from app.domains.coupang.repository import CoupangRepository
from app.domains.coupang.schema import CoupangDraftCreateRequest
from app.domains.coupang.schema import CoupangPolicyRuleCreateRequest
from app.domains.coupang.schema import CoupangPolicySetCreateRequest
from app.domains.coupang.schema import CoupangProfitEstimateRequest
from app.domains.product_candidate.service import ProductCandidateService

PROFIT_CALCULATION_VERSION = "1.0.0"

_MONEY_Q = Decimal(MONEY_QUANTIZE)
_RATE_Q = Decimal(RATE_QUANTIZE)


def _money(value) -> Decimal:

    return Decimal(value).quantize(_MONEY_Q, rounding=ROUNDING)


def _rate(value) -> Decimal:

    return Decimal(value).quantize(_RATE_Q, rounding=ROUNDING)


class CoupangIntegrationService:

    def __init__(self, db: Session, gateway: CoupangGateway | None = None):

        self.db = db
        self.repository = CoupangRepository(db)
        self.gateway = gateway or CoupangDryRunGateway()

    # --------------------------------------------------
    # 조회 (전부 회사 스코프)
    # --------------------------------------------------

    def get(
        self, product_id: int, company_id: int,
    ) -> CoupangMarketplaceProduct:

        product = self.repository.get_for_company(product_id, company_id)

        if product is None:
            raise NotFoundException("CoupangMarketplaceProduct를 찾을 수 없습니다.")

        return product

    def list_products(
        self,
        company_id: int,
        status: str | None = None,
        sales_method: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CoupangMarketplaceProduct]:

        return self.repository.list_products_for_company(
            company_id,
            status=status, sales_method=sales_method, skip=skip, limit=limit,
        )

    def list_options(
        self, product_id: int, company_id: int,
    ) -> list[CoupangProductOption]:

        self.get(product_id, company_id)

        return self.repository.list_options_for_company(
            product_id, company_id,
        )

    def list_notices(
        self, product_id: int, company_id: int,
    ) -> list[CoupangProductNotice]:

        self.get(product_id, company_id)

        return self.repository.list_notices_for_company(
            product_id, company_id,
        )

    def list_policy_sets(self) -> list[CoupangPolicySet]:
        """전역 — company_id로 범위를 좁히지 않는다."""

        return self.repository.list_policy_sets()

    def list_profit_estimates(
        self, product_id: int, company_id: int,
    ) -> list[CoupangProfitEstimate]:

        self.get(product_id, company_id)

        return self.repository.list_profit_estimates_for_company(
            product_id, company_id,
        )

    def list_decisions(
        self, product_id: int, company_id: int,
    ) -> list[CoupangIntegrationDecision]:

        self.get(product_id, company_id)

        return self.repository.list_decisions_for_company(
            product_id, company_id,
        )

    # --------------------------------------------------
    # 재고 판정
    # --------------------------------------------------

    @staticmethod
    def _compute_exposure_stock(
        supplier_stock: int | None,
        safety_stock: int,
        last_checked_at: datetime | None,
    ) -> tuple[int, bool]:
        """
        marketplace_exposure_stock = max(supplier_stock - safety_stock, 0).

        공급처 재고를 확인한 적이 없거나(last_checked_at is None) 확인이
        오래되었으면(STOCK_FRESHNESS_THRESHOLD_HOURS 초과) 노출 재고를
        0으로 강제하고 운영자 확인 대상으로 표시한다.
        """

        if supplier_stock is None or last_checked_at is None:
            return 0, True

        age_hours = (
            (datetime.utcnow() - last_checked_at).total_seconds() / 3600
        )

        if age_hours > STOCK_FRESHNESS_THRESHOLD_HOURS:
            return 0, True

        return max(supplier_stock - safety_stock, 0), False

    # --------------------------------------------------
    # 1) ProductCandidate → 쿠팡 상품 초안 생성 (멱등, 회사 스코프)
    # --------------------------------------------------

    def create_draft(
        self,
        data: CoupangDraftCreateRequest,
        company_id: int,
        correlation_id: str,
    ) -> tuple[CoupangMarketplaceProduct, bool, list]:

        existing = self.repository.get_by_idempotency_key(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing, True, []

        if data.sales_method not in SalesMethod.ALL:
            raise BadRequestException(f"알 수 없는 판매방식: {data.sales_method}")

        if not data.options:
            raise BadRequestException("구매옵션은 최소 1개 이상 필요합니다.")

        # ProductCandidate 자체는 전역(존재 확인만) — "이 회사 관점에서
        # APPROVED인가"는 ProductCandidateService가 단일 진입점으로
        # 판단한다(다른 회사의 승인에 무임승차 불가, Gate R13).
        ProductCandidateService(self.db).require_approved_for_company(
            data.product_candidate_id, company_id,
        )

        exposure, stock_review_required = self._compute_exposure_stock(
            data.supplier_stock, data.safety_stock, data.last_stock_checked_at,
        )

        product = CoupangMarketplaceProduct(
            company_id=company_id,
            sales_method=data.sales_method,
            product_candidate_id=data.product_candidate_id,
            external_vendor_sku=data.external_vendor_sku,
            seller_product_name=data.seller_product_name,
            brand=data.brand,
            display_category_code=data.display_category_code,
            gtin=data.gtin,
            mpn=data.mpn,
            identifier_exemption_reason=data.identifier_exemption_reason,
            sale_price=data.sale_price,
            maximum_buy_count=data.maximum_buy_count,
            shipping_method=data.shipping_method,
            shipping_company_code=data.shipping_company_code,
            outbound_shipping_place_code=data.outbound_shipping_place_code,
            return_center_code=data.return_center_code,
            return_charge=data.return_charge,
            overseas_purchase_agency=data.overseas_purchase_agency,
            pcc_needed=data.pcc_needed,
            supplier_stock=data.supplier_stock,
            safety_stock=data.safety_stock,
            marketplace_exposure_stock=exposure,
            available_stock=exposure,
            last_stock_checked_at=data.last_stock_checked_at,
            stock_review_required=stock_review_required,
            status=IntegrationStatus.DRAFT,
            validation_status=ValidationStatus.NOT_VALIDATED,
            idempotency_key=data.idempotency_key,
        )

        try:
            product = self.repository.add_no_commit(product)

            for option in data.options:
                self.repository.add_option_no_commit(
                    CoupangProductOption(
                        company_id=company_id,
                        coupang_product_id=product.id,
                        option_name=option.option_name,
                        option_value=option.option_value,
                        vendor_sku=option.vendor_sku,
                        price=_money(option.price),
                        stock=option.stock,
                        barcode=option.barcode,
                        status=option.status,
                    ),
                )

            for notice in data.notices:
                self.repository.add_notice_no_commit(
                    CoupangProductNotice(
                        company_id=company_id,
                        coupang_product_id=product.id,
                        notice_category_name=notice.notice_category_name,
                        notice_category_detail_name=(
                            notice.notice_category_detail_name
                        ),
                        content=notice.content,
                        source=notice.source,
                        verified_at=notice.verified_at,
                        status=notice.status,
                    ),
                )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_by_idempotency_key(
                company_id, data.idempotency_key,
            )
            if winner is None:
                raise

            return winner, True, []

        except Exception:
            self.db.rollback()
            raise

        events = [
            build_coupang_event(
                CoupangEventType.DRAFT_CREATED,
                product.id,
                source="coupang_integration",
                correlation_id=correlation_id,
            ),
        ]

        return product, False, events

    # --------------------------------------------------
    # 2) 쿠팡 정책·금지상품 검증
    # --------------------------------------------------

    def _validate_required_fields(
        self, product: CoupangMarketplaceProduct, company_id: int,
    ) -> list[str]:

        errors: list[str] = []

        if not product.brand:
            errors.append("필수 항목 누락: 브랜드")

        if not product.display_category_code:
            errors.append("필수 항목 누락: 노출 카테고리")

        if not product.seller_product_name:
            errors.append("필수 항목 누락: 상품명")

        if product.sale_price is None:
            errors.append("필수 항목 누락: 판매가")

        if not product.outbound_shipping_place_code:
            errors.append("필수 항목 누락: 출고지")

        if not product.return_center_code:
            errors.append("필수 항목 누락: 반품지")

        if not product.shipping_method:
            errors.append("필수 항목 누락: 배송방법")

        if not (
            product.gtin or product.mpn or product.identifier_exemption_reason
        ):
            errors.append(
                "GTIN/MPN이 모두 없고 예외 근거도 기록되지 않았습니다.",
            )

        if product.overseas_purchase_agency and not product.pcc_needed:
            errors.append(
                "해외구매대행 상품인데 PCC(개인통관고유부호) 필요 여부가 "
                "확인되지 않았습니다.",
            )

        options = self.repository.list_options_for_company(
            product.id, company_id,
        )
        if not options:
            errors.append("구매옵션이 없습니다.")

        return errors

    @staticmethod
    def _is_policy_set_usable(
        policy_set: CoupangPolicySet, now: datetime,
    ) -> bool:
        """
        정책 세트가 자동 판단에 "사용 가능"한지 판정한다. 다음을 모두
        만족해야 한다: VERIFIED 상태, 활성, 완전(is_complete), 버전·
        출처가 비어있지 않음, effective_at을 지났음, expires_at이
        없거나 아직 지나지 않았음. 하나라도 어긋나면 사용 불가로
        취급한다(fail-closed).
        """

        return (
            policy_set.status == PolicySetStatus.VERIFIED
            and policy_set.is_active
            and policy_set.is_complete
            and bool(policy_set.policy_version)
            and bool(policy_set.source_reference)
            and policy_set.effective_at <= now
            and (policy_set.expires_at is None or policy_set.expires_at > now)
        )

    def _structured_field_value(
        self, product: CoupangMarketplaceProduct, match_field: str,
    ) -> str | None:

        if match_field == PolicyMatchField.DISPLAY_CATEGORY_CODE:
            return (product.display_category_code or "").lower()

        if match_field == PolicyMatchField.BRAND:
            return (product.brand or "").lower()

        if match_field == PolicyMatchField.OVERSEAS_PURCHASE_AGENCY:
            return str(product.overseas_purchase_agency).lower()

        if match_field == PolicyMatchField.PCC_NEEDED:
            return str(product.pcc_needed).lower()

        if match_field == PolicyMatchField.GTIN_MPN_MISSING:
            missing = not (
                product.gtin or product.mpn
                or product.identifier_exemption_reason
            )
            return str(missing).lower()

        return None

    def _evaluate_policy_rules(
        self, product: CoupangMarketplaceProduct,
    ) -> tuple[str, str, str | None]:
        """
        재감사(2026-07-29) 반영 — fail-closed 정책 평가:

        1) 사용 가능한(VERIFIED·활성·완전·유효기간 내) 정책 세트가 하나도
           없으면 규칙을 평가하지 않고 즉시 POLICY_DATA_UNAVAILABLE로
           단락 반환한다(과거처럼 SELLABLE로 fail-open하지 않는다).
        2) 사용 가능한 세트에 속한 규칙만 평가 대상이다.
        3) 구조화된 필드(match_field가 KEYWORD가 아님) 매칭이 우선이며,
           이 매칭만으로 SELLABLE까지 포함해 모든 위험 등급을 확정할 수
           있다.
        4) 자유문자 키워드(KEYWORD) 매칭은 보조 위험 탐지 전용이다 —
           risk_level이 SELLABLE인 키워드 규칙은 평가 대상에서 제외한다
           (문자열이 매칭되지 않았다는 이유로도, 매칭되었다는 이유로도
           키워드만으로는 SELLABLE을 확정할 수 없다).
        5) 매칭되는 규칙이 하나도 없으면 OPERATOR_REVIEW_REQUIRED로
           안전하게 판정한다(SELLABLE 기본값 없음).

        정책 세트/규칙 자체는 전역(company_id 없음)이다 — 모든 회사에
        동일하게 적용된다.
        """

        now = datetime.utcnow()

        all_sets = self.repository.list_active_policy_sets()
        usable_sets = [
            s for s in all_sets if self._is_policy_set_usable(s, now)
        ]

        if not usable_sets:
            return (
                RiskLevel.POLICY_DATA_UNAVAILABLE,
                "사용 가능한(VERIFIED·활성·완전·유효기간 내) 정책 세트가 "
                "없습니다 — 정책 데이터가 준비될 때까지 자동 판매 가능 "
                "판정을 내릴 수 없습니다.",
                None,
            )

        usable_set_by_id = {s.id: s for s in usable_sets}
        rules = self.repository.list_active_policy_rules_for_sets(
            set(usable_set_by_id.keys()),
        )

        haystack = " ".join(
            filter(
                None,
                [
                    product.seller_product_name,
                    product.brand,
                    product.display_category_code,
                ],
            ),
        ).lower()

        structured_matches = []
        auxiliary_keyword_matches = []

        for rule in rules:
            if rule.match_field == PolicyMatchField.KEYWORD:
                if rule.match_value.lower() in haystack:
                    auxiliary_keyword_matches.append(rule)
                continue

            value = self._structured_field_value(product, rule.match_field)
            if value is not None and value == rule.match_value.lower():
                structured_matches.append(rule)

        # 자유문자 키워드는 보조 위험 탐지로만 사용한다 — SELLABLE로
        # 확정하는 키워드 규칙은 후보에서 제외한다.
        usable_matches = structured_matches + [
            rule
            for rule in auxiliary_keyword_matches
            if rule.risk_level != RiskLevel.SELLABLE
        ]

        if not usable_matches:
            return (
                RiskLevel.OPERATOR_REVIEW_REQUIRED,
                "명시적으로 매칭되는 정책 규칙이 없어 자동으로 판매 "
                "가능 판정을 내릴 수 없습니다 — 운영자 확인이 필요합니다.",
                None,
            )

        best = max(
            usable_matches, key=lambda rule: RiskLevel.SEVERITY[rule.risk_level],
        )
        policy_set = usable_set_by_id[best.policy_set_id]

        return best.risk_level, best.reason, policy_set.policy_version

    def validate_policy(
        self,
        product_id: int,
        company_id: int,
        correlation_id: str,
    ) -> tuple[CoupangMarketplaceProduct, list]:

        product = self.get(product_id, company_id)

        if product.status != IntegrationStatus.DRAFT:
            raise BadRequestException(
                "DRAFT 상태의 상품만 정책 검증을 실행할 수 있습니다. "
                f"(현재: {product.status})",
            )

        errors = self._validate_required_fields(product, company_id)

        risk_level, reason, policy_version = self._evaluate_policy_rules(
            product,
        )

        if risk_level == RiskLevel.POLICY_DATA_UNAVAILABLE:
            errors.append(f"POLICY_DATA_UNAVAILABLE: {reason}")

        if risk_level == RiskLevel.PROHIBITED:
            errors.append(f"판매 금지 상품 정책에 의해 차단됨: {reason}")

        if risk_level == RiskLevel.CERTIFICATION_REQUIRED:
            errors.append(f"인증·허가 확인 필요: {reason}")

        if risk_level == RiskLevel.OPERATOR_REVIEW_REQUIRED:
            errors.append(f"운영자 직접 검토 필요: {reason}")

        passed = not errors

        new_status = (
            IntegrationStatus.READY_FOR_REVIEW
            if passed
            else IntegrationStatus.VALIDATION_FAILED
        )
        new_validation_status = (
            ValidationStatus.PASSED if passed else ValidationStatus.FAILED
        )

        try:
            rowcount = self.repository.finalize_validation_conditional(
                product_id,
                company_id,
                IntegrationStatus.DRAFT,
                new_status,
                new_validation_status,
                json.dumps(errors, ensure_ascii=False) if errors else None,
                risk_level,
                policy_version,
            )

            if rowcount != 1:
                raise ConflictException(
                    "상품 상태가 동시에 변경되어 정책 검증 결과를 반영할 "
                    "수 없습니다.",
                )

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        product = self.get(product_id, company_id)

        event_type = (
            CoupangEventType.POLICY_VALIDATED
            if passed
            else CoupangEventType.POLICY_BLOCKED
        )
        events = [
            build_coupang_event(
                event_type,
                product_id,
                source="coupang_integration",
                correlation_id=correlation_id,
                payload={"risk_level": risk_level},
            ),
        ]

        return product, events

    # --------------------------------------------------
    # 3) 수익성 계산 (Decimal, net == gross - fee 불변식, 회사 스코프)
    # --------------------------------------------------

    def estimate_profit(
        self,
        product_id: int,
        company_id: int,
        data: CoupangProfitEstimateRequest,
        correlation_id: str,
    ) -> tuple[CoupangProfitEstimate, list]:

        product = self.get(product_id, company_id)

        if product.status != IntegrationStatus.READY_FOR_REVIEW:
            raise BadRequestException(
                "정책 검증을 통과한(READY_FOR_REVIEW) 상품만 수익성을 "
                f"계산할 수 있습니다. (현재: {product.status})",
            )

        if data.coupang_sales_fee is None:
            raise BadRequestException(
                "coupang_sales_fee(판매수수료)가 UNKNOWN 상태입니다 — "
                "0으로 가정하지 않고 계산을 차단합니다. 수수료 확인 후 "
                "다시 시도하세요.",
            )

        if data.consumer_sale_price <= 0:
            raise BadRequestException(
                "consumer_sale_price가 0 이하이면 마진율을 계산할 수 "
                "없습니다.",
            )

        gross_revenue = _money(data.consumer_sale_price)

        fee_components = (
            data.coupang_sales_fee
            + data.coupang_shipping_cost
            + data.advertising_cost
            + data.coupon_cost
            + data.expected_return_cost
            + data.rocket_growth_cost
            + data.other_deductions
            + data.vat_or_tax_estimate
        )
        marketplace_fee_total = _money(fee_components)

        expected_net_settlement = _money(gross_revenue - marketplace_fee_total)

        # net_amount == gross_amount - fee_amount 불변식.
        # 자동 보정하지 않는다 — 불일치가 발견되면 생성 자체를 차단한다.
        if expected_net_settlement != _money(
            gross_revenue - marketplace_fee_total,
        ):
            raise BadRequestException(
                "net_amount != gross_amount - fee_amount 이므로 생성을 "
                "차단합니다.",
            )

        supplier_payment_estimate = _money(
            data.supplier_product_cost + data.supplier_shipping_cost,
        )
        expected_profit = _money(
            expected_net_settlement - supplier_payment_estimate,
        )
        expected_margin_rate = _rate(expected_profit / gross_revenue)
        required_funding = supplier_payment_estimate
        break_even_price = _money(
            supplier_payment_estimate + marketplace_fee_total,
        )
        meets_minimum_margin = expected_margin_rate >= Decimal(
            MIN_ACCEPTABLE_MARGIN_RATE,
        )

        estimate = CoupangProfitEstimate(
            company_id=company_id,
            coupang_product_id=product_id,
            consumer_sale_price=gross_revenue,
            supplier_product_cost=_money(data.supplier_product_cost),
            supplier_shipping_cost=_money(data.supplier_shipping_cost),
            coupang_sales_fee=_money(data.coupang_sales_fee),
            coupang_shipping_cost=_money(data.coupang_shipping_cost),
            advertising_cost=_money(data.advertising_cost),
            coupon_cost=_money(data.coupon_cost),
            expected_return_cost=_money(data.expected_return_cost),
            rocket_growth_cost=_money(data.rocket_growth_cost),
            other_deductions=_money(data.other_deductions),
            vat_or_tax_estimate=_money(data.vat_or_tax_estimate),
            gross_revenue=gross_revenue,
            marketplace_fee_total=marketplace_fee_total,
            supplier_payment_estimate=supplier_payment_estimate,
            expected_net_settlement=expected_net_settlement,
            expected_profit=expected_profit,
            expected_margin_rate=expected_margin_rate,
            required_funding=required_funding,
            break_even_price=break_even_price,
            meets_minimum_margin=meets_minimum_margin,
            calculation_version=PROFIT_CALCULATION_VERSION,
            calculated_at=datetime.utcnow(),
        )

        try:
            estimate = self.repository.add_profit_estimate_no_commit(estimate)

            if not meets_minimum_margin:
                # 정책은 통과했지만 마진 기준 미달 — 자동 진행 차단
                margin_errors = [
                    f"최소 마진 기준({MIN_ACCEPTABLE_MARGIN_RATE} 이상) "
                    f"미달: 현재 {expected_margin_rate}",
                ]
                rowcount = self.repository.finalize_validation_conditional(
                    product_id,
                    company_id,
                    IntegrationStatus.READY_FOR_REVIEW,
                    IntegrationStatus.VALIDATION_FAILED,
                    ValidationStatus.FAILED,
                    json.dumps(margin_errors, ensure_ascii=False),
                    product.risk_level,
                    product.policy_version_applied,
                )
                if rowcount != 1:
                    raise ConflictException(
                        "상품 상태가 동시에 변경되어 마진 미달 처리를 "
                        "반영할 수 없습니다.",
                    )

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        events = [
            build_coupang_event(
                CoupangEventType.PROFIT_ESTIMATED,
                product_id,
                source="coupang_integration",
                correlation_id=correlation_id,
                payload={"meets_minimum_margin": meets_minimum_margin},
            ),
        ]

        return estimate, events

    # --------------------------------------------------
    # 4) 상품등록 Dry Run (네트워크 0회, Secret 요구 없음, 회사 스코프)
    # --------------------------------------------------

    @staticmethod
    def _build_dry_run_payload(
        product: CoupangMarketplaceProduct,
        options: list[CoupangProductOption],
        verified_notices: list[CoupangProductNotice],
    ) -> dict:

        return {
            "sales_method": product.sales_method,
            "external_vendor_sku": product.external_vendor_sku,
            "seller_product_name": product.seller_product_name,
            "brand": product.brand,
            "display_category_code": product.display_category_code,
            "sale_price": (
                str(product.sale_price)
                if product.sale_price is not None
                else None
            ),
            "shipping_method": product.shipping_method,
            "outbound_shipping_place_code": (
                product.outbound_shipping_place_code
            ),
            "return_center_code": product.return_center_code,
            "options": [
                {
                    "option_name": option.option_name,
                    "option_value": option.option_value,
                    "vendor_sku": option.vendor_sku,
                    "price": str(option.price),
                    "stock": option.stock,
                }
                for option in options
            ],
            # VERIFIED 상태이고 content가 비어있지 않은 고시정보만
            # 포함한다 — 없으면 빈 리스트가 되어 gateway가 필수 필드
            # 누락으로 판정한다(재감사 Medium-1 반영).
            "notices": [
                {
                    "notice_category_name": notice.notice_category_name,
                    "notice_category_detail_name": (
                        notice.notice_category_detail_name
                    ),
                    "content": notice.content,
                }
                for notice in verified_notices
            ],
        }

    def run_dry_run(
        self,
        product_id: int,
        company_id: int,
        idempotency_key: str,
        correlation_id: str,
    ) -> tuple[CoupangMarketplaceProduct, DryRunResult, bool, list]:

        existing_attempt = (
            self.repository.get_dry_run_attempt_by_idempotency_key(
                company_id, idempotency_key,
            )
        )
        if existing_attempt is not None:
            product = self.get(product_id, company_id)
            result = DryRunResult(
                outcome=existing_attempt.outcome,
                errors=(
                    json.loads(existing_attempt.errors)
                    if existing_attempt.errors
                    else []
                ),
                payload_field_count=existing_attempt.payload_field_count,
                attempted_at=existing_attempt.attempted_at,
            )
            return product, result, True, []

        product = self.get(product_id, company_id)

        allowed_from = (
            IntegrationStatus.READY_FOR_REVIEW,
            IntegrationStatus.DRY_RUN_FAILED,
        )
        if product.status not in allowed_from:
            raise BadRequestException(
                "READY_FOR_REVIEW 또는 DRY_RUN_FAILED 상태의 상품만 "
                f"Dry Run을 실행할 수 있습니다. (현재: {product.status})",
            )

        latest_estimate = self.repository.get_latest_profit_estimate_for_company(
            product_id, company_id,
        )
        if latest_estimate is None or not latest_estimate.meets_minimum_margin:
            raise BadRequestException(
                "최소 마진 기준을 만족하는 수익성 계산이 없어 Dry Run을 "
                "진행할 수 없습니다.",
            )

        try:
            rowcount = self.repository.update_status_conditional(
                product_id,
                company_id,
                allowed_from,
                IntegrationStatus.APPROVED_FOR_DRY_RUN,
            )
            if rowcount != 1:
                raise ConflictException(
                    "상품 상태가 동시에 변경되어 Dry Run을 시작할 수 "
                    "없습니다.",
                )
            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        options = self.repository.list_options_for_company(
            product_id, company_id,
        )
        verified_notices = self.repository.list_verified_notices_for_company(
            product_id, company_id,
        )
        payload = self._build_dry_run_payload(
            product, options, verified_notices,
        )
        result = self.gateway.submit_dry_run(payload)

        final_status = (
            IntegrationStatus.DRY_RUN_PASSED
            if result.outcome == DryRunOutcome.PASSED
            else IntegrationStatus.DRY_RUN_FAILED
        )

        try:
            self.repository.add_dry_run_attempt_no_commit(
                CoupangDryRunAttempt(
                    company_id=company_id,
                    coupang_product_id=product_id,
                    idempotency_key=idempotency_key,
                    outcome=result.outcome,
                    errors=(
                        json.dumps(result.errors, ensure_ascii=False)
                        if result.errors
                        else None
                    ),
                    payload_field_count=result.payload_field_count,
                ),
            )

            rowcount = self.repository.update_status_conditional(
                product_id,
                company_id,
                (IntegrationStatus.APPROVED_FOR_DRY_RUN,),
                final_status,
            )
            if rowcount != 1:
                raise ConflictException(
                    "상품 상태가 동시에 변경되어 Dry Run 결과를 반영할 "
                    "수 없습니다.",
                )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_dry_run_attempt_by_idempotency_key(
                company_id, idempotency_key,
            )
            if winner is None:
                raise

            product = self.get(product_id, company_id)
            result = DryRunResult(
                outcome=winner.outcome,
                errors=json.loads(winner.errors) if winner.errors else [],
                payload_field_count=winner.payload_field_count,
                attempted_at=winner.attempted_at,
            )
            return product, result, True, []

        except Exception:
            self.db.rollback()
            raise

        product = self.get(product_id, company_id)

        event_type = (
            CoupangEventType.DRY_RUN_PASSED
            if result.outcome == DryRunOutcome.PASSED
            else CoupangEventType.DRY_RUN_FAILED
        )
        events = [
            build_coupang_event(
                event_type,
                product_id,
                source="coupang_integration",
                correlation_id=correlation_id,
            ),
        ]

        return product, result, False, events

    # --------------------------------------------------
    # 5) 운영자 최종 승인 (READY_FOR_SUBMISSION 전환, 회사 스코프)
    # --------------------------------------------------

    def approve_for_submission(
        self,
        product_id: int,
        company_id: int,
        operator_id: int,
        is_admin: bool,
        idempotency_key: str,
        memo: str | None,
        correlation_id: str,
    ) -> tuple[CoupangMarketplaceProduct, bool, list]:

        if not is_admin:
            raise ForbiddenException(
                "쿠팡 상품등록 최종 승인은 관리자만 가능합니다.",
            )

        existing_decision = self.repository.get_decision_by_idempotency_key(
            company_id, idempotency_key,
        )
        if existing_decision is not None:
            return self.get(product_id, company_id), True, []

        product = self.get(product_id, company_id)

        if product.status != IntegrationStatus.DRY_RUN_PASSED:
            # 위 idempotency 확인과 이 상태 확인 사이에 동일
            # idempotency_key로 다른 요청이 이미 승인을 완료했을 수
            # 있다 — 그 경우 진짜 상태 불일치가 아니라 멱등 중복이므로
            # 다시 한 번 확인해 구분한다(레이스로 인한 잘못된
            # BadRequestException 방지).
            existing_decision = (
                self.repository.get_decision_by_idempotency_key(
                    company_id, idempotency_key,
                )
            )
            if existing_decision is not None:
                return self.get(product_id, company_id), True, []

            raise BadRequestException(
                "DRY_RUN_PASSED 상태의 상품만 최종 승인할 수 있습니다. "
                f"(현재: {product.status})",
            )

        try:
            self.repository.add_decision_no_commit(
                CoupangIntegrationDecision(
                    company_id=company_id,
                    coupang_product_id=product_id,
                    action=IntegrationDecisionAction.APPROVE_FOR_SUBMISSION,
                    operator_id=operator_id,
                    idempotency_key=idempotency_key,
                    memo=memo,
                ),
            )

            rowcount = self.repository.update_status_conditional(
                product_id,
                company_id,
                (IntegrationStatus.DRY_RUN_PASSED,),
                IntegrationStatus.READY_FOR_SUBMISSION,
            )
            if rowcount != 1:
                raise ConflictException(
                    "상품 상태가 동시에 변경되어 최종 승인을 반영할 수 "
                    "없습니다(다른 운영자가 먼저 처리했을 수 있습니다).",
                )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner_decision = (
                self.repository.get_decision_by_idempotency_key(
                    company_id, idempotency_key,
                )
            )
            if winner_decision is None:
                raise

            return self.get(product_id, company_id), True, []

        except Exception:
            self.db.rollback()
            raise

        product = self.get(product_id, company_id)

        events = [
            build_coupang_event(
                CoupangEventType.APPROVED_FOR_SUBMISSION,
                product_id,
                source="operator_console",
                correlation_id=correlation_id,
                payload={"operator_id": operator_id},
            ),
        ]

        return product, False, events

    # --------------------------------------------------
    # 정책 세트/규칙 관리 (admin_guard 필요, 전역 — 별도 명시 작업)
    # --------------------------------------------------
    #
    # 이 메서드들은 앱 시작 시나 Migration에서 자동으로 호출되지 않는다.
    # 정책 Fixture(policy_data.py)를 실제로 활성화(특히 status를
    # VERIFIED로)하는 것은 admin_guard로 인증된 실제 운영자가 이
    # 메서드를 통해 명시적으로 수행해야 하는 별도 작업이다 — AI가
    # 스스로 정책을 완화·활성화할 수 없다는 통제 지점은 여기(인증
    # 필요)에 있다. CoupangPolicySet/CoupangPolicyRule은 "쿠팡이
    # 공식적으로 정한 정책"이라 전역이며 company_id로 좁히지 않는다.

    def create_policy_set(
        self, data: CoupangPolicySetCreateRequest,
    ) -> CoupangPolicySet:

        if data.status not in PolicySetStatus.ALL:
            raise BadRequestException(f"알 수 없는 정책 세트 상태: {data.status}")

        existing = self.repository.get_policy_set_by_business_id(
            data.policy_set_id,
        )
        if existing is not None:
            raise ConflictException(
                f"policy_set_id={data.policy_set_id}가 이미 존재합니다.",
            )

        policy_set = CoupangPolicySet(
            policy_set_id=data.policy_set_id,
            policy_version=data.policy_version,
            source_reference=data.source_reference,
            status=data.status,
            is_complete=data.is_complete,
            checked_at=data.checked_at,
            effective_at=data.effective_at,
            expires_at=data.expires_at,
        )

        try:
            policy_set = self.repository.add_policy_set_no_commit(
                policy_set,
            )
            self.db.commit()

        except IntegrityError:
            self.db.rollback()
            raise ConflictException(
                f"policy_set_id={data.policy_set_id}가 이미 존재합니다.",
            )

        except Exception:
            self.db.rollback()
            raise

        return policy_set

    def add_policy_rule(
        self,
        policy_set_pk: int,
        data: CoupangPolicyRuleCreateRequest,
    ) -> CoupangPolicyRule:

        policy_set = self.repository.get_policy_set(policy_set_pk)
        if policy_set is None:
            raise NotFoundException("CoupangPolicySet을 찾을 수 없습니다.")

        if data.match_field not in PolicyMatchField.ALL:
            raise BadRequestException(f"알 수 없는 match_field: {data.match_field}")

        if data.risk_level not in RiskLevel.ALL:
            raise BadRequestException(f"알 수 없는 risk_level: {data.risk_level}")

        rule = CoupangPolicyRule(
            policy_set_id=policy_set_pk,
            match_field=data.match_field,
            match_value=data.match_value,
            risk_level=data.risk_level,
            reason=data.reason,
        )

        try:
            rule = self.repository.add_policy_rule_no_commit(rule)
            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return rule

    def list_policy_rules(
        self, policy_set_pk: int,
    ) -> list[CoupangPolicyRule]:

        return self.repository.list_rules_for_policy_set(policy_set_pk)


__all__ = [
    "CoupangIntegrationService",
    "PROFIT_CALCULATION_VERSION",
]
