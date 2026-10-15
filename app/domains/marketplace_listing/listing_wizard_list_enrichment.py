"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/listing_wizard_list_enrichment.py

위저드 목록 보강(2026-10-15, UI 개선 시안 3단계) — 목록에서 "무슨 상품인지" 바로 알아보도록 상품명·대표 사진·공급처·판매채널·
단계 번호(n/10)·확인사항을 목록 응답에 더한다. 기존 필드는 그대로 두고 선택 필드만 추가한다(기존 화면·API 계약 유지).

원칙:
- 읽기 전용이다. 새 판정을 만들지 않고, 이미 저장된 값에서만 계산한다(없으면 비워 둔다 — 추측해 채우지 않는다).
- 행마다 조회하지 않도록 후보·사진·판매계정은 한 번에 모아 읽는다(목록 최대 200행).
- 확인사항은 "코드"로만 돌려준다 — 문구는 화면이 번역한다.
=========================================================
"""

from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy.orm import Session

from app.domains.marketplace_listing.constants import WizardStatus
from app.domains.marketplace_listing.constants import WizardStep
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.media_asset.model import MediaAsset
from app.domains.product_candidate.model import ProductCandidate

# 확인사항 코드 — 화면이 `lw.attention.<code>`로 번역한다.
ATTENTION_NEEDS_CORRECTION = "NEEDS_CORRECTION"
ATTENTION_SUBMIT_FAILED = "SUBMIT_FAILED"
ATTENTION_NO_IMAGE = "NO_IMAGE_SELECTED"
ATTENTION_NO_CHANNEL = "NO_CHANNEL"
ATTENTION_NO_ECONOMICS = "NO_ECONOMICS"
ATTENTION_AWAITING_APPROVAL = "AWAITING_APPROVAL"
ATTENTION_READY_TO_SUBMIT = "READY_TO_SUBMIT"
ATTENTION_PARTIAL = "PARTIALLY_SUCCEEDED"

_OPEN_STATUSES = frozenset({
    WizardStatus.DRAFT, WizardStatus.NEEDS_CORRECTION, WizardStatus.READY_FOR_APPROVAL,
})
_SOURCE_LABELS = {"ONCHANNEL": "온채널"}


def step_number(current_step: str) -> int | None:
    """1부터 시작하는 단계 번호. 알 수 없는 단계는 None."""

    try:
        return WizardStep.ORDERED.index(current_step) + 1
    except ValueError:
        return None


def _loads(raw: str | None, default):
    try:
        value = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def attention_codes(wizard: ListingWizard, *, selected_media: list, channel_selections: list, economics: list) -> list[str]:
    """이 위저드에서 사람이 봐야 할 일 — 저장된 값과 상태만으로 계산한다."""

    codes: list[str] = []
    status = wizard.status
    step = step_number(wizard.current_step) or 1
    if status == WizardStatus.NEEDS_CORRECTION:
        codes.append(ATTENTION_NEEDS_CORRECTION)
    if status == WizardStatus.FAILED:
        codes.append(ATTENTION_SUBMIT_FAILED)
    if status == WizardStatus.PARTIALLY_SUCCEEDED:
        codes.append(ATTENTION_PARTIAL)
    if status in _OPEN_STATUSES:
        # 해당 단계를 지나왔는데 그 단계의 결과가 비어 있으면 알린다.
        if step > WizardStep.ORDERED.index(WizardStep.MEDIA) + 1 and not selected_media:
            codes.append(ATTENTION_NO_IMAGE)
        if step > WizardStep.ORDERED.index(WizardStep.CHANNELS) + 1 and not channel_selections:
            codes.append(ATTENTION_NO_CHANNEL)
        if step > WizardStep.ORDERED.index(WizardStep.ECONOMICS) + 1 and not economics:
            codes.append(ATTENTION_NO_ECONOMICS)
        if status == WizardStatus.READY_FOR_APPROVAL:
            codes.append(ATTENTION_AWAITING_APPROVAL)
    if status == WizardStatus.APPROVED:
        codes.append(ATTENTION_READY_TO_SUBMIT)
    return codes


def enrich_list_items(db: Session, company_id: int, wizards: Iterable[ListingWizard]) -> dict[int, dict]:
    """위저드 id → 목록 보강 필드. 조회는 후보·사진·판매계정 각각 한 번씩만 한다."""

    wizards = list(wizards)
    candidate_ids = {w.product_candidate_id for w in wizards if w.product_candidate_id}
    candidates: dict[int, ProductCandidate] = {}
    if candidate_ids:
        for row in db.query(ProductCandidate).filter(ProductCandidate.id.in_(candidate_ids)).all():
            candidates[row.id] = row

    # 대표 사진 후보 — 선택한 이미지가 없을 때 쓸, 후보의 첫 원본 대표 이미지.
    fallback_thumb: dict[int, int] = {}
    if candidate_ids:
        rows = (
            db.query(MediaAsset.owner_id, MediaAsset.id)
            .filter(MediaAsset.company_id == company_id, MediaAsset.owner_type == "PRODUCT_CANDIDATE")
            .filter(MediaAsset.owner_id.in_(candidate_ids), MediaAsset.status == "ACTIVE")
            .filter(MediaAsset.purpose == "MAIN", MediaAsset.asset_role == "ORIGINAL")
            .order_by(MediaAsset.id.asc())
            .all()
        )
        for owner_id, asset_id in rows:
            fallback_thumb.setdefault(owner_id, asset_id)

    parsed: dict[int, dict] = {}
    account_ids: set[int] = set()
    selected_asset_ids: set[int] = set()
    for w in wizards:
        selected = [x for x in _loads(w.selected_media_asset_ids_json, []) if isinstance(x, int)]
        selections = [x for x in _loads(w.channel_selections_json, []) if isinstance(x, dict)]
        economics = _loads(w.economics_input_json, [])
        parsed[w.id] = {"selected": selected, "selections": selections, "economics": economics, "draft": _loads(w.draft_json, {})}
        account_ids.update(s.get("marketplace_account_id") for s in selections if isinstance(s.get("marketplace_account_id"), int))
        if selected:
            selected_asset_ids.add(selected[0])

    accounts: dict[int, tuple[str, str]] = {}
    if account_ids:
        rows = (
            db.query(MarketplaceAccount.id, MarketplaceAccount.account_name, MarketplaceChannel.code)
            .join(MarketplaceChannel, MarketplaceChannel.id == MarketplaceAccount.channel_id)
            .filter(MarketplaceAccount.company_id == company_id, MarketplaceAccount.id.in_(account_ids))
            .all()
        )
        accounts = {aid: (name, code) for aid, name, code in rows}

    # 선택한 첫 이미지가 이 회사의 활성 이미지일 때만 대표 사진으로 쓴다.
    valid_selected: set[int] = set()
    if selected_asset_ids:
        valid_selected = {
            row[0] for row in db.query(MediaAsset.id).filter(
                MediaAsset.company_id == company_id, MediaAsset.id.in_(selected_asset_ids), MediaAsset.status == "ACTIVE",
            ).all()
        }

    result: dict[int, dict] = {}
    for w in wizards:
        info = parsed[w.id]
        candidate = candidates.get(w.product_candidate_id) if w.product_candidate_id else None
        name = str(info["draft"].get("product_name") or "").strip() or (candidate.product_name if candidate else None)
        source_ref = (candidate.source_reference or "") if candidate else ""
        supplier = _SOURCE_LABELS.get(source_ref.split(":", 1)[0]) if ":" in source_ref else None
        first_selected = info["selected"][0] if info["selected"] else None
        thumb = first_selected if first_selected in valid_selected else fallback_thumb.get(w.product_candidate_id)
        channels = []
        for sel in info["selections"]:
            account = accounts.get(sel.get("marketplace_account_id"))
            if account:
                channels.append({"code": account[1], "account_name": account[0]})
        result[w.id] = {
            "product_name": name,
            "supplier_label": supplier,
            "channels": channels,
            "step_number": step_number(w.current_step),
            "step_total": len(WizardStep.ORDERED),
            "thumbnail_asset_id": thumb,
            "attention": attention_codes(
                w, selected_media=info["selected"], channel_selections=info["selections"], economics=info["economics"],
            ),
        }
    return result
