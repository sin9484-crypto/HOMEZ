"""
=========================================================
Homez OS

File : tests/test_purchase_task_order_submission_review.py

2026-09-09 후속("발주 전 최종 검토 화면") —
PurchaseTaskService.build_order_submission_review() 격리 테스트.
쿠팡 주문(Order/OrderItem) → PurchaseTask → 온채널 연결 계정 →
온채널 상품·옵션(가짜 Adapter) → 수량·가격·배송비 → 수취정보까지
연결한 읽기 전용 검토 화면의 데이터 조립을 검증한다.

이 파일은 PurchaseOrderSubmissionService.submit_order()를 어디서도
호출하지 않는다(그 사실 자체가 여러 테스트에서 명시적으로 증명된다).
실제 네트워크·실제 Windows Credential Manager는 전혀 건드리지
않는다 — WindowsCredentialStore()를 InMemoryCredentialStore로
바꿔치기해서 PurchaseTaskService가 내부적으로 만드는
PurchaseChannelConnectionService 인스턴스까지 전부 격리시킨다.
=========================================================
"""

import os
import tempfile
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationRead
from app.domains.order.model import Order
from app.domains.order.model import OrderItem
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.constants import ChannelConnectionStatus
from app.domains.purchase_task.constants import ConnectionMethod
from app.domains.purchase_task.model import (
    PurchaseChannelConnection,
    PurchaseChannelConnectionEvent,
    PurchaseOrderApproval,
    PurchaseSalesApplicationAttempt,
    PurchaseRecord,
    PurchaseTask,
    PurchaseTaskBudgetReservation,
    PurchaseTaskCandidate,
    PurchaseTaskCsvImportLog,
    PurchaseTaskEmailLog,
    PurchaseTaskEmailPreference,
    PurchaseTaskPolicySetting,
    PurchaseTaskTrackingInfo,
)
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService
from app.domains.purchase_task.service import PurchaseTaskService
from app.domains.role.model import Role
from app.domains.user.model import User

