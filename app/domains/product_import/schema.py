"""
=========================================================
Homez OS

File : app/domains/product_import/schema.py

URL 상품 가져오기(2026-10-14) — 요청·응답 스키마.

흐름: URL 검색 → 기재 내용 복사(사용자가 고쳐 확정) → 수익 계산 → 상품 후보·위저드 초안 생성.
읽어 온 값은 전부 "제안"이다. 브랜드·원산지·사용권·정보고시 확인 같은 사용자 확인 항목은 이 기능이
자동으로 확정하지 않는다(쿠팡 실등록에서 겪은 반려의 교훈).
=========================================================
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

SourceKind = Literal["ONCHANNEL", "GENERIC"]


class UrlSearchRequest(BaseModel):

    url: str = Field(min_length=8, max_length=2000)

    model_config = ConfigDict(extra="forbid")


class ImageSuggestion(BaseModel):

    url: str
    # MAIN(대표·추가 이미지) 또는 DETAIL(상세설명용 긴 이미지 후보). 확정이 아니라 제안이다.
    suggested_purpose: Literal["MAIN", "DETAIL"] = "MAIN"
    selected_by_default: bool = False
    note: str | None = None


class SupplierOffer(BaseModel):
    """공급처(온채널) 공식 API에서 읽은 가격·옵션 — 읽지 못하면 비워 두고 사유를 warnings에 남긴다."""

    product_code: str
    option_label: str | None = None
    supply_price: Decimal | None = None
    base_shipping_cost: int | None = None
    jeju_shipping_cost: int | None = None
    remote_area_shipping_cost: int | None = None
    status: str | None = None
    tax_exempt: bool | None = None


class NoticeRow(BaseModel):

    label: str
    value: str


class SimilarCandidate(BaseModel):
    """이미 있는 후보 중 이 상품과 같거나 비슷한 것(읽기 전용 알림 — 합치거나 막지 않는다)."""

    candidate_id: int
    product_name: str
    status: str = ""
    relation: Literal["DUPLICATE", "VARIANT", "SIMILAR"]
    core_score: float
    reasons: list[str] = Field(default_factory=list)


class UrlSearchResponse(BaseModel):

    source_kind: SourceKind
    source_url: str
    # 후보 중복 방지·추적에 쓰는 기준값(예: ONCHANNEL:CH5306792, URL:www.example.com:<해시>)
    source_reference: str
    title: str | None = None
    suggested_product_name: str | None = None
    suggested_description: str | None = None
    price_text: str | None = None
    supplier_name: str | None = None
    origin_text: str | None = None
    manufacturer_text: str | None = None
    model_name: str | None = None
    notice_rows: list[NoticeRow] = Field(default_factory=list)
    images: list[ImageSuggestion] = Field(default_factory=list)
    offer: SupplierOffer | None = None
    warnings: list[str] = Field(default_factory=list)
    # 2026-10-18 S1 — 이미 있는 후보 중 같거나 비슷한 것(가까운 순 최대 5개). 후보를 막거나 합치지 않는다.
    similar_candidates: list[SimilarCandidate] = Field(default_factory=list)


class MarginProposalRequest(BaseModel):

    cost_of_goods: Decimal = Field(ge=0)
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)
    channel_fee_rate: Decimal = Field(default=Decimal("0.108"), ge=0, le=1)
    payment_fee_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    return_reserve_rate: Decimal = Field(default=Decimal("0.03"), ge=0, le=1)
    packaging_cost: Decimal = Field(default=Decimal("0"), ge=0)
    target_margin_rate: Decimal = Field(default=Decimal("0.18"), ge=0, le=Decimal("0.9"))
    # 사용자가 판매가를 직접 정하면 그 판매가의 마진만 다시 계산한다(제안은 그대로 함께 돌려준다).
    sale_price: Decimal | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")


class MarginProposalResponse(BaseModel):

    proposed_sale_price: Decimal | None
    sale_price: Decimal | None
    expected_revenue: Decimal | None = None
    total_cost: Decimal | None = None
    margin_amount: Decimal | None = None
    margin_rate: Decimal | None = None
    break_even_price: Decimal | None = None
    meets_min_margin: bool | None = None
    min_margin_rate: Decimal
    warnings: list[str] = Field(default_factory=list)


class DraftCreateRequest(BaseModel):
    """사용자가 화면에서 확인·수정한 값만 받는다."""

    source_kind: SourceKind
    source_url: str = Field(min_length=8, max_length=2000)
    source_reference: str = Field(min_length=1, max_length=200)
    product_name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    category_hint: str | None = Field(default=None, max_length=100)
    keywords: list[str] = Field(default_factory=list)
    # 검색 결과의 고시 표 — 5단계 정보고시 칸의 '제안'으로만 쓰이도록 후보 근거에 보관한다(연락처류는 저장하지 않는다).
    notice_rows: list[NoticeRow] = Field(default_factory=list, max_length=60)
    image_urls_main: list[str] = Field(default_factory=list)
    image_urls_detail: list[str] = Field(default_factory=list)
    # 원가·판매가·비율을 모두 주면 위저드 6단계 경제성 입력까지 채운다(없으면 건너뛴다).
    margin: MarginProposalRequest | None = None
    # 2026-10-18 — URL 분석 화면의 계산 결과 스냅샷(화면 기준). 있으면 후보 근거(append-only)에 보관해 나중에 추적한다.
    analysis_report: dict | None = None
    creation_idempotency_key: str = Field(min_length=1, max_length=160)

    model_config = ConfigDict(extra="forbid")


class ImageImportResult(BaseModel):

    source_url: str
    purpose: str
    success: bool
    asset_id: int | None = None
    error_message: str | None = None


class DraftCreateResponse(BaseModel):

    candidate_id: int
    candidate_created: bool
    wizard_id: int
    wizard_created: bool
    economics_saved: bool
    images: list[ImageImportResult] = Field(default_factory=list)
    # 사용자가 직접 해야 하는 남은 확인 목록(후보 승인, 사용권·브랜드·정보고시 확인, 8단계 승인, 전송).
    next_steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NoticeSuggestionRequest(BaseModel):

    candidate_id: int
    labels: list[str] = Field(default_factory=list, max_length=80)

    model_config = ConfigDict(extra="forbid")


class NoticeSuggestionResponse(BaseModel):
    """쿠팡 정보고시 칸 라벨 → 제안 값. 못 맞춘 칸은 들어 있지 않다."""

    suggestions: dict[str, str] = Field(default_factory=dict)
    source_found: bool = False


# ----------------------------------------------------------------------
# 2026-10-18 URL 상품 분석(AI) · 단가 · 이익 계산
# ----------------------------------------------------------------------

VatState = Literal["INCLUDED", "EXCLUDED", "EXEMPT"]


class AnalyzeRequest(BaseModel):

    url: str = Field(min_length=8, max_length=2000)

    model_config = ConfigDict(extra="forbid")


class CalculateRequest(BaseModel):
    """계산 입력 — 모르는 값(공급가·부가세·배송비)은 비워 두면 계산하지 않고 보류로 돌려준다(0으로 채우지 않는다)."""

    channel: str = Field(default="COUPANG", min_length=2, max_length=20)
    category_code: str | None = Field(default=None, max_length=20)
    supply_price: Decimal | None = Field(default=None, ge=0)
    vat_state: VatState | None = None
    shipping_cost: Decimal | None = Field(default=None, ge=0)
    sell_quantity: int = Field(default=1, ge=1, le=1000)
    packaging_cost: Decimal = Field(default=Decimal("0"), ge=0)
    return_reserve_rate: Decimal = Field(default=Decimal("0.03"), ge=0, le=Decimal("0.5"))
    payment_fee_rate: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("0.5"))
    sale_price: Decimal | None = Field(default=None, ge=0)
    pack_count: int | None = Field(default=None, ge=1, le=1000)
    unit_volume: Decimal | None = Field(default=None, gt=0, le=100000)
    unit: str | None = Field(default=None, max_length=10)
    risk_codes: list[str] = Field(default_factory=list, max_length=10)
    acknowledged_risks: list[str] = Field(default_factory=list, max_length=10)
    ai_sourced: list[Literal["supply_price", "vat_state", "shipping_cost"]] = Field(default_factory=list, max_length=3)
    ai_values_confirmed: bool = False

    model_config = ConfigDict(extra="forbid")


class ChannelFeeSaveRequest(BaseModel):

    channel: str = Field(min_length=2, max_length=20)
    category_code: str = Field(min_length=1, max_length=20)
    rate: Decimal
    note: str | None = Field(default=None, max_length=40)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 2026-10-18 마케팅 AI M2 — 경쟁 가격 비교
# ---------------------------------------------------------------------------

class CompetitorReadRequest(BaseModel):
    """경쟁 상품 주소 1~5개(사용자가 건건이 넣은 것). 검색 결과 수집·페이지 넘김은 없다."""

    urls: list[str] = Field(min_length=1, max_length=5)

    model_config = ConfigDict(extra="forbid")


class CompetitorEntryRequest(BaseModel):
    """계산에 넣을 한 줄. AI가 읽은 값(AI_PAGE)은 사용자가 페이지에서 직접 확인했다는 표시(confirmed)가 있어야 센다."""

    label: str = Field(min_length=1, max_length=60)
    url: str | None = Field(default=None, max_length=2000)
    price: int = Field(ge=1, le=100_000_000)
    shipping_fee: int | None = Field(default=None, ge=0, le=1_000_000)
    pack_count: int | None = Field(default=None, ge=1, le=1000)
    unit_volume: Decimal | None = Field(default=None, gt=0, le=100000)
    unit: str | None = Field(default=None, max_length=10)
    source: Literal["AI_PAGE", "MANUAL"] = "MANUAL"
    confirmed: bool = False
    checked_at: str | None = Field(default=None, max_length=40)

    model_config = ConfigDict(extra="forbid")


class CompetitorCompareRequest(BaseModel):

    entries: list[CompetitorEntryRequest] = Field(min_length=1, max_length=10)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 2026-10-18 마케팅 AI M3 — 가격 전략 제안
# ---------------------------------------------------------------------------

class MarketInput(BaseModel):
    """M2 경쟁 가격 비교 결과에서 가져온 시장 구간. 항목이 2개 이상이면 중앙값·최고가가 있어야 한다."""

    n: int = Field(ge=1, le=10)
    basis: Literal["total", "display"]
    min: Decimal = Field(ge=0, le=Decimal("100000000"))
    median: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    max: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_range(self):
        if self.n >= 2:
            if self.median is None or self.max is None:
                raise ValueError("경쟁 가격이 2곳 이상이면 중앙값과 최고가가 있어야 합니다.")
            if not self.min <= self.median <= self.max:
                raise ValueError("시장 구간은 최저 ≤ 중앙값 ≤ 최고 순서여야 합니다.")
        elif self.median is not None or self.max is not None:
            raise ValueError("경쟁 가격이 1곳이면 중앙값과 최고가를 쓸 수 없습니다.")
        return self


class StrategyRequest(CalculateRequest):
    """가격 전략 입력 = 계산 입력(원가·부가세·배송비·수수료 등) + 선택적 시장 구간. `sale_price`는 사용자가 생각하는 판매가(평가용)다."""

    market: MarketInput | None = None
