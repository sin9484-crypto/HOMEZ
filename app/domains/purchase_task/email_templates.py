"""
=========================================================
Homez OS

File : app/domains/purchase_task/email_templates.py

작업 G — ko-KR/en-US 이메일 템플릿. 고객 전체 주소·전체 전화번호·
비밀번호·Credential·카드/계좌 정보·전체 외부 주문번호는 절대
넣지 않는다 — 외부 주문번호는 마스킹된 짧은 형태만 허용한다.
=========================================================
"""

from __future__ import annotations

from app.domains.purchase_task.constants import EmailNotificationEventType

_SUBJECTS = {
    "ko-KR": {
        EmailNotificationEventType.TASK_CREATED: "[HOMEZ] 새 구매 작업이 생성되었습니다",
        EmailNotificationEventType.CANDIDATES_READY: "[HOMEZ] 구매 후보가 준비되었습니다",
        EmailNotificationEventType.REVIEW_REQUIRED: "[HOMEZ] 확인이 필요한 구매 작업이 있습니다",
        EmailNotificationEventType.PAYMENT_REQUIRED: "[HOMEZ] 최종 결제가 필요합니다",
        EmailNotificationEventType.PRICE_CHANGED: "[HOMEZ] 가격이 변경되었습니다",
        EmailNotificationEventType.OUT_OF_STOCK: "[HOMEZ] 품절된 후보가 있습니다",
        EmailNotificationEventType.DELIVERY_NOT_AVAILABLE: "[HOMEZ] 배송 불가 지역입니다",
        EmailNotificationEventType.MATCH_UNCERTAIN: "[HOMEZ] 동일상품 판정이 불확실합니다",
        EmailNotificationEventType.MARGIN_INSUFFICIENT: "[HOMEZ] 예상마진이 기준에 미달합니다",
        EmailNotificationEventType.BUDGET_INSUFFICIENT: "[HOMEZ] 매입예산이 부족·초과되었습니다",
        EmailNotificationEventType.DEADLINE_APPROACHING: "[HOMEZ] 구매 기한이 임박했습니다",
        EmailNotificationEventType.ORDER_NUMBER_REQUIRED: "[HOMEZ] 주문번호 입력이 필요합니다",
        EmailNotificationEventType.TRACKING_REQUIRED: "[HOMEZ] 송장번호 입력이 필요합니다",
        EmailNotificationEventType.PURCHASE_SUCCESS: "[HOMEZ] 구매가 완료 처리되었습니다",
        EmailNotificationEventType.PURCHASE_FAILED: "[HOMEZ] 구매 작업이 실패했습니다",
        EmailNotificationEventType.RESULT_UNCERTAIN: "[HOMEZ] 구매 결과가 불명확합니다",
        EmailNotificationEventType.SHIPPING_DELAYED: "[HOMEZ] 배송이 지연되고 있습니다",
        EmailNotificationEventType.CANCEL_RETURN_REFUND_REQUIRED: "[HOMEZ] 취소·반품·환불 처리가 필요합니다",
        EmailNotificationEventType.EMERGENCY_STOP_ACTIVE: "[HOMEZ] Emergency Stop이 활성화되었습니다",
    },
    "en-US": {
        EmailNotificationEventType.TASK_CREATED: "[HOMEZ] A new purchase task was created",
        EmailNotificationEventType.CANDIDATES_READY: "[HOMEZ] Purchase candidates are ready",
        EmailNotificationEventType.REVIEW_REQUIRED: "[HOMEZ] A purchase task needs your review",
        EmailNotificationEventType.PAYMENT_REQUIRED: "[HOMEZ] Final payment is needed",
        EmailNotificationEventType.PRICE_CHANGED: "[HOMEZ] Price has changed",
        EmailNotificationEventType.OUT_OF_STOCK: "[HOMEZ] A candidate is out of stock",
        EmailNotificationEventType.DELIVERY_NOT_AVAILABLE: "[HOMEZ] Delivery is not available",
        EmailNotificationEventType.MATCH_UNCERTAIN: "[HOMEZ] Same-product match is uncertain",
        EmailNotificationEventType.MARGIN_INSUFFICIENT: "[HOMEZ] Expected margin is below threshold",
        EmailNotificationEventType.BUDGET_INSUFFICIENT: "[HOMEZ] Purchase budget is insufficient or exceeded",
        EmailNotificationEventType.DEADLINE_APPROACHING: "[HOMEZ] Purchase deadline is approaching",
        EmailNotificationEventType.ORDER_NUMBER_REQUIRED: "[HOMEZ] Order number entry is needed",
        EmailNotificationEventType.TRACKING_REQUIRED: "[HOMEZ] Tracking number entry is needed",
        EmailNotificationEventType.PURCHASE_SUCCESS: "[HOMEZ] Purchase recorded successfully",
        EmailNotificationEventType.PURCHASE_FAILED: "[HOMEZ] A purchase task failed",
        EmailNotificationEventType.RESULT_UNCERTAIN: "[HOMEZ] Purchase result is uncertain",
        EmailNotificationEventType.SHIPPING_DELAYED: "[HOMEZ] Shipping is delayed",
        EmailNotificationEventType.CANCEL_RETURN_REFUND_REQUIRED: "[HOMEZ] Cancel/return/refund action needed",
        EmailNotificationEventType.EMERGENCY_STOP_ACTIVE: "[HOMEZ] Emergency Stop is active",
    },
}

_BODY_TEMPLATES = {
    "ko-KR": "{product_title} 구매 작업에 확인이 필요합니다.\n\n{detail}\n\nHOMEZ Desktop 콘솔에서 확인하세요.",
    "en-US": "Your purchase task for {product_title} needs attention.\n\n{detail}\n\nPlease check the HOMEZ Desktop console.",
}


def mask_order_number(order_number: str | None) -> str:
    """전체 외부 주문번호를 이메일에 넣지 않는다 — 마지막 4자리만
    남기고 마스킹한다."""

    if not order_number:
        return "-"
    if len(order_number) <= 4:
        return "*" * len(order_number)
    return "*" * (len(order_number) - 4) + order_number[-4:]


def render_subject(event_type: str, locale: str) -> str:

    table = _SUBJECTS.get(locale) or _SUBJECTS["ko-KR"]
    return table.get(event_type, f"[HOMEZ] {event_type}")


def render_body(
    event_type: str, locale: str, *, product_title: str, detail: str,
) -> str:

    template = _BODY_TEMPLATES.get(locale) or _BODY_TEMPLATES["ko-KR"]
    return template.format(product_title=product_title, detail=detail)


__all__ = ["mask_order_number", "render_subject", "render_body"]
