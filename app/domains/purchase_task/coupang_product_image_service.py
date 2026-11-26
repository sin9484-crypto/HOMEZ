"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_product_image_service.py

2026-11-26 — 쿠팡 쓰기: **등록 상품의 대표·추가 이미지·상세설명 사진 교체**(사용자 "전체 이미지 변경 대표로 쓸 이미지도 고려해서 만들어라").
쿠팡 "상품 수정(승인필요)" — 승인 뒤 반영. 상품명 수정(`coupang_product_name_service`)과 같은 틀을 따른다:

  1) 사진은 사람이 고른다(대표 1장·추가 0~9장·상세 1~10장). 사진마다 이 회사·이 상품 후보의 활성 사진인지, 용도(대표=MAIN, 추가=ADDITIONAL,
     상세=DETAIL)·형식(PNG/JPG)·크기(가로세로 500~5,000, 대표는 1,000 이상 정사각형)를 보고, 쿠팡 등록상품번호가 이 상품 후보와 연결된
     번호(공급처 옵션 연결의 판매 SKU가 `PC{후보}-`로 시작)인지 확인한다. 하나라도 어긋나면 보내지 않는다.
  2) **상품 전체를 다시 보내는 API**다 — 본문은 방금 읽은 상품 조회 응답을 그대로 복사해 옵션마다 `images`·`contents`(와 승인 요청 표시)만 바꾼다.
     보내기 직전에 원본과 본문을 비교해 그 밖의 값이 하나라도 다르거나 옵션 번호(sellerProductItemId)가 없으면 보내지 않는다.
  3) 쿠팡 상태가 승인완료(변경 요청)·승인반려(다시 제출)일 때만, 사용자가 '승인 뒤 반영·반려될 수 있음'을 확인(`confirm_review`)했을 때만 보낸다.
  4) 사진의 공개 주소는 **보내기로 한 뒤에만** 만든다(R2 업로드 — 등록 때와 같은 `R2MediaHostingService.ensure_public_url`, 이미 올린 사진은 그대로 씀).
     미리보기는 업로드하지 않는다. 쿠팡 읽기가 막히거나 조건이 안 맞으면 업로드 전에 멈춘다.
  5) 안전 — 비상 정지·기능 모드(운영자 승인)·승인 센터의 "쿠팡 상품 사진 수정" 허용(읽기·쓰기 각각 횟수형)이 없으면 읽지도 보내지도 않는다.
     중복 방지·결과불명은 작업 장부(대상 키 = 등록상품번호 + 사진 묶음 지문 + 요청 참조값)가 맡는다.
