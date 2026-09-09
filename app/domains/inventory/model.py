"""
=========================================================
Homez OS

File : app/domains/inventory/model.py

Inventory Model — V7 Gate 3(2026-08-15) 처음부터 재설계.

이전 파일(products/suppliers FK를 참조하는 pre-pivot 구조,
main.py 미마운트, 참조하는 곳은 마운트 해제된 레거시 order/product
뿐 — Phase C 독립 재감사·`docs/HOMEZ_V7_INVENTORY_PLAN.md`에서 확인)를
V7 설계 관점에서 완전히 대체한다. company_id를 처음부터 모든 테이블에
포함한다.

주의(2026-08-15 실측 발견, Whitelist 밖이라 원인 파일은 건드리지
않음): 이 파일 맨 아래 `Inventory`(pre-pivot 원본 클래스, 파일 하단에
그대로 보존)는 새 설계와 무관하지만 제거할 수 없다 — main.py가
무조건 import하는 `app/domains/product`(마운트는 안 됐지만 import
자체는 실행됨, Whitelist 밖 WIP 파일)의 `service.py`/`policy.py`가
`from app.domains.inventory.model import Inventory`를 직접 import하고,
`app/domains/product/model.py`의 `Product.inventories` relationship과
`app/domains/supplier/model.py`의 `Supplier.inventories` relationship이
`back_populates`로 이 클래스를 참조해 `configure_mappers()` 자체가
이 클래스 없이는 실패한다. 이 클래스를 지우면 app.main 전체가 기동
불가 상태가 된다 — 이번 Gate에서 새로 만든 코드는 전부 이 클래스를
참조하지 않는다(완전히 별개, 순수 하위호환 목적).

설계 근거(계획 문서 대비 편차, 상세는
gate3_inventory_core_result.md 1절):
  - `available_qty`/`reserved_qty`를 매번 이벤트 합산으로 재계산하지
    않고, 이 저장소가 이미 검증한 FundingAccount(현재상태) +
    FundingLedger(append-only 이력) 2계층 패턴을 그대로 재사용한다.
    InventorySku가 현재상태(mutate), InventoryLedgerEvent가 append-only
    이력(quantity_delta + available_after/reserved_after 스냅샷)이다.
  - 예약은 FundingHold와 동일한 철학의 상태 머신
    (RESERVED→RELEASED|CONSUMED, 재전이 불가)을 갖는 별도 엔티티
    InventoryReservation으로 분리한다 — "이미 반환된 예약을 다시
    반환/커�밋할 수 없다"는 요구사항 5를 조건부 UPDATE 하나로
    강제하기 위함(FundingHold.status 전이와 동일 기법).
  - 채널 매핑은 새 채널/계정 개념을 만들지 않고 기존
    marketplace_listing.MarketplaceListing(이미 company_id 스코프
    완비, 채널별 등록/승인 흐름 보유)에 논리 참조만 추가한다 —
    중복 구현 금지 지시를 따른다.

모든 cross-domain 참조는 이 저장소 전역 관례대로 실제 SQLAlchemy
ForeignKey가 아닌 "논리 참조"(정수 컬럼 + 주석)다(company_id 참조가
companies.id를 FK로 걸지 않는 것과 동일 — Modular Monolith 안에서도
Domain 간 강한 결합을 피하는 기존 컨벤션).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import text

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base


class InventorySku(Base):
    """
    회사 스코프 SKU(옵션 단위) — ProductCandidate 승인 후보 하나가
    여러 옵션(색상/사이즈 등)을 가질 수 있다는 요구사항의 "재고 관점"
    표현이다. 채널별 등록/승인 자체는 marketplace_listing/
    listing_package가 이미 담당하므로 여기서 다시 만들지 않는다 —
    이 테이블은 오직 "이 옵션의 재고 수량"만 책임진다.

    available_qty/reserved_qty는 현재상태(mutate, FundingAccount와
    동일 철학) — 소스오브트루스는 InventoryLedgerEvent(append-only)지만
    조회 성능을 위해 이 두 컬럼을 매 원자적 연산마다 함께 갱신한다.
    """

    __tablename__ = "inventory_skus"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "sku_code",
            name="uq_inventory_skus_company_sku_code",
        ),
        UniqueConstraint(
            "company_id",
            "product_candidate_id",
            "option_label",
            name="uq_inventory_skus_company_candidate_option",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (companies.id) — 격리 필수.
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (product_candidates.id) — 승인된 판매상품(계획 문서 2절
    # "product_candidate_id 또는 향후 정식 SKU 식별자" 중 전자를 채택,
    # 이 테이블 자체가 그 "정식 SKU 식별자"다).
    product_candidate_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 회사 스코프 유일 SKU 코드(운영자가 직접 부여하는 재고관리 코드).
    sku_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # 옵션 표현(예: "레드 / L") — 자유 문자열. 색상/사이즈 등 구조화된
    # 축이 여러 개여도 이번 Gate 범위에서는 표시용 단일 문자열로 충분
    # (구조화가 필요해지면 후속 Gate에서 JSON 컬럼으로 확장 가능,
    # 이번에 과설계하지 않는다).
    option_label: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="기본",
    )

    available_qty: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    reserved_qty: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # 안전재고선(회사별 설정 가능, 계획 문서 2절) — 조회/경고용, 이
    # 값 자체가 가용재고 차단 기준은 아니다(0 미만만 차단, 안전재고는
    # UI/운영 경고 신호).
    safety_stock: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class InventoryReservation(Base):
    """
    주문 예약 상태 머신 — RESERVED에서만 RELEASED 또는 CONSUMED로
    전이 가능(재전이·역행 불가). FundingHold(HELD/COMMITTED/RELEASED)와
    동일한 철학. 실제 주문 도메인은 이번 Gate 범위가 아니므로
    reference_type/reference_id는 미래의 order 연결을 위해 nullable로
    남겨둔다(계획 문서 4절 — 주문 도메인이 실제로 생기면 이 컬럼으로
    연결).
    """

    __tablename__ = "inventory_reservations"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_inventory_reservations_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (inventory_skus.id)
    inventory_sku_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # RESERVED / RELEASED / CONSUMED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="RESERVED",
        index=True,
    )

    # 미래 주문 도메인 연결용(V7 Gate 4) — 이번 Gate에서는 항상 NULL일
    # 수 있다.
    reference_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    reference_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    triggered_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class InventoryLedgerEvent(Base):
    """
    재고 증감 append-only 원장(계획 문서 2/3절). 과거 행은 절대
    UPDATE/DELETE하지 않는다 — 이 저장소 전역 append-only 관례
    (FundingLedger, MarketplaceListingStatusEvent, AuditLog와 동일).

    idempotency_key는 예약(RESERVED/RELEASED/CONSUMED)에는 필요 없다
    (InventoryReservation 자체가 idempotency_key를 갖고 상태 머신으로
    이중 처리를 막는다) — RESTOCKED/ADJUSTED/CHANNEL_SYNC처럼 예약을
    거치지 않는 이벤트만 자체 idempotency_key를 쓴다. 그래서 UNIQUE는
    idempotency_key가 NULL이 아닌 행에만 적용되는 부분 유일 인덱스다
    (FundingLedger.uq_funding_ledger_settlement_type과 동일 기법).
    """

    __tablename__ = "inventory_ledger_events"
    __table_args__ = (
        Index(
            "uq_inventory_ledger_events_idempotency",
            "company_id",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key IS NOT NULL"),
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (inventory_skus.id)
    inventory_sku_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # RESERVED / RELEASED / CONSUMED / RESTOCKED / ADJUSTED / CHANNEL_SYNC
    event_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    # 부호 포함 — RESERVED/CONSUMED는 available 기준 음수, RESTOCKED는
    # 양수, ADJUSTED는 입력값 그대로(양/음 모두 가능), CHANNEL_SYNC는
    # 항상 0(재고를 바꾸지 않고 외부에 반영만 한다).
    quantity_delta: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 이벤트 적용 후 스냅샷(조회 성능용, 소스오브트루스는 아니다).
    available_after: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    reserved_after: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    safety_stock_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # nullable — 채널별 재고가 아닌 순수 재고 변경(RESERVED 등)은 NULL.
    channel_code: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # 논리 참조 (inventory_reservations.id) — RESERVED/RELEASED/CONSUMED만.
    reservation_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # RESTOCKED/ADJUSTED/CHANNEL_SYNC 전용 멱등 키(예약 경로는
    # InventoryReservation.idempotency_key가 이미 담당).
    idempotency_key: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    # ADJUSTED일 때 서비스 레벨에서 필수(요구사항 7).
    reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    triggered_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class InventoryChannelMapping(Base):
    """
    SKU(옵션) × 판매채널 매핑 — "재고 관점"의 채널별 SKU만 추가한다.
    실제 채널 등록/승인 흐름(제출·자격·이행방식 선택)은
    marketplace_listing.MarketplaceListing이 이미 담당하므로 여기서는
    그 Listing에 대한 논리 참조 + 채널 고유 SKU 코드 + 동기화 상태만
    보관한다(중복 구현 금지 지시 준수).
    """

    __tablename__ = "inventory_channel_mappings"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "inventory_sku_id",
            "marketplace_listing_id",
            name="uq_inventory_channel_mappings_sku_listing",
        ),
        UniqueConstraint(
            "company_id",
            "channel_code",
            "channel_sku",
            name="uq_inventory_channel_mappings_company_channel_sku",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (inventory_skus.id)
    inventory_sku_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (marketplace_listings.id) — 기존 채널 등록/승인 흐름 재사용.
    marketplace_listing_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # marketplace_channels.code와 동일 값(비정규화, 조회/제약 편의).
    channel_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    # 채널이 실제로 사용하는 외부 SKU 코드(예: 쿠팡 externalVendorSku).
    channel_sku: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # PENDING / SYNCED / FAILED
    last_sync_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    last_sync_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


# =========================================================
# LEGACY — pre-pivot 호환 유지 전용(2026-08-15 V7 Gate 3에서 발견,
# 원인 파일은 Whitelist 밖이라 수정하지 않음. 위 파일 상단 주석 참고).
# 새 V7 설계(InventorySku/InventoryReservation/InventoryLedgerEvent/
# InventoryChannelMapping)는 이 클래스를 참조하지 않는다. 필드/관계는
# 기존 원본과 완전히 동일하게 보존한다(동작 변경 없음, 순수 import·
# configure_mappers() 호환 목적).
# =========================================================

from sqlalchemy import Column
from sqlalchemy import ForeignKey


class Inventory(Base):

    __tablename__ = "inventories"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    product_id = Column(
        Integer,
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    supplier_id = Column(
        Integer,
        ForeignKey(
            "suppliers.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    sku = Column(
        String(100),
        nullable=True,
        index=True,
    )

    quantity = Column(
        Integer,
        nullable=False,
        default=0,
    )

    min_quantity = Column(
        Integer,
        nullable=False,
        default=0,
    )

    safety_quantity = Column(
        Integer,
        nullable=False,
        default=0,
    )

    reserved_quantity = Column(
        Integer,
        nullable=False,
        default=0,
    )

    available_quantity = Column(
        Integer,
        nullable=False,
        default=0,
    )

    status = Column(
        String(50),
        nullable=False,
        default="AVAILABLE",
        index=True,
    )

    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    inbound_count = Column(
        Integer,
        nullable=False,
        default=0,
    )

    outbound_count = Column(
        Integer,
        nullable=False,
        default=0,
    )

    low_stock_alert = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    forecast_quantity = Column(
        Integer,
        nullable=False,
        default=0,
    )

    ai_score = Column(
        Integer,
        nullable=False,
        default=0,
    )

    note = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=True,
    )

    deleted_at = Column(
        DateTime,
        nullable=True,
    )

    product = relationship(
        "Product",
        back_populates="inventories",
    )

    supplier = relationship(
        "Supplier",
        back_populates="inventories",
    )

    def __repr__(self) -> str:

        return (
            f"Inventory("
            f"id={self.id}, "
            f"product_id={self.product_id}, "
            f"quantity={self.quantity}"
            f")"
        )


# 2026-08-15 실측 전체 회귀(25청크)로 발견한 결함 수정: 위 `Inventory`의
# `relationship("Product", ...)`/`relationship("Supplier", ...)`는
# SQLAlchemy 공유 Base 레지스트리에서 이름을 문자열로 늦게(첫 mapper
# configure 시점에) 해석한다 — 이 모듈이 import되는 프로세스 안에
# Product/Category/Brand/Supplier도 함께 import돼 등록돼 있어야 그
# 해석이 성공한다. 청크 분할 전체 회귀에서 그 4개를 아무도 import하지
# 않는 5개 파일 묶음(tests/test_funding_company_scope_hardening.py 등)
# 과 이 모듈을 import하는 tests/test_gate3_inventory_migration.py가
# 같은 프로세스에 우연히 묶이면서 20개 테스트가 mapper 초기화 실패로
# 무더기로 깨졌다 — "테스트 파일마다 import를 잊지 않는다"에 기대는
# 방식은 어떤 조합의 테스트가 같은 프로세스에 묶이느냐에 따라 다시
# 깨질 수 있는 근본적으로 불안정한 해법이다(운영에서도 어떤 요청이
# 먼저 들어와 mapper를 처음 configure하느냐에 따라 재현될 수 있는
# 잠재적 결함이었다). 이 모듈(model.py) 자체가 항상 함께 등록하도록
# 여기서 직접 import하는 것이 유일하게 안정적인 수정이다.
#
# 반드시 `Inventory` 클래스 정의가 끝난 "뒤"에 이 import를 둔다 —
# `app.domains.product.__init__`이 `app.domains.product.service`를
# 거쳐 다시 `from app.domains.inventory.model import Inventory`를
# import하므로(순환 참조), 이 4개 import를 클래스 정의 "앞"에 두면
# 아직 `Inventory`가 이 모듈 네임스페이스에 없는 상태에서 순환
# import가 걸려 `ImportError: cannot import name 'Inventory' from
# partially initialized module`로 깨진다(직접 재현·확인). 클래스
# 정의 뒤에 두면 그 시점엔 이미 `Inventory`가 이 모듈에 바인딩돼
# 있어 순환 import가 안전하게 풀린다.
from app.domains.brand.model import Brand  # noqa: F401,E402
from app.domains.category.model import Category  # noqa: F401,E402
from app.domains.product.model import Product  # noqa: F401,E402
from app.domains.supplier.model import Supplier  # noqa: F401,E402


__all__ = [
    "InventorySku",
    "InventoryReservation",
    "InventoryLedgerEvent",
    "InventoryChannelMapping",
    "Inventory",
]
