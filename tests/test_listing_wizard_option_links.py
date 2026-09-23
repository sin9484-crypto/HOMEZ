"""
=========================================================
Homez OS

File : tests/test_listing_wizard_option_links.py

2026-09-21 옵션 연결 — 등록 제출 한 건 단위의 (1) 서버가 확정하는 "판매하기로 선택한
전체 옵션" 범위, (2) 옵션별 공급처 연결 저장, (3) 등록 결과 상세조회로 쿠팡 옵션번호를
부착하는 흐름, (4) 준비 완료 판정.

기존 위저드 픽스처(`ListingWizardLiveServiceTestCase._submitted_wizard`)로 실제
위저드→승인→제출 경로를 끝까지 만들고, 쿠팡·온채널은 가짜로 대체한다. 이 테스트는
등록(생성) API를 호출하는 `send()`를 준비 단계에서 가짜 Provider로 딱 1번만 쓰며,
옵션 식별자 동기화가 등록을 다시 호출하지 않는다는 것을 검증한다.
=========================================================
"""

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.marketplace_listing.coupang_live_provider import ProductOptionIdentifier
from app.domains.marketplace_listing.coupang_live_provider import (
    ProductOptionIdentifiersResult,
)
from app.domains.marketplace_listing.listing_wizard_live_service import (
    ListingWizardLiveService,
)
from app.domains.marketplace_listing.listing_wizard_option_link_service import (
    ListingWizardOptionLinkService,
)
from app.domains.purchase_task.channel_adapter import CapabilitySupport
from app.domains.purchase_task.channel_adapter import ChannelProductOption
from app.domains.purchase_task.channel_adapter import ProductLookupResult
from app.domains.purchase_task.model import SupplierOptionLink
from app.domains.purchase_task.supplier_option_link_service import (
    SupplierOptionLinkService,
)
from app.domains.store_connection.model import StoreConnection
import tests.test_coupang_live_submission as live_tests

PRODUCT = "CH1234567"
ITEMS = [
    {"itemName": "블랙", "externalVendorSku": "SKU-BLACK", "salePrice": 9000},
    {"itemName": "화이트", "externalVendorSku": "SKU-WHITE", "salePrice": 9500},
    {"itemName": "그레이", "externalVendorSku": "SKU-GRAY", "salePrice": 9800},
]


class _StubConnections:
    """PurchaseChannelConnectionService 대역 — 온채널을 전혀 호출하지 않는다."""

    def __init__(self, company_id):
        self.company_id = company_id
        self.options = tuple(
            ChannelProductOption(
                option_id=f"OPT{i}", label=f"공급옵션{i}", price=Decimal("5000"),
                in_stock=True,
            )
            for i in (1, 2, 3)
        )

    def get_connection_or_404(self, connection_id, company_id):
        if company_id != self.company_id or connection_id != 7:
            raise NotFoundException("매입 계정을 찾을 수 없습니다.")
        return SimpleNamespace(id=connection_id, mall_code="ONCHANNEL")

    def lookup_product(self, connection_id, company_id, code, *, triggered_by=None):
        return ProductLookupResult(
            support=CapabilitySupport.SUPPORTED, external_product_id=code, title="공급 상품",
            options=self.options, detail="가짜 조회",
        )


class _RegistrationProvider(live_tests._FakeProvider):
    """등록(create)은 준비 단계에서 1번만 쓰고, 이후 옵션 식별자 동기화가 다시 호출하면
    실패로 잡는다."""

    def __init__(self):
        super().__init__()
        self.id_result = ProductOptionIdentifiersResult(outcome="UNKNOWN", error_code="X")
        self.id_calls = []

    def get_product_option_identifiers(self, seller_product_id):
        self.id_calls.append(seller_product_id)
        return self.id_result


def _ids(pairs, *, status="승인완료"):
    return ProductOptionIdentifiersResult(
        outcome="FOUND", raw_status_name=status, http_status=200,
        items=tuple(
            ProductOptionIdentifier(sku, None if vid is None else str(vid), "1")
            for sku, vid in pairs
        ),
    )


