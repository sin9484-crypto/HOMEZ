"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/registration_assistant.py

등록 도우미(2026-10-14) — 쿠팡 위저드 5단계(판매 방식·필수 정보)를 사람이 손으로 채우던 일을 도구로 만든다.

입력: 온채널에서 읽어 둔 정보고시 행(후보 근거), 위저드 초안(옵션), 쿠팡 카테고리 메타데이터, 같은 판매계정의
      이전 성공 등록(판매자 공통값), 사용자가 정한 판매가·재고.
출력: 5단계 저장 형식(`FulfillmentSelectionInput`)과 "특이점·주의점" 목록.

원칙(쿠팡 실등록에서 겪은 반려의 교훈):
- 값을 추측해 채우지 않는다. 온채널 값 또는 사용자가 이미 확정한 값(이전 등록)에서만 가져오고, 도출하지 못한 필수 칸은
  `unresolved`로 남겨 저장을 막는다(fail-closed).
- 무브랜드는 글자가 아니라 NO_BRAND 상태로 둔다(쿠팡은 "브랜드 없음" 글자를 상표로 본다).
- 어린이·위생 관련 표현은 법적 제약(어린이제품 KC 등)이 있어 키워드·상품명에서 걸러 주의점으로 남긴다.
- 이 모듈의 계산부는 DB·네트워크에 접근하지 않는다. 저장·근거 기록은 `RegistrationAssistantService`가 맡는다.
=========================================================
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

NOTES_EVIDENCE_PREFIX = "REGISTRATION_NOTES:"
NOTICE_EVIDENCE_PREFIX = "URL_IMPORT_NOTICE:"

# 이전 성공 등록에서 그대로 가져오는 "판매자 공통" 값 — 상품이 달라도 같은 판매자 설정이다.
SELLER_DEFAULT_KEYS = (
    "deliveryMethod", "deliveryChargeType", "deliveryCharge", "deliveryChargeOnReturn", "returnCharge",
    "deliveryCompanyCode", "vendorUserId",
)
# 브랜드 3상태 중 무브랜드 확인 기록(이전 등록에서 사용자가 확인한 표현).
NO_BRAND_KEYS = ("brandState", "brand", "brandId", "officialBrandName", "brandEnrollmentStatus", "brandLookupFingerprint")

_ORIGIN_CODES = {"중국": "CHINA", "대한민국": "KOREA", "한국": "KOREA", "국산": "KOREA"}

# 쿠팡 고시 라벨(정규화) → 온채널 정보고시 라벨 후보(정규화 일치).
_NOTICE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "제품소재": ("제품소재", "소재", "재질", "원단"),
    "소재": ("제품소재", "소재", "재질"),
    "재질": ("제품소재", "재질", "소재"),
    "색상": ("색상", "컬러"),
    "치수": ("치수", "사이즈", "크기"),
    "크기": ("치수", "크기", "사이즈"),
    "제조자수입자": ("제조자/수입자", "제조자", "수입자", "제조사", "제조원"),
    "제조국": ("제조국", "원산지"),
    "세탁방법및취급시주의사항": ("세탁방법및취급시주의사항", "세탁방법"),
    "제조연월": ("제조년월일", "제조연월", "제조년월", "출시년월"),
    "품질보증기준": ("품질보증기준",),
}
# 이전 성공 등록의 같은 라벨 값을 판매자 공통 값으로 재사용하는 고시 칸(판매자 책임자·연락처 등).
_SELLER_NOTICE_LABELS = ("as책임자와전화번호", "소비자상담관련전화번호")  # `_norm` 결과(소문자·구두점 제거)와 같은 꼴

# 어린이·초경 연상 표현 — 어린이제품 안전특별법(KC) 대상으로 오인될 수 있어 키워드·상품명에서 거른다.
_CHILD_TERMS = ("아동", "어린이", "초등", "여아", "남아", "유아", "초경", "첫생리", "키즈", "주니어")
# 효능을 단정하는 표현 — 상세 이미지·문구에서 쓰려면 표시·광고 확인이 필요하다.
_CLAIM_TERMS = ("항균", "살균", "세균", "치료", "예방", "완화", "의약")

MIN_MARGIN_RATE = Decimal("0.18")


def _norm(text: Any) -> str:
    return re.sub(r"[\s/()\[\]·,.\-_:]+", "", str(text or "")).lower()


@dataclass
class AssistantNote:
    level: str  # INFO / CAUTION / BLOCKER
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}


