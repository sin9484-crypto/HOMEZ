"""
=========================================================
Homez OS

File : tests/test_health_ai_service.py

2026-10-18 — 서버 관리 AI T2: 운영 상태 이상을 AI가 쉬운 말로 설명(`health_ai_service.py`)을 고정한다(가짜 AI, 바깥 호출 0).

  - 설명할 이상(FAIL/WARN)이 없으면 AI를 호출하지 않는다. 요청에는 아무 글도 받지 않고 점검 결과는 서버가 모은다.
  - AI에는 허용 목록(점검 코드·수준·고정 뜻·수치·알려진 작업 번호)만 간다 — 로그·경로·설정·개인정보 없음. 한 번에 최대 4개(심각한 순).
  - 호출은 월 한도·승인 센터 허용(`AI_ADVICE`)을 거친다. 같은 이상 조합(코드+수준)은 수치가 바뀌어도 한 번만 묻고 회사별로 저장해 다시 보여 준다.
  - 응답 검증(항목별): 알려진 코드만·코드당 1개·숫자/HTML/주소/약속/삭제 조치 금지·길이 제한. 통과한 항목이 없으면 전체를 버린다(비용은 기록).
=========================================================
"""

import json
import threading
import time
import unittest
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.domains.diagnostics import health_ai_service as svc
from app.domains.diagnostics import health_summary as hs
from app.domains.diagnostics.health_ai_service import EVENT_ACTION
from app.domains.diagnostics.health_ai_service import HealthAiService
from app.domains.diagnostics.health_ai_service import validate_advice
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.service import ExternalCallNotAllowed
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from tests.test_url_analysis_service import KEY
from tests.test_url_analysis_service import DbBase
from tests.test_url_analysis_service import FakeClient

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/diagnostics/health_ai_service.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "app/domains/diagnostics/router.py").read_text(encoding="utf-8")

BACKUP = {"code": "BACKUP_STALE", "level": "WARN", "params": {"days": 9}}
DISK = {"code": "DISK_LOW", "level": "WARN", "params": {"free_mb": 512}}
SCHED = {"code": "SCHEDULER_NOT_RUNNING", "level": "FAIL", "params": {}}
INFO = {"code": "REHEARSAL_NONE", "level": "INFO", "params": {}}
GOOD = {
    "summary": "백업이 오래됐고 저장 공간이 부족해지고 있어요.",
    "items": [
        {"code": "BACKUP_STALE", "cause": "한동안 백업을 만들지 않았어요.", "steps": ["앱에서 백업을 새로 만들어 두세요."]},
        {"code": "DISK_LOW", "cause": "저장 공간이 부족해지고 있어요.", "steps": ["쓰지 않는 큰 파일을 다른 곳으로 옮겨 공간을 확보하세요.", "공간을 확보한 뒤 상태를 다시 확인하세요."]},
    ],
}


class FakeHealth:
    def __init__(self, *checks):
        self.checks = list(checks)
        self.calls = 0

    def summary(self):
        self.calls += 1
        return {"overall": "WARN", "checks": list(self.checks), "passed": [], "unknown": [], "checked_at": "2026-10-18T12:00:00"}


class ExplainBase(DbBase):

    def setUp(self):
        super().setUp()
        self.client = FakeClient(GOOD)
        self.ai = AiDraftService(self.db, self.store, client_factory=lambda k, m: self.client)
        self.ai.save_settings(self.user_id, self.company_id, model="claude-test", monthly_limit_krw=5000, in_price=4000.0, out_price=20000.0, api_key=KEY)
        self.health = FakeHealth(SCHED, BACKUP, DISK, INFO)
        self.service = HealthAiService(self.db, self.ai, health=self.health)

    def grant(self, count=3):
        ExternalCallPolicyService(self.db).grant(
            self.company_id, system=ExternalSystem.LLM, kind=CallKind.WRITE, purpose=CallPurpose.AI_ADVICE,
            mode=AllowanceMode.COUNTED, count=count, user_id=self.user_id)

    def explain(self):
        return self.service.explain(self.company_id, self.user_id)

    def stored_rows(self):
        return [json.loads(r[0]) for r in self.db.execute(text("SELECT description FROM audit_logs WHERE action = :a ORDER BY id"), {"a": EVENT_ACTION}).fetchall()]


