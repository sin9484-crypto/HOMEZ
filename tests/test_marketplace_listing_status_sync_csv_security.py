"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_status_sync_csv_security.py

2026-08-05 CTO 반려 반영 — Phase 4 CSV 내보내기 보안 검증(item 5).
Fake Provider만 사용, 외부 네트워크 호출 0건. 실제 homez.db는 사용하지
않는다(임시 SQLite 파일).

검증 범위:
  - Formula Injection 방어(=, +, -, @, tab, CR로 시작하는 값) — 상품명
    (사용자 입력)과 external_listing_id·platform_raw_status(Provider
    출처) 양쪽 모두.
  - UTF-8 BOM 존재(Excel 한글 호환).
  - 최대 내보내기 행수 제한(조용히 자르지 않고 명시적으로 차단).
  - 오류 코드 컬럼이 항상 고정 enum 값이지 원본 예외 메시지·스택
    트레이스가 아님(내부 정보 노출 금지).
  - 회사 스코프 강제(다른 회사 데이터가 CSV에 절대 섞이지 않음).
=========================================================
"""

import csv
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.automation_safety.model import EmergencyStop
from app.domains.company.model import Company
from app.domains.marketplace_listing.constants import (
    ListingStatusCheckErrorCode,
)
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentCapability,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentEligibility,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentSelection,
)
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.marketplace_listing.model import MarketplaceListingDraft
from app.domains.marketplace_listing.model import (
    MarketplaceListingStatusEvent,
)
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.marketplace_listing.model import (
    MarketplaceSubmissionApproval,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceListingCreateRequest,
)
from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.marketplace_listing.status_sync_service import (
    ListingStatusSyncService,
)
from app.domains.marketplace_listing.status_sync_service import (
    _CSV_HEADER_LABELS,
)

# 2026-08-07 Gate H — CSV 헤더가 ko-KR/en-US로 지역화되면서
# "product_name" 같은 원문 컬럼 키가 더 이상 헤더 문자열 그대로
# 나오지 않는다. export_csv() 호출부가 전부 locale 인자 없이(기본값
# ko-KR) 호출하므로, 헤더를 그 지역화된 문구로 찾는다.
_KO_HEADER = _CSV_HEADER_LABELS["ko-KR"]
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.user.model import User  # noqa: F401

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class CsvSecurityTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceFulfillmentCapability.__table__,
                MarketplaceListingDraft.__table__,
                MarketplaceListing.__table__,
                MarketplaceListingStatusEvent.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmissionApproval.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__,
                EmergencyStop.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")

        self.service = ListingStatusSyncService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_company(self, label, business_number):

        company = Company(
            name=f"회사 {label}", business_number=business_number,
            ceo="테스트", phone="02-000-0000",
            email=f"{label.lower()}@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()

        return company

    def _seed_channel(self):

        channel = (
            self.db.query(MarketplaceChannel)
            .filter(MarketplaceChannel.code == "COUPANG")
            .first()
        )
        if channel is None:
            channel = MarketplaceChannel(
                code="COUPANG", name="쿠팡",
                doc_verification_status="VERIFIED",
            )
            self.db.add(channel)
            self.db.commit()

        return channel

    def _seed_listing(self, company_id, ref, product_name=None, external_listing_id=None):

        candidate = ProductCandidate(
            candidate_key=f"test:COUPANG:{ref}", source_type="TREND",
            source_reference=ref, market="COUPANG",
            product_name=product_name or f"상품 {ref}", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        channel = self._seed_channel()

        account = MarketplaceAccount(
            company_id=company_id, channel_id=channel.id,
            account_code=f"acct-{ref}", account_name=f"계정 {ref}",
        )
        self.db.add(account)
        self.db.commit()

        listing_service = MarketplaceListingService(self.db)
        listing, _dup = listing_service.create_listing(
            MarketplaceListingCreateRequest(
                product_candidate_id=candidate.id,
                marketplace_account_id=account.id,
            ),
            company_id,
        )

        if external_listing_id is not None:
            listing_row = (
                self.db.query(MarketplaceListing)
                .filter(MarketplaceListing.id == listing.id)
                .first()
            )
            listing_row.external_listing_id = external_listing_id
            self.db.commit()

        return listing.id

    def _csv_rows(self, csv_text):
        """BOM을 제거하고 csv.reader로 파싱해 행 목록을 돌려준다."""

        text = csv_text.lstrip("﻿")
        return list(csv.reader(io.StringIO(text)))

    # ----------------------------------------------------
    # Formula Injection 방어 — 사용자 입력(상품명)
    # ----------------------------------------------------

    def test_malicious_product_name_prefixes_are_neutralized(self):

        for i, prefix in enumerate(_DANGEROUS_PREFIXES):
            malicious_name = f"{prefix}cmd|'/C calc'!A1-{i}"
            self._seed_listing(
                self.company_a.id, f"prod-mal-{i}", product_name=malicious_name,
            )

        csv_text = self.service.export_csv(self.company_a.id)
        rows = self._csv_rows(csv_text)
        header = rows[0]
        name_idx = header.index(_KO_HEADER["product_name"])

        data_rows = rows[1:]
        self.assertEqual(len(data_rows), len(_DANGEROUS_PREFIXES))

        for row in data_rows:
            cell = row[name_idx]
            # 원본 위험 접두문자로 셀이 "시작"하면 안 된다(엑셀이
            # 수식으로 해석) — 작은따옴표로 무력화돼 있어야 한다.
            self.assertTrue(
                cell.startswith("'"), f"무력화되지 않은 셀: {cell!r}",
            )
            self.assertNotEqual(cell[0], "=")
            self.assertNotEqual(cell[0], "+")

    def test_benign_product_name_is_not_modified(self):

        self._seed_listing(
            self.company_a.id, "benign-1", product_name="정상 상품명 123",
        )

        csv_text = self.service.export_csv(self.company_a.id)
        rows = self._csv_rows(csv_text)
        header = rows[0]
        name_idx = header.index(_KO_HEADER["product_name"])

        self.assertEqual(rows[1][name_idx], "정상 상품명 123")

    # ----------------------------------------------------
    # Formula Injection 방어 — Provider 출처(external_listing_id)
    # ----------------------------------------------------

    def test_malicious_external_listing_id_is_neutralized(self):

        for i, prefix in enumerate(_DANGEROUS_PREFIXES):
            self._seed_listing(
                self.company_a.id, f"ext-mal-{i}",
                external_listing_id=f"{prefix}HYPERLINK(\"http://evil\")-{i}",
            )

        csv_text = self.service.export_csv(self.company_a.id)
        rows = self._csv_rows(csv_text)
        header = rows[0]
        ext_idx = header.index(_KO_HEADER["external_listing_id"])

        for row in rows[1:]:
            cell = row[ext_idx]
            self.assertTrue(
                cell.startswith("'"), f"무력화되지 않은 셀: {cell!r}",
            )

    def test_malicious_platform_raw_status_is_neutralized(self):
        """
        platform_raw_status는 Provider가 채우는 값이다 — Fake Provider
        결과를 실제로 거치게 한 뒤, 저장된 값을 악성 문자열로 덮어써
        CSV 내보내기 시점에 그 값이 무력화되는지 검증한다(Provider가
        신뢰할 수 없는 소스라는 전제).
        """

        listing_id = self._seed_listing(self.company_a.id, "raw-mal-1")
        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == listing_id)
            .first()
        )
        listing.platform_raw_status = "=cmd|'/C calc'!A1"
        self.db.commit()

        csv_text = self.service.export_csv(self.company_a.id)
        rows = self._csv_rows(csv_text)
        header = rows[0]
        raw_idx = header.index(_KO_HEADER["platform_raw_status"])

        self.assertTrue(rows[1][raw_idx].startswith("'"))

    # ----------------------------------------------------
    # UTF-8 BOM / Excel 한글 호환
    # ----------------------------------------------------

    def test_csv_starts_with_utf8_bom(self):

        self._seed_listing(self.company_a.id, "bom-1", product_name="한글 상품명")

        csv_text = self.service.export_csv(self.company_a.id)

        self.assertTrue(csv_text.startswith("﻿"))
        self.assertEqual(
            csv_text.encode("utf-8")[:3], b"\xef\xbb\xbf",
        )

    def test_korean_text_survives_bom_strip_and_parses_correctly(self):

        self._seed_listing(
            self.company_a.id, "kr-1", product_name="한글 상품명 테스트",
        )

        csv_text = self.service.export_csv(self.company_a.id)
        rows = self._csv_rows(csv_text)
        header = rows[0]
        name_idx = header.index(_KO_HEADER["product_name"])

        self.assertEqual(rows[1][name_idx], "한글 상품명 테스트")

    # ----------------------------------------------------
    # 최대 내보내기 행수
    # ----------------------------------------------------

    def test_export_exceeding_max_rows_is_rejected(self):

        for i in range(3):
            self._seed_listing(self.company_a.id, f"limit-{i}")

        with patch(
            "app.domains.marketplace_listing.status_sync_service."
            "_MAX_CSV_EXPORT_ROWS",
            2,
        ):
            with self.assertRaises(BadRequestException):
                self.service.export_csv(self.company_a.id)

    def test_export_at_exactly_max_rows_succeeds(self):

        for i in range(2):
            self._seed_listing(self.company_a.id, f"exact-{i}")

        with patch(
            "app.domains.marketplace_listing.status_sync_service."
            "_MAX_CSV_EXPORT_ROWS",
            2,
        ):
            csv_text = self.service.export_csv(self.company_a.id)

        rows = self._csv_rows(csv_text)
        self.assertEqual(len(rows) - 1, 2)  # header 제외

    # ----------------------------------------------------
    # 오류 코드 — 원본 예외 메시지·스택 트레이스 노출 금지
    # ----------------------------------------------------

    def test_error_code_column_is_always_a_fixed_enum_value(self):

        listing_id = self._seed_listing(self.company_a.id, "err-1")
        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.id == listing_id)
            .first()
        )
        listing.external_listing_id = "TRIGGER_5XX"
        self.db.commit()

        self.service.refresh_status(
            listing_id, self.company_a.id, triggered_by=1,
        )

        csv_text = self.service.export_csv(self.company_a.id)
        rows = self._csv_rows(csv_text)
        header = rows[0]
        error_idx = header.index(_KO_HEADER["status_last_refresh_error_code"])

        error_value = rows[1][error_idx]
        self.assertIn(error_value, ("", *ListingStatusCheckErrorCode.ALL))
        # 원본 예외 문자열·경로·스택트레이스 흔적이 없어야 한다.
        self.assertNotIn("Traceback", error_value)
        self.assertNotIn("File \"", error_value)
        self.assertNotIn(".py", error_value)

    # ----------------------------------------------------
    # 회사 스코프 강제
    # ----------------------------------------------------

    def test_csv_export_never_leaks_other_company_data(self):

        self._seed_listing(
            self.company_a.id, "scope-a", product_name="회사A 전용 상품",
        )
        self._seed_listing(
            self.company_b.id, "scope-b", product_name="회사B 전용 상품",
        )

        csv_text = self.service.export_csv(self.company_a.id)

        self.assertIn("회사A 전용 상품", csv_text)
        self.assertNotIn("회사B 전용 상품", csv_text)


if __name__ == "__main__":
    unittest.main()