@dataclass
class Proposal:
    selection: dict[str, Any]
    draft_keywords: list[str]
    notes: list[AssistantNote] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    item_count: int = 0

    @property
    def ready(self) -> bool:
        return not self.unresolved and not any(n.level == "BLOCKER" for n in self.notes)


class AssistantRequest(BaseModel):
    """`propose`/`apply` 공통 입력 — 값은 전부 사용자가 정한 것이다."""

    marketplace_account_id: int | None = None
    display_category_code: str = Field(min_length=1, max_length=20)
    sale_price: int = Field(gt=0)
    stock: int = Field(default=10, ge=1, le=9999)
    unit_cost: int | None = Field(default=None, ge=0, description="공급 원가(마진 확인용)")
    shipping_cost: int | None = Field(default=None, ge=0, description="판매자 부담 배송비(마진 확인용)")
    channel_fee_rate: Decimal = Field(default=Decimal("0.108"), ge=0, le=1)
    return_reserve_rate: Decimal = Field(default=Decimal("0.05"), ge=0, le=1)
    notice_overrides: dict[str, str] = Field(default_factory=dict)
    notice_category: str | None = Field(default=None, max_length=60, description="고시 카테고리 직접 지정")
    # 3단계 사용권 확인 선언 — 사용자가 직접 한 것만 true로 보낸다.
    image_rights_confirmed: bool = False
    # 이미 R2에 올려 둔 결과(별도 단계) — 없으면 비워 둔 채 저장하고 미해결로 알린다.
    images: list[dict] = Field(default_factory=list)
    contents: list[dict] = Field(default_factory=list)
    contents_source_asset_ids: list[int] = Field(default_factory=list)
    confirm_notice: bool = False
    expected_version: int | None = None
    # 온채널 공식 API가 이 상품을 찾았는지(모르면 비워 둔다) — 못 찾았으면 주의점으로 남긴다.
    onch_api_found: bool | None = None

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 순수 계산부
# ---------------------------------------------------------------------------


def parse_manufacture_month(raw: str) -> tuple[str, date | None]:
    """"2022-03-05" → ("2022년 03월", date). 읽을 수 없으면 (원문, None)."""

    match = re.search(r"(\d{4})\D+(\d{1,2})(?:\D+(\d{1,2}))?", str(raw or ""))
    if not match:
        return str(raw or "").strip(), None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return str(raw).strip(), None
    return f"{year}년 {month:02d}월", date(year, month, 1)


def pick_seller_defaults(prior_required_fields: dict | None, prior_notice_information: dict | None) -> dict[str, Any]:
    """이전 성공 등록에서 판매자 공통 값만 골라 낸다(없으면 빈 딕셔너리)."""

    prior = prior_required_fields or {}
    picked = {key: prior[key] for key in SELLER_DEFAULT_KEYS if key in prior and prior[key] not in (None, "")}
    for key in NO_BRAND_KEYS:
        if prior.get("brandState") == "NO_BRAND" and key in prior:
            picked[key] = prior[key]
    seller_notice = {}
    for key, value in (prior_notice_information or {}).items():
        label = key.split("::", 1)[-1]
        if _norm(label) in _SELLER_NOTICE_LABELS and str(value).strip():
            seller_notice[_norm(label)] = str(value)
    picked["_seller_notice"] = seller_notice
    return picked


