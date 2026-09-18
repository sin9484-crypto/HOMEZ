"""
=========================================================
Homez OS

File : app/domains/purchase_task/channel_adapter.py

Gate PT-3(2026-09-08, item 7 "사용자 계정 기반 매입 연결") — 매입처
Adapter 계약. app/domains/retail_purchase/provider.py와 철학은
같지만("Fake 동작을 실제 연결 성공으로 표시하지 않는다", 지원하지
않는 기능은 정직하게 NOT_SUPPORTED) 이 도메인 고유의 전제가 다르다:

- purchase_task는 "사람이 직접 로그인·결제한다"는 것이 설계 그
  자체다(constants.py 상단 docstring). 이 Adapter는 그 사람을
  대신해 로그인·결제를 실행하지 않는다 — 오직 "이 매입처에서 무엇을
  실제로 확인할 수 있는지"만 선언하고 조회한다.
- 확인하지 못한 기능은 UNKNOWN이지 NOT_SUPPORTED가 아니다(둘을
  구분하는 것 자체가 지시문 3번의 핵심 요구사항 — "미지원"과
  "미확인"은 다른 사실이다).
- 화면 자동화(로그인 자동 수행, DOM 클릭으로 결제 실행)는 이 계층
  어디에도 없다. AI가 구조 변경을 해석하는 것은 허용되지만, 상품·
  수량·금액·결제 성공 여부를 추측으로 확정하지 않는다 — 확정은
  항상 사람의 명시적 확인(PurchaseRecord 등 기존 흐름)을 거친다.
=========================================================
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from app.domains.purchase_task.constants import CapabilitySupport
from app.domains.purchase_task.constants import ChannelCapability
from app.domains.purchase_task.constants import ChannelConnectionStatus
from app.domains.purchase_task.constants import CREDENTIAL_REVERIFICATION_WINDOW_HOURS


# --------------------------------------------------
# 결과 데이터 계약 — 전부 support 필드를 가진다. UNKNOWN/NOT_SUPPORTED
# 일 때는 그 외 필드를 추측으로 채우지 않는다(None으로 둔다).
# --------------------------------------------------

@dataclass(frozen=True)
class ConnectionCheckResult:
    """2026-09-08 사용자 정정 — 서로 다른 4가지 사실을 하나의 status로
    뭉개지 않는다:
    1. credential_registered — Credential Manager 등에 자격증명이
       저장돼 있는가(Adapter가 직접 확인 가능한 경우만, 그 외에는
       항상 False).
    2. verified / verified_at — 사람이 실제로 이 연결이 살아있음을
       마지막으로 확인한 적이 있는가/언제(이 값은 Adapter가 스스로
       만들어내지 않는다 — 호출자가 PurchaseChannelConnection Model의
       verified_at을 그대로 넘겨준다. Adapter는 DB를 모른다).
    3. checked_at — 이 조회 자체를 지금 수행한 시각(= "마지막 확인
       시각"이 아니라 "지금 이 API를 호출한 시각").
    4. status — 위 두 사실을 조합한 표시용 요약값일 뿐, 사람이 실제로
       확인한 적이 없으면 credential_registered가 True여도 CONNECTED가
       아니라 REGISTERED_UNVERIFIED다."""

    mall_code: str
    status: str  # ChannelConnectionStatus — 아래 규칙으로만 계산한다
    credential_registered: bool
    verified: bool
    verified_at: datetime | None
    account_label: str | None
    checked_at: datetime
    detail: str


def _compute_connection_status(
    *, credential_registered: bool, verified_at: datetime | None,
    account_label: str | None, credential_capable: bool = False,
) -> str:
    """모든 Adapter가 동일한 규칙으로 status를 계산하게 강제한다 —
    개별 Adapter가 임의로 CONNECTED를 선언하지 못하게 하는 것이 이
    함수를 따로 둔 이유다.

    2026-09-08 재정정 — id=3/4 사고 원인. `credential_capable=True`인
    매입처(현재 온채널)는 "지금 이 순간 자격증명이 실제로 저장돼
    있는가"가 CONNECTED의 필요조건이다 — 과거 verified_at이 남아
    있어도(예: 자격증명 없이 "연결 확인 완료"를 눌렀던 과거 기록,
    또는 자격증명을 지운 뒤) 그 값을 신뢰하지 않고 NOT_CONNECTED로
    되돌린다. credential_capable=False(자격증명 개념 자체가 없는
    BROWSER_LOGIN 매입처)는 기존 규칙을 그대로 따른다 — credential_
    registered가 항상 False인 것이 "미등록"이 아니라 "개념 없음"이기
    때문이다.

    2026-09-08 후속("인증 최신성 결함 처리") — credential_capable=True
    매입처의 verified_at도 CREDENTIAL_REVERIFICATION_WINDOW_HOURS보다
    오래되면 CONNECTED가 아니라 EXPIRED를 반환한다(BROWSER_LOGIN의
    BROWSER_LOGIN_TRUST_WINDOW_DAYS와 동일한 이유 — 온채널의 공식
    만료 정책이 확인되지 않았으므로, 오래된 성공 기록을 무기한
    CONNECTED로 표시하지 않는다). EXPIRED는 ChannelConnectionStatus.
    USABLE_FOR_EXECUTION에 없으므로 이 상태에서는 매입 작업 배정도
    실제 발주 판정도 자동으로 막힌다."""

    if credential_capable and not credential_registered:
        return ChannelConnectionStatus.NOT_CONNECTED
    if verified_at is not None:
        if (
            credential_capable
            and datetime.utcnow() - verified_at
            >= timedelta(hours=CREDENTIAL_REVERIFICATION_WINDOW_HOURS)
        ):
            return ChannelConnectionStatus.EXPIRED
        return ChannelConnectionStatus.CONNECTED
    if credential_registered:
        return ChannelConnectionStatus.REGISTERED_UNVERIFIED
    if account_label is not None:
        return ChannelConnectionStatus.LOGIN_REQUIRED
    return ChannelConnectionStatus.NOT_CONNECTED


@dataclass(frozen=True)
class LoginRequirementResult:

    support: str  # CapabilitySupport
    requires_login: bool | None
    detail: str


@dataclass(frozen=True)
class ChannelProductOption:

    option_id: str
    label: str
    price: Decimal | None
    in_stock: bool | None


@dataclass(frozen=True)
class ChannelAttributeValue:
    """2026-09-16 전면 감사 후속(10-4, HOMEZ_USER_OPERATION_
    SETTINGS.md) — 공급처가 실제로 확인해 준 속성 값 하나(제조사/
    원산지/모델명/포장수량/규격/인증정보 등). `value=None`은
    "확인하지 못했다"는 뜻이지, 빈 문자열이나 추측값으로 채우지
    않는다 — `interpretable=False`가 항상 함께 온다(둘을 따로
    둔 이유는 향후 "응답에 필드는 있었지만 해석 불가"와 "애초에
    확인할 방법이 없음"을 구분할 여지를 남기기 위함일 뿐, 현재
    구현은 항상 둘을 묶어서 UNKNOWN으로 취급한다)."""

    value: str | None
    source: str | None
    confirmed_at: datetime | None
    interpretable: bool


UNKNOWN_ATTRIBUTE = ChannelAttributeValue(
    value=None, source=None, confirmed_at=None, interpretable=False,
)


@dataclass(frozen=True)
class ChannelShippingInfo:
    """2026-09-11 후속(반자동 완료 라운드) — 매입처가 상품 상세
    응답에서 제공하는 배송비 "제안값"일 뿐, 확정값이 아니다. Gate D
    (order_submission_service.py::_verify_point_balance_or_block)는
    이 값을 자동으로 신뢰해 통과시키지 않는다 — 사용자가 발주 검토
    화면에서 배송비를 직접 확인·입력할 때 참고 자료로만 보여준다."""

    send_type: str | None
    quantity_threshold: int | None
    base_shipping_cost: int | None
    jeju_shipping_cost: int | None
    remote_area_shipping_cost: int | None


@dataclass(frozen=True)
class ProductLookupResult:
    """2026-09-16 전면 감사 후속(10-4) — 아래 6개 속성 필드는 전부
    `ChannelAttributeValue`(값+출처+확인시각+해석가능여부)로만
    채운다. 기본값은 전부 `UNKNOWN_ATTRIBUTE`다 — 기존 Adapter
    구현체(Fake, 기본 UNKNOWN Adapter)는 아무것도 바꾸지 않아도
    그대로 동작한다(하위 호환). 실제 온채널 Adapter도 마찬가지로
    기본값(UNKNOWN)을 그대로 쓴다 — 공식 스펙(`docs/HOMEZ_
    ONCHANNEL_OPENAPI_SPEC_20260908.json`)의 `GET seller/product/
    {code}` 응답에 `gosi_info`(정보고시) 필드가 존재하긴 하지만
    스펙 자체가 "상세 필드는 정보고시 API를 참고하라"고만 적혀
    있을 뿐 내부 키를 전혀 문서화하지 않았고, 그 정보고시 API
    (`GET common/gosi`) 역시 스펙에 응답 스키마가 없다 — 추측으로
    `gosi_info`의 내부 키를 매핑하면 이 계약 전체가 지켜온
    "미확인을 확정값으로 취급하지 않는다" 원칙을 정면으로 어기는
    것이므로, 온채널 쪽 확인 답변이나 실제 인증 호출로 그 키가
    확정되기 전까지는 이 6개 필드를 전부 정직하게 UNKNOWN으로
    둔다."""

    support: str
    external_product_id: str | None
    title: str | None
    options: tuple[ChannelProductOption, ...]
    detail: str
    shipping_info: ChannelShippingInfo | None = None
    manufacturer: ChannelAttributeValue = UNKNOWN_ATTRIBUTE
    origin_country: ChannelAttributeValue = UNKNOWN_ATTRIBUTE
    model_name: ChannelAttributeValue = UNKNOWN_ATTRIBUTE
    package_quantity: ChannelAttributeValue = UNKNOWN_ATTRIBUTE
    size_specification: ChannelAttributeValue = UNKNOWN_ATTRIBUTE
    certification_identifiers: ChannelAttributeValue = UNKNOWN_ATTRIBUTE


@dataclass(frozen=True)
class ChannelProductSummary:
    """목록 조회 1건 요약 — 상세 옵션까지는 담지 않는다(목록 API
    자체가 옵션 상세를 안 주는 매입처도 있다, 온채널은 준다면 count만
    노출)."""

    external_product_id: str
    title: str


@dataclass(frozen=True)
class ProductListLookupResult:
    """2026-09-08 후속(item 7 승인된 상품 목록 조회 검증 전용) — 상품
    "상세"(코드로 1건)가 아니라 "목록"(페이지 단위) 조회 결과. 사용자가
    명시적으로 승인한 `GET seller/product?page=1&page_size=1` 호출
    계약과 1:1로 대응한다."""

    support: str
    total_returned: int
    items: tuple[ChannelProductSummary, ...]
    detail: str


@dataclass(frozen=True)
class MemberPointCheckResult:
    """2026-09-08 후속("결제 의미 확인" 재조사 라운드) — GET common/
    member/point 진단 조회 결과. **이 응답의 스키마는 공식 스펙에
    정의돼 있지 않다** — 그래서 이 dataclass는 "항상 이 필드가
    있다"는 계약을 표현하지 않는다. `observed_fields`는 이번 1회
    응답에서 실제로 관측된 키 목록일 뿐, 다음 호출에도 같은 구조가
    반환된다는 보장이 아니다.

    `point`는 필드가 없거나·null이거나·정수가 아니면 반드시 None이다
    — 해석할 수 없을 때 0으로 대체하지 않는다(0은 "실제로 0이라고
    응답했다"는 사실 전용 값이다, `point_interpretable`로 그
    차이를 구분한다).

    `member_id_masked`는 이미 마스킹된 값만 담는다 — 이 dataclass가
    존재하는 시점에 원문 member_id는 이미 사라졌다(호출자가 다시
    복원할 방법이 없다는 것이 곧 원문 미노출 보장이다)."""

    support: str
    member_id_masked: str | None
    point: int | None
    point_interpretable: bool
    observed_fields: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class OrderFormResult:
    """작업 4번 지시: "장바구니 추가 등 계정에 기록을 남기는 조작은
    읽기 전용 조회와 구분한다" — 이 결과는 항상 읽기 전용 견적이며,
    이걸 반환했다고 해서 매입처 쪽에 실제 주문서·장바구니가 생성된
    것은 아니다(mutates_remote_state=False가 그 사실을 명시한다)."""

    support: str
    estimated_item_amount: Decimal | None
    estimated_shipping_fee: Decimal | None
    estimated_total_amount: Decimal | None
    mutates_remote_state: bool
    detail: str


@dataclass(frozen=True)
class PaymentExecutabilityResult:

    support: str
    supported_payment_methods: tuple[str, ...]
    requires_additional_auth: bool | None
    detail: str


@dataclass(frozen=True)
class OrderLookupResult:

    support: str
    external_order_number: str | None
    status_text: str | None
    amount: Decimal | None
    detail: str


@dataclass(frozen=True)
class TrackingLookupResult:
    """2026-09-10 후속(온채널 공식 답변 — "부분배송·복수송장 미지원"
    확정) — `multiple_deliveries_detected=True`일 때는 `courier`·
    `tracking_number`가 항상 None이다. 온채널이 복수 송장을 반환한
    경우 어느 것이 "그" 송장인지 자동으로 고르지 않는다 — 사람이
    온채널 원본 화면에서 직접 확인해야 한다(단일 송장 정책)."""

    support: str
    courier: str | None
    tracking_number: str | None
    delivery_status: str | None
    detail: str
    multiple_deliveries_detected: bool = False


@dataclass(frozen=True)
class CancelSupportResult:

    support: str
    cancel_window_note: str | None
    detail: str


@dataclass(frozen=True)
class SalesApplicationResult:
    """2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수"
    확정) — 판매신청 시도 1건의 결과. `submitted`가 True라는 것은
    "온채널이 신청 접수를 확인했다"는 뜻일 뿐 "승인됐다"는 뜻이
    아니다 — 승인 상태를 조회하는 API 자체가 없다는 것도 공식
    답변으로 확정됐으므로, 이 dataclass는 애초에 승인 여부를
    표현하는 필드를 두지 않는다(없는 사실을 있는 것처럼 필드로
    만들지 않는다)."""

    support: str
    submitted: bool
    applied_product_code: str | None
    detail: str


class PurchaseChannelAdapterError(Exception):
    """이 계층에서 나는 모든 오류의 공통 부모. Secret·개인정보를
    메시지에 담지 않는다."""


# --------------------------------------------------
# Adapter 계약 — 8개 메서드 전부 기본 구현이 UNKNOWN을 반환한다(추상
# 메서드로 강제하지 않는 이유: 매입처마다 확인된 기능의 개수가 다르고,
# 확인하지 못한 나머지를 서브클래스가 매번 똑같은 UNKNOWN 보일러플레이트로
# 채우게 하지 않기 위함). 실제로 확인한 기능만 서브클래스가 override한다.
# --------------------------------------------------

class PurchaseChannelAdapter(ABC):

    mall_code: str = "ABSTRACT"

    def check_connection(
        self, account_label: str | None, *, verified_at: datetime | None = None,
    ) -> ConnectionCheckResult:
        """기본 구현은 자격증명 개념 자체가 없는 매입처(브라우저 로그인
        전용 등)를 위한 것이다 — credential_registered는 항상 False.
        verified_at은 PurchaseChannelConnection Model의 값을 호출자가
        그대로 넘긴 것일 뿐, 이 메서드가 스스로 만들어내지 않는다."""

        credential_registered = False
        status = _compute_connection_status(
            credential_registered=credential_registered, verified_at=verified_at,
            account_label=account_label,
        )
        return ConnectionCheckResult(
            mall_code=self.mall_code, status=status,
            credential_registered=credential_registered,
            verified=verified_at is not None, verified_at=verified_at,
            account_label=account_label, checked_at=datetime.utcnow(),
            detail="이 매입처는 API 자격증명 개념을 확인할 방법이 없습니다 — 연결 상태는 사람이 직접 확인합니다.",
        )

    def check_login_requirement(self) -> LoginRequirementResult:

        return LoginRequirementResult(
            support=CapabilitySupport.UNKNOWN, requires_login=None,
            detail="로그인 필요 여부가 아직 확인되지 않았습니다.",
        )

    def lookup_product(self, external_product_id: str) -> ProductLookupResult:

        return ProductLookupResult(
            support=CapabilitySupport.UNKNOWN, external_product_id=None,
            title=None, options=(),
            detail="상품·옵션·가격·재고 조회 기능이 아직 확인되지 않았습니다.",
        )

    def prepare_order_form(
        self, external_product_id: str, option_id: str | None, quantity: int,
    ) -> OrderFormResult:

        return OrderFormResult(
            support=CapabilitySupport.UNKNOWN, estimated_item_amount=None,
            estimated_shipping_fee=None, estimated_total_amount=None,
            mutates_remote_state=False,
            detail="주문서 작성·최종 금액 확인 기능이 아직 확인되지 않았습니다.",
        )

    def check_payment_executability(self) -> PaymentExecutabilityResult:

        return PaymentExecutabilityResult(
            support=CapabilitySupport.UNKNOWN, supported_payment_methods=(),
            requires_additional_auth=None,
            detail="결제 실행 가능 여부가 아직 확인되지 않았습니다.",
        )

    def lookup_order(self, external_order_number: str) -> OrderLookupResult:

        return OrderLookupResult(
            support=CapabilitySupport.UNKNOWN, external_order_number=None,
            status_text=None, amount=None,
            detail="기존 주문 조회 기능이 아직 확인되지 않았습니다.",
        )

    def lookup_tracking(self, external_order_number: str) -> TrackingLookupResult:

        return TrackingLookupResult(
            support=CapabilitySupport.UNKNOWN, courier=None,
            tracking_number=None, delivery_status=None,
            detail="배송·송장 조회 기능이 아직 확인되지 않았습니다.",
        )

    def check_cancel_support(self) -> CancelSupportResult:

        return CancelSupportResult(
            support=CapabilitySupport.UNKNOWN, cancel_window_note=None,
            detail="취소 지원 여부가 아직 확인되지 않았습니다.",
        )

    def apply_for_sale(self, external_product_id: str) -> SalesApplicationResult:
        """2026-09-10 후속 — ChannelCapability.ALL(item 7이 지정한
        고정 8개 항목)에는 없는 신규 메서드다(submit_order/check_
        member_point와 같은 선례 — 온채널 공식 답변으로 새로 확정된
        기능은 그 8개 목록을 건드리지 않고 이렇게 별도로 추가한다).
        기본 구현은 "이 매입처가 판매신청 개념 자체를 지원하는지도
        확인되지 않았다"는 뜻이다."""

        return SalesApplicationResult(
            support=CapabilitySupport.UNKNOWN, submitted=False,
            applied_product_code=None,
            detail="판매신청 기능이 아직 확인되지 않았습니다.",
        )

    def capability_matrix(self) -> dict[str, str]:
        """화면 표시용 — 8개 항목 각각의 support 상태만 가볍게 모아
        반환한다(실제 조회를 수행하지 않는다, 정적 선언). 서브클래스가
        override하지 않으면 이 8개가 전부 확인 방법이 없다는 뜻이므로
        UNKNOWN이 아니라 각 메서드의 명시적 지원 여부를 알아야 하는
        호출부는 반드시 해당 메서드를 직접 호출해야 한다 — 이 메서드는
        요약 화면 전용이다."""

        return {capability: CapabilitySupport.UNKNOWN for capability in ChannelCapability.ALL}


