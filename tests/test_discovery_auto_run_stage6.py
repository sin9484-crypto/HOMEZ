"""
=========================================================
Homez OS

File : tests/test_discovery_auto_run_stage6.py

2026-10-31 — AI 통합 계획 6단계(정해진 시간 실행, 규칙·비용 0)를 고정한다. 네트워크 없음(네이버 HTTP는 가짜, 승인 센터·감사 기록·
자동화 모드는 임시 DB의 실제 코드).

  - 네이버 검색어 트렌드도 승인 센터를 거친다: 허용이 없으면 HTTP를 보내지 않고 허용 요청(검색어 트렌드 조회, 10회)이 올라간다.
    사람이 누를 때(라우터)와 자동 실행 때 같은 문이다. 호출 결과(성공·실패)가 호출 기록에 남는다.
  - 자동 실행: '상품 발굴' 자동화 모드가 '자동'일 때만, 한국 시각 아침 6시 이후, 하루 한 번. 신제품 판정(출시일 있는 것만)과
    짧은 규칙 검색어의 트렌드 조회(최대 10건)만 한다 — 긴 검색어·빈 검색어는 건너뛰고, 사람 검토 중인 후보와 다른 회사는 건드리지 않고,
    평가·승인은 하지 않는다. 비상 정지면 아무것도 하지 않는다. 결과는 감사 기록 한 줄로 남고 화면이 그것을 읽는다.
  - 쉬운 보기 '탐색 흐름' 탭의 자동 실행 칸과 켜기·끄기(기존 기능별 자동화 모드 경로).
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.main  # noqa: F401 — 모든 모델을 메타데이터에 올린다
from app.core.api_usage_tracker import ApiUsageTracker
from app.database.base import Base
from app.domains.automation_safety.service import SafetyService
from app.domains.decision.model import DecisionEvaluation
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import AllowanceStatus
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallNotAllowed
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.product_candidate import discovery_auto_run_service as auto
from app.domains.product_candidate import router as router_module
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.scheduler import jobs
from app.domains.trend_discovery.adapter import NaverDataLabTrendAdapter

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

A, B = 1, 2
# 한국 시각 2026-10-31 07:00 = UTC 2026-10-30 22:00
MORNING = datetime(2026, 10, 30, 22, 0)
DAWN = datetime(2026, 10, 30, 20, 59)      # 한국 시각 05:59
NEXT_DAY = datetime(2026, 10, 31, 22, 0)
KNOWN = {"생수": [40, 45, 52, 60], "무릎보호대": [20, 22, 21, 25]}


class FakeResponse:

    def __init__(self, status_code, payload):
        self.status_code, self._payload = status_code, payload

    def json(self):
        return self._payload


class FakeNaverHttp:

    def __init__(self, fail_on=()):
        self.sent = []
        self.fail_on = set(fail_on)

    def __call__(self, url, headers=None, json=None, timeout=None):
        keyword = json["keywordGroups"][0]["keywords"][0]
        self.sent.append(keyword)
        if keyword in self.fail_on:
            raise ConnectionError("down")
        series = KNOWN.get(keyword)
        results = [{"data": [{"ratio": v} for v in series]}] if series else []
        return FakeResponse(200, {"results": results})


class FakeStore:

    def read(self, reference):
        return {"client_id": "id", "client_secret": "secret"}


_TEMPLATE = {}


def _template_db() -> str:
    """전체 스키마는 한 번만 만들고 시험마다 파일을 복사한다(이 PC에서 시험마다 만들면 몇 분이 걸린다)."""

    if "path" not in _TEMPLATE:
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "template.db")
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            # audit_logs는 ORM 모델이 없는 실제 테이블이라 직접 만든다(실제 DB와 같은 열).
            conn.execute(text(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50), created_at DATETIME)"))
        engine.dispose()
        _TEMPLATE["path"], _TEMPLATE["folder"] = path, folder
    return _TEMPLATE["path"]


def tearDownModule():
    if "folder" in _TEMPLATE:
        shutil.rmtree(_TEMPLATE["folder"], ignore_errors=True)


class DbCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copyfile(_template_db(), f"{self.tmp}/t.db")
        self.engine = create_engine(f"sqlite:///{self.tmp}/t.db", connect_args={"check_same_thread": False})
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()
        self.http = FakeNaverHttp()
        self.tracker = ApiUsageTracker(Path(self.tmp) / "usage.json")
        self.key = True

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 준비 ----

    def seed(self, company_id, name, brand=None, release=None):
        candidate, _ = ProductCandidateService(self.db).register_private_candidate(
            ProductCandidatePrivateCreate(source_reference=f"ref-{company_id}-{name}", market="COUPANG",
                                          product_name=name, brand_hint=brand, release_date=release),
            company_id=company_id, correlation_id="c",
        )
        return candidate

    def automatic(self, company_id=A, mode="AUTOMATIC"):
        SafetyService(self.db).set_function_mode(company_id, "PRODUCT_DISCOVERY", mode, set_by=1, is_admin=True)

    def allow(self, count, company_id=A):
        return ExternalCallPolicyService(self.db).grant(
            company_id, system=ExternalSystem.NAVER, kind=CallKind.READ, purpose=CallPurpose.TREND_LOOKUP,
            mode=AllowanceMode.COUNTED, count=count, user_id=1,
        )

    def adapter_factory(self, db, company_id):
        return NaverDataLabTrendAdapter(FakeStore(), "ref", http_post=self.http, usage_tracker=self.tracker,
                                        call_gate=auto.naver_trend_gate(db, company_id))

    def service(self):
        return auto.DiscoveryAutoRunService(self.db, adapter_factory=self.adapter_factory, key_check=lambda: self.key)

    def candidate(self, cid):
        self.db.expire_all()
        return self.db.get(ProductCandidate, cid)

    def evidence(self, cid):
        self.db.expire_all()
        return [e.payload_summary for e in self.db.query(ProductCandidateEvidence).filter_by(candidate_id=cid).all()]

    def standard_set(self):
        water = self.seed(A, "백산수 생수 500ml x 20PET", brand="백산수")
        knit = self.seed(A, "부드럽고 가벼운 간절기 슬림 여성 긴팔 니트 티셔츠")
        black = self.seed(A, "블랙 (본품) 500ml")
        knee = self.seed(A, "무릎보호대", release=date(2026, 10, 25))
        review = self.seed(A, "검토 중 무릎보호대 세트", release=date(2026, 10, 28))
        self.db.add(DecisionEvaluation(
            company_id=A, candidate_id=review.id, policy_version="v1", input_snapshot_json="{}", input_fingerprint="f" * 64,
            total_score=0.5, confidence=0.6, recommendation="RECOMMEND_HOLD", recommendation_reason="r", status="PENDING_REVIEW",
            idempotency_key=f"k-{review.id}", evaluator_kind="RULE", evaluator_version="1"))
        self.db.commit()
        other = self.seed(B, "생수", release=date(2026, 10, 29))
        return water, knit, black, knee, review, other


# ---------------------------------------------------------------------------
# 승인 센터 문
# ---------------------------------------------------------------------------

class GateTests(DbCase):

    def test_the_naver_lookup_is_an_enforced_purpose_with_a_ten_lookup_default_request(self):
        self.assertIn(ExternalSystem.NAVER, ExternalSystem.ALL)
        self.assertEqual(ExternalSystem.LABELS[ExternalSystem.NAVER], "네이버 검색어 트렌드")
        self.assertIn(CallPurpose.TREND_LOOKUP, CallPurpose.ALL)
        self.assertIn(CallPurpose.TREND_LOOKUP, CallPurpose.ENFORCED)
        self.assertEqual(CallPurpose.LABELS[CallPurpose.TREND_LOOKUP], "검색어 트렌드 조회")

    def test_without_an_allowance_nothing_is_sent_or_counted_and_a_request_appears(self):
        adapter = self.adapter_factory(self.db, A)
        with self.assertRaises(ExternalCallNotAllowed):
            adapter.fetch("생수")
        self.assertEqual(self.http.sent, [])
        self.assertTrue(self.tracker.is_within_limit("naver_search_trend", daily_limit=None, monthly_limit=1).within_limit)  # 무료 한도도 쓰지 않았다
        request = self.db.query(ExternalCallAllowance).one()
        self.assertEqual((request.system, request.kind, request.purpose, request.status, request.mode, request.requested_count),
                         ("NAVER", "READ", "TREND_LOOKUP", AllowanceStatus.PENDING, AllowanceMode.COUNTED, 10))

    def test_with_an_allowance_the_call_goes_out_once_and_its_result_is_logged(self):
        allowance = self.allow(2)
        adapter = self.adapter_factory(self.db, A)
        self.assertIsNotNone(adapter.fetch("생수"))
        self.http.fail_on = {"무릎보호대"}
        self.assertIsNone(adapter.fetch("무릎보호대"))
        self.assertEqual(self.http.sent, ["생수", "무릎보호대"])
        logs = [(l.target, l.outcome, l.allowance_id) for l in self.db.query(ExternalCallLog).order_by(ExternalCallLog.id)]
        self.assertEqual(logs, [("kw:생수", "SUCCEEDED", allowance.id), ("kw:무릎보호대", "FAILED", allowance.id)])
        self.db.expire_all()
        self.assertEqual(self.db.get(ExternalCallAllowance, allowance.id).status, AllowanceStatus.EXHAUSTED)
        with self.assertRaises(ExternalCallNotAllowed):
            adapter.fetch("생수")
        self.assertEqual(len(self.http.sent), 2)

    def test_a_missing_key_does_not_ask_the_approval_center(self):
        class NoKey:
            def read(self, reference):
                from app.core.windows_credential_store import CredentialNotFoundError
                raise CredentialNotFoundError(reference)

        adapter = NaverDataLabTrendAdapter(NoKey(), "ref", http_post=self.http, usage_tracker=self.tracker,
                                           call_gate=auto.naver_trend_gate(self.db, A))
        self.assertIsNone(adapter.fetch("생수"))
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)
        self.assertEqual(self.http.sent, [])

    def test_the_button_path_uses_the_same_gate(self):
        candidate = self.seed(A, "백산수 생수 500ml x 20PET", brand="백산수")
        user = type("U", (), {"id": 7, "company_id": A, "role": "ADMIN"})()
        adapter = router_module.get_trend_adapter(FakeStore(), current_user=user, db=self.db)
        adapter._http_post, adapter.usage_tracker = self.http, self.tracker  # 실제 네트워크·실제 설정 폴더를 쓰지 않게
        with self.assertRaises(ExternalCallNotAllowed) as caught:
            router_module.refresh_trend_analysis(candidate.id, data=None, current_user=user, db=self.db, adapter=adapter)
        self.assertIn("EXTERNAL_ALLOWANCE_REQUIRED", str(caught.exception))
        self.assertEqual(self.http.sent, [])
        self.assertIsNone(self.candidate(candidate.id).trend_score)
        self.assertEqual(self.db.query(ExternalCallAllowance).one().requested_by, 7)
        self.allow(1)
        result = router_module.refresh_trend_analysis(candidate.id, data=None, current_user=user, db=self.db, adapter=adapter)
        self.assertEqual((result.status, self.http.sent), ("APPLIED", ["생수"]))
        self.assertEqual(self.db.query(ExternalCallLog).filter_by(outcome="SUCCEEDED").one().actor_user_id, 7)

    def test_emergency_stop_blocks_the_lookup_even_with_an_allowance(self):
        self.allow(5)
        SafetyService(self.db).activate_emergency_stop("시험", set_by=1, is_admin=True)
        with self.assertRaises(ExternalCallNotAllowed) as caught:
            self.adapter_factory(self.db, A).fetch("생수")
        self.assertEqual(caught.exception.reason, "ESTOP_ACTIVE")
        self.assertEqual(self.http.sent, [])


# ---------------------------------------------------------------------------
# 언제 도는가
# ---------------------------------------------------------------------------

class ScheduleTests(DbCase):

    def test_it_never_runs_while_the_mode_is_manual(self):
        self.seed(A, "백산수 생수 500ml x 20PET", brand="백산수")
        self.assertFalse(self.service().due(A, now=MORNING))  # 한 번도 정하지 않음 = 수동
        for mode in ("MANUAL", "SEMI_AUTOMATIC", "PAUSED"):
            self.automatic(mode=mode)
            self.assertFalse(self.service().due(A, now=MORNING), mode)

    def test_it_runs_once_a_day_after_six_in_the_morning_korean_time(self):
        self.automatic()
        svc = self.service()
        self.assertFalse(svc.due(A, now=DAWN))
        self.assertTrue(svc.due(A, now=datetime(2026, 10, 30, 21, 0)))  # 한국 시각 06:00 정각
        self.assertTrue(svc.due(A, now=MORNING))
        svc.run(A, now=MORNING)
        self.assertFalse(svc.due(A, now=MORNING))
        self.assertFalse(svc.due(A, now=datetime(2026, 10, 31, 14, 59)))  # 한국 시각 같은 날 23:59
        self.assertTrue(svc.due(A, now=NEXT_DAY))
        svc.run(A, now=NEXT_DAY)
        self.assertEqual(svc.last_run(A)["at"], "2026-11-01T07:00")  # 가장 최근 실행을 읽는다
        self.assertFalse(svc.due(A, now=NEXT_DAY))

    def test_the_job_runs_only_due_companies_once_and_survives_a_crash(self):
        self.standard_set()
        self.automatic(A)
        self.automatic(B, mode="MANUAL")
        self.allow(10)
        self.assertEqual(auto.DiscoveryAutoRunService.candidate_companies(self.db), [A, B])
        for _ in range(2):
            jobs.run_discovery_auto_run_job(session_factory=self.SessionLocal, adapter_factory=self.adapter_factory, now=MORNING)
        records = self.db.execute(text("SELECT entity_id FROM audit_logs WHERE action = 'DISCOVERY_AUTO_RUN'")).fetchall()
        self.assertEqual([r[0] for r in records], [str(A)])

        class Broken:  # 세션을 연 뒤 안에서 터지는 경우 — 예외를 밖으로 내지 않고 세션은 닫는다
            closed = False

            def get_bind(self):
                raise RuntimeError("boom")

            def close(self):
                Broken.closed = True

        jobs.run_discovery_auto_run_job(session_factory=Broken)
        self.assertTrue(Broken.closed)

    def test_the_job_is_registered_hourly_and_watched_by_the_health_check(self):
        source = (ROOT / "app/domains/scheduler/jobs.py").read_text(encoding="utf-8")
        body = source.split("def register_all_jobs")[1]
        self.assertIn("run_discovery_auto_run_job,\n        minutes=DISCOVERY_AUTO_RUN_INTERVAL_MINUTES,\n        job_id=DISCOVERY_AUTO_RUN_JOB_ID,", body.replace("\r\n", "\n"))
        self.assertEqual(jobs.DISCOVERY_AUTO_RUN_INTERVAL_MINUTES, 60)
        from app.domains.diagnostics.health_service import expected_job_ids

        self.assertIn(jobs.DISCOVERY_AUTO_RUN_JOB_ID, expected_job_ids())


# ---------------------------------------------------------------------------
# 무엇을 하는가
# ---------------------------------------------------------------------------

class RunTests(DbCase):

    def test_a_full_run_checks_new_products_and_short_keywords_only(self):
        water, knit, black, knee, review, other = self.standard_set()
        self.automatic()
        self.allow(10)
        summary = self.service().run(A, now=MORNING)
        self.assertEqual(sorted(self.http.sent), ["무릎보호대", "생수"])
        self.assertEqual(summary, {"at": "2026-10-31T07:00", "trend_saved": 2, "trend_no_data": 0, "new_saved": 1,
                                   "skipped_long": 1, "skipped_empty": 1, "deferred": 0, "failed": 0, "blocked": None})
        self.assertIsNotNone(self.candidate(water.id).trend_score)
        self.assertTrue(any("검색어 '생수'(규칙)" in e for e in self.evidence(water.id)))
        self.assertEqual((self.candidate(knee.id).is_new_product, self.candidate(knee.id).novelty_score is not None), (True, True))
        for untouched in (knit, black, review, other):
            c = self.candidate(untouched.id)
            self.assertEqual((c.trend_score, c.is_new_product), (None, None), untouched.product_name)
        self.assertEqual(self.service().last_run(A), summary)
        self.assertIsNone(self.service().last_run(B))

    def test_no_allowance_stops_lookups_but_still_checks_new_products(self):
        water, _knit, _black, knee, _review, _other = self.standard_set()
        self.automatic()
        summary = self.service().run(A, now=MORNING)
        self.assertEqual((summary["blocked"], summary["deferred"], summary["trend_saved"], summary["new_saved"]), ("ALLOWANCE_REQUIRED", 2, 0, 1))
        self.assertEqual(self.http.sent, [])
        self.assertEqual(self.db.query(ExternalCallAllowance).filter_by(status="PENDING").count(), 1)
        self.assertIsNone(self.candidate(water.id).trend_score)
        self.assertTrue(self.candidate(knee.id).is_new_product)

    def test_a_used_up_allowance_stops_in_the_middle(self):
        self.standard_set()
        self.automatic()
        self.allow(1)
        summary = self.service().run(A, now=MORNING)
        self.assertEqual((summary["trend_saved"], summary["deferred"], summary["blocked"]), (1, 1, "ALLOWANCE_REQUIRED"))
        self.assertEqual(len(self.http.sent), 1)

    def test_at_most_ten_lookups_per_run(self):
        for i in range(12):
            self.seed(A, f"상품{i:02d} 생수")
        self.automatic()
        self.allow(50)
        summary = self.service().run(A, now=MORNING)
        self.assertEqual(auto.MAX_TREND_LOOKUPS_PER_RUN, 10)
        self.assertEqual((len(self.http.sent), summary["trend_no_data"], summary["deferred"]), (10, 10, 2))

    def test_no_key_skips_lookups_without_asking_the_approval_center(self):
        self.standard_set()
        self.automatic()
        self.key = False
        summary = self.service().run(A, now=MORNING)
        self.assertEqual((summary["blocked"], summary["deferred"], summary["new_saved"]), ("NO_KEY", 2, 1))
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)
        self.assertEqual(self.http.sent, [])

    def test_an_unknown_key_state_still_goes_through_the_gate(self):
        self.seed(A, "백산수 생수 500ml x 20PET", brand="백산수")
        self.automatic()
        self.allow(1)
        self.key = None
        self.assertEqual(self.service().run(A, now=MORNING)["trend_saved"], 1)

    def test_emergency_stop_does_nothing_at_all(self):
        water, _knit, _black, knee, _review, _other = self.standard_set()
        self.automatic()
        self.allow(10)
        SafetyService(self.db).activate_emergency_stop("시험", set_by=1, is_admin=True)
        summary = self.service().run(A, now=MORNING)
        self.assertEqual((summary["blocked"], summary["trend_saved"], summary["new_saved"]), ("ESTOP", 0, 0))
        self.assertIsNone(self.candidate(knee.id).is_new_product)
        self.assertEqual(self.http.sent, [])

    def test_a_failing_lookup_counts_as_no_data_and_the_rest_go_on(self):
        self.standard_set()
        self.automatic()
        self.allow(10)
        self.http.fail_on = {"생수"}
        summary = self.service().run(A, now=MORNING)
        self.assertEqual((summary["trend_saved"], summary["trend_no_data"], summary["failed"]), (1, 1, 0))

    def test_one_broken_candidate_does_not_stop_the_others(self):
        self.standard_set()
        self.automatic()
        self.allow(10)
        svc = self.service()
        real = svc._adapter_factory

        class Exploding:
            def __init__(self, inner):
                self.inner, self.seen = inner, []

            def fetch(self, keyword):
                self.seen.append(keyword)
                if len(self.seen) == 1:  # 처음 조회하는 후보가 터진다 — 뒤의 후보는 계속 조회해야 한다
                    raise RuntimeError("boom")
                return self.inner.fetch(keyword)

        built = []
        svc._adapter_factory = lambda db, cid: built.append(Exploding(real(db, cid))) or built[-1]
        summary = svc.run(A, now=MORNING)
        self.assertEqual(len(built[0].seen), 2)
        self.assertEqual((summary["failed"], summary["trend_saved"]), (1, 1))

    def test_the_run_never_evaluates_approves_or_writes_outside(self):
        source = (ROOT / "app/domains/product_candidate/discovery_auto_run_service.py").read_text(encoding="utf-8")
        for forbidden in ("DecisionService", "evaluate(", "approve", "reject(", "register_private_candidate", "purchase_task", "coupang", "requests."):
            self.assertNotIn(forbidden, source, forbidden)


# ---------------------------------------------------------------------------
# 화면이 읽는 상태
# ---------------------------------------------------------------------------

class StatusTests(DbCase):

    def test_the_flow_endpoint_reports_the_switch_and_the_last_run(self):
        user = type("U", (), {"id": 7, "company_id": A, "role": "ADMIN"})()
        body = router_module.get_discovery_flow(limit=200, current_user=user, db=self.db, credential_store=FakeStore())
        self.assertEqual(body["auto_run"], {"mode": "MANUAL", "automatic": False, "last_run": None, "run_from_hour": 6, "max_lookups": 10})
        self.automatic()
        self.service().run(A, now=MORNING)
        body = router_module.get_discovery_flow(limit=200, current_user=user, db=self.db, credential_store=FakeStore())
        self.assertTrue(body["auto_run"]["automatic"])
        self.assertEqual(body["auto_run"]["last_run"]["at"], "2026-10-31T07:00")

    def test_without_the_tables_the_switch_is_unknown_and_nothing_runs(self):
        engine = create_engine(f"sqlite:///{self.tmp}/bare.db")
        Base.metadata.create_all(bind=engine, tables=[ProductCandidate.__table__])
        bare = sessionmaker(bind=engine)()
        try:
            svc = auto.DiscoveryAutoRunService(bare)
            self.assertEqual(svc.status(A)["mode"], None)
            self.assertFalse(svc.due(A, now=MORNING))
            self.assertEqual(auto.DiscoveryAutoRunService.candidate_companies(bare), [])
        finally:
            bare.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# 쉬운 보기
# ---------------------------------------------------------------------------

def js_function(name):
    from tests.test_easy_briefing_ui import JS as SAME, function_source

    assert SAME == JS
    return function_source(name)


def disc_table():
    block = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:disc|error)[a-z_.]*)": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const chip = (kind, text) => `<span class="chip ${kind}">${esc(text)}</span>`;
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenTests(unittest.TestCase):

    def run_js(self, expression):
        consts = re.search(r"^  const FLOW_BLOCKED_KEYS = [^\n]+$", JS, re.M).group(0)
        sources = "\n".join([consts, js_function("flowAutoHtml"), js_function("flowTrendError")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(disc_table())},
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def html(self, auto_run):
        return self.run_js(f"flowAutoHtml({json.dumps(auto_run)})")

    def test_off_shows_a_turn_on_button_and_the_rules(self):
        html = self.html({"mode": "MANUAL", "automatic": False, "last_run": None, "run_from_hour": 6, "max_lookups": 10})
        for text in ("자동 실행(하루 한 번)", "꺼짐", "아침 6시", "최대 10건", "'검색어 트렌드 조회' 허용", "아직 자동으로 돈 적이 없어요", 'data-ez-flow-auto="AUTOMATIC"', "자동 실행 켜기"):
            self.assertIn(text, html)

    def test_on_shows_the_last_run_and_a_turn_off_button(self):
        last = {"at": "2026-10-31T07:00", "trend_saved": 2, "trend_no_data": 1, "new_saved": 1, "skipped_long": 1, "skipped_empty": 2, "deferred": 3, "blocked": "ALLOWANCE_REQUIRED"}
        html = self.html({"mode": "AUTOMATIC", "automatic": True, "last_run": last, "run_from_hour": 6, "max_lookups": 10})
        for text in ("켜짐", "마지막 실행 2026-10-31 07:00", "트렌드 저장 2건", "데이터 없음 1건", "신제품 판정 1건", "사람이 고를 검색어 3건", "다음으로 미룸 3건",
                     "네이버 조회 허용이 없어", 'data-ez-flow-auto="MANUAL"', "자동 실행 끄기"):
            self.assertIn(text, html)

    def test_each_stop_reason_has_its_own_line_and_unknown_reasons_add_nothing(self):
        base = {"mode": "AUTOMATIC", "automatic": True, "run_from_hour": 6, "max_lookups": 10}
        for reason, text in (("ESTOP", "긴급 중지가 켜져 있어"), ("NO_KEY", "키가 없어"), ("ALLOWANCE_REQUIRED", "허용이 없어")):
            self.assertIn(text, self.html({**base, "last_run": {"at": "x", "blocked": reason}}))
        self.assertNotIn("ez-c-red", self.html({**base, "last_run": {"at": "x", "blocked": "SOMETHING"}}))
        self.assertNotIn("ez-c-red", self.html({**base, "last_run": {"at": "x", "blocked": None}}))

    def test_an_unknown_switch_shows_no_button_and_missing_status_shows_nothing(self):
        html = self.html({"mode": None, "automatic": False, "last_run": None, "run_from_hour": 6, "max_lookups": 10})
        self.assertIn("확인 못 함", html)
        self.assertNotIn("data-ez-flow-auto", html)
        self.assertEqual(self.html(None), "")

    def test_server_text_is_escaped(self):
        html = self.html({"mode": "AUTOMATIC", "automatic": True, "last_run": {"at": "<b>x</b>"}, "run_from_hour": 6, "max_lookups": 10})
        self.assertNotIn("<b>x", html)

    def test_the_allowance_refusal_becomes_plain_words(self):
        self.assertIn("승인 센터에 '검색어 트렌드 조회' 허용 요청을 올렸어요", self.run_js('flowTrendError({"message": "EXTERNAL_ALLOWANCE_REQUIRED: 네이버 …"})'))
        self.assertEqual(self.run_js('flowTrendError({"message": "다른 문제"})'), "다른 문제")


class WiringTests(unittest.TestCase):

    def test_the_tab_shows_the_box_and_switches_through_the_existing_mode_route_after_a_confirm(self):
        render = JS.split("async function renderFlow(host)")[1].split("async function loadAnalysis()")[0]
        self.assertIn("flowNoteHtml() + flowAutoHtml(data.auto_run)", render)
        self.assertEqual(render.count("bindAuto();"), 2)  # 후보가 없어도 켜고 끌 수 있다
        toggle = js_function("flowAutoToggle")
        self.assertLess(toggle.index("await ezDialog("), toggle.index('api("/console/api/function-modes/PRODUCT_DISCOVERY", { method: "POST"'))
        self.assertIn("if (!ok) return false;", toggle)

    def test_the_trend_dialog_says_the_allowance_is_needed_and_maps_the_refusal(self):
        self.assertIn("flowState.note = { tone: \"bad\", text: flowTrendError(err) };", js_function("flowTrendAsk"))
        ko = re.search(r'"easy\.disc\.trend_body": "([^"]*)"', TEXTS).group(1)
        self.assertIn("'검색어 트렌드 조회' 허용이 있어야", ko)

    def test_every_outside_system_has_a_name_on_the_approval_center_summary(self):
        # 승인 센터 '오늘 나간 호출' 줄은 `easy.sys.<시스템>`을 쓴다 — 없으면 키가 그대로 보인다(6단계 E2E에서 발견).
        self.assertIn("T(`easy.sys.${system.toLowerCase()}`)", JS)
        for locale in ("ko-KR", "en-US"):
            block = re.search(r'"%s": \{(.*?)\n  \}' % locale, TEXTS, re.S).group(1)
            for system in ExternalSystem.ALL:
                self.assertRegex(block, r'"easy\.sys\.%s": "[^"]+"' % system.lower(), (locale, system))

    def test_every_new_text_exists_in_both_languages_with_matching_placeholders(self):
        keys = set(re.findall(r'"(easy\.disc\.(?:auto|trend_need)[a-z_]*)"', JS))
        self.assertGreaterEqual(len(keys), 21)
        ko = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        en = re.search(r'"en-US": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        for key in keys:
            kv = re.search(rf'"{re.escape(key)}": "([^"]*)"', ko)
            ev = re.search(rf'"{re.escape(key)}": "([^"]*)"', en)
            self.assertTrue(kv and ev, key)
            self.assertEqual(set(re.findall(r"\{(\w+)\}", kv.group(1))), set(re.findall(r"\{(\w+)\}", ev.group(1))), key)


if __name__ == "__main__":
    unittest.main()
