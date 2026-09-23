"""
=========================================================
Homez OS

File : tests/test_supplier_option_identifier_paths.py

2026-09-21 옵션 연결 — 쿠팡 식별자 두 개(판매자 SKU `externalVendorSku` / 옵션번호
`vendorItemId`)를 **같은 문자열 공간에서 무조건 비교하지 않는** 해석 경로 테스트.

배경(실제 코드): 주문 수집은 `channel_sku = externalVendorSkuCode.strip() or vendorItemId`
로 두 식별자를 하나로 합쳐 `OrderItem.channel_sku`에 넣는다(coupang_normalizer.py).
원본 `vendorItemId`는 `UnresolvedOrderItem.vendor_item_id`에 따로 남고
`resolved_order_item_id`로 OrderItem과 이어진다. 공식 문서상 `externalVendorSkuCode`는
optional이므로 SKU 없이 들어오는 주문도 정상이다.

실제 쿠팡·온채널 호출 없음(가짜 Adapter, InMemoryCredentialStore).
=========================================================
"""

from decimal import Decimal

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.purchase_task.model import SupplierOptionLink
from app.domains.purchase_task.supplier_option_link_service import (
    SupplierOptionLinkService,
)
from app.domains.purchase_task.supplier_option_link_service import resolution_to_dict
from sqlalchemy.exc import IntegrityError
from tests.test_purchase_task_order_submission_review import NOW
from tests.test_supplier_option_link_service import LinkTestCaseBase
from tests.test_supplier_option_link_service import PRODUCT


class IdentifierPathTestCaseBase(LinkTestCaseBase):

    def _vendor_order_task(
        self, *, sku, vendor, quantity=1, store=None, company=None,
        channel_order_id="CPG-V1", connection=None, sku_absent=False,
    ):
        """수집 단계와 같은 모양으로 주문을 만든다.
        sku_absent=True  → 주문에 판매자 SKU가 없어 channel_sku == vendorItemId(합쳐진 값)
        vendor=None      → 수집 원본(UnresolvedOrderItem)이 없는 옛 주문"""

        company = company or self.company_a
        store = store or self.store_a
        order = self._create_order(company=company, channel_order_id=channel_order_id)
        item = self._create_order_item(order, quantity=quantity)
        item.channel_sku = vendor if sku_absent else sku
        fulfillment = OrderChannelFulfillment(
            company_id=company.id, store_connection_id=store.id, order_id=order.id,
            channel_order_id=channel_order_id, shipment_box_id="BOX-V",
            raw_status="ACCEPT", ordered_at=NOW,
        )
        self.db.add(fulfillment)
        self.db.commit()
        if vendor is not None:
            self.db.add(UnresolvedOrderItem(
                company_id=company.id, fulfillment_id=fulfillment.id,
                channel_item_id="001", vendor_item_id=vendor,
                channel_sku=item.channel_sku, product_name_snapshot="옵션",
                quantity=quantity, unit_price=Decimal("1000"),
                order_price=Decimal("1000"), currency_code="KRW",
                status="RESOLVED", resolved_order_item_id=item.id,
            ))
            self.db.commit()
        task = self._create_task(
            key=f"vendor:{channel_order_id}", order=order, order_item=item,
            quantity=quantity,
        )
        self.service.assign_channel_connection(
            task.id, company.id, (connection or self.connection).id,
        )
        return task

    def _attach_vendor(self, sku, vendor, seller="SP-1"):
        self.links.attach_coupang_identifiers(
            self.company_a.id, store_connection_id=self.store_a.id,
            seller_product_id=seller, actor_user_id=1,
            items=[{"externalVendorSku": sku, "vendorItemId": vendor}],
        )