NOW = datetime(2026, 9, 9, 12, 0, 0)

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class OrderSubmissionReviewTestCaseBase(unittest.TestCase):

    def setUp(self):

        reset_recent_auth_state_for_tests()
        self.addCleanup(reset_recent_auth_state_for_tests)

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, FundingAccount.__table__,
                FundingLedger.__table__, Order.__table__, OrderItem.__table__,
                PurchaseTask.__table__, PurchaseTaskCandidate.__table__,
                PurchaseTaskBudgetReservation.__table__,
                PurchaseRecord.__table__, PurchaseTaskTrackingInfo.__table__,
                PurchaseTaskEmailPreference.__table__,
                PurchaseTaskEmailLog.__table__,
                PurchaseTaskPolicySetting.__table__,
                PurchaseTaskCsvImportLog.__table__,
                PurchaseChannelConnection.__table__,
                PurchaseChannelConnectionEvent.__table__,
                PurchaseSalesApplicationAttempt.__table__,
                PurchaseOrderApproval.__table__,
                AutomationModeState.__table__, EmergencyStop.__table__,
                ExecutionLimit.__table__, ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__, Role.__table__, User.__table__,
                Notification.__table__, NotificationRead.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.execute(text(AUDIT_LOGS_DDL))

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="부산",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.db.add(FundingAccount(
            company_id=self.company_a.id, total_funding=1000000.0,
        ))
        self.db.commit()

        policy_svc = PurchaseTaskPolicyService(self.db)
        setting = policy_svc.get_or_create_default_settings(self.company_a.id)
        setting.min_net_profit = 0
        setting.min_margin_rate = 0
        setting.max_price_increase_rate = 0.5
        setting.require_return_allowed = True
        self.db.commit()

        # 2026-09-09 후속 — 실제 Windows Credential Manager를 전혀
        # 건드리지 않는다. PurchaseTaskService.build_order_submission_
        # review()가 내부적으로 만드는 PurchaseChannelConnectionService도
        # 이 store를 쓰게 한다(생성자 주입 지점이 없으므로 WindowsCredentialStore
        # 클래스 자체를 바꿔치기 — 이전 라운드 실제 자격증명 유출 사고
        # 이후 확립된 격리 방법과 동일).
        self.credential_store = InMemoryCredentialStore()
        import app.core.windows_credential_store as wcs_module
        patcher = mock.patch.object(
            wcs_module, "WindowsCredentialStore",
            lambda: self.credential_store,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.service = PurchaseTaskService(self.db)
        self.connection_service = PurchaseChannelConnectionService(
            self.db, credential_store=self.credential_store,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # ---------------- 공통 픽스처 ----------------

    def _create_order(
        self, *, company=None, channel_order_id="CPG-0001",
        receiver_name="홍길동", receiver_phone="010-1234-5678",
        receiver_address="서울시 어딘가 123", receiver_zipcode="04524",
    ) -> Order:

        company = company or self.company_a
        order = Order(
            company_id=company.id, channel_code="COUPANG",
            channel_order_id=channel_order_id, order_number=f"O-{channel_order_id}",
            buyer_name=receiver_name, receiver_name=receiver_name,
            receiver_phone=receiver_phone, receiver_address=receiver_address,
            receiver_zipcode=receiver_zipcode, total_amount=30000.0,
            ordered_at=NOW,
        )
        self.db.add(order)
        self.db.commit()
        self.db.refresh(order)
        return order

    def _create_order_item(
        self, order: Order, *, quantity=1, product_name="무선이어폰",
        unit_price=25000.0,
    ) -> OrderItem:

        item = OrderItem(
            company_id=order.company_id, order_id=order.id,
            inventory_sku_id=1, channel_sku="SKU-1",
            sku_code_snapshot="SKU-1", product_name_snapshot=product_name,
            quantity=quantity, unit_price=unit_price,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def _create_task(
        self, *, key="review:1", order: Order, order_item: OrderItem | None = None,
        product_title="무선이어폰", quantity=1,
    ) -> PurchaseTask:

        return self.service.create_task(
            order.company_id, source_order_id=order.id,
            source_order_item_id=order_item.id if order_item else None,
            product_title=product_title, brand="브랜드A", manufacturer="브랜드A",
            model_name="MODEL-1", gtin="1111111111111", capacity="100ml",
            quantity=quantity, color_or_scent="블랙", options=["기본"],
            coupang_sale_amount=30000.0, coupang_fee_amount=3000.0,
            purchase_deadline=NOW + timedelta(days=3),
            idempotency_key=key,
        )

    def _create_ready_connection(self, *, company=None) -> PurchaseChannelConnection:
        """자격증명 등록 + verified_at을 방금으로 설정해 조회 인증
        확인(CONNECTED, 만료 전) 상태로 만든다."""

        company = company or self.company_a
        connection = self.connection_service.create_connection(
            company.id, mall_code="ONCHANNEL", account_label="검토용 계정",
        )
        self.connection_service.save_credential(
            connection.id, company.id, auth_key="test-jwt",
        )
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow()
        self.db.commit()
        return row

    def _install_fake_connection_adapter(
        self, *, lookup_product_result=None, lookup_product_error=None,
        check_member_point_result=None, check_member_point_error=None,
    ):
        """2026-09-10 후속(Phase 4 — 이 검토 화면이 포인트 잔액도
        함께 보여주게 됨) — `check_member_point_result`를 넘기지
        않으면 "잔액 충분"을 뜻하는 합성 성공 결과를 기본값으로
        쓴다. 이 파일의 대다수 테스트는 포인트 게이트 자체를
        검증하려는 게 아니므로, 기본값으로 그 관심사를 격리한다
        (포인트 게이트 자체의 세부 동작은 PointBalanceReviewTestCase
        전담)."""

        if check_member_point_result is None and check_member_point_error is None:
            from app.domains.purchase_task.channel_adapter import (
                CapabilitySupport, MemberPointCheckResult,
            )
            check_member_point_result = MemberPointCheckResult(
                support=CapabilitySupport.SUPPORTED, member_id_masked="t***",
                point=999_999_999, point_interpretable=True,
                observed_fields=("member_id", "point"), detail="FAKE — 잔액 충분",
            )

        class _FakeAdapter:
            def lookup_product(self_inner, external_product_id):
                if lookup_product_error is not None:
                    raise lookup_product_error
                return lookup_product_result

            def check_member_point(self_inner):
                if check_member_point_error is not None:
                    raise check_member_point_error
                return check_member_point_result

            def check_connection(self_inner, account_label, *, verified_at=None):
                from datetime import timedelta as _td
                if verified_at is None:
                    status = ChannelConnectionStatus.REGISTERED_UNVERIFIED
                elif datetime.utcnow() - verified_at >= _td(hours=24):
                    status = ChannelConnectionStatus.EXPIRED
                else:
                    status = ChannelConnectionStatus.CONNECTED

                class _Result:
                    pass
                result = _Result()
                result.status = status
                return result

        patcher = mock.patch(
            "app.domains.purchase_task.channel_connection_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_lookup_result(self, *, title="무선이어폰", options=None):

        from app.domains.purchase_task.channel_adapter import (
            CapabilitySupport, ChannelProductOption, ProductLookupResult,
        )

        if options is None:
            from decimal import Decimal
            options = (
                ChannelProductOption(
                    option_id="OPT1", label="블랙", price=Decimal("25000"),
                    in_stock=True,
                ),
            )
        return ProductLookupResult(
            support=CapabilitySupport.SUPPORTED,
            external_product_id="CH1234567", title=title,
            options=options, detail="가짜 조회 결과(테스트 전용).",
        )


class HappyPathTestCase(OrderSubmissionReviewTestCaseBase):
    """쿠팡 주문 → PurchaseTask → 온채널 연결 계정 → 온채널 상품·
    옵션 → 수량·가격 → 수취정보(마스킹)까지 한 응답에 전부 모이는지
    확인한다."""

    def test_full_review_assembles_all_pieces(self):

        order = self._create_order(receiver_name="김철수", receiver_phone="010-9999-8888")
        item = self._create_order_item(order, quantity=2, product_name="무선이어폰")
        task = self._create_task(order=order, order_item=item, quantity=2)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(
            task.id, self.company_a.id, connection.id,
        )
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id,
            external_product_id="CH1234567",
            options=[{"id": "OPT1", "qty": 2}],
            triggered_by=1,
        )

        self.assertEqual(review["task_id"], task.id)
        self.assertEqual(review["source_order_id"], order.id)
        self.assertEqual(review["source_order_quantity"], 2)
        self.assertEqual(review["connection_id"], connection.id)
        self.assertEqual(review["connection_mall_code"], "ONCHANNEL")
        self.assertEqual(review["product"]["title"], "무선이어폰")
        self.assertEqual(review["product"]["estimated_item_amount"], 50000)
        self.assertFalse(review["product"]["any_selected_option_out_of_stock"])
        self.assertFalse(review["product"]["any_selected_option_price_unknown"])
        self.assertFalse(review["quantity_mismatch_warning"])
        self.assertFalse(review["product_title_mismatch_warning"])
        # 수취정보는 기본적으로 마스킹된다(재인증 토큰을 넘기지 않았다).
        self.assertFalse(review["recipient"]["unmasked"])
        self.assertNotEqual(review["recipient"]["name"], "김철수")
        self.assertNotIn("9999", review["recipient"]["phone"] or "")


class RecipientPiiProtectionTestCase(OrderSubmissionReviewTestCaseBase):
    """개인정보의 화면·로그·DB 노출 방지."""

    def _setup_ready_review(self):

        order = self._create_order(receiver_name="이영희", receiver_phone="010-5555-4444")
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(
            task.id, self.company_a.id, connection.id,
        )
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )
        return order, task

    def test_recipient_masked_without_recent_auth_token(self):

        order, task = self._setup_ready_review()

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertFalse(review["recipient"]["unmasked"])
        self.assertNotIn("이영희", str(review["recipient"]))
        self.assertNotIn("5555", str(review["recipient"]))

    def test_recipient_masked_with_wrong_or_reused_token(self):

        order, task = self._setup_ready_review()

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}],
            recent_auth_token="not-a-real-token", triggered_by=1,
        )
        self.assertFalse(review["recipient"]["unmasked"])

    def test_recipient_unmasked_with_valid_recent_auth_token(self):

        order, task = self._setup_ready_review()
        token, _ = issue_recent_auth_token(1)

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}],
            recent_auth_token=token, triggered_by=1,
        )
        self.assertTrue(review["recipient"]["unmasked"])
        self.assertEqual(review["recipient"]["name"], "이영희")

    def test_recent_auth_token_is_single_use(self):

        order, task = self._setup_ready_review()
        token, _ = issue_recent_auth_token(1)

        first = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}],
            recent_auth_token=token, triggered_by=1,
        )
        self.assertTrue(first["recipient"]["unmasked"])

        second = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}],
            recent_auth_token=token, triggered_by=1,
        )
        self.assertFalse(
            second["recipient"]["unmasked"],
            "1회용 재인증 토큰을 두 번째 호출에 재사용할 수 있으면 안 된다.",
        )

    def test_audit_log_description_never_contains_recipient_pii(self):

        order, task = self._setup_ready_review()
        token, _ = issue_recent_auth_token(1)

        self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}],
            recent_auth_token=token, triggered_by=1,
        )

        rows = self.db.execute(
            text("SELECT description FROM audit_logs"),
        ).fetchall()
        combined = " ".join(str(r[0]) for r in rows)
        self.assertNotIn("이영희", combined)
        self.assertNotIn("5555", combined)
        self.assertNotIn(order.receiver_address, combined)