=========================================================
"""

from __future__ import annotations

import copy
import hashlib
import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.marketplace_listing.coupang_live_provider import CANONICAL_STATUS_MAP
from app.domains.media_asset.model import MediaAsset
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import MAX_VENDOR_ITEM_ID_DIGITS
from app.domains.purchase_task.coupang_product_name_service import EDITABLE_STATUSES
from app.domains.purchase_task.coupang_stock_service import REQUEST_REF
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

MAX_ADDITIONAL = 9
MAX_CONTENTS = 10
MIN_SIDE, MAX_SIDE, MAIN_MIN_SIDE = 500, 5000, 1000
IMAGE_MIMES = ("image/png", "image/jpeg")
TOP_KEYS_ALLOWED = frozenset({"items", "requested"})
ITEM_KEYS_ALLOWED = frozenset({"images", "contents"})
_MISSING = object()


def images_fingerprint(main_id: int, additional_ids: list[int], content_ids: list[int]) -> str:
    raw = f"m:{main_id}|a:{','.join(map(str, additional_ids))}|c:{','.join(map(str, content_ids))}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_images_body(data: dict, main_url: str, additional_urls: list[str], content_urls: list[str]) -> dict:
    """상품 조회 응답을 그대로 복사해 옵션마다 사진(대표 순서 0, 추가 1~)과 상세설명(등록 때와 같은 TEXT/TEXT `<img>` 한 장씩)만 바꾼다."""

    images = [{"imageOrder": 0, "imageType": "REPRESENTATION", "vendorPath": main_url}]
    images += [{"imageOrder": i + 1, "imageType": "DETAIL", "vendorPath": url} for i, url in enumerate(additional_urls)]
    contents = [{"contentsType": "TEXT", "contentDetails": [{"detailType": "TEXT", "content": f"<html><div><img src='{url}' /></div></html>"}]}
                for url in content_urls]
    body = copy.deepcopy(data)
    for item in body.get("items") or []:
        if isinstance(item, dict):
            item["images"] = copy.deepcopy(images)
            item["contents"] = copy.deepcopy(contents)
    body["requested"] = True
    return body


def images_body_guard(data: dict, body: dict) -> str | None:
    """보내기 직전 검사 — 옵션의 사진·상세설명과 승인 요청 표시 말고는 아무것도 달라지면 안 된다."""

    top = {k for k in set(data) | set(body) if data.get(k, _MISSING) != body.get(k, _MISSING)}
    if not top <= TOP_KEYS_ALLOWED:
        return "BODY_CHANGED_OTHER_FIELDS"
    before, after = data.get("items"), body.get("items")
    if not isinstance(after, list) or not after or not isinstance(before, list) or len(before) != len(after):
        return "BODY_NO_OPTIONS"
    for old, new in zip(before, after):
        if not isinstance(old, dict) or not isinstance(new, dict):
            return "BODY_NO_OPTIONS"
        changed = {k for k in set(old) | set(new) if old.get(k, _MISSING) != new.get(k, _MISSING)}
        if not changed <= ITEM_KEYS_ALLOWED:
            return "BODY_CHANGED_OTHER_FIELDS"
        if not str(new.get("sellerProductItemId") or "").strip().isdigit():
            return "BODY_OPTION_ID_MISSING"
    return None


@dataclass
class ImageBlocker:
    code: str
    message: str


@dataclass
class ImagePlan:
    seller_product_id: str
    product_candidate_id: int | None
    main_asset_id: Any
    additional_asset_ids: list
    content_asset_ids: list
    request_ref: str | None = None
    blockers: list[ImageBlocker] = field(default_factory=list)
    gate_blockers: list[ImageBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    status_name: str | None = None
    mode: str | None = None
    option_count: int | None = None
    current_image_count: int | None = None
    live_checked: bool = False
    fingerprint: str | None = None
    data: dict | None = None
    assets: dict = field(default_factory=dict)

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers

    def to_dict(self) -> dict:
        # 상품 본문은 화면에 내보내지 않는다 — 상태·옵션 수·사진 번호만.
        return {
            "seller_product_id": self.seller_product_id, "product_candidate_id": self.product_candidate_id,
            "main_asset_id": self.main_asset_id, "additional_asset_ids": self.additional_asset_ids, "content_asset_ids": self.content_asset_ids,
            "request_ref": self.request_ref, "sendable": self.sendable, "live_checked": self.live_checked, "mode": self.mode,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "coupang": {"status_name": self.status_name, "option_count": self.option_count, "current_image_count": self.current_image_count},
            "fingerprint": self.fingerprint,
        }


class CoupangProductImageService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None, hosting_factory: Callable[[Session], Any] | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)
        self._hosting_factory = hosting_factory or self._default_hosting

    @staticmethod
    def _default_hosting(session: Session):
        from app.core.windows_credential_store import WindowsCredentialStore
        from app.domains.media_asset.public_hosting import R2MediaHostingService

        return R2MediaHostingService(session, WindowsCredentialStore())

    # ---- 계획(미리보기) — 로컬 판정 + 쿠팡 상품 조회 1회, 업로드 없음 ----

    def plan(self, company_id: int, seller_product_id: str, product_candidate_id: int, main_asset_id: Any, additional_asset_ids: Any,
             content_asset_ids: Any, *, request_ref: str | None = None, live: bool = True) -> ImagePlan:
        product = str(seller_product_id or "").strip()
        plan = ImagePlan(product, product_candidate_id if isinstance(product_candidate_id, int) else None, main_asset_id,
                         list(additional_asset_ids or []), list(content_asset_ids or []), request_ref=request_ref)
        if not product.isdigit() or len(product) > MAX_VENDOR_ITEM_ID_DIGITS:
            plan.blockers.append(ImageBlocker("INVALID_SELLER_PRODUCT_ID", "쿠팡 등록상품번호는 숫자여야 한다."))
        if request_ref is not None and not REQUEST_REF.match(str(request_ref)):
            plan.blockers.append(ImageBlocker("INVALID_REQUEST_REF", "요청 참조값 형식이 올바르지 않다(미리보기를 다시 열어 주세요)."))
        self._check_assets(plan, company_id)
        if not plan.blockers:
            self._check_product_link(plan, company_id)
        self._resolve_store(plan, company_id)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        plan.fingerprint = images_fingerprint(plan.main_asset_id, plan.additional_asset_ids, plan.content_asset_ids)
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _check_assets(self, plan: ImagePlan, company_id: int) -> None:
        ids = [plan.main_asset_id] + plan.additional_asset_ids + plan.content_asset_ids
        if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            plan.blockers.append(ImageBlocker("INVALID_ASSET_ID", "사진 번호는 정수여야 한다."))
            return
        if plan.product_candidate_id is None:
            plan.blockers.append(ImageBlocker("INVALID_PRODUCT_CANDIDATE", "상품 후보 번호가 필요하다."))
            return
        if len(set(ids)) != len(ids):
            plan.blockers.append(ImageBlocker("DUPLICATE_ASSET", "같은 사진을 두 번 고를 수 없다."))
        if len(plan.additional_asset_ids) > MAX_ADDITIONAL:
            plan.blockers.append(ImageBlocker("TOO_MANY_ADDITIONAL", f"추가 이미지는 {MAX_ADDITIONAL}장까지다."))
        if not 1 <= len(plan.content_asset_ids) <= MAX_CONTENTS:
            plan.blockers.append(ImageBlocker("CONTENTS_COUNT", f"상세설명 사진은 1~{MAX_CONTENTS}장이어야 한다."))
        rows = {a.id: a for a in self.db.query(MediaAsset).filter(MediaAsset.company_id == company_id, MediaAsset.id.in_(set(ids))).all()}
        roles = [(plan.main_asset_id, "MAIN")] + [(i, "ADDITIONAL") for i in plan.additional_asset_ids] + [(i, "DETAIL") for i in plan.content_asset_ids]
        for asset_id, purpose in roles:
            asset = rows.get(asset_id)
            problem = None
            if asset is None:
                problem = "없는 사진이다"
            elif asset.owner_type != "PRODUCT_CANDIDATE" or asset.owner_id != plan.product_candidate_id:
                problem = "이 상품 후보의 사진이 아니다"
            elif asset.status != "ACTIVE":
                problem = "쓸 수 없는 사진이다(활성 아님)"
            elif asset.rights_status == "RIGHTS_DENIED":
                problem = "사용이 금지된 사진이다"
            elif asset.purpose != purpose:
                problem = f"용도가 {purpose}가 아니다({asset.purpose})"
            elif asset.mime_type not in IMAGE_MIMES:
                problem = "PNG·JPG만 보낼 수 있다"
            elif not (asset.width and asset.height and MIN_SIDE <= asset.width <= MAX_SIDE and MIN_SIDE <= asset.height <= MAX_SIDE):
                problem = f"가로세로가 {MIN_SIDE}~{MAX_SIDE}이어야 한다"
            elif purpose == "MAIN" and not (asset.width == asset.height and asset.width >= MAIN_MIN_SIDE):
                problem = f"대표 사진은 {MAIN_MIN_SIDE} 이상 정사각형이어야 한다"
            if problem:
                plan.blockers.append(ImageBlocker("ASSET_NOT_USABLE", f"사진 #{asset_id}: {problem}."))
            else:
                plan.assets[asset_id] = asset

    def _check_product_link(self, plan: ImagePlan, company_id: int) -> None:
        rows = self.db.execute(
            text("SELECT channel_sku FROM supplier_option_links WHERE company_id = :c AND coupang_seller_product_id = :p"),
            {"c": company_id, "p": plan.seller_product_id},
        ).fetchall()
        prefix = f"PC{plan.product_candidate_id}-"
        if not rows or not all(str(r[0] or "").startswith(prefix) for r in rows):
            plan.blockers.append(ImageBlocker(
                "PRODUCT_MISMATCH", "이 쿠팡 등록상품번호가 고른 상품 후보와 연결돼 있지 않다(공급처 옵션 연결 기준) — 다른 상품의 사진을 바꾸지 않는다.",
            ))

    def _resolve_store(self, plan: ImagePlan, company_id: int) -> None:
        stores = (
            self.db.query(StoreConnection)
            .filter(StoreConnection.company_id == company_id, StoreConnection.marketplace_code == "COUPANG",
                    StoreConnection.connection_status == ConnectionStatus.CONNECTED)
            .all()
        )
        if len(stores) == 1:
            plan.store_connection_id = stores[0].id
        elif not stores:
            plan.blockers.append(ImageBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다."))
        else:
            plan.blockers.append(ImageBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다."))

    def _gate_checks(self, plan: ImagePlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(ImageBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(ImageBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _live_checks(self, plan: ImagePlan, company_id: int) -> None:
        snapshot, failure = self._actions_factory(self.db).read_product(
            company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=plan.seller_product_id,
            action_type=ChannelActionType.PRODUCT_IMAGE_UPDATE,
        )
        if snapshot is None or not snapshot.ok:
            detail = failure.detail if failure is not None else (snapshot.detail if snapshot is not None else "")
            code = "EXTERNAL_CALL_NOT_ALLOWED" if detail.startswith("EXTERNAL_") else "PRODUCT_READ_FAILED"
            plan.blockers.append(ImageBlocker(code, f"쿠팡 상품을 읽지 못해 보내지 않는다. {detail}".strip()))
            return
        plan.live_checked = True
        plan.data = snapshot.data
        plan.status_name = snapshot.status_name
        items = snapshot.data.get("items") or []
        plan.option_count = len(items)
        plan.current_image_count = len((items[0] or {}).get("images") or []) if items and isinstance(items[0], dict) else 0
        plan.mode = EDITABLE_STATUSES.get(CANONICAL_STATUS_MAP.get(str(snapshot.status_name or "").strip(), ""))
        if plan.mode is None:
            plan.blockers.append(ImageBlocker(
                "NOT_EDITABLE_STATUS",
                f"쿠팡 상품 상태가 '{snapshot.status_name or '알 수 없음'}'라 보내지 않는다(승인완료·승인반려 상품만 고칠 수 있다).",
            ))
            return
        dry = build_images_body(snapshot.data, "https://x/0.png", [f"https://x/{i}.png" for i in plan.additional_asset_ids],
                                [f"https://x/c{i}.png" for i in plan.content_asset_ids])
        problem = images_body_guard(snapshot.data, dry)
        if problem is not None:
            plan.blockers.append(ImageBlocker(problem, "상품 수정 본문 검사에 걸려 보내지 않는다(사진 말고 다른 값이 달라지거나 옵션 번호가 없다)."))

    # ---- 전송 ----

    def send(self, company_id: int, user_id: int, seller_product_id: str, product_candidate_id: int, main_asset_id: int,
             additional_asset_ids: list[int], content_asset_ids: list[int], *, request_ref: str, confirm_review: bool = False,
             retry_action_required: bool = False) -> dict:
        if not REQUEST_REF.match(str(request_ref or "")):
            raise BadRequestException("요청 참조값 형식이 올바르지 않다 — 미리보기를 다시 열어 주세요. 보내지 않았다.")
        if confirm_review is not True:
            raise BadRequestException("바꾼 사진은 쿠팡 승인 뒤에 반영되고 반려될 수 있다 — 이 점을 따로 확인해 주세요. 보내지 않았다.")
        plan = self.plan(company_id, seller_product_id, product_candidate_id, main_asset_id, additional_asset_ids, content_asset_ids,
                         request_ref=request_ref, live=True)
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked or plan.data is None:
            raise BadRequestException("사진을 바꿀 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))

        hosting = self._hosting_factory(self.db)
        url = {asset_id: hosting.ensure_public_url(asset) for asset_id, asset in plan.assets.items()}   # 보내기로 한 뒤에만 공개 주소
        self.db.commit()
        body = build_images_body(plan.data, url[plan.main_asset_id], [url[i] for i in plan.additional_asset_ids],
                                 [url[i] for i in plan.content_asset_ids])
        problem = images_body_guard(plan.data, body)
        if problem is not None:
            raise BadRequestException(f"상품 수정 본문 검사에 걸려 보내지 않았다: {problem}")
        actions = self._actions_factory(self.db)
        result = actions.set_product_images(
            company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=plan.seller_product_id,
            fingerprint=plan.fingerprint, body=body, request_ref=request_ref, triggered_by=user_id, retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_PRODUCT_IMAGE_UPDATE", entity="coupang_seller_product", entity_id=plan.seller_product_id,
            description=(f"쿠팡 상품 사진 수정 {result.outcome}({plan.mode}): 대표 #{plan.main_asset_id}, 추가 {plan.additional_asset_ids}, "
                         f"상세 {plan.content_asset_ids}")[:490],
            company_id=company_id,
        )
        self.db.commit()
        after = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 상품을 다시 읽는다(읽기 1회). 사진은 쿠팡 승인 뒤 바뀌므로 상태만 적는다.
            snap, _ = actions.read_product(company_id=company_id, store_connection_id=plan.store_connection_id,
                                           seller_product_id=plan.seller_product_id, action_type=ChannelActionType.PRODUCT_IMAGE_UPDATE)
            if snap is not None and snap.ok:
                after = {"status_name": snap.status_name}
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "after": after,
                "uploaded": sorted(url), "plan": plan.to_dict()}


__all__ = ["CoupangProductImageService", "ImagePlan", "ImageBlocker", "build_images_body", "images_body_guard", "images_fingerprint",
           "MAX_ADDITIONAL", "MAX_CONTENTS", "TOP_KEYS_ALLOWED", "ITEM_KEYS_ALLOWED"]
