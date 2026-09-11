"""
=========================================================
Homez OS

File : tests/test_purchase_task_router.py

Gate PT-1(2026-08-22) 검증 — purchase_task 라우터. httpx 미설치로
라우터 함수를 직접 호출한다. 실제 homez.db는 전혀 접근하지 않는다.
=========================================================
"""

import asyncio
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import UnauthorizedException
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
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
from app.domains.purchase_task.model import (
    PurchaseChannelConnection,
    PurchaseChannelConnectionEvent,
    PurchaseOrderApproval,
    PurchaseOrderSubmissionAttempt,
    PurchaseOrderUnknownResolutionEvent,
    PurchaseRecord,
    PurchaseSalesApplicationAttempt,
    PurchaseTask,
    PurchaseTaskBudgetReservation,
    PurchaseTaskCandidate,
    PurchaseTaskCsvImportLog,
    PurchaseTaskEmailLog,
    PurchaseTaskEmailPreference,
    PurchaseTaskEmailProviderSetting,
    PurchaseTaskPolicySetting,
    PurchaseTaskTrackingInfo,
)
from app.domains.purchase_task.router import (
    add_candidate,
    confirm_order_shipping_cost,
    create_task,
    evaluate_and_prepare,
    finalize_order_approval,
    get_email_preference,
    get_email_provider_setting,
    get_order_approval,
    get_policy,
    get_task,
    import_csv,
    list_order_submission_attempts,
    list_tasks,
    open_payment_page,
    preview_csv_upload,
    record_purchase,
    reconcile_orders,
    resolve_unknown_attempt,
    run_match_check,
    submit_real_order,
    update_email_preference,
    update_email_provider_setting,
    update_policy,
)
from app.domains.purchase_task.schema import (
    CandidateCreate,
    EmailPreferenceUpdate,
    EmailProviderSettingUpdate,
    EvaluateRequest,
    FinalizeOrderApprovalRequest,
    MatchCheckRequest,
    OrderSubmissionReviewOptionInput,
    PolicySettingUpdate,
    PurchaseTaskCreate,
    RecordPurchaseRequest,
    ResolveUnknownAttemptRequest,
    ShippingCostConfirmationRequest,
    SourceAttributesInput,
    SubmitRealOrderRequest,
)
from app.domains.purchase_task.constants import (
    OrderSubmissionStatus,
    PurchaseOrderApprovalStatus,
    ShippingCostConfirmationSource,
    UnknownResolutionStatus,
)
from app.domains.role.model import Role
from app.domains.user.model import User

NOW = datetime(2026, 8, 22, 12, 0, 0)

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class _FakeUser:

    def __init__(self, user_id, company_id, role=None):
        self.id = user_id
        self.company_id = company_id
        # 2026-09-11 후속(반자동 완료 라운드) — require_permission()이
        # is_super_admin()을 거쳐 getattr(user, "role", None)만
        # 읽는다(DB role_id 조인 없이) — role="super_admin"이면
        # 어떤 permission 코드든 통과한다. 기존 테스트는 role=None
        # 기본값을 그대로 쓰므로 영향 없다.
        self.role = role


class _FakeUploadFile:
    """UploadFile 대역 — httpx 없이 라우터 함수를 직접 호출하기 위함."""

    def __init__(self, content: bytes):
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _run(coro):

    return asyncio.run(coro)