class ProductOptionMismatchTestCase(OrderSubmissionReviewTestCaseBase):
    """상품 또는 옵션 불일치."""

    def test_title_mismatch_flagged(self):

        order = self._create_order()
        item = self._create_order_item(order, product_name="무선이어폰")
        task = self._create_task(order=order, order_item=item, product_title="무선이어폰")
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(title="완전히 다른 상품"),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertTrue(review["product_title_mismatch_warning"])

    def test_quantity_mismatch_flagged(self):

        order = self._create_order()
        item = self._create_order_item(order, quantity=3)
        task = self._create_task(order=order, order_item=item, quantity=3)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertTrue(review["quantity_mismatch_warning"])
        self.assertTrue(review["send_blocked"])


class StockAndPriceChangeTestCase(OrderSubmissionReviewTestCaseBase):
    """품절과 가격·배송비 변경."""

    def test_out_of_stock_option_flagged_and_blocks(self):

        from decimal import Decimal
        from app.domains.purchase_task.channel_adapter import ChannelProductOption

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(options=(
                ChannelProductOption(
                    option_id="OPT1", label="품절옵션", price=Decimal("10000"),
                    in_stock=False,
                ),
            )),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertTrue(review["product"]["any_selected_option_out_of_stock"])
        self.assertTrue(review["send_blocked"])

    def test_price_unknown_when_option_not_found_in_live_lookup(self):

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            # 실제 조회 결과에 없는 옵션 id를 선택한 상황.
            options=[{"id": "NOT-A-REAL-OPTION", "qty": 1}], triggered_by=1,
        )
        self.assertTrue(review["product"]["any_selected_option_price_unknown"])
        self.assertIsNone(review["product"]["estimated_item_amount"])
        self.assertTrue(review["send_blocked"])

    def test_price_change_between_two_reviews_reflects_fresh_lookup_not_cache(self):

        from decimal import Decimal
        from app.domains.purchase_task.channel_adapter import ChannelProductOption

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)

        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(options=(
                ChannelProductOption(
                    option_id="OPT1", label="A", price=Decimal("10000"), in_stock=True,
                ),
            )),
        )
        first = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertEqual(first["product"]["estimated_item_amount"], 10000)

        # 두 번째 호출 전에 실제 가격이 바뀌었다고 가정 — 캐시 없이
        # 매번 새로 조회해야 한다.
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(options=(
                ChannelProductOption(
                    option_id="OPT1", label="A", price=Decimal("15000"), in_stock=True,
                ),
            )),
        )
        second = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertEqual(second["product"]["estimated_item_amount"], 15000)

    def test_shipping_fee_never_fabricated(self):
        """온채널 공식 배송비 견적 API가 확인되지 않았으므로, 이
        화면은 배송비를 절대 추정해서 채우지 않는다."""

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertFalse(review["shipping_fee_known"])


