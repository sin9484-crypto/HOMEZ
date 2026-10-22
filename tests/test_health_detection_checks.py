"""
=========================================================
Homez OS

File : tests/test_health_detection_checks.py

2026-10-21 — 반영 A(모니터링 점검 결과 반영): 시스템 점검(T1)에 **감시가 조용히 멈추는 경우**를 더했다. 79시간 동안 주문 자동 감지가 승인 센터 허용 만료로 멈춰 있었는데
앱 화면은 "일이 없다"와 "감시를 못 한다"를 구분하지 못했고, 매번 사람이 점검 스크립트를 돌려서야 알았다.

  - 규칙(`health_summary.py`, 순수): 운영 화면이 이미 쓰는 `describe_health` 코드를 그대로 옮긴다(허용 없음·오류·일시 중지·실패·연결 정보·환경 제한·꺼 둠·정상). 정상으로 보여도 마지막 시도가
    30분을 넘기면 정상이라 믿지 않는다. 모르는 코드·읽지 못한 값은 unknown(정상 아님).
  - 읽기 허용(주문·배송 / 반품·민원·정산 / 상품 상태): 지금 쓸 수 있으면 통과, 전에 허용이 있었는데 못 쓰면 WARN, 한 번도 없으면 INFO.
  - 수집(`health_service.py`): **읽기만 한다** — 만료된 허용을 EXPIRED로 바꾸거나 허용 요청을 만들거나 호출 기록을 남기지 않는다(`authorize`를 쓰지 않는다).
  - T2 설명 코드와 쉬운 보기 문구가 새 코드를 모두 가진다.
=========================================================
"""

import os
import re
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.model import FunctionAutomationState
from app.domains.diagnostics import health_ai_service as ai
from app.domains.diagnostics import health_summary as hs
from app.domains.diagnostics.health_service import HealthService
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.order.collection_model import OrderAutoCollectionState

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 21, 12, 0, 0)
WATCHED = ["ORDER_TRACKING", "AFTER_SALES_CHECK", "PRODUCT_STATUS_CHECK"]
SERVICE_SRC = (ROOT / "app/domains/diagnostics/health_service.py").read_text(encoding="utf-8")


def snap(**overrides):
    base = {
        "now": NOW, "scheduler_enabled": False, "latest_backup_at": NOW - timedelta(days=1), "rehearsal_known": True,
        "latest_rehearsal": {"at": NOW - timedelta(days=1), "ok": True}, "disk_free_bytes": 50 * 1024 ** 3, "estop_active": False,
        "detection": {"health_code": "OK", "minutes_since_attempt": 3}, "reads": {"lapsed": [], "never": []},
    }
    base.update(overrides)
    return base


def check_of(result, code):
    found = [c for c in result["checks"] if c["code"] == code]
    return found[0] if found else None


