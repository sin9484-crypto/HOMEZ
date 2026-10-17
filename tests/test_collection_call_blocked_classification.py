"""
=========================================================
Homez OS

File : tests/test_collection_call_blocked_classification.py

2026-10-17 — 연동 안정화 A1: 실행 환경(개발용 범위 한정 가드)이 호출을 막은 것을 앱·쿠팡 장애로 세지 않는다.

사고(2026-10-17 13:44): 가드 규칙이 만료돼 가드가 `RuntimeError("범위 한정 가드: …")`로 쿠팡 요청을 막았는데, 분류가 `PermissionError`만 알아봐서
`ORDER_PROVIDER_INTERNAL_ERROR`로 세어졌고 3회 연속 실패로 "신규 주문 자동 감지"가 자동 중지됐다(사용자가 수동 재개해야 했다).

고정하는 규칙:
- 막힘 표지로 시작하는 `RuntimeError`는 `ORDER_PROVIDER_CALL_BLOCKED`다. 다른 `RuntimeError`는 여전히 내부 오류다.
- 막힘은 연속 실패로 세지 않아, 몇 번을 반복해도 기능이 ERROR로 내려가지 않는다.
- 진짜 내부 오류는 기존처럼 3회 연속이면 자동 중지된다(막힘 처리가 실패 감시를 약하게 만들지 않는다).
=========================================================
"""

from datetime import timedelta

from app.domains.order.auto_collection_scheduler import CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD
from app.domains.order.coupang_collection_service import CALL_BLOCK_MARKERS
from app.domains.order.coupang_collection_service import classify_provider_exception
from app.domains.order.collection_model import OrderAutoCollectionState
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from tests.test_order_auto_collection_scheduler import T0
from tests.test_order_detection_health import Base_

GUARD_BLOCK = RuntimeError("범위 한정 가드: 허용되지 않은 요청 api-gateway.coupang.com/…")


class ClassificationTests(Base_):

    def test_a_guard_block_runtime_error_is_a_blocked_call(self):
        self.assertEqual(classify_provider_exception(GUARD_BLOCK), "ORDER_PROVIDER_CALL_BLOCKED")
        for marker in CALL_BLOCK_MARKERS:
            self.assertEqual(classify_provider_exception(RuntimeError(f"{marker}: 예산 소진")), "ORDER_PROVIDER_CALL_BLOCKED")

    def test_other_runtime_errors_stay_internal_errors(self):
        self.assertEqual(classify_provider_exception(RuntimeError("something broke")), "ORDER_PROVIDER_INTERNAL_ERROR")
        self.assertEqual(classify_provider_exception(RuntimeError("가드 아님: 범위 한정 가드")), "ORDER_PROVIDER_INTERNAL_ERROR")
        self.assertEqual(classify_provider_exception(ValueError("범위 한정 가드: x")), "ORDER_PROVIDER_INTERNAL_ERROR")

    def test_the_existing_classes_are_unchanged(self):
        self.assertEqual(classify_provider_exception(PermissionError("x")), "ORDER_PROVIDER_CALL_BLOCKED")


class DemotionTests(Base_):

    def _ticks(self, factory, count):
        for i in range(count):
            self.tick(factory, now=T0 + timedelta(minutes=5 * i))

    def test_repeated_guard_blocks_never_demote_the_function(self):
        self.automatic()
        self._ticks(self.raising_factory(GUARD_BLOCK), CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD + 3)
        state = self.db.query(OrderAutoCollectionState).one()
        self.assertEqual(state.consecutive_failure_count, 0)
        self.assertEqual(self.safety.get_function_mode(1, FunctionCode.ORDER_COLLECTION), FunctionMode.AUTOMATIC)
        self.assertEqual(state.last_error_summary, "ORDER_PROVIDER_CALL_BLOCKED")

    def test_real_internal_errors_still_demote_after_the_threshold(self):
        self.automatic()
        self._ticks(self.raising_factory(RuntimeError("boom")), CONSECUTIVE_FAILURE_DEMOTE_THRESHOLD)
        self.assertEqual(self.safety.get_function_mode(1, FunctionCode.ORDER_COLLECTION), FunctionMode.ERROR)

    def test_collection_resumes_normally_after_the_block_clears(self):
        self.automatic()
        self._ticks(self.raising_factory(GUARD_BLOCK), 2)
        self.tick(self.ok_factory(), now=T0 + timedelta(minutes=20))
        state = self.db.query(OrderAutoCollectionState).one()
        self.assertEqual(state.consecutive_failure_count, 0)
        self.assertEqual(state.last_status, "SUCCEEDED")


if __name__ == "__main__":
    import unittest

    unittest.main()