def map_notice_information(
    notice_fields: list[dict], onch_rows: list[tuple[str, str]], seller_notice: dict[str, str],
    overrides: dict[str, str], category_display_name: str,
) -> tuple[dict[str, str], list[str], list[AssistantNote]]:
    """쿠팡 고시 칸을 온채널 값·판매자 공통 값·사용자 지정 값으로 채운다.

    반환: (notice_information, 채우지 못한 필수 칸 key 목록, 노트)."""

    onch = {_norm(label): value for label, value in onch_rows if str(value).strip()}
    all_not_applicable = {
        _norm(label) for label, value in onch_rows if _norm(value) in ("해당사항없음", "해당없음")
    }
    info: dict[str, str] = {}
    unresolved: list[str] = []
    notes: list[AssistantNote] = []
    for field_def in notice_fields:
        key = str(field_def["key"])
        label_norm = _norm(field_def.get("label") or key.split("::", 1)[-1])
        required = bool(field_def.get("required", True))
        value: str | None = None
        source = ""
        if key in overrides and str(overrides[key]).strip():
            value, source = str(overrides[key]).strip(), "사용자 지정"
        elif label_norm in seller_notice:
            value, source = seller_notice[label_norm], "이전 등록의 판매자 공통 값"
        else:
            for candidate in _NOTICE_SYNONYMS.get(label_norm, (label_norm,)):
                hit = onch.get(_norm(candidate))
                if hit:
                    value, source = hit, "온채널 정보고시"
                    break
            if value is None and label_norm.startswith("kc") and all_not_applicable:
                value, source = "해당사항없음", "온채널 KC 인증 항목이 모두 해당사항없음"
            if value is None and label_norm in ("품명및모델명", "품명"):
                value, source = "", "상품명 기반(아래에서 채움)"
        if label_norm == "제조연월" and value:
            formatted, parsed = parse_manufacture_month(value)
            value = formatted
            if parsed is not None and (date.today() - parsed).days > 730:
                notes.append(AssistantNote(
                    "CAUTION", "STALE_MANUFACTURE_DATE",
                    f"온채널의 제조 시기가 {formatted}로 2년 넘게 지났다 — 재고가 오래된 상품일 수 있어 공급처에 "
                    "현재 출고 상품의 제조 시기를 확인해야 한다(고시에 쓴 값이 실제와 다르면 표시 위반이 된다).",
                ))
        if value is None or not str(value).strip():
            if value == "":
                info[key] = ""  # 상품명 기반 칸은 호출부가 채운다.
                continue
            if required:
                unresolved.append(key)
            continue
        info[key] = str(value).strip()
        if source in ("이전 등록의 판매자 공통 값",):
            notes.append(AssistantNote("INFO", "NOTICE_SELLER_VALUE", f"'{field_def.get('label')}'은 {source}을 재사용했다."))
    return info, unresolved, notes


def choose_notice_group(
    notice_fields: list[dict], onch_rows: list[tuple[str, str]], requested: str | None,
) -> tuple[str | None, AssistantNote | None]:
    """고시 카테고리 하나를 고른다. 요청 지정 > 온채널 "상품정보제공고시 구분"과 일치 > 없음(미해결)."""

    groups = list(dict.fromkeys(str(f["key"]).split("::", 1)[0] for f in notice_fields))
    if not groups:
        return None, AssistantNote("BLOCKER", "NO_NOTICE_GROUP", "쿠팡 카테고리에 고시 항목이 없다.")
    if requested:
        if requested in groups:
            return requested, None
        return None, AssistantNote("BLOCKER", "NOTICE_GROUP_UNKNOWN", f"지정한 고시 카테고리 '{requested}'가 이 카테고리에 없다: {', '.join(groups)}")
    hint = next((v for l, v in onch_rows if "상품정보제공고시구분" in _norm(l)), "")
    for group in groups:
        if hint and (_norm(group) == _norm(hint)):
            return group, AssistantNote(
                "INFO", "NOTICE_GROUP_FROM_ONCH",
                f"고시 카테고리는 온채널의 '상품정보제공고시 구분'({hint})과 같은 '{group}'을 골랐다(선택지: {', '.join(groups)}).",
            )
    return None, AssistantNote(
        "BLOCKER", "NOTICE_GROUP_UNMATCHED",
        f"온채널 고시 구분({hint or '없음'})과 일치하는 쿠팡 고시 카테고리가 없다 — 직접 지정해야 한다: {', '.join(groups)}",
    )


def safe_keywords(raw_keywords: list[str], limit: int = 20) -> tuple[list[str], list[str]]:
    """어린이 연상 표현이 든 키워드를 걸러 (남긴 키워드, 걸러 낸 키워드)로 돌려준다."""

    kept: list[str] = []
    dropped: list[str] = []
    for word in raw_keywords:
        clean = word.strip().lstrip("#").strip()
        if not clean or clean in kept:
            continue
        if any(term in clean for term in _CHILD_TERMS):
            dropped.append(clean)
            continue
        kept.append(clean)
    return kept[:limit], dropped


def keywords_from_onch_rows(onch_rows: list[tuple[str, str]]) -> list[str]:
    for label, value in onch_rows:
        if "추천키워드" in _norm(label):
            return [part.strip() for part in re.split(r"\s*#\s*", str(value)) if part.strip()]
    return []