class DetectionRuleTests(unittest.TestCase):

    def test_each_describe_health_code_maps_to_a_level(self):
        expected = {
            "NEEDS_ALLOWANCE": ("DETECTION_NEEDS_ALLOWANCE", hs.WARN), "ERROR": ("DETECTION_ERROR", hs.FAIL), "PAUSED": ("DETECTION_PAUSED", hs.WARN),
            "FAILING": ("DETECTION_FAILING", hs.WARN), "BLOCKED": ("DETECTION_BLOCKED", hs.WARN), "ENV_BLOCKED": ("DETECTION_ENV_BLOCKED", hs.WARN),
            "OFF": ("DETECTION_OFF", hs.INFO),
        }
        self.assertEqual(hs.DETECTION_HEALTH_CODES, expected)
        for health_code, (code, level) in expected.items():
            result = hs.evaluate(snap(detection={"health_code": health_code, "minutes_since_attempt": 12}))
            found = check_of(result, code)
            self.assertIsNotNone(found, health_code)
            self.assertEqual(found["level"], level, health_code)
            self.assertEqual(found["params"], {"minutes": 12})
            self.assertNotIn("DETECTION", result["passed"])
            self.assertNotIn("DETECTION", result["unknown"])

    def test_the_stopped_by_missing_allowance_case_is_a_warning(self):
        # 2026-10-21 실제 사례: 마지막 시도 4,720분 전, 허용 없음
        result = hs.evaluate(snap(detection={"health_code": "NEEDS_ALLOWANCE", "minutes_since_attempt": 4720}))
        self.assertEqual(result["overall"], hs.WARN)
        self.assertEqual(check_of(result, "DETECTION_NEEDS_ALLOWANCE")["params"]["minutes"], 4720)

    def test_error_mode_makes_the_overall_result_fail(self):
        self.assertEqual(hs.evaluate(snap(detection={"health_code": "ERROR", "minutes_since_attempt": 1}))["overall"], hs.FAIL)

    def test_off_is_info_only(self):
        result = hs.evaluate(snap(detection={"health_code": "OFF", "minutes_since_attempt": None}))
        self.assertEqual(result["overall"], hs.INFO)
        self.assertEqual(check_of(result, "DETECTION_OFF")["params"], {})  # 값이 없으면 숫자를 지어내지 않는다

    def test_ok_passes_but_a_long_silence_is_not_trusted(self):
        self.assertEqual(hs.DETECTION_STALE_MINUTES, 30)
        for minutes in (0, 5, 30):
            result = hs.evaluate(snap(detection={"health_code": "OK", "minutes_since_attempt": minutes}))
            self.assertIn("DETECTION", result["passed"], minutes)
            self.assertIsNone(check_of(result, "DETECTION_STALE"), minutes)
        for minutes in (31, 60, 4720):
            result = hs.evaluate(snap(detection={"health_code": "OK", "minutes_since_attempt": minutes}))
            found = check_of(result, "DETECTION_STALE")
            self.assertIsNotNone(found, minutes)
            self.assertEqual((found["level"], found["params"]), (hs.WARN, {"minutes": minutes}))
            self.assertNotIn("DETECTION", result["passed"])

    def test_ok_without_a_known_attempt_time_passes_and_waiting_passes(self):
        for health_code, minutes in (("OK", None), ("WAITING", None), ("WAITING", 999)):
            result = hs.evaluate(snap(detection={"health_code": health_code, "minutes_since_attempt": minutes}))
            self.assertIn("DETECTION", result["passed"], (health_code, minutes))

    def test_unknown_or_missing_state_is_unknown_not_healthy(self):
        for value in (None, {}, {"health_code": "SOMETHING_NEW"}, {"health_code": None}, "OK", []):
            result = hs.evaluate(snap(detection=value))
            self.assertIn("DETECTION", result["unknown"], repr(value))
            self.assertNotIn("DETECTION", result["passed"], repr(value))
        without_key = snap()
        del without_key["detection"]
        self.assertIn("DETECTION", hs.evaluate(without_key)["unknown"])

    def test_minutes_that_are_not_ints_are_not_reported(self):
        for bad in ("12", 1.5, True, None):
            result = hs.evaluate(snap(detection={"health_code": "FAILING", "minutes_since_attempt": bad}))
            self.assertEqual(check_of(result, "DETECTION_FAILING")["params"], {}, repr(bad))


class ReadsRuleTests(unittest.TestCase):

    def test_lapsed_is_a_warning_never_is_info_and_both_can_show(self):
        result = hs.evaluate(snap(reads={"lapsed": ["AFTER_SALES_CHECK"], "never": ["PRODUCT_STATUS_CHECK"]}))
        lapsed, never = check_of(result, "READS_ALLOWANCE_LAPSED"), check_of(result, "READS_NOT_ALLOWED")
        self.assertEqual((lapsed["level"], lapsed["params"]), (hs.WARN, {"purposes": ["AFTER_SALES_CHECK"]}))
        self.assertEqual((never["level"], never["params"]), (hs.INFO, {"purposes": ["PRODUCT_STATUS_CHECK"]}))
        self.assertEqual(result["overall"], hs.WARN)
        self.assertNotIn("READS", result["passed"])

    def test_nothing_lapsed_or_never_passes(self):
        result = hs.evaluate(snap())
        self.assertIn("READS", result["passed"])
        self.assertIsNone(check_of(result, "READS_ALLOWANCE_LAPSED"))
        self.assertIsNone(check_of(result, "READS_NOT_ALLOWED"))

    def test_unreadable_reads_are_unknown(self):
        for value in (None, "x", []):
            result = hs.evaluate(snap(reads=value))
            self.assertIn("READS", result["unknown"], repr(value))
            self.assertNotIn("READS", result["passed"], repr(value))

    def test_only_never_makes_the_overall_info(self):
        result = hs.evaluate(snap(reads={"lapsed": [], "never": list(WATCHED)}))
        self.assertEqual(result["overall"], hs.INFO)


class ServiceTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=self.engine, tables=[
            OrderAutoCollectionState.__table__, ExternalCallAllowance.__table__, ExternalCallLog.__table__, FunctionAutomationState.__table__,
        ])
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def service(self, db=None):
        return HealthService(db or self.db, scheduler_getter=lambda: None, scheduler_enabled=False, disk_path=Path(self.db_path), expected_jobs=[], now_factory=lambda: NOW)

    def set_mode(self, mode):
        self.db.add(FunctionAutomationState(company_id=1, function_code=FunctionCode.ORDER_COLLECTION, mode=mode, set_by=1))
        self.db.commit()

    def add_state(self, minutes_ago, last_status, *, succeeded=True, failures=0):
        at = NOW - timedelta(minutes=minutes_ago)
        self.db.add(OrderAutoCollectionState(
            company_id=1, interval_minutes=5, last_attempted_at=at, last_succeeded_at=at if succeeded else None, last_status=last_status,
            consecutive_failure_count=failures,
        ))
        self.db.commit()

    def allow(self, purpose, *, status=AllowanceStatus.ACTIVE, mode=AllowanceMode.STANDING, expires_in_hours=24, remaining=None, kind=CallKind.READ, system=ExternalSystem.COUPANG):
        row = ExternalCallAllowance(
            company_id=1, system=system, kind=kind, purpose=purpose, mode=mode, status=status, per_hour_limit=100, remaining_count=remaining,
            expires_at=None if expires_in_hours is None else NOW + timedelta(hours=expires_in_hours),
        )
        self.db.add(row)
        self.db.commit()
        return row.id

    def summary(self):
        return self.service().summary()

    # ---- 감지 ----

    def test_stopped_for_lack_of_allowance_is_reported_with_the_elapsed_minutes(self):
        from app.domains.order.auto_collection_scheduler import OrderCollectionTickOutcome

        self.set_mode(FunctionMode.AUTOMATIC)
        self.add_state(4720, OrderCollectionTickOutcome.SKIPPED_NO_ALLOWANCE, succeeded=False)
        result = self.summary()
        found = check_of(result, "DETECTION_NEEDS_ALLOWANCE")
        self.assertIsNotNone(found)
        self.assertEqual((found["level"], found["params"]["minutes"]), (hs.WARN, 4720))
        self.assertEqual(result["overall"], hs.WARN)

    def test_a_healthy_recent_run_passes_and_an_old_one_is_stale(self):
        from app.domains.order.auto_collection_scheduler import OrderCollectionTickOutcome

        self.set_mode(FunctionMode.AUTOMATIC)
        self.add_state(3, OrderCollectionTickOutcome.SUCCEEDED if hasattr(OrderCollectionTickOutcome, "SUCCEEDED") else "SUCCEEDED")
        self.assertIn("DETECTION", self.summary()["passed"])
        row = self.db.query(OrderAutoCollectionState).one()
        row.last_attempted_at = NOW - timedelta(minutes=45)
        self.db.commit()
        result = self.summary()
        self.assertEqual(check_of(result, "DETECTION_STALE")["params"]["minutes"], 45)

    def test_a_manual_function_mode_is_info_not_a_warning(self):
        self.add_state(10, "SUCCEEDED")  # 기능 모드 기록 없음 = 안전한 기본값(수동)
        result = self.summary()
        found = check_of(result, "DETECTION_OFF")
        self.assertIsNotNone(found)
        self.assertEqual(found["level"], hs.INFO)

    def test_no_state_row_or_missing_tables_are_unknown(self):
        self.assertIn("DETECTION", self.summary()["unknown"])
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}")
        try:
            db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
            result = self.service(db).summary()
            self.assertIn("DETECTION", result["unknown"])
            self.assertIn("READS", result["unknown"])
            self.assertNotIn("DETECTION", result["passed"])
            db.close()
        finally:
            engine.dispose()
            os.remove(path)

    # ---- 읽기 허용 ----

    def test_never_granted_purposes_are_info(self):
        result = self.summary()
        found = check_of(result, "READS_NOT_ALLOWED")
        self.assertEqual(found["params"]["purposes"], WATCHED)
        self.assertIsNone(check_of(result, "READS_ALLOWANCE_LAPSED"))

    def test_active_unexpired_allowance_counts_and_the_others_stay_never(self):
        self.allow("ORDER_TRACKING")
        result = self.summary()
        self.assertEqual(check_of(result, "READS_NOT_ALLOWED")["params"]["purposes"], ["AFTER_SALES_CHECK", "PRODUCT_STATUS_CHECK"])

    def test_an_any_purpose_read_allowance_covers_all_three(self):
        self.allow("ANY")
        result = self.summary()
        self.assertIn("READS", result["passed"])
        self.assertIsNone(check_of(result, "READS_NOT_ALLOWED"))

    def test_ended_allowances_are_lapsed_not_never(self):
        for status in (AllowanceStatus.EXPIRED, AllowanceStatus.REVOKED, AllowanceStatus.EXHAUSTED):
            self.db.query(ExternalCallAllowance).delete()
            self.db.commit()
            self.allow("AFTER_SALES_CHECK", status=status)
            result = self.summary()
            self.assertEqual(check_of(result, "READS_ALLOWANCE_LAPSED")["params"]["purposes"], ["AFTER_SALES_CHECK"], status)
            self.assertEqual(check_of(result, "READS_NOT_ALLOWED")["params"]["purposes"], ["ORDER_TRACKING", "PRODUCT_STATUS_CHECK"], status)

    def test_active_status_but_past_expiry_is_lapsed_and_boundary_counts_as_ended(self):
        for hours in (-1, 0):
            self.db.query(ExternalCallAllowance).delete()
            self.db.commit()
            self.allow("AFTER_SALES_CHECK", expires_in_hours=hours)
            result = self.summary()
            self.assertEqual(check_of(result, "READS_ALLOWANCE_LAPSED")["params"]["purposes"], ["AFTER_SALES_CHECK"], hours)
        self.db.query(ExternalCallAllowance).delete()
        self.db.commit()
        self.allow("AFTER_SALES_CHECK", expires_in_hours=None)  # 기한 없음은 쓸 수 있다
        self.assertNotIn("AFTER_SALES_CHECK", (check_of(self.summary(), "READS_ALLOWANCE_LAPSED") or {"params": {"purposes": []}})["params"]["purposes"])

    def test_counted_allowance_needs_remaining_uses(self):
        self.allow("AFTER_SALES_CHECK", mode=AllowanceMode.COUNTED, remaining=0)
        self.assertEqual(check_of(self.summary(), "READS_ALLOWANCE_LAPSED")["params"]["purposes"], ["AFTER_SALES_CHECK"])
        self.db.query(ExternalCallAllowance).delete()
        self.db.commit()
        self.allow("AFTER_SALES_CHECK", mode=AllowanceMode.COUNTED, remaining=3)
        result = self.summary()
        self.assertIsNone(check_of(result, "READS_ALLOWANCE_LAPSED"))
        self.assertNotIn("AFTER_SALES_CHECK", check_of(result, "READS_NOT_ALLOWED")["params"]["purposes"])

    def test_pending_and_rejected_requests_are_not_grants(self):
        for status in (AllowanceStatus.PENDING, AllowanceStatus.REJECTED):
            self.db.query(ExternalCallAllowance).delete()
            self.db.commit()
            self.allow("AFTER_SALES_CHECK", status=status)
            result = self.summary()
            self.assertIsNone(check_of(result, "READS_ALLOWANCE_LAPSED"), status)
            self.assertIn("AFTER_SALES_CHECK", check_of(result, "READS_NOT_ALLOWED")["params"]["purposes"], status)

    def test_write_kind_or_other_system_allowances_do_not_count(self):
        self.allow("AFTER_SALES_CHECK", kind=CallKind.WRITE)
        self.allow("AFTER_SALES_CHECK", system=ExternalSystem.ONCH)
        self.assertIn("AFTER_SALES_CHECK", check_of(self.summary(), "READS_NOT_ALLOWED")["params"]["purposes"])

    def test_other_purposes_do_not_count_as_a_read_allowance(self):
        self.allow("ORDER_DETECTION")
        self.allow("AI_DRAFT")
        self.assertEqual(check_of(self.summary(), "READS_NOT_ALLOWED")["params"]["purposes"], WATCHED)

    # ---- 읽기 전용 ----

    def test_collecting_the_snapshot_changes_nothing(self):
        expired_id = self.allow("AFTER_SALES_CHECK", expires_in_hours=-5)
        self.allow("ORDER_TRACKING", status=AllowanceStatus.PENDING)
        before = (self.db.query(func.count(ExternalCallAllowance.id)).scalar(), self.db.query(func.count(ExternalCallLog.id)).scalar())
        self.summary()
        self.summary()
        self.db.expire_all()
        after = (self.db.query(func.count(ExternalCallAllowance.id)).scalar(), self.db.query(func.count(ExternalCallLog.id)).scalar())
        self.assertEqual(before, after)  # 허용 요청·호출 기록이 새로 생기지 않았다
        self.assertEqual(self.db.get(ExternalCallAllowance, expired_id).status, AllowanceStatus.ACTIVE)  # 만료 표시도 하지 않았다

    def test_the_readers_do_not_write_or_authorize(self):
        start = SERVICE_SRC.index("def _detection")
        end = SERVICE_SRC.index("def snapshot")
        body = SERVICE_SRC[start:end]
        for forbidden in (r"\.add\(", r"\.commit\(", r"\.execute\(", r"\bupdate\(", r"\bdelete\(", "authorize", "ensure_pending_request", r"\.grant\(", "set_function_mode", "describe_health_write"):
            self.assertNotRegex(body, forbidden)


