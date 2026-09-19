"""
=========================================================
Homez OS

File : tests/test_purchase_order_submission_service.py

Gate PT-3(2026-09-08 후속, "확인된 발주 계약 구현") —
PurchaseOrderSubmissionService 격리 테스트. 실제 네트워크·실제
Windows Credential Manager를 전혀 건드리지 않는다(InMemoryCredentialStore
+ 가짜 Adapter만 사용). 정상 응답, 명시적 거절, 응답 형식 오류,
전송 결과 불명, 동시 실행/반복 클릭(중복 잠금), 프로세스 재시작,
자격증명 관련 실패, 다중 계정·회사 격리를 모두 다룬다.
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
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.purchase_task.constants import BudgetReservationStatus
from app.domains.purchase_task.model import PurchaseRecord
from app.domains.purchase_task.model import PurchaseTaskBudgetReservation
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo
from app.domains.purchase_task.repository import PurchaseTaskRepository
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.constants import ChannelConnectionStatus
from app.domains.purchase_task.constants import OrderSubmissionStatus
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.purchase_task.model import PurchaseOrderApproval
from app.domains.purchase_task.model import PurchaseOrderUnknownResolutionEvent
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskPolicySetting
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.model import PurchaseSalesApplicationAttempt
from app.domains.purchase_task.constants import PurchaseOrderApprovalStatus
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.order_submission_service import (
    PurchaseOrderSubmissionService,
)
from app.domains.purchase_task.constants import SalesApplicationStatus
from app.domains.price_stock_safety.model import VirtualStockZeroProposal
from app.domains.product_attribute_match.model import ProductAttributeComparisonItem
from app.domains.product_attribute_match.model import ProductAttributeComparisonRun
from app.domains.recall_notice.model import RecallProductBlock
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용


VALID_KWARGS = dict(
    product_code="CH1234567",
    options=[{"id": 1, "qty": 1}],
    recv_name="홍길동", recv_tell="02-000-0000", recv_mobile="010-0000-0000",
    zipcode="00000", address="서울시 어딘가",
)


_AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class _FakeSalesApplicationResult:
    """2026-09-10 후속 — 발주 전 판매신청 게이트가 참조하는
    SalesApplicationResult를 흉내낸다. 이 테스트 파일의 Fake
    Adapter들은 발주(submit_order) 자체를 검증하는 것이 목적이므로,
    판매신청 게이트는 항상 "접수 확인됨"으로 통과시켜 발주 로직만
    격리해서 본다 — 판매신청 게이트 자체의 세부 동작은
    tests/test_purchase_sales_application_service.py가 전담한다."""

    def __init__(self, *, submitted=True, applied_product_code=None, detail="FAKE"):
        self.support = "SUPPORTED"
        self.submitted = submitted
        self.applied_product_code = applied_product_code
        self.detail = detail


class OrderSubmissionServiceTestCaseBase(unittest.TestCase):

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
                PurchaseOrderSubmissionAttempt.__table__,
                PurchaseSalesApplicationAttempt.__table__,
                PurchaseOrderApproval.__table__,
                PurchaseTask.__table__,
                PurchaseTaskPolicySetting.__table__,
                EmergencyStop.__table__, FunctionAutomationState.__table__,
                VirtualStockZeroProposal.__table__,
                ProductAttributeComparisonRun.__table__,
                ProductAttributeComparisonItem.__table__,
                RecallProductBlock.__table__,
            ],
        )
        with self.engine.connect() as conn:
            conn.exec_driver_sql(_AUDIT_LOGS_DDL)
            conn.commit()
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
        self.connection_service = PurchaseChannelConnectionService(
            self.db, credential_store=self.credential_store,
        )
        self.service = PurchaseOrderSubmissionService(
            self.db, credential_store=self.credential_store,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _new_service_same_db(self) -> PurchaseOrderSubmissionService:
        """"프로세스 재시작"을 흉내낸다 — 같은 파일 DB에 새 세션·새
        서비스 인스턴스로 다시 접속한다(메모리 상태는 전혀 공유하지
        않는다, 오직 DB에 이미 커밋된 것만 본다)."""

        new_db = self.SessionLocal()
        self.addCleanup(new_db.close)
        return PurchaseOrderSubmissionService(
            new_db, credential_store=self.credential_store,
        )

    def _make_ready_connection(self, *, company=None) -> PurchaseChannelConnection:
        """CREDENTIAL 방식 연결을 만들고, 자격증명 등록 + 실제 조회
        성공을 흉내내 verify_connection_ready_for_order_submission()을
        통과할 수 있는 상태(CONNECTED)로 만든다."""

        company = company or self.company_a
        connection = self.connection_service.create_connection(
            company.id, mall_code="ONCHANNEL", account_label="발주 테스트 계정",
        )
        self.connection_service.save_credential(
            connection.id, company.id, auth_key="test-jwt",
        )
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        from datetime import datetime
        row.status = "CONNECTED"
        row.verified_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return row

    def _patch_contract_confirmed(self):
        """2026-09-09 후속("계약 상태 세분화") — 실제
        ONCHANNEL_ORDER_CONTRACT_STATUS의 4개 항목은 여전히 전부
        미확인이다(공식 답변 대기 중). 이 헬퍼는
        is_onchannel_order_contract_fully_confirmed()의 반환값을
        **이 테스트의 범위 안에서만** True로 바꿔치기해, 이미 구현된
        하위 로직(성공/거절/형식오류/결과불명 분류, 중복 잠금,
        재시작 복구)이 계약 게이트를 지났다고 가정했을 때 올바르게
        동작하는지만 검증한다 — 실제 계약이 확인됐다는 뜻이 절대
        아니다(실제 항목별 상태는 건드리지 않는다). 이 헬퍼를 쓰는
        테스트는 "confirm_real_submission=True + 계약 확인됨(가정)"
        조합에서의 하위 로직만 증명하며, 실제 기본값(계약 미확인)
        에서 발주가 열리는지는 별도로(패치 없이) 증명해야 한다."""

        patcher = mock.patch(
            "app.domains.purchase_task.channel_connection_service."
            "is_onchannel_order_contract_fully_confirmed",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_point_balance_gate_passes(self):
        """2026-09-10 Phase 4 + 2026-09-11 반자동 완료 라운드 Phase
        5·7 갱신 — `_verify_point_balance_and_shipping_or_block()`
        는 이제 유효한 사용자 최종 승인(PurchaseOrderApproval)이
        없으면 차단한다(더 이상 "항상" 차단이 아니다 — order_
        submission_service.py의 메서드 docstring 참고). 이 저장소의
        다른 게이트(발주 실행 자체, 매입 작업 배정)를 테스트하는
        대다수 기존 테스트는 그 게이트들의 로직만 격리해서 보고
        싶어하므로, 이 헬퍼는 Gate D(포인트·배송비 승인)만 이 테스트
        범위 안에서 통과한 것으로 바꿔치기한다 — 실제 승인이 생겼다는
        뜻이 절대 아니다. `mark_consumed()`가 이 반환값의 `.status`
        속성을 설정하므로 단순 Mock을 반환한다(무해 — 실제 DB 행이
        아니다). Gate D 자체의 진짜 동작(승인 없으면 차단, 있으면
        통과)은 PointBalanceGateTestCase/OrderApprovalGateTestCase가
        패치 없이 별도로 증명한다."""

        patcher = mock.patch(
            "app.domains.purchase_task.order_submission_service."
            "PurchaseOrderSubmissionService._verify_point_balance_and_shipping_or_block",
            return_value=mock.Mock(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _install_fake_adapter(self, *, result=None, error=None, call_log=None):

        self._patch_point_balance_gate_passes()

        class _FakeAdapter:
            def submit_order(self_inner, request):
                if call_log is not None:
                    call_log.append(1)
                if error is not None:
                    raise error
                return result

            def apply_for_sale(self_inner, external_product_id):
                return _FakeSalesApplicationResult(
                    applied_product_code=external_product_id,
                )

        patcher = mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class ConfirmationGateTestCase(OrderSubmissionServiceTestCaseBase):

    def test_missing_confirm_flag_rejected_before_any_db_or_network_work(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_fake_adapter(result="ORDER123", call_log=call_log)

        with self.assertRaises(BadRequestException):
            self.service.submit_order(
                connection.id, self.company_a.id,
                idempotency_key="k-1", **VALID_KWARGS,
            )

        self.assertEqual(call_log, [])
        self.assertEqual(
            self.db.query(PurchaseOrderSubmissionAttempt).count(), 0,
            "승인 게이트를 통과하지 못했으면 시도 행 자체가 생기면 안 된다.",
        )


class InputValidationTestCase(OrderSubmissionServiceTestCaseBase):

    def test_missing_required_field_rejected(self):

        connection = self._make_ready_connection()
        self._install_fake_adapter(result="ORDER123")

        kwargs = dict(VALID_KWARGS)
        kwargs["recv_name"] = ""
        with self.assertRaises(BadRequestException):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-1",
                confirm_real_submission=True, **kwargs,
            )

    def test_invalid_option_quantity_rejected(self):

        connection = self._make_ready_connection()
        self._install_fake_adapter(result="ORDER123")

        kwargs = dict(VALID_KWARGS)
        kwargs["options"] = [{"id": 1, "qty": 0}]
        with self.assertRaises(BadRequestException):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-1",
                confirm_real_submission=True, **kwargs,
            )


class ConnectionReadinessTestCase(OrderSubmissionServiceTestCaseBase):

    def test_unverified_connection_blocked_separately_from_task_assignment_gate(self):
        """verify_connection_ready_for_order_submission()이
        select_connection_for_task()와 별개로 동작한다 — 등록만
        되고 실제 조회 성공이 없는 연결은 발주도 배정도 둘 다
        막히지만, 각자 독립된 코드 경로다."""

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="미검증",
        )
        self.connection_service.save_credential(
            connection.id, self.company_a.id, auth_key="x",
        )
        self._install_fake_adapter(result="ORDER123")

        with self.assertRaises(ConflictException):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-1",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertEqual(self.db.query(PurchaseOrderSubmissionAttempt).count(), 0)

    def test_browser_login_connection_rejected(self):

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="네이버",
        )
        self._install_fake_adapter(result="ORDER123")

        with self.assertRaises(BadRequestException):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-1",
                confirm_real_submission=True, **VALID_KWARGS,
            )


class SuccessAndFailureClassificationTestCase(OrderSubmissionServiceTestCaseBase):
    """정상 응답 / 명시적 거절 / 응답 형식 오류 / 전송 결과 불명.

    이 클래스 전체가 _patch_contract_confirmed()를 쓴다 — 실제
    계약은 여전히 미확인(False)이지만, 여기서 검증하려는 것은
    "계약이 확인된 이후 Adapter 응답을 올바르게 분류하는가"라는
    이미 구현된 하위 로직이지 "지금 실제로 발주가 열리는가"가
    아니다. 후자는 NoSingleConditionAloneOpensRealOrderPathTestCase
    가 패치 없이(=실제 기본값 그대로) 별도로 증명한다."""

    def setUp(self):
        super().setUp()
        self._patch_contract_confirmed()

    def test_successful_submission_records_succeeded_with_order_code(self):

        connection = self._make_ready_connection()
        self._install_fake_adapter(result="ORDER-SUCCESS-1")

        attempt = self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-success",
            confirm_real_submission=True, **VALID_KWARGS,
        )

        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)
        self.assertEqual(attempt.external_order_code, "ORDER-SUCCESS-1")
        self.assertIsNotNone(attempt.finished_at)

        # 2026-09-15 Phase 9C(7-16) — 실제 발주 성공은 연결의 휴면
        # 판정 기준 시각(last_successful_order_at)도 함께 갱신해야
        # 한다.
        refreshed_connection = self.db.query(PurchaseChannelConnection).get(
            connection.id,
        )
        self.assertIsNotNone(refreshed_connection.last_successful_order_at)

    def test_explicit_rejection_records_rejected_not_unknown(self):

        from app.domains.purchase_task.onchannel_client import OnchannelValidationError

        connection = self._make_ready_connection()
        self._install_fake_adapter(
            error=OnchannelValidationError("요청 오류(400): 필수 파라미터 누락"),
        )

        with self.assertRaises(OnchannelValidationError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-rejected",
                confirm_real_submission=True, **VALID_KWARGS,
            )

        attempt = self.service.get_attempt(connection.id, self.company_a.id, "k-rejected")
        self.assertEqual(attempt.status, OrderSubmissionStatus.REJECTED)
        self.assertIsNone(attempt.external_order_code)

    def test_response_format_error_records_result_unknown(self):

        from app.domains.purchase_task.onchannel_client import OnchannelResponseFormatError

        connection = self._make_ready_connection()
        self._install_fake_adapter(
            error=OnchannelResponseFormatError("온채널 발주 응답에 필수 필드가 없습니다: order_code"),
        )

        with self.assertRaises(OnchannelResponseFormatError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-format",
                confirm_real_submission=True, **VALID_KWARGS,
            )

        attempt = self.service.get_attempt(connection.id, self.company_a.id, "k-format")
        self.assertEqual(attempt.status, OrderSubmissionStatus.RESULT_UNKNOWN)

    def test_network_timeout_records_result_unknown_not_failed(self):
        """전송 결과 불명 — 타임아웃 등은 REJECTED가 아니라
        RESULT_UNKNOWN이어야 한다(실패로 단정하지 않는다는 원칙)."""

        from app.domains.purchase_task.onchannel_client import OnchannelNetworkError

        connection = self._make_ready_connection()
        self._install_fake_adapter(
            error=OnchannelNetworkError("온채널 API 호출 실패(네트워크, 결과 불명): Timeout"),
        )

        with self.assertRaises(OnchannelNetworkError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-timeout",
                confirm_real_submission=True, **VALID_KWARGS,
            )

        attempt = self.service.get_attempt(connection.id, self.company_a.id, "k-timeout")
        self.assertEqual(attempt.status, OrderSubmissionStatus.RESULT_UNKNOWN)
        self.assertIsNone(attempt.external_order_code)

    def test_credential_related_failure_records_rejected_not_unknown(self):
        """자격증명 소실·무효로 인한 실패 — 네트워크에 아예 도달하지
        않았다는 사실이 확실하므로 REJECTED다(RESULT_UNKNOWN 아님)."""

        from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError

        connection = self._make_ready_connection()
        self._install_fake_adapter(
            error=PurchaseChannelAdapterError("온채널 자격증명이 저장되어 있지 않습니다."),
        )

        with self.assertRaises(PurchaseChannelAdapterError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-cred",
                confirm_real_submission=True, **VALID_KWARGS,
            )

        attempt = self.service.get_attempt(connection.id, self.company_a.id, "k-cred")
        self.assertEqual(attempt.status, OrderSubmissionStatus.REJECTED)

    def test_unexpected_exception_records_result_unknown(self):

        connection = self._make_ready_connection()
        self._install_fake_adapter(error=RuntimeError("전혀 예상 못한 오류"))

        with self.assertRaises(RuntimeError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-unexpected",
                confirm_real_submission=True, **VALID_KWARGS,
            )

        attempt = self.service.get_attempt(connection.id, self.company_a.id, "k-unexpected")
        self.assertEqual(attempt.status, OrderSubmissionStatus.RESULT_UNKNOWN)

    def test_recipient_pii_never_persisted_in_submission_attempt(self):
        """2026-09-09 후속(격리 검증 계속 — 개인정보 마스킹) —
        수취인명·연락처·주소는 PurchaseOrderSubmissionAttempt 테이블에
        컬럼 자체가 없다(model.py 설계). 여기서는 그 사실을 실제
        저장된 원시 DB row에서 직접 확인한다 — ORM 매핑을 거치지
        않고 raw SQL로 테이블 전체 컬럼을 읽어, PII 문자열이 어느
        컬럼에도(문자열로 우연히 섞여 들어가는 경우까지) 없음을
        증명한다."""

        connection = self._make_ready_connection()
        self._install_fake_adapter(result="ORDER-PII-CHECK")

        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-pii-check",
            confirm_real_submission=True, **VALID_KWARGS,
        )

        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy import text

        column_names = {
            col["name"] for col in
            sa_inspect(self.engine).get_columns("purchase_order_submission_attempts")
        }
        pii_column_candidates = {
            "recv_name", "recv_tell", "recv_mobile", "address", "address_detail",
        }
        self.assertEqual(
            column_names & pii_column_candidates, set(),
            "발주 시도 테이블에 개인정보 컬럼 자체가 존재하면 안 된다.",
        )

        raw_row = self.db.execute(
            text(
                "SELECT * FROM purchase_order_submission_attempts "
                "WHERE idempotency_key = 'k-pii-check'",
            ),
        ).fetchone()
        raw_text = " ".join(str(v) for v in raw_row if v is not None)
        for pii_value in (
            VALID_KWARGS["recv_name"], VALID_KWARGS["recv_tell"],
            VALID_KWARGS["recv_mobile"], VALID_KWARGS["address"],
        ):
            self.assertNotIn(
                pii_value, raw_text,
                f"개인정보 값({pii_value!r})이 저장된 row 어디에도 있으면 안 된다.",
            )


class DuplicateLockAndRestartRecoveryTestCase(OrderSubmissionServiceTestCaseBase):
    """동시 실행 / 반복 클릭 / 프로세스 재시작.

    _patch_contract_confirmed() 사용 — 이미 구현된 잠금·복구
    로직만 검증한다(실제 계약 확인 여부와 무관)."""

    def setUp(self):
        super().setUp()
        self._patch_contract_confirmed()

    def _task_attempt(self, *, status, resolution="UNRESOLVED"):
        attempt = self.service._create_locked_attempt(
            connection_id=1, company_id=self.company_a.id,
            purchase_task_id=777, idempotency_key=f"task-lock-{status}-{resolution}",
            mall_code="ONCHANNEL", product_code="CH1",
            options=[{"id": 1, "qty": 1}], triggered_by=1,
        )
        attempt.status = status
        attempt.unknown_resolution_status = resolution
        self.db.commit()
        return attempt

    def test_task_lock_blocks_pending_inflight_and_succeeded_attempts(self):
        for status in (
            OrderSubmissionStatus.PENDING,
            OrderSubmissionStatus.IN_FLIGHT,
            OrderSubmissionStatus.SUCCEEDED,
        ):
            with self.subTest(status=status):
                attempt = self._task_attempt(status=status)
                self.assertTrue(self.service._has_blocking_task_attempt(777, self.company_a.id))
                self.db.delete(attempt)
                self.db.commit()

    def test_confirmed_unknown_blocks_retry_but_confirmed_absence_allows_it(self):
        from app.domains.purchase_task.constants import UnknownResolutionStatus

        attempt = self._task_attempt(
            status=OrderSubmissionStatus.RESULT_UNKNOWN,
            resolution=UnknownResolutionStatus.ORDER_CONFIRMED,
        )
        self.assertTrue(self.service._has_blocking_task_attempt(777, self.company_a.id))
        self.db.delete(attempt)
        self.db.commit()

        self._task_attempt(
            status=OrderSubmissionStatus.RESULT_UNKNOWN,
            resolution=UnknownResolutionStatus.ORDER_NOT_CONFIRMED,
        )
        self.assertFalse(self.service._has_blocking_task_attempt(777, self.company_a.id))

    def test_repeated_click_with_same_idempotency_key_blocked_after_first_success(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_fake_adapter(result="ORDER-1", call_log=call_log)

        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-repeat",
            confirm_real_submission=True, **VALID_KWARGS,
        )
        self.assertEqual(len(call_log), 1)

        with self.assertRaises(ConflictException):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-repeat",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        # 두 번째 시도는 실제 Adapter까지 도달하지 않는다 — DB 잠금이
        # 네트워크 호출보다 먼저 막는다.
        self.assertEqual(len(call_log), 1)

    def test_internal_finalize_failure_after_external_success_leaves_attempt_in_flight_and_blocks_retry(self):
        """2026-09-19 후속(항목 4 — 과거 안전성 보고 근거 보완) —
        "mtime 불변"이나 "성공 후 반복 클릭 차단" 테스트만으로는
        "외부 발주는 성공했지만 그 직후 내부 확정 커밋이 실패"하는
        정확한 시나리오를 직접 다루지 않는다는 지적에 대한 격리
        테스트다. 실제 발주는 하지 않는다 — 여전히 Fake Adapter.

        attempt.status=IN_FLIGHT는 adapter.submit_order() 호출
        *이전*에 이미 커밋된다(order_submission_service.py:462-471).
        그 직후 _finalize_attempt(SUCCEEDED)의 커밋이 예외로
        실패하면, DB에는 IN_FLIGHT 그대로 남아야 한다 — 그리고 그
        상태 확인은 매번 새로 DB를 읽는 _has_blocking_task_attempt/
        UNIQUE 제약 기반이므로, "재시작"(새 서비스 인스턴스)
        이후에도 재시도가 실 API를 다시 호출하지 않아야 한다."""

        connection = self._make_ready_connection()
        call_log = []
        self._install_fake_adapter(result="ORDER-CRASH-1", call_log=call_log)

        real_finalize = PurchaseOrderSubmissionService._finalize_attempt

        def _boom(self_svc, attempt, *, status, external_order_code=None, failure_detail=None):
            if status == OrderSubmissionStatus.SUCCEEDED:
                raise RuntimeError("시뮬레이션: 내부 확정 커밋 중 DB 오류")
            return real_finalize(
                self_svc, attempt, status=status,
                external_order_code=external_order_code, failure_detail=failure_detail,
            )

        with mock.patch.object(PurchaseOrderSubmissionService, "_finalize_attempt", _boom):
            with self.assertRaises(RuntimeError):
                self.service.submit_order(
                    connection.id, self.company_a.id, idempotency_key="k-crash",
                    confirm_real_submission=True, **VALID_KWARGS,
                )

        self.assertEqual(len(call_log), 1, "외부 호출 자체는 실제로 1회 나갔다(성공 응답까지 받음).")

        attempt = (
            self.db.query(PurchaseOrderSubmissionAttempt)
            .filter(PurchaseOrderSubmissionAttempt.idempotency_key == "k-crash")
            .one()
        )
        self.assertEqual(
            attempt.status, OrderSubmissionStatus.IN_FLIGHT,
            "확정 커밋이 실패하면 그 이전에 이미 커밋된 IN_FLIGHT 상태로 남아야 한다.",
        )
        self.assertIsNone(
            attempt.external_order_code,
            "확정 실패 시 order_code도 함께 커밋되지 않아야 한다(반쪽 기록 금지).",
        )

        # "재시작" 흉내 — 같은 DB에 대해 완전히 새 서비스 인스턴스로
        # 같은 idempotency_key를 재시도한다.
        restarted_service = PurchaseOrderSubmissionService(self.db)
        call_log_after_restart = []
        self._install_fake_adapter(result="ORDER-CRASH-2", call_log=call_log_after_restart)

        with self.assertRaises(ConflictException):
            restarted_service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-crash",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertEqual(
            call_log_after_restart, [],
            "재시작 후 재시도도 실 API를 다시 호출하지 않는다 — DB 잠금이 먼저 막는다.",
        )

    def test_concurrent_attempt_with_same_pending_key_blocked_at_db_level(self):
        """동시 실행 — 이미 PENDING/IN_FLIGHT인 시도가 있으면(아직
        결과가 나기 전이라도) 두 번째 시도는 UNIQUE 제약으로 거부된다."""

        connection = self._make_ready_connection()
        # 첫 번째 "동시 요청"이 먼저 행을 만든 상태를 직접 재현한다
        # (실제 스레드 두 개를 띄우지 않아도 DB 제약 자체가 핵심이다 —
        # migration 테스트에서 이미 UNIQUE 제약 자체는 별도 검증함).
        self.service._create_locked_attempt(
            connection_id=connection.id, company_id=self.company_a.id,
            purchase_task_id=None, idempotency_key="k-concurrent",
            mall_code="ONCHANNEL", product_code="CH1", options=[{"id": 1, "qty": 1}],
            triggered_by=None,
        )

        call_log = []
        self._install_fake_adapter(result="ORDER-2", call_log=call_log)

        with self.assertRaises(ConflictException):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-concurrent",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertEqual(call_log, [])

    def test_concurrent_attempt_with_different_key_same_task_blocked_at_db_level(self):
        """2026-09-15 전면 감사 후속(Phase 3) — _has_blocking_task_
        attempt()는 INSERT 이전 SELECT라, 두 "동시" 요청이 옵션이
        달라 서로 다른 idempotency_key를 계산했다면(예: 다른 옵션)
        이 사전 검사만으로는 둘 다 통과할 수 있다. 이 테스트는 그
        사전 검사를 거치지 않고 _create_locked_attempt를 직접 두 번
        호출해 "이미 둘 다 사전 검사를 통과한 뒤" 상태를 재현한다 —
        그래도 부분 UNIQUE INDEX(uq_purchase_order_submission_
        attempts_active_task)가 두 번째 INSERT 자체를 거부해야
        한다."""

        self.service._create_locked_attempt(
            connection_id=1, company_id=self.company_a.id,
            purchase_task_id=555, idempotency_key="k-race-a",
            mall_code="ONCHANNEL", product_code="CH1",
            options=[{"id": 1, "qty": 1}], triggered_by=None,
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service._create_locked_attempt(
                connection_id=1, company_id=self.company_a.id,
                purchase_task_id=555, idempotency_key="k-race-b",
                mall_code="ONCHANNEL", product_code="CH1",
                options=[{"id": 2, "qty": 3}], triggered_by=None,
            )
        self.assertIn("업무 주문", str(ctx.exception))

        # 다른 회사(company_b)는 같은 purchase_task_id 숫자를 써도
        # 전혀 차단되지 않아야 한다 — 이 인덱스가 회사 격리를
        # 위반하지 않는다는 것을 함께 확인한다.
        self.service._create_locked_attempt(
            connection_id=1, company_id=self.company_b.id,
            purchase_task_id=555, idempotency_key="k-race-c",
            mall_code="ONCHANNEL", product_code="CH1",
            options=[{"id": 1, "qty": 1}], triggered_by=None,
        )

    def test_result_unknown_attempt_survives_process_restart_and_still_blocks_retry(self):
        """프로세스 재시작 복구 — 결과 불명 상태로 끝난 시도가 새
        프로세스(새 DB 세션·새 서비스 인스턴스)에서도 그대로 보이고,
        같은 idempotency_key로는 여전히 재시도할 수 없다."""

        from app.domains.purchase_task.onchannel_client import OnchannelNetworkError

        connection = self._make_ready_connection()
        self._install_fake_adapter(
            error=OnchannelNetworkError("타임아웃"),
        )
        with self.assertRaises(OnchannelNetworkError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-restart",
                confirm_real_submission=True, **VALID_KWARGS,
            )

        # "재시작" — 완전히 새 세션·새 서비스 인스턴스로 같은 파일 DB에
        # 다시 접속한다.
        restarted_service = self._new_service_same_db()
        recovered = restarted_service.get_attempt(
            connection.id, self.company_a.id, "k-restart",
        )
        self.assertIsNotNone(recovered, "재시작 후에도 시도 이력이 그대로 남아야 한다.")
        self.assertEqual(recovered.status, OrderSubmissionStatus.RESULT_UNKNOWN)

        call_log = []

        class _FakeAdapter:
            def submit_order(self_inner, request):
                call_log.append(1)
                return "SHOULD-NOT-HAPPEN"

            def apply_for_sale(self_inner, external_product_id):
                return _FakeSalesApplicationResult(
                    applied_product_code=external_product_id,
                )

        with mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        ):
            with self.assertRaises(ConflictException):
                restarted_service.submit_order(
                    connection.id, self.company_a.id, idempotency_key="k-restart",
                    confirm_real_submission=True, **VALID_KWARGS,
                )
        self.assertEqual(
            call_log, [],
            "재시작 후에도 같은 idempotency_key로는 자동이든 수동이든 재전송하면 안 된다.",
        )

    def test_new_idempotency_key_after_unknown_result_is_allowed(self):
        """결과 불명 이후 사람이 직접 새 idempotency_key로 다시
        시도하는 것은 막지 않는다(자동 재시도만 막는다)."""

        from app.domains.purchase_task.onchannel_client import OnchannelNetworkError

        connection = self._make_ready_connection()
        self._install_fake_adapter(error=OnchannelNetworkError("타임아웃"))
        with self.assertRaises(OnchannelNetworkError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-first",
                confirm_real_submission=True, **VALID_KWARGS,
            )

        with mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
        ) as get_adapter:
            class _FakeAdapter:
                def submit_order(self_inner, request):
                    return "ORDER-NEW"

                def apply_for_sale(self_inner, external_product_id):
                    return _FakeSalesApplicationResult(
                        applied_product_code=external_product_id,
                    )
            get_adapter.return_value = _FakeAdapter()

            attempt = self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-second-manual",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)


class MultiTenantIsolationTestCase(OrderSubmissionServiceTestCaseBase):
    """다중 계정·회사 격리.

    _patch_contract_confirmed() 사용 — company_id 격리 자체는 계약
    게이트보다 먼저 검사되므로 첫 테스트(다른 회사 소유 연결 거부)는
    이 패치와 무관하게 통과한다. 나머지 두 테스트는 idempotency_key
    잠금 범위(회사 단위)를 확인하려면 성공 경로까지 가야 해서
    패치가 필요하다."""

    def setUp(self):
        super().setUp()
        self._patch_contract_confirmed()

    def test_other_company_cannot_submit_against_connection(self):

        connection = self._make_ready_connection(company=self.company_a)
        self._install_fake_adapter(result="ORDER-X")

        with self.assertRaises(Exception):
            self.service.submit_order(
                connection.id, self.company_b.id, idempotency_key="k-cross",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertEqual(
            self.db.query(PurchaseOrderSubmissionAttempt)
            .filter(PurchaseOrderSubmissionAttempt.company_id == self.company_b.id)
            .count(),
            0,
        )

    def test_same_idempotency_key_different_company_both_allowed(self):

        conn_a = self._make_ready_connection(company=self.company_a)
        conn_b = self._make_ready_connection(company=self.company_b)
        self._install_fake_adapter(result="ORDER-SHARED")

        attempt_a = self.service.submit_order(
            conn_a.id, self.company_a.id, idempotency_key="shared-key",
            confirm_real_submission=True, **VALID_KWARGS,
        )
        attempt_b = self.service.submit_order(
            conn_b.id, self.company_b.id, idempotency_key="shared-key",
            confirm_real_submission=True, **VALID_KWARGS,
        )

        self.assertEqual(attempt_a.status, OrderSubmissionStatus.SUCCEEDED)
        self.assertEqual(attempt_b.status, OrderSubmissionStatus.SUCCEEDED)

    def test_two_connections_same_company_independent_idempotency_space(self):
        """같은 회사 안에서도 서로 다른 연결이면 idempotency_key
        범위가 사실상 회사 단위로 공유된다는 점을 명시적으로
        확인한다(company_id, idempotency_key) UNIQUE — connection_id는
        제약에 포함되지 않는다, 그래서 같은 회사의 두 연결이 우연히
        같은 키를 쓰면 두 번째는 막힌다)."""

        conn_1 = self._make_ready_connection(company=self.company_a)
        conn_2 = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="두 번째 계정",
        )
        self.connection_service.save_credential(
            conn_2.id, self.company_a.id, auth_key="y",
        )
        row2 = self.db.query(PurchaseChannelConnection).get(conn_2.id)
        from datetime import datetime
        row2.status = "CONNECTED"
        row2.verified_at = datetime.utcnow()
        self.db.commit()

        self._install_fake_adapter(result="ORDER-1")
        self.service.submit_order(
            conn_1.id, self.company_a.id, idempotency_key="same-key-diff-connection",
            confirm_real_submission=True, **VALID_KWARGS,
        )

        with self.assertRaises(ConflictException):
            self.service.submit_order(
                conn_2.id, self.company_a.id, idempotency_key="same-key-diff-connection",
                confirm_real_submission=True, **VALID_KWARGS,
            )


class NoSingleConditionAloneOpensRealOrderPathTestCase(OrderSubmissionServiceTestCaseBase):
    """2026-09-08 후속(7번 결함 감사, "발주 준비 판정 수정" 이후
    재정정) — "CONNECTED 또는 verified_at만으로 발주가 허용되는
    경로가 없는가"를 사용자가 지정한 8개 조건(원 지시의 7개 + 다른
    회사 소유 연결) 각각에 대해 증명한다. 세 층을 분리해서 본다:

    - **게이트A(계정 배정 가능 + 조회 인증 확인)** —
      `verify_connection_ready_for_order_submission()`이 자체적으로
      검사하는 (1)(2) 조건. 대부분의 조건은 여기서부터 이미 막힌다
      (등록만/브라우저로그인형/포인트조회만/오래된-이제는 EXPIRED인-
      기록/이전 자격증명 버전 전부 이 단계에서 ConflictException·
      BadRequestException). 상품·주문 조회 성공만 이 단계를 의도대로
      통과한다(실제로 인증된 요청을 보낼 수 있다는 사실은 참이므로).
    - **게이트B(실제 발주 실행 허용, 계약 확인)** — 게이트A를
      통과해도 `is_onchannel_order_contract_fully_confirmed()`가
      False인 한(4개 계약 항목 중 하나라도 미확인이면 False) 여기서
      막힌다. 상품·주문 조회 성공 조건은 실제로
      이 게이트에서만 막힌다 — 그래서 이 두 조건이 "confirm_real_
      submission=True여도 계약 미확인이면 Fake 전송조차 호출되지
      않는다"를 증명하는 핵심 사례다.
    - **게이트C(confirm_real_submission)** — submit_order()의 첫
      줄. 이 파일 자신 외에는 어디서도 True를 넘기지 않는다(grep
      확인). 게이트A/B를 신경 쓰지 않고 항상 가장 먼저 막는
      마지막 방어선.

    아래 각 테스트는 "이 조건 하나만으로" 실제 발주(어댑터의
    submit_order 호출)까지 도달하지 않음을 증명하고, 정확히 어느
    게이트가 막는지까지 구분해서 기록한다."""

    def _install_fake_connection_adapter(
        self, *, lookup_product_result=None, lookup_order_result=None,
        check_member_point_result=None,
    ):
        """channel_connection_service가 쓰는 Adapter 팩토리를
        가짜로 바꿔치기한다(order_submission_service의 Adapter와는
        별개 — 발주 실행 자체는 이 테스트들에서 아예 일어나지
        않으므로 그쪽은 건드릴 필요가 없다)."""

        class _FakeConnectionAdapter:
            def lookup_product(self_inner, external_product_id):
                return lookup_product_result

            def lookup_order(self_inner, external_order_number):
                return lookup_order_result

            def check_member_point(self_inner):
                return check_member_point_result

            def check_connection(self_inner, account_label, *, verified_at=None):
                class _Result:
                    status = (
                        ChannelConnectionStatus.CONNECTED if verified_at is not None
                        else ChannelConnectionStatus.REGISTERED_UNVERIFIED
                    )
                return _Result()

        patcher = mock.patch(
            "app.domains.purchase_task.channel_connection_service.get_purchase_channel_adapter",
            return_value=_FakeConnectionAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _assert_submit_order_blocked_without_confirm_flag(self, connection):
        """게이트1 — confirm_real_submission을 넘기지 않는 한(=현재
        저장소의 모든 실제 호출부와 동일한 상황) submit_order()가
        BadRequestException으로 즉시 거부되고, 시도 행이 하나도
        생기지 않으며, 발주 어댑터가 아예 호출되지 않음을 증명한다."""

        submit_order_call_log = []

        class _AssertNeverCalledAdapter:
            def submit_order(self_inner, request):
                submit_order_call_log.append(1)
                return "SHOULD-NOT-HAPPEN"

        with mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
            return_value=_AssertNeverCalledAdapter(),
        ):
            with self.assertRaises(BadRequestException):
                self.service.submit_order(
                    connection.id, self.company_a.id,
                    idempotency_key="k-no-confirm", **VALID_KWARGS,
                )
        self.assertEqual(
            submit_order_call_log, [],
            "confirm_real_submission 없이는 발주 Adapter가 절대 호출되면 안 된다.",
        )
        self.assertEqual(
            self.db.query(PurchaseOrderSubmissionAttempt).count(), 0,
            "게이트1을 통과 못 했으면 시도 행 자체가 생기면 안 된다.",
        )

    def _assert_submit_order_blocked_even_with_confirm_flag(self, connection):
        """게이트C(confirm_real_submission=True)를 일부러 넘겨도,
        실제 기본값(4개 계약 항목 전부 미확인, 패치 없음)에서는
        게이트A 또는 게이트B가 여전히 막아
        Fake 전송 Adapter가 절대 호출되지 않음을 증명한다. 이 헬퍼는
        _patch_contract_confirmed()를 호출하지 않는다 — 실제
        저장소 기본값 그대로 검증하는 것이 핵심이다."""

        submit_order_call_log = []

        class _AssertNeverCalledAdapter:
            def submit_order(self_inner, request):
                submit_order_call_log.append(1)
                return "SHOULD-NOT-HAPPEN"

        with mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
            return_value=_AssertNeverCalledAdapter(),
        ):
            with self.assertRaises((BadRequestException, ConflictException)):
                self.service.submit_order(
                    connection.id, self.company_a.id,
                    idempotency_key="k-confirmed-but-blocked",
                    confirm_real_submission=True, **VALID_KWARGS,
                )
        self.assertEqual(
            submit_order_call_log, [],
            "confirm_real_submission=True를 넘겨도, 계약이 확인되지 "
            "않은 실제 기본값에서는 발주 Adapter가 절대 호출되면 안 된다.",
        )
        self.assertEqual(
            self.db.query(PurchaseOrderSubmissionAttempt)
            .filter(PurchaseOrderSubmissionAttempt.idempotency_key == "k-confirmed-but-blocked")
            .count(),
            0,
            "게이트A/B를 통과 못 했으면 시도 행 자체가 생기면 안 된다"
            "(시도 행은 게이트A·B를 모두 지난 뒤에만 생성된다).",
        )

    def _assert_submit_order_reaches_adapter_with_explicit_confirm(
        self, connection, *, idempotency_key,
    ):
        """2026-09-10 신규 — 게이트A(실제 인증)·게이트B(온채널 계약
        확인)가 둘 다 참인 연결에서, `confirm_real_submission=True`를
        호출부가 명시적으로 넘겼을 때만 실제로 발주 Adapter까지
        도달함을 증명한다. Fake Adapter를 써서 실제 네트워크 호출
        없이 이 사실만 확인한다 — 이 헬퍼 자신은 실제 발주를 승인하는
        것이 아니라, "세 조건이 모두 갖춰지면 코드 경로가 실제로
        열린다"는 배선 자체를 검증한다."""

        self._patch_point_balance_gate_passes()
        submit_order_call_log = []

        class _RecordingAdapter:
            def submit_order(self_inner, request):
                submit_order_call_log.append(request)
                return "FAKE-ORDER-CODE-FOR-WIRING-TEST"

            def apply_for_sale(self_inner, external_product_id):
                return _FakeSalesApplicationResult(
                    applied_product_code=external_product_id,
                )

        with mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
            return_value=_RecordingAdapter(),
        ):
            attempt = self.service.submit_order(
                connection.id, self.company_a.id,
                idempotency_key=idempotency_key,
                confirm_real_submission=True, **VALID_KWARGS,
            )

        self.assertEqual(
            len(submit_order_call_log), 1,
            "게이트A·B·C가 전부 참이면 Fake Adapter가 정확히 1번 "
            "호출돼야 한다(더 많지도 적지도 않게).",
        )
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)
        self.assertEqual(attempt.external_order_code, "FAKE-ORDER-CODE-FOR-WIRING-TEST")

    # 1) 자격증명 저장만 ------------------------------------------------

    def test_credential_saved_alone_does_not_open_order_path(self):

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="자격증명만",
        )
        self.connection_service.save_credential(
            connection.id, self.company_a.id, auth_key="only-saved",
        )
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertEqual(row.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)

        # 게이트A 단독으로도 이미 막힌다(자격증명 저장 자체는
        # "연결 확인"이 아니므로 CONNECTED가 아니다) — 게이트B(계약
        # 확인)까지 갈 필요도 없다.
        with self.assertRaises(ConflictException):
            self.connection_service.verify_connection_ready_for_order_submission(
                connection.id, self.company_a.id,
            )
        self._assert_submit_order_blocked_without_confirm_flag(connection)
        self._assert_submit_order_blocked_even_with_confirm_flag(connection)

    # 2) 수동 연결 확인(BROWSER_LOGIN 자기보고) --------------------------

    def test_manual_browser_login_confirm_alone_does_not_open_order_path(self):

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="NAVER_SHOPPING", account_label="네이버 수동확인",
        )
        self.connection_service.mark_verified(connection.id, self.company_a.id)
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)

        # 게이트A 단독으로도 이미 막힌다(CREDENTIAL 방식이 아니므로
        # BadRequestException — "API 기반 발주를 지원하지 않음").
        with self.assertRaises(BadRequestException):
            self.connection_service.verify_connection_ready_for_order_submission(
                connection.id, self.company_a.id,
            )
        self._assert_submit_order_blocked_without_confirm_flag(connection)
        self._assert_submit_order_blocked_even_with_confirm_flag(connection)

    # 3) 상품 조회 성공만 ------------------------------------------------

    def test_product_lookup_success_alone_reaches_adapter_only_with_explicit_confirm(self):
        """2026-09-10 온채널 공식 답변 도착 후 정정 — 이 테스트의
        원래 이름은 "...does_not_open_order_path"였고, 그때는
        게이트B(계약 미확인)가 상품 조회 성공만으로는 실제 발주에
        닿지 않는다는 것을 독립적으로 증명했다. 이제 온채널 4개
        계약 항목이 전부 공식 답변으로 확인돼(docs/HOMEZ_PROJECT_
        STATE.md 2026-09-10 절, app/domains/purchase_task/
        constants.py::ONCHANNEL_ORDER_CONTRACT_STATUS)
        `is_onchannel_order_contract_fully_confirmed()`가 실제
        기본값에서도 True가 됐다 — 게이트B는 더 이상 이 조건을
        막지 않는다(패치 없이도 통과, 의도된 변화다). 그래서 이
        테스트는 이제 다른 사실을 증명한다: 게이트A(실제 인증된
        조회 성공)·게이트B(계약 확인)가 둘 다 참이어도, 게이트C
        (`confirm_real_submission=True`를 호출부가 명시적으로 넘기지
        않는 한)는 여전히 독립적으로 막는다 — "조회 성공"이라는
        사실 자체는 실제 발주를 승인한 것이 전혀 아니다."""

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="상품조회만",
        )
        self.connection_service.save_credential(
            connection.id, self.company_a.id, auth_key="k1",
        )
        self._install_fake_connection_adapter(lookup_product_result={"fake": "product"})
        self.connection_service.lookup_product(connection.id, self.company_a.id, "CH1")

        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertEqual(
            row.status, ChannelConnectionStatus.CONNECTED,
            "상품 조회 성공이 CONNECTED를 기록하는 것 자체는 의도된 설계다"
            "(사용자 확정 사항).",
        )

        # 게이트A·게이트B 둘 다 실제 기본값(패치 없음)에서 통과한다 —
        # 온채널 계약이 실제로 확인됐기 때문이다(더 이상 가정이 아님).
        ready = self.connection_service.verify_connection_ready_for_order_submission(
            connection.id, self.company_a.id,
        )
        self.assertEqual(ready.id, connection.id)

        # confirm_real_submission 없이는 여전히 즉시 거부된다(게이트C,
        # 계약 확인 여부와 완전히 무관 — submit_order()의 첫 줄).
        self._assert_submit_order_blocked_without_confirm_flag(connection)

        # confirm_real_submission=True를 명시적으로 넘기면, 이제는
        # (게이트A·B가 참이므로) 실제로 Adapter까지 도달한다 — 이것이
        # 정확히 의도된 최종 동작이다: 진짜 인증된 연결 + 확인된
        # 계약 + 호출부의 명시적 승인 셋 다 있어야만 실제 전송
        # 코드에 닿는다. Fake Adapter로 실제 네트워크 호출 없이
        # 이 사실만 증명한다(발주 자체를 실행하지 않는다).
        self._assert_submit_order_reaches_adapter_with_explicit_confirm(
            connection, idempotency_key="k-product-lookup-then-confirmed",
        )

    # 4) 주문 조회 성공만 ------------------------------------------------

    def test_order_lookup_success_alone_reaches_adapter_only_with_explicit_confirm(self):
        """2026-09-10 정정 — 위 상품 조회 테스트와 동일한 이유(온채널
        계약 확인 후 게이트B가 더 이상 막지 않음)."""

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="주문조회만",
        )
        self.connection_service.save_credential(
            connection.id, self.company_a.id, auth_key="k1",
        )
        self._install_fake_connection_adapter(lookup_order_result={"fake": "order"})
        self.connection_service.lookup_order(connection.id, self.company_a.id, "ORD1")

        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)

        ready = self.connection_service.verify_connection_ready_for_order_submission(
            connection.id, self.company_a.id,
        )
        self.assertEqual(ready.id, connection.id)

        self._assert_submit_order_blocked_without_confirm_flag(connection)
        self._assert_submit_order_reaches_adapter_with_explicit_confirm(
            connection, idempotency_key="k-order-lookup-then-confirmed",
        )

    # 5) 포인트 조회 성공만(2026-09-08 발견된 결함의 회귀 테스트) -------

    def test_point_check_success_alone_does_not_open_order_path(self):

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="포인트조회만",
        )
        self.connection_service.save_credential(
            connection.id, self.company_a.id, auth_key="k1",
        )
        self._install_fake_connection_adapter(
            check_member_point_result="fake-point-result",
        )
        self.connection_service.check_member_point(connection.id, self.company_a.id)

        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertEqual(
            row.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED,
            "포인트 조회는 연결 확인 상태를 절대 건드리면 안 된다"
            "(2026-09-08에 발견·수정된 결함의 핵심 회귀 조건).",
        )
        self.assertIsNone(row.verified_at)

        # 게이트A 단독으로도 이미 막힌다 — 포인트 조회는 CONNECTED를
        # 만들지 않으므로 여전히 REGISTERED_UNVERIFIED다.
        with self.assertRaises(ConflictException):
            self.connection_service.verify_connection_ready_for_order_submission(
                connection.id, self.company_a.id,
            )
        self._assert_submit_order_blocked_without_confirm_flag(connection)
        self._assert_submit_order_blocked_even_with_confirm_flag(connection)

    # 6) 오래된 CONNECTED 기록 -------------------------------------------

    def test_stale_old_connected_record_alone_does_not_open_order_path(self):
        """2026-09-08 후속("인증 최신성 결함 처리") 이후 재정정 —
        이전 버전은 CREDENTIAL 방식 verified_at에 만료 기한이 없다는
        잔여 위험을 정직하게 기록만 하고 게이트1(현재는 게이트C)
        하나에만 의존했다. 이제 CREDENTIAL_REVERIFICATION_WINDOW_
        HOURS를 넘긴 verified_at은 _apply_status_recompute()가 자동
        으로 EXPIRED로 낮춘다(constants.py, channel_adapter.py) —
        그래서 이 조건은 이제 게이트A 자체에서 막힌다(더 이상 게이트
        C에만 의존하는 잔여 위험이 아니다)."""

        from datetime import datetime, timedelta

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="오래된기록",
        )
        self.connection_service.save_credential(
            connection.id, self.company_a.id, auth_key="k1",
        )
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        row.status = ChannelConnectionStatus.CONNECTED
        row.verified_at = datetime.utcnow() - timedelta(days=400)
        self.db.commit()

        # 실제 OnchannelChannelAdapter를 그대로 쓴다(Adapter를 가짜로
        # 바꿔치기하지 않는다) — 만료 계산이 진짜 Adapter 경로에서
        # 일어나는지 확인해야 하므로. InMemoryCredentialStore만
        # 주입돼 있어 네트워크는 여전히 호출되지 않는다.
        # 게이트A 자체가 이제 EXPIRED로 막는다(계약 확인 여부와
        # 무관하게 — 계약이 확인됐다고 가정해도 여전히 막힘을 함께
        # 증명한다).
        with mock.patch(
            "app.domains.purchase_task.channel_connection_service."
            "is_onchannel_order_contract_fully_confirmed",
            return_value=True,
        ):
            with self.assertRaises(ConflictException):
                self.connection_service.verify_connection_ready_for_order_submission(
                    connection.id, self.company_a.id,
                )
        refreshed = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertEqual(refreshed.status, ChannelConnectionStatus.EXPIRED)

        self._assert_submit_order_blocked_without_confirm_flag(connection)
        self._assert_submit_order_blocked_even_with_confirm_flag(connection)

    # 7) 다른 계정 또는 이전 자격증명 버전의 인증 결과 --------------------

    def test_older_credential_version_verification_alone_does_not_open_order_path(self):
        """이전 자격증명(v1)으로 실제 조회가 성공해 CONNECTED가 된
        뒤, 자격증명을 v2로 재저장하면 save_credential()이 무조건
        verified_at을 지운다(2026-09-08 재정정, id=3/4 사고 이후 이미
        구현됨) — v1의 인증 성공이 v2에 재사용되지 않음을 다시
        확인한다."""

        connection = self.connection_service.create_connection(
            self.company_a.id, mall_code="ONCHANNEL", account_label="자격증명버전",
        )
        self.connection_service.save_credential(
            connection.id, self.company_a.id, auth_key="v1-key",
        )
        self._install_fake_connection_adapter(lookup_product_result={"fake": "p"})
        self.connection_service.lookup_product(connection.id, self.company_a.id, "CH1")
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertEqual(row.status, ChannelConnectionStatus.CONNECTED)

        # 자격증명을 새 버전으로 재저장 — v1의 인증 성공 기록이
        # v2에 재사용되면 안 된다.
        self.connection_service.save_credential(
            connection.id, self.company_a.id, auth_key="v2-key",
        )
        row = self.db.query(PurchaseChannelConnection).get(connection.id)
        self.assertEqual(row.status, ChannelConnectionStatus.REGISTERED_UNVERIFIED)
        self.assertIsNone(row.verified_at)

        with self.assertRaises(ConflictException):
            self.connection_service.verify_connection_ready_for_order_submission(
                connection.id, self.company_a.id,
            )
        self._assert_submit_order_blocked_without_confirm_flag(connection)
        self._assert_submit_order_blocked_even_with_confirm_flag(connection)

    def test_cross_account_connection_alone_does_not_open_order_path(self):
        """"다른 계정"의 또 다른 해석 — 다른 회사(company_b) 소유
        연결로 회사 A가 발주를 시도할 수 없음(멀티테넌시 격리,
        MultiTenantIsolationTestCase와 동일 원칙을 이 감사 항목
        번호에도 명시적으로 대응시킨다)."""

        connection = self._make_ready_connection(company=self.company_b)
        self._install_fake_adapter(result="SHOULD-NOT-HAPPEN")

        with self.assertRaises(Exception):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-cross-2",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertEqual(
            self.db.query(PurchaseOrderSubmissionAttempt)
            .filter(PurchaseOrderSubmissionAttempt.company_id == self.company_a.id)
            .count(),
            0,
        )