def build_items(
    options: list[dict], definitions: list[dict], *, sku_prefix: str, sale_price: int, stock: int,
) -> tuple[list[dict], list[str], dict[str, str]]:
    """옵션 조합(색상×사이즈 등)마다 쿠팡 아이템 한 개를 만든다. 필수 구매옵션을 채우지 못하면 미해결.

    초안의 옵션 이름이 카테고리 정의에 없으면, 정의된 이름 중 그 이름으로 끝나는 것이 **정확히 하나**일 때만
    그 이름으로 옮긴다(예: "사이즈" → "패션의류/잡화 사이즈"). 후보가 둘 이상이거나 없으면 미해결로 둔다.
    반환: (아이템, 미해결, {초안 옵션 이름: 쿠팡 정의 이름} — 이름을 옮긴 것만)."""

    exposed_required = [d for d in definitions if d.get("exposed") and d.get("required")]
    unresolved: list[str] = []
    defined_names = [d["attribute_type_name"] for d in definitions if d.get("exposed")]
    aliases: dict[str, str] = {}
    option_names: list[str] = []
    option_values = [o["values"] for o in options if o.get("values")]
    for o in options:
        if not o.get("values"):
            continue
        name = o["name"]
        if name not in defined_names:
            matches = [d for d in defined_names if _norm(d).endswith(_norm(name)) and d not in {o2["name"] for o2 in options}]
            if len(matches) == 1:
                aliases[name] = matches[0]
                name = matches[0]
            else:
                unresolved.append(f"구매옵션 정의에 없는 옵션:{name}")
        option_names.append(name)
    for d in exposed_required:
        name = d["attribute_type_name"]
        if name not in option_names and name != "수량":
            unresolved.append(f"구매옵션:{name}")
    if unresolved:
        return [], unresolved, aliases
    combos = list(itertools.product(*option_values)) if option_values else [()]
    items: list[dict] = []
    for index, combo in enumerate(combos, start=1):
        attrs = dict(zip(option_names, combo))
        if any(d["attribute_type_name"] == "수량" for d in exposed_required):
            attrs.setdefault("수량", "1")
        for name, value in attrs.items():
            definition = next((d for d in definitions if d["attribute_type_name"] == name), None)
            if definition and definition.get("input_type") == "SELECT" and definition.get("input_values"):
                if value not in definition["input_values"]:
                    unresolved.append(f"구매옵션 허용값 밖:{name}={value}")
        items.append({
            "itemName": " ".join(str(v) for v in combo) or "기본",
            "externalVendorSku": f"{sku_prefix}-{index:02d}",
            "salePrice": sale_price, "originalPrice": sale_price,
            "maximumBuyCount": stock, "unitCount": 1,
            "optionAttributes": attrs,
        })
    return items, unresolved, aliases


def margin_check(request: AssistantRequest) -> tuple[Decimal, Decimal] | None:
    """(마진금액, 마진율). 원가·배송비를 알려 주지 않았으면 None."""

    if request.unit_cost is None:
        return None
    price = Decimal(request.sale_price)
    cost = Decimal(request.unit_cost) + Decimal(request.shipping_cost or 0)
    amount = price - cost - price * request.channel_fee_rate - price * request.return_reserve_rate
    return amount, (amount / price)


