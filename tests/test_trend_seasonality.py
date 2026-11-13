"""
=========================================================
Homez OS

File : tests/test_trend_seasonality.py

2026-11-13 — 트렌드 계절 보정(조사 문서 3-4): `seasonality.py`(판정 규칙), 네이버 Adapter의 기간 지정 조회와 날짜(period),
관심 키워드 조회의 저장값, 쉬운 보기 '작년 이맘때와 비교' 칸을 고정한다. 임시 SQLite — 네트워크 0(가짜 http_post).

  - 판정: 작년 이맘때도 오른 오름은 '계절', 작년엔 없던 오름은 '새 움직임', 오름 없이 작년보다 높/낮음, 비슷, 자료 1년 미만·날짜 모름은 '비교 불가'.
  - 날짜로 맞춘다(점 순서로 52주 전을 찾지 않는다). 입력 순서가 섞여도 같다. NaN·음수·날짜 오류는 비교 불가.
  - 기존 점수는 최근 90일만으로 계산(2년을 받아도 점수 뜻이 바뀌지 않는다). 기간을 바꿀 수 없는 Adapter는 예전 그대로 + 비교 불가.
  - 조회 1회로 2년을 받는다(호출 수 그대로). 못 읽으면 이전 판정을 지우지 않는다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from datetime import date
from datetime import timedelta
from pathlib import Path

from app.domains.trend_discovery import seasonality as ss
from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter
from app.domains.trend_discovery.adapter import TrendSignal
from app.domains.trend_discovery.service import TrendDiscoveryService
from tests.test_easy_briefing_ui import function_source
from tests.test_trend_keywords_d3 import A
from tests.test_trend_keywords_d3 import DbCase
from tests.test_trend_keywords_d3 import FakeAdapter

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
LATEST = date(2026, 11, 9)          # 월요일(네이버 주 단위 기간 시작일 모양)


def weeks(n=105, latest=LATEST):
    return [(latest - timedelta(weeks=n - 1 - i)).isoformat() for i in range(n)]


def build(periods, rule):
    """rule(지금으로부터 몇 주 전) → 값."""

    latest = date.fromisoformat(periods[-1])
    return [float(rule((latest - date.fromisoformat(p)).days // 7)) for p in periods]


def spike_at(ago_weeks_list, base=20.0, high=60.0):
    """지정한 '몇 주 전' 구간(최근 4주 묶음)에 값을 올린다."""

    def rule(ago):
        return high if any(start <= ago <= start + 3 for start in ago_weeks_list) else base
    return rule


class JudgeTests(unittest.TestCase):

    def test_a_rise_that_also_happened_last_year_is_seasonal(self):
        p = weeks()
        out = ss.judge(build(p, spike_at([0, 52])), p)
        self.assertEqual(out.label, ss.SEASONAL)
        self.assertAlmostEqual(out.rise, 2.0)
        self.assertAlmostEqual(out.last_year_rise, 2.0)
        self.assertAlmostEqual(out.yoy, 1.0)

    def test_a_rise_that_did_not_happen_last_year_is_emerging(self):
        p = weeks()
        out = ss.judge(build(p, spike_at([0])), p)
        self.assertEqual(out.label, ss.EMERGING)
        self.assertAlmostEqual(out.last_year_rise, 0.0)
        self.assertAlmostEqual(out.yoy, 3.0)

    def test_a_seasonal_rise_much_bigger_than_last_year_is_emerging(self):
        p = weeks()
        out = ss.judge(build(p, lambda ago: 90 if ago <= 3 else (40 if 52 <= ago <= 55 else 20)), p)
        self.assertEqual(out.label, ss.EMERGING)       # 작년에도 올랐지만 작년의 1.5배 이상

    def test_no_rise_but_higher_or_lower_than_last_year(self):
        p = weeks()
        self.assertEqual(ss.judge(build(p, lambda ago: 40 if ago < 52 else 20), p).label, ss.ABOVE_LAST_YEAR)
        self.assertEqual(ss.judge(build(p, lambda ago: 10 if ago < 52 else 30), p).label, ss.BELOW_LAST_YEAR)
        flat = ss.judge(build(p, lambda ago: 30), p)
        self.assertEqual(flat.label, ss.STEADY)
        self.assertAlmostEqual(flat.yoy, 1.0)

    def test_thresholds_are_inclusive_at_the_edges(self):
        p = weeks()
        out = ss.judge(build(p, lambda ago: 26 if ago <= 3 or 52 <= ago <= 55 else 20), p)   # +30% 두 해
        self.assertEqual(out.label, ss.SEASONAL)
        out = ss.judge(build(p, lambda ago: 25.9 if ago <= 3 else 20), p)                      # +29.5%
        self.assertEqual(out.label, ss.STEADY)
        self.assertEqual(ss.judge(build(p, lambda ago: 30 if ago < 52 else 20), p).label, ss.ABOVE_LAST_YEAR)   # 정확히 1.5배
        exact_low = ss.judge(build(p, lambda ago: 67 if ago < 52 else 100), p)                    # 정확히 0.67배
        self.assertEqual(exact_low.yoy, ss.YOY_LOW)
        self.assertEqual(exact_low.label, ss.BELOW_LAST_YEAR)
        self.assertEqual(ss.judge(build(p, lambda ago: 68 if ago < 52 else 100), p).label, ss.STEADY)

    def test_nothing_last_year(self):
        p = weeks()
        self.assertEqual(ss.judge(build(p, lambda ago: 50 if ago < 52 else 0), p).label, ss.EMERGING)
        both_zero = ss.judge(build(p, lambda ago: 0), p)
        self.assertEqual(both_zero.label, ss.STEADY)
        self.assertIsNone(both_zero.yoy)

    def test_not_enough_data_or_unknown_dates(self):
        short = weeks(60)
        self.assertEqual(ss.judge(build(short, lambda ago: 30), short).label, ss.NOT_ENOUGH)       # 1년 전 기준 구간이 없다
        p = weeks()
        values = build(p, lambda ago: 30)
        self.assertEqual(ss.judge(values, []).label, ss.NOT_ENOUGH)
        self.assertEqual(ss.judge(values, p[:-1]).label, ss.NOT_ENOUGH)
        self.assertEqual(ss.judge([], []).label, ss.NOT_ENOUGH)
        self.assertEqual(ss.judge(values[:-1] + [float("nan")], p).label, ss.NOT_ENOUGH)
        self.assertEqual(ss.judge(values[:-1] + [-1.0], p).label, ss.NOT_ENOUGH)
        self.assertEqual(ss.judge(values, p[:-1] + ["2026-13-01"]).label, ss.NOT_ENOUGH)
        self.assertEqual(ss.judge(None, None).label, ss.NOT_ENOUGH)
        self.assertEqual(ss.judge(values[:-1] + ["x"], p).label, ss.NOT_ENOUGH)

    def test_dates_not_positions_decide_last_year(self):
        p = weeks()
        values = build(p, spike_at([0, 52]))
        # 중간 주 몇 개가 빠져도(네이버가 점을 빼도) 날짜로 맞추므로 같은 판정
        keep = [i for i in range(len(p)) if i not in (10, 11, 30)]
        self.assertEqual(ss.judge([values[i] for i in keep], [p[i] for i in keep]).label, ss.SEASONAL)
        # 순서를 섞어도 같다
        order = list(reversed(range(len(p))))
        self.assertEqual(ss.judge([values[i] for i in order], [p[i] for i in order]).label, ss.SEASONAL)

    def test_as_dict_rounds_and_keeps_none(self):
        self.assertEqual(ss.SeasonResult(ss.NOT_ENOUGH).as_dict(), {"label": "NOT_ENOUGH", "rise": None, "last_year_rise": None, "yoy": None})
        self.assertEqual(ss.SeasonResult(ss.STEADY, 0.12345, -0.5, 1.23456).as_dict()["yoy"], 1.235)

    def test_recent_slice_keeps_the_last_90_days(self):
        p = weeks()
        values = list(range(len(p)))
        series, periods = ss.recent_slice(values, p)
        self.assertEqual(periods[-1], p[-1])
        self.assertTrue(all(date.fromisoformat(x) > LATEST - timedelta(days=90) for x in periods))
        self.assertEqual(len(series), 13)
        self.assertEqual(series, values[-13:])
        self.assertEqual(ss.recent_slice([1, 2, 3], []), ([1, 2, 3], []))          # 날짜를 모르면 그대로(기존 동작)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeStore:
    def read(self, reference):
        return {"client_id": "id", "client_secret": "secret"}


class FakeTracker:
    def is_within_limit(self, *a, **k):
        from types import SimpleNamespace
        return SimpleNamespace(within_limit=True)

    def record_call(self, *a, **k):
        pass


class AdapterTests(unittest.TestCase):

    def adapter(self, body):
        self.sent = []

        def post(url, *, headers, json, timeout):  # noqa: A002
            self.sent.append(json)
            return FakeResponse(200, body)
        return NaverDataLabTrendAdapter(FakeStore(), "ref", http_post=post, usage_tracker=FakeTracker())

    def setUp(self):
        from unittest import mock
        patcher = mock.patch("app.domains.trend_discovery.adapter.require_active_capability", lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lookback_is_only_the_start_date_and_periods_are_kept(self):
        body = {"results": [{"data": [{"period": "2026-11-02", "ratio": 50}, {"period": "2026-11-09", "ratio": 70}]}]}
        a = self.adapter(body)
        got = a.fetch_with_lookback("생수", ss.SEASON_LOOKBACK_DAYS)
        self.assertEqual(got.periods, ["2026-11-02", "2026-11-09"])
        self.assertEqual(got.search_volume_series, [50.0, 70.0])
        sent = self.sent[0]
        self.assertEqual(date.fromisoformat(sent["endDate"]) - date.fromisoformat(sent["startDate"]), timedelta(days=ss.SEASON_LOOKBACK_DAYS))
        self.assertEqual(sent["timeUnit"], "week")
        self.assertEqual(len(self.sent), 1)
        a.fetch("생수")
        self.assertEqual(date.fromisoformat(self.sent[1]["endDate"]) - date.fromisoformat(self.sent[1]["startDate"]), timedelta(days=90))   # 기본은 그대로

    def test_a_missing_period_drops_all_dates(self):
        body = {"results": [{"data": [{"period": "2026-11-02", "ratio": 50}, {"ratio": 70}]}]}
        self.assertEqual(self.adapter(body).fetch("생수").periods, [])


def long_signal(keyword, rule):
    p = weeks()
    return TrendSignal(keyword=keyword, market="NAVER_SHOPPING", search_volume_series=build(p, rule), observed_at="2026-11-09T00:00:00Z", source="fake", periods=p)


class LongAdapter(FakeAdapter):
    def __init__(self, signals):
        super().__init__()
        self.signals = signals
        self.lookbacks = []

    def fetch_with_lookback(self, keyword, lookback_days):
        self.lookbacks.append(lookback_days)
        self.fetched.append(keyword)
        return self.signals.get(keyword)


class KeywordServiceTests(DbCase):

    def row(self, keyword):
        return next(r for r in self.service().list_rows(self.uid, A)["rows"] if r["keyword"] == keyword)

    def test_long_lookup_stores_the_season_and_keeps_the_90_day_score(self):
        self.add("생수")
        sig = long_signal("생수", spike_at([0, 52]))
        adapter = LongAdapter({"생수": sig})
        self.assertEqual(self.service().lookup_one(self.uid, A, "생수", adapter), "OK")
        self.assertEqual(adapter.lookbacks, [ss.SEASON_LOOKBACK_DAYS])
        row = self.row("생수")
        self.assertEqual(row["season"], ss.SEASONAL)
        self.assertEqual(row["yoy"], 1.0)
        self.assertEqual(row["rise"], 2.0)
        self.assertEqual(row["last_year_rise"], 2.0)
        recent, periods = ss.recent_slice(sig.search_volume_series, sig.periods)
        expected = TrendDiscoveryService().evaluate(TrendSignal(**{**sig.__dict__, "search_volume_series": recent, "periods": periods}))
        self.assertEqual(row["score"], expected.trend_score)
        self.assertEqual(row["points"], 13)
        self.assertEqual((row["first"], row["last"]), (round(recent[0], 2), round(recent[-1], 2)))

    def test_an_adapter_without_a_long_lookup_works_as_before(self):
        self.add("생수")
        adapter = FakeAdapter({"생수": (40.0, 45.0, 60.0)})
        self.assertEqual(self.service().lookup_one(self.uid, A, "생수", adapter), "OK")
        row = self.row("생수")
        self.assertEqual(row["season"], ss.NOT_ENOUGH)
        self.assertEqual((row["first"], row["last"], row["points"]), (40.0, 60.0, 3))
        self.assertEqual(row["score"], TrendDiscoveryService().evaluate(TrendSignal(keyword="생수", market="M", search_volume_series=[40.0, 45.0, 60.0], observed_at="x")).trend_score)

    def test_not_read_keeps_the_previous_season(self):
        self.add("생수")
        self.service().lookup_one(self.uid, A, "생수", LongAdapter({"생수": long_signal("생수", spike_at([0]))}))
        self.assertEqual(self.row("생수")["season"], ss.EMERGING)
        self.assertEqual(self.service().lookup_one(self.uid, A, "생수", LongAdapter({})), "NO_DATA")
        row = self.row("생수")
        self.assertEqual(row["state"], "NO_DATA")
        self.assertEqual(row["season"], ss.EMERGING)

    def test_a_keyword_never_looked_up_has_no_season(self):
        self.add("생수")
        self.assertIsNone(self.row("생수")["season"])


def kw_texts():
    """한국어 표만(영어 표가 같은 키로 덮어쓰지 않게 영어 표 시작 전까지)."""

    ko_part = TEXTS.split('"easy.kw.col_change": "About 13 weeks ago → now"', 1)[0]
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.kw\.[a-z_]+)": ("(?:[^"\\]|\\.)*")', ko_part)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const chip = (tone, text) => `<i class="${tone}">${text}</i>`;
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenTests(unittest.TestCase):

    def run_js(self, expression):
        season = re.search(r"^  const KW_SEASON = \{.*?^  \};$", JS, re.M | re.S).group(0)
        script = HARNESS.replace("__SOURCES__", season + "\n" + function_source("kwSeasonCell")).replace("__EXPR__", expression)
        ko = {k: v for k, v in kw_texts().items()}
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(ko)}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_each_label_and_the_numbers(self):
        cases = {"SEASONAL": ("wait", "계절"), "EMERGING": ("ok", "새 움직임"), "ABOVE_LAST_YEAR": ("ok", "작년보다 높음"),
                 "BELOW_LAST_YEAR": ("gray", "작년보다 낮음"), "STEADY": ("gray", "작년과 비슷"), "NOT_ENOUGH": ("gray", "비교 불가"), "<x>": ("gray", "비교 불가")}
        for code, (tone, word) in cases.items():
            html = self.run_js(f"kwSeasonCell({{season: {json.dumps(code)}}})")
            self.assertIn(f'<i class="{tone}">{word}</i>', html, code)
            self.assertNotIn("<x>", html)
        html = self.run_js("kwSeasonCell({season: 'SEASONAL', yoy: 1.04, last_year_rise: 0.62})")
        self.assertIn("작년 같은 때의 1배", html)
        self.assertIn("작년 이맘때 +62% 변화", html)
        html = self.run_js("kwSeasonCell({season: 'BELOW_LAST_YEAR', yoy: 0.55, last_year_rise: -0.2})")
        self.assertIn("0.6배", html)
        self.assertIn("-20%", html)

    def test_missing_or_odd_values_are_not_invented(self):
        self.assertIn("—", self.run_js("kwSeasonCell({season: null})"))
        self.assertIn("—", self.run_js("kwSeasonCell({})"))
        html = self.run_js("kwSeasonCell({season: 'STEADY', yoy: null, last_year_rise: 'x'})")
        self.assertNotIn("<small", html)
        self.assertNotIn("NaN", html)


class SourceTests(unittest.TestCase):

    def test_the_column_sits_after_the_change_and_texts_exist_in_both_languages(self):
        self.assertIn('"easy.kw.col_change", "easy.kw.col_season", "easy.kw.col_at"', JS)
        self.assertIn('<td>${kwSeasonCell(r)}</td>', JS)
        self.assertIn('${esc(T("easy.kw.season_note"))}', JS)
        keys = [m for m in re.findall(r'"(easy\.kw\.(?:col_season|season_[a-z_]+))"', JS)]
        self.assertGreaterEqual(len(set(keys)), 10)
        ko_part, en_part = TEXTS.split('"easy.kw.col_change": "About 13 weeks ago → now"', 1)
        for key in set(keys):
            self.assertIn(f'"{key}":', ko_part, key)
            self.assertIn(f'"{key}":', en_part, key)
        self.assertIn("조회 횟수는 그대로", kw_texts()["easy.kw.season_note"])
        self.assertIn("1년이 안 되면 '비교 불가'", kw_texts()["easy.kw.season_note"])     # 자료가 짧을 때(길 때가 아니다)

    def test_the_service_asks_for_two_years_in_one_call(self):
        src = (ROOT / "app/domains/trend_discovery/keyword_watch_service.py").read_text(encoding="utf-8")
        block = src[src.index("def lookup_one"):src.index("def lookup(")]
        self.assertEqual(block.count("long_fetch(keyword, seasonality.SEASON_LOOKBACK_DAYS)"), 1)    # 2년을 한 번에
        self.assertEqual(block.count("adapter.fetch("), 1)              # 기간을 못 바꾸는 Adapter용 하나뿐
        self.assertIn("if callable(long_fetch) else adapter.fetch(keyword)", block)
        self.assertGreaterEqual(ss.SEASON_LOOKBACK_DAYS, 364 + 84)


if __name__ == "__main__":
    unittest.main()
