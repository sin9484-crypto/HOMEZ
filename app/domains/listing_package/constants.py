"""
=========================================================
Homez OS

File : app/domains/listing_package/constants.py
=========================================================
"""


class ListingPackageMode:

    STANDARD = "STANDARD"
    AI_AUTO_PROPOSAL = "AI_AUTO_PROPOSAL"

    ALL = (STANDARD, AI_AUTO_PROPOSAL)


class ListingPackageStatus:

    DRAFT = "DRAFT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"

    ALL = (DRAFT, READY_FOR_REVIEW, APPROVED, SUBMITTED, CANCELLED)


class ListingPackageApprovalStatus:

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    ALL = (APPROVED, REJECTED)


__all__ = [
    "ListingPackageMode",
    "ListingPackageStatus",
    "ListingPackageApprovalStatus",
]
