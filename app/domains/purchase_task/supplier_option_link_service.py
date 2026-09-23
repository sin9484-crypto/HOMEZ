"""
=========================================================
Homez OS

File : app/domains/purchase_task/supplier_option_link_service.py

2026-09-21 옵션 연결 — 쿠팡에서 주문된 판매 옵션(회사 + 쿠팡 스토어 연결 +
channel_sku)이 온채널의 어느 상품코드·옵션ID에 해당하는지 영구 저장하고
발주 검토·승인에서 재사용한다.

원칙:
- 조인 키는 쿠팡 externalVendorSku(= 주문 수집의 channel_sku)다. 이름·배열
  순서로 옵션을 매칭하지 않는다.
- 연결은 발주·결제 승인이 아니다. 연결을 재사용해도 가격·재고·배송비·한도·
  최종 승인은 기존 실행 정책대로 매번 확인한다.
- 저장 전에 그 계정으로 공급처 상품을 실조회해 옵션ID가 그 상품에 속하는지
  확인한다(확인하지 못하면 저장하지 않는다).
- 공급처 옵션이 사라지거나 옵션명이 바뀌거나 매입 계정이 바뀌면 조용히
  대체하지 않고 NEEDS_REVIEW로 내려 사용자의 재확인을 기다린다.
- 한 판매 옵션이 여러 공급 옵션·상품의 묶음인 경우는 지원하지 않고 이유와
  함께 차단한다.
- `supplier_option_links` 테이블이 아직 없는(Migration 미적용) DB에서는 이
  기능만 "사용 불가"로 조용히 물러나고 기존 수동 입력 경로는 그대로 동작한다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.model import OrderItem
from app.domains.purchase_task.constants import CapabilitySupport
from app.domains.purchase_task.constants import SupplierOptionLinkStatus
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import SupplierOptionLink
from app.domains.store_connection.model import StoreConnection

SUPPORTED_SUPPLIER_MALL_CODE = "ONCHANNEL"
MAX_CHANNEL_SKU_LENGTH = 150

# 해석 결과(TaskLinkResolution.state) — 화면·검토·승인이 같은 값을 쓴다.
STATE_UNAVAILABLE = "UNAVAILABLE"          # 연결 저장소(테이블) 미적용
STATE_NO_ORDER_ITEM = "NO_ORDER_ITEM"      # 쿠팡 주문 품목과 연결된 작업이 아님
STATE_STORE_UNRESOLVED = "STORE_UNRESOLVED"  # 주문의 쿠팡 스토어를 하나로 특정 못함
STATE_NO_LINK = "NO_LINK"                  # 저장된 연결 없음(수동 입력 경로)
# 판매자 SKU와 쿠팡 옵션번호(vendorItemId)가 서로 다른 연결을 가리키거나 한쪽 근거만으로는
# 안전하게 하나로 정할 수 없다 — 자동 선택하지 않고 차단한다.
STATE_IDENTIFIER_CONFLICT = "IDENTIFIER_CONFLICT"
STATE_ACTIVE = SupplierOptionLinkStatus.ACTIVE
STATE_NEEDS_REVIEW = SupplierOptionLinkStatus.NEEDS_REVIEW
STATE_DISABLED = SupplierOptionLinkStatus.DISABLED


@dataclass(frozen=True)
class SupplierComponent:
    product_code: str
    option_id: str
    units: int = 1


@dataclass(frozen=True)
class TaskLinkResolution:
    state: str
    channel_sku: str | None = None
    store_connection_id: int | None = None
    sold_quantity: int | None = None
    link: SupplierOptionLink | None = None
    detail: str = ""
    # 주문이 실제로 가져온 쿠팡 옵션번호(수집 단계 원본, 없으면 None)와 판매자 SKU 존재 여부
    # ("PRESENT" = 옵션번호와 다른 SKU가 있음 / "ABSENT_OR_SAME" = SKU가 없거나 옵션번호와
    # 같은 문자열이라 SKU로 인정하지 않음 / "UNKNOWN" = 수집 원본을 찾지 못함).
    vendor_item_id: str | None = None
    seller_sku_state: str = "UNKNOWN"
    # 어떤 식별자로 연결을 찾았는지: "SKU" | "VENDOR_ITEM_ID" | "BOTH" | None
    linked_by: str | None = None

    @property
    def expected_product_code(self) -> str | None:
        return self.link.supplier_product_code if self.link else None

    @property
    def expected_options(self) -> list[dict]:
        """이 작업의 판매 수량에 대응하는 공급 옵션 주문 구성(ID는 문자열,
        수량 = 판매 수량 × 구성 수량)."""

        if self.link is None or self.sold_quantity is None:
            return []
        return [{
            "id": self.link.supplier_option_id,
            "qty": self.sold_quantity * self.link.units_per_sale,
        }]


def resolution_to_dict(resolution: TaskLinkResolution) -> dict:
    """작업 단위 연결 해석 결과를 화면·API 응답 형태로 만든다(비밀값·고객정보 없음)."""

    link = resolution.link
    return {
        "state": resolution.state, "detail": resolution.detail,
        "link_id": link.id if link else None,
        "expected_product_code": resolution.expected_product_code,
        "expected_options": resolution.expected_options,
        "units_per_sale": link.units_per_sale if link else None,
        "channel_sku": resolution.channel_sku,
        "order_vendor_item_id": resolution.vendor_item_id,
        "seller_sku_state": resolution.seller_sku_state,
        "linked_by": resolution.linked_by,
        "supplier_option_name": link.supplier_option_name_snapshot if link else None,
        "status_reason": link.status_reason if link else None,
        "coupang_ids_confirmed": (
            bool(link.coupang_vendor_item_id) if link else None
        ),
    }


@dataclass(frozen=True)
class ReviewLinkEvaluation:
    state: str
    detail: str
    blocked_reasons: list[str] = field(default_factory=list)
    expected_product_code: str | None = None
    expected_options: list[dict] = field(default_factory=list)
    link_id: int | None = None
    units_per_sale: int | None = None


def _clean_sku(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BadRequestException("쿠팡 SKU(externalVendorSku)를 입력해야 합니다.")
    if value != value.strip():
        raise BadRequestException(
            "SKU 앞뒤에 공백이 있으면 안 됩니다 — 주문 수집이 공백 없이 읽어 "
            "오므로 연결이 영원히 맞지 않습니다.",
        )
    if len(value) > MAX_CHANNEL_SKU_LENGTH:
        raise BadRequestException(
            f"SKU는 {MAX_CHANNEL_SKU_LENGTH}자를 넘을 수 없습니다.",
        )
    return value


class SupplierOptionLinkService:

    def __init__(self, db: Session, *, connection_service=None):

        self.db = db
        self._connection_service = connection_service

    # ---------------- 사용 가능 여부 ----------------

    @staticmethod
    def is_store_available(db: Session) -> bool:
        """Migration이 아직 적용되지 않은 DB에서는 False — 호출부(검토·승인)는
        이 경우 기존 수동 입력 경로로 그대로 진행한다."""

        try:
            return bool(
                sa_inspect(db.get_bind()).has_table(SupplierOptionLink.__tablename__),
            )
        except Exception:  # noqa: BLE001 — 확인 자체가 실패하면 "없다"로 취급
            return False

    def _connections(self):
        if self._connection_service is None:
            from app.domains.purchase_task.channel_connection_service import (
                PurchaseChannelConnectionService,
            )

            self._connection_service = PurchaseChannelConnectionService(self.db)
        return self._connection_service

    # ---------------- 조회 ----------------

    def get_link(
        self, company_id: int, store_connection_id: int, channel_sku: str,
    ) -> SupplierOptionLink | None:

        return (
            self.db.query(SupplierOptionLink)
            .filter(
                SupplierOptionLink.company_id == company_id,
                SupplierOptionLink.store_connection_id == store_connection_id,
                SupplierOptionLink.channel_sku == channel_sku,
            )
            .first()
        )

    def get_link_by_vendor_item(
        self, company_id: int, store_connection_id: int, vendor_item_id: str,
    ) -> SupplierOptionLink | None:
        """쿠팡 옵션번호로 찾는다 — 회사·판매 계정 범위 안에서 DB UNIQUE가 유일성을 보장."""

        return (
            self.db.query(SupplierOptionLink)
            .filter(
                SupplierOptionLink.company_id == company_id,
                SupplierOptionLink.store_connection_id == store_connection_id,
                SupplierOptionLink.coupang_vendor_item_id == vendor_item_id,
            )
            .first()
        )

    def list_links(
        self, company_id: int, *, store_connection_id: int | None = None,
        channel_sku: str | None = None,
    ) -> list[SupplierOptionLink]:

        query = self.db.query(SupplierOptionLink).filter(
            SupplierOptionLink.company_id == company_id,
        )
        if store_connection_id is not None:
            query = query.filter(
                SupplierOptionLink.store_connection_id == store_connection_id,
            )
        if channel_sku is not None:
            query = query.filter(SupplierOptionLink.channel_sku == channel_sku)
        return query.order_by(SupplierOptionLink.id.asc()).all()

    # ---------------- 저장 ----------------

    def save_link(
        self, company_id: int, *, store_connection_id: int, channel_sku: str,
        purchase_connection_id: int, components: list[SupplierComponent],
        confirmed_by: int, replace: bool = False,
    ) -> SupplierOptionLink:
        """사용자가 확인한 판매 옵션 ↔ 공급 옵션 대응을 저장한다.

        같은 값으로 다시 저장하면 멱등(이미 ACTIVE면 그대로 반환, 사용 중지
        상태였다면 실조회가 통과할 때 ACTIVE로 복귀). 다른 공급 옵션에 이미
        연결돼 있으면 `replace=True`로 명시 확인해야만 바꾼다(버전 증가)."""

        if not self.is_store_available(self.db):
            raise ConflictException(
                "옵션 연결 저장소가 아직 준비되지 않았습니다(Migration 승인 대기).",
            )

        sku = _clean_sku(channel_sku)

        if len(components) != 1:
            raise BadRequestException(
                "한 판매 옵션이 여러 공급 옵션·상품의 묶음인 구성은 아직 "
                "지원하지 않습니다 — 공급 옵션 하나에 구성 수량을 지정하는 "
                "경우만 연결할 수 있습니다.",
            )
        component = components[0]
        product_code = str(component.product_code or "").strip()
        option_id = str(component.option_id or "").strip()
        if not product_code or not option_id:
            raise BadRequestException("공급 상품코드와 옵션ID를 입력해야 합니다.")
        if len(product_code) > 50 or len(option_id) > 50:
            raise BadRequestException("공급 상품코드·옵션ID가 너무 깁니다.")
        units = component.units
        if isinstance(units, bool) or not isinstance(units, int) or units < 1:
            raise BadRequestException(
                "구성 수량은 1 이상의 정수여야 합니다(판매 1개가 공급 옵션 "
                "몇 개에 해당하는지).",
            )

        store = (
            self.db.query(StoreConnection)
            .filter(
                StoreConnection.id == store_connection_id,
                StoreConnection.company_id == company_id,
            )
            .first()
        )
        if store is None:
            raise NotFoundException("판매 채널 연결을 찾을 수 없습니다.")

        purchase_connection = self._connections().get_connection_or_404(
            purchase_connection_id, company_id,
        )
        if purchase_connection.mall_code != SUPPORTED_SUPPLIER_MALL_CODE:
            raise BadRequestException("옵션 연결은 현재 온채널 매입 계정만 지원합니다.")

        option_label = self._verify_supplier_option(
            company_id, purchase_connection_id, product_code, option_id,
            triggered_by=confirmed_by,
        )

        now = datetime.utcnow()
        existing = self.get_link(company_id, store_connection_id, sku)
        if existing is None:
            link = SupplierOptionLink(
                company_id=company_id, store_connection_id=store_connection_id,
                channel_sku=sku, purchase_connection_id=purchase_connection_id,
                supplier_product_code=product_code, supplier_option_id=option_id,
                supplier_option_name_snapshot=option_label,
                units_per_sale=units, status=SupplierOptionLinkStatus.ACTIVE,
                version=1, confirmed_by=confirmed_by, confirmed_at=now,
            )
            self.db.add(link)
            try:
                self.db.flush()
            except IntegrityError:
                # 동시 저장 — 진 쪽은 먼저 저장된 행을 다시 읽어 같은 값이면
                # 멱등, 다르면 충돌로 처리한다(중복 행은 UNIQUE가 막는다).
                self.db.rollback()
                existing = self.get_link(company_id, store_connection_id, sku)
                if existing is None:
                    raise
                return self._reconcile_existing(
                    existing, company_id, purchase_connection_id, product_code,
                    option_id, units, option_label, confirmed_by, replace, now,
                )
            write_audit_log(
                self.db, user_id=confirmed_by, company_id=company_id,
                action="SUPPLIER_OPTION_LINK_SAVED", entity="supplier_option_link",
                entity_id=str(link.id),
                description=(
                    f"store={store_connection_id} sku={sku} → 온채널 "
                    f"{product_code}/{option_id} ×{units}"
                ),
            )
            self.db.commit()
            self.db.refresh(link)
            return link

        return self._reconcile_existing(
            existing, company_id, purchase_connection_id, product_code, option_id,
            units, option_label, confirmed_by, replace, now,
        )

    def _reconcile_existing(
        self, existing, company_id, purchase_connection_id, product_code,
        option_id, units, option_label, confirmed_by, replace, now,
    ) -> SupplierOptionLink:

        same = (
            existing.purchase_connection_id == purchase_connection_id
            and existing.supplier_product_code == product_code
            and existing.supplier_option_id == option_id
            and existing.units_per_sale == units
        )
        if same and existing.status == SupplierOptionLinkStatus.ACTIVE:
            return existing
        if not same and not replace:
            raise ConflictException(
                "이 쿠팡 SKU는 이미 다른 공급 옵션에 연결돼 있습니다 — 바꾸려면 "
                "기존 연결을 확인하고 교체(replace)를 명시해야 합니다.",
            )

        result = self.db.execute(
            update(SupplierOptionLink)
            .where(SupplierOptionLink.id == existing.id)
            .where(SupplierOptionLink.version == existing.version)
            .values(
                purchase_connection_id=purchase_connection_id,
                supplier_product_code=product_code,
                supplier_option_id=option_id,
                supplier_option_name_snapshot=option_label,
                units_per_sale=units, status=SupplierOptionLinkStatus.ACTIVE,
                status_reason=None, version=existing.version + 1,
                confirmed_by=confirmed_by, confirmed_at=now, updated_at=now,
            ),
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "다른 요청이 먼저 이 연결을 바꿨습니다 — 다시 확인하세요.",
            )
        write_audit_log(
            self.db, user_id=confirmed_by, company_id=company_id,
            action=(
                "SUPPLIER_OPTION_LINK_RECONFIRMED" if same
                else "SUPPLIER_OPTION_LINK_REPLACED"
            ),
            entity="supplier_option_link", entity_id=str(existing.id),
            description=(
                f"이전 {existing.supplier_product_code}/{existing.supplier_option_id}"
                f"×{existing.units_per_sale} → {product_code}/{option_id}×{units}"
            ),
        )
        self.db.commit()
        self.db.expire_all()
        return self.db.get(SupplierOptionLink, existing.id)

    def _verify_supplier_option(
        self, company_id: int, purchase_connection_id: int, product_code: str,
        option_id: str, *, triggered_by: int | None,
    ) -> str | None:
        """그 매입 계정으로 공급 상품을 실조회해 옵션ID가 그 상품에 속하는지
        **ID로** 확인한다(이름·순서 사용 안 함). 확인하지 못하면 저장하지 않는다."""

        try:
            lookup = self._connections().lookup_product(
                purchase_connection_id, company_id, product_code,
                triggered_by=triggered_by,
            )
        except (BadRequestException, ConflictException, NotFoundException):
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConflictException(
                f"공급처 상품을 확인하지 못해 연결을 저장하지 않았습니다: {exc}",
            ) from exc

        if lookup.support != CapabilitySupport.SUPPORTED:
            raise ConflictException(
                "공급처 상품 조회가 지원되지 않아 연결을 저장하지 않았습니다.",
            )
        matches = [o for o in lookup.options if str(o.option_id) == option_id]
        if len(matches) != 1:
            raise ConflictException(
                f"공급 상품 {product_code}에 옵션ID {option_id}가 "
                f"{'없습니다' if not matches else '중복돼 있습니다'} — 연결을 "
                "저장하지 않았습니다.",
            )
        label = (matches[0].label or "").strip()
        return label[:200] if label else None

    # ---------------- 해제 ----------------

    def disable_link(
        self, company_id: int, link_id: int, *, actor_user_id: int,
        reason: str = "사용자가 연결을 해제함",
    ) -> SupplierOptionLink:

        link = (
            self.db.query(SupplierOptionLink)
            .filter(
                SupplierOptionLink.id == link_id,
                SupplierOptionLink.company_id == company_id,
            )
            .first()
        )
        if link is None:
            raise NotFoundException("옵션 연결을 찾을 수 없습니다.")
        link.status = SupplierOptionLinkStatus.DISABLED
        link.status_reason = reason[:200]
        write_audit_log(
            self.db, user_id=actor_user_id, company_id=company_id,
            action="SUPPLIER_OPTION_LINK_DISABLED", entity="supplier_option_link",
            entity_id=str(link.id), description=reason[:200],
        )
        self.db.commit()
        self.db.refresh(link)
        return link

    # ---------------- 작업(주문 품목) → 연결 해석 ----------------

    def resolve_for_task(self, task: PurchaseTask) -> TaskLinkResolution:
        """구매 작업 → 원본 주문 품목(channel_sku·수량) → 쿠팡 스토어 →
        저장된 연결. 읽기 전용."""

        if not self.is_store_available(self.db):
            return TaskLinkResolution(
                STATE_UNAVAILABLE,
                detail="옵션 연결 저장소가 아직 적용되지 않았습니다(수동 입력 경로).",
            )
        if task.source_order_item_id is None:
            return TaskLinkResolution(
                STATE_NO_ORDER_ITEM,
                detail="쿠팡 주문 품목과 연결된 작업이 아니라 연결을 적용할 수 없습니다.",
            )
        item = (
            self.db.query(OrderItem)
            .filter(
                OrderItem.id == task.source_order_item_id,
                OrderItem.company_id == task.company_id,
            )
            .first()
        )
        if item is None or not item.channel_sku:
            return TaskLinkResolution(
                STATE_NO_ORDER_ITEM, detail="원본 주문 품목을 찾을 수 없습니다.",
            )
        store_ids = {
            row[0] for row in self.db.query(OrderChannelFulfillment.store_connection_id)
            .filter(
                OrderChannelFulfillment.company_id == task.company_id,
                OrderChannelFulfillment.order_id == item.order_id,
            )
            .distinct().all()
        }
        if len(store_ids) != 1:
            return TaskLinkResolution(
                STATE_STORE_UNRESOLVED, channel_sku=item.channel_sku,
                sold_quantity=item.quantity,
                detail=(
                    "이 주문의 쿠팡 판매 계정을 하나로 특정하지 못해 연결을 "
                    "자동 적용하지 않습니다."
                ),
            )
        store_id = next(iter(store_ids))
        return self._resolve_link(task, item, store_id)

    def _order_vendor_item_id(self, task: PurchaseTask, item: OrderItem) -> str | None:
        """수집 단계 원본(UnresolvedOrderItem.vendor_item_id)에서 주문이 실제로 가져온
        쿠팡 옵션번호를 되찾는다. OrderItem은 SKU와 옵션번호를 합친 channel_sku 하나만
        갖기 때문이다. 원본이 없거나 하나로 정해지지 않으면 None(추측하지 않는다)."""

        try:
            has_staging = sa_inspect(self.db.get_bind()).has_table(
                UnresolvedOrderItem.__tablename__,
            )
        except Exception:  # noqa: BLE001 — 확인하지 못하면 "원본 없음"(기존 경로 유지)
            has_staging = False
        if not has_staging:
            return None
        rows = (
            self.db.query(UnresolvedOrderItem.vendor_item_id)
            .filter(
                UnresolvedOrderItem.company_id == task.company_id,
                UnresolvedOrderItem.resolved_order_item_id == item.id,
            )
            .distinct().all()
        )
        values = {str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()}
        return next(iter(values)) if len(values) == 1 else None

    def _resolve_link(
        self, task: PurchaseTask, item: OrderItem, store_id: int,
    ) -> TaskLinkResolution:
        """판매자 SKU와 쿠팡 옵션번호(vendorItemId)는 서로 다른 식별자다. 수집 단계는
        SKU가 없으면 옵션번호를 channel_sku에 넣기 때문에 그대로 비교하면 두 문자열
        공간이 섞인다 — 옵션번호는 수집 원본에서 따로 되찾아 SKU 경로와 옵션번호
        경로를 각각 조회하고, 둘이 모순되면 자동 선택하지 않고 차단한다."""

        company_id = task.company_id
        channel_sku = item.channel_sku
        vendor = self._order_vendor_item_id(task, item)
        if vendor is None:
            sku_state = "UNKNOWN"
        elif channel_sku == vendor:
            sku_state = "ABSENT_OR_SAME"
        else:
            sku_state = "PRESENT"

        base = dict(
            channel_sku=channel_sku, store_connection_id=store_id,
            sold_quantity=item.quantity, vendor_item_id=vendor,
            seller_sku_state=sku_state,
        )
        link_by_sku = self.get_link(company_id, store_id, channel_sku)
        link_by_vendor = (
            self.get_link_by_vendor_item(company_id, store_id, vendor)
            if vendor else None
        )

        def conflict(detail: str) -> TaskLinkResolution:
            return TaskLinkResolution(STATE_IDENTIFIER_CONFLICT, detail=detail, **base)

        def found(link: SupplierOptionLink, by: str) -> TaskLinkResolution:
            return TaskLinkResolution(
                link.status, link=link, detail=link.status_reason or "",
                linked_by=by, **base,
            )

        if sku_state == "ABSENT_OR_SAME":
            # 판매자 SKU 없이(또는 옵션번호와 같은 문자열로) 들어온 주문 — 옵션번호 경로만
            # 신뢰한다. 같은 문자열의 SKU 연결이 따로 있으면 두 식별자를 섞어 쓴 것이라
            # 출처를 알 수 없어 차단한다.
            if link_by_vendor is not None:
                if link_by_sku is not None and link_by_sku.id != link_by_vendor.id:
                    return conflict(
                        "주문의 옵션번호와 같은 문자열의 SKU 연결이 다른 옵션을 가리킵니다 "
                        "— 두 식별자를 구분할 수 없어 자동으로 고르지 않습니다.",
                    )
                return found(link_by_vendor, "VENDOR_ITEM_ID")
            if link_by_sku is not None:
                return conflict(
                    "판매자 SKU 없이 들어온 주문인데 옵션번호와 같은 문자열의 SKU 연결만 "
                    "있어 어느 식별자인지 알 수 없습니다 — 자동으로 고르지 않습니다.",
                )
            return TaskLinkResolution(
                STATE_NO_LINK, detail=(
                    "판매자 SKU 없이 들어온 주문이고 이 쿠팡 옵션번호로 확인된 연결이 없습니다 "
                    "— 상품 준비 화면에서 쿠팡 옵션번호를 확인·연결한 뒤 다시 시도하세요."
                ), **base,
            )

        # SKU가 있거나(옵션번호와 다름) 수집 원본을 찾지 못한 경우 — SKU 경로가 기본이다.
        if link_by_sku is not None and link_by_vendor is not None:
            if link_by_sku.id != link_by_vendor.id:
                return conflict(
                    "판매자 SKU와 쿠팡 옵션번호가 서로 다른 연결을 가리킵니다 — 자동으로 "
                    "고르지 않습니다. 연결을 확인하세요.",
                )
            return found(link_by_sku, "BOTH")
        if link_by_sku is not None:
            stored = link_by_sku.coupang_vendor_item_id
            if vendor is not None and stored is not None and stored != vendor:
                return conflict(
                    "이 판매자 SKU에 확인돼 있는 쿠팡 옵션번호와 주문의 옵션번호가 "
                    "다릅니다 — 자동으로 고르지 않습니다. 연결을 확인하세요.",
                )
            return found(link_by_sku, "SKU")
        if link_by_vendor is not None:
            return conflict(
                "주문의 판매자 SKU가 이 쿠팡 옵션번호로 저장된 연결의 SKU와 다릅니다 "
                "— 자동으로 고르지 않습니다. 연결을 확인하세요.",
            )
        return TaskLinkResolution(
            STATE_NO_LINK, detail=(
                "저장된 공급처 옵션 연결이 없습니다 — 사용자가 공급 상품·옵션을 "
                "직접 확인해 선택해야 하며, 확인 뒤 연결로 저장할 수 있습니다."
            ), **base,
        )

    # ---------------- 발주 검토 평가 ----------------

    def evaluate_review(
        self, task: PurchaseTask, *, requested_product_code: str,
        requested_options: list[dict], product_options, product_lookup_ok: bool,
    ) -> ReviewLinkEvaluation:
        """발주 검토가 이미 실조회한 공급 옵션 목록(`product_options`)으로
        저장된 연결을 재검증한다. 연결이 없거나 저장소가 없으면 차단하지
        않는다(기존 수동 경로). 연결이 있으면 요청이 연결과 다를 때, 옵션이
        사라지거나 바뀌었을 때, 매입 계정이 바뀌었을 때 모두 차단한다."""

        resolution = self.resolve_for_task(task)
        state = resolution.state
        if state in (
            STATE_UNAVAILABLE, STATE_NO_ORDER_ITEM, STATE_STORE_UNRESOLVED,
            STATE_NO_LINK,
        ):
            return ReviewLinkEvaluation(state=state, detail=resolution.detail)
        if state == STATE_IDENTIFIER_CONFLICT:
            return ReviewLinkEvaluation(
                state=state, detail=resolution.detail,
                blocked_reasons=[
                    "쿠팡 식별자가 서로 맞지 않아 옵션 연결을 자동으로 쓰지 않습니다 — "
                    + resolution.detail,
                ],
            )

        link = resolution.link
        base = dict(
            state=state, link_id=link.id,
            expected_product_code=link.supplier_product_code,
            expected_options=resolution.expected_options,
            units_per_sale=link.units_per_sale,
        )
        if state in (STATE_NEEDS_REVIEW, STATE_DISABLED):
            label = "재확인 필요" if state == STATE_NEEDS_REVIEW else "해제됨"
            return ReviewLinkEvaluation(
                detail=f"저장된 옵션 연결이 {label} 상태입니다.",
                blocked_reasons=[
                    f"저장된 공급처 옵션 연결이 {label} 상태라 자동으로 쓰지 않습니다"
                    + (f" — 사유: {link.status_reason}" if link.status_reason else "")
                    + ". 공급 옵션을 다시 확인해 연결을 저장하세요.",
                ],
                **base,
            )

        reasons: list[str] = []
        if task.channel_connection_id != link.purchase_connection_id:
            reasons.append(
                "이 작업에 배정된 매입 계정이 옵션 연결에 저장된 매입 계정과 "
                "다릅니다 — 계정이 바뀌면 옵션ID가 그대로 유효한지 알 수 없어 "
                "조용히 대체하지 않습니다. 연결을 다시 확인해 저장하세요.",
            )
        if requested_product_code != link.supplier_product_code:
            reasons.append(
                f"요청한 공급 상품({requested_product_code})이 저장된 연결"
                f"({link.supplier_product_code})과 다릅니다.",
            )
        expected = resolution.expected_options
        requested = sorted(
            (str(o.get("id")), o.get("qty")) for o in requested_options
        )
        wanted = sorted((o["id"], o["qty"]) for o in expected)
        if requested != wanted:
            reasons.append(
                "선택한 옵션·수량이 저장된 연결과 다릅니다 — 기대: "
                + ", ".join(f"옵션 {i} × {q}" for i, q in wanted)
                + f"(판매 수량 {resolution.sold_quantity} × 구성 수량 "
                f"{link.units_per_sale}).",
            )

        if product_lookup_ok:
            by_id = {str(o.option_id): o for o in product_options}
            live = by_id.get(link.supplier_option_id)
            if live is None:
                self._mark_needs_review(
                    link, "공급처에서 연결된 옵션이 사라졌거나 다른 상품으로 바뀜",
                )
                reasons.append(
                    "저장된 공급 옵션이 공급처에서 조회되지 않습니다 — 다른 옵션으로 "
                    "대체하지 않고 연결을 재확인 대기로 바꿨습니다.",
                )
            else:
                live_label = (live.label or "").strip()[:200]
                snapshot = link.supplier_option_name_snapshot
                if snapshot and live_label and live_label != snapshot:
                    self._mark_needs_review(
                        link, "공급 옵션명이 저장 당시와 달라짐(구성 변경 가능성)",
                    )
                    reasons.append(
                        f"공급 옵션명이 저장 당시('{snapshot}')와 달라졌습니다"
                        f"('{live_label}') — 구성이 바뀌었을 수 있어 연결을 재확인 "
                        "대기로 바꿨습니다.",
                    )
                elif live.in_stock is False:
                    reasons.append("연결된 공급 옵션이 지금 품절입니다.")
        else:
            reasons.append(
                "연결된 공급 옵션을 지금 확인하지 못해 연결을 신뢰하지 않습니다.",
            )

        state_now = self.db.get(SupplierOptionLink, link.id).status
        return ReviewLinkEvaluation(
            detail="저장된 연결로 발주 검토안을 만들었습니다(승인은 아닙니다).",
            blocked_reasons=reasons, **{**base, "state": state_now},
        )

    def _mark_needs_review(self, link: SupplierOptionLink, reason: str) -> None:

        result = self.db.execute(
            update(SupplierOptionLink)
            .where(SupplierOptionLink.id == link.id)
            .where(SupplierOptionLink.status == SupplierOptionLinkStatus.ACTIVE)
            .values(
                status=SupplierOptionLinkStatus.NEEDS_REVIEW,
                status_reason=reason[:200],
            ),
        )
        if result.rowcount == 1:
            write_audit_log(
                self.db, user_id=None, company_id=link.company_id,
                action="SUPPLIER_OPTION_LINK_NEEDS_REVIEW",
                entity="supplier_option_link", entity_id=str(link.id),
                description=reason[:200],
            )
        self.db.commit()
        self.db.expire_all()

    # ---------------- 승인 시점 서버 검증 ----------------

    def approval_block_reason(
        self, task: PurchaseTask, *, connection_id: int, product_code: str | None,
        options: list[dict] | None,
    ) -> str | None:
        """최종 승인 때 서버에서 다시 확인한다(화면 값을 믿지 않는다). 연결이
        저장돼 있으면 옵션 구성 생략도 허용하지 않는다."""

        resolution = self.resolve_for_task(task)
        if resolution.state in (
            STATE_UNAVAILABLE, STATE_NO_ORDER_ITEM, STATE_STORE_UNRESOLVED,
            STATE_NO_LINK,
        ):
            return None
        if resolution.state == STATE_IDENTIFIER_CONFLICT:
            return (
                "쿠팡 식별자(판매자 SKU·옵션번호)가 서로 맞지 않아 승인할 수 없습니다 — "
                + resolution.detail
            )
        link = resolution.link
        if resolution.state in (STATE_NEEDS_REVIEW, STATE_DISABLED):
            return (
                "저장된 공급처 옵션 연결이 재확인 필요/해제 상태라 승인할 수 "
                "없습니다 — 공급 옵션을 다시 확인해 연결을 저장하세요."
            )
        if connection_id != link.purchase_connection_id:
            return (
                "승인하려는 매입 계정이 옵션 연결에 저장된 계정과 다릅니다 — "
                "연결을 다시 확인해 저장하세요."
            )
        if product_code != link.supplier_product_code:
            return (
                f"승인하려는 공급 상품({product_code})이 저장된 연결"
                f"({link.supplier_product_code})과 다릅니다."
            )
        if options is None:
            return (
                "옵션 연결이 저장된 상품은 승인 시 옵션 구성을 반드시 함께 "
                "보내야 합니다(승인-발주 결합 검증을 생략할 수 없습니다)."
            )
        requested = sorted((str(o.get("id")), o.get("qty")) for o in options)
        wanted = sorted((o["id"], o["qty"]) for o in resolution.expected_options)
        if requested != wanted:
            return (
                "승인하려는 옵션·수량이 저장된 연결과 다릅니다 — 기대: "
                + ", ".join(f"옵션 {i} × {q}" for i, q in wanted) + "."
            )
        return None

    def clear_coupang_identifiers(
        self, company_id: int, link_id: int, *, actor_user_id: int,
        reason: str = "사용자가 쿠팡 식별자를 초기화함",
    ) -> SupplierOptionLink:
        """식별자 충돌 복구용 명시 동작 — 저장된 쿠팡 상품번호·옵션번호를 비우고 연결을
        재확인 필요로 내린다(공급 옵션 대응은 그대로). 다시 등록 결과를 조회해 부착하고
        연결을 다시 확인해 저장해야 ACTIVE로 돌아온다. 이미 승인·발주된 건은 동결된
        스냅샷이라 바뀌지 않는다."""

        link = (
            self.db.query(SupplierOptionLink)
            .filter(
                SupplierOptionLink.id == link_id,
                SupplierOptionLink.company_id == company_id,
            )
            .first()
        )
        if link is None:
            raise NotFoundException("옵션 연결을 찾을 수 없습니다.")
        link.coupang_seller_product_id = None
        link.coupang_vendor_item_id = None
        link.status = SupplierOptionLinkStatus.NEEDS_REVIEW
        link.status_reason = reason[:200]
        write_audit_log(
            self.db, user_id=actor_user_id, company_id=company_id,
            action="SUPPLIER_OPTION_LINK_IDENTIFIERS_CLEARED",
            entity="supplier_option_link", entity_id=str(link.id),
            description=reason[:200],
        )
        self.db.commit()
        self.db.refresh(link)
        return link

    # ---------------- 주문 단위 저장(서버가 판매 계정·SKU를 정한다) ----------------

    def save_link_for_task(
        self, task: PurchaseTask, *, supplier_product_code: str,
        supplier_option_id: str, units: int, confirmed_by: int,
        replace: bool = False,
    ) -> tuple[SupplierOptionLink, TaskLinkResolution]:
        """화면이 보낸 판매 계정·SKU를 믿지 않는다 — 이 작업의 주문 품목에서 서버가
        판매 계정과 판매자 SKU를 정하고, 매입 계정은 작업에 배정된 계정만 쓴다.
        판매자 SKU 없이 들어온 주문은 어떤 등록 옵션인지 알 수 없어 여기서 새 연결을
        만들지 않는다(상품 준비 화면의 등록 결과 확인 경로로 연결한다)."""

        resolution = self.resolve_for_task(task)
        state = resolution.state
        if state in (
            STATE_UNAVAILABLE, STATE_NO_ORDER_ITEM, STATE_STORE_UNRESOLVED,
            STATE_IDENTIFIER_CONFLICT,
        ):
            raise ConflictException(
                resolution.detail or "이 작업에는 옵션 연결을 저장할 수 없습니다.",
            )
        if task.channel_connection_id is None:
            raise BadRequestException("이 작업에 매입 계정이 배정되지 않았습니다.")

        existing = resolution.link
        if existing is None and resolution.seller_sku_state == "ABSENT_OR_SAME":
            raise BadRequestException(
                "판매자 SKU 없이 들어온 주문이라 어떤 등록 옵션인지 알 수 없어 여기서 "
                "연결을 만들 수 없습니다 — 상품 준비 화면에서 등록 결과의 쿠팡 옵션번호를 "
                "확인해 연결하세요.",
            )
        sku = existing.channel_sku if existing is not None else resolution.channel_sku
        # 재확인 필요·해제 상태를 다시 확인해 저장하는 것은 명시적 재확인이므로 교체를
        # 따로 요구하지 않는다. ACTIVE 연결을 다른 옵션으로 바꾸려면 replace가 필요하다.
        needs_explicit_replace = existing is not None and existing.status == STATE_ACTIVE
        link = self.save_link(
            task.company_id, store_connection_id=resolution.store_connection_id,
            channel_sku=sku, purchase_connection_id=task.channel_connection_id,
            components=[SupplierComponent(supplier_product_code, supplier_option_id, units)],
            confirmed_by=confirmed_by,
            replace=replace or (existing is not None and not needs_explicit_replace),
        )

        # 주문이 가져온 쿠팡 옵션번호는 쿠팡 자체 데이터로 SKU와의 짝을 보여 주는
        # 확인된 근거다 — 아직 비어 있을 때만, 그 번호가 다른 연결에 없을 때만 채운다.
        vendor = resolution.vendor_item_id
        if (
            vendor and resolution.seller_sku_state == "PRESENT"
            and link.coupang_vendor_item_id is None
            and self.get_link_by_vendor_item(
                task.company_id, resolution.store_connection_id, vendor,
            ) is None
        ):
            link.coupang_vendor_item_id = vendor
            write_audit_log(
                self.db, user_id=confirmed_by, company_id=task.company_id,
                action="SUPPLIER_OPTION_LINK_VENDOR_ITEM_FROM_ORDER",
                entity="supplier_option_link", entity_id=str(link.id),
                description="주문 원본의 쿠팡 옵션번호를 확인된 식별자로 저장",
            )
            self.db.commit()
            self.db.refresh(link)
        return link, self.resolve_for_task(task)

    # ---------------- 등록 결과 식별자 부착 ----------------

    def attach_coupang_identifiers(
        self, company_id: int, *, store_connection_id: int,
        seller_product_id: str, items: list[dict],
        expected_skus: list[str] | None = None, actor_user_id: int | None = None,
    ) -> dict:
        """쿠팡 상세조회에서 확인된 상품·옵션 식별자를 **판매자 SKU로만** 기존 연결에
        부착한다(응답 순서·옵션명 미사용). 생성 응답에 모든 옵션번호가 있다고
        가정하지 않는다 — 번호가 아직 없는 옵션은 "미발급"으로 남기고, 부분 성공·
        충돌·연결 없음은 옵션별로 구분해 돌려주며 어느 하나라도 있으면 complete=False
        (준비 미완료)다. 이미 다른 값이 있으면 덮어쓰지 않고 그 연결을 재확인 필요로
        내린다. 새 연결은 만들지 않는다(등록 성공과 연결 저장 성공은 별개 기록).

        items: [{"externalVendorSku": str|None, "vendorItemId": str|None}]
        expected_skus: 서버가 확정한 판매 옵션 SKU 목록(없으면 응답의 SKU만 대상)."""

        if not self.is_store_available(self.db):
            raise ConflictException("옵션 연결 저장소가 아직 준비되지 않았습니다.")
        seller_product_id = str(seller_product_id or "").strip()
        if not seller_product_id:
            raise BadRequestException("쿠팡 상품 ID(sellerProductId)가 필요합니다.")

        by_sku: dict[str, str | None] = {}
        seen_vendor: dict[str, str] = {}
        skipped_without_sku = 0
        for entry in items:
            raw_sku = entry.get("externalVendorSku")
            if not isinstance(raw_sku, str) or raw_sku == "":
                skipped_without_sku += 1
                continue
            vendor = str(entry.get("vendorItemId") or "").strip() or None
            if raw_sku in by_sku:
                return self._attach_result(
                    "AMBIGUOUS_RESPONSE", {}, skipped_without_sku, [],
                    detail=f"조회 결과에 SKU {raw_sku}가 중복돼 있어 옵션을 구분할 수 없습니다.",
                )
            if vendor is not None:
                if vendor in seen_vendor:
                    return self._attach_result(
                        "AMBIGUOUS_RESPONSE", {}, skipped_without_sku, [],
                        detail=(
                            f"조회 결과에서 옵션번호 {vendor}가 SKU {seen_vendor[vendor]}와 "
                            f"{raw_sku}에 중복돼 옵션을 구분할 수 없습니다."
                        ),
                    )
                seen_vendor[vendor] = raw_sku
            by_sku[raw_sku] = vendor

        targets = list(expected_skus) if expected_skus is not None else list(by_sku)
        outcomes: dict[str, str] = {}
        for sku in targets:
            if sku not in by_sku:
                outcomes[sku] = "MISSING_IN_RESPONSE"
                continue
            vendor = by_sku[sku]
            link = self.get_link(company_id, store_connection_id, sku)
            if link is None:
                outcomes[sku] = "NO_LINK"
                continue
            if link.coupang_seller_product_id not in (None, seller_product_id):
                self._flag_conflict(link, "쿠팡 상품번호가 저장된 값과 다름")
                outcomes[sku] = "CONFLICT"
                continue
            if vendor is None:
                # 번호가 없다는 것은 "정보 없음"이지 "다른 값"이 아니다 — 충돌로 보지 않는다.
                # 이미 확인된 번호가 있으면 그대로 유지하고(이번 조회로는 확인되지 않았다고만
                # 보고), 없으면 아직 발급 전(승인 전)으로 본다. 어느 쪽이든 완료가 아니다.
                if link.coupang_vendor_item_id is not None:
                    outcomes[sku] = "ID_NOT_RETURNED"
                    continue
                link.coupang_seller_product_id = seller_product_id
                outcomes[sku] = "ID_NOT_ISSUED"
                continue
            if link.coupang_vendor_item_id not in (None, vendor):
                self._flag_conflict(link, "쿠팡 옵션번호가 저장된 값과 다름")
                outcomes[sku] = "CONFLICT"
                continue
            other = self.get_link_by_vendor_item(company_id, store_connection_id, vendor)
            if other is not None and other.id != link.id:
                self._flag_conflict(link, "같은 쿠팡 옵션번호가 다른 연결에 이미 있음")
                self._flag_conflict(other, "같은 쿠팡 옵션번호가 다른 연결에서도 조회됨")
                outcomes[sku] = "CONFLICT"
                continue
            already = (
                link.coupang_vendor_item_id == vendor
                and link.coupang_seller_product_id == seller_product_id
            )
            link.coupang_seller_product_id = seller_product_id
            link.coupang_vendor_item_id = vendor
            outcomes[sku] = "ALREADY_ATTACHED" if already else "ATTACHED"

        out_of_scope = (
            sorted(set(by_sku) - set(targets)) if expected_skus is not None else []
        )
        counts = {
            k: sum(1 for v in outcomes.values() if v == k)
            for k in sorted(set(outcomes.values()))
        }
        write_audit_log(
            self.db, user_id=actor_user_id, company_id=company_id,
            action="SUPPLIER_OPTION_LINK_COUPANG_IDS_ATTACHED",
            entity="supplier_option_link", entity_id=seller_product_id,
            description=f"옵션 식별자 부착 결과 {counts} · 범위 밖 {len(out_of_scope)}건",
        )
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ConflictException(
                "쿠팡 옵션번호가 다른 연결과 겹쳐 저장하지 않았습니다 — 다시 확인하세요.",
            ) from None
        complete = bool(targets) and all(
            v in ("ATTACHED", "ALREADY_ATTACHED") for v in outcomes.values()
        )
        return self._attach_result(
            "COMPLETE" if complete else "INCOMPLETE", outcomes, skipped_without_sku,
            out_of_scope,
        )

    @staticmethod
    def _attach_result(state, outcomes, skipped_without_sku, out_of_scope, *, detail=""):
        return {
            "state": state, "complete": state == "COMPLETE", "outcomes": outcomes,
            "response_items_without_sku": skipped_without_sku,
            "response_skus_outside_scope": out_of_scope, "detail": detail,
        }

    def _flag_conflict(self, link: SupplierOptionLink, reason: str) -> None:
        """식별자 충돌 — 덮어쓰지 않고 그 연결을 재확인 필요로 내려 발주 검토·승인을 막는다."""

        if link.status == SupplierOptionLinkStatus.ACTIVE:
            link.status = SupplierOptionLinkStatus.NEEDS_REVIEW
        link.status_reason = f"쿠팡 식별자 충돌: {reason}"[:200]
        write_audit_log(
            self.db, user_id=None, company_id=link.company_id,
            action="SUPPLIER_OPTION_LINK_IDENTIFIER_CONFLICT",
            entity="supplier_option_link", entity_id=str(link.id),
            description=reason[:200],
        )

    # ---------------- 준비 상태 ----------------

    def readiness(
        self, company_id: int, *, store_connection_id: int | None, skus: list[str],
        scope_confirmed: bool = False,
    ) -> dict:
        """판매하기로 선택한 옵션 각각의 연결·쿠팡 식별자 상태를 본다.

        `scope_confirmed`는 서버가 옵션 목록을 직접 확정했을 때만 True다(등록 제출의
        동결된 옵션 목록). 클라이언트가 보낸 SKU 목록(기본값)은 "선택 범위 확인"일
        뿐이라 all_ready가 절대 True가 되지 않는다 — 일부만 보내 전체 준비 완료로
        오인시키지 못하게 한다. 옵션 하나라도 연결 누락·재확인 필요·해제·쿠팡 옵션번호
        미확인이면 all_ready=False다."""

        scope = "SERVER_CONFIRMED" if scope_confirmed else "CLIENT_SUPPLIED"
        if not self.is_store_available(self.db):
            return {
                "scope": scope, "total": len(skus), "per_option": [],
                "selected_ready": False, "all_ready": False,
                "ready": False, "reason": "STORE_UNAVAILABLE", "per_sku": {},
            }
        per_option = []
        for sku in skus:
            link = (
                self.get_link(company_id, store_connection_id, sku)
                if store_connection_id is not None else None
            )
            if link is None:
                per_option.append({
                    "sku": sku, "link_state": "MISSING", "ids_state": "NONE",
                    "link_id": None, "supplier_product_code": None,
                    "supplier_option_id": None, "supplier_option_name": None,
                    "units_per_sale": None, "status_reason": None,
                })
                continue
            per_option.append({
                "sku": sku, "link_state": link.status,
                "ids_state": "CONFIRMED" if link.coupang_vendor_item_id else "UNCONFIRMED",
                "link_id": link.id, "supplier_product_code": link.supplier_product_code,
                "supplier_option_id": link.supplier_option_id,
                "supplier_option_name": link.supplier_option_name_snapshot,
                "units_per_sale": link.units_per_sale, "status_reason": link.status_reason,
            })
        links_ready = bool(per_option) and all(
            o["link_state"] == SupplierOptionLinkStatus.ACTIVE for o in per_option
        )
        ids_ready = bool(per_option) and all(o["ids_state"] == "CONFIRMED" for o in per_option)
        all_ready = scope_confirmed and links_ready and ids_ready
        if all_ready:
            reason = None
        elif not per_option:
            reason = "NO_OPTIONS"
        elif not scope_confirmed:
            reason = "SCOPE_UNCONFIRMED"
        elif any(o["link_state"] == "MISSING" for o in per_option):
            reason = "LINK_MISSING"
        elif not links_ready:
            reason = "LINK_NEEDS_REVIEW"
        else:
            reason = "IDS_UNCONFIRMED"
        return {
            "scope": scope, "total": len(per_option), "per_option": per_option,
            "selected_ready": links_ready and ids_ready, "all_ready": all_ready,
            # 이전 필드 호환 — 전체 준비 완료만 ready로 본다.
            "ready": all_ready, "reason": reason,
            "per_sku": {o["sku"]: o["link_state"] for o in per_option},
        }