class WizardOptionLinkBase(unittest.TestCase):
    """기존 위저드 테스트 픽스처를 합성해 쓴다(상속하면 그 파일의 테스트가 이 모듈에서
    다시 실행되므로 인스턴스로 감싼다)."""

    def setUp(self):
        self.h = live_tests.ListingWizardLiveServiceTestCase(
            "test_success_persists_external_reference_and_duplicate_is_blocked",
        )
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.addCleanup(self.h.tearDown)
        Base.metadata.create_all(
            bind=self.h.engine,
            tables=[StoreConnection.__table__, SupplierOptionLink.__table__],
        )
        self.db = self.h.db
        self.company_id = self.h.company_id
        self.live = ListingWizardLiveService(self.db)
        self.stub = _StubConnections(self.company_id)
        self.links = SupplierOptionLinkService(self.db, connection_service=self.stub)
        self.service = ListingWizardOptionLinkService(
            self.db, links=self.links, live=self.live,
        )

    # ---- 제출·등록 준비 ----

    def _prepare(self, *, register=True, items=ITEMS, store=True):
        self.wizard, result = self.h._submitted_wizard(live_ready=True)
        self.submission_id = result.submission_id
        self.provider = _RegistrationProvider()
        if register:
            self.live.send(self.wizard.id, self.submission_id, self.company_id, self.provider)
        submission = self.live.marketplace.get_submission_for_company(
            self.submission_id, self.company_id,
        )
        self.submission = submission
        # 다중 옵션으로 만든다 — 동결된 판매 방식과 위저드 입력을 함께 바꿔 일관되게 둔다.
        selection = self.live.marketplace.get_selection_for_company(
            submission.selection_id, self.company_id,
        )
        fields = json.loads(selection.required_fields_json)
        fields["items"] = items
        selection.required_fields_json = json.dumps(fields, ensure_ascii=False)
        entries = json.loads(self.wizard.channel_selections_json)
        entries[0]["required_fields"]["items"] = items
        self.wizard.channel_selections_json = json.dumps(entries, ensure_ascii=False)
        self.db.commit()
        if store:
            account = self.live.marketplace.get_account_for_company(
                submission.marketplace_account_id, self.company_id,
            )
            channel = self.live.marketplace.get_channel(account.channel_id)
            row = StoreConnection(
                company_id=self.company_id, marketplace_code=channel.code,
                seller_identifier=account.account_code, display_name="쿠팡",
                connection_status="CONNECTED", created_by=1,
                creation_idempotency_key="wiz-store", creation_request_fingerprint="f" * 64,
            )
            self.db.add(row)
            self.db.commit()
            self.store_id = row.id
        return submission

    def _view(self):
        return self.service.view(self.wizard.id, self.submission_id, self.company_id)

    def _link(self, sku, option="OPT1", units=1, replace=False):
        return self.service.save_option_link(
            self.wizard.id, self.submission_id, self.company_id, channel_sku=sku,
            purchase_connection_id=7, supplier_product_code=PRODUCT,
            supplier_option_id=option, units=units, replace=replace, actor_user_id=1,
        )

    def _sync(self, result):
        self.provider.id_result = result
        return self.service.sync_identifiers(
            self.wizard.id, self.submission_id, self.company_id, self.provider,
            actor_user_id=1,
        )

    def _link_all(self):
        for index, item in enumerate(ITEMS, start=1):
            self._link(item["externalVendorSku"], option=f"OPT{index}")


