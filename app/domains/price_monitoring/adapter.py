"""
=========================================================
Homez OS

File : app/domains/price_monitoring/adapter.py

2026-09-06 "가격·재고 자동 모니터링" — Adapter 계약.

읽기 전용 공개 페이지 확인만 한다(로그인·계정 자격증명 사용 안 함).
대상 사이트가 무엇이든, 이 Adapter는 절대 봇 탐지를 우회하려 하지
않는다 — 403 등 접근 거부는 정직하게 FAILED로 기록하고 재시도하지
않는다(같은 요청을 다른 방식으로 다시 시도하는 로직 자체가 이미
회피 시도이므로 만들지 않는다). 쿠팡처럼 서버가 자동화 접근을
실시간으로 차단하는 사이트는 애초에 이 Adapter의 대상이 될 수 없다
(2026-09-06 실측 확인 — HTTP 403).

가격·재고 판정 방식(price_extract_regex/stock_keyword)은 대상
페이지의 HTML 구조에 의존하는 정규식/키워드를 운영자가 직접
지정한다 — 특정 사이트 전용 파서를 코드에 하드코딩하지 않는다(사이트
구조가 바뀌면 코드 배포 없이 Watch 설정만 고치면 되게 하기 위함).
=========================================================
"""

from __future__ import annotations

import re
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from typing import Protocol


class _WatchLike(Protocol):
    target_url: str
    price_extract_regex: str | None
    stock_keyword: str | None


@dataclass(frozen=True)
class PriceStockCheckResult:

    status: str  # SUCCEEDED / FAILED
    price: Decimal | None = None
    in_stock: bool | None = None
    error_code: str | None = None
    error_summary: str | None = None


class PriceStockSourceAdapter(ABC):

    @abstractmethod
    def check(self, watch: _WatchLike) -> PriceStockCheckResult:
        ...


def _extract_price_and_stock(
    text: str, watch: _WatchLike,
) -> PriceStockCheckResult:
    """HttpPriceStockAdapter/HeadlessBrowserPriceStockAdapter가 공유하는
    순수 추출 로직 — 렌더링된 텍스트를 어떻게 얻었는지와 무관하게
    동일하게 동작한다."""

    price: Decimal | None = None
    if watch.price_extract_regex:
        match = re.search(watch.price_extract_regex, text)
        if match:
            digits_only = re.sub(r"[^\d.]", "", match.group(1))
            try:
                price = Decimal(digits_only) if digits_only else None
            except InvalidOperation:
                price = None
        if price is None:
            return PriceStockCheckResult(
                status="FAILED", error_code="PRICE_PATTERN_NOT_FOUND",
            )

    in_stock: bool | None = None
    if watch.stock_keyword:
        in_stock = watch.stock_keyword not in text

    return PriceStockCheckResult(
        status="SUCCEEDED", price=price, in_stock=in_stock,
    )


class FakePriceStockAdapter(PriceStockSourceAdapter):
    """네트워크 호출 없이 결정론적으로 동작한다 — 테스트/시연 전용."""

    def __init__(self, fixed_price: Decimal = Decimal("10000"), in_stock: bool = True):
        self.fixed_price = fixed_price
        self.in_stock = in_stock

    def check(self, watch: _WatchLike) -> PriceStockCheckResult:

        return PriceStockCheckResult(
            status="SUCCEEDED", price=self.fixed_price, in_stock=self.in_stock,
        )