class ExplainTests(ExplainBase):

    def test_nothing_to_explain_means_no_call(self):
        self.grant()
        for checks in ([], [INFO], [{"code": "SCHEDULER_DISABLED", "level": "INFO", "params": {}}], [{"code": "NEW_FAIL", "level": "FAIL", "params": {}}]):
            self.service = HealthAiService(self.db, self.ai, health=FakeHealth(*checks))
            with self.assertRaises(BadRequestException, msg=str(checks)):
                self.explain()
        self.assertEqual(self.client.calls, [])

    def test_the_advice_is_returned_stored_per_item_and_priced(self):
        self.grant()
        out = self.explain()
        self.assertEqual([i["code"] for i in out["advice"]["items"]], ["BACKUP_STALE", "DISK_LOW"])
        self.assertEqual(out["advice"]["summary"], GOOD["summary"])
        self.assertEqual(out["codes"], ["SCHEDULER_NOT_RUNNING", "BACKUP_STALE", "DISK_LOW"])
        self.assertFalse(out["cached"])
        self.assertAlmostEqual(out["usage"]["cost_krw"], 20.0, places=2)
        usage = self.db.execute(text("SELECT entity_id, description FROM audit_logs WHERE action = 'AI_DRAFT_USAGE'")).fetchall()
        self.assertEqual(len(usage), 1)
        self.assertTrue(usage[0][0].endswith(":SYS"))
        self.assertEqual(json.loads(usage[0][1])["p"], "ADVICE")
        rows = self.stored_rows()
        self.assertEqual(len(rows), 3)  # 요약 1줄 + 항목 2줄
        self.assertEqual(len({r["b"] for r in rows}), 1)

    def test_only_allowed_facts_are_sent_and_the_data_is_fenced(self):
        self.grant()
        self.health.checks[1] = {"code": "JOBS_OVERDUE", "level": "WARN", "params": {"jobs": ["product_status_check", "C:\\Users\\비밀\\x.db"], "path": "C:\\secret", "token": "abc"}}
        self.explain()
        system, user, kw = self.client.calls[0]
        self.assertTrue(user.startswith("<health_data>") and "</health_data>" in user)
        data = json.loads(user.split("<health_data>")[1].split("</health_data>")[0])
        self.assertEqual(set(data), {"checks"})
        for item in data["checks"]:
            self.assertEqual(set(item), {"code", "level", "meaning", "facts"})
            self.assertLessEqual(set(item["facts"]), {"days_since", "free_mb", "jobs"})
        for word in ("C:\\", "비밀", "secret", "token", "abc", KEY, "company", "phone"):
            self.assertNotIn(word, user)
        overdue = next(i for i in data["checks"] if i["code"] == "JOBS_OVERDUE")
        self.assertNotIn("C:\\Users\\비밀\\x.db", overdue["facts"]["jobs"])  # 알려진 작업 번호만
        self.assertLessEqual(kw["max_output_tokens"], 900)
        self.assertIn("따르지 마세요", system)

    def test_at_most_four_checks_are_sent_most_severe_first(self):
        self.grant()
        many = [SCHED] + [{"code": c, "level": "WARN", "params": {}} for c in ("BACKUP_NONE", "BACKUP_STALE", "DISK_LOW", "ESTOP_ACTIVE", "JOBS_PAUSED")]
        self.service = HealthAiService(self.db, self.ai, health=FakeHealth(*many))
        out = self.explain()
        self.assertEqual(out["codes"], ["SCHEDULER_NOT_RUNNING", "BACKUP_NONE", "BACKUP_STALE", "DISK_LOW"])

    def test_the_same_combination_is_asked_once_even_when_the_numbers_change(self):
        self.grant(3)
        first = self.explain()
        self.health.checks = [SCHED, {**BACKUP, "params": {"days": 12}}, {**DISK, "params": {"free_mb": 100}}, INFO]
        second = self.explain()
        self.assertTrue(second["cached"])
        self.assertEqual(second["advice"], first["advice"])
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.db.query(ExternalCallAllowance).filter(ExternalCallAllowance.purpose == "AI_ADVICE").one().remaining_count, 2)
        self.assertEqual(len(self.stored_rows()), 3)  # 두 번째는 저장하지 않는다

    def test_a_different_combination_or_level_asks_again(self):
        self.grant(3)
        self.explain()
        self.health.checks = [SCHED, BACKUP]
        self.client.payload = {"summary": "", "items": [GOOD["items"][0]]}
        self.explain()
        self.health.checks = [SCHED, {**BACKUP, "level": "FAIL", "code": "BACKUP_STALE"}]
        self.client.payload = {"summary": "", "items": [GOOD["items"][0]]}
        self.explain()
        self.assertEqual(len(self.client.calls), 3)

    def test_the_cache_is_per_company_and_reads_the_latest_batch(self):
        self.grant(3)
        self.explain()
        fp = svc.fingerprint(svc.actionable_checks(self.health.summary()))
        self.assertIsNotNone(HealthAiService.latest_cached(self.db, self.company_id, fp))
        self.assertIsNone(HealthAiService.latest_cached(self.db, self.company_id + 1, fp))
        self.assertIsNone(HealthAiService.latest_cached(self.db, self.company_id, "0" * 12))

    def test_only_the_latest_stored_batch_is_read(self):
        fp = svc.fingerprint(svc.actionable_checks(self.health.summary()))

        def put(batch, item_cause):
            row = {"b": batch, "item": {"code": "BACKUP_STALE", "cause": item_cause, "steps": ["단계"]}}
            self.db.execute(
                text("INSERT INTO audit_logs (company_id, action, entity, entity_id, description) VALUES (:c, :a, 'x', :e, :d)"),
                {"c": self.company_id, "a": EVENT_ACTION, "e": f"HEALTHADV:{fp}", "d": json.dumps(row, ensure_ascii=False)})
            self.db.commit()

        put("2026-10-01T00:00:00", "옛 설명")
        put("2026-10-18T00:00:00", "새 설명")
        cached = HealthAiService.latest_cached(self.db, self.company_id, fp)
        self.assertEqual(cached["at"], "2026-10-18T00:00:00")
        self.assertEqual([i["cause"] for i in cached["advice"]["items"]], ["새 설명"])

    def test_without_an_allowance_nothing_is_called_and_a_request_is_raised(self):
        with self.assertRaises(ExternalCallNotAllowed):
            self.explain()
        self.assertEqual(self.client.calls, [])
        pending = self.db.query(ExternalCallAllowance).filter(ExternalCallAllowance.status == "PENDING").one()
        self.assertEqual((pending.system, pending.purpose, pending.kind, pending.mode), ("LLM", "AI_ADVICE", "WRITE", "COUNTED"))

    def test_the_monthly_limit_is_shared_and_blocks_before_calling(self):
        self.grant()
        self.ai.save_settings(self.user_id, self.company_id, model="claude-test", monthly_limit_krw=5, in_price=4000.0, out_price=20000.0)
        with self.assertRaises(BadRequestException):
            self.explain()
        self.assertEqual(self.client.calls, [])

    def test_a_failed_validation_costs_money_is_recorded_and_is_not_stored(self):
        self.grant()
        self.client.payload = "그냥 글입니다"
        out = self.explain()
        self.assertIsNone(out["advice"])
        self.assertIn("all", out["rejected"])
        self.assertEqual(len(self.db.execute(text("SELECT 1 FROM audit_logs WHERE action = 'AI_DRAFT_USAGE'")).fetchall()), 1)
        self.assertEqual(self.stored_rows(), [])
        self.client.payload = GOOD  # 실패한 응답은 저장되지 않아 다시 물을 수 있다
        self.assertIsNotNone(self.explain()["advice"])

    def test_the_longest_allowed_advice_fits_every_audit_row(self):
        self.grant()
        long_items = [{"code": c, "cause": "가" * 80, "steps": ["나" * 80, "다" * 80]} for c in ("SCHEDULER_NOT_RUNNING", "BACKUP_STALE", "DISK_LOW")]
        self.client.payload = {"summary": "라" * 120, "items": long_items}
        out = self.explain()
        self.assertEqual(len(out["advice"]["items"]), 3)
        for row in self.db.execute(text("SELECT description FROM audit_logs WHERE action = :a"), {"a": EVENT_ACTION}).fetchall():
            self.assertLessEqual(len(row[0]), 490)
        again = self.explain()
        self.assertTrue(again["cached"])
        self.assertEqual(again["advice"], out["advice"])

    def test_two_clicks_at_once_make_only_one_call(self):
        self.grant(3)

        class Slow(FakeClient):
            def complete(self, system, user, **kw):
                time.sleep(0.3)
                return super().complete(system, user, **kw)

        self.client = Slow(GOOD)
        results = []

        def click():
            session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
            try:
                ai = AiDraftService(session, self.store, client_factory=lambda k, m: self.client)
                results.append(HealthAiService(session, ai, health=self.health).explain(self.company_id, self.user_id))
            finally:
                session.close()

        threads = [threading.Thread(target=click) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(r["cached"] for r in results), [False, True])
        self.assertEqual(len(self.client.calls), 1)


