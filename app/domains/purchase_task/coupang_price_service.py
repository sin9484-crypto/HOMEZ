"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_price_service.py

2026-10-25 — 쿠팡 쓰기 2번: **옵션별 판매가 변경**(외부 변경). 재고 수량 변경(`coupang_stock_service.py`)과 같은 틀을 따른다.
이 모듈이 지키는 것:

  1) 값은 만들어 내지 않는다 — 바꿀 판매가와 **매입가·배송비는 사용자가 입력한다**(HOMEZ가 공급가를 추측하지 않는다).
     현재 판매가는 쿠팡 재고 조회(salePrice)로 그 자리에서 읽고, 미리보기에서 본 값과 지금 값이 다르면 보내지 않는다.
  2) 손해 방지 — 입력한 매입가·배송비와 HOMEZ 기록값(수수료 9.8%·반품 예비 3%)·회사 최소 마진율(기본 18%)로 계산한
     **최저 판매가보다 낮으면 보내지 않는다**(자동으로 값을 올려 맞추지 않는다). 수수료율은 카테고리마다 다를 수 있어 화면에 가정으로 보인다.
  3) 쿠팡 변경 폭 — 쿠팡은 기본으로 변경 전 가격 대비 50% 인하·100% 인상까지만 받는다. HOMEZ는 그 제한을 풀지 않으므로(force 미전송)
     범위 밖이면 미리 막고 윙에서 직접 바꾸도록 안내한다. 20% 이상 바뀌면 사용자가 따로 확인(`confirm_big_change`)해야 보낸다.
  4) 안전 — 비상 정지가 켜져 있거나 기능 모드가 "운영자 승인"이 아니면 보내지 않는다. 승인 센터의 "쿠팡 판매가 변경" 허용이
     없으면 읽지도 보내지도 않는다(읽기·쓰기 각각 횟수형).
  5) 중복 방지·결과불명 — 작업 장부가 전담한다. 같은 가격으로 다시 맞추는 일이 있어 사용자 요청마다 참조값(`request_ref`)을 대상 키에 넣는다.
  6) 전송이 성공해도 **판매가를 다시 읽어** 실제로 바뀌었는지 알려 준다. 응답 모양은 공식 문서 예시로만 안다(실제로 받아 본 적 없음).
=========================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from decimal import ROUND_CEILING
from decimal import Decimal
from typing import Callable

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import COUPANG_MAX_DECREASE
from app.domains.purchase_task.coupang_channel_control_provider import COUPANG_MAX_INCREASE
from app.domains.purchase_task.coupang_channel_control_provider import MAX_SALE_PRICE
from app.domains.purchase_task.coupang_channel_control_provider import MAX_VENDOR_ITEM_ID_DIGITS
from app.domains.purchase_task.coupang_channel_control_provider import MIN_SALE_PRICE
from app.domains.purchase_task.coupang_channel_control_provider import PRICE_UNIT
from app.domains.purchase_task.coupang_stock_service import REQUEST_REF
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection
from app.domains.supplier.offer_rules import MarginAssumptions
from app.domains.supplier.offer_rules import SupplierOffer
from app.domains.supplier.offer_rules import floor_price
from app.domains.supplier.offer_rules import margin_at

logger = logging.getLogger("homez")

MAX_COST = 10_000_000  # 매입가·배송비 입력 상한(HOMEZ 자체) — 잘못 친 큰 수를 막는다
BIG_CHANGE_RATE = Decimal("0.20")  # 이 이상 바뀌면 따로 확인을 받는다


@dataclass
class PriceBlocker:
    code: str
    message: str