class ResolutionPathsTestCase(IdentifierPathTestCaseBase):

    def test_seller_sku_path_finds_the_link_and_reports_how(self):
        self._save("HOMEZ-A", option="OPT1")
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9001")

        resolution = self.links.resolve_for_task(task)

        self.assertEqual(resolution.state, "ACTIVE")
        self.assertEqual(resolution.linked_by, "SKU")
        self.assertEqual(resolution.seller_sku_state, "PRESENT")
        self.assertEqual(resolution.vendor_item_id, "9001")

    def test_both_identifiers_agreeing_is_reported_as_both(self):
        self._save("HOMEZ-A", option="OPT1")
        self._attach_vendor("HOMEZ-A", "9001")
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9001")
        self.assertEqual(self.links.resolve_for_task(task).linked_by, "BOTH")

    def test_order_without_seller_sku_is_found_by_the_confirmed_option_number(self):
        """판매자 SKU 없이 들어온 주문(channel_sku가 옵션번호와 같은 문자열)은 등록
        결과에서 확인된 vendorItemId 대응으로 찾는다."""

        self._save("HOMEZ-A", option="OPT1", units=2)
        self._attach_vendor("HOMEZ-A", "9001")
        task = self._vendor_order_task(
            sku=None, vendor="9001", quantity=3, sku_absent=True,
        )

        resolution = self.links.resolve_for_task(task)

        self.assertEqual(resolution.state, "ACTIVE")
        self.assertEqual(resolution.linked_by, "VENDOR_ITEM_ID")
        self.assertEqual(resolution.seller_sku_state, "ABSENT_OR_SAME")
        self.assertEqual(resolution.expected_options, [{"id": "OPT1", "qty": 6}])

        review = self._review(task, [{"id": "OPT1", "qty": 6}])
        self.assertEqual(review["supplier_link"]["state"], "ACTIVE")
        self.assertFalse(review["quantity_mismatch_warning"])
        wrong = self._review(task, [{"id": "OPT2", "qty": 6}])
        self.assertTrue(any("연결과 다릅니다" in r for r in wrong["blocked_reasons"]))

    def test_sku_less_order_with_unconfirmed_option_number_is_not_guessed(self):
        self._save("HOMEZ-A", option="OPT1")          # 옵션번호는 아직 미확인
        task = self._vendor_order_task(sku=None, vendor="9001", sku_absent=True)

        resolution = self.links.resolve_for_task(task)

        self.assertEqual(resolution.state, "NO_LINK")
        self.assertIsNone(resolution.link)
        self.assertIn("옵션번호", resolution.detail)
        # 검토는 수동 입력 경로로 남고, 저장된 연결로 임의 발주 구성을 만들지 않는다.
        review = self._review(task, [{"id": "OPT1", "qty": 1}])
        self.assertEqual(review["supplier_link"]["expected_options"], [])

    def test_sku_and_option_number_pointing_at_different_links_is_blocked(self):
        self._save("HOMEZ-A", option="OPT1")
        self._save("HOMEZ-B", option="OPT2")
        self._attach_vendor("HOMEZ-B", "9002")
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9002")

        resolution = self.links.resolve_for_task(task)

        self.assertEqual(resolution.state, "IDENTIFIER_CONFLICT")
        self.assertIsNone(resolution.link)
        self.assertEqual(resolution.expected_options, [])
        review = self._review(task, [{"id": "OPT1", "qty": 1}])
        self.assertTrue(any("식별자" in r for r in review["blocked_reasons"]), review["blocked_reasons"])
        self.assertIn("식별자", self.links.approval_block_reason(
            task, connection_id=self.connection.id, product_code=PRODUCT,
            options=[{"id": "OPT1", "qty": 1}],
        ))

    def test_stored_option_number_that_differs_from_the_order_is_blocked(self):
        self._save("HOMEZ-A", option="OPT1")
        self._attach_vendor("HOMEZ-A", "9001")
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="7777")

        self.assertEqual(
            self.links.resolve_for_task(task).state, "IDENTIFIER_CONFLICT",
        )

    def test_order_sku_differing_from_the_link_that_owns_the_option_number_is_blocked(self):
        self._save("HOMEZ-B", option="OPT2")
        self._attach_vendor("HOMEZ-B", "9002")
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9002")   # A는 연결 없음

        resolution = self.links.resolve_for_task(task)

        self.assertEqual(resolution.state, "IDENTIFIER_CONFLICT")
        self.assertIsNone(resolution.link)

    def test_a_seller_sku_that_equals_another_options_number_is_never_mixed(self):
        """다른 옵션의 SKU가 우연히 이 주문의 옵션번호와 같은 문자열이어도, SKU 없이 들어온
        주문(channel_sku == vendorItemId)은 그 SKU 연결로 해석하지 않고 차단한다."""

        self._save("55501", option="OPT1")            # 판매자 SKU가 숫자 문자열
        self._save("HOMEZ-B", option="OPT2")
        self._attach_vendor("HOMEZ-B", "55501")       # 다른 옵션의 옵션번호가 같은 문자열
        sku_less = self._vendor_order_task(
            sku=None, vendor="55501", sku_absent=True, channel_order_id="CPG-V2",
        )

        resolution = self.links.resolve_for_task(sku_less)

        self.assertEqual(resolution.state, "IDENTIFIER_CONFLICT")

    def test_equal_string_sku_link_alone_is_not_trusted_as_an_option_number(self):
        self._save("55501", option="OPT1")            # SKU 연결만 있고 옵션번호는 미확인
        task = self._vendor_order_task(sku=None, vendor="55501", sku_absent=True)

        resolution = self.links.resolve_for_task(task)

        self.assertEqual(resolution.state, "IDENTIFIER_CONFLICT")
        self.assertIsNone(resolution.link)

    def test_order_without_collection_source_falls_back_to_the_sku_path(self):
        self._save("HOMEZ-A", option="OPT1")
        task = self._vendor_order_task(sku="HOMEZ-A", vendor=None)

        resolution = self.links.resolve_for_task(task)

        self.assertEqual(resolution.state, "ACTIVE")
        self.assertEqual(resolution.seller_sku_state, "UNKNOWN")
        self.assertEqual(resolution.linked_by, "SKU")

    def test_option_number_lookup_is_scoped_to_company_and_sales_account(self):
        self._save("HOMEZ-A", option="OPT1")
        self._attach_vendor("HOMEZ-A", "9001")
        other_store = self._create_store(self.company_a, seller="SELLER-2")
        sku_less_other_store = self._vendor_order_task(
            sku=None, vendor="9001", sku_absent=True, store=other_store,
            channel_order_id="CPG-V3",
        )
        self.assertEqual(self.links.resolve_for_task(sku_less_other_store).state, "NO_LINK")

        # 다른 회사가 같은 옵션번호로 자기 연결을 가져도 서로 보이지 않는다.
        self.assertIsNone(
            self.links.get_link_by_vendor_item(self.company_b.id, self.store_a.id, "9001"),
        )
        self.assertIsNone(
            self.links.get_link_by_vendor_item(self.company_a.id, self.store_b.id, "9001"),
        )

    def test_the_database_itself_refuses_a_duplicate_option_number_per_sales_account(self):
        self._save("HOMEZ-A", option="OPT1")
        self._save("HOMEZ-B", option="OPT2")
        first = self._link_row("HOMEZ-A")
        second = self._link_row("HOMEZ-B")
        first.coupang_vendor_item_id = "9001"
        self.db.commit()
        second.coupang_vendor_item_id = "9001"
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()
        # NULL(미확인)은 여러 개 허용 — 아직 확인 전인 옵션이 여러 개일 수 있다.
        self.assertEqual(
            self.db.query(SupplierOptionLink)
            .filter(SupplierOptionLink.coupang_vendor_item_id.is_(None)).count(), 1,
        )

    def test_api_payload_carries_no_secrets_or_customer_information(self):
        self._save("HOMEZ-A", option="OPT1")
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9001")
        payload = resolution_to_dict(self.links.resolve_for_task(task))
        self.assertEqual(set(payload), {
            "state", "detail", "link_id", "expected_product_code", "expected_options",
            "units_per_sale", "channel_sku", "order_vendor_item_id", "seller_sku_state",
            "linked_by", "supplier_option_name", "status_reason", "coupang_ids_confirmed",
        })
        self.assertNotIn("홍길동", repr(payload))


