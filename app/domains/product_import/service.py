"""
=========================================================
Homez OS

File : app/domains/product_import/service.py

URL 상품 가져오기(2026-10-14) — 검색 → 수익 계산 → 상품 후보·위저드 초안 생성.

원칙(쿠팡 실등록 2건에서 얻은 교훈):
  - 읽은 값은 전부 "제안"이다. 브랜드·원산지·사용권·정보고시 확인과 후보 승인, 8단계 승인, 쿠팡 전송은
    이 서비스가 하지 않는다 — 사용자가 기존 화면에서 직접 한다.
  - 외부 접근은 사용자가 입력한 URL 1건당 페이지 읽기(+ 온채널이면 공식 API 상품 조회 1회)뿐이다.
    SSRF 방어(내부망 차단, 리디렉션 재검증, 크기 제한)는 media_asset/url_import.py를 그대로 쓴다.
  - 못 읽은 값은 비워 두고 warnings에 이유를 남긴다. 추측으로 채우지 않는다.
=========================================================
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.domains.marketplace_listing.listing_wizard_schema import EconomicsInputItem
from app.domains.marketplace_listing.listing_wizard_schema import WizardCreateRequest
from app.domains.marketplace_listing.listing_wizard_schema import WizardDraftUpdateRequest
from app.domains.marketplace_listing.listing_wizard_schema import WizardEconomicsUpdateRequest
from app.domains.marketplace_listing.listing_wizard_schema import WizardSourceUpdateRequest
from app.domains.marketplace_listing.listing_wizard_service import ListingWizardService
from app.domains.marketplace_listing.margin_calculator import calculate_economics
from app.domains.marketplace_listing.margin_calculator import derive_sale_price_for_margin_rate
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.constants import MediaAssetPurpose
from app.domains.media_asset.constants import MediaAssetSourceClassification
from app.domains.media_asset.url_import import Transport
from app.domains.media_asset.url_import import UrlImportError
from app.domains.media_asset.url_import import fetch_image_safely
from app.domains.media_asset.url_import import validate_fetchable_url
from app.domains.media_asset.url_import_service import UrlImageImportService
from app.domains.product_candidate.constants import EvidenceType
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.product_import import schema
from app.domains.product_import.generic_extractor import claim_words_in
from app.domains.product_import.generic_extractor import extract_generic
from app.domains.product_import.html_tree import parse_html
from app.domains.product_import.notice_suggestions import decode_notice_rows
from app.domains.product_import.notice_suggestions import encode_notice_rows
from app.domains.product_import.notice_suggestions import suggest_notice_values
from app.domains.product_import.onchannel_page import is_onchannel_product_url
from app.domains.product_import.onchannel_page import parse_onchannel_page
from app.domains.product_import.onchannel_page import suggest_description

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_IMAGE_SUGGESTIONS = 40
MIN_MARGIN_RATE = Decimal("0.18")  # HOMEZ 기본 최소 마진(사용자 결정, 2026-10-06)
MAX_NOTICE_ROWS = 40
_LONG_SEGMENT_HEIGHT = 1200

# 상품 정보 표에서 "고시"가 아닌(상품 기본 정보) 라벨 — 정보고시 목록에서는 뺀다.
_NON_NOTICE_LABELS = frozenset({
    "제품명", "상품코드", "공급업체분류", "공급사페이지", "판매사가", "운영채널분류", "과면세 구분", "최소구매수량",
    "택배비/택배사", "배송비 분류", "주문마감/발송처/기간", "제품상태", "입점일자", "회원등급", "선택 상품수",
})
_ORIGIN_LABELS = ("제조국", "원산지", "제조국(원산지)")
_MANUFACTURER_LABELS = ("제조자/수입자", "제조자(수입자)", "제조사", "제조원", "수입자")
_MODEL_LABELS = ("품명 및 모델명", "모델명", "품명")


class ProductImportError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 실패(원인 설명 포함). 예외 원문(자격증명·주소)은 담지 않는다."""


