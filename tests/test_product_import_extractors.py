"""
=========================================================
Homez OS

File : tests/test_product_import_extractors.py

URL 상품 가져오기(2026-10-14)의 HTML 해석(순수 함수)을 고정한다. 네트워크·DB 없음.
=========================================================
"""

import unittest

from app.domains.product_import.generic_extractor import claim_words_in
from app.domains.product_import.generic_extractor import extract_generic
from app.domains.product_import.html_tree import label_value_pairs
from app.domains.product_import.html_tree import parse_html
from app.domains.product_import.onchannel_page import is_onchannel_product_url
from app.domains.product_import.onchannel_page import parse_onchannel_page
from app.domains.product_import.onchannel_page import suggest_description
from tests.product_import_fixtures import GENERIC_HTML
from tests.product_import_fixtures import GENERIC_URL
from tests.product_import_fixtures import ONCHANNEL_HTML
from tests.product_import_fixtures import ONCHANNEL_URL


class HtmlTreeTests(unittest.TestCase):

    def test_broken_html_never_raises(self):
        for html in ("", "<div><span>열림", "</p></div>text<table><tr><td>a", "<<<>>>", None):
            with self.subTest(html=html):
                parse_html(html).text()

    def test_script_and_style_text_is_not_part_of_page_text(self):
        root = parse_html("<p>보이는 글</p><script>var x='비밀'</script><style>.a{}</style>")
        self.assertEqual(root.text(), "보이는 글")

    def test_label_value_pairs_from_table_dl_and_onch_list(self):
        root = parse_html(
            "<table><tr><td>색상</td><td>블랙</td></tr></table>"
            "<dl><dt>재질</dt><dd>나일론</dd></dl>"
            "<ul><li><div class='prod_detail_title'>상품코드</div><div>CH1234567</div></li></ul>",
        )
        self.assertEqual(
            label_value_pairs(root),
            [("색상", "블랙"), ("재질", "나일론"), ("상품코드", "CH1234567")],
        )

    def test_overlong_label_or_empty_value_is_dropped(self):
        root = parse_html("<tr><td>" + "가" * 60 + "</td><td>값</td></tr><tr><td>라벨</td><td> </td></tr>")
        self.assertEqual(label_value_pairs(root), [])


class OnchannelPageTests(unittest.TestCase):

    def test_url_detection(self):
        self.assertTrue(is_onchannel_product_url(ONCHANNEL_URL))
        self.assertTrue(is_onchannel_product_url("https://onch3.co.kr/dbcenter_renewal/detail.php?num=1"))
        for url in (
            "https://www.onch3.co.kr/dbcenter_renewal/detail.php",
            "https://www.onch3.co.kr/dbcenter_renewal/detail.php?num=abc",
            "https://evil-onch3.co.kr.example.com/dbcenter_renewal/detail.php?num=1",
            "https://www.example.com/dbcenter_renewal/detail.php?num=1",
            "not a url",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_onchannel_product_url(url))

    def test_public_fields_are_read(self):
        page = parse_onchannel_page(ONCHANNEL_HTML, ONCHANNEL_URL)
        self.assertEqual(page.product_code, "CH5306792")
        self.assertEqual(page.title, "슬개건 러닝 무릎보호대 무릎연골보호 슬개골")
        self.assertEqual(page.supplier_class, "수입사")
        self.assertEqual(page.value_of("제조국"), "중국")
        self.assertEqual(page.value_of("제조자/수입자"), "JJ협력사")

    def test_images_come_only_from_the_product_areas(self):
        page = parse_onchannel_page(ONCHANNEL_HTML, ONCHANNEL_URL)
        self.assertEqual(
            page.main_image_urls,
            ["https://onimg.onch3.co.kr/products/2026/08/27/main1.png",
             "https://onimg.onch3.co.kr/products/2026/08/27/main2.png"],
        )
        self.assertEqual(
            page.detail_image_urls,
            ["https://onimg.onch3.co.kr/products/2026/09/04/detail1.png",
             "https://onimg.onch3.co.kr/products/2026/09/04/detail2.png"],
        )
        everything = page.main_image_urls + page.detail_image_urls
        self.assertFalse(any("rank_thumb" in u or "other_product" in u or "no_img" in u for u in everything))

    def test_description_is_built_only_from_notice_facts(self):
        page = parse_onchannel_page(ONCHANNEL_HTML, ONCHANNEL_URL)
        self.assertEqual(
            suggest_description(page),
            "구성: 2개 1set, 색상: 블랙, 소재: 나일론, EVA, 크기: 약 15x15cm, 제조국: 중국",
        )

    def test_title_falls_back_to_the_page_title_without_the_site_suffix(self):
        page = parse_onchannel_page("<title>다른 상품 - 온채널</title>", ONCHANNEL_URL)
        self.assertEqual(page.title, "다른 상품")
        self.assertIsNone(page.product_code)

    def test_page_without_known_labels_gives_empty_suggestions(self):
        page = parse_onchannel_page("<html></html>", ONCHANNEL_URL)
        self.assertIsNone(suggest_description(page))
        self.assertEqual(page.main_image_urls + page.detail_image_urls, [])