class HttpPriceStockAdapter(PriceStockSourceAdapter):
    """
    실제 HTTP GET으로 공개 페이지를 읽는다. 로그인하지 않고, 세션·
    쿠키를 재사용하지 않으며, 실패 시 다른 방식으로 재시도하지 않는다
    (봇 탐지 우회 금지 원칙).
    """

    REQUEST_TIMEOUT_SECONDS = 10
    MAX_RESPONSE_BYTES = 5_000_000
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HomezPriceMonitor/1.0"
    )

    def __init__(self, http_get=None):
        if http_get is not None:
            self._http_get = http_get
        else:
            import requests

            self._http_get = requests.get

    def check(self, watch: _WatchLike) -> PriceStockCheckResult:

        try:
            response = self._http_get(
                watch.target_url,
                headers={"User-Agent": self.USER_AGENT},
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — 네트워크 실패는 FAILED로 정직하게 기록
            return PriceStockCheckResult(
                status="FAILED", error_code="NETWORK_ERROR",
                error_summary=type(exc).__name__,
            )

        if response.status_code != 200:
            return PriceStockCheckResult(
                status="FAILED", error_code=f"HTTP_{response.status_code}",
                error_summary=(
                    "사이트가 접근을 거부했습니다 — 우회를 시도하지 "
                    "않고 그대로 실패로 기록합니다."
                    if response.status_code in (403, 429)
                    else None
                ),
            )

        text = response.text[: self.MAX_RESPONSE_BYTES]

        return _extract_price_and_stock(text, watch)


class HeadlessBrowserPriceStockAdapter(PriceStockSourceAdapter):
    """
    2026-09-06 추가 — 자바스크립트로 상품을 그리는 사이트(에누리·
    11번가·무신사 등, 서버가 차단한 게 아니라 정적 HTML에 상품이 없을
    뿐이라고 실측 확인된 사이트) 전용. Playwright의 표준 headless
    Chromium 실행일 뿐, 지문 위장·프록시 로테이션·CAPTCHA 우회 등
    어떤 봇 탐지 회피 기법도 쓰지 않는다 — HttpPriceStockAdapter와
    동일한 "차단되면 그대로 실패" 원칙을 그대로 따른다.

    브라우저 기동 비용이 크므로(실측 약 15~20초/페이지) Context
    Manager로 써서 여러 Watch를 한 브라우저 인스턴스로 순회할 수
    있다(스케줄러 Job이 이렇게 쓴다). 단독으로 check() 한 번만
    호출해도 내부적으로 기동→확인→종료를 자동으로 처리한다.
    """

    NAVIGATION_TIMEOUT_MS = 15000
    RENDER_WAIT_MS = 2000

    def __init__(self):
        self._playwright = None
        self._browser = None

    def __enter__(self) -> "HeadlessBrowserPriceStockAdapter":

        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:

        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def check(self, watch: _WatchLike) -> PriceStockCheckResult:

        if self._browser is None:
            with self:
                return self._check_with_browser(watch)
        return self._check_with_browser(watch)

    def _check_with_browser(self, watch: _WatchLike) -> PriceStockCheckResult:

        try:
            page = self._browser.new_page()
        except Exception as exc:  # noqa: BLE001
            return PriceStockCheckResult(
                status="FAILED", error_code="BROWSER_ERROR",
                error_summary=type(exc).__name__,
            )

        try:
            try:
                page.goto(
                    watch.target_url, wait_until="domcontentloaded",
                    timeout=self.NAVIGATION_TIMEOUT_MS,
                )
            except Exception as exc:  # noqa: BLE001 — 타임아웃/차단은 재시도 없이 FAILED
                return PriceStockCheckResult(
                    status="FAILED", error_code="NAVIGATION_ERROR",
                    error_summary=type(exc).__name__,
                )

            page.wait_for_timeout(self.RENDER_WAIT_MS)

            try:
                text = page.inner_text("body")
            except Exception as exc:  # noqa: BLE001
                return PriceStockCheckResult(
                    status="FAILED", error_code="EXTRACTION_ERROR",
                    error_summary=type(exc).__name__,
                )
        finally:
            page.close()

        return _extract_price_and_stock(text, watch)


__all__ = [
    "PriceStockCheckResult",
    "PriceStockSourceAdapter",
    "FakePriceStockAdapter",
    "HttpPriceStockAdapter",
    "HeadlessBrowserPriceStockAdapter",
]
