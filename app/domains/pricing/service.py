"""
=========================================================
Homez OS

File : app/domains/pricing/service.py

Pricing & Margin Reconciliation Service — V7 Gate 5(2026-08-15).

기존 검증된 도메인을 연결·재사용한다(정산/마진 계산기를 다시 만들지
않는다):
  - `app/domains/marketplace_listing/margin_calculator.py`(Gate I) —
    Decimal 손익 계산 순수 함수를 그대로 호출한다.
  - `app/domains/settlement`(V2.3) — MarketplaceSettlement를 읽기
    전용으로만 참조한다(이 서비스가 Settlement의 상태를 직접 바꾸지
    않는다 — 정산 상태 변경은 여전히 SettlementService 전담, 도메인
    경계 유지).
  - `app/domains/order`/`purchase`/`inventory`(Gate 3/4) — 실제
    판매수량·매입원가·채널SKU매핑을 읽기 전용으로만 참조한다.

트랜잭션 원칙(Gate 4가 겪은 결함과 동일 클래스를 피하기 위해):
  - 이 서비스 자신의 모든 하위 연산은 `repository`의 `_no_commit`류
    메서드(add/flush만, commit 없음)만 쓴다 — 이 서비스가 호출하는
    다른 Domain Service는 전혀 없다(순수 읽기 전용 cross-domain
    쿼리만 사용, Order/Inventory/Funding 등 자체 commit/rollback을
    갖는 Service는 호출하지 않는다) — 따라서 이 서비스의 각 공개
    메서드는 항상 정확히 1회의 최종 commit만 갖는다.
  - `record_actual_margin()`만 예외적으로 2단계 commit을 쓴다:
    (1) MarginSnapshot들을 먼저 확정 commit한 뒤, (2) 그 다음
    SettlementReconciliation upsert를 시도한다 — 이유는 (2)가
    IntegrityError로 rollback()해야 할 수 있는데, 그 rollback이
    (1)에서 만든 아직 커밋되지 않은 MarginSnapshot들까지 함께
    지워버리면 안 되기 때문이다(V7 Gate 4에서 실제로 겪은 "하위
    연산의 rollback이 내 자신의 미확정 변경까지 지운다" 결함과 정확히
    같은 클래스 — 이번엔 재발하지 않도록 미리 커밋 경계를 나눴다).
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import ROUND_HALF_UP
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.pricing.constants import EstimatedComponent
from app.domains.pricing.constants import MarginSnapshotReason
from app.domains.pricing.constants import MarginType
from app.domains.pricing.constants import PriceChangeStatus
from app.domains.pricing.constants import ReconciliationStatus
from app.domains.pricing.fingerprint import compute_pricing_fingerprint
from app.domains.pricing.model import MarginSnapshot
from app.domains.pricing.model import PriceChangeRequest
from app.domains.pricing.model import PriceChangeStatusEvent
from app.domains.pricing.model import ProductPricing
from app.domains.pricing.model import SettlementReconciliation
from app.domains.pricing.repository import PricingRepository
from app.domains.pricing.schema import EconomicsInputsUpdate
from app.domains.pricing.schema import PriceChangeCreate
from app.domains.pricing.schema import PriceChangeDecision
from app.domains.pricing.schema import ProductPricingInitCreate
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)

_MONEY_QUANT = Decimal("0.01")
_RATE_QUANT = Decimal("0.0001")


def _money(value) -> Decimal:

    return Decimal(value).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _rate(value) -> Decimal:

    return Decimal(value).quantize(_RATE_QUANT, rounding=ROUND_HALF_UP)


class PricingService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = PricingRepository(db)

    # --------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------

    def _get_pricing_required(
        self, pricing_id: int, company_id: int,
    ) -> ProductPricing:

        pricing = self.repository.get_pricing_by_id(pricing_id, company_id)

        if pricing is None:
            raise NotFoundException("가격 정보를 찾을 수 없습니다.")

        return pricing

    def _get_pricing_by_listing_required(
        self, listing_id: int, company_id: int,
    ) -> ProductPricing:

        pricing = self.repository.get_pricing_by_listing(
            listing_id, company_id,
        )

        if pricing is None:
            raise NotFoundException(
                "이 리스팅의 가격 정보가 아직 초기화되지 않았습니다.",
            )

        return pricing

    def _get_price_change_required(
        self, request_id: int, company_id: int,
    ) -> PriceChangeRequest:

        request = self.repository.get_price_change_by_id(
            request_id, company_id,
        )

        if request is None:
            raise NotFoundException("가격 변경 요청을 찾을 수 없습니다.")

        return request

    @staticmethod
    def _compute_expected_economics(
        *,
        listing_id: int,
        sale_price,
        cost_of_goods,
        shipping_cost,
        packaging_cost,
        ad_cost,
        channel_fee_rate,
        payment_fee_rate,
        return_reserve_rate,
        tax_basis_rate,
    ):
        """
        margin_calculator.calculate_economics()를 그대로 재사용한다
        (요구사항 1 — 새 계산기를 만들지 않는다). EconomicsInputItem.
        marketplace_account_id는 계산기가 결과에 그대로 반향만 하는
        상관관계 id일 뿐이라 listing_id를 그대로 넘긴다(다른 의미로
        재해석하지 않는다 — 이 서비스는 그 반향값을 사용하지 않는다).
        """

        from app.domains.marketplace_listing.listing_wizard_schema import (
            EconomicsInputItem,
        )
        from app.domains.marketplace_listing.margin_calculator import (
            calculate_economics,
        )

        item = EconomicsInputItem(
            marketplace_account_id=listing_id,
            cost_of_goods=Decimal(cost_of_goods),
            sale_price=Decimal(sale_price),
            channel_fee_rate=Decimal(channel_fee_rate),
            payment_fee_rate=Decimal(payment_fee_rate),
            shipping_cost=Decimal(shipping_cost),
            packaging_cost=Decimal(packaging_cost),
            ad_cost=Decimal(ad_cost),
            return_reserve_rate=Decimal(return_reserve_rate),
            tax_basis_rate=Decimal(tax_basis_rate),
        )

        return calculate_economics(item)

    @staticmethod
    def _build_expected_snapshot(
        *,
        company_id: int,
        listing_id: int,
        sale_price,
        cost_of_goods,
        shipping_cost,
        packaging_cost,
        ad_cost,
        channel_fee_rate,
        payment_fee_rate,
        return_reserve_rate,
        tax_basis_rate,
        result,
        reason: str,
    ) -> MarginSnapshot:

        sale_price_d = Decimal(sale_price)

        channel_fee = _money(sale_price_d * Decimal(channel_fee_rate))
        payment_fee = _money(sale_price_d * Decimal(payment_fee_rate))
        return_reserve = _money(
            sale_price_d * Decimal(return_reserve_rate),
        )
        tax = _money(sale_price_d * Decimal(tax_basis_rate))

        return MarginSnapshot(
            company_id=company_id,
            listing_id=listing_id,
            margin_type=MarginType.EXPECTED,
            order_id=None,
            settlement_id=None,
            reason=reason,
            quantity_basis=1,
            revenue=result.expected_revenue,
            cost_of_goods=_money(Decimal(cost_of_goods)),
            channel_fee=channel_fee,
            payment_fee=payment_fee,
            shipping_cost=_money(Decimal(shipping_cost)),
            packaging_cost=_money(Decimal(packaging_cost)),
            ad_cost=_money(Decimal(ad_cost)),
            return_reserve=return_reserve,
            tax=tax,
            refund_adjustment=Decimal("0"),
            total_cost=result.total_cost,
            margin_amount=result.margin_amount,
            margin_rate=result.margin_rate,
            estimated_components_json="[]",
        )

    @staticmethod
    def _pricing_fingerprint(pricing: ProductPricing) -> str:

        return compute_pricing_fingerprint(
            listing_id=pricing.listing_id,
            sale_price=Decimal(pricing.current_sale_price),
            cost_of_goods=Decimal(pricing.cost_of_goods),
            shipping_cost=Decimal(pricing.shipping_cost),
            packaging_cost=Decimal(pricing.packaging_cost),
            ad_cost=Decimal(pricing.ad_cost),
            channel_fee_rate=Decimal(pricing.channel_fee_rate),
            payment_fee_rate=Decimal(pricing.payment_fee_rate),
            return_reserve_rate=Decimal(pricing.return_reserve_rate),
            tax_basis_rate=Decimal(pricing.tax_basis_rate),
        )

    # --------------------------------------------------
    # ProductPricing 초기화 / 원가 갱신(요구사항 1)
    # --------------------------------------------------

    def initialize_pricing(
        self,
        company_id: int,
        data: ProductPricingInitCreate,
        actor_user_id: int | None = None,
    ) -> ProductPricing:

        existing = self.repository.get_pricing_by_listing(
            data.listing_id, company_id,
        )
        if existing is not None:
            return existing

        pricing = ProductPricing(
            company_id=company_id,
            listing_id=data.listing_id,
            current_sale_price=data.initial_sale_price,
            cost_of_goods=data.cost_of_goods,
            shipping_cost=data.shipping_cost,
            packaging_cost=data.packaging_cost,
            ad_cost=data.ad_cost,
            channel_fee_rate=data.channel_fee_rate,
            payment_fee_rate=data.payment_fee_rate,
            return_reserve_rate=data.return_reserve_rate,
            tax_basis_rate=data.tax_basis_rate,
            expected_revenue=Decimal("0"),
            expected_total_cost=Decimal("0"),
            expected_margin_amount=Decimal("0"),
            expected_margin_rate=Decimal("0"),
        )

        try:
            pricing = self.repository.add_pricing_no_commit(pricing)
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_pricing_by_listing(
                data.listing_id, company_id,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "가격 정보 초기화 중 충돌이 발생했습니다.",
            )

        result = self._compute_expected_economics(
            listing_id=pricing.listing_id,
            sale_price=data.initial_sale_price,
            cost_of_goods=data.cost_of_goods,
            shipping_cost=data.shipping_cost,
            packaging_cost=data.packaging_cost,
            ad_cost=data.ad_cost,
            channel_fee_rate=data.channel_fee_rate,
            payment_fee_rate=data.payment_fee_rate,
            return_reserve_rate=data.return_reserve_rate,
            tax_basis_rate=data.tax_basis_rate,
        )

        pricing.expected_revenue = result.expected_revenue
        pricing.expected_total_cost = result.total_cost
        pricing.expected_margin_amount = result.margin_amount
        pricing.expected_margin_rate = result.margin_rate
        pricing.expected_break_even_price = result.break_even_price
        self.repository.save_pricing_no_commit(pricing)

        snapshot = self._build_expected_snapshot(
            company_id=company_id,
            listing_id=pricing.listing_id,
            sale_price=data.initial_sale_price,
            cost_of_goods=data.cost_of_goods,
            shipping_cost=data.shipping_cost,
            packaging_cost=data.packaging_cost,
            ad_cost=data.ad_cost,
            channel_fee_rate=data.channel_fee_rate,
            payment_fee_rate=data.payment_fee_rate,
            return_reserve_rate=data.return_reserve_rate,
            tax_basis_rate=data.tax_basis_rate,
            result=result,
            reason=MarginSnapshotReason.PRICING_INITIALIZED,
        )
        self.repository.add_margin_snapshot_no_commit(snapshot)

        self.db.commit()
        self.db.refresh(pricing)

        return pricing

    def update_economics_inputs(
        self,
        pricing_id: int,
        company_id: int,
        data: EconomicsInputsUpdate,
        actor_user_id: int | None = None,
    ) -> ProductPricing:

        pricing = self._get_pricing_required(pricing_id, company_id)
        expected_version = pricing.version
        sale_price = Decimal(pricing.current_sale_price)

        result = self._compute_expected_economics(
            listing_id=pricing.listing_id,
            sale_price=sale_price,
            cost_of_goods=data.cost_of_goods,
            shipping_cost=data.shipping_cost,
            packaging_cost=data.packaging_cost,
            ad_cost=data.ad_cost,
            channel_fee_rate=data.channel_fee_rate,
            payment_fee_rate=data.payment_fee_rate,
            return_reserve_rate=data.return_reserve_rate,
            tax_basis_rate=data.tax_basis_rate,
        )

        values = dict(
            cost_of_goods=data.cost_of_goods,
            shipping_cost=data.shipping_cost,
            packaging_cost=data.packaging_cost,
            ad_cost=data.ad_cost,
            channel_fee_rate=data.channel_fee_rate,
            payment_fee_rate=data.payment_fee_rate,
            return_reserve_rate=data.return_reserve_rate,
            tax_basis_rate=data.tax_basis_rate,
            expected_revenue=result.expected_revenue,
            expected_total_cost=result.total_cost,
            expected_margin_amount=result.margin_amount,
            expected_margin_rate=result.margin_rate,
            expected_break_even_price=result.break_even_price,
        )

        rowcount = self.repository.update_pricing_conditional(
            pricing.id, company_id, expected_version, values,
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "다른 요청이 먼저 원가 정보를 갱신했습니다 — 다시 "
                "시도하세요.",
            )

        snapshot = self._build_expected_snapshot(
            company_id=company_id,
            listing_id=pricing.listing_id,
            sale_price=sale_price,
            cost_of_goods=data.cost_of_goods,
            shipping_cost=data.shipping_cost,
            packaging_cost=data.packaging_cost,
            ad_cost=data.ad_cost,
            channel_fee_rate=data.channel_fee_rate,
            payment_fee_rate=data.payment_fee_rate,
            return_reserve_rate=data.return_reserve_rate,
            tax_basis_rate=data.tax_basis_rate,
            result=result,
            reason=MarginSnapshotReason.ECONOMICS_UPDATE,
        )
        self.repository.add_margin_snapshot_no_commit(snapshot)

        self.db.commit()

        return self.repository.get_pricing_by_id(pricing.id, company_id)

    def get_pricing(
        self, pricing_id: int, company_id: int,
    ) -> ProductPricing:

        return self._get_pricing_required(pricing_id, company_id)

    def get_pricing_by_listing(
        self, listing_id: int, company_id: int,
    ) -> ProductPricing:

        return self._get_pricing_by_listing_required(listing_id, company_id)

    def list_pricings(
        self, company_id: int, skip: int = 0, limit: int = 100,
    ) -> list[ProductPricing]:

        return self.repository.list_pricings_for_company(
            company_id, skip, limit,
        )

    # --------------------------------------------------
    # 가격 변경 승인 흐름(요구사항 3)
    # --------------------------------------------------

    def request_price_change(
        self,
        company_id: int,
        listing_id: int,
        data: PriceChangeCreate,
        actor_user_id: int,
    ) -> PriceChangeRequest:

        # Audit(2026-08-21, AG-0) — 되돌림: 가격 변경 요청은 운영자가
        # 직접 수행하는 핵심 업무 CRUD다(AI가 숫자를 제안했든 안 했든
        # 이 엔드포인트 자체는 동일하게 동작해야 한다) — AI Capability
        # Registry로 게이트하지 않는다. PRICING_INVENTORY는 향후 실제
        # "가격 제안 계산" 기능(아직 미구현)에만 적용한다.

        pricing = self._get_pricing_by_listing_required(
            listing_id, company_id,
        )

        existing = self.repository.get_price_change_by_idempotency(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing

        if pricing.pending_price_change_id is not None:
            raise ConflictException(
                "이미 진행 중인 가격 변경 요청이 있습니다 — 먼저 "
                "처리하세요.",
            )

        fingerprint = self._pricing_fingerprint(pricing)

        request = PriceChangeRequest(
            company_id=company_id,
            listing_id=listing_id,
            product_pricing_id=pricing.id,
            previous_sale_price=pricing.current_sale_price,
            requested_sale_price=data.requested_sale_price,
            request_fingerprint=fingerprint,
            reason=data.reason,
            status=PriceChangeStatus.PENDING,
            requested_by=actor_user_id,
            requested_at=datetime.utcnow(),
            idempotency_key=data.idempotency_key,
        )

        try:
            request = self.repository.add_price_change_no_commit(request)
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_price_change_by_idempotency(
                company_id, data.idempotency_key,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "가격 변경 요청 생성 중 충돌이 발생했습니다.",
            )

        rowcount = self.repository.update_pricing_conditional(
            pricing.id, company_id, pricing.version,
            {"pending_price_change_id": request.id},
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "이미 진행 중인 가격 변경 요청이 있습니다 — 먼저 "
                "처리하세요.",
            )

        self.repository.add_status_event_no_commit(PriceChangeStatusEvent(
            company_id=company_id,
            price_change_request_id=request.id,
            listing_id=listing_id,
            previous_status=None,
            new_status=PriceChangeStatus.PENDING,
            sale_price_snapshot=pricing.current_sale_price,
            reason=data.reason,
            actor_user_id=actor_user_id,
        ))

        self.db.commit()
        self.db.refresh(request)

        dispatch_operational_event(
            self.db, "PRICE_CHANGE_APPROVAL_NEEDED",
            company_id=company_id, user_id=actor_user_id,
            idempotency_key=f"price-change-approval:{request.id}:PENDING",
            title="판매가 변경 승인이 필요합니다",
            message=f"가격 변경 요청 #{request.id}이 승인 대기 중입니다.",
            link_path="pricing", entity_ref=f"price_change_request:{request.id}",
            reason=data.reason or "판매가 변경 요청",
            entity_summary=f"상품 등록 #{listing_id}",
            amount_summary=(
                f"{request.previous_sale_price} → {request.requested_sale_price}"
            ),
        )

        return request

    def approve_price_change(
        self,
        request_id: int,
        company_id: int,
        actor_user_id: int,
        data: PriceChangeDecision | None = None,
    ) -> PriceChangeRequest:

        request = self._get_price_change_required(request_id, company_id)

        if request.status == PriceChangeStatus.APPROVED:
            return request

        if request.status != PriceChangeStatus.PENDING:
            raise BadRequestException(
                "PENDING 상태에서만 승인할 수 있습니다. "
                f"(현재: {request.status})",
            )

        pricing = self._get_pricing_required(
            request.product_pricing_id, company_id,
        )

        current_fingerprint = self._pricing_fingerprint(pricing)
        if current_fingerprint != request.request_fingerprint:
            raise ConflictException(
                "요청 이후 상품 가격/원가 정보가 변경되어 이 요청은 "
                "더 이상 유효하지 않습니다 — 다시 요청하세요.",
            )

        now = datetime.utcnow()
        decision_reason = data.decision_reason if data is not None else None

        rowcount = self.repository.decide_price_change_conditional(
            request.id, company_id, PriceChangeStatus.APPROVED,
            actor_user_id, now, decision_reason,
        )
        if rowcount != 1:
            self.db.rollback()
            refreshed = self.repository.get_price_change_by_id(
                request.id, company_id,
            )
            if (
                refreshed is not None
                and refreshed.status == PriceChangeStatus.APPROVED
            ):
                return refreshed
            raise ConflictException(
                "이 요청은 이미 다른 처리로 상태가 변경되었습니다.",
            )

        result = self._compute_expected_economics(
            listing_id=pricing.listing_id,
            sale_price=request.requested_sale_price,
            cost_of_goods=pricing.cost_of_goods,
            shipping_cost=pricing.shipping_cost,
            packaging_cost=pricing.packaging_cost,
            ad_cost=pricing.ad_cost,
            channel_fee_rate=pricing.channel_fee_rate,
            payment_fee_rate=pricing.payment_fee_rate,
            return_reserve_rate=pricing.return_reserve_rate,
            tax_basis_rate=pricing.tax_basis_rate,
        )

        pricing_rowcount = self.repository.update_pricing_conditional(
            pricing.id, company_id, pricing.version,
            {
                "current_sale_price": request.requested_sale_price,
                "pending_price_change_id": None,
                "expected_revenue": result.expected_revenue,
                "expected_total_cost": result.total_cost,
                "expected_margin_amount": result.margin_amount,
                "expected_margin_rate": result.margin_rate,
                "expected_break_even_price": result.break_even_price,
            },
        )
        if pricing_rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "가격 반영 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        snapshot = self._build_expected_snapshot(
            company_id=company_id,
            listing_id=pricing.listing_id,
            sale_price=request.requested_sale_price,
            cost_of_goods=pricing.cost_of_goods,
            shipping_cost=pricing.shipping_cost,
            packaging_cost=pricing.packaging_cost,
            ad_cost=pricing.ad_cost,
            channel_fee_rate=pricing.channel_fee_rate,
            payment_fee_rate=pricing.payment_fee_rate,
            return_reserve_rate=pricing.return_reserve_rate,
            tax_basis_rate=pricing.tax_basis_rate,
            result=result,
            reason=MarginSnapshotReason.PRICE_CHANGE_APPROVED,
        )
        self.repository.add_margin_snapshot_no_commit(snapshot)

        self.repository.add_status_event_no_commit(PriceChangeStatusEvent(
            company_id=company_id,
            price_change_request_id=request.id,
            listing_id=request.listing_id,
            previous_status=PriceChangeStatus.PENDING,
            new_status=PriceChangeStatus.APPROVED,
            sale_price_snapshot=request.requested_sale_price,
            reason=decision_reason,
            actor_user_id=actor_user_id,
        ))

        # 2026-08-15 V7 Gate 8 — 가격변경승인은 이 도메인 자체의
        # append-only 이력(PriceChangeStatusEvent, 바로 위)에 이미
        # 남지만, 회사를 넘나드는 SUPER_ADMIN 감사 화면
        # (`GET /admin/audit-logs`)에서는 전역 `audit_logs`에 없으면
        # 보이지 않는다 — 재고 수동조정(inventory)과 동일한 민감도로
        # 취급해 같은 트랜잭션에 함께 기록한다.
        write_audit_log(
            self.db,
            user_id=actor_user_id,
            action="PRICE_CHANGE_APPROVED",
            entity="price_change_request",
            entity_id=str(request.id),
            description=(
                f"가격변경 승인 (listing_id={request.listing_id}, "
                f"{request.previous_sale_price}원 → "
                f"{request.requested_sale_price}원)"
            ),
            company_id=company_id,
        )

        self.db.commit()

        # 2026-08-15 V7 Gate 8 — 회사에 알림(최선 노력, 커밋이 이미
        # 끝난 뒤에만 시도한다 — 알림 실패가 이미 성공한 가격변경
        # 승인 자체를 되돌리면 안 된다).
        try:
            from app.domains.notification_center.service import (
                NotificationService,
            )

            NotificationService(self.db).notify_company(
                company_id=company_id,
                category="pricing",
                level="info",
                title="가격 변경 승인",
                message=(
                    f"listing_id={request.listing_id} 판매가가 "
                    f"{request.requested_sale_price}원으로 승인·"
                    "반영되었습니다."
                ),
                link_path="pricing",
            )
        except Exception:  # noqa: BLE001 — 알림 실패는 최선 노력
            # NotificationService.notify_company()가 실패하면
            # self.db가 "이전 flush 실패로 rollback 대기" 상태로
            # 남는다 — 여기서 즉시 rollback해 세션을 계속 쓸 수 있는
            # 상태로 되돌린다(이미 성공한 가격변경 승인 자체는 위
            # commit()에서 이미 확정됐으므로 영향 없음).
            self.db.rollback()

        return self.repository.get_price_change_by_id(request.id, company_id)

    def reject_price_change(
        self,
        request_id: int,
        company_id: int,
        actor_user_id: int,
        data: PriceChangeDecision | None = None,
    ) -> PriceChangeRequest:

        request = self._get_price_change_required(request_id, company_id)

        if request.status == PriceChangeStatus.REJECTED:
            return request

        if request.status != PriceChangeStatus.PENDING:
            raise BadRequestException(
                "PENDING 상태에서만 거절할 수 있습니다. "
                f"(현재: {request.status})",
            )

        now = datetime.utcnow()
        decision_reason = data.decision_reason if data is not None else None

        rowcount = self.repository.decide_price_change_conditional(
            request.id, company_id, PriceChangeStatus.REJECTED,
            actor_user_id, now, decision_reason,
        )
        if rowcount != 1:
            self.db.rollback()
            refreshed = self.repository.get_price_change_by_id(
                request.id, company_id,
            )
            if (
                refreshed is not None
                and refreshed.status == PriceChangeStatus.REJECTED
            ):
                return refreshed
            raise ConflictException(
                "이 요청은 이미 다른 처리로 상태가 변경되었습니다.",
            )

        self._clear_pending_link_if_matches(request, company_id)

        self.repository.add_status_event_no_commit(PriceChangeStatusEvent(
            company_id=company_id,
            price_change_request_id=request.id,
            listing_id=request.listing_id,
            previous_status=PriceChangeStatus.PENDING,
            new_status=PriceChangeStatus.REJECTED,
            sale_price_snapshot=request.previous_sale_price,
            reason=decision_reason,
            actor_user_id=actor_user_id,
        ))

        self.db.commit()

        return self.repository.get_price_change_by_id(request.id, company_id)

    def cancel_price_change(
        self,
        request_id: int,
        company_id: int,
        actor_user_id: int,
    ) -> PriceChangeRequest:

        request = self._get_price_change_required(request_id, company_id)

        if request.status == PriceChangeStatus.CANCELLED:
            return request

        if request.status != PriceChangeStatus.PENDING:
            raise BadRequestException(
                "PENDING 상태에서만 취소할 수 있습니다. "
                f"(현재: {request.status})",
            )

        if request.requested_by != actor_user_id:
            raise ForbiddenException(
                "본인이 요청한 가격 변경만 취소할 수 있습니다.",
            )

        now = datetime.utcnow()

        rowcount = self.repository.decide_price_change_conditional(
            request.id, company_id, PriceChangeStatus.CANCELLED,
            actor_user_id, now, None,
        )
        if rowcount != 1:
            self.db.rollback()
            refreshed = self.repository.get_price_change_by_id(
                request.id, company_id,
            )
            if (
                refreshed is not None
                and refreshed.status == PriceChangeStatus.CANCELLED
            ):
                return refreshed
            raise ConflictException(
                "이 요청은 이미 다른 처리로 상태가 변경되었습니다.",
            )

        self._clear_pending_link_if_matches(request, company_id)

        self.repository.add_status_event_no_commit(PriceChangeStatusEvent(
            company_id=company_id,
            price_change_request_id=request.id,
            listing_id=request.listing_id,
            previous_status=PriceChangeStatus.PENDING,
            new_status=PriceChangeStatus.CANCELLED,
            sale_price_snapshot=request.previous_sale_price,
            reason=None,
            actor_user_id=actor_user_id,
        ))

        self.db.commit()

        return self.repository.get_price_change_by_id(request.id, company_id)

    def _clear_pending_link_if_matches(
        self, request: PriceChangeRequest, company_id: int,
    ) -> None:

        pricing = self.repository.get_pricing_by_id(
            request.product_pricing_id, company_id,
        )
        if pricing is not None and pricing.pending_price_change_id == (
            request.id
        ):
            self.repository.update_pricing_conditional(
                pricing.id, company_id, pricing.version,
                {"pending_price_change_id": None},
            )

    def list_price_changes(
        self, listing_id: int, company_id: int,
        skip: int = 0, limit: int = 100,
    ) -> list[PriceChangeRequest]:

        return self.repository.list_price_changes_for_listing(
            listing_id, company_id, skip, limit,
        )

    def list_status_events(
        self, request_id: int, company_id: int,
    ) -> list[PriceChangeStatusEvent]:

        self._get_price_change_required(request_id, company_id)

        return self.repository.list_status_events_for_request(
            request_id, company_id,
        )

    # --------------------------------------------------
    # 마진 스냅샷 조회 / 괴리(요구사항 2)
    # --------------------------------------------------

    def list_margin_snapshots(
        self, listing_id: int, company_id: int,
        margin_type: str | None = None,
        skip: int = 0, limit: int = 100,
    ) -> list[MarginSnapshot]:

        return self.repository.list_snapshots_for_listing(
            listing_id, company_id, margin_type, skip, limit,
        )

    def get_margin_variance(
        self, listing_id: int, company_id: int,
        order_id: int | None = None,
    ) -> dict:

        expected = self.repository.get_latest_snapshot(
            listing_id, company_id, MarginType.EXPECTED,
        )
        actual = self.repository.get_latest_snapshot(
            listing_id, company_id, MarginType.ACTUAL, order_id=order_id,
        )

        margin_amount_variance = None
        margin_rate_variance = None

        if expected is not None and actual is not None:
            margin_amount_variance = _money(
                Decimal(actual.margin_amount)
                - Decimal(expected.margin_amount),
            )
            margin_rate_variance = _rate(
                Decimal(actual.margin_rate)
                - Decimal(expected.margin_rate),
            )

        return {
            "listing_id": listing_id,
            "expected": expected,
            "latest_actual": actual,
            "margin_amount_variance": margin_amount_variance,
            "margin_rate_variance": margin_rate_variance,
        }

    # --------------------------------------------------
    # 실제 마진 반영 / 정산 대사(요구사항 2/4/5)
    # --------------------------------------------------

    def record_actual_margin(
        self,
        order_id: int,
        company_id: int,
        triggered_by: int | None = None,
    ) -> dict:
        """
        한 Order의 실제 손익을 계산해 ACTUAL MarginSnapshot으로
        남기고, Order × Settlement 대사(SettlementReconciliation)를
        갱신한다. 순수 읽기 전용으로만 Order/Purchase/Inventory/
        Settlement를 참조한다(그 도메인들의 Service를 호출하지
        않는다 — 이 Service가 그들의 상태를 바꾸지 않는다).
        """

        from app.domains.inventory.model import InventoryChannelMapping
        from app.domains.order.model import Order
        from app.domains.order.model import OrderItem
        from app.domains.purchase.model import PurchaseItem
        from app.domains.settlement.model import MarketplaceSettlement

        order = (
            self.db.query(Order)
            .filter(Order.id == order_id)
            .filter(Order.company_id == company_id)
            .first()
        )
        if order is None:
            raise NotFoundException("주문을 찾을 수 없습니다.")

        items = (
            self.db.query(OrderItem)
            .filter(OrderItem.order_id == order_id)
            .filter(OrderItem.company_id == company_id)
            .all()
        )

        settlement = (
            self.db.query(MarketplaceSettlement)
            .filter(MarketplaceSettlement.order_id == order_id)
            .filter(MarketplaceSettlement.company_id == company_id)
            .order_by(MarketplaceSettlement.id.desc())
            .first()
        )

        order_revenue_total = (
            Decimal(str(order.total_amount)) if order.total_amount
            else Decimal("0")
        )

        snapshots: list[MarginSnapshot] = []
        skipped: list[str] = []
        total_expected_net = Decimal("0")
        total_refund = Decimal("0")

        for item in items:

            mapping = (
                self.db.query(InventoryChannelMapping)
                .filter(InventoryChannelMapping.company_id == company_id)
                .filter(
                    InventoryChannelMapping.channel_code
                    == order.channel_code,
                )
                .filter(
                    InventoryChannelMapping.channel_sku == item.channel_sku,
                )
                .first()
            )
            if mapping is None:
                skipped.append(f"order_item:{item.id}:no_channel_mapping")
                continue

            pricing = self.repository.get_pricing_by_listing(
                mapping.marketplace_listing_id, company_id,
            )
            if pricing is None:
                skipped.append(
                    f"order_item:{item.id}:pricing_not_initialized",
                )
                continue

            quantity = item.quantity
            unit_price = Decimal(str(item.unit_price))
            revenue = _money(unit_price * quantity)
            estimated: list[str] = []

            purchase_items = (
                self.db.query(PurchaseItem)
                .filter(PurchaseItem.order_item_id == item.id)
                .filter(PurchaseItem.company_id == company_id)
                .all()
            )
            if purchase_items:
                cost_of_goods = _money(sum(
                    (Decimal(str(pi.subtotal_cost)) for pi in
                     purchase_items),
                    Decimal("0"),
                ))
            else:
                cost_of_goods = _money(
                    Decimal(pricing.cost_of_goods) * quantity,
                )
                estimated.append(EstimatedComponent.COST_OF_GOODS)

            if (
                settlement is not None
                and settlement.status == "DEPOSITED"
                and order_revenue_total > 0
            ):
                share = revenue / order_revenue_total
                channel_fee = _money(
                    Decimal(str(settlement.fee_amount)) * share,
                )
                payment_fee = Decimal("0.00")
                if len(items) > 1:
                    # 실제 정산 수수료는 있지만 품목별 안분은 매출
                    # 비례 추정이다(마켓이 품목별 수수료를 분리해
                    # 내려주지 않는다) — 주문에 품목이 1개뿐이면 안분이
                    # 곧 실측과 동일하므로 추정 표시하지 않는다.
                    estimated.append(EstimatedComponent.CHANNEL_FEE)
            else:
                channel_fee = _money(
                    revenue * Decimal(pricing.channel_fee_rate),
                )
                payment_fee = _money(
                    revenue * Decimal(pricing.payment_fee_rate),
                )
                estimated.append(EstimatedComponent.CHANNEL_FEE)
                estimated.append(EstimatedComponent.PAYMENT_FEE)

            shipping_cost = _money(Decimal(pricing.shipping_cost) * quantity)
            packaging_cost = _money(
                Decimal(pricing.packaging_cost) * quantity,
            )
            ad_cost = _money(Decimal(pricing.ad_cost) * quantity)
            return_reserve = _money(
                revenue * Decimal(pricing.return_reserve_rate),
            )
            tax = _money(revenue * Decimal(pricing.tax_basis_rate))
            estimated.extend([
                EstimatedComponent.SHIPPING_COST,
                EstimatedComponent.PACKAGING_COST,
                EstimatedComponent.AD_COST,
                EstimatedComponent.RETURN_RESERVE,
                EstimatedComponent.TAX,
            ])

            refund_adjustment = _money(
                unit_price * (item.returned_quantity or 0),
            )
            total_refund += refund_adjustment

            total_cost = (
                cost_of_goods + channel_fee + payment_fee + shipping_cost
                + packaging_cost + ad_cost + return_reserve + tax
            )
            margin_amount = revenue - total_cost - refund_adjustment
            margin_rate = (
                (margin_amount / revenue) if revenue != 0 else Decimal("0")
            )

            snapshot = MarginSnapshot(
                company_id=company_id,
                listing_id=pricing.listing_id,
                margin_type=MarginType.ACTUAL,
                order_id=order.id,
                settlement_id=(
                    settlement.id if settlement is not None else None
                ),
                reason=MarginSnapshotReason.SETTLEMENT_RECONCILED,
                quantity_basis=quantity,
                revenue=revenue,
                cost_of_goods=cost_of_goods,
                channel_fee=channel_fee,
                payment_fee=payment_fee,
                shipping_cost=shipping_cost,
                packaging_cost=packaging_cost,
                ad_cost=ad_cost,
                return_reserve=return_reserve,
                tax=tax,
                refund_adjustment=refund_adjustment,
                total_cost=_money(total_cost),
                margin_amount=_money(margin_amount),
                margin_rate=_rate(margin_rate),
                estimated_components_json=json.dumps(estimated),
            )
            snapshot = self.repository.add_margin_snapshot_no_commit(
                snapshot,
            )
            snapshots.append(snapshot)

            # 이 품목의 "기대 net"(Settlement.net_amount에 대응하는
            # 개념) — Settlement.net_amount의 Fixed Rule은
            # `net == gross - fee`뿐이다(app/domains/settlement/
            # service.py::validate_amounts). return_reserve/tax는
            # HOMEZ 내부 마진 계획용 가정일 뿐 마켓이 실제로 정산
            # 단계에서 떼는 항목이 아니므로, "채널이 실제로 얼마를
            # 입금해야 하는가"를 뜻하는 이 기대값에는 channel_fee/
            # payment_fee만 반영한다(원가·배송비 등도 당연히
            # 미반영 — Settlement는 채널→HOMEZ 입금액이지 마진이
            # 아니다). return_reserve/tax는 margin_amount 계산에는
            # 그대로 반영된다(위 total_cost).
            total_expected_net += _money(
                revenue - channel_fee - payment_fee,
            )

        # 2단계 commit 경계 1/2 — MarginSnapshot들을 먼저 확정 commit한다
        # (아래 SettlementReconciliation upsert가 IntegrityError로
        # rollback해야 할 수 있는데, 그 rollback이 방금 만든 아직
        # 커밋되지 않은 MarginSnapshot까지 함께 지우면 안 된다 — V7
        # Gate 4에서 실제로 겪은 결함과 같은 클래스, service.py 상단
        # docstring 참고).
        self.db.commit()

        reconciliation = None
        if items:
            reconciliation = self._upsert_reconciliation(
                company_id=company_id,
                order_id=order.id,
                settlement=settlement,
                expected_net_amount=_money(total_expected_net),
                refund_amount=_money(total_refund),
            )

        return {
            "order_id": order.id,
            "snapshots": snapshots,
            "reconciliation": reconciliation,
            "skipped_items": skipped,
        }

    def _upsert_reconciliation(
        self,
        *,
        company_id: int,
        order_id: int,
        settlement,
        expected_net_amount: Decimal,
        refund_amount: Decimal,
    ) -> SettlementReconciliation:

        actual_net_amount = None
        variance_amount = None
        status = ReconciliationStatus.PENDING_SETTLEMENT

        if settlement is not None and settlement.status == "DEPOSITED":
            actual_net_amount = _money(Decimal(str(settlement.net_amount)))
            variance_amount = _money(actual_net_amount - expected_net_amount)
            status = (
                ReconciliationStatus.MATCHED
                if variance_amount == Decimal("0.00")
                else ReconciliationStatus.MISMATCH
            )

        existing = self.repository.get_reconciliation_by_order(
            order_id, company_id,
        )

        # 관리자가 명시적으로 보류한 대사 결과는 자동 재계산이 덮어쓰지
        # 않는다 — 보류 해제는 release_reconciliation_hold()를 통해서만
        # (CLAUDE.md 최상위 규칙 — 금융 불일치 자동 보정 금지와 동일
        # 정신을 대사 계층에도 적용).
        if existing is not None and existing.status == (
            ReconciliationStatus.HELD
        ):
            return existing

        if existing is None:
            reconciliation = SettlementReconciliation(
                company_id=company_id,
                order_id=order_id,
                settlement_id=(
                    settlement.id if settlement is not None else None
                ),
                expected_net_amount=expected_net_amount,
                actual_net_amount=actual_net_amount,
                variance_amount=variance_amount,
                refund_amount=refund_amount,
                status=status,
                reconciled_at=(
                    datetime.utcnow() if actual_net_amount is not None
                    else None
                ),
            )
            try:
                reconciliation = self.repository.add_reconciliation_no_commit(  # noqa: E501
                    reconciliation,
                )
                self.db.commit()
                if status == ReconciliationStatus.MISMATCH:
                    dispatch_operational_event(
                        self.db, "RECONCILIATION_REVIEW_NEEDED",
                        company_id=company_id, user_id=None,
                        idempotency_key=f"reconciliation-review:{reconciliation.id}:MISMATCH",
                        title="정산 차이 확인이 필요합니다",
                        message=f"주문 #{order_id}의 예상 정산액과 실제 정산액이 다릅니다.",
                        link_path="pricing", entity_ref=f"reconciliation:{reconciliation.id}",
                        reason="정산 금액 불일치",
                        entity_summary=f"주문 #{order_id}",
                        amount_summary=f"차이 {variance_amount}",
                    )
                return reconciliation
            except IntegrityError:
                self.db.rollback()
                existing = self.repository.get_reconciliation_by_order(
                    order_id, company_id,
                )
                if existing is None:
                    raise
                if existing.status == ReconciliationStatus.HELD:
                    return existing

        self.repository.update_reconciliation_conditional(
            existing.id, company_id,
            {
                "settlement_id": (
                    settlement.id if settlement is not None else None
                ),
                "expected_net_amount": expected_net_amount,
                "actual_net_amount": actual_net_amount,
                "variance_amount": variance_amount,
                "refund_amount": refund_amount,
                "status": status,
                "reconciled_at": (
                    datetime.utcnow() if actual_net_amount is not None
                    else existing.reconciled_at
                ),
            },
        )
        self.db.commit()

        if status == ReconciliationStatus.MISMATCH:
            dispatch_operational_event(
                self.db, "RECONCILIATION_REVIEW_NEEDED",
                company_id=company_id, user_id=None,
                idempotency_key=f"reconciliation-review:{existing.id}:MISMATCH:{variance_amount}",
                title="정산 차이 확인이 필요합니다",
                message=f"주문 #{order_id}의 예상 정산액과 실제 정산액이 다릅니다.",
                link_path="pricing", entity_ref=f"reconciliation:{existing.id}",
                reason="정산 금액 불일치",
                entity_summary=f"주문 #{order_id}",
                amount_summary=f"차이 {variance_amount}",
            )

        return self.repository.get_reconciliation_by_order(
            order_id, company_id,
        )

    def get_reconciliation(
        self, order_id: int, company_id: int,
    ) -> SettlementReconciliation:

        reconciliation = self.repository.get_reconciliation_by_order(
            order_id, company_id,
        )
        if reconciliation is None:
            raise NotFoundException(
                "대사 기록을 찾을 수 없습니다 — 먼저 실제 마진을 "
                "계산하세요.",
            )

        return reconciliation

    def list_reconciliations(
        self, company_id: int, status: str | None = None,
        skip: int = 0, limit: int = 100,
    ) -> list[SettlementReconciliation]:

        return self.repository.list_reconciliations_for_company(
            company_id, status, skip, limit,
        )

    def hold_reconciliation(
        self, order_id: int, company_id: int, notes: str,
        actor_user_id: int | None = None,
    ) -> SettlementReconciliation:

        existing = self.repository.get_reconciliation_by_order(
            order_id, company_id,
        )
        if existing is None:
            raise NotFoundException(
                "대사 기록을 찾을 수 없습니다 — 먼저 실제 마진을 "
                "계산하세요.",
            )
        if existing.status == ReconciliationStatus.HELD:
            return existing

        rowcount = self.repository.update_reconciliation_conditional(
            existing.id, company_id,
            {"status": ReconciliationStatus.HELD, "notes": notes},
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "대사 상태가 동시에 변경되었습니다 — 다시 시도하세요.",
            )

        self.db.commit()

        return self.repository.get_reconciliation_by_order(
            order_id, company_id,
        )

    def release_reconciliation_hold(
        self, order_id: int, company_id: int,
        actor_user_id: int | None = None,
    ) -> SettlementReconciliation:

        existing = self.repository.get_reconciliation_by_order(
            order_id, company_id,
        )
        if existing is None:
            raise NotFoundException("대사 기록을 찾을 수 없습니다.")
        if existing.status != ReconciliationStatus.HELD:
            raise BadRequestException(
                "HELD 상태에서만 보류를 해제할 수 있습니다. "
                f"(현재: {existing.status})",
            )

        if existing.actual_net_amount is None:
            fallback_status = ReconciliationStatus.PENDING_SETTLEMENT
        elif (
            existing.variance_amount is not None
            and Decimal(existing.variance_amount) == Decimal("0.00")
        ):
            fallback_status = ReconciliationStatus.MATCHED
        else:
            fallback_status = ReconciliationStatus.MISMATCH

        rowcount = self.repository.update_reconciliation_conditional(
            existing.id, company_id, {"status": fallback_status},
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException("대사 상태가 동시에 변경되었습니다.")

        self.db.commit()

        return self.repository.get_reconciliation_by_order(
            order_id, company_id,
        )

    # --------------------------------------------------
    # 회사별 회계 CSV 내보내기(요구사항 6)
    # --------------------------------------------------

    def export_accounting_csv(
        self, company_id: int, *, include_economics: bool,
        locale: str = "ko-KR",
    ) -> str:

        from app.domains.pricing.csv_export import MAX_CSV_EXPORT_ROWS
        from app.domains.pricing.csv_export import build_accounting_csv
        from app.domains.pricing.csv_export import build_accounting_row

        pricings = self.repository.list_pricings_for_company(
            company_id, skip=0, limit=MAX_CSV_EXPORT_ROWS + 1,
        )
        if len(pricings) > MAX_CSV_EXPORT_ROWS:
            raise BadRequestException(
                f"내보낼 행이 너무 많습니다(최대 {MAX_CSV_EXPORT_ROWS}건)"
                " — 조건을 좁혀 다시 시도하세요.",
            )

        rows = []
        for pricing in pricings:
            pending_request = None
            if pricing.pending_price_change_id is not None:
                pending_request = self.repository.get_price_change_by_id(
                    pricing.pending_price_change_id, company_id,
                )

            latest_actual = self.repository.get_latest_snapshot(
                pricing.listing_id, company_id, MarginType.ACTUAL,
            )

            reconciliation = None
            recent_actual_order_id = (
                latest_actual.order_id if latest_actual is not None
                else None
            )
            if recent_actual_order_id is not None:
                reconciliation = self.repository.get_reconciliation_by_order(
                    recent_actual_order_id, company_id,
                )

            rows.append(build_accounting_row(
                pricing, pending_request, latest_actual, reconciliation,
            ))

        return build_accounting_csv(
            rows, include_economics=include_economics, locale=locale,
        )


__all__ = [
    "PricingService",
]
