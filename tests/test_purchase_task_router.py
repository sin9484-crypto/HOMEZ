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
    PurchaseRecord,
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
    create_task,
    evaluate_and_prepare,
    get_email_preference,
    get_email_provider_setting,
    get_policy,
    get_task,
    import_csv,
    list_tasks,
    open_payment_page,
    preview_csv_upload,
    record_purchase,
    reconcile_orders,
    run_match_check,
    update_email_preference,
    update_email_provider_setting,
    update_policy,
)
from app.domains.purchase_task.schema import (
    CandidateCreate,
    EmailPreferenceUpdate,
    EmailProviderSettingUpdate,
    EvaluateRequest,
    MatchCheckRequest,
    PolicySettingUpdate,
    PurchaseTaskCreate,
    RecordPurchaseRequest,
    SourceAttributesInput,
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

    def __init__(self, user_id, company_id):
        self.id = user_id
        self.company_id = company_id


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


if __name__ == "__main__":
    unittest.main()