# --------------------------------------------------
# FakePurchaseChannelAdapter — 테스트 전용, 네트워크 호출 없음,
# 완전 결정론적. retail_purchase.provider.FakeRetailPurchaseProvider와
# 동일한 목적.
# --------------------------------------------------

class FakePurchaseChannelAdapter(PurchaseChannelAdapter):

    mall_code = "FAKE_CHANNEL"

    def __init__(self, *, connected: bool = True):

        self._connected = connected

    def check_connection(
        self, account_label: str | None, *, verified_at: datetime | None = None,
    ) -> ConnectionCheckResult:
        """FAKE는 credential_registered를 self._connected로 시뮬레이션
        하지만, CONNECTED로 표시되려면 여전히 verified_at이 있어야
        한다 — 테스트 전용 Adapter도 실제와 동일한 규칙을 따른다."""

        credential_registered = self._connected
        status = _compute_connection_status(
            credential_registered=credential_registered, verified_at=verified_at,
            account_label=account_label,
        )
        return ConnectionCheckResult(
            mall_code=self.mall_code, status=status,
            credential_registered=credential_registered,
            verified=verified_at is not None, verified_at=verified_at,
            account_label=account_label, checked_at=datetime.utcnow(),
            detail="FAKE Adapter — 테스트 전용, 실제 매입처가 아닙니다.",
        )

    def check_login_requirement(self) -> LoginRequirementResult:

        return LoginRequirementResult(
            support=CapabilitySupport.SUPPORTED, requires_login=not self._connected,
            detail="FAKE Adapter",
        )

    def lookup_product(self, external_product_id: str) -> ProductLookupResult:

        return ProductLookupResult(
            support=CapabilitySupport.SUPPORTED,
            external_product_id=external_product_id,
            title=f"[FAKE] {external_product_id}",
            options=(
                ChannelProductOption(
                    option_id="opt-1", label="기본",
                    price=Decimal("10000.00"), in_stock=True,
                ),
            ),
            detail="FAKE Adapter",
        )

    def prepare_order_form(
        self, external_product_id: str, option_id: str | None, quantity: int,
    ) -> OrderFormResult:

        item_amount = Decimal("10000.00") * quantity
        shipping = Decimal("3000.00")
        return OrderFormResult(
            support=CapabilitySupport.SUPPORTED,
            estimated_item_amount=item_amount,
            estimated_shipping_fee=shipping,
            estimated_total_amount=item_amount + shipping,
            mutates_remote_state=False,
            detail="FAKE Adapter — 읽기 전용 견적",
        )

    def check_payment_executability(self) -> PaymentExecutabilityResult:

        return PaymentExecutabilityResult(
            support=CapabilitySupport.SUPPORTED,
            supported_payment_methods=("CARD",),
            requires_additional_auth=False,
            detail="FAKE Adapter",
        )

    def lookup_order(self, external_order_number: str) -> OrderLookupResult:

        return OrderLookupResult(
            support=CapabilitySupport.SUPPORTED,
            external_order_number=external_order_number,
            status_text="ORDERED", amount=Decimal("13000.00"),
            detail="FAKE Adapter",
        )

    def lookup_tracking(self, external_order_number: str) -> TrackingLookupResult:

        return TrackingLookupResult(
            support=CapabilitySupport.SUPPORTED, courier="FAKE_LOGISTICS",
            tracking_number=f"FAKE-TRK-{external_order_number}",
            delivery_status="IN_TRANSIT", detail="FAKE Adapter",
        )

    def check_cancel_support(self) -> CancelSupportResult:

        return CancelSupportResult(
            support=CapabilitySupport.SUPPORTED,
            cancel_window_note="FAKE — 발송 전까지 취소 가능",
            detail="FAKE Adapter",
        )

    def apply_for_sale(self, external_product_id: str) -> SalesApplicationResult:

        return SalesApplicationResult(
            support=CapabilitySupport.SUPPORTED, submitted=True,
            applied_product_code=external_product_id, detail="FAKE Adapter",
        )

    def capability_matrix(self) -> dict[str, str]:

        return {capability: CapabilitySupport.SUPPORTED for capability in ChannelCapability.ALL}


