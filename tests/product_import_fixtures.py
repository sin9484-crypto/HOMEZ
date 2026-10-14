"""URL 상품 가져오기 테스트 공용 픽스처 — 실제 네트워크·DNS를 쓰지 않는다.

ONCHANNEL_HTML은 2026-10-14에 실제 온채널 상품 페이지(num=12535780)에서 확인한 구조(info 목록 `div.prod_detail_title`,
고시 표 `tr > td + td`, 상세 이미지 `div.gosi_wrap figure.image img`, 대표 이미지 `img.prod_img_list`)를 줄여 만든 것이다.
"""

from __future__ import annotations

from app.domains.media_asset.url_import import FetchedResponse

ONCHANNEL_URL = "https://www.onch3.co.kr/dbcenter_renewal/detail.php?num=12535780"

ONCHANNEL_HTML = """<!doctype html><html><head><title>슬개건 러닝 무릎보호대 무릎연골보호 슬개골 - 온채널</title></head><body>
<div class="header_menu_box"><ul class="head_rank_list"><li><img src="https://onimg.onch3.co.kr/products/2026/08/27/rank_thumb.png"></li></ul></div>
<div class="prod_detail_imgbox">
  <img src="https://onimg.onch3.co.kr/products/2026/08/27/main1.png" width="46" class="prod_img_list">
  <img src="https://onimg.onch3.co.kr/products/2026/08/27/main2.png" width="46" class="prod_img_list">
  <img src="https://www.onch3.co.kr/images/no_img.png" class="prod_img_list">
</div>
<ul>
 <li><div class="prod_detail_title">제품명</div><div>슬개건 러닝 무릎보호대 무릎연골보호 슬개골</div></li>
 <li><div class="prod_detail_title">상품코드</div><div>CH5306792</div></li>
 <li><div class="prod_detail_title">공급업체분류</div><div> 수입사 </div></li>
 <li><div class="prod_detail_title">판매사가</div><div>로그인시 볼 수 있습니다.</div></li>
</ul>
<div class="dbcenter_detail"><div class="prod_detail_btn"><div class="gosi_wrap">
 <table>
  <tr><td>품명 및 모델명</td><td>오조 슬개건 러닝 무릎보호대</td></tr>
  <tr><td>제품 구성</td><td>2개 1set</td></tr>
  <tr><td>색상</td><td>블랙</td></tr>
  <tr><td>재질</td><td>나일론, EVA</td></tr>
  <tr><td>크기/중량</td><td>약 15x15cm</td></tr>
  <tr><td>제조자/수입자</td><td>JJ협력사</td></tr>
  <tr><td>제조국</td><td>중국</td></tr>
  <tr><td>KC 인증유형</td><td>해당사항없음</td></tr>
 </table>
 <div class="content_section"><figure class="image"><img src="https://onimg.onch3.co.kr/products/2026/09/04/detail1.png"></figure>
 <figure class="image"><img src="https://onimg.onch3.co.kr/products/2026/09/04/detail2.png"></figure></div>
</div></div></div>
<img class="product_img_thumbnail lazy-img" src="https://onimg.onch3.co.kr/products/2024/10/25/other_product.png">
</body></html>"""

GENERIC_URL = "https://shop.example.test/item/123"

GENERIC_HTML = """<html><head><title>사이트 이름 | 기본 제목</title>
<meta property="og:title" content="튼튼한 무릎 밴드 2P">
<meta property="og:image" content="/img/main.jpg">
<meta name="description" content="가볍고 편안한 밴드">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"튼튼한 무릎 밴드 2P",
"description":"설명 문구","brand":{"@type":"Brand","name":"튼튼"},"offers":{"@type":"Offer","price":"12900","priceCurrency":"KRW"}}</script>
</head><body><img src="/img/a.jpg"><img src="data:image/png;base64,AAAA"><img src="https://cdn.example.test/b.png">
<table><tr><th>소재</th><td>나일론</td></tr></table></body></html>"""


class FakeTransport:
    """URL → FetchedResponse. 등록되지 않은 URL은 404."""

    def __init__(self, pages: dict[str, FetchedResponse] | None = None):
        self.pages = pages or {}
        self.calls: list[str] = []

    def add_html(self, url: str, html: str, content_type: str = "text/html; charset=utf-8") -> None:
        self.pages[url] = FetchedResponse(200, {"Content-Type": content_type}, html.encode("utf-8"))

    def get(self, url, *, timeout, max_bytes):
        self.calls.append(url)
        return self.pages.get(url) or FetchedResponse(404, {}, b"")


class FakeOption:
    def __init__(self, label, price):
        self.option_id = "1"
        self.label = label
        self.price = price


class FakeShipping:
    base_shipping_cost = 3000
    jeju_shipping_cost = 5000
    remote_area_shipping_cost = 5000


class FakeLookup:
    def __init__(self, options=None):
        from decimal import Decimal

        self.options = options if options is not None else (FakeOption("기본", Decimal("5720")),)
        self.shipping_info = FakeShipping()
        self.status = "1"
        self.tax_exempt = False


class FakeConnection:
    def __init__(self, id=7, is_active=True):
        self.id = id
        self.is_active = is_active


class FakeConnectionService:
    """PurchaseChannelConnectionService의 읽기 계약만 흉내낸다 — 호출 기록을 남긴다."""

    def __init__(self, connections=None, lookup=None, error: Exception | None = None):
        self.connections = [FakeConnection()] if connections is None else connections
        self.lookup = lookup or FakeLookup()
        self.error = error
        self.lookup_calls: list[tuple] = []

    def list_connections(self, company_id, *, mall_code=None, include_inactive=False):
        return self.connections

    def lookup_product(self, connection_id, company_id, external_product_id, *, triggered_by=None):
        self.lookup_calls.append((connection_id, company_id, external_product_id))
        if self.error is not None:
            raise self.error
        return self.lookup
