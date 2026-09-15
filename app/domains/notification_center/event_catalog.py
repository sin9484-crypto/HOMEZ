"""
=========================================================
Homez OS

File : app/domains/notification_center/event_catalog.py

Gate PT-3(2026-08-23 17차 지시) — 운영 승인·예외 알림의 중앙
이벤트 카탈로그. 심각도·기본 발송정책을 여기 한 곳에서만 정의한다.

`wired` 필드에 대한 정직한 설명: 이 카탈로그는 지시문이 나열한 모든
업무를 전부 담고 있지만, 실제로 그 이벤트를 발생시키는 코드 호출
지점이 이미 존재하는 것은 그중 일부뿐이다(사전 감사 결과 확인 —
예: 마진 미달 추적, 가격 변경률 한도, 공급처·쿠팡 반품 상태 불일치,
구매자 취소 요청 추적, CAPTCHA/OTP 개입, 앱 업데이트 설치 승인,
백업/복원 확인 흐름은 이 저장소에 아직 그 자체가 구현돼 있지 않다).
`wired=False`인 항목은 이 Gate에서 실제로 발생시키는 코드가 없다 —
카탈로그에 미리 정의해 두어, 그 업무 로직이 나중에 만들어지면 즉시
`NotificationDeliveryService.dispatch()`를 호출하기만 하면 되게
준비해 둔 것뿐이다(추측으로 만든 트리거를 심지 않았다는 뜻).

MIGRATION_APPROVAL_NEEDED도 실제로 연결하려 했으나(app/core/
migration_approval.py::get_migration_status()), 그 GET 엔드포인트는
로그인 화면 진입 전에도 동작해야 하는 의도된 설계라 current_user도
db Session도 받지 않는다 — 이 알림을 누구(어느 회사) 앞으로 보낼지
결정할 근거가 이 지점에 없다. 근거 없이 추측으로 회사/사용자를
정하지 않는다는 원칙에 따라 여기서는 연결하지 않고 wired=False로
남긴다(카탈로그 정의만 존재).

Gate PT-3 인수 보완에서 실제 트리거는 10개로 늘었다. 기존 3개
(EStop 활성화/해제, 이메일 발송 실패)에 상품후보 검토, 상품등록
최종승인, 상품등록 실패, 가격변경 승인, 정산차이 검토, 반품·교환
승인, 판매채널 Credential 재인증을 기존 서비스의 커밋 완료 지점에
연결했다. 나머지는 여전히 실제 호출 지점이 없는 카탈로그 정의다.
ESTOP_DEACTIVATION_APPROVAL_NEEDED는 실행을 막는 사전 승인 게이트가
아니라 해제 실행 직후의 사후 검토 알림으로 구현했다 — 그 차이를
그대로 공개한다(automation_safety Domain 파일을 수정하지 않는다는
원칙과, 새 승인 상태 모델 도입이 이번 Gate 범위를 넘는다는 판단
때문).

purchase_task 도메인의 20개 이메일 이벤트(`purchase_task.constants.
EmailNotificationEventType`)는 이 카탈로그에 포함하지 않는다 — 이미
Gate PT-1/PT-2에서 독립적으로 완성·검증된 별도 시스템이며, 이번
Gate에서 재구현하거나 흡수하지 않는다(중복 도메인 금지 원칙을
반대 방향으로 지킨 것 — 이미 잘 동작하는 것을 건드리지 않는다).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass


class NotificationSeverity:

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    ALL = (CRITICAL, HIGH, MEDIUM, LOW)


class NotificationCategory:

    PRODUCT = "product"
    PRICING = "pricing"
    PURCHASING = "purchasing"
    ORDER = "order"
    RETURN = "return"
    SECURITY = "security"

    ALL = (PRODUCT, PRICING, PURCHASING, ORDER, RETURN, SECURITY)


@dataclass(frozen=True)
class NotificationEventDefinition:

    event_code: str
    category: str
    severity: str
    # Critical 중에서도 "끌 수 없어야" 하는 항목만 True(지시문:
    # "Critical 알림은 비활성화할 수 없도록 검토").
    critical_cannot_disable: bool = False
    # 기본 발송정책 — 사용자가 이벤트별로 override 가능(설정 화면).
    default_email_immediate: bool = False
    default_unconfirmed_wait_minutes: int | None = None
    # 실제로 이 이벤트를 발생시키는 코드 호출 지점이 이 Gate에
    # 존재하는지(정직 공개용 — 위 모듈 docstring 참고).
    wired: bool = False
    description_ko: str = ""
    description_en: str = ""


def _e(*args, **kwargs) -> NotificationEventDefinition:

    return NotificationEventDefinition(*args, **kwargs)


EVENT_CATALOG: dict[str, NotificationEventDefinition] = {
    d.event_code: d for d in [

        # ---------------- A. 상품·판매 ----------------
        _e(
            "CANDIDATE_REVIEW_NEEDED", NotificationCategory.PRODUCT,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=240, wired=True,
            description_ko="상품 후보 검토가 필요합니다.",
            description_en="A product candidate needs review.",
        ),
        _e(
            "LISTING_FINAL_APPROVAL_NEEDED", NotificationCategory.PRODUCT,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="상품 등록 최종 승인이 필요합니다.",
            description_en="Final approval is needed to list a product.",
        ),
        _e(
            "CHANNEL_POLICY_VIOLATION", NotificationCategory.PRODUCT,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="판매채널 정책 위반 또는 필수정보가 부족합니다.",
            description_en="Channel policy violation or missing required fields.",
        ),
        _e(
            "APPROVAL_INVALIDATED_BY_CHANGE", NotificationCategory.PRODUCT,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=240, wired=False,
            description_ko="가격·옵션·이미지·배송조건 변경으로 기존 승인이 무효화됐습니다.",
            description_en="A prior approval was invalidated by a price/option/image/shipping change.",
        ),
        _e(
            "LISTING_SUBMISSION_FAILED", NotificationCategory.PRODUCT,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="쿠팡 상품 등록이 실패했습니다 — 재시도 승인이 필요합니다.",
            description_en="Coupang listing submission failed — retry approval needed.",
        ),

        # ---------------- B. 수익·가격 ----------------
        _e(
            "MARGIN_BELOW_MINIMUM", NotificationCategory.PRICING,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="최소 예상이익 또는 최소 마진율에 미달했습니다.",
            description_en="Below the minimum expected profit or margin rate.",
        ),
        _e(
            "PURCHASE_COST_CHANGED", NotificationCategory.PRICING,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=240, wired=False,
            description_ko="매입가·배송비·수수료가 변경됐습니다.",
            description_en="Purchase cost, shipping fee, or commission changed.",
        ),
        _e(
            "PRICE_CHANGE_APPROVAL_NEEDED", NotificationCategory.PRICING,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="판매가 변경 승인이 필요합니다.",
            description_en="Sale price change needs approval.",
        ),
        _e(
            "PRICE_CHANGE_RATE_EXCEEDED", NotificationCategory.PRICING,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=False,
            description_ko="가격 변경률 한도를 초과했습니다.",
            description_en="Price change rate limit exceeded.",
        ),
        _e(
            "RECONCILIATION_REVIEW_NEEDED", NotificationCategory.PRICING,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=480, wired=True,
            description_ko="정산 차이 확인이 필요합니다.",
            description_en="A settlement reconciliation difference needs review.",
        ),

        # ---------------- C. 매입·발주(purchase_task 자체 20개 이벤트는
        # 별도 시스템 — 여기 포함하지 않는다) ----------------

        # ---------------- D. 주문·배송 ----------------
        _e(
            "ORDER_MANUAL_REVIEW_NEEDED", NotificationCategory.ORDER,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=240, wired=False,
            description_ko="쿠팡 신규 주문 중 수동 확인이 필요한 건이 있습니다.",
            description_en="A new Coupang order needs manual review.",
        ),
        _e(
            "TRACKING_NUMBER_NEEDED", NotificationCategory.ORDER,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=False,
            description_ko="송장번호 입력이 필요합니다.",
            description_en="A tracking number needs to be entered.",
        ),
        _e(
            "TRACKING_SUBMISSION_FAILED", NotificationCategory.ORDER,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=False,
            description_ko="쿠팡 송장 전송이 실패했습니다.",
            description_en="Tracking submission to Coupang failed.",
        ),
        _e(
            "FULFILLMENT_DEADLINE_APPROACHING", NotificationCategory.ORDER,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=180, wired=False,
            description_ko="출고기한이 임박했거나 초과했습니다.",
            description_en="Fulfillment deadline is approaching or has passed.",
        ),
        _e(
            "DELIVERY_DELAYED", NotificationCategory.ORDER,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=480, wired=False,
            description_ko="배송이 지연되고 있습니다.",
            description_en="Delivery is delayed.",
        ),
        _e(
            "DELIVERY_CONFIRMATION_FAILED", NotificationCategory.ORDER,
            NotificationSeverity.LOW, wired=False,
            description_ko="배송 완료 확인에 실패했습니다.",
            description_en="Delivery completion confirmation failed.",
        ),
        _e(
            "PARTIAL_SHIPMENT_MISMATCH", NotificationCategory.ORDER,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=240, wired=False,
            description_ko="부분 출고 또는 송장 불일치가 있습니다.",
            description_en="Partial shipment or tracking mismatch detected.",
        ),

        # ---------------- E. 취소·반품·환불 ----------------
        _e(
            "BUYER_CANCEL_REQUEST", NotificationCategory.RETURN,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=False,
            description_ko="구매자가 취소를 요청했습니다.",
            description_en="A buyer requested a cancellation.",
        ),
        _e(
            "POST_PAYMENT_CANCEL_NEEDED", NotificationCategory.RETURN,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=False,
            description_ko="결제 후 구매 취소 처리가 필요합니다.",
            description_en="A post-payment purchase cancellation is needed.",
        ),
        _e(
            "RETURN_EXCHANGE_APPROVAL_NEEDED", NotificationCategory.RETURN,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="반품·교환 승인이 필요합니다.",
            description_en="A return/exchange needs approval.",
        ),
        _e(
            "PARTIAL_REFUND_DECISION_NEEDED", NotificationCategory.RETURN,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=240, wired=False,
            description_ko="부분환불 또는 재배송 결정이 필요합니다.",
            description_en="A partial refund or reship decision is needed.",
        ),
        _e(
            "SUPPLIER_COUPANG_RETURN_MISMATCH", NotificationCategory.RETURN,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=240, wired=False,
            description_ko="공급처 반품과 쿠팡 반품 상태가 일치하지 않습니다.",
            description_en="Supplier return status and Coupang return status disagree.",
        ),

        # ---------------- F. 보안·시스템 ----------------
        _e(
            "CHANNEL_CREDENTIAL_EXPIRED", NotificationCategory.SECURITY,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="판매채널 Credential이 만료됐거나 재인증이 필요합니다.",
            description_en="A sales channel credential expired or needs reauthentication.",
        ),
        _e(
            "SUPPLIER_LOOKUP_REPEATED_FAILURE", NotificationCategory.SECURITY,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko=(
                "매입처 연결의 실제 조회가 반복해서 실패하고 있습니다 "
                "(HOMEZ_USER_OPERATION_SETTINGS.md 8-16)."
            ),
            description_en=(
                "Real lookups against a purchase channel connection have "
                "failed repeatedly."
            ),
        ),
        _e(
            "ESTOP_ACTIVATED", NotificationCategory.SECURITY,
            NotificationSeverity.CRITICAL, critical_cannot_disable=True,
            default_email_immediate=True, wired=True,
            description_ko="Emergency Stop이 활성화됐습니다.",
            description_en="Emergency Stop has been activated.",
        ),
        _e(
            "ESTOP_DEACTIVATION_APPROVAL_NEEDED", NotificationCategory.SECURITY,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="Emergency Stop 해제 승인이 필요합니다.",
            description_en="Emergency Stop deactivation needs approval.",
        ),
        _e(
            "MIGRATION_APPROVAL_NEEDED", NotificationCategory.SECURITY,
            NotificationSeverity.CRITICAL, critical_cannot_disable=True,
            default_email_immediate=True, wired=False,
            description_ko="데이터베이스 구조 업데이트(Migration) 승인이 필요합니다.",
            description_en="A database schema update (migration) needs approval.",
        ),
        _e(
            "APP_UPDATE_APPROVAL_NEEDED", NotificationCategory.SECURITY,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=1440, wired=False,
            description_ko="앱 업데이트 설치 승인이 필요합니다.",
            description_en="An app update installation needs approval.",
        ),
        _e(
            "BACKUP_RESTORE_CONFIRMATION_NEEDED", NotificationCategory.SECURITY,
            NotificationSeverity.MEDIUM,
            default_unconfirmed_wait_minutes=240, wired=False,
            description_ko="백업·복원 확인이 필요합니다.",
            description_en="A backup/restore action needs confirmation.",
        ),
        _e(
            "NOTIFICATION_EMAIL_DELIVERY_FAILED", NotificationCategory.SECURITY,
            NotificationSeverity.LOW, wired=True,
            description_ko="알림 이메일 발송이 실패했습니다.",
            description_en="A notification email failed to send.",
        ),
        # 2026-09-09 Phase 1(로그인 무차별 대입 방어, HOMEZ_USER_
        # OPERATION_SETTINGS.md 11번 "비밀번호 반복 오류나 이상 접근이
        # 발견되면 로그인을 잠시 차단하고 사용자에게 알린다") —
        # app/domains/auth/service.py::AuthService._notify_account_locked
        # 에서 실제로 발생시킨다(wired=True).
        _e(
            "LOGIN_ACCOUNT_LOCKED", NotificationCategory.SECURITY,
            NotificationSeverity.CRITICAL, critical_cannot_disable=True,
            default_email_immediate=True, wired=True,
            description_ko="연속 로그인 실패로 계정이 잠시 잠겼습니다.",
            description_en="An account was temporarily locked after repeated login failures.",
        ),
        # 2026-09-09 Phase 4(HOMEZ_USER_OPERATION_SETTINGS.md 13번 —
        # "가격 인상, 재고부족, 배송지연, 반품·환불, 결제 한도 초과와
        # AI 근거 부족 항목을 하나의 사용자 확인 목록에 모은다") —
        # app/domains/purchase_task/service.py에서 가격 인상 감지 시
        # 발생시킨다(wired=True). 다른 4개 트리거(재고부족·인증만료·
        # API오류·스키마불일치)는 아직 이 이벤트를 발생시키지 않는다
        # (감지 코드 자체가 다른 Domain에 흩어져 있어 이번 Phase
        # 범위에서는 가격 인상 하나만 실제로 연결했다 — 정직하게
        # 공개).
        _e(
            "FUNCTION_AUTOMATION_DEMOTED_TO_ERROR", NotificationCategory.SECURITY,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="시스템이 감지한 문제로 특정 기능의 자동화가 오류 상태로 낮아졌습니다.",
            description_en="A specific function's automation was demoted to an error state due to a system-detected issue.",
        ),
        # 2026-09-09 Phase 5(HOMEZ_USER_OPERATION_SETTINGS.md 1·11·14번
        # — "DB 복구 가능 여부를 매주 자동 또는 안내 기반으로 시험하고
        # 결과를 기록한다", "실패 알림") —
        # app/domains/restore/service.py::RestoreService.
        # run_weekly_rehearsal()에서 발생시킨다(wired=True). 기존
        # BACKUP_RESTORE_CONFIRMATION_NEEDED(대화형 복원 확인 프롬프트
        # 용, 아직 그 화면 자체가 없어 wired=False로 남아있음)와는
        # 성격이 다른 별개 이벤트다 — 이건 "복구가 되는지 정기적으로
        # 확인해봤더니 실패했다"는 경보다.
        _e(
            "BACKUP_RESTORE_REHEARSAL_FAILED", NotificationCategory.SECURITY,
            NotificationSeverity.HIGH, default_email_immediate=True,
            wired=True,
            description_ko="주간 백업 복구 리허설이 실패했습니다 — 실제 복구가 안 될 수 있습니다.",
            description_en="The weekly backup restore rehearsal failed — real recovery may not work.",
        ),
    ]
}


def get_event_definition(event_code: str) -> NotificationEventDefinition:

    definition = EVENT_CATALOG.get(event_code)
    if definition is None:
        raise KeyError(f"알 수 없는 알림 이벤트입니다: {event_code!r}")
    return definition


__all__ = [
    "NotificationSeverity",
    "NotificationCategory",
    "NotificationEventDefinition",
    "EVENT_CATALOG",
    "get_event_definition",
]