class AuthExpiredTestCase(OrderSubmissionReviewTestCaseBase):
    """인증 만료."""

    def test_expired_connection_blocks_review(self):

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)

        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        row.verified_at = datetime.utcnow() - timedelta(hours=25)
        self.db.commit()

        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertTrue(review["send_blocked"])
        self.assertTrue(
            any("조회 인증" in r or "EXPIRED" in r for r in review["blocked_reasons"]),
        )


class DuplicateReviewCallTestCase(OrderSubmissionReviewTestCaseBase):
    """동일 주문(작업) 반복 클릭 — 검토는 읽기 전용이므로 몇 번을
    다시 열어도 새 행이 생기거나 상태가 오염되면 안 된다."""

    def test_repeated_review_calls_create_no_submission_attempt_rows(self):

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        for _ in range(5):
            self.service.build_order_submission_review(
                task.id, self.company_a.id, external_product_id="CH1",
                options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
            )

        # purchase_order_submission_attempts 테이블 자체를 이 테스트
        # DB에 만들지 않았다 — 이 리뷰 경로가 그 테이블에 뭔가 쓰려고
        # 했다면 여기서 OperationalError(no such table)로 실패했을
        # 것이다. 5번 반복 호출이 예외 없이 끝났다는 사실 자체가
        # "시도 행을 만들지 않는다"는 증거다.
        task_after = self.service.get_task(task.id, self.company_a.id)
        self.assertEqual(task_after.status, task.status)


