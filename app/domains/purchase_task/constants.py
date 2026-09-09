"""
=========================================================
Homez OS

File : app/domains/purchase_task/constants.py

Gate PT-1(2026-08-22 15차 지시) — A(구매 링크 전달)+E(구매 작업
큐)+F(주문번호·송장 가져오기) 혼합형 매입·발주 Workflow. 소비자
계정 자동 로그인·DOM 자동화·CAPTCHA/OTP 우회는 이 도메인 어디에도
없다 — 사람이 직접 링크를 열고 로그인·결제하며, HOMEZ는 검색어·
후보·마진·예산까지만 준비한다.

`app/domains/retail_purchase/`(Provider 계약 기반)는 그대로
보존한다(미래 확장용) — 이 도메인이 그것을 대체하지 않는다.
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class PurchaseTaskStatus:

    SEARCH_REQUIRED = "SEARCH_REQUIRED"
    CANDIDATES_READY = "CANDIDATES_READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PURCHASE_READY = "PURCHASE_READY"
    USER_PAYMENT_PENDING = "USER_PAYMENT_PENDING"
    TRACKING_REQUIRED = "TRACKING_REQUIRED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCEL_REQUIRED = "CANCEL_REQUIRED"
    RETURN_REQUIRED = "RETURN_REQUIRED"
    REFUND_PENDING = "REFUND_PENDING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"

    # Gate PT-2(2026-08-22 16차 지시) — 쿠팡 주문 자동 연결 신규 상태.
    # 재고 보유로 매입이 필요 없는 품목을 "생략됐다는 사실 자체가
    # 안 보이는" 무기록이 아니라 명확한 상태로 남긴다(멱등성·조회
    # 근거 확보).
    SKIPPED_INVENTORY_AVAILABLE = "SKIPPED_INVENTORY_AVAILABLE"
    # 결제 전(예산 미점유) 단계에서 원 주문이 취소된 경우의 종결
    # 상태 — 이미 점유된 예산이 있는 상태(PURCHASE_READY 이상)에서의
    # 취소는 기존 CANCEL_REQUIRED/UNCERTAIN/RETURN_REQUIRED를 그대로
    # 재사용한다(신규 상태를 늘리지 않는다).
    SOURCE_ORDER_CANCELLED = "SOURCE_ORDER_CANCELLED"

    ALL = (
        SEARCH_REQUIRED, CANDIDATES_READY, REVIEW_REQUIRED, PURCHASE_READY,
        USER_PAYMENT_PENDING, TRACKING_REQUIRED, SHIPPED,
        DELIVERED, CANCEL_REQUIRED, RETURN_REQUIRED, REFUND_PENDING,
        COMPLETED, BLOCKED, FAILED, UNCERTAIN,
        SKIPPED_INVENTORY_AVAILABLE, SOURCE_ORDER_CANCELLED,
    )

    # 예산이 실제로 점유돼 있는 상태 — UNCERTAIN도 포함한다(결제
    # 여부가 불확실한 동안 중복결제 방지를 위해 해제하지 않으므로
    # 여전히 "점유 중"으로 취급해야 동시 진행 한도 등이 정확하다).
    BUDGET_HOLDING = (
        PURCHASE_READY, USER_PAYMENT_PENDING,
        TRACKING_REQUIRED, SHIPPED, DELIVERED, CANCEL_REQUIRED,
        RETURN_REQUIRED, REFUND_PENDING, UNCERTAIN,
    )

    # 이 상태에서는 절대 자동으로 다음 단계를 재시도하지 않는다 —
    # 사람이 실제 쇼핑몰 주문내역을 확인한 뒤에만 수동으로 다음을
    # 결정한다.
    NO_AUTO_RETRY = (BLOCKED, FAILED, UNCERTAIN)

    TERMINAL = (
        COMPLETED, BLOCKED, FAILED,
        SKIPPED_INVENTORY_AVAILABLE, SOURCE_ORDER_CANCELLED,
    )

    # 2026-09-07 V7 통합 매입 감사 후속(HOMEZ_V7_PROCUREMENT_LEDGER_
    # AUDIT_20260907.md §3/§8) — 같은 source_order_item_id에 대해 새
    # 작업을 만들어도 안전한 종결 상태만 여기 둔다. COMPLETED는
    # TERMINAL이지만 실제 매입이 이미 끝났다는 뜻이라 **여기 포함하지
    # 않는다** — 포함하면 중복 매입을 그대로 허용하게 된다(감사가
    # 지적한 정확히 그 결함).
    SAFE_TO_RECREATE_AFTER = (
        BLOCKED, FAILED, SKIPPED_INVENTORY_AVAILABLE, SOURCE_ORDER_CANCELLED,
    )


class PurchaseTaskCreationSource:
    """Gate PT-2 — 이 작업이 어떻게 생겨났는지(UI 필터·표시용).
    상태 전이 규칙에는 관여하지 않는 순수 표시/필터 메타데이터다."""

    MANUAL = "MANUAL"
    ORDER_AUTO = "ORDER_AUTO"

    ALL = (MANUAL, ORDER_AUTO)


class ShoppingMallCode:
    """지시문 명시: 네이버만 구매처로 고정하지 않는다 — 4개를
    동등하게 지원하고, OTHER로 임의 쇼핑몰 후보도 등록 가능하다."""

    NAVER_SHOPPING = "NAVER_SHOPPING"
    ELEVENST = "ELEVENST"
    GMARKET = "GMARKET"
    AUCTION = "AUCTION"
    OTHER = "OTHER"

    ALL = (NAVER_SHOPPING, ELEVENST, GMARKET, AUCTION, OTHER)

    # URL 허용 도메인(작업 B — 위험 스킴·임의 도메인 거부). 정확한
    # 서브도메인까지는 강제하지 않는다(플랫폼이 여러 서브도메인을
    # 씀) — 등록된 최상위 도메인 접미사로만 검증한다.
    ALLOWED_DOMAIN_SUFFIXES = {
        NAVER_SHOPPING: ("naver.com",),
        ELEVENST: ("11st.co.kr",),
        GMARKET: ("gmarket.co.kr",),
        AUCTION: ("auction.co.kr",),
    }


class PurchaseTaskCandidateMatchTier:
    """retail_purchase.constants.SameProductConfidenceTier와 동일한
    값을 그대로 쓴다(중복 정의 대신 그 모듈을 직접 import해서
    재사용 — service.py 참고). 여기서는 문서화 목적으로만 값을
    다시 적어 둔다."""

    AUTO_CANDIDATE = "AUTO_CANDIDATE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"


class BudgetReservationStatus:

    RESERVED = "RESERVED"
    EXTENDED = "EXTENDED"
    RELEASED = "RELEASED"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"

    ALL = (RESERVED, EXTENDED, RELEASED, CONFIRMED, EXPIRED)
    ACTIVE = (RESERVED, EXTENDED)


class PurchaseTaskFailureCode:

    OUT_OF_STOCK = "OUT_OF_STOCK"
    DELIVERY_NOT_AVAILABLE = "DELIVERY_NOT_AVAILABLE"
    PRICE_CHANGED = "PRICE_CHANGED"
    MARGIN_INSUFFICIENT = "MARGIN_INSUFFICIENT"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    BUDGET_RESERVATION_EXPIRED = "BUDGET_RESERVATION_EXPIRED"
    PRODUCT_MATCH_INSUFFICIENT = "PRODUCT_MATCH_INSUFFICIENT"
    DUPLICATE_ORDER_NUMBER = "DUPLICATE_ORDER_NUMBER"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    POLICY_CHANGED_SINCE_CHECK = "POLICY_CHANGED_SINCE_CHECK"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"


class EmailNotificationEventType:

    TASK_CREATED = "TASK_CREATED"
    CANDIDATES_READY = "CANDIDATES_READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    PRICE_CHANGED = "PRICE_CHANGED"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    DELIVERY_NOT_AVAILABLE = "DELIVERY_NOT_AVAILABLE"
    MATCH_UNCERTAIN = "MATCH_UNCERTAIN"
    MARGIN_INSUFFICIENT = "MARGIN_INSUFFICIENT"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    DEADLINE_APPROACHING = "DEADLINE_APPROACHING"
    ORDER_NUMBER_REQUIRED = "ORDER_NUMBER_REQUIRED"
    TRACKING_REQUIRED = "TRACKING_REQUIRED"
    PURCHASE_SUCCESS = "PURCHASE_SUCCESS"
    PURCHASE_FAILED = "PURCHASE_FAILED"
    RESULT_UNCERTAIN = "RESULT_UNCERTAIN"
    SHIPPING_DELAYED = "SHIPPING_DELAYED"
    CANCEL_RETURN_REFUND_REQUIRED = "CANCEL_RETURN_REFUND_REQUIRED"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    # Gate PT-2(16차 지시) 신규 — 이메일 자체의 발송 실패를 최대
    # 재시도 소진 후 사용자에게 알린다(Desktop 알림 경로로만, 이메일
    # 실패를 이메일로 다시 알리려 하지 않는다 — email_service.py 참고).
    EMAIL_DELIVERY_FAILED = "EMAIL_DELIVERY_FAILED"

    ALL = (
        TASK_CREATED, CANDIDATES_READY, REVIEW_REQUIRED, PAYMENT_REQUIRED,
        PRICE_CHANGED, OUT_OF_STOCK, DELIVERY_NOT_AVAILABLE, MATCH_UNCERTAIN,
        MARGIN_INSUFFICIENT, BUDGET_INSUFFICIENT, DEADLINE_APPROACHING,
        ORDER_NUMBER_REQUIRED, TRACKING_REQUIRED, PURCHASE_SUCCESS,
        PURCHASE_FAILED, RESULT_UNCERTAIN, SHIPPING_DELAYED,
        CANCEL_RETURN_REFUND_REQUIRED, EMERGENCY_STOP_ACTIVE,
        EMAIL_DELIVERY_FAILED,
    )


class CapabilitySupport:
    """Gate PT-3(2026-09-08, item 7) — 매입처 Adapter가 특정 기능을
    실제로 확인했는지 여부. 세 값만 있다. SUPPORTED가 아니면 그
    방법(API/공식 문서/스크린샷 조사 등)과 근거를 Adapter 서브클래스
    docstring에 남긴다."""

    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    UNKNOWN = "UNKNOWN"

    ALL = (SUPPORTED, NOT_SUPPORTED, UNKNOWN)


class ChannelCapability:
    """item 7 지시문 3번이 나열한 8개 매입처 Adapter 계약 항목."""

    CONNECTION_CHECK = "CONNECTION_CHECK"
    LOGIN_REQUIREMENT_CHECK = "LOGIN_REQUIREMENT_CHECK"
    PRODUCT_OPTION_PRICE_STOCK_LOOKUP = "PRODUCT_OPTION_PRICE_STOCK_LOOKUP"
    ORDER_FORM_AND_FINAL_AMOUNT = "ORDER_FORM_AND_FINAL_AMOUNT"
    PAYMENT_EXECUTABILITY_CHECK = "PAYMENT_EXECUTABILITY_CHECK"
    EXISTING_ORDER_LOOKUP = "EXISTING_ORDER_LOOKUP"
    SHIPPING_TRACKING_LOOKUP = "SHIPPING_TRACKING_LOOKUP"
    CANCEL_SUPPORT_CHECK = "CANCEL_SUPPORT_CHECK"

    ALL = (
        CONNECTION_CHECK, LOGIN_REQUIREMENT_CHECK,
        PRODUCT_OPTION_PRICE_STOCK_LOOKUP, ORDER_FORM_AND_FINAL_AMOUNT,
        PAYMENT_EXECUTABILITY_CHECK, EXISTING_ORDER_LOOKUP,
        SHIPPING_TRACKING_LOOKUP, CANCEL_SUPPORT_CHECK,
    )


class ChannelConnectionStatus:
    """item 7 지시문 5번 UI 명세("연결됨 / 로그인 필요 / 만료됨 / 추가
    인증 필요 / 오류")와 1:1로 대응한다. NOT_CONNECTED는 그 UI
    명세에는 없지만 "계정을 아직 추가하지 않음"과 "연결했다가 오류가
    난 것"을 구분하기 위해 둔다.

    2026-09-08 사용자 정정 — REGISTERED_UNVERIFIED를 추가한다. 자격
    증명(API 키 등)이 Credential Manager에 저장돼 있다는 사실"만"으로
    CONNECTED로 표시하지 않는다 — "자격증명 등록 여부"와 "실제 인증
    확인 여부(사람이 직접 확인한 시각이 있는지)"는 서로 다른 사실이다.
    CONNECTED는 verified_at이 실제로 있을 때만 쓴다."""

    NOT_CONNECTED = "NOT_CONNECTED"
    REGISTERED_UNVERIFIED = "REGISTERED_UNVERIFIED"
    CONNECTED = "CONNECTED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    EXPIRED = "EXPIRED"
    ADDITIONAL_AUTH_REQUIRED = "ADDITIONAL_AUTH_REQUIRED"
    ERROR = "ERROR"

    ALL = (
        NOT_CONNECTED, REGISTERED_UNVERIFIED, CONNECTED, LOGIN_REQUIRED,
        EXPIRED, ADDITIONAL_AUTH_REQUIRED, ERROR,
    )

    # 실행(매입 작업에 이 연결을 배정) 가능한 상태 — 그 외 상태는
    # 서비스 계층이 항상 명시적으로 차단한다(지시문 4번: 비활성·미검증
    # 연결로 매입 작업을 실행하지 않는다).
    USABLE_FOR_EXECUTION = (CONNECTED,)


class ConnectionMethod:
    """이 매입처 계정을 HOMEZ가 어떤 방식으로 다루는지 — 저장하는
    참조의 종류가 이 값에 따라 달라진다(CREDENTIAL은 credential_
    reference를, BROWSER_LOGIN은 향후 browser_profile_reference를
    쓰되 지금은 실제 자동화가 없으므로 항상 NULL)."""

    CREDENTIAL = "CREDENTIAL"
    BROWSER_LOGIN = "BROWSER_LOGIN"
    UNKNOWN = "UNKNOWN"

    ALL = (CREDENTIAL, BROWSER_LOGIN, UNKNOWN)


class PurchaseChannelMallCode:
    """item 7(2026-09-08 후속) — "최초 지원 매입처와 연결 방식을
    확정한다" 지시에 따라 확정한 목록. docs/HOMEZ_V7_PROCUREMENT_
    CHANNEL_SURVEY_20260907.md §2 조사 결과: 네이버쇼핑·11번가·
    G마켓·옥션은 "구매자로서 발주"하는 공식 API가 없다고 확인됨
    (판매자용 API만 존재) — 그래서 이 4개는 전부 BROWSER_LOGIN
    (사람이 직접 로그인·결제, HOMEZ는 자동 로그인하지 않음)이다.
    온채널은 실제 사업자 계정·API 키 발급 이력이 있어 CREDENTIAL로
    분류하지만, 발주 API의 실제 기술 문서를 아직 확보하지 못해
    "자격증명 등록 확인"까지만 실제로 지원한다(그 이상은 미확인
    상태를 정직하게 유지).

    ONCHANNEL을 app/domains/purchase_task/constants.py의 기존
    ShoppingMallCode(구매 후보 URL 허용 도메인 검증용, 별개 개념)에
    섞지 않는다 — 이 목록은 "매입처 연결 계정"(PurchaseChannelConnection)
    전용이다."""

    NAVER_SHOPPING = ShoppingMallCode.NAVER_SHOPPING
    ELEVENST = ShoppingMallCode.ELEVENST
    GMARKET = ShoppingMallCode.GMARKET
    AUCTION = ShoppingMallCode.AUCTION
    ONCHANNEL = "ONCHANNEL"
    OTHER = ShoppingMallCode.OTHER

    ALL = (NAVER_SHOPPING, ELEVENST, GMARKET, AUCTION, ONCHANNEL, OTHER)

    # 서버가 신뢰하는 연결 방식 — 클라이언트가 요청 바디로 보낸
    # connection_method는 무시하고 이 매핑으로 덮어쓴다(품목 3번 지시:
    # "자격증명 등록 또는 사용자 직접 로그인 흐름을 구현한다"를 매입처
    # 코드 기준으로 서버가 강제한다).
    CONNECTION_METHOD_BY_MALL = {
        NAVER_SHOPPING: ConnectionMethod.BROWSER_LOGIN,
        ELEVENST: ConnectionMethod.BROWSER_LOGIN,
        GMARKET: ConnectionMethod.BROWSER_LOGIN,
        AUCTION: ConnectionMethod.BROWSER_LOGIN,
        ONCHANNEL: ConnectionMethod.CREDENTIAL,
        OTHER: ConnectionMethod.UNKNOWN,
    }

    @classmethod
    def resolve_connection_method(cls, mall_code: str) -> str:

        return cls.CONNECTION_METHOD_BY_MALL.get(mall_code, ConnectionMethod.UNKNOWN)


# 사람이 직접 로그인하는 매입처(BROWSER_LOGIN)의 "연결 확인 완료"를
# HOMEZ가 계속 CONNECTED로 신뢰하는 최대 기간. HOMEZ는 그 세션을
# 기술적으로 확인할 방법이 전혀 없으므로(자동 로그인 금지 원칙 —
# docs/HOMEZ_V7_RETAIL_PURCHASE_SAFETY_GUIDE.md), 사람의 마지막 확인
# 시각 자체를 만료시켜 재확인을 유도하는 것이 유일하게 정직한
# 안전장치다. 실제 각 쇼핑몰의 세션 정책과는 무관한, HOMEZ 자체의
# 보수적인 신뢰 기간이다.
BROWSER_LOGIN_TRUST_WINDOW_DAYS = 14


# 2026-09-08 후속(7번 결함 마무리, "인증 최신성 결함 처리") —
# CREDENTIAL 방식(현재 온채널) 연결의 verified_at도 무기한 유효한
# 인증으로 신뢰하지 않는다. 온채널의 공식 토큰/세션 만료 정책은
# 스펙 어디에도 없다(docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md
# 확인) — 그래서 이 값은 "온채널이 이 기간 뒤에 토큰을 만료시킨다"는
# 뜻이 **아니다**. BROWSER_LOGIN_TRUST_WINDOW_DAYS와 동일한 이유로,
# 실제 매입처 정책과 무관한 HOMEZ 자체의 보수적인 재확인 주기다 —
# 확인 정책이 공식적으로 밝혀지기 전까지, 오래된 성공 기록을 계속
# CONNECTED로 표시하는 대신 EXPIRED(재확인 필요)로 낮춰 실제로는
# 최신 상태를 모른다는 사실을 화면에 정직하게 드러낸다. 공식 정책이
# 확인되면 이 값을 그 정책에 맞게 교체하고 근거를
# docs/HOMEZ_PROJECT_STATE.md에 기록한다.
CREDENTIAL_REVERIFICATION_WINDOW_HOURS = 24


class OnchannelOrderContractItem:
    """2026-09-09 후속("계약 상태 세분화") — 온채널 실제 발주 실행에
    필요한 공식 계약 사실을 하나의 boolean으로 뭉뚱그리지 않는다.
    단일 boolean이었을 때의 위험: 그 값 하나를 실수로(또는 4개 중
    1개 답변만 받고 성급하게) True로 바꾸면 나머지 3개가 여전히
    미확인이어도 전체 발주가 즉시 열린다 — 감사 결과 이 위험이
    실제로 존재했다. 4개 항목을 독립적으로 추적해 이 위험을
    구조적으로 없앤다: 항목 하나가 확인돼도 나머지가 미확인이면
    전체 판정은 여전히 막힌다(is_onchannel_order_contract_fully_
    confirmed 참고)."""

    SALES_APPLICATION = "SALES_APPLICATION"
    PAYMENT_SOURCE = "PAYMENT_SOURCE"
    DUPLICATE_PREVENTION = "DUPLICATE_PREVENTION"
    RESULT_RECONCILIATION = "RESULT_RECONCILIATION"

    ALL = (
        SALES_APPLICATION, PAYMENT_SOURCE, DUPLICATE_PREVENTION,
        RESULT_RECONCILIATION,
    )

    LABELS = {
        SALES_APPLICATION: "판매신청 필요 조건·승인 확인 방법",
        PAYMENT_SOURCE: "발주 시 결제 발생 시점·방식·잔액 부족 처리",
        DUPLICATE_PREVENTION: "sale_code 유일성 범위·중복 요청 서버측 응답",
        RESULT_RECONCILIATION: "타임아웃 등 결과 불명 후 안전한 주문 재조회 방법",
    }


@dataclass(frozen=True)
class OnchannelOrderContractItemStatus:
    """계약 항목 하나의 확인 상태. confirmed=True 하나만으로는
    "제대로 확인됨"으로 치지 않는다 — 공식 근거(문의 답변 요약이나
    문서 링크)와 확인 시각이 함께 있어야만 한다(is_properly_confirmed).
    이렇게 하면 "근거 없이 값만 바꿔치기"가 코드 구조상 불가능해진다
    — confirmed=True만 적고 official_basis를 비워 두면 여전히
    미확인으로 취급된다."""

    confirmed: bool
    official_basis: str | None
    confirmed_at: datetime | None

    @property
    def is_properly_confirmed(self) -> bool:
        return bool(
            self.confirmed and self.official_basis
            and self.confirmed_at is not None
        )


# 2026-09-09 후속("계약 상태 세분화") — 온채널 문의는 이미 발송됐고
# 공식 답변 대기 중이다(사용자 조치 대기 아님). 답변이 하나씩
# 도착할 때마다 이 딕셔너리의 **해당 항목만** 갱신한다(나머지는
# 손대지 않는다) — confirmed=True와 함께 official_basis·confirmed_at
# 을 반드시 함께 채운다. 갱신 근거는 항상 docs/HOMEZ_PROJECT_STATE.md
# 에도 남긴다. 셋 다 채워야 그 항목이 "제대로 확인됨"으로 인정된다
# (OnchannelOrderContractItemStatus.is_properly_confirmed).
ONCHANNEL_ORDER_CONTRACT_STATUS: dict[str, OnchannelOrderContractItemStatus] = {
    item: OnchannelOrderContractItemStatus(
        confirmed=False, official_basis=None, confirmed_at=None,
    )
    for item in OnchannelOrderContractItem.ALL
}


def is_onchannel_order_contract_fully_confirmed() -> bool:
    """verify_connection_ready_for_order_submission()이 부르는 단일
    진입점 — 4개 항목 전부가 "제대로 확인됨"이어야 True다. order_
    submission_service.submit_order()의 confirm_real_submission
    하드 게이트나 라우터 미배선은 "지금 실행을 막아 둔 임시 조치"일
    뿐 이 판정 자체를 대신하지 않는다(그 두 조치가 사라져도 이
    함수가 False를 반환하는 한 여전히 막는다)."""

    return all(
        status.is_properly_confirmed
        for status in ONCHANNEL_ORDER_CONTRACT_STATUS.values()
    )


def unconfirmed_onchannel_order_contract_items() -> tuple[str, ...]:
    """아직 (제대로) 확인되지 않은 항목의 한글 설명 — 발주 준비
    판정 실패 메시지와 화면 안내에 그대로 쓴다."""

    return tuple(
        OnchannelOrderContractItem.LABELS[item]
        for item in OnchannelOrderContractItem.ALL
        if not ONCHANNEL_ORDER_CONTRACT_STATUS[item].is_properly_confirmed
    )


class ChannelConnectionEventType:
    """PurchaseChannelConnectionEvent(append-only 감사 로그)의
    이벤트 종류."""

    CREATED = "CREATED"
    VERIFIED = "VERIFIED"
    LABEL_RENAMED = "LABEL_RENAMED"
    STATUS_CHANGED = "STATUS_CHANGED"
    DEACTIVATED = "DEACTIVATED"
    REACTIVATED = "REACTIVATED"

    ALL = (
        CREATED, VERIFIED, LABEL_RENAMED, STATUS_CHANGED,
        DEACTIVATED, REACTIVATED,
    )


class EmailSendStatus:

    SENT = "SENT"
    FAILED = "FAILED"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    SKIPPED_OPTED_OUT = "SKIPPED_OPTED_OUT"

    ALL = (SENT, FAILED, PROVIDER_NOT_CONFIGURED, SKIPPED_OPTED_OUT)


class OrderSubmissionStatus:
    """2026-09-08 후속("확인된 발주 계약 구현") —
    PurchaseOrderSubmissionAttempt.status. "결과 불명 상태에서
    자동 재시도하지 않는다"는 이 저장소 전역 원칙을 상태 하나로
    표현한다 — RESULT_UNKNOWN은 FAILED가 아니다(실패로 단정하지
    않는다는 뜻 그 자체).

    상태 전이: PENDING(행 생성, 아직 전송 안 함) -> IN_FLIGHT(HTTP
    전송 시작, 응답 대기) -> 정확히 하나로 종결:
      - SUCCEEDED(온채널이 order_code를 돌려줌)
      - REJECTED(온채널이 명시적으로 거부 — 400/401/403/404/409 등,
        "안 됐다"는 사실 자체는 확실하다)
      - RESULT_UNKNOWN(타임아웃·연결 끊김·응답 형식 오류 등 — 실제로
        온채널 서버에 도달해 주문이 생겼는지조차 이 프로세스는 알
        수 없다. 프로세스가 재시작돼도 이 상태는 그대로 남는다 —
        "재시작 복구"란 재시작 후에도 이 사실을 잃지 않고 그대로
        보여준다는 뜻이지, 자동으로 성공/실패를 추정한다는 뜻이
        아니다)."""

    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"

    ALL = (PENDING, IN_FLIGHT, SUCCEEDED, REJECTED, RESULT_UNKNOWN)

    # 같은 idempotency_key로 새 시도를 다시 만들 수 없는 상태 —
    # PENDING/IN_FLIGHT(이미 진행 중이거나 진행 예정)와 SUCCEEDED
    # (이미 성공한 발주를 또 보낼 이유가 없다)는 항상 잠금 대상이다.
    # REJECTED와 RESULT_UNKNOWN도 잠금 대상이다 — "왜 막혔는지"를
    # 사람이 직접 확인한 뒤 새 idempotency_key로 다시 시도해야 한다
    # (같은 키의 자동 재시도를 이 서비스가 스스로 풀어주지 않는다).
    LOCKED = (PENDING, IN_FLIGHT, SUCCEEDED, REJECTED, RESULT_UNKNOWN)


__all__ = [
    "PurchaseTaskStatus",
    "ShoppingMallCode",
    "PurchaseTaskCandidateMatchTier",
    "BudgetReservationStatus",
    "PurchaseTaskFailureCode",
    "EmailNotificationEventType",
    "CapabilitySupport",
    "ChannelCapability",
    "ChannelConnectionStatus",
    "ConnectionMethod",
    "PurchaseChannelMallCode",
    "BROWSER_LOGIN_TRUST_WINDOW_DAYS",
    "CREDENTIAL_REVERIFICATION_WINDOW_HOURS",
    "OnchannelOrderContractItem",
    "OnchannelOrderContractItemStatus",
    "ONCHANNEL_ORDER_CONTRACT_STATUS",
    "is_onchannel_order_contract_fully_confirmed",
    "unconfirmed_onchannel_order_contract_items",
    "ChannelConnectionEventType",
    "EmailSendStatus",
    "OrderSubmissionStatus",
]