class SaveForTaskTestCase(IdentifierPathTestCaseBase):

    def _save_task(self, task, *, option="OPT1", units=1, replace=False, product=PRODUCT):
        return self.links.save_link_for_task(
            task, supplier_product_code=product, supplier_option_id=option,
            units=units, confirmed_by=1, replace=replace,
        )

    def test_first_confirmation_saves_and_the_next_order_reuses_it(self):
        first = self._vendor_order_task(sku="HOMEZ-A", vendor="9001", channel_order_id="CPG-1")
        link, resolution = self._save_task(first, units=2)

        self.assertEqual(resolution.state, "ACTIVE")
        self.assertEqual(link.channel_sku, "HOMEZ-A")
        self.assertEqual(link.purchase_connection_id, self.connection.id)
        self.assertEqual(link.coupang_vendor_item_id, "9001")   # 주문 원본의 확인된 번호
        self.db.expire_all()

        # 다음 주문: 같은 옵션이 SKU 없이 들어와도 저장된 옵션번호로 찾는다.
        second = self._vendor_order_task(
            sku=None, vendor="9001", quantity=3, sku_absent=True, channel_order_id="CPG-2",
        )
        again = self.links.resolve_for_task(second)
        self.assertEqual((again.state, again.linked_by), ("ACTIVE", "VENDOR_ITEM_ID"))
        self.assertEqual(again.expected_options, [{"id": "OPT1", "qty": 6}])

    def test_server_derives_store_and_sku_from_the_task_not_from_the_client(self):
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9001")
        link, _ = self._save_task(task)
        self.assertEqual(link.store_connection_id, self.store_a.id)
        self.assertEqual(link.channel_sku, "HOMEZ-A")

    def test_sku_less_order_cannot_create_a_link_here(self):
        task = self._vendor_order_task(sku=None, vendor="9001", sku_absent=True)
        with self.assertRaises(BadRequestException) as ctx:
            self._save_task(task)
        self.assertIn("SKU", str(ctx.exception))
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 0)

    def test_identifier_conflict_and_unresolved_states_are_not_saved_over(self):
        self._save("HOMEZ-B", option="OPT2")
        self._attach_vendor("HOMEZ-B", "9002")
        conflict = self._vendor_order_task(sku="HOMEZ-A", vendor="9002")
        with self.assertRaises(ConflictException):
            self._save_task(conflict)
        self.assertIsNone(self._link_row("HOMEZ-A"))

    def test_changing_an_active_link_requires_an_explicit_replace(self):
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9001")
        self._save_task(task, option="OPT1")
        with self.assertRaises(ConflictException):
            self._save_task(task, option="OPT2")
        self.assertEqual(self._link_row("HOMEZ-A").supplier_option_id, "OPT1")
        link, resolution = self._save_task(task, option="OPT2", replace=True)
        self.assertEqual((link.supplier_option_id, link.version), ("OPT2", 2))
        self.assertEqual(resolution.expected_options[0]["id"], "OPT2")

    def test_reconfirming_a_needs_review_link_is_the_explicit_action(self):
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9001")
        link, _ = self._save_task(task)
        self.links._mark_needs_review(link, "공급 옵션명이 바뀜")
        self.assertEqual(self.links.resolve_for_task(task).state, "NEEDS_REVIEW")

        _, resolution = self._save_task(task)

        self.assertEqual(resolution.state, "ACTIVE")

    def test_task_without_a_purchase_account_is_refused(self):
        order = self._create_order(channel_order_id="CPG-NC")
        item = self._create_order_item(order)
        item.channel_sku = "HOMEZ-A"
        self.db.add(OrderChannelFulfillment(
            company_id=self.company_a.id, store_connection_id=self.store_a.id,
            order_id=order.id, channel_order_id="CPG-NC", shipment_box_id="B",
            raw_status="ACCEPT", ordered_at=NOW,
        ))
        self.db.commit()
        task = self._create_task(key="nc:1", order=order, order_item=item)
        with self.assertRaises(BadRequestException):
            self._save_task(task)

    def test_unverifiable_supplier_option_saves_nothing(self):
        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9001")
        with self.assertRaises(ConflictException):
            self._save_task(task, option="NOT-THERE")
        self.assertEqual(self.db.query(SupplierOptionLink).count(), 0)

    def test_saving_a_link_never_changes_an_already_frozen_approval(self):
        """이전 승인·발주 기록은 별도 테이블에 동결돼 있고, 연결 저장은 그 테이블을
        읽지도 쓰지도 않는다."""

        from app.domains.purchase_task.model import PurchaseOrderApproval

        task = self._vendor_order_task(sku="HOMEZ-A", vendor="9001")
        self._save_task(task, option="OPT1")
        approval = PurchaseOrderApproval(
            company_id=self.company_a.id, purchase_task_id=task.id,
            connection_id=self.connection.id, product_code=PRODUCT,
            options_snapshot_json='[{"id": "OPT1", "qty": 1}]',
        )
        self.db.add(approval)
        self.db.commit()
        approval_id = approval.id

        self._save_task(task, option="OPT2", replace=True)

        self.db.expire_all()
        frozen = self.db.get(PurchaseOrderApproval, approval_id)
        self.assertEqual(frozen.options_snapshot_json, '[{"id": "OPT1", "qty": 1}]')


