"""
=========================================================
Homez OS

File : tests/test_coupang_seller_connection_routing.py

2026-10-05 — 승인 대상 판매계정 → 실제 쿠팡 판매 연결 해석과, 메타데이터·물류·브랜드·등록
전송 Provider가 **같은 연결**을 쓰는지 격리 검증한다. 실제 Windows Credential Manager·실제
쿠팡 API·실제 homez.db는 쓰지 않는다(자격증명 저장소와 Provider 클래스를 대체한다).

연결 행의 `credential_reference`는 테스트용 문자열이고, 대체한 저장소는 그 참조를 그대로
돌려주므로 "어느 연결의 자격증명으로 Provider가 만들어졌는지"를 값 없이 확인할 수 있다.
=========================================================
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ServiceUnavailableException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing import listing_wizard_router as router
from app.domains.marketplace_listing.coupang_seller_connection import (
    SELLER_CONNECTION_AMBIGUOUS,
    SELLER_CONNECTION_NOT_CONNECTED,
    SELLER_CONNECTION_NOT_FOUND,
    SellerConnectionError,
    resolve_seller_connection,
    seller_connection_descriptor,
    wizard_target_account_id,
)
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.store_connection.model import StoreConnection


class _RecordingStore:
    """자격증명 값 대신 읽은 참조 문자열을 돌려준다."""

    reads: list[str] = []

    def read(self, reference):
        _RecordingStore.reads.append(reference)
        return {"reference": reference}


class SellerConnectionRoutingTestCase(unittest.TestCase):

    def setUp(self):
        os.environ.pop("HOMEZ_TEST_FAKE_COUPANG_PROVIDER", None)
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, MarketplaceChannel.__table__,
                MarketplaceAccount.__table__, StoreConnection.__table__,
            ],
        )
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        def _company(name, number):
            row = Company(
                name=name, business_number=number, ceo="t", phone="1",
                email=f"{number}@example.com", address="t",
            )
            self.db.add(row)
            self.db.commit()
            return row.id

        self.company = _company("연결 라우팅 회사", "666-66-66661")
        self.other_company = _company("다른 회사", "666-66-66662")
        self.coupang = MarketplaceChannel(
            code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.naver = MarketplaceChannel(
            code="NAVER_SMARTSTORE", name="네이버", doc_verification_status="VERIFIED",
        )
        self.db.add_all([self.coupang, self.naver])
        self.db.commit()
        _RecordingStore.reads = []

    # ---- helpers ----

    def _account(self, code, *, company=None, channel=None, active=True):
        row = MarketplaceAccount(
            company_id=company or self.company,
            channel_id=(channel or self.coupang).id,
            account_code=code, account_name="이름", is_active=active,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _connection(
        self, seller, *, company=None, status="CONNECTED", ref="default",
        display_name="같은 표시 이름", version=1, marketplace="COUPANG",
    ):
        row = StoreConnection(
            company_id=company or self.company, marketplace_code=marketplace,
            display_name=display_name, seller_identifier=seller,
            credential_reference=(f"ref-{seller}" if ref == "default" else ref),
            connection_status=status, credential_version=version, created_by=1,
            creation_idempotency_key=f"k-{seller}-{company}-{marketplace}",
            creation_request_fingerprint="f" * 64,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _error_code(self, function, *args):
        with self.assertRaises(SellerConnectionError) as ctx:
            function(*args)
        return ctx.exception.code

    # ---- 해석 규칙 ----

    def test_designated_account_resolves_to_its_own_connection_not_the_first_or_latest(self):

        account_a = self._account("acct-a")
        account_b = self._account("acct-b")
        conn_a = self._connection("acct-a")
        conn_b = self._connection("acct-b")  # id가 더 크다 — 예전 방식이면 이쪽이 뽑혔다

        self.assertEqual(resolve_seller_connection(self.db, self.company, account_a.id).id, conn_a.id)
        self.assertEqual(resolve_seller_connection(self.db, self.company, account_b.id).id, conn_b.id)

    def test_display_name_equality_does_not_link_account_to_connection(self):

        account = self._account("acct-a")
        # 표시 이름은 같지만 판매자 식별자(account_code 기준)가 다른 연결뿐이다.
        self._connection("someone-else", display_name="계정")
        self.assertEqual(
            self._error_code(resolve_seller_connection, self.db, self.company, account.id),
            SELLER_CONNECTION_NOT_FOUND,
        )

    def test_single_connected_connection_is_not_assumed_to_be_the_approved_account(self):
        """연결이 하나뿐이어도 다른 판매자 식별자면 이 계정의 연결이 아니다."""

        account = self._account("approved-account")
        self._connection("another-seller")
        self.assertEqual(
            self._error_code(resolve_seller_connection, self.db, self.company, account.id),
            SELLER_CONNECTION_NOT_FOUND,
        )

    def test_other_company_account_or_connection_is_never_used(self):

        account = self._account("acct-a")
        self._connection("acct-a", company=self.other_company)  # 다른 회사의 같은 식별자
        self.assertEqual(
            self._error_code(resolve_seller_connection, self.db, self.company, account.id),
            SELLER_CONNECTION_NOT_FOUND,
        )
        # 다른 회사의 계정 id로 이 회사 범위에서 조회할 수 없다.
        foreign = self._account("acct-x", company=self.other_company)
        self._connection("acct-x", company=self.other_company)
        self.assertEqual(
            self._error_code(resolve_seller_connection, self.db, self.company, foreign.id),
            SELLER_CONNECTION_NOT_FOUND,
        )

    def test_unconnected_or_credential_less_connection_is_blocked(self):

        for status, ref in (
            ("ERROR", "default"), ("DISABLED", "default"),
            ("NOT_CONFIGURED", "default"), ("CONNECTED", None),
        ):
            with self.subTest(status=status, ref=ref):
                self.db.query(StoreConnection).delete()
                self.db.query(MarketplaceAccount).delete()
                self.db.commit()
                account = self._account("acct-a")
                self._connection("acct-a", status=status, ref=ref)
                self.assertEqual(
                    self._error_code(resolve_seller_connection, self.db, self.company, account.id),
                    SELLER_CONNECTION_NOT_CONNECTED,
                )

    def test_missing_inactive_or_non_coupang_account_is_blocked(self):

        self._connection("acct-a")
        self.assertEqual(
            self._error_code(resolve_seller_connection, self.db, self.company, None),
            SELLER_CONNECTION_NOT_FOUND,
        )
        self.assertEqual(
            self._error_code(resolve_seller_connection, self.db, self.company, 9999),
            SELLER_CONNECTION_NOT_FOUND,
        )
        inactive = self._account("acct-a", active=False)
        self.assertEqual(
            self._error_code(resolve_seller_connection, self.db, self.company, inactive.id),
            SELLER_CONNECTION_NOT_FOUND,
        )
        naver_account = self._account("naver-a", channel=self.naver)
        self._connection("naver-a", marketplace="NAVER_SMARTSTORE")
        self.assertEqual(
            self._error_code(resolve_seller_connection, self.db, self.company, naver_account.id),
            SELLER_CONNECTION_NOT_FOUND,
        )

    def test_wizard_stage_target_account_must_be_unambiguous(self):

        def wizard(*ids):
            return SimpleNamespace(channel_selections_json=json.dumps(
                [{"marketplace_account_id": i} for i in ids],
            ))

        self.assertEqual(wizard_target_account_id(wizard(7)), 7)
        self.assertEqual(wizard_target_account_id(wizard(7), 7), 7)
        self.assertEqual(wizard_target_account_id(wizard(7, 8), 8), 8)
        self.assertEqual(self._error_code(wizard_target_account_id, wizard()), SELLER_CONNECTION_NOT_FOUND)
        self.assertEqual(self._error_code(wizard_target_account_id, wizard(7, 8)), SELLER_CONNECTION_AMBIGUOUS)
        self.assertEqual(self._error_code(wizard_target_account_id, wizard(7), 9), SELLER_CONNECTION_NOT_FOUND)

    def test_descriptor_changes_when_the_connection_or_credential_version_changes(self):

        account = self._account("acct-a")
        conn = self._connection("acct-a", version=1)
        first = seller_connection_descriptor(self.db, self.company, account.id)
        self.assertEqual(first["store_connection_id"], conn.id)
        self.assertNotIn("credential_reference", first)  # 비밀·참조는 담지 않는다

        conn.credential_version = 2  # 자격증명 교체
        self.db.commit()
        self.assertNotEqual(first, seller_connection_descriptor(self.db, self.company, account.id))

        conn.connection_status = "DISABLED"  # 해석 불가 → 식별 정보가 비어 있다
        self.db.commit()
        self.assertIsNone(
            seller_connection_descriptor(self.db, self.company, account.id)["store_connection_id"],
        )

    # ---- Provider 팩토리: 모든 단계가 같은 연결을 쓰고, 막히면 Provider를 만들지 않는다 ----

    def _patched_providers(self):
        """각 Provider 클래스를 기록용으로 대체한다 — 생성된 횟수와 받은 참조를 센다."""

        created = []

        class _Recorder:
            def __init__(self, credential, *args, **kwargs):
                created.append((type(self).__name__, credential["reference"]))

        classes = {}
        for name in ("Metadata", "Logistics", "Brand", "Live"):
            classes[name] = type(name, (_Recorder,), {})
        patches = [
            patch("app.core.windows_credential_store.WindowsCredentialStore", _RecordingStore),
            patch(
                "app.domains.marketplace_listing.coupang_category_metadata_provider.CoupangCategoryMetadataProvider",
                classes["Metadata"],
            ),
            patch(
                "app.domains.marketplace_listing.coupang_logistics_provider.CoupangLogisticsProvider",
                classes["Logistics"],
            ),
            patch(
                "app.domains.marketplace_listing.coupang_brand_provider.CoupangLiveBrandProvider",
                classes["Brand"],
            ),
            patch(
                "app.domains.marketplace_listing.coupang_live_provider.CoupangLiveProductProvider",
                classes["Live"],
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return created

    def _all_factories(self, resolver):
        return [
            router._coupang_metadata_provider(self.db, self.company, resolver),
            router._coupang_logistics_provider(self.db, self.company, resolver),
            router._coupang_brand_provider(self.db, self.company, resolver),
            router._coupang_live_product_provider(self.db, self.company, resolver),
        ]

    def test_every_stage_uses_the_designated_connection_even_with_two_coupang_connections(self):

        created = self._patched_providers()
        account_a = self._account("acct-a")
        self._account("acct-b")
        self._connection("acct-a")
        self._connection("acct-b")  # 더 늦게 만든 다른 연결

        self._all_factories(lambda: account_a.id)

        self.assertEqual(
            created,
            [("Metadata", "ref-acct-a"), ("Logistics", "ref-acct-a"),
             ("Brand", "ref-acct-a"), ("Live", "ref-acct-a")],
            "메타데이터·물류·브랜드·등록 전송이 모두 지정 계정의 연결을 써야 한다",
        )
        self.assertNotIn("ref-acct-b", _RecordingStore.reads)

    def test_blocked_targets_create_no_provider_and_read_no_credential(self):
        """미연결·다른 회사·모호한 대상·지정 없음은 외부 호출(Provider 생성) 전에 막힌다."""

        created = self._patched_providers()
        account = self._account("acct-a")
        self._connection("acct-a", status="ERROR")
        foreign_account = self._account("acct-x", company=self.other_company)
        self._connection("acct-x", company=self.other_company)
        two_accounts = SimpleNamespace(channel_selections_json=json.dumps(
            [{"marketplace_account_id": account.id}, {"marketplace_account_id": foreign_account.id}],
        ))

        resolvers = {
            "not connected": lambda: account.id,
            "other company's account": lambda: foreign_account.id,
            "ambiguous wizard accounts": router._wizard_account_resolver(two_accounts, None),
            "no account selected": lambda: None,
        }
        for label, resolver in resolvers.items():
            for factory in (
                router._coupang_metadata_provider, router._coupang_logistics_provider,
                router._coupang_brand_provider, router._coupang_live_product_provider,
            ):
                with self.subTest(case=label, factory=factory.__name__):
                    with self.assertRaises(ServiceUnavailableException):
                        factory(self.db, self.company, resolver)
        # 대상을 지정하지 않는 옛 호출 방식도 막힌다.
        for factory in (
            router._coupang_metadata_provider, router._coupang_logistics_provider,
            router._coupang_brand_provider, router._coupang_live_product_provider,
        ):
            with self.assertRaises(ServiceUnavailableException):
                factory(self.db, self.company)

        self.assertEqual(created, [])
        self.assertEqual(_RecordingStore.reads, [])

    def test_submission_resolver_uses_the_submissions_own_account_and_company(self):

        from app.domains.marketplace_listing.model import MarketplaceSubmission

        self.assertTrue(hasattr(MarketplaceSubmission, "marketplace_account_id"))
        # 존재하지 않는/다른 회사 제출은 NotFound(대상 계정을 추측하지 않는다).
        from app.core.exceptions import NotFoundException

        Base.metadata.create_all(bind=self.engine, tables=[MarketplaceSubmission.__table__])
        with self.assertRaises(NotFoundException):
            router._submission_account_resolver(self.db, self.company, 4242)()


if __name__ == "__main__":
    unittest.main()
