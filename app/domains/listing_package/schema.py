"""
=========================================================
Homez OS

File : app/domains/listing_package/schema.py
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ChannelSelectionItem(BaseModel):

    channel_code: str
    fulfillment_mode: str


class ImageOptions(BaseModel):

    main_count: int = Field(default=1, ge=0, le=5)
    detail_count: int = Field(default=2, ge=0, le=10)
    provider_code: str = Field(default="FAKE")
    style_params: dict = Field(default_factory=dict)


class CreateListingPackageRequest(BaseModel):

    product_candidate_id: int
    channel_selections: list[ChannelSelectionItem] = Field(..., min_length=1)
    image_options: ImageOptions = Field(default_factory=ImageOptions)
    mode: str = Field(default="STANDARD")
    idempotency_key: str = Field(..., min_length=1, max_length=160)


class ChannelReadiness(BaseModel):

    channel_code: str
    fulfillment_mode: str
    ready: bool
    missing_reasons: list[str] = Field(default_factory=list)


class ListingPackageResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    product_candidate_id: int
    mode: str
    status: str
    draft_payload: dict
    channel_selection: list[ChannelSelectionItem]
    image_options: dict
    channel_readiness: list[ChannelReadiness]
    estimated_revenue: float | None
    risk_summary: str | None
    recommendation_reason: str | None
    package_fingerprint: str
    submit_ready: bool
    image_job_id: int | None
    image_job_status: str | None
    created_at: datetime
    updated_at: datetime


class ListingPackageApprovalRequest(BaseModel):

    expected_package_fingerprint: str
    idempotency_key: str = Field(..., min_length=1, max_length=160)


class ListingPackageRejectionRequest(BaseModel):

    reason: str = Field(..., min_length=1, max_length=1000)
    idempotency_key: str = Field(..., min_length=1, max_length=160)


class ListingPackageApprovalResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_package_id: int
    status: str
    package_fingerprint_snapshot: str
    approved_by: int | None
    approved_at: datetime | None
    requested_by: int
    requested_at: datetime
    reason: str | None


__all__ = [
    "ChannelSelectionItem",
    "ImageOptions",
    "CreateListingPackageRequest",
    "ChannelReadiness",
    "ListingPackageResponse",
    "ListingPackageApprovalRequest",
    "ListingPackageRejectionRequest",
    "ListingPackageApprovalResponse",
]
