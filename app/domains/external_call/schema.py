"""
2026-10-16 — 바깥 호출 허용 API 스키마. 사용자가 보낸 값만 받고 추가 필드는 거절한다.
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class GrantRequest(BaseModel):
    """요청 없이 사용자가 직접 허용을 만든다."""

    system: str = Field(min_length=1, max_length=20)
    kind: str = Field(min_length=1, max_length=10)
    purpose: str = Field(min_length=1, max_length=40)
    mode: str = Field(min_length=1, max_length=10)
    count: int | None = Field(default=None, ge=1, le=50)
    per_hour_limit: int | None = Field(default=None, ge=1, le=120)
    hours: int | None = Field(default=None, ge=1, le=720)
    reason: str | None = Field(default=None, max_length=300)

    model_config = ConfigDict(extra="forbid")


class ApproveRequest(BaseModel):
    """승인 대기 요청을 허용한다. 비워 두면 요청이 제안한 값(횟수·시간당 상한)을 쓴다."""

    count: int | None = Field(default=None, ge=1, le=50)
    per_hour_limit: int | None = Field(default=None, ge=1, le=120)
    hours: int | None = Field(default=None, ge=1, le=720)

    model_config = ConfigDict(extra="forbid")


__all__ = ["GrantRequest", "ApproveRequest"]
