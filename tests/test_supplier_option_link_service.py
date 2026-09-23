"""
=========================================================
Homez OS

File : tests/test_supplier_option_link_service.py

2026-09-21 옵션 연결 — 쿠팡 판매 옵션(externalVendorSku = 주문 channel_sku)
↔ 온채널 상품코드·옵션ID 영구 연결(SupplierOptionLink) 격리 테스트.

실제 쿠팡·온채널 호출은 전혀 없다: 공급처 조회는 가짜 Adapter로,
자격증명은 InMemoryCredentialStore로 격리한다(기존 발주 검토 테스트의
픽스처를 그대로 재사용). 어떤 테스트도 submit_order()를 호출하지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.purchase_task.channel_adapter import CapabilitySupport
from app.domains.purchase_task.channel_adapter import ChannelProductOption
from app.domains.purchase_task.channel_adapter import ProductLookupResult
from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError
from app.domains.purchase_task.constants import SupplierOptionLinkStatus
from app.domains.purchase_task.model import PurchaseOrderApproval
from app.domains.purchase_task.model import SupplierOptionLink
from app.domains.purchase_task.supplier_option_link_service import SupplierComponent
from app.domains.purchase_task.supplier_option_link_service import (
    SupplierOptionLinkService,
)
from app.domains.store_connection.model import StoreConnection
from tests.test_purchase_task_order_submission_review import NOW
from tests.test_purchase_task_order_submission_review import (
    OrderSubmissionReviewTestCaseBase,
)

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "20260921_00_create_supplier_option_link_schema.sql"

PRODUCT = "CH1234567"


def _option(option_id, label, *, price="25000", in_stock=True):
    return ChannelProductOption(
        option_id=option_id, label=label, price=Decimal(price), in_stock=in_stock,
    )


class LinkTestCaseBase(OrderSubmissionReviewTestCaseBase):

    def setUp(self):

        super().setUp()
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                StoreConnection.__table__, OrderChannelFulfillment.__table__,
                UnresolvedOrderItem.__table__, SupplierOptionLink.__table__,
            ],
        )
        self.store_a = self._create_store(self.company_a)
        self.store_b = self._create_store(self.company_b)
        self.connection = self._create_ready_connection()
        self.links = SupplierOptionLinkService(
            self.db, connection_service=self.connection_service,
        )
        self.options = (
            _option("OPT1", "블랙"), _option("OPT2", "화이트"),
        )
        self._set_supplier_options(self.options)

    # ---------------- 픽스처 ----------------

    def _create_store(self, company, seller="SELLER-1") -> StoreConnection:

        store = StoreConnection(
            company_id=company.id, marketplace_code="COUPANG",
            display_name="쿠팡 스토어", seller_identifier=seller,
            connection_status="CONNECTED", created_by=1,
            creation_idempotency_key=f"store-{company.id}-{seller}",
            creation_request_fingerprint="f" * 64,
        )
        self.db.add(store)
        self.db.commit()
        self.db.refresh(store)
        return store

    def _set_supplier_options(self, options, *, error=None):
        """공급처 조회 응답을 교체한다(가짜 Adapter를 다시 설치 — 나중에
        설치한 patch가 이긴다)."""

        self._install_fake_connection_adapter(
            lookup_product_result=None if error else self._make_lookup_result(
                options=tuple(options),
            ),
            lookup_product_error=error,
        )

    def _order_task(
        self, *, sku="HOMEZ-A", quantity=1, store=None, company=None,
        channel_order_id="CPG-1", connection=None, key=None,
    ):
        """쿠팡 주문(스토어 귀속 포함) → 주문 품목(channel_sku) → 구매 작업."""

        company = company or self.company_a
        store = store or self.store_a
        order = self._create_order(company=company, channel_order_id=channel_order_id)
        item = self._create_order_item(order, quantity=quantity)
        item.channel_sku = sku
        self.db.add(OrderChannelFulfillment(
            company_id=company.id, store_connection_id=store.id, order_id=order.id,
            channel_order_id=channel_order_id, shipment_box_id="BOX-1",
            raw_status="ACCEPT", ordered_at=NOW,
        ))
        self.db.commit()
        task = self._create_task(
            key=key or f"review:{channel_order_id}", order=order, order_item=item,
            quantity=quantity,
        )
        conn = connection or self.connection
        self.service.assign_channel_connection(task.id, company.id, conn.id)
        return task

    def _save(self, sku="HOMEZ-A", *, option="OPT1", units=1, product=PRODUCT,
              replace=False, store=None, company=None, connection=None):

        company = company or self.company_a
        return self.links.save_link(
            company.id, store_connection_id=(store or self.store_a).id,
            channel_sku=sku, purchase_connection_id=(connection or self.connection).id,
            components=[SupplierComponent(product, option, units)],
            confirmed_by=1, replace=replace,
        )

    def _review(self, task, options, *, product=PRODUCT):

        return self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id=product,
            options=options, triggered_by=1,
        )

    def _link_row(self, sku="HOMEZ-A", store=None):

        self.db.expire_all()
        return self.links.get_link(self.company_a.id, (store or self.store_a).id, sku)


class SaveAndReuseTestCase(LinkTestCaseBase):

    def test_single_option_link_is_reused_for_the_next_order(self):
        self._save("HOMEZ-A", option="OPT1", units=1)
        first = self._order_task(sku="HOMEZ-A", quantity=1, channel_order_id="CPG-1")
        second = self._order_task(sku="HOMEZ-A", quantity=3, channel_order_id="CPG-2")

        r1 = self._review(first, [{"id": "OPT1", "qty": 1}])
        r2 = self._review(second, [{"id": "OPT1", "qty": 3}])

        for review, qty in ((r1, 1), (r2, 3)):
            self.assertEqual(review["supplier_link"]["state"], "ACTIVE")
            self.assertEqual(
                review["supplier_link"]["expected_options"],
                [{"id": "OPT1", "qty": qty}],
            )
            self.assertFalse(review["quantity_mismatch_warning"])
            self.assertFalse(
                [r for r in review["blocked_reasons"] if "연결" in r],
                review["blocked_reasons"],
            )

    def test_units_per_sale_multiplies_the_expected_supplier_quantity(self):
        """판매 1세트 = 공급 낱개 3개. 판매 2세트면 공급 6개여야 한다."""

        self._save("SET-3", option="OPT1", units=3)
        task = self._order_task(sku="SET-3", quantity=2)

        good = self._review(task, [{"id": "OPT1", "qty": 6}])
        self.assertFalse(good["quantity_mismatch_warning"])
        self.assertEqual(
            good["supplier_link"]["expected_options"], [{"id": "OPT1", "qty": 6}],
        )
        self.assertEqual(good["supplier_link"]["units_per_sale"], 3)

        bad = self._review(task, [{"id": "OPT1", "qty": 2}])
        self.assertTrue(bad["quantity_mismatch_warning"])
        self.assertTrue(any("기대" in r for r in bad["blocked_reasons"]))

    def test_multiple_options_each_use_their_own_link_and_partial_selection(self):
        """3개 옵션 중 사용자가 판매하기로 고른 2개만 연결한다 — 연결 없는
        SKU 주문은 링크로 조용히 채워지지 않고 수동 확인 경로에 남는다."""

        self._save("HOMEZ-BLACK", option="OPT1")
        self._save("HOMEZ-WHITE", option="OPT2")
        black = self._order_task(sku="HOMEZ-BLACK", channel_order_id="CPG-B")
        white = self._order_task(sku="HOMEZ-WHITE", channel_order_id="CPG-W")
        unselected = self._order_task(sku="HOMEZ-UNSELECTED", channel_order_id="CPG-U")

        self.assertEqual(
            self._review(black, [{"id": "OPT1", "qty": 1}])["supplier_link"][
                "expected_options"], [{"id": "OPT1", "qty": 1}],
        )
        self.assertEqual(
            self._review(white, [{"id": "OPT2", "qty": 1}])["supplier_link"][
                "expected_options"], [{"id": "OPT2", "qty": 1}],
        )
        # 서로의 옵션으로 바꿔 넣으면 차단된다.
        crossed = self._review(black, [{"id": "OPT2", "qty": 1}])
        self.assertTrue(any("연결과 다릅니다" in r for r in crossed["blocked_reasons"]))

        none = self._review(unselected, [{"id": "OPT1", "qty": 1}])
        self.assertEqual(none["supplier_link"]["state"], "NO_LINK")
        self.assertEqual(none["supplier_link"]["expected_options"], [])

    def test_same_label_options_and_reordered_response_keep_the_id_mapping(self):
        """이름이 같은 옵션 둘 + 응답 순서가 뒤집혀도 연결은 옵션ID로만 결정된다."""

        twins = (_option("OPT1", "블랙"), _option("OPT2", "블랙"))
        self._set_supplier_options(twins)
        self._save("HOMEZ-TWIN-2", option="OPT2")
        task = self._order_task(sku="HOMEZ-TWIN-2")

        for ordered in (twins, tuple(reversed(twins))):
            self._set_supplier_options(ordered)
            ok = self._review(task, [{"id": "OPT2", "qty": 1}])
            self.assertEqual(ok["supplier_link"]["state"], "ACTIVE")
            self.assertFalse(
                [r for r in ok["blocked_reasons"] if "연결" in r or "옵션" in r],
                ok["blocked_reasons"],
            )
            wrong = self._review(task, [{"id": "OPT1", "qty": 1}])
            self.assertTrue(any("연결과 다릅니다" in r for r in wrong["blocked_reasons"]))

    def test_duplicate_save_is_idempotent_and_changes_require_replace(self):
        first = self._save("HOMEZ-A", option="OPT1")
        again = self._save("HOMEZ-A", option="OPT1")
        self.assertEqual(first.id, again.id)
        self.assertEqual(again.version, 1)
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 1)

        with self.assertRaises(ConflictException):
            self._save("HOMEZ-A", option="OPT2")
        self.assertEqual(self._link_row().supplier_option_id, "OPT1")

        replaced = self._save("HOMEZ-A", option="OPT2", replace=True)
        self.assertEqual(replaced.id, first.id)
        self.assertEqual(replaced.supplier_option_id, "OPT2")
        self.assertEqual(replaced.version, 2)
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 1)

    def test_later_link_edit_does_not_change_an_already_approved_snapshot(self):
        link = self._save("HOMEZ-A", option="OPT1")
        task = self._order_task(sku="HOMEZ-A")
        approval = PurchaseOrderApproval(
            company_id=self.company_a.id, purchase_task_id=task.id,
            connection_id=self.connection.id, product_code=PRODUCT,
            options_snapshot_json='[{"id": "OPT1", "qty": 1}]',
        )
        self.db.add(approval)
        self.db.commit()
        approval_id = approval.id

        self._save("HOMEZ-A", option="OPT2", replace=True)

        self.db.expire_all()
        frozen = self.db.get(PurchaseOrderApproval, approval_id)
        self.assertEqual(frozen.product_code, PRODUCT)
        self.assertEqual(frozen.options_snapshot_json, '[{"id": "OPT1", "qty": 1}]')
        self.assertEqual(self.db.get(SupplierOptionLink, link.id).supplier_option_id, "OPT2")

    def test_padded_or_blank_sku_is_rejected(self):
        for sku in ("", "  ", " SKU", "SKU ", "A" * 151):
            with self.subTest(sku=repr(sku)):
                with self.assertRaises(BadRequestException):
                    self._save(sku)
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 0)


class WrongLinkPreventionTestCase(LinkTestCaseBase):

    def test_complex_bundles_and_bad_units_are_blocked_with_a_reason(self):
        for components in (
            [SupplierComponent(PRODUCT, "OPT1", 1), SupplierComponent(PRODUCT, "OPT2", 1)],
            [],
        ):
            with self.subTest(n=len(components)):
                with self.assertRaises(BadRequestException) as ctx:
                    self.links.save_link(
                        self.company_a.id, store_connection_id=self.store_a.id,
                        channel_sku="BUNDLE", purchase_connection_id=self.connection.id,
                        components=components, confirmed_by=1,
                    )
                self.assertTrue(str(ctx.exception.message if hasattr(ctx.exception, "message") else ctx.exception))
        for units in (0, -1, True, 1.5):
            with self.subTest(units=units):
                with self.assertRaises(BadRequestException):
                    self._save("BAD-UNITS", units=units)
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 0)

    def test_option_that_does_not_belong_to_the_product_is_never_saved(self):
        with self.assertRaises(ConflictException):
            self._save("HOMEZ-A", option="NOT-THERE")
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 0)

    def test_unverifiable_supplier_lookup_saves_nothing(self):
        self._set_supplier_options((), error=RuntimeError("timeout"))
        with self.assertRaises(ConflictException):
            self._save("HOMEZ-A")
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 0)

    def test_deleted_supplier_option_needs_review_and_is_not_substituted(self):
        self._save("HOMEZ-A", option="OPT1")
        task = self._order_task(sku="HOMEZ-A")
        self._set_supplier_options((_option("OPT2", "화이트"),))

        review = self._review(task, [{"id": "OPT2", "qty": 1}])

        self.assertEqual(self._link_row().status, SupplierOptionLinkStatus.NEEDS_REVIEW)
        self.assertTrue(any("사라졌" in r or "조회되지" in r for r in review["blocked_reasons"]))
        # 재검토도 (옵션이 돌아왔더라도) 자동 복귀하지 않는다 — 사용자가 다시 저장해야 한다.
        self._set_supplier_options(self.options)
        again = self._review(task, [{"id": "OPT1", "qty": 1}])
        self.assertEqual(again["supplier_link"]["state"], "NEEDS_REVIEW")
        self.assertTrue(again["blocked_reasons"])
        self.assertEqual(self._link_row().supplier_option_id, "OPT1")

        self._save("HOMEZ-A", option="OPT1")
        self.assertEqual(self._link_row().status, SupplierOptionLinkStatus.ACTIVE)

    def test_relabelled_supplier_option_needs_review(self):
        self._save("HOMEZ-A", option="OPT1")
        task = self._order_task(sku="HOMEZ-A")
        self._set_supplier_options((_option("OPT1", "블랙 2개입"), _option("OPT2", "화이트")))

        review = self._review(task, [{"id": "OPT1", "qty": 1}])

        self.assertEqual(self._link_row().status, SupplierOptionLinkStatus.NEEDS_REVIEW)
        self.assertTrue(any("옵션명" in r for r in review["blocked_reasons"]))

    def test_out_of_stock_supplier_option_blocks_but_keeps_the_link(self):
        self._save("HOMEZ-A", option="OPT1")
        task = self._order_task(sku="HOMEZ-A")
        self._set_supplier_options((_option("OPT1", "블랙", in_stock=False), _option("OPT2", "화이트")))

        review = self._review(task, [{"id": "OPT1", "qty": 1}])

        self.assertTrue(any("품절" in r for r in review["blocked_reasons"]))
        self.assertEqual(self._link_row().status, SupplierOptionLinkStatus.ACTIVE)

    def test_unverifiable_live_lookup_blocks_instead_of_trusting_the_link(self):
        self._save("HOMEZ-A", option="OPT1")
        task = self._order_task(sku="HOMEZ-A")
        self._set_supplier_options((), error=PurchaseChannelAdapterError("timeout"))

        review = self._review(task, [{"id": "OPT1", "qty": 1}])

        self.assertTrue(review["blocked_reasons"])
        self.assertEqual(self._link_row().status, SupplierOptionLinkStatus.ACTIVE)

    def test_changed_purchase_account_blocks_without_silent_substitution(self):
        self._save("HOMEZ-A", option="OPT1")
        other = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="다른 계정",
        )
        self.connection_service.save_credential(other.id, self.company_a.id, auth_key="other-jwt")
        row = self.db.get(type(self.connection), other.id)
        row.status = self.connection.status
        row.verified_at = datetime.utcnow()
        self.db.commit()
        task = self._order_task(sku="HOMEZ-A", connection=row)

        review = self._review(task, [{"id": "OPT1", "qty": 1}])

        self.assertTrue(any("매입 계정" in r for r in review["blocked_reasons"]))
        self.assertEqual(self._link_row().purchase_connection_id, self.connection.id)

    def test_disabled_link_is_not_used(self):
        link = self._save("HOMEZ-A", option="OPT1")
        task = self._order_task(sku="HOMEZ-A")
        self.links.disable_link(self.company_a.id, link.id, actor_user_id=1)

        review = self._review(task, [{"id": "OPT1", "qty": 1}])

        self.assertEqual(review["supplier_link"]["state"], "DISABLED")
        self.assertTrue(any("해제" in r for r in review["blocked_reasons"]))

    def test_link_is_scoped_to_the_store_and_company(self):
        self._save("HOMEZ-A", option="OPT1")
        # 같은 회사의 다른 쿠팡 스토어 — 같은 SKU 문자열이어도 연결이 없다.
        other_store = self._create_store(self.company_a, seller="SELLER-2")
        task = self._order_task(sku="HOMEZ-A", store=other_store, channel_order_id="CPG-S2")
        self.assertEqual(self.links.resolve_for_task(task).state, "NO_LINK")

        # 다른 회사는 A의 스토어·매입 계정으로 저장할 수 없고 A의 연결을 보지 못한다.
        with self.assertRaises(NotFoundException):
            self.links.save_link(
                self.company_b.id, store_connection_id=self.store_a.id,
                channel_sku="HOMEZ-A", purchase_connection_id=self.connection.id,
                components=[SupplierComponent(PRODUCT, "OPT1", 1)], confirmed_by=1,
            )
        with self.assertRaises(NotFoundException):
            self.links.save_link(
                self.company_b.id, store_connection_id=self.store_b.id,
                channel_sku="HOMEZ-A", purchase_connection_id=self.connection.id,
                components=[SupplierComponent(PRODUCT, "OPT1", 1)], confirmed_by=1,
            )
        self.assertEqual(self.links.list_links(self.company_b.id), [])
        link = self._link_row()
        with self.assertRaises(NotFoundException):
            self.links.disable_link(self.company_b.id, link.id, actor_user_id=9)
        self.assertEqual(self._link_row().status, SupplierOptionLinkStatus.ACTIVE)

    def test_order_with_ambiguous_store_is_not_auto_linked(self):
        self._save("HOMEZ-A", option="OPT1")
        task = self._order_task(sku="HOMEZ-A")
        item = self.db.get(type(self.db.get(type(task), task.id)), task.id)
        order_id = item.source_order_id
        self.db.add(OrderChannelFulfillment(
            company_id=self.company_a.id,
            store_connection_id=self._create_store(self.company_a, seller="SELLER-9").id,
            order_id=order_id, channel_order_id="CPG-1", shipment_box_id="BOX-2",
            raw_status="ACCEPT", ordered_at=NOW,
        ))
        self.db.commit()
        self.assertEqual(self.links.resolve_for_task(task).state, "STORE_UNRESOLVED")


class ApprovalServerValidationTestCase(LinkTestCaseBase):

    def test_saved_link_is_rechecked_on_the_server_at_approval(self):
        self._save("SET-3", option="OPT1", units=3)
        task = self._order_task(sku="SET-3", quantity=2)
        check = lambda **kw: self.links.approval_block_reason(  # noqa: E731
            task, **{"connection_id": self.connection.id, "product_code": PRODUCT, **kw},
        )

        self.assertIsNone(check(options=[{"id": "OPT1", "qty": 6}]))
        self.assertIn("반드시", check(options=None))
        self.assertIn("옵션·수량", check(options=[{"id": "OPT1", "qty": 2}]))
        self.assertIn("옵션·수량", check(options=[{"id": "OPT2", "qty": 6}]))
        self.assertIn("공급 상품", check(options=[{"id": "OPT1", "qty": 6}], product_code="CH9"))
        self.assertIn("계정", check(options=[{"id": "OPT1", "qty": 6}], connection_id=9999))

    def test_needs_review_link_cannot_be_approved(self):
        link = self._save("HOMEZ-A", option="OPT1")
        task = self._order_task(sku="HOMEZ-A")
        self.links._mark_needs_review(link, "테스트")
        self.assertIn(
            "재확인", self.links.approval_block_reason(
                task, connection_id=self.connection.id, product_code=PRODUCT,
                options=[{"id": "OPT1", "qty": 1}],
            ),
        )

    def test_manual_path_without_a_link_is_unchanged(self):
        task = self._order_task(sku="NO-LINK-SKU")
        self.assertIsNone(self.links.approval_block_reason(
            task, connection_id=self.connection.id, product_code="ANYTHING", options=None,
        ))
        review = self._review(task, [{"id": "OPT1", "qty": 1}])
        self.assertEqual(review["supplier_link"]["state"], "NO_LINK")
        self.assertFalse(review["quantity_mismatch_warning"])
        # 수량은 기존처럼 판매 수량과 직접 비교한다.
        mismatch = self._review(task, [{"id": "OPT1", "qty": 2}])
        self.assertTrue(mismatch["quantity_mismatch_warning"])

    def test_task_without_a_source_order_item_uses_the_manual_path(self):
        order = self._create_order(channel_order_id="CPG-X")
        task = self._create_task(key="manual:1", order=order, order_item=None)
        self.assertEqual(self.links.resolve_for_task(task).state, "NO_ORDER_ITEM")


class RegistrationResultAndReadinessTestCase(LinkTestCaseBase):

    ITEMS = [
        {"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": 111},
        {"externalVendorSku": "HOMEZ-WHITE", "vendorItemId": 222},
    ]

    def _attach(self, items, seller_product_id="SP-1"):
        return self.links.attach_coupang_identifiers(
            self.company_a.id, store_connection_id=self.store_a.id,
            seller_product_id=seller_product_id, items=items, actor_user_id=1,
        )

    def test_attach_matches_by_sku_only_even_when_the_response_is_reordered(self):
        self._save("HOMEZ-BLACK", option="OPT1")
        self._save("HOMEZ-WHITE", option="OPT2")

        result = self._attach(list(reversed(self.ITEMS)))

        self.assertEqual(result["state"], "COMPLETE")
        self.assertTrue(result["complete"])
        self.assertEqual(
            result["outcomes"], {"HOMEZ-BLACK": "ATTACHED", "HOMEZ-WHITE": "ATTACHED"},
        )
        self.assertEqual(self._link_row("HOMEZ-BLACK").coupang_vendor_item_id, "111")
        self.assertEqual(self._link_row("HOMEZ-WHITE").coupang_vendor_item_id, "222")
        self.assertEqual(self._link_row("HOMEZ-BLACK").coupang_seller_product_id, "SP-1")

    def test_attach_does_not_create_links_and_marks_unlinked_options_incomplete(self):
        self._save("HOMEZ-BLACK", option="OPT1")

        result = self._attach(self.ITEMS)

        self.assertEqual(
            result["outcomes"], {"HOMEZ-BLACK": "ATTACHED", "HOMEZ-WHITE": "NO_LINK"},
        )
        self.assertEqual(result["state"], "INCOMPLETE")
        self.assertFalse(result["complete"])
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 1)

    def test_attach_never_treats_a_missing_or_partial_response_as_complete(self):
        """서버가 확정한 옵션 목록(expected_skus) 중 조회 결과에 없거나 번호가 아직
        없는 옵션이 하나라도 있으면 준비 미완료다. 범위 밖 SKU는 부착하지 않는다."""

        self._save("HOMEZ-BLACK", option="OPT1")
        self._save("HOMEZ-WHITE", option="OPT2")
        self._save("HOMEZ-GRAY", option="OPT1", product="CH1234567")

        result = self.links.attach_coupang_identifiers(
            self.company_a.id, store_connection_id=self.store_a.id,
            seller_product_id="SP-1", actor_user_id=1,
            expected_skus=["HOMEZ-BLACK", "HOMEZ-WHITE", "HOMEZ-GRAY"],
            items=[
                {"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": 111},
                {"externalVendorSku": "HOMEZ-WHITE", "vendorItemId": None},  # 승인 전
                {"externalVendorSku": "OTHER-PRODUCT-SKU", "vendorItemId": 999},
                {"externalVendorSku": None, "vendorItemId": 555},
            ],
        )

        self.assertEqual(result["outcomes"], {
            "HOMEZ-BLACK": "ATTACHED", "HOMEZ-WHITE": "ID_NOT_ISSUED",
            "HOMEZ-GRAY": "MISSING_IN_RESPONSE",
        })
        self.assertFalse(result["complete"])
        self.assertEqual(result["response_skus_outside_scope"], ["OTHER-PRODUCT-SKU"])
        self.assertEqual(result["response_items_without_sku"], 1)
        self.assertIsNone(self._link_row("HOMEZ-WHITE").coupang_vendor_item_id)
        self.assertEqual(self._link_row("HOMEZ-WHITE").coupang_seller_product_id, "SP-1")

        # 승인 후 다시 조회하면 채워지고, 같은 값의 재실행은 멱등이다.
        rerun = self.links.attach_coupang_identifiers(
            self.company_a.id, store_connection_id=self.store_a.id,
            seller_product_id="SP-1", actor_user_id=1,
            expected_skus=["HOMEZ-BLACK", "HOMEZ-WHITE"],
            items=[
                {"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": "111"},
                {"externalVendorSku": "HOMEZ-WHITE", "vendorItemId": "222"},
            ],
        )
        self.assertEqual(rerun["outcomes"], {
            "HOMEZ-BLACK": "ALREADY_ATTACHED", "HOMEZ-WHITE": "ATTACHED",
        })
        self.assertTrue(rerun["complete"])

    def test_attach_refuses_ambiguous_responses_without_partial_writes(self):
        self._save("HOMEZ-BLACK", option="OPT1")
        for items in (
            [
                {"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": 1},
                {"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": 2},
            ],
            [
                {"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": 7},
                {"externalVendorSku": "HOMEZ-WHITE", "vendorItemId": 7},
            ],
        ):
            with self.subTest(items=items):
                result = self._attach(items)
                self.assertEqual(result["state"], "AMBIGUOUS_RESPONSE")
                self.assertFalse(result["complete"])
                self.assertEqual(result["outcomes"], {})
                self.assertIsNone(self._link_row("HOMEZ-BLACK").coupang_vendor_item_id)
                self.assertIsNone(self._link_row("HOMEZ-BLACK").coupang_seller_product_id)
        with self.assertRaises(BadRequestException):
            self._attach(self.ITEMS, seller_product_id=" ")

    def test_attach_conflict_never_overwrites_and_blocks_the_link(self):
        self._save("HOMEZ-BLACK", option="OPT1")
        self._attach([self.ITEMS[0]])
        self._attach([self.ITEMS[0]])  # 같은 값 재부착은 멱등

        for items, seller in (
            ([{"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": 999}], "SP-1"),
            ([self.ITEMS[0]], "SP-OTHER"),
        ):
            with self.subTest(items=items, seller=seller):
                # 각 반복은 이전 충돌로 NEEDS_REVIEW가 된 뒤이므로 다시 ACTIVE로 되돌려 시작한다.
                self._save("HOMEZ-BLACK", option="OPT1")
                result = self._attach(items, seller_product_id=seller)
                self.assertEqual(result["outcomes"], {"HOMEZ-BLACK": "CONFLICT"})
                self.assertFalse(result["complete"])
                row = self._link_row("HOMEZ-BLACK")
                self.assertEqual(row.coupang_vendor_item_id, "111")
                self.assertEqual(row.coupang_seller_product_id, "SP-1")
                self.assertEqual(row.status, SupplierOptionLinkStatus.NEEDS_REVIEW)
                self.assertIn("충돌", row.status_reason)

    def test_a_lookup_without_the_number_is_missing_information_not_a_conflict(self):
        """이미 확인된 옵션번호가 있는 옵션에 대해 이번 조회가 번호를 돌려주지 않아도(승인 대기
        등) 서로 다른 두 값이 생긴 것이 아니므로 연결을 막지 않고 저장값을 유지한다.
        다만 이번 조회로는 확인되지 않았으므로 부착 완료(complete)는 아니다."""

        self._save("HOMEZ-BLACK", option="OPT1")
        self._attach([self.ITEMS[0]])

        result = self._attach([{"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": None}])

        self.assertEqual(result["outcomes"], {"HOMEZ-BLACK": "ID_NOT_RETURNED"})
        self.assertFalse(result["complete"])
        row = self._link_row("HOMEZ-BLACK")
        self.assertEqual(row.status, SupplierOptionLinkStatus.ACTIVE)
        self.assertEqual(row.coupang_vendor_item_id, "111")

    def test_same_option_number_on_two_links_is_a_conflict_for_both(self):
        self._save("HOMEZ-BLACK", option="OPT1")
        self._save("HOMEZ-WHITE", option="OPT2")
        self._attach([self.ITEMS[0]])

        result = self._attach([{"externalVendorSku": "HOMEZ-WHITE", "vendorItemId": 111}])

        self.assertEqual(result["outcomes"], {"HOMEZ-WHITE": "CONFLICT"})
        self.assertEqual(self._link_row("HOMEZ-BLACK").status, "NEEDS_REVIEW")
        self.assertEqual(self._link_row("HOMEZ-WHITE").status, "NEEDS_REVIEW")
        self.assertIsNone(self._link_row("HOMEZ-WHITE").coupang_vendor_item_id)

    def test_conflict_is_recoverable_only_by_the_explicit_clear_and_reconfirm(self):
        link = self._save("HOMEZ-BLACK", option="OPT1")
        self._attach([self.ITEMS[0]])
        self._attach([{"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": 999}])
        self.assertEqual(self._link_row("HOMEZ-BLACK").status, "NEEDS_REVIEW")

        cleared = self.links.clear_coupang_identifiers(
            self.company_a.id, link.id, actor_user_id=1,
        )
        self.assertIsNone(cleared.coupang_vendor_item_id)
        self.assertIsNone(cleared.coupang_seller_product_id)
        self.assertEqual(cleared.status, "NEEDS_REVIEW")
        self.assertEqual(cleared.supplier_option_id, "OPT1")  # 공급 대응은 그대로

        self._attach([{"externalVendorSku": "HOMEZ-BLACK", "vendorItemId": 999}])
        self._save("HOMEZ-BLACK", option="OPT1")
        row = self._link_row("HOMEZ-BLACK")
        self.assertEqual((row.status, row.coupang_vendor_item_id), ("ACTIVE", "999"))

        with self.assertRaises(NotFoundException):
            self.links.clear_coupang_identifiers(self.company_b.id, link.id, actor_user_id=9)

    def test_attach_failure_leaves_nothing_attached_and_a_rerun_recovers(self):
        self._save("HOMEZ-BLACK", option="OPT1")
        self._save("HOMEZ-WHITE", option="OPT2")
        with mock.patch(
            "app.domains.purchase_task.supplier_option_link_service.write_audit_log",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaises(RuntimeError):
                self._attach(self.ITEMS)
        self.db.rollback()
        self.assertIsNone(self._link_row("HOMEZ-BLACK").coupang_vendor_item_id)
        self.assertIsNone(self._link_row("HOMEZ-WHITE").coupang_vendor_item_id)

        result = self._attach(self.ITEMS)
        self.assertTrue(result["complete"])

    def test_readiness_requires_active_links_and_confirmed_option_numbers(self):
        skus = ["HOMEZ-BLACK", "HOMEZ-WHITE"]
        kw = dict(store_connection_id=self.store_a.id, skus=skus, scope_confirmed=True)

        self.assertEqual(self.links.readiness(self.company_a.id, **kw)["reason"], "LINK_MISSING")
        self._save("HOMEZ-BLACK", option="OPT1")
        partial = self.links.readiness(self.company_a.id, **kw)
        self.assertFalse(partial["all_ready"])
        self.assertEqual(
            partial["per_sku"], {"HOMEZ-BLACK": "ACTIVE", "HOMEZ-WHITE": "MISSING"},
        )

        white = self._save("HOMEZ-WHITE", option="OPT2")
        linked_only = self.links.readiness(self.company_a.id, **kw)
        self.assertFalse(linked_only["all_ready"])          # 쿠팡 옵션번호 미확인
        self.assertEqual(linked_only["reason"], "IDS_UNCONFIRMED")
        self.assertTrue(linked_only["selected_ready"] is False)

        self._attach(self.ITEMS)
        done = self.links.readiness(self.company_a.id, **kw)
        self.assertTrue(done["all_ready"])
        self.assertIsNone(done["reason"])

        self.links._mark_needs_review(white, "테스트")
        self.assertFalse(self.links.readiness(self.company_a.id, **kw)["all_ready"])
        self.assertFalse(self.links.readiness(
            self.company_a.id, store_connection_id=self.store_a.id, skus=[],
            scope_confirmed=True,
        )["all_ready"])

    def test_client_supplied_option_list_can_never_be_all_ready(self):
        """클라이언트가 일부 SKU만 보내 전체 준비 완료로 오인시킬 수 없다 — 서버가
        옵션 목록을 확정하지 않은 기본 경로는 모든 옵션이 완벽해도 all_ready=False."""

        self._save("HOMEZ-BLACK", option="OPT1")
        self._attach([self.ITEMS[0]])

        subset = self.links.readiness(
            self.company_a.id, store_connection_id=self.store_a.id, skus=["HOMEZ-BLACK"],
        )

        self.assertEqual(subset["scope"], "CLIENT_SUPPLIED")
        self.assertTrue(subset["selected_ready"])   # 보낸 범위는 확인됨
        self.assertFalse(subset["all_ready"])       # 그러나 전체 준비 완료는 아님
        self.assertFalse(subset["ready"])
        self.assertEqual(subset["reason"], "SCOPE_UNCONFIRMED")

    def test_unresolved_sales_account_never_reports_anything_ready(self):
        result = self.links.readiness(
            self.company_a.id, store_connection_id=None, skus=["HOMEZ-BLACK"],
            scope_confirmed=True,
        )
        self.assertFalse(result["all_ready"])
        self.assertEqual(result["per_option"][0]["link_state"], "MISSING")

    def test_internal_save_failure_leaves_no_link_and_readiness_stays_false(self):
        with mock.patch(
            "app.domains.purchase_task.supplier_option_link_service.write_audit_log",
            side_effect=RuntimeError("disk full"),
        ):
            with self.assertRaises(RuntimeError):
                self._save("HOMEZ-A")
        self.db.rollback()
        self.assertIsNone(self._link_row("HOMEZ-A"))
        self.assertFalse(self.links.readiness(
            self.company_a.id, store_connection_id=self.store_a.id, skus=["HOMEZ-A"],
        )["ready"])


class ConcurrencyAndPersistenceTestCase(LinkTestCaseBase):

    def test_lost_race_with_the_same_values_is_idempotent(self):
        """저장 직전에 다른 요청이 같은 연결을 먼저 만든 경우 — UNIQUE가 중복을
        막고 진 쪽은 먼저 저장된 행을 그대로 돌려준다."""

        real_get = self.links.get_link
        calls = {"n": 0}

        def racing_get(company_id, store_id, sku):
            calls["n"] += 1
            if calls["n"] == 1:
                other = self.SessionLocal()
                try:
                    other.add(SupplierOptionLink(
                        company_id=company_id, store_connection_id=store_id,
                        channel_sku=sku, purchase_connection_id=self.connection.id,
                        supplier_product_code=PRODUCT, supplier_option_id="OPT1",
                        units_per_sale=1, status="ACTIVE", version=1,
                        confirmed_by=2, confirmed_at=datetime.utcnow(),
                    ))
                    other.commit()
                finally:
                    other.close()
                return None  # 이 요청은 "아직 없다"고 봤다
            return real_get(company_id, store_id, sku)

        with mock.patch.object(self.links, "get_link", side_effect=racing_get):
            link = self._save("HOMEZ-A", option="OPT1")

        self.assertEqual(link.supplier_option_id, "OPT1")
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 1)

    def test_lost_race_with_different_values_is_a_conflict_not_a_silent_overwrite(self):
        real_get = self.links.get_link
        calls = {"n": 0}

        def racing_get(company_id, store_id, sku):
            calls["n"] += 1
            if calls["n"] == 1:
                other = self.SessionLocal()
                try:
                    other.add(SupplierOptionLink(
                        company_id=company_id, store_connection_id=store_id,
                        channel_sku=sku, purchase_connection_id=self.connection.id,
                        supplier_product_code=PRODUCT, supplier_option_id="OPT2",
                        units_per_sale=1, status="ACTIVE", version=1,
                        confirmed_by=2, confirmed_at=datetime.utcnow(),
                    ))
                    other.commit()
                finally:
                    other.close()
                return None
            return real_get(company_id, store_id, sku)

        with mock.patch.object(self.links, "get_link", side_effect=racing_get):
            with self.assertRaises(ConflictException):
                self._save("HOMEZ-A", option="OPT1")
        self.assertEqual(self._link_row().supplier_option_id, "OPT2")

    def test_stale_replace_loses_to_the_version_check(self):
        link = self._save("HOMEZ-A", option="OPT1")
        stale = self.db.get(SupplierOptionLink, link.id)
        other = self.SessionLocal()
        try:
            other.execute(text(
                "UPDATE supplier_option_links SET version = 2, "
                "supplier_option_id = 'OPT2' WHERE id = :i"), {"i": link.id})
            other.commit()
        finally:
            other.close()

        with self.assertRaises(ConflictException):
            self.links._reconcile_existing(
                stale, self.company_a.id, self.connection.id, PRODUCT, "OPT1", 5,
                "블랙", 1, True, datetime.utcnow(),
            )
        self.db.expire_all()
        self.assertEqual(self.db.get(SupplierOptionLink, link.id).supplier_option_id, "OPT2")

    def test_parallel_saves_of_the_same_link_produce_one_row_and_no_integrity_error(self):
        barrier = threading.Barrier(4)
        outcomes, errors = [], []
        # 다른 스레드의 Session이 소유한 ORM 객체는 읽지 않는다 — 원시값만 넘긴다.
        company_id = self.company_a.id
        store_id = self.store_a.id
        connection_id = self.connection.id

        def worker():
            session = self.SessionLocal()
            try:
                service = SupplierOptionLinkService(
                    session, connection_service=self.connection_service.__class__(
                        session, credential_store=self.credential_store,
                    ),
                )
                barrier.wait(timeout=10)
                link = service.save_link(
                    company_id, store_connection_id=store_id,
                    channel_sku="HOMEZ-A", purchase_connection_id=connection_id,
                    components=[SupplierComponent(PRODUCT, "OPT1", 1)], confirmed_by=1,
                )
                outcomes.append((link.supplier_option_id, link.units_per_sale))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(any(t.is_alive() for t in threads))
        for exc in errors:
            self.assertIsInstance(exc, (ConflictException,), repr(exc))
        self.assertGreaterEqual(len(outcomes), 1)
        self.assertEqual(set(outcomes), {("OPT1", 1)})
        self.db.expire_all()
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 1)

    def test_link_survives_a_restart_on_a_file_database(self):
        self._save("HOMEZ-A", option="OPT2", units=2)
        company_id, store_id = self.company_a.id, self.store_a.id
        self.db.close()
        self.engine.dispose()

        engine = create_engine(f"sqlite:///{self.db_path}")
        try:
            session = sessionmaker(bind=engine)()
            try:
                restored = SupplierOptionLinkService(session).get_link(
                    company_id, store_id, "HOMEZ-A",
                )
                self.assertEqual(
                    (restored.supplier_product_code, restored.supplier_option_id,
                     restored.units_per_sale, restored.status, restored.version),
                    (PRODUCT, "OPT2", 2, "ACTIVE", 1),
                )
            finally:
                session.close()
        finally:
            engine.dispose()
        # tearDown이 다시 닫아도 되도록 세션을 되돌려 놓는다.
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.db = sessionmaker(bind=self.engine)()


class UnmigratedDatabaseFallbackTestCase(LinkTestCaseBase):
    """Migration이 아직 적용되지 않은 DB + 최신 코드 — 기존 수동 경로가 그대로
    동작해야 한다(restricted-mode 503로 번지지 않는다)."""

    def setUp(self):
        super().setUp()
        SupplierOptionLink.__table__.drop(self.engine)

    def test_store_is_reported_unavailable_and_review_still_works(self):
        self.assertFalse(SupplierOptionLinkService.is_store_available(self.db))
        task = self._order_task(sku="HOMEZ-A")

        review = self._review(task, [{"id": "OPT1", "qty": 1}])

        self.assertEqual(review["supplier_link"]["state"], "UNAVAILABLE")
        self.assertFalse(review["quantity_mismatch_warning"])
        self.assertEqual(review["product"]["estimated_item_amount"], 25000)

    def test_writes_are_refused_and_reads_are_safe(self):
        with self.assertRaises(ConflictException):
            self._save("HOMEZ-A")
        self.assertEqual(
            self.links.readiness(
                self.company_a.id, store_connection_id=self.store_a.id, skus=["X"],
            )["reason"], "STORE_UNAVAILABLE",
        )
        task = self._order_task(sku="HOMEZ-A")
        self.assertIsNone(self.links.approval_block_reason(
            task, connection_id=self.connection.id, product_code=PRODUCT, options=None,
        ))


class RouterProtectionTestCase(unittest.TestCase):

    def test_every_link_route_requires_admin_guard(self):
        from app.core.guard import AdminGuard
        from app.domains.purchase_task.supplier_option_link_router import router

        self.assertTrue(router.routes)
        for route in router.routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(AdminGuard, calls, route.path)


class SupplierOptionLinkMigrationTestCase(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        for p in (self.path, self.path + ".model"):
            if os.path.exists(p):
                os.remove(p)

    def test_migration_matches_model_table_and_constraints(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            migration_columns = {
                r[1]: (r[2], r[3]) for r in conn.execute(
                    "PRAGMA table_info(supplier_option_links)")
            }
            migration_indexes = {
                r[1] for r in conn.execute("PRAGMA index_list(supplier_option_links)")
            }
        finally:
            conn.close()

        engine = create_engine(f"sqlite:///{self.path}.model")
        try:
            Base.metadata.create_all(engine, tables=[SupplierOptionLink.__table__])
            model = sqlite3.connect(self.path + ".model")
            try:
                model_columns = {
                    r[1]: (r[2], r[3]) for r in model.execute(
                        "PRAGMA table_info(supplier_option_links)")
                }
                model_indexes = {
                    r[1] for r in model.execute("PRAGMA index_list(supplier_option_links)")
                }
            finally:
                model.close()
        finally:
            engine.dispose()
        self.assertEqual(migration_columns, model_columns)
        self.assertEqual(migration_indexes, model_indexes)

    def test_unique_scope_and_units_check_are_enforced_by_the_database(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            insert = (
                "INSERT INTO supplier_option_links (company_id, store_connection_id, "
                "channel_sku, purchase_connection_id, supplier_product_code, "
                "supplier_option_id, units_per_sale, status, version, confirmed_by, "
                "confirmed_at, created_at, updated_at) VALUES "
                "(1, 1, ?, 1, 'CH1', 'OPT1', ?, 'ACTIVE', 1, 1, "
                "'2026-09-21', '2026-09-21', '2026-09-21')"
            )
            conn.execute(insert, ("SKU", 1))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(insert, ("SKU", 1))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(insert, ("SKU-2", 0))
        finally:
            conn.close()

    def test_migration_rejects_reapplication(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
