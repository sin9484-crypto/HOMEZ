"""
=========================================================
Homez OS

File : app/domains/purchase_task/supplier_stop_sale_operator_service.py

2026-10-05 — 공급처 판매중단 처리(`SupplierStopSaleService`)의 **운영자 재처리 경로**.
이전에는 같은 상품의 다음 발주 시도가 있어야만 이전 판매중지·취소 결과를 다시 평가할 수
있었다(호출처가 `submit_order` 하나). 이 서비스는 기존 작업 장부 서비스를 호출하는 두 개의
명령만 제공한다 — 새 스케줄러나 범용 재시도 플랫폼이 아니다.

  1) `reconcile` — **결과 조회·대조.** 읽기 전용 쿠팡 조회만 한다. 외부 변경 요청(판매중지·
     주문 취소)은 이 경로에서 구조적으로 보낼 수 없다(`reconcile_pending`은 쓰기 메서드를
     호출하지 않는다). 확인된 사실만 장부·연결 상태·(승인 범위 안의) 내부 주문에 반영한다.
     비상정지 중에도 실행된다(확정 정책: 긴급 중지는 변경을 멈추고 조회·진단은 유지) — 이때
     내부 주문 취소 반영은 계속 차단된다.
  2) `re_execute` — **외부 변경 재실행.** 사람이 명시적으로 부른 경우에만, 다음을 **다시** 검사한 뒤
     기존 처리(`handle_explicit_stop_sale`)에 맡긴다:
       - 승인자(운영자 id)와 승인된 업체코드(`expected_vendor_id`)가 있다 — 없으면 요청하지 않는다
       - **현재** 공급처 상태가 여전히 명시적 판매중단(3)이다 — 조회 실패·그 외 상태는 재실행 거부
       - 기능 모드·비상정지, 장부의 재시도 시각·횟수, 조치 필요 상태는 기존 처리가 판정한다
         (UNKNOWN은 조회로 미적용이 확인된 뒤에만 한도 안에서 다시 요청한다)
     WING 로그인 ID는 위저드(`source_wizard_id`)를 지정했을 때 **그 위저드가 선택한 판매계정이
     명시적으로 해석되는 판매 연결과 요청 대상 연결이 같은 경우에만** 그 요청에 쓴다 — 전역 적용·
     저장 없음.
모든 쿼리는 `company_id`로 격리하고 운영자 명령은 감사 로그를 남긴다.
=========================================================
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.channel_adapter import get_purchase_channel_adapter
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.supplier_stop_sale_service import (
    StopSaleReport, SupplierStopSaleService,
)

logger = logging.getLogger("homez")


def wing_user_ids_by_store(entries: list, resolve_store_id: Callable[[int], int | None]) -> dict[int, str]:
    """위저드 채널 선택 항목에서 `판매 연결 id → WING ID`를 만든다. 선택한 판매계정이 명시적
    판매 연결로 해석되는 항목만 포함한다(해석 실패·값 없음은 제외 — 추측하지 않는다)."""

    result: dict[int, str] = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        account_id = entry.get("marketplace_account_id")
        value = ((entry.get("required_fields") or {}).get("vendorUserId") or "")
        value = value.strip() if isinstance(value, str) else ""
        if account_id is None or not value:
            continue
        store_id = resolve_store_id(account_id)
        if store_id is not None:
            result[store_id] = value
    return result


class SupplierStopSaleOperatorService:

    def __init__(
        self, db: Session, *, adapter_factory=None, actions_factory=None,
        credential_store=None, order_service=None,
    ):
        self.db = db
        self._credential_store = credential_store
        self._adapter_factory = adapter_factory or self._default_adapter
        self._actions_factory = actions_factory or self._default_actions
        self._order_service = order_service

    # ---- 기본 구현(운영) ----

    def _default_adapter(self, connection: PurchaseChannelConnection):
        return get_purchase_channel_adapter(
            connection.mall_code, credential_reference=connection.credential_reference,
            credential_store=self._credential_store,
        )

    def _default_actions(self, *, expected_vendor_id=None, wing_user_id=None):
        return CoupangChannelActions(
            self.db, credential_store=self._credential_store,
            expected_vendor_id=expected_vendor_id, wing_user_id=wing_user_id,
        )

    # ---- 공통 ----

    def _connection(self, company_id: int, connection_id: int) -> PurchaseChannelConnection:
        connection = (
            self.db.query(PurchaseChannelConnection)
            .filter(
                PurchaseChannelConnection.id == connection_id,
                PurchaseChannelConnection.company_id == company_id,
            )
            .first()
        )
        if connection is None:
            raise NotFoundException("이 회사의 매입처 연결을 찾을 수 없습니다.")
        return connection

    def _service(self, actions) -> SupplierStopSaleService:
        return SupplierStopSaleService(
            self.db, channel_actions=actions, order_service=self._order_service,
        )

    def _wing_resolver(self, company_id: int, wizard_id: int | None):
        if wizard_id is None:
            return None
        from app.domains.marketplace_listing.coupang_seller_connection import (
            SellerConnectionError, resolve_seller_connection,
        )
        from app.domains.marketplace_listing.listing_wizard_repository import (
            ListingWizardRepository,
        )

        wizard = ListingWizardRepository(self.db).get_for_company(wizard_id, company_id)
        if wizard is None:
            raise NotFoundException("지정한 상품등록 작업을 찾을 수 없습니다.")
        try:
            entries = json.loads(wizard.channel_selections_json or "[]")
        except (TypeError, ValueError):
            entries = []

        def resolve(account_id: int) -> int | None:
            try:
                return resolve_seller_connection(self.db, company_id, account_id).id
            except SellerConnectionError:
                return None

        by_store = wing_user_ids_by_store(entries, resolve)
        return lambda store_connection_id: by_store.get(store_connection_id)

    def _audit(self, action: str, user_id: int, company_id: int, connection_id: int,
               code: str, report: StopSaleReport) -> None:
        write_audit_log(
            self.db, user_id=user_id, action=action, entity="supplier_stop_sale",
            entity_id=f"{connection_id}:{code}", company_id=company_id,
            description=f"운영자 명령 결과: {report.summary()}"[:500],
        )
        self.db.commit()

    # ---- 1) 조회·대조(읽기 전용) ----

    def reconcile(
        self, *, company_id: int, connection_id: int, product_code: str, operator_user_id: int,
    ) -> StopSaleReport:
        code = (product_code or "").strip()
        if not code:
            raise BadRequestException("공급처 상품코드가 필요합니다.")
        self._connection(company_id, connection_id)
        # 업체코드·WING ID는 읽기 대조에 필요 없다(요청을 보내지 않는다).
        service = self._service(self._actions_factory())
        report = service.reconcile_pending(
            company_id=company_id, connection_id=connection_id, product_code=code,
            triggered_by=operator_user_id, approved_by=operator_user_id,
        )
        self._audit("SUPPLIER_STOP_SALE_OPERATOR_RECONCILE", operator_user_id, company_id,
                    connection_id, code, report)
        return report

    # ---- 2) 외부 변경 재실행 ----

    def re_execute(
        self, *, company_id: int, connection_id: int, product_code: str,
        operator_user_id: int, expected_vendor_id: str,
        source_wizard_id: int | None = None, retry_action_required: bool = False,
    ) -> StopSaleReport:
        code = (product_code or "").strip()
        vendor = (expected_vendor_id or "").strip()
        if not code:
            raise BadRequestException("공급처 상품코드가 필요합니다.")
        if not vendor:
            raise BadRequestException(
                "외부 변경을 다시 실행하려면 승인된 쿠팡 업체코드(expected_vendor_id)를 "
                "지정해야 합니다 — 지정하지 않으면 요청하지 않습니다.",
            )
        connection = self._connection(company_id, connection_id)

        # 현재 상태 재확인: 이전 사건이 아니라 지금 공급처가 판매중단을 명시하는가.
        try:
            product = self._adapter_factory(connection).lookup_product(code)
        except Exception as exc:  # noqa: BLE001 — 조회 실패는 판매중단이 아니다
            logger.info("재실행 전 공급처 조회 실패: %s", type(exc).__name__)
            raise ConflictException(
                "공급처 현재 상태를 조회하지 못해 재실행하지 않았습니다(통신·인증 오류는 판매중단이 아닙니다).",
            ) from exc
        if not SupplierStopSaleService.is_explicit_stop_sale(product):
            raise ConflictException(
                "공급처 현재 상태가 판매중단이 아니라서 재실행하지 않았습니다"
                f"(상태: {getattr(product, 'status', None)}).",
            )

        actions = self._actions_factory(
            expected_vendor_id=vendor,
            wing_user_id=self._wing_resolver(company_id, source_wizard_id),
        )
        report = self._service(actions).handle_explicit_stop_sale(
            company_id=company_id, connection_id=connection_id, product_code=code,
            supplier_status=product.status, triggered_by=operator_user_id,
            approved_by=operator_user_id, retry_action_required=retry_action_required,
        )
        self._audit("SUPPLIER_STOP_SALE_OPERATOR_REEXECUTE", operator_user_id, company_id,
                    connection_id, code, report)
        return report


__all__ = ["SupplierStopSaleOperatorService", "wing_user_ids_by_store"]
