"""
=========================================================
Homez OS

File : tests/test_purchase_channel_connection_service.py

Gate PT-3(2026-09-08, item 7 "사용자 계정 기반 매입 연결") —
PurchaseChannelConnectionService 격리 테스트. 임시 SQLite 파일만
사용, 실제 homez.db·Windows Credential Manager를 전혀 건드리지
않는다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.constants import BROWSER_LOGIN_TRUST_WINDOW_DAYS
from app.domains.purchase_task.constants import ChannelConnectionStatus
from app.domains.purchase_task.constants import ConnectionMethod
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용


class ChannelConnectionServiceTestCaseBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, PurchaseChannelConnection.__table__,
                PurchaseChannelConnectionEvent.__table__,
            ],
        )
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

        self.credential_store = InMemoryCredentialStore()
        self.service = PurchaseChannelConnectionService(
            self.db, credential_store=self.credential_store,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)


class CreateAndListTestCase(ChannelConnectionServiceTestCaseBase):

    def test_create_defaults_to_login_required_not_connected(self):

        connection = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING",
            account_label="사업자 계정 A",
        )
        self.assertEqual(connection.status, ChannelConnectionStatus.LOGIN_REQUIRED)
        self.assertIsNone(connection.verified_at)
        self.assertTrue(connection.is_active)

    def test_multiple_accounts_on_same_mall_are_both_kept(self):
        """같은 매입처의 다중 계정 — 지시문 6번 격리 항목."""

        c1 = self.service.create_connection(
            self.company_a.id, mall_code="ELEVENST", account_label="계정1",
        )
        c2 = self.service.create_connection(
            self.company_a.id, mall_code="ELEVENST", account_label="계정2",
        )
        self.assertNotEqual(c1.id, c2.id)

        listed = self.service.list_connections(self.company_a.id, mall_code="ELEVENST")
        self.assertEqual({c.id for c in listed}, {c1.id, c2.id})

    def test_create_is_idempotent_via_idempotency_key(self):

        first = self.service.create_connection(
            self.company_a.id, mall_code="GMARKET", account_label="계정1",
            idempotency_key="dup-1",
        )
        second = self.service.create_connection(
            self.company_a.id, mall_code="GMARKET", account_label="다른 이름으로 재시도",
            idempotency_key="dup-1",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.account_label, "계정1")  # 두 번째 호출 값으로 바뀌지 않음

    def test_blank_label_rejected(self):

        with self.assertRaises(BadRequestException):
            self.service.create_connection(
                self.company_a.id, mall_code="GMARKET", account_label="   ",
            )


class CompanyIsolationTestCase(ChannelConnectionServiceTestCaseBase):
    """회사 간 조회·수정·실행 격리 — 지시문 6번 격리 항목 1순위."""

    def setUp(self):

        super().setUp()
        self.connection = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A사 계정",
        )

    def test_other_company_cannot_read(self):

        with self.assertRaises(NotFoundException):
            self.service.get_connection_or_404(self.connection.id, self.company_b.id)

    def test_other_company_cannot_rename(self):

        with self.assertRaises(NotFoundException):
            self.service.rename_connection(
                self.connection.id, self.company_b.id, "탈취 시도",
            )

    def test_other_company_cannot_deactivate(self):

        with self.assertRaises(NotFoundException):
            self.service.deactivate_connection(self.connection.id, self.company_b.id)

    def test_other_company_cannot_select_for_execution(self):

        with self.assertRaises(NotFoundException):
            self.service.select_connection_for_task(
                self.connection.id, self.company_b.id,
            )

    def test_other_company_list_never_includes_it(self):

        listed = self.service.list_connections(self.company_b.id)
        self.assertEqual(listed, [])


class RenameTestCase(ChannelConnectionServiceTestCaseBase):

    def test_rename_keeps_same_id_and_preserves_verified_state(self):
        """표시 이름 변경 후 연결 유지 — 지시문 6번 격리 항목."""

        connection = self.service.create_connection(
            self.company_a.id, mall_code="AUCTION", account_label="원래 이름",
        )
        self.service.mark_verified(connection.id, self.company_a.id)

        renamed = self.service.rename_connection(
            connection.id, self.company_a.id, "새 이름",
        )
        self.assertEqual(renamed.id, connection.id)
        self.assertEqual(renamed.account_label, "새 이름")
        self.assertEqual(renamed.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(renamed.verified_at)


class CredentialVsVerifiedDistinctionTestCase(ChannelConnectionServiceTestCaseBase):
    """자격증명 존재와 실제 인증 성공의 구분 — 지시문 2번/6번 핵심."""

    def test_check_connection_status_never_sets_verified_at_by_itself(self):

        connection = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A사 계정",
        )
        # 온채널 Adapter는 실제 Windows Credential Manager를 조회하므로
        # 이 테스트 환경(자격증명 없음)에서는 NOT_CONNECTED로 남는다
        # (2026-09-08 재정정 — credential_capable 매입처는 자격증명이
        # 없으면 항상 NOT_CONNECTED, LOGIN_REQUIRED가 아니다).
        refreshed = self.service.check_connection_status(
            connection.id, self.company_a.id,
        )
        self.assertIsNone(refreshed.verified_at)
        self.assertNotEqual(refreshed.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(refreshed.last_checked_at)

    def test_mark_verified_is_the_only_way_to_reach_connected_for_browser_login(self):

        connection = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A사 계정",
        )
        self.assertNotEqual(connection.status, ChannelConnectionStatus.CONNECTED)

        verified = self.service.mark_verified(connection.id, self.company_a.id)
        self.assertEqual(verified.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(verified.verified_at)

    def test_mark_verified_rejected_for_credential_method_connection(self):
        """2026-09-08 재정정(id=3/4 사고 재발 방지) — CREDENTIAL 방식은
        자격증명 없이(또는 있어도) 사람이 "연결 확인 완료"를 눌러
        CONNECTED를 만들 수 없다. id=3/4가 실제로 이 경로로 자격증명
        없이 CONNECTED가 됐던 사고의 재발 방지 테스트."""

        connection = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A사 계정",
        )
        with self.assertRaises(BadRequestException):
            self.service.mark_verified(connection.id, self.company_a.id)

        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertIsNone(row.verified_at)
        self.assertNotEqual(row.status, ChannelConnectionStatus.CONNECTED)

    def test_stale_verified_at_without_credential_is_not_trusted(self):
        """id=3/4 사고 재현 + 회귀 방지 — DB에 verified_at이 남아있어도
        (과거의 잘못된 self-attestation 흔적) 지금 자격증명이 없으면
        check_connection_status()가 그 값을 신뢰하지 않고 NOT_CONNECTED로
        바로잡는다. 실제 사고 데이터(id=3, id=4)와 동일한 재현 조건:
        자격증명 미저장 + verified_at만 과거에 채워짐."""

        connection = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A사 계정",
        )
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow()
        self.db.commit()

        refreshed = self.service.check_connection_status(
            connection.id, self.company_a.id,
        )
        self.assertEqual(refreshed.status, ChannelConnectionStatus.NOT_CONNECTED)


class DeactivateReactivateTestCase(ChannelConnectionServiceTestCaseBase):

    def test_deactivate_then_execution_is_blocked(self):
        """비활성 계정 실행 차단 — 지시문 6번 격리 항목."""

        connection = self.service.create_connection(
            self.company_a.id, mall_code="GMARKET", account_label="A사 계정",
        )
        self.service.mark_verified(connection.id, self.company_a.id)
        self.service.deactivate_connection(connection.id, self.company_a.id)

        with self.assertRaises(ConflictException):
            self.service.select_connection_for_task(connection.id, self.company_a.id)

    def test_unverified_active_connection_also_blocked_from_execution(self):

        connection = self.service.create_connection(
            self.company_a.id, mall_code="GMARKET", account_label="A사 계정",
        )
        with self.assertRaises(ConflictException):
            self.service.select_connection_for_task(connection.id, self.company_a.id)

    def test_verified_active_connection_is_selectable(self):

        connection = self.service.create_connection(
            self.company_a.id, mall_code="GMARKET", account_label="A사 계정",
        )
        self.service.mark_verified(connection.id, self.company_a.id)

        selected = self.service.select_connection_for_task(
            connection.id, self.company_a.id, expected_mall_code="GMARKET",
        )
        self.assertEqual(selected.id, connection.id)

    def test_mall_code_mismatch_rejected(self):

        connection = self.service.create_connection(
            self.company_a.id, mall_code="GMARKET", account_label="A사 계정",
        )
        self.service.mark_verified(connection.id, self.company_a.id)

        with self.assertRaises(BadRequestException):
            self.service.select_connection_for_task(
                connection.id, self.company_a.id, expected_mall_code="ELEVENST",
            )

    def test_reactivate_restores_execution_eligibility(self):

        connection = self.service.create_connection(
            self.company_a.id, mall_code="GMARKET", account_label="A사 계정",
        )
        self.service.mark_verified(connection.id, self.company_a.id)
        self.service.deactivate_connection(connection.id, self.company_a.id)
        self.service.reactivate_connection(connection.id, self.company_a.id)

        selected = self.service.select_connection_for_task(
            connection.id, self.company_a.id,
        )
        self.assertTrue(selected.is_active)


class DeleteConnectionTestCase(ChannelConnectionServiceTestCaseBase):
    """2026-09-08 후속(사용자 요청 — "불필요한것 선택 삭제") —
    delete_connection()은 "연결 해제"(비활성화, 되돌릴 수 있음)와
    달리 행 자체를 완전히 지운다. Model docstring의 기존 불변식
    (과거 PurchaseTask/PurchaseRecord의 channel_connection_id는
    절대 지우지 않는다)을 지키기 위해, 실제로 참조된 적이 있는
    연결은 삭제를 거부해야 한다."""

    def setUp(self):

        super().setUp()

        from app.domains.purchase_task.model import PurchaseRecord, PurchaseTask

        Base.metadata.create_all(
            bind=self.engine, tables=[PurchaseTask.__table__, PurchaseRecord.__table__],
        )
        self.PurchaseTask = PurchaseTask
        self.PurchaseRecord = PurchaseRecord

    def test_delete_unused_connection_removes_row_and_events(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="테스트용",
        )
        connection_id = c.id

        self.service.delete_connection(connection_id, self.company_a.id)

        self.assertIsNone(
            self.db.query(PurchaseChannelConnection).get(connection_id),
        )
        self.assertEqual(
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(PurchaseChannelConnectionEvent.connection_id == connection_id)
            .count(),
            0,
        )

    def test_delete_blocked_when_referenced_by_purchase_task(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="사용된 계정",
        )
        task = self.PurchaseTask(
            company_id=self.company_a.id, source_order_id=1,
            product_title="테스트 상품", idempotency_key="task-1",
            channel_connection_id=c.id,
        )
        self.db.add(task)
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.delete_connection(c.id, self.company_a.id)

        self.assertIsNotNone(self.db.query(PurchaseChannelConnection).get(c.id))

    def test_delete_blocked_when_referenced_by_purchase_record(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="사용된 계정",
        )
        task = self.PurchaseTask(
            company_id=self.company_a.id, source_order_id=1,
            product_title="테스트 상품", idempotency_key="task-1",
        )
        self.db.add(task)
        self.db.commit()
        record = self.PurchaseRecord(
            company_id=self.company_a.id, purchase_task_id=task.id,
            shopping_mall_code="NAVER_SHOPPING", channel_connection_id=c.id,
            external_order_number="ORDER-1", actual_amount=10000,
            purchased_at=datetime.utcnow(), recorded_by=1,
            idempotency_key="record-1",
        )
        self.db.add(record)
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.delete_connection(c.id, self.company_a.id)

        self.assertIsNotNone(self.db.query(PurchaseChannelConnection).get(c.id))

    def test_delete_rejects_other_company(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A사 계정",
        )
        with self.assertRaises(NotFoundException):
            self.service.delete_connection(c.id, self.company_b.id)

        self.assertIsNotNone(self.db.query(PurchaseChannelConnection).get(c.id))

    def test_delete_credential_connection_also_removes_stored_credential(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="온채널 계정",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        self.assertTrue(self.credential_store.exists(c.credential_reference))

        self.service.delete_connection(c.id, self.company_a.id)

        self.assertFalse(self.credential_store.exists("homez_channel_connection_" + str(c.id)))

    def test_delete_connection_without_credential_saved_does_not_raise(self):
        """credential_reference는 있지만 실제 자격증명은 저장된 적
        없는 경우(CredentialNotFoundError) — 삭제가 그 이유로
        실패해서는 안 된다(최선 노력 정리일 뿐)."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="미등록 계정",
        )
        self.service.delete_connection(c.id, self.company_a.id)

        self.assertIsNone(self.db.query(PurchaseChannelConnection).get(c.id))