class ExplanationAndTextTests(unittest.TestCase):

    NEW_CODES = ("DETECTION_NEEDS_ALLOWANCE", "DETECTION_ERROR", "DETECTION_PAUSED", "DETECTION_FAILING", "DETECTION_BLOCKED", "DETECTION_ENV_BLOCKED", "DETECTION_STALE", "READS_ALLOWANCE_LAPSED")

    def test_every_actionable_new_code_has_an_ai_meaning(self):
        for code in self.NEW_CODES:
            self.assertIn(code, ai.CODE_MEANING)
            self.assertTrue(ai.CODE_MEANING[code].strip())
        self.assertNotIn("DETECTION_OFF", ai.CODE_MEANING)       # INFO는 AI에게 묻지 않는다
        self.assertNotIn("READS_NOT_ALLOWED", ai.CODE_MEANING)

    def test_ai_facts_only_carry_allow_listed_values(self):
        checks = [
            {"code": "DETECTION_NEEDS_ALLOWANCE", "level": "WARN", "params": {"minutes": 4720, "secret": "x"}},
            {"code": "READS_ALLOWANCE_LAPSED", "level": "WARN", "params": {"purposes": ["AFTER_SALES_CHECK", "ORDER_DETECTION", "<b>x</b>"]}},
        ]
        prompt = ai.build_user_prompt(checks)
        self.assertIn('"minutes_since_last_attempt": 4720', prompt)
        self.assertIn("반품·문의·정산", prompt)
        self.assertNotIn("ORDER_DETECTION", prompt)  # 허용 목록 밖 목적 코드는 보내지 않는다
        self.assertNotIn("secret", prompt)
        self.assertNotIn("<b>", prompt)

    def test_the_actionable_codes_come_out_of_t1(self):
        result = hs.evaluate(snap(detection={"health_code": "NEEDS_ALLOWANCE", "minutes_since_attempt": 9}, reads={"lapsed": ["AFTER_SALES_CHECK"], "never": []}))
        self.assertEqual([c["code"] for c in ai.actionable_checks(result)], ["DETECTION_NEEDS_ALLOWANCE", "READS_ALLOWANCE_LAPSED"])

    def test_easy_view_has_text_for_every_new_actionable_code_in_both_languages(self):
        view = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
        texts = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
        keys = dict(re.findall(r"([A-Z_]+): \"(easy\.brief\.sys_[a-z_]+)\"", view))
        for code in self.NEW_CODES:
            self.assertIn(code, keys)
            self.assertEqual(texts.count(f'"{keys[code]}":'), 2, code)  # 한국어·영어 한 번씩
        for key in ("easy.brief.reads_order_tracking", "easy.brief.reads_after_sales_check", "easy.brief.reads_product_status_check"):
            self.assertEqual(texts.count(f'"{key}":'), 2, key)

    def test_placeholders_match_between_languages_and_are_not_repeated(self):
        texts = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
        for key in ("sys_det_allowance", "sys_det_stale", "sys_reads_lapsed"):
            values = re.findall(rf'"easy\.brief\.{key}": "([^"]*)"', texts)
            self.assertEqual(len(values), 2, key)
            ko, en = (sorted(re.findall(r"\{(\w+)\}", v)) for v in values)
            self.assertEqual(ko, en, key)
            self.assertEqual(len(ko), len(set(ko)), key)  # 같은 자리표시자를 두 번 쓰지 않는다(치환기가 첫 번째만 바꾼다)

    def test_the_briefing_text_function_never_prints_undefined(self):
        view = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
        start = view.index("function briefSysText")
        body = view[start:view.index("function briefingHtml")]
        self.assertIn("val(p.minutes)", body)
        self.assertIn('"…"', body)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (DetectionRuleTests, ReadsRuleTests, ServiceTests, ExplanationAndTextTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == "__main__":
    unittest.main()
