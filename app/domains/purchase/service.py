"""
=========================================================
Homez OS

File : app/domains/purchase/service.py

Purchase Service — V7 Gate 4(2026-08-15) 처음부터 재설계.

무재고 중개·주문대행 발주/입고 흐름(요구사항 4):
  create_purchase() — 주문 품목에 대해 공급처 발주를 생성하고 총
    비용만큼 Funding Hold를 건다(FundingService.ensure_supply_hold(
    company_id=...) 필수 사용 — Gate 2가 선택적으로 열어둔 매개변수를
    이번에 실제로 채운다).
  confirm_purchase() — REQUESTED → CONFIRMED(공급처 접수 확인).
  receive_purchase() — CONFIRMED → RECEIVED. PurchaseItem 단위로
    Inventory.restock() 호출 + FundingService.confirm_supplier_
    payment(company_id=...) 호출(요구사항 4) + OUT_OF_STOCK이었던
    연결 주문 품목을 자동 재예약(OrderService.retry_reservation(),
    요구사항 3/4 연결 고리를 닫는다).
  cancel_purchase() — REQUESTED/CONFIRMED → CANCELLED. Hold는
    order_id 단위라 여기서 개별 해제하지 않는다(Order.cancel_order()가
    Hold 해제를 전담 — 한 Hold를 여러 Purchase가 나눠 쓸 수 있어
    Purchase 단위 해제는 안전하지 않다).
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.order.constants import OrderItemStatus
from app.domains.purchase.constants import PurchaseStatus
from app.domains.purchase.constants import PurchaseSubmissionStatus
from app.domains.purchase.fingerprint import canonical_json
from app.domains.purchase.fingerprint import sha256_hex
from app.domains.purchase.model import Purchase
from app.domains.purchase.model import PurchaseItem
from app.domains.purchase.repository import PurchaseRepository
from app.domains.purchase.schema import PurchaseCreate
from app.domains.purchase.supplier_order_providers import (
    SupplierOrderProviderError,
)
from app.domains.purchase.supplier_order_providers import SupplierOrderRequest
from app.domains.purchase.supplier_order_providers import (
    get_supplier_order_provider,
)


class PurchaseService:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = PurchaseRepository(db)

    # --------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------

    def _get_purchase_required(
        self,
        purchase_id: int,
        company_id: int,
    ) -> Purchase:

        purchase = self.repository.get_purchase_for_company(
            purchase_id, company_id,
        )

        if purchase is None:
            raise NotFoundException("발주(Purchase)를 찾을 수 없습니다.")

        return purchase

    # --------------------------------------------------
    # 발주 생성(요구사항 4)
    # --------------------------------------------------

    def create_purchase(
        self,
        company_id: int,
        data: PurchaseCreate,
        triggered_by: int | None = None,
    ) -> Purchase:

        existing = self.repository.get_purchase_by_idempotency(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing

        from app.domains.order.repository import OrderRepository

        order_repository = OrderRepository(self.db)

        order = order_repository.get_order_for_company(
            data.order_id, company_id,
        )
        if order is None:
            raise NotFoundException("주문을 찾을 수 없습니다.")

        resolved_items: list[tuple] = []
        total_cost = 0.0

        for item_data in data.items:
            order_item = order_repository.get_item_for_company(
                item_data.order_item_id, company_id,
            )
            if order_item is None or order_item.order_id != order.id:
                raise NotFoundException("주문 품목을 찾을 수 없습니다.")

            if order_item.status == OrderItemStatus.CANCELLED:
                raise BadRequestException(
                    "취소된 주문 품목에는 발주를 생성할 수 없습니다.",
                )

            if order_item.purchase_id is not None:
                raise BadRequestException(
                    f"주문 품목 {order_item.id}에는 이미 발주가 "
                    "생성되어 있습니다.",
                )

            subtotal = float(item_data.unit_cost) * order_item.quantity
            total_cost += subtotal
            resolved_items.append((order_item, item_data.unit_cost, subtotal))

        purchase = Purchase(
            company_id=company_id,
            order_id=order.id,
            supplier_id=data.supplier_id,
            status=PurchaseStatus.REQUESTED,
            idempotency_key=data.idempotency_key,
            total_cost=total_cost,
            supplier_order_number=data.supplier_order_number,
            memo=data.memo,
        )

        try:
            purchase = self.repository.add_purchase_no_commit(purchase)
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_purchase_by_idempotency(
                company_id, data.idempotency_key,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "발주 생성 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        for order_item, unit_cost, subtotal in resolved_items:
            self.repository.add_purchase_item_no_commit(
                PurchaseItem(
                    company_id=company_id,
                    purchase_id=purchase.id,
                    order_item_id=order_item.id,
                    inventory_sku_id=order_item.inventory_sku_id,
                    quantity=order_item.quantity,
                    unit_cost=unit_cost,
                    subtotal_cost=subtotal,
                ),
            )
            order_item.purchase_id = purchase.id
            order_repository.save_item_no_commit(order_item)

        if total_cost > 0:
            from app.domains.funding.service import FundingService

            try:
                FundingService(self.db).ensure_supply_hold(
                    order_id=order.id,
                    amount=total_cost,
                    purchase_id=purchase.id,
                    company_id=company_id,
                )
            except Exception:
                self.db.rollback()
                raise

        self.db.commit()
        self.db.refresh(purchase)

        return purchase

    # --------------------------------------------------
    # 상태 전이(요구사항 4)
    # --------------------------------------------------

    def confirm_purchase(
        self,
        purchase_id: int,
        company_id: int,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> Purchase:

        now = now or datetime.utcnow()

        purchase = self._get_purchase_required(purchase_id, company_id)

        rowcount = self.repository.transition_purchase_status_conditional(
            purchase_id, company_id,
            PurchaseStatus.REQUESTED, PurchaseStatus.CONFIRMED,
            extra_values={"confirmed_at": now},
        )

        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "REQUESTED 상태가 아닌 발주는 확정할 수 없습니다 — "
                f"현재 상태: {purchase.status}",
            )

        self.db.commit()
        self.db.refresh(purchase)

        return purchase

    def receive_purchase(
        self,
        purchase_id: int,
        company_id: int,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> Purchase:
        """
        입고 확정(요구사항 4) — PurchaseItem 단위로 Inventory.restock()
        을 호출하고, 발주 총액만큼 Funding.confirm_supplier_payment(
        company_id=...)를 호출한다. 이어서 재고 부족(OUT_OF_STOCK)
        이었던 연결 주문 품목을 자동 재예약해 요구사항 3(주문→재고
        예약) ↔ 요구사항 4(발주→입고) 연결 고리를 닫는다.
        """

        now = now or datetime.utcnow()

        purchase = self._get_purchase_required(purchase_id, company_id)

        rowcount = self.repository.transition_purchase_status_conditional(
            purchase_id, company_id,
            PurchaseStatus.CONFIRMED, PurchaseStatus.RECEIVED,
            extra_values={"received_at": now},
        )

        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "CONFIRMED 상태가 아닌 발주는 입고 확정할 수 없습니다 "
                f"— 먼저 발주를 확정(confirm)하세요. 현재 상태: "
                f"{purchase.status}",
            )

        from app.domains.inventory.schema import InventoryRestockRequest
        from app.domains.inventory.service import InventoryService
        from app.domains.order.repository import OrderRepository
        from app.domains.order.service import OrderService

        inventory_service = InventoryService(self.db)
        order_repository = OrderRepository(self.db)
        order_service = OrderService(self.db)

        items = self.repository.list_items_for_purchase(
            purchase_id, company_id,
        )

        for item in items:
            inventory_service.restock(
                item.inventory_sku_id,
                company_id,
                InventoryRestockRequest(
                    quantity=item.quantity,
                    idempotency_key=(
                        f"purchase_item:{item.id}:restock"
                    ),
                    reason=f"공급처 입고(발주 #{purchase_id})",
                ),
                triggered_by,
            )

            order_item = order_repository.get_item_for_company(
                item.order_item_id, company_id,
            )
            if (
                order_item is not None
                and order_item.status == OrderItemStatus.OUT_OF_STOCK
            ):
                order_service.retry_reservation(
                    order_item.id, company_id, triggered_by,
                )

        from app.domains.funding.schema import SupplierPaymentConfirm
        from app.domains.funding.service import FundingService

        FundingService(self.db).confirm_supplier_payment(
            purchase.id,
            SupplierPaymentConfirm(
                memo=f"발주 #{purchase_id} 입고 확정 지급",
            ),
            company_id=company_id,
        )

        self.db.commit()
        self.db.refresh(purchase)

        return purchase

    def cancel_purchase(
        self,
        purchase_id: int,
        company_id: int,
        reason: str,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> Purchase:

        now = now or datetime.utcnow()

        purchase = self._get_purchase_required(purchase_id, company_id)

        if purchase.status not in (
            PurchaseStatus.REQUESTED, PurchaseStatus.CONFIRMED,
        ):
            raise BadRequestException(
                "REQUESTED/CONFIRMED 상태가 아닌 발주는 취소할 수 "
                f"없습니다 — 현재 상태: {purchase.status}",
            )

        rowcount = self.repository.transition_purchase_status_conditional(
            purchase_id, company_id,
            purchase.status, PurchaseStatus.CANCELLED,
            extra_values={"cancelled_at": now, "memo": reason},
        )

        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "발주 취소 처리 중 상태가 이미 변경되었습니다 — 다시 "
                "조회 후 시도하세요.",
            )

        from app.domains.order.repository import OrderRepository

        order_repository = OrderRepository(self.db)

        for item in self.repository.list_items_for_purchase(
            purchase_id, company_id,
        ):
            order_item = order_repository.get_item_for_company(
                item.order_item_id, company_id,
            )
            if order_item is not None and order_item.purchase_id == purchase_id:
                order_item.purchase_id = None
                order_repository.save_item_no_commit(order_item)

        self.db.commit()
        self.db.refresh(purchase)

        return purchase

    # --------------------------------------------------
    # 공급처 발주 전송(SupplierOrderProvider 연결, 2026-08-20 2차 지시)
    # --------------------------------------------------
    #
    # "실제 발주는 승인 필요"는 Provider 호출 자체를 코드에서 빼라는
    # 뜻이 아니라, 승인(status=CONFIRMED, 기존 confirm_purchase())과
    # 실제 전송(이 메서드)을 분리해 전송 직전에 명시적 재확인 게이트를
    # 두라는 뜻이다(요구사항). 외부 호출 조건을 전부 통과해야만
    # Provider.create_order()를 호출한다 — Fake/Manual/Csv 어떤
    # Provider든 이 메서드를 통해서만 호출된다(자동 트리거 지점 없음).

    def _is_estop_active(self) -> bool:

        from app.domains.automation_safety.model import EmergencyStop

        latest = (
            self.db.query(EmergencyStop)
            .order_by(EmergencyStop.id.desc())
            .first()
        )

        return latest is not None and latest.is_active

    def _compute_approval_fingerprint(
        self, purchase: Purchase, items: list[PurchaseItem],
    ) -> str:

        payload = {
            "purchase_id": purchase.id,
            "company_id": purchase.company_id,
            "supplier_id": purchase.supplier_id,
            "total_cost": purchase.total_cost,
            "items": sorted(
                [
                    {
                        "order_item_id": item.order_item_id,
                        "inventory_sku_id": item.inventory_sku_id,
                        "quantity": item.quantity,
                        "unit_cost": item.unit_cost,
                    }
                    for item in items
                ],
                key=lambda x: x["order_item_id"],
            ),
        }

        return sha256_hex(canonical_json(payload))

    def _check_submittable_and_build_order_lines(
        self, purchase: Purchase, company_id: int,
    ) -> tuple[list[PurchaseItem], list[tuple[str, int, float]]]:
        """submit_to_supplier()/retry_submission_to_supplier() 공용 —
        상태·EStop·공급처 관계 승인·가격 일치를 재확인하고 전송용
        품목 라인을 만든다. 재시도(retry)에서도 이 검사를 전부
        그대로 다시 통과해야 한다 — 실패 이후 EStop이 켜졌거나 가격이
        바뀌었을 수 있으므로."""

        if purchase.status != PurchaseStatus.CONFIRMED:
            raise BadRequestException(
                "CONFIRMED(사용자 승인 완료) 상태의 발주만 전송할 수 "
                f"있습니다 — 현재 상태: {purchase.status}",
            )

        if self._is_estop_active():
            raise BadRequestException(
                "ESTOP_BLOCKED: 비상 정지가 활성화되어 있어 공급처 "
                "발주를 전송할 수 없습니다.",
            )

        from app.domains.source.model import CompanySupplierRelation

        relation = (
            self.db.query(CompanySupplierRelation)
            .filter(CompanySupplierRelation.company_id == company_id)
            .filter(CompanySupplierRelation.supplier_id == purchase.supplier_id)
            .first()
        )
        if relation is None or relation.approval_status != "APPROVED":
            raise BadRequestException(
                "SUPPLIER_RELATION_NOT_APPROVED: 이 공급처와의 거래관계가 "
                "승인(APPROVED)되지 않았습니다 — 먼저 공급처 거래정보를 "
                "승인하세요.",
            )

        items = self.repository.list_items_for_purchase(purchase.id, company_id)
        if not items:
            raise BadRequestException("발주 품목이 없습니다.")

        from app.domains.inventory.repository import InventoryRepository
        from app.domains.source.repository import SourceRepository

        inventory_repository = InventoryRepository(self.db)
        source_repository = SourceRepository(self.db)

        order_lines: list[tuple[str, int, float]] = []
        for item in items:
            sku = inventory_repository.get_sku_for_company(
                item.inventory_sku_id, company_id,
            )
            if sku is None:
                raise BadRequestException(
                    f"품목 {item.id}의 재고 SKU를 찾을 수 없습니다.",
                )

            active_links = source_repository.list_links_for_candidate(
                company_id, sku.product_candidate_id,
            )
            matching = [
                link for link in active_links
                if link.supplier_id == purchase.supplier_id
                and link.unit_cost == item.unit_cost
            ]
            if not matching:
                raise BadRequestException(
                    "PRICE_CHANGED: 발주 품목의 단가가 현재 활성 "
                    "SupplierProductLink와 일치하지 않습니다(가격 변경 "
                    "또는 연결 비활성화) — 발주안을 다시 생성하세요.",
                )

            order_lines.append((matching[0].supplier_sku, item.quantity, item.unit_cost))

        return items, order_lines

    def _run_submission_attempt(
        self, purchase_id: int, company_id: int, provider_code: str,
        triggered_by: int, supplier_id: int,
        order_lines: list[tuple[str, int, float]],
        fingerprint: str, audit_action_prefix: str,
        test_scenario: str | None = None,
    ) -> Purchase:
        """claim(또는 reclaim) 성공 이후 공통 경로 — Provider 호출 +
        결과 저장 + 감사로그. write_audit_log()는 자체적으로 commit()
        하지 않으므로(app/core/audit_db.py), 반드시 commit() 이전에
        호출해 상태 변경과 감사로그를 한 트랜잭션으로 묶는다(2026-08-20
        4차 라운드에서 media_asset 도메인에 실제로 있던 것과 동일한
        결함 패턴 — 이 함수를 새로 만드는 김에 처음부터 올바른 순서로
        작성한다)."""

        try:
            provider = get_supplier_order_provider(provider_code)
        except SupplierOrderProviderError as exc:
            raise BadRequestException(str(exc)) from exc

        correlation_id = f"purchase:{purchase_id}:submit:{fingerprint[:16]}"

        request = SupplierOrderRequest(
            purchase_id=purchase_id, supplier_id=supplier_id,
            idempotency_key=f"purchase-{purchase_id}",
            items=tuple(order_lines),
            test_scenario=test_scenario,
        )

        try:
            result = provider.create_order(request)
        except Exception as exc:  # noqa: BLE001
            self.repository.save_submission_result_no_commit(
                purchase_id, company_id,
                {
                    "submission_status": PurchaseSubmissionStatus.FAILED,
                    "submission_provider_code": provider_code,
                    "submission_error_code": type(exc).__name__,
                    "submission_retryable": True,
                    "correlation_id": correlation_id,
                    "submitted_at": datetime.utcnow(),
                },
            )
            write_audit_log(
                self.db, company_id=company_id, user_id=triggered_by,
                action=f"{audit_action_prefix}_FAILED", entity="Purchase",
                entity_id=str(purchase_id),
                description=f"공급처 발주 전송 실패: {type(exc).__name__}",
            )
            self.db.commit()

            return self._get_purchase_required(purchase_id, company_id)

        if result.status == "ACCEPTED":
            submission_status = PurchaseSubmissionStatus.SUBMITTED
        elif result.status == "PARTIAL":
            submission_status = PurchaseSubmissionStatus.PARTIALLY_ACCEPTED
        elif result.status == "REJECTED":
            submission_status = PurchaseSubmissionStatus.REJECTED
        else:
            submission_status = PurchaseSubmissionStatus.FAILED

        self.repository.save_submission_result_no_commit(
            purchase_id, company_id,
            {
                "submission_status": submission_status,
                "submission_provider_code": provider_code,
                "supplier_order_id": result.supplier_order_id,
                "submitted_at": datetime.utcnow(),
                "confirmed_price": result.confirmed_price,
                "accepted_quantities_json": canonical_json(
                    result.accepted_quantities,
                ),
                "rejected_quantities_json": canonical_json(
                    result.rejected_quantities,
                ),
                "submission_error_code": result.error_code,
                "submission_retryable": result.retryable,
                "submission_retry_after_seconds": result.retry_after_seconds,
                "correlation_id": correlation_id,
            },
        )

        write_audit_log(
            self.db, company_id=company_id, user_id=triggered_by,
            action=audit_action_prefix, entity="Purchase",
            entity_id=str(purchase_id),
            description=(
                f"공급처 발주 전송({provider_code}) — "
                f"supplier_order_id={result.supplier_order_id}, "
                f"status={submission_status}"
            ),
        )
        self.db.commit()

        return self._get_purchase_required(purchase_id, company_id)

    def submit_to_supplier(
        self, purchase_id: int, company_id: int, provider_code: str,
        triggered_by: int, test_scenario: str | None = None,
    ) -> Purchase:

        purchase = self._get_purchase_required(purchase_id, company_id)
        items, order_lines = self._check_submittable_and_build_order_lines(
            purchase, company_id,
        )

        fingerprint = self._compute_approval_fingerprint(purchase, items)

        claimed = self.repository.claim_submission_conditional(
            purchase_id, company_id, fingerprint,
        )
        if claimed != 1:
            self.db.rollback()
            existing = self._get_purchase_required(purchase_id, company_id)
            if existing.submission_status is not None:
                return existing
            raise ConflictException(
                "발주 전송이 이미 진행 중입니다 — 다시 조회 후 시도하세요.",
            )
        self.db.commit()

        return self._run_submission_attempt(
            purchase_id, company_id, provider_code, triggered_by,
            purchase.supplier_id, order_lines, fingerprint,
            "PURCHASE_SUBMITTED_TO_SUPPLIER", test_scenario=test_scenario,
        )

    def retry_submission_to_supplier(
        self, purchase_id: int, company_id: int, provider_code: str,
        triggered_by: int, test_scenario: str | None = None,
    ) -> Purchase:
        """2026-08-21 작업 1(항목 19) — FAILED 상태의 발주 전송만
        재시도할 수 있다. SUBMITTED/PARTIALLY_ACCEPTED/REJECTED처럼
        공급처가 이미 실제로 응답한 상태는 재시도 대상이 아니다(중복
        발주 방지).

        2026-08-21 5차 지시(작업 3) — 두 가지 서버측 재시도 게이트를
        추가한다(둘 다 클라이언트 버튼 비활성만으로는 충분하지
        않다 — API를 직접 호출하면 우회될 수 있으므로 방어적으로
        서버에서도 막는다).
        1) `submission_retryable is False`(재시도 불가능 오류)면
           차단한다.
        2) `submission_retry_after_seconds`가 설정돼 있고 그 대기
           시간이 아직 지나지 않았으면 차단한다."""

        purchase = self._get_purchase_required(purchase_id, company_id)

        if purchase.submission_status != PurchaseSubmissionStatus.FAILED:
            raise BadRequestException(
                "FAILED(전송 실패) 상태의 발주만 재시도할 수 있습니다 — "
                f"현재 전송 상태: {purchase.submission_status}",
            )

        if purchase.submission_retryable is False:
            raise BadRequestException(
                "NOT_RETRYABLE: 이 발주 전송 실패는 재시도할 수 없는 "
                f"오류입니다(오류 코드: {purchase.submission_error_code}) — "
                "발주안을 다시 검토하세요.",
            )

        if (
            purchase.submission_retry_after_seconds is not None
            and purchase.submitted_at is not None
        ):
            available_at = purchase.submitted_at + timedelta(
                seconds=purchase.submission_retry_after_seconds,
            )
            now = datetime.utcnow()
            if now < available_at:
                remaining = int((available_at - now).total_seconds())
                raise BadRequestException(
                    f"RETRY_AFTER_NOT_ELAPSED: 아직 재시도할 수 없습니다 "
                    f"— {remaining}초 후 다시 시도하세요.",
                )

        items, order_lines = self._check_submittable_and_build_order_lines(
            purchase, company_id,
        )

        fingerprint = self._compute_approval_fingerprint(purchase, items)

        reclaimed = self.repository.reclaim_failed_submission_conditional(
            purchase_id, company_id, fingerprint,
        )
        if reclaimed != 1:
            self.db.rollback()
            existing = self._get_purchase_required(purchase_id, company_id)
            if existing.submission_status != PurchaseSubmissionStatus.FAILED:
                return existing
            raise ConflictException(
                "발주 재시도가 이미 진행 중입니다 — 다시 조회 후 시도하세요.",
            )
        self.db.commit()

        return self._run_submission_attempt(
            purchase_id, company_id, provider_code, triggered_by,
            purchase.supplier_id, order_lines, fingerprint,
            "PURCHASE_SUBMITTED_TO_SUPPLIER", test_scenario=test_scenario,
        )

    # --------------------------------------------------
    # 조회
    # --------------------------------------------------

    def get_purchase(
        self,
        purchase_id: int,
        company_id: int,
    ) -> Purchase:

        return self._get_purchase_required(purchase_id, company_id)

    def list_purchases(
        self,
        company_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Purchase]:

        return self.repository.list_purchases_for_company(
            company_id, status, skip, limit,
        )

    def list_items(
        self,
        purchase_id: int,
        company_id: int,
    ) -> list[PurchaseItem]:

        self._get_purchase_required(purchase_id, company_id)

        return self.repository.list_items_for_purchase(
            purchase_id, company_id,
        )


__all__ = [
    "PurchaseService",
]
