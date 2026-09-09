"""
=========================================================
Homez OS

File : tests/test_listing_wizard_csv_export.py

Gate U-3(2026-08-10) — `listing_wizard.export` CSV 내보내기 검증.
전부 임시 SQLite DB에서만 검증한다(실제 homez.db에 Permission 행을
만들지 않는다 — 기존 test_listing_wizard_permission.py와 동일한
관례).
=========================================================
"""

import csv
import io
import json
import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.security import hash_password
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.marketplace_listing import listing_wizard_router as wr
from app.domains.marketplace_listing.listing_wizard_approval_nonce import (
    clear_all_wizard_approval_nonces,
)
from app.domains.marketplace_listing.listing_wizard_csv_export import (
    MAX_CSV_EXPORT_ROWS,
)
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_ECONOMICS_VIEW,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_EXPORT,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_WIZARD_VIEW,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    EconomicsInputItem,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardCreateRequest,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardEconomicsUpdateRequest,
)
from app.domains.marketplace_listing.listing_wizard_service import (
    ListingWizardService,
)
from app.domains.marketplace_listing.model import ListingWizard
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
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.marketplace_listing.model import (
    MarketplaceSubmissionApproval,
)
from app.domains.media_asset.model import MediaAsset
from app.domains.permission.model import Permission
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User

_COUNTER = 0


def _key(prefix: str) -> str:

    global _COUNTER
    _COUNTER += 1
    return f"{prefix}-{_COUNTER}"


def _csv_rows(csv_text: str) -> list[list[str]]:

    return list(csv.reader(io.StringIO(csv_text.lstrip("﻿"))))


