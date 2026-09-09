"""
=========================================================
Homez OS

File : tests/test_automation_safety.py

HOMEZ V2.4 Commerce Safety Layer
Emergency Stop / Automation Mode / Execution Limit 검증

표준 라이브러리 unittest만 사용. 신규 패키지 없음.
homez.db는 사용하지 않고, 테스트 전용 임시 SQLite 파일 DB를 사용한다.
=========================================================
"""

import itertools
import os
import tempfile
import threading
import unittest

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ForbiddenException
from app.database.base import Base
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.constants import SafetyDecision
from app.domains.automation_safety.constants import SafetyReason
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService


class AutomationSafetyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

        self._counter = itertools.count(1)

    def tearDown(self):

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _key(self, label: str) -> str:

        return f"{label}:{next(self._counter)}"

    # --------------------------------------------------
    # 1) 기본 거부 (아무 설정도 없을 때)
    # --------------------------------------------------

    def test_default_mode_is_recommend_only_and_denies_execution(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)

            self.assertEqual(
                service.get_current_mode(), AutomationMode.RECOMMEND_ONLY,
            )

            result = service.evaluate(
                idempotency_key=self._key("default"),
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
            )

            self.assertEqual(result["decision"], SafetyDecision.DENY)
            self.assertIn(SafetyReason.MODE_NOT_ALLOWED, result["reasons"])

        finally:
            db.close()

    # --------------------------------------------------
    # 2) 추천 모드 명시적 설정 시에도 거부
    # --------------------------------------------------

    def test_recommend_only_mode_denies_execution(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.RECOMMEND_ONLY, set_by=1, is_admin=True,
            )

            result = service.evaluate(
                idempotency_key=self._key("recommend"),
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
            )

            self.assertEqual(result["decision"], SafetyDecision.DENY)
            self.assertIn(SafetyReason.MODE_NOT_ALLOWED, result["reasons"])

        finally:
            db.close()

    # --------------------------------------------------
    # 3) DISABLED 모드는 항상 거부
    # --------------------------------------------------

    def test_disabled_mode_denies_execution(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.DISABLED, set_by=1, is_admin=True,
            )

            result = service.evaluate(
                idempotency_key=self._key("disabled"),
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
            )

            self.assertEqual(result["decision"], SafetyDecision.DENY)
            self.assertIn(SafetyReason.MODE_NOT_ALLOWED, result["reasons"])

        finally:
            db.close()

    # --------------------------------------------------
    # 4) 승인 필요 → 승인 후 허용
    # --------------------------------------------------

    def test_operator_approval_mode_requires_approval_then_allows(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.OPERATOR_APPROVAL, set_by=1, is_admin=True,
            )

            unapproved = service.evaluate(
                idempotency_key=self._key("approval"),
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
                operator_approved=False,
            )
            self.assertEqual(
                unapproved["decision"], SafetyDecision.REQUIRE_APPROVAL,
            )
            self.assertIn(
                SafetyReason.OPERATOR_APPROVAL_REQUIRED, unapproved["reasons"],
            )

            approved = service.evaluate(
                idempotency_key=self._key("approval"),
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
                operator_approved=True,
            )
            self.assertEqual(approved["decision"], SafetyDecision.ALLOW)
            self.assertEqual(approved["reasons"], [])

        finally:
            db.close()

    # --------------------------------------------------
    # 5) Emergency Stop은 모드와 무관하게 우선 차단
    # --------------------------------------------------

    def test_emergency_stop_blocks_regardless_of_mode(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
            )

            self.assertFalse(service.is_emergency_stop_active())

            service.activate_emergency_stop(
                reason="긴급 점검", set_by=1, is_admin=True,
            )
            self.assertTrue(service.is_emergency_stop_active())

            blocked = service.evaluate(
                idempotency_key=self._key("stop"),
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
            )
            self.assertEqual(blocked["decision"], SafetyDecision.DENY)
            self.assertIn(
                SafetyReason.EMERGENCY_STOP_ACTIVE, blocked["reasons"],
            )

            service.deactivate_emergency_stop(cleared_by=1, is_admin=True)
            self.assertFalse(service.is_emergency_stop_active())

            allowed = service.evaluate(
                idempotency_key=self._key("stop"),
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
            )
            self.assertEqual(allowed["decision"], SafetyDecision.ALLOW)

        finally:
            db.close()

    # --------------------------------------------------
    # 6) Emergency Stop 활성화/해제 멱등성 (중복 행 방지)
    # --------------------------------------------------

    def test_emergency_stop_activate_deactivate_idempotent(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)

            first = service.activate_emergency_stop(
                reason="1차", set_by=1, is_admin=True,
            )
            second = service.activate_emergency_stop(
                reason="2차(무시되어야 함)", set_by=2, is_admin=True,
            )
            self.assertEqual(first.id, second.id)

            active_rows = (
                db.query(EmergencyStop)
                .filter(EmergencyStop.is_active.is_(True))
                .count()
            )
            self.assertEqual(active_rows, 1)

            cleared_first = service.deactivate_emergency_stop(
                cleared_by=9, is_admin=True,
            )
            cleared_second = service.deactivate_emergency_stop(
                cleared_by=10, is_admin=True,
            )
            self.assertEqual(cleared_first.id, cleared_second.id)

        finally:
            db.close()

    # --------------------------------------------------
    # 7) 자금 한도 경계값
    # --------------------------------------------------

    def test_funding_limit_boundary(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
            )
            service.set_limit(
                product_id=None,
                set_by=1,
                is_admin=True,
                daily_funding_limit=10000.0,
            )

            at_limit = service.evaluate(
                idempotency_key=self._key("funding"),
                product_id=1,
                funding_amount=10000.0,
                quantity=1,
            )
            self.assertEqual(at_limit["decision"], SafetyDecision.ALLOW)

            over_limit = service.evaluate(
                idempotency_key=self._key("funding"),
                product_id=1,
                funding_amount=1.0,
                quantity=1,
            )
            self.assertEqual(over_limit["decision"], SafetyDecision.DENY)
            self.assertIn(
                SafetyReason.FUNDING_LIMIT_EXCEEDED, over_limit["reasons"],
            )

        finally:
            db.close()

    # --------------------------------------------------
    # 8) 수량 한도 경계값
    # --------------------------------------------------

    def test_quantity_limit_boundary(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
            )
            service.set_limit(
                product_id=None,
                set_by=1,
                is_admin=True,
                daily_quantity_limit=5,
            )

            at_limit = service.evaluate(
                idempotency_key=self._key("qty"),
                product_id=1,
                funding_amount=0.0,
                quantity=5,
            )
            self.assertEqual(at_limit["decision"], SafetyDecision.ALLOW)

            over_limit = service.evaluate(
                idempotency_key=self._key("qty"),
                product_id=1,
                funding_amount=0.0,
                quantity=1,
            )
            self.assertEqual(over_limit["decision"], SafetyDecision.DENY)
            self.assertIn(
                SafetyReason.QUANTITY_LIMIT_EXCEEDED, over_limit["reasons"],
            )

        finally:
            db.close()

    # --------------------------------------------------
    # 9) 중복 요청 — ALLOW로 반환하지 않고 명시적 DENY(DUPLICATE_REQUEST)
    # --------------------------------------------------

    def test_duplicate_request_is_denied_not_allowed(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
            )
            service.set_limit(
                product_id=None,
                set_by=1,
                is_admin=True,
                daily_funding_limit=1000.0,
            )

            key = self._key("dup")

            first = service.evaluate(
                idempotency_key=key,
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
            )
            self.assertEqual(first["decision"], SafetyDecision.ALLOW)

            second = service.evaluate(
                idempotency_key=key,
                product_id=1,
                funding_amount=1000.0,
                quantity=1,
            )
            self.assertEqual(second["decision"], SafetyDecision.DENY)
            self.assertIn(
                SafetyReason.DUPLICATE_REQUEST, second["reasons"],
            )

            usage_count = (
                db.query(ExecutionUsage)
                .filter(ExecutionUsage.idempotency_key == key)
                .count()
            )
            self.assertEqual(usage_count, 1)

            # 한도가 이미 꽉 찬 상태에서 새 키로 요청하면 거부되어야 한다
            # (재호출이 한도를 다시 깎지 않았다는 방증).
            third = service.evaluate(
                idempotency_key=self._key("dup"),
                product_id=1,
                funding_amount=1.0,
                quantity=1,
            )
            self.assertEqual(third["decision"], SafetyDecision.DENY)
            self.assertIn(
                SafetyReason.FUNDING_LIMIT_EXCEEDED, third["reasons"],
            )

        finally:
            db.close()

    # --------------------------------------------------
    # 9-b) Emergency Stop은 idempotency 재조회보다 먼저 검사된다
    # --------------------------------------------------

    def test_emergency_stop_checked_before_idempotency_replay(self):
        """
        이미 ALLOW로 성공 처리된(idempotency_key가 존재하는) 요청이라도,
        그 이후 Emergency Stop이 활성화되면 동일 키로 재조회했을 때
        DUPLICATE_REQUEST가 아니라 EMERGENCY_STOP_ACTIVE로 거부되어야
        한다 — 사고 대응 중에는 idempotency 재조회 경로보다 Emergency
        Stop이 항상 우선한다.
        """

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
            )

            key = self._key("estop-priority")

            first = service.evaluate(
                idempotency_key=key,
                product_id=1,
                funding_amount=100.0,
                quantity=1,
            )
            self.assertEqual(first["decision"], SafetyDecision.ALLOW)

            service.activate_emergency_stop(
                reason="사고 대응", set_by=1, is_admin=True,
            )

            replay = service.evaluate(
                idempotency_key=key,
                product_id=1,
                funding_amount=100.0,
                quantity=1,
            )
            self.assertEqual(replay["decision"], SafetyDecision.DENY)
            self.assertIn(
                SafetyReason.EMERGENCY_STOP_ACTIVE, replay["reasons"],
            )
            self.assertNotIn(
                SafetyReason.DUPLICATE_REQUEST, replay["reasons"],
            )

        finally:
            db.close()

    # --------------------------------------------------
    # 9-c) 별도 커넥션 동시성: 실행 한도를 동시에 초과 통과할 수 없다
    # --------------------------------------------------

    def test_concurrent_requests_cannot_jointly_exceed_funding_limit(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        SessionLocal2 = sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

        setup_db = SessionLocal2()
        setup_service = SafetyService(setup_db)
        setup_service.set_mode(
            AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
        )
        setup_service.set_limit(
            product_id=None, set_by=1, is_admin=True,
            daily_funding_limit=100.0,
        )
        setup_db.close()

        thread_count = 10
        per_request_amount = 20.0  # 100 / 20 = 정확히 5건만 통과해야 함
        results = {}
        barrier = threading.Barrier(thread_count)

        def worker(i):

            thread_db = SessionLocal2()
            service = SafetyService(thread_db)

            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

            try:
                result = service.evaluate(
                    idempotency_key=f"concurrent-funding-{i}",
                    product_id=None,
                    funding_amount=per_request_amount,
                    quantity=1,
                )
                results[i] = result["decision"]
            except Exception as e:  # noqa: BLE001
                results[i] = f"ERROR:{type(e).__name__}"
            finally:
                thread_db.close()

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(thread_count)
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=30)

        try:
            self.assertEqual(len(results), thread_count)

            allowed = [k for k, v in results.items() if v == SafetyDecision.ALLOW]
            denied = [k for k, v in results.items() if v == SafetyDecision.DENY]

            self.assertEqual(
                len(allowed), 5,
                f"허용 건수가 5건이 아님(한도 초과 통과 가능성): {results}",
            )
            self.assertEqual(len(denied), 5)

            verify_db = SessionLocal2()

            try:
                period_usage = (
                    verify_db.query(ExecutionPeriodUsage)
                    .filter(ExecutionPeriodUsage.scope_key == "GLOBAL")
                    .first()
                )
                self.assertIsNotNone(period_usage)
                self.assertEqual(
                    float(period_usage.consumed_funding), 100.0,
                    "동시 요청 후 최종 소비량이 한도를 넘거나 미달함 — "
                    "원자성이 깨졌을 가능성",
                )

                usage_rows = verify_db.query(ExecutionUsage).count()
                self.assertEqual(usage_rows, 5)

            finally:
                verify_db.close()

        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 10) 감사 기록
    # --------------------------------------------------

    def test_audit_trail_recorded(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.LIMITED_AUTOMATION, set_by=7, is_admin=True,
            )

            stop = service.activate_emergency_stop(
                reason="테스트 점검", set_by=7, is_admin=True,
                audit_ref="INCIDENT-123",
            )
            self.assertEqual(stop.reason, "테스트 점검")
            self.assertEqual(stop.set_by, 7)
            self.assertIsNotNone(stop.set_at)
            self.assertEqual(stop.audit_ref, "INCIDENT-123")

            service.deactivate_emergency_stop(cleared_by=8, is_admin=True)

            service.evaluate(
                idempotency_key=self._key("audit"),
                product_id=42,
                funding_amount=2500.0,
                quantity=3,
            )

            usage = (
                db.query(ExecutionUsage)
                .filter(ExecutionUsage.product_id == 42)
                .first()
            )
            self.assertIsNotNone(usage)
            self.assertEqual(float(usage.funding_amount), 2500.0)
            self.assertEqual(usage.quantity, 3)
            self.assertIsNotNone(usage.occurred_at)

            mode_history = (
                db.query(AutomationModeState)
                .order_by(AutomationModeState.id.asc())
                .all()
            )
            self.assertEqual(len(mode_history), 1)
            self.assertEqual(mode_history[0].set_by, 7)

        finally:
            db.close()

    # --------------------------------------------------
    # 11) 권한 없는 변경 차단
    # --------------------------------------------------

    def test_unauthorized_changes_are_blocked(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)

            with self.assertRaises(ForbiddenException):
                service.set_mode(
                    AutomationMode.LIMITED_AUTOMATION,
                    set_by=1,
                    is_admin=False,
                )

            with self.assertRaises(ForbiddenException):
                service.activate_emergency_stop(
                    reason="무단 시도", set_by=1, is_admin=False,
                )

            with self.assertRaises(ForbiddenException):
                service.deactivate_emergency_stop(
                    cleared_by=1, is_admin=False,
                )

            with self.assertRaises(ForbiddenException):
                service.set_limit(
                    product_id=None,
                    set_by=1,
                    is_admin=False,
                    daily_funding_limit=1000.0,
                )

            # 실제로 아무 것도 반영되지 않았는지 확인
            self.assertIsNone(service.repository.get_current_mode_state())
            self.assertIsNone(service.repository.get_latest_emergency_stop())
            self.assertIsNone(service.repository.get_active_limit(None))

        finally:
            db.close()

    # --------------------------------------------------
    # 12) 잘못된 요청 거부
    # --------------------------------------------------

    def test_invalid_request_is_denied(self):

        db = self.SessionLocal()

        try:
            service = SafetyService(db)
            service.set_mode(
                AutomationMode.LIMITED_AUTOMATION, set_by=1, is_admin=True,
            )

            negative_funding = service.evaluate(
                idempotency_key=self._key("invalid"),
                product_id=1,
                funding_amount=-1.0,
                quantity=1,
            )
            self.assertEqual(negative_funding["decision"], SafetyDecision.DENY)
            self.assertIn(
                SafetyReason.INVALID_REQUEST, negative_funding["reasons"],
            )

            negative_qty = service.evaluate(
                idempotency_key=self._key("invalid"),
                product_id=1,
                funding_amount=1.0,
                quantity=-1,
            )
            self.assertEqual(negative_qty["decision"], SafetyDecision.DENY)
            self.assertIn(
                SafetyReason.INVALID_REQUEST, negative_qty["reasons"],
            )

        finally:
            db.close()

    # --------------------------------------------------
    # 13) Domain 경계: Funding/Settlement Model을 직접 참조하지 않음
    # --------------------------------------------------

    def test_automation_safety_does_not_import_funding_or_settlement_models(self):

        import app.domains.automation_safety.model as safety_model
        import app.domains.automation_safety.service as safety_service

        for module in (safety_model, safety_service):
            source = module.__file__
            with open(source, encoding="utf-8") as f:
                content = f.read()

            self.assertNotIn("app.domains.funding.model", content)
            self.assertNotIn("app.domains.settlement.model", content)
            self.assertNotIn("app.domains.order", content)
            self.assertNotIn("app.domains.purchase", content)


if __name__ == "__main__":
    unittest.main()
