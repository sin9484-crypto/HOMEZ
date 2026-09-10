"""
=========================================================
Homez OS

File : app/domains/supplier_capability/service.py

2026-09-10 Phase 9(HOMEZ_USER_OPERATION_SETTINGS.md 7번) — 공급처
프로필·능력 플래그 관리. "미확인 기능은 절대 추측하지 않는다"를
`get_capability_matrix()`가 구조적으로 지킨다 — 한 번도 기록한 적
없는 플래그는 항상 UNKNOWN으로 채워진다(None이나 임의 추측값이
아니라 명시적인 "미확인" 상태).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.domains.supplier_capability.constants import CapabilitySupport
from app.domains.supplier_capability.constants import SupplierCapabilityFlag
from app.domains.supplier_capability.model import SupplierCapabilityRecord
from app.domains.supplier_capability.model import SupplierProfile
from app.domains.supplier_capability.repository import SupplierCapabilityRepository


class SupplierCapabilityService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = SupplierCapabilityRepository(db)

    # ------------------------------
    # SupplierProfile
    # ------------------------------

    def get_or_create_profile(self, supplier_id: int) -> SupplierProfile:

        profile = self.repository.get_profile(supplier_id)

        if profile is not None:
            return profile

        return self.repository.create_profile(
            SupplierProfile(supplier_id=supplier_id),
        )

    def set_profile(
        self,
        *,
        supplier_id: int,
        user_id: int,
        is_admin: bool,
        is_international: bool | None = None,
        country_code: str | None = None,
        default_currency: str | None = None,
        consignment_direct_to_customer: bool | None = None,
    ) -> SupplierProfile:

        if not is_admin:
            raise ForbiddenException(
                "공급처 프로필 변경은 관리자만 가능합니다.",
            )

        profile = self.get_or_create_profile(supplier_id)

        if is_international is not None:
            profile.is_international = is_international
        if country_code is not None:
            profile.country_code = country_code
        if default_currency is not None:
            profile.default_currency = default_currency
        if consignment_direct_to_customer is not None:
            profile.consignment_direct_to_customer = (
                consignment_direct_to_customer
            )

        return self.repository.save_profile(profile)

    # ------------------------------
    # SupplierCapabilityRecord — upsert
    # ------------------------------

    def set_capability(
        self,
        *,
        supplier_id: int,
        user_id: int,
        is_admin: bool,
        capability: str,
        support: str,
        note: str | None = None,
    ) -> SupplierCapabilityRecord:

        if not is_admin:
            raise ForbiddenException(
                "공급처 능력 판정 변경은 관리자만 가능합니다.",
            )

        if capability not in SupplierCapabilityFlag.ALL:
            raise BadRequestException(
                f"알 수 없는 능력 플래그: {capability}",
            )

        if support not in CapabilitySupport.ALL:
            raise BadRequestException(
                f"알 수 없는 지원 상태: {support}",
            )

        record = self.repository.get_record(supplier_id, capability)

        if record is None:
            return self.repository.create_record(
                SupplierCapabilityRecord(
                    supplier_id=supplier_id, capability=capability,
                    support=support, note=note, checked_by=user_id,
                ),
            )

        record.support = support
        record.note = note
        record.checked_by = user_id
        record.checked_at = datetime.utcnow()

        return self.repository.save_record(record)

    def get_capability_matrix(self, supplier_id: int) -> dict[str, dict]:
        """
        8개 플래그 전부를 항상 채워서 반환한다 — 기록된 적 없는
        플래그는 `{"support": "UNKNOWN", "note": None}`로 채운다
        (추측하지 않는다는 원칙을 이 메서드가 구조적으로 지킨다).
        """

        records = {
            r.capability: r
            for r in self.repository.list_records_for_supplier(supplier_id)
        }

        matrix: dict[str, dict] = {}
        for flag in SupplierCapabilityFlag.ALL:
            record = records.get(flag)
            if record is None:
                matrix[flag] = {"support": CapabilitySupport.UNKNOWN, "note": None}
            else:
                matrix[flag] = {"support": record.support, "note": record.note}

        return matrix


__all__ = ["SupplierCapabilityService"]