class ListingWizardCsvExportTestCase(unittest.TestCase):

    def setUp(self):

        clear_all_wizard_approval_nonces()

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, Permission.__table__,
                RolePermission.__table__, User.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                MarketplaceChannel.__table__, MarketplaceAccount.__table__,
                MarketplaceFulfillmentCapability.__table__,
                MarketplaceListingDraft.__table__, MarketplaceListing.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmissionApproval.__table__,
                MarketplaceFulfillmentEligibility.__table__,
                MarketplaceSubmission.__table__, ListingWizard.__table__,
                MediaAsset.__table__, AutomationModeState.__table__,
                EmergencyStop.__table__, ExecutionLimit.__table__,
                ExecutionUsage.__table__, ExecutionPeriodUsage.__table__,
            ],
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        SafetyService(self.db).set_mode(
            AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
        )

        self.company_a = Company(
            name="A", business_number="1", ceo="t", phone="t",
            email="a@example.com", address="t",
        )
        self.company_b = Company(
            name="B", business_number="2", ceo="t", phone="t",
            email="b@example.com", address="t",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.admin_role = Role(name="Administrator", code="ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.admin_role, self.viewer_role])
        self.db.commit()

        self.permissions = {}
        for code in (
            LISTING_WIZARD_VIEW, LISTING_WIZARD_EXPORT, LISTING_ECONOMICS_VIEW,
        ):
            perm = Permission(name=code, code=code, active=True)
            self.db.add(perm)
            self.permissions[code] = perm
        self.db.commit()

        self.admin_user = User(
            username="admin1", email="admin1@example.com",
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=self.admin_role.id, company_id=self.company_a.id,
            is_active=True,
        )
        self.viewer_user = User(
            username="viewer1", email="viewer1@example.com",
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=self.viewer_role.id, company_id=self.company_a.id,
            is_active=True,
        )
        self.other_company_admin = User(
            username="admin2", email="admin2@example.com",
            password_hash=hash_password("Str0ng!Passw0rd"),
            role_id=self.admin_role.id, company_id=self.company_b.id,
            is_active=True,
        )
        self.db.add_all([
            self.admin_user, self.viewer_user, self.other_company_admin,
        ])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        clear_all_wizard_approval_nonces()

    def _grant(self, role: Role, code: str):

        self.db.add(RolePermission(
            role_id=role.id, permission_id=self.permissions[code].id,
        ))
        self.db.commit()

    def _create_wizard_with_economics(self, company_id, product_name):

        service = ListingWizardService(self.db)
        wizard, _dup = service.create(
            WizardCreateRequest(creation_idempotency_key=_key("k")),
            created_by=self.admin_user.id, company_id=company_id,
        )
        channel = MarketplaceChannel(
            code=_key("COUPANG"), name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()
        account = MarketplaceAccount(
            company_id=company_id, channel_id=channel.id,
            account_code=_key("acct"), account_name="테스트계정",
        )
        self.db.add(account)
        self.db.commit()

        wizard.draft_json = json.dumps({"product_name": product_name})
        self.db.add(wizard)
        self.db.commit()

        wizard = service.update_economics(
            wizard.id, company_id,
            data=WizardEconomicsUpdateRequest(
                expected_version=wizard.version,
                items=[EconomicsInputItem(
                    marketplace_account_id=account.id,
                    cost_of_goods="5000", sale_price="20000",
                )],
            ),
        )
        return wizard

    # ---------------- Permission 게이팅 ----------------
    #
    # 라우터 함수 직접 호출은 FastAPI Depends 체인을 타지 않으므로
    # (test_listing_wizard_permission.py와 동일한 이유) Guard 자체의
    # 통과/차단은 `ListingWizardPermissionGuard(...)`를 직접 호출해
    # 검증한다 — `wr.export_wizards_csv(current_user=..., db=...)`
    # 직접 호출은 응답 모양(컬럼 가시성 등)만 검증하는 데 쓴다.

    def test_view_only_permission_does_not_pass_export_guard(self):

        self._grant(self.viewer_role, LISTING_WIZARD_VIEW)

        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            ListingWizardPermissionGuard(LISTING_WIZARD_EXPORT)(
                current_user=self.viewer_user, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_export_permission_passes_export_guard(self):

        self._grant(self.viewer_role, LISTING_WIZARD_EXPORT)

        result = ListingWizardPermissionGuard(LISTING_WIZARD_EXPORT)(
            current_user=self.viewer_user, db=self.db,
        )
        self.assertEqual(result.id, self.viewer_user.id)

    def test_admin_always_passes_export_guard_without_grant(self):

        result = ListingWizardPermissionGuard(LISTING_WIZARD_EXPORT)(
            current_user=self.admin_user, db=self.db,
        )
        self.assertEqual(result.id, self.admin_user.id)

    # ---------------- Economics 컬럼 완전 제외 ----------------

    def test_without_economics_permission_columns_are_absent_entirely(self):

        self._grant(self.viewer_role, LISTING_WIZARD_EXPORT)
        self._create_wizard_with_economics(self.company_a.id, "상품A")

        response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.viewer_user, db=self.db,
        )
        header = _csv_rows(response.body.decode("utf-8"))[0]
        self.assertNotIn("원가 합계", header)
        self.assertNotIn("판매가 합계", header)
        self.assertNotIn("마진 합계", header)
        self.assertNotIn("평균 마진율", header)

    def test_with_economics_permission_columns_are_present_with_real_values(self):

        self._grant(self.viewer_role, LISTING_WIZARD_EXPORT)
        self._grant(self.viewer_role, LISTING_ECONOMICS_VIEW)
        self._create_wizard_with_economics(self.company_a.id, "상품A")

        response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.viewer_user, db=self.db,
        )
        rows = _csv_rows(response.body.decode("utf-8"))
        header, data_row = rows[0], rows[1]
        self.assertIn("원가 합계", header)
        cost_idx = header.index("원가 합계")
        self.assertEqual(data_row[cost_idx], "5000.00")

    # ---------------- 회사 격리 ----------------

    def test_cross_company_data_never_appears_in_export(self):

        self._create_wizard_with_economics(self.company_a.id, "회사A상품")
        self._create_wizard_with_economics(self.company_b.id, "회사B상품")

        response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.admin_user, db=self.db,
        )
        csv_text = response.body.decode("utf-8")
        self.assertIn("회사A상품", csv_text)
        self.assertNotIn("회사B상품", csv_text)

        response_b = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.other_company_admin, db=self.db,
        )
        csv_text_b = response_b.body.decode("utf-8")
        self.assertIn("회사B상품", csv_text_b)
        self.assertNotIn("회사A상품", csv_text_b)

    # ---------------- 최대 행수 ----------------

    def test_export_exceeding_max_rows_is_rejected(self):

        from unittest import mock
        with mock.patch(
            "app.domains.marketplace_listing.listing_wizard_service."
            "MAX_CSV_EXPORT_ROWS", 1,
        ):
            self._create_wizard_with_economics(self.company_a.id, "상품1")
            self._create_wizard_with_economics(self.company_a.id, "상품2")

            with self.assertRaises(BadRequestException):
                wr.export_wizards_csv(
                    status=None, locale="ko-KR",
                    current_user=self.admin_user, db=self.db,
                )

    def test_export_at_exactly_max_rows_succeeds(self):

        from unittest import mock
        with mock.patch(
            "app.domains.marketplace_listing.listing_wizard_service."
            "MAX_CSV_EXPORT_ROWS", 2,
        ):
            self._create_wizard_with_economics(self.company_a.id, "상품1")
            self._create_wizard_with_economics(self.company_a.id, "상품2")

            response = wr.export_wizards_csv(
                status=None, locale="ko-KR",
                current_user=self.admin_user, db=self.db,
            )
            self.assertEqual(response.status_code, 200)

    # ---------------- Formula Injection 방어 ----------------

    def test_malicious_product_name_is_neutralized(self):

        malicious_names = [
            "=cmd|'/C calc'!A1", "+SUM(1+1)", "-2+3", "@SUM(1)",
            "\tmalicious", "\rmalicious",
        ]
        for name in malicious_names:
            self._create_wizard_with_economics(self.company_a.id, name)

        response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.admin_user, db=self.db,
        )
        rows = _csv_rows(response.body.decode("utf-8"))
        header = rows[0]
        name_idx = header.index("상품명")
        for row in rows[1:]:
            cell = row[name_idx]
            self.assertTrue(cell.startswith("'"), cell)

    def test_benign_product_name_is_not_modified(self):

        self._create_wizard_with_economics(self.company_a.id, "정상 상품명")

        response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.admin_user, db=self.db,
        )
        rows = _csv_rows(response.body.decode("utf-8"))
        header = rows[0]
        name_idx = header.index("상품명")
        self.assertEqual(rows[1][name_idx], "정상 상품명")

    # ---------------- UTF-8 BOM ----------------

    def test_csv_starts_with_utf8_bom(self):

        self._create_wizard_with_economics(self.company_a.id, "상품A")

        response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.admin_user, db=self.db,
        )
        self.assertTrue(response.body.startswith(b"\xef\xbb\xbf"))

    # ---------------- ko-KR/en-US 헤더 ----------------

    def test_locale_switches_header_labels(self):

        self._create_wizard_with_economics(self.company_a.id, "상품A")

        ko_response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.admin_user, db=self.db,
        )
        en_response = wr.export_wizards_csv(
            status=None, locale="en-US",
            current_user=self.admin_user, db=self.db,
        )
        ko_header = _csv_rows(ko_response.body.decode("utf-8"))[0]
        en_header = _csv_rows(en_response.body.decode("utf-8"))[0]

        self.assertIn("상품명", ko_header)
        self.assertIn("Product Name", en_header)

    def test_unknown_locale_falls_back_to_korean(self):

        self._create_wizard_with_economics(self.company_a.id, "상품A")

        response = wr.export_wizards_csv(
            status=None, locale="fr-FR",
            current_user=self.admin_user, db=self.db,
        )
        header = _csv_rows(response.body.decode("utf-8"))[0]
        self.assertIn("상품명", header)

    # ---------------- Credential/Provider 원문 미노출 ----------------

    def test_export_never_contains_credential_like_strings(self):

        self._create_wizard_with_economics(self.company_a.id, "상품A")

        response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.admin_user, db=self.db,
        )
        csv_text = response.body.decode("utf-8").lower()
        for forbidden in (
            "password", "api_key", "apikey", "secret", "access_key",
            "credential",
        ):
            self.assertNotIn(forbidden, csv_text)

    # ---------------- Content-Disposition (파일명 injection 없음) ----------------

    def test_content_disposition_is_fixed_constant(self):

        self._create_wizard_with_economics(self.company_a.id, "상품A")

        response = wr.export_wizards_csv(
            status=None, locale="ko-KR",
            current_user=self.admin_user, db=self.db,
        )
        self.assertEqual(
            response.headers["content-disposition"],
            "attachment; filename=listing-wizards.csv",
        )


if __name__ == "__main__":
    unittest.main()
