"""
2026-11-01 — 사용자 결정 D3: 트렌드 키워드는 사람이 입력한 목록에서(가짜 네이버, 바깥 호출 0).

고정하는 약속
  - 목록은 사용자·회사별, 최대 30개, 중복·빈 값·40자 초과·개인정보로 보이는 글은 거절한다.
  - 조회는 호출마다 승인 센터 '검색어 트렌드 조회' 허용 1회. 허용·키가 없으면 호출하지 않고 이유를 돌려준다.
  - 못 읽으면 0점으로 꾸미지 않는다. 이전에 받은 점수는 지우지 않는다. 조회 중 지운 키워드는 되살아나지 않는다.
  - 한 번 누르면 오늘 안 본 것을 최대 10개, 한 번도 안 본 것 → 오래된 것 순. 자동 실행은 후보 조회 뒤 남은 건수만 쓴다.
  - 판매 판단·후보 등록은 하지 않는다. 화면은 키워드끼리 검색량을 비교할 수 없다고 알린다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.main  # noqa: F401 — 모든 모델을 메타데이터에 올린다
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.automation_safety.service import SafetyService
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.product_candidate import router as candidate_router
from app.domains.product_candidate.discovery_auto_run_service import DiscoveryAutoRunService
from app.domains.product_candidate.discovery_auto_run_service import naver_trend_gate
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter
from app.domains.trend_discovery.adapter import TrendSignal
from app.domains.trend_discovery.keyword_watch_service import MAX_KEYWORDS
from app.domains.trend_discovery.keyword_watch_service import TrendKeywordService
from app.domains.user.model import User
from app.domains.user_settings.service import UserSettingService
from tests.test_easy_briefing_ui import HARNESS
from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import NODE
from tests.test_easy_briefing_ui import NOW
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

A, B = 1, 2
NOON = datetime(2026, 11, 1, 3, 0)        # 한국 시각 11-01 12:00
NEXT_DAY = datetime(2026, 11, 2, 3, 0)    # 한국 시각 11-02 12:00
LATE = datetime(2026, 11, 1, 15, 30)      # 한국 시각 11-02 00:30

_TEMPLATE = {}


def _template_db() -> str:
    if "path" not in _TEMPLATE:
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "template.db")
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50), created_at DATETIME)"))
        engine.dispose()
        _TEMPLATE["path"], _TEMPLATE["folder"] = path, folder
    return _TEMPLATE["path"]


def tearDownModule():
    if "folder" in _TEMPLATE:
        shutil.rmtree(_TEMPLATE["folder"], ignore_errors=True)
    _TEMPLATE.clear()


def signal(keyword, series=(40.123, 45.0, 60.0)):
    return TrendSignal(keyword=keyword, market="NAVER_SHOPPING", search_volume_series=list(series), observed_at=datetime.utcnow().isoformat() + "Z", source="fake")


class FakeAdapter:
    """fetch마다 호출을 기록한다. `answers`에 없으면 None(못 읽음)."""

    def __init__(self, answers=None, raise_for=None):
        self.answers = answers if answers is not None else {}
        self.raise_for = raise_for or {}
        self.fetched = []

    def fetch(self, keyword):
        self.fetched.append(keyword)
        if keyword in self.raise_for:
            raise self.raise_for[keyword]
        series = self.answers.get(keyword)
        return signal(keyword, series) if series else None


class DbCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copyfile(_template_db(), f"{self.tmp}/t.db")
        self.engine = create_engine(f"sqlite:///{self.tmp}/t.db", connect_args={"check_same_thread": False})
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.now = NOON
        self.uid = self.user("u1")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def user(self, name, company_id=A, active=True):
        u = User(company_id=company_id, email=f"{name}@example.com", username=name, password_hash="x", is_active=active)
        self.db.add(u)
        self.db.commit()
        return u.id

    def service(self):
        return TrendKeywordService(self.db, now_factory=lambda: self.now)

    def add(self, *words, uid=None, company_id=A):
        for word in words:
            self.service().add(uid or self.uid, company_id, word)

    def rows(self, uid=None, company_id=A):
        return {r["keyword"]: r for r in self.service().list_rows(uid or self.uid, company_id)["rows"]}

    def allow(self, count=10, company_id=A):
        return ExternalCallPolicyService(self.db).grant(
            company_id, system=ExternalSystem.NAVER, kind=CallKind.READ, purpose=CallPurpose.TREND_LOOKUP,
            mode=AllowanceMode.COUNTED, count=count, user_id=1)


class ListTests(DbCase):

    def test_a_keyword_is_added_cleaned_and_starts_unread(self):
        out = self.service().add(self.uid, A, "  무선\n청소기 ")
        self.assertEqual([r["keyword"] for r in out["rows"]], ["무선 청소기"])
        row = out["rows"][0]
        self.assertEqual((row["state"], row["score"], row["at"], row["due"]), ("NEVER", None, None, True))
        self.assertEqual((out["max"], out["due"], out["per_press"]), (30, 1, 10))

    def test_bad_keywords_are_refused(self):
        for bad in ("", "   ", None, "가" * 41, "010-1234-5678", "a@b.co", "https://x.kr", "12345678901"):
            with self.assertRaises(BadRequestException, msg=repr(bad)):
                self.service().add(self.uid, A, bad)
        self.assertEqual(self.rows(), {})
        self.add("가" * 40)  # 경계
        self.add("3단 우산")  # 짧은 숫자는 된다

    def test_duplicates_are_refused_ignoring_case(self):
        self.add("Vacuum")
        with self.assertRaises(BadRequestException):
            self.add("vacuum")
        self.assertEqual(list(self.rows()), ["Vacuum"])

    def test_at_most_thirty_and_a_full_list_still_fits_the_setting_limit(self):
        self.add(*[f"{'키' * 37}{i:02d}" for i in range(MAX_KEYWORDS)])
        self.assertEqual(len(self.rows()), 30)
        with self.assertRaises(BadRequestException):
            self.add("하나 더")
        fake = FakeAdapter({k: (1234.5678, 2.25, 99.99) for k in self.rows()})
        self.allow(40)
        for _ in range(3):
            self.service().lookup(self.uid, A, fake)
        self.assertEqual({r["state"] for r in self.rows().values()}, {"OK"})

    def test_lists_are_per_user_and_company(self):
        other, foreign = self.user("u2"), self.user("u3", company_id=B)
        self.add("생수")
        self.assertEqual(self.rows(other), {})
        self.assertEqual(self.rows(foreign, B), {})
        self.assertEqual(self.rows(self.uid, B), {})

    def test_removing_needs_an_existing_keyword_and_ignores_case(self):
        self.add("Vacuum", "생수")
        self.service().remove(self.uid, A, "vacuum")
        self.assertEqual(list(self.rows()), ["생수"])
        with self.assertRaises(BadRequestException):
            self.service().remove(self.uid, A, "없음")

    def test_nothing_goes_out_when_adding_or_removing(self):
        self.add("생수")
        self.service().remove(self.uid, A, "생수")
        self.assertEqual(self.db.query(ExternalCallLog).count(), 0)
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)

    def test_broken_stored_values_do_not_crash(self):
        self.add("생수")
        self.db.execute(text("UPDATE user_settings SET setting_value = '[1]'"))
        self.db.commit()
        self.assertEqual(self.rows(), {})
        self.db.execute(text("UPDATE user_settings SET setting_value = '{{'"))
        self.db.commit()
        self.assertEqual(self.rows(), {})
        self.db.execute(text("UPDATE user_settings SET setting_value = :v"), {"v": json.dumps({"items": [{"k": "생수"}, {"x": 1}, 3]}, ensure_ascii=False)})
        self.db.commit()
        self.assertEqual(list(self.rows()), ["생수"])


class LookupTests(DbCase):

    def test_a_result_is_saved_with_the_score_rule_and_the_two_ends(self):
        self.add("생수")
        out = self.service().lookup(self.uid, A, FakeAdapter({"생수": (40.126, 45.0, 60.0)}))
        self.assertEqual((out["saved"], out["no_data"], out["blocked"], out["left"]), (1, 0, None, 0))
        row = self.rows()["생수"]
        self.assertEqual((row["state"], row["first"], row["last"], row["points"], row["at"], row["score_at"], row["due"]),
                         ("OK", 40.13, 60.0, 3, "2026-11-01T12:00", "2026-11-01T12:00", False))
        self.assertAlmostEqual(row["score"], 0.5 + ((60.0 - 40.126) / 40.126) * 0.5, places=3)
        self.assertEqual(row["confidence"], 0.9)

    def test_rows_are_sorted_by_score_with_unread_last(self):
        self.add("낮음", "안봄", "높음")
        self.service().lookup(self.uid, A, FakeAdapter({"낮음": (50, 50, 40), "높음": (50, 50, 60)}), keyword="낮음")
        self.service().lookup(self.uid, A, FakeAdapter({"높음": (50, 50, 60)}), keyword="높음")
        self.assertEqual([r["keyword"] for r in self.service().list_rows(self.uid, A)["rows"]], ["높음", "낮음", "안봄"])

    def test_not_read_is_not_a_zero_and_keeps_the_earlier_score(self):
        self.add("생수")
        self.service().lookup(self.uid, A, FakeAdapter({"생수": (40, 45, 60)}))
        before = self.rows()["생수"]
        self.now = NEXT_DAY
        out = self.service().lookup(self.uid, A, FakeAdapter({}))
        self.assertEqual((out["saved"], out["no_data"]), (0, 1))
        row = self.rows()["생수"]
        self.assertEqual((row["state"], row["at"]), ("NO_DATA", "2026-11-02T12:00"))
        self.assertEqual((row["score"], row["first"], row["last"], row["score_at"]), (before["score"], 40.0, 60.0, "2026-11-01T12:00"))

    def test_never_read_stays_scoreless(self):
        self.add("생수")
        self.service().lookup(self.uid, A, FakeAdapter({}))
        row = self.rows()["생수"]
        self.assertEqual((row["state"], row["score"], row["first"]), ("NO_DATA", None, None))

    def test_too_few_points_is_its_own_state_without_a_score(self):
        self.add("생수")
        out = self.service().lookup(self.uid, A, FakeAdapter({"생수": (40, 60)}))
        self.assertEqual(out["few_data"], 1)
        row = self.rows()["생수"]
        self.assertEqual((row["state"], row["score"], row["points"]), ("FEW_DATA", None, 2))

    def test_one_press_reads_ten_never_read_first_then_oldest(self):
        words = [f"k{i:02d}" for i in range(12)]
        self.add(*words)
        answers = {w: (1, 2, 3) for w in words}
        fake = FakeAdapter(answers)
        out = self.service().lookup(self.uid, A, fake)
        self.assertEqual(fake.fetched, words[:10])
        self.assertEqual((out["saved"], out["left"]), (10, 2))
        self.now = NEXT_DAY
        fake = FakeAdapter(answers)
        self.service().lookup(self.uid, A, fake, limit=4)
        self.assertEqual(fake.fetched[:2], ["k10", "k11"])  # 어제 못 본 것(한 번도 안 본 것)부터
        self.assertEqual(fake.fetched[2:], ["k00", "k01"])

    def test_what_was_read_today_is_not_read_again_until_the_korean_day_changes(self):
        self.add("생수")
        fake = FakeAdapter({"생수": (1, 2, 3)})
        self.service().lookup(self.uid, A, fake)
        out = self.service().lookup(self.uid, A, fake)
        self.assertEqual((len(fake.fetched), out["saved"], out["left"]), (1, 0, 0))
        self.assertEqual(self.service().list_rows(self.uid, A)["due"], 0)
        self.now = LATE
        self.assertEqual(self.service().list_rows(self.uid, A)["due"], 1)
        self.service().lookup(self.uid, A, fake)
        self.assertEqual(len(fake.fetched), 2)

    def test_a_named_keyword_is_read_again_even_today_and_must_be_in_the_list(self):
        self.add("생수")
        fake = FakeAdapter({"생수": (1, 2, 3)})
        self.service().lookup(self.uid, A, fake)
        self.service().lookup(self.uid, A, fake, keyword="생수")
        self.assertEqual(len(fake.fetched), 2)
        with self.assertRaises(BadRequestException):
            self.service().lookup(self.uid, A, fake, keyword="없음")
        self.assertEqual(len(fake.fetched), 2)

    def test_no_key_means_no_call(self):
        self.add("생수", "우산")
        fake = FakeAdapter({"생수": (1, 2, 3)})
        out = self.service().lookup(self.uid, A, fake, key_check=lambda: False)
        self.assertEqual((out["blocked"], out["left"], fake.fetched), ("NO_KEY", 2, []))
        self.assertEqual(self.rows()["생수"]["state"], "NEVER")
        self.service().lookup(self.uid, A, fake, key_check=lambda: None)  # 모르면 막지 않는다
        self.assertEqual(len(fake.fetched), 2)

    def test_nothing_to_read_asks_nothing(self):
        checks = []
        out = self.service().lookup(self.uid, A, FakeAdapter(), key_check=lambda: checks.append(1))
        self.assertEqual((checks, out["blocked"]), ([], None))

    def test_one_failure_does_not_stop_the_rest(self):
        self.add("가", "나")
        fake = FakeAdapter({"나": (1, 2, 3)}, raise_for={"가": RuntimeError("x")})
        with self.assertLogs("homez", level="ERROR"):
            out = self.service().lookup(self.uid, A, fake)
        self.assertEqual((out["failed"], out["saved"]), (1, 1))
        self.assertEqual(self.rows()["가"]["state"], "NEVER")

    def test_a_keyword_removed_during_a_lookup_does_not_come_back(self):
        self.add("가", "나")
        service = self.service()

        class Removing(FakeAdapter):
            def fetch(inner, keyword):
                service.remove(self.uid, A, keyword)
                return signal(keyword)

        self.assertEqual(service.lookup_one(self.uid, A, "가", Removing()), "OK")
        self.assertEqual(list(self.rows()), ["나"])

    def test_a_save_race_is_retried_with_the_latest_list(self):
        self.add("가")
        real_put = UserSettingService.put
        calls = []

        def racing(svc, user_id, company_id, key, value, version, schema):
            calls.append(1)
            if len(calls) == 1:
                real_put(svc, user_id, company_id, key, {"items": [{"k": "가"}, {"k": "다른 창에서 추가"}]}, version, schema)
                raise ConflictException("먼저 저장됨")
            return real_put(svc, user_id, company_id, key, value, version, schema)

        with mock.patch.object(UserSettingService, "put", racing):
            self.service().add(self.uid, A, "나")
        self.assertEqual(sorted(self.rows()), ["가", "나", "다른 창에서 추가"])

    def test_a_second_lost_race_is_reported_not_swallowed(self):
        with mock.patch.object(UserSettingService, "put", side_effect=ConflictException("x")):
            with self.assertRaises(ConflictException):
                self.service().add(self.uid, A, "나")


class GateTests(DbCase):
    """실제 Adapter + 가짜 HTTP로 승인 센터 문까지 확인한다."""

    def adapter(self, http, company_id=A):
        class Store:
            def read(self, reference):
                return {"client_id": "id", "client_secret": "secret"}

        class Tracker:
            def is_within_limit(self, *a, **k):
                return type("R", (), {"within_limit": True})()

            def record_call(self, *a, **k):
                pass

        return NaverDataLabTrendAdapter(Store(), "ref", http_post=http, usage_tracker=Tracker(), call_gate=naver_trend_gate(self.db, company_id, actor_user_id=7))

    def http(self):
        sent = []

        def post(url, headers=None, json=None, timeout=None):
            sent.append(json["keywordGroups"][0]["keywords"][0])
            return type("R", (), {"status_code": 200, "json": lambda s: {"results": [{"data": [{"ratio": 40}, {"ratio": 50}, {"ratio": 60}]}]}})()

        post.sent = sent
        return post

    def test_without_an_allowance_nothing_is_called_and_a_request_is_raised(self):
        self.add("생수", "우산")
        http = self.http()
        out = self.service().lookup(self.uid, A, self.adapter(http))
        self.assertEqual((http.sent, out["blocked"], out["left"], out["saved"]), ([], "ALLOWANCE_REQUIRED", 2, 0))
        self.assertEqual(self.rows()["생수"]["state"], "NEVER")
        pending = self.db.query(ExternalCallAllowance).filter_by(status="PENDING").one()
        self.assertEqual((pending.system, pending.kind, pending.purpose, pending.requested_by), ("NAVER", "READ", "TREND_LOOKUP", 7))

    def test_each_lookup_uses_one_allowance_and_running_out_keeps_results(self):
        self.add("생수", "우산", "의자")
        allowance = self.allow(2)
        http = self.http()
        out = self.service().lookup(self.uid, A, self.adapter(http))
        self.assertEqual(http.sent, ["생수", "우산"])
        self.assertEqual((out["saved"], out["blocked"], out["left"]), (2, "ALLOWANCE_REQUIRED", 1))
        self.db.expire_all()
        self.assertEqual(self.db.get(ExternalCallAllowance, allowance.id).remaining_count, 0)
        self.assertEqual(self.rows()["우산"]["state"], "OK")
        self.assertEqual(self.rows()["의자"]["state"], "NEVER")
        self.assertEqual(self.db.query(ExternalCallLog).filter_by(outcome="SUCCEEDED").count(), 2)

    def test_emergency_stop_blocks(self):
        self.add("생수")
        self.allow()
        SafetyService(self.db).activate_emergency_stop("시험", set_by=1, is_admin=True)
        http = self.http()
        out = self.service().lookup(self.uid, A, self.adapter(http))
        self.assertEqual((http.sent, out["blocked"]), ([], "ESTOP"))

    def test_only_the_keyword_is_sent(self):
        self.add("생수")
        self.allow()
        captured = {}

        def post(url, headers=None, json=None, timeout=None):
            captured.update(json)
            return type("R", (), {"status_code": 200, "json": lambda s: {"results": [{"data": [{"ratio": 1}, {"ratio": 2}, {"ratio": 3}]}]}})()

        self.service().lookup(self.uid, A, self.adapter(post))
        self.assertEqual(captured["keywordGroups"], [{"groupName": "생수", "keywords": ["생수"]}])
        self.assertEqual(set(captured), {"startDate", "endDate", "timeUnit", "keywordGroups"})


class AutoRunTests(DbCase):

    def auto(self, adapter, key=True):
        return DiscoveryAutoRunService(
            self.db, adapter_factory=lambda db, company_id: adapter, key_check=lambda: key,
            now_factory=lambda: self.now, release_filler=lambda company_id: {"filled": 0, "blocked": None},
        )

    def seed_candidate(self, name):
        candidate, _ = ProductCandidateService(self.db).register_private_candidate(
            ProductCandidatePrivateCreate(source_reference=f"ref-{name}", market="COUPANG", product_name=name), company_id=A, correlation_id="c")
        return candidate.id

    def test_the_morning_run_reads_keywords_and_records_it(self):
        self.add("생수", "우산")
        fake = FakeAdapter({"생수": (1, 2, 3)})
        summary = self.auto(fake).run(A, now=NOON)
        self.assertEqual(sorted(fake.fetched), ["생수", "우산"])
        self.assertEqual((summary["kw_saved"], summary["kw_no_data"], summary["kw_left"], summary["blocked"]), (1, 1, 0, None))
        self.assertEqual(self.rows()["생수"]["state"], "OK")

    def test_candidates_go_first_and_keywords_share_what_is_left(self):
        ids = [self.seed_candidate(f"상품{i}") for i in range(7)]
        self.add(*[f"키{i}" for i in range(5)])
        fake = FakeAdapter({f"상품{i}": (1, 2, 3) for i in range(7)})
        summary = self.auto(fake).run(A, now=NOON)
        self.assertEqual(len(fake.fetched), 10)
        self.assertEqual(fake.fetched[-3:], ["키0", "키1", "키2"])
        self.assertEqual((summary["trend_saved"], summary["kw_no_data"], summary["kw_left"]), (7, 3, 2))
        self.assertTrue(ids)

    def test_no_key_is_reported_without_a_call(self):
        self.add("생수")
        fake = FakeAdapter()
        summary = self.auto(fake, key=False).run(A, now=NOON)
        self.assertEqual((fake.fetched, summary["blocked"], summary["kw_left"]), ([], "NO_KEY", 1))

    def test_a_blocked_lookup_stops_and_is_reported(self):
        from app.domains.external_call.service import ExternalCallNotAllowed

        self.add("생수", "우산")
        fake = FakeAdapter(raise_for={"생수": ExternalCallNotAllowed("NAVER", "READ", "TREND_LOOKUP")})
        summary = self.auto(fake).run(A, now=NOON)
        self.assertEqual((summary["blocked"], summary["kw_left"]), ("ALLOWANCE_REQUIRED", 2))
        self.assertEqual(fake.fetched, ["생수"])

    def test_inactive_users_and_other_companies_are_not_read(self):
        gone, foreign = self.user("gone", active=False), self.user("foreign", company_id=B)
        self.add("퇴사자 키워드", uid=gone)
        self.add("남의 회사 키워드", uid=foreign, company_id=B)
        self.add("생수")
        fake = FakeAdapter()
        self.auto(fake).run(A, now=NOON)
        self.assertEqual(fake.fetched, ["생수"])

    def test_a_setting_row_of_another_company_does_not_count(self):
        self.add("생수", company_id=B)  # A 회사 사용자의 B 회사 설정 행 — 어느 회사 자동 실행에도 잡히지 않는다
        self.assertEqual(TrendKeywordService(self.db).company_users(A), [])
        self.assertEqual(TrendKeywordService(self.db).company_users(B), [])

    def test_a_candidate_lookup_that_was_blocked_stops_keywords_too(self):
        from app.domains.external_call.service import ExternalCallNotAllowed

        self.seed_candidate("상품")
        self.add("생수")
        fake = FakeAdapter(raise_for={"상품": ExternalCallNotAllowed("NAVER", "READ", "TREND_LOOKUP")})
        summary = self.auto(fake).run(A, now=NOON)
        self.assertEqual(fake.fetched, ["상품"])
        self.assertEqual((summary["blocked"], summary["kw_saved"], summary["kw_no_data"]), ("ALLOWANCE_REQUIRED", 0, 0))

    def test_several_users_share_the_budget(self):
        other = self.user("u2")
        self.add(*[f"가{i}" for i in range(8)])
        self.add(*[f"나{i}" for i in range(8)], uid=other)
        fake = FakeAdapter()
        summary = self.auto(fake).run(A, now=NOON)
        self.assertEqual(len(fake.fetched), 10)
        self.assertEqual((summary["kw_no_data"], summary["kw_left"]), (10, 6))

    def test_a_crash_keeps_the_candidate_results(self):
        self.add("생수")
        service = self.auto(FakeAdapter())
        with mock.patch.object(TrendKeywordService, "company_users", side_effect=RuntimeError("x")):
            with self.assertLogs("homez", level="ERROR"):
                summary = service.run(A, now=NOON)
        self.assertEqual(summary["failed"], 1)

    def test_it_touches_no_candidate_or_decision(self):
        one = self.seed_candidate("상품 아주 많이 긴 이름 그대로 쓰는 상품명 예시")
        self.add("생수")
        self.auto(FakeAdapter({"생수": (1, 2, 3)})).run(A, now=NOON)
        self.db.expire_all()
        self.assertIsNone(self.db.get(ProductCandidate, one).trend_score)
        self.assertEqual(self.db.query(ProductCandidate).count(), 1)


class RouteTests(DbCase):

    def test_routes_are_admin_only_and_static(self):
        from app.core.guard import admin_guard

        expected = {"/product-candidates/trend-keywords": {"GET", "POST"}, "/product-candidates/trend-keywords/remove": {"POST"}, "/product-candidates/trend-keywords/lookup": {"POST"}}
        for path, methods in expected.items():
            routes = [r for r in app.main.app.routes if getattr(r, "path", "") == path]
            self.assertEqual({m for r in routes for m in r.methods}, methods, path)
            for route in routes:
                self.assertIn(admin_guard, [d.call for d in route.dependant.dependencies], path)

    def test_the_lookup_route_reports_no_key_without_calling(self):
        self.add("생수")
        store = mock.Mock()
        from app.core.windows_credential_store import CredentialNotFoundError

        store.read.side_effect = CredentialNotFoundError("x")
        user = mock.Mock(id=self.uid, company_id=A)
        fake = FakeAdapter({"생수": (1, 2, 3)})
        out = candidate_router.lookup_trend_keywords(data=None, current_user=user, db=self.db, adapter=fake, credential_store=store)
        self.assertEqual((out["blocked"], fake.fetched), ("NO_KEY", []))
        listing = candidate_router.list_trend_keywords(current_user=user, db=self.db, credential_store=store)
        self.assertIs(listing["naver_key_registered"], False)
        self.assertEqual(listing["rows"][0]["keyword"], "생수")

    def test_extra_fields_are_refused(self):
        from pydantic import ValidationError

        from app.domains.product_candidate.schema import TrendKeywordRequest

        with self.assertRaises(ValidationError):
            TrendKeywordRequest(keyword="생수", company_id=2)


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------

def kw_texts(lang="ko-KR"):
    block = re.search(rf'"{lang}": \{{(.*?)\n  \}}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:kw\.[a-z_]+|an\.(?:col_trend|no_score|tab_kw)|disc\.(?:col_next|auto_blk_estop)|error))": ("(?:[^"\\]|\\.)*")', block)}


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenFunctionTests(unittest.TestCase):

    def run_js(self, expression):
        consts = re.search(r"^  const KW_BLOCKED = [^\n]+$", JS, re.M).group(0)
        sources = "\n".join([consts, 'const chip = (tone, text) => `<i class="${tone}">${text}</i>`;'] + [function_source(n) for n in ("scoreCell", "kwChangeText", "kwStateCell", "kwNote", "kwRowsHtml")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__NOW__", str(NOW)).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(kw_texts())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_the_change_is_shown_with_its_percent_and_never_invented(self):
        self.assertEqual(self.run_js("kwChangeText({first: 40, last: 60})"), "40 → 60 (+50%)")
        self.assertEqual(self.run_js("kwChangeText({first: 80, last: 60})"), "80 → 60 (-25%)")
        self.assertEqual(self.run_js("kwChangeText({first: 0, last: 60})"), "0 → 60")
        self.assertEqual(self.run_js("kwChangeText({first: null, last: null})"), "—")
        self.assertEqual(self.run_js("kwChangeText({first: 'x', last: 2})"), "—")

    def test_not_read_never_looks_like_zero(self):
        html = self.run_js("kwRowsHtml([{keyword: '<b>생수</b>', state: 'NO_DATA', score: null, first: null, last: null, at: '2026-11-01T12:00'}])")
        self.assertIn("&lt;b&gt;생수&lt;/b&gt;", html)
        self.assertNotIn("<b>생수", html)
        self.assertIn("못 읽음", html)
        self.assertNotIn(">0.00<", html)
        self.assertIn("2026-11-01 12:00", html)
        kept = self.run_js("kwRowsHtml([{keyword: '생수', state: 'NO_DATA', score: 0.75, score_at: '2026-10-30T07:00', first: 40, last: 60, at: '2026-11-01T12:00'}])")
        self.assertIn("2026-10-30 07:00에 받은 점수", kept)
        ok = self.run_js("kwRowsHtml([{keyword: '생수', state: 'OK', score: 0.75, score_at: '2026-11-01T12:00', first: 40, last: 60, at: '2026-11-01T12:00'}])")
        self.assertNotIn("에 받은 점수", ok)

    def test_each_state_has_its_label(self):
        for state, word in (("OK", "읽음"), ("NO_DATA", "못 읽음"), ("FEW_DATA", "데이터 부족"), ("NEVER", "조회 전"), ("???", "조회 전")):
            self.assertIn(word, self.run_js(f"kwStateCell({{state: {json.dumps(state)}}})"))

    def test_the_result_note_is_honest(self):
        ok = self.run_js("kwNote({saved: 2, no_data: 1, few_data: 1, failed: 0, blocked: null, left: 0})")
        self.assertEqual(ok["tone"], "ok")
        self.assertIn("읽음 2개 · 못 읽음 2개 · 오류 0개", ok["text"])
        self.assertIn("0점이라는 뜻이 아니에요", ok["text"])
        self.assertEqual(self.run_js("kwNote({saved: 0, no_data: 1, failed: 0})")["tone"], "warn")
        self.assertEqual(self.run_js("kwNote({saved: 1, no_data: 0, failed: 1})")["tone"], "warn")
        more = self.run_js("kwNote({saved: 10, left: 2})")
        self.assertIn("2개가 남았어요", more["text"])
        none = self.run_js("kwNote({saved: 0, no_data: 0, few_data: 0, failed: 0, blocked: null})")
        self.assertEqual(none, {"tone": "warn", "text": kw_texts()["easy.kw.nothing"]})

    def test_being_blocked_is_not_shown_as_done(self):
        blocked = self.run_js("kwNote({saved: 0, blocked: 'ALLOWANCE_REQUIRED'})")
        self.assertEqual(blocked["tone"], "bad")
        self.assertIn("검색어 트렌드 조회", blocked["text"])
        self.assertNotIn("읽음 0개", blocked["text"])
        partial = self.run_js("kwNote({saved: 2, blocked: 'ALLOWANCE_REQUIRED'})")
        self.assertEqual(partial["tone"], "warn")
        self.assertIn("읽음 2개", partial["text"])
        self.assertIn("네이버 키", self.run_js("kwNote({blocked: 'NO_KEY'})")["text"])


class ScreenWiringTests(unittest.TestCase):

    def test_the_tab_opens_the_screen(self):
        self.assertIn('["KEYWORD", "easy.an.tab_kw", ""]', JS)
        self.assertIn('tab === "KEYWORD"', JS)
        self.assertIn("renderKeywords(host);", JS)

    def test_the_bulk_lookup_confirms_first(self):
        render = function_source("renderKeywords")
        self.assertLess(render.index('T("easy.kw.look_title")'), render.index("await lookup({});"))
        self.assertIn("if (!ok) return;", render)
        self.assertIn('api("/product-candidates/trend-keywords/lookup", { method: "POST"', render)
        self.assertIn('maxlength="40"', render)

    def test_the_screen_says_keywords_cannot_be_compared(self):
        lead = kw_texts()["easy.kw.lead"]
        for word in ("키워드끼리 검색량을 비교할 수는 없어요", "0.5가 변화 없음", "검색어 트렌드 조회"):
            self.assertIn(word, lead)

    def test_texts_exist_in_both_languages(self):
        ko, en = set(kw_texts("ko-KR")), set(kw_texts("en-US"))
        self.assertEqual({k for k in ko if ".kw." in k}, {k for k in en if ".kw." in k})
        self.assertGreater(len([k for k in ko if ".kw." in k]), 25)

    def test_the_auto_run_card_reports_keywords(self):
        auto = function_source("flowAutoHtml")
        self.assertIn('T("easy.disc.auto_kw"', auto)


if __name__ == "__main__":
    unittest.main()
