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


# 2026-09-15 전면 감사 후속(Phase 9, HOMEZ_USER_OPERATION_SETTINGS.md
# 8-16 — "매입처 조회 실패가 계속되면 사용자에게 알린다"). 실제
# 조회(lookup_product/list_products 등)가 이 횟수만큼 연속으로
# 실패하면(성공이 한 번도 끼지 않고) 사용자에게 알림을 보낸다.
# 값을 낮게 잡을수록 알림이 빨리 오지만 일시적 네트워크 잡음에도
# 민감해진다 — 3회는 "우연 1~2회"와 "실제로 계속 안 됨"을 구분하는
# 가장 보수적인 최소값으로 선택했다(문서에 구체적 횟수가 명시되어
# 있지 않으므로 추측하지 않고, 이 상수 하나로 조정 가능하게 둔다).
CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD = 3


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


# 2026-09-10 — 온채널 공식 답변 도착, 4개 항목 전부 갱신(사용자가
# 온채널에 직접 문의해 받은 답변을 대화로 전달 — docs/HOMEZ_
# ONCHANNEL_OPENAPI_FINDINGS_20260908.md의 "온채널 문의할 질문" 절
# 6개 질문 중 1~5번에 대응, 6번(인증키 발급 상태 조회)은 이번 답변에
# 포함되지 않아 여전히 미확인 — 이 항목의 판정 대상이 아님).
#
# 잔존 세부 불확실성(각 항목 official_basis에도 명시): (1) 판매신청
# "승인 여부"를 직접 조회하는 별도 API는 없다 — 이건 확인 안 된 게
# 아니라 "그런 API가 없다"는 것 자체가 확인된 사실이다(product/apply
# 응답과 실제 order/regist 결과로만 간접 판단 가능, Phase 3 구현이
# 이 판단 로직을 정확히 그렇게 만든다). (2) 잔액 부족 시 온채널이
# 정확히 어떤 오류 코드를 주는지는 답변에 없으나, HOMEZ 자체 사전
# 포인트 검사(Phase 4)가 그 응답을 받기 전에 항상 먼저 차단하므로
# 실제 위험으로 이어지지 않는다. 이 두 잔존 불확실성은 "그래서 이
# 항목을 계속 미확인으로 둔다"가 아니라 "그래서 이렇게 구현한다"는
# 설계 근거로 이미 반영됐다 — 자세한 내용은 각 basis 문자열과
# docs/HOMEZ_PROJECT_STATE.md 2026-09-10 절 참고.
ONCHANNEL_ORDER_CONTRACT_STATUS: dict[str, OnchannelOrderContractItemStatus] = {
    OnchannelOrderContractItem.SALES_APPLICATION: OnchannelOrderContractItemStatus(
        confirmed=True,
        official_basis=(
            "온채널 공식 답변(2026-09-10, 사용자 문의): 발주 전 판매신청이 "
            "필수다. 판매신청 승인 여부를 조회하는 별도 API는 없다(product/"
            "apply 응답과 이후 실제 order/regist 호출 결과로만 간접 판단) "
            "— '없다'는 사실이 확인된 답이며 추측으로 대체하지 않는다."
        ),
        confirmed_at=datetime(2026, 9, 10, 23, 0, 0),
    ),
    OnchannelOrderContractItem.PAYMENT_SOURCE: OnchannelOrderContractItemStatus(
        confirmed=True,
        official_basis=(
            "온채널 공식 답변(2026-09-10, 사용자 문의): 발주는 사전 충전된 "
            "포인트(예치금) 차감 방식이다. GET /openapi/common/member/point"
            "의 point 필드가 발주 가능 잔액이다. 잔액 부족 시 온채널 서버의 "
            "정확한 오류 코드는 답변에 없으나, HOMEZ가 발주 전 point를 먼저 "
            "조회해 소요 예상 금액과 비교·차단하므로(Phase 4) 그 응답을 "
            "받을 필요 자체가 없도록 설계했다."
        ),
        confirmed_at=datetime(2026, 9, 10, 23, 0, 0),
    ),
    OnchannelOrderContractItem.DUPLICATE_PREVENTION: OnchannelOrderContractItemStatus(
        confirmed=True,
        official_basis=(
            "온채널 공식 답변(2026-09-10, 사용자 문의): 동일 sale_code로 "
            "중복 발주해도 온채널 서버가 제한하지 않는다(서버측 멱등성 "
            "보장 없음, 명확히 확인). 따라서 HOMEZ 자체 (company_id, "
            "idempotency_key) UNIQUE 제약(PurchaseOrderSubmissionAttempt, "
            "Gate PT-3에서 이미 구현·검증됨)이 유일한 중복 방지 수단임이 "
            "확정됐다."
        ),
        confirmed_at=datetime(2026, 9, 10, 23, 0, 0),
    ),
    OnchannelOrderContractItem.RESULT_RECONCILIATION: OnchannelOrderContractItemStatus(
        confirmed=True,
        official_basis=(
            "온채널 공식 답변(2026-09-10, 사용자 문의): 주문 생성 확인 "
            "기준은 HTTP 200 + order_code 존재다. sale_code로 이후 주문을 "
            "재조회하는 기능은 없다(명확히 확인 — '없다'는 사실 자체가 "
            "답). 따라서 타임아웃 등 결과 불명 상황에서 안전하게 자동 "
            "재조회할 방법이 없다는 것이 확정됐다 — RESULT_UNKNOWN은 "
            "영구적으로 사람이 온채널 자체 주문내역에서 직접 대조해 수동 "
            "해소해야 하며, 자동 재시도·자동 재조회는 선택이 아니라 "
            "구조적으로 불가능함이 공식적으로 확정됐다."
        ),
        confirmed_at=datetime(2026, 9, 10, 23, 0, 0),
    ),
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


class SalesApplicationStatus:
    """2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수"
    확정) — PurchaseSalesApplicationAttempt.status. OrderSubmissionStatus와
    같은 이유로 같은 5단계 상태를 쓰지만, 이름은 SUCCEEDED가 아니라
    SUBMITTED다 — "성공"이라는 말이 "승인됐다"로 오독될 위험이
    있어서다. 이 상태가 표현하는 것은 정확히 "온채널이 신청 접수를
    HTTP 200으로 확인했다"는 사실 하나뿐이다 — 실제 승인 여부는
    이 상태만으로 알 수 없고(승인 상태 조회 API 자체가 없다고
    공식 답변으로 확정됨), 이 저장소는 그 사실을 추측으로 메우지
    않는다.

    상태 전이는 OrderSubmissionStatus와 동일하다: PENDING ->
    IN_FLIGHT -> 정확히 하나로 종결(SUBMITTED/REJECTED/RESULT_
    UNKNOWN). 다만 발주와 달리 판매신청은 "같은 상품을 다시 신청해도
    금전적 중복 위험이 없다"(스펙상 요청 바디에 결제·금액 필드가
    아예 없다) — 그래서 이 상태는 OrderSubmissionStatus.LOCKED처럼
    "같은 키로 재시도 금지"를 강제하지 않는다. REJECTED/RESULT_
    UNKNOWN이었던 행은 (company_id, connection_id, product_code)
    UNIQUE 제약 위에서 같은 행을 갱신하며 재시도할 수 있다(append-only
    잠금이 아니라 PurchaseChannelConnection과 같은 현재상태 갱신형
    행이다)."""

    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"

    ALL = (PENDING, IN_FLIGHT, SUBMITTED, REJECTED, RESULT_UNKNOWN)

    # 발주 전 게이트를 통과시켜도 되는 유일한 상태 — SUBMITTED만
    # "접수 확인됨"이다. PENDING/IN_FLIGHT(아직 끝나지 않음)는 물론
    # REJECTED/RESULT_UNKNOWN도 통과시키지 않는다(둘 다 "접수됐다"는
    # 사실을 확인하지 못한 상태이기 때문 — 추측으로 통과시키지 않는다).
    SATISFIES_ORDER_GATE = (SUBMITTED,)


class ShippingCostConfirmationSource:
    """2026-09-11 후속(반자동 완료 라운드, Phase 5) — 온채널에
    배송비를 사전 확정할 공식 API가 없다는 사실이 확정됐으므로
    (docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md "정정
    (2026-09-11)" 절), 사용자가 외부 화면에서 직접 확인한 값을
    근거와 함께 입력하는 반자동 보완책의 출처 종류. 이 값 자체가
    "배송비를 안다"는 증거가 아니다 — "사람이 어디서 확인했다고
    주장하는지"만 기록한다(감사 목적)."""

    ONCHANNEL_PRODUCT_PAGE = "ONCHANNEL_PRODUCT_PAGE"
    SUPPLIER_NOTICE = "SUPPLIER_NOTICE"
    ONCHANNEL_SUPPORT_ANSWER = "ONCHANNEL_SUPPORT_ANSWER"
    OTHER_USER_CONFIRMED = "OTHER_USER_CONFIRMED"

    ALL = (
        ONCHANNEL_PRODUCT_PAGE, SUPPLIER_NOTICE,
        ONCHANNEL_SUPPORT_ANSWER, OTHER_USER_CONFIRMED,
    )

    LABELS_KO = {
        ONCHANNEL_PRODUCT_PAGE: "온채널 상품 화면 확인",
        SUPPLIER_NOTICE: "공급사 안내 확인",
        ONCHANNEL_SUPPORT_ANSWER: "온채널 고객센터 답변",
        OTHER_USER_CONFIRMED: "기타 사용자 확인",
    }


class PurchaseOrderApprovalStatus:
    """2026-09-11 후속(Phase 5·7) — PurchaseOrderApproval.status.
    "발주 최종 승인" 1건의 현재 상태. ACTIVE만 실제 발주(Gate D/E)를
    통과시킨다. 승인 유효시간(기본 10분)이 지나거나, 배송비·가격이
    확인 시점과 달라지면 즉시 무효화한다 — 무효화된 승인을 그대로
    두고 재확인만 나중에 하는 것을 금지한다(그 사이 실제 발주가
    끼어들 여지를 구조적으로 없앤다)."""

    # 배송비를 아직 확인하지 않은 초기 상태(행은 생성됐지만 승인
    # 절차가 시작되지 않음) — 이 상태에서는 "발주 버튼"이 비활성이다.
    PENDING_SHIPPING_COST = "PENDING_SHIPPING_COST"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    INVALIDATED_PRICE_CHANGE = "INVALIDATED_PRICE_CHANGE"
    INVALIDATED_SHIPPING_CHANGE = "INVALIDATED_SHIPPING_CHANGE"
    CONSUMED = "CONSUMED"
    SUPERSEDED = "SUPERSEDED"

    ALL = (
        PENDING_SHIPPING_COST, ACTIVE, EXPIRED, INVALIDATED_PRICE_CHANGE,
        INVALIDATED_SHIPPING_CHANGE, CONSUMED, SUPERSEDED,
    )

    # 실제 발주를 허용하는 유일한 상태.
    SATISFIES_ORDER_GATE = (ACTIVE,)


class UnknownResolutionStatus:
    """2026-09-11 후속(운영 전 최종 검증 라운드, 지시문 5번) —
    PurchaseOrderSubmissionAttempt.unknown_resolution_status.
    RESULT_UNKNOWN 상태의 발주 시도를 사람이 온채널 관리자 화면에서
    직접 확인해 확정하는 절차의 결과값. 온채널은 sale_code 조회를
    지원하지 않으므로 이 확인은 항상 사람이 수행한다 — HOMEZ가
    자동으로 UNKNOWN을 FAILED나 SUCCEEDED로 바꾸는 경로는 어디에도
    없다."""

    # 기본값 — 아직 아무도 확인하지 않음.
    UNRESOLVED = "UNRESOLVED"
    # 온채널 관리자 화면에서 실제 주문이 생성된 것을 확인함
    # (order_code 필수 입력).
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    # 온채널 관리자 화면에서 주문이 생성되지 않은 것을 확인함
    # (근거 메모 필수 입력). 확정 후에도 같은 idempotency_key로는
    # 여전히 재발주할 수 없다 — 사용자가 새 발주안을 만들어야 한다.
    ORDER_NOT_CONFIRMED = "ORDER_NOT_CONFIRMED"
    # 사용자가 확인을 시도했으나 아직 판단할 수 없음 — 이 상태도
    # UNRESOLVED와 마찬가지로 후속 발주를 계속 차단한다.
    STILL_UNCLEAR = "STILL_UNCLEAR"

    ALL = (UNRESOLVED, ORDER_CONFIRMED, ORDER_NOT_CONFIRMED, STILL_UNCLEAR)

    # 이 상태들 중 하나인 동안은 해당 PurchaseTask의 모든 새 발주
    # 시도를 차단한다(order_submission_service.py::
    # _has_unresolved_unknown_attempt).
    BLOCKS_RETRY = (UNRESOLVED, STILL_UNCLEAR)


class TrackingRefreshResult:
    """2026-09-11 후속(운영 전 최종 검증 라운드, 지시문 6번) —
    PurchaseTaskTrackingInfo.last_live_refresh_result. 실제 매입처
    API로 송장을 다시 조회한 결과 — 조회 실패는 배송조회 실패로만
    기록하고 기존에 저장된 실제 발주 성공 상태(PurchaseTask.status
    등)는 절대 바꾸지 않는다."""

    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    MULTIPLE_DELIVERIES = "MULTIPLE_DELIVERIES"
    NOT_FOUND = "NOT_FOUND"
    LOOKUP_FAILED = "LOOKUP_FAILED"

    ALL = (UPDATED, UNCHANGED, MULTIPLE_DELIVERIES, NOT_FOUND, LOOKUP_FAILED)


# 2026-09-11 후속(Phase 7) — 사용자 지시 원문의 "권장 시작 기준".
# PurchaseTaskPolicySetting에 회사가 명시적으로 값을 설정하지
# 않았으면(None) 이 상수를 대신 쓴다 — 기존 후보 평가 단계
# (policy_service.py)의 "설정 없으면 무제한"과 다른 규칙이다.
# 이유: 이 게이트는 실제 온채널 발주(되돌릴 수 없는 금전 행동)
# 직전이므로, 설정을 안 했다는 사실을 "무제한 허용"으로 읽지 않고
# 안전한 시작값으로 읽는다.
RECOMMENDED_PER_ORDER_MAX_AMOUNT = 50_000
RECOMMENDED_DAILY_PURCHASE_LIMIT_AMOUNT = 100_000
# 2026-09-12 후속(V7 기준선 정리, Phase 4 자동결제 한도 실행경로
# 감사) — per_order_max_amount·daily_purchase_limit_amount와 같은
# 이유로 월간 한도도 같은 게이트에서 같은 규칙을 따라야 하는데,
# 실제로는 빠져 있었다(order_approval_service.py::finalize_approval이
# per_order_max/daily만 재확인하고 monthly_purchase_budget_amount는
# PurchaseTask 생성 시점의 policy_service.evaluate()에서만 한 번
# 확인되고 이 최종 게이트에서는 재확인되지 않았다).
RECOMMENDED_MONTHLY_PURCHASE_BUDGET_AMOUNT = 500_000
RECOMMENDED_MIN_MARGIN_RATE = 0.15
RECOMMENDED_MIN_NET_PROFIT = 5_000
RECOMMENDED_MIN_RESIDUAL_POINTS = 100_000
RECOMMENDED_APPROVAL_VALIDITY_MINUTES = 10


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
    "SalesApplicationStatus",
    "ShippingCostConfirmationSource",
    "PurchaseOrderApprovalStatus",
    "UnknownResolutionStatus",
    "TrackingRefreshResult",
    "RECOMMENDED_PER_ORDER_MAX_AMOUNT",
    "RECOMMENDED_DAILY_PURCHASE_LIMIT_AMOUNT",
    "RECOMMENDED_MONTHLY_PURCHASE_BUDGET_AMOUNT",
    "RECOMMENDED_MIN_MARGIN_RATE",
    "RECOMMENDED_MIN_NET_PROFIT",
    "RECOMMENDED_MIN_RESIDUAL_POINTS",
    "RECOMMENDED_APPROVAL_VALIDITY_MINUTES",
]
