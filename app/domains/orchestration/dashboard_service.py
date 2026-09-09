"""
=========================================================
Homez OS

File : app/domains/orchestration/dashboard_service.py

운영 Dashboard 집계(M, 2026-08-20) — 전부 실제 테이블에서 직접
COUNT한다. 집계 방법이 아직 없는 항목(배송 지연 임계값, 정산 차이
계산 등)은 None으로 반환한다 — 0이나 임의 숫자로 채워 넣지 않는다
(요구사항: "집계 API가 없으면 허위 숫자를 표시하지 않는다").
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.order.constants import OrderItemStatus
from app.domains.order.model import OrderItem
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.purchase.constants import PurchaseStatus
from app.domains.purchase.constants import PurchaseSubmissionStatus
from app.domains.purchase.model import Purchase
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.model import ReturnOrder


class DashboardService:

    def __init__(self, db: Session):

        self.db = db

    def _estop_active(self) -> bool:

        from app.domains.automation_safety.model import EmergencyStop

        latest = (
            self.db.query(EmergencyStop)
            .order_by(EmergencyStop.id.desc())
            .first()
        )

        return latest is not None and latest.is_active

    def get_summary(self, company_id: int) -> dict:
        # Audit(2026-08-21, AG-0) — 되돌림: Dashboard 요약은 실제
        # 테이블 COUNT 조회일 뿐이다 — 단순 조회가 AI 비활성 때문에
        # 실패하면 안 된다. OPERATIONS_COORDINATION은 향후 "다음 작업
        # 우선순위 제안" 같은 AI 추천 기능(아직 미구현)에만 적용한다.

        candidates_needing_input = (
            self.db.query(ProductCandidate)
            .filter(
                (ProductCandidate.owner_company_id == company_id)
                | (ProductCandidate.visibility == "GLOBAL"),
            )
            .filter(ProductCandidate.status == CandidateStatus.DISCOVERED)
            .count()
        )

        out_of_stock_items = (
            self.db.query(OrderItem)
            .filter(OrderItem.company_id == company_id)
            .filter(OrderItem.status == OrderItemStatus.OUT_OF_STOCK)
            .count()
        )

        purchase_approval_pending = (
            self.db.query(Purchase)
            .filter(Purchase.company_id == company_id)
            .filter(Purchase.status == PurchaseStatus.REQUESTED)
            .count()
        )

        # 2026-08-21 작업 3 — 실제로 발견·수정한 결함: 기존 쿼리는
        # Purchase.status==CANCELLED(사용자가 의도적으로 취소한 발주)를
        # "실패"로 잘못 집계했다. "발주 실패"가 실제로 의미하는 것은
        # submission_status==FAILED(공급처 전송이 실패해 재시도가
        # 필요한 상태)다 — 이 컬럼은 CANCELLED보다 나중에 추가된
        # additive 컬럼이라 이 쿼리가 갱신되지 않았던 것으로 보인다.
        purchase_failed = (
            self.db.query(Purchase)
            .filter(Purchase.company_id == company_id)
            .filter(
                Purchase.submission_status == PurchaseSubmissionStatus.FAILED,
            )
            .count()
        )

        return_refund_pending = (
            self.db.query(ReturnOrder)
            .filter(ReturnOrder.company_id == company_id)
            .filter(ReturnOrder.status.notin_(
                (ReturnOrderStatus.COMPLETED, ReturnOrderStatus.REJECTED),
            ))
            .count()
        )

        return {
            "product_input_required": candidates_needing_input,
            "image_required": None,
            "coupang_submission_approval_pending": None,
            "new_orders": None,
            "out_of_stock_items": out_of_stock_items,
            "supplier_selection_required": out_of_stock_items,
            "purchase_approval_pending": purchase_approval_pending,
            "purchase_failed": purchase_failed,
            "shipment_delayed": None,
            "return_refund_pending": return_refund_pending,
            "settlement_variance": None,
            "estop_active": self._estop_active(),
        }


__all__ = [
    "DashboardService",
]