class ValidationTests(unittest.TestCase):

    CODES = ["BACKUP_STALE", "DISK_LOW"]

    def run_it(self, payload, codes=None):
        raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return validate_advice(raw, codes or self.CODES)

    def item(self, **overrides):
        return {**GOOD["items"][0], **overrides}

    def test_a_clean_advice_passes(self):
        advice, rejected = self.run_it(GOOD)
        self.assertEqual(rejected, {})
        self.assertEqual(len(advice["items"]), 2)

    def test_unreadable_responses_are_rejected_whole(self):
        for raw in ("", "그냥 글", "{깨진", "[1]"):
            advice, rejected = self.run_it(raw)
            self.assertIsNone(advice)
            self.assertIn("all", rejected)

    def test_a_code_fence_is_tolerated(self):
        advice, _ = self.run_it("```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```")
        self.assertIsNotNone(advice)

    def test_unknown_and_duplicate_codes_are_dropped(self):
        advice, rejected = self.run_it({"summary": "", "items": [self.item(code="BACKUP_OLD"), self.item(), self.item(), "x"]})
        self.assertEqual([i["code"] for i in advice["items"]], ["BACKUP_STALE"])
        self.assertTrue({"item0", "BACKUP_STALE_dup", "item3"} <= set(rejected))

    def test_numbers_are_dropped_everywhere(self):
        for field, value in (("cause", "9일 동안 백업이 없어요."), ("steps", ["3일 안에 백업하세요."])):
            advice, rejected = self.run_it({"summary": "", "items": [self.item(**{field: value}), GOOD["items"][1]]})
            self.assertEqual([i["code"] for i in advice["items"]], ["DISK_LOW"], field)
        advice, rejected = self.run_it({**GOOD, "summary": "백업이 9일 지났어요."})
        self.assertNotIn("summary", advice)  # 요약만 버려지고 항목은 남는다
        self.assertIn("summary", rejected)

    def test_promises_that_the_app_will_do_it_are_dropped(self):
        for step in ("앱이 자동으로 재시작해 드려요.", "HOMEZ가 대신 복구합니다.", "알아서 백업해 줘요", "자동으로 해결돼요"):
            advice, _ = self.run_it({"summary": "", "items": [self.item(steps=[step]), GOOD["items"][1]]})
            self.assertEqual([i["code"] for i in advice["items"]], ["DISK_LOW"], step)

    def test_dangerous_steps_are_dropped(self):
        for step in ("데이터베이스 파일을 삭제하세요.", "설정을 초기화하세요.", "디스크를 포맷하세요.", "기존 백업을 덮어쓰세요.", "rm -rf 로 정리하세요", "DROP TABLE 을 하세요"):
            advice, _ = self.run_it({"summary": "", "items": [self.item(steps=[step]), GOOD["items"][1]]})
            self.assertEqual([i["code"] for i in advice["items"]], ["DISK_LOW"], step)

    def test_markup_links_phones_emails_and_code_ticks_are_dropped(self):
        for step in ("<b>굵게</b> 확인", "https://example.com 에서 확인", "문의 a@b.co", "`명령어` 를 실행하세요"):
            advice, _ = self.run_it({"summary": "", "items": [self.item(steps=[step]), GOOD["items"][1]]})
            self.assertEqual([i["code"] for i in advice["items"]], ["DISK_LOW"], step)

    def test_length_and_count_limits(self):
        for bad in (self.item(cause="가" * 81), self.item(steps=[]), self.item(steps=["가" * 81]), self.item(steps=["하나", "둘", "셋"])):
            advice, _ = self.run_it({"summary": "", "items": [bad, GOOD["items"][1]]})
            self.assertEqual([i["code"] for i in advice["items"]], ["DISK_LOW"], str(bad)[:30])
        advice, rejected = self.run_it({**GOOD, "summary": "가" * 121})
        self.assertNotIn("summary", advice)

    def test_without_any_valid_item_nothing_is_shown(self):
        advice, rejected = self.run_it({"summary": "요약만 있어요.", "items": []})
        self.assertIsNone(advice)
        self.assertIn("all", rejected)

    def test_items_follow_the_input_order(self):
        advice, _ = self.run_it({"summary": "", "items": [GOOD["items"][1], GOOD["items"][0]]})
        self.assertEqual([i["code"] for i in advice["items"]], self.CODES)