class AuditTrailTestCase(ChannelConnectionServiceTestCaseBase):

    def test_every_lifecycle_action_leaves_an_event(self):

        connection = self.service.create_connection(
            self.company_a.id, mall_code="GMARKET", account_label="A사 계정",
            triggered_by=42,
        )
        self.service.mark_verified(connection.id, self.company_a.id, triggered_by=42)
        self.service.rename_connection(
            connection.id, self.company_a.id, "새 이름", triggered_by=42,
        )
        self.service.deactivate_connection(
            connection.id, self.company_a.id, triggered_by=42,
        )
        self.service.reactivate_connection(
            connection.id, self.company_a.id, triggered_by=42,
        )

        events = (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(PurchaseChannelConnectionEvent.connection_id == connection.id)
            .order_by(PurchaseChannelConnectionEvent.id.asc())
            .all()
        )
        event_types = [e.event_type for e in events]
        self.assertEqual(
            event_types,
            ["CREATED", "VERIFIED", "LABEL_RENAMED", "DEACTIVATED", "REACTIVATED"],
        )
        for event in events:
            self.assertEqual(event.triggered_by, 42)
            self.assertEqual(event.company_id, self.company_a.id)


class NoSecretLeakageTestCase(ChannelConnectionServiceTestCaseBase):

    def test_connection_row_never_stores_secret_value(self):
        """비밀정보 미노출 — 지시문 6번 격리 항목. Model 자체에 비밀값을
        담을 컬럼이 없다는 것을 스키마 레벨에서 재확인한다."""

        column_names = {c.name for c in PurchaseChannelConnection.__table__.columns}
        for forbidden in ("password", "pw", "cookie", "card_number", "cvc", "pin"):
            self.assertFalse(
                any(forbidden in name for name in column_names),
                f"{forbidden}로 보이는 컬럼이 있습니다: {column_names}",
            )


