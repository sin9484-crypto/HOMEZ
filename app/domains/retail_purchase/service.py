"""
=========================================================
Homez OS

File : app/domains/retail_purchase/service.py

Gate RP-1(2026-08-22) — 구매 실행 상태머신 오케스트레이션(지시문 F).

예산 예약은 app/domains/funding/service.py::FundingHold를 그대로
재사용하지 않는다 — FundingHold는 order_id(=쿠팡 등 원 주문) 1개당
정확히 1개만 존재하도록 설계돼 있어(app/domains/purchase/model.py
::Purchase 헤더 주석 참고), 한 원 주문에 여러 RetailPurchaseOrder
(품목별·구매처별)가 생길 수 있는 이 도메인과 keyspace가 맞지 않는다
(같은 order_id로 두 도메인이 각자 Hold를 만들려 하면 서로 다른
의미의 예약이 충돌한다). 대신 FundingAccount.total_funding/
held_amount(잔액 자체)와 FundingLedger(감사 이력)는 그대로 재사용
하고, "동시 구매에서 예산을 이중 예약하지 않는다"는 신규 요구사항을
만족시키기 위해 조건부 UPDATE(이 코드베이스에 이미 확립된 패턴 —
settlement/repository.py::confirm_deposit_conditional 등과 동일
원칙)로 held_amount 증가 자체를 원자화하는 새 메서드를 이 서비스에
둔다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.automation_safety.service import SafetyService
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.funding.service import FundingService
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)
from app.domains.retail_purchase.constants import ProviderErrorCode
from app.domains.retail_purchase.constants import RetailPurchaseOrderStatus
from app.domains.retail_purchase.constants import RetailPurchasePolicyDecision
from app.domains.retail_purchase.constants import RetailPurchasePolicyReason
from app.domains.retail_purchase.constants import (
    RetailPurchaseUncertainHandlingPolicy,
)
from app.domains.retail_purchase.model import RetailPurchaseOrder
from app.domains.retail_purchase.policy_service import (
    RetailPurchasePolicyCheckInput,
)
from app.domains.retail_purchase.policy_service import RetailPurchasePolicyService
from app.domains.retail_purchase.policy_service import (
    compute_margin_check_fingerprint,
)
from app.domains.retail_purchase.provider import CheckoutLineItem
from app.domains.retail_purchase.provider import LiveInputRequiredError
from app.domains.retail_purchase.provider import PlaceOrderRequest
from app.domains.retail_purchase.provider import RetailPurchaseProviderError
from app.domains.retail_purchase.provider import get_retail_purchase_provider
from app.domains.retail_purchase.repository import RetailPurchaseRepository


def _audit(
    db: Session, *, company_id: int, user_id: int | None,
    action: str, entity_id: str, description: str,
) -> None:

    write_audit_log(
        db, user_id=user_id, action=action, entity="retail_purchase_order",
        entity_id=entity_id, description=description,
        company_id=company_id,
    )


class RetailPurchaseService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = RetailPurchaseRepository(db)
        self.policy = RetailPurchasePolicyService(db)
        self.safety = SafetyService(db)
        self.funding = FundingService(db)

    # --------------------------------------------------
    # 예산 원자적 예약/해제/확정 — FundingAccount 재사용, FundingHold는
    # 재사용하지 않음(위 모듈 docstring 참고).
    # --------------------------------------------------

    def _reserve_budget_conditional(
        self, account_id: int, amount: float,
    ) -> bool:
        """단일 UPDATE 문 안에서 "지금 예약 가능한지"와 "예약 반영"을
        함께 수행한다 — 두 동시 요청이 동시에 통과해 예산을
        초과시키는 경쟁을 SQL 레벨에서 원천 차단한다."""

        result = self.db.execute(
            update(FundingAccount)
            .where(
                FundingAccount.id == account_id,
                (FundingAccount.total_funding - FundingAccount.held_amount)
                >= amount,
            )
            .values(held_amount=FundingAccount.held_amount + amount),
        )
        return result.rowcount == 1

    def _release_budget(self, account_id: int, amount: float) -> None:

        self.db.execute(
            update(FundingAccount)
            .where(FundingAccount.id == account_id)
            .values(
                held_amount=FundingAccount.held_amount - amount,
            ),
        )

    def _append_budget_ledger(
        self, account_id: int, company_id: int, amount: float,
        entry_type: str, retail_purchase_order_id: int, memo: str,
    ) -> None:

        self.db.add(FundingLedger(
            company_id=company_id, account_id=account_id, amount=amount,
            type=entry_type, reference_type="retail_purchase_order",
            reference_id=retail_purchase_order_id, memo=memo,
        ))

    # --------------------------------------------------
    # 구매요청 생성(PROPOSED) — 아직 정책 검사 전.
    # --------------------------------------------------

    def create_purchase_request(
        self, company_id: int, *, source_order_id: int, provider_code: str,
        product_url: str, external_product_id: str,
        selected_option: str | None, quantity: int, idempotency_key: str,
        match_confidence: float | None = None,
        match_evidence_json: str = "[]",
        expected_amount: float | None = None,
        correlation_id: str | None = None,
        created_by: int | None = None,
    ) -> RetailPurchaseOrder:

        existing = self.repository.get_by_idempotency(company_id, idempotency_key)
        if existing is not None:
            return existing

        order = RetailPurchaseOrder(
            company_id=company_id, source_order_id=source_order_id,
            provider_code=provider_code, product_url=product_url,
            external_product_id=external_product_id,
            selected_option=selected_option, quantity=quantity,
            match_confidence=match_confidence,
            match_evidence_json=match_evidence_json,
            expected_amount=expected_amount,
            status=RetailPurchaseOrderStatus.PROPOSED,
            idempotency_key=idempotency_key, correlation_id=correlation_id,
        )
        order = self.repository.add_order(order)

        _audit(
            self.db, company_id=company_id, user_id=created_by,
            action="RETAIL_PURCHASE_ORDER_CREATED", entity_id=str(order.id),
            description=(
                f"소매 구매요청 생성: provider={provider_code}, "
                f"source_order_id={source_order_id}"
            ),
        )

        self.db.commit()
        return order

    def _get_order_required(
        self, order_id: int, company_id: int,
    ) -> RetailPurchaseOrder:

        order = self.repository.get_order(order_id, company_id)
        if order is None:
            raise NotFoundException("구매 실행 기록을 찾을 수 없습니다.")
        return order

    # --------------------------------------------------
    # 정책 검사 → 예산 예약 → 견적 → 재검사 → place_order
    # --------------------------------------------------

    def run_policy_check(
        self, order_id: int, company_id: int,
        policy_input: RetailPurchasePolicyCheckInput,
        actor_user_id: int | None = None,
    ):
        """반환값은 (RetailPurchaseOrder, RetailPurchasePolicyCheckResult)
        튜플이다 — 호출자(Router)가 판정 사유·순이익 등을 다시 계산할
        필요 없이 그대로 응답에 담을 수 있게 한다(정책 평가를 두 번
        하지 않는다)."""

        order = self._get_order_required(order_id, company_id)

        # quantity는 호출자가 policy_input에 채운 값을 신뢰하지 않고
        # 항상 주문 자체의 값으로 강제한다 — 호출자가 실수로 다른
        # 수량을 넣어도(또는 기본값 1을 그대로 둬도) 상품별 최대
        # 구매수량 정책이 실제 주문 수량 기준으로 정확히 평가된다.
        import dataclasses as _dataclasses
        policy_input = _dataclasses.replace(policy_input, quantity=order.quantity)

        if order.status != RetailPurchaseOrderStatus.PROPOSED:
            raise BadRequestException(
                "PROPOSED 상태에서만 정책 검사를 수행할 수 있습니다. "
                f"(현재: {order.status})",
            )

        result = self.policy.evaluate(company_id, policy_input)

        if result.decision == RetailPurchasePolicyDecision.BLOCK:
            order.status = RetailPurchaseOrderStatus.BLOCKED
            order.failure_code = ",".join(result.reasons) or "POLICY_BLOCKED"
            order.retryable = False
            _audit(
                self.db, company_id=company_id, user_id=actor_user_id,
                action="RETAIL_PURCHASE_POLICY_BLOCKED", entity_id=str(order_id),
                description=f"정책 검사 차단: {order.failure_code}",
            )
            self.db.commit()

            # Section 3(2026-08-24) — 마진 미달 전용 알림. EVIDENCE_
            # REQUIRED(값을 몰라서 계산 자체를 못한 경우)는 여기 해당
            # 하지 않는다 — 실제로 계산된 net_profit/margin_rate가
            # 임계값에 미달했을 때만(하드코딩된 0 대체가 아니라 실제
            # 계산값 기준) 발송한다. dispatch는 커밋 이후에만 한다
            # (알림 실패가 이미 확정된 BLOCKED 상태를 되돌리지 않는다).
            margin_reasons = {
                RetailPurchasePolicyReason.MIN_PROFIT_NOT_MET,
                RetailPurchasePolicyReason.MIN_MARGIN_RATE_NOT_MET,
            }
            if margin_reasons & set(result.reasons):
                setting = self.policy.get_or_create_default_settings(company_id)
                fingerprint = compute_margin_check_fingerprint(
                    policy_input, setting,
                )
                dispatch_operational_event(
                    self.db, "MARGIN_BELOW_MINIMUM",
                    company_id=company_id, user_id=None,
                    idempotency_key=f"margin-below-minimum:{order.id}:{fingerprint}",
                    title="최소 예상이익 또는 최소 마진율에 미달했습니다",
                    message=(
                        f"소매 구매요청 #{order.id}의 예상 순이익 또는 "
                        "마진율이 회사 최소 기준에 미달합니다."
                    ),
                    link_path="retail-purchase", entity_ref=f"retail_purchase_order:{order.id}",
                    reason=",".join(sorted(margin_reasons & set(result.reasons))),
                    entity_summary=f"주문 #{order.id}",
                    amount_summary=(
                        f"예상순이익 {result.expected_net_profit}, "
                        f"예상마진율 {result.expected_margin_rate}"
                    ),
                )

            return order, result

        if result.expected_net_profit is not None:
            order.expected_net_profit = float(result.expected_net_profit)

        order.policy_fingerprint = self.policy.current_policy_fingerprint(
            company_id,
        )
        order.status = RetailPurchaseOrderStatus.POLICY_CHECKED
        _audit(
            self.db, company_id=company_id, user_id=actor_user_id,
            action="RETAIL_PURCHASE_POLICY_CHECKED", entity_id=str(order_id),
            description=f"정책 검사 통과: decision={result.decision}",
        )
        self.db.commit()
        return order, result

    def _ensure_policy_unchanged_since_check(
        self, order: RetailPurchaseOrder,
    ) -> None:
        """정책 검사(POLICY_CHECKED) 이후 관리자가 정책을 바꿨다면,
        예전 판정을 그대로 밀어붙이지 않고 다시 정책 검사를 받도록
        차단한다(2026-08-22 14차 지시 — "기존 승인의 전제가 바뀌면
        승인 fingerprint 무효화"). 정책 검사를 아직 한 번도 거치지
        않은 주문(policy_fingerprint가 None)은 이 검사 대상이 아니다
        — 각 단계 자체의 상태 전이 가드가 이미 그 경우를 차단한다."""

        if order.policy_fingerprint is None:
            return

        current = self.policy.current_policy_fingerprint(order.company_id)
        if current != order.policy_fingerprint:
            raise ConflictException(
                "POLICY_CHANGED_SINCE_CHECK: 정책 검사 이후 자동구매 "
                "정책이 변경되어 이전 판정을 더 진행할 수 없습니다 — "
                "정책 검사를 다시 수행하세요.",
            )

    def reserve_budget(
        self, order_id: int, company_id: int, required_amount: Decimal,
        actor_user_id: int | None = None,
    ) -> RetailPurchaseOrder:

        order = self._get_order_required(order_id, company_id)

        if order.status != RetailPurchaseOrderStatus.POLICY_CHECKED:
            raise BadRequestException(
                "POLICY_CHECKED 상태에서만 예산을 예약할 수 있습니다. "
                f"(현재: {order.status})",
            )

        self._ensure_policy_unchanged_since_check(order)

        account = self.funding.repository.get_account_by_company(company_id)
        if account is None:
            raise BadRequestException(
                "사업 운영자금 계정이 없어 예산을 예약할 수 없습니다.",
            )

        amount = float(required_amount)
        ok = self._reserve_budget_conditional(account.id, amount)

        if not ok:
            order.status = RetailPurchaseOrderStatus.BLOCKED
            order.failure_code = "BUDGET_INSUFFICIENT"
            _audit(
                self.db, company_id=company_id, user_id=actor_user_id,
                action="RETAIL_PURCHASE_BUDGET_RESERVE_FAILED",
                entity_id=str(order_id),
                description=f"예산 예약 실패(부족): amount={amount}",
            )
            self.db.commit()
            return order

        self._append_budget_ledger(
            account.id, company_id, amount, FundingService.TYPE_HOLD_CREATE,
            order.id, "소매 구매 예산 예약",
        )
        order.status = RetailPurchaseOrderStatus.BUDGET_RESERVED
        order.expected_amount = amount
        _audit(
            self.db, company_id=company_id, user_id=actor_user_id,
            action="RETAIL_PURCHASE_BUDGET_RESERVED", entity_id=str(order_id),
            description=f"예산 예약 완료: amount={amount}",
        )
        self.db.commit()
        return order

    def _release_reserved_budget(
        self, order: RetailPurchaseOrder, memo: str,
    ) -> None:
        """이미 BUDGET_HOLDING 계열 상태였던 주문의 예약을 해제한다
        — 실패·차단·불확실 판정 시 반드시 호출한다."""

        if order.status not in RetailPurchaseOrderStatus.BUDGET_HOLDING:
            return
        if order.expected_amount is None:
            return

        account = self.funding.repository.get_account_by_company(order.company_id)
        if account is None:
            return

        self._release_budget(account.id, float(order.expected_amount))
        self._append_budget_ledger(
            account.id, order.company_id, float(order.expected_amount),
            FundingService.TYPE_HOLD_RELEASE, order.id, memo,
        )

    def request_quote(
        self, order_id: int, company_id: int,
        actor_user_id: int | None = None,
    ):
        """반환값은 (RetailPurchaseOrder, PurchaseQuote) 튜플이다 —
        place_order()가 quote_id를 다시 받아야 하므로 quote 객체
        자체(만료 시각 포함)를 그대로 호출자에게 전달한다."""

        order = self._get_order_required(order_id, company_id)

        if order.status != RetailPurchaseOrderStatus.BUDGET_RESERVED:
            raise BadRequestException(
                "BUDGET_RESERVED 상태에서만 견적을 요청할 수 있습니다. "
                f"(현재: {order.status})",
            )

        self._ensure_policy_unchanged_since_check(order)

        provider = get_retail_purchase_provider(order.provider_code)
        items = (CheckoutLineItem(
            external_product_id=order.external_product_id,
            option_id=order.selected_option, quantity=order.quantity,
        ),)

        try:
            quote = provider.create_purchase_quote(items)
        except RetailPurchaseProviderError as e:
            order.status = RetailPurchaseOrderStatus.FAILED
            order.failure_code = e.error_code
            order.retryable = e.retryable
            _audit(
                self.db, company_id=company_id, user_id=actor_user_id,
                action="RETAIL_PURCHASE_QUOTE_FAILED", entity_id=str(order_id),
                description=f"견적 요청 실패: {e.error_code}",
            )
            self.db.commit()
            raise BadRequestException(str(e)) from e

        order.actual_amount = float(quote.total_amount)
        order.status = RetailPurchaseOrderStatus.QUOTED
        self.db.commit()
        return order, quote

    def place_order(
        self, order_id: int, company_id: int, *, quote_id: str,
        shipping_address_reference: str, created_by: int | None = None,
    ) -> RetailPurchaseOrder:
        """
        실행 순서(지시문 9 그대로): 재조회 → 중복 확인(idempotency) →
        가격 재검사 → place_order → 성공 시 예산 확정 / 실패 시 예약
        해제 / 결과 불명확 시 절대 재시도하지 않음.
        """

        order = self._get_order_required(order_id, company_id)

        if self.safety.is_emergency_stop_active():
            raise BadRequestException(
                "Emergency Stop이 활성화되어 있어 구매 실행을 진행할 "
                "수 없습니다.",
            )

        if order.status in RetailPurchaseOrderStatus.NO_AUTO_RETRY:
            raise ConflictException(
                "이 구매 실행은 실패/차단/결과 불명확 상태입니다 — "
                "자동으로 다시 결제를 시도하지 않습니다. 쇼핑몰 "
                "주문내역에서 실제 존재 여부를 먼저 확인하세요.",
            )

        if order.status != RetailPurchaseOrderStatus.QUOTED:
            raise BadRequestException(
                "QUOTED 상태에서만 구매를 실행할 수 있습니다. "
                f"(현재: {order.status})",
            )

        self._ensure_policy_unchanged_since_check(order)

        provider = get_retail_purchase_provider(order.provider_code)

        order.status = RetailPurchaseOrderStatus.PLACING
        self.db.commit()

        request = PlaceOrderRequest(
            idempotency_key=order.idempotency_key, quote_id=quote_id,
            shipping_address_reference=shipping_address_reference,
            max_total_amount=Decimal(str(order.actual_amount or 0)),
            correlation_id=order.correlation_id,
        )

        try:
            result = provider.place_order(request)
        except LiveInputRequiredError:
            # 실 계약 전 Provider — 상태를 되돌리고 예산은 유지한다
            # (다시 시도할 여지를 남긴다, 실패가 아니라 미연결이므로).
            order.status = RetailPurchaseOrderStatus.QUOTED
            self.db.commit()
            raise
        except RetailPurchaseProviderError as e:
            self._release_reserved_budget(order, "Provider 오류로 예산 해제")
            order.status = RetailPurchaseOrderStatus.FAILED
            order.failure_code = e.error_code
            order.retryable = e.retryable
            _audit(
                self.db, company_id=company_id, user_id=created_by,
                action="RETAIL_PURCHASE_ORDER_PLACE_FAILED",
                entity_id=str(order_id),
                description=f"구매 실행 실패(Provider 오류): {e.error_code}",
            )
            self.db.commit()
            raise BadRequestException(str(e)) from e

        if result.status == "ORDERED":
            order.status = RetailPurchaseOrderStatus.ORDERED
            order.external_order_id = result.external_order_id
            order.external_order_number = result.external_order_number
            if result.actual_amount is not None:
                order.actual_amount = float(result.actual_amount)
            self._confirm_budget(order)
            _audit(
                self.db, company_id=company_id, user_id=created_by,
                action="RETAIL_PURCHASE_ORDER_PLACED", entity_id=str(order_id),
                description=(
                    f"구매 실행 성공: external_order_id="
                    f"{result.external_order_id}"
                ),
            )
        elif result.status == "UNCERTAIN":
            order.status = RetailPurchaseOrderStatus.UNCERTAIN
            order.failure_code = result.error_code
            order.retryable = False
            # 결과가 불명확하면 예산은 해제하지 않는다 — 실제로
            # 결제됐을 가능성이 있으므로, 사람이 쇼핑몰 주문내역을
            # 확인하기 전까지는 예약을 그대로 유지한다(중복 결제
            # 방지가 예산 회수보다 우선).
            _audit(
                self.db, company_id=company_id, user_id=created_by,
                action="RETAIL_PURCHASE_ORDER_UNCERTAIN", entity_id=str(order_id),
                description=f"구매 실행 결과 불명확: {result.error_code}",
            )
        else:
            # 상태를 FAILED로 바꾸기 "전에" 해제해야 한다 —
            # _release_reserved_budget()은 order.status가 아직
            # BUDGET_HOLDING 계열일 때만 해제를 수행한다(중복 해제
            # 방지 가드). 순서를 바꾸면 가드 자체가 정상적인 최초
            # 해제까지 막아버린다.
            self._release_reserved_budget(order, "구매 실패로 예산 해제")
            order.status = RetailPurchaseOrderStatus.FAILED
            order.failure_code = result.error_code
            order.retryable = result.retryable
            _audit(
                self.db, company_id=company_id, user_id=created_by,
                action="RETAIL_PURCHASE_ORDER_PLACE_FAILED",
                entity_id=str(order_id),
                description=f"구매 실행 실패: {result.error_code}",
            )

        self.db.commit()
        return order

    def _confirm_budget(self, order: RetailPurchaseOrder) -> None:

        account = self.funding.repository.get_account_by_company(order.company_id)
        if account is None or order.expected_amount is None:
            return

        # 예약 금액과 실 결제금액 차이가 있으면 차액만큼 조정한다
        # (실제 결제금액이 더 낮으면 초과 예약분을 해제, 더 높으면
        # place_order 진입 전 max_total_amount 재검증을 이미 통과한
        # 값이므로 추가 예약).
        reserved = float(order.expected_amount)
        actual = float(order.actual_amount or reserved)
        diff = actual - reserved

        if diff != 0:
            self.db.execute(
                update(FundingAccount)
                .where(FundingAccount.id == account.id)
                .values(held_amount=FundingAccount.held_amount + diff),
            )

        self._append_budget_ledger(
            account.id, order.company_id, actual,
            FundingService.TYPE_HOLD_COMMIT,
            order.id, "소매 구매 성공 — 예산 사용 확정",
        )

        # 사용 확정 = total_funding에서 실제 차감(향후 정산 시
        # FundingAccount.total_funding 자체를 줄이는 것은 이
        # 라운드 범위 밖 — 기존 FundingHold COMMIT 처리와 동일하게
        # held_amount만 유지하고 실제 지출 반영은 별도 정산 절차로
        # 남긴다는 점을 정직하게 기록한다).

    def cancel_order(
        self, order_id: int, company_id: int, reason: str,
        actor_user_id: int | None = None,
    ) -> RetailPurchaseOrder:

        order = self._get_order_required(order_id, company_id)

        if order.status not in (
            RetailPurchaseOrderStatus.ORDERED,
            RetailPurchaseOrderStatus.TRACKING_PENDING,
        ):
            raise BadRequestException(
                "ORDERED/TRACKING_PENDING 상태에서만 취소할 수 "
                f"있습니다. (현재: {order.status})",
            )

        provider = get_retail_purchase_provider(order.provider_code)

        try:
            provider.cancel_order(order.external_order_id)
        except RetailPurchaseProviderError as e:
            raise BadRequestException(str(e)) from e

        order.status = RetailPurchaseOrderStatus.CANCEL_PENDING
        _audit(
            self.db, company_id=company_id, user_id=actor_user_id,
            action="RETAIL_PURCHASE_ORDER_CANCEL_REQUESTED",
            entity_id=str(order_id), description=f"구매 취소 요청: {reason}",
        )
        self.db.commit()
        return order

    def refresh_tracking(
        self, order_id: int, company_id: int,
    ) -> RetailPurchaseOrder:

        order = self._get_order_required(order_id, company_id)

        if order.external_order_id is None:
            raise BadRequestException("아직 실제 주문번호가 없습니다.")

        provider = get_retail_purchase_provider(order.provider_code)

        try:
            tracking = provider.get_tracking(order.external_order_id)
        except RetailPurchaseProviderError as e:
            raise BadRequestException(str(e)) from e

        order.tracking_company = tracking.tracking_company
        order.tracking_number = tracking.tracking_number
        if order.status == RetailPurchaseOrderStatus.ORDERED:
            order.status = RetailPurchaseOrderStatus.TRACKING_PENDING
        self.db.commit()
        return order

    def _maybe_auto_release_uncertain_budget(
        self, order: RetailPurchaseOrder, *, now: datetime | None = None,
    ) -> None:
        """UNCERTAIN 상태에서 정책이 AUTO_RELEASE_AFTER_TIMEOUT이고
        지정 시간이 지났으면 예산만 반환한다 — 구매 자체(Provider
        재호출)는 어떤 경우에도 시도하지 않는다(2026-08-22 14차 지시,
        절대 금지 사항 "UNCERTAIN 자동 재시도 금지"는 정책으로 끌 수
        없다). 조회 시점 지연 평가(automation_safety.EligibilityService
        와 동일 철학) — 별도 백그라운드 스케줄러를 두지 않는다."""

        if order.status != RetailPurchaseOrderStatus.UNCERTAIN:
            return
        if order.uncertain_budget_released:
            return

        setting = self.policy.get_or_create_default_settings(order.company_id)
        if (
            setting.uncertain_handling_policy
            != RetailPurchaseUncertainHandlingPolicy.AUTO_RELEASE_AFTER_TIMEOUT
        ):
            return
        if setting.uncertain_auto_release_after_hours is None:
            return

        now = now or datetime.utcnow()
        deadline = order.updated_at + timedelta(
            hours=setting.uncertain_auto_release_after_hours,
        )
        if now < deadline:
            return

        self._release_reserved_budget(
            order, "UNCERTAIN 결과 자동 예산 반환 정책(구매 재시도 아님)",
        )
        order.uncertain_budget_released = True
        _audit(
            self.db, company_id=order.company_id, user_id=None,
            action="RETAIL_PURCHASE_UNCERTAIN_BUDGET_AUTO_RELEASED",
            entity_id=str(order.id),
            description="정책 시간 초과로 UNCERTAIN 주문 예산 자동 반환",
        )
        self.db.commit()

    def get_order(
        self, order_id: int, company_id: int,
    ) -> RetailPurchaseOrder:

        order = self._get_order_required(order_id, company_id)
        self._maybe_auto_release_uncertain_budget(order)
        return order

    def list_orders(
        self, company_id: int, *, status: str | None = None,
        skip: int = 0, limit: int = 100,
    ) -> list[RetailPurchaseOrder]:

        orders = self.repository.list_orders(
            company_id, status=status, skip=skip, limit=limit,
        )
        for order in orders:
            self._maybe_auto_release_uncertain_budget(order)
        return orders


__all__ = ["RetailPurchaseService"]