class _FakePointResult:

    def __init__(self, *, support="SUPPORTED", point=None, point_interpretable=True):
        self.support = support
        self.member_id_masked = "t***"
        self.point = point
        self.point_interpretable = point_interpretable
        self.observed_fields = ("member_id", "point")
        self.detail = "FAKE"


class _FakeProductOption:

    def __init__(self, option_id, price):
        self.option_id = option_id
        self.label = "기본"
        self.price = price
        self.in_stock = True


class _FakeProductResult:

    def __init__(self, *, support="SUPPORTED", options=()):
        self.support = support
        self.external_product_id = "CH1234567"
        self.title = "테스트 상품"
        self.options = options
        self.detail = "FAKE"


class PointBalanceGateTestCase(OrderSubmissionServiceTestCaseBase):
    """2026-09-10 Phase 4 + 2026-09-11 반자동 완료 라운드 Phase 5·7
    갱신 — `_verify_point_balance_and_shipping_or_block()`의 실제
    (패치 없는) 동작을 증명한다. 온채널에 배송비 사전 확인 API가
    없다는 확인된 사실은 그대로지만, 이제는 유효한 사용자 최종
    승인(PurchaseOrderApproval)이 있으면 통과한다 — "항상 차단"이던
    옛 동작은 더 이상 사실이 아니다. 이 클래스는 여전히 승인이 없을
    때의 차단 동작을 전담하고, 승인이 있을 때 실제로 통과하는지는
    `OrderApprovalGateIntegrationTestCase`가 별도로 증명한다. 다른
    클래스들이 쓰는 `_patch_point_balance_gate_passes()`는 이
    클래스에서는 절대 호출하지 않는다."""

    def _install_point_and_product_adapter(
        self, *, point_result=None, product_result=None,
        point_error=None, product_error=None,
    ):

        class _FakeAdapter:
            def check_member_point(self_inner):
                if point_error is not None:
                    raise point_error
                return point_result

            def lookup_product(self_inner, external_product_id):
                if product_error is not None:
                    raise product_error
                return product_result

            def apply_for_sale(self_inner, external_product_id):
                return _FakeSalesApplicationResult(
                    applied_product_code=external_product_id,
                )

            def submit_order(self_inner, request):
                raise AssertionError(
                    "Gate D가 통과됐다는 뜻이다 — 이 클래스의 전제와 어긋난다.",
                )

        patcher = mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pending_zero_stock_proposal_blocks_before_point_check(self):
        """2026-09-15 Phase 9F(8-19) — 같은 상품에 PENDING 가상재고 0
        제안이 있으면 포인트·상품가 조회 자체를 시도하지 않고 먼저
        차단한다(어댑터가 전혀 호출되지 않아야 한다)."""

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_result=_FakePointResult(point=1_000_000),
        )
        self.db.add(VirtualStockZeroProposal(
            company_id=self.company_a.id, connection_id=connection.id,
            product_code=VALID_KWARGS["product_code"],
            reason="테스트 — 판매 가능 여부 확인 불가",
        ))
        self.db.commit()

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id,
                idempotency_key="k-zero-stock-pending",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("가상재고 0 제안", str(ctx.exception))

    def test_blocked_attribute_comparison_blocks_before_point_check(self):
        """2026-09-15 Phase 9G(10-4) — 이 상품에 대해 가장 최근 실행된
        속성 비교가 BLOCKED이고 아직 해소되지 않았으면 포인트·상품가
        조회 자체를 시도하지 않고 먼저 차단한다."""

        from app.domains.product_attribute_match.service import (
            ProductAttributeMatchService,
        )

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_result=_FakePointResult(point=1_000_000),
        )
        ProductAttributeMatchService(self.db).run_comparison(
            company_id=self.company_a.id,
            product_identifier=VALID_KWARGS["product_code"],
            supplier_values={"NAME": ("상품A", "SUPPLIER", None)},
            sales_channel_values={"NAME": ("상품B", "CHANNEL", None)},
            homez_current_values={},
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id,
                idempotency_key="k-attribute-mismatch-pending",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("속성 비교", str(ctx.exception))

    def test_point_query_failure_blocks(self):

        from app.domains.purchase_task.onchannel_client import OnchannelAuthenticationError

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_error=OnchannelAuthenticationError("인증 실패"),
        )

        with self.assertRaises(OnchannelAuthenticationError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-point-fail",
                confirm_real_submission=True, **VALID_KWARGS,
            )

    def test_unclear_point_response_blocks(self):

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_result=_FakePointResult(point=None, point_interpretable=False),
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-point-unclear",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("해석할 수 없습니다", str(ctx.exception))

    def test_product_lookup_failure_blocks(self):

        from app.domains.purchase_task.onchannel_client import OnchannelNotFoundError

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_result=_FakePointResult(point=1_000_000),
            product_error=OnchannelNotFoundError("상품 없음"),
        )

        with self.assertRaises(OnchannelNotFoundError):
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-product-fail",
                confirm_real_submission=True, **VALID_KWARGS,
            )

    def test_missing_option_price_blocks(self):

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_result=_FakePointResult(point=1_000_000),
            product_result=_FakeProductResult(options=()),
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-option-missing",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("가격을 확인할 수 없습니다", str(ctx.exception))

    def test_insufficient_balance_blocks_with_distinct_message(self):

        from decimal import Decimal

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_result=_FakePointResult(point=100),
            product_result=_FakeProductResult(
                options=(_FakeProductOption("1", Decimal("10000")),),
            ),
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-insufficient",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("보다 적습니다", str(ctx.exception))

    def test_no_purchase_task_id_blocks_since_approval_cannot_be_looked_up(self):
        """purchase_task_id 없이는 어떤 승인을 조회해야 할지조차
        알 수 없다 — 발주를 차단한다."""

        from decimal import Decimal

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_result=_FakePointResult(point=1_000_000),
            product_result=_FakeProductResult(
                options=(_FakeProductOption("1", Decimal("10000")),),
            ),
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-shipping-unconfirmed",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("배송비", str(ctx.exception))
        self.assertEqual(
            self.db.query(PurchaseOrderSubmissionAttempt).count(), 0,
            "Gate D를 통과 못 했으면 발주 시도 행 자체가 생기면 안 된다.",
        )

    def test_purchase_task_id_without_any_approval_blocks(self):

        from decimal import Decimal

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter(
            point_result=_FakePointResult(point=1_000_000),
            product_result=_FakeProductResult(
                options=(_FakeProductOption("1", Decimal("10000")),),
            ),
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-no-approval",
                purchase_task_id=999, confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("승인", str(ctx.exception))


class OrderApprovalGateIntegrationTestCase(OrderSubmissionServiceTestCaseBase):
    """2026-09-11 신규(반자동 완료 라운드 Phase 5·7) — 유효한 사용자
    최종 승인(PurchaseOrderApproval, status=ACTIVE)이 있으면 Gate D를
    실제로 통과해 발주 Adapter까지 도달하는지 증명한다. 승인 행은
    여기서 직접 DB에 삽입한다(PurchaseOrderApprovalService의 전체
    확인·finalize 흐름은 tests/test_purchase_order_approval_service.py
    가 전담 — 이 클래스는 order_submission_service.py가 그 결과를
    올바르게 "소비"하는지만 본다)."""

    def _install_point_and_product_adapter_with_submit(
        self, *, point=1_000_000, price=10000, call_log=None,
    ):

        from decimal import Decimal

        class _FakeAdapter:
            def check_member_point(self_inner):
                return _FakePointResult(point=point)

            def lookup_product(self_inner, external_product_id):
                return _FakeProductResult(
                    options=(_FakeProductOption("1", Decimal(str(price))),),
                )

            def apply_for_sale(self_inner, external_product_id):
                return _FakeSalesApplicationResult(
                    applied_product_code=external_product_id,
                )

            def submit_order(self_inner, request):
                if call_log is not None:
                    call_log.append(request)
                return "ORDER-VIA-APPROVAL"

        patcher = mock.patch(
            "app.domains.purchase_task.order_submission_service.get_purchase_channel_adapter",
            return_value=_FakeAdapter(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _insert_active_approval(
        self, *, connection_id, product_code, item_amount_snapshot,
        shipping_cost_amount, purchase_task_id=1,
    ):

        from datetime import datetime, timedelta

        approval = PurchaseOrderApproval(
            company_id=self.company_a.id, connection_id=connection_id,
            purchase_task_id=purchase_task_id, product_code=product_code,
            status=PurchaseOrderApprovalStatus.ACTIVE,
            shipping_cost_amount=shipping_cost_amount,
            shipping_cost_is_free_confirmed=(shipping_cost_amount == 0),
            item_amount_snapshot=item_amount_snapshot,
            approved_by=1, approved_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def test_valid_active_approval_reaches_adapter_and_gets_consumed(self):

        connection = self._make_ready_connection()
        call_log = []
        self._install_point_and_product_adapter_with_submit(
            point=1_000_000, price=10000, call_log=call_log,
        )
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=42,
        )

        attempt = self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-approval-ok",
            purchase_task_id=42, confirm_real_submission=True, **VALID_KWARGS,
        )

        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)
        self.assertEqual(len(call_log), 1)

        approval = (
            self.db.query(PurchaseOrderApproval)
            .filter(PurchaseOrderApproval.purchase_task_id == 42)
            .first()
        )
        self.assertEqual(approval.status, PurchaseOrderApprovalStatus.CONSUMED)

    def test_approval_with_mismatched_price_blocks(self):
        """승인 시점 가격 스냅샷과 지금 조회한 가격이 다르면(가격
        인상 등) 승인을 신뢰하지 않는다."""

        connection = self._make_ready_connection()
        call_log = []
        self._install_point_and_product_adapter_with_submit(
            point=1_000_000, price=12000, call_log=call_log,  # 승인 당시 10000에서 인상됨
        )
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=43,
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-approval-stale",
                purchase_task_id=43, confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("가격", str(ctx.exception))
        self.assertEqual(call_log, [], "가격이 어긋나면 발주 Adapter가 호출되면 안 된다.")

    def test_expired_approval_blocks(self):

        from datetime import datetime, timedelta

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        approval = self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=44,
        )
        approval.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-approval-expired",
                purchase_task_id=44, confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("승인", str(ctx.exception))

    def test_insufficient_points_including_shipping_blocks(self):
        """상품가만으로는 충분해 보여도(포인트>상품가), 승인된
        배송비를 더하면 부족한 경우를 정확히 잡아낸다."""

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=12000, price=10000)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=45,
        )

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-approval-insufficient",
                purchase_task_id=45, confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("배송비 포함 최종 필요 포인트", str(ctx.exception))

    # ---- 2026-09-18 D2 반자동 필수 흐름 보완 ----------------------------
    # 온채널 API 발주 성공 후 PurchaseTask.status가 TRACKING_REQUIRED로
    # 전환되지 않으면 송장 등록 화면에 도달할 방법이 없어 흐름이
    # 끊긴다(record_purchase()는 이 트랙에서 전혀 호출되지 않는다).

    def _create_task(self, *, task_id, status=PurchaseTaskStatus.SEARCH_REQUIRED):
        task = PurchaseTask(
            id=task_id, company_id=self.company_a.id, source_order_id=1,
            product_title="테스트 상품", idempotency_key=f"task-{task_id}",
            status=status,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def test_successful_order_advances_linked_purchase_task_to_tracking_required(self):
        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        self._create_task(task_id=46, status=PurchaseTaskStatus.PURCHASE_READY)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=46,
        )

        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-advance-task",
            purchase_task_id=46, confirm_real_submission=True, **VALID_KWARGS,
        )

        task = self.db.query(PurchaseTask).filter(PurchaseTask.id == 46).one()
        self.assertEqual(task.status, PurchaseTaskStatus.TRACKING_REQUIRED)

        audit_row = self.db.execute(
            text(
                "SELECT action FROM audit_logs WHERE entity_id = '46' "
                "AND action = 'PURCHASE_TASK_ADVANCED_AFTER_ONCHANNEL_ORDER'",
            ),
        ).fetchone()
        self.assertIsNotNone(audit_row)

    def test_terminal_purchase_task_status_is_not_overwritten(self):
        """이미 종결/차단된 작업(예: BLOCKED)은 발주가 성공해도 그대로
        둔다 — 임의로 되돌리거나 앞으로 넘기지 않는다."""

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        self._create_task(task_id=47, status=PurchaseTaskStatus.BLOCKED)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=47,
        )

        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-terminal-task",
            purchase_task_id=47, confirm_real_submission=True, **VALID_KWARGS,
        )

        task = self.db.query(PurchaseTask).filter(PurchaseTask.id == 47).one()
        self.assertEqual(task.status, PurchaseTaskStatus.BLOCKED)

    def test_advance_helper_is_a_no_op_when_task_row_does_not_exist(self):
        """`purchase_task_id`가 주어졌지만 실제 행이 없으면(예: 이미
        삭제됐거나 다른 회사 소속) 조용히 아무 것도 하지 않는다 —
        발주 자체는 이미 성공했으므로 예외를 던지지 않는다."""

        self.service._advance_task_after_successful_order(
            999999, self.company_a.id, order_code="ORDER-X", triggered_by=1,
        )  # 예외 없이 조용히 반환되어야 한다.
        self.assertEqual(
            self.db.query(PurchaseTask).filter(PurchaseTask.id == 999999).count(), 0,
        )


