from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class ActivityLogBase(
    BaseModel
):
    user_id: int | None = None
    action: str
    target: str | None = None
    detail: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    status: str = "SUCCESS"
    category: str | None = None
    description: str | None = None
    metadata: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    created_by: str | None = None


class ActivityLogCreate(
    ActivityLogBase
):
    pass


class ActivityLogUpdate(
    BaseModel
):
    action: str | None = None
    target: str | None = None
    detail: str | None = None
    status: str | None = None
    category: str | None = None
    description: str | None = None
    metadata: str | None = None
class ActivityLogResponse(
    ActivityLogBase
):

    id: int
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class ActivityLogListResponse(
    BaseModel
):

    items: list[ActivityLogResponse]
    total: int


class ActivityLogFilter(
    BaseModel
):

    user_id: int | None = None
    action: str | None = None
    status: str | None = None
    category: str | None = None
    request_id: str | None = None
    session_id: str | None = None
class ActivityLogSearchRequest(
    BaseModel
):

    keyword: str | None = None

    user_id: int | None = None

    action: str | None = None

    status: str | None = None

    start_date: datetime | None = None

    end_date: datetime | None = None

    page: int = 1

    size: int = 20


class ActivityLogSummary(
    BaseModel
):

    total: int

    success_count: int

    failed_count: int

    latest_activity: datetime | None = None  
class ActivityLogExportRequest(
    BaseModel
):

    user_id: int | None = None

    action: str | None = None

    status: str | None = None

    category: str | None = None

    start_date: datetime | None = None

    end_date: datetime | None = None

    format: str = "json"


class ActivityLogDeleteRequest(
    BaseModel
):

    ids: list[int]


__all__ = [
    "ActivityLogBase",
    "ActivityLogCreate",
    "ActivityLogUpdate",
    "ActivityLogResponse",
    "ActivityLogListResponse",
    "ActivityLogFilter",
    "ActivityLogSearchRequest",
    "ActivityLogSummary",
    "ActivityLogExportRequest",
    "ActivityLogDeleteRequest",
]     