class MultiTaskPartialFailureTestCase(OrderSubmissionReviewTestCaseBase):
    """다수 주문과 일부 주문 실패 — 한 작업의 검토 실패가 다른
    작업의 검토에 영향을 주면 안 된다."""

    def test_one_task_missing_order_does_not_affect_other_task_review(self):

        good_order = self._create_order(channel_order_id="CPG-GOOD")
        good_item = self._create_order_item(good_order)
        good_task = self._create_task(
            key="review:multi:good", order=good_order, order_item=good_item,
        )

        bad_task = self.service.create_task(
            self.company_a.id, source_order_id=999999, source_order_item_id=None,
            product_title="존재하지 않는 주문", brand=None, manufacturer=None,
            model_name=None, gtin=None, capacity=None, quantity=1,
            color_or_scent=None, idempotency_key="review:multi:bad",
        )

        connection = self._create_ready_connection()
        self.service.assign_channel_connection(good_task.id, self.company_a.id, connection.id)
        self.service.assign_channel_connection(bad_task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        with self.assertRaises(NotFoundException):
            self.service.build_order_submission_review(
                bad_task.id, self.company_a.id, external_product_id="CH1",
                options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
            )

        # 실패 이후에도 정상 작업의 검토는 그대로 성공한다(격리).
        review = self.service.build_order_submission_review(
            good_task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertEqual(review["task_id"], good_task.id)


class ChannelConnectionMissingTestCase(OrderSubmissionReviewTestCaseBase):

    def test_review_without_assigned_connection_raises(self):

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)

        with self.assertRaises(BadRequestException):
            self.service.build_order_submission_review(
                task.id, self.company_a.id, external_product_id="CH1",
                options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
            )


class CrossCompanyIsolationTestCase(OrderSubmissionReviewTestCaseBase):

    def test_other_company_cannot_review_task(self):

        order = self._create_order(company=self.company_a)
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection(company=self.company_a)
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        with self.assertRaises(NotFoundException):
            self.service.build_order_submission_review(
                task.id, self.company_b.id, external_product_id="CH1",
                options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
            )


class ContractGateAlwaysBlocksTestCase(OrderSubmissionReviewTestCaseBase):
    """실제 전송 버튼은 계약 4개가 모두 확인되지 않은 상태에서는
    서버·UI 양쪽에서 차단된다 — 여기서는 서버(검토 응답) 쪽을
    확인한다.

    2026-09-10 후속 — 온채널 공식 답변으로 계약 4항목이 전부
    confirmed=True로 갱신됐다(constants.py의 ONCHANNEL_ORDER_
    CONTRACT_STATUS 참고). 이 클래스는 "메커니즘"(미확인 상태면
    항상 차단됨)을 테스트하는 것이지 "지금 이 순간의 실제 값"을
    테스트하는 것이 아니다 — 그래서 `setUp`에서 합성 미확인
    baseline으로 강제 리셋한다(tests/test_purchase_channel_
    connection_service.py의 OnchannelOrderContractStatusTestCase와
    동일한 패턴). 실제 현재값(전부 확인됨)은 별도 테스트 클래스
    (ContractGateRealCurrentValueTestCase)가 전담한다."""

    def setUp(self):

        super().setUp()

        from app.domains.purchase_task import constants as contract_constants

        self._contract_patchers = [
            mock.patch.object(
                contract_constants, "is_onchannel_order_contract_fully_confirmed",
                return_value=False,
            ),
            mock.patch.object(
                contract_constants, "unconfirmed_onchannel_order_contract_items",
                return_value=("SALES_APPLICATION", "PAYMENT_SOURCE"),
            ),
        ]
        for patcher in self._contract_patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_send_blocked_true_and_contract_items_listed(self):

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertTrue(review["send_blocked"])
        self.assertTrue(
            any("계약" in r for r in review["blocked_reasons"]),
        )

    def test_review_method_never_references_submit_order(self):
        """이 검토 조립 메서드의 소스 코드 자체에 submit_order 호출이
        없다는 것을 회귀적으로 증명한다(리팩터링 중 실수로 실제
        전송을 끼워 넣는 것을 막는 안전망)."""

        import ast
        import inspect
        import textwrap

        source = inspect.getsource(
            PurchaseTaskService.build_order_submission_review,
        )
        tree = ast.parse(textwrap.dedent(source))
        func_node = tree.body[0]
        # docstring(첫 Expr/Constant 문)을 뺀 실제 코드 부분만 다시
        # 소스로 되돌려 검사한다 — 문서화 목적으로 메서드 이름을
        # 언급하는 것과 실제로 그 메서드를 호출하는 것은 다르다.
        body_without_docstring = ast.Module(body=func_node.body[1:], type_ignores=[])
        code_only = ast.unparse(body_without_docstring)
        self.assertNotIn("submit_order", code_only)
        self.assertNotIn("PurchaseOrderSubmissionService", code_only)
        self.assertNotIn("confirm_real_submission", code_only)


class ContractGateRealCurrentValueTestCase(OrderSubmissionReviewTestCaseBase):
    """2026-09-10 신규 — 온채널 공식 답변 반영 이후 실제 현재값으로는
    계약 미확인 사유가 더 이상 차단 목록에 나타나지 않음을 고정한다
    (패치 없음, 이 파일의 다른 테스트와 달리 실제 모듈 상태 그대로
    검증). 이 테스트가 실패한다면 계약 confirmed 상태가 실제로
    되돌아갔다는 뜻이므로, ONCHANNEL_ORDER_CONTRACT_STATUS를 다시
    확인해야 한다."""

    def test_contract_reason_absent_when_all_four_items_confirmed(self):

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )
        self.assertFalse(
            any("계약" in r for r in review["blocked_reasons"]),
            "온채널 공식 답변으로 계약 4항목이 전부 확인됐으므로, 계약 "
            "미확인을 사유로 한 차단은 더 이상 나타나면 안 된다.",
        )


