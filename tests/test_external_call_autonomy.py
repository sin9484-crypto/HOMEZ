"""
=========================================================
Homez OS

File : tests/test_external_call_autonomy.py

2026-11-14 — 승인 피로 줄이기·기능별 자율 단계 표(조사 문서 3-5). 임시 SQLite — 네트워크 0.

  - 연장: 기간을 고르지 않으면 지난번과 같은 기간(30일 허용이 하루짜리로 줄지 않는다). 고르면 그 기간(최대 30일).
  - 미리 알림: 허용 기간의 10%, 최소 12시간·최대 72시간(24시간 허용은 예전처럼 12시간 전, 30일 허용은 3일 전).
  - 자율 단계 표: 바깥 호출 목적마다 한 줄, 실제 동작대로의 단계. 돈·고객 글·재심사는 언제나 매번 확인, AI는 제안까지.
    올리기 후보는 연속 성공 30회·30일 실패 0일 때 '제안'만. 내리기 검토는 쓰기 실패 1회부터, 그 밖은 실패율 5%(3회 이상) 또는 연속 실패 3회.
    표는 아무것도 바꾸지 않는다(허용 상태·기록 그대로).
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from datetime import timedelta
from pathlib import Path

from app.domains.external_call import autonomy as au
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.constants import LogOutcome
from app.domains.external_call.constants import MAX_STANDING_HOURS
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from tests.test_easy_briefing_ui import function_source
from tests.test_external_call_allowance import ServiceBase

ROOT = Path(__file__).resolve().parents[1]
ROUTER = (ROOT / "app/domains/external_call/router.py").read_text(encoding="utf-8")
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


class RenewPeriodTests(ServiceBase):

    def standing(self, hours, purpose=CallPurpose.ORDER_DETECTION, per_hour=14):
        return self.grant(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=purpose, mode=AllowanceMode.STANDING,
                          count=None, per_hour_limit=per_hour, hours=hours)

    def test_a_thirty_day_allowance_is_offered_three_days_ahead(self):
        self.standing(720)
        self.clock[0] += timedelta(hours=720 - 73)
        self.assertEqual(self.svc().renewable(1), [])
        self.clock[0] += timedelta(hours=2)                         # 71시간 남음
        items = self.svc().renewable(1)
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["granted_hours"], items[0]["lead_hours"], items[0]["hours_left"]), (720, 72.0, 71.0))

    def test_the_lead_time_follows_the_period_within_bounds(self):
        svc = self.svc()
        self.assertEqual(svc.lead_hours(24), 12.0)                  # 예전과 같음
        self.assertAlmostEqual(svc.lead_hours(168), 16.8)
        self.assertEqual(svc.lead_hours(720), 72.0)
        self.assertEqual(svc.lead_hours(1), 12.0)

    def test_a_day_allowance_still_waits_until_twelve_hours_before(self):
        self.standing(24)
        self.clock[0] += timedelta(hours=11.9)
        self.assertEqual(self.svc().renewable(1), [])
        self.clock[0] += timedelta(hours=0.2)
        self.assertEqual(len(self.svc().renewable(1)), 1)

    def test_renewing_without_a_choice_keeps_each_items_own_period(self):
        self.standing(720)
        self.clock[0] += timedelta(hours=700)
        self.standing(24, purpose=CallPurpose.AFTER_SALES_CHECK, per_hour=30)
        self.clock[0] += timedelta(hours=19)                       # 30일 허용 1시간·하루 허용 5시간 남음
        created = {a.purpose: a for a in self.svc().renew_expiring(1, user_id=1)}
        self.assertEqual(created["ORDER_DETECTION"].expires_at, self.clock[0] + timedelta(hours=720))
        self.assertEqual(created["AFTER_SALES_CHECK"].expires_at, self.clock[0] + timedelta(hours=24))
        self.assertEqual((created["ORDER_DETECTION"].per_hour_limit, created["AFTER_SALES_CHECK"].per_hour_limit), (14, 30))

    def test_a_chosen_period_applies_to_all(self):
        self.standing(720)
        self.clock[0] += timedelta(hours=700)
        self.standing(24, purpose=CallPurpose.AFTER_SALES_CHECK, per_hour=30)
        self.clock[0] += timedelta(hours=19)
        created = self.svc().renew_expiring(1, user_id=1, hours=168)
        self.assertEqual(len(created), 2)
        for a in created:
            self.assertEqual(a.expires_at, self.clock[0] + timedelta(hours=168))

    def test_an_explicit_window_is_still_honoured(self):
        self.standing(720)
        self.clock[0] += timedelta(hours=720 - 100)
        self.assertEqual(self.svc().renewable(1), [])
        self.assertEqual(len(self.svc().renewable(1, within_hours=120)), 1)

    def test_the_period_is_read_from_the_record_and_clamped(self):
        row = ExternalCallAllowance(expires_at=None, decided_at=None)
        self.assertEqual(self.svc().granted_hours(row), 24)
        a = self.standing(720)
        self.assertEqual(self.svc().granted_hours(a), 720)
        a.expires_at = a.decided_at + timedelta(hours=5000)
        self.assertEqual(self.svc().granted_hours(a), MAX_STANDING_HOURS)
        a.expires_at = a.decided_at - timedelta(hours=1)
        self.assertEqual(self.svc().granted_hours(a), 24)


class TableTests(ServiceBase):

    def table(self, company=1):
        return {r["purpose"]: r for r in au.AutonomyTableService(self.db, now_factory=lambda: self.clock[0]).table(company)["rows"]}

    def log(self, purpose, kind, outcome, days_ago=0.0, company=1, n=1):
        for _ in range(n):
            self.db.add(ExternalCallLog(company_id=company, system="COUPANG", kind=kind, purpose=purpose, outcome=outcome,
                                        created_at=self.clock[0] - timedelta(days=days_ago)))
        self.db.commit()

    def test_every_purpose_has_exactly_one_row_in_a_fixed_order(self):
        purposes = [f.purpose for f in au.FEATURES]
        self.assertEqual(sorted(purposes), sorted(p for p in CallPurpose.ALL if p != CallPurpose.ANY))
        self.assertEqual(len(purposes), len(set(purposes)))
        self.assertEqual(list(self.table()), purposes)

    def test_money_customer_text_and_review_writes_are_always_confirmed(self):
        by = {f.purpose: f for f in au.FEATURES}
        for p in (CallPurpose.PRICE_UPDATE, CallPurpose.RETURN_PROCESS, CallPurpose.INQUIRY_REPLY, CallPurpose.PRODUCT_NAME_UPDATE):
            self.assertEqual((by[p].stage, by[p].raise_policy), (au.PREPARE, au.ALWAYS_CONFIRM), p)
        for p in (CallPurpose.AI_DRAFT, CallPurpose.AI_ANALYSIS, CallPurpose.AI_ADVICE, CallPurpose.IMAGE_GENERATION, CallPurpose.AI_BACKGROUND):
            self.assertEqual((by[p].stage, by[p].raise_policy), (au.SUGGEST, au.AI_SUGGEST_ONLY), p)
        for f in au.FEATURES:
            if f.stage == au.PREPARE:
                self.assertEqual((f.kind, f.how), (CallKind.WRITE, au.ON_PRESS), f.purpose)
            if f.stage == au.AUTO:
                self.assertEqual((f.how, f.raise_policy), (au.SCHEDULED, au.TOP), f.purpose)
            if f.raise_policy == au.ELIGIBLE:
                self.assertEqual(f.stage, au.PREPARE, f.purpose)

    def test_raise_rule(self):
        stock = next(f for f in au.FEATURES if f.purpose == CallPurpose.STOCK_UPDATE)
        price = next(f for f in au.FEATURES if f.purpose == CallPurpose.PRICE_UPDATE)
        self.assertEqual(au.judge_raise(stock, 30, 0), au.CANDIDATE)
        self.assertEqual(au.judge_raise(stock, 29, 0), au.NOT_YET)
        self.assertEqual(au.judge_raise(stock, 60, 1), au.NOT_YET)
        self.assertEqual(au.judge_raise(price, 999, 0), au.NEVER)

    def test_lower_rule(self):
        stock = next(f for f in au.FEATURES if f.purpose == CallPurpose.STOCK_UPDATE)
        detect = next(f for f in au.FEATURES if f.purpose == CallPurpose.ORDER_DETECTION)
        self.assertEqual(au.judge_lower(stock, 1, 50, 0), au.REVIEW)        # 쓰기는 한 번 실패부터
        self.assertEqual(au.judge_lower(stock, 0, 50, 0), au.OK)
        self.assertEqual(au.judge_lower(detect, 3, 597, 0), au.OK)          # 0.5% — 자주 도는 조회의 일시 오류
        self.assertEqual(au.judge_lower(detect, 3, 57, 0), au.REVIEW)       # 5%
        self.assertEqual(au.judge_lower(detect, 3, 58, 0), au.OK)           # 4.9%
        self.assertEqual(au.judge_lower(detect, 2, 2, 0), au.OK)            # 50%지만 2회
        self.assertEqual(au.judge_lower(detect, 3, 0, 3), au.REVIEW)
        self.assertEqual(au.judge_lower(detect, 0, 0, 3), au.REVIEW)        # 지금 연속 실패

    def test_counts_streaks_and_windows_come_from_the_records(self):
        W, R = CallKind.WRITE, CallKind.READ
        self.log(CallPurpose.STOCK_UPDATE, W, LogOutcome.FAILED, days_ago=40)          # 30일 밖
        self.log(CallPurpose.STOCK_UPDATE, W, LogOutcome.SUCCEEDED, days_ago=20, n=30)
        self.log(CallPurpose.STOCK_UPDATE, R, LogOutcome.FAILED, days_ago=1, n=5)      # 읽기는 이 기능의 핵심 호출이 아니다
        self.log(CallPurpose.STOCK_UPDATE, W, LogOutcome.ALLOWED, days_ago=0)          # 결과 미정은 세지 않는다
        row = self.table()[CallPurpose.STOCK_UPDATE]
        self.assertEqual((row["succeeded_30d"], row["failed_30d"], row["failed_7d"], row["success_streak"]), (30, 0, 0, 30))
        self.assertEqual((row["raise"], row["lower"]), (au.CANDIDATE, au.OK))
        self.log(CallPurpose.STOCK_UPDATE, W, LogOutcome.FAILED, days_ago=0)
        row = self.table()[CallPurpose.STOCK_UPDATE]
        self.assertEqual((row["success_streak"], row["fail_streak"], row["failed_7d"]), (0, 1, 1))
        self.assertEqual((row["raise"], row["lower"]), (au.NOT_YET, au.REVIEW))

    def test_allowances_now_skip_expired_and_undecided_ones_and_other_companies(self):
        self.grant(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING,
                   count=None, per_hour_limit=14, hours=720)
        old = self.grant(system=ExternalSystem.ONCH, kind=CallKind.READ, purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING,
                         count=None, per_hour_limit=30, hours=1)
        self.svc().ensure_pending_request(1, ExternalSystem.COUPANG, CallKind.WRITE, CallPurpose.STOCK_UPDATE)
        self.svc().grant(2, system=ExternalSystem.COUPANG, kind=CallKind.WRITE, purpose=CallPurpose.STOCK_UPDATE, mode=AllowanceMode.COUNTED, count=3, user_id=1)
        self.clock[0] += timedelta(hours=2)
        rows = self.table()
        self.assertEqual([(a["system"], a["mode"], a["per_hour_limit"], a["kind_label"]) for a in rows[CallPurpose.ORDER_DETECTION]["allowances"]], [("COUPANG", "STANDING", 14, "조회")])
        self.assertEqual(rows[CallPurpose.STOCK_UPDATE]["allowances"], [])
        self.assertEqual(self.db.get(ExternalCallAllowance, old.id).status, AllowanceStatus.ACTIVE)     # 표는 상태를 바꾸지 않는다
        self.svc().grant(1, system=ExternalSystem.COUPANG, kind=CallKind.WRITE, purpose=CallPurpose.STOCK_UPDATE, mode=AllowanceMode.COUNTED, count=3, user_id=1)
        self.assertEqual(self.table()[CallPurpose.STOCK_UPDATE]["allowances"][0]["remaining_count"], 3)

    def test_allowances_are_grouped_by_system_kind_and_mode(self):
        for count in (2, 5):
            self.grant(system=ExternalSystem.COUPANG, kind=CallKind.WRITE, purpose=CallPurpose.STOCK_UPDATE, mode=AllowanceMode.COUNTED, count=count)
        self.grant(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.STOCK_UPDATE, mode=AllowanceMode.COUNTED, count=3)
        used = self.grant(system=ExternalSystem.COUPANG, kind=CallKind.WRITE, purpose=CallPurpose.STOCK_UPDATE, mode=AllowanceMode.COUNTED, count=1)
        used.remaining_count = 0
        self.grant(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING, count=None, per_hour_limit=14, hours=24)
        late = self.grant(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING, count=None, per_hour_limit=20, hours=720)
        self.grant(system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.ORDER_DETECTION, mode=AllowanceMode.STANDING, count=None, per_hour_limit=10, hours=48)
        self.db.commit()
        rows = self.table()
        self.assertEqual([(a["kind"], a["remaining_count"], a["count"]) for a in rows[CallPurpose.STOCK_UPDATE]["allowances"]], [("READ", 3, 1), ("WRITE", 7, 2)])
        [standing] = rows[CallPurpose.ORDER_DETECTION]["allowances"]
        self.assertEqual((standing["expires_at"], standing["per_hour_limit"], standing["count"]), (late.expires_at.isoformat(), 20, 3))

    def test_other_companies_records_are_not_counted_and_nothing_is_written(self):
        self.log(CallPurpose.STOCK_UPDATE, CallKind.WRITE, LogOutcome.SUCCEEDED, company=2, n=40)
        before = (self.db.query(ExternalCallLog).count(), self.db.query(ExternalCallAllowance).count())
        row = self.table()[CallPurpose.STOCK_UPDATE]
        self.assertEqual((row["succeeded_30d"], row["success_streak"]), (0, 0))
        self.assertEqual((self.db.query(ExternalCallLog).count(), self.db.query(ExternalCallAllowance).count()), before)


class WiringTests(unittest.TestCase):

    def test_the_renew_request_allows_up_to_thirty_days_and_defaults_to_same_as_last_time(self):
        block = ROUTER.split("class RenewRequest")[1].split("@router")[0]
        self.assertIn("within_hours: int | None = Field(default=None, ge=1, le=168)", block)
        self.assertIn("hours: int | None = Field(default=None, ge=1, le=MAX_STANDING_HOURS)", block)
        self.assertIn('extra="forbid"', block)
        self.assertEqual(MAX_STANDING_HOURS, 720)

    def test_the_table_route_is_admin_only_and_read_only(self):
        block = ROUTER.split('@router.get("/autonomy")')[1].split("@router.")[0]
        self.assertIn("Depends(AdminGuard)", block)
        self.assertNotIn("commit", block)
        self.assertNotIn("write_audit_log", block)
        self.assertIn("AutonomyTableService(db).table(current_user.company_id)", block)
        service = (ROOT / "app/domains/external_call/autonomy.py").read_text(encoding="utf-8")
        for word in ("commit(", ".add(", ".delete(", "flush("):
            self.assertNotIn(word, service, word)
        self.assertIsNone(re.search(r"\.status\s*=(?!=)", service))       # 상태를 바꾸지 않는다(비교 ==는 된다)

    def test_the_screen_loads_the_table_and_picks_the_period_before_the_password(self):
        self.assertIn("autonomyLoad();", JS)
        self.assertIn('<div id="ez-auto-box"', JS)
        body = JS.split("async function renewAllDialog(")[1].split("async function grantDialog(")[0]
        self.assertLess(body.index('name="period"'), body.index("await ezDialog("))
        self.assertLess(body.index("renewRequestBody("), body.index("promptRecentAuthToken"))
        self.assertIn("JSON.stringify(body)", body)

    def test_texts_exist_in_both_languages_and_no_longer_promise_24_hours(self):
        ko, en = TEXTS.split('"easy.ap.renew_title": "{n} allowances expire soon"', 1)
        keys = set(re.findall(r'"(easy\.auto\.[a-z_]+|easy\.ap\.(?:renew_period|renew_same|period_days|period_hours|renew_last))"', JS))
        self.assertGreaterEqual(len(keys), 25)
        for key in keys:
            self.assertIn(f'"{key}":', ko, key)
            self.assertIn(f'"{key}":', en, key)
        for key in ("easy.ap.renew_sub", "easy.ap.renew_btn", "easy.ap.renew_confirm_body", "easy.today.renew_detail"):
            for part in (ko, en):
                value = re.search(r'"%s": ("(?:[^"\\]|\\.)*")' % re.escape(key), part).group(1)
                self.assertNotIn("24", json.loads(value), key)


def ko_texts():
    ko = TEXTS.split('"easy.ap.renew_title": "{n} allowances expire soon"', 1)[0]
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:auto|ap)\.[a-z_]+)": ("(?:[^"\\]|\\.)*")', ko)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const chip = (kind, text) => `<span class="ez-chip ${kind}">${esc(text)}</span>`;
const B = { fmtDate: (v) => `D(${v})` };
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenTests(unittest.TestCase):

    def run_js(self, expression):
        consts = []
        for name in ("AUTO_STAGE", "AUTO_HOW", "AUTO_RAISE", "RENEW_PERIODS"):
            consts.append(re.search(r"^  const %s = .*?;$" % name, JS, re.M | re.S).group(0))
        funcs = [function_source(n) for n in ("autonomyCount", "autonomyAllowText", "autonomyNextHtml", "autonomyTableHtml", "periodText", "renewRequestBody")]
        script = HARNESS.replace("__SOURCES__", "\n".join(consts + funcs)).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(ko_texts())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_rows_show_stage_allowance_record_and_next_step(self):
        rows = [
            {"purpose_label": "신규 주문 자동 감지", "stage": "AUTO", "how": "SCHEDULED", "succeeded_30d": 1781, "failed_30d": 6, "raise": "AT_TOP", "lower": "OK",
             "allowances": [{"system_label": "쿠팡", "kind_label": "조회", "mode": "STANDING", "expires_at": "2026-12-08T04:53:34"}, {"system_label": "온채널", "kind_label": "조회", "mode": "COUNTED", "remaining_count": 6}]},
            {"purpose_label": "쿠팡 재고 수량 변경", "stage": "PREPARE", "how": "ON_PRESS", "succeeded_30d": 2, "failed_30d": 0, "success_streak": 2, "raise_needed": 30, "raise": "NOT_YET", "lower": "OK", "allowances": []},
            {"purpose_label": "쿠팡 판매가 변경", "stage": "PREPARE", "how": "ON_PRESS", "raise": "NEVER", "lower": "REVIEW", "failed_7d": 1, "fail_streak": 1, "allowances": []},
            {"purpose_label": "출고일", "stage": "PREPARE", "how": "ON_PRESS", "raise": "CANDIDATE", "success_streak": 31, "lower": "OK"},
            {"purpose_label": "<b>x</b>", "stage": "WHAT", "how": "NOPE", "succeeded_30d": "NaN", "raise": "ZZZ", "lower": "ZZZ"},
        ]
        html = self.run_js(f"autonomyTableHtml({{rows: {json.dumps(rows, ensure_ascii=False)}}})")
        self.assertIn('<span class="ez-chip ok">정해진 범위 자동</span>', html)
        self.assertIn("일정에 따라 스스로", html)
        self.assertIn("쿠팡 조회 상시 · D(2026-12-08T04:53:34)까지", html)
        self.assertIn("온채널 조회 남은 6회", html)
        self.assertIn("성공 1781 · 실패 6", html)
        self.assertIn("가장 높은 단계", html)
        self.assertIn("자동 후보까지 연속 성공 2/30회(30일 실패 0)", html)
        self.assertIn("허용 없음", html)
        self.assertIn('<span class="ez-chip red">내리기 검토</span>', html)
        self.assertIn("최근 7일 실패 1회 · 지금 연속 실패 1회", html)
        self.assertIn("돈·고객에게 보이는 글이라 매번 확인", html)
        self.assertIn('<span class="ez-chip ok">자동 후보(제안)</span>', html)
        self.assertIn("연속 성공 31회", html)
        self.assertIn('<span class="ez-chip gray">보기</span>', html)          # 모르는 단계는 '보기'로
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("NaN", html)
        self.assertNotIn("ZZZ", html)
        self.assertIn("저절로 올라가지 않아요", html)

    def test_empty_or_missing_data_is_not_invented(self):
        for value in ("null", "{}", "{rows: []}"):
            self.assertIn("불러오지 못했어요", self.run_js(f"autonomyTableHtml({value})"))

    def test_period_choice(self):
        self.assertEqual(self.run_js("[renewRequestBody(''), renewRequestBody('24'), renewRequestBody('720'), renewRequestBody('999'), renewRequestBody('x')]"),
                         [{}, {"hours": 24}, {"hours": 720}, {}, {}])
        self.assertEqual(self.run_js("[periodText(720), periodText(24), periodText(36), periodText(0), periodText('x')]"), ["30일", "1일", "36시간", "—", "—"])


if __name__ == "__main__":
    unittest.main()
