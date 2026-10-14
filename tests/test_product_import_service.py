"""
=========================================================
Homez OS

File : tests/test_product_import_service.py

URL 상품 가져오기(2026-10-14) 서비스 검증 — 검색 → 수익 계산 → 후보·위저드 초안 생성.
실제 네트워크·DNS·쿠팡·온채널 호출 없음(FakeTransport, FakeConnectionService, 임시 SQLite).
=========================================================
"""

import io
import os
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.media_asset.model import MediaAsset
from app.domains.media_asset.url_import import FetchedResponse
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_import import schema
from app.domains.product_import.service import ProductImportError
from app.domains.product_import.service import ProductImportService
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)
from tests.product_import_fixtures import GENERIC_HTML
from tests.product_import_fixtures import GENERIC_URL
from tests.product_import_fixtures import ONCHANNEL_HTML
from tests.product_import_fixtures import ONCHANNEL_URL
from tests.product_import_fixtures import FakeConnectionService
from tests.product_import_fixtures import FakeLookup
from tests.product_import_fixtures import FakeOption
from tests.product_import_fixtures import FakeTransport

_NO_DNS = mock.patch(
    "app.domains.media_asset.url_import._resolve_and_validate_host", lambda hostname: None,
)


def _png(width=600, height=600) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, "PNG")
    return buffer.getvalue()


