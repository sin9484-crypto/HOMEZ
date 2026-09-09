"""
=========================================================
Homez OS

File : app/domains/retail_purchase/provider.py

RetailPurchaseProvider 경계(Gate RP-1, 2026-08-22) — app/domains/
purchase/supplier_order_providers.py와 동일한 철학: 실제 계약된
Provider가 없으면 place_order()는 항상 명시적으로 차단한다
(LIVE_INPUT_REQUIRED). 소비자 쇼핑몰 로그인 자동화·화면 조작 결제는
이 계층에 존재하지 않는다 — capability를 선언하지 못하는 기능은
화면 자동화로 몰래 보완하지 않고 NOT_SUPPORTED로 정직하게 표시한다.
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domains.retail_purchase.constants import ProviderCapability
from app.domains.retail_purchase.constants import ProviderErrorCode


class RetailPurchaseProviderError(Exception):
    """Provider 자체가 요청을 처리할 수 없을 때. Secret·개인정보를
    메시지에 담지 않는다.

    2026-08-22 14차 지시(작업 5) — 모든 Provider 오류는 표준
    ProviderErrorCode 하나로 분류되고(error_code), 재시도 가능
    여부(retryable)·Rate Limit 대기시간(retry_after_seconds)·요청
    추적용 상관ID(correlation_id)를 함께 들고 다닌다. 자유 문자열
    오류코드를 새로 만들지 않는다."""

    default_error_code: str = ProviderErrorCode.PURCHASE_FAILED
    default_retryable: bool = False

    def __init__(
        self, message: str, *, error_code: str | None = None,
        retryable: bool | None = None, retry_after_seconds: int | None = None,
        correlation_id: str | None = None, error_detail: str | None = None,
    ):

        super().__init__(message)
        self.error_code = error_code or self.default_error_code
        self.retryable = (
            self.default_retryable if retryable is None else retryable
        )
        self.retry_after_seconds = retry_after_seconds
        self.correlation_id = correlation_id
        self.error_detail = error_detail


class NotSupportedByProviderError(RetailPurchaseProviderError):
    """Provider가 이 capability를 애초에 선언하지 않았을 때 — 화면
    자동화로 몰래 보완하지 않고 그대로 차단한다."""

    default_error_code = ProviderErrorCode.NOT_SUPPORTED
    default_retryable = False


class LiveInputRequiredError(RetailPurchaseProviderError):
    """실제 계약·Credential이 아직 연결되지 않아 place_order() 등
    실행 계열 메서드를 진행할 수 없을 때(Gate 15 Live Gate 대상)."""

    default_error_code = ProviderErrorCode.PROVIDER_NOT_CONNECTED
    default_retryable = False


class CredentialRequiredError(RetailPurchaseProviderError):
    """계약은 있으나 이 회사의 Credential(API Key 등)이 아직
    등록되지 않았을 때 — 실 Provider 구현이 사용한다(Fake는 항상
    연결된 것으로 취급하므로 발생시키지 않는다)."""

    default_error_code = ProviderErrorCode.CREDENTIAL_REQUIRED
    default_retryable = False


class ReauthRequiredError(RetailPurchaseProviderError):
    """Credential은 있으나 만료·거부돼 재인증이 필요할 때."""

    default_error_code = ProviderErrorCode.REAUTH_REQUIRED
    default_retryable = False


class RateLimitedError(RetailPurchaseProviderError):
    """Provider가 호출 빈도 제한을 반환했을 때 — retry_after_seconds를
    반드시 채워서 던진다(호출자가 그 시간만큼 대기 후 재시도할 근거로
    쓴다)."""

    default_error_code = ProviderErrorCode.RATE_LIMITED
    default_retryable = True


class ProviderUnavailableError(RetailPurchaseProviderError):
    """Provider 쪽 장애(5xx, 타임아웃 등) — 결제가 실행되지 않았음이
    확실할 때만 이 오류를 쓴다(결제 여부가 불확실하면 대신
    PlaceOrderResult.status="UNCERTAIN"을 반환해야 한다 — 예외로
    던지면 결과가 어느 쪽인지 호출자가 구분할 수 없다)."""

    default_error_code = ProviderErrorCode.PROVIDER_UNAVAILABLE
    default_retryable = True


class ProductNotFoundError(RetailPurchaseProviderError):
    """search_product/get_product 대상이 Provider 쪽에 존재하지
    않을 때."""

    default_error_code = ProviderErrorCode.PRODUCT_NOT_FOUND
    default_retryable = False


class ProductMismatchError(RetailPurchaseProviderError):
    """Provider 자체가(HOMEZ의 동일상품 판정과 별개로) 요청한 옵션·
    구성과 실제 상품이 다르다고 응답할 때."""

    default_error_code = ProviderErrorCode.PRODUCT_MISMATCH
    default_retryable = False


# --------------------------------------------------
# 요청/응답 데이터 계약
# --------------------------------------------------

@dataclass(frozen=True)
class ProductSearchQuery:

    keyword: str
    brand: str | None = None
    model_name: str | None = None
    gtin: str | None = None
    limit: int = 20


@dataclass(frozen=True)
class ProductSearchResultItem:

    external_product_id: str
    product_url: str
    title: str
    brand: str | None
    manufacturer: str | None
    model_name: str | None
    gtin: str | None
    seller_name: str | None
    seller_trust_score: float | None  # 0.0~1.0, 확인 불가면 None
    list_price: Decimal | None
    in_stock: bool | None
    is_authorized_dealer: bool | None


@dataclass(frozen=True)
class ProductSearchResult:

    query: ProductSearchQuery
    items: tuple[ProductSearchResultItem, ...]
    evaluated_at: datetime


@dataclass(frozen=True)
class ProductOption:

    option_id: str
    label: str
    color_or_scent: str | None
    capacity: str | None
    additional_price: Decimal


@dataclass(frozen=True)
class ProductDetail:

    external_product_id: str
    product_url: str
    title: str
    brand: str | None
    manufacturer: str | None
    model_name: str | None
    gtin: str | None
    options: tuple[ProductOption, ...]
    components: tuple[str, ...]
    is_authorized_dealer: bool | None
    certification_info: str | None
    return_policy_summary: str | None
    return_allowed: bool | None
    seller_name: str | None
    seller_trust_score: float | None
    fetched_at: datetime


@dataclass(frozen=True)
class IdentityVerificationResult:
    """구매 계정/Provider 연결 자체가 유효한지(로그인 자동화가
    아니라 API 키·계약 상태 확인)."""

    verified: bool
    provider_code: str
    external_account_reference: str | None
    status_detail: str


@dataclass(frozen=True)
class CheckoutLineItem:

    external_product_id: str
    option_id: str | None
    quantity: int


@dataclass(frozen=True)
class CheckoutCalculation:

    items: tuple[CheckoutLineItem, ...]
    item_total: Decimal
    shipping_fee: Decimal
    total_amount: Decimal
    estimated_delivery_days: int | None
    calculated_at: datetime


@dataclass(frozen=True)
class PurchaseQuote:
    """실 결제 직전 견적 — place_order 직전에 반드시 재검증에 쓰인다."""

    quote_id: str
    items: tuple[CheckoutLineItem, ...]
    total_amount: Decimal
    shipping_fee: Decimal
    in_stock: bool
    expires_at: datetime
    quoted_at: datetime


@dataclass(frozen=True)
class PurchaseReservation:
    """Provider 측 재고/가격 예약(있는 경우) — 모든 Provider가
    지원하지는 않는다(PURCHASE_QUOTE capability로만 선언)."""

    reservation_id: str
    quote_id: str
    expires_at: datetime


@dataclass(frozen=True)
class PlaceOrderRequest:

    idempotency_key: str
    quote_id: str
    shipping_address_reference: str
    max_total_amount: Decimal  # 재검증 상한 — 견적보다 비싸지면 차단
    correlation_id: str | None = None  # 요청 추적용 — 응답에 그대로 반향


@dataclass(frozen=True)
class PlaceOrderResult:

    status: str  # ORDERED / FAILED / UNCERTAIN
    external_order_id: str | None
    external_order_number: str | None
    actual_amount: Decimal | None
    error_code: str | None  # ProviderErrorCode.ALL 중 하나(성공 시 None)
    retryable: bool
    idempotency_key: str | None = None  # 요청 idempotency_key 반향
    correlation_id: str | None = None  # 요청 correlation_id 반향
    error_detail: str | None = None  # 오류 세부(디버깅용, 표준 코드는 error_code)
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class OrderStatusResult:

    external_order_id: str
    status: str
    tracking_company: str | None
    tracking_number: str | None


@dataclass(frozen=True)
class CancelResult:

    external_order_id: str
    status: str
    cancelled: bool
    error_code: str | None


@dataclass(frozen=True)
class TrackingResult:

    external_order_id: str
    tracking_company: str | None
    tracking_number: str | None
    status: str | None


@dataclass(frozen=True)
class BalanceOrCredit:

    provider_code: str
    external_account_reference: str
    available_balance: Decimal | None
    credit_limit: Decimal | None
    as_of: datetime


# --------------------------------------------------
# Provider 인터페이스
# --------------------------------------------------

class RetailPurchaseProvider(ABC):

    code: str = "ABSTRACT"
    capabilities: frozenset[str] = frozenset()

    def has_capability(self, capability: str) -> bool:

        return capability in self.capabilities

    def _require_capability(self, capability: str) -> None:

        if not self.has_capability(capability):
            raise NotSupportedByProviderError(
                f"{self.code} Provider는 {capability}를 지원하지 "
                "않습니다(NOT_SUPPORTED) — 화면 자동화로 보완하지 "
                "않습니다.",
            )

    @abstractmethod
    def search_product(self, query: ProductSearchQuery) -> ProductSearchResult:
        ...

    @abstractmethod
    def get_product(self, external_product_id: str) -> ProductDetail:
        ...

    @abstractmethod
    def verify_identity(self) -> IdentityVerificationResult:
        ...

    @abstractmethod
    def calculate_checkout(
        self, items: tuple[CheckoutLineItem, ...],
    ) -> CheckoutCalculation:
        ...

    @abstractmethod
    def create_purchase_quote(
        self, items: tuple[CheckoutLineItem, ...],
    ) -> PurchaseQuote:
        ...

    @abstractmethod
    def reserve_purchase(self, quote_id: str) -> PurchaseReservation:
        ...

    @abstractmethod
    def place_order(self, request: PlaceOrderRequest) -> PlaceOrderResult:
        ...

    @abstractmethod
    def get_order(self, external_order_id: str) -> OrderStatusResult:
        ...

    @abstractmethod
    def cancel_order(self, external_order_id: str) -> CancelResult:
        ...

    @abstractmethod
    def get_tracking(self, external_order_id: str) -> TrackingResult:
        ...

    @abstractmethod
    def get_balance_or_credit(self) -> BalanceOrCredit:
        ...


# --------------------------------------------------
# FakeRetailPurchaseProvider — 테스트 전용, 네트워크 호출 없음,
# 완전 결정론적.
# --------------------------------------------------

class FakeRetailPurchaseScenario:

    NORMAL = "NORMAL"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PRICE_INCREASED = "PRICE_INCREASED"
    QUOTE_EXPIRED = "QUOTE_EXPIRED"
    ORDER_FAILED = "ORDER_FAILED"
    ORDER_UNCERTAIN = "ORDER_UNCERTAIN"

    ALL = (
        NORMAL, OUT_OF_STOCK, PRICE_INCREASED, QUOTE_EXPIRED,
        ORDER_FAILED, ORDER_UNCERTAIN,
    )


class FakeRetailPurchaseProvider(RetailPurchaseProvider):
    """실제 네트워크 호출 없이 결정론적으로 동작한다 — idempotency_key
    로 재요청하면 항상 같은 결과를 낸다. Sandbox/테스트 전용이며
    UI에는 항상 TEST_ONLY로 표시된다.

    견적/주문 상태는 클래스 레벨(프로세스 전역)로 공유한다 — 실제
    Provider라면 이 상태는 로컬 인스턴스가 아니라 원격 서버가
    기억한다(get_retail_purchase_provider()가 매 호출마다 새
    인스턴스를 만들어도, request_quote()에서 만든 quote_id를
    place_order()가 다른 인스턴스에서도 그대로 찾을 수 있어야
    한다 — 인스턴스 속성으로 두면 이 재현이 깨진다). 테스트 간
    격리는 reset_state()로 명시적으로 수행한다."""

    code = "FAKE"
    capabilities = frozenset(ProviderCapability.ALL)

    _quotes: dict[str, PurchaseQuote] = {}
    _orders: dict[str, PlaceOrderResult] = {}
    _orders_by_idempotency: dict[str, str] = {}

    def __init__(self, scenario: str = FakeRetailPurchaseScenario.NORMAL):

        self.scenario = scenario

    @classmethod
    def reset_state(cls) -> None:
        """테스트 fixture 전용 — 프로세스 전역 시뮬레이션 상태를
        비운다(실제 Provider에는 대응 개념이 없다, 순수 테스트 격리
        도구)."""

        cls._quotes.clear()
        cls._orders.clear()
        cls._orders_by_idempotency.clear()

    def search_product(self, query: ProductSearchQuery) -> ProductSearchResult:

        now = datetime.utcnow()
        item = ProductSearchResultItem(
            external_product_id=f"FAKE-PRD-{query.keyword}",
            product_url=f"https://fake.example/{query.keyword}",
            title=f"[FAKE] {query.keyword}",
            brand=query.brand, manufacturer=query.brand,
            model_name=query.model_name, gtin=query.gtin,
            seller_name="FAKE 판매자", seller_trust_score=0.95,
            list_price=Decimal("10000.00"),
            in_stock=(self.scenario != FakeRetailPurchaseScenario.OUT_OF_STOCK),
            is_authorized_dealer=True,
        )
        return ProductSearchResult(query=query, items=(item,), evaluated_at=now)

    def get_product(self, external_product_id: str) -> ProductDetail:

        now = datetime.utcnow()
        return ProductDetail(
            external_product_id=external_product_id,
            product_url=f"https://fake.example/{external_product_id}",
            title=f"[FAKE] {external_product_id}",
            brand="FAKE_BRAND", manufacturer="FAKE_BRAND",
            model_name="FAKE-MODEL-1", gtin="0000000000000",
            options=(
                ProductOption(
                    option_id="opt-1", label="기본",
                    color_or_scent=None, capacity=None,
                    additional_price=Decimal("0.00"),
                ),
            ),
            components=("본품",),
            is_authorized_dealer=True,
            certification_info="FAKE-CERT-1",
            return_policy_summary="7일 이내 반품 가능(FAKE)",
            return_allowed=True,
            seller_name="FAKE 판매자", seller_trust_score=0.95,
            fetched_at=now,
        )

    def verify_identity(self) -> IdentityVerificationResult:

        return IdentityVerificationResult(
            verified=True, provider_code=self.code,
            external_account_reference="FAKE-ACCOUNT",
            status_detail="FAKE Provider — Sandbox 항상 연결됨",
        )

    def _price_for_scenario(self, base: Decimal) -> Decimal:

        if self.scenario == FakeRetailPurchaseScenario.PRICE_INCREASED:
            return base * Decimal("1.5")
        return base

    def calculate_checkout(
        self, items: tuple[CheckoutLineItem, ...],
    ) -> CheckoutCalculation:

        now = datetime.utcnow()
        unit_price = self._price_for_scenario(Decimal("10000.00"))
        item_total = sum(
            (unit_price * item.quantity for item in items), Decimal("0.00"),
        )
        shipping_fee = Decimal("3000.00")

        return CheckoutCalculation(
            items=items, item_total=item_total, shipping_fee=shipping_fee,
            total_amount=item_total + shipping_fee,
            estimated_delivery_days=2, calculated_at=now,
        )

    def create_purchase_quote(
        self, items: tuple[CheckoutLineItem, ...],
    ) -> PurchaseQuote:

        from datetime import timedelta

        calc = self.calculate_checkout(items)
        now = datetime.utcnow()
        quote_id = f"FAKE-QUOTE-{'-'.join(i.external_product_id for i in items)}-{int(now.timestamp())}"

        expires_at = now + (
            timedelta(seconds=1)
            if self.scenario == FakeRetailPurchaseScenario.QUOTE_EXPIRED
            else timedelta(minutes=10)
        )

        quote = PurchaseQuote(
            quote_id=quote_id, items=items,
            total_amount=calc.total_amount, shipping_fee=calc.shipping_fee,
            in_stock=(self.scenario != FakeRetailPurchaseScenario.OUT_OF_STOCK),
            expires_at=expires_at, quoted_at=now,
        )
        self._quotes[quote_id] = quote
        return quote

    def reserve_purchase(self, quote_id: str) -> PurchaseReservation:

        from datetime import timedelta

        quote = self._quotes.get(quote_id)
        if quote is None:
            raise RetailPurchaseProviderError(f"알 수 없는 quote_id: {quote_id}")

        return PurchaseReservation(
            reservation_id=f"FAKE-RES-{quote_id}", quote_id=quote_id,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )

    def place_order(self, request: PlaceOrderRequest) -> PlaceOrderResult:

        if request.idempotency_key in self._orders_by_idempotency:
            existing_order_id = self._orders_by_idempotency[request.idempotency_key]
            return self._orders[existing_order_id]

        quote = self._quotes.get(request.quote_id)
        if quote is None:
            raise RetailPurchaseProviderError(
                f"알 수 없는 quote_id: {request.quote_id}",
            )

        now = datetime.utcnow()

        if quote.expires_at < now:
            result = PlaceOrderResult(
                status="FAILED", external_order_id=None,
                external_order_number=None, actual_amount=None,
                error_code=ProviderErrorCode.PURCHASE_FAILED, retryable=False,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
                error_detail="quote_expired",
            )
        elif quote.total_amount > request.max_total_amount:
            result = PlaceOrderResult(
                status="FAILED", external_order_id=None,
                external_order_number=None, actual_amount=None,
                error_code=ProviderErrorCode.PRICE_CHANGED, retryable=False,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
                error_detail="quoted_amount_exceeds_max_total_amount",
            )
        elif self.scenario == FakeRetailPurchaseScenario.ORDER_FAILED:
            result = PlaceOrderResult(
                status="FAILED", external_order_id=None,
                external_order_number=None, actual_amount=None,
                error_code=ProviderErrorCode.PURCHASE_FAILED, retryable=True,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
                error_detail="fake_scenario_order_failed",
            )
        elif self.scenario == FakeRetailPurchaseScenario.ORDER_UNCERTAIN:
            result = PlaceOrderResult(
                status="UNCERTAIN", external_order_id=None,
                external_order_number=None, actual_amount=None,
                error_code=ProviderErrorCode.RESULT_UNCERTAIN, retryable=False,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
                error_detail="fake_scenario_timeout_uncertain",
            )
        else:
            order_id = f"FAKE-ORDER-{request.idempotency_key}"
            result = PlaceOrderResult(
                status="ORDERED", external_order_id=order_id,
                external_order_number=order_id,
                actual_amount=quote.total_amount,
                error_code=None, retryable=False,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
            )

        if result.external_order_id is not None:
            self._orders[result.external_order_id] = result
            self._orders_by_idempotency[request.idempotency_key] = result.external_order_id
        else:
            # 실패/불확실도 idempotency_key 재사용 시 같은 결과를
            # 내야 한다 — 재결제(중복 결제) 금지 원칙.
            fake_key = f"__failed__:{request.idempotency_key}"
            self._orders[fake_key] = result
            self._orders_by_idempotency[request.idempotency_key] = fake_key

        return result

    def get_order(self, external_order_id: str) -> OrderStatusResult:

        result = self._orders.get(external_order_id)
        if result is None:
            raise RetailPurchaseProviderError(
                f"알 수 없는 external_order_id: {external_order_id}",
            )
        return OrderStatusResult(
            external_order_id=external_order_id, status=result.status,
            tracking_company=None, tracking_number=None,
        )

    def cancel_order(self, external_order_id: str) -> CancelResult:

        if external_order_id not in self._orders:
            raise RetailPurchaseProviderError(
                f"알 수 없는 external_order_id: {external_order_id}",
            )
        return CancelResult(
            external_order_id=external_order_id, status="CANCELLED",
            cancelled=True, error_code=None,
        )

    def get_tracking(self, external_order_id: str) -> TrackingResult:

        if external_order_id not in self._orders:
            raise RetailPurchaseProviderError(
                f"알 수 없는 external_order_id: {external_order_id}",
            )
        return TrackingResult(
            external_order_id=external_order_id,
            tracking_company="FAKE_LOGISTICS",
            tracking_number=f"FAKE-TRK-{external_order_id}",
            status="IN_TRANSIT",
        )

    def get_balance_or_credit(self) -> BalanceOrCredit:

        return BalanceOrCredit(
            provider_code=self.code,
            external_account_reference="FAKE-ACCOUNT",
            available_balance=Decimal("100000000.00"),
            credit_limit=None, as_of=datetime.utcnow(),
        )


# --------------------------------------------------
# 실 계약 전 Provider들 — 전부 place_order()를 LiveInputRequiredError
# 로 차단한다. search_product/get_product 등도 실제 공식 API 계약이
# 없으면 NOT_SUPPORTED다(추정으로 채우지 않는다).
# --------------------------------------------------

class _UncontractedRetailPurchaseProvider(RetailPurchaseProvider):
    """서면 계약·공식 API 연동 전 상태를 나타내는 공통 베이스 —
    모든 메서드가 아직 아무것도 지원하지 않는다는 사실을 정직하게
    반환한다(capabilities는 비어 있음)."""

    capabilities: frozenset[str] = frozenset()

    def _blocked(self, method: str):

        raise LiveInputRequiredError(
            f"{self.code} Provider는 아직 서면 계약·공식 API 연동이 "
            f"완료되지 않았습니다({method} 차단, LIVE_INPUT_REQUIRED) "
            "— 화면 자동화로 대체하지 않습니다.",
        )

    def search_product(self, query: ProductSearchQuery) -> ProductSearchResult:
        self._require_capability(ProviderCapability.PRODUCT_SEARCH)
        raise NotSupportedByProviderError("unreachable")

    def get_product(self, external_product_id: str) -> ProductDetail:
        self._require_capability(ProviderCapability.PRODUCT_SEARCH)
        raise NotSupportedByProviderError("unreachable")

    def verify_identity(self) -> IdentityVerificationResult:

        return IdentityVerificationResult(
            verified=False, provider_code=self.code,
            external_account_reference=None,
            status_detail="CONTRACT_REQUIRED",
        )

    def calculate_checkout(
        self, items: tuple[CheckoutLineItem, ...],
    ) -> CheckoutCalculation:
        self._require_capability(ProviderCapability.PURCHASE_QUOTE)
        raise NotSupportedByProviderError("unreachable")

    def create_purchase_quote(
        self, items: tuple[CheckoutLineItem, ...],
    ) -> PurchaseQuote:
        self._require_capability(ProviderCapability.PURCHASE_QUOTE)
        raise NotSupportedByProviderError("unreachable")

    def reserve_purchase(self, quote_id: str) -> PurchaseReservation:
        self._require_capability(ProviderCapability.PURCHASE_QUOTE)
        raise NotSupportedByProviderError("unreachable")

    def place_order(self, request: PlaceOrderRequest) -> PlaceOrderResult:
        self._blocked("place_order")

    def get_order(self, external_order_id: str) -> OrderStatusResult:
        self._require_capability(ProviderCapability.ORDER_CREATE)
        raise NotSupportedByProviderError("unreachable")

    def cancel_order(self, external_order_id: str) -> CancelResult:
        self._require_capability(ProviderCapability.ORDER_CANCEL)
        raise NotSupportedByProviderError("unreachable")

    def get_tracking(self, external_order_id: str) -> TrackingResult:
        self._require_capability(ProviderCapability.TRACKING)
        raise NotSupportedByProviderError("unreachable")

    def get_balance_or_credit(self) -> BalanceOrCredit:
        self._require_capability(ProviderCapability.BALANCE)
        raise NotSupportedByProviderError("unreachable")


class ProcurementGatewayProvider(_UncontractedRetailPurchaseProvider):
    """계약된 구매대행사 API — Gate RP-1 시점에는 실제 파트너가 없다."""

    code = "PROCUREMENT_GATEWAY"


class OfficialMarketplacePurchaseProvider(_UncontractedRetailPurchaseProvider):
    """대형 쇼핑몰의 공식 구매/파트너 API — Gate RP-1 시점에는 공개
    문서 조사만 완료했고 실제 계약은 없다(docs/ 조사 결과 참고)."""

    code = "OFFICIAL_MARKETPLACE"


class CorporateProcurementProvider(_UncontractedRetailPurchaseProvider):
    """기업구매·후불 계약 — 추후 연결."""

    code = "CORPORATE_PROCUREMENT"


class VirtualCardProcurementProvider(_UncontractedRetailPurchaseProvider):
    """카드사·핀테크 가상 법인카드 제휴 — 추후 연결."""

    code = "VIRTUAL_CARD"


class DirectSupplierProvider(_UncontractedRetailPurchaseProvider):
    """고정 공급처 자동 발주 — 사용자 확정 보류 항목(수익화 모델
    별도 검토), 계약 인터페이스만 미리 정의."""

    code = "DIRECT_SUPPLIER"


_PROVIDER_REGISTRY: dict[str, type[RetailPurchaseProvider]] = {
    "FAKE": FakeRetailPurchaseProvider,
    "PROCUREMENT_GATEWAY": ProcurementGatewayProvider,
    "OFFICIAL_MARKETPLACE": OfficialMarketplacePurchaseProvider,
    "CORPORATE_PROCUREMENT": CorporateProcurementProvider,
    "VIRTUAL_CARD": VirtualCardProcurementProvider,
    "DIRECT_SUPPLIER": DirectSupplierProvider,
}


def get_retail_purchase_provider(code: str) -> RetailPurchaseProvider:

    provider_cls = _PROVIDER_REGISTRY.get(code)
    if provider_cls is None:
        raise RetailPurchaseProviderError(
            f"알 수 없는 Provider입니다: {code}",
        )
    return provider_cls()


__all__ = [
    "RetailPurchaseProviderError",
    "NotSupportedByProviderError",
    "LiveInputRequiredError",
    "CredentialRequiredError",
    "ReauthRequiredError",
    "RateLimitedError",
    "ProviderUnavailableError",
    "ProductNotFoundError",
    "ProductMismatchError",
    "ProductSearchQuery",
    "ProductSearchResultItem",
    "ProductSearchResult",
    "ProductOption",
    "ProductDetail",
    "IdentityVerificationResult",
    "CheckoutLineItem",
    "CheckoutCalculation",
    "PurchaseQuote",
    "PurchaseReservation",
    "PlaceOrderRequest",
    "PlaceOrderResult",
    "OrderStatusResult",
    "CancelResult",
    "TrackingResult",
    "BalanceOrCredit",
    "RetailPurchaseProvider",
    "FakeRetailPurchaseScenario",
    "FakeRetailPurchaseProvider",
    "ProcurementGatewayProvider",
    "OfficialMarketplacePurchaseProvider",
    "CorporateProcurementProvider",
    "VirtualCardProcurementProvider",
    "DirectSupplierProvider",
    "get_retail_purchase_provider",
]