class ScopeAndReadinessTestCase(WizardOptionLinkBase):

    def test_server_lists_every_selected_option_even_when_only_one_is_linked(self):
        self._prepare()
        self._link("SKU-BLACK", option="OPT1")

        view = self._view()

        self.assertTrue(view["scope"]["confirmed"], view["scope"])
        self.assertEqual([o["sku"] for o in view["options"]], [i["externalVendorSku"] for i in ITEMS])
        by_sku = {o["sku"]: o for o in view["options"]}
        self.assertEqual(by_sku["SKU-BLACK"]["link_state"], "ACTIVE")
        self.assertEqual(by_sku["SKU-WHITE"]["link_state"], "MISSING")
        self.assertEqual(by_sku["SKU-GRAY"]["link_state"], "MISSING")
        self.assertFalse(view["readiness"]["all_ready"])
        self.assertEqual(view["readiness"]["reason"], "LINK_MISSING")

    def test_a_client_supplied_subset_can_never_look_all_ready(self):
        self._prepare()
        self._link_all()
        self._sync(_ids([("SKU-BLACK", 1), ("SKU-WHITE", 2), ("SKU-GRAY", 3)]))
        self.assertTrue(self._view()["readiness"]["all_ready"])   # 서버 확정 범위는 완료

        # 같은 상태에서 클라이언트가 한 SKU만 보내면 "선택 범위 확인"일 뿐이다.
        subset = self.links.readiness(
            self.company_id, store_connection_id=self.store_id, skus=["SKU-BLACK"],
        )
        self.assertEqual(subset["scope"], "CLIENT_SUPPLIED")
        self.assertTrue(subset["selected_ready"])
        self.assertFalse(subset["all_ready"])

    def test_unregistered_submission_is_not_ready_and_sync_never_calls_out(self):
        self._prepare(register=False)
        self._link_all()

        view = self._view()

        self.assertFalse(view["registered"])
        self.assertEqual(view["readiness"]["reason"], "NOT_REGISTERED")
        self.assertFalse(view["readiness"]["all_ready"])
        with self.assertRaises(BadRequestException) as ctx:
            self._sync(_ids([("SKU-BLACK", 1)]))
        self.assertIn("SELLER_PRODUCT_ID_NOT_AVAILABLE", str(ctx.exception))
        self.assertEqual(self.provider.id_calls, [])

    def test_save_is_limited_to_the_options_chosen_for_this_registration(self):
        self._prepare()
        with self.assertRaises(BadRequestException):
            self._link("SKU-NOT-CHOSEN")
        with self.assertRaises(NotFoundException):
            self.service.save_option_link(
                self.wizard.id, self.submission_id, self.company_id, channel_sku="SKU-BLACK",
                purchase_connection_id=999, supplier_product_code=PRODUCT,
                supplier_option_id="OPT1", units=1, replace=False, actor_user_id=1,
            )
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 0)

    def test_wizard_input_changed_after_selection_downgrades_the_scope(self):
        """등록된 것이 동결된 선택인지 위저드의 새 입력인지 확정할 수 없으면 모든 옵션이
        연결·확인돼 있어도 전체 준비 완료로 표시하지 않는다."""

        self._prepare()
        self._link_all()
        self._sync(_ids([("SKU-BLACK", 1), ("SKU-WHITE", 2), ("SKU-GRAY", 3)]))
        self.assertTrue(self._view()["readiness"]["all_ready"])

        entries = json.loads(self.wizard.channel_selections_json)
        entries[0]["required_fields"]["items"] = ITEMS[:2]
        self.wizard.channel_selections_json = json.dumps(entries, ensure_ascii=False)
        self.db.commit()

        view = self._view()
        self.assertFalse(view["scope"]["confirmed"])
        self.assertIn("WIZARD_CHANGED_AFTER_SELECTION", view["scope"]["reason"])
        self.assertFalse(view["readiness"]["all_ready"])
        self.assertEqual(view["readiness"]["reason"], "SCOPE_UNCONFIRMED")

    def test_unresolved_sales_account_downgrades_the_scope(self):
        self._prepare(store=False)
        view = self._view()
        self.assertFalse(view["scope"]["confirmed"])
        self.assertIn("STORE_CONNECTION_NOT_FOUND", view["scope"]["reason"])
        self.assertFalse(view["readiness"]["all_ready"])
        with self.assertRaises(BadRequestException):
            self._link("SKU-BLACK")

    def test_other_company_cannot_read_or_change_this_registration(self):
        self._prepare()
        with self.assertRaises(NotFoundException):
            self.service.view(self.wizard.id, self.submission_id, self.h.other_company_id)
        with self.assertRaises(NotFoundException):
            self.service.sync_identifiers(
                self.wizard.id, self.submission_id, self.h.other_company_id, self.provider,
                actor_user_id=1,
            )
        self.assertEqual(self.provider.id_calls, [])