class GenericExtractorTests(unittest.TestCase):

    def test_json_ld_wins_and_values_are_read(self):
        page = extract_generic(GENERIC_HTML, GENERIC_URL)
        self.assertEqual(page.title, "튼튼한 무릎 밴드 2P")
        self.assertEqual(page.description, "설명 문구")
        self.assertEqual(page.price_text, "12900 KRW")
        self.assertEqual(page.brand_hint, "튼튼")
        self.assertIn(("소재", "나일론"), page.pairs)

    def test_images_are_absolute_deduplicated_and_data_urls_dropped(self):
        page = extract_generic(GENERIC_HTML, GENERIC_URL)
        urls = [i.url for i in page.images]
        self.assertEqual(
            urls,
            ["https://shop.example.test/img/main.jpg", "https://shop.example.test/img/a.jpg",
             "https://cdn.example.test/b.png"],
        )
        self.assertTrue(page.images[0].declared_representative)

    def test_decoration_images_and_size_variants_are_not_listed_as_product_photos(self):
        # 실제 CJ The Market(2026-10-14): 푸터 SVG 4개와 같은 사진의 webp 변형이 후보에 섞였다.
        html = (
            "<meta property='og:image' content='https://img.example.test/p/987/main.jpg'>"
            "<img src='https://img.example.test/p/987/main.jpg?SF=webp&RS=550'>"
            "<img src='https://img.example.test/p/107/other.jpg?SF=webp&RS=1080'>"
            "<img src='/the/_nuxt/img_footer_ccm_pc1.abc.svg'><img src='/assets/logo.png'><img src='/a/icon-cart.png'>"
        )
        urls = [i.url for i in extract_generic(html, GENERIC_URL).images]
        self.assertEqual(urls, ["https://img.example.test/p/987/main.jpg", "https://img.example.test/p/107/other.jpg?SF=webp&RS=1080"])

    def test_falls_back_to_meta_and_title_when_there_is_no_json_ld(self):
        page = extract_generic(
            "<title>제목만</title><meta property='og:description' content='설명'>"
            "<p>가격 12,900원 입니다</p>", GENERIC_URL,
        )
        self.assertEqual(page.title, "제목만")
        self.assertEqual(page.description, "설명")
        self.assertEqual(page.price_text, "12,900원")
        self.assertIsNone(page.brand_hint)

    def test_unreadable_values_stay_none_instead_of_being_guessed(self):
        page = extract_generic("<html><body>내용 없음</body></html>", GENERIC_URL)
        self.assertIsNone(page.title)
        self.assertIsNone(page.price_text)
        self.assertEqual(page.images, [])

    def test_malformed_json_ld_is_ignored(self):
        page = extract_generic(
            "<script type='application/ld+json'>{broken</script><title>제목</title>", GENERIC_URL,
        )
        self.assertEqual(page.title, "제목")

    def test_claim_words_are_detected_for_the_name_warning(self):
        self.assertEqual(claim_words_in("무릎연골보호 밴드"), ["연골"])
        self.assertEqual(claim_words_in("러닝 무릎보호대 슬개건밴드"), [])
        self.assertEqual(claim_words_in(None), [])


if __name__ == "__main__":
    unittest.main()