# --------------------------------------------------
# OnchannelChannelAdapter — item 6에서 조사한 실제 현황을 그대로
# 반영한다(docs/HOMEZ_V7_PROCUREMENT_CHANNEL_SURVEY_20260907.md).
# 연결 상태 확인만 실제로 가능하다(Windows Credential Manager 등록
# 여부 조회) — 그 외 7개는 공식 기술 문서가 없어 전부 UNKNOWN이다.
# 기본 구현을 override하지 않은 메서드는 그대로 부모의 UNKNOWN을
# 물려받는다.
# --------------------------------------------------

class OnchannelChannelAdapter(PurchaseChannelAdapter):
    """온채널(국내 B2B 도매·위탁) — 2026-09-08 후속: 사용자가 실제
    공식 OpenAPI 스펙 URL을 공유해 기술 문서를 확보했다(원본
    docs/HOMEZ_ONCHANNEL_OPENAPI_SPEC_20260908.json, 요약
    docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md). 온채널 용어의
    "판매사"(seller) = HOMEZ의 역할이다.

    실제 구현한 것: 상품 조회(GET seller/product{,/{code}})·주문
    조회(GET seller/order{,/{code}}) — 읽기 전용, `OnchannelApiClient`
    (app/domains/purchase_task/onchannel_client.py)에 위임한다.

    2026-09-18 정정(D2 반자동 흐름 감사, 결함 아님 — 문서 드리프트) —
    바로 위 문단이 "발주(POST seller/order/regist)는 이 Adapter에도
    아직 만들지 않았다"고 남아 있었으나 이는 오래된 서술이다. 실제로는
    `submit_order()`(아래, `OnchannelApiClient.register_order()`에
    위임)가 이미 구현되어 있고, `PurchaseOrderSubmissionService.
    submit_order()`(order_submission_service.py)를 통해 실제 발주
    경로가 이 Adapter에 도달한다 — `confirm_real_submission=True`
    (라우터가 재인증 토큰과 함께만 전달), EmergencyStop/PAUSED·ERROR
    미해제, 판매신청 접수 확인, 포인트·배송비 재확인, 유효한
    사용자 최종 승인(10분 이내, 미소비) 네 가지가 전부 통과해야만
    실제로 호출된다 — "코드가 존재한다"와 "승인됐다"는 여전히
    구분된다.

    **이 세션은 실제 저장된 JWT로 이 메서드들을 단 한 번도 호출하지
    않았다** — 코드는 준비됐지만 실행은 사용자의 명시적 승인(정확한
    호출 1회, 재시도 없음)을 기다린다.

    자격증명은 전역 단일 슬롯을 쓰지 않는다 — 회사가 온채널 계정을
    여러 개 연결해도 서로 섞이지 않도록, 호출자(서비스 계층)가 해당
    PurchaseChannelConnection.credential_reference를 반드시 넘겨야
    한다.

    2026-09-11 정정(운영 전 최종 검증 라운드, Credential 격리 결함
    수정) — 이전에는 `credential_reference`를 넘기지 않으면
    `OnchannelSupplierOrderProvider.CREDENTIAL_REFERENCE`("homez_
    onchannel_api", app/domains/purchase 레거시 단일연결 도메인이
    소유한 이름)로 "안전하게" 대체한다고 문서화돼 있었다 — 실제로는
    안전하지 않았다. 이 머신의 Credential Manager에 그 이름으로
    실제 유효한 온채널 자격증명이 있어, 브라우저 UI 검증 중
    `credential_reference=None`으로 만든 테스트 연결 하나가 그
    레거시 자격증명을 그대로 빌려 실제 인증된 GET 요청을 온채널
    운영 서버로 보낸 사고가 실제로 발생했다(부작용 없는 읽기
    실패였지만, 승인 없는 라이브 호출이었다). 이제
    `credential_reference`가 없으면 절대 다른 자격증명으로 대체하지
    않는다 — `_read_credential()`이 즉시 None을 반환해(Credential
    Store 자체를 조회하지 않음) 이후 모든 실제 호출 메서드가
    `PurchaseChannelAdapterError`로 막힌다. `app/domains/store_
    connection/service.py::verify_existing()`이 이미 쓰고 있던
    "참조 없음 = 즉시 차단" 패턴과 동일하게 맞췄다."""

    mall_code = "ONCHANNEL"

    def __init__(
        self, credential_store=None, credential_reference=None, http_get=None,
        http_post=None,
    ):

        if credential_store is None:
            from app.core.windows_credential_store import WindowsCredentialStore
            credential_store = WindowsCredentialStore()
        self._credential_store = credential_store

        # 2026-09-11 정정 — None을 다른 이름으로 대체하지 않는다(위
        # 클래스 docstring 참고). 이 연결에 배정된 credential_
        # reference가 정확히 없으면, 이 Adapter는 어떤 자격증명도
        # 갖지 않은 것으로 취급한다.
        self._credential_reference = credential_reference

        # 테스트 전용 주입 지점 — 실제 코드 경로는 절대 이 값들을
        # 넘기지 않는다(None이면 OnchannelApiClient가 진짜 requests.
        # get/post를 쓴다).
        self._http_get = http_get
        self._http_post = http_post

    def _read_credential(self) -> dict | None:
        """credential_reference가 없으면 Credential Store 자체를
        절대 조회하지 않는다(어떤 target_name으로도 None을 넘기지
        않는다) — 스토어 구현체가 None을 예상치 못한 방식으로
        처리할 가능성 자체를 차단한다(2026-09-11 정정 — 실제로
        `WindowsCredentialStore.read(None)`이 어떻게 동작하는지
        검증된 적이 없었다는 사실이 이번 사고 조사에서 드러났다)."""

        if self._credential_reference is None:
            return None

        from app.core.windows_credential_store import (
            CredentialNotFoundError, CredentialStoreError,
        )

        try:
            return self._credential_store.read(self._credential_reference)
        except (CredentialNotFoundError, CredentialStoreError):
            return None

    def check_connection(
        self, account_label: str | None, *, verified_at: datetime | None = None,
    ) -> ConnectionCheckResult:
        """2026-09-08 사용자 정정 — 자격증명이 Credential Manager에
        있다는 사실만으로 CONNECTED를 선언하지 않는다. credential_
        registered=True + verified_at=None이면 status는
        REGISTERED_UNVERIFIED("등록됨 · 연결 미확인")다. verified_at은
        이 Adapter가 알 수 없다 — 오직 PurchaseChannelConnection Model에
        사람이 명시적으로 "연결 확인 완료"를 기록했을 때만 서비스
        계층이 그 값을 여기로 넘겨준다."""

        credential_registered = self._read_credential() is not None

        status = _compute_connection_status(
            credential_registered=credential_registered, verified_at=verified_at,
            account_label=account_label, credential_capable=True,
        )

        if status == ChannelConnectionStatus.CONNECTED:
            detail = (
                f"자격증명 등록됨, {verified_at.isoformat()}에 실제 API 조회 성공으로 "
                "연결 확인이 기록되었습니다."
            )
        elif status == ChannelConnectionStatus.REGISTERED_UNVERIFIED:
            detail = (
                "자격증명이 저장되어 있지만 실제 API 조회 성공 기록이 없습니다 "
                "(등록됨 ≠ 연결 확인됨). 상품 조회 등 실제 조회가 성공해야만 "
                "CONNECTED로 표시됩니다 — 사람이 직접 성공을 선언할 수 없습니다."
            )
        else:
            detail = (
                "저장된 온채널 API 자격증명이 없습니다"
                + ("(과거 연결 확인 기록은 자격증명이 없는 한 더 이상 유효하지 않습니다)."
                   if verified_at is not None else ".")
            )

        return ConnectionCheckResult(
            mall_code=self.mall_code, status=status,
            credential_registered=credential_registered,
            verified=verified_at is not None, verified_at=verified_at,
            account_label=account_label, checked_at=datetime.utcnow(),
            detail=detail,
        )

    def _require_client(self):
        """실제 호출 직전에만 부른다 — capability_matrix()나 check_
        connection()은 이 메서드를 거치지 않는다(그 둘은 절대 실제
        네트워크를 열지 않는다는 계약)."""

        from app.domains.purchase_task.onchannel_client import OnchannelApiClient

        credential = self._read_credential()
        if credential is None:
            raise PurchaseChannelAdapterError(
                "온채널 자격증명이 저장되어 있지 않습니다 — 먼저 API 키를 "
                "등록하세요(ONCHANNEL_CREDENTIAL_REQUIRED).",
            )
        auth_key = str(credential.get("auth_key", ""))
        if not auth_key:
            raise PurchaseChannelAdapterError(
                "저장된 온채널 자격증명에 인증키가 없습니다(ONCHANNEL_CREDENTIAL_INVALID).",
            )
        kwargs = {}
        if self._http_get is not None:
            kwargs["http_get"] = self._http_get
        if self._http_post is not None:
            kwargs["http_post"] = self._http_post
        return OnchannelApiClient(auth_key=auth_key, **kwargs)

    def lookup_product(self, external_product_id: str) -> ProductLookupResult:
        """GET /openapi/seller/product/{code} — 실제 호출한다(코드
        준비 완료, 실행은 사용자 승인 대기 — 이 메서드 자체는 "언제
        호출해도 되는지"를 판단하지 않는다, 그건 호출자의 책임이다).
        온채널 쪽 실패(401/403/404/429/응답형식오류/네트워크오류)는
        그대로 다시 던진다 — 조용히 빈 결과로 뭉개지 않는다(호출자가
        어떤 실패인지 구분해서 사용자에게 보여줄 수 있어야 한다)."""

        client = self._require_client()
        product = client.get_product(external_product_id)

        options = tuple(
            ChannelProductOption(
                option_id=str(opt.option_id), label=opt.label,
                price=Decimal(str(opt.price)) if opt.price is not None else None,
                in_stock=(opt.stock_qty or 0) > 0 if opt.stock_qty is not None else None,
            )
            for opt in product.options
        )
        shipping_info = None
        if product.shipping_info is not None:
            shipping_info = ChannelShippingInfo(
                send_type=product.shipping_info.send_type,
                quantity_threshold=product.shipping_info.quantity_threshold,
                base_shipping_cost=product.shipping_info.base_shipping_cost,
                jeju_shipping_cost=product.shipping_info.jeju_shipping_cost,
                remote_area_shipping_cost=product.shipping_info.remote_area_shipping_cost,
            )
        return ProductLookupResult(
            support=CapabilitySupport.SUPPORTED,
            external_product_id=product.product_code, title=product.title,
            options=options, detail="온채널 실 API 조회 결과.",
            shipping_info=shipping_info,
        )

    def list_products(
        self, *, page: int = 1, page_size: int = 1,
    ) -> ProductListLookupResult:
        """GET /openapi/seller/product — 승인된 상품 목록 조회 검증
        전용(item 7). `lookup_product`(코드로 상세 1건)와는 별개의
        엔드포인트다 — 사용자가 실제로 승인한 계약(`page`/`page_size`
        파라미터, 특정 상품 코드를 몰라도 호출 가능)과 정확히
        일치시키기 위해 새로 만들었다."""

        client = self._require_client()
        products = client.list_products(page=page, page_size=page_size)

        items = tuple(
            ChannelProductSummary(external_product_id=p.product_code, title=p.title)
            for p in products
        )
        return ProductListLookupResult(
            support=CapabilitySupport.SUPPORTED,
            total_returned=len(items), items=items,
            detail="온채널 실 API 상품 목록 조회 결과.",
        )

    def submit_order(self, request) -> str:
        """POST /openapi/seller/order/regist — 실제 발주. `request`는
        `OnchannelOrderRegistrationRequest`(개인정보 포함, 이 메서드는
        그 값을 로그로 남기지 않는다 — 그 클래스 자체가 __repr__을
        마스킹해 두었다).

        이 메서드는 "지금 호출해도 되는지"를 스스로 판단하지 않는다
        — 그건 `order_submission_service.py`의 idempotency 잠금과
        연결 상태 검증을 거친 호출자의 책임이다. 성공하면 order_code
        문자열만 반환한다(응답 전체를 넘기지 않는다 — 호출부가 실수로
        원문 응답을 통째로 로그에 남기는 습관을 만들지 않기 위함)."""

        client = self._require_client()
        return client.register_order(request)

    def apply_for_sale(self, external_product_id: str) -> SalesApplicationResult:
        """POST /openapi/seller/product/apply — 실제 판매신청.
        2026-09-10 온채널 공식 답변으로 "발주 전 필수"가 확정됐다.
        `submitted=True`는 온채널이 HTTP 200으로 접수를 확인했다는
        뜻일 뿐, 실제 승인 여부는 이 응답만으로 알 수 없다(승인 상태
        조회 API 자체가 없다고 공식 답변으로 확정됨) — 그래서 이
        메서드도, 이 메서드가 반환하는 결과도 "승인됨"이라는 말을
        쓰지 않는다."""

        client = self._require_client()
        applied_code = client.apply_for_sale(external_product_id)

        return SalesApplicationResult(
            support=CapabilitySupport.SUPPORTED, submitted=True,
            applied_product_code=applied_code,
            detail=(
                "온채널 실 API 판매신청 접수 확인(HTTP 200) — 승인 여부는 "
                "별도로 조회할 방법이 없습니다(공식 답변으로 확정됨)."
            ),
        )

    def check_member_point(self) -> MemberPointCheckResult:
        """GET /openapi/common/member/point — 발주·결제 계약 조사
        전용 진단 호출. 응답 스키마가 공식 문서에 없으므로 필드
        존재·타입을 방어적으로 해석한다 — 누락·null·잘못된 타입은
        전부 "해석 불가"로 남기고 추측으로 채우지 않는다."""

        from app.domains.purchase_task.onchannel_client import mask_pii

        client = self._require_client()
        raw = client.get_member_point()

        observed_fields = tuple(raw.keys()) if isinstance(raw, dict) else ()

        member_id_masked = None
        if isinstance(raw, dict):
            member_id_raw = raw.get("member_id")
            if isinstance(member_id_raw, str) and member_id_raw:
                member_id_masked = mask_pii(member_id_raw)

        point: int | None = None
        point_interpretable = False
        if isinstance(raw, dict) and "point" in raw:
            point_raw = raw["point"]
            # bool은 int의 서브클래스라 isinstance(True, int)가 True다 —
            # True/False를 실수로 1/0 포인트로 오인하지 않게 별도 제외.
            if isinstance(point_raw, int) and not isinstance(point_raw, bool):
                point = point_raw
                point_interpretable = True

        return MemberPointCheckResult(
            support=CapabilitySupport.SUPPORTED,
            member_id_masked=member_id_masked, point=point,
            point_interpretable=point_interpretable,
            observed_fields=observed_fields,
            detail=(
                "온채널 실 API 포인트 조회 결과 — 응답 스키마가 공식 문서에 "
                "없어 이번 응답에서 관측된 필드만 반영했다(고정 계약 아님)."
            ),
        )

    def lookup_order(self, external_order_number: str) -> OrderLookupResult:
        """GET /openapi/seller/order/{code} — 실제 호출한다(코드
        준비 완료, 실행은 사용자 승인 대기)."""

        client = self._require_client()
        order = client.get_order(external_order_number)

        return OrderLookupResult(
            support=CapabilitySupport.SUPPORTED,
            external_order_number=order.order_code,
            status_text=order.detail_status or order.status,
            amount=Decimal(str(order.order_price)),
            detail="온채널 실 API 조회 결과.",
        )

    def lookup_tracking(self, external_order_number: str) -> TrackingLookupResult:
        """스펙에 별도 배송조회 엔드포인트가 없다 — 주문 상세
        응답(GET seller/order/{code})의 deliverys 배열에 포함되어
        있으므로 그 호출을 그대로 재사용한다."""

        client = self._require_client()
        order = client.get_order(external_order_number)

        if not order.deliverys:
            return TrackingLookupResult(
                support=CapabilitySupport.SUPPORTED, courier=None,
                tracking_number=None, delivery_status=order.detail_status,
                detail="아직 등록된 송장이 없습니다(온채널 실 API 조회 결과).",
            )

        if len(order.deliverys) > 1:
            # 2026-09-10 후속(온채널 공식 답변 — "부분배송·복수송장
            # 미지원" 확정) — 단일 송장 정책. 온채널이 2건 이상의
            # 송장을 반환하면 어느 것이 "그" 송장인지 자동으로 고르지
            # 않는다(예: 최신순으로 추정하지 않는다) — 사람이 온채널
            # 원본에서 직접 확인해야 한다.
            return TrackingLookupResult(
                support=CapabilitySupport.SUPPORTED, courier=None,
                tracking_number=None, delivery_status=order.detail_status,
                detail=(
                    f"송장이 {len(order.deliverys)}건 감지됐습니다 — 온채널은 "
                    "부분배송·복수송장을 공식적으로 지원하지 않는다고 확인됐으나 "
                    "실제 응답에 2건 이상이 왔습니다. 자동으로 하나를 선택하지 "
                    "않습니다 — 온채널 원본 화면에서 직접 확인하세요."
                ),
                multiple_deliveries_detected=True,
            )

        latest = order.deliverys[-1]
        return TrackingLookupResult(
            support=CapabilitySupport.SUPPORTED, courier=latest.courier,
            tracking_number=latest.tracking_number,
            delivery_status=order.detail_status,
            detail="온채널 실 API 조회 결과(주문 상세의 배송 정보).",
        )

    def capability_matrix(self) -> dict[str, str]:
        """2026-09-08 후속 — 실제 스펙 확보 후 갱신. 코드가 준비됐다고
        곧바로 SUPPORTED로 표시하지 않는다 — 스펙상 엔드포인트가
        존재하고 이 Adapter가 실제로 그 요청/응답 형태를 구현했을
        때만 SUPPORTED다. CANCEL_SUPPORT_CHECK는 스펙 18개 엔드포인트
        어디에도 취소 API가 없어 NOT_SUPPORTED로 정정한다(추측으로
        지원한다고 하지 않는다). PAYMENT_EXECUTABILITY_CHECK는 회원
        포인트 조회(common/member/point)가 있으나 이것이 실제 결제
        가능 여부 판단과 어떤 관계인지 스펙만으로는 알 수 없어
        UNKNOWN으로 유지한다. ORDER_FORM_AND_FINAL_AMOUNT(주문서
        작성·최종 금액 확인)는 별도 견적 엔드포인트가 없고 상품
        상세의 옵션가로만 추정 가능해 UNKNOWN으로 유지한다(실제 발주
        시점 최종 금액과 다를 수 있음)."""

        matrix = super().capability_matrix()
        matrix[ChannelCapability.CONNECTION_CHECK] = CapabilitySupport.SUPPORTED
        matrix[ChannelCapability.PRODUCT_OPTION_PRICE_STOCK_LOOKUP] = CapabilitySupport.SUPPORTED
        matrix[ChannelCapability.EXISTING_ORDER_LOOKUP] = CapabilitySupport.SUPPORTED
        matrix[ChannelCapability.SHIPPING_TRACKING_LOOKUP] = CapabilitySupport.SUPPORTED
        matrix[ChannelCapability.CANCEL_SUPPORT_CHECK] = CapabilitySupport.NOT_SUPPORTED
        return matrix


