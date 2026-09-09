"""
=========================================================
Homez OS

File : tests/test_notification_center.py

Gate Y-3(2026-08-12) — 알림 센터 검증. 전부 임시 SQLite 파일만
사용한다.
=========================================================
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.company.model import Company  # noqa: F401 (User FK 해석용)
from app.domains.notification_center import router as notification_router_module
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationRead
from app.domains.notification_center.service import NotificationService
from app.domains.role.model import Role  # noqa: F401 (User FK 해석용)
from app.domains.user.model import User  # noqa: F401 (User FK 해석용)


class NotificationCenterTestCase(unittest.TestCase):

    COMPANY_A = 1
    COMPANY_B = 2
    USER_1 = 10
    USER_2 = 20

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_notif_test_"))
        self.app_db_path = self.tmp_dir / "app.db"
        self.engine = create_engine(f"sqlite:///{self.app_db_path}")
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.service = NotificationService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ----------------------------------------------------
    # 생성 검증
    # ----------------------------------------------------

    def test_notify_user_creates_personal_notification(self):

        n = self.service.notify_user(
            company_id=self.COMPANY_A,
            user_id=self.USER_1,
            category="backup",
            level="info",
            title="백업 완료",
            message="정상적으로 완료되었습니다.",
        )

        self.assertIsNotNone(n.id)
        self.assertEqual(n.user_id, self.USER_1)
        self.assertFalse(n.is_read)

    def test_notify_company_creates_broadcast_notification(self):

        n = self.service.notify_company(
            company_id=self.COMPANY_A,
            category="system",
            level="warning",
            title="점검 예정",
            message="오늘 밤 점검이 있습니다.",
        )

        self.assertIsNone(n.user_id)

    def test_invalid_level_rejected(self):

        with self.assertRaises(BadRequestException):
            self.service.notify_user(
                company_id=self.COMPANY_A,
                user_id=self.USER_1,
                category="backup",
                level="not_a_real_level",
                title="t",
                message="m",
            )

    def test_empty_fields_rejected(self):

        with self.assertRaises(BadRequestException):
            self.service.notify_user(
                company_id=self.COMPANY_A,
                user_id=self.USER_1,
                category="backup",
                level="info",
                title="",
                message="m",
            )

    # ----------------------------------------------------
    # 회사 격리
    # ----------------------------------------------------

    def test_company_isolation_personal(self):

        self.service.notify_user(
            company_id=self.COMPANY_A,
            user_id=self.USER_1,
            category="backup",
            level="info",
            title="A사 알림",
            message="m",
        )

        rows_b = self.service.list_for_user(self.COMPANY_B, self.USER_1)
        self.assertEqual(len(rows_b), 0)

        rows_a = self.service.list_for_user(self.COMPANY_A, self.USER_1)
        self.assertEqual(len(rows_a), 1)

    def test_company_isolation_broadcast(self):

        self.service.notify_company(
            company_id=self.COMPANY_A,
            category="system",
            level="info",
            title="A사 공지",
            message="m",
        )

        rows_b = self.service.list_for_user(self.COMPANY_B, self.USER_1)
        self.assertEqual(len(rows_b), 0)

    def test_personal_notification_not_visible_to_other_user(self):

        self.service.notify_user(
            company_id=self.COMPANY_A,
            user_id=self.USER_1,
            category="backup",
            level="info",
            title="1번 사용자 전용",
            message="m",
        )

        rows_user2 = self.service.list_for_user(
            self.COMPANY_A,
            self.USER_2,
        )
        self.assertEqual(len(rows_user2), 0)

    def test_broadcast_visible_to_all_users_in_company(self):

        self.service.notify_company(
            company_id=self.COMPANY_A,
            category="system",
            level="info",
            title="전체 공지",
            message="m",
        )

        rows_user1 = self.service.list_for_user(
            self.COMPANY_A,
            self.USER_1,
        )
        rows_user2 = self.service.list_for_user(
            self.COMPANY_A,
            self.USER_2,
        )
        self.assertEqual(len(rows_user1), 1)
        self.assertEqual(len(rows_user2), 1)

    # ----------------------------------------------------
    # 읽음 처리 — 핵심: 공지 읽음이 다른 사용자에게 전파되지 않는다
    # ----------------------------------------------------

    def test_mark_read_personal_notification(self):

        n = self.service.notify_user(
            company_id=self.COMPANY_A,
            user_id=self.USER_1,
            category="backup",
            level="info",
            title="t",
            message="m",
        )

        self.service.mark_read(self.COMPANY_A, self.USER_1, n.id)

        rows = self.service.list_for_user(self.COMPANY_A, self.USER_1)
        self.assertTrue(rows[0][1])  # is_read

    def test_mark_read_broadcast_does_not_leak_to_other_user(self):
        """
        핵심 회귀 테스트: 회사 공지를 사용자 1이 읽어도, 사용자 2에게는
        여전히 안읽음으로 보여야 한다(공유 행에 직접 쓰면 실패한다).
        """

        n = self.service.notify_company(
            company_id=self.COMPANY_A,
            category="system",
            level="info",
            title="공지",
            message="m",
        )

        self.service.mark_read(self.COMPANY_A, self.USER_1, n.id)

        rows_user1 = self.service.list_for_user(
            self.COMPANY_A,
            self.USER_1,
        )
        rows_user2 = self.service.list_for_user(
            self.COMPANY_A,
            self.USER_2,
        )

        self.assertTrue(rows_user1[0][1])
        self.assertFalse(rows_user2[0][1])

        # 원본 행 자체는 훼손되지 않아야 한다(다른 도메인이 이
        # 알림을 다시 조회할 때도 안전).
        raw = self.db.query(Notification).filter(
            Notification.id == n.id,
        ).first()
        self.assertFalse(raw.is_read)
        self.assertIsNone(raw.read_at)

        # NotificationRead에는 사용자 1의 읽음 행만 있어야 한다.
        reads = self.db.query(NotificationRead).all()
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0].user_id, self.USER_1)

    def test_mark_read_is_idempotent(self):

        n = self.service.notify_company(
            company_id=self.COMPANY_A,
            category="system",
            level="info",
            title="공지",
            message="m",
        )

        self.service.mark_read(self.COMPANY_A, self.USER_1, n.id)
        self.service.mark_read(self.COMPANY_A, self.USER_1, n.id)

        reads = self.db.query(NotificationRead).all()
        self.assertEqual(len(reads), 1)

    def test_mark_read_nonexistent_notification_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.mark_read(self.COMPANY_A, self.USER_1, 99999)

    def test_mark_read_other_companys_notification_raises_not_found(self):

        n = self.service.notify_user(
            company_id=self.COMPANY_A,
            user_id=self.USER_1,
            category="backup",
            level="info",
            title="t",
            message="m",
        )

        with self.assertRaises(NotFoundException):
            self.service.mark_read(self.COMPANY_B, self.USER_1, n.id)

    def test_count_unread(self):

        self.service.notify_user(
            company_id=self.COMPANY_A,
            user_id=self.USER_1,
            category="backup",
            level="info",
            title="1",
            message="m",
        )
        self.service.notify_company(
            company_id=self.COMPANY_A,
            category="system",
            level="info",
            title="2",
            message="m",
        )

        self.assertEqual(
            self.service.count_unread(self.COMPANY_A, self.USER_1),
            2,
        )

        self.service.mark_all_read(self.COMPANY_A, self.USER_1)

        self.assertEqual(
            self.service.count_unread(self.COMPANY_A, self.USER_1),
            0,
        )
        # 다른 사용자는 여전히 공지 1건이 안읽음이어야 한다.
        self.assertEqual(
            self.service.count_unread(self.COMPANY_A, self.USER_2),
            1,
        )

    def test_unread_only_filter(self):

        n1 = self.service.notify_user(
            company_id=self.COMPANY_A,
            user_id=self.USER_1,
            category="backup",
            level="info",
            title="1",
            message="m",
        )
        self.service.notify_user(
            company_id=self.COMPANY_A,
            user_id=self.USER_1,
            category="backup",
            level="info",
            title="2",
            message="m",
        )

        self.service.mark_read(self.COMPANY_A, self.USER_1, n1.id)

        rows = self.service.list_for_user(
            self.COMPANY_A,
            self.USER_1,
            unread_only=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0].title, "2")


# ----------------------------------------------------
# Router 계약 — 인증 필요(전체 역할 접근 가능) + 요청 바디 무결성
# ----------------------------------------------------


class NotificationRouterContractTestCase(unittest.TestCase):

    def test_all_routes_require_authentication(self):
        """
        admin_guard가 아니라 get_current_user여야 한다 — Manager/
        Staff/Viewer도 자기 알림은 봐야 한다. 동시에 "아무 인증도
        없이" 노출된 라우트는 없어야 한다(dependant.dependencies가
        비어 있지 않아야 함).
        """

        routes = notification_router_module.router.routes
        # Gate PT-2F(16차 지시)에서 GET /notifications/unified 추가 — 5개.
        # Gate PT-3(2026-08-23 17차 지시)에서 알림 설정 6개 추가
        # (GET/PUT preference, GET catalog, GET/PUT event-preferences,
        # POST preference/test-email) — 총 11개.
        self.assertEqual(len(routes), 11)

        for route in routes:
            self.assertGreater(len(route.dependant.dependencies), 0)

    def test_no_company_or_user_id_in_request_bodies(self):
        """
        company_id/user_id는 오직 current_user에서만 와야 한다 —
        요청 스키마 어디에도 그런 필드가 없는지 확인한다(다른
        회사·다른 사용자 알림을 조작할 방법이 없어야 한다).
        """

        from app.domains.notification_center.schema import (
            MarkAllReadResponse,
            MarkReadResponse,
            NotificationCatalogEventResponse,
            NotificationEventPreferenceResponse,
            NotificationEventPreferenceUpdate,
            NotificationPreferenceResponse,
            NotificationPreferenceUpdate,
            NotificationResponse,
            TestNotificationEmailRequest,
            UnifiedNotificationListResponse,
            UnifiedNotificationResponse,
            UnreadCountResponse,
        )

        for model in (
            MarkAllReadResponse,
            MarkReadResponse,
            NotificationResponse,
            UnifiedNotificationResponse,
            UnifiedNotificationListResponse,
            UnreadCountResponse,
            NotificationPreferenceResponse,
            NotificationPreferenceUpdate,
            NotificationEventPreferenceResponse,
            NotificationEventPreferenceUpdate,
            NotificationCatalogEventResponse,
            TestNotificationEmailRequest,
        ):
            field_names = set(model.model_fields.keys())
            self.assertNotIn("company_id", field_names)
            self.assertNotIn("user_id", field_names)


# ----------------------------------------------------
# Gate PT-2F — 단일 알림 조회 계약(UnifiedNotificationService)
# ----------------------------------------------------


class UnifiedNotificationServiceTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_unified_notif_test_"))
        self.app_db_path = self.tmp_dir / "app.db"
        self.engine = create_engine(f"sqlite:///{self.app_db_path}")

        from app.domains.automation_safety.model import AutomationModeState
        from app.domains.automation_safety.model import EmergencyStop
        from app.domains.automation_safety.model import ExecutionLimit
        from app.domains.automation_safety.model import ExecutionPeriodUsage
        from app.domains.automation_safety.model import ExecutionUsage
        from app.domains.purchase_task.model import PurchaseTask

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, User.__table__,
                Notification.__table__, NotificationRead.__table__,
                PurchaseTask.__table__,
                AutomationModeState.__table__, EmergencyStop.__table__,
                ExecutionLimit.__table__, ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.service = NotificationService(self.db)

        self.company = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_task(self, status="SEARCH_REQUIRED"):

        from app.domains.purchase_task.model import PurchaseTask

        task = PurchaseTask(
            company_id=self.company.id, source_order_id=1,
            product_title="테스트 상품", quantity=1, status=status,
            idempotency_key=f"unified-test:{status}:{id(object())}",
        )
        self.db.add(task)
        self.db.commit()
        return task

    def test_purchase_task_notification_gets_action_required_when_open(self):

        from app.domains.notification_center.unified_service import (
            UnifiedNotificationService,
        )

        task = self._create_task(status="REVIEW_REQUIRED")
        self.service.notify_user(
            company_id=self.company.id, user_id=1, category="purchase_task",
            level="info", title="확인 필요", message="m",
            link_path=f"purchase-task-detail?id={task.id}",
        )

        result = UnifiedNotificationService(self.db).list_unified(
            self.company.id, 1,
        )
        self.assertFalse(result["emergency_stop_active"])
        item = result["items"][0]
        self.assertEqual(item["purchase_task_id"], task.id)
        self.assertEqual(item["action_status"], "ACTION_REQUIRED")

    def test_purchase_task_notification_gets_action_done_when_terminal(self):

        from app.domains.notification_center.unified_service import (
            UnifiedNotificationService,
        )

        task = self._create_task(status="COMPLETED")
        self.service.notify_user(
            company_id=self.company.id, user_id=1, category="purchase_task",
            level="info", title="완료", message="m",
            link_path=f"purchase-task-detail?id={task.id}",
        )

        result = UnifiedNotificationService(self.db).list_unified(
            self.company.id, 1,
        )
        self.assertEqual(result["items"][0]["action_status"], "ACTION_DONE")

    def test_duplicate_link_path_is_deduplicated_keeping_latest(self):

        from app.domains.notification_center.unified_service import (
            UnifiedNotificationService,
        )

        task = self._create_task(status="SEARCH_REQUIRED")
        self.service.notify_user(
            company_id=self.company.id, user_id=1, category="purchase_task",
            level="info", title="첫 알림", message="m",
            link_path=f"purchase-task-detail?id={task.id}",
        )
        self.service.notify_user(
            company_id=self.company.id, user_id=1, category="purchase_task",
            level="info", title="최신 알림", message="m",
            link_path=f"purchase-task-detail?id={task.id}",
        )

        result = UnifiedNotificationService(self.db).list_unified(
            self.company.id, 1,
        )
        matching = [
            it for it in result["items"] if it["purchase_task_id"] == task.id
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["title"], "최신 알림")

    def test_emergency_stop_active_is_flagged_and_prioritized(self):

        from app.domains.automation_safety.model import EmergencyStop
        from app.domains.notification_center.unified_service import (
            UnifiedNotificationService,
        )

        self.db.add(EmergencyStop(
            id=1, is_active=True, set_by=1, reason="테스트",
        ))
        self.db.commit()

        self.service.notify_user(
            company_id=self.company.id, user_id=1, category="purchase_task",
            level="info", title="일반 알림", message="m",
        )

        result = UnifiedNotificationService(self.db).list_unified(
            self.company.id, 1,
        )
        self.assertTrue(result["emergency_stop_active"])
        self.assertEqual(result["items"][0]["category"], "automation_safety")

    def test_non_purchase_task_notification_has_no_action_status(self):

        from app.domains.notification_center.unified_service import (
            UnifiedNotificationService,
        )

        self.service.notify_company(
            company_id=self.company.id, category="system", level="info",
            title="일반 공지", message="m",
        )

        result = UnifiedNotificationService(self.db).list_unified(
            self.company.id, 1,
        )
        item = result["items"][0]
        self.assertIsNone(item["purchase_task_id"])
        self.assertIsNone(item["action_status"])


if __name__ == "__main__":
    unittest.main()