class ServerSideConnectionMethodTestCase(ChannelConnectionServiceTestCaseBase):
    """2026-09-08 후속 — connection_method는 클라이언트가 정하지
    못한다. mall_code만으로 서버가 확정한다(item 7 지시 2/3번:
    "브라우저 기반 매입처는 별도 연결 방식으로 구분한다")."""

    def test_naver_shopping_is_browser_login(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.assertEqual(c.connection_method, ConnectionMethod.BROWSER_LOGIN)

    def test_elevenst_gmarket_auction_are_browser_login(self):

        for mall in ("ELEVENST", "GMARKET", "AUCTION"):
            c = self.service.create_connection(
                self.company_a.id, mall_code=mall, account_label=f"A-{mall}",
            )
            self.assertEqual(c.connection_method, ConnectionMethod.BROWSER_LOGIN)

    def test_onchannel_is_credential(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.assertEqual(c.connection_method, ConnectionMethod.CREDENTIAL)

    def test_unknown_mall_is_unknown_method(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="OTHER", account_label="A",
        )
        self.assertEqual(c.connection_method, ConnectionMethod.UNKNOWN)


class BrowserLoginExpiryTestCase(ChannelConnectionServiceTestCaseBase):
    """2026-09-08 후속 — HOMEZ는 브라우저 로그인형 매입처의 세션을
    기술적으로 확인할 방법이 없다(자동 로그인 금지). 유일하게 정직한
    신호는 "사람이 마지막으로 확인한 지 너무 오래됐다"는 시간 경과뿐
    이다."""

    def test_recently_verified_stays_connected(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.service.mark_verified(c.id, self.company_a.id)

        refreshed = self.service.check_connection_status(c.id, self.company_a.id)
        self.assertEqual(refreshed.status, ChannelConnectionStatus.CONNECTED)

    def test_verification_older_than_trust_window_expires(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.service.mark_verified(c.id, self.company_a.id)

        # 신뢰 기간보다 오래된 것처럼 verified_at을 직접 과거로 되돌린다
        # (서비스가 스스로 이렇게 만들지는 않는다 — 테스트 전용 조작).
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.verified_at = datetime.utcnow() - timedelta(
            days=BROWSER_LOGIN_TRUST_WINDOW_DAYS + 1,
        )
        self.db.commit()

        refreshed = self.service.check_connection_status(c.id, self.company_a.id)
        self.assertEqual(refreshed.status, ChannelConnectionStatus.EXPIRED)
        # verified_at 자체는 여전히 남아있다(과거에 확인됐다는 사실은
        # 지우지 않는다) — status만 EXPIRED로 바뀐다.
        self.assertIsNotNone(refreshed.verified_at)

    def test_expired_connection_is_not_selectable_for_execution(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.service.mark_verified(c.id, self.company_a.id)
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.verified_at = datetime.utcnow() - timedelta(
            days=BROWSER_LOGIN_TRUST_WINDOW_DAYS + 1,
        )
        self.db.commit()
        self.service.check_connection_status(c.id, self.company_a.id)

        with self.assertRaises(ConflictException):
            self.service.select_connection_for_task(c.id, self.company_a.id)

    def test_credential_method_connection_service_passes_through_fake_adapter_result(self):
        """이 테스트는 실제 OnchannelChannelAdapter의 만료 규칙을
        검증하지 않는다 — check_connection_status() 서비스 메서드가
        Adapter가 돌려준 status를 그대로 신뢰해 전달한다는 사실만
        확인한다(만료 개념이 없는 가짜 Adapter를 일부러 주입).
        실제 온채널 Adapter 자신의 만료 동작은
        CredentialReverificationWindowTestCase에서 진짜 Adapter로
        직접 검증한다(2026-09-08 후속 — 이전 버전은 이 테스트 이름과
        설명이 "CREDENTIAL 방식은 만료되지 않는다"는 지금은 틀린
        사실을 실제 시스템 동작인 것처럼 기록하고 있었다 — 정정)."""

        import unittest.mock as mock

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(
            c.id, self.company_a.id, auth_key="test-jwt",
        )
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.verified_at = datetime.utcnow() - timedelta(
            days=BROWSER_LOGIN_TRUST_WINDOW_DAYS + 100,
        )
        self.db.commit()

        class _FakeConnectedAdapterWithNoExpiryConcept:
            def check_connection(self_inner, account_label, *, verified_at=None):
                from app.domains.purchase_task.channel_adapter import ConnectionCheckResult

                status = (
                    ChannelConnectionStatus.CONNECTED if verified_at is not None
                    else ChannelConnectionStatus.REGISTERED_UNVERIFIED
                )
                return ConnectionCheckResult(
                    mall_code="ONCHANNEL", status=status, credential_registered=True,
                    verified=verified_at is not None, verified_at=verified_at,
                    account_label=account_label, checked_at=datetime.utcnow(),
                    detail="fake — 이 테스트 전용, 만료 개념을 일부러 흉내내지 않음.",
                )

        with mock.patch(
            "app.domains.purchase_task.channel_connection_service.get_purchase_channel_adapter",
            return_value=_FakeConnectedAdapterWithNoExpiryConcept(),
        ):
            refreshed = self.service.check_connection_status(c.id, self.company_a.id)

        self.assertEqual(refreshed.status, ChannelConnectionStatus.CONNECTED)


class CredentialReverificationWindowTestCase(ChannelConnectionServiceTestCaseBase):
    """2026-09-08 후속("인증 최신성 결함 처리") — CREDENTIAL 방식
    (온채널)도 verified_at을 무기한 신뢰하지 않는다. 진짜
    OnchannelChannelAdapter를 그대로 쓴다(InMemoryCredentialStore만
    주입 — 네트워크는 여전히 호출되지 않는다, check_connection()은
    Credential Manager 조회만 하고 실제 HTTP 요청을 보내지 않는다).
    이 값(CREDENTIAL_REVERIFICATION_WINDOW_HOURS)은 온채널의 공식
    정책이 아니라 HOMEZ 자체 보수적 재확인 주기라는 점은
    constants.py에 이미 기록돼 있다 — 여기서는 그 주기가 실제로
    적용되는지만 검증한다."""

    def test_verified_within_window_stays_connected(self):

        from app.domains.purchase_task.constants import (
            CREDENTIAL_REVERIFICATION_WINDOW_HOURS,
        )

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="test-jwt")
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow() - timedelta(
            hours=CREDENTIAL_REVERIFICATION_WINDOW_HOURS - 1,
        )
        self.db.commit()

        refreshed = self.service.check_connection_status(c.id, self.company_a.id)
        self.assertEqual(refreshed.status, ChannelConnectionStatus.CONNECTED)

    def test_verified_past_window_expires(self):

        from app.domains.purchase_task.constants import (
            CREDENTIAL_REVERIFICATION_WINDOW_HOURS,
        )

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="test-jwt")
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow() - timedelta(
            hours=CREDENTIAL_REVERIFICATION_WINDOW_HOURS + 1,
        )
        self.db.commit()

        refreshed = self.service.check_connection_status(c.id, self.company_a.id)
        self.assertEqual(refreshed.status, ChannelConnectionStatus.EXPIRED)
        # verified_at 자체는 지우지 않는다 — 과거에 확인된 적이
        # 있다는 사실은 유지하고 status만 재확인 필요로 낮춘다.
        self.assertIsNotNone(refreshed.verified_at)

    def test_expired_credential_connection_blocks_order_readiness(self):
        """EXPIRED는 USABLE_FOR_EXECUTION에 없으므로
        verify_connection_ready_for_order_submission()도 자동으로
        막힌다는 것을 확인한다(EXPIRED 자체가 새 방어선 역할을
        한다는 것을 직접 증명)."""

        from app.core.exceptions import ConflictException
        from app.domains.purchase_task.constants import (
            CREDENTIAL_REVERIFICATION_WINDOW_HOURS,
        )

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="test-jwt")
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow() - timedelta(
            hours=CREDENTIAL_REVERIFICATION_WINDOW_HOURS + 1,
        )
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.verify_connection_ready_for_order_submission(
                c.id, self.company_a.id,
            )


class AdditionalAuthRequiredSelfReportTestCase(ChannelConnectionServiceTestCaseBase):
    """2026-09-08 후속 — 2단계 인증 등은 HOMEZ가 감지할 방법이 없어
    사람이 직접 표시하는 자기보고 상태다."""

    def test_mark_additional_auth_required_sets_status_and_keeps_verified_at(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.service.mark_verified(c.id, self.company_a.id)
        original_verified_at = c.verified_at

        updated = self.service.mark_additional_auth_required(
            c.id, self.company_a.id, triggered_by=7,
        )
        self.assertEqual(
            updated.status, ChannelConnectionStatus.ADDITIONAL_AUTH_REQUIRED,
        )
        self.assertEqual(updated.verified_at, original_verified_at)

    def test_status_survives_check_connection_status_until_reverified(self):
        """자동 재확인(check_connection_status)이 이 자기보고 상태를
        조용히 지우면 안 된다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.service.mark_verified(c.id, self.company_a.id)
        self.service.mark_additional_auth_required(c.id, self.company_a.id)

        refreshed = self.service.check_connection_status(c.id, self.company_a.id)
        self.assertEqual(
            refreshed.status, ChannelConnectionStatus.ADDITIONAL_AUTH_REQUIRED,
        )

        # 사람이 다시 로그인해 확인하면 정상적으로 해제된다.
        reverified = self.service.mark_verified(c.id, self.company_a.id)
        self.assertEqual(reverified.status, ChannelConnectionStatus.CONNECTED)

    def test_additional_auth_required_connection_not_selectable_for_execution(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.service.mark_verified(c.id, self.company_a.id)
        self.service.mark_additional_auth_required(c.id, self.company_a.id)

        with self.assertRaises(ConflictException):
            self.service.select_connection_for_task(c.id, self.company_a.id)

    def test_cannot_mark_additional_auth_required_on_inactive_connection(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.service.deactivate_connection(c.id, self.company_a.id)

        with self.assertRaises(BadRequestException):
            self.service.mark_additional_auth_required(c.id, self.company_a.id)


class CredentialReferenceWiringTestCase(ChannelConnectionServiceTestCaseBase):
    """2026-09-08 후속 — 회사별 자격증명 참조와 계정 연결 ID를
    일관되게 사용한다(지시문 1번)."""

    def test_credential_method_connection_gets_own_reference_on_create(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.assertIsNotNone(c.credential_reference)
        self.assertIn(str(c.id), c.credential_reference)

    def test_browser_login_connection_has_no_credential_reference(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        self.assertIsNone(c.credential_reference)

    def test_two_onchannel_connections_get_different_references(self):

        c1 = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="계정1",
        )
        c2 = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="계정2",
        )
        self.assertNotEqual(c1.credential_reference, c2.credential_reference)


class SaveCredentialTestCase(ChannelConnectionServiceTestCaseBase):

    def test_save_credential_on_browser_login_connection_rejected(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="A",
        )
        with self.assertRaises(BadRequestException):
            self.service.save_credential(c.id, self.company_a.id, auth_key="x")

    def test_save_credential_rejects_blank_key(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        with self.assertRaises(BadRequestException):
            self.service.save_credential(c.id, self.company_a.id, auth_key="   ")

    def test_other_company_cannot_save_credential(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        with self.assertRaises(NotFoundException):
            self.service.save_credential(c.id, self.company_b.id, auth_key="x")

    def test_save_credential_alone_does_not_reach_connected(self):
        """저장만으로 인증 성공 처리 불가 — 지시문 4번 테스트 목록."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        saved = self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        self.assertEqual(saved.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)
        self.assertIsNone(saved.verified_at)

    def test_resaving_credential_invalidates_previous_verification(self):
        """자격증명 변경 시 과거 인증 성공을 현재 성공으로 재사용하지
        않는다 — 지시문 1번/4번. 이전에(예: 실제 조회 성공으로) 이미
        CONNECTED였던 연결도 자격증명을 다시 저장하면 그 즉시 미검증
        상태로 되돌아간다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="old-key")
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow()
        self.db.commit()

        resaved = self.service.save_credential(c.id, self.company_a.id, auth_key="new-key")
        self.assertEqual(resaved.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)
        self.assertIsNone(resaved.verified_at)

    def test_credential_store_write_failure_leaves_registration_untouched(self):
        """저장 실패 시 등록 성공 표시 불가 — 지시문 4번 테스트 목록.
        Credential Manager 쓰기 자체가 예외를 던지면, 상태 변경·감사
        이벤트 기록·commit이 전혀 일어나지 않는다."""

        class _FailingStore:
            def save(self, *_args, **_kwargs):
                raise RuntimeError("동기화 되지 않는 시뮬레이션 저장소 오류")

        service = PurchaseChannelConnectionService(self.db, credential_store=_FailingStore())
        c = service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        before_events = (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(PurchaseChannelConnectionEvent.connection_id == c.id)
            .count()
        )

        with self.assertRaises(RuntimeError):
            service.save_credential(c.id, self.company_a.id, auth_key="x")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertNotEqual(row.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)
        after_events = (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(PurchaseChannelConnectionEvent.connection_id == c.id)
            .count()
        )
        self.assertEqual(before_events, after_events)

    def test_save_credential_does_not_put_value_in_event_log(self):
        """비밀정보 미노출 — 감사 이벤트에도 값을 남기지 않는다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        secret = "eyJhbGciOiJIUzI1NiJ9.super-secret-jwt-value-must-not-leak"
        self.service.save_credential(
            c.id, self.company_a.id, auth_key=secret, allowed_ip="1.2.3.4",
        )

        events = (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(PurchaseChannelConnectionEvent.connection_id == c.id)
            .all()
        )
        for event in events:
            self.assertNotIn(secret, event.detail or "")
            self.assertNotIn("1.2.3.4", event.detail or "")


class RealLookupServiceWiringTestCase(ChannelConnectionServiceTestCaseBase):
    """서비스 레이어가 회사 격리를 지킨 뒤에만 실제 조회를 시도하는지
    — 실제 네트워크는 열지 않는다(자격증명이 아예 없어 Adapter가
    네트워크를 열기 전에 막힌다)."""

    def test_lookup_product_other_company_blocked_before_any_network_call(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        with self.assertRaises(NotFoundException):
            self.service.lookup_product(c.id, self.company_b.id, "CH1")

    def test_lookup_product_without_credential_raises(self):

        from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        with self.assertRaises(PurchaseChannelAdapterError):
            self.service.lookup_product(c.id, self.company_a.id, "CH1")

    def test_lookup_product_passes_own_credential_store_to_adapter(self):
        """2026-09-08 후속 — 격리 검증 중 실제 Windows Credential
        Manager 오염 사고 재발 방지 회귀 테스트. 서비스가 자기
        credential_store(여기서는 InMemoryCredentialStore)를 그대로
        Adapter 생성에 넘기는지 확인한다 — 넘기지 않으면 Adapter가
        몰래 실제 저장소를 새로 만들어, 테스트가 "격리됐다"고 믿는
        것과 실제로 조회되는 저장소가 어긋난다(그 어긋남이 실제
        사고 원인이었다)."""

        import unittest.mock as mock

        from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        captured = {}
        real_factory = self.service.lookup_product.__globals__["get_purchase_channel_adapter"]

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            return real_factory(*args, **kwargs)

        with mock.patch(
            "app.domains.purchase_task.channel_connection_service.get_purchase_channel_adapter",
            side_effect=_capture,
        ):
            with self.assertRaises(PurchaseChannelAdapterError):
                self.service.lookup_product(c.id, self.company_a.id, "CH1")

        self.assertIs(captured.get("credential_store"), self.credential_store)

    def test_lookup_tracking_other_company_blocked_before_any_network_call(self):
        """2026-09-10 신규(Phase 7) — lookup_tracking()도 lookup_
        product()·lookup_order()와 동일한 회사 격리 게이트를 먼저
        거친다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        with self.assertRaises(NotFoundException):
            self.service.lookup_tracking(c.id, self.company_b.id, "MO_1")

    def test_lookup_tracking_without_credential_raises(self):

        from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        with self.assertRaises(PurchaseChannelAdapterError):
            self.service.lookup_tracking(c.id, self.company_a.id, "MO_1")


class RealCheckRecordingTestCase(ChannelConnectionServiceTestCaseBase):
    """2026-09-08 재정정(id=3/4 사고 이후) — CREDENTIAL 방식은 실제
    조회 성공만이 연결 확인을 기록한다. Adapter 생성 지점(module-level
    get_purchase_channel_adapter)을 가짜로 바꿔치기해 네트워크 없이
    "성공"/"인증실패"/"그 외 실패" 세 갈래를 검증한다 — 지시문 4번
    테스트 목록의 나머지 항목(조회 실패 시 성공 표시 불가, 다른
    연결의 인증 결과 재사용 불가)."""

    def _install_fake_adapter(
        self, *, lookup_product_result=None, lookup_product_error=None,
        list_products_result=None, list_products_error=None,
        check_member_point_result=None, check_member_point_error=None,
        lookup_tracking_result=None, lookup_tracking_error=None,
        on_call=None,
    ):
        """on_call은 Adapter가 "네트워크 응답을 받은 시점"을 흉내내는
        지점에서 호출된다 — 요청 시작(서비스가 지문을 읽은 뒤)과 응답
        저장(서비스가 지문을 다시 읽는 시점) 사이에 자격증명이
        바뀌는 상황을 시뮬레이션하는 유일한 훅이다."""

        import unittest.mock as mock

        class _FakeAdapter:
            def lookup_product(self_inner, external_product_id):
                if on_call is not None:
                    on_call()
                if lookup_product_error is not None:
                    raise lookup_product_error
                return lookup_product_result

            def list_products(self_inner, *, page=1, page_size=1):
                if on_call is not None:
                    on_call()
                if list_products_error is not None:
                    raise list_products_error
                return list_products_result

            def check_member_point(self_inner):
                if on_call is not None:
                    on_call()
                if check_member_point_error is not None:
                    raise check_member_point_error
                return check_member_point_result

            def lookup_tracking(self_inner, external_order_number):
                if on_call is not None:
                    on_call()
                if lookup_tracking_error is not None:
                    raise lookup_tracking_error
                return lookup_tracking_result

            def check_connection(self_inner, account_label, *, verified_at=None):
                # verify_connection_ready_for_order_submission()이 상태를
                # 다시 계산할 때 거치는 경로 — 이 가짜 Adapter로도 최소한
                # 동작하도록 실제 규칙(verified_at 유무)만 흉내낸다.
                class _Result:
                    status = (
                        ChannelConnectionStatus.CONNECTED if verified_at is not None
                        else ChannelConnectionStatus.REGISTERED_UNVERIFIED
                    )
                return _Result()

        patcher = mock.patch(
            "app.domains.purchase_task.channel_connection_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_successful_lookup_records_verified_at_and_connected(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        self._install_fake_adapter(lookup_product_result={"fake": "product"})

        self.service.lookup_product(c.id, self.company_a.id, "CH1")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(row.verified_at)
        events = (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(PurchaseChannelConnectionEvent.connection_id == c.id)
            .order_by(PurchaseChannelConnectionEvent.id.desc())
            .first()
        )
        self.assertEqual(events.event_type, "VERIFIED")

    def test_successful_lookup_tracking_records_verified_at_and_connected(self):
        """2026-09-10 신규(Phase 7) — lookup_tracking()도 실제 조회
        성공 시 lookup_product·lookup_order와 동일하게 연결 확인을
        기록한다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        self._install_fake_adapter(lookup_tracking_result={"fake": "tracking"})

        self.service.lookup_tracking(c.id, self.company_a.id, "MO_1")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(row.verified_at)

    def test_successful_list_products_records_verified_at_and_connected(self):
        """item 7 승인된 상품 목록 조회(page=1&page_size=1) 경로도
        lookup_product와 동일하게 실제 성공 시 연결 확인을 기록한다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        self._install_fake_adapter(list_products_result={"fake": "product-list"})

        self.service.list_products(c.id, self.company_a.id, page=1, page_size=1)

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(row.verified_at)
        events = (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(PurchaseChannelConnectionEvent.connection_id == c.id)
            .order_by(PurchaseChannelConnectionEvent.id.desc())
            .first()
        )
        self.assertEqual(events.event_type, "VERIFIED")
        self.assertIn("상품 목록", events.detail)

    def test_point_check_success_does_not_touch_connection_status_or_verified_at(self):
        """2026-09-08 후속 — "포인트 조회 성공을 발주 권한 검증으로
        사용하지 않는다"는 지시의 핵심 회귀 테스트. lookup_product/
        list_products와 달리 check_member_point()는 성공해도
        status/verified_at을 절대 바꾸지 않는다 — 그래야 포인트
        조회만으로 발주 실행 게이트(verify_connection_ready_for_
        order_submission)가 열리지 않는다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        before_status = (
            self.db.query(PurchaseChannelConnection).get(c.id).status
        )
        self._install_fake_adapter(check_member_point_result="fake-point-result")

        self.service.check_member_point(c.id, self.company_a.id)

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, before_status)
        self.assertIsNone(row.verified_at)

        with self.assertRaises(ConflictException):
            self.service.verify_connection_ready_for_order_submission(
                c.id, self.company_a.id,
            )

    def test_point_check_failure_does_not_touch_connection_status(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.status = ChannelConnectionStatus.CONNECTED
        from datetime import datetime
        row.verified_at = datetime.utcnow()
        self.db.commit()

        self._install_fake_adapter(check_member_point_error=ValueError("일시 오류"))

        with self.assertRaises(ValueError):
            self.service.check_member_point(c.id, self.company_a.id)

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(row.verified_at)

    def test_non_auth_failure_does_not_change_status(self):
        """조회 실패 시 성공 표시 불가 — 인증과 무관한 실패(예: 404/
        형식 오류에 대응하는 일반 예외)는 자격증명이 잘못됐다는 증거가
        아니므로 상태를 건드리지 않는다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        self._install_fake_adapter(lookup_product_error=ValueError("상품을 찾을 수 없음"))

        with self.assertRaises(ValueError):
            self.service.lookup_product(c.id, self.company_a.id, "CH1")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)
        self.assertIsNone(row.verified_at)

    def test_authentication_failure_downgrades_to_unusable(self):
        """인증 실패 시 현재 연결 사용 불가로 기록 — 지시문 1번."""

        from app.domains.purchase_task.onchannel_client import OnchannelAuthenticationError

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow()
        self.db.commit()

        self._install_fake_adapter(
            lookup_product_error=OnchannelAuthenticationError("401 — 잘못된 접근입니다."),
        )

        with self.assertRaises(OnchannelAuthenticationError):
            self.service.lookup_product(c.id, self.company_a.id, "CH1")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, ChannelConnectionStatus.ERROR)
        self.assertIsNone(row.verified_at)

    def test_consecutive_failure_count_increments_regardless_of_error_type(self):
        """2026-09-15 전면 감사 후속(Phase 9, 8-16) — 인증 실패든
        일반 실패든 종류와 무관하게 연속 실패 횟수를 센다("조회
        실패가 계속되면"은 인증 실패로 한정하지 않는다)."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        self._install_fake_adapter(
            lookup_product_error=ValueError("일반 오류"),
        )

        for expected_count in (1, 2):
            with self.assertRaises(ValueError):
                self.service.lookup_product(c.id, self.company_a.id, "CH1")
            row = self.db.query(PurchaseChannelConnection).get(c.id)
            self.assertEqual(row.consecutive_failure_count, expected_count)

    def test_consecutive_failure_count_resets_on_success(self):

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        self._install_fake_adapter(
            lookup_product_error=ValueError("일반 오류"),
        )
        with self.assertRaises(ValueError):
            self.service.lookup_product(c.id, self.company_a.id, "CH1")
        with self.assertRaises(ValueError):
            self.service.lookup_product(c.id, self.company_a.id, "CH1")
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.consecutive_failure_count, 2)

        self._install_fake_adapter(lookup_product_result={"fake": "product"})
        self.service.lookup_product(c.id, self.company_a.id, "CH1")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.consecutive_failure_count, 0)

    def test_verification_success_does_not_leak_to_sibling_connection(self):
        """다른 회사·다른 연결의 인증 결과 재사용 불가 — 지시문 4번.
        같은 회사 안에서도 연결 A의 실제 조회 성공이 연결 B에 영향을
        주지 않는다(각 연결이 각자의 verified_at을 갖는다)."""

        c1 = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        c2 = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="B",
        )
        self.service.save_credential(c1.id, self.company_a.id, auth_key="key-1")
        self.service.save_credential(c2.id, self.company_a.id, auth_key="key-2")
        self._install_fake_adapter(lookup_product_result={"fake": "product"})

        self.service.lookup_product(c1.id, self.company_a.id, "CH1")

        row1 = self.db.query(PurchaseChannelConnection).get(c1.id)
        row2 = self.db.query(PurchaseChannelConnection).get(c2.id)
        self.assertEqual(row1.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(row1.verified_at)
        self.assertEqual(row2.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)
        self.assertIsNone(row2.verified_at)

    def test_credential_changed_mid_call_does_not_record_success(self):
        """요청 시작 시 자격증명 지문과 응답 저장 시 지문을 대조한다
        — 조회 도중(네트워크 왕복 시간 동안) 자격증명이 재저장되면,
        방금 받은 성공 응답을 "지금" 자격증명의 연결 확인으로
        반영하지 않는다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="old-key")

        def _swap_credential_mid_call():
            self.credential_store.save(c.credential_reference, {"auth_key": "new-key", "allowed_ip": ""})

        self._install_fake_adapter(
            lookup_product_result={"fake": "product"}, on_call=_swap_credential_mid_call,
        )

        self.service.lookup_product(c.id, self.company_a.id, "CH1")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertNotEqual(row.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNone(row.verified_at)
        events = (
            self.db.query(PurchaseChannelConnectionEvent)
            .filter(PurchaseChannelConnectionEvent.connection_id == c.id)
            .order_by(PurchaseChannelConnectionEvent.id.desc())
            .first()
        )
        self.assertIn("자격증명이 변경", events.detail)

    def test_credential_deleted_mid_call_does_not_record_success(self):
        """조회 도중 자격증명이 아예 삭제된 경우도 동일하게 방지한다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="old-key")

        def _delete_credential_mid_call():
            self.credential_store.delete(c.credential_reference)

        self._install_fake_adapter(
            lookup_product_result={"fake": "product"}, on_call=_delete_credential_mid_call,
        )

        self.service.lookup_product(c.id, self.company_a.id, "CH1")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertNotEqual(row.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNone(row.verified_at)

    def test_credential_unchanged_mid_call_still_records_success(self):
        """자격증명이 그대로면(정상 경로) 지문 대조가 거짓양성을 내지
        않는다 — on_call 훅이 있어도 값을 바꾸지 않으면 평소처럼
        CONNECTED로 기록된다."""

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="same-key")

        def _noop():
            pass

        self._install_fake_adapter(
            lookup_product_result={"fake": "product"}, on_call=_noop,
        )

        self.service.lookup_product(c.id, self.company_a.id, "CH1")

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)
        self.assertIsNotNone(row.verified_at)

    def test_full_lifecycle_never_constructs_a_real_credential_store(self):
        """2026-09-08 후속 — 격리 검증 중 실제 Windows Credential
        Manager 오염 사고 재발 방지, 전체 경로 검증. 자격증명 등록
        (save_credential) → 상태 확인(check_connection_status, Adapter가
        읽기) → 실제 조회 성공 기록(lookup_product) → 연결 해제
        (deactivate_connection)까지 전 구간에서, 이 서비스에 주입한
        InMemoryCredentialStore 하나만 쓰이고 실제 WindowsCredentialStore
        는 단 한 번도 생성되지 않는다는 것을 직접 감시(spy)해 확인한다
        — 값이 우연히 맞아떨어져서가 아니라 구조적으로 그렇다는 증거."""

        import unittest.mock as mock

        from app.core.windows_credential_store import WindowsCredentialStore

        with mock.patch.object(
            WindowsCredentialStore, "__init__",
            side_effect=AssertionError(
                "실제 WindowsCredentialStore()가 격리 테스트 도중 생성되려 "
                "했습니다 — credential_store 주입 배선이 어딘가 끊겼습니다.",
            ),
        ):
            c = self.service.create_connection(
                self.company_a.id, mall_code="ONCHANNEL", account_label="A",
            )
            self.service.save_credential(c.id, self.company_a.id, auth_key="x")
            self.service.check_connection_status(c.id, self.company_a.id)

            self._install_fake_adapter(lookup_product_result={"fake": "product"})
            self.service.lookup_product(c.id, self.company_a.id, "CH1")

            self.service.deactivate_connection(c.id, self.company_a.id)

        row = self.db.query(PurchaseChannelConnection).get(c.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)
        self.assertFalse(row.is_active)
        # InMemoryCredentialStore에는 여전히 남아있다(deactivate가
        # 자격증명 자체를 지우지는 않는다 — 별도 기능).
        self.assertTrue(self.credential_store.exists(row.credential_reference))


class RepeatedLookupFailureNotificationTestCase(unittest.TestCase):
    """2026-09-15 전면 감사 후속(Phase 9, HOMEZ_USER_OPERATION_
    SETTINGS.md 8-16 — "매입처 조회 실패가 계속되면 사용자에게
    알린다"). 위 RealCheckRecordingTestCase의 경량 fixture는
    notification_email_logs/users 테이블이 없어 알림 발송 자체를
    검증할 수 없다(dispatch_operational_event가 테이블 부재 시
    조용히 no-op한다) — 이 클래스는 bootstrap_environment()로 전체
    스키마를 갖춘 임시 DB를 써서 실제로 알림 행이 만들어지는지
    확인한다."""

    def setUp(self):

        from pathlib import Path

        from app.database.bootstrap import bootstrap_environment

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        repo_root = Path(__file__).resolve().parent.parent
        result = bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=repo_root / "migrations",
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company = Company(
            name="공급처 알림 테스트 회사", business_number="333-33-33333",
            ceo="테스트", phone="02-000-0000",
            email="supplier-notify@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="supplierlookupadmin",
            email="supplierlookupadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.credential_store = InMemoryCredentialStore()
        self.service = PurchaseChannelConnectionService(
            self.db, credential_store=self.credential_store,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def _install_fake_adapter(self, *, error=None, result=None):

        import unittest.mock as mock

        class _FakeAdapter:
            def lookup_product(self_inner, external_product_id):
                if error is not None:
                    raise error
                return result

        patcher = mock.patch(
            "app.domains.purchase_task.channel_connection_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _notification_rows(self):

        from sqlalchemy import text

        return self.db.execute(
            text(
                "SELECT user_id, event_code FROM notification_email_logs "
                "WHERE event_code = 'SUPPLIER_LOOKUP_REPEATED_FAILURE'",
            ),
        ).fetchall()

    def test_notifies_super_admin_exactly_when_threshold_reached(self):

        from app.domains.purchase_task.constants import (
            CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD,
        )

        c = self.service.create_connection(
            self.company.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company.id, auth_key="x")
        self._install_fake_adapter(error=ValueError("일반 오류"))

        for i in range(1, CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD):
            with self.assertRaises(ValueError):
                self.service.lookup_product(c.id, self.company.id, "CH1")
            self.assertEqual(
                len(self._notification_rows()), 0,
                f"임계치({CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD}) 도달 전인 "
                f"{i}회차에는 알림이 없어야 한다.",
            )

        with self.assertRaises(ValueError):
            self.service.lookup_product(c.id, self.company.id, "CH1")

        rows = self._notification_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], self.admin.id)

    def test_does_not_notify_again_on_further_failures_in_same_streak(self):
        """임계치 도달 이후에도 스트릭이 계속되면(성공 없이) 매번
        반복 알림을 보내지 않는다 — 임계치 "도달 순간"에만 1회."""

        from app.domains.purchase_task.constants import (
            CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD,
        )

        c = self.service.create_connection(
            self.company.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company.id, auth_key="x")
        self._install_fake_adapter(error=ValueError("일반 오류"))

        for _ in range(CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD + 3):
            with self.assertRaises(ValueError):
                self.service.lookup_product(c.id, self.company.id, "CH1")

        self.assertEqual(len(self._notification_rows()), 1)

    def test_success_then_new_streak_notifies_again(self):
        """성공으로 스트릭이 끊긴 뒤 새 스트릭이 다시 임계치에
        도달하면 새 알림이 (다시) 발송돼야 한다."""

        from app.domains.purchase_task.constants import (
            CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD,
        )

        c = self.service.create_connection(
            self.company.id, mall_code="ONCHANNEL", account_label="A",
        )
        self.service.save_credential(c.id, self.company.id, auth_key="x")

        self._install_fake_adapter(error=ValueError("일반 오류"))
        for _ in range(CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD):
            with self.assertRaises(ValueError):
                self.service.lookup_product(c.id, self.company.id, "CH1")
        self.assertEqual(len(self._notification_rows()), 1)

        self._install_fake_adapter(result={"fake": "product"})
        self.service.lookup_product(c.id, self.company.id, "CH1")

        self._install_fake_adapter(error=ValueError("일반 오류"))
        for _ in range(CONSECUTIVE_LOOKUP_FAILURE_NOTIFY_THRESHOLD):
            with self.assertRaises(ValueError):
                self.service.lookup_product(c.id, self.company.id, "CH1")

        self.assertEqual(len(self._notification_rows()), 2)


class OnchannelOrderContractStatusTestCase(unittest.TestCase):
    """2026-09-09 후속("계약 상태 세분화") — 사용자가 요청한 감사:
    "단일 boolean을 임의로 True로 바꾸면 전체 발주가 허용되는
    구조인지"를 직접 증명한다. DB 없이 constants.py의 순수 함수만
    검증한다(부수효과 없음)."""

    def setUp(self):
        import copy

        from app.domains.purchase_task import constants as c

        self._constants = c
        # 각 테스트가 전역 dict를 건드려도 다른 테스트에 영향이
        # 없도록 원본을 저장해 두고 tearDown에서 되돌린다.
        self._original_status = copy.deepcopy(c.ONCHANNEL_ORDER_CONTRACT_STATUS)

        # 2026-09-10 — 온채널 공식 답변으로 실제 기본값이 "4개 전부
        # 확인됨"으로 바뀌었다(아래 파일 하단 주석 참고). 이 클래스는
        # "부분 확인만으로는 전체가 열리지 않는다"는 메커니즘 자체를
        # 증명하는 것이 목적이지, 실제 운영 확인 상태를 다시
        # 증명하는 것이 아니다 — 그래서 각 테스트는 항상 "전부
        # 미확인"인 합성 기준선에서 시작하도록 여기서 강제로
        # 초기화한다(실제 운영 상태와 무관하게 메커니즘만 독립적으로
        # 검증). 실제 운영 확인 상태 자체는
        # OnchannelOrderContractStatusCurrentValueTestCase가 별도로
        # 고정한다.
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS.clear()
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS.update({
            item: self._constants.OnchannelOrderContractItemStatus(
                confirmed=False, official_basis=None, confirmed_at=None,
            )
            for item in self._constants.OnchannelOrderContractItem.ALL
        })

    def tearDown(self):
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS.clear()
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS.update(self._original_status)

    def test_all_items_start_unconfirmed(self):
        """합성 기준선(setUp이 강제 초기화한 전부-미확인 상태)에서
        시작한다는 뜻이다 — 실제 운영 확인 상태를 뜻하지 않는다(위
        setUp 주석 참고)."""

        self.assertFalse(self._constants.is_onchannel_order_contract_fully_confirmed())
        self.assertEqual(
            len(self._constants.unconfirmed_onchannel_order_contract_items()), 4,
        )

    def test_single_item_confirmed_alone_does_not_fully_confirm(self):
        """감사 핵심 — 4개 항목 중 1개만(근거·시각까지 제대로) 확인
        되어도 전체 판정은 여전히 False다. 이전 단일 boolean
        설계였다면 그 하나를 True로 바꾸는 순간 전체가 열렸을
        것이다 — 이 테스트가 그 위험이 지금은 없다는 것을 증명한다."""

        from datetime import datetime

        item = self._constants.OnchannelOrderContractItem.SALES_APPLICATION
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS[item] = (
            self._constants.OnchannelOrderContractItemStatus(
                confirmed=True, official_basis="온채널 답변 이메일(가정, 테스트용)",
                confirmed_at=datetime.utcnow(),
            )
        )

        self.assertFalse(self._constants.is_onchannel_order_contract_fully_confirmed())
        self.assertEqual(
            len(self._constants.unconfirmed_onchannel_order_contract_items()), 3,
        )

    def test_confirmed_true_without_official_basis_is_not_properly_confirmed(self):
        """confirmed=True만 있고 근거가 비어 있으면 여전히 미확인
        취급이다 — "근거 없이 값만 바꿔치기"를 막는 핵심 보증."""

        from datetime import datetime

        item = self._constants.OnchannelOrderContractItem.PAYMENT_SOURCE
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS[item] = (
            self._constants.OnchannelOrderContractItemStatus(
                confirmed=True, official_basis=None,
                confirmed_at=datetime.utcnow(),
            )
        )

        status = self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS[item]
        self.assertFalse(status.is_properly_confirmed)
        self.assertFalse(self._constants.is_onchannel_order_contract_fully_confirmed())

    def test_confirmed_true_without_confirmed_at_is_not_properly_confirmed(self):

        item = self._constants.OnchannelOrderContractItem.DUPLICATE_PREVENTION
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS[item] = (
            self._constants.OnchannelOrderContractItemStatus(
                confirmed=True, official_basis="온채널 답변(가정, 테스트용)",
                confirmed_at=None,
            )
        )

        status = self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS[item]
        self.assertFalse(status.is_properly_confirmed)
        self.assertFalse(self._constants.is_onchannel_order_contract_fully_confirmed())

    def test_all_four_properly_confirmed_passes(self):
        """네 항목 전부 근거·시각까지 갖춰야만 통과한다는 것도
        함께 증명한다(이 테스트만으로 실제 계약이 확인됐다고
        기록하지 않는다 — 순수 함수 동작 검증용)."""

        from datetime import datetime

        for item in self._constants.OnchannelOrderContractItem.ALL:
            self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS[item] = (
                self._constants.OnchannelOrderContractItemStatus(
                    confirmed=True, official_basis="온채널 답변(가정, 테스트용)",
                    confirmed_at=datetime.utcnow(),
                )
            )

        self.assertTrue(self._constants.is_onchannel_order_contract_fully_confirmed())
        self.assertEqual(
            self._constants.unconfirmed_onchannel_order_contract_items(), (),
        )


class OnchannelOrderContractStatusCurrentValueTestCase(unittest.TestCase):
    """2026-09-10 — 온채널 공식 답변 도착 후 실제 운영 확인 상태를
    고정한다(위 OnchannelOrderContractStatusTestCase는 메커니즘만
    검증하고 setUp에서 매번 합성 기준선으로 초기화하므로 이 사실을
    검증하지 않는다 — 이 클래스가 그 사실 자체를 담당). 모듈을
    전혀 건드리지 않는다(순수 조회) — 다른 테스트의 dict 초기화·
    복원과 절대 경합하지 않는다."""

    def test_all_four_items_are_confirmed_with_official_basis(self):

        from app.domains.purchase_task import constants as c

        self.assertTrue(c.is_onchannel_order_contract_fully_confirmed())
        self.assertEqual(c.unconfirmed_onchannel_order_contract_items(), ())

        for item in c.OnchannelOrderContractItem.ALL:
            status = c.ONCHANNEL_ORDER_CONTRACT_STATUS[item]
            self.assertTrue(status.is_properly_confirmed, item)
            self.assertIn("온채널 공식 답변", status.official_basis)
            self.assertIsNotNone(status.confirmed_at)

    def test_duplicate_prevention_basis_states_server_does_not_dedupe(self):
        """가장 실무적으로 중요한 사실(온채널 서버가 sale_code 중복을
        막아주지 않는다 — 그래서 HOMEZ 자체 UNIQUE 제약이 유일한
        방어선이다)이 근거 문구에서 실수로 사라지지 않는지 고정한다."""

        from app.domains.purchase_task import constants as c

        basis = c.ONCHANNEL_ORDER_CONTRACT_STATUS[
            c.OnchannelOrderContractItem.DUPLICATE_PREVENTION
        ].official_basis
        self.assertIn("제한하지 않는다", basis)

    def test_result_reconciliation_basis_states_no_requery_method_exists(self):
        """"sale_code로 재조회 불가"라는 확정 사실(자동 재시도 금지
        원칙의 근거)이 사라지지 않는지 고정한다."""

        from app.domains.purchase_task import constants as c

        basis = c.ONCHANNEL_ORDER_CONTRACT_STATUS[
            c.OnchannelOrderContractItem.RESULT_RECONCILIATION
        ].official_basis
        self.assertIn("재조회하는 기능은 없다", basis)


class OnchannelOrderContractPartialConfirmationIntegrationTestCase(
    ChannelConnectionServiceTestCaseBase,
):
    """2026-09-09 후속 — 위 순수 함수 테스트를 실제
    verify_connection_ready_for_order_submission() 경로로도
    한 번 더 증명한다(통합 테스트). 4개 중 1개만 제대로 확인된
    상태에서도 발주 준비 판정은 여전히 막힌다."""

    def setUp(self):
        super().setUp()
        import copy

        from app.domains.purchase_task import constants as c

        self._constants = c
        self._original_status = copy.deepcopy(c.ONCHANNEL_ORDER_CONTRACT_STATUS)
        self.addCleanup(self._restore)

        # 2026-09-10 — 위 OnchannelOrderContractStatusTestCase.setUp
        # 과 동일한 이유로, 이 통합 테스트도 실제 운영 확인 상태와
        # 무관하게 "부분 확인만으로는 막힌다"는 메커니즘 자체를
        # 항상 전부-미확인 합성 기준선에서 검증한다.
        c.ONCHANNEL_ORDER_CONTRACT_STATUS.clear()
        c.ONCHANNEL_ORDER_CONTRACT_STATUS.update({
            item: c.OnchannelOrderContractItemStatus(
                confirmed=False, official_basis=None, confirmed_at=None,
            )
            for item in c.OnchannelOrderContractItem.ALL
        })

    def _restore(self):
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS.clear()
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS.update(self._original_status)

    def test_one_of_four_contract_items_confirmed_still_blocks_readiness(self):

        from datetime import datetime

        item = self._constants.OnchannelOrderContractItem.SALES_APPLICATION
        self._constants.ONCHANNEL_ORDER_CONTRACT_STATUS[item] = (
            self._constants.OnchannelOrderContractItemStatus(
                confirmed=True, official_basis="온채널 답변(가정, 테스트용)",
                confirmed_at=datetime.utcnow(),
            )
        )

        c = self.service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="부분확인",
        )
        self.service.save_credential(c.id, self.company_a.id, auth_key="x")
        row = self.db.query(PurchaseChannelConnection).get(c.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow()
        self.db.commit()

        with self.assertRaises(ConflictException) as ctx:
            self.service.verify_connection_ready_for_order_submission(
                c.id, self.company_a.id,
            )
        # 실패 메시지가 "판매신청"은 이미 확인됐다고 언급하지 않고
        # 나머지 3개만 남았다고 정확히 알려주는지까지 확인한다.
        message = str(ctx.exception)
        self.assertNotIn("판매신청", message)
        self.assertIn("결제", message)


if __name__ == "__main__":
    unittest.main()