class SearchTests(unittest.TestCase):

    def setUp(self):
        _NO_DNS.start()
        self.addCleanup(_NO_DNS.stop)
        self.transport = FakeTransport()
        self.transport.add_html(ONCHANNEL_URL, ONCHANNEL_HTML)
        self.transport.add_html(GENERIC_URL, GENERIC_HTML)

    def _service(self, connection_service=None):
        return ProductImportService(db=None, transport=self.transport, connection_service=connection_service)

    def test_onchannel_page_plus_official_api_price(self):
        connections = FakeConnectionService()
        result = self._service(connections).search(ONCHANNEL_URL, company_id=1, user_id=9)

        self.assertEqual(result.source_kind, "ONCHANNEL")
        self.assertEqual(result.source_reference, "ONCHANNEL:CH5306792")
        self.assertEqual(result.origin_text, "중국")
        self.assertEqual(result.supplier_name, "수입사")
        self.assertEqual(result.offer.supply_price, Decimal("5720"))
        self.assertEqual(result.offer.base_shipping_cost, 3000)
        self.assertEqual(connections.lookup_calls, [(7, 1, "CH5306792")])
        purposes = [(i.suggested_purpose, i.selected_by_default) for i in result.images]
        self.assertEqual(purposes.count(("MAIN", True)), 2)
        self.assertEqual(purposes.count(("DETAIL", True)), 2)
        self.assertEqual(self.transport.calls, [ONCHANNEL_URL])  # 페이지 읽기는 정확히 1회

    def test_suspicious_words_in_the_page_are_warned_not_removed(self):
        result = self._service(FakeConnectionService()).search(ONCHANNEL_URL, 1, 9)
        self.assertEqual(result.suggested_product_name, "슬개건 러닝 무릎보호대 무릎연골보호 슬개골")  # 지우지 않는다
        joined = " ".join(result.warnings)
        self.assertIn("연골", joined)          # 효능 표현 경고
        self.assertIn("오조", joined)          # 모델명 첫 단어(브랜드 가능성) 경고

    def test_missing_connection_leaves_price_empty_with_a_reason(self):
        for service_obj, expected in (
            (None, "연결이 준비되지"),
            (FakeConnectionService(connections=[]), "사용 가능한 온채널 API 연결이 없어"),
        ):
            with self.subTest(expected=expected):
                result = self._service(service_obj).search(ONCHANNEL_URL, 1, 9)
                self.assertIsNone(result.offer)
                self.assertTrue(any(expected in w for w in result.warnings), result.warnings)

    def test_api_failure_never_leaks_the_raw_error_and_keeps_page_info(self):
        secret = "KEY-abc123-should-not-appear"
        result = self._service(FakeConnectionService(error=RuntimeError(secret))).search(ONCHANNEL_URL, 1, 9)
        self.assertIsNone(result.offer)
        text_ = " ".join(result.warnings)
        self.assertIn("RuntimeError", text_)
        self.assertNotIn(secret, text_)
        self.assertEqual(result.source_reference, "ONCHANNEL:CH5306792")

    def test_not_found_from_the_api_explains_the_likely_cause_without_claiming_it(self):
        # 실제 확인(2026-10-14): 페이지에는 있는 상품인데 API가 NotFound — 판매신청 전 상품일 수 있다.
        class OnchannelNotFoundError(Exception):
            pass

        result = self._service(FakeConnectionService(error=OnchannelNotFoundError("404"))).search(ONCHANNEL_URL, 1, 9)
        self.assertIsNone(result.offer)
        joined = " ".join(result.warnings)
        self.assertIn("찾지 못했습니다", joined)
        self.assertIn("판매신청", joined)
        self.assertIn("수 있습니다", joined)   # 단정하지 않는다
        self.assertEqual(result.suggested_product_name, "슬개건 러닝 무릎보호대 무릎연골보호 슬개골")

    def test_multiple_options_use_the_first_and_say_so(self):
        lookup = FakeLookup(options=(FakeOption("블랙", Decimal("5720")), FakeOption("화이트", Decimal("5900"))))
        result = self._service(FakeConnectionService(lookup=lookup)).search(ONCHANNEL_URL, 1, 9)
        self.assertEqual(result.offer.option_label, "블랙")
        self.assertTrue(any("옵션이 2개" in w for w in result.warnings))

    def test_generic_page_is_best_effort_with_a_clear_caution(self):
        result = self._service().search(GENERIC_URL, 1, 9)
        self.assertEqual(result.source_kind, "GENERIC")
        self.assertTrue(result.source_reference.startswith("URL:shop.example.test:"))
        self.assertEqual(result.suggested_product_name, "튼튼한 무릎 밴드 2P")
        self.assertEqual(result.price_text, "12900 KRW")
        self.assertIsNone(result.offer)
        self.assertTrue(any("확인하고 고쳐" in w for w in result.warnings))
        self.assertTrue(any("브랜드" in w and "튼튼" in w for w in result.warnings))
        defaults = [i.url for i in result.images if i.selected_by_default]
        self.assertEqual(defaults, ["https://shop.example.test/img/main.jpg"])

    def test_same_url_gives_the_same_source_reference(self):
        first = self._service().search(GENERIC_URL, 1, 9).source_reference
        second = self._service().search(GENERIC_URL, 1, 9).source_reference
        self.assertEqual(first, second)

    def test_blocked_and_failing_pages_raise_readable_errors(self):
        with self.assertRaises(ProductImportError):
            self._service().search("https://shop.example.test/missing", 1, 9)  # 404
        self.transport.pages["https://shop.example.test/pdf"] = FetchedResponse(
            200, {"Content-Type": "application/pdf"}, b"%PDF",
        )
        with self.assertRaises(ProductImportError) as ctx:
            self._service().search("https://shop.example.test/pdf", 1, 9)
        self.assertIn("HTML 페이지가 아닙니다", str(ctx.exception))

    def test_internal_addresses_and_non_http_schemes_never_reach_the_transport(self):
        _NO_DNS.stop()
        try:
            for url in ("http://127.0.0.1/admin", "http://localhost/x", "http://192.168.0.5/x", "file:///etc/passwd",
                        "http://user:pw@shop.example.test/x"):
                with self.subTest(url=url):
                    with self.assertRaises(ProductImportError):
                        self._service().search(url, 1, 9)
            self.assertEqual(self.transport.calls, [])
        finally:
            _NO_DNS.start()

    def test_redirects_are_followed_with_a_limit(self):
        self.transport.pages["https://shop.example.test/r1"] = FetchedResponse(
            302, {}, b"", redirect_location=GENERIC_URL,
        )
        self.assertEqual(self._service().search("https://shop.example.test/r1", 1, 9).source_kind, "GENERIC")
        for i in range(6):
            self.transport.pages[f"https://shop.example.test/loop{i}"] = FetchedResponse(
                302, {}, b"", redirect_location=f"https://shop.example.test/loop{i + 1}",
            )
        with self.assertRaises(ProductImportError) as ctx:
            self._service().search("https://shop.example.test/loop0", 1, 9)
        self.assertIn("리디렉션", str(ctx.exception))

    def test_euc_kr_pages_are_decoded(self):
        html = "<html><head><title>한글 상품 이름</title></head><body></body></html>"
        self.transport.pages["https://shop.example.test/kr"] = FetchedResponse(
            200, {"Content-Type": "text/html; charset=euc-kr"}, html.encode("euc-kr"),
        )
        self.assertEqual(self._service().search("https://shop.example.test/kr", 1, 9).title, "한글 상품 이름")