class PurchaseTaskRouterTestCase(unittest.TestCase):

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
                FundingLedger.__table__, PurchaseTask.__table__,
                PurchaseTaskCandidate.__table__,
                PurchaseTaskBudgetReservation.__table__,
                PurchaseRecord.__table__, PurchaseTaskTrackingInfo.__table__,
                PurchaseTaskEmailPreference.__table__,
                PurchaseTaskEmailLog.__table__,
                PurchaseTaskPolicySetting.__table__,
                PurchaseTaskEmailProviderSetting.__table__,
                PurchaseTaskCsvImportLog.__table__,
                AutomationModeState.__table__, EmergencyStop.__table__,
                ExecutionLimit.__table__, ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__, Role.__table__,
                User.__table__,
                Notification.__table__, NotificationRead.__table__,
                Order.__table__, OrderItem.__table__,
                PurchaseOrderApproval.__table__,
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
            email="b@example.com", address="서울",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.account_a = FundingAccount(
            company_id=self.company_a.id, total_funding=1000000.0,
        )
        self.db.add(self.account_a)
        self.db.commit()

        self.user_a = _FakeUser(1, self.company_a.id)
        self.user_b = _FakeUser(2, self.company_b.id)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create(self, user, key="pt:1"):

        return create_task(
            PurchaseTaskCreate(
                source_order_id=1, product_title="무선이어폰",
                brand="브랜드A", manufacturer="브랜드A", model_name="MODEL-1",
                gtin="1111111111111", capacity="100ml", quantity=1,
                color_or_scent="블랙", options=["기본"],
                coupang_sale_amount=30000, coupang_fee_amount=3000,
                purchase_deadline=NOW + timedelta(days=3),
                idempotency_key=key,
            ),
            current_user=user, db=self.db,
        )

    def _to_ready(self, user, key="pt:ready"):

        task = self._create(user, key=key)
        candidate = add_candidate(
            task.id,
            CandidateCreate(
                shopping_mall_code="NAVER_SHOPPING",
                product_url=f"https://search.shopping.naver.com/product/{key}",
                brand="브랜드A", manufacturer="브랜드A", model_name="MODEL-1",
                gtin="1111111111111", capacity="100ml", color_or_scent="블랙",
                options=["기본"], estimated_price=10000, estimated_shipping_fee=3000,
                estimated_delivery_days=2, seller_trust_score=0.9,
                return_allowed=True,
            ),
            current_user=user, db=self.db,
        )
        source = SourceAttributesInput(
            brand="브랜드A", manufacturer="브랜드A", model_name="MODEL-1",
            gtin="1111111111111", capacity="100ml", color_or_scent="블랙",
            options=["기본"],
        )
        run_match_check(
            task.id, candidate.id, MatchCheckRequest(source=source),
            current_user=user, db=self.db,
        )
        result = evaluate_and_prepare(
            task.id, candidate.id, EvaluateRequest(source=source),
            current_user=user, db=self.db,
        )
        return result.task, candidate

    def test_create_and_get_round_trip(self):

        created = self._create(self.user_a)
        fetched = get_task(created.id, current_user=self.user_a, db=self.db)
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.status, "SEARCH_REQUIRED")

    def test_company_b_cannot_get_company_a_task(self):

        created = self._create(self.user_a)
        with self.assertRaises(NotFoundException):
            get_task(created.id, current_user=self.user_b, db=self.db)

    def test_list_only_returns_own_company(self):

        self._create(self.user_a, key="pt:list")
        result_a = list_tasks(status_filter=None, current_user=self.user_a, db=self.db)
        result_b = list_tasks(status_filter=None, current_user=self.user_b, db=self.db)
        self.assertEqual(len(result_a), 1)
        self.assertEqual(len(result_b), 0)

    def test_list_filters_by_source_order_id(self):

        created = self._create(self.user_a, key="pt:src-filter")
        matching = list_tasks(
            status_filter=None, source_order_id=created.source_order_id,
            current_user=self.user_a, db=self.db,
        )
        non_matching = list_tasks(
            status_filter=None, source_order_id=99999,
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].id, created.id)
        self.assertEqual(len(non_matching), 0)

    def test_reconcile_orders_requires_admin_and_is_idempotent(self):

        result1 = reconcile_orders(current_user=self.user_a, db=self.db)
        result2 = reconcile_orders(current_user=self.user_a, db=self.db)
        self.assertEqual(result1, result2)

    def test_full_flow_via_router(self):

        task, candidate = self._to_ready(self.user_a, key="flow")
        self.assertEqual(task.status, "PURCHASE_READY")

        task = open_payment_page(task.id, current_user=self.user_a, db=self.db)
        self.assertEqual(task.status, "USER_PAYMENT_PENDING")

        task = record_purchase(
            task.id,
            RecordPurchaseRequest(
                shopping_mall_code="NAVER_SHOPPING",
                external_order_number="N-ROUTER-1", actual_amount=13000,
                actual_shipping_fee=3000, purchased_at=NOW,
                idempotency_key="rec:router:1",
            ),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(task.status, "TRACKING_REQUIRED")

    def test_policy_update_requires_recent_auth(self):

        with self.assertRaises(UnauthorizedException):
            update_policy(
                PolicySettingUpdate(min_net_profit=1000),
                current_user=self.user_a, db=self.db, recent_auth_token=None,
                if_unmodified_since=None,
            )

    def test_policy_update_succeeds_and_optimistic_concurrency_enforced(self):

        current = get_policy(current_user=self.user_a, db=self.db)

        token, _ = issue_recent_auth_token(self.user_a.id)
        updated = update_policy(
            PolicySettingUpdate(min_net_profit=1500),
            current_user=self.user_a, db=self.db, recent_auth_token=token,
            if_unmodified_since=current.updated_at.isoformat(),
        )
        self.assertEqual(updated.min_net_profit, 1500)

        # 같은 stale updated_at으로 다시 시도하면 충돌.
        token2, _ = issue_recent_auth_token(self.user_a.id)
        with self.assertRaises(ConflictException):
            update_policy(
                PolicySettingUpdate(min_net_profit=2000),
                current_user=self.user_a, db=self.db, recent_auth_token=token2,
                if_unmodified_since=current.updated_at.isoformat(),
            )

    def test_email_provider_setting_requires_recent_auth_and_never_stores_secret(self):

        default = get_email_provider_setting(current_user=self.user_a, db=self.db)
        self.assertEqual(default.provider_type, "NONE")
        self.assertFalse(default.is_active)

        with self.assertRaises(UnauthorizedException):
            update_email_provider_setting(
                EmailProviderSettingUpdate(
                    provider_type="SMTP", smtp_host="smtp.example.com",
                    smtp_port=587, credential_reference="cred-ref-only",
                ),
                current_user=self.user_a, db=self.db, recent_auth_token=None,
            )

        token, _ = issue_recent_auth_token(self.user_a.id)
        updated = update_email_provider_setting(
            EmailProviderSettingUpdate(
                provider_type="SMTP", smtp_host="smtp.example.com",
                smtp_port=587, credential_reference="cred-ref-only",
                is_active=False,
            ),
            current_user=self.user_a, db=self.db, recent_auth_token=token,
        )
        self.assertEqual(updated.provider_type, "SMTP")
        self.assertEqual(updated.smtp_host, "smtp.example.com")
        self.assertTrue(updated.has_credential_reference)
        # 응답 스키마 자체에 credential_reference 원문을 절대 담지 않는다
        # (has_credential_reference 불리언만 노출).
        self.assertFalse(hasattr(updated, "credential_reference"))
        # 값을 저장해도 실제 발송 경로는 여전히 미연결이다(정직 공개).
        self.assertFalse(updated.is_active)

    def test_email_preference_round_trip(self):

        pref = get_email_preference(current_user=self.user_a, db=self.db)
        self.assertTrue(pref.enabled)

        updated = update_email_preference(
            EmailPreferenceUpdate(enabled=False, event_toggles={}),
            current_user=self.user_a, db=self.db,
        )
        self.assertFalse(updated.enabled)

    def test_csv_preview_and_import(self):

        raw = (
            "purchase_task_id,shopping_mall_code,external_order_number,"
            "actual_amount\n"
        ).encode("utf-8")
        preview = _run(preview_csv_upload(
            file=_FakeUploadFile(raw), current_user=self.user_a,
        ))
        self.assertTrue(preview.column_mapping_ok)

        task, candidate = self._to_ready(self.user_a, key="csvflow")
        open_payment_page(task.id, current_user=self.user_a, db=self.db)

        csv_bytes = (
            "purchase_task_id,shopping_mall_code,external_order_number,"
            "actual_amount\n"
            f"{task.id},NAVER_SHOPPING,N-CSV-1,13000\n"
        ).encode("utf-8")
        result = _run(import_csv(
            file=_FakeUploadFile(csv_bytes), current_user=self.user_a, db=self.db,
        ))
        self.assertEqual(result.success_rows, 1)
        self.assertEqual(result.failure_rows, 0)


class OrderApprovalRouterTestCase(unittest.TestCase):
    """2026-09-11 후속(반자동 완료 라운드 Phase 5·7) — /order-approval
    라우터 3종의 배선·회사 격리만 확인한다(세부 게이트 로직 자체는
    tests/test_purchase_order_approval_service.py가 격리 단위로
    전담 — 여기서는 라우터가 그 서비스를 올바르게 호출하고 회사
    경계를 지키는지만 본다). PurchaseTaskRouterTestCase를 상속하지
    않는다 — unittest가 상속받은 test_ 메서드까지 다시 discover해
    같은 테스트가 두 클래스 이름으로 중복 실행되는 것을 피하기
    위해, 필요한 setUp만 이 클래스 안에 그대로 옮겨 둔다."""

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
                FundingLedger.__table__, PurchaseTask.__table__,
                PurchaseTaskCandidate.__table__,
                PurchaseTaskBudgetReservation.__table__,
                PurchaseRecord.__table__, PurchaseTaskTrackingInfo.__table__,
                PurchaseTaskPolicySetting.__table__,
                Role.__table__, User.__table__,
                Order.__table__, OrderItem.__table__,
                PurchaseOrderApproval.__table__,
                PurchaseChannelConnection.__table__,
                PurchaseChannelConnectionEvent.__table__,
                PurchaseOrderSubmissionAttempt.__table__,
                PurchaseOrderUnknownResolutionEvent.__table__,
                PurchaseSalesApplicationAttempt.__table__,
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
            email="b@example.com", address="서울",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.account_a = FundingAccount(
            company_id=self.company_a.id, total_funding=1000000.0,
        )
        self.db.add(self.account_a)
        self.db.commit()

        self.connection_a = PurchaseChannelConnection(
            company_id=self.company_a.id, mall_code="ONCHANNEL",
            account_label="테스트 계정", status="CONNECTED",
            connection_method="CREDENTIAL", idempotency_key="conn:a",
        )
        self.db.add(self.connection_a)
        self.db.commit()

        self.user_a = _FakeUser(1, self.company_a.id)
        self.user_b = _FakeUser(2, self.company_b.id)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create(self, user, key="pt:1"):

        return create_task(
            PurchaseTaskCreate(
                source_order_id=1, product_title="무선이어폰",
                brand="브랜드A", manufacturer="브랜드A", model_name="MODEL-1",
                gtin="1111111111111", capacity="100ml", quantity=1,
                color_or_scent="블랙", options=["기본"],
                coupang_sale_amount=30000, coupang_fee_amount=3000,
                purchase_deadline=NOW + timedelta(days=3),
                idempotency_key=key,
            ),
            current_user=user, db=self.db,
        )

    def test_get_order_approval_returns_none_when_not_started(self):

        task = self._create(self.user_a, key="oa:none")

        result = get_order_approval(task.id, current_user=self.user_a, db=self.db)
        self.assertIsNone(result)

    def test_confirm_then_finalize_then_get_reflects_active_state(self):

        task = self._create(self.user_a, key="oa:flow")

        confirmed = confirm_order_shipping_cost(
            task.id,
            ShippingCostConfirmationRequest(
                shipping_cost_amount=1000, is_free_shipping_confirmed=False,
                source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
                basis_memo="상품 상세 화면 캡처", external_product_id="CH1",
                connection_id=4,
            ),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(confirmed.status, PurchaseOrderApprovalStatus.PENDING_SHIPPING_COST)
        self.assertEqual(confirmed.shipping_cost_amount, 1000)

        finalized = finalize_order_approval(
            task.id,
            FinalizeOrderApprovalRequest(
                connection_id=4, item_amount=5000, current_points=1_000_000,
            ),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(finalized.status, PurchaseOrderApprovalStatus.ACTIVE)
        self.assertIsNotNone(finalized.expires_at)

        fetched = get_order_approval(
            task.id, connection_id=4, current_user=self.user_a, db=self.db,
        )
        self.assertEqual(fetched.status, PurchaseOrderApprovalStatus.ACTIVE)
        self.assertEqual(fetched.id, finalized.id)

    def test_other_company_cannot_confirm_shipping_cost_for_task(self):

        task = self._create(self.user_a, key="oa:isolated")

        with self.assertRaises(NotFoundException):
            confirm_order_shipping_cost(
                task.id,
                ShippingCostConfirmationRequest(
                    shipping_cost_amount=1000, is_free_shipping_confirmed=False,
                    source=ShippingCostConfirmationSource.ONCHANNEL_PRODUCT_PAGE,
                    external_product_id="CH1", connection_id=4,
                ),
                current_user=self.user_b, db=self.db,
            )

    def test_other_company_cannot_get_order_approval_for_task(self):

        task = self._create(self.user_a, key="oa:isolated2")

        with self.assertRaises(NotFoundException):
            get_order_approval(task.id, current_user=self.user_b, db=self.db)

    def test_invalid_shipping_cost_source_rejected_by_pydantic_schema(self):
        """스키마 자체는 자유 문자열을 허용하므로(source: str), 서비스
        계층의 검증이 실제로 걸어지는지 라우터 경유로도 확인한다."""

        task = self._create(self.user_a, key="oa:badsource")

        with self.assertRaises(BadRequestException):
            confirm_order_shipping_cost(
                task.id,
                ShippingCostConfirmationRequest(
                    shipping_cost_amount=1000, is_free_shipping_confirmed=False,
                    source="NOT_A_REAL_SOURCE", external_product_id="CH1",
                    connection_id=4,
                ),
                current_user=self.user_a, db=self.db,
            )

    def _submit_request(self, *, confirm_real_submission=False):

        return SubmitRealOrderRequest(
            connection_id=4, idempotency_key="submit-test-1",
            product_code="CH1234567",
            options=[OrderSubmissionReviewOptionInput(id="OPT1", qty=1)],
            recv_name="홍길동", recv_tell="02-000-0000", recv_mobile="010-0000-0000",
            zipcode="00000", address="서울시 어딘가",
            confirm_real_submission=confirm_real_submission,
        )

    def test_submit_without_recent_auth_rejected(self):
        """실제 발주 엔드포인트는 단순 조회보다 엄격하다 — 재인증
        토큰이 없으면 permission 검사·submit_order() 어느 쪽도
        건드리지 않고 즉시 거부한다."""

        task = self._create(self.user_a, key="submit:no-auth")

        with self.assertRaises(UnauthorizedException):
            submit_real_order(
                task.id, self._submit_request(),
                current_user=self.user_a, db=self.db, recent_auth_token=None,
            )

    def test_submit_without_confirm_flag_rejected_by_underlying_service(self):
        """재인증·권한은 전부 통과해도, confirm_real_submission이
        기본값(False)이면 submit_order() 자신의 fail-closed 게이트가
        여전히 막는다 — 이 라우터가 그 게이트를 대신 열어주지
        않는다."""

        admin = _FakeUser(9, self.company_a.id, role="super_admin")
        task = self._create(admin, key="submit:no-confirm")
        token, _ = issue_recent_auth_token(admin.id)

        with self.assertRaises(BadRequestException):
            submit_real_order(
                task.id, self._submit_request(confirm_real_submission=False),
                current_user=admin, db=self.db, recent_auth_token=token,
            )

    def test_other_company_cannot_submit_order_for_task(self):

        admin_b = _FakeUser(10, self.company_b.id, role="super_admin")
        task = self._create(self.user_a, key="submit:isolated")
        token, _ = issue_recent_auth_token(admin_b.id)

        with self.assertRaises(NotFoundException):
            submit_real_order(
                task.id, self._submit_request(confirm_real_submission=True),
                current_user=admin_b, db=self.db, recent_auth_token=token,
            )

    def _insert_attempt(
        self, task, *, status=OrderSubmissionStatus.RESULT_UNKNOWN,
        idempotency_key="hist-1",
    ):

        attempt = PurchaseOrderSubmissionAttempt(
            company_id=self.company_a.id, connection_id=self.connection_a.id,
            purchase_task_id=task.id, idempotency_key=idempotency_key,
            mall_code="ONCHANNEL", product_code="CH1234567",
            options_json='[{"id": "OPT1", "qty": 1}]', status=status,
        )
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)
        return attempt

    def test_list_attempts_returns_history_for_own_task(self):

        task = self._create(self.user_a, key="hist:own")
        self._insert_attempt(task, idempotency_key="hist-own-1")

        results = list_order_submission_attempts(
            task.id, current_user=self.user_a, db=self.db,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].idempotency_key, "hist-own-1")
        self.assertEqual(results[0].connection_id, self.connection_a.id)

    def test_other_company_cannot_list_attempts(self):

        task = self._create(self.user_a, key="hist:isolated")
        self._insert_attempt(task, idempotency_key="hist-isolated-1")

        with self.assertRaises(NotFoundException):
            list_order_submission_attempts(
                task.id, current_user=self.user_b, db=self.db,
            )

    def test_resolve_unknown_requires_result_unknown_status(self):

        task = self._create(self.user_a, key="resolve:not-unknown")
        attempt = self._insert_attempt(
            task, status=OrderSubmissionStatus.SUCCEEDED,
            idempotency_key="resolve-not-unknown-1",
        )

        with self.assertRaises(ConflictException):
            resolve_unknown_attempt(
                task.id, attempt.id,
                ResolveUnknownAttemptRequest(
                    resolution=UnknownResolutionStatus.ORDER_NOT_CONFIRMED,
                    basis="확인함",
                ),
                current_user=self.user_a, db=self.db,
            )

    def test_other_company_cannot_resolve_unknown_attempt(self):

        task = self._create(self.user_a, key="resolve:isolated")
        attempt = self._insert_attempt(task, idempotency_key="resolve-isolated-1")

        with self.assertRaises(NotFoundException):
            resolve_unknown_attempt(
                task.id, attempt.id,
                ResolveUnknownAttemptRequest(
                    resolution=UnknownResolutionStatus.ORDER_NOT_CONFIRMED,
                    basis="확인함",
                ),
                current_user=self.user_b, db=self.db,
            )

    def test_resolve_unknown_records_confirmed_order_code(self):

        task = self._create(self.user_a, key="resolve:confirmed")
        attempt = self._insert_attempt(task, idempotency_key="resolve-confirmed-1")

        result = resolve_unknown_attempt(
            task.id, attempt.id,
            ResolveUnknownAttemptRequest(
                resolution=UnknownResolutionStatus.ORDER_CONFIRMED,
                order_code="OC-CONFIRMED-ROUTER-1",
            ),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(result.unknown_resolution_status, UnknownResolutionStatus.ORDER_CONFIRMED)
        self.assertEqual(result.unknown_resolved_order_code, "OC-CONFIRMED-ROUTER-1")
        self.assertEqual(result.unknown_resolved_by, self.user_a.id)


if __name__ == "__main__":
    unittest.main()
