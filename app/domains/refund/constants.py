"""
=========================================================
Homez OS

File : app/domains/refund/constants.py

2026-09-10 Phase 8(HOMEZ_USER_OPERATION_SETTINGS.md 8·9번 — "취소·
반품·교환·환불을 별도 상태로 관리한다", "반품은 사용자 안내와 승인을
거친 뒤에만 진행한다") — Critical 결함 #2("환불(refund) 도메인이
통째로 빈 스캐폴딩")를 메운다.

`app/domains/return_order`(RETURN/EXCHANGE, 물류 상태 — 회수·검수)
와 이 도메인은 완전히 별개다: return_order는 "물건이 돌아오는
과정"을, 이 refund 도메인은 "돈이 돌아가는 과정"을 다룬다. 하나의
반품이 이 두 도메인 모두에 각각 행을 가질 수 있다(`Refund.
return_order_id` 논리 참조로 연결, FK 없음).
=========================================================
"""

from __future__ import annotations


class RefundType:

    # 고객에게 돌려주는 환불(판매채널 통해 발생한 고객 반품·취소 등).
    CUSTOMER_REFUND = "CUSTOMER_REFUND"
    # 매입처로부터 회수하는 환불(HOMEZ가 매입처에 이미 지불한 대금
    # 중 일부/전부를 돌려받는 경우).
    SUPPLIER_RECLAIM = "SUPPLIER_RECLAIM"

    ALL = (CUSTOMER_REFUND, SUPPLIER_RECLAIM)


class RefundStatus:
    """
    "returns proceed only after user notice + approval"(문서 9번)를
    구조적으로 강제한다 — AWAITING_APPROVAL에서 APPROVED로 가는
    전이는 `RefundService.approve_refund()`를 통해서만, 그리고 그
    메서드는 항상 사람(SuperAdmin)의 명시적 호출을 요구한다. 이
    전이에는 `FunctionMode.AUTOMATIC`이라 해도 예외가 없다 — Phase
    3의 "자동 모드라는 이유만으로 결제·발주·환불 권한이 확대되지
    않는다"는 원칙을 환불에서는 "자동 승인 자체를 아예 만들지
    않는다"는 방식으로 가장 엄격하게 적용했다(Payment Domain의
    AUTOMATIC 자동승인과 의도적으로 다른 설계 — 이유는
    app/domains/refund/service.py 상단 주석 참고).
    """

    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        AWAITING_APPROVAL: {APPROVED, REJECTED},
        APPROVED: {EXECUTED},
        REJECTED: set(),
        EXECUTED: set(),
    }


__all__ = ["RefundType", "RefundStatus"]
