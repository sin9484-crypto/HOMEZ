"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/candidate_pipeline_schema.py

V7 Gate 6 — 후보 선택→초안 생성→이미지 생성 통합 파이프라인 계약.
=========================================================
"""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class CandidatePipelineRunRequest(BaseModel):

    candidate_id: int
    wizard_creation_idempotency_key: str = Field(min_length=1, max_length=160)
    image_job_idempotency_key: str = Field(min_length=1, max_length=160)
    image_purposes: list[str] = Field(default_factory=lambda: ["MAIN"])
    image_provider_code: str = Field(default="FAKE")
    content_provider_code: str = Field(default="FAKE")

    model_config = ConfigDict(extra="forbid")


class CandidateScoreSnapshot(BaseModel):

    trend_score: float | None
    novelty_score: float | None
    demand_score: float | None
    competition_score: float | None
    margin_score: float | None
    risk_score: float | None
    confidence: float | None


class CandidatePipelineResult(BaseModel):
    """
    Recommend 모드에서는 wizard_id/image_job_id가 항상 None이다(제안만
    반환하고 아무것도 실행하지 않는다). Auto 모드(LIMITED_AUTOMATION)
    +Emergency Stop 비활성+정책 통과일 때만 실제로 위저드/이미지 Job이
    만들어지고 auto_applied=True가 된다.
    """

    automation_mode: str
    auto_applied: bool

    suggested_description: str
    suggested_keywords: list[str]
    content_source: str
    content_provider_code: str

    content_policy_passed: bool
    content_policy_blocking_flags: list[str]
    content_policy_warning_flags: list[str]
    content_policy_checklist_version: str

    candidate_scores: CandidateScoreSnapshot

    wizard_id: int | None = None
    wizard_status: str | None = None
    wizard_version: int | None = None

    image_job_id: int | None = None
    image_job_status: str | None = None

    model_config = ConfigDict(extra="forbid")


class CandidatePipelineImageSelectionRequest(BaseModel):

    wizard_id: int
    image_job_id: int
    selected_result_ids: list[int] = Field(min_length=1)
    expected_version: int

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "CandidatePipelineRunRequest",
    "CandidateScoreSnapshot",
    "CandidatePipelineResult",
    "CandidatePipelineImageSelectionRequest",
]