class SalesApplicationAndPointBalanceReviewTestCase(OrderSubmissionReviewTestCaseBase):
    """2026-09-10 신규(Phase 3~4 — 판매신청·포인트 잔액 게이트를 이
    검토 화면에도 반영) — submit_order()가 실제로 적용하는 게이트와
    이 화면의 예고가 어긋나지 않는지 확인한다."""

    def _make_ready_task_and_connection(self):

        order = self._create_order()
        item = self._create_order_item(order)
        task = self._create_task(order=order, order_item=item)
        connection = self._create_ready_connection()
        self.service.assign_channel_connection(task.id, self.company_a.id, connection.id)
        return task, connection

    def test_sales_application_not_confirmed_is_reported_and_blocks(self):

        task, connection = self._make_ready_task_and_connection()
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1234567",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )

        self.assertFalse(review["sales_application"]["confirmed"])
        self.assertTrue(
            any("판매신청" in r for r in review["blocked_reasons"]),
        )

    def test_sales_application_confirmed_is_not_blocked_for_that_reason(self):

        from app.domains.purchase_task.constants import SalesApplicationStatus
        from app.domains.purchase_task.model import PurchaseSalesApplicationAttempt

        task, connection = self._make_ready_task_and_connection()
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )
        self.db.add(PurchaseSalesApplicationAttempt(
            company_id=self.company_a.id, connection_id=connection.id,
            mall_code="ONCHANNEL", product_code="CH1234567",
            status=SalesApplicationStatus.SUBMITTED,
            applied_product_code="CH1234567",
        ))
        self.db.commit()

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1234567",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )

        self.assertTrue(review["sales_application"]["confirmed"])
        self.assertFalse(
            any("판매신청" in r for r in review["blocked_reasons"]),
        )

    def test_point_balance_shown_and_insufficient_balance_blocks(self):

        from app.domains.purchase_task.channel_adapter import (
            CapabilitySupport, MemberPointCheckResult,
        )

        task, connection = self._make_ready_task_and_connection()
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
            check_member_point_result=MemberPointCheckResult(
                support=CapabilitySupport.SUPPORTED, member_id_masked="t***",
                point=100, point_interpretable=True,
                observed_fields=("member_id", "point"), detail="FAKE — 잔액 부족",
            ),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1234567",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )

        self.assertEqual(review["point_balance"]["point"], 100)
        self.assertTrue(
            any("보다 적습니다" in r for r in review["blocked_reasons"]),
        )

    def test_point_balance_unclear_response_blocks(self):

        from app.domains.purchase_task.channel_adapter import (
            CapabilitySupport, MemberPointCheckResult,
        )

        task, connection = self._make_ready_task_and_connection()
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
            check_member_point_result=MemberPointCheckResult(
                support=CapabilitySupport.SUPPORTED, member_id_masked="t***",
                point=None, point_interpretable=False,
                observed_fields=(), detail="FAKE — 해석 불가",
            ),
        )

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1234567",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )

        self.assertFalse(review["point_balance"]["point_interpretable"])
        self.assertTrue(
            any("해석할 수 없습니다" in r for r in review["blocked_reasons"]),
        )

    def test_shipping_unconfirmed_always_blocks_even_when_everything_else_is_ready(self):
        """이 게이트의 핵심 회귀 테스트 — 판매신청·포인트·상품가가
        전부 정상이어도, 배송비를 사전에 확인할 방법이 없다는 사실
        때문에 이 화면도 항상 차단 사유를 보여준다(submit_order()의
        Gate D와 일치시킨다 — 화면과 실제 게이트가 어긋나지 않게)."""

        from app.domains.purchase_task.constants import SalesApplicationStatus
        from app.domains.purchase_task.model import PurchaseSalesApplicationAttempt

        task, connection = self._make_ready_task_and_connection()
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )
        self.db.add(PurchaseSalesApplicationAttempt(
            company_id=self.company_a.id, connection_id=connection.id,
            mall_code="ONCHANNEL", product_code="CH1234567",
            status=SalesApplicationStatus.SUBMITTED,
            applied_product_code="CH1234567",
        ))
        self.db.commit()

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1234567",
            options=[{"id": "OPT1", "qty": 1}], triggered_by=1,
        )

        self.assertTrue(review["send_blocked"])
        self.assertTrue(
            any("배송비" in r for r in review["blocked_reasons"]),
        )

    def test_valid_active_approval_matching_price_unblocks_shipping_reason(self):
        """2026-09-11 신규(반자동 완료 라운드 Phase 5·7) — 유효한
        사용자 최종 승인이 있고 그 가격 스냅샷이 지금 조회한 가격과
        일치하면, 이 화면의 배송비 차단 사유가 사라지고
        order_approval이 채워진다(submit_order()의 Gate D와 동일한
        판정이어야 한다)."""

        from datetime import timedelta

        from app.domains.purchase_task.constants import PurchaseOrderApprovalStatus
        from app.domains.purchase_task.constants import SalesApplicationStatus
        from app.domains.purchase_task.model import PurchaseOrderApproval
        from app.domains.purchase_task.model import PurchaseSalesApplicationAttempt

        task, connection = self._make_ready_task_and_connection()
        self._install_fake_connection_adapter(
            lookup_product_result=self._make_lookup_result(),
        )
        self.db.add(PurchaseSalesApplicationAttempt(
            company_id=self.company_a.id, connection_id=connection.id,
            mall_code="ONCHANNEL", product_code="CH1234567",
            status=SalesApplicationStatus.SUBMITTED,
            applied_product_code="CH1234567",
        ))
        self.db.add(PurchaseOrderApproval(
            company_id=self.company_a.id, connection_id=connection.id,
            purchase_task_id=task.id, product_code="CH1234567",
            status=PurchaseOrderApprovalStatus.ACTIVE,
            shipping_cost_amount=3000, shipping_cost_is_free_confirmed=False,
            shipping_cost_source="ONCHANNEL_PRODUCT_PAGE",
            item_amount_snapshot=50000,  # _make_lookup_result() 기본값과 일치
            required_points_snapshot=53000, current_points_snapshot=999_999_999,
            margin_amount_snapshot=10000, margin_rate_snapshot=0.2,
            approved_by=1, approved_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        ))
        self.db.commit()

        review = self.service.build_order_submission_review(
            task.id, self.company_a.id, external_product_id="CH1234567",
            options=[{"id": "OPT1", "qty": 2}], triggered_by=1,
        )

        self.assertTrue(review["shipping_fee_known"])
        self.assertFalse(
            any("배송비" in r for r in review["blocked_reasons"]),
        )
        self.assertIsNotNone(review["order_approval"])
        self.assertEqual(review["order_approval"]["status"], PurchaseOrderApprovalStatus.ACTIVE)
        self.assertEqual(review["order_approval"]["shipping_cost_amount"], 3000)
        self.assertTrue(review["order_approval"]["matches_current_price"])


if __name__ == "__main__":
    unittest.main()
