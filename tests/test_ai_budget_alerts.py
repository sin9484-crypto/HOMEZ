"""
=========================================================
Homez OS

File : tests/test_ai_budget_alerts.py

2026-11-15 — 조사 문서 3-8 '비용 최적화' 중 지금 할 수 있는 부분: AI(Claude) 월 한도 50·80·100% 알림을 고정한다(가짜 AI 클라이언트, 바깥 호출 0).

  - 단계(`budget_stage`): 이번 달 사용액이 한도의 50%·80%·100% 이상이면 그 단계, 한도가 없거나 50% 미만이면 없음. 경계는 '이상'.
  - AI 사용 설정 상태·호출 직후 사용량(`record_usage`)이 단계를 함께 준다. 지난달 기록은 세지 않는다. 알림은 호출을 새로 막지 않는다.
  - 운영 상태 점검(AI_BUDGET): 100%·80% → WARN(오늘 화면 알림), 50% → INFO, 한도 없음 → 통과, 점검하는 사람을 모르면 unknown(정상 아님).
  - AI 설명에는 사용 비율(%)만 가고 금액은 가지 않는다. 쉬운 보기·콘솔이 한국어·영어 문구를 가진다.
  - 프롬프트 캐시는 넣지 않았다(호출 0건, 앞부분이 짧음) — 이 결정은 문서에 있고 여기서는 `cache_control`이 없음을 고정하지 않는다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.diagnostics import health_ai_service as ai
from app.domains.diagnostics import health_summary as hs
from app.domains.diagnostics.health_service import HealthService
from app.domains.marketplace_listing.ai_draft_service import AiDraftService
from app.domains.marketplace_listing.ai_draft_service import budget_stage
from app.domains.user_settings.model import UserSetting

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 15, 3, 0, 0)
KEY = "sk-ant-TEST-0123456789abcdefghij"


def snap(**overrides):
    base = {
        "now": NOW, "scheduler_enabled": False, "latest_backup_at": NOW, "rehearsal_known": True,
        "latest_rehearsal": {"at": NOW, "ok": True}, "disk_free_bytes": 50 * 1024 ** 3, "estop_active": False,
        "detection": {"health_code": "OK", "minutes_since_attempt": 3}, "reads": {"lapsed": [], "never": []},
        "replaced_links": {"links": 0, "resolutions": 0}, "ai_budget": {"limit_krw": 1000, "used_krw": 0.0, "stage": None},
    }
    base.update(overrides)
    return base


def check_of(result, code):
    found = [c for c in result["checks"] if c["code"] == code]
    return found[0] if found else None


class StageTests(unittest.TestCase):

    def test_boundaries_are_inclusive(self):
        cases = [(0, None), (499.99, None), (500, 50), (799.99, 50), (800, 80), (999.99, 80), (1000, 100), (1500, 100)]
        for used, stage in cases:
            self.assertEqual(budget_stage(used, 1000), stage, used)

    def test_no_limit_means_no_stage(self):
        for limit in (0, -5, None):
            self.assertIsNone(budget_stage(10_000, limit), limit)
        self.assertIsNone(budget_stage(None, 1000))

    def test_odd_limit_uses_exact_comparison_not_rounding(self):
        # 한도 3원: 1.5원이 정확히 50%. 1.49원은 50% 미만.
        self.assertEqual(budget_stage(1.5, 3), 50)
        self.assertIsNone(budget_stage(1.49, 3))
        self.assertEqual(budget_stage(2.4, 3), 80)
        self.assertEqual(budget_stage(2.39, 3), 50)


class RuleTests(unittest.TestCase):

    def test_reached_and_high_warn_with_percent(self):
        reached = check_of(hs.evaluate(snap(ai_budget={"limit_krw": 1000, "used_krw": 1234.5, "stage": 100})), "AI_BUDGET_REACHED")
        self.assertEqual((reached["level"], reached["params"]), (hs.WARN, {"pct": 123}))
        high = check_of(hs.evaluate(snap(ai_budget={"limit_krw": 1000, "used_krw": 818.0, "stage": 80})), "AI_BUDGET_HIGH")  # 81.8% → 81(버림)
        self.assertEqual((high["level"], high["params"]), (hs.WARN, {"pct": 81}))

    def test_half_is_info_only(self):
        result = hs.evaluate(snap(ai_budget={"limit_krw": 1000, "used_krw": 600.0, "stage": 50}))
        self.assertEqual(check_of(result, "AI_BUDGET_HALF")["level"], hs.INFO)
        self.assertEqual(result["overall"], hs.INFO)
        self.assertNotIn("AI_BUDGET", result["passed"])

    def test_below_half_or_no_limit_passes(self):
        for budget in ({"limit_krw": 1000, "used_krw": 100.0, "stage": None}, {"limit_krw": 0, "used_krw": 0.0, "stage": None},
                       {"limit_krw": 0, "used_krw": 50.0, "stage": 100}):
            result = hs.evaluate(snap(ai_budget=budget))
            self.assertIn("AI_BUDGET", result["passed"], budget)
            self.assertFalse([c for c in result["checks"] if c["code"].startswith("AI_BUDGET")], budget)

    def test_unknown_owner_or_unreadable_is_unknown_not_normal(self):
        for value in (None, "x", []):
            result = hs.evaluate(snap(ai_budget=value))
            self.assertIn("AI_BUDGET", result["unknown"], repr(value))
            self.assertNotIn("AI_BUDGET", result["passed"], repr(value))
        without_key = snap()
        del without_key["ai_budget"]
        self.assertIn("AI_BUDGET", hs.evaluate(without_key)["unknown"])


class ServiceTests(unittest.TestCase):

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{path}")
        self.addCleanup(os.remove, path)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(bind=self.engine, tables=[Company.__table__, UserSetting.__table__])
        with self.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50))"
            ))
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.addCleanup(self.db.close)
        company = Company(name="한도 시험 회사", business_number="555-55-55555", ceo="시험", phone="02-000-0000", email="b@example.com", address="서울")
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id
        self.store = InMemoryCredentialStore()
        self.user_id = 7

    def svc(self, now=NOW):
        return AiDraftService(self.db, self.store, now_factory=lambda: now)

    def configure(self, limit=1000):
        self.svc().save_settings(self.user_id, self.company_id, model="claude-test", monthly_limit_krw=limit, in_price=4000.0, out_price=20000.0, api_key=KEY)

    def spend(self, cost, now=NOW):
        return self.svc(now).record_usage(self.company_id, self.user_id, "T", "claude-test",
                                          {"input_tokens": 10, "output_tokens": 10, "cost_krw": cost, "ok": True})

    def health(self):
        return HealthService(self.db, scheduler_getter=lambda: None, scheduler_enabled=False, expected_jobs=[], now_factory=lambda: NOW)

    def test_status_and_usage_carry_the_stage(self):
        self.configure(1000)
        self.assertIsNone(self.svc().status(self.user_id, self.company_id)["month_alert_pct"])
        self.assertIsNone(self.spend(400)["month_alert_pct"])
        self.assertEqual(self.spend(100)["month_alert_pct"], 50)          # 500 / 1000
        self.assertEqual(self.spend(300)["month_alert_pct"], 80)          # 800
        self.assertEqual(self.svc().status(self.user_id, self.company_id)["month_alert_pct"], 80)
        self.assertEqual(self.spend(200)["month_alert_pct"], 100)         # 1000

    def test_last_month_is_not_counted(self):
        self.configure(1000)
        self.spend(900, now=datetime(2026, 10, 31, 14, 0, 0))
        self.assertIsNone(self.svc().status(self.user_id, self.company_id)["month_alert_pct"])
        self.assertEqual(self.svc().month_budget(self.user_id, self.company_id), {"limit_krw": 1000, "used_krw": 0.0, "calls": 0, "stage": None})

    def test_without_settings_there_is_no_stage(self):
        self.assertIsNone(self.spend(5000)["month_alert_pct"])
        self.assertEqual(self.svc().month_budget(self.user_id, self.company_id)["stage"], None)

    def test_health_summary_warns_for_the_checking_user(self):
        self.configure(2000)
        self.spend(1700)
        found = check_of(self.health().summary(ai_owner=(self.user_id, self.company_id)), "AI_BUDGET_HIGH")
        self.assertEqual(found["params"], {"pct": 85})

    def test_health_summary_without_owner_is_unknown(self):
        self.configure(1000)
        self.spend(1000)
        result = self.health().summary()
        self.assertIn("AI_BUDGET", result["unknown"])
        self.assertIsNone(check_of(result, "AI_BUDGET_REACHED"))

    def test_another_users_settings_are_not_used(self):
        self.configure(1000)
        self.spend(1000)
        result = self.health().summary(ai_owner=(self.user_id + 1, self.company_id))
        self.assertIn("AI_BUDGET", result["passed"])                     # 그 사람은 한도를 정하지 않았다

    def test_collection_only_reads(self):
        src = (ROOT / "app/domains/diagnostics/health_service.py").read_text(encoding="utf-8")
        body = src[src.index("def _ai_budget"):src.index("def snapshot")]
        for forbidden in (r"\.add\(", r"\.commit\(", r"\.execute\(", r"\bupdate\(", r"\bdelete\(", r"\.merge\(", r"\.flush\(", r"\.store\b", r"_read_key"):
            self.assertNotRegex(body, forbidden)
        src = (ROOT / "app/domains/marketplace_listing/ai_draft_service.py").read_text(encoding="utf-8")
        body = src[src.index("def month_budget"):src.index("def save_settings")]
        self.assertNotIn("self.store", body)
        self.assertNotIn("_key_saved", body)

    def test_router_passes_the_logged_in_user(self):
        src = (ROOT / "app/domains/diagnostics/router.py").read_text(encoding="utf-8")
        body = src[src.index("def get_health_summary"):src.index("def get_health_advice_credential_store")]
        self.assertIn("summary(ai_owner=(current_user.id, current_user.company_id))", body)


class ExplanationAndTextTests(unittest.TestCase):

    def test_ai_meaning_only_for_warnings_and_only_the_percent_is_sent(self):
        for code in ("AI_BUDGET_HIGH", "AI_BUDGET_REACHED"):
            self.assertTrue(ai.CODE_MEANING[code].strip())
        self.assertNotIn("AI_BUDGET_HALF", ai.CODE_MEANING)
        checks = [{"code": "AI_BUDGET_HIGH", "level": "WARN", "params": {"pct": 85, "used_krw": 850, "limit_krw": 1000}}]
        self.assertEqual([c["code"] for c in ai.actionable_checks({"checks": checks})], ["AI_BUDGET_HIGH"])
        prompt = ai.build_user_prompt(checks)
        self.assertIn('"budget_used_pct": 85', prompt)
        self.assertNotIn("850", prompt)
        self.assertNotIn("1000", prompt)

    def test_easy_view_briefing_has_text_in_both_languages_with_the_percent(self):
        view = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
        texts = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
        keys = dict(re.findall(r"([A-Z_]+): \"(easy\.brief\.sys_[a-z_]+)\"", view))
        self.assertNotIn("AI_BUDGET_HALF", keys)                         # 50%는 오늘 화면에 띄우지 않는다
        for code in ("AI_BUDGET_HIGH", "AI_BUDGET_REACHED"):
            found = re.findall(r'"' + re.escape(keys[code]) + r'": "([^"]*)"', texts)
            self.assertEqual(len(found), 2, code)
            for text in found:
                self.assertIn("{pct}", text)
        self.assertIn("pct: val(p.pct)", view)

    def test_easy_view_notes_follow_every_usage_line(self):
        view = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
        texts = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
        self.assertEqual(view.count("${aiBudgetNote(res.usage)}"), 4)      # 비서·문의 초안·반려 설명·시스템 설명
        self.assertIn("${aiBudgetNote(s)}", view)                         # AI 사용 설정 화면
        self.assertEqual(view.count("res.usage.month_used_krw"), 4)
        for stage in (50, 80, 100):
            self.assertEqual(len(re.findall(r'"easy\.ai\.budget_' + str(stage) + r'": "[^"]+"', texts)), 2, stage)

    def test_console_notes_follow_every_usage_line(self):
        console = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
        self.assertEqual(console.count("aiBudgetNoteHtml(usage)"), 3)
        self.assertEqual(console.count("aiBudgetNoteHtml(data.usage)"), 1)
        self.assertIn("aiBudgetNoteText(st)", console)
        for lang in ("ko-KR", "en-US"):
            src = (ROOT / f"app/web/i18n/{lang}.js").read_text(encoding="utf-8")
            for stage in (50, 80, 100):
                self.assertRegex(src, r'"ai\.budget_' + str(stage) + r'": "[^"]+"', (lang, stage))



@unittest.skipUnless(shutil.which("node"), "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenFunctionTests(unittest.TestCase):

    def run_note(self, info):
        from tests.test_easy_briefing_ui import function_source

        script = ("const T = (k) => '[' + k + ']'; const esc = (s) => String(s);\n" + function_source("aiBudgetNote")
                  + f"\nprocess.stdout.write(JSON.stringify(aiBudgetNote({json.dumps(info)})));")
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_each_stage_shows_its_own_line_and_nothing_otherwise(self):
        for stage in (50, 80, 100):
            html = self.run_note({"month_alert_pct": stage})
            self.assertIn(f"[easy.ai.budget_{stage}]", html)
        for info in ({"month_alert_pct": None}, {"month_alert_pct": 70}, {}, None, {"month_alert_pct": "<b>"}):
            self.assertEqual(self.run_note(info), "", info)


if __name__ == "__main__":
    unittest.main()