def _decode_html(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    candidates = [charset_match.group(1)] if charset_match else []
    head = body[:4096].decode("ascii", errors="ignore")
    meta_match = re.search(r"<meta[^>]+charset=[\"']?([\w-]+)", head, re.IGNORECASE)
    if meta_match:
        candidates.append(meta_match.group(1))
    candidates.append("utf-8")
    for charset in candidates:
        try:
            return body.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _first_value(pairs: list[tuple[str, str]], labels: tuple[str, ...]) -> str | None:
    for wanted in labels:
        for label, value in pairs:
            if label == wanted:
                return value
    return None


def _clean_name(title: str | None) -> str | None:
    if not title:
        return None
    name = re.sub(r"\s+", " ", title).strip()
    return name[:100] or None


class ProductImportService:

    def __init__(self, db: Session, transport: Transport, connection_service=None):
        self.db = db
        self.transport = transport
        # 온채널 공식 API 조회용(PurchaseChannelConnectionService). 없으면 가격 없이 페이지 정보만 돌려준다.
        self.connection_service = connection_service

    # ------------------------------------------------------------------
    # 1) URL 검색
    # ------------------------------------------------------------------

    def _fetch_html(self, url: str) -> tuple[str, str]:
        current = url
        redirects = 0
        while True:
            validate_fetchable_url(current)
            response = self.transport.get(current, timeout=10.0, max_bytes=MAX_HTML_BYTES)
            if response.status_code in (301, 302, 303, 307, 308):
                redirects += 1
                if redirects > MAX_REDIRECTS or not response.redirect_location:
                    raise ProductImportError("페이지가 너무 많이 이동(리디렉션)해서 가져오지 않았습니다.")
                current = response.redirect_location
                continue
            break
        if response.status_code != 200:
            raise ProductImportError(f"상품 페이지를 가져오지 못했습니다(HTTP {response.status_code}).")
        if len(response.body) > MAX_HTML_BYTES:
            raise ProductImportError("상품 페이지가 너무 큽니다.")
        content_type = response.headers.get("Content-Type") or ""
        if content_type and "html" not in content_type.lower():
            raise ProductImportError(f"HTML 페이지가 아닙니다(Content-Type={content_type.split(';')[0]}).")
        return _decode_html(response.body, content_type), current

    def search(self, url: str, company_id: int, user_id: int | None) -> schema.UrlSearchResponse:
        return self.search_with_text(url, company_id, user_id)[0]

    def search_with_text(self, url: str, company_id: int, user_id: int | None) -> tuple[schema.UrlSearchResponse, str]:
        """`search`와 같은 읽기(페이지 1회, 온채널이면 공식 API 상품 조회 1회)에 보이는 페이지 글을 함께 돌려준다.
        URL 상품 분석(AI)이 근거 인용을 확인할 때 쓴다 — 추가 접근은 없다."""

        url = url.strip()
        try:
            html, final_url = self._fetch_html(url)
        except UrlImportError as exc:
            raise ProductImportError(str(exc)) from exc

        page_text = re.sub(r"\s+", " ", parse_html(html).text() or "").strip()
        if is_onchannel_product_url(final_url):
            return self._search_onchannel(html, final_url, company_id, user_id), page_text
        return self._search_generic(html, final_url), page_text

    def _search_onchannel(
        self, html: str, url: str, company_id: int, user_id: int | None,
    ) -> schema.UrlSearchResponse:
        page = parse_onchannel_page(html, url)
        warnings: list[str] = []
        pairs = page.pairs

        notice_rows = [
            schema.NoticeRow(label=label, value=value)
            for label, value in pairs if label not in _NON_NOTICE_LABELS
        ][:MAX_NOTICE_ROWS]

        images: list[schema.ImageSuggestion] = []
        for image_url in page.main_image_urls[:MAX_IMAGE_SUGGESTIONS]:
            images.append(schema.ImageSuggestion(
                url=image_url, suggested_purpose="MAIN", selected_by_default=True,
                note="대표·추가 이미지 후보 — 가로·세로 500px 이상이어야 쿠팡에 등록됩니다.",
            ))
        for image_url in page.detail_image_urls[:MAX_IMAGE_SUGGESTIONS]:
            images.append(schema.ImageSuggestion(
                url=image_url, suggested_purpose="DETAIL", selected_by_default=True,
                note="상세설명용 긴 이미지일 수 있습니다 — 가져오면 자동으로 구간 분할됩니다.",
            ))
        if not images:
            warnings.append("페이지에서 이미지를 찾지 못했습니다 — 이미지는 3단계에서 직접 올려 주세요.")

        offer = None
        if page.product_code is None:
            warnings.append(
                "온채널 상품코드(CH…)를 읽지 못했습니다 — 가격·옵션을 자동으로 가져올 수 없습니다. "
                "상품코드를 확인한 뒤 원가를 직접 입력하세요.",
            )
            source_reference = f"URL:{urlparse(url).hostname}:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}"
        else:
            source_reference = f"ONCHANNEL:{page.product_code}"
            offer = self._lookup_offer(page.product_code, company_id, user_id, warnings)

        model_name = _first_value(pairs, _MODEL_LABELS)
        title = page.title
        name = _clean_name(title)
        self._append_name_warnings(name, model_name, warnings)

        return schema.UrlSearchResponse(
            source_kind="ONCHANNEL", source_url=url, source_reference=source_reference,
            title=title, suggested_product_name=name,
            suggested_description=suggest_description(page),
            price_text=None,  # 온채널 가격은 로그인해야 보이고 공식 API(offer)로만 읽는다.
            supplier_name=page.supplier_class,
            origin_text=_first_value(pairs, _ORIGIN_LABELS),
            manufacturer_text=_first_value(pairs, _MANUFACTURER_LABELS),
            model_name=model_name, notice_rows=notice_rows, images=images, offer=offer, warnings=warnings,
        )

    def _lookup_offer(
        self, product_code: str, company_id: int, user_id: int | None, warnings: list[str],
    ) -> schema.SupplierOffer | None:
        if self.connection_service is None:
            warnings.append("온채널 API 연결이 준비되지 않아 가격을 읽지 못했습니다 — 원가를 직접 입력하세요.")
            return None
        try:
            connections = self.connection_service.list_connections(company_id, mall_code="ONCHANNEL")
        except Exception as exc:  # noqa: BLE001 — 원문은 숨기고 유형만 알린다.
            warnings.append(f"온채널 연결 목록을 읽지 못했습니다(오류 유형: {type(exc).__name__}).")
            return None
        active = [c for c in connections if getattr(c, "is_active", True)]
        if not active:
            warnings.append("사용 가능한 온채널 API 연결이 없어 가격을 읽지 못했습니다 — 원가를 직접 입력하세요.")
            return None
        try:
            result = self.connection_service.lookup_product(
                active[0].id, company_id, product_code, triggered_by=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "OnchannelNotFoundError":
                # 2026-10-14 실제 확인: 아직 판매신청하지 않은 상품은 공식 API가 "없음"으로 답했다
                # (페이지에는 상품이 보이는데도). 원인을 단정하지 않고 가능성과 대응만 알린다.
                warnings.append(
                    f"온채널 API가 이 상품({product_code})을 찾지 못했습니다 — 판매신청을 하지 않은 상품은 "
                    "API로 조회되지 않을 수 있습니다. 온채널에서 판매사가를 확인해 원가를 직접 입력하세요.",
                )
                return None
            warnings.append(
                f"온채널 상품 조회에 실패해 가격을 읽지 못했습니다(오류 유형: {type(exc).__name__}). "
                "원가를 직접 입력하거나 잠시 뒤 다시 검색하세요.",
            )
            return None

        options = list(getattr(result, "options", ()) or ())
        if not options:
            warnings.append("온채널 응답에 옵션·가격이 없습니다 — 원가를 직접 입력하세요.")
            first = None
        else:
            first = options[0]
            if len(options) > 1:
                warnings.append(
                    f"옵션이 {len(options)}개입니다 — 첫 옵션('{first.label}')의 가격을 보여 줍니다. "
                    "다른 옵션을 팔려면 원가를 직접 확인하세요.",
                )
        shipping = getattr(result, "shipping_info", None)
        return schema.SupplierOffer(
            product_code=product_code,
            option_label=getattr(first, "label", None) if first else None,
            supply_price=getattr(first, "price", None) if first else None,
            base_shipping_cost=getattr(shipping, "base_shipping_cost", None),
            jeju_shipping_cost=getattr(shipping, "jeju_shipping_cost", None),
            remote_area_shipping_cost=getattr(shipping, "remote_area_shipping_cost", None),
            status=getattr(result, "status", None),
            tax_exempt=getattr(result, "tax_exempt", None),
        )

    def _search_generic(self, html: str, url: str) -> schema.UrlSearchResponse:
        extracted = extract_generic(html, url)
        warnings = [
            "일반 페이지는 사이트 구조가 달라 읽은 값이 틀리거나 비어 있을 수 있습니다 — 반드시 확인하고 고쳐 주세요.",
        ]
        if not extracted.title:
            warnings.append("상품명을 읽지 못했습니다 — 직접 입력하세요.")
        if not extracted.price_text:
            warnings.append("가격 문구를 읽지 못했습니다 — 원가를 직접 입력하세요.")

        images = []
        for image in extracted.images[:MAX_IMAGE_SUGGESTIONS]:
            images.append(schema.ImageSuggestion(
                url=image.url, suggested_purpose="MAIN",
                selected_by_default=image.declared_representative,
                note="페이지가 대표 이미지로 지정함" if image.declared_representative else None,
            ))

        pairs = extracted.pairs
        model_name = _first_value(pairs, _MODEL_LABELS)
        name = _clean_name(extracted.title)
        self._append_name_warnings(name, model_name, warnings)
        if extracted.brand_hint:
            warnings.append(f"페이지에 브랜드로 보이는 표기('{extracted.brand_hint}')가 있습니다 — 브랜드 확인이 필요합니다.")

        host = urlparse(url).hostname or "unknown"
        return schema.UrlSearchResponse(
            source_kind="GENERIC", source_url=url,
            source_reference=f"URL:{host}:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}",
            title=extracted.title, suggested_product_name=name,
            suggested_description=(extracted.description or None),
            price_text=extracted.price_text,
            origin_text=_first_value(pairs, _ORIGIN_LABELS),
            manufacturer_text=_first_value(pairs, _MANUFACTURER_LABELS),
            model_name=model_name,
            notice_rows=[schema.NoticeRow(label=a, value=b) for a, b in pairs][:MAX_NOTICE_ROWS],
            images=images, offer=None, warnings=warnings,
        )

    @staticmethod
    def _append_name_warnings(name: str | None, model_name: str | None, warnings: list[str]) -> None:
        words = claim_words_in(name)
        if words:
            warnings.append(
                f"상품명에 의료·효능으로 오인될 수 있는 표현이 있습니다({', '.join(words)}) — "
                "쿠팡 심사에서 반려될 수 있으니 순화해서 입력하세요.",
            )
        if name and model_name:
            first_token = model_name.split()[0] if model_name.split() else ""
            if first_token and first_token not in name and len(first_token) >= 2:
                warnings.append(
                    f"고시의 모델명 첫 단어 '{first_token}'가 상품명에 없습니다 — 브랜드일 수 있으니 "
                    "브랜드 여부를 확인하세요(브랜드 상품이면 바코드가 필요합니다).",
                )

    # ------------------------------------------------------------------
    # 2) 수익 계산
    # ------------------------------------------------------------------

    @staticmethod
    def propose_margin(request: schema.MarginProposalRequest) -> schema.MarginProposalResponse:
        base = dict(
            marketplace_account_id=0, cost_of_goods=request.cost_of_goods,
            channel_fee_rate=request.channel_fee_rate, payment_fee_rate=request.payment_fee_rate,
            shipping_cost=request.shipping_cost, packaging_cost=request.packaging_cost,
            return_reserve_rate=request.return_reserve_rate, tax_basis_rate=Decimal("0"),
        )
        warnings: list[str] = []
        probe = EconomicsInputItem(sale_price=Decimal("0"), **base)
        derived = derive_sale_price_for_margin_rate(probe, request.target_margin_rate)
        proposed = None
        if derived is None:
            warnings.append("수수료율과 목표 마진율의 합이 100% 이상이라 어떤 판매가로도 달성할 수 없습니다.")
        else:
            # 10원 단위로 올림 — 내림하면 목표 마진 아래로 내려갈 수 있다.
            proposed = Decimal(math.ceil(derived / 10) * 10)

        sale_price = request.sale_price if request.sale_price is not None else proposed
        result = None
        if sale_price is not None and sale_price > 0:
            result = calculate_economics(EconomicsInputItem(sale_price=sale_price, **base))

        meets = None
        if result is not None and result.margin_rate is not None:
            meets = result.margin_rate >= MIN_MARGIN_RATE
            if not meets:
                warnings.append(
                    f"마진율이 최소 기준 {int(MIN_MARGIN_RATE * 100)}%보다 낮습니다 — 이 가격으로는 등록하면 안 됩니다.",
                )
        if request.channel_fee_rate == Decimal("0.108"):
            warnings.append("수수료율 10.8%는 기본 가정입니다 — 쿠팡 카테고리의 실제 수수료율로 고쳐서 다시 계산하세요.")

        return schema.MarginProposalResponse(
            proposed_sale_price=proposed, sale_price=sale_price,
            expected_revenue=getattr(result, "expected_revenue", None),
            total_cost=getattr(result, "total_cost", None),
            margin_amount=getattr(result, "margin_amount", None),
            margin_rate=getattr(result, "margin_rate", None),
            break_even_price=getattr(result, "break_even_price", None),
            meets_min_margin=meets, min_margin_rate=MIN_MARGIN_RATE, warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 정보고시 제안(개선 A9) — 5단계에서 빈 칸에만 채울 "제안"
    # ------------------------------------------------------------------

    def notice_suggestions(
        self, candidate_id: int, labels: list[str], company_id: int,
    ) -> schema.NoticeSuggestionResponse:
        evidence = ProductCandidateService(self.db).list_evidence(candidate_id, company_id)
        newest = None
        for row in evidence:
            rows = decode_notice_rows(row.payload_summary)
            if rows and (newest is None or row.id > newest[0]):
                newest = (row.id, rows)
        if newest is None:
            return schema.NoticeSuggestionResponse(suggestions={}, source_found=False)
        return schema.NoticeSuggestionResponse(
            suggestions=suggest_notice_values(newest[1], labels), source_found=True,
        )

    # ------------------------------------------------------------------
    # 3) 후보 + 위저드 초안 생성
    # ------------------------------------------------------------------

    def _default_coupang_account_id(self, company_id: int) -> int | None:
        from app.domains.marketplace_listing.model import MarketplaceAccount
        from app.domains.marketplace_listing.model import MarketplaceChannel

        row = (
            self.db.query(MarketplaceAccount.id)
            .join(MarketplaceChannel, MarketplaceChannel.id == MarketplaceAccount.channel_id)
            .filter(MarketplaceAccount.company_id == company_id, MarketplaceChannel.code == "COUPANG")
            .order_by(MarketplaceAccount.id)
            .first()
        )
        return row[0] if row else None

    def create_draft(
        self, request: schema.DraftCreateRequest, company_id: int, user_id: int,
    ) -> schema.DraftCreateResponse:
        warnings: list[str] = []
        if request.margin is not None:
            proposal = self.propose_margin(request.margin)
            if proposal.meets_min_margin is False:
                raise ProductImportError(
                    "마진율이 최소 기준(18%)보다 낮아 초안을 만들지 않았습니다 — 판매가를 올리거나 원가·배송비를 확인하세요.",
                )

        candidate, candidate_events = ProductCandidateService(self.db).register_private_candidate(
            ProductCandidatePrivateCreate(
                source_reference=request.source_reference, market="COUPANG",
                product_name=request.product_name[:200],
                category_hint=request.category_hint,
            ),
            company_id=company_id,
            correlation_id=f"url-import-{request.creation_idempotency_key}"[:100],
        )
        # 이미 있던 후보(같은 source_reference)는 이벤트 없이 그대로 돌려준다(register_private_candidate의 멱등 계약).
        candidate_created = bool(candidate_events)
        if request.notice_rows:
            self.db.add(ProductCandidateEvidence(
                candidate_id=candidate.id, evidence_type=EvidenceType.RAW_SOURCE,
                payload_summary=encode_notice_rows([(r.label, r.value) for r in request.notice_rows]),
            ))
            self.db.commit()

        if request.analysis_report:
            snapshot = json.dumps(request.analysis_report, ensure_ascii=False, separators=(",", ":"))
            self.db.add(ProductCandidateEvidence(
                candidate_id=candidate.id, evidence_type=EvidenceType.RAW_SOURCE,
                payload_summary=("AI_ANALYSIS_REPORT:(화면 스냅샷) " + snapshot)[:3900],
            ))
            self.db.commit()

        wizard_service = ListingWizardService(self.db)
        wizard, duplicate = wizard_service.create(
            WizardCreateRequest(
                source_type="MANUAL", product_candidate_id=candidate.id,
                creation_idempotency_key=f"urlimport-{request.creation_idempotency_key}"[:160],
            ),
            created_by=user_id, company_id=company_id,
        )

        economics_saved = False
        if duplicate:
            warnings.append("같은 요청으로 이미 만든 위저드가 있어 입력값을 덮어쓰지 않았습니다.")
        else:
            wizard = wizard_service.update_source(
                wizard.id, company_id,
                WizardSourceUpdateRequest(expected_version=wizard.version, product_candidate_id=candidate.id),
            )
            wizard = wizard_service.update_draft(
                wizard.id, company_id,
                WizardDraftUpdateRequest(
                    expected_version=wizard.version, product_name=request.product_name,
                    category=request.category_hint, description=request.description,
                    keywords=[k for k in request.keywords if k.strip()][:20],
                ),
            )
            if request.margin is not None:
                account_id = self._default_coupang_account_id(company_id)
                sale_price = (
                    request.margin.sale_price if request.margin.sale_price is not None
                    else self.propose_margin(request.margin).proposed_sale_price
                )
                if account_id is None or sale_price is None:
                    warnings.append("쿠팡 판매계정을 찾지 못해 6단계 경제성 입력은 채우지 않았습니다 — 위저드에서 직접 입력하세요.")
                else:
                    wizard = wizard_service.update_economics(
                        wizard.id, company_id,
                        WizardEconomicsUpdateRequest(
                            expected_version=wizard.version,
                            items=[EconomicsInputItem(
                                marketplace_account_id=account_id,
                                cost_of_goods=request.margin.cost_of_goods, sale_price=sale_price,
                                channel_fee_rate=request.margin.channel_fee_rate,
                                payment_fee_rate=request.margin.payment_fee_rate,
                                shipping_cost=request.margin.shipping_cost,
                                packaging_cost=request.margin.packaging_cost,
                                return_reserve_rate=request.margin.return_reserve_rate,
                                tax_basis_rate=Decimal("0"),
                            )],
                        ),
                    )
                    economics_saved = True

        images = self._import_images(request, candidate.id, company_id, user_id)
        if any(not item.success for item in images):
            warnings.append("일부 이미지를 가져오지 못했습니다 — 3단계에서 직접 올리거나 다시 시도하세요.")

        next_steps = [
            "상품 후보 화면에서 '기본 정보 확인' 후 후보를 승인하세요(승인 전에는 사전검사가 막습니다).",
            "위저드 3단계에서 이미지 사용권을 확인하세요(사용자 책임 선언).",
            "5단계에서 브랜드·원산지·카테고리·정보고시·구매옵션을 확인하세요(자동으로 확정하지 않았습니다).",
            "대표 이미지는 가로·세로 500px 이상이어야 합니다.",
            "8단계 승인과 쿠팡 전송은 기존대로 직접 진행합니다.",
        ]
        return schema.DraftCreateResponse(
            candidate_id=candidate.id, candidate_created=candidate_created,
            wizard_id=wizard.id, wizard_created=not duplicate, economics_saved=economics_saved,
            images=images, next_steps=next_steps, warnings=warnings,
        )

    def _import_images(
        self, request: schema.DraftCreateRequest, candidate_id: int, company_id: int, user_id: int,
    ) -> list[schema.ImageImportResult]:
        results: list[schema.ImageImportResult] = []
        classification = (
            MediaAssetSourceClassification.SUPPLIER if request.source_kind == "ONCHANNEL"
            else MediaAssetSourceClassification.UNKNOWN
        )
        main_urls = list(dict.fromkeys(request.image_urls_main))[:MAX_IMAGE_SUGGESTIONS]
        if main_urls:
            import_service = UrlImageImportService(self.db, self.transport)
            for item in import_service.import_images_from_urls_batch(
                owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=candidate_id,
                purpose=MediaAssetPurpose.MAIN, source_urls=main_urls,
                source_classification=classification, company_id=company_id,
            ):
                results.append(schema.ImageImportResult(
                    source_url=item.source_url, purpose="MAIN", success=item.success,
                    asset_id=getattr(item.asset, "id", None), error_message=item.error_message,
                ))

        detail_urls = list(dict.fromkeys(request.image_urls_detail))[:MAX_IMAGE_SUGGESTIONS]
        if detail_urls:
            from app.domains.media_asset.job_queue_service import ImageGenerationJobQueueService

            queue_service = ImageGenerationJobQueueService(self.db)
            for index, url in enumerate(detail_urls, start=1):
                try:
                    fetched = fetch_image_safely(url, self.transport)
                    original, _segments = queue_service.upload_and_split_long_detail_image(
                        owner_type=MediaAssetOwnerType.PRODUCT_CANDIDATE, owner_id=candidate_id,
                        image_bytes=fetched.image_bytes, original_filename=f"detail-{index}",
                        segment_height=_LONG_SEGMENT_HEIGHT, requested_by=user_id, company_id=company_id,
                        mode="SECTION",  # 섹션 경계에서 자른다(글자·표가 중간에서 잘리지 않게)
                    )
                    results.append(schema.ImageImportResult(
                        source_url=url, purpose="DETAIL", success=True, asset_id=original.id,
                    ))
                except Exception as exc:  # noqa: BLE001 — 한 장이 실패해도 나머지는 계속한다.
                    message = str(exc) if isinstance(exc, UrlImportError) else (
                        f"상세 이미지를 처리하지 못했습니다(오류 유형: {type(exc).__name__})."
                    )
                    results.append(schema.ImageImportResult(
                        source_url=url, purpose="DETAIL", success=False, error_message=message,
                    ))
        return results


__all__ = ["ProductImportError", "ProductImportService"]