_ADAPTER_REGISTRY: dict[str, type[PurchaseChannelAdapter]] = {
    "FAKE_CHANNEL": FakePurchaseChannelAdapter,
    "ONCHANNEL": OnchannelChannelAdapter,
}


def get_purchase_channel_adapter(
    mall_code: str, *, credential_reference: str | None = None,
    credential_store=None,
) -> PurchaseChannelAdapter:
    """credential_reference는 CREDENTIAL 방식 매입처(현재 온채널)에만
    의미가 있다 — 넘기면 그 특정 연결(PurchaseChannelConnection)의
    자격증명 슬롯을 쓴다. 넘기지 않으면(2026-09-11 정정, Credential
    격리 결함 수정) 온채널 Adapter는 **어떤 자격증명도 갖지 않은
    것으로 취급한다** — 예전처럼 이전 전역 슬롯(`homez_onchannel_
    api`)으로 조용히 대체하지 않는다. 이 대체가 실제로 안전하지
    않았다는 사실이 브라우저 UI 검증 중 실 온채널 운영 서버로의
    승인 없는 라이브 호출로 드러났다(app/domains/purchase_task/
    channel_adapter.py::OnchannelChannelAdapter docstring 참고).

    2026-09-08 후속(격리 검증 중 실제 Credential Manager 오염 사고
    재발 방지) — credential_store를 넘기지 않으면 OnchannelChannelAdapter가
    스스로 실제 WindowsCredentialStore()를 새로 만든다. 호출자(주로
    PurchaseChannelConnectionService)가 테스트용 InMemoryCredentialStore
    등 다른 저장소를 이미 주입받아 쓰고 있다면, 이 인자로 반드시 그
    "같은" 저장소를 넘겨야 한다 — 그렇지 않으면 서비스 계층은 가짜
    저장소를 보고, Adapter는 몰래 진짜 저장소를 봐서 서로 다른
    진실을 갖게 된다(격리된 줄 알았던 테스트가 실제 Windows
    Credential Manager를 오염시킨 사고의 직접 원인)."""

    adapter_cls = _ADAPTER_REGISTRY.get(mall_code)
    if adapter_cls is None:
        raise PurchaseChannelAdapterError(f"알 수 없는 매입처 Adapter입니다: {mall_code}")
    if adapter_cls is OnchannelChannelAdapter:
        kwargs = {}
        if credential_reference is not None:
            kwargs["credential_reference"] = credential_reference
        if credential_store is not None:
            kwargs["credential_store"] = credential_store
        return adapter_cls(**kwargs)
    return adapter_cls()


__all__ = [
    "CapabilitySupport",
    "ChannelCapability",
    "ChannelConnectionStatus",
    "ConnectionCheckResult",
    "LoginRequirementResult",
    "ChannelProductOption",
    "ChannelAttributeValue",
    "UNKNOWN_ATTRIBUTE",
    "ChannelShippingInfo",
    "ProductLookupResult",
    "ChannelProductSummary",
    "ProductListLookupResult",
    "MemberPointCheckResult",
    "OrderFormResult",
    "PaymentExecutabilityResult",
    "OrderLookupResult",
    "TrackingLookupResult",
    "CancelSupportResult",
    "SalesApplicationResult",
    "PurchaseChannelAdapterError",
    "PurchaseChannelAdapter",
    "FakePurchaseChannelAdapter",
    "OnchannelChannelAdapter",
    "get_purchase_channel_adapter",
]
