from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.activity_log.schema import (
    ActivityLogCreate,
    ActivityLogResponse,
    ActivityLogListResponse,
)

from app.domains.activity_log.service import (
    ActivityLogService,
)


router = APIRouter(
    prefix="/activity-logs",
    tags=["Activity Logs"],
)


def get_service(
    db: Session = Depends(get_db),
) -> ActivityLogService:

    return ActivityLogService(
        db,
    )


@router.post(
    "",
    response_model=ActivityLogResponse,
)
def create_activity_log(
    data: ActivityLogCreate,
    service: ActivityLogService = Depends(
        get_service
    ),
):

    return service.create(
        **data.model_dump()
    )


@router.get(
    "",
    response_model=ActivityLogListResponse,
)
def get_activity_logs(
    service: ActivityLogService = Depends(
        get_service
    ),
):

    items = service.list()

    return {
        "items": items,
        "total": len(items),
    }
@router.get(
    "/{log_id}",
    response_model=ActivityLogResponse,
)
def get_activity_log(
    log_id: int,
    service: ActivityLogService = Depends(
        get_service
    ),
):

    return service.get(
        log_id,
    )


@router.get(
    "/user/{user_id}",
    response_model=list[ActivityLogResponse],
)
def get_user_activity_logs(
    user_id: int,
    service: ActivityLogService = Depends(
        get_service
    ),
):

    return service.get_by_user(
        user_id,
    )


@router.get(
    "/recent",
    response_model=list[ActivityLogResponse],
)
def get_recent_activity_logs(
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    service: ActivityLogService = Depends(
        get_service
    ),
):

    return service.get_recent(
        limit,
    )
@router.get(
    "/action/{action}",
    response_model=list[ActivityLogResponse],
)
def get_action_activity_logs(
    action: str,
    service: ActivityLogService = Depends(
        get_service
    ),
):

    return service.get_by_action(
        action,
    )


@router.get(
    "/status/{status}",
    response_model=list[ActivityLogResponse],
)
def get_status_activity_logs(
    status: str,
    service: ActivityLogService = Depends(
        get_service
    ),
):

    return service.get_by_status(
        status,
    )


@router.get(
    "/request/{request_id}",
    response_model=ActivityLogResponse,
)
def get_request_activity_log(
    request_id: str,
    service: ActivityLogService = Depends(
        get_service
    ),
):

    return service.get_by_request_id(
        request_id,
    )
@router.get(
    "/session/{session_id}",
    response_model=list[ActivityLogResponse],
)
def get_session_activity_logs(
    session_id: str,
    service: ActivityLogService = Depends(
        get_service
    ),
):

    return service.get_by_session_id(
        session_id,
    )


@router.delete(
    "/{log_id}",
)
def delete_activity_log(
    log_id: int,
    service: ActivityLogService = Depends(
        get_service
    ),
):

    result = service.delete(
        log_id,
    )

    return {
        "success": result,
    }


__all__ = [
    "router",
]