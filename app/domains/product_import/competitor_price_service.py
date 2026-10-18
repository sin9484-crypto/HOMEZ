"""
=========================================================
Homez OS

File : app/domains/product_import/competitor_price_service.py

2026-10-18 — 마케팅 AI M2: **경쟁 가격 비교**. 두 단계로 나눈다(Phase E 분석·계산과 같은 구조):
  1) `read` — 사용자가 넣은 경쟁 상품 주소(1~5개)를 한 건씩 읽고(페이지 1회씩) 주소마다 AI 해석 1회로 표시 가격·배송비·구성을 뽑는다. 사용자가 누를 때만 나간다.
     값마다 페이지 원문 인용 검증을 거치고, AI가 읽은 금액은 아직 "확인 전"이다. 결과는 저장하지 않는다.
  2) `compare` — AI 없이 **결정적 통계**만 한다(`competitor_price_stats`). 사용자가 확인했거나 직접 입력한 항목만 센다. 몇 번이든 무료.

범위 제한(쿠팡 약관·공식 API 조사 결과: docs/research/20261018_M0_MARKETING_DATA_SOURCES.md): 쿠팡 공식 API에는 경쟁 가격이 없다. 그래서 사용자가 주소를 한 건씩 넣는 방식만 쓴다.
검색 결과 페이지 수집·페이지 넘김·대량 수집은 하지 않는다. 한 번에 최대 5개, 읽기 전용이다. 온채널 같은 공급처 상품 주소는 경쟁 가격으로 쓰지 않는다(공식 API를 부르지 않는다).
호출은 `call_llm` 한 경로(월 한도 → 승인 센터 `AI_ANALYSIS` 허용 → 호출 → 사용 기록)로만 나간다. 첫 주소에서 한도·허용 문제가 나면 그대로 알리고, 중간에 나면 이미 쓴 결과는 살리고 나머지는 건너뛴다.
=========================================================
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import ServiceUnavailableException
from app.domains.external_call.constants import CallPurpose
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from app.domains.media_asset.url_import import UrlImportError
from app.domains.product_import.competitor_price_provider import SYSTEM_PROMPT
from app.domains.product_import.competitor_price_provider import validate_competitor
from app.domains.product_import.competitor_price_stats import CompetitorEntry
from app.domains.product_import.competitor_price_stats import compare
from app.domains.product_import.competitor_price_stats import url_key
from app.domains.product_import.generic_extractor import extract_generic
from app.domains.product_import.html_tree import parse_html
from app.domains.product_import.onchannel_page import is_onchannel_product_url
from app.domains.product_import.service import ProductImportError
from app.domains.product_import.service import ProductImportService
from app.domains.product_import.url_analysis_provider import PageFacts
from app.domains.product_import.url_analysis_provider import build_user_prompt

MAX_URLS = 5
MAX_OUTPUT_TOKENS = 700
STOP_ERRORS = (BadRequestException, ForbiddenException, ServiceUnavailableException)


def dedupe_urls(urls: list[str]) -> list[str]:
    """입력 순서를 지키며 같은 주소를 한 번만 남긴다. 1~5개가 아니면 거절한다."""

    kept: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = (raw or "").strip()
        key = url_key(url)
        if not url or key in seen:
            continue
        seen.add(key)
        kept.append(url)
    if not kept:
        raise BadRequestException("경쟁 상품 주소를 1개 이상 입력해 주세요.")
    if len(kept) > MAX_URLS:
        raise BadRequestException(f"한 번에 최대 {MAX_URLS}개까지만 읽을 수 있습니다.")
    return kept


class CompetitorPriceService:

    def __init__(self, db: Session, import_service: ProductImportService, ai: AiDraftService, *, now_factory=None):
        self.db = db
        self.importer = import_service
        self.ai = ai
        self._now = now_factory or datetime.utcnow

    # ---------------- 1) 읽기 + AI 해석 ----------------

    def read(self, urls: list[str], company_id: int, user_id: int) -> dict[str, Any]:
        targets = dedupe_urls(urls)
        results: list[dict[str, Any]] = []
        total_cost = 0.0
        stopped: str | None = None
        for index, url in enumerate(targets):
            if stopped:
                results.append({"url": url, "ok": False, "skipped": True, "error": stopped})
                continue
            try:
                row = self._read_one(url, company_id, user_id)
            except (ProductImportError, UrlImportError) as exc:
                results.append({"url": url, "ok": False, "skipped": False, "error": str(exc)})
                continue
            except STOP_ERRORS as exc:
                if index == 0 or not any(r.get("ok") for r in results):
                    raise  # 아직 쓴 비용이 없다 — 한도·허용 문제를 그대로 알린다
                stopped = str(getattr(exc, "detail", exc))
                results.append({"url": url, "ok": False, "skipped": True, "error": stopped})
                continue
            total_cost += float(row["usage"]["cost_krw"])
            results.append(row)
        return {"results": results, "stopped_reason": stopped, "total_cost_krw": round(total_cost, 2), "max_urls": MAX_URLS}

    def _read_one(self, url: str, company_id: int, user_id: int) -> dict[str, Any]:
        html, final_url = self.importer._fetch_html(url)  # 같은 패키지의 읽기(SSRF 방어·리디렉션 재검증·크기 제한)를 그대로 쓴다
        if is_onchannel_product_url(final_url):
            raise ProductImportError("공급처 상품 주소는 경쟁 가격으로 쓰지 않아요 — 다른 판매처의 상품 주소를 넣어 주세요.")
        extracted = extract_generic(html, final_url)
        page_text = re.sub(r"\s+", " ", parse_html(html).text() or "").strip()
        page = PageFacts(title=extracted.title, description=extracted.description, price_text=extracted.price_text, page_text=page_text)
        result, cost, meta = self.ai.call_llm(
            company_id, user_id, target="COMP", system=SYSTEM_PROMPT, user_prompt=build_user_prompt(page),
            ext_purpose=CallPurpose.AI_ANALYSIS, max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        analysis = validate_competitor(result.text, page)
        usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "cost_krw": cost, "ok": "price" in analysis.facts, "purpose": "COMPETITOR"}
        month = self.ai.record_usage(company_id, user_id, "COMP", result.model, usage)
        prefill = self._prefill(analysis.facts)
        return {
            "url": url, "final_url": final_url, "ok": True, "skipped": False, "title": extracted.title, "checked_at": self._now().isoformat(timespec="seconds"),
            "seller": analysis.facts.get("seller"), "option_note": analysis.facts.get("option_note"), "prefill": prefill,
            "rejected": analysis.rejected, "warnings": analysis.warnings,
            "usage": {**usage, "model": result.model, **month, "monthly_limit_krw": meta["limit"]},
        }

    @staticmethod
    def _prefill(facts: dict[str, Any]) -> dict[str, Any]:
        prefill: dict[str, Any] = {}
        for key in ("price", "list_price", "shipping_fee", "pack_count"):
            if key in facts:
                prefill[key] = {"value": str(facts[key]["value"]), "source": "AI_PAGE", "evidence": facts[key]["evidence"]}
        if "unit_volume" in facts:
            uv = facts["unit_volume"]
            prefill["unit_volume"] = {"value": str(uv["value"]), "unit": uv["unit"], "source": "AI_PAGE", "evidence": uv["evidence"]}
        return prefill

    # ---------------- 2) 결정적 통계 ----------------

    @staticmethod
    def compare(entries: list[CompetitorEntry]) -> dict[str, Any]:
        return compare(entries)


def to_entry(data: Any) -> CompetitorEntry:
    """요청 모델 한 줄을 통계 입력으로."""

    return CompetitorEntry(
        label=data.label, price=data.price, url=data.url, shipping_fee=data.shipping_fee, pack_count=data.pack_count,
        unit_volume=Decimal(data.unit_volume) if data.unit_volume is not None else None, unit=data.unit,
        source=data.source, confirmed=data.confirmed, checked_at=data.checked_at,
    )