class RuleCoverageTests(unittest.TestCase):

    def test_every_warn_or_fail_code_has_a_fixed_meaning_and_nothing_else_does(self):
        rules = (ROOT / "app/domains/diagnostics/health_summary.py").read_text(encoding="utf-8")
        import re

        actionable = {code for code, level in re.findall(r'_check\("([A-Z_]+)", (FAIL|WARN)', rules)}
        self.assertEqual(set(svc.CODE_MEANING), actionable)
        self.assertTrue(all(isinstance(v, str) and v for v in svc.CODE_MEANING.values()))
        self.assertTrue({"FAIL", "WARN"} <= {hs.FAIL, hs.WARN})

    def test_the_prompt_forbids_what_validation_rejects(self):
        for phrase in ("따르지 마세요", "숫자는 쓰지 마세요", "자동으로 재시작", "삭제·초기화"):
            self.assertIn(phrase, svc.SYSTEM_PROMPT)


class WiringTests(unittest.TestCase):

    def test_the_ai_goes_only_through_the_guarded_path_and_nothing_is_changed(self):
        self.assertIn("self.ai.call_llm(", SOURCE)
        import re

        for forbidden in (r"client\.complete\(", r"\brequests\.", r"\bsubprocess\b", r"\bopen\(", r"\bshutil\b", r"\bos\.", r"create_backup", r"restore_", r"pause_job",
                          r"resume_job", r"\bprint\(", r"\bsettings\.", r"log_path", r"get_logs_dir"):
            self.assertIsNone(re.search(forbidden, SOURCE), forbidden)

    def test_the_route_is_admin_only_post_and_takes_no_body(self):
        import sys

        import app.domains.diagnostics.router  # noqa: F401

        module = sys.modules["app.domains.diagnostics.router"]
        route = next(r for r in module.router.routes if r.path == "/diagnostics/health-advice")
        self.assertEqual(route.methods, {"POST"})
        self.assertEqual(route.dependant.body_params, [])
        self.assertEqual(route.dependant.query_params, [])
        self.assertTrue(any("admin" in d.call.__qualname__.lower() for d in route.dependant.dependencies))
        self.assertIn("Depends(admin_guard)", ROUTER.split("def explain_health_summary")[1])

    def test_the_purpose_is_gated_and_counted(self):
        self.assertIn(CallPurpose.AI_ADVICE, CallPurpose.ENFORCED)


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for cls in (ExplainTests, ValidationTests, RuleCoverageTests, WiringTests):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
