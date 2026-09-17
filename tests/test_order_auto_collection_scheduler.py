"""
=========================================================
Homez OS

File : tests/test_order_auto_collection_scheduler.py

2026-09-16 개인 베타 잔여 작업(Phase 6, HOMEZ_USER_OPERATION_SETTINGS.md
2-8) — "신규 주문 5분 자동 감지" 스케줄러 게이트 로직 격리 테스트.
이 파일은 이미 잘 검증된 하위 계층(중복방지·정규화·커서 전진 규칙 —
test_coupang_order_collection_service.py, test_order_multi_channel_
collection_service.py, test_order_collection_cursor_service.py)을
다시 검증하지 않는다. 여기서는 새로 추가된 오케스트레이션 계층
(app.domains.order.auto_collection_scheduler)의 5개 게이트·회사별
격리·연속실패 강등만 검증한다. Fake Provider + 명시적 now(Fake
Clock)만 사용 — 실제 쿠팡 API 호출 없음.

`is_restricted_mode_check`에는 항상 결정적 콜러블(`lambda: False`)을
주입한다 — 실제 `is_restricted_mode()`는 프로세스 전역 캐시를
읽으므로, 격리된 테스트 DB로 이 파일을 실행해도 다른 테스트가 남긴
전역 상태나 "아직 계산 안 됨"의 fail-closed(True) 기본값이 섞여
들어와 이 파일의 결과가 실행 순서에 따라 달라지는 결함을 만든다.

지시문이 요구한 12개 시나리오:
  1) 신규주문수집    -> test_new_order_is_collected_successfully
  2) 중복주문        -> test_duplicate_only_run_is_still_success
  3) 실행중중복스케줄 -> test_already_running_is_skipped_not_crashed
  4) 인증만료        -> test_expired_credential_is_skipped_not_counted_as_failure
  5) 호출제한        -> test_interval_not_elapsed_skips_without_calling_provider
  6) 부분실패        -> test_full_failure_increments_consecutive_failure_count
  7) 재시작복구      -> test_restart_resumes_from_last_successful_position
  8) 다중계정격리    -> test_one_connection_failure_does_not_count_as_company_failure
  9) 회사격리        -> test_companies_are_isolated_from_each_other
  10) EmergencyStop  -> test_emergency_stop_skips_entire_tick
  11) PAUSED·ERROR   -> test_paused_and_error_modes_are_skipped
  12) 알림실패        -> test_repeated_failure_demotes_without_raising_and_stops_next_tick
=========================================================
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.automation_safety.service import SafetyService
from app.domains.order.adapters.coupang_collection import CoupangOrderCollectionResult
from app.domains.order.adapters.coupang_collection import CoupangOrderPage
from app.domains.order.auto_collection_scheduler import CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD
from app.domains.order.auto_collection_scheduler import OrderCollectionTickOutcome
from app.domains.order.auto_collection_scheduler import get_or_create_auto_collection_state
from app.domains.order.auto_collection_scheduler import plan_manual_trigger
from app.domains.order.auto_collection_scheduler import run_order_collection_tick
from app.domains.order.auto_collection_scheduler import set_interval_minutes
from app.domains.order.auto_collection_scheduler import trigger_company_now
from app.domains.order.collection_model import OrderAutoCollectionState
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import OrderCollectionCursor
from app.domains.order.collection_model import OrderSkuResolution
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.collection_service import OrderCollectionCursorService
from app.domains.order.model import Order
from app.domains.platform_alert.model import PlatformAlertDeliveryLog
from app.domains.platform_alert.model import PlatformAlertRecipient
from app.domains.store_connection.model import StoreConnection
from tests.test_coupang_order_normalizer import order

T0 = datetime(2026, 9, 16, 9, 0, 0)

_NOT_RESTRICTED = lambda: False  # noqa: E731 - 테스트 전용 결정적 스텁


class _Provider:

    def __init__(self, result, on_collect=None):
        self.result = result
        self.on_collect = on_collect

    def collect(self, **kwargs):
        if self.on_collect is not None:
            self.on_collect(kwargs)
        return self.result


def _empty_success_result():
    return CoupangOrderCollectionResult(True, pages=(), http_status=200)


def _one_order_success_result():
    return CoupangOrderCollectionResult(
        True, pages=(CoupangOrderPage((order(),), None),), http_status=200,
    )


class OrderAutoCollectionSchedulerTestCase(unittest.TestCase):

    def setUp(self):

        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(
            self.engine,
            tables=[
                StoreConnection.__table__, OrderChannelFulfillment.__table__,
                UnresolvedOrderItem.__table__, OrderCollectionCursor.__table__,
                OrderSkuResolution.__table__, Order.__table__,
                OrderAutoCollectionState.__table__,
                FunctionAutomationState.__table__, EmergencyStop.__table__,
                PlatformAlertRecipient.__table__, PlatformAlertDeliveryLog.__table__,
            ],
        )
        self.db = sessionmaker(bind=self.engine)()
        self.store = InMemoryCredentialStore()
        self.safety = SafetyService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        os.remove(self.path)

    # ---------------- 헬퍼 ----------------

    def _make_connection(
        self, *, company_id=1, cred_name, status="CONNECTED",
        idem="create-1", expires_at=None,
    ):
        self.store.save(cred_name, {
            "vendor_id": cred_name, "access_key": "access", "secret_key": "secret",
        })
        connection = StoreConnection(
            company_id=company_id, marketplace_code="COUPANG",
            display_name="쿠팡", seller_identifier=cred_name,
            credential_reference=cred_name, masked_credential_hint="***",
            connection_status=status, credential_version=1, created_by=1,
            creation_idempotency_key=idem, creation_request_fingerprint="a" * 64,
            expires_at=expires_at,
        )
        self.db.add(connection)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def _set_automatic(self, company_id, mode=FunctionMode.AUTOMATIC):
        self.safety.set_function_mode(
            company_id, FunctionCode.ORDER_COLLECTION, mode,
            set_by=1, is_admin=True,
        )

    def _factory(self, result, on_collect=None):
        return lambda _credentials: _Provider(result, on_collect)

    def _tick(self, **kwargs):
        kwargs.setdefault("is_restricted_mode_check", _NOT_RESTRICTED)
        return run_order_collection_tick(self.db, self.store, **kwargs)

    def _trigger(self, company_id, **kwargs):
        kwargs.setdefault("is_restricted_mode_check", _NOT_RESTRICTED)
        return trigger_company_now(self.db, self.store, company_id, **kwargs)

    # ---------------- 0) ACCEPT 전용 범위(Phase 7A 사후 감사) ----------------

    def test_tick_only_queries_accept_status_not_all_six(self):
        """2026-09-17 Phase 7A 사후 감사 — 자동 tick은 신규 주문
        감지 목적상 ACCEPT 하나만 조회해야 한다. 예전에는 6개 상태를
        전부 조회해 계정당 tick마다 외부 GET 6회가 나갔다(실제 Live
        검증에서 승인 범위(1회)를 넘겨 6회가 나간 원인)."""

        self._make_connection(cred_name="cred-1")
        self._set_automatic(1)

        calls = []
        result = self._tick(
            now=T0,
            provider_factory=self._factory(
                _empty_success_result(), on_collect=lambda kw: calls.append(kw),
            ),
        )

        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.SUCCEEDED)
        self.assertEqual(len(calls), 1)  # 6번이 아니라 1번만 외부 호출
        self.assertEqual(calls[0]["status"], "ACCEPT")

    def test_manual_trigger_only_queries_accept_status_not_all_six(self):

        self._make_connection(cred_name="cred-1")

        calls = []
        entry = self._trigger(
            1, now=T0,
            provider_factory=self._factory(
                _empty_success_result(), on_collect=lambda kw: calls.append(kw),
            ),
        )

        self.assertEqual(entry.outcome, OrderCollectionTickOutcome.SUCCEEDED)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["status"], "ACCEPT")

    # ---------------- dry-run 실행계획(Phase 7A 사후 감사 요구사항 6) ----------------

    def test_plan_reports_accept_only_and_no_external_calls(self):
        """계획 함수 자체는 외부 호출·DB 쓰기를 전혀 하지 않는다 —
        Fake Provider조차 넘기지 않고도 계획이 나와야 한다."""

        self._make_connection(cred_name="cred-1")
        plan = plan_manual_trigger(self.db, 1, now=T0)

        self.assertEqual(plan.company_id, 1)
        self.assertEqual(plan.statuses, ("ACCEPT",))
        self.assertEqual(len(plan.connections), 1)
        self.assertEqual(plan.connections[0].status, "ACCEPT")
        self.assertEqual(plan.active_connection_count, 1)
        self.assertEqual(plan.min_external_get_calls, 1)
        # 2026-09-17 Phase 7A 사후 감사 2차(결함 1) — 최대치는
        # 페이지네이션 상한(DEFAULT_MAX_PAGES)까지 반영해야 하며 최소치와
        # 같아서는 안 된다(숨기지 않는다).
        self.assertEqual(plan.max_external_get_calls, plan.max_pages_per_connection)
        self.assertGreater(plan.max_external_get_calls, plan.min_external_get_calls)
        self.assertEqual(plan.retry_count, 0)
        self.assertFalse(plan.will_submit_purchase_order_or_payment)

    def test_plan_scales_with_number_of_connected_connections(self):

        self._make_connection(cred_name="cred-a", idem="idem-a")
        self._make_connection(cred_name="cred-b", idem="idem-b")
        plan = plan_manual_trigger(self.db, 1, now=T0)

        self.assertEqual(len(plan.connections), 2)
        self.assertEqual(plan.min_external_get_calls, 2)
        self.assertEqual(plan.max_external_get_calls, 2 * plan.max_pages_per_connection)

    def test_plan_matches_actual_trigger_call_count(self):
        """확인창(계획)의 최소 호출 수는 실제로 페이지네이션이 없는
        실행의 실제 호출 횟수와 정확히 일치해야 한다 — 설명과 실행이
        어긋나지 않는지의 핵심 검증. 최대 호출 수는 실제 호출 횟수보다
        작을 수 없다(페이지 상한까지의 이론적 상한이므로)."""

        self._make_connection(cred_name="cred-1")
        plan = plan_manual_trigger(self.db, 1, now=T0)

        calls = []
        self._trigger(
            1, now=T0,
            provider_factory=self._factory(
                _empty_success_result(), on_collect=lambda kw: calls.append(kw),
            ),
        )

        self.assertEqual(plan.min_external_get_calls, len(calls))
        self.assertGreaterEqual(plan.max_external_get_calls, len(calls))

    def test_plan_excludes_disconnected_connections(self):

        conn = self._make_connection(cred_name="cred-1")
        conn.connection_status = "ERROR"
        self.db.commit()

        plan = plan_manual_trigger(self.db, 1, now=T0)
        self.assertEqual(len(plan.connections), 0)
        self.assertEqual(plan.active_connection_count, 0)
        self.assertEqual(plan.min_external_get_calls, 0)
        self.assertEqual(plan.max_external_get_calls, 0)

    # ---------------- 1) 신규주문수집 ----------------

    def test_new_order_is_collected_successfully(self):

        self._make_connection(cred_name="cred-1")
        self._set_automatic(1)

        result = self._tick(
            now=T0, provider_factory=self._factory(_one_order_success_result()),
        )

        self.assertEqual(result.outcome, "RAN")
        self.assertEqual(len(result.entries), 1)
        entry = result.entries[0]
        self.assertEqual(entry.company_id, 1)
        self.assertEqual(entry.outcome, OrderCollectionTickOutcome.SUCCEEDED)
        self.assertEqual(self.db.query(Order).count(), 1)

        state = get_or_create_auto_collection_state(self.db, 1)
        self.assertIsNotNone(state.last_succeeded_at)
        self.assertEqual(state.consecutive_failure_count, 0)

    # ---------------- 2) 중복주문 ----------------

    def test_duplicate_only_run_is_still_success(self):

        self._make_connection(cred_name="cred-1")
        self._set_automatic(1)

        factory = self._factory(_one_order_success_result())
        self._tick(now=T0, provider_factory=factory)

        second_tick = T0 + timedelta(minutes=10)
        result = self._tick(now=second_tick, provider_factory=factory)

        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.SUCCEEDED)
        # 같은 주문이 두 번 들어와도 Order 행은 여전히 1개다(기존
        # 중복방지 로직 재사용 — 여기서 재검증하지 않고 결과만 확인).
        self.assertEqual(self.db.query(Order).count(), 1)

    # ---------------- 3) 실행중중복스케줄 ----------------

    def test_already_running_is_skipped_not_crashed(self):

        connection = self._make_connection(cred_name="cred-1")
        self._set_automatic(1)

        # 이미 RUNNING 상태로 잠가 둔다(예: 수동 "지금 확인"이 먼저
        # 실행 중) — 첫 channel_status(ALLOWED_STATUSES 중 하나)에
        # 대해서만 잠가도 run_all()의 그 connection 루프가 첫 항목에서
        # ConflictException을 던진다. `CoupangOrderCollectionService.
        # run()`이 내부에서 `acquire()`를 호출할 때 `now`를 넘기지
        # 않으므로(기존 코드, 건드리지 않음) 항상 실제 벽시계 시각을
        # 쓴다 — 그래서 여기서도 `now=T0`(가짜 과거 시각)를 주면
        # STALE_LOCK_AFTER(15분) 판정에 걸려 "오래된 잠금"으로 취급돼
        # 버린다. 실제 벽시계로 잠가야 충돌이 재현된다.
        from app.domains.order.adapters.coupang_collection import ALLOWED_STATUSES

        first_status = sorted(ALLOWED_STATUSES)[0]
        OrderCollectionCursorService(self.db).acquire(1, connection.id, first_status)

        result = self._tick(now=T0, provider_factory=self._factory(_empty_success_result()))

        self.assertEqual(
            result.entries[0].outcome, OrderCollectionTickOutcome.SKIPPED_ALREADY_RUNNING,
        )
        state = get_or_create_auto_collection_state(self.db, 1)
        self.assertEqual(state.consecutive_failure_count, 0)

    # ---------------- 4) 인증만료 ----------------

    def test_expired_credential_is_skipped_not_counted_as_failure(self):

        self._make_connection(
            cred_name="cred-1", expires_at=T0 - timedelta(days=1),
        )
        self._set_automatic(1)

        result = self._tick(now=T0, provider_factory=self._factory(_empty_success_result()))

        self.assertEqual(
            result.entries[0].outcome, OrderCollectionTickOutcome.SKIPPED_VALIDATION_BLOCKED,
        )
        state = get_or_create_auto_collection_state(self.db, 1)
        self.assertEqual(state.consecutive_failure_count, 0)

    # ---------------- 5) 호출제한(간격) ----------------

    def test_interval_not_elapsed_skips_without_calling_provider(self):

        self._make_connection(cred_name="cred-1")
        self._set_automatic(1)
        set_interval_minutes(self.db, 1, 5)

        calls = []
        factory = self._factory(_empty_success_result(), on_collect=lambda kw: calls.append(kw))

        self._tick(now=T0, provider_factory=factory)
        first_call_count = len(calls)
        self.assertGreater(first_call_count, 0)

        too_soon = T0 + timedelta(minutes=2)
        result = self._tick(now=too_soon, provider_factory=factory)

        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.NOT_DUE)
        self.assertEqual(len(calls), first_call_count)  # Provider가 다시 호출되지 않았다

    # ---------------- 6) 부분실패(전체실패 카운트) ----------------

    def test_full_failure_increments_consecutive_failure_count(self):

        self._make_connection(cred_name="cred-1")
        self._set_automatic(1)

        failing_result = CoupangOrderCollectionResult(
            False, pages=(), http_status=500, error_code="ORDER_PROVIDER_5XX",
        )
        result = self._tick(now=T0, provider_factory=self._factory(failing_result))

        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.FAILED)
        state = get_or_create_auto_collection_state(self.db, 1)
        self.assertEqual(state.consecutive_failure_count, 1)
        self.assertLess(state.consecutive_failure_count, CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD)
        # 아직 강등되지 않았다 — 함수 모드는 여전히 AUTOMATIC.
        self.assertEqual(
            self.safety.get_function_mode(1, FunctionCode.ORDER_COLLECTION),
            FunctionMode.AUTOMATIC,
        )

    # ---------------- 7) 재시작복구 ----------------

    def test_restart_resumes_from_last_successful_position(self):
        """`OrderCollectionCursorService.acquire()`(기존 코드, 건드리지
        않음)는 자신을 호출하는 `CoupangOrderCollectionService.run()`이
        `now`를 넘기지 않으므로 항상 실제 벽시계 시각을 기준으로
        조회 구간을 계산한다 — 그래서 이 테스트의 `now=T0`(스케줄러
        레벨의 Fake Clock, "이번 tick이 언제 실행됐다고 칠지"만
        결정)는 실제 조회 구간(created_at_from/to) 계산에는 반영되지
        않는다. 대신 "재시작 후 INITIAL_LOOKBACK(1시간)만큼 처음부터
        다시 훑지 않고, OVERLAP(5분)만큼만 겹쳐서 재개하는지"를 실제
        벽시계 기준으로 직접 검증한다."""

        self._make_connection(cred_name="cred-1")
        self._set_automatic(1)

        from datetime import timezone

        before_first = datetime.now(timezone.utc)
        self._tick(now=T0, provider_factory=self._factory(_empty_success_result()))

        windows = []
        second_now = T0 + timedelta(minutes=30)  # 스케줄러 레벨 간격판단용(NOT_DUE 방지)
        self._tick(
            now=second_now,
            provider_factory=self._factory(
                _empty_success_result(), on_collect=lambda kw: windows.append(kw),
            ),
        )

        self.assertTrue(windows)
        from_times = {kw["created_at_from"] for kw in windows}
        # INITIAL_LOOKBACK(1시간 전)이었다면 from_time이 test 시작
        # 시각보다 한참 전(최소 55분 전)이었을 것이다 — 실제로는 첫
        # tick이 끝난 지점 근처(OVERLAP 5분 이내)에서 재개해야 한다.
        for from_time in from_times:
            self.assertGreater(from_time, before_first - timedelta(minutes=10))

    # ---------------- 8) 다중계정격리(부분실패가 회사 전체 실패로 세지 않음) ----------------

    def test_one_connection_failure_does_not_count_as_company_failure(self):

        self._make_connection(cred_name="cred-ok", idem="idem-ok")
        self._make_connection(cred_name="cred-bad", idem="idem-bad")
        self._set_automatic(1)

        def factory(credentials):
            if credentials["vendor_id"] == "cred-bad":
                return _Provider(CoupangOrderCollectionResult(
                    False, pages=(), http_status=500, error_code="ORDER_PROVIDER_5XX",
                ))
            return _Provider(_empty_success_result())

        result = self._tick(now=T0, provider_factory=factory)

        self.assertEqual(result.entries[0].outcome, OrderCollectionTickOutcome.PARTIAL)
        state = get_or_create_auto_collection_state(self.db, 1)
        # 한 계정만 실패했고 다른 계정은 성공했다 — 회사 전체 연속
        # 실패 카운터는 올라가지 않는다(한 계정의 문제가 전체 자동화를
        # 끄지 않는다).
        self.assertEqual(state.consecutive_failure_count, 0)
        self.assertIsNotNone(state.last_succeeded_at)

    # ---------------- 9) 회사격리 ----------------

    def test_companies_are_isolated_from_each_other(self):

        self._make_connection(company_id=1, cred_name="cred-a")
        self._make_connection(company_id=2, cred_name="cred-b")
        self._set_automatic(2)  # 회사 1은 기본값(MANUAL)로 남겨 둔다

        calls = []
        factory = self._factory(_empty_success_result(), on_collect=lambda kw: calls.append(kw))
        result = self._tick(now=T0, provider_factory=factory)

        by_company = {e.company_id: e.outcome for e in result.entries}
        self.assertEqual(by_company[1], OrderCollectionTickOutcome.SKIPPED_NOT_AUTOMATIC)
        self.assertEqual(by_company[2], OrderCollectionTickOutcome.SUCCEEDED)
        self.assertTrue(calls)  # 회사 2에 대해서만 실제로 Provider가 호출됐다

    # ---------------- 10) EmergencyStop ----------------

    def test_emergency_stop_skips_entire_tick(self):

        self._make_connection(cred_name="cred-1")
        self._set_automatic(1)
        self.safety.activate_emergency_stop("테스트 긴급정지", set_by=1, is_admin=True)

        calls = []
        result = self._tick(
            now=T0,
            provider_factory=self._factory(_empty_success_result(), on_collect=lambda kw: calls.append(kw)),
        )

        self.assertEqual(result.outcome, OrderCollectionTickOutcome.SKIPPED_EMERGENCY_STOP)
        self.assertEqual(result.entries, ())
        self.assertEqual(calls, [])

    # ---------------- 11) PAUSED·ERROR ----------------

    def test_paused_and_error_modes_are_skipped(self):

        self._make_connection(company_id=1, cred_name="cred-a")
        self._make_connection(company_id=2, cred_name="cred-b")
        self._set_automatic(1, mode=FunctionMode.PAUSED)
        self.safety.demote_function_to_error(2, FunctionCode.ORDER_COLLECTION, reason="테스트")

        result = self._tick(now=T0, provider_factory=self._factory(_empty_success_result()))

        by_company = {e.company_id: e.outcome for e in result.entries}
        self.assertEqual(by_company[1], OrderCollectionTickOutcome.SKIPPED_NOT_AUTOMATIC)
        self.assertEqual(by_company[2], OrderCollectionTickOutcome.SKIPPED_NOT_AUTOMATIC)

    # ---------------- 12) 알림실패(알림 시도가 업무를 막지 않음) ----------------

    def test_repeated_failure_demotes_without_raising_and_stops_next_tick(self):

        self._make_connection(cred_name="cred-1")
        self._set_automatic(1)

        failing_result = CoupangOrderCollectionResult(
            False, pages=(), http_status=500, error_code="ORDER_PROVIDER_5XX",
        )
        factory = self._factory(failing_result)

        now = T0
        for _ in range(CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD):
            self._tick(now=now, provider_factory=factory)
            now += timedelta(minutes=10)

        # 알림 발송 시도(회사 관리자 + 서버 관리자) 도중 예외가 나도
        # 여기까지 삼켜져야 한다 — 이 assert 자체가 "예외 없이 여기
        # 도달했다"는 증거다.
        self.assertEqual(
            self.safety.get_function_mode(1, FunctionCode.ORDER_COLLECTION),
            FunctionMode.ERROR,
        )

        # 강등 이후 다음 tick은 더 이상 시도하지 않는다(ERROR는
        # AUTOMATIC이 아니므로).
        next_result = self._tick(now=now, provider_factory=factory)
        self.assertEqual(
            next_result.entries[0].outcome, OrderCollectionTickOutcome.SKIPPED_NOT_AUTOMATIC,
        )

    # ---------------- 추가: 수동 "지금 확인"(Phase 7 선행 계약 검증) ----------------

    def test_manual_trigger_ignores_not_due_but_respects_emergency_stop(self):

        self._make_connection(cred_name="cred-1")
        # 함수 모드를 켜지 않아도(기본 MANUAL) 수동 트리거는 동작한다.
        entry = self._trigger(1, now=T0, provider_factory=self._factory(_empty_success_result()))
        self.assertEqual(entry.outcome, OrderCollectionTickOutcome.SUCCEEDED)

        self.safety.activate_emergency_stop("테스트", set_by=1, is_admin=True)
        blocked = self._trigger(
            1, now=T0 + timedelta(minutes=1),
            provider_factory=self._factory(_empty_success_result()),
        )
        self.assertEqual(blocked.outcome, OrderCollectionTickOutcome.SKIPPED_EMERGENCY_STOP)


if __name__ == "__main__":
    unittest.main()