class AutomationSafetyGateTestCase(OrderSubmissionServiceTestCaseBase):
    """2026-09-10 신규(Phase 9 — 자동화 모드 배선) — 비상정지·PAUSED·
    ERROR는 confirm_real_submission=True를 넘겨도 뚫리지 않는다.
    기본값(MANUAL, 비상정지 없음)에서는 이 게이트가 막지 않는다는
    것은 이 파일의 다른 모든 성공 경로 테스트가 이미 증명한다 —
    여기서는 PAUSED/ERROR/비상정지 세 경우만 전담한다."""

    def test_emergency_stop_blocks_even_with_confirm_flag(self):

        from app.domains.automation_safety.model import EmergencyStop

        connection = self._make_ready_connection()
        self._install_fake_adapter(result="SHOULD-NOT-HAPPEN")
        self.db.add(EmergencyStop(is_active=True, reason="테스트", set_by=1))
        self.db.commit()

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-estop",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("비상정지", str(ctx.exception))
        self.assertEqual(self.db.query(PurchaseOrderSubmissionAttempt).count(), 0)

    def test_function_paused_blocks_even_with_confirm_flag(self):

        from app.domains.automation_safety.constants import FunctionCode
        from app.domains.automation_safety.constants import FunctionMode
        from app.domains.automation_safety.model import FunctionAutomationState

        connection = self._make_ready_connection()
        self._install_fake_adapter(result="SHOULD-NOT-HAPPEN")
        self.db.add(FunctionAutomationState(
            company_id=self.company_a.id, function_code=FunctionCode.PURCHASE_ORDER,
            mode=FunctionMode.PAUSED, set_by=1,
        ))
        self.db.commit()

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-paused",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("일시 중지", str(ctx.exception))

    def test_function_error_mode_blocks_even_with_confirm_flag(self):

        from app.domains.automation_safety.constants import FunctionCode
        from app.domains.automation_safety.constants import FunctionMode
        from app.domains.automation_safety.model import FunctionAutomationState

        connection = self._make_ready_connection()
        self._install_fake_adapter(result="SHOULD-NOT-HAPPEN")
        self.db.add(FunctionAutomationState(
            company_id=self.company_a.id, function_code=FunctionCode.PURCHASE_ORDER,
            mode=FunctionMode.ERROR, set_by=1,
        ))
        self.db.commit()

        with self.assertRaises(ConflictException) as ctx:
            self.service.submit_order(
                connection.id, self.company_a.id, idempotency_key="k-error-mode",
                confirm_real_submission=True, **VALID_KWARGS,
            )
        self.assertIn("오류", str(ctx.exception))

    def test_other_companys_paused_mode_does_not_affect_this_company(self):
        """회사별 격리 — company_b의 PAUSED 설정이 company_a의 발주를
        막으면 안 된다."""

        from app.domains.automation_safety.constants import FunctionCode
        from app.domains.automation_safety.constants import FunctionMode
        from app.domains.automation_safety.model import FunctionAutomationState

        connection = self._make_ready_connection(company=self.company_a)
        self._install_fake_adapter(result="ORDER-ISOLATED")
        self.db.add(FunctionAutomationState(
            company_id=self.company_b.id, function_code=FunctionCode.PURCHASE_ORDER,
            mode=FunctionMode.PAUSED, set_by=1,
        ))
        self.db.commit()

        attempt = self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-not-affected",
            confirm_real_submission=True, **VALID_KWARGS,
        )
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)

    def test_manual_mode_default_does_not_block_at_this_gate(self):
        """기본값(설정한 적 없음 = MANUAL)은 이 게이트에서 막히지
        않는다 — MANUAL/SEMI_AUTOMATIC/AUTOMATIC 세 모드 모두
        confirm_real_submission=True라는 동일한 승인을 요구할 뿐,
        이 게이트 자체가 그 셋을 구분해서 차단하지 않는다."""

        connection = self._make_ready_connection()
        self._install_fake_adapter(result="ORDER-MANUAL-DEFAULT")

        attempt = self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-manual-default",
            confirm_real_submission=True, **VALID_KWARGS,
        )
        self.assertEqual(attempt.status, OrderSubmissionStatus.SUCCEEDED)