def build_proposal(
    *,
    wizard_draft: dict, candidate_id: int, source_reference: str, account_id: int,
    onch_rows: list[tuple[str, str]], metadata: dict, seller_defaults: dict,
    outbound_code: str | None, return_center_code: str | None, request: AssistantRequest,
) -> Proposal:
    notes: list[AssistantNote] = []
    unresolved: list[str] = []
    seller_defaults = dict(seller_defaults)
    seller_notice = seller_defaults.pop("_seller_notice", {})

    # 1) 고시 — 쿠팡은 고시 카테고리 하나만 쓴다(위저드 화면과 같은 규칙): 온채널의 "상품정보제공고시 구분"과 같은 것을 고른다.
    all_notice_fields = metadata.get("notice_fields") or []
    chosen_group, group_note = choose_notice_group(all_notice_fields, onch_rows, request.notice_category)
    if group_note is not None:
        notes.append(group_note)
    notice_fields = [f for f in all_notice_fields if str(f["key"]).split("::", 1)[0] == chosen_group]
    notice_info, notice_missing, notice_notes = map_notice_information(
        notice_fields, onch_rows, seller_notice, request.notice_overrides, metadata.get("display_category_name", ""),
    )
    notes.extend(notice_notes)
    unresolved.extend(f"고시:{key}" for key in notice_missing)
    product_name = str(wizard_draft.get("product_name") or "").strip()
    for key, value in list(notice_info.items()):
        if value == "":
            notice_info[key] = product_name
    notice_info["__notice_category_name"] = chosen_group or ""

    # 2) 옵션·아이템
    code = source_reference.split(":", 1)[-1]
    sku_prefix = f"PC{candidate_id}-{code}"
    items, option_unresolved, option_aliases = build_items(
        wizard_draft.get("options") or [], metadata.get("purchase_option_fields") or [],
        sku_prefix=sku_prefix, sale_price=request.sale_price, stock=request.stock,
    )
    unresolved.extend(option_unresolved)
    for draft_name, defined_name in option_aliases.items():
        notes.append(AssistantNote(
            "INFO", "OPTION_NAME_MAPPED",
            f"초안 옵션 '{draft_name}'은 쿠팡 카테고리의 구매옵션 '{defined_name}'(이름이 '{draft_name}'로 끝나는 유일한 정의)로 옮겼다.",
        ))

    # 3) 원산지·브랜드
    origin_text = next((v for l, v in onch_rows if _norm(l) == "제조국"), "")
    origin = _ORIGIN_CODES.get(origin_text.strip())
    if origin is None:
        unresolved.append("원산지(제조국)")
    if seller_defaults.get("brandState") != "NO_BRAND":
        unresolved.append("브랜드 확인(이전 무브랜드 기록 없음)")

    # 4) 판매자 공통
    missing_seller = [k for k in SELLER_DEFAULT_KEYS if k not in seller_defaults]
    if missing_seller:
        unresolved.extend(f"판매자 공통:{k}" for k in missing_seller)
    if not outbound_code or not return_center_code:
        unresolved.append("출고지·반품지 선택")

    # 5) 키워드
    kept, dropped = safe_keywords(keywords_from_onch_rows(onch_rows))
    if dropped:
        notes.append(AssistantNote(
            "CAUTION", "CHILD_TERMS_DROPPED",
            "온채널 추천 키워드 중 어린이·초경 연상 표현(" + ", ".join(dropped[:6]) + ")은 뺐다 — 어린이제품 안전특별법(KC) "
            "대상으로 오인되거나 연령 오표시가 될 수 있다. 상품명·상세·이미지 문구에도 쓰지 않는다.",
        ))
    if any(term in product_name for term in _CHILD_TERMS):
        notes.append(AssistantNote("BLOCKER", "CHILD_TERM_IN_NAME", "상품명에 어린이 연상 표현이 있다 — 고쳐야 한다."))

    # 6) 가격·마진
    margin = margin_check(request)
    if margin is not None:
        amount, rate = margin
        text = f"판매가 {request.sale_price:,}원 기준 마진 {amount:,.0f}원({rate * 100:.1f}%)"
        if rate < MIN_MARGIN_RATE:
            notes.append(AssistantNote("BLOCKER", "MARGIN_BELOW_MIN", text + " — HOMEZ 최소 마진 18% 미달."))
        else:
            notes.append(AssistantNote("INFO", "MARGIN_OK", text + f"(수수료율 {request.channel_fee_rate * 100:.1f}%·반품 예비 {request.return_reserve_rate * 100:.0f}% 가정)."))
    if request.channel_fee_rate == Decimal("0.108"):
        notes.append(AssistantNote("CAUTION", "FEE_RATE_ASSUMED", "수수료율 10.8%는 카테고리 실제 수수료율을 확인하지 못한 가정이다."))

    # 7) 일반 주의점
    if len(items) >= 12:
        notes.append(AssistantNote(
            "CAUTION", "MANY_OPTIONS",
            f"옵션 조합이 {len(items)}개다 — 쿠팡은 아이템마다 이미지·상세설명을 함께 보내 요청이 커진다. "
            "전송 실패(요청 크기·옵션 상한) 시 옵션을 나눠 등록해야 할 수 있다.",
        ))
    if request.onch_api_found is False:
        notes.append(AssistantNote(
            "CAUTION", "ONCH_API_NOT_FOUND",
            "온채널 API가 이 상품을 조회하지 못했다(판매신청 승인 전일 수 있음) — 공급가·재고·옵션 ID를 API로 확인하지 못했다. "
            "주문이 들어오기 전에 옵션 ID를 연결(옵션 식별자 동기화)해야 자동 발주가 된다.",
        ))
    notes.append(AssistantNote(
        "CAUTION", "OPTION_SKU_PLACEHOLDER",
        f"외부 판매자 SKU는 임시 규칙({sku_prefix}-NN)이다 — 온채널 옵션 ID가 확인되면 옵션 연결에서 맞춰야 한다.",
    ))
    notes.append(AssistantNote(
        "CAUTION", "CLAIM_TERMS_IN_IMAGES",
        "상세 이미지 안의 글자(" + "·".join(_CLAIM_TERMS[:4]) + " 등 효능 표현)는 자동으로 읽지 못한다 — 사람이 보고 항균·치료 등 "
        "단정 표현이 있는 컷은 빼야 한다.",
    ))
    if not request.image_rights_confirmed:
        unresolved.append("이미지 사용권 확인(사용자 선언)")
    if not request.images:
        unresolved.append("대표 이미지 공개 주소(R2 업로드)")
    if not request.contents:
        unresolved.append("상세설명 contents(R2 업로드)")

    brand_fields = {k: seller_defaults[k] for k in NO_BRAND_KEYS if k in seller_defaults}
    required_fields: dict[str, Any] = {
        **{k: seller_defaults[k] for k in SELLER_DEFAULT_KEYS if k in seller_defaults},
        **brand_fields,
        "maximumBuyCount": request.stock,
        "originalPrice": request.sale_price, "salePrice": request.sale_price,
        "displayCategoryCode": int(request.display_category_code) if request.display_category_code.isdigit() else None,
        "items": items,
        "images": list(request.images), "contents": list(request.contents),
        "contentsSourceAssetIds": list(request.contents_source_asset_ids),
        "liveImageRightsConfirmed": bool(request.image_rights_confirmed),
    }
    required_fields = {k: v for k, v in required_fields.items() if v is not None}

    attributes: dict[str, Any] = {
        "origin_country": origin or "",
        "brand": seller_defaults.get("brand", ""),
        "product_identifier": items[0]["externalVendorSku"] if items else "",
        "official_category_code": str(request.display_category_code),
        "category_metadata_version": metadata.get("metadata_version", ""),
        "category_metadata_fingerprint": metadata.get("metadata_fingerprint", ""),
        "notice_information": notice_info,
        "notice_required_field_keys": [f["key"] for f in notice_fields if f.get("required", True)],
        "notice_field_definitions": notice_fields,
        "purchase_option_field_definitions": metadata.get("purchase_option_fields") or [],
        # 채널 정책 규칙(PRODUCT_IDENTIFICATION_REQUIRED)이 구매옵션 존재 근거를 요구한다. 다중 옵션 상품은 아이템마다
        # optionAttributes를 갖지만(전송 내용은 그쪽이 쓰인다), 정책 근거로는 첫 조합을 대표로 둔다.
        "purchase_options": dict(items[0]["optionAttributes"]) if items else {},
    }
    selection = {
        "marketplace_account_id": account_id, "fulfillment_mode": "SELLER_FULFILLED",
        "required_fields": required_fields,
        "outbound_shipping_place_code": outbound_code, "return_center_code": return_center_code,
        "channel_policy_attributes": attributes,
        "channel_policy_confirmed_evidence_rule_codes": ["CATEGORY_NOTICE_INFO_REQUIRED"] if request.confirm_notice else [],
    }
    return Proposal(
        selection=selection, draft_keywords=kept, notes=notes, unresolved=sorted(set(unresolved)), item_count=len(items),
    )


def notes_payload(proposal: Proposal, *, wizard_id: int, applied: bool, when: str) -> str:
    return NOTES_EVIDENCE_PREFIX + json.dumps({
        "wizard_id": wizard_id, "applied": applied, "at": when, "item_count": proposal.item_count,
        "unresolved": proposal.unresolved, "notes": [n.to_dict() for n in proposal.notes],
    }, ensure_ascii=False)


def metadata_to_dict(metadata, fingerprint: str) -> dict:
    """공급자 `CategoryMetadata` → 엔드포인트 응답과 같은 dict 형식."""

    return {
        "display_category_code": metadata.display_category_code,
        "display_category_name": metadata.display_category_name,
        "metadata_version": metadata.version,
        "metadata_fingerprint": fingerprint,
        "notice_fields": [f.__dict__ for f in metadata.notice_fields],
        "purchase_option_fields": [
            {**f.__dict__, "usable_units": list(f.usable_units), "input_values": list(f.input_values)}
            for f in metadata.purchase_option_fields
        ],
    }