class RegistrationResultLinkingTestCase(WizardOptionLinkBase):

    def test_reordered_response_attaches_by_sku_and_completes_readiness(self):
        self._prepare()
        self._link_all()

        result = self._sync(_ids([("SKU-GRAY", 303), ("SKU-BLACK", 101), ("SKU-WHITE", 202)]))

        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(self.provider.id_calls, [self.submission.external_submission_ref])
        self.assertEqual(len(self.provider.calls), 1)   # 등록(create)은 준비 때 1번뿐
        rows = {l.channel_sku: l for l in self.db.query(SupplierOptionLink).all()}
        self.assertEqual(
            {k: v.coupang_vendor_item_id for k, v in rows.items()},
            {"SKU-BLACK": "101", "SKU-WHITE": "202", "SKU-GRAY": "303"},
        )
        self.assertTrue(result["view"]["readiness"]["all_ready"])

    def test_not_yet_approved_product_keeps_the_readiness_incomplete_until_numbers_exist(self):
        self._prepare()
        self._link_all()

        pending = self._sync(_ids(
            [("SKU-BLACK", None), ("SKU-WHITE", None), ("SKU-GRAY", None)], status="임시저장",
        ))
        self.assertEqual(pending["state"], "INCOMPLETE")
        self.assertEqual(
            set(pending["attach"]["outcomes"].values()), {"ID_NOT_ISSUED"},
        )
        self.assertFalse(pending["view"]["readiness"]["all_ready"])
        self.assertEqual(pending["view"]["readiness"]["reason"], "IDS_UNCONFIRMED")

        done = self._sync(_ids([("SKU-BLACK", 1), ("SKU-WHITE", 2), ("SKU-GRAY", 3)]))
        self.assertTrue(done["view"]["readiness"]["all_ready"])

    def test_partial_numbers_are_partial_success_not_ready(self):
        self._prepare()
        self._link_all()

        result = self._sync(_ids([("SKU-BLACK", 1), ("SKU-WHITE", None)]))

        self.assertEqual(result["attach"]["outcomes"], {
            "SKU-BLACK": "ATTACHED", "SKU-WHITE": "ID_NOT_ISSUED",
            "SKU-GRAY": "MISSING_IN_RESPONSE",
        })
        self.assertFalse(result["attach"]["complete"])
        self.assertFalse(result["view"]["readiness"]["all_ready"])

    def test_unconfirmed_lookup_changes_nothing_and_never_reregisters(self):
        self._prepare()
        self._link_all()
        for outcome in (
            ProductOptionIdentifiersResult(outcome="UNKNOWN", error_code="TIMEOUT",
                                           error_summary="응답 시간 초과"),
            ProductOptionIdentifiersResult(outcome="NOT_FOUND", http_status=404),
        ):
            with self.subTest(outcome=outcome.outcome):
                result = self._sync(outcome)
                self.assertEqual(result["state"], "UNVERIFIED")
                self.assertIsNone(result["attach"])
                self.assertFalse(result["view"]["readiness"]["all_ready"])
        self.assertTrue(all(
            l.coupang_vendor_item_id is None for l in self.db.query(SupplierOptionLink).all()
        ))
        self.assertEqual(len(self.provider.calls), 1)   # 등록(create) 재호출 없음

    def test_identifier_conflict_blocks_readiness_until_cleared_and_reconfirmed(self):
        self._prepare()
        self._link_all()
        self._sync(_ids([("SKU-BLACK", 1), ("SKU-WHITE", 2), ("SKU-GRAY", 3)]))

        conflict = self._sync(_ids([("SKU-BLACK", 999), ("SKU-WHITE", 2), ("SKU-GRAY", 3)]))

        self.assertEqual(conflict["attach"]["outcomes"]["SKU-BLACK"], "CONFLICT")
        view = conflict["view"]
        self.assertFalse(view["readiness"]["all_ready"])
        self.assertEqual(view["readiness"]["reason"], "LINK_NEEDS_REVIEW")
        black = next(o for o in view["options"] if o["sku"] == "SKU-BLACK")
        self.assertEqual(black["link_state"], "NEEDS_REVIEW")
        row = self.links.get_link(self.company_id, self.store_id, "SKU-BLACK")
        self.assertEqual(row.coupang_vendor_item_id, "1")           # 덮어쓰지 않았다

        self.links.clear_coupang_identifiers(self.company_id, row.id, actor_user_id=1)
        recovered = self._sync(_ids([("SKU-BLACK", 999), ("SKU-WHITE", 2), ("SKU-GRAY", 3)]))
        self.assertFalse(recovered["view"]["readiness"]["all_ready"])   # 아직 재확인 필요
        self._link("SKU-BLACK", option="OPT1")                          # 명시적 재확인
        self.assertTrue(self._view()["readiness"]["all_ready"])

    def test_attach_failure_after_a_successful_lookup_is_recoverable_by_rerun(self):
        self._prepare()
        self._link_all()
        with mock.patch(
            "app.domains.purchase_task.supplier_option_link_service.write_audit_log",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaises(RuntimeError):
                self._sync(_ids([("SKU-BLACK", 1), ("SKU-WHITE", 2), ("SKU-GRAY", 3)]))
        self.db.rollback()
        self.assertTrue(all(
            l.coupang_vendor_item_id is None for l in self.db.query(SupplierOptionLink).all()
        ))

        rerun = self._sync(_ids([("SKU-BLACK", 1), ("SKU-WHITE", 2), ("SKU-GRAY", 3)]))
        self.assertTrue(rerun["view"]["readiness"]["all_ready"])
        self.assertEqual(len(self.provider.calls), 1)

    def test_internal_save_failure_after_external_registration_is_never_reregistered(self):
        """외부 등록은 성공했는데 결과 저장이 실패한 제출(상태 SUBMITTING, 상품 ID 없음)은
        옵션 식별자를 조회할 수 없고, 다시 등록되지도 않는다."""

        self.wizard, result = self.h._submitted_wizard(live_ready=True)
        self.submission_id = result.submission_id
        provider = _RegistrationProvider()
        original = self.live.marketplace.update_submission_status_conditional
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return original(*args, **kwargs)
            raise RuntimeError("SIMULATED_DISK_IO_ERROR")

        with mock.patch.object(
            self.live.marketplace, "update_submission_status_conditional", side_effect=flaky,
        ):
            with self.assertRaises(RuntimeError):
                self.live.send(self.wizard.id, self.submission_id, self.company_id, provider)
        self.db.rollback()
        self.assertEqual(len(provider.calls), 1)

        self.provider = provider
        view = self._view()
        self.assertFalse(view["registered"])
        self.assertFalse(view["readiness"]["all_ready"])
        with self.assertRaises(BadRequestException):
            self._sync(_ids([("SKU-001", 1)]))
        with self.assertRaises(ForbiddenException):
            self.live.send(self.wizard.id, self.submission_id, self.company_id, provider)
        self.assertEqual(len(provider.calls), 1)   # 재등록 없음
        self.assertEqual(provider.id_calls, [])


class RouteProtectionTestCase(unittest.TestCase):

    @staticmethod
    def _required_permission(route):
        """ListingWizardPermissionGuard(code)가 만든 dependency의 클로저에서 요구 권한을 읽는다."""

        codes = set()
        for dep in route.dependant.dependencies:
            if "ListingWizardPermissionGuard" in getattr(dep.call, "__qualname__", ""):
                codes |= {c.cell_contents for c in (dep.call.__closure__ or ())}
        return codes

    def test_new_routes_are_permission_guarded_with_the_right_permission(self):
        from app.core.guard import AdminGuard
        from app.domains.marketplace_listing.listing_wizard_permissions import (
            LISTING_WIZARD_SUBMIT,
            LISTING_WIZARD_VIEW,
        )
        from app.domains.marketplace_listing.listing_wizard_router import router as wizard_router
        from app.domains.purchase_task.router import router as task_router

        found = {}
        for route in wizard_router.routes:
            if route.path.endswith("/option-links") or route.path.endswith("/sync-identifiers"):
                for method in route.methods:
                    found[(method, route.path.rsplit("/", 1)[-1])] = self._required_permission(route)
        self.assertEqual(found, {
            ("GET", "option-links"): {LISTING_WIZARD_VIEW},
            ("PUT", "option-links"): {LISTING_WIZARD_SUBMIT},
            ("POST", "sync-identifiers"): {LISTING_WIZARD_SUBMIT},
        })

        task_routes = [r for r in task_router.routes if r.path.endswith("/supplier-option-link")]
        self.assertEqual({tuple(sorted(r.methods)) for r in task_routes}, {("GET",), ("PUT",)})
        for route in task_routes:
            self.assertIn(AdminGuard, [d.call for d in route.dependant.dependencies])


if __name__ == "__main__":
    unittest.main()