class MarginProposalTests(unittest.TestCase):

    def _propose(self, **kwargs):
        request = schema.MarginProposalRequest(**kwargs)
        return ProductImportService.propose_margin(request)

    def test_proposal_meets_the_target_and_is_rounded_up_to_10_won(self):
        result = self._propose(cost_of_goods=Decimal("5720"), shipping_cost=Decimal("3000"))
        self.assertEqual(result.proposed_sale_price % 10, 0)
        self.assertGreaterEqual(result.margin_rate, Decimal("0.18"))
        self.assertTrue(result.meets_min_margin)

    def test_matches_the_real_registration(self):
        # 러닝 무릎보호대: 원가 5,720 + 배송 3,000, 수수료 10.8%, 반품 3% → 12,900원에서 18.60% (서버 계산과 같아야 한다).
        result = self._propose(cost_of_goods=Decimal("5720"), shipping_cost=Decimal("3000"), sale_price=Decimal("12900"))
        self.assertEqual(result.margin_rate, Decimal("0.1860"))
        self.assertEqual(result.margin_amount, Decimal("2399.80"))

    def test_user_price_below_minimum_is_flagged(self):
        result = self._propose(cost_of_goods=Decimal("5720"), shipping_cost=Decimal("3000"), sale_price=Decimal("9900"))
        self.assertFalse(result.meets_min_margin)
        self.assertTrue(any("최소 기준" in w for w in result.warnings))
        self.assertIsNotNone(result.proposed_sale_price)  # 제안값은 그대로 함께 돌려준다

    def test_impossible_rate_sum_gives_no_price_instead_of_a_guess(self):
        result = self._propose(
            cost_of_goods=Decimal("1000"), channel_fee_rate=Decimal("0.7"), target_margin_rate=Decimal("0.4"),
        )
        self.assertIsNone(result.proposed_sale_price)
        self.assertIsNone(result.margin_rate)
        self.assertTrue(any("달성할 수 없습니다" in w for w in result.warnings))

    def test_default_fee_assumption_is_called_out(self):
        result = self._propose(cost_of_goods=Decimal("1000"))
        self.assertTrue(any("10.8%" in w for w in result.warnings))
        result = self._propose(cost_of_goods=Decimal("1000"), channel_fee_rate=Decimal("0.098"))
        self.assertFalse(any("10.8%" in w for w in result.warnings))


