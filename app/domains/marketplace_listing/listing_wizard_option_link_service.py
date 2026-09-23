"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_option_link_service.py

2026-09-21 옵션 연결 — 쿠팡 등록(제출) 한 건에 대해 "판매하기로 선택한 전체 옵션"을
**서버가** 확정하고(클라이언트가 보낸 SKU 목록을 믿지 않는다), 그 옵션마다 공급처
연결·쿠팡 옵션번호 상태를 보여 주고, 연결을 저장하고, 등록 결과 조회로 쿠팡 옵션
번호를 부착한다.

원칙:
- 옵션 목록의 근거는 제출에 묶인 동결된 판매 방식(`MarketplaceFulfillmentSelection.
  required_fields_json`, 스키마 검증·fingerprint 고정)이다. 위저드 입력이 그 뒤에
  달라졌으면 어느 쪽이 등록된 것인지 확정할 수 없어 "범위 미확정"으로 내린다.
- 이 서비스의 외부 호출은 `sync_identifiers`의 상품 상세조회 GET 1회뿐이다. 등록
  (생성) API는 어디에서도 호출하지 않는다 — 결과를 모를 때 재등록하지 않는다.
- 조회 결과가 UNKNOWN/NOT_FOUND이면 아무것도 저장하지 않는다.
=========================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.marketplace_listing.constants import SubmissionStatus
from app.domains.marketplace_listing.listing_wizard_live_service import (
    ListingWizardLiveService,
)
from app.domains.purchase_task.supplier_option_link_service import SupplierComponent
from app.domains.purchase_task.supplier_option_link_service import (
    SupplierOptionLinkService,
)
from app.domains.store_connection.model import StoreConnection


@dataclass(frozen=True)
class OptionScope:
    submission_id: int
    wizard_id: int
    seller_product_id: str | None
    registered: bool
    store_connection_id: int | None
    options: list[dict] = field(default_factory=list)
    confirmed: bool = False
    reason: str | None = None

    @property
    def skus(self) -> list[str]:
        return [o["sku"] for o in self.options]