class ConcurrentAttachTestCase(IdentifierPathTestCaseBase):

    def test_parallel_attach_of_the_same_result_is_idempotent(self):
        """두 요청이 같은 등록 결과를 동시에 부착해도 중복·유령 행 없이 같은 값으로 수렴한다.
        스레드는 다른 Session 소유 ORM 객체를 읽지 않는다(원시값만 넘긴다)."""

        import threading

        self._save("HOMEZ-A", option="OPT1")
        self._save("HOMEZ-B", option="OPT2")
        company_id, store_id = self.company_a.id, self.store_a.id
        items = [
            {"externalVendorSku": "HOMEZ-A", "vendorItemId": 111},
            {"externalVendorSku": "HOMEZ-B", "vendorItemId": 222},
        ]
        barrier = threading.Barrier(3)
        results, errors = [], []

        def worker():
            session = self.SessionLocal()
            try:
                service = SupplierOptionLinkService(session)
                barrier.wait(timeout=10)
                results.append(service.attach_coupang_identifiers(
                    company_id, store_connection_id=store_id, seller_product_id="SP-1",
                    items=items, actor_user_id=1,
                ))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(any(t.is_alive() for t in threads))
        for exc in errors:
            self.assertIsInstance(exc, ConflictException, repr(exc))
        self.assertGreaterEqual(len(results), 1)
        self.db.expire_all()
        rows = {l.channel_sku: (l.coupang_vendor_item_id, l.coupang_seller_product_id, l.status)
                for l in self.db.query(SupplierOptionLink).all()}
        self.assertEqual(rows, {
            "HOMEZ-A": ("111", "SP-1", "ACTIVE"), "HOMEZ-B": ("222", "SP-1", "ACTIVE"),
        })