class OnchannelSpendLimitProtectionTestCase(OrderApprovalGateIntegrationTestCase):
    """2026-09-19 항목 2/4/5(지출한도 누락 해소) — 실제 submit_order()
    성공이 송장조회 실행 여부와 무관하게 즉시 지출한도(daily/monthly
    purchase limit, `sum_recorded_amount_since`)에 반영되는지, 그리고
    이후 실 API 확정·환불이 그 한도 판정과 일관되는지 실제 판정
    결과와 장부 금액으로 검증한다. 실제 발주·결제·환불 API는 쓰지
    않는다(Fake Adapter만 사용)."""

    def setUp(self):

        super().setUp()
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                FundingAccount.__table__, FundingLedger.__table__,
                PurchaseTaskBudgetReservation.__table__,
                PurchaseRecord.__table__,
                PurchaseOrderUnknownResolutionEvent.__table__,
                PurchaseTaskTrackingInfo.__table__,
            ],
        )
        self.account = FundingAccount(
            company_id=self.company_a.id, total_funding=1_000_000.0,
        )
        self.db.add(self.account)
        self.db.commit()
        self.repository = PurchaseTaskRepository(self.db)

    def _create_reserved_task(
        self, *, task_id, amount, company=None,
        status=PurchaseTaskStatus.PURCHASE_READY,
    ):

        company = company or self.company_a
        account = (
            self.account if company is self.company_a
            else self.db.query(FundingAccount)
            .filter(FundingAccount.company_id == company.id).first()
        )
        task = PurchaseTask(
            id=task_id, company_id=company.id, source_order_id=task_id,
            product_title="테스트 상품", idempotency_key=f"task-{task_id}",
            status=status,
        )
        self.db.add(task)
        self.db.commit()

        reservation = PurchaseTaskBudgetReservation(
            company_id=company.id, purchase_task_id=task.id,
            account_id=account.id, amount=amount,
            status=BudgetReservationStatus.RESERVED,
            expires_at=datetime.utcnow() + timedelta(hours=24),
        )
        self.db.add(reservation)
        account.held_amount = (account.held_amount or 0.0) + amount
        self.db.commit()
        self.db.refresh(reservation)

        task.budget_reservation_id = reservation.id
        self.db.commit()
        self.db.refresh(task)
        return task, reservation

    def _spent(self, company=None, days=1):

        company = company or self.company_a
        return self.repository.sum_recorded_amount_since(
            company.id, datetime.utcnow() - timedelta(days=days),
        )

    def test_successful_order_immediately_reduces_remaining_limit_before_any_tracking_lookup(self):

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        task, reservation = self._create_reserved_task(task_id=201, amount=13000.0)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=201,
        )

        self.assertEqual(self._spent(), 0.0, "발주 전에는 아직 지출이 없다.")

        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-limit-1",
            purchase_task_id=201, confirm_real_submission=True, **VALID_KWARGS,
        )

        # 송장조회(refresh_tracking_live)는 한 번도 실행하지 않았다.
        self.assertEqual(
            self._spent(), 13000.0,
            "송장조회를 실행하지 않았어도 발주 성공 직후부터 지출한도에 반영돼야 한다.",
        )
        self.db.refresh(reservation)
        self.assertEqual(
            reservation.status, BudgetReservationStatus.PENDING_VERIFICATION,
        )

    def test_next_purchase_evaluation_actually_blocks_on_reduced_headroom(self):
        """지시문 2번 — "테이블이 다르다"가 아니라 실제 다음
        승인·발주 가능 금액(PurchaseTaskPolicyService.evaluate())으로
        재현한다."""

        from decimal import Decimal
        from app.domains.purchase_task.policy_service import (
            PurchaseTaskPolicyCheckInput,
            PurchaseTaskPolicyReason,
            PurchaseTaskPolicyService,
        )

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        self._create_reserved_task(task_id=202, amount=13000.0)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=202,
        )
        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-limit-2",
            purchase_task_id=202, confirm_real_submission=True, **VALID_KWARGS,
        )

        policy = PurchaseTaskPolicyService(self.db)
        setting = policy.get_or_create_default_settings(self.company_a.id)
        setting.daily_purchase_limit_amount = 13000.0
        self.db.commit()

        result = policy.evaluate(
            self.company_a.id,
            PurchaseTaskPolicyCheckInput(
                match_confidence=1.0, match_tier="EXACT", quantity=1,
                gtin="1111111111111", model_name="M1",
                expected_net_profit=Decimal("1000"),
                expected_margin_rate=Decimal("10"),
                required_budget_amount=Decimal("5000"),
                estimated_delivery_days=2, return_allowed=True,
            ),
        )
        self.assertIn(
            PurchaseTaskPolicyReason.DAILY_LIMIT_EXCEEDED, result.reasons,
            "송장조회를 실행하지 않았어도 방금 확정된 온채널 실비용만으로 "
            "다음 발주 승인이 실제로 막혀야 한다.",
        )

    def test_unknown_confirmed_by_human_also_reduces_limit_immediately(self):
        """UNKNOWN 유지 및 수동 성공 확정 — resolve_unknown_attempt()
        경로도 동일하게 즉시 한도를 보호해야 한다."""

        connection = self._make_ready_connection()
        self._create_reserved_task(task_id=203, amount=13000.0)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=203,
        )
        attempt = self.service._create_locked_attempt(
            connection_id=connection.id, company_id=self.company_a.id,
            purchase_task_id=203, idempotency_key="k-unknown-limit",
            mall_code="ONCHANNEL", product_code="CH1234567",
            options=[{"id": 1, "qty": 1}], triggered_by=1,
        )
        attempt.status = OrderSubmissionStatus.RESULT_UNKNOWN
        self.db.commit()

        self.assertEqual(self._spent(), 0.0)

        self.service.resolve_unknown_attempt(
            attempt.id, self.company_a.id,
            resolution="ORDER_CONFIRMED", order_code="OC-HUMAN-CONFIRMED",
            resolved_by=1,
        )

        self.assertEqual(
            self._spent(), 13000.0,
            "사람이 UNKNOWN을 ORDER_CONFIRMED로 확정해도 송장조회 없이 즉시 반영돼야 한다.",
        )

    def test_final_api_amount_differs_from_approved_amount_adjusts_and_stays_counted(self):
        """승인금액과 실제금액 불일치 — Stage 1(승인액) 이후 Stage 2
        (실 API 확인)가 다른 금액을 확정하면 차액이 반영되고, 지출
        한도 집계는 최종 금액으로 유지돼야 한다."""

        from app.domains.purchase_task.service import PurchaseTaskService

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        task, reservation = self._create_reserved_task(task_id=204, amount=13000.0)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=204,
        )
        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-limit-diff",
            purchase_task_id=204, confirm_real_submission=True, **VALID_KWARGS,
        )
        self.assertEqual(self._spent(), 13000.0, "Stage 1 직후 승인액 기준.")

        task_service = PurchaseTaskService(self.db)
        self.db.refresh(task)
        task_service._reconcile_onchannel_order_reservation(
            task, self.company_a.id, order_code="ORDER-VIA-APPROVAL",
            sum_product_price=10800, sum_delivery_price=3500, sum_add_price=0,
            triggered_by=1,
        )

        self.db.refresh(reservation)
        self.assertEqual(reservation.status, BudgetReservationStatus.CONFIRMED)
        self.assertEqual(
            self._spent(), 14300.0,
            "실 API가 확인한 최종 금액(14,300원)으로 한도 집계가 갱신돼야 한다.",
        )

    def test_refund_after_confirmation_is_not_undone_by_a_later_stale_tracking_refresh(self):
        """전액·부분 환불 후 재조회 시 환불이 되돌려지지 않음 —
        취소·반품이 시작된 뒤 지연 도착한 송장조회가 이미 처리된
        환불을 되돌리면 안 된다."""

        from app.domains.purchase_task.service import PurchaseTaskService

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        task, reservation = self._create_reserved_task(task_id=205, amount=13000.0)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=205,
        )
        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-refund-1",
            purchase_task_id=205, confirm_real_submission=True, **VALID_KWARGS,
        )

        task_service = PurchaseTaskService(self.db)
        self.db.refresh(task)
        task_service._reconcile_onchannel_order_reservation(
            task, self.company_a.id, order_code="ORDER-VIA-APPROVAL",
            sum_product_price=10000, sum_delivery_price=3000, sum_add_price=0,
            triggered_by=1,
        )
        self.db.refresh(self.account)
        held_after_confirm = self.account.held_amount

        task.status = PurchaseTaskStatus.CANCEL_REQUIRED
        self.db.commit()
        task_service.record_refund(
            task.id, self.company_a.id, refund_amount=13000.0, recorded_by=1,
        )
        self.db.refresh(self.account)
        held_after_refund = self.account.held_amount
        self.assertEqual(held_after_refund, held_after_confirm - 13000.0)

        # 지연 도착한(또는 실수로 다시 실행된) 송장조회 — 환불 후.
        task_service._reconcile_onchannel_order_reservation(
            task, self.company_a.id, order_code="ORDER-VIA-APPROVAL",
            sum_product_price=10500, sum_delivery_price=3200, sum_add_price=0,
            triggered_by=1,
        )
        self.db.refresh(self.account)
        self.assertEqual(
            self.account.held_amount, held_after_refund,
            "취소·반품이 시작된 뒤에는 늦게 도착한 조회가 절대 예산을 다시 건드리지 않는다.",
        )

    def test_partial_refund_then_late_refresh_does_not_revert_partial_refund(self):
        """부분 환불도 동일하게 보호돼야 한다."""

        from app.domains.purchase_task.service import PurchaseTaskService

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        task, reservation = self._create_reserved_task(task_id=206, amount=13000.0)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=206,
        )
        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-refund-2",
            purchase_task_id=206, confirm_real_submission=True, **VALID_KWARGS,
        )

        task_service = PurchaseTaskService(self.db)
        self.db.refresh(task)
        task_service._reconcile_onchannel_order_reservation(
            task, self.company_a.id, order_code="ORDER-VIA-APPROVAL",
            sum_product_price=10000, sum_delivery_price=3000, sum_add_price=0,
            triggered_by=1,
        )

        task.status = PurchaseTaskStatus.RETURN_REQUIRED
        self.db.commit()
        task_service.record_refund(
            task.id, self.company_a.id, refund_amount=3000.0, recorded_by=1,
        )
        self.db.refresh(self.account)
        held_after_partial_refund = self.account.held_amount

        task_service._reconcile_onchannel_order_reservation(
            task, self.company_a.id, order_code="ORDER-VIA-APPROVAL",
            sum_product_price=11000, sum_delivery_price=3000, sum_add_price=0,
            triggered_by=1,
        )
        self.db.refresh(self.account)
        self.assertEqual(self.account.held_amount, held_after_partial_refund)

    def test_manual_track_purchase_record_behavior_is_unchanged(self):
        """수동 구매 경로의 기존 동작 유지 — PurchaseRecord 기반
        집계는 이번 변경 전과 동일해야 한다."""

        record = PurchaseRecord(
            company_id=self.company_a.id, purchase_task_id=901,
            shopping_mall_code="NAVER_SHOPPING", external_order_number="N-1",
            actual_amount=5000.0, actual_shipping_fee=2500.0,
            purchased_at=datetime.utcnow(), recorded_by=1,
            idempotency_key="manual-rec-1",
        )
        self.db.add(record)
        self.db.commit()

        self.assertEqual(
            self._spent(), 5000.0,
            "actual_shipping_fee는 여전히 한도 집계에 포함하지 않는다(기존 동작).",
        )

    def test_manual_and_onchannel_spend_in_same_window_are_both_counted_without_double_counting(self):

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        self._create_reserved_task(task_id=207, amount=13000.0)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=207,
        )
        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-mixed-1",
            purchase_task_id=207, confirm_real_submission=True, **VALID_KWARGS,
        )

        record = PurchaseRecord(
            company_id=self.company_a.id, purchase_task_id=902,
            shopping_mall_code="NAVER_SHOPPING", external_order_number="N-2",
            actual_amount=7000.0, actual_shipping_fee=1000.0,
            purchased_at=datetime.utcnow(), recorded_by=1,
            idempotency_key="manual-rec-2",
        )
        self.db.add(record)
        self.db.commit()

        self.assertEqual(self._spent(), 20000.0, "13,000(온채널) + 7,000(수동), 이중계산 없음.")

    def test_company_isolation_between_connections_and_tasks(self):
        """회사·연결 간 데이터 격리."""

        account_b = FundingAccount(company_id=self.company_b.id, total_funding=500_000.0)
        self.db.add(account_b)
        self.db.commit()

        connection_a = self._make_ready_connection(company=self.company_a)
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        self._create_reserved_task(task_id=208, amount=13000.0, company=self.company_a)
        self._insert_active_approval(
            connection_id=connection_a.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=208,
        )
        self.service.submit_order(
            connection_a.id, self.company_a.id,
            idempotency_key="k-isolation-a", purchase_task_id=208,
            confirm_real_submission=True, **VALID_KWARGS,
        )

        self.assertEqual(self._spent(company=self.company_a), 13000.0)
        self.assertEqual(
            self._spent(company=self.company_b), 0.0,
            "B사는 A사의 온채널 실비용 확정과 완전히 무관해야 한다.",
        )

    def test_external_spend_is_recorded_even_when_funds_cannot_cover_the_difference(self):
        """2026-09-20 — 이미 나간 외부 지출은 운영 가능 금액이 모자라도
        숨기거나 기록을 거부하지 않는다. 기록하고(한도 집계 포함), 다음
        위험 실행이 막히게 한다."""

        from decimal import Decimal
        from app.domains.purchase_task.policy_service import (
            PurchaseTaskPolicyCheckInput,
            PurchaseTaskPolicyReason,
            PurchaseTaskPolicyService,
        )

        self.account.total_funding = 15000.0
        self.db.commit()
        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=13000)
        task, reservation = self._create_reserved_task(task_id=211, amount=13000.0)
        self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=13000, shipping_cost_amount=3000,
            purchase_task_id=211,
        )

        self.service.submit_order(
            connection.id, self.company_a.id, idempotency_key="k-overdrawn",
            purchase_task_id=211, confirm_real_submission=True, **VALID_KWARGS,
        )

        self.db.refresh(reservation)
        self.db.refresh(self.account)
        self.assertEqual(reservation.status, BudgetReservationStatus.PENDING_VERIFICATION)
        self.assertEqual(reservation.amount, 16000.0)
        self.assertEqual(self._spent(), 16000.0, "자금이 모자라도 이미 나간 지출은 한도 집계에 남는다.")
        self.assertEqual(self.account.held_amount, 16000.0)

        policy = PurchaseTaskPolicyService(self.db)
        result = policy.evaluate(
            self.company_a.id,
            PurchaseTaskPolicyCheckInput(
                match_confidence=1.0, match_tier="EXACT", quantity=1,
                gtin="1111111111111", model_name="M1",
                expected_net_profit=Decimal("1000"),
                expected_margin_rate=Decimal("10"),
                required_budget_amount=Decimal("1000"),
                estimated_delivery_days=2, return_allowed=True,
            ),
        )
        self.assertIn(
            PurchaseTaskPolicyReason.BUDGET_INSUFFICIENT, result.reasons,
            "초과 기록 이후 다음 승인은 운영 가능 금액 부족으로 막혀야 한다.",
        )

    def test_unresolved_attempt_amount_stays_in_approval_layer_limit_after_approval_expiry(self):
        """외부 성공 직후 내부 저장 실패(IN_FLIGHT 잔존) — 승인 유효시간이
        지나도 그 금액이 발주 승인 단계의 일간/월간 한도 집계에서 빠지지
        않아야 한다(같은 작업 재발주는 이미 _has_blocking_task_attempt가
        막지만, 다른 작업의 승인은 한도가 막아야 한다)."""

        from app.domains.purchase_task.order_approval_service import (
            PurchaseOrderApprovalService,
        )

        connection = self._make_ready_connection()
        self._install_point_and_product_adapter_with_submit(point=1_000_000, price=10000)
        self._create_reserved_task(task_id=212, amount=13000.0)
        approval = self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=212,
        )

        real_finalize = PurchaseOrderSubmissionService._finalize_attempt

        def _boom(self_svc, attempt, *, status, external_order_code=None, failure_detail=None):
            if status == OrderSubmissionStatus.SUCCEEDED:
                raise RuntimeError("시뮬레이션: 내부 확정 커밋 중 DB 오류")
            return real_finalize(
                self_svc, attempt, status=status,
                external_order_code=external_order_code, failure_detail=failure_detail,
            )

        with mock.patch.object(PurchaseOrderSubmissionService, "_finalize_attempt", _boom):
            with self.assertRaises(RuntimeError):
                self.service.submit_order(
                    connection.id, self.company_a.id, idempotency_key="k-save-fail",
                    purchase_task_id=212, confirm_real_submission=True, **VALID_KWARGS,
                )

        approval_service = PurchaseOrderApprovalService(self.db)
        since = datetime.utcnow() - timedelta(hours=24)
        self.assertEqual(
            approval_service._sum_reserved_amount(self.company_a.id, since), 13000,
            "유효시간 안에는 ACTIVE 승인으로 집계된다.",
        )

        self.db.refresh(approval)
        approval.expires_at = datetime.utcnow() - timedelta(minutes=1)
        self.db.commit()
        self.assertEqual(
            approval_service._sum_reserved_amount(self.company_a.id, since), 13000,
            "승인이 만료돼도 결과 미확정(IN_FLIGHT) 발주 금액은 한도에서 빠지지 않는다.",
        )

    def test_unknown_attempt_amount_is_protected_until_human_resolves_it_as_not_created(self):

        from app.domains.purchase_task.order_approval_service import (
            PurchaseOrderApprovalService,
        )

        connection = self._make_ready_connection()
        self._create_reserved_task(task_id=213, amount=13000.0)
        approval = self._insert_active_approval(
            connection_id=connection.id, product_code="CH1234567",
            item_amount_snapshot=10000, shipping_cost_amount=3000,
            purchase_task_id=213,
        )
        approval.status = PurchaseOrderApprovalStatus.EXPIRED
        approval.expires_at = datetime.utcnow() - timedelta(minutes=1)
        attempt = self.service._create_locked_attempt(
            connection_id=connection.id, company_id=self.company_a.id,
            purchase_task_id=213, idempotency_key="k-unknown-approval-layer",
            mall_code="ONCHANNEL", product_code="CH1234567",
            options=[{"id": 1, "qty": 1}], triggered_by=1,
        )
        attempt.status = OrderSubmissionStatus.RESULT_UNKNOWN
        self.db.commit()

        approval_service = PurchaseOrderApprovalService(self.db)
        since = datetime.utcnow() - timedelta(hours=24)
        self.assertEqual(
            approval_service._sum_reserved_amount(self.company_a.id, since), 13000,
            "사람이 확정하기 전 UNKNOWN 금액은 만료된 승인이어도 한도에 남는다.",
        )

        self.service.resolve_unknown_attempt(
            attempt.id, self.company_a.id, resolution="ORDER_NOT_CONFIRMED",
            basis="온채널 관리자 화면에서 주문 없음을 직접 확인", resolved_by=1,
        )
        self.assertEqual(
            approval_service._sum_reserved_amount(self.company_a.id, since), 0,
            "주문 미생성이 확인되면 그 금액은 한도에서 빠진다.",
        )

    def test_daily_window_boundary_excludes_older_and_includes_newer_confirmations(self):
        """일간 경계에서 누락 또는 이중 차감 없음."""

        task_old, reservation_old = self._create_reserved_task(task_id=209, amount=9000.0)
        reservation_old.status = BudgetReservationStatus.CONFIRMED
        reservation_old.confirmed_at = datetime.utcnow() - timedelta(days=2)
        self.db.commit()

        task_new, reservation_new = self._create_reserved_task(task_id=210, amount=11000.0)
        reservation_new.status = BudgetReservationStatus.PENDING_VERIFICATION
        reservation_new.confirmed_at = datetime.utcnow() - timedelta(hours=1)
        self.db.commit()

        self.assertEqual(
            self._spent(days=1), 11000.0,
            "24시간이 지난 확정 건은 일간 집계에서 빠지고, 최근 건만 잡혀야 한다.",
        )
        self.assertEqual(
            self._spent(days=30), 20000.0,
            "30일 창에는 두 건 모두 잡혀야 한다.",
        )


if __name__ == "__main__":
    unittest.main()