class ListingWizardOptionLinkService:

    def __init__(
        self, db: Session, *, links: SupplierOptionLinkService | None = None,
        live: ListingWizardLiveService | None = None,
    ):
        self.db = db
        self.links = links or SupplierOptionLinkService(db)
        self.live = live or ListingWizardLiveService(db)

    # ---------------- 서버가 확정하는 옵션 범위 ----------------

    def scope(self, wizard_id: int, submission_id: int, company_id: int) -> OptionScope:
        wizard, submission, _listing, selection = self.live.submission_context(
            wizard_id, submission_id, company_id,
        )
        registered = (
            submission.status == SubmissionStatus.SUBMITTED
            and bool(submission.external_submission_ref)
        )
        seller_product_id = submission.external_submission_ref or None

        reasons: list[str] = []
        options: list[dict] = []
        try:
            required = json.loads(selection.required_fields_json or "{}")
        except (TypeError, ValueError):
            required = {}
        raw_items = required.get("items") if isinstance(required, dict) else None
        if not isinstance(raw_items, list) or not raw_items:
            reasons.append("OPTIONS_NOT_FOUND")
        else:
            seen: set[str] = set()
            for item in raw_items:
                sku = item.get("externalVendorSku") if isinstance(item, dict) else None
                if not isinstance(sku, str) or not sku or sku != sku.strip() or sku in seen:
                    reasons.append("OPTION_SKU_INVALID")
                    continue
                seen.add(sku)
                options.append({
                    "sku": sku, "item_name": str(item.get("itemName") or ""),
                    "sale_price": item.get("salePrice"),
                })

        # 위저드 입력이 선택 이후 달라졌다면 등록된 것이 어느 쪽인지 확정할 수 없다.
        try:
            entries = json.loads(wizard.channel_selections_json or "[]")
        except (TypeError, ValueError):
            entries = []
        entry = next(
            (x for x in entries if isinstance(x, dict)
             and x.get("marketplace_account_id") == submission.marketplace_account_id),
            None,
        )
        if entry is not None:
            wizard_items = (entry.get("required_fields") or {}).get("items") or []
            wizard_skus = {
                i.get("externalVendorSku") for i in wizard_items if isinstance(i, dict)
            }
            if wizard_skus != {o["sku"] for o in options}:
                reasons.append("WIZARD_CHANGED_AFTER_SELECTION")

        store_id = self._store_connection_id(submission, company_id)
        if store_id is None:
            reasons.append("STORE_CONNECTION_NOT_FOUND")

        return OptionScope(
            submission_id=submission.id, wizard_id=wizard.id,
            seller_product_id=seller_product_id, registered=registered,
            store_connection_id=store_id, options=options,
            confirmed=not reasons and bool(options), reason=", ".join(reasons) or None,
        )

    def _store_connection_id(self, submission, company_id: int) -> int | None:
        marketplace = self.live.marketplace
        account = marketplace.get_account_for_company(
            submission.marketplace_account_id, company_id,
        )
        if account is None:
            return None
        channel = marketplace.get_channel(account.channel_id)
        if channel is None:
            return None
        rows = (
            self.db.query(StoreConnection.id)
            .filter(
                StoreConnection.company_id == company_id,
                StoreConnection.marketplace_code == channel.code,
                StoreConnection.seller_identifier == account.account_code,
            )
            .all()
        )
        return rows[0][0] if len(rows) == 1 else None

    # ---------------- 읽기 전용 보기 ----------------

    def view(self, wizard_id: int, submission_id: int, company_id: int) -> dict:
        scope = self.scope(wizard_id, submission_id, company_id)
        readiness = self.links.readiness(
            company_id, store_connection_id=scope.store_connection_id,
            skus=scope.skus, scope_confirmed=scope.confirmed,
        )
        by_sku = {o["sku"]: o for o in readiness["per_option"]}
        options = []
        for option in scope.options:
            state = by_sku.get(option["sku"], {})
            options.append({**option, **{
                k: state.get(k) for k in (
                    "link_state", "ids_state", "link_id", "supplier_product_code",
                    "supplier_option_id", "supplier_option_name", "units_per_sale",
                    "status_reason",
                )
            }})
        # 쿠팡에 등록되지 않았거나 등록 결과를 모르는 제출은 준비 완료가 될 수 없다.
        all_ready = readiness["all_ready"] and scope.registered
        reason = readiness["reason"]
        if not scope.registered:
            reason = "NOT_REGISTERED"
        return {
            "wizard_id": scope.wizard_id, "submission_id": scope.submission_id,
            "registered": scope.registered,
            "seller_product_id": scope.seller_product_id,
            "store_connection_id": scope.store_connection_id,
            "scope": {"confirmed": scope.confirmed, "reason": scope.reason},
            "options": options,
            "readiness": {
                "scope": readiness["scope"], "total": readiness["total"],
                "selected_ready": readiness["selected_ready"],
                "all_ready": all_ready, "reason": reason,
            },
        }

    # ---------------- 연결 저장(판매 계정·SKU 범위는 서버가 정한다) ----------------

    def save_option_link(
        self, wizard_id: int, submission_id: int, company_id: int, *,
        channel_sku: str, purchase_connection_id: int, supplier_product_code: str,
        supplier_option_id: str, units: int, replace: bool, actor_user_id: int,
    ) -> dict:
        scope = self.scope(wizard_id, submission_id, company_id)
        if scope.store_connection_id is None:
            raise BadRequestException("이 등록의 쿠팡 판매 계정을 확인할 수 없습니다.")
        if channel_sku not in scope.skus:
            raise BadRequestException(
                "이 등록에서 판매하기로 선택한 옵션이 아닙니다 — 연결을 저장하지 않았습니다.",
            )
        self.links.save_link(
            company_id, store_connection_id=scope.store_connection_id,
            channel_sku=channel_sku, purchase_connection_id=purchase_connection_id,
            components=[SupplierComponent(supplier_product_code, supplier_option_id, units)],
            confirmed_by=actor_user_id, replace=replace,
        )
        return self.view(wizard_id, submission_id, company_id)

    # ---------------- 등록 결과 옵션 식별자 확인(외부 GET 1회) ----------------

    def sync_identifiers(
        self, wizard_id: int, submission_id: int, company_id: int, provider, *,
        actor_user_id: int,
    ) -> dict:
        scope = self.scope(wizard_id, submission_id, company_id)
        if not scope.registered or not scope.seller_product_id:
            raise BadRequestException(
                "SELLER_PRODUCT_ID_NOT_AVAILABLE: 아직 쿠팡에 등록되지 않았거나 상품 ID를 "
                "확인할 수 없어 옵션 번호를 조회할 수 없습니다. 결과를 모르는 등록은 "
                "다시 등록하지 말고 등록 결과 정합화로 먼저 확인하세요.",
            )
        if scope.store_connection_id is None:
            raise BadRequestException("이 등록의 쿠팡 판매 계정을 확인할 수 없습니다.")

        result = provider.get_product_option_identifiers(scope.seller_product_id)
        if result.outcome != "FOUND":
            # 확인하지 못했다 — 아무것도 저장하지 않고, 등록을 다시 시도하지도 않는다.
            return {
                "outcome": result.outcome, "state": "UNVERIFIED",
                "detail": result.error_summary or (
                    "쿠팡에서 상품을 찾지 못했습니다." if result.outcome == "NOT_FOUND"
                    else "옵션 번호를 확인하지 못했습니다."
                ),
                "attach": None, "view": self.view(wizard_id, submission_id, company_id),
            }

        attach = self.links.attach_coupang_identifiers(
            company_id, store_connection_id=scope.store_connection_id,
            seller_product_id=scope.seller_product_id,
            items=[
                {"externalVendorSku": i.external_vendor_sku, "vendorItemId": i.vendor_item_id}
                for i in result.items
            ],
            expected_skus=scope.skus if scope.confirmed else None,
            actor_user_id=actor_user_id,
        )
        return {
            "outcome": "FOUND", "state": attach["state"],
            "raw_status_name": result.raw_status_name, "detail": attach["detail"],
            "attach": attach, "view": self.view(wizard_id, submission_id, company_id),
        }


__all__ = ["ListingWizardOptionLinkService", "OptionScope"]