@dataclass
class PricePlan:
    vendor_item_id: str
    price: int
    unit_cost: int
    shipping_cost: int
    request_ref: str | None = None
    blockers: list[PriceBlocker] = field(default_factory=list)
    gate_blockers: list[PriceBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    current_price: int | None = None
    current_stock: int | None = None
    on_sale: bool | None = None
    floor_price: int | None = None
    margin_rate: Decimal | None = None
    assumptions: MarginAssumptions | None = None
    change_rate: Decimal | None = None
    big_change: bool = False
    already_applied: bool = False
    live_checked: bool = False
    ledger: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_applied

    def to_dict(self) -> dict:
        a = self.assumptions
        return {
            "vendor_item_id": self.vendor_item_id, "price": self.price, "request_ref": self.request_ref, "sendable": self.sendable,
            "live_checked": self.live_checked, "already_applied": self.already_applied, "big_change": self.big_change,
            "change_rate": float(self.change_rate) if self.change_rate is not None else None,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "coupang": {"current_price": self.current_price, "current_stock": self.current_stock, "on_sale": self.on_sale},
            "margin": {
                "unit_cost": self.unit_cost, "shipping_cost": self.shipping_cost, "floor_price": self.floor_price,
                "margin_rate": float(self.margin_rate) if self.margin_rate is not None else None,
                "fee_rate": float(a.fee_rate) if a else None, "return_reserve_rate": float(a.return_reserve_rate) if a else None,
                "min_margin": float(a.min_margin) if a else None,
            },
            "ledger": self.ledger, "price_unit": PRICE_UNIT, "max_price": MAX_SALE_PRICE,
        }


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _ceil_to_unit(value: Decimal) -> int:
    """최저 판매가는 10원 단위로 **올린다**(내리면 기준 마진을 밑돈다)."""

    return int((value / PRICE_UNIT).to_integral_value(rounding=ROUND_CEILING)) * PRICE_UNIT


class CoupangPriceService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 계획(미리보기) — 로컬 판정 + 쿠팡 재고 조회 1회(판매가)
    # ------------------------------------------------------------

    def plan(
        self, company_id: int, vendor_item_id: str, price: int, *, unit_cost: int, shipping_cost: int,
        request_ref: str | None = None, live: bool = True,
    ) -> PricePlan:
        item = str(vendor_item_id or "").strip()
        plan = PricePlan(vendor_item_id=item, price=price, unit_cost=unit_cost, shipping_cost=shipping_cost, request_ref=request_ref)
        if not item.isdigit() or len(item) > MAX_VENDOR_ITEM_ID_DIGITS:
            plan.blockers.append(PriceBlocker("INVALID_VENDOR_ITEM_ID", "쿠팡 옵션번호(옵션 ID)는 숫자여야 한다."))
        if not _int_in(price, MIN_SALE_PRICE, MAX_SALE_PRICE) or price % PRICE_UNIT != 0:
            plan.blockers.append(PriceBlocker("INVALID_PRICE", f"판매가는 {MIN_SALE_PRICE}~{MAX_SALE_PRICE:,}원의 10원 단위 정수여야 한다."))
        costs_ok = _int_in(unit_cost, 1, MAX_COST) and _int_in(shipping_cost, 0, MAX_COST)
        if not costs_ok:
            plan.blockers.append(PriceBlocker(
                "INVALID_COST", f"매입가는 1~{MAX_COST:,}원, 배송비는 0~{MAX_COST:,}원의 정수로 입력해야 한다(마진 확인에 필요하다).",
            ))
        if request_ref is not None and not REQUEST_REF.match(str(request_ref)):
            plan.blockers.append(PriceBlocker("INVALID_REQUEST_REF", "요청 참조값 형식이 올바르지 않다(미리보기를 다시 열어 주세요)."))
        if costs_ok:
            self._margin_checks(plan, company_id)
        self._resolve_store(plan, company_id)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        plan.ledger = self._ledger_summary(company_id, plan)
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _margin_checks(self, plan: PricePlan, company_id: int) -> None:
        defaults = MarginAssumptions()
        min_margin = ChannelPolicyService(self.db).get_effective_min_target_margin_rate(company_id)
        plan.assumptions = MarginAssumptions(
            fee_rate=defaults.fee_rate, return_reserve_rate=defaults.return_reserve_rate, min_margin=Decimal(min_margin),
        )
        offer = SupplierOffer(
            supplier_code="USER_INPUT", external_id=plan.vendor_item_id, title="",
            unit_cost=Decimal(plan.unit_cost), shipping_cost=Decimal(plan.shipping_cost),
        )
        floor = floor_price(offer, plan.assumptions)
        if floor is None:
            plan.blockers.append(PriceBlocker("MARGIN_UNKNOWN", "마진 기준을 계산할 수 없어 보내지 않는다(회사 마진 설정을 확인해 주세요)."))
            return
        plan.floor_price = _ceil_to_unit(floor)
        if _int_in(plan.price, MIN_SALE_PRICE, MAX_SALE_PRICE):
            plan.margin_rate = margin_at(offer, Decimal(plan.price), plan.assumptions)
            if plan.price < plan.floor_price:
                plan.blockers.append(PriceBlocker(
                    "BELOW_MARGIN_FLOOR",
                    f"이 판매가는 최저 판매가 {plan.floor_price:,}원보다 낮아 최소 마진을 지키지 못한다 — 보내지 않는다.",
                ))

    def _resolve_store(self, plan: PricePlan, company_id: int) -> None:
        stores = (
            self.db.query(StoreConnection)
            .filter(
                StoreConnection.company_id == company_id, StoreConnection.marketplace_code == "COUPANG",
                StoreConnection.connection_status == ConnectionStatus.CONNECTED,
            )
            .all()
        )
        if len(stores) == 1:
            plan.store_connection_id = stores[0].id
        elif not stores:
            plan.blockers.append(PriceBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다."))
        else:
            plan.blockers.append(PriceBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다."))

    def _gate_checks(self, plan: PricePlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(PriceBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(PriceBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: PricePlan) -> dict | None:
        if plan.request_ref is None:
            return None
        actions = self._actions_factory(self.db)
        row = actions._journal.get(company_id, ChannelActionType.PRICE_UPDATE, actions.price_key(plan.vendor_item_id, plan.price, plan.request_ref))
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: PricePlan, company_id: int) -> None:
        snapshot, failure = self._actions_factory(self.db).read_price(
            company_id=company_id, store_connection_id=plan.store_connection_id, vendor_item_id=plan.vendor_item_id,
        )
        if snapshot is None or not snapshot.ok:
            detail = failure.detail if failure is not None else (snapshot.detail if snapshot is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(PriceBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(PriceBlocker("PRICE_READ_FAILED", f"쿠팡 판매가를 읽지 못해 보내지 않는다. {detail}".strip()))
            return
        if snapshot.sale_price is None or snapshot.sale_price <= 0:
            plan.blockers.append(PriceBlocker("PRICE_READ_FAILED", "쿠팡 응답에 지금 판매가가 없어 보내지 않는다."))
            return
        plan.live_checked = True
        plan.current_price = snapshot.sale_price
        plan.current_stock = snapshot.amount_in_stock
        plan.on_sale = snapshot.on_sale
        if plan.current_price == plan.price:
            plan.already_applied = True
            return
        current = Decimal(plan.current_price)
        plan.change_rate = (Decimal(plan.price) - current) / current
        if Decimal(plan.price) < current * (1 - Decimal(str(COUPANG_MAX_DECREASE))) or Decimal(plan.price) > current * (1 + Decimal(str(COUPANG_MAX_INCREASE))):
            plan.blockers.append(PriceBlocker(
                "OUT_OF_COUPANG_RANGE",
                "쿠팡은 지금 가격 대비 50% 넘게 내리거나 100% 넘게 올리는 변경을 기본으로 받지 않는다 — 이 변경은 쿠팡 윙에서 직접 해 주세요.",
            ))
        if abs(plan.change_rate) >= BIG_CHANGE_RATE:
            plan.big_change = True

    # ------------------------------------------------------------
    # 전송
    # ------------------------------------------------------------

    def send(
        self, company_id: int, user_id: int, vendor_item_id: str, price: int, *, unit_cost: int, shipping_cost: int,
        expected_current_price: int, request_ref: str, confirm_big_change: bool = False, retry_action_required: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> dict:
        if not REQUEST_REF.match(str(request_ref or "")):
            raise BadRequestException("요청 참조값 형식이 올바르지 않다 — 미리보기를 다시 열어 주세요. 보내지 않았다.")
        plan = self.plan(
            company_id, vendor_item_id, price, unit_cost=unit_cost, shipping_cost=shipping_cost, request_ref=request_ref, live=True,
        )
        if plan.already_applied:
            return {"outcome": "ALREADY_DONE", "detail": "쿠팡 판매가가 이미 그 가격이라 보내지 않았다.", "executed": False, "verified": True,
                    "after_price": plan.current_price, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked:
            raise BadRequestException("판매가를 바꿀 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if isinstance(expected_current_price, bool) or expected_current_price != plan.current_price:
            raise ConflictException("화면에서 확인한 지금 판매가와 쿠팡의 지금 판매가가 다르다 — 미리보기를 다시 확인해 주세요. 보내지 않았다.")
        if plan.big_change and confirm_big_change is not True:
            raise BadRequestException("판매가가 20% 이상 바뀐다 — 큰 변경임을 따로 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        actions = self._actions_factory(self.db)
        result = actions.set_price(
            company_id=company_id, store_connection_id=plan.store_connection_id, vendor_item_id=plan.vendor_item_id,
            price=price, request_ref=request_ref, triggered_by=user_id, retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_PRICE_UPDATE", entity="coupang_vendor_item", entity_id=plan.vendor_item_id,
            description=f"쿠팡 옵션 판매가 변경 {result.outcome}: {plan.current_price} → {price} (매입가 {unit_cost}, 배송비 {shipping_cost})",
            company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        after_price: int | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 판매가를 다시 읽는다(읽기 1회). 읽지 못하면 확인 안 됨(None).
            after, _ = actions.read_price(company_id=company_id, store_connection_id=plan.store_connection_id, vendor_item_id=plan.vendor_item_id)
            if after is not None and after.ok and after.sale_price is not None:
                after_price = after.sale_price
                verified = after_price == price
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified, "after_price": after_price,
                "plan": plan.to_dict()}

    def reconcile(self, company_id: int, vendor_item_id: str, price: int, request_ref: str) -> dict:
        """결과불명(UNKNOWN)인 요청을 쿠팡 재고 조회의 판매가로 확인한다(읽기 GET 1회, 보내지 않음)."""

        if not REQUEST_REF.match(str(request_ref or "")):
            raise BadRequestException("요청 참조값 형식이 올바르지 않다.")
        item = str(vendor_item_id or "").strip()
        if not item.isdigit() or len(item) > MAX_VENDOR_ITEM_ID_DIGITS or not _int_in(price, MIN_SALE_PRICE, MAX_SALE_PRICE) or price % PRICE_UNIT:
            raise BadRequestException("확인할 수 없는 요청이다: INVALID_TARGET")
        plan = PricePlan(vendor_item_id=item, price=price, unit_cost=0, shipping_cost=0, request_ref=request_ref)
        self._resolve_store(plan, company_id)
        if plan.blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in plan.blockers))
        result = self._actions_factory(self.db).check_price(
            company_id=company_id, store_connection_id=plan.store_connection_id, vendor_item_id=item,
            price=price, request_ref=request_ref,
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangPriceService", "PricePlan", "PriceBlocker", "BIG_CHANGE_RATE", "MAX_COST"]
