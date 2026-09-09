"""
=========================================================
Homez OS

File : app/domains/orchestration/router.py

읽기 전용 projection API — admin_guard로 보호, company 격리.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.orchestration.dashboard_service import DashboardService
from app.domains.orchestration.priority_service import (
    OperationsPriorityService,
)
from app.domains.orchestration.projection_service import (
    OrchestrationProjectionService,
)
from app.domains.user.model import User

router = APIRouter(prefix="/orchestration", tags=["Orchestration"])


@router.get("/order-items/{order_item_id}/stage")
def get_order_item_stage(
    order_item_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrchestrationProjectionService(db)
    stage = service.project_order_item_stage(
        order_item_id, current_user.company_id,
    )

    return {"order_item_id": order_item_id, "stage": stage}


@router.get("/dashboard/summary")
def get_dashboard_summary(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DashboardService(db)

    return service.get_summary(current_user.company_id)


@router.get("/priorities")
def get_operations_priorities(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """Gate AI-F2(2026-08-22) — 운영 우선순위 읽기 전용 집계. 위
    /dashboard/summary(단순 COUNT)와 완전히 별개다 — AI Capability
    상태와 무관하게 그 엔드포인트는 항상 그대로 동작한다."""

    service = OperationsPriorityService(db)
    items, envelope = service.analyze(current_user.company_id)

    return {
        "items": [
            {
                "category": i.category,
                "urgency": i.urgency,
                "label": i.label,
                "count": i.count,
                "source_capability": i.source_capability,
            }
            for i in items
        ],
        "ai_result": envelope.model_dump(mode="json"),
    }


__all__ = [
    "router",
]
