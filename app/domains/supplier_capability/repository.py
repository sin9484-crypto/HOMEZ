"""
=========================================================
Homez OS

File : app/domains/supplier_capability/repository.py

2026-09-10 Phase 9 — SupplierProfile/SupplierCapabilityRecord
저장소 계층.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.supplier_capability.model import SupplierCapabilityRecord
from app.domains.supplier_capability.model import SupplierProfile


class SupplierCapabilityRepository:

    def __init__(self, db: Session):

        self.db = db

    # ------------------------------
    # SupplierProfile
    # ------------------------------

    def get_profile(self, supplier_id: int) -> SupplierProfile | None:

        return (
            self.db.query(SupplierProfile)
            .filter(SupplierProfile.supplier_id == supplier_id)
            .first()
        )

    def create_profile(self, profile: SupplierProfile) -> SupplierProfile:

        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)

        return profile

    def save_profile(self, profile: SupplierProfile) -> SupplierProfile:

        self.db.commit()
        self.db.refresh(profile)

        return profile

    # ------------------------------
    # SupplierCapabilityRecord (upsert)
    # ------------------------------

    def get_record(
        self, supplier_id: int, capability: str,
    ) -> SupplierCapabilityRecord | None:

        return (
            self.db.query(SupplierCapabilityRecord)
            .filter(
                SupplierCapabilityRecord.supplier_id == supplier_id,
                SupplierCapabilityRecord.capability == capability,
            )
            .first()
        )

    def list_records_for_supplier(
        self, supplier_id: int,
    ) -> list[SupplierCapabilityRecord]:

        return (
            self.db.query(SupplierCapabilityRecord)
            .filter(SupplierCapabilityRecord.supplier_id == supplier_id)
            .all()
        )

    def create_record(
        self, record: SupplierCapabilityRecord,
    ) -> SupplierCapabilityRecord:

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        return record

    def save_record(
        self, record: SupplierCapabilityRecord,
    ) -> SupplierCapabilityRecord:

        self.db.commit()
        self.db.refresh(record)

        return record


__all__ = ["SupplierCapabilityRepository"]