class CreateDraftTests(unittest.TestCase):

    def setUp(self):
        _NO_DNS.start()
        self.addCleanup(_NO_DNS.stop)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            Company.__table__, ProductCandidate.__table__, ProductCandidateEvidence.__table__,
            ProductCandidateSelection.__table__, MarketplaceChannel.__table__, MarketplaceAccount.__table__,
            ListingWizard.__table__, MediaAsset.__table__,
        ])
        with self.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, "
                "description VARCHAR(500), ip_address VARCHAR(50))",
            ))
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        company = Company(name="가져오기 테스트", business_number="777-77-77777", ceo="t", phone="02", email="a@b.c", address="서울")
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id
        channel = MarketplaceChannel(code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED")
        self.db.add(channel)
        self.db.commit()
        self.account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel.id, account_code="acct", account_name="계정",
        )
        self.db.add(self.account)
        self.db.commit()
        self.transport = FakeTransport()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _request(self, **overrides):
        data = dict(
            source_kind="ONCHANNEL", source_url=ONCHANNEL_URL, source_reference="ONCHANNEL:CH5306792",
            product_name="러닝 무릎보호대 슬개건밴드", description="구성: 2개 1set", category_hint="무릎보호대",
            keywords=["무릎보호대", " ", "슬개건밴드"],
            margin=schema.MarginProposalRequest(
                cost_of_goods=Decimal("5720"), shipping_cost=Decimal("3000"), sale_price=Decimal("12900"),
            ),
            creation_idempotency_key="key-1",
        )
        data.update(overrides)
        return schema.DraftCreateRequest(**data)

    def _service(self):
        return ProductImportService(self.db, self.transport)

    def test_creates_candidate_wizard_draft_and_economics_without_approving_anything(self):
        result = self._service().create_draft(self._request(), self.company_id, user_id=1)

        self.assertTrue(result.candidate_created)
        self.assertTrue(result.wizard_created)
        self.assertTrue(result.economics_saved)

        candidate = self.db.get(ProductCandidate, result.candidate_id)
        self.assertEqual(candidate.source_reference, "ONCHANNEL:CH5306792")
        self.assertEqual(candidate.status, "DISCOVERED")          # 승인은 사용자가 직접 한다
        self.assertEqual(candidate.owner_company_id, self.company_id)
        self.assertEqual(self.db.query(ProductCandidateSelection).count(), 0)

        wizard = self.db.get(ListingWizard, result.wizard_id)
        self.assertEqual(wizard.product_candidate_id, candidate.id)
        self.assertEqual(wizard.status, "DRAFT")                  # 승인·제출 단계로 넘기지 않았다
        self.assertIsNone(wizard.approved_at)
        self.assertIn("러닝 무릎보호대 슬개건밴드", wizard.draft_json)
        self.assertNotIn(" ", [k for k in __import__("json").loads(wizard.draft_json)["keywords"]])
        self.assertIn("12900", wizard.economics_input_json)
        self.assertTrue(any("후보를 승인" in step for step in result.next_steps))

    def test_low_margin_stops_before_anything_is_created(self):
        request = self._request(margin=schema.MarginProposalRequest(
            cost_of_goods=Decimal("5720"), shipping_cost=Decimal("3000"), sale_price=Decimal("9900"),
        ))
        with self.assertRaises(ProductImportError):
            self._service().create_draft(request, self.company_id, user_id=1)
        self.assertEqual(self.db.query(ProductCandidate).count(), 0)
        self.assertEqual(self.db.query(ListingWizard).count(), 0)

    def test_same_key_is_idempotent_and_does_not_overwrite_user_edits(self):
        first = self._service().create_draft(self._request(), self.company_id, user_id=1)
        second = self._service().create_draft(
            self._request(product_name="다른 이름으로 다시"), self.company_id, user_id=1,
        )
        self.assertEqual(first.wizard_id, second.wizard_id)
        self.assertFalse(second.wizard_created)
        self.assertFalse(second.candidate_created)
        self.assertEqual(self.db.query(ListingWizard).count(), 1)
        self.assertNotIn("다른 이름으로 다시", self.db.get(ListingWizard, first.wizard_id).draft_json)
        self.assertTrue(any("덮어쓰지 않았습니다" in w for w in second.warnings))

    def test_without_a_coupang_account_the_economics_step_is_skipped_with_a_reason(self):
        self.db.delete(self.account)
        self.db.commit()
        result = self._service().create_draft(self._request(), self.company_id, user_id=1)
        self.assertFalse(result.economics_saved)
        self.assertTrue(any("판매계정을 찾지 못해" in w for w in result.warnings))

    def test_without_margin_input_only_the_draft_is_made(self):
        result = self._service().create_draft(self._request(margin=None), self.company_id, user_id=1)
        self.assertFalse(result.economics_saved)
        self.assertIsNone(self.db.get(ListingWizard, result.wizard_id).economics_input_json)

    def test_notice_rows_are_kept_as_candidate_evidence_and_come_back_as_suggestions(self):
        rows = [
            schema.NoticeRow(label="제조국", value="중국"),
            schema.NoticeRow(label="크기/중량", value="약 15x15cm"),
            schema.NoticeRow(label="A/S 책임자와 전화번호", value="070-8983-8490"),
        ]
        result = self._service().create_draft(self._request(notice_rows=rows), self.company_id, user_id=1)
        evidence = self.db.query(ProductCandidateEvidence).filter(
            ProductCandidateEvidence.candidate_id == result.candidate_id,
            ProductCandidateEvidence.payload_summary.like("URL_IMPORT_NOTICE:%"),
        ).all()
        self.assertEqual(len(evidence), 1)
        self.assertNotIn("070-8983-8490", evidence[0].payload_summary)  # 공급처 연락처는 저장하지 않는다

        suggestion = self._service().notice_suggestions(
            result.candidate_id, ["크기, 중량", "제조국", "A/S 책임자와 전화번호", "색상"], self.company_id,
        )
        self.assertTrue(suggestion.source_found)
        self.assertEqual(suggestion.suggestions, {"크기, 중량": "약 15x15cm", "제조국": "중국"})

    def test_candidate_without_imported_notice_reports_no_source(self):
        result = self._service().create_draft(self._request(), self.company_id, user_id=1)
        suggestion = self._service().notice_suggestions(result.candidate_id, ["제조국"], self.company_id)
        self.assertFalse(suggestion.source_found)
        self.assertEqual(suggestion.suggestions, {})

    def test_other_companies_cannot_read_the_suggestions(self):
        result = self._service().create_draft(
            self._request(notice_rows=[schema.NoticeRow(label="제조국", value="중국")]), self.company_id, user_id=1,
        )
        with self.assertRaises(Exception):
            self._service().notice_suggestions(result.candidate_id, ["제조국"], company_id=self.company_id + 999)

    def test_main_images_are_imported_and_failures_are_reported_per_image(self):
        good = "https://onimg.onch3.co.kr/products/ok.png"
        bad = "https://onimg.onch3.co.kr/products/missing.png"
        self.transport.pages[good] = FetchedResponse(200, {"Content-Type": "image/png"}, _png())
        result = self._service().create_draft(
            self._request(image_urls_main=[good, bad, good]), self.company_id, user_id=1,
        )
        by_url = {i.source_url: i for i in result.images}
        self.assertEqual(len(result.images), 2)                    # 중복 URL은 한 번만
        self.assertTrue(by_url[good].success)
        self.assertIsNotNone(by_url[good].asset_id)
        self.assertFalse(by_url[bad].success)
        self.assertTrue(by_url[bad].error_message)
        self.assertTrue(any("일부 이미지를 가져오지 못했습니다" in w for w in result.warnings))
        asset = self.db.get(MediaAsset, by_url[good].asset_id)
        self.assertEqual(asset.rights_status, "RIGHTS_UNVERIFIED")  # 사용권은 사용자가 직접 확인한다
        self.assertEqual(asset.source_classification, "SUPPLIER")


if __name__ == "__main__":
    unittest.main()
